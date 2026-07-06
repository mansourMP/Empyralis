"use client";

import type { FleetAgent } from "./fleet-data";
import { deriveStatus, statusClass, timeAgo } from "./fleet-presentation";

const money = (n: number) => `$${n.toFixed(4)}`;

/**
 * The Linear-density agent list (~32px single-line rows), shared by the flat
 * /agents page and a project's agent list. One row: status dot, name, preset
 * badge, channel, last-active, today's cost. Optional groupByProject renders
 * project section headers instead of a flat list.
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
            {group.map((a) => (
              <AgentRow key={a.agent_id} agent={a} cost={costByAgent.get(a.agent_id) || 0} onSelect={onSelect} />
            ))}
          </div>
        ))}
      </div>
    );
  }

  return (
    <div className="fleet-agents-list">
      {agents.map((a) => (
        <AgentRow key={a.agent_id} agent={a} cost={costByAgent.get(a.agent_id) || 0} onSelect={onSelect} />
      ))}
    </div>
  );
}

function AgentRow({
  agent,
  cost,
  onSelect,
}: {
  agent: FleetAgent;
  cost: number;
  onSelect: (agentId: string, projectId: string) => void;
}) {
  const st = deriveStatus(agent.hardware_status || "unknown");
  const preset = (agent.capability_preset || agent.purpose_preset || "").toLowerCase().replace(/_/g, " ");
  return (
    <button
      type="button"
      className="fleet-agent-row"
      onClick={() => onSelect(agent.agent_id, agent.project_id || "")}
    >
      <span className={`fleet-detail-dot ${statusClass(st.tone)}`} />
      <span className="fleet-agent-row-main">
        <span className="fleet-agent-row-title">{agent.label || "Unnamed agent"}</span>
        {preset && <span className="fleet-badge fleet-badge--preset">{preset}</span>}
      </span>
      <span className="fleet-agent-row-tag">{agent.channel || "No channel"}</span>
      <span className="fleet-agent-row-tag">{timeAgo(agent.last_activity)}</span>
      <span className="fleet-agent-row-cost">{cost > 0 ? money(cost) : "—"}</span>
    </button>
  );
}
