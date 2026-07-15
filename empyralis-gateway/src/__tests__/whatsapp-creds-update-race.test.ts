import { mkdtemp, rm } from "fs/promises";
import { tmpdir } from "os";
import path from "path";
import test from "node:test";
import assert from "node:assert/strict";

import { WhatsAppPersonalRuntime } from "../channels/whatsapp/runtime";
import { GatewayStateDb } from "../state/db";
import { readFileRaw, resolveCredsPath } from "../channels/whatsapp/auth-persistence";

// FIX: creds.update had no equivalent to connection.update's staleness guard
// (see whatsapp-disconnect-race.test.ts for that one) — Baileys keeps
// emitting on an abandoned socket's own event emitter even after we've
// moved on to a fresh one (e.g. reconnectForConfigUpdate() nulled
// this.socket and reconnected), so a late creds.update from the OLD socket
// could overwrite the on-disk auth state with stale creds, clobbering
// whatever the current, live socket had already advanced past.
//
// creds.update's handler now persists through WhatsAppAuthPersistence (see
// ../channels/whatsapp/auth-persistence.ts) instead of calling Baileys' own
// bundled saveCreds() directly, so this test asserts on the real,
// authoritative signal -- what actually landed in creds.json on disk --
// rather than counting calls to a mock function that's intentionally never
// invoked anymore.

function buildMockAdapter() {
  const credsUpdateHandlers: Array<() => void | Promise<void>> = [];
  let socketIndex = -1;
  const adapter = {
    loadAuthState: async () => {
      // loadAuthState always runs immediately before createSocket within the
      // same connectSocketInternal() pass, so socketIndex + 1 correctly
      // predicts the index createSocket is about to assign.
      const idx = socketIndex + 1;
      return {
        state: { creds: { registered: false, me: { id: `socket-${idx}@s.whatsapp.net` } } },
        // Baileys' own bundled saveCreds is intentionally never called by
        // runtime.ts anymore -- that bare non-atomic writeFile is exactly
        // what WhatsAppAuthPersistence replaces. Kept here only because the
        // BaileysAuthBundle shape requires it.
        saveCreds: async () => undefined,
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
  return { adapter, credsUpdateHandlers };
}

test("a stale creds.update event from an abandoned socket cannot clobber the current socket's auth state", async () => {
  const rootDir = await mkdtemp(path.join(tmpdir(), "empyralis-whatsapp-creds-update-race-"));
  try {
    const { adapter, credsUpdateHandlers } = buildMockAdapter();
    const runtime = new WhatsAppPersonalRuntime(new GatewayStateDb(rootDir), { adapter: adapter as any });

    // Drive two real connect passes so the real closures under test (the
    // ones registered inside connectSocketInternal) are what we exercise —
    // socket 0 is the "old" one a reconnect leaves behind, socket 1 is the
    // one this.socket ends up pointing at.
    await (runtime as any).connectSocketInternal();
    await (runtime as any).connectSocketInternal();
    assert.equal(credsUpdateHandlers.length, 2, "both connect passes should have registered a creds.update handler");

    const credsPath = resolveCredsPath((runtime as any).sessionStore.authStateDir());

    // Fire the STALE (socket 0) creds.update handler — mirrors Baileys
    // still emitting on an abandoned socket after we've moved on.
    await credsUpdateHandlers[0]();
    assert.equal(
      await readFileRaw(credsPath),
      null,
      "a stale socket's creds.update must not persist its auth bundle to disk at all",
    );

    // Fire the CURRENT (socket 1) creds.update handler — must still work.
    await credsUpdateHandlers[1]();
    const raw = await readFileRaw(credsPath);
    assert.ok(raw, "the live socket's creds.update should persist normally");
    assert.equal(
      (JSON.parse(raw!) as { me: { id: string } }).me.id,
      "socket-1@s.whatsapp.net",
      "the persisted content must be the LIVE socket's creds, never the stale one's",
    );
  } finally {
    await rm(rootDir, { recursive: true, force: true });
  }
});
