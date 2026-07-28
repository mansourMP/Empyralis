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
 * PRESENTATION — Linear's board, not a card-in-a-tray kanban:
 *   · The columns have NO container. No background, no border, no rounded
 *     tray. A column is a header row and a stack of cards floating on the
 *     page; the only vertical separation is the gap between them. Drawing
 *     the tray is what made this read boxed-in and dated.
 *   · Status is a progressively-filled RING (task-status.TaskStatusIcon),
 *     not a flat dot — see that file for why.
 *   · The per-agent identity tint is still the shared TINTS/tintForAgent
 *     helper the Agents list and the flat task list use.
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
 * COLUMN HEADER CONTROLS (MAN-127 FIX 3) — `+` and `⋯`:
 *   · `+` opens the composer with THIS column's status already set, so a task
 *     filed in Todo starts as Todo. No second step, no dragging it over.
 *   · `⋯` carries exactly two entries, and the shortness is the point:
 *       – "Hide column" — seven statuses is a lot of horizontal board, and a
 *         team that never uses `in_review` should be able to stop looking at
 *         it. Real, used, reversible.
 *       – "Show all columns" — only present while something IS hidden. Hiding
 *         without a way back from the same menu is a trap.
 *     Linear's menu also offers "Select all", which we deliberately do NOT
 *     copy: the board has no multi-select and no bulk action to perform on a
 *     selection, so the entry would select things and then offer nothing to do
 *     with them. Fewer entries that all work beats a longer menu that looks
 *     like Linear's.
 *
 * Hidden columns persist PER WORKSPACE (`fleet:board-hidden:v1:<workspaceId>`,
 * the same `fleet:*` localStorage convention the rail width/collapse and the
 * tab strip already use). Per workspace rather than per project because
 * "I never use in_review" is a statement about how a TEAM works, not about
 * one project; and it is a view preference, so it stays client-side.
 *
 * Desktop-first by explicit scope (1440x900). Below the board's own
 * breakpoint the columns simply keep scrolling horizontally rather than
 * re-laying out — a real mobile board is separate, later work.
 */

import { useCallback, useEffect, useRef, useState, type CSSProperties, type DragEvent } from "react";
import { MoreHorizontal, Plus } from "lucide-react";

import { dueLabel } from "./TasksList";
import { TINTS, tintForAgent, timeAgo } from "./fleet-presentation";
import { AgentSigil } from "./fleet-indicators";
import { TaskStatusIcon, TaskPriorityIcon, taskStatusLabel, taskPriority, taskShortId, TASK_PRIORITY_LABELS } from "./task-status";
import { FLEET_TASK_STATUSES, countTasksByStatus, type FleetAgent, type FleetTask, type FleetTaskStatus } from "./fleet-data";

/** The dataTransfer type the card writes and the column reads. Namespaced so
 *  a drop of anything else (a file, a text selection, a card from some other
 *  future board) is ignored rather than half-handled. */
const DRAG_MIME = "application/x-fleet-task-id";

// taskShortId moved to ./task-status (the tab strip and the routed task page
// both need it and neither should pull the whole board in). Re-exported so
// existing importers keep working.
export { taskShortId };

/** localStorage key for this workspace's hidden columns — `fleet:*`, versioned,
 *  workspace-scoped, exactly like fleet-tabs' own key. */
function hiddenColumnsKey(workspaceId: string): string {
  return `fleet:board-hidden:v1:${workspaceId}`;
}

function readHiddenColumns(workspaceId: string): FleetTaskStatus[] {
  try {
    const raw = window.localStorage.getItem(hiddenColumnsKey(workspaceId));
    if (!raw) return [];
    const parsed = JSON.parse(raw);
    if (!Array.isArray(parsed)) return [];
    // Filtered against the live vocabulary, so a status removed from the
    // product can't keep hiding a column that no longer exists.
    return parsed.filter((s): s is FleetTaskStatus => FLEET_TASK_STATUSES.includes(s as FleetTaskStatus));
  } catch {
    return [];
  }
}

export function TasksBoard({
  workspaceId,
  tasks,
  agents,
  selectedTaskId,
  taskHref,
  onSelect,
  onStatusChange,
  onCreateTask,
}: {
  /** Scopes the hidden-column preference. Absent → nothing persists and the
   *  hide control is a within-session toggle only. */
  workspaceId?: string;
  tasks: FleetTask[];
  /** Agents in this project — the only valid assignees. */
  agents: FleetAgent[];
  selectedTaskId?: string | null;
  /** The task's real route. Stamped on each card as `data-tab-href`, which is
   *  what makes ⌘/Ctrl+click and middle-click open it in a background content
   *  tab (see FleetTabs). A card is a drag source, so it stays a <div role=
   *  "button"> rather than becoming an <a> — the attribute is how it opts into
   *  link-like modifier gestures without giving up drag-and-drop. */
  taskHref?: (taskId: string) => string;
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
  // Hydrated in an effect, never during render, so the server's markup and the
  // client's first paint agree (same discipline as useFleetPreferences).
  const [hidden, setHidden] = useState<FleetTaskStatus[]>([]);
  useEffect(() => {
    if (workspaceId) setHidden(readHiddenColumns(workspaceId));
  }, [workspaceId]);

  const writeHidden = useCallback(
    (next: FleetTaskStatus[]) => {
      setHidden(next);
      if (!workspaceId) return;
      try {
        window.localStorage.setItem(hiddenColumnsKey(workspaceId), JSON.stringify(next));
      } catch {
        /* localStorage unavailable — the toggle still works for this session */
      }
    },
    [workspaceId],
  );

  const visibleStatuses = FLEET_TASK_STATUSES.filter((s) => !hidden.includes(s));

  // Counts come from the one shared helper (fleet-data.countTasksByStatus),
  // the same one the Overview tab's stat grid reads — so a column header and
  // the roll-up above it cannot disagree about what a status means.
  const counts = countTasksByStatus(tasks);

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

  return (
    <div className="fleet-board" role="list" aria-label="Task board">
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
              <ColumnMenu
                label={label}
                canHide={visibleStatuses.length > 1}
                hiddenCount={hidden.length}
                onHide={() => writeHidden([...hidden, status])}
                onShowAll={() => writeHidden([])}
              />
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
                <div className="fleet-board-column-empty" aria-hidden>
                  {dragOverStatus === status ? "Drop here" : "—"}
                </div>
              ) : (
                columnTasks.map((task, index) => (
                  <TaskCard
                    key={task.id}
                    task={task}
                    agents={agents}
                    index={index}
                    selected={selectedTaskId === task.id}
                    dragging={draggingTaskId === task.id}
                    href={taskHref?.(task.id)}
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

/**
 * The `⋯` column menu. Two entries, both of which do something (see the file
 * header for what was left out and why). Same dismissal contract as every
 * other menu in the fleet surface: click anywhere else, or Esc, closes it.
 */
function ColumnMenu({
  label,
  canHide,
  hiddenCount,
  onHide,
  onShowAll,
}: {
  label: string;
  /** False on the last visible column — hiding it would leave an empty board
   *  with no column header left to un-hide from. */
  canHide: boolean;
  hiddenCount: number;
  onHide: () => void;
  onShowAll: () => void;
}) {
  const [open, setOpen] = useState(false);
  const wrapRef = useRef<HTMLSpanElement | null>(null);

  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      if (!wrapRef.current?.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false);
    };
    document.addEventListener("mousedown", onDown, true);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDown, true);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  return (
    <span className="fleet-board-column-menuwrap" ref={wrapRef}>
      <button
        type="button"
        className={`fleet-board-column-btn${open ? " is-open" : ""}`}
        aria-haspopup="menu"
        aria-expanded={open}
        aria-label={`${label} column options`}
        title={`${label} column options`}
        onClick={() => setOpen((v) => !v)}
      >
        <MoreHorizontal size={14} strokeWidth={2} />
      </button>
      {open ? (
        <div className="fleet-board-column-menu" role="menu" aria-label={`${label} column`}>
          <button
            type="button"
            role="menuitem"
            className="fleet-composer-pop-item"
            disabled={!canHide}
            onClick={() => {
              onHide();
              setOpen(false);
            }}
          >
            <span className="fleet-composer-pop-label">Hide column</span>
          </button>
          {hiddenCount > 0 ? (
            <button
              type="button"
              role="menuitem"
              className="fleet-composer-pop-item"
              onClick={() => {
                onShowAll();
                setOpen(false);
              }}
            >
              <span className="fleet-composer-pop-label">
                Show all columns ({hiddenCount} hidden)
              </span>
            </button>
          ) : null}
        </div>
      ) : null}
    </span>
  );
}

function TaskCard({
  task,
  agents,
  index,
  selected,
  dragging,
  href,
  onSelect,
  onStatusChange,
  onDragStateChange,
}: {
  task: FleetTask;
  agents: FleetAgent[];
  index: number;
  selected: boolean;
  dragging: boolean;
  href?: string;
  onSelect: (taskId: string) => void;
  onStatusChange: (taskId: string, status: FleetTaskStatus) => void;
  onDragStateChange: (taskId: string | null) => void;
}) {
  const assignee = agents.find((a) => a.agent_id === task.assignee_agent_id) || null;
  const due = dueLabel(task.due_at);
  const updated = timeAgo(task.updated_at || task.created_at);
  // Absent field → 0 ("no priority"), never a crash and never a blank slot.
  // See task-status.taskPriority for why nothing reads task.priority raw.
  const priority = taskPriority(task);

  const tint = assignee ? TINTS[tintForAgent(assignee, index)] : null;
  const avatarStyle = (tint ? { "--tile-bg": tint.bg, "--tile-fg": tint.fg } : {}) as CSSProperties;

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
      {/* Title first, meta underneath — Linear's card order. The old version
          led with a metadata row, which pushed the one thing you actually
          scan for down a line on every card. */}
      <div className="fleet-board-card-title">{task.title || "Untitled task"}</div>

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

        <span className="fleet-board-card-prio" title={TASK_PRIORITY_LABELS[priority]}>
          <TaskPriorityIcon priority={priority} size={14} />
        </span>

        <span className="fleet-board-card-id">{taskShortId(task.id)}</span>

        {/* Due date when there is one, last-touched otherwise — two different
            facts in one slot, so the tooltip says which one you're reading
            rather than leaving "Aug 1" and "2d ago" to look interchangeable. */}
        <span
          className={`fleet-board-card-date${due ? "" : " fleet-cell-muted"}`}
          title={due ? "Due date" : updated ? "Last updated" : "No due date"}
        >
          {due || updated || "—"}
        </span>

        {assignee ? (
          <span className="fleet-agent-avatar" style={avatarStyle} title={assignee.label || "Unnamed agent"}>
            <AgentSigil seed={assignee.agent_id} size={12} />
          </span>
        ) : (
          <span className="fleet-board-card-unassigned" title="Unassigned" aria-hidden />
        )}
      </div>
    </article>
  );
}
