/**
 * The task vocabulary's own visual language — status ring + priority glyph.
 *
 * WHY THIS IS SEPARATE FROM fleet-indicators.tsx: that file holds the AGENT
 * status vocabulary (StatusDot / StatusChip, keyed by AgentStatusTone — a
 * health signal: online, degraded, offline). A task's seven states are a
 * lifecycle, not a health reading, and they have to stay distinguishable as
 * seven columns sitting side by side. Folding them into the agent tones is
 * exactly what produced the bug this replaces: `awaiting_input` and
 * `in_review` both mapped to `degraded`, so two of the seven board columns
 * were the same amber.
 *
 * THE RING IDIOM. A flat coloured dot says "this task is amber". Linear's
 * ring says "this task is HALF DONE" — the circle fills as the task advances
 * (dashed → hollow → half → three-quarter → solid), so the board reads as
 * progress even in greyscale, and colour is a second, redundant channel
 * rather than the only one. That redundancy is also the accessibility story:
 * the seven states are still told apart by someone who cannot separate the
 * blue from the green.
 *
 * Colour comes from the --task-* custom properties in lib/ui/theme-tokens.css
 * (defined per theme), never from a hex literal here — a fill fraction is
 * geometry and belongs in this file; a colour is a theme decision and does
 * not.
 */

import type { CSSProperties } from "react";

import type { FleetTaskStatus } from "./fleet-data";

// ── Identity ────────────────────────────────────────────────────────────────

/** A short, stable, human-quotable handle for a task, derived from its real
 *  id (`task_<16 hex>`) — NOT a new identifier and never persisted. Linear's
 *  board leads every card with one; this is the honest version of that until
 *  the backend has a real per-project sequence number.
 *
 *  Lives here (not in TasksBoard, where it started) so the tab strip and the
 *  routed task page can name a task without importing the whole board. */
export function taskShortId(id: string): string {
  const suffix = String(id || "").replace(/^task_/, "");
  return (suffix.slice(0, 6) || "------").toUpperCase();
}

// ── Status ──────────────────────────────────────────────────────────────────

type StatusVisual = {
  label: string;
  /** CSS custom property holding this status' colour, per theme. */
  colorVar: string;
  /** How much of the ring's interior is filled, 0..1. Non-decreasing across
   *  the board's left-to-right column order, so the icon alone tells you
   *  which direction is "forward". */
  fill: number;
  /** Backlog only: the outline is dashed, the "not really on the board yet"
   *  reading Linear uses for the same state. */
  dashed?: boolean;
  /** Done only: a solid disc with a check struck through it, rather than a
   *  ring filled to 100% — the terminal state should look terminal. */
  complete?: boolean;
};

const STATUS_VISUALS: Record<FleetTaskStatus, StatusVisual> = {
  backlog: { label: "Backlog", colorVar: "--task-backlog", fill: 0, dashed: true },
  todo: { label: "Todo", colorVar: "--task-todo", fill: 0 },
  in_progress: { label: "In progress", colorVar: "--task-progress", fill: 0.5 },
  awaiting_input: { label: "Needs input", colorVar: "--task-input", fill: 0.5 },
  blocked: { label: "Blocked", colorVar: "--task-blocked", fill: 0.5 },
  in_review: { label: "In review", colorVar: "--task-review", fill: 0.75 },
  done: { label: "Done", colorVar: "--task-done", fill: 1, complete: true },
};

export function taskStatusVisual(status: FleetTaskStatus): StatusVisual {
  return STATUS_VISUALS[status] ?? STATUS_VISUALS.todo;
}

export function taskStatusLabel(status: FleetTaskStatus): string {
  return taskStatusVisual(status).label;
}

/** Ring geometry, in the 14x14 user space every icon here shares.
 *  Outer ring: r 5.25 + 1.5 stroke → outer edge 6.0, inner edge 4.5.
 *  Fill: an r-2 circle stroked 4 wide covers 0..4, leaving a 0.5 gap inside
 *  the ring — the gap is what stops a part-filled ring reading as a smudge.
 *  Dashing THAT stroke from its own circumference is the standard pie trick;
 *  rotate -90deg so the fill starts at 12 o'clock like a clock hand. */
const FILL_R = 2;
const FILL_CIRCUMFERENCE = 2 * Math.PI * FILL_R;

/**
 * The status ring for one task. `size` is the rendered box in px; the art is
 * authored at 14 and scales cleanly because it is all strokes and circles.
 */
export function TaskStatusIcon({
  status,
  size = 14,
  className,
}: {
  status: FleetTaskStatus;
  size?: number;
  className?: string;
}) {
  const v = taskStatusVisual(status);
  const style = { color: `var(${v.colorVar})` } as CSSProperties;

  return (
    <svg
      className={className}
      width={size}
      height={size}
      viewBox="0 0 14 14"
      style={style}
      aria-hidden
      focusable="false"
      shapeRendering="geometricPrecision"
    >
      {v.complete ? (
        <>
          <circle cx="7" cy="7" r="6" fill="currentColor" />
          {/* The check is knocked out in white rather than the surface colour:
              --task-done is the violet accent in both themes and is dark
              enough for white to hold contrast on either. */}
          <path
            d="M4.1 7.15 L6.05 9.05 L9.9 5.05"
            fill="none"
            stroke="#fff"
            strokeWidth="1.6"
            strokeLinecap="round"
            strokeLinejoin="round"
          />
        </>
      ) : (
        <>
          <circle
            cx="7"
            cy="7"
            r="5.25"
            fill="none"
            stroke="currentColor"
            strokeWidth="1.5"
            strokeDasharray={v.dashed ? "2.4 2.1" : undefined}
            strokeLinecap={v.dashed ? "round" : undefined}
          />
          {v.fill > 0 ? (
            <circle
              cx="7"
              cy="7"
              r={FILL_R}
              fill="none"
              stroke="currentColor"
              strokeWidth={FILL_R * 2}
              strokeDasharray={`${(v.fill * FILL_CIRCUMFERENCE).toFixed(3)} ${FILL_CIRCUMFERENCE.toFixed(3)}`}
              transform="rotate(-90 7 7)"
            />
          ) : null}
        </>
      )}
    </svg>
  );
}

/**
 * Ring + label, for the flat task list and anywhere a status needs spelling
 * out. Deliberately borrows `.fleet-schip`'s existing shell (inline-flex,
 * 12px medium, --text-secondary) and adds no CSS: the label stays neutral
 * grey and the RING carries the colour, which is the same "colour is
 * information, not decoration" discipline the agent chips follow.
 */
export function TaskStatusChip({ status }: { status: FleetTaskStatus }) {
  return (
    <span className="fleet-schip">
      <TaskStatusIcon status={status} size={13} />
      {taskStatusLabel(status)}
    </span>
  );
}

// ── Sub-tasks ───────────────────────────────────────────────────────────────

/**
 * The sub-task rollup — "1/3" against a donut filled to the same fraction.
 *
 * The numbers are real and already in hand: project_tasks_service computes
 * `subtask_count` / `subtask_done_count` in the same LATERAL join that fetched
 * the row (see migrations/add_task_parent.sql), so a board of 200 cards costs
 * zero extra reads to draw this.
 *
 * The donut is deliberately the SAME geometry as TaskStatusIcon's ring, in
 * neutral ink rather than a status colour: a card must not sprout a second
 * coloured ring competing with the one that carries lifecycle meaning. Shape
 * says "a fraction of something is finished"; the digits say which fraction.
 *
 * Renders NOTHING when the task has no sub-tasks — which is nearly every task.
 * A "0/0" or an empty slot on every ordinary card is exactly the dead glyph
 * this pass exists to remove.
 */
export function TaskSubtaskProgress({
  done,
  total,
  size = 11,
  className,
}: {
  /** `task.subtask_done_count` — absent on a server predating the migration. */
  done?: number | null;
  /** `task.subtask_count` — same. */
  total?: number | null;
  size?: number;
  className?: string;
}) {
  const count = Math.max(0, Math.floor(Number(total) || 0));
  if (count === 0) return null;
  const complete = Math.min(count, Math.max(0, Math.floor(Number(done) || 0)));
  const fraction = complete / count;

  return (
    <span
      className={`fleet-subtask-progress${className ? ` ${className}` : ""}`}
      title={`${complete} of ${count} sub-${count === 1 ? "task" : "tasks"} done`}
    >
      <svg
        width={size}
        height={size}
        viewBox="0 0 14 14"
        aria-hidden
        focusable="false"
        shapeRendering="geometricPrecision"
      >
        <circle cx="7" cy="7" r="5.25" fill="none" stroke="currentColor" strokeWidth="1.5" opacity="0.45" />
        {fraction > 0 ? (
          <circle
            cx="7"
            cy="7"
            r={FILL_R}
            fill="none"
            stroke="currentColor"
            strokeWidth={FILL_R * 2}
            strokeDasharray={`${(fraction * FILL_CIRCUMFERENCE).toFixed(3)} ${FILL_CIRCUMFERENCE.toFixed(3)}`}
            transform="rotate(-90 7 7)"
          />
        ) : null}
      </svg>
      {complete}/{count}
    </span>
  );
}

// ── Priority ────────────────────────────────────────────────────────────────

/**
 * Linear's own integer convention, which the backend adopted verbatim:
 *   0 none · 1 urgent · 2 high · 3 medium · 4 low
 * Note 1 is the MOST urgent — the scale counts down, not up. Sorting by the
 * raw integer therefore puts urgent first and "none" last only if 0 is
 * special-cased, which taskPriorityRank below does.
 */
export type TaskPriority = 0 | 1 | 2 | 3 | 4;

export const TASK_PRIORITIES: TaskPriority[] = [0, 1, 2, 3, 4];

export const TASK_PRIORITY_LABELS: Record<TaskPriority, string> = {
  0: "No priority",
  1: "Urgent",
  2: "High",
  3: "Medium",
  4: "Low",
};

/**
 * Read a task's priority defensively. The column is being added by a separate
 * change and may simply not be in the API response yet, so ANYTHING that
 * isn't one of the five known integers — undefined, null, "2", 9, NaN —
 * degrades to 0 ("no priority") rather than rendering a broken glyph. This is
 * the single place that decision is made; nothing else in the UI should look
 * at `task.priority` directly.
 */
export function taskPriority(task: { priority?: unknown } | null | undefined): TaskPriority {
  const raw = task?.priority;
  const n = typeof raw === "number" ? raw : typeof raw === "string" ? Number(raw) : NaN;
  return n === 1 || n === 2 || n === 3 || n === 4 ? (n as TaskPriority) : 0;
}

/** Sort key: urgent(1) → low(4) → none(0). "No priority" sorts last, which is
 *  what a reader means by it, not "more urgent than urgent". */
export function taskPriorityRank(priority: TaskPriority): number {
  return priority === 0 ? 99 : priority;
}

/** How many of the three bars are lit, per priority. */
const PRIORITY_BARS: Record<TaskPriority, number> = { 0: 0, 1: 3, 2: 3, 3: 2, 4: 1 };

/** Three ascending bars in a 14x14 box, Linear's exact idiom. */
const BAR_GEOMETRY = [
  { x: 1.5, y: 8.5, h: 4 },
  { x: 5.5, y: 5.5, h: 7 },
  { x: 9.5, y: 2.5, h: 10 },
];

/**
 * The priority glyph.
 *
 * `0` (no priority) draws three muted dashes — a PLACEHOLDER, and one that
 * only earns its place on a surface where something else depends on this
 * glyph's width:
 *   · TasksGroupedList — a fixed 16px grid track; an empty cell there keeps
 *     the id/status/title columns aligned down the whole list.
 *   · TasksList — an inline glyph leading every row's title; dropping it on
 *     untriaged rows only would leave the titles ragged row to row.
 *   · TaskComposer / TaskDetailView — here the glyph IS the priority control's
 *     current value, and "No priority" is a real, selectable choice. A trigger
 *     with no icon at all would be a control with nothing on it.
 *
 * Nowhere else. On the board card (TasksBoard.TaskCard) the whole element is
 * omitted when priority is 0 — a card is a free-floating box with nothing to
 * line up against, so three grey dashes there were just a glyph announcing it
 * had nothing to say, on every untriaged card. CLAUDE.md: "A control whose own
 * label admits it does nothing is a design bug, not a caption." Do not add the
 * dashes back to a surface that has no alignment to protect.
 */
export function TaskPriorityIcon({
  priority,
  size = 14,
  className,
}: {
  priority: TaskPriority;
  size?: number;
  className?: string;
}) {
  const title = TASK_PRIORITY_LABELS[priority] ?? TASK_PRIORITY_LABELS[0];

  if (priority === 1) {
    // Urgent is the one priority that gets colour — a filled tile with a bang,
    // so it is findable at a glance in a column of grey bars.
    return (
      <svg
        className={className}
        width={size}
        height={size}
        viewBox="0 0 14 14"
        style={{ color: "var(--priority-urgent)" } as CSSProperties}
        role="img"
        aria-label={title}
        focusable="false"
      >
        <rect x="1" y="1" width="12" height="12" rx="3" fill="currentColor" />
        <rect x="6.25" y="3.4" width="1.5" height="4.6" rx="0.75" fill="#fff" />
        <circle cx="7" cy="10.1" r="0.95" fill="#fff" />
      </svg>
    );
  }

  const lit = PRIORITY_BARS[priority] ?? 0;

  return (
    <svg
      className={className}
      width={size}
      height={size}
      viewBox="0 0 14 14"
      role="img"
      aria-label={title}
      focusable="false"
    >
      {priority === 0
        ? BAR_GEOMETRY.map((b, i) => (
            <rect key={i} x={b.x} y={6.25} width="3" height="1.5" rx="0.75" fill="var(--priority-bar-empty)" />
          ))
        : BAR_GEOMETRY.map((b, i) => (
            <rect
              key={i}
              x={b.x}
              y={b.y}
              width="3"
              height={b.h}
              rx="1"
              fill={i < lit ? "var(--priority-bar)" : "var(--priority-bar-empty)"}
            />
          ))}
    </svg>
  );
}
