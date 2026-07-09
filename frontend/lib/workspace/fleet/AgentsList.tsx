"use client";

import type { CSSProperties, KeyboardEvent } from "react";
import { useCallback, useEffect, useRef, useState } from "react";
import { Play, Square } from "lucide-react";

import { type FleetAgent, type FleetProject, resumeFleetAgent, stopFleetAgent } from "./fleet-data";
import { deriveStatus, timeAgo, tintForAgent, TINTS } from "./fleet-presentation";
import { StatusDot } from "./fleet-indicators";
import { ProjectIcon } from "./fleet-project-identity";

const LAST_VIEWED_KEY = "fleet:list-last-viewed-agent";

/** Display-time guard against old rows in the shared DB written before the
 *  2026-07-09 U3-A fleet_control-title humanization fix — those still carry
 *  a raw "Fleet: {action} → {id}"-shaped string. New writes never match
 *  this (see _humanize_fleet_action in fleet_tools.py); this just keeps a
 *  stale row from ever surfacing plumbing instead of an honest empty. */
const RAW_INTERNAL_TITLE = /^Fleet:\s|ainstall_[a-z0-9]|(?:^|[\s:])ws_[a-z0-9]/i;

function activityPreviewText(agent: FleetAgent): string {
  const preview = (agent.activity_preview || "").trim();
  if (!preview || RAW_INTERNAL_TITLE.test(preview)) return "No activity yet";
  return preview;
}

/** Call right before navigating from a list row into an agent's detail page —
 *  paired with the read inside AgentsList below, so Esc/back into this list
 *  restores focus to the row the user came from instead of dropping focus
 *  back to <body>. */
export function rememberLastViewedAgent(agentId: string) {
  try { window.sessionStorage.setItem(LAST_VIEWED_KEY, agentId); } catch { /* best-effort */ }
}

function consumeLastViewedAgent(): string {
  try {
    const id = window.sessionStorage.getItem(LAST_VIEWED_KEY);
    if (id) window.sessionStorage.removeItem(LAST_VIEWED_KEY);
    return id || "";
  } catch {
    return "";
  }
}

// 4 decimal places, matching billing/page.tsx and FleetAgentDetail.tsx's cost
// formatters — real per-turn costs are fractions of a cent (e.g. $0.0003),
// which toFixed(2) always rounds down to a misleading "$0.00".
const money = (n: number) => `$${n.toFixed(4)}`;

// model_config → short brand-family label. Keeps the Brain column readable at
// a glance and identical width across rows ("Sonnet 5", "DeepSeek", "GPT-5") —
// raw ids ("deepseek-chat", "claude-3-5-sonnet-20241022") would blow the
// column out and force horizontal scrolling.
function brainLabel(config?: Record<string, any>): string {
  const raw = String(config?.model || config?.resolved_model || "").toLowerCase();
  const provider = String(config?.provider || "").toLowerCase();
  if (raw.includes("deepseek") || provider === "deepseek") return "DeepSeek";
  if (raw.includes("sonnet-5") || raw === "claude-sonnet-5") return "Sonnet 5";
  if (raw.includes("sonnet")) return "Sonnet";
  if (raw.includes("opus")) return "Opus";
  if (raw.includes("haiku")) return "Haiku";
  if (raw.includes("gpt-5")) return "GPT-5";
  if (raw.includes("gpt-4")) return "GPT-4";
  if (raw.includes("llama")) return "Llama";
  if (raw.includes("ollama") || provider === "ollama") return "Ollama";
  if (provider === "anthropic") return "Claude";
  if (provider === "openai") return "GPT";
  if (raw) return raw.length > 14 ? `${raw.slice(0, 14)}…` : raw;
  return "";
}

function channelAbbr(c: string): string {
  const l = c.toLowerCase();
  if (l.includes("telegram")) return "TG";
  if (l.includes("slack")) return "SL";
  if (l.includes("discord")) return "DC";
  if (l.includes("whatsapp")) return "WA";
  if (l.includes("wechat")) return "WC";
  return c.slice(0, 2).toUpperCase();
}

// "2m", "3h", "5d" — the compact tail from fleet-presentation's timeAgo,
// stripping the "ago" suffix so the Last-active column stays narrow.
function compactAgo(iso: string | null | undefined): string {
  if (!iso) return "";
  return timeAgo(iso).replace(/\s+ago$/, "");
}

/**
 * Fleet agent list — dense, column-aligned rows on a shared 6-column grid, with
 * a muted header row above and no per-agent cards. Every value has a column;
 * every empty column renders a deliberate placeholder (—, None, never, $0.00,
 * "No activity yet") instead of a naked dash floating in dead space.
 *
 * Grid: Agent(1fr, min 260) · Brain(120) · Channels(120) · Last active(96,
 * right) · Cost(84, right) · Status(132, right).
 *
 * Keyboard: roving tabindex across rows — ArrowUp/Down and j/k move focus,
 * Home/End jump to the ends, Enter/Space open (native button semantics).
 * Returning here after viewing an agent (Esc/back) restores focus to that
 * agent's row rather than resetting to the top.
 */
export function AgentsList({
  workspaceId,
  agents,
  costByAgent,
  projectById,
  groupByProject,
  onSelect,
  onAgentStoppedChanged,
}: {
  workspaceId: string;
  agents: FleetAgent[];
  costByAgent: Map<string, number>;
  projectById?: Map<string, FleetProject>;
  groupByProject?: boolean;
  onSelect: (agentId: string, projectId: string) => void;
  /** Called after a stop/resume mutation succeeds — the caller should
   *  re-fetch (useFleetAgents().refresh) so the row's status reflects it. */
  onAgentStoppedChanged?: () => void;
}) {
  const containerRef = useRef<HTMLDivElement>(null);
  const [activeRowId, setActiveRowId] = useState<string | null>(null);
  const restoreIdRef = useRef<string | null>(null);
  if (restoreIdRef.current === null) restoreIdRef.current = consumeLastViewedAgent();
  const consumedFocusRef = useRef(false);

  const restoreId = restoreIdRef.current;
  const fallbackId = (restoreId && agents.some((a) => a.agent_id === restoreId))
    ? restoreId
    : (agents[0]?.agent_id ?? null);
  const rovingId = (activeRowId && agents.some((a) => a.agent_id === activeRowId)) ? activeRowId : fallbackId;

  useEffect(() => {
    if (consumedFocusRef.current) return;
    if (!restoreId) { consumedFocusRef.current = true; return; }
    if (!agents.some((a) => a.agent_id === restoreId)) return;
    consumedFocusRef.current = true;
    const el = containerRef.current?.querySelector<HTMLDivElement>(`[data-agent-id="${CSS.escape(restoreId)}"]`);
    el?.focus();
  }, [agents, restoreId]);

  const onKeyDown = useCallback((e: React.KeyboardEvent<HTMLDivElement>) => {
    if (e.metaKey || e.ctrlKey || e.altKey) return;
    const lower = e.key.toLowerCase();
    const isDown = e.key === "ArrowDown" || lower === "j";
    const isUp = e.key === "ArrowUp" || lower === "k";
    const isHome = e.key === "Home";
    const isEnd = e.key === "End";
    if (!isDown && !isUp && !isHome && !isEnd) return;
    const rows = Array.from(containerRef.current?.querySelectorAll<HTMLDivElement>(".fleet-agent-row") || []);
    if (rows.length === 0) return;
    const current = rows.indexOf(document.activeElement as HTMLDivElement);
    const from = current >= 0 ? current : 0;
    let next = from;
    if (isDown) next = Math.min(rows.length - 1, from + 1);
    else if (isUp) next = Math.max(0, from - 1);
    else if (isHome) next = 0;
    else if (isEnd) next = rows.length - 1;
    e.preventDefault();
    // PrimaryRail also binds a raw j/k listener on window for its own G-then-key
    // chords, with no notion of "focus is inside a list that owns these keys
    // right now" — left alone, j/k here would double as rail-focus moves too,
    // and a stray Enter afterward would navigate the whole page via whatever
    // rail item that left focused. Stopping propagation keeps j/k/Home/End
    // scoped to this list while it has focus.
    e.stopPropagation();
    const nextRow = rows[next];
    const nextId = nextRow.dataset.agentId;
    if (nextId) setActiveRowId(nextId);
    nextRow.focus();
  }, []);

  const renderRow = (a: FleetAgent, index: number) => (
    <AgentRow
      key={a.agent_id}
      workspaceId={workspaceId}
      agent={a}
      index={index}
      cost={costByAgent.get(a.agent_id) || 0}
      onSelect={onSelect}
      onStoppedChanged={onAgentStoppedChanged}
      tabIndex={a.agent_id === rovingId ? 0 : -1}
    />
  );

  const body = groupByProject
    ? (() => {
        const groups = new Map<string, FleetAgent[]>();
        for (const a of agents) {
          const key = a.project_id || "";
          if (!groups.has(key)) groups.set(key, []);
          groups.get(key)!.push(a);
        }
        let flatIdx = 0;
        return Array.from(groups.entries()).map(([projectId, group]) => {
          const proj = projectId ? projectById?.get(projectId) : undefined;
          const projectLabel = proj?.name || "Ungrouped";
          return (
            <div key={projectId || "ungrouped"} className="fleet-agent-group">
              <div className="fleet-agent-group-header">
                <ProjectIcon icon={proj?.icon} tint={proj?.tint} size={16} glyphSize={10} />
                <span className="fleet-agent-group-name">{projectLabel}</span>
                <span className="fleet-agent-group-count">
                  · {group.length} {group.length === 1 ? "agent" : "agents"}
                </span>
              </div>
              {group.map((a) => renderRow(a, flatIdx++))}
            </div>
          );
        });
      })()
    : agents.map((a, i) => renderRow(a, i));

  return (
    <div
      className="fleet-agents-list"
      ref={containerRef}
      onKeyDown={onKeyDown}
    >
      <div className="fleet-agents-list-header" aria-hidden>
        <span>Agent</span>
        <span>Brain</span>
        <span>Channels</span>
        <span className="is-right">Last active</span>
        <span className="is-right">Cost</span>
        <span className="is-right">Status</span>
      </div>
      {body}
    </div>
  );
}

function AgentRow({
  workspaceId,
  agent,
  index,
  cost,
  onSelect,
  onStoppedChanged,
  tabIndex,
}: {
  workspaceId: string;
  agent: FleetAgent;
  index: number;
  cost: number;
  onSelect: (agentId: string, projectId: string) => void;
  onStoppedChanged?: () => void;
  tabIndex: number;
}) {
  const [busy, setBusy] = useState(false);
  const stopped = Boolean(agent.stopped?.active);
  const st = deriveStatus(agent.hardware_status || "unknown", stopped, Boolean(agent.current_run_id));
  const preset = (agent.capability_preset || agent.purpose_preset || "").toLowerCase().replace(/_/g, " ");
  const initial = (agent.label || "A").charAt(0).toUpperCase();
  const tint = tintForAgent(agent, index);
  const avatarStyle = {
    "--tile-bg": TINTS[tint].bg,
    "--tile-fg": TINTS[tint].fg,
  } as CSSProperties;
  const brain = brainLabel(agent.model_config);
  const channel = (agent.channel || "").trim();
  const relative = compactAgo(agent.last_activity);

  const activate = () => onSelect(agent.agent_id, agent.project_id || "");
  const handleKey = (e: KeyboardEvent<HTMLDivElement>) => {
    if (e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      activate();
    }
  };
  const toggleStop = async (e: React.MouseEvent) => {
    e.stopPropagation();
    if (busy) return;
    setBusy(true);
    const result = stopped
      ? await resumeFleetAgent(workspaceId, agent.agent_id)
      : await stopFleetAgent(workspaceId, agent.agent_id);
    setBusy(false);
    if (result.ok) onStoppedChanged?.();
  };

  return (
    <div
      role="button"
      className="fleet-agent-row"
      data-agent-id={agent.agent_id}
      tabIndex={tabIndex}
      onClick={activate}
      onKeyDown={handleKey}
    >
      <span className="fleet-agent-cell-agent">
        <StatusDot tone={st.tone} size={8} />
        <span className="fleet-agent-avatar" style={avatarStyle}>{initial}</span>
        <span className="fleet-agent-cell-agent-text">
          <span className="fleet-agent-cell-agent-line1">
            <span className="fleet-agent-name">{agent.label || "Unnamed agent"}</span>
            {preset && <span className="fleet-badge fleet-badge--preset">{preset}</span>}
          </span>
          <span className="fleet-agent-preview">{activityPreviewText(agent)}</span>
        </span>
      </span>

      <span className={`fleet-agent-cell-brain${brain ? "" : " fleet-cell-muted"}`}>
        {brain || "—"}
      </span>

      <span className="fleet-agent-cell-channels">
        {channel
          ? <span className="fleet-channel-chip">{channelAbbr(channel)}</span>
          : <span className="fleet-cell-muted">None</span>}
      </span>

      <span className={`fleet-agent-cell-right${relative ? "" : " fleet-cell-muted"}`}>
        {relative || "never"}
      </span>

      <span className={`fleet-agent-cell-right${cost > 0 ? "" : " fleet-cell-muted"}`}>
        {money(cost)}
      </span>

      <span className="fleet-agent-cell-status">
        <StatusDot tone={st.tone} size={8} />
        <span>{st.label}</span>
        <button
          type="button"
          className="fleet-agent-stop-btn"
          title={stopped ? "Resume agent" : "Stop agent"}
          aria-label={stopped ? `Resume ${agent.label || "agent"}` : `Stop ${agent.label || "agent"}`}
          disabled={busy}
          onClick={toggleStop}
        >
          {stopped ? <Play size={12} strokeWidth={2} /> : <Square size={12} strokeWidth={2} />}
        </button>
      </span>
    </div>
  );
}
