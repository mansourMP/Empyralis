/**
 * Work ledger rules, proven against the REAL functions work-ledger.ts
 * exports rather than a re-typed copy of them — same discipline
 * my-work.test.ts and inbox-needs-you.test.ts already apply.
 *
 * Fixtures below are built from the PRODUCER's real row shape
 * (control_plane_repository._row_to_agent_trace /
 * routes_agent_traces.py's list_agent_traces response), not from a guess —
 * see work-ledger.ts's own header for the file:line citations. A mock
 * protects a seam, not a path; a fixture protects a shape, not a path
 * (CLAUDE.md) — these fixtures carry the exact field set and the exact
 * literal `root_agent_id`/`surface`/`outcome` values the backend actually
 * writes, including both real assistant literals ("sage" and
 * "sage_main_agent") and the "specialist:{install_id}" prefix a real
 * agent's trace carries.
 *
 * Run: npx tsx lib/workspace/fleet/work-ledger.test.ts
 */

import {
  WORK_LEDGER_STALE_AFTER_MS,
  excludeAssistantRows,
  filterWorkLedgerRows,
  parseWorkLedgerAgentRef,
  planWorkLedgerView,
  sortWorkLedgerRows,
  workLedgerDetailHref,
  workLedgerStatus,
  workLedgerStatusCounts,
  workLedgerSurfaceLabel,
  type WorkLedgerTraceShape,
} from "./work-ledger";

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

const trace = (over: Partial<WorkLedgerTraceShape> & { id?: string } = {}): WorkLedgerTraceShape & { id: string } => ({
  id: over.id || "trace_1",
  root_agent_id: "specialist:ainstall_abc123",
  surface: "web",
  started_at: "2026-08-29T10:00:00Z",
  finished_at: "2026-08-29T10:01:00Z",
  outcome: "success",
  ...over,
});

// ── workLedgerStatus: outcome → displayed state ───────────────────────────

// The fixture's own start, so these two assertions keep testing what they
// were written to test -- that `finished_at` governs, not `outcome` -- and
// are not silently retested against the staleness rule added later. That
// rule gets its own explicit assertions further down.
const FIXTURE_NOW = Date.parse("2026-08-29T10:05:00Z");

assert(
  workLedgerStatus(trace({ finished_at: null, outcome: null }), FIXTURE_NOW) === "working",
  "a trace with no finished_at is still working, regardless of outcome",
);
assert(
  workLedgerStatus(trace({ finished_at: null, outcome: "success" }), FIXTURE_NOW) === "working",
  "finished_at wins over a stray outcome value on an unfinished row",
);
assert(
  workLedgerStatus(trace({ outcome: "needs_input" })) === "waiting",
  "needs_input reads as waiting",
);
assert(
  workLedgerStatus(trace({ outcome: "NEEDS_INPUT" })) === "waiting",
  "outcome comparison is case-insensitive",
);
assert(
  workLedgerStatus(trace({ outcome: "failed" })) === "failed",
  "failed reads as failed — the exact bug WorkTab.tsx's own comment documents fixing",
);
assert(
  workLedgerStatus(trace({ outcome: "success" })) === "done",
  "success reads as done",
);
assert(
  workLedgerStatus(trace({ outcome: "partial" })) === "done",
  "partial folds into done — ported from classifyThreadStatus's own existing behavior, not new leniency",
);
assert(
  workLedgerStatus(trace({ outcome: "some_future_outcome_value" })) === "done",
  "an outcome vocabulary this module doesn't recognize still reads as done, never silently as failed",
);

// ── parseWorkLedgerAgentRef: the three real root_agent_id shapes ─────────

assert(
  (() => {
    const ref = parseWorkLedgerAgentRef("specialist:ainstall_abc123");
    return ref.kind === "specialist" && ref.installId === "ainstall_abc123";
  })(),
  "specialist:{id} parses to the bare install id",
);
assert(
  parseWorkLedgerAgentRef("sage").kind === "assistant",
  "the bare 'sage' literal (agent_turn.py's own fallback) is the assistant",
);
assert(
  parseWorkLedgerAgentRef("sage_main_agent").kind === "assistant",
  "SAGE_MAIN_AGENT_ID ('sage_main_agent', the dedicated Ask AI surface) is also the assistant",
);
assert(
  parseWorkLedgerAgentRef("specialist:").kind === "assistant",
  "a malformed specialist: prefix with no id falls back to assistant, not an empty installId",
);
assert(
  parseWorkLedgerAgentRef(null).kind === "assistant",
  "a missing root_agent_id is the assistant, never a crash",
);

// ── workLedgerDetailHref: real destination, or honestly none ─────────────

assert(
  workLedgerDetailHref({ workspaceId: "ws_1", rootAgentId: "specialist:ainstall_abc123" }) ===
    "/w/ws_1/agents/ainstall_abc123/work",
  "a specialist row links to that agent's own Work tab",
);
assert(
  workLedgerDetailHref({ workspaceId: "ws_1", rootAgentId: "sage_main_agent" }) === null,
  "an assistant row has no page to link to — the Ask AI console is a docked panel, not a route",
);
assert(
  Boolean(workLedgerDetailHref({ workspaceId: "ws 1", rootAgentId: "specialist:a b" })?.includes("%20")),
  "both the workspace id and the install id are URL-encoded into the href",
);

// ── workLedgerSurfaceLabel ────────────────────────────────────────────────

assert(workLedgerSurfaceLabel("web") === "Web", "web surface");
assert(workLedgerSurfaceLabel("sage") === "Ask AI console", "the sage surface prints the current product name, never the retired one");
assert(workLedgerSurfaceLabel(null) === "Unknown", "a missing surface is honestly Unknown");
assert(
  workLedgerSurfaceLabel("some_new_surface") === "Some New Surface",
  "an unrecognized future surface still prints something real, not a bare 'Unknown'",
);

// ── sortWorkLedgerRows: newest started first, id as a stable tiebreak ────

const unsorted = [
  trace({ id: "a", started_at: "2026-08-29T09:00:00Z" }),
  trace({ id: "b", started_at: "2026-08-29T11:00:00Z" }),
  trace({ id: "c", started_at: "2026-08-29T10:00:00Z" }),
];
assert(
  sortWorkLedgerRows(unsorted).map((t) => t.id).join(",") === "b,c,a",
  "rows sort newest-started first",
);
const tied = [
  trace({ id: "x", started_at: "2026-08-29T09:00:00Z" }),
  trace({ id: "y", started_at: "2026-08-29T09:00:00Z" }),
];
assert(
  sortWorkLedgerRows(tied).map((t) => t.id).join(",") === "y,x",
  "a tied started_at breaks on id, descending — a stable, reproducible order",
);
assert(
  (() => {
    const original = [trace({ id: "a", started_at: "2026-08-29T09:00:00Z" }), trace({ id: "b", started_at: "2026-08-29T11:00:00Z" })];
    sortWorkLedgerRows(original);
    return original[0].id === "a";
  })(),
  "sorting never mutates the input array",
);

// ── workLedgerStatusCounts / filterWorkLedgerRows: the ledger's grouping ──

const mixed: WorkLedgerTraceShape[] = [
  trace({ id: "w1", finished_at: null }),
  trace({ id: "n1", outcome: "needs_input" }),
  trace({ id: "f1", outcome: "failed" }),
  trace({ id: "f2", outcome: "failed" }),
  trace({ id: "d1", outcome: "success" }),
];
const counts = workLedgerStatusCounts(mixed, FIXTURE_NOW);
assert(counts.working === 1 && counts.waiting === 1 && counts.failed === 2 && counts.done === 1, `status counts match the fixture, got ${JSON.stringify(counts)}`);
assert(
  filterWorkLedgerRows(mixed, "failed", FIXTURE_NOW).length === 2,
  "filtering to 'failed' returns exactly the failed rows",
);
assert(
  filterWorkLedgerRows(mixed, "all", FIXTURE_NOW).length === mixed.length,
  "'all' returns every row",
);
const filterAgree =
  filterWorkLedgerRows(mixed, "done", FIXTURE_NOW).length === counts.done &&
  filterWorkLedgerRows(mixed, "working", FIXTURE_NOW).length === counts.working;
assert(filterAgree, "the filter and the counts always agree — one rule, two readers");

// ── planWorkLedgerView: empty vs. could-not-load can never share a state ──

assert(planWorkLedgerView({ loading: true, error: null, rows: [] }).kind === "loading", "loading wins first");
assert(
  planWorkLedgerView({ loading: true, error: "boom", rows: [trace()] }).kind === "loading",
  "still loading wins even if a stale error or stale rows are hanging around",
);
assert(
  planWorkLedgerView({ loading: false, error: "Could not load", rows: [] }).kind === "error",
  "a fetch failure reads as error, not as empty",
);
assert(
  (() => {
    const state = planWorkLedgerView({ loading: false, error: "Could not load", rows: [] });
    return state.kind === "error" && state.message === "Could not load";
  })(),
  "the error state carries the real message, not a generic one",
);
assert(
  planWorkLedgerView({ loading: false, error: null, rows: [] }).kind === "empty",
  "no error and no rows is genuinely empty",
);
assert(
  planWorkLedgerView({ loading: false, error: "boom", rows: [trace()] }).kind === "error",
  "an error present alongside rows still reads as error — never silently swallowed because some rows came back",
);
assert(
  (() => {
    const state = planWorkLedgerView({ loading: false, error: null, rows: [trace({ id: "a" }), trace({ id: "b" })] });
    return state.kind === "rows" && state.rows.length === 2;
  })(),
  "rows present, no error, not loading -> the real rows",
);

// ── Ask AI is not an agent, and this is an agent ledger ───────────────────
// Founder, 2026-08-30, on the shipped page: "what is this ask ai doing
// here?" Every row on his first real load was the assistant. CLAUDE.md:
// Ask AI and agents are separate systems that may never be mixed.

const MIXED: WorkLedgerTraceShape[] = [
  trace({ id: "agent-1", root_agent_id: "specialist:inst_a" }),
  trace({ id: "assistant-sage", root_agent_id: "sage" }),
  trace({ id: "assistant-main", root_agent_id: "sage_main_agent" }),
  trace({ id: "agent-2", root_agent_id: "specialist:inst_b" }),
  trace({ id: "assistant-blank", root_agent_id: "" }),
];
const onlyAgents = excludeAssistantRows(MIXED);
assert(onlyAgents.length === 2, "the assistant is excluded from an AGENT ledger");
assert(
  onlyAgents.every((r) => String(r.root_agent_id).startsWith("specialist:")),
  "every surviving row is a real specialist agent",
);
assert(
  !onlyAgents.some((r) => String(r.id || "").startsWith("assistant")),
  "no assistant row survives under ANY of its stored ids -- sage, sage_main_agent, or blank",
);
// The converse, so the rule cannot pass by emptying the ledger:
assert(
  excludeAssistantRows([trace({ id: "x", root_agent_id: "specialist:inst_z" })]).length === 1,
  "a genuine agent row is KEPT -- the filter narrows, it does not delete the ledger",
);

// ── "Working" is a claim, and an old unfinished trace cannot support it ───
// Production had 250 of 250 web traces with no finish, the oldest two
// months old, every one rendering as "Working". "Still running" and
// "nobody closed this out" are two different facts; they may not share one
// signal.

const NOW = Date.parse("2026-08-30T12:00:00Z");
const freshRunning = trace({
  id: "fresh",
  started_at: new Date(NOW - 60_000).toISOString(),
  finished_at: null,
  outcome: null,
});
const staleRunning = trace({
  id: "stale",
  started_at: new Date(NOW - WORK_LEDGER_STALE_AFTER_MS - 60_000).toISOString(),
  finished_at: null,
  outcome: null,
});
assert(workLedgerStatus(freshRunning, NOW) === "working", "a just-started unfinished run really is working");
assert(
  workLedgerStatus(staleRunning, NOW) === "unknown",
  "an unfinished run past the staleness line reports NO RESULT RECORDED, never 'working'",
);
assert(
  workLedgerStatus(staleRunning, NOW) !== "failed" && workLedgerStatus(staleRunning, NOW) !== "done",
  "and it does not invent an outcome it never observed either",
);
// An unreadable start date must not be declared lost on the strength of a
// date this code could not parse.
assert(
  workLedgerStatus(trace({ id: "nodate", started_at: "not-a-date", finished_at: null, outcome: null }), NOW) === "working",
  "an unparseable start keeps the optimistic reading rather than guessing",
);
// Counts and the list must share ONE clock, or a chip can disagree with
// the rows beneath it.
const mixedAges = [freshRunning, staleRunning];
const c = workLedgerStatusCounts(mixedAges, NOW);
assert(
  c.working === 1 && c.unknown === 1,
  "counts age rows against the SAME clock the list uses",
);
assert(
  filterWorkLedgerRows(mixedAges, "unknown", NOW).length === 1 &&
    filterWorkLedgerRows(mixedAges, "working", NOW).length === 1,
  "filtering agrees with the counts for both states",
);


console.log(`\n${passed} passed, ${failed} failed`);
if (failed > 0) {
  process.exit(1);
}
