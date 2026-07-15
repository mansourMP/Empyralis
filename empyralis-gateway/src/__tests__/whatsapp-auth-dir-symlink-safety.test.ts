import { promises as fs } from "fs";
import { mkdtemp, rm } from "fs/promises";
import { tmpdir } from "os";
import path from "path";
import test from "node:test";
import assert from "node:assert/strict";

import { WhatsAppSessionStore } from "../channels/whatsapp/session-store";
import { GatewayStateDb } from "../state/db";

test("clearAuthStateDir refuses to delete when the auth dir itself is a symlink, and leaves the real target untouched", async () => {
  const rootDir = await mkdtemp(path.join(tmpdir(), "empyralis-whatsapp-symlink-safety-"));
  const elsewhereDir = await mkdtemp(path.join(tmpdir(), "empyralis-whatsapp-symlink-elsewhere-"));
  try {
    const sentinelPath = path.join(elsewhereDir, "sentinel.txt");
    await fs.writeFile(sentinelPath, "do-not-delete", "utf8");

    const whatsappDir = path.join(rootDir, "whatsapp");
    await fs.mkdir(whatsappDir, { recursive: true });
    const authDirPath = path.join(whatsappDir, "auth");
    await fs.symlink(elsewhereDir, authDirPath, "dir");

    const store = new WhatsAppSessionStore(new GatewayStateDb(rootDir));
    assert.equal(store.authStateDir(), authDirPath);

    await store.clearAuthStateDir();

    const sentinelStillThere = await fs.readFile(sentinelPath, "utf8");
    assert.equal(sentinelStillThere, "do-not-delete", "the symlink TARGET must never be touched");
    const linkStat = await fs.lstat(authDirPath);
    assert.ok(linkStat.isSymbolicLink(), "the symlink itself must be left exactly as it was, not unlinked either");
  } finally {
    await rm(rootDir, { recursive: true, force: true });
    await rm(elsewhereDir, { recursive: true, force: true });
  }
});

test("clearAuthStateDir refuses to delete when an ancestor (whatsapp/) is a symlink, not just the leaf auth dir", async () => {
  const rootDir = await mkdtemp(path.join(tmpdir(), "empyralis-whatsapp-symlink-ancestor-"));
  const elsewhereDir = await mkdtemp(path.join(tmpdir(), "empyralis-whatsapp-symlink-ancestor-elsewhere-"));
  try {
    const realAuthDir = path.join(elsewhereDir, "auth");
    await fs.mkdir(realAuthDir, { recursive: true });
    const sentinelPath = path.join(realAuthDir, "sentinel.txt");
    await fs.writeFile(sentinelPath, "do-not-delete", "utf8");

    // whatsapp/ itself (the PARENT of auth/, not auth/ itself) is the symlink.
    const whatsappDirPath = path.join(rootDir, "whatsapp");
    await fs.symlink(elsewhereDir, whatsappDirPath, "dir");

    const store = new WhatsAppSessionStore(new GatewayStateDb(rootDir));
    await store.clearAuthStateDir();

    const sentinelStillThere = await fs.readFile(sentinelPath, "utf8");
    assert.equal(sentinelStillThere, "do-not-delete");
  } finally {
    await rm(rootDir, { recursive: true, force: true });
    await rm(elsewhereDir, { recursive: true, force: true });
  }
});

test("clearAuthStateDir still deletes a genuine (non-symlinked) auth dir normally", async () => {
  const rootDir = await mkdtemp(path.join(tmpdir(), "empyralis-whatsapp-symlink-positive-control-"));
  try {
    const store = new WhatsAppSessionStore(new GatewayStateDb(rootDir));
    const authDir = await store.ensureAuthStateDir();
    await fs.writeFile(path.join(authDir, "creds.json"), "{}", "utf8");

    await store.clearAuthStateDir();

    await assert.rejects(() => fs.lstat(authDir), /ENOENT/, "a real auth dir must still be removed");
  } finally {
    await rm(rootDir, { recursive: true, force: true });
  }
});

test("clearAuthStateDir is a no-op (does not throw) when the auth dir was never created", async () => {
  const rootDir = await mkdtemp(path.join(tmpdir(), "empyralis-whatsapp-symlink-noop-"));
  try {
    const store = new WhatsAppSessionStore(new GatewayStateDb(rootDir));
    await store.clearAuthStateDir();
  } finally {
    await rm(rootDir, { recursive: true, force: true });
  }
});
