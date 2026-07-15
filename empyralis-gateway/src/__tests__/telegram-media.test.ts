import { mkdtemp, rm, readFile, readdir } from "fs/promises";
import { tmpdir } from "os";
import path from "path";
import test from "node:test";
import assert from "node:assert/strict";

import {
  mapTelegramInboundMessage,
  mapTelegramOutboundMediaItem,
  mapTelegramOutboundResult,
} from "../channels/telegram/message-mapper";
import {
  TelegramPersonalRuntime,
  classifyTelegramInboundMedia,
  downloadAndStoreTelegramMedia,
  extensionForTelegramMime,
} from "../channels/telegram/runtime";
import { TELEGRAM_PERSONAL_CHANNEL_KEY, TELEGRAM_PERSONAL_PROVIDER } from "../channels/telegram/session-store";
import { GatewayStateDb } from "../state/db";

// ===========================================================================
// extensionForTelegramMime
// ===========================================================================

test("extensionForTelegramMime: known mime types map to their canonical extension", () => {
  assert.equal(extensionForTelegramMime("image/jpeg"), ".jpg");
  assert.equal(extensionForTelegramMime("audio/ogg"), ".ogg");
  assert.equal(extensionForTelegramMime("video/mp4"), ".mp4");
  assert.equal(extensionForTelegramMime("image/webp"), ".webp");
});

test("extensionForTelegramMime: unknown mime falls back to the original filename's extension", () => {
  assert.equal(extensionForTelegramMime("application/x-custom", "report.docx"), ".docx");
});

test("extensionForTelegramMime: unknown mime with no usable filename derives from the mime subtype", () => {
  assert.equal(extensionForTelegramMime("application/x-special"), ".xspecial");
});

test("extensionForTelegramMime: nothing usable falls back to .bin", () => {
  assert.equal(extensionForTelegramMime(undefined), ".bin");
  assert.equal(extensionForTelegramMime(""), ".bin");
});

// ===========================================================================
// classifyTelegramInboundMedia
// ===========================================================================
//
// Several of these mocks deliberately set BOTH a specific getter (voice,
// sticker) AND `document`, because that's what a real GramJS message looks
// like — voice notes and stickers ARE GramJS "documents" under the hood
// (see node_modules/telegram/tl/custom/message.js), so the classifier must
// check the specific getters before falling back to the generic one.

test("classifyTelegramInboundMedia: photo -> image", () => {
  const result = classifyTelegramInboundMedia({ photo: {}, file: { mimeType: "image/jpeg" } });
  assert.equal(result?.kind, "image");
});

test("classifyTelegramInboundMedia: voice wins over the generic document fallback", () => {
  const result = classifyTelegramInboundMedia({ voice: {}, document: {}, file: { mimeType: "audio/ogg" } });
  assert.equal(result?.kind, "voice");
});

test("classifyTelegramInboundMedia: video -> video", () => {
  const result = classifyTelegramInboundMedia({ video: {}, document: {}, file: { mimeType: "video/mp4" } });
  assert.equal(result?.kind, "video");
});

test("classifyTelegramInboundMedia: videoNote (round video message) -> video", () => {
  const result = classifyTelegramInboundMedia({ videoNote: {}, document: {}, file: { mimeType: "video/mp4" } });
  assert.equal(result?.kind, "video");
});

test("classifyTelegramInboundMedia: gif (silent animation) -> video", () => {
  const result = classifyTelegramInboundMedia({ gif: {}, document: {}, file: { mimeType: "video/mp4" } });
  assert.equal(result?.kind, "video");
});

test("classifyTelegramInboundMedia: audio -> audio", () => {
  const result = classifyTelegramInboundMedia({ audio: {}, document: {}, file: { mimeType: "audio/mpeg" } });
  assert.equal(result?.kind, "audio");
});

test("classifyTelegramInboundMedia: sticker wins over the generic document fallback", () => {
  const result = classifyTelegramInboundMedia({ sticker: {}, document: {}, file: { mimeType: "image/webp" } });
  assert.equal(result?.kind, "image");
});

test("classifyTelegramInboundMedia: plain document -> file", () => {
  const result = classifyTelegramInboundMedia({ document: {}, file: { mimeType: "application/zip" } });
  assert.equal(result?.kind, "file");
});

test("classifyTelegramInboundMedia: no media on the message -> null", () => {
  assert.equal(classifyTelegramInboundMedia({}), null);
  assert.equal(classifyTelegramInboundMedia(undefined), null);
  assert.equal(classifyTelegramInboundMedia(null), null);
  // `photo` truthy but no `.file` (shouldn't happen in real GramJS, but the
  // guard must not crash on it) -> null.
  assert.equal(classifyTelegramInboundMedia({ photo: {} }), null);
});

// ===========================================================================
// downloadAndStoreTelegramMedia (mocks GramJS's client.downloadMedia)
// ===========================================================================

test("downloadAndStoreTelegramMedia writes bytes under <stateDir>/telegram-media/ and returns a matching descriptor", async () => {
  const stateDir = await mkdtemp(path.join(tmpdir(), "empyralis-telegram-media-"));
  try {
    const buffer = Buffer.from("fake-jpeg-bytes");
    const client = { downloadMedia: async () => buffer };
    const result = await downloadAndStoreTelegramMedia({
      client,
      rawMessage: {},
      classification: { kind: "image", file: { mimeType: "image/jpeg", size: buffer.length } },
      stateDir,
      externalMessageId: "12345",
    });
    assert.ok(result, "expected a media descriptor, got null");
    assert.equal(result?.kind, "image");
    assert.equal(result?.mimeType, "image/jpeg");
    assert.equal(result?.sizeBytes, buffer.length);
    assert.ok(result?.mediaId.startsWith("telegram-media/"), `expected mediaId under telegram-media/, got ${result?.mediaId}`);
    assert.ok(result?.mediaId.endsWith(".jpg"), `expected a .jpg extension, got ${result?.mediaId}`);

    // Proves the "media_id is a path relative to the shared gateway state
    // dir" contract end-to-end: joining media_id onto stateDir must resolve
    // to the exact bytes GramJS "downloaded".
    const onDisk = await readFile(path.join(stateDir, result!.mediaId));
    assert.deepEqual(onDisk, buffer);
  } finally {
    await rm(stateDir, { recursive: true, force: true });
  }
});

test("downloadAndStoreTelegramMedia carries filename and duration_sec through when Telegram reports them", async () => {
  const stateDir = await mkdtemp(path.join(tmpdir(), "empyralis-telegram-media-"));
  try {
    const buffer = Buffer.from("ogg-bytes");
    const client = { downloadMedia: async () => buffer };
    const result = await downloadAndStoreTelegramMedia({
      client,
      rawMessage: {},
      classification: { kind: "voice", file: { mimeType: "audio/ogg", duration: 12, name: "voice-note.ogg" } },
      stateDir,
      externalMessageId: "vn-1",
    });
    assert.equal(result?.durationSec, 12);
    assert.equal(result?.filename, "voice-note.ogg");
  } finally {
    await rm(stateDir, { recursive: true, force: true });
  }
});

test("downloadAndStoreTelegramMedia skips the download entirely when the declared size already exceeds maxBytes", async () => {
  const stateDir = await mkdtemp(path.join(tmpdir(), "empyralis-telegram-media-"));
  try {
    let downloadCalled = false;
    const client = { downloadMedia: async () => { downloadCalled = true; return Buffer.alloc(10); } };
    const result = await downloadAndStoreTelegramMedia({
      client,
      rawMessage: {},
      classification: { kind: "video", file: { mimeType: "video/mp4", size: 100 } },
      stateDir,
      externalMessageId: "big-1",
      maxBytes: 50,
    });
    assert.equal(result, null);
    assert.equal(downloadCalled, false, "must not call client.downloadMedia once the declared size already exceeds the cap");
  } finally {
    await rm(stateDir, { recursive: true, force: true });
  }
});

test("downloadAndStoreTelegramMedia discards (and never writes to disk) an oversized download when the declared size was missing", async () => {
  const stateDir = await mkdtemp(path.join(tmpdir(), "empyralis-telegram-media-"));
  try {
    const bigBuffer = Buffer.alloc(100);
    const client = { downloadMedia: async () => bigBuffer };
    const result = await downloadAndStoreTelegramMedia({
      client,
      rawMessage: {},
      classification: { kind: "file", file: { mimeType: "application/pdf" } }, // no declared size
      stateDir,
      externalMessageId: "big-2",
      maxBytes: 50,
    });
    assert.equal(result, null);
    const entries = await readdir(path.join(stateDir, "telegram-media")).catch(() => []);
    assert.deepEqual(entries, [], "an oversized download must never be persisted to disk");
  } finally {
    await rm(stateDir, { recursive: true, force: true });
  }
});

test("downloadAndStoreTelegramMedia returns null (never throws) when client.downloadMedia rejects", async () => {
  const stateDir = await mkdtemp(path.join(tmpdir(), "empyralis-telegram-media-"));
  try {
    const client = { downloadMedia: async () => { throw new Error("FILE_REFERENCE_EXPIRED"); } };
    const result = await downloadAndStoreTelegramMedia({
      client,
      rawMessage: {},
      classification: { kind: "image", file: { mimeType: "image/jpeg" } },
      stateDir,
      externalMessageId: "err-1",
    });
    assert.equal(result, null);
  } finally {
    await rm(stateDir, { recursive: true, force: true });
  }
});

test("downloadAndStoreTelegramMedia returns null when downloadMedia resolves to undefined (nothing to save)", async () => {
  const stateDir = await mkdtemp(path.join(tmpdir(), "empyralis-telegram-media-"));
  try {
    const client = { downloadMedia: async () => undefined };
    const result = await downloadAndStoreTelegramMedia({
      client,
      rawMessage: {},
      classification: { kind: "image", file: { mimeType: "image/jpeg" } },
      stateDir,
      externalMessageId: "empty-1",
    });
    assert.equal(result, null);
  } finally {
    await rm(stateDir, { recursive: true, force: true });
  }
});

// ===========================================================================
// message-mapper.ts: mapTelegramInboundMessage
// ===========================================================================

test("mapTelegramInboundMessage: translates the media array to the snake_case wire shape", () => {
  const mapped = mapTelegramInboundMessage({
    externalMessageId: "1",
    remoteJid: "user-1",
    text: "check this out",
    media: [
      { kind: "image", mediaId: "telegram-media/1-a.jpg", mimeType: "image/jpeg", sizeBytes: 2048 },
    ],
  });
  assert.ok(mapped);
  assert.deepEqual(mapped?.message.media, [
    {
      kind: "image",
      media_id: "telegram-media/1-a.jpg",
      mime_type: "image/jpeg",
      filename: undefined,
      size_bytes: 2048,
      duration_sec: undefined,
    },
  ]);
});

test("mapTelegramInboundMessage: a caption-less media message (empty text) is NOT dropped", () => {
  const mapped = mapTelegramInboundMessage({
    externalMessageId: "2",
    remoteJid: "user-1",
    text: "",
    media: [{ kind: "voice", mediaId: "telegram-media/2-b.ogg", mimeType: "audio/ogg", sizeBytes: 512, durationSec: 4 }],
  });
  assert.ok(mapped, "a media-only message must still be published");
  assert.equal(mapped?.message.text, "");
  assert.equal(mapped?.message.media?.length, 1);
});

test("mapTelegramInboundMessage: neither text nor media is still dropped (regression — unchanged pre-media behavior)", () => {
  assert.equal(
    mapTelegramInboundMessage({ externalMessageId: "3", remoteJid: "user-1", text: "" }),
    null,
  );
});

test("mapTelegramInboundMessage: text-only messages have no media key at all (regression — unchanged shape)", () => {
  const mapped = mapTelegramInboundMessage({ externalMessageId: "4", remoteJid: "user-1", text: "hello" });
  assert.ok(mapped);
  assert.equal("media" in mapped!.message, false);
});

// ===========================================================================
// message-mapper.ts: mapTelegramOutboundMediaItem
// ===========================================================================

test("mapTelegramOutboundMediaItem: normalizes a valid item to camelCase", () => {
  const item = mapTelegramOutboundMediaItem({
    kind: "image",
    source_path: "/tmp/photo.jpg",
    mime_type: "image/jpeg",
    caption: "a photo",
  });
  assert.deepEqual(item, {
    kind: "image",
    sourcePath: "/tmp/photo.jpg",
    sourceUrl: undefined,
    mimeType: "image/jpeg",
    caption: "a photo",
    asVoice: false,
  });
});

test("mapTelegramOutboundMediaItem: accepts source_url in place of source_path", () => {
  const item = mapTelegramOutboundMediaItem({ kind: "file", source_url: "https://example.com/doc.pdf" });
  assert.equal(item.sourceUrl, "https://example.com/doc.pdf");
  assert.equal(item.sourcePath, undefined);
});

test("mapTelegramOutboundMediaItem: as_voice survives normalization", () => {
  const item = mapTelegramOutboundMediaItem({ kind: "voice", source_path: "/tmp/a.ogg", as_voice: true });
  assert.equal(item.asVoice, true);
});

test("mapTelegramOutboundMediaItem: rejects an unsupported kind", () => {
  assert.throws(
    () => mapTelegramOutboundMediaItem({ kind: "sticker", source_path: "/tmp/x.webp" }),
    /Unsupported Telegram outbound media kind/,
  );
});

test("mapTelegramOutboundMediaItem: rejects an item with neither source_path nor source_url", () => {
  assert.throws(
    () => mapTelegramOutboundMediaItem({ kind: "image" }),
    /source_path or source_url/,
  );
});

// ===========================================================================
// message-mapper.ts: mapTelegramOutboundResult
// ===========================================================================

test("mapTelegramOutboundResult: includes media only when the caller passed a non-empty array", () => {
  const withMedia = mapTelegramOutboundResult(
    { idempotencyKey: "k1", remoteJid: "user-1", text: "", media: [{ kind: "image", external_message_id: "tg-1" }] },
    { externalMessageId: "tg-1", remoteJid: "user-1" },
  );
  assert.deepEqual(withMedia.media, [{ kind: "image", external_message_id: "tg-1" }]);

  const withoutMedia = mapTelegramOutboundResult(
    { idempotencyKey: "k2", remoteJid: "user-1", text: "hello" },
    { externalMessageId: "tg-2", remoteJid: "user-1" },
  );
  assert.equal("media" in withoutMedia, false, "text-only results must not gain a media key (regression)");
});

// ===========================================================================
// End-to-end: TelegramPersonalRuntime with an injected mock adapter client
// (mirrors the pattern in channel-draft-outbound.test.ts).
// ===========================================================================

function outboundFrame(patch: Record<string, unknown>) {
  return {
    kind: "request" as const,
    id: String(patch.idempotency_key || "request-1"),
    type: "channel.outbound" as const,
    ts: new Date().toISOString(),
    payload: {
      channel_key: TELEGRAM_PERSONAL_CHANNEL_KEY,
      provider: TELEGRAM_PERSONAL_PROVIDER,
      remote_jid: "user-1",
      idempotency_key: "reply-1",
      text: "",
      ...patch,
    },
  };
}

test("inbound end-to-end: a media message's published channel.inbound event matches the contract shape", async () => {
  const rootDir = await mkdtemp(path.join(tmpdir(), "empyralis-telegram-inbound-media-"));
  try {
    const runtime = new TelegramPersonalRuntime(new GatewayStateDb(rootDir));
    const published: Array<{ type: string; payload: any }> = [];
    runtime.setPublisher({
      publishEvent: async (type, payload) => {
        published.push({ type, payload });
      },
      publishStateUpdate: async () => undefined,
    });

    await (runtime as any).handleInboundMessage({
      externalMessageId: "999",
      remoteJid: "user-1",
      text: "check this out",
      media: [{ kind: "image", mediaId: "telegram-media/999-abc.jpg", mimeType: "image/jpeg", sizeBytes: 1234 }],
    });

    assert.equal(published.length, 1);
    assert.equal(published[0].type, "channel.inbound");
    const payload = published[0].payload;
    assert.equal(payload.channel_key, TELEGRAM_PERSONAL_CHANNEL_KEY);
    assert.equal(payload.message.text, "check this out");
    assert.deepEqual(payload.message.media, [
      {
        kind: "image",
        media_id: "telegram-media/999-abc.jpg",
        mime_type: "image/jpeg",
        filename: undefined,
        size_bytes: 1234,
        duration_sec: undefined,
      },
    ]);
  } finally {
    await rm(rootDir, { recursive: true, force: true });
  }
});

test("inbound end-to-end: a caption-less media message is published (not silently dropped)", async () => {
  const rootDir = await mkdtemp(path.join(tmpdir(), "empyralis-telegram-inbound-media-"));
  try {
    const runtime = new TelegramPersonalRuntime(new GatewayStateDb(rootDir));
    const published: Array<{ payload: any }> = [];
    runtime.setPublisher({
      publishEvent: async (_type, payload) => { published.push({ payload }); },
      publishStateUpdate: async () => undefined,
    });

    await (runtime as any).handleInboundMessage({
      externalMessageId: "1000",
      remoteJid: "user-1",
      text: "",
      media: [{ kind: "voice", mediaId: "telegram-media/1000-x.ogg", mimeType: "audio/ogg", sizeBytes: 500, durationSec: 3 }],
    });

    assert.equal(published.length, 1);
    assert.equal(published[0].payload.message.text, "");
    assert.equal(published[0].payload.message.media.length, 1);
  } finally {
    await rm(rootDir, { recursive: true, force: true });
  }
});

test("inbound end-to-end: a message with neither text nor media is still dropped (regression)", async () => {
  const rootDir = await mkdtemp(path.join(tmpdir(), "empyralis-telegram-inbound-media-"));
  try {
    const runtime = new TelegramPersonalRuntime(new GatewayStateDb(rootDir));
    const published: unknown[] = [];
    runtime.setPublisher({
      publishEvent: async (_type, payload) => { published.push(payload); },
      publishStateUpdate: async () => undefined,
    });

    await (runtime as any).handleInboundMessage({ externalMessageId: "1001", remoteJid: "user-1", text: "" });

    assert.equal(published.length, 0);
  } finally {
    await rm(rootDir, { recursive: true, force: true });
  }
});

test("outbound end-to-end: a media-only dispatch (no text) calls sendMedia, never sendMessage", async () => {
  const rootDir = await mkdtemp(path.join(tmpdir(), "empyralis-telegram-outbound-media-"));
  try {
    const runtime = new TelegramPersonalRuntime(new GatewayStateDb(rootDir));
    const sentMedia: Array<{ remoteJid: string; media: any }> = [];
    let sendMessageCalled = false;
    (runtime as any).client = {
      sendMessage: async () => { sendMessageCalled = true; return { externalMessageId: "should-not-happen" }; },
      sendMedia: async (remoteJid: string, media: unknown) => {
        sentMedia.push({ remoteJid, media });
        return { externalMessageId: "tg-media-1", remoteJid };
      },
    };

    const result = await runtime.handleChannelOutbound(outboundFrame({
      idempotency_key: "media-send-1",
      text: "",
      media: [{ kind: "image", source_path: "/tmp/photo.jpg", mime_type: "image/jpeg", caption: "look at this" }],
    }) as any);

    assert.equal(sendMessageCalled, false, "must not send a separate text message when text is empty");
    assert.equal(sentMedia.length, 1);
    assert.equal(sentMedia[0].remoteJid, "user-1");
    assert.deepEqual(sentMedia[0].media, {
      kind: "image",
      sourcePath: "/tmp/photo.jpg",
      sourceUrl: undefined,
      mimeType: "image/jpeg",
      caption: "look at this",
      asVoice: false,
    });
    assert.equal(result.delivered, true);
    assert.deepEqual(result.media, [{ kind: "image", external_message_id: "tg-media-1" }]);
  } finally {
    await rm(rootDir, { recursive: true, force: true });
  }
});

test("outbound end-to-end: text and media together send the media then a trailing text message", async () => {
  const rootDir = await mkdtemp(path.join(tmpdir(), "empyralis-telegram-outbound-media-"));
  try {
    const runtime = new TelegramPersonalRuntime(new GatewayStateDb(rootDir));
    const calls: string[] = [];
    (runtime as any).client = {
      sendMessage: async (remoteJid: string, text: string) => {
        calls.push(`text:${text}`);
        return { externalMessageId: "tg-text-1", remoteJid };
      },
      sendMedia: async (remoteJid: string, media: any) => {
        calls.push(`media:${media.kind}`);
        return { externalMessageId: "tg-media-1", remoteJid };
      },
    };

    const result = await runtime.handleChannelOutbound(outboundFrame({
      idempotency_key: "combo-1",
      text: "here's the file",
      media: [{ kind: "file", source_url: "https://example.com/doc.pdf" }],
    }) as any);

    assert.deepEqual(calls, ["media:file", "text:here's the file"]);
    assert.equal(result.external_message_id, "tg-text-1");
    assert.deepEqual(result.media, [{ kind: "file", external_message_id: "tg-media-1" }]);
    assert.equal(result.text, "here's the file");
  } finally {
    await rm(rootDir, { recursive: true, force: true });
  }
});

test("outbound end-to-end: voice kind + as_voice reaches sendMedia unchanged (mapping boundary)", async () => {
  const rootDir = await mkdtemp(path.join(tmpdir(), "empyralis-telegram-outbound-media-"));
  try {
    const runtime = new TelegramPersonalRuntime(new GatewayStateDb(rootDir));
    let capturedMedia: any;
    (runtime as any).client = {
      sendMessage: async () => ({ externalMessageId: "unused" }),
      sendMedia: async (remoteJid: string, media: any) => {
        capturedMedia = media;
        return { externalMessageId: "tg-voice-1", remoteJid };
      },
    };

    await runtime.handleChannelOutbound(outboundFrame({
      idempotency_key: "voice-1",
      text: "",
      media: [{ kind: "voice", source_path: "/tmp/note.ogg", as_voice: true }],
    }) as any);

    assert.equal(capturedMedia.kind, "voice");
    assert.equal(capturedMedia.asVoice, true);
    assert.equal(capturedMedia.sourcePath, "/tmp/note.ogg");
  } finally {
    await rm(rootDir, { recursive: true, force: true });
  }
});

test("outbound end-to-end: an unsupported media kind rejects the dispatch before any send is attempted", async () => {
  const rootDir = await mkdtemp(path.join(tmpdir(), "empyralis-telegram-outbound-media-"));
  try {
    const runtime = new TelegramPersonalRuntime(new GatewayStateDb(rootDir));
    let sent = false;
    (runtime as any).client = {
      sendMessage: async () => { sent = true; return {}; },
      sendMedia: async () => { sent = true; return {}; },
    };

    await assert.rejects(
      () => runtime.handleChannelOutbound(outboundFrame({
        idempotency_key: "bad-kind-1",
        text: "",
        media: [{ kind: "sticker", source_path: "/tmp/x.webp" }],
      }) as any),
      /Unsupported Telegram outbound media kind/,
    );
    assert.equal(sent, false);
  } finally {
    await rm(rootDir, { recursive: true, force: true });
  }
});

test("outbound end-to-end: a media item with no source rejects the dispatch", async () => {
  const rootDir = await mkdtemp(path.join(tmpdir(), "empyralis-telegram-outbound-media-"));
  try {
    const runtime = new TelegramPersonalRuntime(new GatewayStateDb(rootDir));
    (runtime as any).client = {
      sendMessage: async () => ({}),
      sendMedia: async () => ({}),
    };

    await assert.rejects(
      () => runtime.handleChannelOutbound(outboundFrame({
        idempotency_key: "no-source-1",
        text: "",
        media: [{ kind: "image" }],
      }) as any),
      /source_path or source_url/,
    );
  } finally {
    await rm(rootDir, { recursive: true, force: true });
  }
});

test("outbound end-to-end: media on an adapter without sendMedia throws a clear error instead of dropping the attachment", async () => {
  const rootDir = await mkdtemp(path.join(tmpdir(), "empyralis-telegram-outbound-media-"));
  try {
    const runtime = new TelegramPersonalRuntime(new GatewayStateDb(rootDir));
    (runtime as any).client = {
      sendMessage: async () => ({ externalMessageId: "x" }),
      // sendMedia intentionally omitted
    };

    await assert.rejects(
      () => runtime.handleChannelOutbound(outboundFrame({
        idempotency_key: "no-adapter-support-1",
        text: "",
        media: [{ kind: "image", source_path: "/tmp/a.jpg" }],
      }) as any),
      /does not support sending media/,
    );
  } finally {
    await rm(rootDir, { recursive: true, force: true });
  }
});

test("outbound end-to-end: retrying the same idempotency_key does not re-send media", async () => {
  const rootDir = await mkdtemp(path.join(tmpdir(), "empyralis-telegram-outbound-media-"));
  try {
    const runtime = new TelegramPersonalRuntime(new GatewayStateDb(rootDir));
    let sendMediaCalls = 0;
    (runtime as any).client = {
      sendMessage: async () => ({ externalMessageId: "x" }),
      sendMedia: async (remoteJid: string) => {
        sendMediaCalls += 1;
        return { externalMessageId: "tg-once", remoteJid };
      },
    };
    const payload = outboundFrame({
      idempotency_key: "dedupe-1",
      text: "",
      media: [{ kind: "image", source_path: "/tmp/a.jpg" }],
    });

    const first = await runtime.handleChannelOutbound(payload as any);
    const second = await runtime.handleChannelOutbound(payload as any);

    assert.equal(sendMediaCalls, 1);
    assert.equal(first.delivered, true);
    assert.equal(second.delivered, true);
    assert.equal(second.external_message_id, first.external_message_id);
  } finally {
    await rm(rootDir, { recursive: true, force: true });
  }
});

test("outbound end-to-end: a text-only dispatch never calls sendMedia and the result has no media key (regression, unchanged shape)", async () => {
  const rootDir = await mkdtemp(path.join(tmpdir(), "empyralis-telegram-outbound-media-"));
  try {
    const runtime = new TelegramPersonalRuntime(new GatewayStateDb(rootDir));
    let sendMediaCalled = false;
    (runtime as any).client = {
      sendMessage: async (remoteJid: string, text: string) => ({ externalMessageId: "tg-plain", remoteJid, text }),
      sendMedia: async () => { sendMediaCalled = true; return {}; },
    };

    const result = await runtime.handleChannelOutbound(outboundFrame({
      idempotency_key: "text-only-1",
      text: "hello",
    }) as any);

    assert.equal(sendMediaCalled, false);
    assert.equal("media" in result, false);
    assert.equal(result.text, "hello");
  } finally {
    await rm(rootDir, { recursive: true, force: true });
  }
});

test("outbound end-to-end: neither text nor media throws the (now media-aware) validation error — regression", async () => {
  const rootDir = await mkdtemp(path.join(tmpdir(), "empyralis-telegram-outbound-media-"));
  try {
    const runtime = new TelegramPersonalRuntime(new GatewayStateDb(rootDir));
    (runtime as any).client = {
      sendMessage: async () => ({}),
      sendMedia: async () => ({}),
    };

    await assert.rejects(
      () => runtime.handleChannelOutbound(outboundFrame({ idempotency_key: "empty-1", text: "" }) as any),
      /requires idempotency_key, remote_jid, and text or media/,
    );
  } finally {
    await rm(rootDir, { recursive: true, force: true });
  }
});

// ===========================================================================
// Capability manifest flip
// ===========================================================================

test("TelegramPersonalRuntime.getManifest() advertises image/file/voice media support", () => {
  const runtime = new TelegramPersonalRuntime(new GatewayStateDb("/tmp/unused-empyralis-telegram-manifest"));
  const manifest = runtime.getManifest();
  assert.deepEqual(manifest.media, { text: true, images: true, files: true, reactions: false, voice: true });
});
