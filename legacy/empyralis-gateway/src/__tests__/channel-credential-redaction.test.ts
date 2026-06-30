import test from "node:test";
import assert from "node:assert/strict";

import { redactTelegramCredentials } from "../channels/telegram/runtime";
import { redactWhatsAppCredentials } from "../channels/whatsapp/runtime";

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

test("whatsapp state: qrCode and pairingCode are redacted", () => {
  const state: Record<string, unknown> = {
    qrCode: "QR_DATA_12345",
    pairingCode: "ABC-DEF-GHI",
    status: "connected",
  };
  const result = redactWhatsAppCredentials(state);
  assert.equal(result.qrCode, "[REDACTED]");
  assert.equal(result.pairingCode, "[REDACTED]");
  assert.equal(result.status, "connected");
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
