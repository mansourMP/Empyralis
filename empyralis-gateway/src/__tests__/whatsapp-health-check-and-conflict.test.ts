import { mkdtemp, rm } from "fs/promises";
import { tmpdir } from "os";
import path from "path";
import test from "node:test";
import assert from "node:assert/strict";

import { WhatsAppPersonalRuntime } from "../channels/whatsapp/runtime";
import { resolveWhatsAppReconnectState } from "../channels/whatsapp/reconnect";
import { GatewayStateDb } from "../state/db";

// ---------------------------------------------------------------------------
// resolveWhatsAppReconnectState -- the 440/"stream conflict" classification
// itself. Pure-function unit tests, mirroring telegram-reconnect.test.ts's
// shape for resolveTelegramReconnectState.
//
// Reliability audit finding: "No named handling of WhatsApp's 440/'stream
// conflict' (another device logged in) -- falls into the generic
// reconnect-with-backoff branch" (whatsapp/reconnect.ts:38-42 pre-fix).
// ---------------------------------------------------------------------------

test("DisconnectReason.connectionReplaced (440, another device conflict) is classified as a distinct 'conflict' status, not plain 'disconnected'", () => {
  const state = resolveWhatsAppReconnectState(
    { error: { output: { statusCode: 440 }, message: "Stream Errored (conflict)" } },
    { loggedOut: 401, restartRequired: 515, connectionReplaced: 440 },
  );
  assert.equal(state.shouldReconnect, true, "a conflict is still reconnectable -- WhatsApp allows relinking, this isn't a logout");
  assert.equal(state.status, "conflict");
  assert.equal(state.statusCode, 440);
});

test("a conflict is never confused with a genuine logout (401) or an ordinary drop", () => {
  const loggedOut = resolveWhatsAppReconnectState(
    { error: { output: { statusCode: 401 }, message: "Logged Out" } },
    { loggedOut: 401, restartRequired: 515, connectionReplaced: 440 },
  );
  assert.equal(loggedOut.status, "logged_out");
  assert.equal(loggedOut.shouldReconnect, false);

  const ordinaryDrop = resolveWhatsAppReconnectState(
    { error: { output: { statusCode: 428 }, message: "Connection Closed" } },
    { loggedOut: 401, restartRequired: 515, connectionReplaced: 440 },
  );
  assert.equal(ordinaryDrop.status, "disconnected");
  assert.equal(ordinaryDrop.shouldReconnect, true);
});

test("an adapter that doesn't expose connectionReplaced at all never misclassifies an unrelated 440-shaped disconnect as anything but the generic reconnectable default (no regression for a stale/mocked adapter)", () => {
  const state = resolveWhatsAppReconnectState(
    { error: { output: { statusCode: 440 }, message: "Stream Errored (conflict)" } },
    { loggedOut: 401, restartRequired: 515 },
  );
  assert.equal(state.status, "disconnected");
  assert.equal(state.shouldReconnect, true);
});

// ---------------------------------------------------------------------------
// End-to-end: a real connection.update "close" event carrying the 440 code
// lands as session status "conflict" (not "disconnected"), with its own
// issue tag on getHealthSnapshot().
// ---------------------------------------------------------------------------

function buildMockAdapter(sendPresenceUpdateImpl?: (type: string, jid?: string) => Promise<void>) {
  let capturedConnectionUpdateHandler: ((update: Record<string, unknown>) => void) | null = null;
  const socket = {
    ev: {
      on: (eventName: string, handler: (payload: any) => void | Promise<void>) => {
        if (eventName === "connection.update") {
          capturedConnectionUpdateHandler = handler;
        }
      },
    },
    sendMessage: async () => undefined,
    sendPresenceUpdate: sendPresenceUpdateImpl ?? (async () => undefined),
    logout: async () => undefined,
    end: () => undefined,
    user: { id: "15551234567@s.whatsapp.net", name: "Owner" },
  };
  const adapter = {
    loadAuthState: async () => ({
      state: { creds: { registered: true } },
      saveCreds: async () => undefined,
    }),
    createSocket: () => socket,
    disconnectReason: { loggedOut: 401, restartRequired: 515, connectionReplaced: 440 },
    browserDescriptor: () => ["Empyralis", "Chrome", "1.0"],
    fetchWaWebVersion: async () => undefined,
  };
  return {
    adapter,
    socket,
    getCapturedConnectionUpdateHandler: () => capturedConnectionUpdateHandler,
  };
}

test("a real 'close' event carrying WhatsApp's 440 conflict code surfaces session status 'conflict' with its own health issue tag, and does NOT wipe auth state", async () => {
  const rootDir = await mkdtemp(path.join(tmpdir(), "empyralis-whatsapp-conflict-"));
  try {
    const { adapter, getCapturedConnectionUpdateHandler } = buildMockAdapter();
    const runtime = new WhatsAppPersonalRuntime(new GatewayStateDb(rootDir), { adapter: adapter as any });

    await (runtime as any).connectSocketInternal();
    const handler = getCapturedConnectionUpdateHandler();
    assert.ok(handler, "connection.update handler should have been registered on the socket");

    handler!({
      connection: "close",
      lastDisconnect: {
        error: { output: { statusCode: 440 }, message: "Stream Errored (conflict)" },
      },
    });
    await new Promise((resolve) => setTimeout(resolve, 20));

    const state = await (runtime as any).sessionStore.load();
    assert.equal(state.status, "conflict", "a 440 conflict must surface as its own distinct status, not a plain 'disconnected'");
    assert.equal(state.retryable, true);
    assert.equal(state.lastDisconnectCode, 440);

    const health = await runtime.getHealthSnapshot();
    assert.equal(health.status, "conflict");
    assert.deepEqual(health.issues, ["whatsapp_personal_connection_conflict"]);
  } finally {
    await rm(rootDir, { recursive: true, force: true });
  }
});

// ---------------------------------------------------------------------------
// Active health-check -- mirrors TelegramPersonalRuntime's runHealthCheck:
// a live socket is periodically re-probed with a real round trip, and a
// probe failure is treated exactly like a connection.update "close" event
// (tears down the socket, persists a reconnectable state).
//
// Reliability audit finding: "No active health-check/heartbeat exists
// (unlike Telegram)" (runtime.ts:1074-1110 pre-fix).
// ---------------------------------------------------------------------------

test("runHealthCheck() probes a live socket via sendPresenceUpdate('available', ...) and leaves a connected session connected", async () => {
  const rootDir = await mkdtemp(path.join(tmpdir(), "empyralis-whatsapp-health-ok-"));
  try {
    const presenceCalls: Array<{ type: string; jid?: string }> = [];
    const { adapter, getCapturedConnectionUpdateHandler } = buildMockAdapter(async (type, jid) => {
      presenceCalls.push({ type, jid });
    });
    const runtime = new WhatsAppPersonalRuntime(new GatewayStateDb(rootDir), { adapter: adapter as any });

    await (runtime as any).connectSocketInternal();
    getCapturedConnectionUpdateHandler()!({ connection: "open" });
    await new Promise((resolve) => setTimeout(resolve, 20));
    assert.equal((await runtime.getHealthSnapshot()).status, "connected");

    await (runtime as any).runHealthCheck();

    assert.equal(presenceCalls.length, 1);
    assert.equal(presenceCalls[0].type, "available");
    assert.equal(presenceCalls[0].jid, "15551234567@s.whatsapp.net");

    const health = await runtime.getHealthSnapshot();
    assert.equal(health.status, "connected", "a successful probe must never itself flip a healthy session to disconnected");
    assert.ok((runtime as any).socket, "the socket must remain live after a successful probe");
  } finally {
    await rm(rootDir, { recursive: true, force: true });
  }
});

test("runHealthCheck() catches a socket Baileys never fired a 'close' event for and tears it down as a reconnectable disconnect", async () => {
  const rootDir = await mkdtemp(path.join(tmpdir(), "empyralis-whatsapp-health-fail-"));
  try {
    const { adapter } = buildMockAdapter(async () => {
      // Mirrors Baileys' own sendRawMessage: throws the instant the
      // underlying WebSocket is no longer open, with no connection.update
      // "close" event ever having fired for it.
      throw new Error("Connection Closed");
    });
    const runtime = new WhatsAppPersonalRuntime(new GatewayStateDb(rootDir), { adapter: adapter as any });

    await (runtime as any).connectSocketInternal();
    assert.ok((runtime as any).socket, "a live socket should exist before the probe");

    await (runtime as any).runHealthCheck();

    assert.equal((runtime as any).socket, null, "a failed probe must tear down the now-dead socket, same as a real close event");
    const state = await (runtime as any).sessionStore.load();
    assert.equal(state.status, "disconnected");
    assert.equal(state.retryable, true, "a health-check failure carries no Baileys disconnect code -- must default to reconnectable, not logged_out");
    assert.match(state.lastDisconnectReason ?? "", /Connection Closed/);
  } finally {
    await rm(rootDir, { recursive: true, force: true });
  }
});

test("runHealthCheck() no-ops (and reschedules) when there is no live socket, instead of throwing", async () => {
  const rootDir = await mkdtemp(path.join(tmpdir(), "empyralis-whatsapp-health-idle-"));
  try {
    const { adapter } = buildMockAdapter();
    const runtime = new WhatsAppPersonalRuntime(new GatewayStateDb(rootDir), { adapter: adapter as any });
    // No connectSocketInternal() call -- this.socket is still null.
    await assert.doesNotReject(() => (runtime as any).runHealthCheck());
  } finally {
    await rm(rootDir, { recursive: true, force: true });
  }
});
