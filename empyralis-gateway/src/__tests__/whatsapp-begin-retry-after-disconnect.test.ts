import { mkdtemp, rm } from "fs/promises";
import { tmpdir } from "os";
import path from "path";
import test from "node:test";
import assert from "node:assert/strict";

import { WhatsAppPersonalRuntime } from "../channels/whatsapp/runtime";
import { GatewayStateDb } from "../state/db";

function buildMockAdapter() {
  let createSocketCalls = 0;
  const socket = {
    ev: { on: () => undefined },
    sendMessage: async () => undefined,
    logout: async () => undefined,
    end: () => undefined,
  };
  const adapter = {
    loadAuthState: async () => ({
      state: { creds: { registered: false } },
      saveCreds: async () => undefined,
    }),
    createSocket: () => {
      createSocketCalls += 1;
      return socket;
    },
    disconnectReason: { loggedOut: 401, restartRequired: 515 },
    browserDescriptor: () => ["Empyralis", "Chrome", "1.0"],
    fetchWaWebVersion: async () => undefined,
  };
  return { adapter, getCreateSocketCalls: () => createSocketCalls };
}

test("an empty configure() call after disconnect() begins a fresh connection attempt, not an error", async () => {
  const rootDir = await mkdtemp(path.join(tmpdir(), "empyralis-whatsapp-begin-retry-"));
  try {
    const { adapter, getCreateSocketCalls } = buildMockAdapter();
    const runtime = new WhatsAppPersonalRuntime(new GatewayStateDb(rootDir), { adapter: adapter as any });
    // reconnectForConfigUpdate() (which handleConfigure's empty-call path
    // drives) only fires while the runtime considers itself started --
    // matches every real Gateway process for its whole lifetime, set here
    // since this test drives connectSocketInternal()/handleConfigure()
    // directly rather than through the real start() lifecycle entry point.
    (runtime as any).started = true;

    await (runtime as any).connectSocketInternal();
    assert.equal(getCreateSocketCalls(), 1, "first connect should have created a socket");

    await (runtime as any).handleDisconnect();
    const idleState = await (runtime as any).sessionStore.load();
    assert.equal(idleState.status, "idle");

    // This is the gap being closed: previously handleConfigure({}) (no
    // phone_number/custom_pairing_code) threw "At least one WhatsApp
    // personal setup field is required" -- the only thing that could ever
    // move the runtime out of idle after a disconnect was restarting the
    // whole Gateway process, which the wizard has no way to trigger.
    const result = await (runtime as any).handleConfigure({});
    assert.equal(result.status, "updated");
    assert.equal(result.reconnect_requested, true);
    assert.equal(getCreateSocketCalls(), 2, "an empty configure() call must actually attempt a fresh connection");

    const afterRetryState = await (runtime as any).sessionStore.load();
    assert.equal(afterRetryState.status, "connecting");
  } finally {
    await rm(rootDir, { recursive: true, force: true });
  }
});

test("configure() still requires a real value when phone_number/custom_pairing_code IS provided (empty-call relaxation isn't a validation bypass)", async () => {
  const rootDir = await mkdtemp(path.join(tmpdir(), "empyralis-whatsapp-begin-retry-validation-"));
  try {
    const { adapter } = buildMockAdapter();
    const runtime = new WhatsAppPersonalRuntime(new GatewayStateDb(rootDir), { adapter: adapter as any });

    await assert.rejects(
      () => (runtime as any).handleConfigure({ phone_number: "" }),
      /phone_number is required when provided/,
    );
    await assert.rejects(
      () => (runtime as any).handleConfigure({ custom_pairing_code: "" }),
      /custom_pairing_code is required when provided/,
    );
  } finally {
    await rm(rootDir, { recursive: true, force: true });
  }
});
