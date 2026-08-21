/**
 * Drives the REAL rules from agent-delete.ts, plus two structural checks
 * that a behavioural test cannot give: that the delete door is actually
 * WIRED (this exact feature went unreachable once already — the backend
 * never moved, the only frontend caller was dropped by an unrelated
 * redesign), and that there is exactly ONE delete path rather than the
 * copy-per-surface shape this codebase has shipped four times.
 *
 * Run: npx tsx lib/workspace/fleet/agent-delete.test.ts
 */

import { readFileSync } from "node:fs";

import {
  UNDELETABLE_AGENT_KIND,
  canDeleteAgent,
  classifyAgentDeleteRecovery,
} from "./agent-delete";

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

// ── The guard: never offer a control the backend will refuse ──────────────
// The SHARPEST assertion in this file is the "real shape" one below. The
// guard this was lifted from keyed on `role === "operator"`, and a real
// workspace's operator comes back as role "specialist" / agent_kind
// "master" — so that check matched nothing and offered Delete on the one
// agent the server always refuses. Measured live, not assumed.

assert(UNDELETABLE_AGENT_KIND === "master", "the guard is keyed on the kind fleet_delete_agent itself resolves");

{
  // The REAL shape, copied from a live GET /fleet/agents response.
  const realOperator = { label: "Sage", role: "specialist", agent_kind: "master" };
  assert(!canDeleteAgent(realOperator), "the workspace operator is undeletable AS IT ACTUALLY COMES BACK from the API");
  assert(
    !canDeleteAgent({ role: "specialist", agent_kind: " Master " }),
    "and the guard is not defeated by casing or whitespace",
  );
}

assert(!canDeleteAgent({ role: "operator" }), "a deployment that DOES stamp role=operator stays undeletable too");
assert(!canDeleteAgent({ role: " Operator " }), "casing and whitespace do not defeat the role disjunct either");
assert(canDeleteAgent({ role: "specialist", agent_kind: "specialist" }), "an ordinary specialist is deletable");
assert(canDeleteAgent({}), "an unknown agent is deletable — the server is still the authority, this only hides a certain refusal");
assert(canDeleteAgent(null), "a missing agent does not crash the control off the screen");
assert(canDeleteAgent(undefined), "nor does an undefined one");

// ── Outcome honesty: three facts, never two ───────────────────────────────

{
  const gone = classifyAgentDeleteRecovery("absent", "Ridge");
  assert(gone.status === "deleted", "a lost response plus a demonstrably absent agent IS a success");

  const present = classifyAgentDeleteRecovery("present", "Ridge");
  assert(
    present.status === "unconfirmed",
    "still there after a lost response is UNCONFIRMED, not a definitive failure — an in-flight delete looks identical",
  );
  const unreadable = classifyAgentDeleteRecovery("unreadable", "Ridge");
  assert(unreadable.status === "unconfirmed", "an unreadable world is unconfirmed too");

  for (const outcome of [present, unreadable]) {
    const message = "message" in outcome ? outcome.message : "";
    assert(message.includes("Ridge"), "an unconfirmed message names the agent");
    assert(/safe to try again/i.test(message), "and says retrying is safe, rather than claiming it failed");
    assert(!/failed|couldn't delete/i.test(message), "and never claims a failure it cannot prove");
  }
}

{
  // "deleted" carries no message at all — there is nothing to explain, and a
  // success sentence is exactly where a lie would hide.
  const gone = classifyAgentDeleteRecovery("absent", "Ridge");
  assert(!("message" in gone), "a confirmed delete carries no explanatory prose");
}

// ── Wired, not just built — the defect this whole file is repairing ───────

const detail = readFileSync(new URL("./FleetAgentDetail.tsx", import.meta.url), "utf8");
assert(detail.includes('from "./agent-delete"'), "the agent detail surface imports the real delete module");
assert(/deleteFleetAgent\s*\(/.test(detail), "and actually calls it");
assert(/canDeleteAgent\s*\(/.test(detail), "and gates the control on the shared guard rather than an inline role check");
assert(detail.includes("AgentDeleteDialog"), "and renders the shared confirmation, not a fourth dialog");
{
  const onDeleted = /onDeleted=\{([\s\S]{0,200}?)\}\s*\n/.exec(detail);
  assert(onDeleted !== null, "CANARY: could not read the onDeleted handler — it moved or was renamed");
  const body = onDeleted?.[1] ?? "";
  assert(/\/agents/.test(body), "a confirmed delete navigates to the agents list");
  assert(
    !/agents\/\$\{|agentId/.test(body),
    "and never back to the agent it just removed — that route is now a 404 the person caused by succeeding",
  );
}

// ── Exactly ONE delete path ───────────────────────────────────────────────

const list = readFileSync(new URL("./AgentsList.tsx", import.meta.url), "utf8");
assert(list.includes('from "./agent-delete"'), "AgentsList uses the shared module too");
assert(
  !/role\s*===\s*"operator"/.test(detail) && !/role\s*===\s*"operator"/.test(list),
  "and no surface keeps the old role-only check, which matched nothing against real data",
);
for (const [name, source] of [["FleetAgentDetail.tsx", detail], ["AgentsList.tsx", list]] as const) {
  // Narrow on purpose: FleetAgentDetail legitimately DELETEs other things
  // (a BYOK capability key). What must not exist twice is a delete of the
  // AGENT itself.
  assert(
    !/fleet\/agents\/\$\{encodeURIComponent\(agentId\)\}`,\s*\{\s*method:\s*"DELETE"/.test(source.replace(/\s+/g, " ")) &&
      !/fleet\/agents\/[^`\n]*`[^;]{0,120}method:\s*"DELETE"/.test(source.replace(/\s+/g, " ")),
    `${name} issues no agent DELETE of its own — the request lives in exactly one place`,
  );
}

const shared = readFileSync(new URL("./agent-delete.ts", import.meta.url), "utf8");
assert(/method:\s*"DELETE"/.test(shared), "CANARY: the shared module really is the one that issues the DELETE");
assert(
  (shared.match(/method:\s*"DELETE"/g) || []).length === 1,
  "and it issues exactly one",
);
assert(
  /fleet\/agents\/\$\{encodeURIComponent\(agentId\)\}/.test(shared),
  "CANARY: the shared module really does target the agent route the backend exposes",
);


// ── The shared agents cache is refreshed before "deleted" is reported ─────
// Without this the caller navigates to the agents list while that cache
// still holds the agent, and at one remaining agent the list redirects
// straight back into the agent that was just deleted. Observed live.

assert(
  /refreshFleetAgents/.test(shared),
  "a confirmed delete refreshes the shared agents cache before it says so",
);
{
  const deletedBranch = shared.slice(shared.indexOf("export async function deleteFleetAgent"));
  const refreshIdx = deletedBranch.indexOf("refreshFleetAgents(workspaceId)");
  const returnIdx = deletedBranch.indexOf('return { status: "deleted" }');
  assert(refreshIdx > -1 && returnIdx > -1 && refreshIdx < returnIdx, "and does so BEFORE returning, not after");
  assert(
    /refreshFleetAgents\(workspaceId\)\.catch\(/.test(deletedBranch),
    "and a failed refresh never downgrades a confirmed delete into a reported failure",
  );
}

// ── Summary ───────────────────────────────────────────────────────────────

console.log(`${passed} passed, ${failed} failed`);
if (failed > 0) process.exit(1);
