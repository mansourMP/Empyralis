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
