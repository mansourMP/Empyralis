"use client";

/**
 * The workspace Agents page's grouped list — the List layout with a grouping
 * applied (status / project / hardware placement), rendered as collapsible
 * sections instead of the flat table. A PARALLEL build to
 * TasksGroupedList.tsx, not a shared/generalized version of it — see
 * agent-view-options.ts's file header for why. Nothing here imports from
 * task-view-options.ts or Tasks*.tsx; the flat table itself (AgentsList.tsx)
 * is untouched and is what still renders whenever grouping is "none".
 *
 * GROUPING BY. "status" reuses the exact same four buckets the Board draws
 * (agentStatusGroup — see agent-view-options.ts); "project" is the reason
 * this feature is worth having on a CROSS-project page (each agent already
 * carries its own project_id, and the per-project Agents tab has no
 * equivalent need for it); "placement" splits by the same Cloud/VPS/Device
 * category the flat table's own Placement column already computes per row.
 * groupAgents() in agent-view-options.ts owns which sections exist and in
 * what order.
 *
 * ZERO-COUNT SECTIONS ARE NEVER SHOWN. Unlike TasksGroupedList's "All" tab
 * (which deliberately keeps every status section, even empty, so a reader
 * can confirm a status is genuinely empty), this list has no such tab — an
 * agent has no lifecycle to audit for silently-empty stages, so there is
 * nothing to gain from drawing a heading over zero rows.
 *
 * COLLAPSE STATE persists per workspace (`fleet:agent-glist-collapsed:v1:*`
 * — its own key, never colliding with TasksGroupedList's `fleet:glist-*`).
 *
 * MOTION. Same discipline as TasksGroupedList: the chevron rotates over
 * --dur-2: the body does not animate, it unmounts.
 */

import { useCallback, useEffect, useMemo, useState, type CSSProperties } from "react";
import { ChevronRight } from "lucide-react";

import {
  AGENT_PLACEMENT_LABELS,
  agentActivityPreviewText,
  agentBrainLabel,
  agentMoney,
  agentPlacementCategory,
  groupAgents,
  parseAgentChannelField,
  type AgentDisplayState,
  type AgentGroup,
  type AgentGrouping,
  type AgentStatusGroup,
} from "./agent-view-options";
import { deriveAgentStatus, resolveHardwarePlacement, type FleetGateway } from "./gateway-box-picker";
import { AgentSigil, StatusDot } from "./fleet-indicators";
import { ProjectIcon } from "./fleet-project-identity";
import { CHANNEL_ICONS, CHANNEL_LABELS } from "./fleet-icons";
import { timeAgo, type AgentStatusTone } from "./fleet-presentation";
import type { FleetAgent, FleetProject } from "./fleet-data";

const STATUS_GROUP_TONE: Record<AgentStatusGroup, AgentStatusTone> = {
  working: "working",
  idle: "ready",
  needs_attention: "degraded",
  offline: "offline",
};

function collapsedKey(workspaceId: string): string {
  return `fleet:agent-glist-collapsed:v1:${workspaceId}`;
}

const MAX_COLLAPSED = 200;

function readCollapsed(workspaceId: string): string[] {
  try {
    const raw = window.localStorage.getItem(collapsedKey(workspaceId));
    if (!raw) return [];
    const parsed = JSON.parse(raw);
    if (!Array.isArray(parsed) || parsed.length > MAX_COLLAPSED) return [];
    return parsed.filter((s): s is string => typeof s === "string" && s.length > 0 && s.length < 128);
  } catch {
    return [];
  }
}

/** The row's grid tracks, rebuilt from whatever the reader left switched on
 *  — same technique TasksGroupedList's gridTracks() uses, so a hidden cell
 *  loses its TRACK too and the remaining columns line up. Avatar+name is
 *  always present (an agent row with no name is not a row); the six
 *  optional tracks come and go with `display`. */
function gridTracks(display: AgentDisplayState, mobile: boolean): string {
  if (mobile) {
    return ["22px", "minmax(0, 1fr)", display.status ? "104px" : null].filter(Boolean).join(" ");
  }
  return [
    "22px",
    "minmax(200px, 1fr)",
    display.brain ? "92px" : null,
    display.placement ? "64px" : null,
    display.channels ? "56px" : null,
    display.lastActive ? "64px" : null,
    display.cost ? "76px" : null,
    display.status ? "128px" : null,
  ]
    .filter(Boolean)
    .join(" ");
}

export function AgentsGroupedList({
  workspaceId,
  agents,
  gateways,
  costByAgent,
  projectById,
  grouping,
  display,
  onSelect,
}: {
  /** Scopes the collapse preference. */
  workspaceId: string;
  agents: FleetAgent[];
  gateways: FleetGateway[];
  costByAgent: Map<string, number>;
  projectById?: Map<string, FleetProject>;
  /** "none" never reaches this component — the page renders AgentsList (the
   *  flat table) for it. */
  grouping: Exclude<AgentGrouping, "none">;
  display: AgentDisplayState;
  onSelect: (agentId: string, projectId: string) => void;
}) {
  const [collapsed, setCollapsed] = useState<string[]>([]);
  useEffect(() => {
    if (workspaceId) setCollapsed(readCollapsed(workspaceId));
  }, [workspaceId]);

  const toggleCollapsed = useCallback(
    (key: string) => {
      setCollapsed((cur) => {
        const next = cur.includes(key) ? cur.filter((s) => s !== key) : [...cur, key];
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

  const sections = useMemo(
    () => groupAgents(agents, grouping, { gateways, projectById: projectById || new Map() }),
    [agents, grouping, gateways, projectById],
  );

  const rowStyle = useMemo(
    () =>
      ({
        "--fleet-agent-glist-grid": gridTracks(display, false),
        "--fleet-agent-glist-grid-mobile": gridTracks(display, true),
      }) as CSSProperties,
    [display],
  );

  if (sections.length === 0) return null;

  return (
    <div className="fleet-agent-glist">
      {sections.map((section) => {
        const isCollapsed = collapsed.includes(section.key);
        return (
          <section key={section.key} className="fleet-agent-glist-section" aria-label={section.label}>
            <header className="fleet-agent-glist-header">
              <button
                type="button"
                className="fleet-agent-glist-header-btn"
                aria-expanded={!isCollapsed}
                onClick={() => toggleCollapsed(section.key)}
              >
                <ChevronRight size={13} strokeWidth={2.25} className="fleet-agent-glist-chevron" />
                <SectionGlyph section={section} projectById={projectById} />
                <span className="fleet-agent-glist-header-title">{section.label}</span>
                <span className="fleet-agent-glist-header-count">{section.count}</span>
              </button>
            </header>

            {isCollapsed ? null : (
              <div className="fleet-agent-glist-rows">
                {section.agents.map((agent) => (
                  <AgentGroupedRow
                    key={`${section.key}:${agent.agent_id}`}
                    agent={agent}
                    gateways={gateways}
                    cost={costByAgent.get(agent.agent_id) || 0}
                    display={display}
                    rowStyle={rowStyle}
                    onSelect={onSelect}
                  />
                ))}
              </div>
            )}
          </section>
        );
      })}
    </div>
  );
}

/** A heading is never just text: status brings the same dot the board column
 *  draws, project brings its own icon/tint (ProjectIcon — the same mark the
 *  sidebar and project pages already use for that project, so a "General"
 *  section here reads as the same project everywhere else in the app).
 *  Placement's own three words (Cloud/VPS/Device) are already the exact
 *  short label the flat table's own chip shows — a placeholder glyph would
 *  only repeat them, the same reasoning TasksGroupedList's SectionGlyph
 *  applies to its own catch-all buckets (Unassigned / No label). The
 *  "Ungrouped" project bucket (an agent with no project_id) gets the same
 *  treatment for the same reason. */
function SectionGlyph({ section, projectById }: { section: AgentGroup; projectById?: Map<string, FleetProject> }) {
  if (section.statusGroup) return <StatusDot tone={STATUS_GROUP_TONE[section.statusGroup]} size={9} />;
  if (section.projectId) {
    const proj = projectById?.get(section.projectId);
    return <ProjectIcon icon={proj?.icon} tint={proj?.tint} size={16} glyphSize={10} />;
  }
  return null;
}

function AgentGroupedRow({
  agent,
  gateways,
  cost,
  display,
  rowStyle,
  onSelect,
}: {
  agent: FleetAgent;
  gateways: FleetGateway[];
  cost: number;
  display: AgentDisplayState;
  rowStyle: CSSProperties;
  onSelect: (agentId: string, projectId: string) => void;
}) {
  const presetRaw = (agent.capability_preset || agent.purpose_preset || "").toLowerCase().replace(/_/g, " ");
  const preset = presetRaw ? presetRaw.charAt(0).toUpperCase() + presetRaw.slice(1) : "";
  const st = deriveAgentStatus(agent, gateways);
  const brain = display.brain ? agentBrainLabel(agent.model_config) : "";
  const placement = display.placement
    ? resolveHardwarePlacement(agent.hardware_access, agent.preferred_gateway_id, gateways, agent.model_config)
    : null;
  const placementShort = placement ? AGENT_PLACEMENT_LABELS[agentPlacementCategory(agent, gateways)] : "";
  const channel = display.channels ? (agent.channel || "").trim() : "";
  const relative = display.lastActive && agent.last_activity ? timeAgo(agent.last_activity) : "";

  const activate = () => onSelect(agent.agent_id, agent.project_id || "");

  return (
    <div
      className="fleet-agent-glist-row"
      style={rowStyle}
      role="button"
      tabIndex={0}
      aria-label={`${agent.label || "Unnamed agent"} — open details`}
      onClick={() => activate()}
      onKeyDown={(e) => {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          activate();
        }
      }}
    >
      <span className="fleet-agent-avatar fleet-agent-glist-cell-avatar">
        <AgentSigil seed={agent.agent_id} size={14} />
      </span>

      <span className="fleet-agent-glist-cell-title">
        <span className="fleet-agent-glist-cell-name">{agent.label || "Unnamed agent"}</span>
        {preset ? <span className="fleet-badge fleet-badge--preset">{preset}</span> : null}
        <span className="fleet-agent-glist-cell-preview">{agentActivityPreviewText(agent)}</span>
      </span>

      {display.brain ? (
        <span className={`fleet-agent-glist-cell-text${brain ? "" : " fleet-cell-muted"}`}>{brain || "—"}</span>
      ) : null}

      {display.placement ? (
        <span className="fleet-agent-glist-cell-text">
          <span className="fleet-channel-chip" title={placement?.label}>
            {placementShort}
          </span>
        </span>
      ) : null}

      {display.channels ? (
        <span className="fleet-agent-glist-cell-text">
          {channel ? <AgentChannelGlyph channel={channel} /> : <span className="fleet-cell-muted">None</span>}
        </span>
      ) : null}

      {display.lastActive ? (
        <span className={`fleet-agent-glist-cell-text${relative ? "" : " fleet-cell-muted"}`}>{relative || "Never"}</span>
      ) : null}

      {display.cost ? (
        <span className={`fleet-agent-glist-cell-text${cost > 0 ? "" : " fleet-cell-muted"}`}>
          {agentMoney(cost)}
        </span>
      ) : null}

      {display.status ? (
        <span className="fleet-agent-glist-cell-status">
          <StatusDot tone={st.tone} size={8} />
          <span>{st.label}</span>
        </span>
      ) : null}
    </div>
  );
}

function AgentChannelGlyph({ channel }: { channel: string }) {
  const { key, extra } = parseAgentChannelField(channel);
  const icon = CHANNEL_ICONS[key];
  const label = CHANNEL_LABELS[key] || key;
  return (
    <>
      {icon ? (
        <img src={icon} alt="" width={14} height={14} title={label} />
      ) : (
        <span className="fleet-channel-chip">{key.slice(0, 2).toUpperCase()}</span>
      )}
      {extra > 0 ? <span className="fleet-channel-chip">+{extra}</span> : null}
    </>
  );
}
