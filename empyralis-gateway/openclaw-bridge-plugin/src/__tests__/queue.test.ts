import { test } from "node:test";
import assert from "node:assert/strict";
import * as os from "node:os";
import * as path from "node:path";
import * as fs from "node:fs/promises";
import * as crypto from "node:crypto";

import { BoundedRetryQueue } from "../queue.js";

async function tmpQueueFile(): Promise<string> {
  const dir = await fs.mkdtemp(path.join(os.tmpdir(), "empyralis-bridge-queue-test-"));
  return path.join(dir, "queue.json");
}

test("enqueue then flushOnce delivers items in FIFO order", async () => {
  const filePath = await tmpQueueFile();
  const delivered: string[] = [];
  const queue = new BoundedRetryQueue<string>({
    filePath,
    maxItems: 10,
    maxAttempts: 3,
    minBackoffMs: 10,
    maxBackoffMs: 100,
    send: async (payload) => {
      delivered.push(payload);
    },
  });
  await queue.enqueue("a");
  await queue.enqueue("b");
  await queue.enqueue("c");
  const result = await queue.flushOnce();
  assert.equal(result.sent, 3);
  assert.equal(result.remaining, 0);
  assert.deepEqual(delivered, ["a", "b", "c"]);
});

test("drop-oldest fires and is observable when the queue is full — never silent", async () => {
  const filePath = await tmpQueueFile();
  const dropped: string[] = [];
  const logged: string[] = [];
  const queue = new BoundedRetryQueue<string>({
    filePath,
    maxItems: 2,
    maxAttempts: 3,
    minBackoffMs: 10,
    maxBackoffMs: 100,
    send: async () => {
      throw new Error("endpoint down");
    },
    onDropped: (item) => dropped.push(item.payload),
    log: (message) => logged.push(message),
  });
  await queue.enqueue("a");
  await queue.enqueue("b");
  await queue.enqueue("c"); // should evict "a"
  assert.equal(queue.size(), 2);
  assert.deepEqual(dropped, ["a"]);
  assert.ok(logged.some((line) => line.includes("dropped oldest")));
});

test("failed sends increment attempts and back off instead of retrying immediately", async () => {
  const filePath = await tmpQueueFile();
  let calls = 0;
  const queue = new BoundedRetryQueue<string>({
    filePath,
    maxItems: 10,
    maxAttempts: 5,
    minBackoffMs: 1000,
    maxBackoffMs: 60_000,
    send: async () => {
      calls += 1;
      throw new Error("network error");
    },
  });
  await queue.enqueue("x");
  const first = await queue.flushOnce(() => 0);
  assert.equal(first.sent, 0);
  assert.equal(first.remaining, 1);
  assert.equal(calls, 1);

  // Immediately flushing again (same clock) must not retry yet — still in backoff.
  const second = await queue.flushOnce(() => 0);
  assert.equal(calls, 1, "must not re-attempt before the backoff window elapses");
  assert.equal(second.remaining, 1);

  // Advance the clock past the backoff window: retry happens.
  const third = await queue.flushOnce(() => 5000);
  assert.equal(calls, 2);
  assert.equal(third.remaining, 1);
});

test("gives up after maxAttempts and reports it, rather than retrying forever", async () => {
  const filePath = await tmpQueueFile();
  const givenUp: string[] = [];
  let calls = 0;
  const queue = new BoundedRetryQueue<string>({
    filePath,
    maxItems: 10,
    maxAttempts: 2,
    minBackoffMs: 0,
    maxBackoffMs: 0,
    send: async () => {
      calls += 1;
      throw new Error("still down");
    },
    onGiveUp: (item) => givenUp.push(item.payload),
  });
  await queue.enqueue("y");
  await queue.flushOnce(() => 0);
  await queue.flushOnce(() => 0);
  assert.equal(calls, 2);
  assert.deepEqual(givenUp, ["y"]);
  assert.equal(queue.size(), 0);
});

test("queue survives a reload from disk (durable across process restart)", async () => {
  const filePath = await tmpQueueFile();
  const queueA = new BoundedRetryQueue<string>({
    filePath,
    maxItems: 10,
    maxAttempts: 3,
    minBackoffMs: 10,
    maxBackoffMs: 100,
    send: async () => {
      throw new Error("down");
    },
  });
  await queueA.enqueue("persisted-item");
  await queueA.flushOnce(); // fails, stays queued, persisted to disk (using the real wall clock for lastAttemptAtMs)

  const delivered: string[] = [];
  const queueB = new BoundedRetryQueue<string>({
    filePath,
    maxItems: 10,
    maxAttempts: 3,
    minBackoffMs: 10,
    maxBackoffMs: 100,
    send: async (payload) => {
      delivered.push(payload);
    },
  });
  await queueB.load();
  assert.equal(queueB.size(), 1);
  // queueA persisted lastAttemptAtMs on the real wall clock; advance relative
  // to that same clock (not an arbitrary small fake epoch) so the backoff
  // window has genuinely elapsed.
  const result = await queueB.flushOnce(() => Date.now() + 1_000_000);
  assert.deepEqual(delivered, ["persisted-item"]);
  assert.equal(result.remaining, 0);
});

test("a missing queue file loads as empty rather than throwing", async () => {
  const dir = await fs.mkdtemp(path.join(os.tmpdir(), "empyralis-bridge-queue-test-"));
  const filePath = path.join(dir, `${crypto.randomUUID()}.json`);
  const queue = new BoundedRetryQueue<string>({
    filePath,
    maxItems: 10,
    maxAttempts: 3,
    minBackoffMs: 10,
    maxBackoffMs: 100,
    send: async () => {},
  });
  await queue.load();
  assert.equal(queue.size(), 0);
});
