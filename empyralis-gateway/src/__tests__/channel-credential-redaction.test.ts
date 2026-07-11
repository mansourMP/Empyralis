import { mkdtemp, rm } from "fs/promises";
import { tmpdir } from "os";
import path from "path";
import test from "node:test";
import assert from "node:assert/strict";

import { redactTelegramCredentials } from "../channels/telegram/runtime";
import { redactWhatsAppCredentials } from "../channels/whatsapp/runtime";
import { WhatsAppSessionStore, type WhatsAppSessionSnapshot } from "../channels/whatsapp/session-store";
import { maskPhoneNumber } from "../channels/telegram/login";
import { GatewayStateDb } from "../state/db";

// ---------------------------------------------------------------------------
// Telegram phone-number masking (WhatsApp's login.ts already used its own
// equivalent helper when building loginHint; Telegram's runtime.ts set
// loginHint to the raw phone number directly at two call sites instead of
// calling this already-existing helper).
// ---------------------------------------------------------------------------

test("maskPhoneNumber redacts all but the last 4 digits", () => {
  assert.equal(maskPhoneNumber("+15551234567"), "*******4567");
  assert.equal(maskPhoneNumber("442071838750"), "********8750");
});

test("maskPhoneNumber handles short/empty input without throwing", () => {
  assert.equal(maskPhoneNumber(""), undefined);
  assert.equal(maskPhoneNumber(undefined), undefined);
  assert.equal(maskPhoneNumber("123"), "123");
});

// ---------------------------------------------------------------------------
// Telegram credential redaction
// ---------------------------------------------------------------------------

test("telegram state: apiHash and phoneNumber are redacted", () => {
  const state: Record<string, unknown> = {
    personal_channels: {
      telegram_personal: {
        channel_key: "telegram_personal",
        apiHash: "abc123secret",
        phoneNumber: "+15551234567",
        status: "connected",
      },
    },
  };
  const result = redactTelegramCredentials(state);
  const channel = (result.personal_channels as Record<string, unknown>)
    .telegram_personal as Record<string, unknown>;
  assert.equal(channel.apiHash, "[REDACTED]");
  assert.equal(channel.phoneNumber, "[REDACTED]");
  assert.equal(channel.status, "connected");
});

test("telegram state: sessionString is redacted", () => {
  const state: Record<string, unknown> = {
    sessionString: "1:ABCDEFGHIJKLMNOPQRSTUVWXYZ",
    status: "connected",
  };
  const result = redactTelegramCredentials(state);
  assert.equal(result.sessionString, "[REDACTED]");
  assert.equal(result.status, "connected");
});

test("telegram state: authState, creds, keys objects are redacted", () => {
  const state: Record<string, unknown> = {
    authState: { something: "secret" },
    creds: { registered: true, me: { id: "user1" } },
    keys: { key1: "value1" },
  };
  const result = redactTelegramCredentials(state);
  assert.deepEqual(result.authState, { redacted: true });
  assert.deepEqual(result.creds, { redacted: true });
  assert.deepEqual(result.keys, { redacted: true });
});

test("telegram state: non-sensitive fields are preserved", () => {
  const state: Record<string, unknown> = {
    status: "connected",
    channel_key: "telegram_personal",
    linked_user_id: "user123",
    retryable: true,
  };
  const result = redactTelegramCredentials(state);
  assert.equal(result.status, "connected");
  assert.equal(result.channel_key, "telegram_personal");
  assert.equal(result.linked_user_id, "user123");
  assert.equal(result.retryable, true);
});

test("telegram state: original object is not mutated", () => {
  const state: Record<string, unknown> = {
    sessionString: "1:ABCDEFGHIJKLMNOPQRSTUVWXYZ",
    apiHash: "secret123",
  };
  const copy = JSON.parse(JSON.stringify(state));
  const result = redactTelegramCredentials(state);
  assert.deepEqual(state, copy, "original state must not be mutated");
  assert.notDeepEqual(result, copy, "redacted result must differ from original");
});

// ---------------------------------------------------------------------------
// WhatsApp credential redaction
// ---------------------------------------------------------------------------

test("whatsapp state: creds, keys, authState objects are redacted", () => {
  const state: Record<string, unknown> = {
    creds: { registered: true, me: { id: "user1" } },
    keys: { key1: "value1" },
    authState: { something: "secret" },
  };
  const result = redactWhatsAppCredentials(state);
  assert.deepEqual(result.creds, { redacted: true });
  assert.deepEqual(result.keys, { redacted: true });
  assert.deepEqual(result.authState, { redacted: true });
});

test("whatsapp state: signalIdentities, preKeys, signedPreKey are redacted", () => {
  const state: Record<string, unknown> = {
    signalIdentities: [{ id: "identity1" }],
    preKeys: [{ keyId: 1 }],
    signedPreKey: { keyId: 2 },
  };
  const result = redactWhatsAppCredentials(state);
  assert.deepEqual(result.signalIdentities, { redacted: true });
  assert.deepEqual(result.preKeys, { redacted: true });
  assert.deepEqual(result.signedPreKey, { redacted: true });
});

// qr_code/pairing_code must survive redaction: they're the single-use pairing
// intent the owner's authenticated status endpoint is supposed to serve, not
// durable session material. This exercises the REAL call path — session
// snapshot -> toGatewayStatePayload() (which is what renames these fields to
// snake_case) -> redactWhatsAppCredentials() — rather than a synthetic
// camelCase object no real caller ever produces, so a future casing "fix"
// can't silently reintroduce a redaction no-op or, worse, start redacting a
// value the feature depends on.
test("whatsapp state: real payload chain — qr_code/pairing_code survive, session material is genuinely absent", async () => {
  const rootDir = await mkdtemp(path.join(tmpdir(), "empyralis-whatsapp-redact-"));
  try {
    const store = new WhatsAppSessionStore(new GatewayStateDb(rootDir));
    const snapshot: WhatsAppSessionSnapshot = {
      channelKey: "whatsapp_personal",
      provider: "whatsapp_baileys",
      status: "qr_required",
      qrCode: "2@QR_DATA_RAW_BAILEYS_PAYLOAD,abc123==",
      pairingCode: "ABCD-1234",
    };
    const payload = store.toGatewayStatePayload(snapshot);
    const result = redactWhatsAppCredentials(payload) as {
      personal_channels: { whatsapp_personal: Record<string, unknown> };
    };
    const channel = result.personal_channels.whatsapp_personal;

    // Must survive — this is the entire QR/pairing-code delivery path.
    assert.equal(channel.qr_code, snapshot.qrCode);
    assert.equal(channel.pairing_code, snapshot.pairingCode);

    // Real Baileys auth material must never appear here at all — it's
    // structurally absent from WhatsAppSessionSnapshot by construction
    // (it lives only in the separate auth-state directory), not merely
    // redacted. Locking that in so a future field addition gets caught.
    assert.equal("sessionString" in channel, false);
    assert.equal("sessionToken" in channel, false);
    assert.equal("creds" in channel, false);
    assert.equal("keys" in channel, false);
    assert.equal("authState" in channel, false);
  } finally {
    await rm(rootDir, { recursive: true, force: true });
  }
});

test("whatsapp state: non-sensitive fields are preserved", () => {
  const state: Record<string, unknown> = {
    status: "connected",
    channel_key: "whatsapp_personal",
    linked_jid: "user@whatsapp.net",
    retryable: true,
  };
  const result = redactWhatsAppCredentials(state);
  assert.equal(result.status, "connected");
  assert.equal(result.channel_key, "whatsapp_personal");
  assert.equal(result.linked_jid, "user@whatsapp.net");
  assert.equal(result.retryable, true);
});

test("whatsapp state: original object is not mutated", () => {
  const state: Record<string, unknown> = {
    qrCode: "QR_DATA_12345",
    creds: { registered: true },
  };
  const copy = JSON.parse(JSON.stringify(state));
  const result = redactWhatsAppCredentials(state);
  assert.deepEqual(state, copy, "original state must not be mutated");
  assert.notDeepEqual(result, copy, "redacted result must differ from original");
});
