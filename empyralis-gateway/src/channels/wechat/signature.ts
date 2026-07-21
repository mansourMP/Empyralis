/** WeChat's/WeCom's server-verification and per-request signature check.
 *
 *  Both the one-time GET server-verification handshake and every inbound
 *  POST carry the same three query-string parameters plus a `signature`
 *  to check against them: `timestamp`, `nonce`, and (for GET) `echostr`.
 *  Tencent's documented algorithm (identical for Official Account and
 *  WeCom's non-"safe mode" callbacks):
 *
 *    1. Take [token, timestamp, nonce], sort lexicographically, join with
 *       no separator.
 *    2. sha1() the joined string, hex-encode.
 *    3. Compare (case-insensitive doesn't matter — sha1 hex digest is
 *       always lowercase from Node's crypto) to the `signature` param.
 *
 *  This is a shared-secret HMAC-shaped scheme without a keyed HMAC
 *  primitive — the "key" is just concatenated into the hashed material.
 *  That is Tencent's spec, not a shortcut taken here; verifyWeChatServerSignature
 *  implements exactly this and nothing more.
 *
 *  NOT implemented: "safe mode" (encodingAesKey / msg_signature over the
 *  encrypted body). If the WeChat/WeCom account this is pointed at has
 *  message encryption turned on, inbound bodies won't be plaintext XML
 *  and this signature check alone is insufficient — decryption
 *  (AES-256-CBC with a SHA1-based msg_signature) would need to be added
 *  first. types.ts's WeChatOfficialConfig.encodingAesKey field is a
 *  placeholder for that future work, not read by this module today.
 */
import { createHash, timingSafeEqual } from "crypto";

export interface WeChatServerVerificationParams {
  token: string;
  timestamp: string;
  nonce: string;
  signature: string;
}

function sha1Hex(value: string): string {
  return createHash("sha1").update(value, "utf8").digest("hex");
}

/** Constant-time-ish string compare (both sides are hex digests of a
 *  known fixed length, so a length mismatch alone doesn't leak much, but
 *  there's no reason not to use timingSafeEqual here). */
function safeEquals(a: string, b: string): boolean {
  const bufA = Buffer.from(a, "utf8");
  const bufB = Buffer.from(b, "utf8");
  if (bufA.length !== bufB.length) {
    return false;
  }
  return timingSafeEqual(bufA, bufB);
}

export function computeWeChatSignature(token: string, timestamp: string, nonce: string): string {
  const parts = [token, timestamp, nonce].map((part) => String(part || ""));
  parts.sort();
  return sha1Hex(parts.join(""));
}

/** Verifies a request's `signature` against `token`+`timestamp`+`nonce`.
 *  Returns false (never throws) for missing/malformed params so callers
 *  can treat any failure uniformly as "reject the request". */
export function verifyWeChatServerSignature(params: WeChatServerVerificationParams): boolean {
  const token = String(params.token || "").trim();
  const timestamp = String(params.timestamp || "").trim();
  const nonce = String(params.nonce || "").trim();
  const signature = String(params.signature || "").trim();
  if (!token || !timestamp || !nonce || !signature) {
    return false;
  }
  const expected = computeWeChatSignature(token, timestamp, nonce);
  return safeEquals(expected, signature);
}

/** Optional replay-window guard: WeChat's/WeCom's own retry behavior and
 *  normal clock drift mean this should stay generous. Not called from
 *  server.ts by default (signature verification alone is what Tencent's
 *  spec requires); exported for callers who want to layer it on. */
export function isWeChatTimestampFresh(
  timestamp: string,
  nowMs: number = Date.now(),
  maxSkewSeconds = 300,
): boolean {
  const parsed = Number.parseInt(String(timestamp || "").trim(), 10);
  if (!Number.isFinite(parsed) || parsed <= 0) {
    return false;
  }
  const deltaSeconds = Math.abs(nowMs / 1000 - parsed);
  return deltaSeconds <= maxSkewSeconds;
}
