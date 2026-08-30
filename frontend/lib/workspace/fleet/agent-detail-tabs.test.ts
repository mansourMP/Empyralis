/**
 * EVERY TAB THE AGENT DETAIL SURFACE OFFERS IS ROUTABLE — the assertion
 * that did not exist, which is why `context` shipped unreachable.
 *
 * The bug this guards, in full, because the shape matters more than the
 * instance: `context` (the per-agent CONTEXT GRANT — CLAUDE.md records the
 * founder's own "an agent belongs to the WORKSPACE... context is GRANTED,
 * never inherited") was declared in FleetAgentDetail's TabId, listed in its
 * TABS with a label and an icon, grouped under Reach in CONFIGURE_GROUPS,
 * and really rendered by `activeTab === "context" && <ContextTab .../>`.
 * Both `[tab]/page.tsx` files kept their OWN hand-written `VALID_TABS`
 * arrays and neither had ever been told about it, so every navigation to
 * `.../agents/{id}/context` — a deep link, or a click on "Context" in the
 * Configure sheet's own rail — was silently coerced to "chat". Offered,
 * rendered, unreachable, in silence: the dead control this product's laws
 * forbid outright.
 *
 * WHY A SOURCE SCAN AND NOT AN IMPORT. The expected set and the actual set
 * must come from different places or a conformance check can only confirm
 * itself (this repo's own `preflight._check_rls` lesson). The ACTUAL set is
 * imported for real — `isAgentDetailTab` / `AGENT_DETAIL_TAB_IDS`, the
 * exact function both routes call. The EXPECTED set is read out of
 * FleetAgentDetail.tsx's source text, because importing that module would
 * drag React, next/navigation and lucide-react into a plain `tsx` run. Same
 * discipline as agent-detail-status-honesty.test.ts, and the same reason.
 *
 * WHAT THE COMPILER ALREADY COVERS, so this file doesn't duplicate it:
 * `TABS` is typed `{ id: TabId; ... }[]` with `TabId = AgentDetailTabId`,
 * so a row whose id isn't in the shared module is a type error. What tsc
 * canNOT see is a route file growing a private list again — which is the
 * whole defect — so that is what the structural half below asserts.
 *
 * Every assertion here was proven RED before green: against the pre-fix
 * tree (two literal VALID_TABS arrays, no shared module) the routability
 * check fails on `context` and both no-private-list checks fail.
 *
 * Run: npx tsx lib/workspace/fleet/agent-detail-tabs.test.ts
 */
import { readFileSync } from "node:fs";

import { AGENT_DETAIL_TAB_IDS, isAgentDetailTab } from "./agent-detail-tabs";

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

const detailSource = readFileSync(new URL("./FleetAgentDetail.tsx", import.meta.url), "utf8");
// ONE route now, not two: the project-scoped twin
// (projects/[projectId]/agents/[agentId]/[tab]/page.tsx) was deleted
// 2026-08-30 — an agent is completely independent of any project, so it
// has exactly one real address. next.config.ts's LEGACY_REDIRECTS covers
// old bookmarks into the deleted route.
const ROUTE_FILES: { label: string; source: string }[] = [
  {
    label: "workspace-scoped route",
    source: readFileSync(
      new URL("../../../app/(account)/w/[workspaceId]/agents/[agentId]/[tab]/page.tsx", import.meta.url),
      "utf8",
    ),
  },
];

// ── Canaries — a scan that reaches nothing must fail loudly, never pass ───
// Every drift test in this codebase carries these; without them a moved
// file or a renamed export turns this whole file into a silent no-op that
// reports green while enforcing nothing.
assert(detailSource.length > 5_000, "CANARY: FleetAgentDetail.tsx was actually read");
for (const route of ROUTE_FILES) {
  assert(route.source.length > 500, `CANARY: the ${route.label} file was actually read`);
}

// ── THE SYNC ASSERTION — everything offered is routable ──────────────────
//
// Both of the surface's own tab lists are scanned, because they answer
// different questions and either one alone would miss a real regression:
//
//   TABS             what a tab IS (id + label + icon) — the presentational
//                    list, read by the Configure rail and the palette.
//   CONFIGURE_GROUPS which of those the Configure sheet actually OFFERS,
//                    grouped Brain / Reach / Compute. `context` was in
//                    here, which is precisely how it was clickable while
//                    being unroutable.
function idsFromTabsArray(source: string): string[] {
  const block = source.match(/const TABS:[\s\S]*?\n\];/);
  if (!block) return [];
  return Array.from(block[0].matchAll(/\{\s*id:\s*"([a-z_]+)"/g)).map((m) => m[1]);
}

function idsFromConfigureGroups(source: string): string[] {
  const block = source.match(/const CONFIGURE_GROUPS:[\s\S]*?\n\];/);
  if (!block) return [];
  return Array.from(block[0].matchAll(/tabs:\s*\[([^\]]*)\]/g))
    .flatMap((m) => Array.from(m[1].matchAll(/"([a-z_]+)"/g)).map((t) => t[1]));
}

const offeredTabs = idsFromTabsArray(detailSource);
const configureTabs = idsFromConfigureGroups(detailSource);

// Canaries on the scrapers themselves — a regex that silently stops
// matching would make every assertion below vacuously true.
assert(offeredTabs.length >= 10, `CANARY: the TABS scraper found real rows (got ${offeredTabs.length})`);
assert(configureTabs.length >= 6, `CANARY: the CONFIGURE_GROUPS scraper found real rows (got ${configureTabs.length})`);
assert(offeredTabs.includes("context"), "CANARY: `context` is still a tab this surface offers");
assert(configureTabs.includes("context"), "CANARY: `context` is still grouped under Reach in the Configure sheet");

for (const id of offeredTabs) {
  assert(isAgentDetailTab(id), `TABS offers "${id}" — both routes must accept it, or it lands on chat instead`);
}
for (const id of configureTabs) {
  assert(
    isAgentDetailTab(id),
    `the Configure sheet offers "${id}" — clicking it must open that section, not silently close the sheet`,
  );
}

// The regression itself, named, so a failure reads as what it is.
assert(isAgentDetailTab("context"), "`context` is routable — the per-agent context grant is reachable");

// ── NO ROUTE MAY KEEP A PRIVATE COPY OF THE LIST ─────────────────────────
// The defect was never a wrong value; it was a SECOND and THIRD list. A
// behavioural test cannot see a new one — it type-checks and behaves fine
// for every tab that happens to be in it — so this is structural.
for (const route of ROUTE_FILES) {
  assert(
    !/const\s+VALID_TABS\s*=/.test(route.source),
    `the ${route.label} keeps no private VALID_TABS array — one source, three consumers`,
  );
  assert(
    /isAgentDetailTab\s*\(/.test(route.source),
    `the ${route.label} decides tab validity with the SHARED isAgentDetailTab guard`,
  );
  assert(
    /from\s+"@\/lib\/workspace\/fleet\/agent-detail-tabs"/.test(route.source),
    `the ${route.label} imports the shared tab vocabulary`,
  );
  // An unrecognized tab must still land on the agent's front door rather
  // than 404 — the graceful fallback that makes retiring a tab safe.
  assert(
    /:\s*"chat"/.test(route.source),
    `the ${route.label} still coerces an unrecognized tab to "chat" rather than dead-ending`,
  );
}

assert(
  /type TabId = AgentDetailTabId;/.test(detailSource),
  "FleetAgentDetail's TabId IS the shared union — this is what makes an unroutable new tab a compile error",
);

// ── The vocabulary's own deliberate members ──────────────────────────────
assert(isAgentDetailTab("work"), "`work` stays routable — a retired surface kept alive so old bookmarks resolve");
assert(!isAgentDetailTab("overview"), "`overview` stays absent — deleted outright, coerces to chat");
assert(!isAgentDetailTab("tools"), "`tools` stays absent — deleted with the tool-authority tier, coerces to chat");
assert(!isAgentDetailTab(""), "an empty segment is not a tab");
assert(!isAgentDetailTab(undefined), "a missing segment is not a tab (params not yet resolved)");
assert(!isAgentDetailTab(42), "a non-string segment is not a tab");
assert(new Set(AGENT_DETAIL_TAB_IDS).size === AGENT_DETAIL_TAB_IDS.length, "the vocabulary carries no duplicates");

// --- Summary ---

console.log(`\n${passed} passed, ${failed} failed`);
if (failed > 0) process.exit(1);
