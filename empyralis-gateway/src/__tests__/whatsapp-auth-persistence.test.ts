import { promises as fs } from "fs";
import { mkdtemp, rm } from "fs/promises";
import { tmpdir } from "os";
import path from "path";
import test from "node:test";
import assert from "node:assert/strict";

import {
  hasPendingAuthWrite,
  hasSymlinkComponent,
  readFileRaw,
  resolveCredsBackupPath,
  resolveCredsPath,
  WhatsAppAuthPersistence,
  writeFileAtomic,
} from "../channels/whatsapp/auth-persistence";

async function withTempAuthDir(fn: (authDir: string) => Promise<void>): Promise<void> {
  const rootDir = await mkdtemp(path.join(tmpdir(), "empyralis-whatsapp-auth-persistence-"));
  const authDir = path.join(rootDir, "whatsapp", "auth");
  await fs.mkdir(authDir, { recursive: true });
  try {
    await fn(authDir);
  } finally {
    await rm(rootDir, { recursive: true, force: true });
  }
}

test("restores creds.json from creds.json.bak when the primary file is corrupted/truncated", async () => {
  await withTempAuthDir(async (authDir) => {
    const persistence = new WhatsAppAuthPersistence(authDir);
    await persistence.saveCreds(() => ({ me: { id: "first@s.whatsapp.net" }, registered: true }));
    await persistence.saveCreds(() => ({ me: { id: "second@s.whatsapp.net" }, registered: true }));
    // At this point creds.json.bak holds the FIRST creds (backed up right
    // before the second write), and creds.json holds the second.
    const backupBeforeCorruption = await readFileRaw(resolveCredsBackupPath(authDir));
    assert.ok(backupBeforeCorruption, "expected a backup to exist after two saves");

    // Simulate a crash mid-write: truncate creds.json to garbage that isn't
    // valid JSON at all (this is NOT going through our atomic writer --
    // it represents damage that happened some other way, e.g. Baileys'
    // own raw writeFile getting interrupted before our code path existed,
    // or a disk hiccup).
    await fs.writeFile(resolveCredsPath(authDir), '{"me":{"id":"second@s.wh', "utf8");
    const corrupted = await readFileRaw(resolveCredsPath(authDir));
    assert.throws(() => JSON.parse(corrupted!), "fixture must actually be invalid JSON");

    const restored = await persistence.restoreFromBackupIfNeeded();
    assert.equal(restored, true, "should report that a restore happened");

    const recoveredRaw = await readFileRaw(resolveCredsPath(authDir));
    assert.ok(recoveredRaw, "creds.json must exist after restore -- not blank identity");
    const recovered = JSON.parse(recoveredRaw!) as { me: { id: string } };
    assert.equal(
      recovered.me.id,
      "first@s.whatsapp.net",
      "restored content must match the last KNOWN-GOOD backup, not be blank/missing",
    );
    assert.equal(recoveredRaw, backupBeforeCorruption, "restored bytes must match the backup exactly");
  });
});

test("restores creds.json from creds.json.bak when the primary file is missing entirely", async () => {
  await withTempAuthDir(async (authDir) => {
    const persistence = new WhatsAppAuthPersistence(authDir);
    await persistence.saveCreds(() => ({ me: { id: "a@s.whatsapp.net" } }));
    await persistence.saveCreds(() => ({ me: { id: "b@s.whatsapp.net" } }));
    await fs.rm(resolveCredsPath(authDir), { force: true });

    const restored = await persistence.restoreFromBackupIfNeeded();
    assert.equal(restored, true);
    const raw = await readFileRaw(resolveCredsPath(authDir));
    assert.ok(raw);
    assert.equal((JSON.parse(raw!) as { me: { id: string } }).me.id, "a@s.whatsapp.net");
  });
});

test("does not restore (and does not touch the file) when creds.json already parses fine", async () => {
  await withTempAuthDir(async (authDir) => {
    const persistence = new WhatsAppAuthPersistence(authDir);
    await persistence.saveCreds(() => ({ me: { id: "only@s.whatsapp.net" } }));
    const before = await readFileRaw(resolveCredsPath(authDir));

    const restored = await persistence.restoreFromBackupIfNeeded();
    assert.equal(restored, false);
    const after = await readFileRaw(resolveCredsPath(authDir));
    assert.equal(after, before, "a healthy creds.json must be left untouched");
  });
});

test("restoreFromBackupIfNeeded returns false (no throw) when both creds.json and the backup are corrupt", async () => {
  await withTempAuthDir(async (authDir) => {
    await fs.writeFile(resolveCredsPath(authDir), "not json at all", "utf8");
    await fs.writeFile(resolveCredsBackupPath(authDir), "also not json", "utf8");
    const persistence = new WhatsAppAuthPersistence(authDir);
    const restored = await persistence.restoreFromBackupIfNeeded();
    assert.equal(restored, false, "nothing safe to restore -- must not throw or fabricate data");
  });
});

test("saveCreds backs up the current valid creds before overwriting, and never clobbers a good backup with a corrupt current file", async () => {
  await withTempAuthDir(async (authDir) => {
    const persistence = new WhatsAppAuthPersistence(authDir);
    await persistence.saveCreds(() => ({ gen: 1 }));
    // No backup should exist yet -- nothing valid existed to back up before
    // the very first write.
    assert.equal(await readFileRaw(resolveCredsBackupPath(authDir)), null);

    await persistence.saveCreds(() => ({ gen: 2 }));
    const backupAfterSecondSave = await readFileRaw(resolveCredsBackupPath(authDir));
    assert.ok(backupAfterSecondSave);
    assert.equal((JSON.parse(backupAfterSecondSave!) as { gen: number }).gen, 1);

    // Externally corrupt the primary file (bypassing our writer) before the
    // next save -- this must NOT get preserved as the new backup.
    await fs.writeFile(resolveCredsPath(authDir), "{not valid", "utf8");
    await persistence.saveCreds(() => ({ gen: 3 }));

    const backupAfterCorruption = await readFileRaw(resolveCredsBackupPath(authDir));
    assert.equal(
      backupAfterCorruption,
      backupAfterSecondSave,
      "backup must remain gen:1 -- a corrupt intermediate file must never overwrite a good backup",
    );
    const finalCreds = await readFileRaw(resolveCredsPath(authDir));
    assert.equal((JSON.parse(finalCreds!) as { gen: number }).gen, 3, "the new write must still land");
  });
});

test("atomic write leaves no partial/temp file behind across several sequential saves", async () => {
  await withTempAuthDir(async (authDir) => {
    const persistence = new WhatsAppAuthPersistence(authDir);
    for (let i = 0; i < 5; i += 1) {
      // Vary payload size (growing then shrinking) to also guard against a
      // truncate-in-place implementation that would leave trailing bytes
      // from a previous, larger write.
      const payload = { gen: i, blob: "x".repeat(i % 2 === 0 ? 50_000 : 10) };
      await persistence.saveCreds(() => payload);
      const raw = await readFileRaw(resolveCredsPath(authDir));
      assert.ok(raw, `creds.json must exist after save #${i}`);
      const parsed = JSON.parse(raw!) as { gen: number };
      assert.equal(parsed.gen, i, "must read back exactly what was just written, never truncated");
    }
    const entries = await fs.readdir(authDir);
    const strayTempFiles = entries.filter((name) => name.includes(".tmp."));
    assert.deepEqual(strayTempFiles, [], `no leftover temp files expected, found: ${strayTempFiles.join(", ")}`);
    const expected = new Set(["creds.json", "creds.json.bak"]);
    for (const name of entries) {
      assert.ok(expected.has(name), `unexpected leftover file in auth dir: ${name}`);
    }
  });
});

test("writeFileAtomic refuses to write through a symlinked target and leaves the real target untouched", async () => {
  await withTempAuthDir(async (authDir) => {
    const elsewhereDir = await mkdtemp(path.join(tmpdir(), "empyralis-whatsapp-auth-elsewhere-"));
    try {
      const realFile = path.join(elsewhereDir, "real-target.json");
      await fs.writeFile(realFile, JSON.stringify({ untouched: true }), "utf8");
      const symlinkPath = path.join(authDir, "creds.json");
      await fs.symlink(realFile, symlinkPath);

      await assert.rejects(() => writeFileAtomic(symlinkPath, JSON.stringify({ untouched: false })));

      const stillReal = await fs.readFile(realFile, "utf8");
      assert.deepEqual(JSON.parse(stillReal), { untouched: true }, "must never follow the symlink to write");
      const symlinkStat = await fs.lstat(symlinkPath);
      assert.ok(symlinkStat.isSymbolicLink(), "the symlink itself must be left exactly as it was");
    } finally {
      await rm(elsewhereDir, { recursive: true, force: true });
    }
  });
});

test("saveCreds skips the write (does not fabricate a null/blank creds.json) when getCreds() returns nothing", async () => {
  await withTempAuthDir(async (authDir) => {
    const persistence = new WhatsAppAuthPersistence(authDir);
    // Simulates a concurrent teardown between creds.update firing and the
    // enqueued save actually running (authBundle already nulled out).
    await persistence.saveCreds(() => undefined);
    const raw = await readFileRaw(resolveCredsPath(authDir));
    assert.equal(raw, null, "no creds.json should be created from an undefined creds value");
  });
});

test("hasPendingWrite()/hasPendingAuthWrite() report true synchronously while a save is in flight and false once it settles", async () => {
  await withTempAuthDir(async (authDir) => {
    const persistence = new WhatsAppAuthPersistence(authDir);
    assert.equal(persistence.hasPendingWrite(), false);
    assert.equal(hasPendingAuthWrite(authDir), false);

    const savePromise = persistence.saveCreds(() => ({ big: "x".repeat(200_000) }));
    // No `await` happened between the call above and these assertions --
    // pendingCounts is incremented synchronously inside saveCreds()/enqueue()
    // before any I/O starts, so this is deterministic, not a timing race.
    assert.equal(persistence.hasPendingWrite(), true, "must be pending immediately after saveCreds() is called");
    assert.equal(hasPendingAuthWrite(authDir), true, "standalone lookup must agree with the instance method");

    await savePromise;
    assert.equal(persistence.hasPendingWrite(), false, "must clear once the write settles");
    assert.equal(hasPendingAuthWrite(authDir), false);
  });
});

test("concurrent saveCreds calls serialize instead of interleaving -- the last enqueued write always wins", async () => {
  await withTempAuthDir(async (authDir) => {
    const persistence = new WhatsAppAuthPersistence(authDir);
    const writes = Array.from({ length: 8 }, (_, i) => i);
    // Fire all of them without awaiting in between.
    const promises = writes.map((i) => persistence.saveCreds(() => ({ gen: i, blob: "y".repeat(20_000) })));
    await Promise.all(promises);

    const raw = await readFileRaw(resolveCredsPath(authDir));
    assert.ok(raw, "creds.json must exist and be fully written");
    const parsed = JSON.parse(raw!) as { gen: number };
    assert.equal(parsed.gen, 7, "the LAST enqueued write must be the final content, not an interleaved partial");
  });
});

test("hasSymlinkComponent detects a symlink anywhere between base and target, and is false for a plain nested path", async () => {
  const rootDir = await mkdtemp(path.join(tmpdir(), "empyralis-whatsapp-symlink-component-"));
  try {
    const whatsappDir = path.join(rootDir, "whatsapp");
    const realAuthDir = path.join(whatsappDir, "auth");
    await fs.mkdir(realAuthDir, { recursive: true });
    assert.equal(await hasSymlinkComponent(rootDir, realAuthDir), false);

    const elsewhereDir = await mkdtemp(path.join(tmpdir(), "empyralis-whatsapp-symlink-elsewhere-"));
    try {
      const symlinkedWhatsappDir = path.join(rootDir, "whatsapp2");
      await fs.symlink(elsewhereDir, symlinkedWhatsappDir, "dir");
      const target = path.join(symlinkedWhatsappDir, "auth");
      assert.equal(await hasSymlinkComponent(rootDir, target), true);
    } finally {
      await rm(elsewhereDir, { recursive: true, force: true });
    }
  } finally {
    await rm(rootDir, { recursive: true, force: true });
  }
});
