import { execFile } from "child_process";
import { mkdtemp, readFile, rm, writeFile } from "fs/promises";
import http from "http";
import { tmpdir } from "os";
import path from "path";
import { promisify } from "util";
import test from "node:test";
import assert from "node:assert/strict";

import { WhatsAppPersonalRuntime } from "../channels/whatsapp/runtime";
import { GatewayStateDb } from "../state/db";
import { WHATSAPP_PERSONAL_CHANNEL_KEY, WHATSAPP_PERSONAL_PROVIDER } from "../channels/whatsapp/session-store";
import {
  defaultWhatsAppMimeTypeForKind,
  detectWhatsAppInboundMedia,
  mapWhatsAppInboundMessage,
  normalizeWhatsAppOutboundMediaList,
} from "../channels/whatsapp/message-mapper";

const execFileAsync = promisify(execFile);

// ---------------------------------------------------------------------------
// Fixtures
// ---------------------------------------------------------------------------

/** Hand-built minimal 16-bit PCM mono WAV -- deliberately NOT Opus/OGG, so
 *  tests that send it as a "voice" item exercise the real transcode path. */
function buildSilentWavBuffer(durationSec = 0.3, sampleRate = 8000): Buffer {
  const numSamples = Math.floor(durationSec * sampleRate);
  const dataSize = numSamples * 2;
  const buffer = Buffer.alloc(44 + dataSize);
  buffer.write("RIFF", 0, "ascii");
  buffer.writeUInt32LE(36 + dataSize, 4);
  buffer.write("WAVE", 8, "ascii");
  buffer.write("fmt ", 12, "ascii");
  buffer.writeUInt32LE(16, 16);
  buffer.writeUInt16LE(1, 20);
  buffer.writeUInt16LE(1, 22);
  buffer.writeUInt32LE(sampleRate, 24);
  buffer.writeUInt32LE(sampleRate * 2, 28);
  buffer.writeUInt16LE(2, 32);
  buffer.writeUInt16LE(16, 34);
  buffer.write("data", 36, "ascii");
  buffer.writeUInt32LE(dataSize, 40);
  return buffer;
}

function buildMockAdapter(opts?: { downloadMedia?: (rawMessage: Record<string, unknown>, ctx: unknown) => Promise<Buffer> }) {
  const sent: Array<{ jid: string; content: Record<string, unknown>; options?: Record<string, unknown> }> = [];
  let sendCounter = 0;
  const socket = {
    ev: { on: () => undefined },
    sendMessage: async (jid: string, content: Record<string, unknown>, options?: Record<string, unknown>) => {
      sendCounter += 1;
      sent.push({ jid, content, options });
      return { key: { id: `wamid-out-${sendCounter}`, remoteJid: jid } };
    },
    sendPresenceUpdate: async () => undefined,
    user: { id: "me@s.whatsapp.net", name: "Sage Owner" },
    logout: async () => undefined,
    end: () => undefined,
  };
  const adapter = {
    loadAuthState: async () => ({
      state: { creds: { registered: true } },
      saveCreds: async () => undefined,
    }),
    createSocket: () => socket,
    disconnectReason: { loggedOut: 401, restartRequired: 515 },
    browserDescriptor: () => ["Empyralis", "Chrome", "1.0"],
    fetchWaWebVersion: async () => undefined,
    downloadMedia:
      opts?.downloadMedia ??
      (async () => {
        throw new Error("downloadMedia was not expected to be called in this test");
      }),
  };
  return { adapter, socket, sent };
}

function outboundFrame(patch: Record<string, unknown>) {
  return {
    kind: "request" as const,
    id: String(patch.idempotency_key || "request-1"),
    type: "channel.outbound" as const,
    ts: new Date().toISOString(),
    payload: {
      channel_key: WHATSAPP_PERSONAL_CHANNEL_KEY,
      provider: WHATSAPP_PERSONAL_PROVIDER,
      operation: "send_final",
      remote_jid: "15551234567@s.whatsapp.net",
      text: "",
      ...patch,
    },
  };
}

// ---------------------------------------------------------------------------
// message-mapper.ts -- pure functions
// ---------------------------------------------------------------------------

test("detectWhatsAppInboundMedia: classifies image/video/voice/audio/document/sticker, with mimetype fallbacks", () => {
  const image = detectWhatsAppInboundMedia({ imageMessage: { mimetype: "image/png", fileLength: 100 } });
  assert.equal(image?.kind, "image");
  assert.equal(image?.mimeType, "image/png");
  assert.equal(image?.declaredSizeBytes, 100);

  const imageNoMime = detectWhatsAppInboundMedia({ imageMessage: {} });
  assert.equal(imageNoMime?.mimeType, "image/jpeg", "falls back to image/jpeg when Baileys omits mimetype");

  const voice = detectWhatsAppInboundMedia({ audioMessage: { ptt: true, seconds: 5, mimetype: "audio/ogg; codecs=opus" } });
  assert.equal(voice?.kind, "voice");
  assert.equal(voice?.durationSec, 5);

  const audio = detectWhatsAppInboundMedia({ audioMessage: { ptt: false } });
  assert.equal(audio?.kind, "audio", "non-ptt audioMessage is 'audio', not 'voice'");

  const video = detectWhatsAppInboundMedia({ videoMessage: { seconds: 12, mimetype: "video/mp4" } });
  assert.equal(video?.kind, "video");
  assert.equal(video?.durationSec, 12);

  const document = detectWhatsAppInboundMedia({
    documentMessage: { fileName: "report.pdf", mimetype: "application/pdf" },
  });
  assert.equal(document?.kind, "file");
  assert.equal(document?.filename, "report.pdf");

  const sticker = detectWhatsAppInboundMedia({ stickerMessage: {} });
  assert.equal(sticker?.kind, "image", "stickers map to 'image' -- the contract has no dedicated sticker kind");
  assert.equal(sticker?.mimeType, "image/webp");

  assert.equal(detectWhatsAppInboundMedia({ conversation: "hi" }), undefined, "pure text messages have no media");
  assert.equal(detectWhatsAppInboundMedia(undefined), undefined);
});

test("mapWhatsAppInboundMessage: a media message with no caption is published, not dropped", () => {
  const raw = {
    key: { id: "wamid-1", remoteJid: "15551234567@s.whatsapp.net", fromMe: false },
    message: { imageMessage: { mimetype: "image/jpeg" } },
    messageTimestamp: Math.floor(Date.now() / 1000),
  };
  const mediaItem = {
    kind: "image" as const,
    media_id: "media-abc",
    mime_type: "image/jpeg",
    size_bytes: 1234,
  };
  const mapped = mapWhatsAppInboundMessage(raw, { media: [mediaItem] });
  assert.ok(mapped, "a media-only message (empty text) must NOT be dropped");
  assert.equal(mapped!.message.text, "");
  assert.deepEqual(mapped!.message.media, [mediaItem]);
});

test("mapWhatsAppInboundMessage: still drops a message with neither text nor media (unchanged behavior)", () => {
  const raw = {
    key: { id: "wamid-2", remoteJid: "15551234567@s.whatsapp.net", fromMe: false },
    message: {},
    messageTimestamp: Math.floor(Date.now() / 1000),
  };
  assert.equal(mapWhatsAppInboundMessage(raw), null);
});

test("mapWhatsAppInboundMessage: a text message with no media omits message.media entirely", () => {
  const raw = {
    key: { id: "wamid-3", remoteJid: "15551234567@s.whatsapp.net", fromMe: false },
    message: { conversation: "just text" },
    messageTimestamp: Math.floor(Date.now() / 1000),
  };
  const mapped = mapWhatsAppInboundMessage(raw);
  assert.ok(mapped);
  assert.equal(mapped!.message.media, undefined);
});

test("normalizeWhatsAppOutboundMediaList: validates kind and requires a source, dropping malformed entries", () => {
  const result = normalizeWhatsAppOutboundMediaList([
    { kind: "image", source_path: "/tmp/a.jpg", caption: "hi" },
    { kind: "bogus", source_path: "/tmp/b.jpg" }, // invalid kind -> dropped
    { kind: "video" }, // no source -> dropped
    { kind: "voice", source_url: "https://example.com/a.mp3", as_voice: true },
    "not-an-object",
    null,
  ]);
  assert.equal(result.length, 2);
  assert.equal(result[0].kind, "image");
  assert.equal(result[0].source_path, "/tmp/a.jpg");
  assert.equal(result[0].caption, "hi");
  assert.equal(result[1].kind, "voice");
  assert.equal(result[1].source_url, "https://example.com/a.mp3");
  assert.equal(result[1].as_voice, true);
});

test("normalizeWhatsAppOutboundMediaList: non-array input yields an empty list", () => {
  assert.deepEqual(normalizeWhatsAppOutboundMediaList(undefined), []);
  assert.deepEqual(normalizeWhatsAppOutboundMediaList("nope"), []);
});

test("defaultWhatsAppMimeTypeForKind: sensible fallback per kind", () => {
  assert.equal(defaultWhatsAppMimeTypeForKind("image"), "image/jpeg");
  assert.equal(defaultWhatsAppMimeTypeForKind("video"), "video/mp4");
  assert.equal(defaultWhatsAppMimeTypeForKind("voice"), "audio/ogg; codecs=opus");
  assert.equal(defaultWhatsAppMimeTypeForKind("audio"), "audio/mpeg");
  assert.equal(defaultWhatsAppMimeTypeForKind("file"), "application/octet-stream");
});

// ---------------------------------------------------------------------------
// Manifest
// ---------------------------------------------------------------------------

test("getManifest(): media capability flags are flipped on for images/files/voice", async () => {
  const rootDir = await mkdtemp(path.join(tmpdir(), "empyralis-whatsapp-media-manifest-"));
  try {
    const runtime = new WhatsAppPersonalRuntime(new GatewayStateDb(rootDir));
    const manifest = runtime.getManifest();
    assert.deepEqual(manifest.media, { text: true, images: true, files: true, reactions: false, voice: true });
  } finally {
    await rm(rootDir, { recursive: true, force: true });
  }
});

// ---------------------------------------------------------------------------
// Inbound integration -- runtime.ts wired to a mock Baileys adapter
// ---------------------------------------------------------------------------

test("inbound: a caption-less image downloads via Baileys, is stored under <stateDir>/whatsapp/media/<media_id>, and publishes the media contract shape", async () => {
  const rootDir = await mkdtemp(path.join(tmpdir(), "empyralis-whatsapp-media-inbound-"));
  try {
    const fakeBytes = Buffer.from("fake-jpeg-bytes-000111");
    let downloadCalls = 0;
    const { adapter } = buildMockAdapter({
      downloadMedia: async () => {
        downloadCalls += 1;
        return fakeBytes;
      },
    });
    const published: any[] = [];
    const runtime = new WhatsAppPersonalRuntime(new GatewayStateDb(rootDir), {
      adapter: adapter as any,
      publisher: {
        publishEvent: async (_type, payload) => {
          published.push(payload);
        },
        publishStateUpdate: async () => undefined,
      },
    });
    await (runtime as any).connectSocketInternal();

    await (runtime as any).handleMessagesUpsert({
      messages: [
        {
          key: { id: "wamid-img-1", remoteJid: "15551234567@s.whatsapp.net", fromMe: false },
          message: { imageMessage: { mimetype: "image/jpeg" } },
          messageTimestamp: Math.floor(Date.now() / 1000),
        },
      ],
    });

    assert.equal(downloadCalls, 1, "should have attempted exactly one media download");
    assert.equal(published.length, 1, "a caption-less image message must still be published (not dropped)");
    const media = (published[0] as any).message.media;
    assert.ok(Array.isArray(media) && media.length === 1);
    const item = media[0];
    assert.equal(item.kind, "image");
    assert.equal(item.mime_type, "image/jpeg");
    assert.equal(item.size_bytes, fakeBytes.length);
    assert.ok(typeof item.media_id === "string" && item.media_id.length > 0);
    assert.equal(item.filename, undefined, "images carry no filename");

    // media_id MUST be a path RELATIVE to the gateway state dir root (the
    // exact contract Telegram uses -- the co-located server resolves it by
    // joining onto the same root), never absolute and never a bare token.
    assert.ok(item.media_id.startsWith("whatsapp/media/"), "media_id is namespaced under whatsapp/media/");
    assert.ok(!path.isAbsolute(item.media_id), "media_id must be RELATIVE to the state dir root, not absolute");
    assert.ok(item.media_id.includes("wamid-img-1"), "media_id basename includes the (sanitized) external message id");

    const storedPath = path.join(rootDir, item.media_id);
    const storedBytes = await readFile(storedPath);
    assert.deepEqual(
      storedBytes,
      fakeBytes,
      "server resolves media_id by joining it onto the state dir root -- the exact bytes must live there",
    );
  } finally {
    await rm(rootDir, { recursive: true, force: true });
  }
});

test("inbound: a voice note (ptt) is classified 'voice' and carries duration_sec", async () => {
  const rootDir = await mkdtemp(path.join(tmpdir(), "empyralis-whatsapp-media-voice-inbound-"));
  try {
    const fakeOpus = Buffer.from("fake-opus-bytes");
    const { adapter } = buildMockAdapter({ downloadMedia: async () => fakeOpus });
    const published: any[] = [];
    const runtime = new WhatsAppPersonalRuntime(new GatewayStateDb(rootDir), {
      adapter: adapter as any,
      publisher: { publishEvent: async (_t, p) => { published.push(p); }, publishStateUpdate: async () => undefined },
    });
    await (runtime as any).connectSocketInternal();

    await (runtime as any).handleMessagesUpsert({
      messages: [
        {
          key: { id: "wamid-voice-1", remoteJid: "15551234567@s.whatsapp.net", fromMe: false },
          message: { audioMessage: { ptt: true, seconds: 7, mimetype: "audio/ogg; codecs=opus" } },
          messageTimestamp: Math.floor(Date.now() / 1000),
        },
      ],
    });

    assert.equal(published.length, 1);
    const item = (published[0] as any).message.media[0];
    assert.equal(item.kind, "voice");
    assert.equal(item.duration_sec, 7);
    assert.equal(item.mime_type, "audio/ogg; codecs=opus");
  } finally {
    await rm(rootDir, { recursive: true, force: true });
  }
});

test("inbound: a declared-oversized attachment skips the download entirely; caption-only text still gets through", async () => {
  const rootDir = await mkdtemp(path.join(tmpdir(), "empyralis-whatsapp-media-oversize-"));
  try {
    let downloadCalls = 0;
    const { adapter } = buildMockAdapter({
      downloadMedia: async () => {
        downloadCalls += 1;
        return Buffer.alloc(0);
      },
    });
    const published: any[] = [];
    const runtime = new WhatsAppPersonalRuntime(new GatewayStateDb(rootDir), {
      adapter: adapter as any,
      publisher: { publishEvent: async (_t, p) => { published.push(p); }, publishStateUpdate: async () => undefined },
    });
    await (runtime as any).connectSocketInternal();

    await (runtime as any).handleMessagesUpsert({
      messages: [
        {
          key: { id: "wamid-big-1", remoteJid: "15551234567@s.whatsapp.net", fromMe: false },
          message: { imageMessage: { mimetype: "image/jpeg", caption: "too big", fileLength: 26 * 1024 * 1024 } },
          messageTimestamp: Math.floor(Date.now() / 1000),
        },
      ],
    });

    assert.equal(downloadCalls, 0, "a declared size over the ~25MB cap must skip the download entirely");
    assert.equal(published.length, 1, "the message should still be published using its caption as text");
    assert.equal((published[0] as any).message.text, "too big");
    assert.equal((published[0] as any).message.media, undefined, "no media item should be attached when the download was skipped");
  } finally {
    await rm(rootDir, { recursive: true, force: true });
  }
});

test("inbound: a failed media download is best-effort -- the message still publishes with its caption text, minus media", async () => {
  const rootDir = await mkdtemp(path.join(tmpdir(), "empyralis-whatsapp-media-failed-dl-"));
  try {
    const { adapter } = buildMockAdapter({
      downloadMedia: async () => {
        throw new Error("network blip");
      },
    });
    const published: any[] = [];
    const runtime = new WhatsAppPersonalRuntime(new GatewayStateDb(rootDir), {
      adapter: adapter as any,
      publisher: { publishEvent: async (_t, p) => { published.push(p); }, publishStateUpdate: async () => undefined },
    });
    await (runtime as any).connectSocketInternal();

    await (runtime as any).handleMessagesUpsert({
      messages: [
        {
          key: { id: "wamid-fail-1", remoteJid: "15551234567@s.whatsapp.net", fromMe: false },
          message: { imageMessage: { mimetype: "image/jpeg", caption: "still here" } },
          messageTimestamp: Math.floor(Date.now() / 1000),
        },
      ],
    });

    assert.equal(published.length, 1, "a failed download must not drop the whole message");
    assert.equal((published[0] as any).message.text, "still here");
    assert.equal((published[0] as any).message.media, undefined);
  } finally {
    await rm(rootDir, { recursive: true, force: true });
  }
});

test("inbound: a hostile external message id cannot traverse out of the media dir (media_id stays under whatsapp/media/)", async () => {
  const rootDir = await mkdtemp(path.join(tmpdir(), "empyralis-whatsapp-media-traversal-"));
  try {
    const { adapter } = buildMockAdapter({ downloadMedia: async () => Buffer.from("x") });
    const published: any[] = [];
    const runtime = new WhatsAppPersonalRuntime(new GatewayStateDb(rootDir), {
      adapter: adapter as any,
      publisher: { publishEvent: async (_t, p) => { published.push(p); }, publishStateUpdate: async () => undefined },
    });
    await (runtime as any).connectSocketInternal();

    await (runtime as any).handleMessagesUpsert({
      messages: [
        {
          key: { id: "../../../../etc/passwd", remoteJid: "15551234567@s.whatsapp.net", fromMe: false },
          message: { imageMessage: { mimetype: "image/jpeg" } },
          messageTimestamp: Math.floor(Date.now() / 1000),
        },
      ],
    });

    assert.equal(published.length, 1);
    const mediaId = (published[0] as any).message.media[0].media_id as string;
    assert.ok(mediaId.startsWith("whatsapp/media/"), "media_id must stay namespaced under whatsapp/media/");
    assert.ok(!mediaId.includes(".."), "path-traversal sequences in the stanza id must be stripped");
    // Resolving it against root must land inside the media dir, never above it.
    const resolved = path.resolve(rootDir, mediaId);
    assert.ok(
      resolved.startsWith(path.resolve(rootDir, "whatsapp", "media") + path.sep),
      "the resolved absolute path must be contained within <root>/whatsapp/media",
    );
  } finally {
    await rm(rootDir, { recursive: true, force: true });
  }
});

// ---------------------------------------------------------------------------
// Outbound integration -- runtime.ts wired to a mock Baileys adapter
// ---------------------------------------------------------------------------

test("outbound: an image media item sends a Baileys {image,caption,mimetype} payload from a local source_path", async () => {
  const rootDir = await mkdtemp(path.join(tmpdir(), "empyralis-whatsapp-media-out-image-"));
  const mediaSrcDir = await mkdtemp(path.join(tmpdir(), "empyralis-whatsapp-media-out-image-src-"));
  try {
    const imgBytes = Buffer.from([0xff, 0xd8, 0xff, 0xd9, 1, 2, 3]);
    const srcPath = path.join(mediaSrcDir, "photo.jpg");
    await writeFile(srcPath, imgBytes);

    const { adapter, sent } = buildMockAdapter();
    const runtime = new WhatsAppPersonalRuntime(new GatewayStateDb(rootDir), { adapter: adapter as any });
    await (runtime as any).connectSocketInternal();

    const result = await (runtime as any).handleChannelOutbound(
      outboundFrame({
        idempotency_key: "idem-img-1",
        text: "check this out",
        media: [{ kind: "image", source_path: srcPath, mime_type: "image/jpeg" }],
      }),
    );

    assert.equal(sent.length, 1);
    assert.deepEqual(sent[0].content.image, imgBytes);
    assert.equal(sent[0].content.caption, "check this out");
    assert.equal(sent[0].content.mimetype, "image/jpeg");
    assert.equal(result.delivered, true);
  } finally {
    await rm(rootDir, { recursive: true, force: true });
    await rm(mediaSrcDir, { recursive: true, force: true });
  }
});

test("outbound: a file media item sends a Baileys {document,fileName,mimetype,caption} payload", async () => {
  const rootDir = await mkdtemp(path.join(tmpdir(), "empyralis-whatsapp-media-out-file-"));
  const mediaSrcDir = await mkdtemp(path.join(tmpdir(), "empyralis-whatsapp-media-out-file-src-"));
  try {
    const fileBytes = Buffer.from("%PDF-1.4 fake pdf bytes");
    const srcPath = path.join(mediaSrcDir, "report.pdf");
    await writeFile(srcPath, fileBytes);

    const { adapter, sent } = buildMockAdapter();
    const runtime = new WhatsAppPersonalRuntime(new GatewayStateDb(rootDir), { adapter: adapter as any });
    await (runtime as any).connectSocketInternal();

    await (runtime as any).handleChannelOutbound(
      outboundFrame({
        idempotency_key: "idem-file-1",
        text: "",
        media: [{ kind: "file", source_path: srcPath, mime_type: "application/pdf", caption: "report" }],
      }),
    );

    assert.equal(sent.length, 1);
    assert.deepEqual(sent[0].content.document, fileBytes);
    assert.equal(sent[0].content.fileName, "report.pdf");
    assert.equal(sent[0].content.mimetype, "application/pdf");
    assert.equal(sent[0].content.caption, "report");
  } finally {
    await rm(rootDir, { recursive: true, force: true });
    await rm(mediaSrcDir, { recursive: true, force: true });
  }
});

test("outbound: a voice item transcodes non-Opus audio to real Opus/OGG via ffmpeg (verified with ffprobe) and sends ptt:true", async () => {
  const rootDir = await mkdtemp(path.join(tmpdir(), "empyralis-whatsapp-media-out-voice-"));
  const mediaSrcDir = await mkdtemp(path.join(tmpdir(), "empyralis-whatsapp-media-out-voice-src-"));
  try {
    const wavBytes = buildSilentWavBuffer(0.3, 8000);
    const srcPath = path.join(mediaSrcDir, "clip.wav");
    await writeFile(srcPath, wavBytes);

    const { adapter, sent } = buildMockAdapter();
    const runtime = new WhatsAppPersonalRuntime(new GatewayStateDb(rootDir), { adapter: adapter as any });
    await (runtime as any).connectSocketInternal();

    await (runtime as any).handleChannelOutbound(
      outboundFrame({
        idempotency_key: "idem-voice-1",
        text: "",
        media: [{ kind: "voice", source_path: srcPath, mime_type: "audio/wav" }],
      }),
    );

    assert.equal(sent.length, 1);
    const audioBuffer = sent[0].content.audio as Buffer;
    assert.ok(Buffer.isBuffer(audioBuffer) && audioBuffer.length > 0);
    assert.equal(sent[0].content.ptt, true);
    assert.equal(sent[0].content.mimetype, "audio/ogg; codecs=opus");
    assert.equal(audioBuffer.subarray(0, 4).toString("ascii"), "OggS", "transcoded output must be a real Ogg container");
    assert.notDeepEqual(audioBuffer, wavBytes, "output must actually be transcoded, not the raw WAV bytes");

    // Independently verify the ACTUAL codec/sample-rate with ffprobe -- this
    // proves the ffmpeg recipe (-c:a libopus -ar 48000 -b:a 64k -f ogg)
    // really took effect end-to-end, not just that ffmpeg was invoked.
    const probeOutPath = path.join(mediaSrcDir, "probe-out.ogg");
    await writeFile(probeOutPath, audioBuffer);
    const { stdout } = await execFileAsync("ffprobe", [
      "-v", "error",
      "-select_streams", "a:0",
      "-show_entries", "stream=codec_name,sample_rate",
      "-of", "csv=p=0",
      probeOutPath,
    ]);
    assert.equal(stdout.trim(), "opus,48000");
  } finally {
    await rm(rootDir, { recursive: true, force: true });
    await rm(mediaSrcDir, { recursive: true, force: true });
  }
});

test("outbound: a voice item whose source is already audio/ogg is sent as-is without invoking ffmpeg", async () => {
  const rootDir = await mkdtemp(path.join(tmpdir(), "empyralis-whatsapp-media-out-voice-noop-"));
  const mediaSrcDir = await mkdtemp(path.join(tmpdir(), "empyralis-whatsapp-media-out-voice-noop-src-"));
  try {
    // Deliberately NOT a real Ogg file -- if transcodeToWhatsAppVoiceOpus
    // were invoked on this, ffmpeg would fail on invalid input. Sending it
    // through byte-for-byte unmodified proves the already-ogg fast path
    // (skip transcode) was taken instead.
    const fakeOggBytes = Buffer.from("not-real-ogg-but-should-pass-through-untouched");
    const srcPath = path.join(mediaSrcDir, "clip.ogg");
    await writeFile(srcPath, fakeOggBytes);

    const { adapter, sent } = buildMockAdapter();
    const runtime = new WhatsAppPersonalRuntime(new GatewayStateDb(rootDir), { adapter: adapter as any });
    await (runtime as any).connectSocketInternal();

    await (runtime as any).handleChannelOutbound(
      outboundFrame({
        idempotency_key: "idem-voice-2",
        text: "",
        media: [{ kind: "voice", source_path: srcPath, mime_type: "audio/ogg" }],
      }),
    );

    assert.equal(sent.length, 1);
    assert.deepEqual(sent[0].content.audio, fakeOggBytes, "already-Opus/OGG source should pass through untranscoded");
    assert.equal(sent[0].content.ptt, true);
  } finally {
    await rm(rootDir, { recursive: true, force: true });
    await rm(mediaSrcDir, { recursive: true, force: true });
  }
});

test("outbound: an image media item fetches bytes from source_url over HTTP", async () => {
  const rootDir = await mkdtemp(path.join(tmpdir(), "empyralis-whatsapp-media-out-url-"));
  const imgBytes = Buffer.from([0xff, 0xd8, 0xaa, 0xbb, 9, 9, 9]);
  const server = http.createServer((_req, res) => {
    res.writeHead(200, { "content-type": "image/jpeg" });
    res.end(imgBytes);
  });
  await new Promise<void>((resolve) => server.listen(0, "127.0.0.1", resolve));
  const address = server.address();
  const port = typeof address === "object" && address ? address.port : 0;
  try {
    const { adapter, sent } = buildMockAdapter();
    const runtime = new WhatsAppPersonalRuntime(new GatewayStateDb(rootDir), { adapter: adapter as any });
    await (runtime as any).connectSocketInternal();

    await (runtime as any).handleChannelOutbound(
      outboundFrame({
        idempotency_key: "idem-url-1",
        text: "",
        media: [{ kind: "image", source_url: `http://127.0.0.1:${port}/photo.jpg`, mime_type: "image/jpeg" }],
      }),
    );

    assert.equal(sent.length, 1);
    assert.deepEqual(sent[0].content.image, imgBytes);
  } finally {
    await new Promise<void>((resolve) => server.close(() => resolve()));
    await rm(rootDir, { recursive: true, force: true });
  }
});

test("outbound: an unresolvable media item (missing file) degrades to a text-only send instead of failing the whole dispatch", async () => {
  const rootDir = await mkdtemp(path.join(tmpdir(), "empyralis-whatsapp-media-out-missing-"));
  try {
    const { adapter, sent } = buildMockAdapter();
    const runtime = new WhatsAppPersonalRuntime(new GatewayStateDb(rootDir), { adapter: adapter as any });
    await (runtime as any).connectSocketInternal();

    const result = await (runtime as any).handleChannelOutbound(
      outboundFrame({
        idempotency_key: "idem-missing-1",
        text: "fallback text",
        media: [{ kind: "image", source_path: "/nonexistent/path/to/nowhere.jpg" }],
      }),
    );

    assert.equal(sent.length, 1);
    assert.equal(sent[0].content.text, "fallback text");
    assert.equal(result.delivered, true);
  } finally {
    await rm(rootDir, { recursive: true, force: true });
  }
});

test("outbound: accepts a media-only dispatch (no text), but still rejects neither-text-nor-media", async () => {
  const rootDir = await mkdtemp(path.join(tmpdir(), "empyralis-whatsapp-media-out-validation-"));
  const mediaSrcDir = await mkdtemp(path.join(tmpdir(), "empyralis-whatsapp-media-out-validation-src-"));
  try {
    const srcPath = path.join(mediaSrcDir, "a.jpg");
    await writeFile(srcPath, Buffer.from([1, 2, 3]));
    const { adapter, sent } = buildMockAdapter();
    const runtime = new WhatsAppPersonalRuntime(new GatewayStateDb(rootDir), { adapter: adapter as any });
    await (runtime as any).connectSocketInternal();

    const result = await (runtime as any).handleChannelOutbound(
      outboundFrame({
        idempotency_key: "idem-mediaonly-1",
        text: "",
        media: [{ kind: "image", source_path: srcPath }],
      }),
    );
    assert.equal(result.delivered, true);
    assert.equal(sent.length, 1);

    await assert.rejects(
      () =>
        (runtime as any).handleChannelOutbound(
          outboundFrame({ idempotency_key: "idem-empty-1", text: "" }),
        ),
      /requires idempotency_key, remote_jid, and text and\/or media/,
    );
  } finally {
    await rm(rootDir, { recursive: true, force: true });
    await rm(mediaSrcDir, { recursive: true, force: true });
  }
});
