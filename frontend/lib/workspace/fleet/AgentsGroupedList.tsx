"use client";

/**
 * The workspace Agents page's LIST layout — one row per agent, in collapsible
 * sections when a grouping is on (status / hardware placement) and
 * as a plain run of rows when it is not. A PARALLEL build to
 * TasksGroupedList.tsx, not a shared/generalized version of it — see
 * agent-view-options.ts's file header for why. Nothing here imports from
 * task-view-options.ts or Tasks*.tsx.
 *
 * IT RENDERS grouping: "none" TOO, and draws NO heading for it. groupAgents
 * answers that case with one synthetic bucket holding every agent
 * (`ungrouped: true`); a heading over it would name the only thing on screen,
 * and its collapse control's one effect would be to hide the entire list —
 * chrome that does not pay for itself, and a control whose own label admits
 * it does nothing. The rows are the list. (This used to be the flat table's
 * job; that table lost its last caller in the 2026-08-22 card-grid redesign,
 * and reviving a third rendering to serve one value of one dropdown would be
 * two lists that must never drift instead of one.)
 *
 * A ROW'S SECOND LINE IS agent-card-face.ts's REACH, and it used to be
 * `activity_preview` — a LIFECYCLE VERB, true of every agent that has ever
 * existed, so a column of it distinguishes nothing. That is the exact finding
 * behind the card grid one layout away. Reach is what differs: the task it is
 * on > tasks waiting > where it answers > neither. Imported from that module
 * rather than reimplemented, so a row here and a card there cannot say
 * different things about one agent. Text only, no channel mark: this row has
 * a Channels cell of its own (display.channels, with the icon), and drawing
 * one channel twice across one row is noise.
 *
 * GROUPING BY. "status" reuses the exact same four buckets the Board draws
 * (agentStatusGroup — see agent-view-options.ts); "placement" splits by the
 * same Cloud/VPS/Device category the flat table's own Placement column
 * already computes per row. groupAgents() in agent-view-options.ts owns
 * which sections exist and in what order.
 *
 * "project" GROUPING WAS REMOVED, 2026-08-30 (founder hard rule: "an agent
 * is completely independent of any project"). It bucketed by `project_id`,
 * a nullable, never-backfilled column that never meant ownership — see
 * groupAgents' own removal note in agent-view-options.ts for the full
 * history. This component no longer takes a `projectById` prop at all.
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
  agentBrainLabel,
  agentDisplayStatus,
  agentPresetBadge,
  agentMoney,
  agentPlacementCategory,
  groupAgents,
  parseAgentChannelField,
  type AgentDisplayState,
  type AgentGroup,
  type AgentGrouping,
  type AgentStatusGroup,
} from "./agent-view-options";
import { agentCardReach, type AgentCardTaskInput } from "./agent-card-face";
import { resolveHardwarePlacement, type FleetGateway } from "./gateway-box-picker";
import { AgentSigil, StatusDot } from "./fleet-indicators";
import { CHANNEL_LABELS, channelIconSrc } from "./fleet-icons";
import { timeAgo, type AgentStatusTone } from "./fleet-presentation";
import type { FleetAgent } from "./fleet-data";

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
  tasksByAgent,
  grouping,
  display,
  onSelect,
}: {
  /** Scopes the collapse preference. */
  workspaceId: string;
  agents: FleetAgent[];
  gateways: FleetGateway[];
  costByAgent: Map<string, number>;
  /** Per-agent tasks — see AgentsBoard.tsx's identical prop doc for why this
   *  is needed (folding an in-progress task into "Working", the same
   *  agentDisplayStatus/agentStatusGroup enrichment used there, so the
   *  "status" grouping and each row's own status cell agree with the Board
   *  and the card grid instead of a fourth opinion on the same fact). Also
   *  what agentCardReach reads for each row's second line. */
  tasksByAgent: Map<string, AgentCardTaskInput[]>;
  /** Every value, "none" included — see the file header for why that case is
   *  this component's job rather than a third rendering's. */
  grouping: AgentGrouping;
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
    () => groupAgents(agents, grouping, { gateways, tasksByAgent }),
    [agents, grouping, gateways, tasksByAgent],
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
        // The synthetic no-grouping bucket has no heading, so it can never be
        // collapsed either — a collapse whose only effect is to hide every row
        // on the page is not a state to leave a reader stranded in.
        const isCollapsed = !section.ungrouped && collapsed.includes(section.key);
        return (
          <section key={section.key} className="fleet-agent-glist-section" aria-label={section.label}>
            {section.ungrouped ? null : (
              <header className="fleet-agent-glist-header">
                <button
                  type="button"
                  className="fleet-agent-glist-header-btn"
                  aria-expanded={!isCollapsed}
                  onClick={() => toggleCollapsed(section.key)}
                >
                  <ChevronRight size={13} strokeWidth={2.25} className="fleet-agent-glist-chevron" />
                  <SectionGlyph section={section} />
                  <span className="fleet-agent-glist-header-title">{section.label}</span>
                  <span className="fleet-agent-glist-header-count">{section.count}</span>
                </button>
              </header>
            )}

            {isCollapsed ? null : (
              <div className="fleet-agent-glist-rows">
                {section.agents.map((agent) => (
                  <AgentGroupedRow
                    key={`${section.key}:${agent.agent_id}`}
                    agent={agent}
                    gateways={gateways}
                    cost={costByAgent.get(agent.agent_id) || 0}
                    tasks={tasksByAgent.get(agent.agent_id) || []}
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
 *  draws. Placement's own three words (Cloud/VPS/Device) are already the
 *  exact short label the flat table's own chip shows — a placeholder glyph
 *  would only repeat them, the same reasoning TasksGroupedList's
 *  SectionGlyph applies to its own catch-all buckets (Unassigned / No
 *  label). "project" grouping (and the icon/tint glyph it drew here,
 *  ProjectIcon) was REMOVED 2026-08-30 — an agent is completely
 *  independent of any project, so there is no project glyph left to draw
 *  on this surface. */
function SectionGlyph({ section }: { section: AgentGroup }) {
  if (section.statusGroup) return <StatusDot tone={STATUS_GROUP_TONE[section.statusGroup]} size={9} />;
  return null;
}

function AgentGroupedRow({
  agent,
  gateways,
  cost,
  tasks,
  display,
  rowStyle,
  onSelect,
}: {
  agent: FleetAgent;
  gateways: FleetGateway[];
  cost: number;
  /** This one agent's own tasks — see AgentsGroupedList's own tasksByAgent
   *  doc. */
  tasks: AgentCardTaskInput[];
  display: AgentDisplayState;
  rowStyle: CSSProperties;
  onSelect: (agentId: string, projectId: string) => void;
}) {
  // "" for the default preset — see agentPresetBadge.
  const preset = agentPresetBadge(agent.capability_preset);
  // Enriched, not the bare deriveAgentStatus — see agentDisplayStatus's own
  // doc comment and AgentsBoard.tsx's identical line.
  const st = agentDisplayStatus(agent, gateways, tasks);
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

      {/* Two LINES in a column, and the badge belongs on the first one. It used
          to be the cell's own third child, which in a column flex gave it a
          full-width line of its own between the name and the reach — measured
          live at 1680x1050 before this wrapper, and it is why a 44px row was
          rendering 58px tall. */}
      <span className="fleet-agent-glist-cell-title">
        <span className="fleet-agent-glist-cell-heading">
          <span className="fleet-agent-glist-cell-name">{agent.label || "Unnamed agent"}</span>
          {preset ? <span className="fleet-badge fleet-badge--preset">{preset}</span> : null}
        </span>
        <span className="fleet-agent-glist-cell-preview">{agentCardReach(agent, tasks).label}</span>
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
  const icon = channelIconSrc(key);
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
