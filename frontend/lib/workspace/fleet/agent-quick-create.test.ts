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
import { AGENT_CREATE_JOBS } from "./agent-create-job";
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

// The two preset assertions below used to prove these values were LITERALS in
// the builder. They are now proving something stronger and more useful: that
// the step-1 job picker's default ("General") still resolves to exactly the
// values every agent has always been created with. Repointed rather than
// deleted -- a caller that passes no job must not have silently changed
// behaviour, and that is the one thing worth a red test here.
assert(payload.capability_preset === "standard", "no job given -> capability_preset standard, unchanged from before the picker");
assert(payload.purpose_preset === "internal_assistant", "no job given -> purpose_preset internal_assistant, unchanged from before the picker");
assert(
  !("audience" in payload),
  "audience is NOT sent -- fleet_create_agent derives it from purpose_preset; a copy of that map here would be a second opinion about one fact",
);

// And a job actually changes them, or the picker writes the default anyway --
// the "built, tested, never wired" defect this codebase has most of.
const supportPayload = buildQuickCreateAgentPayload("proj_123", "", "", null, "support");
assert(supportPayload.purpose_preset === "customer_facing", "a customer-facing job posts customer_facing");
// ...and the capability preset is the one thing a job may NOT change. Asserted
// HERE, at the payload that actually goes over the wire, and not only in
// agent-create-job.ts's own test -- this is the boundary where a weaker agent
// would really be requested. Founder, 2026-08-29: "fundamentally all agents
// must be the same ... underneath every other agent is going to be the same."
for (const job of AGENT_CREATE_JOBS) {
  const jobPayload = buildQuickCreateAgentPayload("proj_123", "", "", null, job.id);
  assert(
    jobPayload.capability_preset === "standard",
    `the "${job.id}" job posts capability_preset standard -- a job is a label, never a weaker agent`,
  );
}
assert(
  buildQuickCreateAgentPayload("proj_123", "", "", null, "nonsense").purpose_preset === "internal_assistant",
  "an unrecognised job id falls back to General rather than posting nothing",
);

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
