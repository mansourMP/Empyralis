import test from "node:test";
import assert from "node:assert/strict";

import { parseSupportedReasoningEfforts, parseSupportedReasoningEffortsOrUnknown } from "../llm/codex-app-server";

// Defensive-parsing coverage for the reasoning-effort forwarding fix
// (fix/gateway-forward-codex-reasoning-fields). codex-app-server.ts's
// `listModels()` reads `supportedReasoningEfforts` straight off codex
// app-server's live `model/list` RPC response — an untrusted external
// shape that can legitimately vary per model/account (verified live:
// gpt-5.6-terra vs gpt-5.6-luna carry different sets, including "ultra",
// a level in no doc and no static table here). This must never throw on a
// malformed or absent entry; a model genuinely missing the field degrades
// to an empty array, not a crash.

test("parseSupportedReasoningEfforts: relays a well-formed live RPC shape verbatim, including undocumented levels", () => {
  const raw = [
    { reasoningEffort: "low", description: "Fast responses with lighter reasoning" },
    { reasoningEffort: "medium", description: "Balances speed and reasoning depth..." },
    { reasoningEffort: "high", description: "Greater reasoning depth for complex problems" },
    { reasoningEffort: "xhigh", description: "Extra high reasoning depth..." },
    { reasoningEffort: "max", description: "Maximum reasoning depth for the hardest problems" },
    { reasoningEffort: "ultra", description: "Maximum reasoning with automatic task delegation" },
  ];
  const result = parseSupportedReasoningEfforts(raw);
  assert.equal(result.length, 6);
  assert.deepEqual(
    result.map((e) => e.reasoningEffort),
    ["low", "medium", "high", "xhigh", "max", "ultra"],
  );
  assert.equal(result[5].description, "Maximum reasoning with automatic task delegation");
});

test("parseSupportedReasoningEfforts: undefined/null/missing field degrades to empty, never throws", () => {
  assert.deepEqual(parseSupportedReasoningEfforts(undefined), []);
  assert.deepEqual(parseSupportedReasoningEfforts(null), []);
  assert.deepEqual(parseSupportedReasoningEfforts({}), []);
});

test("parseSupportedReasoningEfforts: not an array at all (a malformed RPC response) degrades to empty, never throws", () => {
  assert.deepEqual(parseSupportedReasoningEfforts("not-an-array"), []);
  assert.deepEqual(parseSupportedReasoningEfforts(42), []);
  assert.deepEqual(parseSupportedReasoningEfforts("ultra"), []);
});

test("parseSupportedReasoningEfforts: a malformed individual item is dropped, not the whole list", () => {
  const raw = [
    { reasoningEffort: "low", description: "ok" },
    null,
    "garbage",
    42,
    { description: "no reasoningEffort key at all" },
    { reasoningEffort: "", description: "empty effort string is not a real level" },
    { reasoningEffort: "high" }, // missing description defaults to ""
  ];
  const result = parseSupportedReasoningEfforts(raw);
  assert.deepEqual(
    result.map((e) => e.reasoningEffort),
    ["low", "high"],
  );
  assert.equal(result[1].description, "");
});

// --- absent vs. empty: two different facts, two different values ---
// Added when the gateway half and the cloud half of this chain were merged.
// Neither half could see this alone: the gateway flattened "the RPC said
// nothing" into the same `[]` that means "the model reports no levels", and
// the cloud then rendered its static fallback ladder for both. The first is
// the MAJORITY live fleet state (any box on a gateway built before this
// field was forwarded), and the second must render no control at all — so
// the collapse either deletes the picker for most customers or leaves a dead
// control on a model that implements none of its options.

test("parseSupportedReasoningEffortsOrUnknown: an absent field is UNKNOWN (null), never an empty list", () => {
  assert.equal(parseSupportedReasoningEffortsOrUnknown(undefined), null);
  assert.equal(parseSupportedReasoningEffortsOrUnknown(null), null);
  assert.equal(parseSupportedReasoningEffortsOrUnknown("not-an-array"), null);
  assert.equal(parseSupportedReasoningEffortsOrUnknown(42), null);
});

test("parseSupportedReasoningEffortsOrUnknown: a genuinely empty array is a real answer — no levels — and stays []", () => {
  assert.deepEqual(parseSupportedReasoningEffortsOrUnknown([]), []);
});

test("parseSupportedReasoningEffortsOrUnknown: absent and empty do not share a value", () => {
  assert.notDeepEqual(parseSupportedReasoningEffortsOrUnknown(undefined), parseSupportedReasoningEffortsOrUnknown([]));
});

test("parseSupportedReasoningEffortsOrUnknown: a non-empty but unintelligible array is UNKNOWN, not 'no levels'", () => {
  // Something was there and nothing survived parsing — that is a failure to
  // understand the answer, not the model answering no.
  assert.equal(parseSupportedReasoningEffortsOrUnknown(["garbage", null, { description: "no level key" }]), null);
});

test("parseSupportedReasoningEffortsOrUnknown: a well-formed live array relays verbatim, undocumented levels included", () => {
  // Verbatim from a real model/list response, 2026-08-20, gpt-5.6-terra.
  const result = parseSupportedReasoningEffortsOrUnknown([
    { reasoningEffort: "max", description: "Maximum reasoning depth for the hardest problems" },
    { reasoningEffort: "ultra", description: "Maximum reasoning with automatic task delegation" },
  ]);
  assert.deepEqual(result?.map((e) => e.reasoningEffort), ["max", "ultra"]);
});
