/**
 * Imports the REAL rules from agent-create-card.ts rather than
 * re-deriving them here — same discipline agent-count-shape.test.ts
 * already applies to planAgentCountShape, for the identical reason
 * (CLAUDE.md: "a check that derives its own expectations from the thing
 * it checks is blind, and reports 'passed'").
 *
 * Run: npx tsx lib/workspace/fleet/agent-create-card.test.ts
 */

import { buildAgentCreatePayload, resolveAgentCreateName } from "./agent-create-card";

let passed = 0;
let failed = 0;

function assert(condition: boolean, label: string): void {
  if (condition) {
    passed++;
  } else {
    failed++;
    console.error(`FAIL: ${label}`);
  }
}

// ── resolveAgentCreateName ───────────────────────────────────────────────

assert(resolveAgentCreateName("", "Basalt") === "Basalt", "empty typed name falls back to the suggestion");
assert(resolveAgentCreateName("   ", "Basalt") === "Basalt", "whitespace-only typed name still falls back");
assert(resolveAgentCreateName("Ruby", "Basalt") === "Ruby", "a typed name always wins over the suggestion");
assert(resolveAgentCreateName("", "") === "", "no suggestion yet and nothing typed is an honest empty string");

// ── buildAgentCreatePayload ───────────────────────────────────────────────

{
  const payload = buildAgentCreatePayload({ name: "  Ruby  ", instructions: "", projectId: "proj-1" });
  assert(payload.name === "Ruby", "the name is trimmed before it reaches the request body");
  assert(payload.instructions === "", "an empty system prompt is a legitimate, honest empty string");
  assert(payload.project_id === "proj-1", "the resolved project id is carried through verbatim, never shown to the caller");
  assert(payload.capability_preset === "standard", "capability_preset stays the fixed safe default");
  assert(payload.purpose_preset === "internal_assistant", "purpose_preset stays the fixed safe default");
  assert(payload.audience === "owner", "audience stays the fixed safe default");
}

{
  const payload = buildAgentCreatePayload({
    name: "YouTube content creation agent",
    instructions: "  Draft video scripts and titles for the channel.  ",
    projectId: "proj-2",
  });
  assert(payload.name === "YouTube content creation agent", "a real name is carried through unchanged");
  assert(
    payload.instructions === "Draft video scripts and titles for the channel.",
    "the system prompt is trimmed but otherwise passed through verbatim",
  );
}

// ── Summary ───────────────────────────────────────────────────────────────

console.log(`${passed} passed, ${failed} failed`);
if (failed > 0) process.exit(1);
