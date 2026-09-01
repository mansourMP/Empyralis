"use client";

/**
 * The project's shared task board — the one surface where a human and the
 * agents in a project work off the same list.
 *
 * Structure deliberately mirrors AgentsList: same grid idiom, same header,
 * same hover/focus behaviour, same full-replacement mobile block. The only
 * new CSS is the three-rule structural clone (.fleet-tasks-list / -header /
 * .fleet-task-row) that exists solely because the column count differs.
 *
 * Emphasis discipline (same rule the Projects table documents): exactly ONE
 * column carries full emphasis — Task — and Assignee/Due/Updated all drop to
 * fleet-cell-secondary, falling further to fleet-cell-muted when empty.
 * Status is deliberately exempt: status colour is information, not
 * decoration (fleet-theme.css:12-13), the same exemption the Agents table's
 * own Status column already has.
 *
 * DISPLAY PROPERTIES. Every cell except the title is switchable from the
 * view-options popover (task-view-options.ts). A switched-off column loses
 * its grid TRACK as well as its contents — `--fleet-list-grid` is rebuilt
 * from whatever survives — so hiding Due doesn't leave a 96px hole where Due
 * used to be. The title track is not offered: a row with no title is not a
 * row.
 */

import { useState, type CSSProperties } from "react";

import { CopyLinkButton } from "@/lib/ui/CopyLinkButton";
import { formatDueDate, timeAgo } from "./fleet-presentation";
import { AgentSigil } from "./fleet-indicators";
import { MemberAvatar } from "./MemberAvatarStack";
import type { WorkspaceMember } from "./members-data";
import { TaskStatusChip, TaskPriorityIcon, TaskWakeDeferralIcon, taskPriority, taskStatusLabel, TASK_PRIORITY_LABELS } from "./task-status";
import { TaskLabelChips } from "./task-labels";
import {
  assigneeOptionValue,
  parseAssigneeOptionValue,
  FLEET_TASK_STATUSES,
  type FleetAgent,
  type FleetTask,
  type FleetTaskStatus,
  type TaskAssigneeSelection,
} from "./fleet-data";
import { DEFAULT_TASK_VIEW_OPTIONS, type TaskDisplayState } from "./task-view-options";

/* Status presentation moved WHOLESALE to ./task-status (taskStatusLabel /
   TaskStatusIcon / TaskStatusChip). The map that used to live here borrowed
   the AGENT-health tone vocabulary (AgentStatusTone), and that borrowing was
   the bug: seven task states do not fit eight health tones, so
   `awaiting_input` and `in_review` both landed on `degraded` and two of the
   seven board columns rendered identical amber. Tasks now own a seven-colour
   ramp plus Linear's progressive ring. */

export function dueLabel(dueAt: string | null | undefined): string {
  const raw = String(dueAt || "").trim();
  if (!raw) return "";
  const d = new Date(raw);
  if (Number.isNaN(d.getTime())) return "";
  // due_at is a date-only field stamped midnight UTC (see formatDueDate's
  // own doc in fleet-presentation.ts) — formatting it in the viewer's local
  // timezone (the old `undefined` locale/no timeZone override here) rolls
  // the calendar day back by one for anyone west of UTC.
  return formatDueDate(d, { month: "short", day: "numeric" });
}

/** The width each optional column claims when it is on. The title track is
 *  the flexible one and is never optional, so it stays out of this map. */
const COLUMN_TRACKS: { key: "assignee" | "due" | "updated" | "status"; track: string; head: string; right?: boolean }[] = [
  { key: "assignee", track: "160px", head: "Assignee" },
  { key: "due", track: "96px", head: "Due", right: true },
  { key: "updated", track: "96px", head: "Updated", right: true },
  { key: "status", track: "132px", head: "Status" },
];

export function TasksList({
  tasks,
  agents,
  members,
  display = DEFAULT_TASK_VIEW_OPTIONS.display,
  hrefFor,
  onAssign,
  onSelect,
  onStatusChange,
}: {
  tasks: FleetTask[];
  /** Agents in this project — valid AGENT assignees. */
  agents: FleetAgent[];
  /** Workspace members (MAN-64/MAN-70) — valid HUMAN assignees. Absent →
   *  the inline picker offers agents only. */
  members?: WorkspaceMember[];
  /** View-options "Display properties". Defaults to everything on, which is
   *  the table this file drew before the popover existed. */
  display?: TaskDisplayState;
  /** The task's own route (`{projectHref}/tasks/{id}`) — same prop name and
   *  shape as DocumentsList's `hrefFor`. Makes the row a real `<a href>`
   *  instead of an onClick div, per CLAUDE.md: "Primary navigation is real
   *  links, so cmd-click and middle-click work." */
  hrefFor: (taskId: string) => string;
  onAssign: (taskId: string, selection: TaskAssigneeSelection) => void;
  onSelect?: (taskId: string) => void;
  /** Optional: makes the Status cell a real control instead of a read-only
   *  chip — same native-<select>-over-glyph technique TasksBoard's card and
   *  TasksGroupedList's row already use (`.fleet-board-card-statuspick`,
   *  reused verbatim here). Board and Grouped List have carried this since
   *  they were built; the flat list never did, which meant the one Task view
   *  with no grouping applied was also the one view where changing a status
   *  required opening the task first — an inconsistency across three renders
   *  of the identical data, not a deliberate omission. */
  onStatusChange?: (taskId: string, status: FleetTaskStatus) => void;
}) {
  const columns = COLUMN_TRACKS.filter((c) => display[c.key]);
  // Trailing 32px track for the Copy link button — same fixed, always-on
  // track .fleet-projects-list's own grid carries for its CopyLinkButton
  // (fleet-theme.css), not a toggleable display property: it's a permanent
  // page control, not a data column.
  const grid = ["minmax(260px, 1fr)", ...columns.map((c) => c.track), "32px"].join(" ");

  return (
    <div className="fleet-tasks-list" style={{ "--fleet-list-grid": grid } as CSSProperties}>
      <div className="fleet-tasks-list-header" role="row">
        <span>Task</span>
        {columns.map((c) => (
          <span key={c.key} className={c.right ? "is-right" : undefined}>
            {c.head}
          </span>
        ))}
        <span />
      </div>
      {tasks.map((task, index) => (
        <TaskRow
          key={task.id}
          task={task}
          agents={agents}
          members={members}
          index={index}
          display={display}
          href={hrefFor(task.id)}
          onAssign={onAssign}
          onSelect={onSelect}
          onStatusChange={onStatusChange}
        />
      ))}
    </div>
  );
}

function TaskRow({
  task,
  agents,
  members,
  index,
  display,
  href,
  onAssign,
  onSelect,
  onStatusChange,
}: {
  task: FleetTask;
  agents: FleetAgent[];
  members?: WorkspaceMember[];
  index: number;
  display: TaskDisplayState;
  href: string;
  onAssign: (taskId: string, selection: TaskAssigneeSelection) => void;
  onSelect?: (taskId: string) => void;
  onStatusChange?: (taskId: string, status: FleetTaskStatus) => void;
}) {
  const [assigning, setAssigning] = useState(false);
  const priority = taskPriority(task);
  const assignee = agents.find((a) => a.agent_id === task.assignee_agent_id) || null;
  const assignedMember = !assignee && task.assignee_user_id
    ? (members || []).find((m) => m.user_id === task.assignee_user_id) || null
    : null;
  const due = dueLabel(task.due_at);
  // `updated_at || created_at` — the same fallback TasksGroupedList's own
  // Updated cell uses. This column read `created_at` outright until
  // 2026-08-01, so a row that HAD been reassigned still reported when it was
  // filed under a heading saying "Updated". A column whose header names one
  // field and whose body prints another is a lie the reader has no way to
  // catch; on this backend updated_at only moves on assign/update (see
  // fleet-data.FleetTask), so falling back to created_at is what "never
  // touched since it was filed" honestly looks like.
  const updated = timeAgo(task.updated_at || task.created_at);

  const currentAssigneeValue = assignee
    ? assigneeOptionValue({ kind: "agent", id: assignee.agent_id })
    : task.assignee_user_id
      ? assigneeOptionValue({ kind: "user", id: task.assignee_user_id })
      : "";

  const assigneeCell = assigning ? (
    <select
      className="fleet-wizard-input"
      autoFocus
      defaultValue={currentAssigneeValue}
      onBlur={() => setAssigning(false)}
      onChange={(e) => {
        const next = parseAssigneeOptionValue(e.target.value);
        setAssigning(false);
        if (next && assigneeOptionValue(next) !== currentAssigneeValue) onAssign(task.id, next);
      }}
      // The row is a real <a> now (see TaskRow below): stopPropagation alone
      // no longer stops the click from following the link, since link-
      // following is resolved from the nearest <a> ancestor independent of
      // JS bubbling. preventDefault is what actually cancels it.
      onClick={(e) => {
        e.preventDefault();
        e.stopPropagation();
      }}
      style={{ height: 28, padding: "0 6px", fontSize: 12 }}
    >
      <option value="">Unassigned</option>
      {agents.length > 0 ? (
        <optgroup label="Agents">
          {agents.map((a) => (
            <option key={a.agent_id} value={assigneeOptionValue({ kind: "agent", id: a.agent_id })}>
              {a.label || "Unnamed agent"}
            </option>
          ))}
        </optgroup>
      ) : null}
      {(members || []).length > 0 ? (
        <optgroup label="People">
          {(members || []).map((m) => (
            <option key={m.user_id} value={assigneeOptionValue({ kind: "user", id: m.user_id })}>
              {m.display_name || m.email}
            </option>
          ))}
        </optgroup>
      ) : null}
    </select>
  ) : (
    <button
      type="button"
      className="fleet-task-assignee-btn"
      onClick={(e) => {
        // Same reasoning as the <select>'s onClick above: preventDefault is
        // what stops the enclosing <a> from navigating, not stopPropagation.
        e.preventDefault();
        e.stopPropagation();
        setAssigning(true);
      }}
    >
      {assignee ? (
        <>
          <span className="fleet-agent-avatar">
            <AgentSigil seed={assignee.agent_id} size={16} />
          </span>
          <span className="fleet-cell-secondary">{assignee.label || "Unnamed agent"}</span>
        </>
      ) : assignedMember ? (
        <>
          <MemberAvatar
            name={assignedMember.display_name || assignedMember.email}
            role={assignedMember.role}
            size="xs"
            tintIndex={index}
          />
          <span className="fleet-cell-secondary">{assignedMember.display_name || assignedMember.email}</span>
        </>
      ) : (
        <span className="fleet-cell-muted">Unassigned</span>
      )}
    </button>
  );

  return (
    // A real link, same pattern DocumentsList already uses for this exact
    // class (see that file's header) — cmd-click/middle-click/hover-URL/
    // open-in-new-tab all worked there and were dead here, on the div/
    // role="button" version this used to be. `onSelect` is optional on this
    // component; when the caller doesn't pass one, a plain click still works
    // via the browser's own navigation to `href` rather than doing nothing.
    <a
      href={href}
      className="fleet-task-row"
      role="row"
      onClick={(e) => {
        if (e.metaKey || e.ctrlKey || e.shiftKey || e.altKey || e.button !== 0) return;
        if (!onSelect) return;
        e.preventDefault();
        onSelect(task.id);
      }}
      onKeyDown={(e) => {
        if (e.key === "Enter" || e.key === " ") {
          if (!onSelect) return;
          e.preventDefault();
          onSelect(task.id);
        }
      }}
    >
      {/* Desktop cells. Each optional cell is omitted, not blanked — the
          header dropped its track too, so an emitted-but-empty <span> would
          push every following cell one column left. */}
      <span className="fleet-agent-cell-agent-text fleet-task-cell-title">
        <span className="fleet-task-cell-titleline">
          {/* Same glyph as the board card, so a task reads the same in both
              layouts. Absent backend field degrades to "no priority". */}
          {display.priority ? (
            <span className="fleet-task-cell-prio" title={TASK_PRIORITY_LABELS[priority]}>
              <TaskPriorityIcon priority={priority} size={14} />
            </span>
          ) : null}
          <span className="fleet-agent-name">{task.title || "Untitled task"}</span>
          {/* Trailing the title, not a column of their own: this table's five
              tracks are already fixed, and a sixth would cost the Task column
              the width it needs. Cap 3 here (2 on a board card) — the row is
              260px+ wide, so three chips fit without pushing the title. */}
          {display.labels ? <TaskLabelChips labels={task.labels} max={3} /> : null}
        </span>
        {display.description && task.description ? (
          <span className="fleet-agent-preview">{task.description}</span>
        ) : null}
      </span>
      {display.assignee ? <span className="fleet-task-cell-assignee">{assigneeCell}</span> : null}
      {display.due ? (
        <span className={`fleet-agent-cell-right fleet-cell-secondary${due ? "" : " fleet-cell-muted"}`}>
          {due || "—"}
        </span>
      ) : null}
      {display.updated ? (
        <span className={`fleet-agent-cell-right fleet-cell-secondary${updated ? "" : " fleet-cell-muted"}`}>
          {updated || "—"}
        </span>
      ) : null}
      {display.status ? (
        <span className="fleet-task-cell-status">
          {onStatusChange ? (
            // Same transparent-native-<select>-over-glyph technique as the
            // board card and the grouped list row (`.fleet-board-card-
            // statuspick`, reused verbatim — it positions the select over
            // whatever it wraps, chip included) — this is the one cell on
            // this row that was a read-only glyph everywhere else on the
            // page already had a working control for.
            <span className="fleet-board-card-statuspick">
              <TaskStatusChip status={task.status} />
              <select
                className="fleet-board-card-status"
                value={task.status}
                aria-label={`Status of ${task.title || "Untitled task"}`}
                title={taskStatusLabel(task.status)}
                onClick={(e) => {
                  e.preventDefault();
                  e.stopPropagation();
                }}
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
            <TaskStatusChip status={task.status} />
          )}
          {/* MAN-294: "In progress" above is the real status, unchanged —
              this is the compact qualifier for "but not actually started
              yet", same icon/colour/title as the board card's. Renders
              nothing on every task that isn't deferred. */}
          <TaskWakeDeferralIcon task={task} size={12} />
        </span>
      ) : null}

      {/* Copy link — same CopyLinkButton the project list row uses
          (fleet-data's projects/page.tsx), placed in the trailing 32px
          track this row's grid always reserves for it (see the grid
          comment above). Its own click handler stops this row's <a> from
          also navigating, matching the assignee button just above. Hidden
          on the mobile collapse along with the other desktop cells (see
          fleet-theme.css's .fleet-task-cell-copy rule) — the row has no
          per-cell room at that width and .fleet-task-row itself remains a
          real link there. */}
      <span className="fleet-task-cell-copy">
        <CopyLinkButton path={href} label={task.title || "this task"} />
      </span>

      {/* Mobile replacement — reuses the Agents list's own mobile classes
          verbatim (they carry no agent-specific semantics), so 375px costs
          no new CSS beyond hiding the desktop cells. */}
      <div className="fleet-agent-row-mobile">
        <div className="fleet-agent-row-mobile-line1">
          <span className="fleet-agent-row-mobile-name">{task.title || "Untitled task"}</span>
          {display.status ? (
            <>
              <TaskStatusChip status={task.status} />
              <TaskWakeDeferralIcon task={task} size={12} />
            </>
          ) : null}
        </div>
        {/* Same toggles as the desktop cells — a display property switched off
            has to be off at 375px too, or the control silently stops working
            at the width where space matters most. Built by filtering rather
            than joining a fixed triple so a hidden field leaves no orphan
            separator. */}
        <div className="fleet-agent-row-mobile-line2">
          {[
            display.assignee
              ? assignee
                ? assignee.label || "Unnamed agent"
                : assignedMember
                  ? assignedMember.display_name || assignedMember.email
                  : "Unassigned"
              : "",
            display.due ? due || "No due date" : "",
            display.updated ? updated || "—" : "",
          ]
            .filter(Boolean)
            .join(" · ")}
        </div>
      </div>
    </a>
  );
}
