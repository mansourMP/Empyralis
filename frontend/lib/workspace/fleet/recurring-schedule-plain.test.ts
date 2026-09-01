/**
 * recurring-schedule-plain.ts — the one seam that turns "every Monday at
 * 9am" into the cron string routes_fleet.py's recurring-schedule route
 * accepts, and back. No cron syntax is asserted against the UI anywhere
 * outside this file — every other surface goes through these functions.
 *
 * Run: npx tsx lib/workspace/fleet/recurring-schedule-plain.test.ts
 */

import {
  buildRecurringCron,
  describeCronPlain,
  describeRecurringSchedule,
  parseRecurringCron,
  type PlainRecurringSchedule,
} from "./recurring-schedule-plain";

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

function eq(actual: unknown, expected: unknown, label: string): void {
  assert(
    JSON.stringify(actual) === JSON.stringify(expected),
    `${label} — expected ${JSON.stringify(expected)}, got ${JSON.stringify(actual)}`,
  );
}

// ── buildRecurringCron: the exact 5-field shape the backend validates ──────
// bounded_scheduler_service.parse_cron_expression requires exactly 5
// space-separated fields (minute hour day month weekday); a wrong field
// count is a real, loud server-side rejection, so the shape here is
// load-bearing, not decoration.
eq(buildRecurringCron({ frequency: "daily", weekday: 0, hour: 9, minute: 0 }), "0 9 * * *", "daily at 9:00");
eq(buildRecurringCron({ frequency: "weekdays", weekday: 0, hour: 17, minute: 30 }), "30 17 * * 1-5", "weekdays at 5:30pm");
eq(buildRecurringCron({ frequency: "weekly", weekday: 1, hour: 9, minute: 0 }), "0 9 * * 1", "weekly on Monday at 9:00");
eq(buildRecurringCron({ frequency: "weekly", weekday: 0, hour: 0, minute: 0 }), "0 0 * * 0", "weekly on Sunday, midnight");

for (const s of buildRecurringCron({ frequency: "daily", weekday: 0, hour: 9, minute: 5 }).split(" ")) {
  assert(/^[\d*-]+$/.test(s), "every field is a bare cron token, never something croniter would reject outright");
}
eq(buildRecurringCron({ frequency: "daily", weekday: 0, hour: 9, minute: 5 }).split(" ").length, 5, "always exactly 5 fields");

// Out-of-range input is clamped, never allowed to build an invalid cron
// field (a bad minute/hour would otherwise fail loud server-side with no
// client-side explanation of why).
eq(buildRecurringCron({ frequency: "daily", weekday: 0, hour: 99, minute: -5 }), "0 23 * * *", "hour/minute are clamped into range");
eq(buildRecurringCron({ frequency: "weekly", weekday: 14, hour: 9, minute: 0 }), "0 9 * * 6", "weekday is clamped into 0-6");

// ── parseRecurringCron: the exact inverse, for editing an existing row ─────
eq(parseRecurringCron("0 9 * * *"), { frequency: "daily", weekday: 0, hour: 9, minute: 0 }, "parses daily");
eq(parseRecurringCron("30 17 * * 1-5"), { frequency: "weekdays", weekday: 0, hour: 17, minute: 30 }, "parses weekdays");
eq(parseRecurringCron("0 9 * * 1"), { frequency: "weekly", weekday: 1, hour: 9, minute: 0 }, "parses weekly");

// Round-trip: build -> parse must always return the same schedule (modulo
// the weekday field being ignored for daily/weekdays, which is why it's
// normalized to 0 on the way out).
const ROUND_TRIP_CASES: PlainRecurringSchedule[] = [
  { frequency: "daily", weekday: 0, hour: 6, minute: 15 },
  { frequency: "weekdays", weekday: 0, hour: 18, minute: 45 },
  { frequency: "weekly", weekday: 5, hour: 12, minute: 0 },
];
for (const schedule of ROUND_TRIP_CASES) {
  const cron = buildRecurringCron(schedule);
  eq(parseRecurringCron(cron), schedule, `round-trips through "${cron}"`);
}

// A cron shape this module doesn't generate — a day-of-month, a step, a
// weekday LIST — must come back null, not a wrong guess. Editing must never
// silently mis-simplify a schedule an agent created with fleet__schedule_
// recurring_task's fuller cron vocabulary.
assert(parseRecurringCron("0 9 1 * *") === null, "a day-of-month cron is not one of our shapes");
assert(parseRecurringCron("*/15 * * * *") === null, "a step expression is not one of our shapes");
assert(parseRecurringCron("0 9 * * 1,3,5") === null, "a weekday LIST is not one of our shapes");
assert(parseRecurringCron("0 9 * * *  ") !== null, "trailing whitespace alone doesn't break parsing");
assert(parseRecurringCron("not a cron") === null, "garbage input returns null, never throws");
assert(parseRecurringCron("") === null, "blank input returns null");

// ── describeRecurringSchedule / describeCronPlain: the sentence a customer
// actually reads — plain language, no field names, no cron ──────────────
eq(describeRecurringSchedule({ frequency: "daily", weekday: 0, hour: 9, minute: 0 }), "Every day at 9:00 AM", "daily, on the hour");
eq(describeRecurringSchedule({ frequency: "daily", weekday: 0, hour: 0, minute: 0 }), "Every day at 12:00 AM", "midnight is 12 AM, not 0 AM");
eq(describeRecurringSchedule({ frequency: "daily", weekday: 0, hour: 12, minute: 0 }), "Every day at 12:00 PM", "noon is 12 PM, not 0 PM");
eq(describeRecurringSchedule({ frequency: "weekdays", weekday: 0, hour: 17, minute: 30 }), "Every weekday at 5:30 PM", "weekdays, PM");
eq(describeRecurringSchedule({ frequency: "weekly", weekday: 1, hour: 9, minute: 5 }), "Every Monday at 9:05 AM", "named weekday, padded minute");

eq(describeCronPlain("0 9 * * *"), "Every day at 9:00 AM", "describeCronPlain matches the parsed sentence");
eq(describeCronPlain("*/15 * * * *"), "Custom schedule", "an unrecognized cron gets an honest, non-lying label — never raw cron syntax");
assert(!/[*/]/.test(describeCronPlain("*/15 * * * *")), "CANARY: the fallback label itself never leaks a cron character");

for (const cron of ["0 9 * * *", "30 17 * * 1-5", "0 9 * * 1", "*/15 * * * *", "0 9 1 * *"]) {
  const label = describeCronPlain(cron);
  assert(!/[\d]+ [\d*-]+ [\d*-]+ [\d*-]+ [\d*-]+/.test(label), `no raw cron field pattern leaks into the label for "${cron}"`);
}

console.log(`\n${passed} passed, ${failed} failed`);
if (failed > 0) {
  process.exit(1);
}
