/**
 * VIEW OPTIONS FOR THE WORKSPACE AGENTS PAGE — the rule, and the two things a
 * behavioural test structurally cannot see.
 *
 * WHY THIS FILE EXISTS AT ALL. The Cards/Board/List switch was wired,
 * unwired and re-wired on this page inside six days, and the reason it was
 * pulled the second time was never the switch: both alternative renderings
 * drew `activity_preview`, a LIFECYCLE VERB, so every board card and every
 * list row read "Created" — true of every agent that has ever existed, so a
 * column of it distinguishes nothing. agent-card-face.ts had already been
 * written to kill exactly that line on the card grid; shipping it on two more
 * surfaces put it back one click away, and the founder reported two
 * disagreeing layouts on one screen.
 *
 * CARDS ITSELF IS GONE, 2026-08-30, on a separate and later founder call:
 * *"i do not want cards thing default should be list ... i only want to see
 * list and board!"* AgentCards.tsx and the "cards" layout value are deleted;
 * List is now the default. `agentCardReach` (agent-card-face.ts) is
 * untouched and still the ONE reach rule both remaining layouts draw — that
 * module was never the Cards grid itself, only the shared face logic every
 * rendering reads, so deleting the grid must not touch it.
 *
 * So the assertions below are in two halves. The behavioural half pins the
 * vocabulary and the maths. The structural half pins the things that made
 * this regress: the lifecycle verb is GONE rather than merely unused, both
 * renderings read the shared reach rule, the two components are IMPORTED AND
 * RENDERED rather than importable-and-dead, and List is still the default.
 *
 * Run: npx tsx lib/workspace/fleet/agent-view-options.test.ts
 */

import { existsSync, readFileSync } from "node:fs";

import {
  AGENT_GROUPING_OPTIONS,
  AGENT_LAYOUT_OPTIONS,
  AGENT_ORDERING_DEFAULT_DIRECTION,
  AGENT_ORDERING_OPTIONS,
  DEFAULT_AGENT_VIEW_OPTIONS,
  agentDisplayStatus,
  agentStatusGroup,
  agentSurfaceFor,
  agentViewStorageKey,
  displayPropertiesFor,
  groupAgents,
  isDefaultAgentViewOptions,
  orderDirectionLabel,
  readAgentViewOptions,
  resetAgentViewOptions,
  sortAgentsForView,
  writeAgentViewOptions,
} from "./agent-view-options";
import { planAgentCardFace } from "./agent-card-face";

// A real localStorage, so the persistence assertions drive the REAL round-trip
// rather than a stand-in for it. Installed after the imports (which hoist) and
// before anything calls read/writeAgentViewOptions — the module only touches
// `window` inside those two functions, never at import time.
const store = new Map<string, string>();
(globalThis as unknown as Record<string, unknown>).window = {
  localStorage: {
    getItem: (k: string) => (store.has(k) ? store.get(k)! : null),
    setItem: (k: string, v: string) => void store.set(k, v),
    removeItem: (k: string) => void store.delete(k),
  },
};

type Options = typeof DEFAULT_AGENT_VIEW_OPTIONS;

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

function opts(over: Partial<Options> = {}): Options {
  return { ...DEFAULT_AGENT_VIEW_OPTIONS, display: { ...DEFAULT_AGENT_VIEW_OPTIONS.display }, ...over };
}

/* eslint-disable @typescript-eslint/no-explicit-any */
function agent(over: Record<string, unknown> = {}): any {
  return {
    agent_id: "ainstall_a",
    label: "Alpha",
    role: "specialist",
    status: "ready",
    enabled: true,
    runtime_target: "cloud",
    // "online" resolves to tone "ready" (fleet-presentation.deriveStatus), the
    // ordinary healthy agent. "unknown" is the NOT-DEPLOYED state and groups
    // as offline — a fixture defaulting to it would have made every case below
    // secretly about a broken agent.
    hardware_status: "online",
    last_heartbeat: null,
    ...over,
  };
}

// ── TWO LAYOUTS, AND LIST LEADS (Cards removed 2026-08-30) ────────────────
assert(
  AGENT_LAYOUT_OPTIONS.map((o) => o.value).join(",") === "list,board",
  `exactly two layouts, list first — got ${AGENT_LAYOUT_OPTIONS.map((o) => o.value).join(",")}`,
);
assert(
  AGENT_LAYOUT_OPTIONS.map((o) => o.label).join("|") === "List|Board",
  "each carries the human word, not the token",
);
assert(
  !AGENT_LAYOUT_OPTIONS.some((o) => (o.value as string) === "cards"),
  `cards is not a legal layout any more — the founder's own words: "i only want to see list and board"`,
);
assert(
  DEFAULT_AGENT_VIEW_OPTIONS.layout === "list",
  "the DEFAULT is List — a reader who never opens the popover lands on the row list, not a grid",
);
assert(
  DEFAULT_AGENT_VIEW_OPTIONS.grouping === "none",
  "…and no grouping, so the default is one plain surface",
);
assert(
  Object.values(DEFAULT_AGENT_VIEW_OPTIONS.display).every((v) => v === true),
  "…with every display property on, so nothing is hidden until someone hides it",
);

// A layout is a real value, not the absence of a grouping. That distinction is
// the whole reason "cards" exists: while it was spelled `list` + `none`, the
// popover's List chip lit up over a grid of cards.
for (const option of AGENT_LAYOUT_OPTIONS) {
  assert(
    agentSurfaceFor(opts({ layout: option.value })) === option.value,
    `${option.value} is its own surface, decided by layout alone`,
  );
  assert(
    agentSurfaceFor(opts({ layout: option.value, grouping: "status" })) === option.value,
    `…and a grouping never changes which surface ${option.value} is`,
  );
}

// ── DISPLAY PROPERTIES: both surfaces offer the same six ──────────────────
// Cards used to be the exception here — a card face is two facts and refuses
// a third, so it offered nothing to toggle. With Cards gone, List and Board
// are the whole vocabulary and both get every field.
for (const surface of AGENT_LAYOUT_OPTIONS.map((o) => o.value)) {
  assert(
    displayPropertiesFor(surface).map((p) => p.key).join(",") ===
      "brain,placement,channels,lastActive,cost,status",
    `${surface} offers all six, in the order the row draws them`,
  );
}

// ── DIRTY / RESET ─────────────────────────────────────────────────────────
assert(isDefaultAgentViewOptions(DEFAULT_AGENT_VIEW_OPTIONS), "the default is not dirty");
for (const layout of AGENT_LAYOUT_OPTIONS) {
  assert(
    isDefaultAgentViewOptions(opts({ layout: layout.value })),
    `switching to ${layout.value} is NOT dirty — Board-or-List is a view you are standing in, not a preference Reset should yank you out of`,
  );
}
assert(!isDefaultAgentViewOptions(opts({ grouping: "status" })), "a grouping is dirty");
assert(!isDefaultAgentViewOptions(opts({ ordering: "cost" })), "an ordering is dirty");
assert(!isDefaultAgentViewOptions(opts({ direction: "asc" })), "a direction is dirty");
assert(
  !isDefaultAgentViewOptions(opts({ display: { ...DEFAULT_AGENT_VIEW_OPTIONS.display, cost: false } })),
  "a hidden property is dirty",
);
const reset = resetAgentViewOptions(opts({ layout: "board", grouping: "project", ordering: "name" }));
assert(reset.layout === "board", "Reset keeps the layout you are looking at");
assert(
  reset.grouping === "none" && reset.ordering === DEFAULT_AGENT_VIEW_OPTIONS.ordering,
  "…and clears everything else",
);

// ── PERSISTENCE ───────────────────────────────────────────────────────────
// The key is versioned, and the version was bumped when "list" changed
// meaning. A v1 blob must not be read under the v2 vocabulary — that would
// move a reader into a view they never picked.
assert(agentViewStorageKey("ws1") === "fleet:agent-view:v2:ws1", "the key carries its own version and workspace");
assert(
  !agentViewStorageKey("ws1").includes("task-view"),
  "…in its OWN namespace, never Tasks' — the two features can never clobber each other",
);
store.set("fleet:agent-view:v1:ws1", JSON.stringify({ layout: "board", grouping: "none" }));
assert(
  readAgentViewOptions("ws1").layout === "list",
  "a v1 blob lives under a DIFFERENT key entirely and is never read — the v2 default (List) stands, not whatever the v1 blob says (if it were read, this would come back 'board')",
);

writeAgentViewOptions("ws1", opts({ layout: "board", grouping: "project", ordering: "cost", direction: "asc" }));
const roundTripped = readAgentViewOptions("ws1");
assert(
  roundTripped.layout === "board" && roundTripped.grouping === "project" && roundTripped.ordering === "cost" && roundTripped.direction === "asc",
  "a real choice round-trips through localStorage",
);
// THE REAL-WORLD CASE THIS FIX EXISTS FOR: an existing reader's blob still
// says `layout: "cards"` — the value this popover offered, and defaulted to,
// until 2026-08-30. It must coerce to the new default rather than crashing
// or rendering nothing (oneOf's allow-list no longer contains it).
store.set("fleet:agent-view:v2:ws1", JSON.stringify({ layout: "cards", grouping: "none", ordering: "last_active" }));
const stale = readAgentViewOptions("ws1");
assert(
  stale.layout === "list",
  `a stored "cards" (no longer a legal value) coerces to the new default (List), got "${stale.layout}"`,
);

store.set("fleet:agent-view:v2:ws1", JSON.stringify({ layout: "kanban", grouping: "moon", ordering: "vibes" }));
const bogus = readAgentViewOptions("ws1");
assert(
  bogus.layout === "list" && bogus.grouping === "none" && bogus.ordering === DEFAULT_AGENT_VIEW_OPTIONS.ordering,
  "any other value outside the vocabulary falls back to the default rather than being trusted",
);
store.set("fleet:agent-view:v2:ws1", "{not json");
assert(readAgentViewOptions("ws1").layout === "list", "unparsable storage is the default, never a throw");
assert(readAgentViewOptions("").layout === "list", "no workspace id is the default, and writes nothing");

// ── GROUPING ──────────────────────────────────────────────────────────────
const three = [agent(), agent({ agent_id: "b", label: "Beta" }), agent({ agent_id: "c", label: "Gamma" })];
const ungrouped = groupAgents(three, "none");
assert(ungrouped.length === 1 && ungrouped[0].agents.length === 3, "no grouping is ONE bucket holding everything");
assert(
  ungrouped[0].ungrouped === true,
  "…and it is FLAGGED as the synthetic bucket, so the list can draw its rows with no heading and no collapse",
);
for (const grouping of AGENT_GROUPING_OPTIONS.filter((g) => g.value !== "none")) {
  assert(
    groupAgents(three, grouping.value).every((section) => section.ungrouped !== true),
    `a real grouping (${grouping.value}) never produces the synthetic bucket`,
  );
}
const stopped = agent({ agent_id: "s", label: "Stopped one", stopped: { active: true } });
const byStatus = groupAgents([...three, stopped], "status");
assert(
  byStatus.every((section) => section.count > 0),
  "a section exists only if it holds an agent — never a heading over zero rows",
);
assert(
  byStatus.some((section) => section.statusGroup === "offline"),
  "a stopped agent lands in the offline-or-stopped column",
);
const byProject = groupAgents(
  [agent({ project_id: "p1" }), agent({ agent_id: "b", label: "Beta" })],
  "project",
);
assert(
  byProject[byProject.length - 1].empty === true,
  "the no-project catch-all sorts LAST — same convention the tasks grouping uses",
);

// ── ORDERING ──────────────────────────────────────────────────────────────
const never = agent({ agent_id: "never", label: "Never", last_activity: null });
const older = agent({ agent_id: "older", label: "Older", last_activity: "2026-08-01T00:00:00Z" });
const newer = agent({ agent_id: "newer", label: "Newer", last_activity: "2026-08-20T00:00:00Z" });
const input = [never, older, newer];
const desc = sortAgentsForView(input, "last_active", "desc", new Map());
const asc = sortAgentsForView(input, "last_active", "asc", new Map());
assert(input.map((a) => a.agent_id).join(",") === "never,older,newer", "sorting never mutates the caller's array");
assert(desc[0].agent_id === "newer", "newest first, descending");
assert(asc[0].agent_id === "older", "oldest first, ascending");
assert(
  desc[desc.length - 1].agent_id === "never" && asc[asc.length - 1].agent_id === "never",
  "an agent that has NEVER been active sinks to the bottom in BOTH directions — flipping the sort must not float missing data to the top",
);
const cost = new Map([["cheap", 0], ["dear", 5]]);
const byCost = sortAgentsForView(
  [agent({ agent_id: "dear", label: "Dear" }), agent({ agent_id: "cheap", label: "Cheap" })],
  "cost",
  "asc",
  cost,
);
assert(
  byCost[0].agent_id === "cheap",
  "$0 is a REAL fact about an agent (it has spent nothing), not missing data — lowest-first genuinely means it first",
);
assert(
  sortAgentsForView([newer, older], "name", "asc", new Map()).map((a) => a.label).join(",") === "Newer,Older",
  "name sorts alphabetically",
);
assert(
  AGENT_ORDERING_OPTIONS.every((o) => AGENT_ORDERING_DEFAULT_DIRECTION[o.value] !== undefined),
  "every ordering key declares the direction that reads right for it",
);
assert(orderDirectionLabel("cost", "desc") === "Highest first", "the direction toggle says what it DOES, not 'desc'");
assert(orderDirectionLabel("name", "asc") === "A to Z", "…in the reader's words for that key");

// ── ONE STATUS, EVERY SURFACE ──────────────────────────────────────────────
// The claim the Board/List pair rests on, asserted rather than commented:
// agentDisplayStatus and the card face agree, so an agent cannot read
// "Working" on one layout and "Ready" one click away.
const activeTask = [{ id: "t1", status: "in_progress", assignee_agent_id: "ainstall_a" }];
for (const [label, a, tasks] of [
  ["a ready agent holding an in-progress task", agent(), activeTask],
  ["a ready agent with nothing in flight", agent(), []],
  ["a stopped agent", agent({ stopped: { active: true } }), activeTask],
  ["an offline agent", agent({ hardware_status: "offline" }), []],
] as const) {
  const shared = agentDisplayStatus(a, [], tasks);
  const face = planAgentCardFace(a, { tone: shared.tone, label: shared.label }, tasks);
  assert(
    face.tone === shared.tone && face.stateLabel === shared.label,
    `${label}: the card face and the row status say the same thing`,
  );
}
assert(
  agentStatusGroup(agent(), [], activeTask) === "working",
  "an in-progress task IS the working signal — current_run_id alone is de-facto always null",
);
assert(agentStatusGroup(agent(), [], []) === "idle", "reachable and quiet is idle");
assert(
  agentStatusGroup(agent({ hardware_status: "error" }), [], []) === "needs_attention",
  "a real config-honesty failure is the only thing that reaches Needs attention",
);

// ── STRUCTURAL: the lifecycle verb is gone, not merely unused ─────────────
const read = (name: string) => readFileSync(new URL(name, import.meta.url), "utf8");
const stripComments = (css: string) => css.replace(/\/\*[\s\S]*?\*\//g, "");

const moduleSource = read("./agent-view-options.ts");
assert(moduleSource.length > 500, "CANARY: agent-view-options.ts was actually read");
assert(
  !/export function agentActivityPreviewText/.test(moduleSource),
  "agentActivityPreviewText is DELETED, not left exported — an exported helper is how a rejected line comes back",
);

const boardSource = read("./AgentsBoard.tsx");
const listSource = read("./AgentsGroupedList.tsx");
assert(boardSource.length > 500 && listSource.length > 500, "CANARY: both renderings were actually read");
for (const [name, source] of [["AgentsBoard.tsx", boardSource], ["AgentsGroupedList.tsx", listSource]] as const) {
  const code = source.replace(/\/\*[\s\S]*?\*\//g, "").replace(/^\s*\/\/.*$/gm, "");
  assert(
    !/activity_preview|agentActivityPreviewText/.test(code),
    `${name} draws no lifecycle verb — "Created" is true of every agent that has ever existed`,
  );
  assert(
    /agentCardReach\s*\(/.test(code),
    `${name} reads the SHARED reach rule (agent-card-face.ts), so it cannot drift from the card grid`,
  );
  assert(
    source.includes('from "./agent-card-face"'),
    `${name} imports it rather than reimplementing it`,
  );
}
// The list is what renders grouping "none" now, so it must be able to accept
// it — a narrowed type there would make the default List view unreachable.
assert(
  /grouping: AgentGrouping;/.test(listSource) && !/Exclude<AgentGrouping, "none">/.test(listSource),
  "the list accepts every grouping including 'none' — that case is its job, not a third rendering's",
);
assert(
  /section\.ungrouped \? null : \(/.test(listSource),
  "…and draws NO heading for the synthetic bucket, whose collapse control's only effect would be to hide the whole list",
);

// ── STRUCTURAL: wired, not merely built ──────────────────────────────────
const AGENTS_DIR = new URL("../../../app/(account)/w/[workspaceId]/agents/", import.meta.url);
const pageSource = readFileSync(new URL("page.tsx", AGENTS_DIR), "utf8");
assert(pageSource.length > 500, "CANARY: the Agents page source was actually read");
assert(
  !existsSync(new URL("./AgentCards.tsx", import.meta.url)),
  "AgentCards.tsx is DELETED, not merely unimported — an importable-and-dead component is how a rejected layout comes back",
);
assert(
  !pageSource.includes('from "@/lib/workspace/fleet/AgentCards"') && !/<AgentCards\b/.test(pageSource),
  "…and the page no longer imports or renders it (a historical mention in a comment explaining the deletion is fine)",
);
for (const component of ["AgentsBoard", "AgentsGroupedList", "AgentViewOptions"] as const) {
  assert(
    new RegExp(`<${component}\\b`).test(pageSource),
    `the page RENDERS ${component} — built-and-never-wired is this codebase's most common defect`,
  );
}
assert(
  /viewOptions\.layout === "board"/.test(pageSource),
  "…and branches on the persisted layout (Board) rather than a second opinion grown on the page",
);
assert(!/layout === "cards"/.test(pageSource), "…and carries no branch for the deleted cards layout");
assert(
  !/viewOptions\.layout === "list"/.test(pageSource),
  "List has no explicit condition of its own — it is the unconditional fall-through, the exact slot Cards used to occupy as the old default",
);
assert(
  /value=\{query\}/.test(pageSource) && (pageSource.match(/value=\{query\}/g) || []).length === 1,
  "ONE search input, drawn once at the page level rather than once per layout — a filter that exists on one view and not the other is a control that vanishes when you switch",
);
// "nothing matched your filter" and "there is nothing here" are different
// facts. Both AgentsBoard and AgentsGroupedList return null on an empty list,
// so without this branch a query that matches nothing renders a blank pane.
assert(
  /No agents match/.test(pageSource) && /listAgents\.length === 0/.test(pageSource),
  "a query that matches nothing says so on every layout",
);

// ── STRUCTURAL: the deleted toolbar wrapper stays deleted ────────────────
// It existed only to arbitrate between AgentViewOptions and a FleetToolbar
// that this page no longer renders. A leftover rule is how the next author
// rebuilds a shell that was deliberately taken away.
const themeCss = stripComments(readFileSync(new URL("./fleet-theme.css", import.meta.url), "utf8"));
assert(
  /\.fleet-agent-view-options\s*\{/.test(themeCss),
  "CANARY: stripping comments left real rules behind — the scans below can actually fail",
);
assert(!/fleet-agent-view-cluster/.test(themeCss), "the view-options cluster wrapper's CSS is deleted");
assert(!/fleet-agent-view-cluster/.test(pageSource), "…and nothing renders it");
assert(
  !/fleet-content-main--agent-board/.test(themeCss),
  "the board's old .fleet-content-main shell is deleted too — this page does not nest that box, and nesting it stacked padding and produced a second scroller",
);
assert(existsSync(new URL("./agent-cards.css", import.meta.url)), "CANARY: the agents stylesheet exists");
const cardsCss = stripComments(readFileSync(new URL("./agent-cards.css", import.meta.url), "utf8"));
assert(
  /\.fleet-agent-surface-toolbar\s*\{/.test(cardsCss),
  "the page's one toolbar row is a real rule (search left, view options right)",
);
assert(
  !/\.fleet-content-toolbar/.test(cardsCss),
  "…and is NOT .fleet-content-toolbar, which carries its own 32px padding and would double .fleet-content's",
);
assert(
  !/\.fleet-agent-card-grid\s*\{/.test(cardsCss) && !/\.fleet-agent-card\s*\{/.test(cardsCss),
  "the deleted grid's own face rules (.fleet-agent-card-grid, .fleet-agent-card) are gone from this file — only the toolbar/search/empty-state rules List and Board still use remain",
);
// Match the IMPORT STATEMENT, not the filename anywhere in the file. The
// first version of this assertion was `pageSource.includes("agent-cards
// .css")`, which the explanatory COMMENT four lines above the import
// satisfies all by itself — deleting the real import left the guard green.
// A check a comment can satisfy is not checking the code. (CLAUDE.md: a
// check that derives its expectations from the thing it checks is blind
// and reports "passed".)
assert(
  /^\s*import\s+["'][^"']*agent-cards\.css["']\s*;?\s*$/m.test(pageSource),
  "the page imports this stylesheet DIRECTLY now that AgentCards.tsx (its old importer) is deleted — otherwise the toolbar/search/empty-state rules never load at all",
);

console.log(`\n${passed} passed, ${failed} failed`);
if (failed > 0) {
  process.exit(1);
}
