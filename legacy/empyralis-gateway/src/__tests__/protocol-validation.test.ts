import test from "node:test";
import assert from "node:assert/strict";

import {
  encodeFrame,
  decodeFrame,
  MAX_FRAME_BYTES,
  MAX_FRAME_DEPTH,
  SAFE_FRAME_TYPES,
} from "../protocol/codec";

const MOCK_VALID_REQUEST = {
  kind: "request" as const,
  protocolVersion: "v1alpha2",
  id: "req-001",
  type: "gateway.heartbeat" as const,
  ts: new Date().toISOString(),
  scope: {
    tenant_id: "tenant-1",
    workspace_id: "ws-1",
    user_id: "user-1",
    device_id: "device-1",
    gateway_id: "gw-1",
  },
  payload: {},
};

const MOCK_VALID_RESPONSE = {
  kind: "response" as const,
  protocolVersion: "v1alpha2",
  id: "req-001",
  ok: true,
  ts: new Date().toISOString(),
  payload: { status: "ok" },
};

const MOCK_VALID_EVENT = {
  kind: "event" as const,
  protocolVersion: "v1alpha2",
  type: "gateway.hello" as const,
  ts: new Date().toISOString(),
  seq: 1,
  payload: { message: "welcome" },
};

test("valid request frame passes decode", () => {
  const raw = JSON.stringify(MOCK_VALID_REQUEST);
  const result = decodeFrame(raw);
  assert.equal(result.ok, true);
  if (result.ok) {
    assert.equal(result.frame.kind, "request");
    assert.equal(result.frame.id, "req-001");
  }
});

test("valid response frame passes decode", () => {
  const raw = JSON.stringify(MOCK_VALID_RESPONSE);
  const result = decodeFrame(raw);
  assert.equal(result.ok, true);
  if (result.ok) {
    assert.equal(result.frame.kind, "response");
    assert.equal(result.frame.ok, true);
  }
});

test("valid event frame passes decode", () => {
  const raw = JSON.stringify(MOCK_VALID_EVENT);
  const result = decodeFrame(raw);
  assert.equal(result.ok, true);
  if (result.ok) {
    assert.equal(result.frame.kind, "event");
    assert.equal(result.frame.type, "gateway.hello");
  }
});

test("request missing id fails", () => {
  const bad = { ...MOCK_VALID_REQUEST, id: "" };
  const raw = JSON.stringify(bad);
  const result = decodeFrame(raw);
  assert.equal(result.ok, false);
  assert.match(result.ok === false ? result.error : "", /id/);
});

test("request missing type fails", () => {
  const bad = { ...MOCK_VALID_REQUEST, type: "" };
  const raw = JSON.stringify(bad);
  const result = decodeFrame(raw);
  assert.equal(result.ok, false);
  assert.match(result.ok === false ? result.error : "", /type/);
});

test("request missing ts fails", () => {
  const bad = { ...MOCK_VALID_REQUEST, ts: "" };
  const raw = JSON.stringify(bad);
  const result = decodeFrame(raw);
  assert.equal(result.ok, false);
  assert.match(result.ok === false ? result.error : "", /ts/);
});

test("response missing ok fails", () => {
  const bad = { ...MOCK_VALID_RESPONSE, ok: undefined };
  const raw = JSON.stringify(bad);
  const result = decodeFrame(raw);
  assert.equal(result.ok, false);
  assert.match(result.ok === false ? result.error : "", /ok/);
});

test("response missing id fails", () => {
  const bad = { ...MOCK_VALID_RESPONSE, id: "" };
  const raw = JSON.stringify(bad);
  const result = decodeFrame(raw);
  assert.equal(result.ok, false);
  assert.match(result.ok === false ? result.error : "", /id/);
});

test("event missing type fails", () => {
  const bad = { ...MOCK_VALID_EVENT, type: "" };
  const raw = JSON.stringify(bad);
  const result = decodeFrame(raw);
  assert.equal(result.ok, false);
  assert.match(result.ok === false ? result.error : "", /type/);
});

test("event missing ts fails", () => {
  const bad = { ...MOCK_VALID_EVENT, ts: "" };
  const raw = JSON.stringify(bad);
  const result = decodeFrame(raw);
  assert.equal(result.ok, false);
  assert.match(result.ok === false ? result.error : "", /ts/);
});

test("event seq must be integer", () => {
  const bad = { ...MOCK_VALID_EVENT, seq: "not-a-number" };
  const raw = JSON.stringify(bad);
  const result = decodeFrame(raw);
  assert.equal(result.ok, false);
  assert.match(result.ok === false ? result.error : "", /seq/);
});

test("unknown kind fails", () => {
  const bad = { ...MOCK_VALID_REQUEST, kind: "ping" };
  const raw = JSON.stringify(bad);
  const result = decodeFrame(raw);
  assert.equal(result.ok, false);
  assert.match(result.ok === false ? result.error : "", /Must be/);
});

test("non-object frame fails decode", () => {
  const result = decodeFrame('"just-a-string"');
  assert.equal(result.ok, false);
  assert.match(result.ok === false ? result.error : "", /object/);
});

test("non-JSON string fails decode", () => {
  const result = decodeFrame("not-json-at-all");
  assert.equal(result.ok, false);
});

test("oversized frame (>256KB) fails", () => {
  // Build a frame just over 256KB
  const bigPayload = { data: "x".repeat(300_000) };
  const frame = { ...MOCK_VALID_REQUEST, payload: bigPayload };
  const raw = JSON.stringify(frame);
  const result = decodeFrame(raw);
  assert.equal(result.ok, false);
  assert.match(result.ok === false ? result.error : "", /exceeds maximum size/);
});

test("frame exactly at MAX_FRAME_BYTES passes size check", () => {
  // Build a frame payload that keeps it just under the limit
  const payloadSize = MAX_FRAME_BYTES - 1500; // Leave room for envelope
  const bigPayload = { data: "x".repeat(payloadSize) };
  const frame = { ...MOCK_VALID_REQUEST, payload: bigPayload };
  const raw = JSON.stringify(frame);
  const result = decodeFrame(raw);
  // It might fail for other reasons (e.g., depth), but not size
  if (!result.ok) {
    assert.equal(result.ok === false ? result.error.includes("exceeds maximum size") : false, false);
  }
});

test("deep nested frame (>32) fails", () => {
  function buildNested(depth: number): Record<string, unknown> {
    if (depth <= 0) return { value: "leaf" };
    return { nested: buildNested(depth - 1) };
  }
  const deepPayload = buildNested(35);
  const frame = { ...MOCK_VALID_REQUEST, payload: deepPayload };
  const raw = JSON.stringify(frame);
  const result = decodeFrame(raw);
  assert.equal(result.ok, false);
  assert.match(result.ok === false ? result.error : "", /depth/);
});

test("encodeFrame rejects invalid frame", () => {
  const badFrame = { kind: "request" as const, id: "", type: "", ts: "", payload: {} } as any;
  const encoded = encodeFrame(badFrame);
  assert.equal(typeof encoded !== "string", true);
  if (typeof encoded !== "string") {
    assert.equal(encoded.ok, false);
  }
});

test("encodeFrame rejects oversized frame", () => {
  const bigPayload = { data: "x".repeat(300_000) };
  const frame = { ...MOCK_VALID_REQUEST, payload: bigPayload };
  const encoded = encodeFrame(frame);
  assert.equal(typeof encoded !== "string", true);
  if (typeof encoded !== "string" && !encoded.ok) {
    assert.match(encoded.error, /exceeds maximum size/);
  }
});

test("encodeFrame returns string for valid frame", () => {
  const encoded = encodeFrame(MOCK_VALID_REQUEST);
  assert.equal(typeof encoded, "string");
});

test("roundtrip encode+decode for valid request", () => {
  const encoded = encodeFrame(MOCK_VALID_REQUEST);
  assert.equal(typeof encoded, "string");
  if (typeof encoded === "string") {
    const decoded = decodeFrame(encoded);
    assert.equal(decoded.ok, true);
    if (decoded.ok) {
      assert.equal(decoded.frame.kind, "request");
      assert.equal((decoded.frame as { id: string }).id, "req-001");
    }
  }
});

test("roundtrip encode+decode for valid response", () => {
  const encoded = encodeFrame(MOCK_VALID_RESPONSE);
  assert.equal(typeof encoded, "string");
  if (typeof encoded === "string") {
    const decoded = decodeFrame(encoded);
    assert.equal(decoded.ok, true);
    if (decoded.ok) {
      assert.equal(decoded.frame.kind, "response");
      assert.equal((decoded.frame as { ok: boolean }).ok, true);
    }
  }
});

test("roundtrip encode+decode for valid event", () => {
  const encoded = encodeFrame(MOCK_VALID_EVENT);
  assert.equal(typeof encoded, "string");
  if (typeof encoded === "string") {
    const decoded = decodeFrame(encoded);
    assert.equal(decoded.ok, true);
    if (decoded.ok) {
      assert.equal(decoded.frame.kind, "event");
    }
  }
});

test("SAFE_FRAME_TYPES contains all known types", () => {
  const knownTypes = [
    "gateway.connect",
    "gateway.heartbeat",
    "gateway.probe",
    "gateway.state.update",
    "gateway.disconnect",
    "tool.invoke",
    "tool.interrupt",
    "channel.outbound",
    "gateway.hello",
    "gateway.presence",
    "channel.inbound",
  ];
  for (const t of knownTypes) {
    assert.equal(SAFE_FRAME_TYPES.has(t), true, `Missing known type: ${t}`);
  }
});

test("unknown request type is rejected", () => {
  const bad = { ...MOCK_VALID_REQUEST, type: "unknown.custom.type" };
  const raw = JSON.stringify(bad);
  const result = decodeFrame(raw);
  assert.equal(result.ok, false);
  assert.match(result.ok === false ? result.error : "", /Unknown request type/);
});

test("unknown event type is rejected", () => {
  const bad = { ...MOCK_VALID_EVENT, type: "unknown.event.type" };
  const raw = JSON.stringify(bad);
  const result = decodeFrame(raw);
  assert.equal(result.ok, false);
  assert.match(result.ok === false ? result.error : "", /Unknown event type/);
});

test("id longer than 256 chars fails", () => {
  const bad = { ...MOCK_VALID_REQUEST, id: "x".repeat(300) };
  const raw = JSON.stringify(bad);
  const result = decodeFrame(raw);
  assert.equal(result.ok, false);
  assert.match(result.ok === false ? result.error : "", /id/);
});

test("type longer than 128 chars fails", () => {
  const bad = { ...MOCK_VALID_REQUEST, type: "gateway.".concat("x".repeat(130)) };
  const raw = JSON.stringify(bad);
  const result = decodeFrame(raw);
  assert.equal(result.ok, false);
});
