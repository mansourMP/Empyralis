/**
 * The task↔document loop's frontend half — buildActivityRows/toolIconFor/
 * fleetRecordHref/fleetRecordLabel are exercised here directly (exported
 * from WorkTab.tsx for exactly this reason — see the export-site comment
 * there), against TraceEvent fixtures shaped like the REAL wire event
 * server_modules/claude_agent_sdk_bridge.py's translate_sdk_message now
 * emits for document__write/edit and every project_task__* tool (see
 * server_modules/tests/test_claude_agent_sdk_bridge.py's
 * TranslateUserMessageLinkedRecordTests for the backend half of the same
 * shape, and test_direct_tool_execution_service.py for the pure
 * build_direct_tool_trace_metadata unit tests) — not a re-typed guess at
 * either.
 *
 * Founder, 2026-08-29: "the task closing and the document changing should
 * surface as one narrated event, not two things you separately notice."
 * The row this file proves is exactly that: ONE row (tool.started +
 * tool.result folded together, same as every other tool call this
 * timeline already renders), carrying a friendly label ("Wrote a
 * document"), the real record's title, and a real, clickable in-app link
 * — or, when the link cannot be trusted, the title alone as plain text
 * rather than a link to nowhere (CLAUDE.md: "a link that would 404 must
 * not render as a link").
 *
 * Run: npx tsx lib/workspace/fleet/tabs/work-tab-activity.test.ts
 */
import { allAgentTraceRefs, buildActivityRows, fleetRecordHref, fleetRecordLabel, toolIconFor, type Thread, type TraceEvent, type Turn } from "./WorkTab";

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

const WORKSPACE_ID = "ws_1";
let seq = 0;

function traceEvent(over: { event_type: string; tool_call_id?: string; data?: Record<string, unknown> }): TraceEvent {
  seq += 1;
  return {
    id: `evt_${seq}`,
    seq,
    ts: "2026-08-29T10:00:00Z",
    event_type: over.event_type,
    tool_call_id: over.tool_call_id,
    data: over.data || {},
  };
}

// ── toolIconFor: document__*/project_task__* labels ─────────────────────
// Before this, both tool names fell through to `Used ${toolName}` — a
// literal, unclickable "Used document__write" row (see the founder brief's
// own trace on this — no case existed for either connector at all).

assert(toolIconFor("document__write").label === "Wrote a document", "document__write reads as 'Wrote a document'");
assert(toolIconFor("document__edit").label === "Edited a document", "document__edit reads as 'Edited a document'");
assert(toolIconFor("document__read").label === "Read a document", "document__read reads as 'Read a document'");
assert(toolIconFor("document__unknown").label === "Used a document tool", "an unrecognized document__ action never falls back to the raw tool name");
assert(toolIconFor("project_task__create").label === "Created a task", "project_task__create reads as 'Created a task'");
assert(toolIconFor("project_task__update").label === "Updated a task", "project_task__update reads as 'Updated a task'");
assert(toolIconFor("project_task__assign").label === "Assigned a task", "project_task__assign reads as 'Assigned a task'");
assert(toolIconFor("project_task__unknown").label === "Used a task tool", "an unrecognized project_task__ action never falls back to the raw tool name");

// ── fleetRecordHref: the SAME relative route shape deep_link_service.py's
// TASK_PATH_TEMPLATE/DOCUMENT_PATH_TEMPLATE and the inbox page's own
// taskHrefFor already build — never a third URL shape for the same route.

assert(
  fleetRecordHref(WORKSPACE_ID, { kind: "task", id: "task_1", project_id: "proj_1" }) === "/w/ws_1/projects/proj_1/tasks/task_1",
  "a task linked_record resolves to the real task route",
);
assert(
  fleetRecordHref(WORKSPACE_ID, { kind: "document", id: "doc_1", project_id: "proj_1" }) === "/w/ws_1/projects/proj_1/documents/doc_1",
  "a document linked_record resolves to the real document route",
);
assert(fleetRecordHref("", { kind: "task", id: "task_1", project_id: "proj_1" }) === null, "no workspaceId means no href, never a best-effort guess");
assert(fleetRecordHref(WORKSPACE_ID, { kind: "task", id: "", project_id: "proj_1" }) === null, "a missing id means no href");
assert(fleetRecordHref(WORKSPACE_ID, { kind: "task", id: "task_1", project_id: "" }) === null, "a missing project_id means no href");
assert(fleetRecordHref(WORKSPACE_ID, null) === null, "a non-object record means no href");
assert(fleetRecordLabel({ title: "Q3 Ledger" }) === "Q3 Ledger", "fleetRecordLabel reads the record's own title");
assert(fleetRecordLabel(null) === "", "fleetRecordLabel never throws on a non-object record");

// ── buildActivityRows: the real tool.started + tool.result event pair ───

function documentWriteEvents(): TraceEvent[] {
  return [
    traceEvent({
      event_type: "tool.started",
      tool_call_id: "call_1",
      data: { tool_name: "document__write", args_preview: { title: "Q3 Ledger" } },
    }),
    traceEvent({
      event_type: "tool.result",
      tool_call_id: "call_1",
      data: {
        status: "ok",
        summary: 'Wrote "Q3 Ledger"',
        execution_environment: "cloud_provider",
        linked_record: { kind: "document", id: "doc_abc123", project_id: "proj_1", title: "Q3 Ledger" },
      },
    }),
  ];
}

{
  const rows = buildActivityRows(documentWriteEvents(), WORKSPACE_ID);
  assert(rows.length === 1, "tool.started + tool.result fold into ONE row, not two — the same 'one narrated event' every other tool call already gets");
  const row = rows[0];
  assert(row.text === "Wrote a document", "the row's own label is the friendly document__write label");
  assert(row.linkLabel === "Q3 Ledger", "the row carries the document's real title as its link label");
  assert(row.linkHref === "/w/ws_1/projects/proj_1/documents/doc_abc123", "the row's link points at the document's real in-app route");
  assert(row.detail === undefined, "the generic JSON summary is not ALSO shown once a linked_record already names the same fact");
}

function taskUpdateEvents(): TraceEvent[] {
  return [
    traceEvent({ event_type: "tool.started", tool_call_id: "call_2", data: { tool_name: "project_task__update" } }),
    traceEvent({
      event_type: "tool.result",
      tool_call_id: "call_2",
      data: {
        status: "ok",
        summary: 'Updated "Reconcile August" → in_review',
        execution_environment: "cloud_provider",
        linked_record: { kind: "task", id: "task_1", project_id: "proj_1", title: "Reconcile August" },
      },
    }),
  ];
}

{
  const rows = buildActivityRows(taskUpdateEvents(), WORKSPACE_ID);
  assert(rows.length === 1, "task update's started + result also fold into one row");
  const row = rows[0];
  assert(row.text === "Updated a task", "the row's own label is the friendly project_task__update label");
  assert(row.linkLabel === "Reconcile August", "the row carries the task's real title as its link label");
  assert(row.linkHref === "/w/ws_1/projects/proj_1/tasks/task_1", "the row's link points at the task's real in-app route");
}

{
  // A failed tool.result never renders a link, even defensively — the
  // backend already refuses to attach linked_record to a failed result
  // (see claude_agent_sdk_bridge.py's `if not is_error and ...` guard), but
  // CLAUDE.md guards display and persistence separately, so this side
  // checks `failed` again rather than trusting the wire.
  const events: TraceEvent[] = [
    traceEvent({ event_type: "tool.started", tool_call_id: "call_3", data: { tool_name: "document__write" } }),
    traceEvent({
      event_type: "tool.result",
      tool_call_id: "call_3",
      data: {
        status: "failed",
        summary: "Document title is required.",
        linked_record: { kind: "document", id: "doc_x", project_id: "proj_1", title: "should never render" },
      },
    }),
  ];
  const rows = buildActivityRows(events, WORKSPACE_ID);
  assert(rows[0].tone === "danger", "the row still reads as failed");
  assert(rows[0].linkHref === undefined && rows[0].linkLabel === undefined, "a failed result's linked_record is never trusted, even if the wire somehow carried one");
}

{
  // subLineFor's step-count-only call site passes no workspaceId at all —
  // buildActivityRows must not throw, and must render no link rather than
  // a broken one built from an absent workspace segment.
  const rows = buildActivityRows(documentWriteEvents());
  assert(rows.length === 1, "buildActivityRows tolerates a missing workspaceId");
  assert(rows[0].linkHref === undefined, "no workspaceId means no href, never a guessed one");
  assert(rows[0].linkLabel === "Q3 Ledger", "the record's title still names what happened even with no link");
}

{
  // document__list / project_task__list_labels return a collection, never a
  // single record — the backend already omits linked_record for these (see
  // test_direct_tool_execution_service.py), and the row must fall back to
  // the plain summary text rather than showing a phantom link.
  const events: TraceEvent[] = [
    traceEvent({ event_type: "tool.started", tool_call_id: "call_4", data: { tool_name: "document__list" } }),
    traceEvent({ event_type: "tool.result", tool_call_id: "call_4", data: { status: "ok", summary: "2 documents." } }),
  ];
  const rows = buildActivityRows(events, WORKSPACE_ID);
  assert(rows[0].text === "Listed documents", "document__list gets its own friendly label");
  assert(rows[0].linkLabel === undefined, "no linked_record on the wire means no link on the row");
  assert(rows[0].detail === "2 documents.", "with no linked_record, the plain summary is shown as detail same as any other tool");
}

// ── allAgentTraceRefs: bug B's root cause, 2026-09-01 ────────────────────
//
// Founder, on a 2-turn Telegram conversation: "if I reload this page, it
// shows four messages... then it loads again by itself and only shows
// two." This file's own top comment already says why: a real agent_turn()
// call opens its OWN agent_traces row every time, so a 2-turn conversation
// is two traces, never one. The pre-fix code (`latestTraceRef`, still used
// for the sidebar's preview line) scanned turns back-to-front and returned
// on the FIRST agent-side turn carrying a trace_id — i.e. only the latest
// exchange's trace was ever discoverable from a thread at all, so the
// detail pane could only ever render one trace no matter which turn it
// asked about. allAgentTraceRefs is what the fix reads instead: every
// trace a turn points at, not just the last one.

function agentTurn(traceId: string | undefined, createdAt: string): Turn {
  return { role: "assistant", content: "a reply", created_at: createdAt, metadata: traceId ? { trace_id: traceId } : {} };
}
function customerTurn(createdAt: string): Turn {
  return { role: "user", content: "a message", created_at: createdAt };
}

{
  // The exact shape of the founder's report: two full exchanges, each its
  // own trace.
  const thread: Thread = {
    id: "th_1",
    turns: [
      customerTurn("2026-08-31T13:14:00Z"),
      agentTurn("trace_a", "2026-08-31T13:15:33Z"),
      customerTurn("2026-08-31T13:16:00Z"),
      agentTurn("trace_b", "2026-08-31T13:17:39Z"),
    ],
  };
  const refs = allAgentTraceRefs(thread);
  assert(refs.length === 2, "a 2-turn conversation carries TWO traces, not one — this is the whole bug");
  assert(refs[0].traceId === "trace_a" && refs[0].assistantIndex === 1, "the FIRST exchange's trace is still discoverable, not just the latest");
  assert(refs[1].traceId === "trace_b" && refs[1].assistantIndex === 3, "the latest exchange's trace is discoverable too — nothing regresses for the single-turn case");
}

{
  // A turn whose assistant reply carries no trace_id at all (predates trace
  // instrumentation, or no control-plane DB) is simply absent from the
  // list — never a fabricated entry with an empty id that would 404 a
  // lookup.
  const thread: Thread = {
    id: "th_2",
    turns: [customerTurn("t0"), agentTurn(undefined, "t1"), customerTurn("t2"), agentTurn("trace_c", "t3")],
  };
  const refs = allAgentTraceRefs(thread);
  assert(refs.length === 1 && refs[0].traceId === "trace_c", "a turn with no trace_id is skipped, not returned as an empty-id ref");
}

{
  // A single-exchange thread — the common case — still resolves to exactly
  // one ref, same as before this fix (no regression for the majority
  // shape: one customer turn, one reply).
  const thread: Thread = { id: "th_3", turns: [customerTurn("t0"), agentTurn("trace_only", "t1")] };
  const refs = allAgentTraceRefs(thread);
  assert(refs.length === 1 && refs[0].traceId === "trace_only", "a single-exchange thread resolves to exactly one trace ref");
}

console.log(`\n${passed} passed, ${failed} failed`);
if (failed > 0) {
  process.exit(1);
}
