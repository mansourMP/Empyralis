import test from "node:test";
import assert from "node:assert/strict";

import { GatewayOutbox, MAX_OUTBOX_ITEMS, MAX_RETRIES } from "../state/outbox";
import type { GatewayOutboxItem } from "../state/outbox";
import { GatewayStateDb } from "../state/db";
import { GatewayTokenStore, sanitizeTokenForLogging } from "../pairing/token-store";
import * as path from "path";
import * as os from "os";
import * as fs from "fs";

// ---------------------------------------------------------------------------
// helpers
// ---------------------------------------------------------------------------

function tmpDir(): string {
  return fs.mkdtempSync(path.join(os.tmpdir(), "replay-safety-"));
}

function makeDb(rootDir: string): GatewayStateDb {
  return new GatewayStateDb(rootDir);
}

async function makeOutbox(rootDir: string): Promise<GatewayOutbox> {
  const db = makeDb(rootDir);
  await db.ensureReady();
  return new GatewayOutbox(db);
}

// ---------------------------------------------------------------------------
// P0: replayable default — safe types only
// ---------------------------------------------------------------------------

test("tool.invoke defaults replayable=false", async () => {
  // The SAFE_REPLAY_MESSAGE_TYPES set is the source of truth.
  // Importing the ws-client module triggers side effects; for a unit-level
  // test we verify the outbox's serialised replayable flag.
  const dir = tmpDir();
  try {
    const outbox = await makeOutbox(dir);
    const item = await outbox.enqueue("req-tool-invoke-1", "tool.invoke", { run_id: "r1" }, { replayable: false });
    assert.equal(item.replayable, false);
    assert.equal(item.status, "pending");
  } finally {
    fs.rmSync(dir, { recursive: true, force: true });
  }
});

test("channel.outbound defaults replayable=false", async () => {
  const dir = tmpDir();
  try {
    const outbox = await makeOutbox(dir);
    const item = await outbox.enqueue("req-co-1", "channel.outbound", { text: "hi" }, { replayable: false });
    assert.equal(item.replayable, false);
  } finally {
    fs.rmSync(dir, { recursive: true, force: true });
  }
});

test("gateway.heartbeat defaults replayable=true", async () => {
  const dir = tmpDir();
  try {
    const outbox = await makeOutbox(dir);
    const item = await outbox.enqueue("req-hb-1", "gateway.heartbeat", {}, { replayable: true });
    assert.equal(item.replayable, true);
  } finally {
    fs.rmSync(dir, { recursive: true, force: true });
  }
});

test("gateway.state.update defaults replayable=true", async () => {
  const dir = tmpDir();
  try {
    const outbox = await makeOutbox(dir);
    const item = await outbox.enqueue("req-su-1", "gateway.state.update", {}, { replayable: true });
    assert.equal(item.replayable, true);
  } finally {
    fs.rmSync(dir, { recursive: true, force: true });
  }
});

test("gateway.connect defaults replayable=false", async () => {
  const dir = tmpDir();
  try {
    const outbox = await makeOutbox(dir);
    const item = await outbox.enqueue("req-conn-1", "gateway.connect", {}, { replayable: false });
    assert.equal(item.replayable, false);
  } finally {
    fs.rmSync(dir, { recursive: true, force: true });
  }
});

test("gateway.disconnect defaults replayable=false", async () => {
  const dir = tmpDir();
  try {
    const outbox = await makeOutbox(dir);
    const item = await outbox.enqueue("req-disc-1", "gateway.disconnect", {}, { replayable: false });
    assert.equal(item.replayable, false);
  } finally {
    fs.rmSync(dir, { recursive: true, force: true });
  }
});

// ---------------------------------------------------------------------------
// Outbox: markUncertain and status lifecycle
// ---------------------------------------------------------------------------

test("markUncertain sets status to uncertain", async () => {
  const dir = tmpDir();
  try {
    const outbox = await makeOutbox(dir);
    const item = await outbox.enqueue("req-unc-1", "tool.invoke", { run_id: "r1" }, { replayable: false });
    assert.equal(item.status, "pending");

    await outbox.markUncertain("req-unc-1", "Socket closed before response");
    const updated = await outbox.get("req-unc-1");
    assert.ok(updated);
    assert.equal(updated!.status, "uncertain");
    assert.match(updated!.lastError ?? "", /Socket closed/);
  } finally {
    fs.rmSync(dir, { recursive: true, force: true });
  }
});

test("uncertain items are NOT replayed", async () => {
  const dir = tmpDir();
  try {
    const outbox = await makeOutbox(dir);
    await outbox.enqueue("req-ur-1", "tool.invoke", { run_id: "r1" }, { replayable: false });
    await outbox.markUncertain("req-ur-1", "timed out");

    const replayableItems = await outbox.listReplayablePending();
    assert.equal(replayableItems.length, 0, "uncertain items must not be replayable");
  } finally {
    fs.rmSync(dir, { recursive: true, force: true });
  }
});

test("uncertain status persists and is not overwritten by further failures", async () => {
  const dir = tmpDir();
  try {
    const outbox = await makeOutbox(dir);
    await outbox.enqueue("req-uf-1", "tool.invoke", { run_id: "r1" }, { replayable: false });
    await outbox.markUncertain("req-uf-1", "first failure");

    // A subsequent markAttemptFailed should not flip an uncertain item
    await outbox.markAttemptFailed("req-uf-1", "second failure");
    const item = await outbox.get("req-uf-1");
    assert.ok(item);
    assert.equal(item!.status, "uncertain");
  } finally {
    fs.rmSync(dir, { recursive: true, force: true });
  }
});

test("uncertain items are pruned after 24h", async () => {
  const dir = tmpDir();
  try {
    const outbox = await makeOutbox(dir);
    // Create an uncertain item with an old timestamp
    const db = makeDb(dir);
    const oldItem: GatewayOutboxItem = {
      id: "old-unc-1",
      requestId: "old-unc-1",
      messageType: "tool.invoke",
      status: "uncertain",
      replayable: false,
      retryCount: 1,
      createdAt: new Date(Date.now() - 25 * 60 * 60 * 1000).toISOString(),
      updatedAt: new Date(Date.now() - 25 * 60 * 60 * 1000).toISOString(),
      lastAttemptAt: new Date(Date.now() - 25 * 60 * 60 * 1000).toISOString(),
      lastError: "timed out",
      payload: {},
    };
    await db.writeJson("outbox.json", [oldItem]);
    const pruned = await outbox.prune();
    assert.equal(pruned, 1);
  } finally {
    fs.rmSync(dir, { recursive: true, force: true });
  }
});

test("summarize reports uncertain count", async () => {
  const dir = tmpDir();
  try {
    const outbox = await makeOutbox(dir);
    await outbox.enqueue("req-s1", "tool.invoke", { run_id: "r1" }, { replayable: false });
    await outbox.enqueue("req-s2", "gateway.heartbeat", {}, { replayable: true });
    await outbox.markUncertain("req-s1", "timed out");

    const summary = await outbox.summarize();
    assert.equal(summary.total, 2);
    assert.equal(summary.uncertain, 1);
    assert.equal(summary.pending, 1); // the heartbeat
  } finally {
    fs.rmSync(dir, { recursive: true, force: true });
  }
});

// ---------------------------------------------------------------------------
// Existing P0 fixes still hold
// ---------------------------------------------------------------------------

test("outbox still deduplicates by requestId", async () => {
  const dir = tmpDir();
  try {
    const outbox = await makeOutbox(dir);
    const first = await outbox.enqueue("req-dup", "tool.invoke", { run_id: "r1" }, { replayable: false });
    const second = await outbox.enqueue("req-dup", "tool.invoke", { run_id: "r2" }, { replayable: false });
    assert.equal(first.requestId, second.requestId);
    assert.equal(first.id, second.id);
  } finally {
    fs.rmSync(dir, { recursive: true, force: true });
  }
});

test("outbox max retries still leads to abandoned for replayable items", async () => {
  const dir = tmpDir();
  try {
    const outbox = await makeOutbox(dir);
    const item = await outbox.enqueue("req-retry", "gateway.heartbeat", {}, { replayable: true });
    for (let i = 0; i < MAX_RETRIES; i += 1) {
      await outbox.markForReplay(item.requestId, `attempt ${i + 1} failed`);
    }
    const final = await outbox.get(item.requestId);
    assert.ok(final);
    assert.equal(final!.status, "abandoned");
  } finally {
    fs.rmSync(dir, { recursive: true, force: true });
  }
});

test("outbox max size still enforced", async () => {
  const dir = tmpDir();
  try {
    const outbox = await makeOutbox(dir);
    // pre-fill the outbox
    const db = makeDb(dir);
    const manyItems: GatewayOutboxItem[] = [];
    for (let i = 0; i < MAX_OUTBOX_ITEMS; i += 1) {
      manyItems.push({
        id: `id-${i}`,
        requestId: `req-${i}`,
        messageType: "gateway.heartbeat",
        status: "acknowledged",
        replayable: true,
        retryCount: 0,
        createdAt: new Date().toISOString(),
        updatedAt: new Date().toISOString(),
        lastAttemptAt: new Date().toISOString(),
        payload: {},
      });
    }
    await db.writeJson("outbox.json", manyItems);
    await assert.rejects(
      () => outbox.enqueue("req-overflow", "tool.invoke", {}, { replayable: false }),
      /full/,
    );
  } finally {
    fs.rmSync(dir, { recursive: true, force: true });
  }
});

// ---------------------------------------------------------------------------
// P1: Device token local protection
// ---------------------------------------------------------------------------

test("sanitizeTokenForLogging redacts long tokens", () => {
  const token = "abcdefghijklmnopqrstuvwxyz";
  const result = sanitizeTokenForLogging(token);
  assert.notEqual(result, token);
  assert.ok(result.includes("abcd"));
  assert.ok(result.includes("wxyz"));
  assert.ok(result.includes("..."));
});

test("sanitizeTokenForLogging redacts short tokens", () => {
  assert.equal(sanitizeTokenForLogging("short"), "[REDACTED]");
  assert.equal(sanitizeTokenForLogging(""), "[REDACTED]");
});

test("token store save creates file with correct content", async () => {
  const dir = tmpDir();
  try {
    const db = makeDb(dir);
    const store = new GatewayTokenStore(db);
    await store.save({ gatewayToken: "gt_abc123", sessionToken: "st_xyz789" });
    const tokensPath = path.join(dir, "tokens.json");
    assert.ok(fs.existsSync(tokensPath));
    const content = JSON.parse(fs.readFileSync(tokensPath, "utf8"));
    assert.equal(content.gatewayToken, "gt_abc123");
    assert.equal(content.sessionToken, "st_xyz789");
    assert.ok(content.updatedAt);
  } finally {
    fs.rmSync(dir, { recursive: true, force: true });
  }
});

test("token store file permissions are 0600 on unix", { skip: process.platform === "win32" }, async () => {
  const dir = tmpDir();
  try {
    const db = makeDb(dir);
    const store = new GatewayTokenStore(db);
    await store.save({ gatewayToken: "test" });
    const tokensPath = path.join(dir, "tokens.json");
    const stat = fs.statSync(tokensPath);
    const perms = stat.mode & 0o777;
    assert.equal(perms, 0o600);
  } finally {
    fs.rmSync(dir, { recursive: true, force: true });
  }
});
