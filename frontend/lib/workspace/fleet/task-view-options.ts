/**
 * VIEW OPTIONS FOR A PROJECT'S TASKS — grouping, ordering, display properties.
 *
 * Linear's view-options popover, built against what a `FleetTask` ACTUALLY
 * carries rather than against Linear's field list. Every option below maps to
 * a field the backend really returns (see fleet-data.FleetTask) and to
 * something a card or a row really draws today. Nothing here offers a control
 * for data we don't have — "no dead controls" (CLAUDE.md) applies to a
 * dropdown option exactly as much as to a button.
 *
 * WHAT IS DELIBERATELY NOT OFFERED, and why — read this before adding to it:
 *   · "Set default for everyone" (Linear's footer). There is no team-defaults
 *     model here: projects hold members directly, and no per-workspace view
 *     preference is persisted server-side. The button would write nowhere.
 *   · A "Status" display toggle on the BOARD and on the GROUPED list. On both
 *     of those the status ring is not a read-out, it is the native <select>
 *     laid over the ring — the ONLY keyboard path for moving a task (HTML5
 *     drag is mouse-only; see TasksBoard's header). Hiding it would take
 *     keyboard users' one way to move work. On the flat list, Status is a
 *     plain chip and hiding it is safe, so that is the one surface offering
 *     it.
 *   · Milestones, Health, Teams, Lead, Dependencies, Start date, Completed
 *     (all on Linear's list). No column, no field, no data.
 *   · Grouping in BOARD view. A board column IS a status — that is what makes
 *     a drop mean "move this task to In review". Re-grouping the columns by
 *     assignee would make a drop mean something the API cannot always do
 *     (there is no unassign endpoint), so the control is not rendered in that
 *     view rather than rendered and half-working.
 *
 * PURE MODULE ON PURPOSE (no React): the vocabularies, the localStorage
 * round-trip, and the group/sort maths are worth reasoning about — and
 * testing — without a component tree, the same split fleet-tabs.ts already
 * makes.
 */

import {
  FLEET_TASK_STATUSES,
  normalizeTaskStatus,
  type FleetAgent,
  type FleetTask,
  type FleetTaskStatus,
} from "./fleet-data";
import type { WorkspaceMember } from "./members-data";
import { TASK_PRIORITY_LABELS, taskPriority, taskPriorityRank, type TaskPriority } from "./task-status";

// ── Vocabulary ──────────────────────────────────────────────────────────────

/** Two views, not three. "Grouped" used to be a third pill; it was never a
 *  third VIEW — it is the list, grouped. Linear models exactly this split and
 *  it is the reason grouping belongs in the popover. */
export type TaskLayout = "board" | "list";

export type TaskGrouping = "none" | "status" | "assignee" | "priority" | "label";

export type TaskOrdering = "priority" | "due" | "created" | "updated" | "title";

export type TaskOrderDirection = "asc" | "desc";

export type TaskDisplayProperty =
  | "priority"
  | "status"
  | "id"
  | "assignee"
  | "labels"
  | "due"
  | "created"
  | "updated"
  | "subtasks"
  | "description";

/** Which of the three task renderings is on screen. The display-property set
 *  is per-surface because the three renderings genuinely draw different
 *  fields — offering "Description" while a board card is on screen would be a
 *  toggle that does nothing, which is the bug this file exists to avoid. */
export type TaskSurface = "board" | "list" | "grouped";

export type TaskDisplayState = Record<TaskDisplayProperty, boolean>;

export type TaskViewOptions = {
  layout: TaskLayout;
  grouping: TaskGrouping;
  ordering: TaskOrdering;
  direction: TaskOrderDirection;
  display: TaskDisplayState;
};

export const TASK_GROUPING_OPTIONS: { value: TaskGrouping; label: string }[] = [
  { value: "none", label: "No grouping" },
  { value: "status", label: "Status" },
  { value: "assignee", label: "Assignee" },
  { value: "priority", label: "Priority" },
  { value: "label", label: "Label" },
];

export const TASK_ORDERING_OPTIONS: { value: TaskOrdering; label: string }[] = [
  { value: "priority", label: "Priority" },
  { value: "due", label: "Due date" },
  { value: "created", label: "Created" },
  { value: "updated", label: "Updated" },
  { value: "title", label: "Title" },
];

/**
 * The direction a key means when nobody has flipped it. "Ascending" is not a
 * useful default for every key — newest-first is what a reader means by
 * "sorted by created", and urgent-first is what they mean by "sorted by
 * priority" — so picking an ordering also picks the direction that reads
 * right for it. The toggle is still there and still flips it.
 */
export const TASK_ORDERING_DEFAULT_DIRECTION: Record<TaskOrdering, TaskOrderDirection> = {
  priority: "asc",
  due: "asc",
  created: "desc",
  updated: "desc",
  title: "asc",
};

/** What the direction toggle actually DOES for this key, said in the reader's
 *  words rather than as "asc"/"desc" — "Urgent first" is a fact about the
 *  list; "ascending" is a fact about a comparator. */
export function orderDirectionLabel(ordering: TaskOrdering, direction: TaskOrderDirection): string {
  const asc = direction === "asc";
  switch (ordering) {
    case "priority":
      return asc ? "Urgent first" : "Low first";
    case "due":
      return asc ? "Soonest first" : "Latest first";
    case "title":
      return asc ? "A to Z" : "Z to A";
    default:
      return asc ? "Oldest first" : "Newest first";
  }
}

/**
 * Every display toggle, and the surfaces each one is real on. A property
 * appears in the popover only while a surface that draws it is on screen.
 *
 *   priority     — the three-bar glyph. Drawn by all three.
 *   status       — the flat list's Status chip ONLY (see the file header for
 *                  why the board card's and grouped row's rings are exempt).
 *   id           — taskShortId, on the board card and the grouped row.
 *   assignee     — agent sigil / member avatar (and, on the flat list, the
 *                  inline reassign control it sits inside).
 *   labels       — TaskLabelChips.
 *   due          — task.due_at.
 *   created      — the board card's date slot when there is no due date.
 *   updated      — the two list renderings' "Updated" column.
 *   subtasks     — the board card's "1/3" rollup donut.
 *   description  — the flat list's second title line.
 */
export const TASK_DISPLAY_PROPERTIES: {
  key: TaskDisplayProperty;
  label: string;
  surfaces: TaskSurface[];
}[] = [
  { key: "priority", label: "Priority", surfaces: ["board", "list", "grouped"] },
  { key: "status", label: "Status", surfaces: ["list"] },
  { key: "id", label: "ID", surfaces: ["board", "grouped"] },
  { key: "assignee", label: "Assignee", surfaces: ["board", "list", "grouped"] },
  { key: "labels", label: "Labels", surfaces: ["board", "list", "grouped"] },
  { key: "due", label: "Due date", surfaces: ["board", "list", "grouped"] },
  { key: "created", label: "Created", surfaces: ["board"] },
  { key: "updated", label: "Updated", surfaces: ["list", "grouped"] },
  { key: "subtasks", label: "Sub-tasks", surfaces: ["board"] },
  { key: "description", label: "Description", surfaces: ["list"] },
];

const ALL_DISPLAY_KEYS = TASK_DISPLAY_PROPERTIES.map((p) => p.key);

/** Everything on. This is exactly what every surface drew before this feature
 *  existed, so a reader who never opens the popover sees no change at all. */
export const DEFAULT_TASK_VIEW_OPTIONS: TaskViewOptions = {
  layout: "board",
  grouping: "none",
  ordering: "created",
  direction: "desc",
  display: Object.fromEntries(ALL_DISPLAY_KEYS.map((k) => [k, true])) as TaskDisplayState,
};

export function taskSurfaceFor(options: TaskViewOptions): TaskSurface {
  if (options.layout === "board") return "board";
  return options.grouping === "none" ? "list" : "grouped";
}

export function displayPropertiesFor(surface: TaskSurface) {
  return TASK_DISPLAY_PROPERTIES.filter((p) => p.surfaces.includes(surface));
}

/**
 * Is anything RESET would touch currently non-default?
 *
 * `layout` is deliberately excluded, here and in resetTaskViewOptions below.
 * Board-or-List is a view the reader is standing in, not an option they
 * tweaked: counting it would light the options button up merely for being on
 * the List, and a Reset that yanked them back to the Board would be a control
 * changing something the popover never showed them.
 */
export function isDefaultTaskViewOptions(options: TaskViewOptions): boolean {
  return (
    options.grouping === DEFAULT_TASK_VIEW_OPTIONS.grouping &&
    options.ordering === DEFAULT_TASK_VIEW_OPTIONS.ordering &&
    options.direction === DEFAULT_TASK_VIEW_OPTIONS.direction &&
    ALL_DISPLAY_KEYS.every((k) => options.display[k] === true)
  );
}

/** Everything the popover controls, back to default — the reader stays in
 *  whichever view they were in. */
export function resetTaskViewOptions(options: TaskViewOptions): TaskViewOptions {
  return {
    ...DEFAULT_TASK_VIEW_OPTIONS,
    layout: options.layout,
    display: { ...DEFAULT_TASK_VIEW_OPTIONS.display },
  };
}

// ── Persistence ─────────────────────────────────────────────────────────────

/** Bumped only when a stored blob can no longer be read into the current
 *  model at all. Adding a display property does NOT warrant a bump: an older
 *  blob is a readable subset and the missing key falls back to its default
 *  (see readTaskViewOptions), where bumping would throw away a reader's whole
 *  configuration to add one checkbox. Same rule fleet-tabs.ts documents. */
const STORAGE_VERSION = 1;

/** `fleet:*`, versioned, workspace-scoped — the convention fleet-tabs.ts and
 *  TasksGroupedList's collapse state already follow. Per WORKSPACE rather
 *  than per project for the same reason the collapse state is: "I read this
 *  grouped by assignee" is a statement about how a team works, not about one
 *  project. */
export function taskViewStorageKey(workspaceId: string): string {
  return `fleet:task-view:v${STORAGE_VERSION}:${workspaceId}`;
}

function oneOf<T extends string>(value: unknown, allowed: readonly T[], fallback: T): T {
  return allowed.includes(value as T) ? (value as T) : fallback;
}

/**
 * Read the stored options, validating EVERY field against the live
 * vocabularies. A grouping that no longer exists, a display key that was
 * removed, a hand-edited blob: each field independently falls back to its
 * default rather than the whole read failing, so one stale key can never
 * strand a reader on a view they cannot get out of.
 */
export function readTaskViewOptions(workspaceId: string): TaskViewOptions {
  const fallback = { ...DEFAULT_TASK_VIEW_OPTIONS, display: { ...DEFAULT_TASK_VIEW_OPTIONS.display } };
  if (!workspaceId) return fallback;
  try {
    const raw = window.localStorage.getItem(taskViewStorageKey(workspaceId));
    if (!raw) return fallback;
    const parsed = JSON.parse(raw);
    if (!parsed || typeof parsed !== "object") return fallback;
    const display = { ...DEFAULT_TASK_VIEW_OPTIONS.display };
    const storedDisplay = parsed.display;
    if (storedDisplay && typeof storedDisplay === "object") {
      for (const key of ALL_DISPLAY_KEYS) {
        if (typeof storedDisplay[key] === "boolean") display[key] = storedDisplay[key];
      }
    }
    return {
      layout: oneOf<TaskLayout>(parsed.layout, ["board", "list"], DEFAULT_TASK_VIEW_OPTIONS.layout),
      grouping: oneOf<TaskGrouping>(
        parsed.grouping,
        TASK_GROUPING_OPTIONS.map((o) => o.value),
        DEFAULT_TASK_VIEW_OPTIONS.grouping,
      ),
      ordering: oneOf<TaskOrdering>(
        parsed.ordering,
        TASK_ORDERING_OPTIONS.map((o) => o.value),
        DEFAULT_TASK_VIEW_OPTIONS.ordering,
      ),
      direction: oneOf<TaskOrderDirection>(parsed.direction, ["asc", "desc"], DEFAULT_TASK_VIEW_OPTIONS.direction),
      display,
    };
  } catch {
    return fallback;
  }
}

export function writeTaskViewOptions(workspaceId: string, options: TaskViewOptions): void {
  if (!workspaceId) return;
  try {
    window.localStorage.setItem(taskViewStorageKey(workspaceId), JSON.stringify(options));
  } catch {
    /* localStorage unavailable (private mode, quota) — the view still works
       for this session, it just won't come back after a reload. */
  }
}

// ── Ordering ────────────────────────────────────────────────────────────────

function timeValue(value: string | null | undefined): number {
  if (!value) return NaN;
  const t = new Date(value).getTime();
  return Number.isNaN(t) ? NaN : t;
}

/** The sort key for one task under one ordering. `null` means THE TASK HAS
 *  NOTHING TO SORT BY — no due date, no priority, no timestamp — and those
 *  always sink to the bottom, in both directions. Flipping the direction must
 *  not float twelve blank rows to the top of the list. */
function orderValue(task: FleetTask, ordering: TaskOrdering): number | string | null {
  switch (ordering) {
    case "priority": {
      const p = taskPriority(task);
      // 0 is "not triaged", not "least urgent" — it is missing data, so it
      // sinks rather than sorting between Low and the end.
      return p === 0 ? null : taskPriorityRank(p);
    }
    case "due": {
      const t = timeValue(task.due_at);
      return Number.isNaN(t) ? null : t;
    }
    case "created": {
      const t = timeValue(task.created_at);
      return Number.isNaN(t) ? null : t;
    }
    case "updated": {
      // Same fallback the grouped list's own "Updated" cell uses: this
      // backend only moves updated_at on assign/update, so a task that has
      // never been touched has only its creation time to offer.
      const t = timeValue(task.updated_at || task.created_at);
      return Number.isNaN(t) ? null : t;
    }
    case "title":
      return (task.title || "").trim().toLowerCase();
    default:
      return null;
  }
}

/**
 * Order a list of tasks. Not mutating: callers hold `tasks` straight off the
 * polled cache and a sort in place would reorder every other consumer's copy.
 *
 * The tiebreak (newest first, then id) is DIRECTION-INDEPENDENT on purpose —
 * it exists to make the order deterministic across the 30s poll, not to be a
 * second sort the reader chose. Without it, two tasks with the same priority
 * could swap places on every refetch.
 */
export function sortTasks(
  tasks: FleetTask[],
  ordering: TaskOrdering,
  direction: TaskOrderDirection,
): FleetTask[] {
  const sign = direction === "asc" ? 1 : -1;
  return [...tasks].sort((a, b) => {
    const av = orderValue(a, ordering);
    const bv = orderValue(b, ordering);
    if (av === null && bv === null) return tiebreak(a, b);
    if (av === null) return 1;
    if (bv === null) return -1;
    let primary = 0;
    if (typeof av === "string" || typeof bv === "string") {
      primary = String(av).localeCompare(String(bv));
    } else {
      primary = av - bv;
    }
    if (primary !== 0) return primary * sign;
    return tiebreak(a, b);
  });
}

function tiebreak(a: FleetTask, b: FleetTask): number {
  const at = timeValue(a.created_at);
  const bt = timeValue(b.created_at);
  if (!Number.isNaN(at) && !Number.isNaN(bt) && at !== bt) return bt - at;
  return String(a.id).localeCompare(String(b.id));
}

// ── Grouping ────────────────────────────────────────────────────────────────

/**
 * One section of a grouped list. `status` / `priority` / `labelColor` are the
 * group's own identity, present only for the grouping that produced it, and
 * are what let a header draw the right glyph without re-deriving it.
 *
 * `createStatus` is the one thing a group can hand the composer: a NEW task
 * is born with a status, so "+" on a status section can pre-set it. There is
 * no equivalent for assignee/priority/label sections (the create route takes
 * a priority but not an assignee, and a label has to be attached after the
 * fact), so those sections render no "+" at all rather than one that quietly
 * ignores which section it was clicked in.
 */
export type TaskGroup = {
  key: string;
  label: string;
  tasks: FleetTask[];
  count: number;
  status?: FleetTaskStatus;
  priority?: TaskPriority;
  labelColor?: string;
  createStatus?: FleetTaskStatus;
  /** True for the catch-all bucket (Unassigned / No priority / No label) —
   *  drawn quieter, and always last. */
  empty?: boolean;
};

function assigneeLabel(
  task: FleetTask,
  agents: FleetAgent[],
  members: WorkspaceMember[],
): { key: string; label: string; empty?: boolean } {
  if (task.assignee_agent_id) {
    const agent = agents.find((a) => a.agent_id === task.assignee_agent_id);
    return { key: `agent:${task.assignee_agent_id}`, label: agent?.label || "Unnamed agent" };
  }
  if (task.assignee_user_id) {
    const member = members.find((m) => m.user_id === task.assignee_user_id);
    return {
      key: `user:${task.assignee_user_id}`,
      label: member?.display_name || member?.email || "Unknown person",
    };
  }
  return { key: "unassigned", label: "Unassigned", empty: true };
}

/**
 * Split tasks into the sections one grouping asks for.
 *
 * ONLY NON-EMPTY GROUPS ARE RETURNED, with one deliberate exception:
 * `includeEmptyStatuses` keeps every status section, which is what the
 * grouped list's "All" tab wants ("show me that Blocked really is empty"
 * rather than "Blocked is filtered away"). The board's own rule — a column
 * exists if and only if it has a task — is enforced inside TasksBoard and is
 * NOT routed through here, so nothing in this file can reintroduce an empty
 * board column.
 *
 * GROUPING BY LABEL PUTS A TASK IN EVERY LABEL IT CARRIES. A task with "bug"
 * and "urgent" appears under both, exactly as it does in Linear; the counts
 * therefore sum to more than the task count, which is correct and is what a
 * label grouping means.
 */
export function groupTasks(
  tasks: FleetTask[],
  grouping: TaskGrouping,
  {
    agents = [],
    members = [],
    includeEmptyStatuses = false,
  }: { agents?: FleetAgent[]; members?: WorkspaceMember[]; includeEmptyStatuses?: boolean } = {},
): TaskGroup[] {
  if (grouping === "none") {
    return [{ key: "all", label: "All tasks", tasks, count: tasks.length }];
  }

  if (grouping === "status") {
    const buckets = new Map<FleetTaskStatus, FleetTask[]>(FLEET_TASK_STATUSES.map((s) => [s, []]));
    for (const task of tasks) buckets.get(normalizeTaskStatus(task.status))?.push(task);
    return FLEET_TASK_STATUSES.filter((s) => includeEmptyStatuses || (buckets.get(s) || []).length > 0).map((s) => ({
      key: s,
      label: "",
      status: s,
      createStatus: s,
      tasks: buckets.get(s) || [],
      count: (buckets.get(s) || []).length,
    }));
  }

  if (grouping === "priority") {
    const buckets = new Map<TaskPriority, FleetTask[]>();
    for (const task of tasks) {
      const p = taskPriority(task);
      if (!buckets.has(p)) buckets.set(p, []);
      buckets.get(p)!.push(task);
    }
    // Urgent(1) → Low(4), then No priority(0) last — taskPriorityRank's own
    // order, so the sections read forward the same way the glyph does.
    return ([1, 2, 3, 4, 0] as TaskPriority[])
      .filter((p) => (buckets.get(p) || []).length > 0)
      .map((p) => ({
        key: `priority:${p}`,
        label: TASK_PRIORITY_LABELS[p],
        priority: p,
        tasks: buckets.get(p) || [],
        count: (buckets.get(p) || []).length,
        empty: p === 0,
      }));
  }

  if (grouping === "assignee") {
    const buckets = new Map<string, TaskGroup>();
    for (const task of tasks) {
      const { key, label, empty } = assigneeLabel(task, agents, members);
      if (!buckets.has(key)) buckets.set(key, { key, label, tasks: [], count: 0, empty });
      const group = buckets.get(key)!;
      group.tasks.push(task);
      group.count += 1;
    }
    // Named assignees alphabetically, "Unassigned" last — a catch-all bucket
    // is where you look when you are done with the named ones.
    return [...buckets.values()].sort((a, b) => {
      if (Boolean(a.empty) !== Boolean(b.empty)) return a.empty ? 1 : -1;
      return a.label.localeCompare(b.label);
    });
  }

  // grouping === "label"
  const buckets = new Map<string, TaskGroup>();
  const NO_LABEL = "__none__";
  for (const task of tasks) {
    const labels = task.labels || [];
    if (labels.length === 0) {
      if (!buckets.has(NO_LABEL)) {
        buckets.set(NO_LABEL, { key: NO_LABEL, label: "No label", tasks: [], count: 0, empty: true });
      }
      const group = buckets.get(NO_LABEL)!;
      group.tasks.push(task);
      group.count += 1;
      continue;
    }
    for (const label of labels) {
      const key = `label:${label.id || label.name}`;
      if (!buckets.has(key)) {
        buckets.set(key, { key, label: label.name, labelColor: label.color || "grey", tasks: [], count: 0 });
      }
      const group = buckets.get(key)!;
      group.tasks.push(task);
      group.count += 1;
    }
  }
  return [...buckets.values()].sort((a, b) => {
    if (Boolean(a.empty) !== Boolean(b.empty)) return a.empty ? 1 : -1;
    return a.label.localeCompare(b.label);
  });
}
