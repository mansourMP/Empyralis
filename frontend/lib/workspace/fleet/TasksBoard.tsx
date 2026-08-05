"use client";

/**
 * The project's kanban board — one column per task status, in the forward
 * vocabulary's own order (fleet-data.FLEET_TASK_STATUSES, which mirrors
 * project_tasks_service.TASK_STATUS_ORDER). Seven columns is the point, not
 * a problem: the board scrolls horizontally rather than collapsing statuses
 * together, so `in_review` — agent-completed work parked for a human — is a
 * place you can actually look at, which is the whole reason the vocabulary
 * grew.
 *
 * A column with zero tasks renders nothing at all — not even its header —
 * per the founder's direct call: "if it doesn't exist, it's not shown by
 * default." Manual hiding (below) is a separate, independent layer on top
 * of this; a column reappears the instant it gets its first task, since
 * visibility is derived from `counts` live on every render, not cached.
 *
 * PRESENTATION — Linear's board, not a card-in-a-tray kanban:
 *   · The columns have NO container. No background, no border, no rounded
 *     tray. A column is a header row and a stack of cards floating on the
 *     page; the only vertical separation is the gap between them. Drawing
 *     the tray is what made this read boxed-in and dated.
 *   · Status is a progressively-filled RING (task-status.TaskStatusIcon),
 *     not a flat dot — see that file for why.
 *   · Assignee avatars are neutral (colour-discipline pass) — no per-agent
 *     identity tint. AgentSigil's own generated shape is what makes one
 *     card's assignee glance-distinguishable from another's, not hue.
 *
 * WHAT A CARD CARRIES, AND THE ONE RULE GOVERNING IT (2026-08-01). Every
 * element is drawn if and only if the task actually HAS that data — there are
 * no placeholders, no empty slots and no glyphs whose meaning is "nothing
 * here". The card grew from title + ring + a dead `---` + id + "9h ago" to
 * carry, conditionally: the parent task it hangs off, its labels, its
 * priority, its sub-task rollup, an explicit date, and its assignee. A task
 * with none of those is still a title, a ring and an id — and that is the
 * correct card for it.
 *
 * DISPLAY PROPERTIES (2026-08-01, founder's call — this paragraph used to say
 * the opposite). A `display` map can now switch individual elements off:
 * priority, id, labels, sub-task rollup, dates, assignee. It does NOT replace
 * the rule above, it composes with it — an element is drawn if the task has
 * that data AND the reader has not switched it off, so turning everything on
 * (the default) gives exactly the card this file has always drawn. What is
 * still not offered is a toggle for anything a card cannot show: see
 * task-view-options.ts for the full list and the reasoning, including why the
 * status ring is exempt (it is the card's only keyboard move control).
 *
 * MOVING A TASK — two paths, both hitting the same handler:
 *   1. Drag a card into another column. Native HTML5 drag-and-drop only
 *      (draggable + dragover + drop) — zero new dependencies.
 *   2. The per-card status <select>. This is NOT a redundant second control:
 *      HTML5 drag-and-drop is mouse-only and has no keyboard equivalent, so
 *      without it the board would be unusable by keyboard.
 *      It is now VISUALLY the status ring rather than a labelled dropdown:
 *      the native <select> is laid transparently over the ring, so it keeps
 *      the platform's own listbox, keyboard model and screen-reader
 *      semantics while costing the card no width. The old version spelled
 *      the status out in words on every card — information the column
 *      heading directly above already carried, and the widest element in
 *      the card's meta row.
 *
 * COLUMN HEADER CONTROLS — just `+`:
 *   `+` opens the composer with THIS column's status already set, so a task
 *   filed in Todo starts as Todo. No second step, no dragging it over.
 *
 * REMOVED 2026-08-01: a per-column "Hide"/"Show all" menu used to live here
 * (persisted to localStorage, independent of task count). It let a column
 * with a real, unaddressed task in it — in_review, blocked, whatever a human
 * had once decided to stop looking at — silently vanish from the board and
 * stay gone, with no on-screen signal that anything was hidden. That is
 * exactly how a real task went unseen for hours. Founder's own words apply
 * without exception now: a column shows if and only if it has a task in it
 * (see `visibleStatuses` below) — nothing overrides that, in either
 * direction, so "hidden but populated" can no longer exist as a state.
 *
 * Desktop-first by explicit scope (1440x900). Below the board's own
 * breakpoint the columns simply keep scrolling horizontally rather than
 * re-laying out — a real mobile board is separate, later work.
 */

import { useEffect, useMemo, useRef, useState, type DragEvent } from "react";
import { CornerDownRight, Plus } from "lucide-react";

import { dueLabel } from "./TasksList";
import { formatDate, formatDateTime } from "./fleet-presentation";
import { AgentSigil } from "./fleet-indicators";
import { MemberAvatar } from "./MemberAvatarStack";
import type { WorkspaceMember } from "./members-data";
import { TaskLabelChips } from "./task-labels";
import {
  TaskStatusIcon,
  TaskPriorityIcon,
  TaskSubtaskProgress,
  TaskWakeDeferralIcon,
  taskStatusLabel,
  taskPriority,
  taskShortId,
  TASK_PRIORITY_LABELS,
} from "./task-status";
import { FLEET_TASK_STATUSES, countTasksByStatus, type FleetAgent, type FleetTask, type FleetTaskStatus } from "./fleet-data";
import { DEFAULT_TASK_VIEW_OPTIONS, type TaskDisplayState } from "./task-view-options";

/** The dataTransfer type the card writes and the column reads. Namespaced so
 *  a drop of anything else (a file, a text selection, a card from some other
 *  future board) is ignored rather than half-handled. */
const DRAG_MIME = "application/x-fleet-task-id";

// taskShortId moved to ./task-status (the tab strip and the routed task page
// both need it and neither should pull the whole board in). Re-exported so
// existing importers keep working.
export { taskShortId };

export function TasksBoard({
  workspaceId,
  tasks,
  agents,
  members,
  selectedTaskId,
  taskHref,
  display = DEFAULT_TASK_VIEW_OPTIONS.display,
  onSelect,
  onStatusChange,
  onCreateTask,
}: {
  /** Unused since the hidden-column feature was removed 2026-08-01; kept in
   *  the prop type so existing callers don't need a matching edit. */
  workspaceId?: string;
  tasks: FleetTask[];
  /** Agents in this project — valid AGENT assignees. */
  agents: FleetAgent[];
  /** Workspace members (MAN-64/MAN-70) — resolves a HUMAN assignee's avatar
   *  on a card. The board has no assignee PICKER (drag/status-select only),
   *  so this is display-only here. */
  members?: WorkspaceMember[];
  selectedTaskId?: string | null;
  /** The task's real route. Stamped on each card as `data-tab-href`, which is
   *  what makes ⌘/Ctrl+click and middle-click open it in a background content
   *  tab (see FleetTabs). A card is a drag source, so it stays a <div role=
   *  "button"> rather than becoming an <a> — the attribute is how it opts into
   *  link-like modifier gestures without giving up drag-and-drop. */
  taskHref?: (taskId: string) => string;
  /** Which card elements this reader wants drawn (the view-options popover's
   *  "Display properties"). Defaults to everything on, which is exactly the
   *  card this board drew before the popover existed. */
  display?: TaskDisplayState;
  onSelect: (taskId: string) => void;
  onStatusChange: (taskId: string, status: FleetTaskStatus) => void;
  /** Column `+`: open the composer with this column's status pre-set. */
  onCreateTask?: (status: FleetTaskStatus) => void;
}) {
  // Which column is currently a valid drop target under the pointer. Held
  // here rather than per-column so leaving one column and entering the next
  // can't leave two highlighted at once.
  const [dragOverStatus, setDragOverStatus] = useState<FleetTaskStatus | null>(null);
  const [draggingTaskId, setDraggingTaskId] = useState<string | null>(null);

  // Counts come from the one shared helper (fleet-data.countTasksByStatus),
  // the same one the Overview tab's stat grid reads — so a column header and
  // the roll-up above it cannot disagree about what a status means.
  const counts = countTasksByStatus(tasks);

  // A column shows if and only if it has a task in it. No manual override in
  // either direction — see the file header for why that used to exist and
  // why it doesn't anymore.
  const visibleStatuses = FLEET_TASK_STATUSES.filter((s) => counts[s] > 0);

  // Parent lookup for the sub-task breadcrumb on a card. The board already
  // holds every task in the project and the backend only ever allows a parent
  // in the SAME project (migrations/add_task_parent.sql), so this resolves
  // locally — a sub-task card costs no extra read to name its parent.
  const tasksById = useMemo(() => new Map(tasks.map((t) => [t.id, t])), [tasks]);

  // MAN-145: seven columns at 292px each is wider than any board viewport,
  // so reaching the trailing columns (Blocked/In review/Done) depends on
  // scrolling sideways. A trackpad's two-finger swipe already does this for
  // free (.fleet-board is a plain overflow-x:auto box), but a PLAIN mouse
  // wheel has no built-in horizontal axis, and whether a browser remaps a
  // vertical wheel gesture onto a horizontal-only scroller is inconsistent
  // across engines — the one input path every pointer device shares (a
  // vertical wheel delta) should not silently do nothing here. This repoints
  // that delta onto scrollLeft by hand rather than trusting the remap.
  //
  // Left alone deliberately: a gesture that already carries a horizontal
  // component (deltaX !== 0 — a real trackpad pan) is native behaviour and
  // is not intercepted. A wheel that starts over a column with its own
  // vertical overflow (a tall stack of cards) is also left alone, so this
  // never steals a column's own scroll before the column has anywhere left
  // to go.
  const boardRef = useRef<HTMLDivElement | null>(null);
  useEffect(() => {
    const board = boardRef.current;
    if (!board) return;
    const onWheel = (e: WheelEvent) => {
      if (e.deltaX !== 0 || e.deltaY === 0) return;
      const columnBody = (e.target as HTMLElement | null)?.closest<HTMLElement>(".fleet-board-column-body");
      if (columnBody && columnBody.scrollHeight > columnBody.clientHeight) return;
      board.scrollLeft += e.deltaY;
      e.preventDefault();
    };
    board.addEventListener("wheel", onWheel, { passive: false });
    return () => board.removeEventListener("wheel", onWheel);
  }, []);

  function handleDrop(e: DragEvent<HTMLElement>, status: FleetTaskStatus) {
    e.preventDefault();
    setDragOverStatus(null);
    setDraggingTaskId(null);
    const taskId = e.dataTransfer.getData(DRAG_MIME);
    if (!taskId) return;
    const task = tasks.find((t) => t.id === taskId);
    // Dropping a card back into the column it came from is a no-op, not a
    // write — otherwise every mis-aimed drag bumps updated_at for nothing.
    if (!task || task.status === status) return;
    onStatusChange(taskId, status);
  }

  // Every column is empty — visibility is pure count > 0 now, so this can
  // only mean the project has zero tasks anywhere. A blank horizontal strip
  // would say nothing here; say it plainly instead.
  if (visibleStatuses.length === 0) {
    return (
      <div className="fleet-empty" style={{ marginTop: "var(--space-3)" }}>
        <div className="fleet-empty-icon">
          <TaskStatusIcon status="todo" size={20} />
        </div>
        <div className="fleet-empty-title">No tasks yet</div>
        {onCreateTask ? (
          <button
            type="button"
            className="fleet-btn fleet-btn--accent-fill"
            style={{ marginTop: "var(--space-3)" }}
            onClick={() => onCreateTask("backlog")}
          >
            Create the first one
          </button>
        ) : null}
      </div>
    );
  }

  return (
    <div className="fleet-board" role="list" aria-label="Task board" ref={boardRef}>
      {visibleStatuses.map((status) => {
        const label = taskStatusLabel(status);
        const columnTasks = tasks.filter((t) => t.status === status);
        return (
          <section
            key={status}
            className={`fleet-board-column${dragOverStatus === status ? " is-drop-target" : ""}`}
            role="listitem"
            aria-label={`${label}, ${counts[status]} ${counts[status] === 1 ? "task" : "tasks"}`}
            onDragOver={(e) => {
              // preventDefault is what MAKES an element a drop target in the
              // HTML5 DnD model — without it the drop event never fires.
              if (!e.dataTransfer.types.includes(DRAG_MIME)) return;
              e.preventDefault();
              e.dataTransfer.dropEffect = "move";
              if (dragOverStatus !== status) setDragOverStatus(status);
            }}
            onDragLeave={(e) => {
              // Only clear when the pointer has actually left this column's
              // box — dragging over a child card fires dragleave on the
              // column otherwise, and the highlight would strobe.
              if (e.currentTarget.contains(e.relatedTarget as Node | null)) return;
              setDragOverStatus((cur) => (cur === status ? null : cur));
            }}
            onDrop={(e) => handleDrop(e, status)}
          >
            <header className="fleet-board-column-header">
              <TaskStatusIcon status={status} size={14} />
              <span className="fleet-board-column-title">{label}</span>
              <span className="fleet-board-column-count">{counts[status]}</span>
              {onCreateTask ? (
                <button
                  type="button"
                  className="fleet-board-column-btn"
                  title={`New task in ${label}`}
                  aria-label={`New task in ${label}`}
                  onClick={() => onCreateTask(status)}
                >
                  <Plus size={14} strokeWidth={2} />
                </button>
              ) : null}
            </header>
            <div className="fleet-board-column-body">
              {columnTasks.length === 0 ? (
                // Nothing to say about an empty column that its own "0" hasn't
                // already said, so idle renders nothing at all. The body keeps
                // its height from `flex: 1`, so the drop target is unchanged.
                dragOverStatus === status ? (
                  <div className="fleet-board-column-empty" aria-hidden>
                    Drop here
                  </div>
                ) : null
              ) : (
                columnTasks.map((task, index) => (
                  <TaskCard
                    key={task.id}
                    task={task}
                    parentTask={task.parent_task_id ? tasksById.get(task.parent_task_id) || null : null}
                    agents={agents}
                    members={members}
                    index={index}
                    selected={selectedTaskId === task.id}
                    dragging={draggingTaskId === task.id}
                    href={taskHref?.(task.id)}
                    display={display}
                    onSelect={onSelect}
                    onStatusChange={onStatusChange}
                    onDragStateChange={setDraggingTaskId}
                  />
                ))
              )}
            </div>
          </section>
        );
      })}
    </div>
  );
}

function TaskCard({
  task,
  parentTask,
  agents,
  members,
  index,
  selected,
  dragging,
  href,
  display,
  onSelect,
  onStatusChange,
  onDragStateChange,
}: {
  task: FleetTask;
  /** The task this one is a sub-task of, already resolved by the board, or
   *  null on a top-level task. */
  parentTask?: FleetTask | null;
  agents: FleetAgent[];
  members?: WorkspaceMember[];
  index: number;
  selected: boolean;
  dragging: boolean;
  href?: string;
  display: TaskDisplayState;
  onSelect: (taskId: string) => void;
  onStatusChange: (taskId: string, status: FleetTaskStatus) => void;
  onDragStateChange: (taskId: string | null) => void;
}) {
  const assignee = agents.find((a) => a.agent_id === task.assignee_agent_id) || null;
  // The human half of MAN-64/MAN-70 -- only looked up when there is no
  // agent assignee, matching the backend's mutual-exclusivity guarantee.
  const assignedMember = !assignee && task.assignee_user_id
    ? (members || []).find((m) => m.user_id === task.assignee_user_id) || null
    : null;
  // Absent field → 0 ("no priority"), never a crash. See
  // task-status.taskPriority for why nothing reads task.priority raw; the
  // GLYPH for 0 is simply not rendered here (see the meta row below).
  const priority = taskPriority(task);

  // THE DATE, AND WHY IT NAMES ITSELF. The card used to print a bare "9h ago"
  // with nothing saying what happened 9h ago; two cards reading "Aug 1" and
  // "2d ago" looked like the same kind of fact and weren't. Both slots are now
  // prefixed with the word for what they are.
  //
  // The fallback is CREATED, not updated. `updated_at` on this backend only
  // moves on assign_task/update_task — a task can collect ten comments and a
  // full agent run without it budging (see fleet-data.FleetTask) — so
  // "Updated 9h ago" would be a confident statement of something we don't
  // actually know. When a task was FILED is a fact we do know, it never
  // changes under the reader, and it is what Linear's own card shows.
  //
  // Each half is gated by its own display toggle BEFORE the fallback runs, so
  // switching "Due date" off on a task that has one falls through to Created
  // rather than leaving the slot blank — the slot shows the best date the
  // reader still wants, or nothing.
  const due = display.due ? dueLabel(task.due_at) : "";
  const created =
    display.created && task.created_at
      ? formatDate(task.created_at, { month: "short", day: "numeric" })
      : "";
  const dateText = due ? `Due ${due}` : created ? `Created ${created}` : "";
  const dateTitle = due
    ? `Due ${formatDate(task.due_at as string, { dateStyle: "full" })}`
    : created
      ? `Created ${formatDateTime(task.created_at as string)}`
      : "";

  const assigneeNode = !display.assignee ? null : assignee ? (
    <span className="fleet-agent-avatar" title={assignee.label || "Unnamed agent"}>
      <AgentSigil seed={assignee.agent_id} size={12} />
    </span>
  ) : assignedMember ? (
    <MemberAvatar
      name={assignedMember.display_name || assignedMember.email}
      role={assignedMember.role}
      size="xs"
      tintIndex={index}
    />
  ) : null;

  return (
    <article
      className={`fleet-board-card${selected ? " is-selected" : ""}${dragging ? " is-dragging" : ""}`}
      tabIndex={0}
      role="button"
      aria-label={`${task.title || "Untitled task"} — open details`}
      data-tab-href={href}
      data-tab-title={task.title || "Untitled task"}
      draggable
      onDragStart={(e) => {
        e.dataTransfer.setData(DRAG_MIME, task.id);
        e.dataTransfer.effectAllowed = "move";
        onDragStateChange(task.id);
      }}
      onDragEnd={() => onDragStateChange(null)}
      onClick={(e) => {
        // A modifier click is the tab layer's ("open in a background tab"),
        // never this card's. FleetTabs already stops such a click before React
        // sees it; this is the second, local guard so the card cannot navigate
        // the current view even if that interception ever stops firing.
        if (e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
        onSelect(task.id);
      }}
      onKeyDown={(e) => {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          onSelect(task.id);
        }
      }}
    >
      {/* A sub-task's parent, above the title — Linear's card order, and the
          only thing that stops a sub-task card being indistinguishable from a
          top-level one on a board that lists both. Text, not a link: the card
          is a drag source and a single click target, and a nested link inside
          it would be a second, competing destination in a 292px box. The
          parent is one click away from the task page's own breadcrumb, which
          IS a link. Renders only when this task has a parent. */}
      {parentTask ? (
        <div
          className="fleet-board-card-parent"
          title={`Sub-task of ${parentTask.title || "Untitled task"}`}
        >
          <CornerDownRight size={11} strokeWidth={2} />
          <span className="fleet-board-card-parent-title">
            {parentTask.title || "Untitled task"}
          </span>
        </div>
      ) : null}

      {/* Title first, meta underneath — Linear's card order. The old version
          led with a metadata row, which pushed the one thing you actually
          scan for down a line on every card. */}
      <div className="fleet-board-card-title">{task.title || "Untitled task"}</div>

      {/* Labels get their OWN line rather than a slot in the meta row below.
          The meta row is already ring + glyph + id + date + avatar inside a
          292px column; a chip squeezed in there would be ellipsed to two
          characters and tell you nothing. This line only exists on cards that
          actually have labels, so the density the card header brags about is
          unchanged for every card that doesn't.
          CAP: 2, then "+N" (TaskLabelChips). Two short chips is what fits the
          column at this font size without wrapping, and a card is a scannable
          handle — the full set is one click away on the task page. */}
      {display.labels ? (
        <TaskLabelChips labels={task.labels} max={2} className="fleet-board-card-labels" />
      ) : null}

      <div className="fleet-board-card-meta">
        {/* Keyboard/AT path for the same move a drag performs: a real native
            <select> laid transparently over the status ring, so the ring is
            what you see and the platform's listbox is what you operate.
            Click and keydown are stopped so using it never also opens the
            detail panel behind it. */}
        <span className="fleet-board-card-statuspick">
          <TaskStatusIcon status={task.status} size={14} />
          <select
            className="fleet-board-card-status"
            value={task.status}
            aria-label={`Status of ${task.title || "Untitled task"}`}
            title={taskStatusLabel(task.status)}
            onClick={(e) => e.stopPropagation()}
            onKeyDown={(e) => e.stopPropagation()}
            onChange={(e) => {
              const next = e.currentTarget.value as FleetTaskStatus;
              if (next !== task.status) onStatusChange(task.id, next);
            }}
          >
            {FLEET_TASK_STATUSES.map((s) => (
              <option key={s} value={s}>
                {taskStatusLabel(s)}
              </option>
            ))}
          </select>
        </span>

        {/* MAN-294: the status ring above still reads `in_progress` — that IS
            the real status, unchanged. This is the compact qualifier for
            "but the agent hasn't actually woken up yet", right beside the
            ring it qualifies. Renders nothing on every task that isn't
            deferred, which is the common case. */}
        <TaskWakeDeferralIcon task={task} size={13} />

        {/* No priority → no glyph. A card is a free-floating box; there is no
            column here for an empty slot to protect, so the three grey dashes
            this used to draw on every untriaged card were a glyph whose only
            message was that it had nothing to say. See task-status.tsx for
            which surfaces DO still want the placeholder and why. */}
        {display.priority && priority !== 0 ? (
          <span className="fleet-board-card-prio" title={TASK_PRIORITY_LABELS[priority]}>
            <TaskPriorityIcon priority={priority} size={14} />
          </span>
        ) : null}

        {display.id ? <span className="fleet-board-card-id">{taskShortId(task.id)}</span> : null}

        {/* "1/3" + a donut, on the tasks that actually have sub-tasks. The
            counts ride along on the row the board already fetched, so this
            costs no request. Renders nothing on a task with none. */}
        {display.subtasks ? (
          <TaskSubtaskProgress
            done={task.subtask_done_count}
            total={task.subtask_count}
            className="fleet-board-card-subtasks"
          />
        ) : null}

        {/* Date + assignee ride to the right as a GROUP, so the row still
            packs correctly when either is missing — a card with no due date,
            no created_at and nobody assigned simply ends after the id rather
            than trailing an em dash and a blank circle where information
            would have been. */}
        {dateText || assigneeNode ? (
          <span className="fleet-board-card-meta-right">
            {dateText ? (
              <span className="fleet-board-card-date" title={dateTitle}>
                {dateText}
              </span>
            ) : null}
            {assigneeNode}
          </span>
        ) : null}
      </div>
    </article>
  );
}
