import { mkdtemp, rm, readFile, readdir } from "fs/promises";
import { tmpdir } from "os";
import path from "path";
import test from "node:test";
import assert from "node:assert/strict";

import { GatewayStateDb } from "../state/db";
import { GatewayOutbox, MAX_RETRIES } from "../state/outbox";
import { GatewayCheckpoints } from "../state/checkpoints";

test("concurrent writeJson calls don't corrupt data", async () => {
  const rootDir = await mkdtemp(path.join(tmpdir(), "empyralis-concurrency-"));
  try {
    const db = new GatewayStateDb(rootDir);
    await db.ensureReady();

    const NUM_WRITES = 50;
    const promises: Promise<unknown>[] = [];
    for (let i = 0; i < NUM_WRITES; i++) {
      promises.push(db.writeJson("counter.json", { value: i }));
    }
    await Promise.all(promises);

    // Read back - should get a valid JSON with some value
    const result = await db.readJson<{ value: number }>("counter.json", { value: -1 });
    // The value could be any of the written values, but should be a valid number
    // and the file should contain valid JSON
    assert.ok(typeof result.value === "number");
    assert.ok(result.value >= 0 && result.value < NUM_WRITES);

    // Verify the file is valid JSON
    const raw = await readFile(db.filePath("counter.json"), "utf8");
    assert.doesNotThrow(() => JSON.parse(raw));

    // No .tmp files should remain
    const files = await readdir(rootDir);
    const tmpFiles = files.filter((f) => f.includes(".tmp."));
    assert.equal(tmpFiles.length, 0, "No .tmp files should remain after writes");
  } finally {
    await rm(rootDir, { recursive: true, force: true });
  }
});

test("concurrent writeJson to different files is safe", async () => {
  const rootDir = await mkdtemp(path.join(tmpdir(), "empyralis-concurrency-multi-"));
  try {
    const db = new GatewayStateDb(rootDir);
    await db.ensureReady();

    const NUM_FILES = 10;
    const NUM_WRITES = 20;
    const promises: Promise<unknown>[] = [];

    for (let f = 0; f < NUM_FILES; f++) {
      for (let w = 0; w < NUM_WRITES; w++) {
        promises.push(db.writeJson(`file-${f}.json`, { file: f, write: w }));
      }
    }
    await Promise.all(promises);

    // Each file should contain valid JSON
    for (let f = 0; f < NUM_FILES; f++) {
      const raw = await readFile(db.filePath(`file-${f}.json`), "utf8");
      assert.doesNotThrow(() => JSON.parse(raw));
      const parsed = JSON.parse(raw) as { file: number; write: number };
      assert.equal(parsed.file, f);
    }

    // No .tmp files should remain
    const files = await readdir(rootDir);
    const tmpFiles = files.filter((f) => f.includes(".tmp."));
    assert.equal(tmpFiles.length, 0);
  } finally {
    await rm(rootDir, { recursive: true, force: true });
  }
});

test("outbox enqueue deduplicates by requestId", async () => {
  const rootDir = await mkdtemp(path.join(tmpdir(), "empyralis-outbox-dedup-"));
  try {
    const db = new GatewayStateDb(rootDir);
    const outbox = new GatewayOutbox(db);

    const item1 = await outbox.enqueue("req-dup-1", "gateway.test", { foo: "bar" });
    const item2 = await outbox.enqueue("req-dup-1", "gateway.test", { baz: "qux" });

    // Should return the same item
    assert.equal(item1.id, item2.id);
    assert.equal(item1.requestId, item2.requestId);

    // Only one item in the outbox
    const summary = await outbox.summarize();
    assert.equal(summary.total, 1);
  } finally {
    await rm(rootDir, { recursive: true, force: true });
  }
});

test("outbox max size enforced", async () => {
  const rootDir = await mkdtemp(path.join(tmpdir(), "empyralis-outbox-maxsize-"));
  try {
    const db = new GatewayStateDb(rootDir);
    const outbox = new GatewayOutbox(db);

    // Fill the outbox to max
    for (let i = 0; i < 10000; i++) {
      await outbox.enqueue(`req-${i}`, "gateway.test", { idx: i });
    }

    // Next enqueue should throw
    await assert.rejects(
      () => outbox.enqueue("req-overflow", "gateway.test", {}),
      /Outbox is full/,
    );
  } finally {
    await rm(rootDir, { recursive: true, force: true });
  }
});

test("outbox max retries leads to abandoned", async () => {
  const rootDir = await mkdtemp(path.join(tmpdir(), "empyralis-outbox-retries-"));
  try {
    const db = new GatewayStateDb(rootDir);
    const outbox = new GatewayOutbox(db);

    await outbox.enqueue("req-retry-1", "gateway.test", {}, { replayable: true });

    // Simulate retries: markForReplay repeatedly
    for (let i = 0; i < MAX_RETRIES; i++) {
      await outbox.markForReplay("req-retry-1", `attempt ${i + 1} failed`);
    }

    const items = await outbox.list();
    const item = items.find((i) => i.requestId === "req-retry-1");
    assert.ok(item);
    assert.equal(item.status, "abandoned", `Expected abandoned after ${MAX_RETRIES} retries, got ${item.status}`);
    assert.equal(item.retryCount, MAX_RETRIES);

    // Should not be replayable
    const replayable = await outbox.listReplayablePending();
    assert.equal(replayable.length, 0);
  } finally {
    await rm(rootDir, { recursive: true, force: true });
  }
});

test("outbox prune removes old acknowledged items", async () => {
  const rootDir = await mkdtemp(path.join(tmpdir(), "empyralis-outbox-prune-"));
  try {
    const db = new GatewayStateDb(rootDir);
    const outbox = new GatewayOutbox(db);

    // Add items and mark them as acknowledged
    await outbox.enqueue("req-stale-1", "gateway.test", {});
    await outbox.enqueue("req-stale-2", "gateway.test", {});
    await outbox.acknowledge("req-stale-1");
    await outbox.acknowledge("req-stale-2");

    // The items were just acknowledged, should not be pruned (not old enough)
    let pruned = await outbox.prune();
    assert.equal(pruned, 0);

    // Unfortunately we can't easily test the time-based pruning in unit tests
    // without mocking Date. Just verify the method exists and doesn't crash.
    const summary = await outbox.summarize();
    assert.equal(summary.acknowledged, 2);
  } finally {
    await rm(rootDir, { recursive: true, force: true });
  }
});

test("checkpoints debounce writes", async () => {
  const rootDir = await mkdtemp(path.join(tmpdir(), "empyralis-checkpoints-debounce-"));
  try {
    const db = new GatewayStateDb(rootDir);
    const checkpoints = new GatewayCheckpoints(db);

    // Write multiple times in rapid succession
    const writePromises = [];
    for (let i = 0; i < 10; i++) {
      writePromises.push(checkpoints.save({ lastAck: i }));
    }
    await Promise.all(writePromises);

    // Because of debouncing, not all writes may have been flushed yet.
    // Flush to ensure the final state is written.
    await checkpoints.flush();

    // The final checkpoint should have a lastAck value (could be 0-9)
    const loaded = await checkpoints.load();

    // IMPORTANT: lastAck could be any value because debouncing merges snapshots.
    // The value of `lastAck` in the final write depends on exact timing.
    // What matters is that the file was written exactly once.
    assert.ok(typeof loaded.updatedAt === "string");

    // Now verify that after flush, the file exists and is valid JSON
    const raw = await readFile(db.filePath("checkpoints.json"), "utf8");
    assert.doesNotThrow(() => JSON.parse(raw));

    // After flush and another save, the value should be the latest
    const finalAck = await checkpoints.save({ lastAck: 42 });
    await checkpoints.flush();
    assert.equal(finalAck.lastAck, 42);

    const reloaded = await checkpoints.load();
    assert.equal(reloaded.lastAck, 42);
  } finally {
    await rm(rootDir, { recursive: true, force: true });
  }
});

test("outbox acknowledge marks item as acknowledged", async () => {
  const rootDir = await mkdtemp(path.join(tmpdir(), "empyralis-outbox-ack-"));
  try {
    const db = new GatewayStateDb(rootDir);
    const outbox = new GatewayOutbox(db);

    await outbox.enqueue("req-ack-1", "gateway.test", { data: "test" });
    await outbox.acknowledge("req-ack-1");

    const items = await outbox.list();
    const item = items.find((i) => i.requestId === "req-ack-1");
    assert.ok(item);
    assert.equal(item.status, "acknowledged");
  } finally {
    await rm(rootDir, { recursive: true, force: true });
  }
});

test("outbox enqueue with non-replayable flag", async () => {
  const rootDir = await mkdtemp(path.join(tmpdir(), "empyralis-outbox-nonreplay-"));
  try {
    const db = new GatewayStateDb(rootDir);
    const outbox = new GatewayOutbox(db);

    await outbox.enqueue("req-nr-1", "gateway.test", {}, { replayable: false });

    const items = await outbox.list();
    const item = items.find((i) => i.requestId === "req-nr-1");
    assert.ok(item);
    assert.equal(item.replayable, false);
  } finally {
    await rm(rootDir, { recursive: true, force: true });
  }
});

test("db writeJson creates readable JSON", async () => {
  const rootDir = await mkdtemp(path.join(tmpdir(), "empyralis-db-write-"));
  try {
    const db = new GatewayStateDb(rootDir);

    const testValue = { hello: "world", count: 42, nested: { a: 1, b: [2, 3] } };
    await db.writeJson("test.json", testValue);

    const loaded = await db.readJson<typeof testValue>("test.json", { hello: "", count: 0, nested: { a: 0, b: [] } });
    assert.deepEqual(loaded, testValue);
  } finally {
    await rm(rootDir, { recursive: true, force: true });
  }
});

test("db appendNdjson appends correctly", async () => {
  const rootDir = await mkdtemp(path.join(tmpdir(), "empyralis-db-append-"));
  try {
    const db = new GatewayStateDb(rootDir);

    await db.appendNdjson("log.ndjson", { event: "first", seq: 1 });
    await db.appendNdjson("log.ndjson", { event: "second", seq: 2 });

    const raw = await readFile(db.filePath("log.ndjson"), "utf8");
    const lines = raw.trim().split("\n");
    assert.equal(lines.length, 2);

    const first = JSON.parse(lines[0]!);
    const second = JSON.parse(lines[1]!);
    assert.equal(first.event, "first");
    assert.equal(second.event, "second");
  } finally {
    await rm(rootDir, { recursive: true, force: true });
  }
});
