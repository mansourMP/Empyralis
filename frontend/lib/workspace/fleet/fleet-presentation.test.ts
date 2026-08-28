/**
 * fleet-presentation unit tests — formatDueDate (MAN-311 defect #2: a task's
 * due date rendered a day early for anyone west of UTC).
 *
 * due_at is a date-only field: `_coerce_due_at`
 * (server_modules/project_tasks_service.py) stamps a bare "YYYY-MM-DD" from
 * <input type=date> as midnight UTC — it has no meaningful time-of-day.
 * Before this fix, TaskDetailView/TasksList/TasksBoard formatted that
 * instant through formatDate/formatDateTime with no `timeZone` override
 * (correct for every REAL instant elsewhere on this surface — created_at,
 * updated_at, comment timestamps), so a viewer west of UTC saw the
 * browser's Intl machinery reinterpret "2026-08-15T00:00:00Z" in local
 * time and roll the calendar day back to Aug 14 — while the edit input,
 * which reads the date via a raw ISO slice, still showed Aug 15. The two
 * views of the same field visibly disagreed. formatDueDate pins
 * `timeZone: "UTC"` so the SAME calendar date the value was stamped with is
 * what renders, in every viewer timezone.
 *
 * This suite forces process.env.TZ to a real west-of-UTC zone before
 * calling either formatter, because that is the exact axis the bug lived
 * on — a UTC-only run would never see it roll back a day. Verified
 * directly (see /tmp scratch run during development) that Node's
 * Date#toLocaleDateString respects a process.env.TZ reassignment made
 * before the call, even mid-process.
 *
 * Run: npx tsx lib/workspace/fleet/fleet-presentation.test.ts
 */

// Pacific/Honolulu is UTC-10 with no DST (deterministic year-round, unlike
// America/Los_Angeles which would need a fixed calendar date chosen around
// its DST transitions) — the westmost real IANA zone, so this is the
// sharpest available proof that a west-of-UTC viewer is broken.
process.env.TZ = "Pacific/Honolulu";

import { agentDisplayLabel, findSageAgent, formatDate, formatDueDate } from './fleet-presentation';

let passed = 0;
let failed = 0;

function assertEqual<T>(actual: T, expected: T, label: string): void {
  if (actual === expected) {
    passed++;
  } else {
    failed++;
    console.error(`FAIL: ${label} — expected ${JSON.stringify(expected)}, got ${JSON.stringify(actual)}`);
  }
}

// A date-only field stamped midnight UTC, exactly as _coerce_due_at
// produces for a bare "2026-08-15" typed into <input type=date>.
const DUE_AT_MIDNIGHT_UTC = "2026-08-15T00:00:00Z";
const OPTS: Intl.DateTimeFormatOptions = { month: "short", day: "numeric", year: "numeric" };

// --- The fix: formatDueDate always agrees with the calendar date the value
//     was stamped with, regardless of the viewer's local timezone. ---

assertEqual(
  formatDueDate(DUE_AT_MIDNIGHT_UTC, OPTS),
  "Aug 15, 2026",
  "formatDueDate renders Aug 15 for a viewer in Pacific/Honolulu (UTC-10) — agrees with the edit input's raw ISO slice",
);

// --- The bug, demonstrated directly. formatDate is CORRECT for every real
//     instant on this surface (created_at, updated_at, ...) — this is not
//     a formatDate defect, it is proof that due_at must never be routed
//     through it, which is exactly the mistake TaskDetailView/TasksList/
//     TasksBoard made before commit f09d84da5 (dueLabel used
//     d.toLocaleDateString(undefined, ...) with no timeZone override at
//     all, an even less controlled version of the same bug). ---

assertEqual(
  formatDate(DUE_AT_MIDNIGHT_UTC, OPTS),
  "Aug 14, 2026",
  "formatDate (no timeZone override) rolls the SAME value back to Aug 14 in Pacific/Honolulu — the exact bug formatDueDate exists to avoid",
);

// --- Sanity: a viewer AT UTC never saw this bug (the day only rolls back
//     west of it), so the fix must be a no-op there. ---

process.env.TZ = "UTC";
assertEqual(
  formatDueDate(DUE_AT_MIDNIGHT_UTC, OPTS),
  "Aug 15, 2026",
  "formatDueDate renders Aug 15 for a UTC viewer too",
);
assertEqual(
  formatDate(DUE_AT_MIDNIGHT_UTC, OPTS),
  "Aug 15, 2026",
  "formatDate also renders Aug 15 at UTC — the bug only bites west of it, confirming the timezone axis is what matters",
);

// --- Sanity: a viewer EAST of UTC (e.g. Asia/Shanghai, UTC+8 — the
//     founder's own timezone) never rolls the day FORWARD either; only the
//     west-of-UTC direction is at risk, and both formatters must agree
//     here regardless. ---

process.env.TZ = "Asia/Shanghai";
assertEqual(
  formatDueDate(DUE_AT_MIDNIGHT_UTC, OPTS),
  "Aug 15, 2026",
  "formatDueDate renders Aug 15 for a viewer in Asia/Shanghai (UTC+8)",
);
assertEqual(
  formatDate(DUE_AT_MIDNIGHT_UTC, OPTS),
  "Aug 15, 2026",
  "formatDate also renders Aug 15 at UTC+8 — midnight UTC is still the same calendar day east of it",
);

// --- The workspace assistant is found STRUCTURALLY, and never prints its
//     stored label (2026-08-28). ---
//
// Both rules exist because of the same fact: the master install's stored
// label on every workspace created before this date is literally "Sage" —
// the persona name the founder removed from the product. That column is
// DATA (rewriting it across live rows is a migration), so the name has to
// stop mattering, in two separate ways.
//
// The rows below are the REAL shape, not an invented one: measured live and
// recorded on FleetAgent.agent_kind's own declaration, the workspace
// operator comes back as `role: "specialist"`, `agent_kind: "master"`. So
// neither role clause in findSageAgent fires on production data, and before
// this change the ONLY thing identifying the assistant was its label
// containing "sage" — which made renaming it silently delete the Ask AI
// console, un-exclude it from every agent list, and break
// agent-count-shape's contract that it never counts.

const REAL_MASTER = { agent_id: "ainstall_ws_sage", label: "Sage", role: "specialist", agent_kind: "master" } as any;
const RENAMED_MASTER = { agent_id: "ainstall_ws_master", label: "Assistant", role: "specialist", agent_kind: "master" } as any;
const SPECIALIST = { agent_id: "ainstall_x", label: "Billing Watcher", role: "specialist", agent_kind: "specialist" } as any;

assertEqual(
  findSageAgent([SPECIALIST, REAL_MASTER])?.agent_id,
  "ainstall_ws_sage",
  "findSageAgent still resolves today's production row (label 'Sage', agent_kind 'master')",
);
assertEqual(
  findSageAgent([SPECIALIST, RENAMED_MASTER])?.agent_id,
  "ainstall_ws_master",
  "findSageAgent resolves a master whose label no longer contains 'sage' — the whole point",
);
assertEqual(
  findSageAgent([SPECIALIST]),
  null,
  "a fleet with no master resolves to null — the exemption is not too wide",
);

assertEqual(
  agentDisplayLabel(REAL_MASTER),
  "Ask AI",
  "the assistant prints as Ask AI even though its stored label says otherwise",
);
assertEqual(
  agentDisplayLabel(RENAMED_MASTER),
  "Ask AI",
  "and prints the same once the stored label is renamed too",
);
assertEqual(
  agentDisplayLabel(SPECIALIST),
  "Billing Watcher",
  "an ordinary specialist keeps its own name — a test that only checked the master would pass if this returned Ask AI for everything",
);
assertEqual(
  agentDisplayLabel(null),
  "Unnamed agent",
  "a missing agent degrades to the same placeholder the render sites used before",
);

// --- Summary ---

console.log(`\n${passed} passed, ${failed} failed`);
if (failed > 0) {
  process.exit(1);
}
