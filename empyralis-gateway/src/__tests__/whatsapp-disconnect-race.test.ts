import { mkdtemp, rm } from "fs/promises";
import { tmpdir } from "os";
import path from "path";
import test from "node:test";
import assert from "node:assert/strict";

import { WhatsAppPersonalRuntime } from "../channels/whatsapp/runtime";
import { GatewayStateDb } from "../state/db";

function buildMockAdapter() {
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
    logout: async () => undefined,
    end: () => undefined,
  };
  const adapter = {
    loadAuthState: async () => ({
      state: { creds: { registered: false } },
      saveCreds: async () => undefined,
    }),
    createSocket: () => socket,
    disconnectReason: { loggedOut: 401, restartRequired: 515 },
    browserDescriptor: () => ["Empyralis", "Chrome", "1.0"],
    fetchWaWebVersion: async () => undefined,
  };
  return {
    adapter,
    getCapturedConnectionUpdateHandler: () => capturedConnectionUpdateHandler,
  };
}

test("a stale connection.update 'close' event from an abandoned socket cannot clobber a fresh disconnect/idle state", async () => {
  const rootDir = await mkdtemp(path.join(tmpdir(), "empyralis-whatsapp-disconnect-race-"));
  try {
    const { adapter, getCapturedConnectionUpdateHandler } = buildMockAdapter();
    const runtime = new WhatsAppPersonalRuntime(new GatewayStateDb(rootDir), { adapter: adapter as any });

    // Drive a real connection attempt so the real closure under test (the
    // one registered inside connectSocketInternal) is the thing we exercise
    // — not a hand-rolled stand-in for it.
    await (runtime as any).connectSocketInternal();
    const staleHandler = getCapturedConnectionUpdateHandler();
    assert.ok(staleHandler, "connection.update handler should have been registered on the socket");

    // Now disconnect — this nulls runtime.socket and writes a clean idle state.
    const result = await (runtime as any).handleDisconnect();
    assert.equal(result.status, "disconnected");

    const idleState = await (runtime as any).sessionStore.load();
    assert.equal(idleState.status, "idle");
    assert.equal(idleState.retryable, true);

    // Simulate Baileys emitting a late "close" event on the now-abandoned
    // socket (e.g. as a consequence of the .logout()/.end() calls we made
    // during handleDisconnect()) arriving AFTER handleDisconnect() has
    // already returned and written the clean idle state.
    staleHandler!({
      connection: "close",
      lastDisconnect: {
        error: { output: { statusCode: 401 }, message: "Intentional Logout" },
      },
    });
    // Let any microtasks/promise chains from the stale handler settle.
    await new Promise((resolve) => setTimeout(resolve, 20));

    const finalState = await (runtime as any).sessionStore.load();
    assert.equal(
      finalState.status,
      "idle",
      "a stale close event from an abandoned socket must not overwrite the post-disconnect idle state",
    );
    assert.equal(finalState.retryable, true);
  } finally {
    await rm(rootDir, { recursive: true, force: true });
  }
});
