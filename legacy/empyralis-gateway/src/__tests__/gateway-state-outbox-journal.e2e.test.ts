import { mkdtemp, rm } from "fs/promises";
import { tmpdir } from "os";
import path from "path";
import test from "node:test";
import assert from "node:assert/strict";

import { GatewayCheckpoints } from "../state/checkpoints";
import { GatewayStateDb } from "../state/db";
import { GatewayJournal } from "../state/journal";
import { GatewayOutbox } from "../state/outbox";

test("gateway state, outbox, and journal survive reconnect and recovery", async () => {
  const rootDir = await mkdtemp(path.join(tmpdir(), "empyralis-gateway-e2e-"));
  try {
    const firstDb = new GatewayStateDb(rootDir);
    const firstCheckpoints = new GatewayCheckpoints(firstDb);
    const firstJournal = new GatewayJournal(firstDb);
    const firstOutbox = new GatewayOutbox(firstDb);

    await firstCheckpoints.saveHealthState("offline", {
      lastDisconnectReason: "socket_closed",
      pendingOutboxCount: 0,
      resumeReady: false,
    });
    await firstOutbox.enqueue(
      "req-tool-1",
      "gateway.tool.invoke",
      {
        capability_id: "computer_control.screenshot",
        arguments: { display_id: "main" },
      },
      { replayable: true },
    );
    const outboundEntry = await firstJournal.append("outbound", "gateway.tool.invoke", {
      request_id: "req-tool-1",
    });
    await firstOutbox.markForReplay("req-tool-1", "socket closed before ack");
    await firstCheckpoints.saveHealthState("reconnecting", {
      pendingOutboxCount: 1,
      lastOutboxError: "socket closed before ack",
      lastAck: outboundEntry.cursor,
      resumeReady: true,
    });
    // Flush debounced writes before reading from a new GatewayStateDb instance
    await firstCheckpoints.flush();

    const reconnectDb = new GatewayStateDb(rootDir);
    const reconnectCheckpoints = new GatewayCheckpoints(reconnectDb);
    const reconnectJournal = new GatewayJournal(reconnectDb);
    const reconnectOutbox = new GatewayOutbox(reconnectDb);

    const reconnectSnapshot = await reconnectCheckpoints.load();
    const replayable = await reconnectOutbox.listReplayablePending();
    assert.equal(reconnectSnapshot.healthState, "reconnecting");
    assert.equal(reconnectSnapshot.pendingOutboxCount, 1);
    assert.equal(reconnectSnapshot.resumeReady, true);
    assert.equal(await reconnectJournal.lastCursor(), 1);
    assert.equal(replayable.length, 1);
    assert.equal(replayable[0].requestId, "req-tool-1");
    assert.equal(replayable[0].status, "pending");
    assert.equal(replayable[0].retryCount, 1);
    assert.equal(replayable[0].lastError, "socket closed before ack");

    await reconnectOutbox.acknowledge("req-tool-1");
    const inboundEntry = await reconnectJournal.append("inbound", "gateway.tool.result", {
      request_id: "req-tool-1",
      ok: true,
    });
    await reconnectCheckpoints.markRecovered({
      healthState: "online",
      pendingOutboxCount: 0,
      lastAck: inboundEntry.cursor,
    });
    await reconnectCheckpoints.flush();

    const recoveredDb = new GatewayStateDb(rootDir);
    const recoveredCheckpoints = new GatewayCheckpoints(recoveredDb);
    const recoveredJournal = new GatewayJournal(recoveredDb);
    const recoveredOutbox = new GatewayOutbox(recoveredDb);
    const recoveredSnapshot = await recoveredCheckpoints.load();
    const recoveredSummary = await recoveredOutbox.summarize();

    assert.equal(recoveredSnapshot.healthState, "online");
    assert.equal(recoveredSnapshot.pendingOutboxCount, 0);
    assert.equal(recoveredSnapshot.resumeReady, true);
    assert.equal(recoveredSnapshot.lastAck, 2);
    assert.equal(await recoveredJournal.lastCursor(), 2);
    assert.equal((await recoveredOutbox.listReplayablePending()).length, 0);
    assert.equal(recoveredSummary.total, 1);
    assert.equal(recoveredSummary.acknowledged, 1);
  } finally {
    await rm(rootDir, { recursive: true, force: true });
  }
});
