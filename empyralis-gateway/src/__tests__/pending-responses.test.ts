import { mkdtemp, rm } from "fs/promises";
import { tmpdir } from "os";
import path from "path";
import test from "node:test";
import assert from "node:assert/strict";

import { GatewayStateDb } from "../state/db";
import { PendingResponseQueue } from "../state/pending-responses";

test("a queued response survives a fresh GatewayStateDb instance (disk-durable, not in-memory)", async () => {
  const rootDir = await mkdtemp(path.join(tmpdir(), "empyralis-gateway-pending-responses-"));
  try {
    const firstDb = new GatewayStateDb(rootDir);
    const firstQueue = new PendingResponseQueue(firstDb);
    await firstQueue.enqueue("req-1", { kind: "response", id: "req-1", ok: true, payload: { text: "hi" } });

    // Simulate the process restarting — a brand new GatewayStateDb over the
    // same directory, same spirit as the outbox/journal e2e test.
    const secondDb = new GatewayStateDb(rootDir);
    const secondQueue = new PendingResponseQueue(secondDb);
    const items = await secondQueue.list();
    assert.equal(items.length, 1);
    assert.equal(items[0].requestId, "req-1");
    assert.deepEqual(items[0].frame, { kind: "response", id: "req-1", ok: true, payload: { text: "hi" } });
  } finally {
    await rm(rootDir, { recursive: true, force: true });
  }
});

test("flush delivers every queued response and removes only the ones that succeeded", async () => {
  const rootDir = await mkdtemp(path.join(tmpdir(), "empyralis-gateway-pending-responses-"));
  try {
    const db = new GatewayStateDb(rootDir);
    const queue = new PendingResponseQueue(db);
    await queue.enqueue("req-1", { id: "req-1" });
    await queue.enqueue("req-2", { id: "req-2" });
    await queue.enqueue("req-3", { id: "req-3" });

    const delivered: string[] = [];
    // req-2 fails (simulating the connection dying mid-flush) — flush should
    // stop there rather than skip it and deliver req-3 out of order.
    await queue.flush(async (frame) => {
      const id = String((frame as { id: string }).id);
      if (id === "req-2") {
        throw new Error("socket closed mid-flush");
      }
      delivered.push(id);
    });

    assert.deepEqual(delivered, ["req-1"]);
    const remaining = await queue.list();
    assert.deepEqual(
      remaining.map((item) => item.requestId).sort(),
      ["req-2", "req-3"],
    );
  } finally {
    await rm(rootDir, { recursive: true, force: true });
  }
});

test("re-queuing the same requestId replaces the earlier entry instead of duplicating it", async () => {
  const rootDir = await mkdtemp(path.join(tmpdir(), "empyralis-gateway-pending-responses-"));
  try {
    const db = new GatewayStateDb(rootDir);
    const queue = new PendingResponseQueue(db);
    await queue.enqueue("req-1", { id: "req-1", attempt: 1 });
    await queue.enqueue("req-1", { id: "req-1", attempt: 2 });
    const items = await queue.list();
    assert.equal(items.length, 1);
    assert.deepEqual(items[0].frame, { id: "req-1", attempt: 2 });
  } finally {
    await rm(rootDir, { recursive: true, force: true });
  }
});
