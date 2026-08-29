/**
 * THE WORKSPACE WORK LEDGER — "what have all my agents actually done,"
 * across every agent, every surface, every project, in one place.
 *
 * THE GAP THIS FILLS. agent_trace_service.py writes a durable `agent_traces`
 * row on every real turn (server_modules/agent_turn.py,
 * agent_turn_runtime_service.py, run_service.py, and all four hardware
 * runtime adapters — verified call sites, not assumed). GET /api/agent-traces
 * (server_modules/routes_agent_traces.py:135, list_agent_traces) is mounted
 * and workspace-scoped. frontend/lib/workspace/workstation-client.ts even
 * types the exact response shape (WorkstationAgentTraceRecord) and builds the
 * URL (`paths.agentTraces`). NONE of that had a UI caller — the one place a
 * trace becomes visible today is tabs/WorkTab.tsx, and only per agent, per
 * thread, one at a time. This module is the missing workspace-wide reader:
 * given the raw rows GET /api/agent-traces already returns, decide what a
 * ledger row says and where it sorts, so the component (WorkLedgerView.tsx)
 * only has to render what this module decides.
 *
 * NOT A SECOND CLIENT. The obvious move — instantiate
 * workstation-client.ts's createWorkstationClient() and call its listTraces()
 * — is exactly what McpServersSection.tsx's own header comment documents
 * NOT doing, for a real, checked reason: createWorkstationClient() is never
 * instantiated anywhere live in the app (no WorkstationKernelProvider is
 * mounted under frontend/app/), so every real page in this directory hits
 * the same REST paths via plain fetch instead. This module follows that
 * established convention rather than being the first page to stand up an
 * unused, orphaned client wrapper — WorkLedgerView.tsx calls
 * `fleetAuthorizedFetch("/api/agent-traces?...")` directly, the exact path
 * and query params workstation-client.ts's own `paths.agentTraces` already
 * documents (workspace_id, thread_id, run_id, surface, outcome,
 * root_agent_id, limit).
 *
 * THE REAL ROW SHAPE, read from the producer, not guessed. Traced end to
 * end: routes_agent_traces.py's list_agent_traces returns
 * `control_plane_repository.list_agent_traces(...)` items verbatim, each
 * built by `_row_to_agent_trace` (control_plane_repository.py:3348) —
 * id, tenant_id, workspace_id, thread_id, run_id, root_agent_id, surface,
 * runtime_target, provider, model, started_at, finished_at, outcome,
 * final_message_id. `create_agent_trace` inserts a row with
 * `finished_at = NULL, outcome = NULL` and `finish_trace` fills both in
 * later — so a NULL `finished_at` is exactly "this run is still going",
 * never "unknown", and `WorkLedgerTraceShape` below is that structural
 * subset (never `WorkstationAgentTraceRecord` itself, so this module can
 * stay React/CSS free and a plain fixture satisfies it without importing
 * the client's ~3000-line surface).
 *
 * OUTCOME → DISPLAYED STATE, ported from WorkTab.tsx's own
 * classifyThreadStatus (tabs/WorkTab.tsx:659) rather than invented fresh —
 * that function is the one place this codebase already solved "what does a
 * trace's outcome mean," including the fix for the exact bug CLAUDE.md
 * warns about generally ("two different facts sharing one signal"): a run
 * that produced nothing used to be recorded as `partial`, read as `done`,
 * and rendered identically to a real success. agent_trace_service.py's
 * TRACE_OUTCOME_* vocabulary (success / partial / failed / needs_input) is
 * the fix, and this module reads the same four values the same way:
 *
 *   finished_at is null        → "working"  (still running; not "unknown")
 *   outcome === "needs_input"  → "waiting"
 *   outcome === "failed"       → "failed"
 *   anything else (success,
 *     partial, an outcome this
 *     vocabulary doesn't know)  → "done"
 *
 * ONE DELIBERATE DIVERGENCE from classifyThreadStatus: that function has a
 * second "waiting" path — an `approval.requested` event with no matching
 * `approval.resolved` — because WorkTab already has the trace's full event
 * log in hand (it fetches GET /api/agent-traces/{id}, events included). This
 * module works off list rows only (GET /api/agent-traces never returns
 * events — see routes_agent_traces.py:158, `items` is bare trace rows), so
 * that branch has no data to run on here. A trace mid-approval with an
 * `outcome` still NULL therefore reads as "working" at the ledger level and
 * only resolves to "waiting" once the backend outcome catches up — an
 * honest gap (real signal this module doesn't have), not a guess.
 *
 * `partial` folding into "done" is not new leniency introduced here — it is
 * classifyThreadStatus's own existing, reviewed behavior (only `needs_input`
 * and `failed` get their own bucket there; everything else, `partial`
 * included, already reads "done"). Porting it keeps this ledger and the
 * per-agent Work tab telling the same story about the same trace.
 *
 * root_agent_id IS NOT AN AGENT ID — it is a routing tag, and the three
 * literal shapes it actually takes are all read off the backend, not
 * guessed: `f"specialist:{install_id}"` (agent_turn.py:243,
 * run_service.py:626 — a real agent's turn), bare `"sage"`
 * (agent_turn.py:244 / run_service.py:627's own fallback when no install id
 * resolved), and `SAGE_MAIN_AGENT_ID` = `"sage_main_agent"`
 * (agent_turn_runtime_service.py:139,4397 — the dedicated Ask AI chat
 * surface). Hardcoding both assistant literals would silently miss a third
 * one the backend adds later; parseWorkLedgerAgentRef instead treats
 * anything WITHOUT the "specialist:" prefix as the assistant, which is the
 * only structural fact common to both real literals and any future one.
 *
 * WHERE A ROW LINKS TO. There is no page that opens one trace by id — only
 * the API (GET /api/agent-traces/{id}) — the same fact inbox-needs-you.ts
 * already documents and solves the same way: the destination is the
 * agent's own Work tab, where its traces are actually read
 * (`/w/{workspaceId}/agents/{installId}/work` — agent-detail-tabs.ts's
 * routable, if unlabeled, `work` alias for the same view `chat` renders).
 * For an assistant-originated row there is no destination AT ALL: the Ask
 * AI console (SageLauncher.tsx) is a docked panel driven by local component
 * state (`open`/`onOpen`/`onClose`), never a route — so
 * workLedgerDetailHref returns null for it, and the row renders as plain,
 * unclickable text rather than a link to nowhere (CLAUDE.md: "no dead
 * controls").
 *
 * Pure, no React, no CSS import — same discipline as agent-card-face.ts /
 * inbox-needs-you.ts, so work-ledger.test.ts drives the real rule the
 * component renders against instead of a re-typed copy of it.
 *
 * Run: npx tsx lib/workspace/fleet/work-ledger.test.ts
 */

/** The structural subset of a GET /api/agent-traces row this module reads —
 *  deliberately narrower than WorkstationAgentTraceRecord (which carries
 *  tenant_id, thread_id, run_id, runtime_target, provider, model,
 *  final_message_id too) so a plain test fixture satisfies it without
 *  importing workstation-client.ts. A real trace row is a superset and
 *  satisfies this for free. */
export type WorkLedgerTraceShape = {
  id?: string | null;
  root_agent_id?: string | null;
  surface?: string | null;
  started_at?: string | null;
  finished_at?: string | null;
  outcome?: string | null;
};

// ── Outcome → displayed state ───────────────────────────────────────────

export type WorkLedgerStatus = "working" | "waiting" | "failed" | "done";

export const WORK_LEDGER_STATUS_LABEL: Record<WorkLedgerStatus, string> = {
  working: "Working",
  waiting: "Waiting on input",
  failed: "Failed",
  done: "Done",
};

/** The `--task-*` custom properties (lib/ui/theme-tokens.css) this status
 *  borrows for its dot — same real-world meaning as the task board's own
 *  in_progress/awaiting_input/blocked/done, reused rather than a fifth
 *  hand-picked color ramp invented for this one surface. Never `--accent`:
 *  accent-restraint.test.ts reserves violet for the single primary filled
 *  button, and none of these are that. */
export const WORK_LEDGER_STATUS_COLOR_VAR: Record<WorkLedgerStatus, string> = {
  working: "--task-progress",
  waiting: "--task-input",
  failed: "--task-blocked",
  done: "--task-done",
};

/** The one place "what does this trace's outcome mean" is decided. See this
 *  file's own header for why these four branches, in this order, and what
 *  they port from WorkTab.tsx's classifyThreadStatus. */
export function workLedgerStatus(trace: WorkLedgerTraceShape): WorkLedgerStatus {
  if (!trace.finished_at) return "working";
  const outcome = String(trace.outcome || "").trim().toLowerCase();
  if (outcome === "needs_input") return "waiting";
  if (outcome === "failed") return "failed";
  return "done";
}

// ── root_agent_id parsing ───────────────────────────────────────────────

export type WorkLedgerAgentRef =
  | { kind: "specialist"; installId: string }
  | { kind: "assistant" };

const SPECIALIST_PREFIX = "specialist:";

/** Structural parse of `root_agent_id` — see this file's header for the
 *  three literal shapes actually observed on the backend. Anything that
 *  does not carry the "specialist:" prefix is the assistant; a blank or
 *  malformed "specialist:" (empty install id) also falls back to
 *  "assistant" rather than a bare, unresolvable installId that would build
 *  a link to nowhere. */
export function parseWorkLedgerAgentRef(rootAgentId: string | null | undefined): WorkLedgerAgentRef {
  const raw = String(rootAgentId || "").trim();
  if (raw.startsWith(SPECIALIST_PREFIX)) {
    const installId = raw.slice(SPECIALIST_PREFIX.length).trim();
    if (installId) return { kind: "specialist", installId };
  }
  return { kind: "assistant" };
}

/** The agent's own Work tab — the one real destination a trace row can
 *  link to (see this file's header). Null for an assistant-originated row:
 *  the Ask AI console has no route to link to, so the row must render as
 *  plain text instead of a dead control. */
export function workLedgerDetailHref(input: {
  workspaceId: string;
  rootAgentId: string | null | undefined;
}): string | null {
  const ref = parseWorkLedgerAgentRef(input.rootAgentId);
  if (ref.kind !== "specialist") return null;
  return `/w/${encodeURIComponent(input.workspaceId)}/agents/${encodeURIComponent(ref.installId)}/work`;
}

// ── Surface labels ──────────────────────────────────────────────────────

/** Every literal `surface` value a real trace carries, read off the actual
 *  start_trace call sites (agent_turn.py `_trace_surface`, run_service.py
 *  `_trace_surface_for_channel` — both cap to web/mobile/desktop/api,
 *  otherwise "channel"; agent_turn_runtime_service.py's dedicated Ask AI
 *  path passes the literal "sage"). "sage" prints as "Ask AI console" —
 *  never the bare retired name (CLAUDE.md: "Never say 'AI'... The old name
 *  is dead") — matching WORKSPACE_ASSISTANT_LABEL's own translation
 *  elsewhere in this directory. An unrecognized future surface still prints
 *  something real (its own capitalized word) rather than "Unknown", which
 *  would read as a bug in this module rather than a new, honestly-unlabeled
 *  value. */
const SURFACE_LABEL: Record<string, string> = {
  web: "Web",
  mobile: "Mobile",
  desktop: "Desktop",
  api: "API",
  channel: "Channel",
  sage: "Ask AI console",
};

export function workLedgerSurfaceLabel(surface: string | null | undefined): string {
  const key = String(surface || "").trim().toLowerCase();
  if (!key) return "Unknown";
  return SURFACE_LABEL[key] || key.replace(/[-_]/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
}

// ── Ordering ─────────────────────────────────────────────────────────────

function startedAtMillis(trace: WorkLedgerTraceShape): number {
  const t = trace.started_at ? Date.parse(trace.started_at) : NaN;
  return Number.isFinite(t) ? t : 0;
}

/** Canonical order for the ledger: most recently STARTED first. Defined
 *  here rather than trusted from the caller — the backend's own query
 *  already orders this way (control_plane_repository.list_agent_traces:
 *  `ORDER BY started_at DESC, id DESC`), but a page that merges more than
 *  one fetch (pagination, a filter change landing while an older request is
 *  still in flight) must not silently inherit whatever order the last
 *  response happened to arrive in — the expected order and the actual order
 *  should never come from the same place. Ties (identical started_at, the
 *  common case for a burst of channel turns) break on `id` descending, for
 *  a stable, reproducible order a test can assert on. */
export function sortWorkLedgerRows<T extends WorkLedgerTraceShape>(rows: readonly T[]): T[] {
  return [...rows].sort((a, b) => {
    const byTime = startedAtMillis(b) - startedAtMillis(a);
    if (byTime !== 0) return byTime;
    return String(b.id || "").localeCompare(String(a.id || ""));
  });
}

// ── Status counts / filtering (the ledger's own grouping) ──────────────────

export type WorkLedgerStatusCounts = Record<WorkLedgerStatus, number>;

/** How many of the current rows are in each displayed state — the number
 *  both the filter chips and their own selected-count badge must agree on,
 *  so they can never be computed from two independently-drifting
 *  expressions (the same discipline inboxNeedsYouCount already applies one
 *  surface over). */
export function workLedgerStatusCounts(rows: readonly WorkLedgerTraceShape[]): WorkLedgerStatusCounts {
  const counts: WorkLedgerStatusCounts = { working: 0, waiting: 0, failed: 0, done: 0 };
  for (const row of rows) counts[workLedgerStatus(row)] += 1;
  return counts;
}

export type WorkLedgerStatusFilter = WorkLedgerStatus | "all";

/** Rows are GROUPED by their displayed status via this one filter, never a
 *  second re-derivation of workLedgerStatus at the call site — the same
 *  "one rule, every reader" shape my-work.ts's isOpenMyWork requires of its
 *  own callers. */
export function filterWorkLedgerRows<T extends WorkLedgerTraceShape>(
  rows: readonly T[],
  status: WorkLedgerStatusFilter,
): T[] {
  if (status === "all") return rows.slice();
  return rows.filter((row) => workLedgerStatus(row) === status);
}

// ── Empty vs. could-not-load (the view state a component renders) ─────────

export type WorkLedgerViewState<T> =
  | { kind: "loading" }
  | { kind: "error"; message: string }
  | { kind: "empty" }
  | { kind: "rows"; rows: T[] };

/** The one place "loading vs. failed vs. genuinely empty vs. has rows" is
 *  decided — CLAUDE.md's outcome-honesty law made structural: "empty" and
 *  "could not load" are different facts (outcome-honesty-drift.test.ts's
 *  whole reason for existing) and must never share one rendered state. A
 *  component calls this with what it actually observed (its own `loading`
 *  flag, its own caught fetch error, the rows it got back) rather than
 *  branching `if (error) ... else if (rows.length === 0) ...` inline itself
 *  — inlining that is exactly the shape ConversationsView.tsx and
 *  InboxPage's own `totallyFailed`/`genuinelyEmpty` split already use, made
 *  testable here instead of re-typed at every call site. `error` wins over
 *  an empty `rows` array on purpose: a caught fetch failure and "the server
 *  said zero rows" must never collapse into the same branch, even though
 *  both leave `rows` empty. */
export function planWorkLedgerView<T>(input: {
  loading: boolean;
  error: string | null;
  rows: readonly T[];
}): WorkLedgerViewState<T> {
  if (input.loading) return { kind: "loading" };
  if (input.error) return { kind: "error", message: input.error };
  if (input.rows.length === 0) return { kind: "empty" };
  return { kind: "rows", rows: input.rows.slice() };
}
