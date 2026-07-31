"use client";

/**
 * The grouped list — Linear's Issues page, and the third shape of the Tasks
 * view beside the Board and the flat List.
 *
 * WHY A THIRD VIEW, when two already exist. They answer different questions:
 *   · Board — "what is the shape of the work right now". You watch it. It is
 *     spatial; seven columns side by side; you read it without reading it.
 *   · List — "show me every task's fields at once", one flat table, sorted by
 *     nothing in particular. It is a spreadsheet.
 *   · Grouped (this) — "let me work THROUGH this". Statuses are headings, not
 *     columns, so the whole backlog is one vertical scroll instead of seven
 *     horizontal ones; you can collapse the statuses you are not touching
 *     today; and the tab strip narrows the seven states down to the ones that
 *     are actually live. This is the view you keep open while grinding.
 *
 * TABS. Active / Backlog / All, derived from FLEET_TASK_STATUSES rather than
 * re-listed — Active is "everything that is neither parked before the work
 * (backlog) nor finished (done)", which stays true if an eighth status is
 * ever added, where a hand-copied list would quietly stop including it.
 *
 * ZERO-COUNT SECTIONS are hidden outside "All". A working view should not
 * make you scroll past four empty headings to reach the twelve things you
 * have to do. "All" keeps them, because "All" is the one place you go to
 * confirm a status is genuinely empty rather than filtered away.
 *
 * COLLAPSE STATE persists per workspace (`fleet:glist-collapsed:v1:<id>`,
 * the same `fleet:*` localStorage convention as the board's hidden columns
 * and the rail's width). Per workspace, not per project, for the same reason:
 * "I never look at Done" is a statement about how a team works.
 *
 * MOTION. The chevron rotates over --dur-2. THE BODY DOES NOT ANIMATE — it
 * unmounts. Animating max-height is a layout-triggering property (reflow
 * every frame, not just composite) and is exactly what fleet-theme.css's
 * .fleet-rail-section-items rule already documents removing; a section
 * holding forty rows would jank on every toggle. Unmounting is also why the
 * collapsed rows cost nothing to keep collapsed.
 */

import { useCallback, useEffect, useMemo, useState } from "react";
import { ChevronRight, Plus } from "lucide-react";

import { dueLabel } from "./TasksList";
import { timeAgo } from "./fleet-presentation";
import { AgentSigil } from "./fleet-indicators";
import { MemberAvatar } from "./MemberAvatarStack";
import type { WorkspaceMember } from "./members-data";
import { TaskLabelChips } from "./task-labels";
import {
  TaskStatusIcon,
  TaskPriorityIcon,
  taskStatusLabel,
  taskPriority,
  taskShortId,
  TASK_PRIORITY_LABELS,
} from "./task-status";
import {
  FLEET_TASK_STATUSES,
  countTasksByStatus,
  normalizeTaskStatus,
  type FleetAgent,
  type FleetTask,
  type FleetTaskStatus,
} from "./fleet-data";

type GroupTab = "active" | "backlog" | "all";

/** Everything that is neither parked before the work nor finished. Derived,
 *  not re-listed — see the file header. Resolves today to
 *  todo · in_progress · awaiting_input · blocked · in_review, in board order. */
const ACTIVE_STATUSES: FleetTaskStatus[] = FLEET_TASK_STATUSES.filter(
  (s) => s !== "backlog" && s !== "done",
);

const TABS: { key: GroupTab; label: string; statuses: FleetTaskStatus[] }[] = [
  { key: "active", label: "Active", statuses: ACTIVE_STATUSES },
  { key: "backlog", label: "Backlog", statuses: ["backlog"] },
  { key: "all", label: "All", statuses: FLEET_TASK_STATUSES },
];

/** localStorage key for this workspace's collapsed sections — `fleet:*`,
 *  versioned, workspace-scoped, exactly like the board's hidden columns. */
function collapsedKey(workspaceId: string): string {
  return `fleet:glist-collapsed:v1:${workspaceId}`;
}

function readCollapsed(workspaceId: string): FleetTaskStatus[] {
  try {
    const raw = window.localStorage.getItem(collapsedKey(workspaceId));
    if (!raw) return [];
    const parsed = JSON.parse(raw);
    if (!Array.isArray(parsed)) return [];
    // Filtered against the live vocabulary, so a status removed from the
    // product can't keep a section that no longer exists folded shut.
    return parsed.filter((s): s is FleetTaskStatus =>
      FLEET_TASK_STATUSES.includes(s as FleetTaskStatus),
    );
  } catch {
    return [];
  }
}

export function TasksGroupedList({
  workspaceId,
  tasks,
  agents,
  members,
  taskHref,
  onSelect,
  onStatusChange,
  onCreateTask,
}: {
  /** Scopes the collapse preference. Absent → collapse is session-only. */
  workspaceId?: string;
  tasks: FleetTask[];
  /** Agents in this project — valid AGENT assignees. */
  agents: FleetAgent[];
  /** Workspace members (MAN-64/MAN-70) — resolves a HUMAN assignee's avatar
   *  on a row. Without this a human-assigned task would render as
   *  "unassigned" here, which is exactly the display bug this rollout
   *  closes. */
  members?: WorkspaceMember[];
  /** The task's real route, stamped on each row as `data-tab-href` so
   *  ⌘/Ctrl+click and middle-click open a background content tab (FleetTabs). */
  taskHref?: (taskId: string) => string;
  onSelect: (taskId: string) => void;
  /** Optional: makes the row's status ring a real control. Omit and the ring
   *  is a read-only glyph. */
  onStatusChange?: (taskId: string, status: FleetTaskStatus) => void;
  /** Section `+`: open the composer with that section's status pre-set — the
   *  same handler the board columns' `+` already uses. */
  onCreateTask?: (status: FleetTaskStatus) => void;
}) {
  const [tab, setTab] = useState<GroupTab>("active");
  // Hydrated in an effect, never during render, so the server's markup and
  // the client's first paint agree (same discipline as the board's hidden
  // columns and useFleetPreferences).
  const [collapsed, setCollapsed] = useState<FleetTaskStatus[]>([]);
  useEffect(() => {
    if (workspaceId) setCollapsed(readCollapsed(workspaceId));
  }, [workspaceId]);

  const toggleCollapsed = useCallback(
    (status: FleetTaskStatus) => {
      setCollapsed((cur) => {
        const next = cur.includes(status) ? cur.filter((s) => s !== status) : [...cur, status];
        if (workspaceId) {
          try {
            window.localStorage.setItem(collapsedKey(workspaceId), JSON.stringify(next));
          } catch {
            /* localStorage unavailable — the toggle still works this session */
          }
        }
        return next;
      });
    },
    [workspaceId],
  );

  // One shared helper for the counts (fleet-data.countTasksByStatus), the same
  // one the board headers and the Overview roll-up read, so a section heading
  // and a stat card cannot disagree about what a status means.
  const counts = useMemo(() => countTasksByStatus(tasks), [tasks]);
  const byStatus = useMemo(() => {
    const map = new Map<FleetTaskStatus, FleetTask[]>();
    for (const status of FLEET_TASK_STATUSES) map.set(status, []);
    // Bucketed through the SAME normalizer countTasksByStatus uses. Without
    // it a legacy `open` row would be counted in the Todo heading and then
    // land in no bucket at all — a section reading "3" over two rows.
    for (const task of tasks) map.get(normalizeTaskStatus(task.status))?.push(task);
    return map;
  }, [tasks]);

  const activeTab = TABS.find((t) => t.key === tab) || TABS[0];
  const sections = activeTab.statuses.filter(
    (status) => tab === "all" || counts[status] > 0,
  );
  const tabTotal = activeTab.statuses.reduce((sum, s) => sum + counts[s], 0);

  return (
    <div className="fleet-glist">
      {/* Underlined tabs, not another .fleet-segmented: a segmented control
          right below the Board/List/Grouped segmented control would read as
          two halves of one switch. These are a filter ON the view, one level
          down, and Linear draws exactly this distinction the same way. */}
      <div className="fleet-glist-tabs" role="tablist" aria-label="Task filter">
        {TABS.map((t) => {
          const total = t.statuses.reduce((sum, s) => sum + counts[s], 0);
          return (
            <button
              key={t.key}
              type="button"
              role="tab"
              aria-selected={tab === t.key}
              className={`fleet-glist-tab${tab === t.key ? " is-active" : ""}`}
              onClick={() => setTab(t.key)}
            >
              {t.label}
              <span className="fleet-glist-tab-count">{total}</span>
            </button>
          );
        })}
      </div>

      {tabTotal === 0 ? (
        <div className="fleet-glist-empty">
          {tab === "active"
            ? "Nothing active. Everything here is either in the backlog or done."
            : tab === "backlog"
              ? "The backlog is empty."
              : "No tasks in this project."}
        </div>
      ) : (
        sections.map((status) => {
          const label = taskStatusLabel(status);
          const rows = byStatus.get(status) || [];
          const isCollapsed = collapsed.includes(status);
          return (
            <section key={status} className="fleet-glist-section" aria-label={label}>
              <header className="fleet-glist-header">
                <button
                  type="button"
                  className="fleet-glist-header-btn"
                  aria-expanded={!isCollapsed}
                  onClick={() => toggleCollapsed(status)}
                >
                  {/* Rotation is the whole animation, and it is a transform —
                      the one property this surface lets motion touch. */}
                  <ChevronRight size={13} strokeWidth={2.25} className="fleet-glist-chevron" />
                  <TaskStatusIcon status={status} size={14} />
                  <span className="fleet-glist-header-title">{label}</span>
                  <span className="fleet-glist-header-count">{counts[status]}</span>
                </button>
                <span className="fleet-glist-header-spacer" />
                {onCreateTask ? (
                  <button
                    type="button"
                    className="fleet-glist-header-add"
                    title={`New task in ${label}`}
                    aria-label={`New task in ${label}`}
                    onClick={() => onCreateTask(status)}
                  >
                    <Plus size={14} strokeWidth={2} />
                  </button>
                ) : null}
              </header>

              {/* Unmounted, not hidden — see the file header on why nothing
                  here animates height. */}
              {isCollapsed ? null : (
                <div className="fleet-glist-rows">
                  {rows.length === 0 ? (
                    <div className="fleet-glist-section-empty">No tasks in {label.toLowerCase()}.</div>
                  ) : (
                    rows.map((task, index) => (
                      <GroupedRow
                        key={task.id}
                        task={task}
                        agents={agents}
                        members={members}
                        index={index}
                        href={taskHref?.(task.id)}
                        onSelect={onSelect}
                        onStatusChange={onStatusChange}
                      />
                    ))
                  )}
                </div>
              )}
            </section>
          );
        })
      )}
    </div>
  );
}

/**
 * One task, one line. EMPHASIS DISCIPLINE, the same rule the flat list and the
 * Projects table document: exactly one cell carries full emphasis — the title.
 * Everything else is muted or secondary, and the only colour on the row is the
 * status ring, the priority glyph's urgent tile, and the label dots — all three
 * of which are information rather than decoration (fleet-theme.css:12).
 */
function GroupedRow({
  task,
  agents,
  members,
  index,
  href,
  onSelect,
  onStatusChange,
}: {
  task: FleetTask;
  agents: FleetAgent[];
  members?: WorkspaceMember[];
  index: number;
  href?: string;
  onSelect: (taskId: string) => void;
  onStatusChange?: (taskId: string, status: FleetTaskStatus) => void;
}) {
  const priority = taskPriority(task);
  const assignee = agents.find((a) => a.agent_id === task.assignee_agent_id) || null;
  const assignedMember = !assignee && task.assignee_user_id
    ? (members || []).find((m) => m.user_id === task.assignee_user_id) || null
    : null;
  const due = dueLabel(task.due_at);
  const updated = timeAgo(task.updated_at || task.created_at);

  return (
    <div
      className="fleet-glist-row"
      role="button"
      tabIndex={0}
      aria-label={`${task.title || "Untitled task"} — open details`}
      data-tab-href={href}
      data-tab-title={task.title || "Untitled task"}
      onClick={(e) => {
        // A modifier click belongs to the tab layer ("open in a background
        // tab"), never to this row — same guard the board card carries.
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
      <span className="fleet-glist-cell-prio" title={TASK_PRIORITY_LABELS[priority]}>
        <TaskPriorityIcon priority={priority} size={14} />
      </span>

      <span className="fleet-glist-cell-id">{taskShortId(task.id)}</span>

      {/* The status ring is the same transparent-native-<select>-over-a-glyph
          control the board card uses, reusing its CSS verbatim: HTML5 drag has
          no keyboard equivalent and neither does a list, so this is how a task
          moves forward from the view you work through a backlog in. */}
      {onStatusChange ? (
        <span className="fleet-board-card-statuspick fleet-glist-cell-status">
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
      ) : (
        <span className="fleet-glist-cell-status" title={taskStatusLabel(task.status)}>
          <TaskStatusIcon status={task.status} size={14} />
        </span>
      )}

      {/* Title and labels share ONE grid track on purpose. As their own
          `auto` track the labels would be a different width on every row —
          each row is its own grid — so Due/Updated/Assignee would zig-zag
          down the page instead of lining up. In one track the title takes the
          slack and ellipses first (min-width:0), the chips never shrink, and
          the four right-hand columns stay dead straight.
          Cap 3 + "+N" here, against 2 on a board card: the row is several
          times wider. */}
      <span className="fleet-glist-cell-title">
        <span className="fleet-glist-cell-name">{task.title || "Untitled task"}</span>
        <TaskLabelChips labels={task.labels} max={3} />
      </span>

      <span className={`fleet-glist-cell-due${due ? "" : " fleet-cell-muted"}`} title={due ? "Due date" : "No due date"}>
        {due || "—"}
      </span>
      <span className="fleet-glist-cell-updated" title="Last updated">
        {updated || "—"}
      </span>

      {assignee ? (
        <span className="fleet-agent-avatar fleet-glist-cell-assignee" title={assignee.label || "Unnamed agent"}>
          <AgentSigil seed={assignee.agent_id} size={12} />
        </span>
      ) : assignedMember ? (
        <span className="fleet-glist-cell-assignee">
          <MemberAvatar
            name={assignedMember.display_name || assignedMember.email}
            role={assignedMember.role}
            size="xs"
            tintIndex={index}
          />
        </span>
      ) : (
        // An empty slot rather than a dash: a column of "—" where the avatars
        // aren't is noise, and "unassigned" reads fine from the absence.
        <span className="fleet-glist-cell-assignee fleet-glist-cell-unassigned" title="Unassigned" aria-hidden />
      )}
    </div>
  );
}
