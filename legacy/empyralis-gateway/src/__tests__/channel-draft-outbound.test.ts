import { mkdtemp, rm } from "fs/promises";
import { tmpdir } from "os";
import path from "path";
import test from "node:test";
import assert from "node:assert/strict";

import { TelegramPersonalRuntime } from "../channels/telegram/runtime";
import { TELEGRAM_PERSONAL_CHANNEL_KEY, TELEGRAM_PERSONAL_PROVIDER } from "../channels/telegram/session-store";
import { WhatsAppPersonalRuntime } from "../channels/whatsapp/runtime";
import { WHATSAPP_PERSONAL_CHANNEL_KEY, WHATSAPP_PERSONAL_PROVIDER } from "../channels/whatsapp/session-store";
import { GatewayStateDb } from "../state/db";

function frame(channelKey: string, provider: string, patch: Record<string, unknown>) {
  return {
    kind: "request" as const,
    id: String(patch.idempotency_key || "request-1"),
    type: "channel.outbound" as const,
    ts: new Date().toISOString(),
    payload: {
      channel_key: channelKey,
      provider,
      remote_jid: "user-1",
      idempotency_key: "reply-1",
      text: "",
      ...patch,
    },
  };
}

test("Telegram draft operations do not send until draft_final", async () => {
  const rootDir = await mkdtemp(path.join(tmpdir(), "empyralis-telegram-draft-"));
  const sent: Array<{ remoteJid: string; text: string }> = [];
  try {
    const runtime = new TelegramPersonalRuntime(new GatewayStateDb(rootDir));
    (runtime as any).client = {
      sendMessage: async (remoteJid: string, text: string) => {
        sent.push({ remoteJid, text });
        return { externalMessageId: "tg-1", remoteJid };
      },
    };

    await runtime.handleChannelOutbound(frame(TELEGRAM_PERSONAL_CHANNEL_KEY, TELEGRAM_PERSONAL_PROVIDER, {
      operation: "draft_start",
      draft_id: "draft-1",
      sequence: 1,
    }));
    await runtime.handleChannelOutbound(frame(TELEGRAM_PERSONAL_CHANNEL_KEY, TELEGRAM_PERSONAL_PROVIDER, {
      operation: "draft_delta",
      draft_id: "draft-1",
      sequence: 2,
      delta: "hello ",
    }));

    assert.equal(sent.length, 0);

    const result = await runtime.handleChannelOutbound(frame(TELEGRAM_PERSONAL_CHANNEL_KEY, TELEGRAM_PERSONAL_PROVIDER, {
      operation: "draft_final",
      draft_id: "draft-1",
      sequence: 3,
      text: "hello world",
    }));

    assert.equal(sent.length, 1);
    assert.equal(sent[0].text, "hello world");
    assert.equal(result.delivered, true);
  } finally {
    await rm(rootDir, { recursive: true, force: true });
  }
});

test("WhatsApp draft operations do not send until draft_final", async () => {
  const rootDir = await mkdtemp(path.join(tmpdir(), "empyralis-whatsapp-draft-"));
  const sent: Array<{ remoteJid: string; text: string }> = [];
  try {
    const runtime = new WhatsAppPersonalRuntime(new GatewayStateDb(rootDir));
    (runtime as any).socket = {
      sendMessage: async (remoteJid: string, content: Record<string, unknown>) => {
        sent.push({ remoteJid, text: String(content.text || "") });
        return { key: { id: "wa-1", remoteJid } };
      },
    };

    await runtime.handleChannelOutbound(frame(WHATSAPP_PERSONAL_CHANNEL_KEY, WHATSAPP_PERSONAL_PROVIDER, {
      operation: "draft_start",
      draft_id: "draft-1",
      sequence: 1,
    }));
    await runtime.handleChannelOutbound(frame(WHATSAPP_PERSONAL_CHANNEL_KEY, WHATSAPP_PERSONAL_PROVIDER, {
      operation: "draft_delta",
      draft_id: "draft-1",
      sequence: 2,
      delta: "hello ",
    }));

    assert.equal(sent.length, 0);

    const result = await runtime.handleChannelOutbound(frame(WHATSAPP_PERSONAL_CHANNEL_KEY, WHATSAPP_PERSONAL_PROVIDER, {
      operation: "draft_final",
      draft_id: "draft-1",
      sequence: 3,
      text: "hello world",
    }));

    assert.equal(sent.length, 1);
    assert.equal(sent[0].text, "hello world");
    assert.equal(result.delivered, true);
  } finally {
    await rm(rootDir, { recursive: true, force: true });
  }
});
