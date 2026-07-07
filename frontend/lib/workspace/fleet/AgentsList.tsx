"use client";

import type { CSSProperties } from "react";
import { Clock, Radio } from "lucide-react";

import type { FleetAgent } from "./fleet-data";
import { deriveStatus, statusClass, timeAgo, tintForAgent, TINTS } from "./fleet-presentation";
import { StatusChip } from "./fleet-indicators";

const money = (n: number) => `$${n.toFixed(4)}`;

/**
 * The Linear-density agent list (~34px single-line rows), shared by the flat
 * /agents page and a project's agent list. One row reads as a rich record:
 * tinted avatar with a live status dot, name + preset badge, a colored status
 * chip, channel, today's cost, and last-active — icon + color + weight in
 * every column, never a line of flat gray text. Optional groupByProject
 * renders project section headers instead of a flat list.
 */
export function AgentsList({
  agents,
  costByAgent,
  projectNameById,
  groupByProject,
  onSelect,
}: {
  agents: FleetAgent[];
  costByAgent: Map<string, number>;
  projectNameById?: Map<string, string>;
  groupByProject?: boolean;
  onSelect: (agentId: string, projectId: string) => void;
}) {
  if (groupByProject) {
    const groups = new Map<string, FleetAgent[]>();
    for (const a of agents) {
      const key = a.project_id || "";
      if (!groups.has(key)) groups.set(key, []);
      groups.get(key)!.push(a);
    }
    return (
      <div className="fleet-agents-list">
        {Array.from(groups.entries()).map(([projectId, group]) => (
          <div key={projectId || "ungrouped"} className="fleet-agent-group">
            <div className="fleet-agent-group-title">
              {(projectId && projectNameById?.get(projectId)) || "Ungrouped"}
            </div>
            {group.map((a, i) => (
              <AgentRow key={a.agent_id} agent={a} index={i} cost={costByAgent.get(a.agent_id) || 0} onSelect={onSelect} />
            ))}
          </div>
        ))}
      </div>
    );
  }

  return (
    <div className="fleet-agents-list">
      {agents.map((a, i) => (
        <AgentRow key={a.agent_id} agent={a} index={i} cost={costByAgent.get(a.agent_id) || 0} onSelect={onSelect} />
      ))}
    </div>
  );
}

function AgentRow({
  agent,
  index,
  cost,
  onSelect,
}: {
  agent: FleetAgent;
  index: number;
  cost: number;
  onSelect: (agentId: string, projectId: string) => void;
}) {
  const st = deriveStatus(agent.hardware_status || "unknown");
  const preset = (agent.capability_preset || agent.purpose_preset || "").toLowerCase().replace(/_/g, " ");
  const initial = (agent.label || "A").charAt(0).toUpperCase();
  const tint = tintForAgent(agent, index);
  const avatarStyle = {
    "--tile-bg": TINTS[tint].bg,
    "--tile-fg": TINTS[tint].fg,
  } as CSSProperties;
  const hasChannel = Boolean(agent.channel);
  return (
    <button
      type="button"
      className="fleet-agent-row"
      onClick={() => onSelect(agent.agent_id, agent.project_id || "")}
    >
      <span className="fleet-agent-row-avatar" style={avatarStyle}>
        {initial}
        <span className={`fleet-agent-row-dot ${statusClass(st.tone)}`} />
      </span>
      <span className="fleet-agent-row-main">
        <span className="fleet-agent-row-title">{agent.label || "Unnamed agent"}</span>
        {preset && <span className="fleet-badge fleet-badge--preset">{preset}</span>}
        {hasChannel && (
          <span className="fleet-agent-row-chan">
            <Radio size={11} strokeWidth={1.75} />
            {agent.channel}
          </span>
        )}
      </span>
      <span className="fleet-agent-row-status">
        <StatusChip tone={st.tone} label={st.label} />
      </span>
      <span className={`fleet-agent-row-cost${cost > 0 ? "" : " is-zero"}`}>
        {cost > 0 ? money(cost) : "—"}
      </span>
      <span className="fleet-agent-row-tag fleet-agent-row-tag--time">
        <Clock size={12} strokeWidth={1.75} />
        {timeAgo(agent.last_activity)}
      </span>
    </button>
  );
}
