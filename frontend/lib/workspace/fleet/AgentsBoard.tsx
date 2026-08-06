"use client";

/**
 * The workspace Agents page's kanban board — one column per real agent
 * status group (agent-view-options.ts's AGENT_STATUS_GROUPS: Working / Idle
 * / Needs attention / Offline or stopped), always in that order. A PARALLEL
 * build to TasksBoard.tsx, not a shared/generalized version of it — see
 * agent-view-options.ts's file header for why. Nothing here imports from
 * task-view-options.ts or Tasks*.tsx.
 *
 * NO DRAG-AND-DROP, ON PURPOSE — the one deliberate structural difference
 * from TasksBoard. A task board's column IS the status: dragging a card
 * across columns is a real PATCH (assign_task's status field) and every
 * column is a legal destination for every task. An agent's board column is
 * a DERIVED bucket (agentStatusGroup folds seven backend-computed
 * AgentStatusTones into four groups — see agent-view-options.ts) — there is
 * no "set an agent's status to Working" endpoint, nor to Idle, nor to Needs
 * attention: those are facts about whether a run is in progress or a brain
 * is signed in, not settable fields. The only real mutation this data
 * supports is Stop/Resume (already on every row of the flat table and the
 * agent's own detail page), and that maps to only ONE column transition
 * (anything -> Offline or stopped), never a drop onto any of the other
 * three. A drag that only works into one of four columns is a control that
 * lies about what it can do three times out of four — so this board is
 * click-to-open only, exactly like a card in the grouped list. Rendering
 * inert dead columns to "look like" the task board would be the opposite of
 * an honest board.
 *
 * A column renders iff it has an agent in it — same rule TasksBoard's own
 * `visibleStatuses` applies, so "Needs attention" simply isn't drawn on a
 * healthy fleet rather than showing up empty every time.
 */

import type { KeyboardEvent, ReactNode } from "react";
import { Fragment, useMemo } from "react";

import {
  AGENT_PLACEMENT_LABELS,
  AGENT_STATUS_GROUPS,
  agentActivityPreviewText,
  agentBrainLabel,
  agentMoney,
  agentPlacementCategory,
  agentStatusGroup,
  parseAgentChannelField,
  type AgentDisplayState,
  type AgentStatusGroup,
} from "./agent-view-options";
import { deriveAgentStatus, resolveHardwarePlacement, type FleetGateway } from "./gateway-box-picker";
import { AgentSigil, StatusDot } from "./fleet-indicators";
import { CHANNEL_ICONS, CHANNEL_LABELS } from "./fleet-icons";
import { timeAgo, type AgentStatusTone } from "./fleet-presentation";
import type { FleetAgent } from "./fleet-data";

/** The dot colour a board column heading draws for its own group — reuses
 *  the app's existing status-tone palette (fleet-sdot--*) rather than
 *  inventing new colours for four buckets that are themselves a coarser view
 *  of those same tones. */
const STATUS_GROUP_TONE: Record<AgentStatusGroup, AgentStatusTone> = {
  working: "working",
  idle: "ready",
  needs_attention: "degraded",
  offline: "offline",
};

export function AgentsBoard({
  agents,
  gateways,
  costByAgent,
  display,
  selectedAgentId,
  onSelect,
}: {
  agents: FleetAgent[];
  /** Paired-Gateway boxes for this workspace — needed to resolve a
   *  brain-bound agent's real status (deriveAgentStatus) and its placement
   *  category. Fetched once at the page level (agents/page.tsx), the same
   *  useWorkspaceGateways hook AgentsList.tsx already calls for the flat
   *  table — this is a second, independent fetch of the same cheap
   *  endpoint, matching how every other surface in this directory (the
   *  Hardware tab, the machine detail page, AgentsList) already fetches it
   *  standalone rather than threading one shared instance through props. */
  gateways: FleetGateway[];
  costByAgent: Map<string, number>;
  /** Which card fields this reader wants drawn (the view-options popover's
   *  "Display properties"). */
  display: AgentDisplayState;
  selectedAgentId?: string | null;
  onSelect: (agentId: string, projectId: string) => void;
}) {
  const columns = useMemo(() => {
    const buckets = new Map<AgentStatusGroup, FleetAgent[]>(AGENT_STATUS_GROUPS.map((g) => [g.value, []]));
    for (const agent of agents) buckets.get(agentStatusGroup(agent, gateways))?.push(agent);
    return AGENT_STATUS_GROUPS.map((g) => ({ ...g, agents: buckets.get(g.value) || [] })).filter(
      (c) => c.agents.length > 0,
    );
  }, [agents, gateways]);

  // Can only trigger if `agents` is non-empty but somehow matches no group —
  // impossible given AGENT_STATUS_GROUPS is exhaustive over agentStatusGroup's
  // own return type. Kept as a defensive fallback, same posture TasksBoard's
  // own (in practice unreachable) empty check takes, since agents/page.tsx
  // already renders its own empty/no-match states before this component is
  // ever mounted.
  if (columns.length === 0) return null;

  return (
    <div className="fleet-agent-board" role="list" aria-label="Agent board">
      {columns.map((col) => (
        <section key={col.value} className="fleet-agent-board-column" role="listitem" aria-label={`${col.label}, ${col.agents.length} ${col.agents.length === 1 ? "agent" : "agents"}`}>
          <header className="fleet-agent-board-column-header">
            <StatusDot tone={STATUS_GROUP_TONE[col.value]} size={8} />
            <span className="fleet-agent-board-column-title">{col.label}</span>
            <span className="fleet-agent-board-column-count">{col.agents.length}</span>
          </header>
          <div className="fleet-agent-board-column-body">
            {col.agents.map((agent) => (
              <AgentCard
                key={agent.agent_id}
                agent={agent}
                gateways={gateways}
                cost={costByAgent.get(agent.agent_id) || 0}
                display={display}
                selected={selectedAgentId === agent.agent_id}
                onSelect={onSelect}
              />
            ))}
          </div>
        </section>
      ))}
    </div>
  );
}

function AgentCard({
  agent,
  gateways,
  cost,
  display,
  selected,
  onSelect,
}: {
  agent: FleetAgent;
  gateways: FleetGateway[];
  cost: number;
  display: AgentDisplayState;
  selected: boolean;
  onSelect: (agentId: string, projectId: string) => void;
}) {
  const presetRaw = (agent.capability_preset || agent.purpose_preset || "").toLowerCase().replace(/_/g, " ");
  const preset = presetRaw ? presetRaw.charAt(0).toUpperCase() + presetRaw.slice(1) : "";
  const st = deriveAgentStatus(agent, gateways);

  const activate = () => onSelect(agent.agent_id, agent.project_id || "");
  const handleClick = () => activate();
  const handleKeyDown = (e: KeyboardEvent) => {
    if (e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      activate();
    }
  };

  const fields: { key: string; node: ReactNode }[] = [];
  if (display.status) {
    fields.push({
      key: "status",
      node: (
        <span className="fleet-agent-board-card-field">
          <StatusDot tone={st.tone} size={7} />
          <span>{st.label}</span>
        </span>
      ),
    });
  }
  if (display.brain) {
    const brain = agentBrainLabel(agent.model_config);
    if (brain) fields.push({ key: "brain", node: <span className="fleet-agent-board-card-field">{brain}</span> });
  }
  if (display.placement) {
    const placement = resolveHardwarePlacement(agent.hardware_access, agent.preferred_gateway_id, gateways, agent.model_config);
    const short = AGENT_PLACEMENT_LABELS[agentPlacementCategory(agent, gateways)];
    fields.push({
      key: "placement",
      node: (
        <span className="fleet-channel-chip fleet-agent-board-card-field" title={placement.label}>
          {short}
        </span>
      ),
    });
  }
  if (display.channels) {
    const channel = (agent.channel || "").trim();
    if (channel) {
      const { key, extra } = parseAgentChannelField(channel);
      const icon = CHANNEL_ICONS[key];
      const label = CHANNEL_LABELS[key] || key;
      fields.push({
        key: "channels",
        node: (
          <span className="fleet-agent-board-card-field">
            {icon ? (
              <img src={icon} alt="" width={14} height={14} title={label} />
            ) : (
              <span className="fleet-channel-chip">{key.slice(0, 2).toUpperCase()}</span>
            )}
            {extra > 0 ? <span className="fleet-channel-chip">+{extra}</span> : null}
          </span>
        ),
      });
    }
  }
  if (display.lastActive) {
    const relative = agent.last_activity ? timeAgo(agent.last_activity) : "";
    fields.push({
      key: "lastActive",
      node: <span className={`fleet-agent-board-card-field${relative ? "" : " fleet-cell-muted"}`}>{relative || "Never active"}</span>,
    });
  }
  if (display.cost) {
    fields.push({
      key: "cost",
      node: <span className={`fleet-agent-board-card-field${cost > 0 ? "" : " fleet-cell-muted"}`}>{agentMoney(cost)}</span>,
    });
  }

  return (
    <article
      className={`fleet-agent-board-card${selected ? " is-selected" : ""}`}
      tabIndex={0}
      role="button"
      aria-label={`${agent.label || "Unnamed agent"} — open details`}
      onClick={handleClick}
      onKeyDown={handleKeyDown}
    >
      <div className="fleet-agent-board-card-head">
        <span className="fleet-agent-avatar">
          <AgentSigil seed={agent.agent_id} size={16} />
        </span>
        <span className="fleet-agent-board-card-name">{agent.label || "Unnamed agent"}</span>
        {preset ? <span className="fleet-badge fleet-badge--preset">{preset}</span> : null}
      </div>
      <div className="fleet-agent-board-card-preview">{agentActivityPreviewText(agent)}</div>
      {fields.length > 0 ? (
        <div className="fleet-agent-board-card-meta">
          {fields.map((f) => (
            <Fragment key={f.key}>{f.node}</Fragment>
          ))}
        </div>
      ) : null}
    </article>
  );
}
