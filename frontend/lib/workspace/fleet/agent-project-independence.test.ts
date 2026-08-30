/**
 * AN AGENT IS COMPLETELY INDEPENDENT OF ANY PROJECT — the founder's hard
 * rule, restated 2026-08-30: *"Agents must not belong to the project, but
 * it must be actually the opposite — they are completely independent.
 * Things agents [do] doesn't belong to a project. That's the hard rule!"*
 * This hardens a decision already in CLAUDE.md ("An agent belongs to the
 * WORKSPACE, never a project. Do not re-nest agents under projects and do
 * not add an Agents tab to a project"), which the code had drifted from in
 * several places by the time this test was written:
 *
 *   - A per-project agent count (and cost/tokens/last-active/status rolled
 *     up the same way) rendered on every Projects page row, derived by
 *     grouping agents on `project_id`.
 *   - "Project" was an agent GROUPING option on the workspace Agents page.
 *   - projects/[projectId]/agents/** was a second, project-scoped route to
 *     the same agent detail surface the workspace-level route already
 *     serves — reachable by URL though never offered as a tab.
 *   - Several live agent-link builders (the command palette, quick-create's
 *     post-creation navigation, the solo-agent redirect, AgentCreateCard's
 *     post-creation hardware link) built that project-scoped URL.
 *
 * Same discipline as primary-rail-nav.test.ts and agent-view-options.test.ts
 * — assertions proven by importing the REAL modules/config (never
 * re-deriving the expected shape here) plus source-text scans for the
 * files that build agent links from a template string rather than a typed
 * helper, so a link a compiler cannot see re-nesting agents under a
 * project still fails here.
 *
 * Run: npx tsx lib/workspace/fleet/agent-project-independence.test.ts
 */

import { existsSync, readFileSync } from "node:fs";

import nextConfig from "../../../next.config";
import { AGENT_GROUPING_OPTIONS } from "./agent-view-options";

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

// ── The route is gone, not just unlinked ──────────────────────────────────

const DELETED_ROUTE_DIR = new URL(
  "../../../app/(account)/w/[workspaceId]/projects/[projectId]/agents",
  import.meta.url,
);
assert(
  !existsSync(DELETED_ROUTE_DIR),
  "projects/[projectId]/agents/** does not exist -- an agent has exactly one real address (agents/[agentId]/[tab]/page.tsx), not one per project it could be reached through",
);

// ── "project" is not an offered agent grouping ────────────────────────────

assert(
  // Cast through `string`, deliberately: "project" is no longer a member of
  // the AgentGrouping union at all, which is the whole point — a plain
  // `g.value === "project"` comparison would be a compile error (no
  // overlap), the strongest possible version of this assertion. The cast
  // keeps the check meaningful for a value read back from, say, a stored
  // popover preference, which is untyped at the boundary.
  !AGENT_GROUPING_OPTIONS.some((g) => (g.value as string) === "project"),
  '"project" is not an agent grouping option -- agents are not grouped by a thing they do not belong to',
);
assert(
  (["none", "status", "placement"] as const).every((v) => AGENT_GROUPING_OPTIONS.some((g) => g.value === v)),
  "the three real groupings survive -- this did not just delete the whole feature",
);

// ── No live source file builds a project-scoped agent link ───────────────
//
// A canary list, not a repo-wide glob: every file below is a KNOWN, live
// agent-link builder (per the sweep that wrote this test) — the exact set
// a future addition to this list would need to join deliberately, which is
// the point. A regex over the whole tree would also flag next.config.ts's
// own redirect definitions (correct, and asserted below) and this file's
// own strings, so a curated list is the honest boundary, not a shortcut.
const LIVE_LINK_BUILDERS = [
  "./FleetCommandPalette.tsx",
  "./agent-quick-create.ts",
  "./AgentCreateCard.tsx",
  "./first-agent-empty.tsx",
  "./AgentsGroupedList.tsx",
  "../../../app/(account)/w/[workspaceId]/agents/page.tsx",
  "../../../app/(account)/w/[workspaceId]/agents/[agentId]/[tab]/page.tsx",
  "../../../app/(account)/w/[workspaceId]/projects/[projectId]/page.tsx",
];

// Matches the exact shape a project-scoped agent link took:
// /projects/{...}/agents/{...} with an interpolated project segment in
// between. Deliberately narrow (not a bare "/projects/" grep, which would
// also flag a project's OWN detail link, a completely legitimate thing for
// e.g. FleetCommandPalette's projectActions to build) — this only matches
// a project segment sitting directly between "projects" and "agents" in
// one template literal.
const PROJECT_SCOPED_AGENT_LINK_RE = /\/projects\/\$\{[^}]*\}\/agents\//;

let filesScanned = 0;
for (const rel of LIVE_LINK_BUILDERS) {
  const source = readFileSync(new URL(rel, import.meta.url), "utf8");
  assert(source.length > 500, `CANARY: ${rel} was actually read`);
  filesScanned++;
  assert(
    !PROJECT_SCOPED_AGENT_LINK_RE.test(source),
    `${rel} builds no /projects/{id}/agents/{id} link -- an agent's address never carries a project segment`,
  );
}
assert(filesScanned === LIVE_LINK_BUILDERS.length, "CANARY: the scan reached every file in the list, not a subset");

// ── LEGACY_REDIRECTS still sends an old bookmark somewhere real ──────────
// Imported for real (next.config's own redirects() function) rather than
// re-typed here, so a later edit that drops these entries fails here
// instead of silently 404ing a bookmark.
async function main() {
  assert(typeof nextConfig.redirects === "function", "CANARY: next.config.ts exports redirects()");
  const redirects = await nextConfig.redirects!();
  assert(redirects.length > 5, "CANARY: next.config's redirect list was actually read, not an empty stand-in");
  for (const [source, destination] of [
    ["/w/:workspaceId/projects/:projectId/agents", "/w/:workspaceId/agents"],
    ["/w/:workspaceId/projects/:projectId/agents/:agentId", "/w/:workspaceId/agents/:agentId"],
    ["/w/:workspaceId/projects/:projectId/agents/:agentId/:tab", "/w/:workspaceId/agents/:agentId/:tab"],
  ] as const) {
    assert(
      redirects.some((r) => r.source === source && r.destination === destination),
      `an old bookmark to ${source} still redirects to ${destination}, dropping only the project segment`,
    );
  }

  console.log(`\n${passed} passed, ${failed} failed`);
  if (failed > 0) {
    process.exit(1);
  }
}

void main();
