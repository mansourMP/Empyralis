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

import { timeAgo, TINTS, tintForAgent, type AgentStatusTone } from "./fleet-presentation";
import { StatusChip, AgentSigil } from "./fleet-indicators";
import type { FleetAgent, FleetTask, FleetTaskStatus } from "./fleet-data";

/** Task status → the existing agent-status tone vocabulary. No new colours:
 *  `blocked` and `awaiting_input` map to the two most saturated tones in the
 *  system (red, amber) because those are the two states that mean an agent
 *  has stopped and a person is needed — the whole point of glancing at the
 *  board. Neither is the accent, which stays reserved for action buttons.
 *
 *  `awaiting_input` → `degraded` reuses a tone whose own definition already
 *  reads "needs your action, not broken" (fleet-theme.css:1869-1874).
 *  `done` → `ready` is a deliberate repurpose: the words differ but the
 *  visual (muted, calm, settled) carries correctly. */
const TASK_STATUS_PRESENTATION: Record<FleetTaskStatus, { tone: AgentStatusTone; label: string }> = {
  open: { tone: "unknown", label: "Open" },
  in_progress: { tone: "working", label: "In progress" },
  blocked: { tone: "error", label: "Blocked" },
  awaiting_input: { tone: "degraded", label: "Needs input" },
  done: { tone: "ready", label: "Done" },
};

export function taskStatusPresentation(status: FleetTaskStatus) {
  return TASK_STATUS_PRESENTATION[status] ?? TASK_STATUS_PRESENTATION.open;
}

function dueLabel(dueAt: string | null | undefined): string {
  const raw = String(dueAt || "").trim();
  if (!raw) return "";
  const d = new Date(raw);
  if (Number.isNaN(d.getTime())) return "";
  return d.toLocaleDateString(undefined, { month: "short", day: "numeric" });
}

export function TasksList({
  tasks,
  agents,
  onAssign,
  onSelect,
}: {
  tasks: FleetTask[];
  /** Agents in this project — the only valid assignees. */
  agents: FleetAgent[];
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
  onAssign,
  onSelect,
}: {
  task: FleetTask;
  agents: FleetAgent[];
  index: number;
  onAssign: (taskId: string, agentId: string) => void;
  onSelect?: (taskId: string) => void;
}) {
  const [assigning, setAssigning] = useState(false);
  const st = taskStatusPresentation(task.status);
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
      onClick={() => onSelect?.(task.id)}
      onKeyDown={(e) => {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          onSelect?.(task.id);
        }
      }}
    >
      {/* Desktop cells */}
      <span className="fleet-agent-cell-agent-text fleet-task-cell-title">
        <span className="fleet-agent-name">{task.title || "Untitled task"}</span>
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
        <StatusChip tone={st.tone} label={st.label} />
      </span>

      {/* Mobile replacement — reuses the Agents list's own mobile classes
          verbatim (they carry no agent-specific semantics), so 375px costs
          no new CSS beyond hiding the desktop cells. */}
      <div className="fleet-agent-row-mobile">
        <div className="fleet-agent-row-mobile-line1">
          <span className="fleet-agent-row-mobile-name">{task.title || "Untitled task"}</span>
          <StatusChip tone={st.tone} label={st.label} />
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
