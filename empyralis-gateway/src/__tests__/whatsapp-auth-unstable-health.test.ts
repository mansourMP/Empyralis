import { promises as fs } from "fs";
import { mkdtemp, rm } from "fs/promises";
import { tmpdir } from "os";
import path from "path";
import test from "node:test";
import assert from "node:assert/strict";

import { WhatsAppPersonalRuntime } from "../channels/whatsapp/runtime";
import { GatewayStateDb } from "../state/db";
import { readFileRaw, resolveCredsBackupPath, resolveCredsPath } from "../channels/whatsapp/auth-persistence";

function buildMockAdapter(loadAuthStateImpl?: (folder: string) => Promise<any>) {
  let capturedCredsUpdateHandler: ((payload?: any) => void | Promise<void>) | null = null;
  const socket = {
    ev: {
      on: (eventName: string, handler: (payload: any) => void | Promise<void>) => {
        if (eventName === "creds.update") {
          capturedCredsUpdateHandler = handler;
        }
      },
    },
    sendMessage: async () => undefined,
    logout: async () => undefined,
    end: () => undefined,
  };
  const adapter = {
    loadAuthState:
      loadAuthStateImpl ??
      (async () => ({
        state: { creds: { registered: false } },
        saveCreds: async () => undefined,
      })),
    createSocket: () => socket,
    disconnectReason: { loggedOut: 401, restartRequired: 515 },
    browserDescriptor: () => ["Empyralis", "Chrome", "1.0"],
    fetchWaWebVersion: async () => undefined,
    // Deliberately NOT setting credsCodec -- proves runtime.ts falls back to
    // DEFAULT_CREDS_CODEC gracefully for adapters that don't provide one
    // (matches how the pre-existing test mocks in this suite are built).
  };
  return {
    adapter,
    getCapturedCredsUpdateHandler: () => capturedCredsUpdateHandler,
  };
}

test("getHealthSnapshot() reports 'unstable' while a creds write is in flight and reverts once it settles", async () => {
  const rootDir = await mkdtemp(path.join(tmpdir(), "empyralis-whatsapp-unstable-health-"));
  try {
    const { adapter, getCapturedCredsUpdateHandler } = buildMockAdapter(async () => ({
      state: { creds: { registered: true, me: { id: "owner@s.whatsapp.net" }, blob: "z".repeat(300_000) } },
      saveCreds: async () => undefined,
    }));
    const runtime = new WhatsAppPersonalRuntime(new GatewayStateDb(rootDir), { adapter: adapter as any });

    await (runtime as any).connectSocketInternal();
    const before = await runtime.getHealthSnapshot();
    assert.notEqual(before.status, "unstable", "must not be unstable before any creds write has happened");

    const credsUpdateHandler = getCapturedCredsUpdateHandler();
    assert.ok(credsUpdateHandler, "creds.update handler should have been registered on the socket");

    // Fire the handler and immediately race a health snapshot against it,
    // without awaiting the handler first -- both start their async work in
    // the same tick. The write path (backup-check + atomic write: several
    // lstat/open/writeFile/fsync/rename/chmod/fsync-parent-dir calls,
    // including two fsyncs) is structurally heavier than the health
    // snapshot's read path (one small JSON read, zero fsyncs), so it
    // reliably outlasts it -- the same class of timing assumption the
    // pre-existing whatsapp-disconnect-race.test.ts already relies on.
    const savePromise = credsUpdateHandler();
    const duringSnapshot = await runtime.getHealthSnapshot();
    assert.equal(duringSnapshot.status, "unstable", "must report unstable while the creds write is in flight");
    assert.equal(duringSnapshot.connected, false, "must not confidently claim connected mid-write");
    assert.deepEqual(duringSnapshot.issues, ["whatsapp_personal_auth_write_in_flight"]);

    await savePromise;
    const afterSnapshot = await runtime.getHealthSnapshot();
    assert.notEqual(afterSnapshot.status, "unstable", "must clear once the write settles");

    // And the write itself must have actually landed correctly.
    const raw = await readFileRaw(resolveCredsPath((runtime as any).sessionStore.authStateDir()));
    assert.ok(raw);
    assert.equal((JSON.parse(raw!) as { me: { id: string } }).me.id, "owner@s.whatsapp.net");
  } finally {
    await rm(rootDir, { recursive: true, force: true });
  }
});

test("a corrupted creds.json is restored from backup BEFORE Baileys ever reads it, end-to-end through connectSocketInternal()", async () => {
  const rootDir = await mkdtemp(path.join(tmpdir(), "empyralis-whatsapp-unstable-restore-e2e-"));
  try {
    const authDir = path.join(rootDir, "whatsapp", "auth");
    await fs.mkdir(authDir, { recursive: true });
    await fs.writeFile(
      resolveCredsBackupPath(authDir),
      JSON.stringify({ me: { id: "good@s.whatsapp.net" }, registered: true }),
      "utf8",
    );
    // Truncated/corrupt -- not valid JSON.
    await fs.writeFile(resolveCredsPath(authDir), '{"me":{"id":"broke', "utf8");

    // This mock's loadAuthState reads creds.json straight off disk, exactly
    // like Baileys' real useMultiFileAuthState does -- if restoreFromBackupIfNeeded()
    // did NOT run first, JSON.parse below throws and the test fails loudly,
    // proving the ordering rather than just asserting a mocked-out value.
    const { adapter } = buildMockAdapter(async (folder: string) => {
      const raw = await fs.readFile(path.join(folder, "creds.json"), "utf8");
      return { state: { creds: JSON.parse(raw) }, saveCreds: async () => undefined };
    });
    const runtime = new WhatsAppPersonalRuntime(new GatewayStateDb(rootDir), { adapter: adapter as any });

    await (runtime as any).connectSocketInternal();

    const bundle = (runtime as any).authBundle;
    assert.ok(bundle, "authBundle must have been set");
    assert.equal(
      bundle.state.creds.me.id,
      "good@s.whatsapp.net",
      "Baileys must have been handed the RESTORED identity, not a blank/corrupt one",
    );

    const onDisk = await readFileRaw(resolveCredsPath(authDir));
    assert.ok(onDisk);
    assert.equal((JSON.parse(onDisk!) as { me: { id: string } }).me.id, "good@s.whatsapp.net");
  } finally {
    await rm(rootDir, { recursive: true, force: true });
  }
});

test("the real (non-mocked) getAdapter() exposes a credsCodec that round-trips Buffer fields exactly like Baileys' own BufferJSON reviver expects", async () => {
  const rootDir = await mkdtemp(path.join(tmpdir(), "empyralis-whatsapp-real-adapter-codec-"));
  try {
    // No adapter injected -- forces the real dynamic-import path in
    // getAdapter(), which pulls BufferJSON from the actual installed
    // @whiskeysockets/baileys package.
    const runtime = new WhatsAppPersonalRuntime(new GatewayStateDb(rootDir));
    const adapter = await (runtime as any).getAdapter();
    assert.ok(adapter.credsCodec, "real adapter must expose a credsCodec");

    const fixture = {
      registered: true,
      noiseKey: { public: Buffer.from("pubkeybytes"), private: Buffer.from("privkeybytes") },
    };
    const serialized = adapter.credsCodec.stringify(fixture);
    // Confirms we're using Baileys' actual base64-string Buffer shape, not
    // Node's default numeric-array Buffer#toJSON() shape (which Baileys'
    // own reviver does NOT revive back into a Buffer -- using the wrong
    // codec would silently corrupt creds on save).
    assert.ok(serialized.includes('"type":"Buffer"'));
    assert.ok(!/"data":\[/.test(serialized), "must not use the numeric-array Buffer shape");

    const revived = adapter.credsCodec.parse(serialized) as typeof fixture;
    assert.ok(Buffer.isBuffer(revived.noiseKey.public), "public key must revive back into a real Buffer");
    assert.ok(Buffer.isBuffer(revived.noiseKey.private), "private key must revive back into a real Buffer");
    assert.equal(revived.noiseKey.public.toString(), "pubkeybytes");
    assert.equal(revived.noiseKey.private.toString(), "privkeybytes");
  } finally {
    await rm(rootDir, { recursive: true, force: true });
  }
});
