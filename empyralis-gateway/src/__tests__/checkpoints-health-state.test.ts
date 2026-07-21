import { mkdtemp, rm } from "fs/promises";
import { tmpdir } from "os";
import path from "path";
import test from "node:test";
import assert from "node:assert/strict";

import { GatewayCheckpoints, GatewayHealthState } from "../state/checkpoints";
import { GatewayStateDb } from "../state/db";

test("persists explicit gateway health states in checkpoints", async () => {
  const rootDir = await mkdtemp(path.join(tmpdir(), "empyralis-gateway-checkpoints-"));
  try {
    const checkpoints = new GatewayCheckpoints(new GatewayStateDb(rootDir));
    const expectedStates: GatewayHealthState[] = [
      "online",
      "offline",
      "reconnecting",
      "degraded",
    ];

    for (const healthState of expectedStates) {
      const saved = await checkpoints.saveHealthState(healthState, {
        pendingOutboxCount: expectedStates.indexOf(healthState),
      });
      // Flush debounced write so load() sees the persisted value
      await checkpoints.flush();
      const loaded = await checkpoints.load();

      assert.equal(saved.healthState, healthState);
      assert.equal(loaded.healthState, healthState);
      assert.equal(loaded.pendingOutboxCount, expectedStates.indexOf(healthState));
      assert.equal(typeof loaded.updatedAt, "string");
    }
  } finally {
    await rm(rootDir, { recursive: true, force: true });
  }
});

test("currentHealthState() defaults to offline and reflects the last saved state synchronously", async () => {
  const rootDir = await mkdtemp(path.join(tmpdir(), "empyralis-gateway-checkpoints-"));
  try {
    const checkpoints = new GatewayCheckpoints(new GatewayStateDb(rootDir));

    // Nothing has ever confirmed a live connection yet — must not default
    // to "online" (the exact dishonesty this fix removes from the
    // heartbeat payload; see cloud/heartbeat-payload.ts).
    assert.equal(checkpoints.currentHealthState(), "offline");

    await checkpoints.saveHealthState("online");
    // Synchronously visible immediately after the save() promise resolves,
    // even though the underlying disk write is still debounced (100ms) —
    // this is what lets GatewayWsClient.sendHeartbeat() thread the real
    // state into the payload without an extra disk read on the hot path.
    assert.equal(checkpoints.currentHealthState(), "online");

    await checkpoints.saveHealthState("degraded", { lastOutboxError: "boom" });
    assert.equal(checkpoints.currentHealthState(), "degraded");

    await checkpoints.saveHealthState("reconnecting");
    assert.equal(checkpoints.currentHealthState(), "reconnecting");

    await checkpoints.saveHealthState("offline");
    assert.equal(checkpoints.currentHealthState(), "offline");
  } finally {
    await rm(rootDir, { recursive: true, force: true });
  }
});

test("currentHealthState() is updated by save() calls that carry healthState alongside other fields (markRecovered's path)", async () => {
  const rootDir = await mkdtemp(path.join(tmpdir(), "empyralis-gateway-checkpoints-"));
  try {
    const checkpoints = new GatewayCheckpoints(new GatewayStateDb(rootDir));
    await checkpoints.save({ healthState: "degraded" as GatewayHealthState });
    assert.equal(checkpoints.currentHealthState(), "degraded");

    // markRecovered() itself doesn't force healthState — callers pass it
    // explicitly in the snapshot (as GatewayWsClient.connect() does with
    // "online"). A save() with no healthState field must leave the last
    // known value untouched rather than reset it.
    await checkpoints.save({ resumeReady: true });
    assert.equal(checkpoints.currentHealthState(), "degraded");

    await checkpoints.markRecovered({ healthState: "online" as GatewayHealthState });
    assert.equal(checkpoints.currentHealthState(), "online");
  } finally {
    await rm(rootDir, { recursive: true, force: true });
  }
});
