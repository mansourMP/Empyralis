import { promises as fs, type Stats } from "fs";
import path from "path";

/**
 * channel.media_fetch (Feature B inbound leg): the server pulls one
 * inbound attachment's raw bytes from the Gateway over the live
 * control-plane WebSocket, because the Gateway (behind a home NAT / an
 * Agent Computer box with no inbound port open) can't be reached with a
 * plain HTTP GET. The server sends a `channel.media_fetch` REQUEST frame
 * with `{channel_key, provider, media_id}`; this module resolves that
 * media_id (a file path RELATIVE to the gateway state dir root — see
 * state/db.ts's GatewayStateDb.rootDirPath() — written earlier by whatever
 * channel runtime downloaded the attachment) to bytes and produces the
 * RESPONSE payload.
 *
 * These types mirror, byte-for-byte, the wire contract the server side
 * already speaks — see fetch_channel_media() in
 * server_modules/gateway_protocol_service.py. They're defined LOCALLY here
 * (not added to protocol/types.ts) because a parallel branch is landing
 * unrelated changes to that shared file; see its
 * GatewayChannelInboundMediaItem for the sibling "how media_id got
 * produced" contract this responds to.
 */
export interface GatewayMediaFetchRequestPayload {
  channel_key: string;
  provider: string;
  media_id: string;
}

export interface GatewayMediaFetchSuccessPayload {
  media_id: string;
  mime_type: string;
  filename: string | null;
  size_bytes: number | null;
  data_base64: string;
}

export interface GatewayMediaFetchErrorPayload {
  code: "media_unavailable";
  message: string;
}

export type GatewayMediaFetchResult =
  | { ok: true; payload: GatewayMediaFetchSuccessPayload }
  | { ok: false; error: GatewayMediaFetchErrorPayload };

/**
 * The control-plane WS frame is capped end-to-end at 16 MiB (see
 * MAX_GATEWAY_FRAME_BYTES in server_modules/gateway_protocol_service.py,
 * mirrored on the gateway side by protocol/codec.ts's MAX_FRAME_BYTES).
 * Base64 inflates raw bytes by ~4/3, and the response also carries a small
 * JSON envelope (id/ts/kind/mime_type/filename/...), so cap the RAW file
 * we're willing to read well under the naive ~12 MiB break-even point:
 * 11.5 MiB raw -> ~15.33 MiB of base64, leaving headroom for the envelope.
 * There's no chunked-transfer mode yet, so anything bigger is reported as
 * media_unavailable rather than partially served — see resolveMediaFetch.
 */
export const MAX_MEDIA_FETCH_RAW_BYTES = Math.floor(11.5 * 1024 * 1024);

/** Small extension -> MIME map covering the attachment kinds Telegram and
 *  WhatsApp actually produce (image/voice/audio/video/document). Not
 *  exhaustive by design — unknown extensions fall back to
 *  application/octet-stream, which every consumer of this payload already
 *  has to handle regardless. */
const EXTENSION_MIME_TYPES: Readonly<Record<string, string>> = {
  ".jpg": "image/jpeg",
  ".jpeg": "image/jpeg",
  ".png": "image/png",
  ".gif": "image/gif",
  ".webp": "image/webp",
  ".bmp": "image/bmp",
  ".svg": "image/svg+xml",
  ".heic": "image/heic",
  ".heif": "image/heif",
  ".tif": "image/tiff",
  ".tiff": "image/tiff",
  ".mp3": "audio/mpeg",
  ".m4a": "audio/mp4",
  ".ogg": "audio/ogg",
  ".oga": "audio/ogg",
  ".opus": "audio/ogg",
  ".wav": "audio/wav",
  ".aac": "audio/aac",
  ".flac": "audio/flac",
  ".amr": "audio/amr",
  ".mp4": "video/mp4",
  ".m4v": "video/mp4",
  ".mov": "video/quicktime",
  ".webm": "video/webm",
  ".mkv": "video/x-matroska",
  ".avi": "video/x-msvideo",
  ".3gp": "video/3gpp",
  ".pdf": "application/pdf",
  ".doc": "application/msword",
  ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
  ".xls": "application/vnd.ms-excel",
  ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
  ".ppt": "application/vnd.ms-powerpoint",
  ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
  ".txt": "text/plain",
  ".csv": "text/csv",
  ".json": "application/json",
  ".xml": "application/xml",
  ".zip": "application/zip",
  ".rar": "application/vnd.rar",
  ".7z": "application/x-7z-compressed",
  ".tar": "application/x-tar",
  ".gz": "application/gzip",
};

function mimeTypeForPath(targetPath: string): string {
  const ext = path.extname(targetPath).toLowerCase();
  return EXTENSION_MIME_TYPES[ext] || "application/octet-stream";
}

function unavailable(message: string): GatewayMediaFetchResult {
  return { ok: false, error: { code: "media_unavailable", message } };
}

/**
 * Resolves a channel.media_fetch request's media_id to bytes on disk and
 * returns the response payload, or a media_unavailable error. Generic
 * across channels by construction — the channel-specific prefix (e.g.
 * "telegram-media/", "whatsapp/media/") is already baked into media_id by
 * whichever channel runtime wrote the file, so this function never needs
 * to know (and deliberately ignores) which channel/provider is asking.
 *
 * Deliberately takes the state root as a plain string (not a
 * GatewayStateDb instance) and touches nothing but the filesystem, so it's
 * directly unit-testable against a throwaway temp directory without
 * standing up a gateway, a socket, or a journal.
 */
export async function resolveMediaFetch(
  stateRootDir: string,
  rawMediaId: string,
): Promise<GatewayMediaFetchResult> {
  const mediaId = String(rawMediaId || "").trim();
  if (!mediaId) {
    return unavailable("media_id is required.");
  }

  const root = path.resolve(stateRootDir);
  const rootWithSep = root.endsWith(path.sep) ? root : root + path.sep;
  // path.join (not path.resolve) for the initial combine: it treats a
  // leading "/" in mediaId as just another literal path segment instead of
  // an override back to filesystem root, so an absolute-looking media_id
  // can't shortcut past the root on its own. path.join also normalizes
  // ".." segments syntactically as part of building the combined path,
  // which is exactly what lets the startsWith check below catch a
  // "../../etc/passwd"-style escape attempt.
  const joined = path.join(root, mediaId);
  const resolvedTarget = path.resolve(joined);
  if (resolvedTarget !== root && !resolvedTarget.startsWith(rootWithSep)) {
    return unavailable("media_id resolves outside the gateway state directory.");
  }

  let stats: Stats;
  try {
    // lstat, not stat: a symlink must be visible AS a symlink here so the
    // check below can refuse it, instead of transparently following it.
    // (This only inspects the final path component; a symlinked
    // INTERMEDIATE directory somewhere under the state root is a residual,
    // narrower risk that would already require local write access to the
    // gateway's own state dir to set up — the same trust level as code
    // execution on the box.)
    stats = await fs.lstat(resolvedTarget);
  } catch {
    return unavailable(`Media file not found: ${mediaId}`);
  }

  if (stats.isSymbolicLink()) {
    return unavailable("media_id must not resolve to a symlink.");
  }
  if (!stats.isFile()) {
    return unavailable(`Media path is not a regular file: ${mediaId}`);
  }
  if (stats.size > MAX_MEDIA_FETCH_RAW_BYTES) {
    return unavailable(
      `Media file is too large to fetch inline (${stats.size} bytes exceeds the ${MAX_MEDIA_FETCH_RAW_BYTES}-byte cap). Chunked transfer is not supported yet.`,
    );
  }

  let buffer: Buffer;
  try {
    buffer = await fs.readFile(resolvedTarget);
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    return unavailable(`Failed to read media file: ${message}`);
  }

  return {
    ok: true,
    payload: {
      media_id: mediaId,
      mime_type: mimeTypeForPath(resolvedTarget),
      filename: path.basename(resolvedTarget),
      size_bytes: stats.size,
      data_base64: buffer.toString("base64"),
    },
  };
}
