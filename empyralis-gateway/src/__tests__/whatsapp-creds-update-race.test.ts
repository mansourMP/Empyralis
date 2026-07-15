import { mkdtemp, rm } from "fs/promises";
import { tmpdir } from "os";
import path from "path";
import test from "node:test";
import assert from "node:assert/strict";

import { WhatsAppPersonalRuntime } from "../channels/whatsapp/runtime";
import { GatewayStateDb } from "../state/db";

// FIX: creds.update had no equivalent to connection.update's staleness guard
// (see whatsapp-disconnect-race.test.ts for that one) — Baileys keeps
// emitting on an abandoned socket's own event emitter even after we've
// moved on to a fresh one (e.g. reconnectForConfigUpdate() nulled
// this.socket and reconnected), so a late creds.update from the OLD socket
// could overwrite the on-disk auth state with stale creds, clobbering
// whatever the current, live socket had already advanced past.

function buildMockAdapter() {
  const credsUpdateHandlers: Array<() => void | Promise<void>> = [];
  const saveCredsCallCounts: number[] = [];
  let socketIndex = -1;
  const adapter = {
    loadAuthState: async () => {
      const idx = saveCredsCallCounts.push(0) - 1;
      return {
        state: { creds: { registered: false } },
        saveCreds: async () => {
          saveCredsCallCounts[idx] += 1;
        },
      };
    },
    createSocket: () => {
      socketIndex += 1;
      const index = socketIndex;
      const handlers: Record<string, (payload: any) => void | Promise<void>> = {};
      credsUpdateHandlers[index] = () => handlers["creds.update"]?.(undefined);
      return {
        ev: {
          on: (eventName: string, handler: (payload: any) => void | Promise<void>) => {
            handlers[eventName] = handler;
          },
        },
        sendMessage: async () => undefined,
        logout: async () => undefined,
        end: () => undefined,
      };
    },
    disconnectReason: { loggedOut: 401, restartRequired: 515 },
    browserDescriptor: () => ["Empyralis", "Chrome", "1.0"],
    fetchWaWebVersion: async () => undefined,
  };
  return { adapter, credsUpdateHandlers, saveCredsCallCounts };
}

test("a stale creds.update event from an abandoned socket cannot clobber the current socket's auth state", async () => {
  const rootDir = await mkdtemp(path.join(tmpdir(), "empyralis-whatsapp-creds-update-race-"));
  try {
    const { adapter, credsUpdateHandlers, saveCredsCallCounts } = buildMockAdapter();
    const runtime = new WhatsAppPersonalRuntime(new GatewayStateDb(rootDir), { adapter: adapter as any });

    // Drive two real connect passes so the real closures under test (the
    // ones registered inside connectSocketInternal) are what we exercise —
    // socket 0 is the "old" one a reconnect leaves behind, socket 1 is the
    // one this.socket ends up pointing at.
    await (runtime as any).connectSocketInternal();
    await (runtime as any).connectSocketInternal();
    assert.equal(credsUpdateHandlers.length, 2, "both connect passes should have registered a creds.update handler");

    // Fire the STALE (socket 0) creds.update handler — mirrors Baileys
    // still emitting on an abandoned socket after we've moved on.
    await credsUpdateHandlers[0]();
    assert.equal(saveCredsCallCounts[0], 0, "a stale socket's creds.update must not persist its auth bundle");

    // Fire the CURRENT (socket 1) creds.update handler — must still work.
    await credsUpdateHandlers[1]();
    assert.equal(saveCredsCallCounts[1], 1, "the live socket's creds.update should persist normally");
  } finally {
    await rm(rootDir, { recursive: true, force: true });
  }
});
