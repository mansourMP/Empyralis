import type { GatewayFrame, GatewayRequestType, GatewayEventType } from "./types";
import { PROTOCOL_VERSION } from "./types";

// Mirrors the server's MAX_GATEWAY_FRAME_BYTES (see
// server_modules/gateway_protocol_service.py) so the gateway's own encoder
// doesn't reject a frame the server is otherwise willing to accept over
// the same socket. Was 256KB (262144) historically; raised to 16MiB to
// match the server-side limit, which itself was already raised from
// 256KB for large tool.invoke/response payloads (see git history on
// gateway_protocol_service.py's MAX_GATEWAY_FRAME_BYTES). This gateway
// hadn't caught up until channel.media_fetch (cloud/media-fetch.ts) made
// the mismatch load-bearing: its base64-encoded file payload can
// legitimately be several MB, well past the old 256KB ceiling.
export const MAX_FRAME_BYTES = 16 * 1024 * 1024; // 16MiB
export const MAX_FRAME_DEPTH = 32;
export const SUPPORTED_PROTOCOL_VERSIONS = [PROTOCOL_VERSION] as const;

export const SAFE_FRAME_TYPES: ReadonlySet<string> = new Set([
  // Request types
  "gateway.connect",
  "gateway.heartbeat",
  "gateway.probe",
  "gateway.state.update",
  "gateway.disconnect",
  "tool.invoke",
  "tool.interrupt",
  "channel.outbound",
  "channel.media_fetch",
  // Event types
  "gateway.hello",
  "gateway.presence",
  "channel.inbound",
  "cli.login.output",
  "tool.invoke.chunk",
]);

interface FrameValidationError {
  ok: false;
  error: string;
}

interface FrameValidationSuccess {
  ok: true;
  frame: GatewayFrame;
}

export type FrameValidationResult = FrameValidationError | FrameValidationSuccess;

function isObject(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

function isNonEmptyString(value: unknown, maxLength: number): value is string {
  return typeof value === "string" && value.trim().length > 0 && value.length <= maxLength;
}

function maxDepth(value: unknown, currentDepth: number): number {
  if (!isObject(value)) {
    return currentDepth;
  }
  let deepest = currentDepth;
  for (const key of Object.keys(value)) {
    const child = value[key];
    if (isObject(child) || Array.isArray(child)) {
      const childDepth = maxDepth(child, currentDepth + 1);
      if (childDepth > deepest) {
        deepest = childDepth;
      }
    }
  }
  return deepest;
}

export function encodeFrame(frame: GatewayFrame): FrameValidationResult | string {
  if (!isObject(frame)) {
    return { ok: false, error: "Frame must be an object." };
  }
  const validation = validateFrame(frame);
  if (!validation.ok) {
    return validation;
  }
  const raw = JSON.stringify(frame);
  const byteLength = Buffer.byteLength(raw, "utf8");
  if (byteLength > MAX_FRAME_BYTES) {
    return { ok: false, error: `Encoded frame exceeds maximum size of ${MAX_FRAME_BYTES} bytes (got ${byteLength}).` };
  }
  return raw;
}

export function decodeFrame(raw: string): FrameValidationResult {
  if (typeof raw !== "string") {
    return { ok: false, error: "Frame must be a string." };
  }
  const byteLength = Buffer.byteLength(raw, "utf8");
  if (byteLength > MAX_FRAME_BYTES) {
    return { ok: false, error: `Frame exceeds maximum size of ${MAX_FRAME_BYTES} bytes (got ${byteLength}).` };
  }
  let parsed: unknown;
  try {
    parsed = JSON.parse(raw);
  } catch {
    return { ok: false, error: "Frame is not valid JSON." };
  }
  if (!isObject(parsed)) {
    return { ok: false, error: "Gateway frame must be an object." };
  }
  const depth = maxDepth(parsed, 1);
  if (depth > MAX_FRAME_DEPTH) {
    return { ok: false, error: `Frame exceeds maximum nesting depth of ${MAX_FRAME_DEPTH} (got ${depth}).` };
  }
  const validation = validateFrame(parsed);
  if (!validation.ok) {
    return validation;
  }
  return { ok: true, frame: parsed as unknown as GatewayFrame };
}

function validateFrame(frame: Record<string, unknown>): FrameValidationResult {
  const kind = String(frame.kind ?? "").trim();
  if (!["request", "response", "event"].includes(kind)) {
    return { ok: false, error: `Unsupported gateway frame kind: "${kind}". Must be "request", "response", or "event".` };
  }
  if (kind === "request") {
    return validateRequest(frame);
  }
  if (kind === "response") {
    return validateResponse(frame);
  }
  return validateEvent(frame);
}

function validateRequest(frame: Record<string, unknown>): FrameValidationResult {
  const { id, type: msgType } = frame;
  if (!isNonEmptyString(id, 256)) {
    return { ok: false, error: "Request frame must have a non-empty 'id' (string, max 256 chars)." };
  }
  if (!isNonEmptyString(msgType, 128)) {
    return { ok: false, error: "Request frame must have a non-empty 'type' (string, max 128 chars)." };
  }
  if (typeof frame.ts !== "string" || frame.ts.trim().length === 0) {
    return { ok: false, error: "Request frame must have a non-empty 'ts' string." };
  }
  if (!SAFE_FRAME_TYPES.has(String(msgType))) {
    return { ok: false, error: `Unknown request type: "${msgType}".` };
  }
  if (frame.protocolVersion !== undefined && typeof frame.protocolVersion !== "string") {
    return { ok: false, error: "Request frame 'protocolVersion' must be a string." };
  }
  return { ok: true, frame: frame as unknown as GatewayFrame };
}

function validateResponse(frame: Record<string, unknown>): FrameValidationResult {
  const { id } = frame;
  if (!isNonEmptyString(id, 256)) {
    return { ok: false, error: "Response frame must have a non-empty 'id' (string, max 256 chars)." };
  }
  if (typeof frame.ok !== "boolean") {
    return { ok: false, error: "Response frame must have a boolean 'ok' field." };
  }
  if (typeof frame.ts !== "string" || frame.ts.trim().length === 0) {
    return { ok: false, error: "Response frame must have a non-empty 'ts' string." };
  }
  if (frame.protocolVersion !== undefined && typeof frame.protocolVersion !== "string") {
    return { ok: false, error: "Response frame 'protocolVersion' must be a string." };
  }
  return { ok: true, frame: frame as unknown as GatewayFrame };
}

function validateEvent(frame: Record<string, unknown>): FrameValidationResult {
  const { type: msgType } = frame;
  if (!isNonEmptyString(msgType, 128)) {
    return { ok: false, error: "Event frame must have a non-empty 'type' (string, max 128 chars)." };
  }
  if (typeof frame.ts !== "string" || frame.ts.trim().length === 0) {
    return { ok: false, error: "Event frame must have a non-empty 'ts' string." };
  }
  if (frame.scope !== undefined && !isObject(frame.scope)) {
    return { ok: false, error: "Event frame 'scope' must be an object." };
  }
  if (frame.seq !== undefined && (typeof frame.seq !== "number" || !Number.isInteger(frame.seq))) {
    return { ok: false, error: "Event frame 'seq' must be an integer." };
  }
  if (!SAFE_FRAME_TYPES.has(String(msgType))) {
    return { ok: false, error: `Unknown event type: "${msgType}".` };
  }
  if (frame.protocolVersion !== undefined && typeof frame.protocolVersion !== "string") {
    return { ok: false, error: "Event frame 'protocolVersion' must be a string." };
  }
  return { ok: true, frame: frame as unknown as GatewayFrame };
}
