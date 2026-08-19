/**
 * Zero-decision agent creation — imports the REAL functions rather than
 * re-deriving their rules here, same discipline agent-count-shape.test.ts
 * and channel-doors.test.ts already apply (CLAUDE.md: "a check that
 * derives its own expectations from the thing it checks is blind, and
 * reports 'passed'"). The expected shape (this file) and the actual shape
 * (agent-quick-create.ts) come from two different places.
 *
 * Run: npx tsx lib/workspace/fleet/agent-quick-create.test.ts
 */

import type { FleetProject } from "./fleet-data";
import {
  buildQuickCreateAgentPayload,
  quickCreateAgentChatPath,
  resolveQuickCreateProjectId,
} from "./agent-quick-create";

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

function project(id: string, isDefault = false): FleetProject {
  return { id, name: id, is_default: isDefault };
}

// ── resolveQuickCreateProjectId ──────────────────────────────────────────

assert(
  resolveQuickCreateProjectId("proj_current", [project("proj_default", true), project("proj_other")]) === "proj_current",
  "an explicit current project wins over any default",
);
assert(
  resolveQuickCreateProjectId(undefined, [project("proj_other"), project("proj_default", true)]) === "proj_default",
  "no current project -> the workspace's is_default project",
);
assert(
  resolveQuickCreateProjectId("", [project("proj_only")]) === "proj_only",
  "no default flagged -> falls back to the first project (matches resolveAgentProjectId)",
);
assert(
  resolveQuickCreateProjectId(undefined, []) === "",
  "a genuinely empty workspace resolves to blank, never throws",
);

// ── buildQuickCreateAgentPayload ─────────────────────────────────────────

const payload = buildQuickCreateAgentPayload("proj_123");
assert(payload.project_id === "proj_123", "payload carries the resolved project id");
assert(payload.name === "", "name is blank -- the server assigns a pool name, never the client");
assert(payload.capability_preset === "standard", "capability_preset is the only non-reserved creatable default");
assert(payload.purpose_preset === "internal_assistant", "purpose_preset defaults to the safe, non-customer-facing preset");
assert(payload.audience === "owner", "audience defaults to the safer, less-blaming assumption");

// A second call with a different project must not leak state between calls
// -- this is a pure function, not a stateful builder.
const payloadB = buildQuickCreateAgentPayload("proj_456");
assert(payloadB.project_id === "proj_456" && payload.project_id === "proj_123", "independent calls never share state");

// ── quickCreateAgentChatPath ─────────────────────────────────────────────

assert(
  quickCreateAgentChatPath({ workspaceId: "ws_1", projectId: "proj_1", agentId: "agent_1" }) ===
    "/w/ws_1/projects/proj_1/agents/agent_1/chat",
  "a resolved project lands straight in that agent's Chat",
);
assert(
  quickCreateAgentChatPath({ workspaceId: "ws_1", projectId: "", agentId: "agent_1" }) === "/w/ws_1/agents",
  "an unresolvable project falls back to the flat agents list, never a broken /projects/agents/{id} link",
);
assert(
  quickCreateAgentChatPath({ workspaceId: "ws_1", projectId: "proj_1", agentId: "" }) === "/w/ws_1/agents",
  "no agent id at all also falls back rather than building a link to nothing",
);
assert(
  quickCreateAgentChatPath({ workspaceId: "a b", projectId: "p/1", agentId: "x y" }) ===
    `/w/${encodeURIComponent("a b")}/projects/${encodeURIComponent("p/1")}/agents/${encodeURIComponent("x y")}/chat`,
  "every path segment is individually URI-encoded",
);

// --- Summary ---

console.log(`\n${passed} passed, ${failed} failed`);
if (failed > 0) {
  process.exit(1);
}
