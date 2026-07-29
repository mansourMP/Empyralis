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
 */

import { useState, type CSSProperties } from "react";

import { timeAgo, TINTS, tintForAgent } from "./fleet-presentation";
import { AgentSigil } from "./fleet-indicators";
import { TaskStatusChip, TaskPriorityIcon, taskPriority, taskStatusLabel, TASK_PRIORITY_LABELS } from "./task-status";
import { TaskLabelChips } from "./task-labels";
import type { FleetAgent, FleetTask, FleetTaskStatus } from "./fleet-data";

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
  return d.toLocaleDateString(undefined, { month: "short", day: "numeric" });
}

export function TasksList({
  tasks,
  agents,
  taskHref,
  onAssign,
  onSelect,
}: {
  tasks: FleetTask[];
  /** Agents in this project — the only valid assignees. */
  agents: FleetAgent[];
  /** The task's real route — stamped as `data-tab-href` so ⌘/Ctrl+click and
   *  middle-click open it in a background content tab (see FleetTabs). */
  taskHref?: (taskId: string) => string;
  onAssign: (taskId: string, agentId: string) => void;
  onSelect?: (taskId: string) => void;
}) {
  return (
    <div className="fleet-tasks-list">
      <div className="fleet-tasks-list-header" role="row">
        <span>Task</span>
        <span>Assignee</span>
        <span className="is-right">Due</span>
        <span className="is-right">Updated</span>
        <span>Status</span>
      </div>
      {tasks.map((task, index) => (
        <TaskRow
          key={task.id}
          task={task}
          agents={agents}
          index={index}
          href={taskHref?.(task.id)}
          onAssign={onAssign}
          onSelect={onSelect}
        />
      ))}
    </div>
  );
}

function TaskRow({
  task,
  agents,
  index,
  href,
  onAssign,
  onSelect,
}: {
  task: FleetTask;
  agents: FleetAgent[];
  index: number;
  href?: string;
  onAssign: (taskId: string, agentId: string) => void;
  onSelect?: (taskId: string) => void;
}) {
  const [assigning, setAssigning] = useState(false);
  const priority = taskPriority(task);
  const assignee = agents.find((a) => a.agent_id === task.assignee_agent_id) || null;
  const due = dueLabel(task.due_at);
  const updated = timeAgo(task.created_at);

  // Tint keyed off the assignee via the same helper the Agents list uses, so
  // one agent reads the same colour here as it does everywhere else.
  const tint = assignee ? TINTS[tintForAgent(assignee, index)] : null;
  const avatarStyle = (tint
    ? { "--tile-bg": tint.bg, "--tile-fg": tint.fg }
    : {}) as CSSProperties;

  const assigneeCell = assigning ? (
    <select
      className="fleet-wizard-input"
      autoFocus
      defaultValue={task.assignee_agent_id || ""}
      onBlur={() => setAssigning(false)}
      onChange={(e) => {
        const next = e.target.value;
        setAssigning(false);
        if (next && next !== task.assignee_agent_id) onAssign(task.id, next);
      }}
      onClick={(e) => e.stopPropagation()}
      style={{ height: 28, padding: "0 6px", fontSize: 12 }}
    >
      <option value="">Unassigned</option>
      {agents.map((a) => (
        <option key={a.agent_id} value={a.agent_id}>
          {a.label || "Unnamed agent"}
        </option>
      ))}
    </select>
  ) : (
    <button
      type="button"
      className="fleet-task-assignee-btn"
      onClick={(e) => {
        e.stopPropagation();
        setAssigning(true);
      }}
    >
      {assignee ? (
        <>
          <span className="fleet-agent-avatar" style={avatarStyle}>
            <AgentSigil seed={assignee.agent_id} size={16} />
          </span>
          <span className="fleet-cell-secondary">{assignee.label || "Unnamed agent"}</span>
        </>
      ) : (
        <span className="fleet-cell-muted">Unassigned</span>
      )}
    </button>
  );

  return (
    <div
      className="fleet-task-row"
      role="row"
      tabIndex={0}
      data-tab-href={href}
      data-tab-title={task.title || "Untitled task"}
      onClick={(e) => {
        // See TasksBoard's TaskCard: a modifier click belongs to the tab
        // layer (background tab), not to this row.
        if (e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
        onSelect?.(task.id);
      }}
      onKeyDown={(e) => {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          onSelect?.(task.id);
        }
      }}
    >
      {/* Desktop cells */}
      <span className="fleet-agent-cell-agent-text fleet-task-cell-title">
        <span className="fleet-task-cell-titleline">
          {/* Same glyph as the board card, so a task reads the same in both
              layouts. Absent backend field degrades to "no priority". */}
          <span className="fleet-task-cell-prio" title={TASK_PRIORITY_LABELS[priority]}>
            <TaskPriorityIcon priority={priority} size={14} />
          </span>
          <span className="fleet-agent-name">{task.title || "Untitled task"}</span>
          {/* Trailing the title, not a column of their own: this table's five
              tracks are already fixed, and a sixth would cost the Task column
              the width it needs. Cap 3 here (2 on a board card) — the row is
              260px+ wide, so three chips fit without pushing the title. */}
          <TaskLabelChips labels={task.labels} max={3} />
        </span>
        {task.description ? (
          <span className="fleet-agent-preview">{task.description}</span>
        ) : null}
      </span>
      <span className="fleet-task-cell-assignee">{assigneeCell}</span>
      <span className={`fleet-agent-cell-right fleet-cell-secondary${due ? "" : " fleet-cell-muted"}`}>
        {due || "—"}
      </span>
      <span className={`fleet-agent-cell-right fleet-cell-secondary${updated ? "" : " fleet-cell-muted"}`}>
        {updated || "—"}
      </span>
      <span className="fleet-task-cell-status">
        <TaskStatusChip status={task.status} />
      </span>

      {/* Mobile replacement — reuses the Agents list's own mobile classes
          verbatim (they carry no agent-specific semantics), so 375px costs
          no new CSS beyond hiding the desktop cells. */}
      <div className="fleet-agent-row-mobile">
        <div className="fleet-agent-row-mobile-line1">
          <span className="fleet-agent-row-mobile-name">{task.title || "Untitled task"}</span>
          <TaskStatusChip status={task.status} />
        </div>
        <div className="fleet-agent-row-mobile-line2">
          {[
            assignee ? assignee.label || "Unnamed agent" : "Unassigned",
            due || "No due date",
            updated || "—",
          ].join(" · ")}
        </div>
      </div>
    </div>
  );
}
