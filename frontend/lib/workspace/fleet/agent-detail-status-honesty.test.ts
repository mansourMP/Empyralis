/**
 * Two bugs, both caught live on a real agent (Billing Watcher) minutes
 * apart, both structural rather than behavioural — a behavioural test on
 * agentDisplayStatus/agent-view-options.ts already existed and stayed green
 * throughout, because the defect was never in the pure function; it was in
 * which surfaces called it.
 *
 * BUG 1 — "Working" had ONE definition (agentDisplayStatus /
 * agent-card-face.ts, per CLAUDE.md) and three of four surfaces used it.
 * FleetAgentDetail.tsx's own header called the BARE deriveAgentStatus, so
 * the workspace grid read "Working" for an agent whose own page, at the
 * same moment, read "Ready". Fixed by having the header enrich with the
 * agent's assigned tasks exactly like AgentCards/AgentsBoard/
 * AgentsGroupedList already do, reusing the SAME shared
 * useFleetWorkspaceTasks cache those surfaces poll (no extra request).
 *
 * BUG 2 — while the agent fetch was in flight (or had genuinely failed),
 * the header rendered the confident, FABRICATED "Unnamed agent" / "Not
 * deployed" — a definite claim about deployment state made before any data
 * had arrived. CLAUDE.md's outcome-honesty law: "empty" and "I could not
 * load this" and "haven't found out yet" may never share one rendering.
 *
 * A behavioural test cannot see either regression: agentDisplayStatus
 * itself was always correct, and a fixture that constructs `agent: null`
 * cannot tell "the caller never fetched" from "the caller is one line away
 * from calling the enriched function it already imports." Only reading the
 * real source proves the wiring, per this repo's own "built, tested, and
 * never wired" failure mode.
 *
 * Every structural assertion below was proven RED before it was green: run
 * against the pre-fix file (deriveAgentStatus called bare, agentLoadPhase
 * absent), every assertion in this file failed.
 *
 * Run: npx tsx lib/workspace/fleet/agent-detail-status-honesty.test.ts
 */
import { readFileSync } from "node:fs";

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

const DETAIL_PATH = new URL("./FleetAgentDetail.tsx", import.meta.url);
const detailSource = readFileSync(DETAIL_PATH, "utf8");
assert(detailSource.length > 5_000, "CANARY: FleetAgentDetail.tsx was actually read, not an empty/missing file");

// ── BUG 1 — the detail header shares the ONE "Working" definition ────────

assert(
  !/\bderiveAgentStatus\(/.test(detailSource),
  "the bare deriveAgentStatus(...) call is gone from this file — a future " +
    "edit reverting to it would type-check and behave silently, which is " +
    "exactly why this must be a source scan and not a behavioural test",
);
assert(
  /\bagentDisplayStatus\s*\(\s*agent\s*\?\?\s*\{\}\s*,\s*gateways\s*,\s*thisAgentTasks\s*\)/.test(detailSource),
  "the header's status comes from agentDisplayStatus fed this agent's own " +
    "tasks — the same enrichment AgentCards/AgentsBoard/AgentsGroupedList " +
    "already apply, per agent-view-options.ts's own header comment",
);
assert(
  /useFleetWorkspaceTasks\s*\(\s*workspaceId\s*\)/.test(detailSource),
  "the tasks come from the SAME shared, workspace-wide cache " +
    "PrimaryRail/Inbox/My work already poll (useFleetWorkspaceTasks) — a " +
    "second, page-local task fetch here would cost an extra request and " +
    "risk yet another surface silently disagreeing with the rest",
);
assert(
  /groupTasksByAgent\s*\(\s*workspaceTasksForStatus\s*\)/.test(detailSource),
  "tasks are bucketed with the shared groupTasksByAgent, not a hand-rolled filter",
);

// ── BUG 2 — loading / failed / not-found are never collapsed ─────────────

assert(
  /agentLoadPhase\s*:\s*"ready"\s*\|\s*"loading"\s*\|\s*"failed"\s*\|\s*"notFound"/.test(detailSource),
  "the header carries a real 4-state fact — ready/loading/failed/notFound — " +
    "never a boolean that can only express two of the four",
);
assert(
  /agentLoadPhase\s*=\s*agent\s*\n?\s*\?\s*"ready"\s*\n?\s*:\s*agentsLoading/.test(detailSource) ||
    /agent\s*\?\s*"ready"\s*:\s*agentsLoading\s*\?\s*"loading"\s*:\s*agentsError\s*\?\s*"failed"\s*:\s*"notFound"/.test(
      detailSource.replace(/\s+/g, " "),
    ),
  "a real agent always wins over a stale loading/error flag from a slower " +
    "sibling poll — data in hand must never be blanked by a loading state",
);
assert(
  /agentsLoading\?:\s*boolean;/.test(detailSource) && /agentsError\?:\s*string \| null;/.test(detailSource),
  "FleetAgentDetail accepts the caller's own useFleetAgents loading/error " +
    "so it can tell 'still fetching' from 'fetch failed' from 'not found'",
);
// The two render sites (top header + Profile sheet identity) must each
// branch on the phase rather than rendering the fixed agentLabel/
// statusLabel unconditionally — a behavioural test can't see a branch that
// exists but is never taken, so this checks both call sites by name.
assert(
  (detailSource.match(/agentLoadPhase === "loading"/g) || []).length >= 2,
  "both identity renders (header + Profile sheet) branch on the loading phase",
);
assert(
  (detailSource.match(/agentLoadPhase === "failed"/g) || []).length >= 2,
  "both identity renders (header + Profile sheet) branch on the failed phase",
);
// The fabricated strings must never render unconditionally — only reachable
// once the phase has actually resolved to "ready" or the rare "notFound".
assert(
  !/agentLabel=\{agent\?\.label \|\| "Unnamed agent"\}\s*\n\s*statusTone=\{status\.tone\}/.test(detailSource) ||
    detailSource.includes('agentLoadPhase={agentLoadPhase}'),
  "the header call site actually passes agentLoadPhase through, not just the fixed fallback strings",
);
assert(
  detailSource.includes("agentLoadPhase={agentLoadPhase}"),
  "the <AgentDetailHeader> call site is wired to the computed phase",
);

// ── Both routed pages actually supply agentsLoading/agentsError ──────────

for (const rel of [
  "../../../app/(account)/w/[workspaceId]/agents/[agentId]/[tab]/page.tsx",
  "../../../app/(account)/w/[workspaceId]/projects/[projectId]/agents/[agentId]/[tab]/page.tsx",
]) {
  const pageSource = readFileSync(new URL(rel, import.meta.url), "utf8");
  assert(pageSource.length > 500, `CANARY: ${rel} was actually read`);
  assert(
    /loading:\s*agentsLoading/.test(pageSource) && /error:\s*agentsError/.test(pageSource),
    `${rel} destructures useFleetAgents' own loading/error, not just \`agents\``,
  );
  assert(
    /agentsLoading=\{agentsLoading\}/.test(pageSource) && /agentsError=\{agentsError\}/.test(pageSource),
    `${rel} forwards both to <FleetAgentDetail> — a page that reads the ` +
      `fetch state and never passes it down is the same silent gap`,
  );
}

console.log(`\n${passed} passed, ${failed} failed`);
if (failed > 0) {
  process.exit(1);
}
