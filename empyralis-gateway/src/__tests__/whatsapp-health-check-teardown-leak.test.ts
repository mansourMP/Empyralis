import { mkdtemp, rm } from "fs/promises";
import { tmpdir } from "os";
import path from "path";
import test from "node:test";
import assert from "node:assert/strict";

import { WhatsAppPersonalRuntime } from "../channels/whatsapp/runtime";
import { GatewayStateDb } from "../state/db";

// ---------------------------------------------------------------------------
// ADVERSARIAL GAP TEST -- NOW UPDATED TO ASSERT THE FIX (resource-safety leg
// of the reliability wave audit).
//
// whatsapp-health-check-and-conflict.test.ts already proves runHealthCheck()
// tears down a half-dead socket's BOOKKEEPING (this.socket set back to null,
// session persisted as "disconnected"/retryable). This file asks the
// adversarial question that test doesn't: does it also tear down the
// SOCKET ITSELF?
//
// FIXED: handleSocketClose() (channels/whatsapp/runtime.ts), the single
// teardown path for both a real Baileys "close" event AND a
// runHealthCheck() probe failure (runtime.ts:1253-1255), now captures the
// outgoing socket reference BEFORE nulling `this.socket`, and calls
// `.end(undefined)` (plus `.ws.close()` if present) on it -- deliberately
// NOT `.logout()`, since this is a reconnectable-close teardown, not a
// deliberate user disconnect; `.logout()` would actively deauthorize the
// linked device over the network. Compare handleDisconnect()
// (runtime.ts:397-416), which explicitly calls both logout() and end()
// because it IS a deliberate full reset.
//
// For a REAL Baileys "close" event this new call is harmless -- Baileys has
// already torn down its own underlying WebSocket before firing
// connection.update, so `.end()` on an already-dead socket is a no-op.
// runHealthCheck()'s own doc comment (runtime.ts:1207-1218) explicitly
// targets a DIFFERENT case: "a half-dead network path where no close frame
// was ever received" -- i.e. sendPresenceUpdate() rejects (ack timeout)
// while the underlying WebSocket may still be technically open. In that
// specific case, before this fix handleSocketClose abandoned the old
// socket object without ever calling .end()/.logout() on it -- the
// underlying connection/timers Baileys owns for that socket were never
// explicitly released by this runtime, once every ~3 minute health-check
// cycle (WHATSAPP_HEALTH_CHECK_INTERVAL_MS) under sustained flaky-but-alive
// network conditions.
//
// This test proves the code-level fact directly (deterministic, no network
// flakiness needed): after a runHealthCheck()-triggered teardown, the old
// socket's own .end() IS now invoked (but never .logout() -- this isn't a
// real logout) -- contrasted with the handleDisconnect() path, which this
// test also exercises and which calls both.
// ---------------------------------------------------------------------------

function buildMockAdapter(sendPresenceUpdateImpl?: (type: string, jid?: string) => Promise<void>) {
  let capturedConnectionUpdateHandler: ((update: Record<string, unknown>) => void) | null = null;
  const endCalls: unknown[] = [];
  const logoutCalls: number[] = [];
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
    logout: async () => {
      logoutCalls.push(1);
    },
    end: (arg: unknown) => {
      endCalls.push(arg);
    },
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
    endCalls,
    logoutCalls,
    getCapturedConnectionUpdateHandler: () => capturedConnectionUpdateHandler,
  };
}

test(
  "FIXED: a runHealthCheck()-triggered teardown now calls .end() (but not .logout()) on the abandoned socket",
  async () => {
    const rootDir = await mkdtemp(path.join(tmpdir(), "empyralis-whatsapp-healthcheck-leak-"));
    try {
      const { adapter, endCalls, logoutCalls } = buildMockAdapter(async () => {
        // Mirrors a half-dead network path: the presence-update round trip
        // times out / rejects while Baileys' own "close" event never fires.
        throw new Error("Connection Closed");
      });
      const runtime = new WhatsAppPersonalRuntime(new GatewayStateDb(rootDir), { adapter: adapter as any });

      await (runtime as any).connectSocketInternal();
      assert.ok((runtime as any).socket, "a live socket should exist before the probe");

      await (runtime as any).runHealthCheck();

      assert.equal((runtime as any).socket, null, "runHealthCheck must still clear the bookkeeping reference (already covered elsewhere)");

      // FIXED: the abandoned socket object is now told to shut itself down.
      assert.deepEqual(
        endCalls,
        [undefined],
        "FIXED: handleSocketClose() now calls socket.end(undefined) on a runHealthCheck-triggered teardown, " +
          "releasing the abandoned socket's transport instead of leaking it (channels/whatsapp/runtime.ts)",
      );
      assert.deepEqual(
        logoutCalls,
        [],
        "a health-check-triggered teardown correctly still does NOT call logout() (this isn't a real logout) -- " +
          "only end() is called, matching a reconnectable-close teardown rather than a deliberate disconnect",
      );
    } finally {
      await rm(rootDir, { recursive: true, force: true });
    }
  },
);

test(
  "CONTRAST (confirms the gap is real, not a misreading): handleDisconnect() DOES call both logout() and end() on teardown",
  async () => {
    const rootDir = await mkdtemp(path.join(tmpdir(), "empyralis-whatsapp-handledisconnect-"));
    try {
      const { adapter, endCalls, logoutCalls } = buildMockAdapter();
      const runtime = new WhatsAppPersonalRuntime(new GatewayStateDb(rootDir), { adapter: adapter as any });

      await (runtime as any).connectSocketInternal();
      assert.ok((runtime as any).socket, "a live socket should exist before disconnect");

      await (runtime as any).handleDisconnect();

      assert.equal(logoutCalls.length, 1, "handleDisconnect() must call logout() on the live socket");
      assert.equal(endCalls.length, 1, "handleDisconnect() must call end() on the live socket");
      assert.equal((runtime as any).socket, null);
    } finally {
      await rm(rootDir, { recursive: true, force: true });
    }
  },
);
