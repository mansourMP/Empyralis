/**
 * Recurring agent schedules — plain language in, a 5-field cron string out,
 * never the reverse shown to a customer.
 *
 * routes_fleet.py's POST .../recurring-schedule accepts a raw cron
 * expression (server_modules/bounded_scheduler_service.py's
 * parse_cron_expression/create_recurring_schedule) — that is the backend's
 * real contract, unchanged here. But CLAUDE.md is explicit: "no cron syntax
 * exposed to the customer. Plain language: how often, and when." This module
 * is the ONE seam that translates between the two, so every surface that
 * renders or builds a recurring schedule goes through the same vocabulary
 * instead of each re-deriving its own reading of a cron string.
 *
 * The vocabulary is deliberately small — every day / every weekday / a
 * single weekday, at one time of day — because that is what "quietly check
 * on things and tell me" actually needs, not because cron can't do more.
 * `describeCronPlain` is honest about that boundary: a cron expression this
 * module didn't generate (an agent's own fleet__schedule_recurring_task
 * call can produce anything valid) is labelled "Custom schedule" rather
 * than mis-translated into a plain sentence it doesn't mean.
 */

export type RecurringFrequency = "daily" | "weekdays" | "weekly";

export type PlainRecurringSchedule = {
  frequency: RecurringFrequency;
  /** 0 = Sunday .. 6 = Saturday (cron's own weekday convention). Only
   *  meaningful when frequency === "weekly". */
  weekday: number;
  hour: number;
  minute: number;
};

export const WEEKDAY_NAMES = [
  "Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday",
] as const;

function clamp(value: number, min: number, max: number): number {
  const n = Math.trunc(value);
  if (Number.isNaN(n)) return min;
  return Math.min(max, Math.max(min, n));
}

/** Plain schedule -> the 5-field cron string bounded_scheduler_service.
 *  parse_cron_expression accepts (minute hour day month weekday). */
export function buildRecurringCron(schedule: PlainRecurringSchedule): string {
  const minute = clamp(schedule.minute, 0, 59);
  const hour = clamp(schedule.hour, 0, 23);
  if (schedule.frequency === "daily") return `${minute} ${hour} * * *`;
  if (schedule.frequency === "weekdays") return `${minute} ${hour} * * 1-5`;
  const weekday = clamp(schedule.weekday, 0, 6);
  return `${minute} ${hour} * * ${weekday}`;
}

/** The reverse — only for the three shapes buildRecurringCron itself
 *  produces. Returns null for anything else (day/month fields other than
 *  "*", a weekday list, step syntax, …) so the UI never pretends to
 *  represent a cron expression it can't actually round-trip. */
export function parseRecurringCron(cron: string): PlainRecurringSchedule | null {
  const fields = String(cron || "").trim().split(/\s+/);
  if (fields.length !== 5) return null;
  const [minuteField, hourField, dayField, monthField, weekdayField] = fields;
  if (dayField !== "*" || monthField !== "*") return null;
  if (!/^\d+$/.test(minuteField) || !/^\d+$/.test(hourField)) return null;
  const minute = Number(minuteField);
  const hour = Number(hourField);
  if (minute > 59 || hour > 23) return null;

  if (weekdayField === "*") return { frequency: "daily", weekday: 0, hour, minute };
  if (weekdayField === "1-5") return { frequency: "weekdays", weekday: 0, hour, minute };
  if (/^\d+$/.test(weekdayField)) {
    const weekday = Number(weekdayField);
    if (weekday <= 6) return { frequency: "weekly", weekday, hour, minute };
  }
  return null;
}

function formatClock(hour: number, minute: number): string {
  const period = hour >= 12 ? "PM" : "AM";
  const h12 = hour % 12 === 0 ? 12 : hour % 12;
  return `${h12}:${String(minute).padStart(2, "0")} ${period}`;
}

/** "Every day at 9:00 AM" / "Every weekday at 9:00 AM" / "Every Monday at
 *  9:00 AM" — the sentence both the live picker and each saved row render,
 *  so what you set is worded exactly like what you see afterward. */
export function describeRecurringSchedule(schedule: PlainRecurringSchedule): string {
  const time = formatClock(schedule.hour, schedule.minute);
  if (schedule.frequency === "daily") return `Every day at ${time}`;
  if (schedule.frequency === "weekdays") return `Every weekday at ${time}`;
  return `Every ${WEEKDAY_NAMES[clamp(schedule.weekday, 0, 6)]} at ${time}`;
}

/** For a saved row: describe its cron honestly, never invent a sentence for
 *  a shape this module doesn't generate. */
export function describeCronPlain(cron: string): string {
  const parsed = parseRecurringCron(cron);
  return parsed ? describeRecurringSchedule(parsed) : "Custom schedule";
}

export const DEFAULT_PLAIN_SCHEDULE: PlainRecurringSchedule = {
  frequency: "daily",
  weekday: 1,
  hour: 9,
  minute: 0,
};
