"use client";

import type { CSSProperties, KeyboardEvent } from "react";
import { useCallback, useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { Play, Square, Trash2 } from "lucide-react";

import { buildCookieAuthHeaders } from "@/lib/auth/csrf";

import { type FleetAgent, type FleetProject, resumeFleetAgent, stopFleetAgent } from "./fleet-data";
import { timeAgo, tintForAgent, TINTS } from "./fleet-presentation";
import { StatusChip, StatusDot, AgentSigil } from "./fleet-indicators";
import { ProjectIcon } from "./fleet-project-identity";
import { CHANNEL_ICONS, CHANNEL_LABELS } from "./fleet-icons";
import {
  type FleetGateway,
  deriveAgentStatus,
  gatewayId,
  hardwarePlacementIsBrainBound,
  resolveHardwarePlacement,
  useWorkspaceGateways,
} from "./gateway-box-picker";

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

// agent.channel is fleet_tools.py's _fetch_agent_channels() output: the
// agent's first enabled channel_key verbatim (the same keys CHANNEL_ICONS is
// keyed by — sage_telegram_hosted, slack, discord_bot, ...), plus a
// " +N" suffix when the agent has more than one enabled channel. Split those
// back apart so the icon lookup gets a clean key and the "+N" renders as its
// own compact pill rather than getting swallowed into the icon's alt text.
function parseChannelField(raw: string): { key: string; extra: number } {
  const trimmed = (raw || "").trim();
  const m = trimmed.match(/^(.*?)\s+\+(\d+)$/);
  if (m) return { key: m[1].trim(), extra: parseInt(m[2], 10) || 0 };
  return { key: trimmed, extra: 0 };
}

/** Channels cell contents — the brand icon for the agent's primary channel
 *  (falling back to a 2-letter chip for a channel_key CHANNEL_ICONS doesn't
 *  have an asset for yet, so an unrecognized key never renders blank), plus
 *  a "+N" pill when _fetch_agent_channels folded more enabled channels into
 *  this one string. The list API only ever returns that one key + a count,
 *  not the full set, so a true icon-per-channel row isn't possible from this
 *  data — "+N" is the honest compact stand-in for "and N more". */
function ChannelCell({ channel }: { channel: string }) {
  const trimmed = (channel || "").trim();
  if (!trimmed) return <span className="fleet-cell-muted">None</span>;
  const { key, extra } = parseChannelField(trimmed);
  const icon = CHANNEL_ICONS[key];
  const label = CHANNEL_LABELS[key] || key;
  return (
    <>
      {icon
        ? <img src={icon} alt="" width={16} height={16} title={label} />
        : <span className="fleet-channel-chip" title={label}>{key.slice(0, 2).toUpperCase()}</span>}
      {extra > 0 && (
        <span className="fleet-channel-chip" title={`+${extra} more channel${extra === 1 ? "" : "s"}`}>
          +{extra}
        </span>
      )}
    </>
  );
}

// hardware_access ("none" | "gateway" | "vps", legacy "all" normalized to
// "gateway" elsewhere) is the real placement enum the Hardware tab's picker
// writes (HardwareTab.tsx's PLACEMENT_OPTIONS: "Cloud only" / "Paired
// computer" / "Cloud VPS"). For a brain-bound agent (cli_subscription/local
// model — see hardwarePlacementIsBrainBound) placement instead follows
// model_config.gateway_binding, same as resolveHardwarePlacement's own
// brain-priority rule. Either way this collapses to the same short word the
// placement wizard's three kinds map to: cloud-only -> "Cloud", a Gateway
// box that IS a cloud VPS (FleetGateway.hardware_kind === "cloud_vps") ->
// "VPS", any other paired computer -> "Device". The full-precision fact
// (the real gateway display_name, or "Cloud", or a disconnected/unpaired
// explainer) is resolveHardwarePlacement's own `label` — never recomputed
// here, just reused as this badge's hover title so the two never disagree.
function resolvePlacementBadge(
  agent: FleetAgent,
  gateways: FleetGateway[],
): { short: "Cloud" | "VPS" | "Device"; display: string; full: string } {
  const placement = resolveHardwarePlacement(
    agent.hardware_access,
    agent.preferred_gateway_id,
    gateways,
    agent.model_config,
  );
  if (placement.tone === "cloud") return { short: "Cloud", display: "Cloud", full: placement.label };
  let short: "VPS" | "Device" = "Device";
  if (hardwarePlacementIsBrainBound(agent.model_config)) {
    const brainGatewayId = String(agent.model_config?.gateway_binding || "").trim();
    const match = brainGatewayId ? gateways.find((g) => gatewayId(g) === brainGatewayId) : undefined;
    short = match?.hardware_kind === "cloud_vps" ? "VPS" : "Device";
  } else {
    short = (agent.hardware_access || "").toLowerCase() === "vps" ? "VPS" : "Device";
  }
  // The chip shows the machine's OWN name ("Compass"), not the opaque category
  // word "Device" — that's what a person recognizes and what answers "where
  // does this run". placement.label is the real gateway display_name (or a
  // "(disconnected)"/"unpaired" explainer); truncate for the narrow column and
  // keep the full string as the hover title. Falls back to the category word
  // for the odd case where label somehow came back empty.
  const name = (placement.label || short).trim();
  const display = name.length > 18 ? `${name.slice(0, 17)}…` : name;
  return { short, display, full: placement.label };
}

// "2m", "3h", "5d" — the compact tail from fleet-presentation's timeAgo,
// stripping the "ago" suffix so the Last-active column stays narrow.
function compactAgo(iso: string | null | undefined): string {
  if (!iso) return "";
  return timeAgo(iso).replace(/\s+ago$/, "");
}

/** DELETE .../fleet/agents/{agentId} — owner-only, irreversible (see
 *  routes_fleet.py / fleet_tools.fleet_delete_agent). Written inline here
 *  rather than added to fleet-data.ts: this build's scope is deliberately
 *  limited to AgentsList.tsx so it doesn't collide with other in-flight
 *  edits to shared fleet files. Mirrors fleet-data.ts's own
 *  postFleetStopControl in shape (credentials + CSRF header + the same
 *  {ok:false, error} normalization on a non-2xx or {ok:false} body). */
async function deleteFleetAgentInline(
  workspaceId: string,
  agentId: string,
): Promise<{ ok: boolean; error?: string }> {
  try {
    const res = await fetch(
      `/api/w/${encodeURIComponent(workspaceId)}/fleet/agents/${encodeURIComponent(agentId)}`,
      {
        method: "DELETE",
        credentials: "include",
        headers: buildCookieAuthHeaders("DELETE"),
      },
    );
    const data = await res.json().catch(() => ({}));
    if (!res.ok || data?.ok === false) {
      return { ok: false, error: String(data?.error || data?.detail || `HTTP ${res.status}`) };
    }
    return { ok: true };
  } catch (e) {
    return { ok: false, error: e instanceof Error ? e.message : "Request failed" };
  }
}

/** Delete-agent confirmation — rendered via portal so its fixed overlay
 *  always covers the full viewport regardless of where the triggering row
 *  sits in the list. Reuses the existing .fleet-small-dialog/.fleet-btn
 *  design-system classes (fleet-theme.css) rather than introducing new
 *  ones, plus the shared --offline-* red tokens (lib/ui/theme-tokens.css)
 *  for the destructive accent — no stylesheet changes needed. */
function DeleteAgentDialog({
  agentName,
  busy,
  error,
  onCancel,
  onConfirm,
}: {
  agentName: string;
  busy: boolean;
  error: string | null;
  onCancel: () => void;
  onConfirm: () => void;
}) {
  useEffect(() => {
    const onKeyDown = (e: globalThis.KeyboardEvent) => {
      if (e.key === "Escape" && !busy) onCancel();
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [busy, onCancel]);

  if (typeof document === "undefined") return null;

  return createPortal(
    <div
      role="presentation"
      onClick={() => { if (!busy) onCancel(); }}
      style={{
        position: "fixed",
        inset: 0,
        background: "rgba(0, 0, 0, 0.5)",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        zIndex: 1000,
      }}
    >
      <div
        role="alertdialog"
        aria-modal="true"
        aria-labelledby="fleet-delete-agent-title"
        className="fleet-small-dialog"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="fleet-small-dialog-header">
          <span id="fleet-delete-agent-title" className="fleet-title">Delete agent</span>
        </div>
        <div className="fleet-small-dialog-body">
          <p style={{ margin: 0, fontSize: 13, color: "var(--text-primary)", lineHeight: 1.5 }}>
            Delete <strong>{agentName}</strong>? This removes its memory, credentials, and channel
            connections and can&apos;t be undone.
          </p>
          {error && (
            <p style={{ margin: 0, fontSize: 12, color: "var(--offline-text)" }}>{error}</p>
          )}
        </div>
        <div className="fleet-small-dialog-footer">
          <button type="button" className="fleet-btn" onClick={onCancel} disabled={busy}>
            Cancel
          </button>
          <button
            type="button"
            className="fleet-btn"
            style={{ color: "#fff", background: "var(--offline-dot)", borderColor: "var(--offline-dot)" }}
            onClick={onConfirm}
            disabled={busy}
          >
            {busy ? "Deleting…" : "Delete agent"}
          </button>
        </div>
      </div>
    </div>,
    document.body,
  );
}

/** Stop-agent confirmation — the row-level twin of the Stop-agent confirm
 *  dialog on the agent detail page (FleetAgentDetail.tsx's
 *  StopAgentControl): same blurred .fleet-detail-backdrop + .fleet-small-dialog
 *  + Cancel/.fleet-btn--danger shape, just reachable from the list row's
 *  inline stop toggle instead of the detail header. Resume is affirmative,
 *  not destructive — it never opens this, see AgentRow's onClick wiring below. */
function StopAgentDialog({
  agentName,
  busy,
  error,
  onCancel,
  onConfirm,
}: {
  agentName: string;
  busy: boolean;
  error: string | null;
  onCancel: () => void;
  onConfirm: () => void;
}) {
  useEffect(() => {
    const onKeyDown = (e: globalThis.KeyboardEvent) => {
      if (e.key === "Escape" && !busy) onCancel();
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [busy, onCancel]);

  if (typeof document === "undefined") return null;

  return createPortal(
    <div
      role="presentation"
      className="fleet-detail-backdrop"
      onClick={() => { if (!busy) onCancel(); }}
    >
      <div
        role="alertdialog"
        aria-modal="true"
        aria-labelledby="fleet-stop-agent-row-title"
        className="fleet-small-dialog"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="fleet-small-dialog-header">
          <span id="fleet-stop-agent-row-title" className="fleet-title">Stop agent</span>
        </div>
        <div className="fleet-small-dialog-body">
          <p style={{ margin: 0, fontSize: 13, color: "var(--text-primary)", lineHeight: 1.5 }}>
            Are you sure you want to stop <strong>{agentName}</strong>? It stops responding on
            every channel until you resume it.
          </p>
          {error && (
            <p style={{ margin: 0, fontSize: 12, color: "var(--offline-text)" }}>{error}</p>
          )}
        </div>
        <div className="fleet-small-dialog-footer">
          <button type="button" className="fleet-btn" onClick={onCancel} disabled={busy}>
            Cancel
          </button>
          <button
            type="button"
            className="fleet-btn fleet-btn--danger"
            onClick={onConfirm}
            disabled={busy}
          >
            {busy ? "Stopping…" : "Stop agent"}
          </button>
        </div>
      </div>
    </div>,
    document.body,
  );
}

/**
 * Fleet agent list — dense, column-aligned rows on a shared 7-column grid, with
 * a muted header row above and no per-agent cards. Every value has a column;
 * every empty column renders a deliberate placeholder (—, None, never, $0.00,
 * "No activity yet") instead of a naked dash floating in dead space.
 *
 * Grid: Agent(1fr, min 260) · Brain(120) · Placement(84) · Channels(120) ·
 * Last active(96, right) · Cost(84, right) · Status(132, right). Placement's
 * short word (Cloud/VPS/Device — see resolvePlacementBadge) carries the real
 * hardware name as its hover title, same as every other title-attribute
 * tooltip in this file (e.g. the stop/resume button below) — no separate
 * popover component for one fact.
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
  /** Called after a stop/resume/delete mutation succeeds — the caller should
   *  re-fetch (useFleetAgents().refresh) so the row's status reflects it (or,
   *  for delete, so the removed agent drops out of `agents` entirely). */
  onAgentStoppedChanged?: () => void;
}) {
  const containerRef = useRef<HTMLDivElement>(null);
  const [activeRowId, setActiveRowId] = useState<string | null>(null);
  // Same paired-Gateway fetch the Hardware tab and HardwareTab's own
  // placement picker use — needed here only to turn a placement-bound
  // agent's raw hardware_access/gateway_binding into resolvePlacementBadge's
  // short word + real hardware name (see that function below).
  const { gateways } = useWorkspaceGateways(workspaceId);
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
      gateways={gateways}
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
        <span className="fleet-col-brain">Brain</span>
        <span className="fleet-col-placement">Placement</span>
        <span className="fleet-col-channels">Channels</span>
        <span className="is-right fleet-col-last-active">Last active</span>
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
  gateways,
  onSelect,
  onStoppedChanged,
  tabIndex,
}: {
  workspaceId: string;
  agent: FleetAgent;
  index: number;
  cost: number;
  gateways: FleetGateway[];
  onSelect: (agentId: string, projectId: string) => void;
  onStoppedChanged?: () => void;
  tabIndex: number;
}) {
  const [busy, setBusy] = useState(false);
  const [stopConfirmOpen, setStopConfirmOpen] = useState(false);
  const [stopError, setStopError] = useState<string | null>(null);
  const [confirmOpen, setConfirmOpen] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [deleteError, setDeleteError] = useState<string | null>(null);
  const stopped = Boolean(agent.stopped?.active);
  // deriveAgentStatus (not bare deriveStatus): folds in the brain's real
  // runnability so a cli_subscription agent whose CLI isn't signed in reads
  // "Needs sign-in", never a false "Ready".
  const st = deriveAgentStatus(agent, gateways);
  // Sentence case ("customer facing" -> "Customer facing"), not Title Case —
  // the badge used to rely on CSS text-transform:capitalize for this, which
  // (a) title-cases every word, not just the first, and (b) silently never
  // applied at all here since ::first-letter doesn't run inside a flex
  // container (.fleet-badge is inline-flex). Doing it once in JS sidesteps
  // both problems.
  const presetRaw = (agent.capability_preset || agent.purpose_preset || "").toLowerCase().replace(/_/g, " ");
  const preset = presetRaw ? presetRaw.charAt(0).toUpperCase() + presetRaw.slice(1) : "";
  const tint = tintForAgent(agent, index);
  const avatarStyle = {
    "--tile-bg": TINTS[tint].bg,
    "--tile-fg": TINTS[tint].fg,
  } as CSSProperties;
  const brain = brainLabel(agent.model_config);
  const channel = (agent.channel || "").trim();
  const placement = resolvePlacementBadge(agent, gateways);
  const relative = compactAgo(agent.last_activity);
  // The workspace operator (Sage) is never deletable (see
  // fleet_tools.fleet_delete_agent's own guard) — don't even offer the
  // control for that row rather than showing an affordance that always errors.
  const isOperator = agent.role === "operator";
  const agentDisplayName = agent.label || "this agent";

  const activate = () => onSelect(agent.agent_id, agent.project_id || "");
  const handleKey = (e: KeyboardEvent<HTMLDivElement>) => {
    // Enter/Space bubbles up from the nested Resume/Stop and Delete buttons
    // below — without this guard, keyboard-activating either of them ALSO
    // fired activate() here, hijacking their own click handlers (e.g.
    // Delete would instead navigate to the agent detail page).
    if (e.target !== e.currentTarget) return;
    if (e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      activate();
    }
  };
  // Resume is affirmative — one click, no confirm. Stop is destructive —
  // clicking the row's Stop toggle opens a confirm dialog (requestStop)
  // instead of firing immediately; the actual stopFleetAgent call only
  // happens from the dialog's Stop button (confirmStop).
  const handleResumeClick = async (e: React.MouseEvent) => {
    e.stopPropagation();
    if (busy) return;
    setBusy(true);
    const result = await resumeFleetAgent(workspaceId, agent.agent_id);
    setBusy(false);
    if (result.ok) onStoppedChanged?.();
  };
  const requestStop = (e: React.MouseEvent) => {
    e.stopPropagation();
    if (busy) return;
    setStopError(null);
    setStopConfirmOpen(true);
  };
  const closeStopConfirm = useCallback(() => {
    if (busy) return;
    setStopConfirmOpen(false);
    setStopError(null);
  }, [busy]);
  const confirmStop = useCallback(async () => {
    setBusy(true);
    setStopError(null);
    const result = await stopFleetAgent(workspaceId, agent.agent_id);
    setBusy(false);
    if (result.ok) {
      setStopConfirmOpen(false);
      onStoppedChanged?.();
    } else {
      setStopError(result.error || "Could not stop this agent.");
    }
  }, [workspaceId, agent.agent_id, onStoppedChanged]);

  const openDeleteConfirm = (e: React.MouseEvent) => {
    e.stopPropagation();
    if (deleting) return;
    setDeleteError(null);
    setConfirmOpen(true);
  };
  const closeDeleteConfirm = useCallback(() => {
    if (deleting) return;
    setConfirmOpen(false);
    setDeleteError(null);
  }, [deleting]);
  const confirmDelete = useCallback(async () => {
    setDeleting(true);
    setDeleteError(null);
    const result = await deleteFleetAgentInline(workspaceId, agent.agent_id);
    setDeleting(false);
    if (result.ok) {
      setConfirmOpen(false);
      onStoppedChanged?.();
    } else {
      setDeleteError(result.error || "Failed to delete agent.");
    }
  }, [workspaceId, agent.agent_id, onStoppedChanged]);

  return (
    <>
    <div
      role="button"
      className="fleet-agent-row"
      data-agent-id={agent.agent_id}
      tabIndex={tabIndex}
      onClick={activate}
      onKeyDown={handleKey}
    >
      <span className="fleet-agent-cell-agent">
        <span className="fleet-agent-avatar" style={avatarStyle}>
          <AgentSigil seed={agent.agent_id} size={16} />
        </span>
        <span className="fleet-agent-cell-agent-text">
          <span className="fleet-agent-cell-agent-line1">
            <span className="fleet-agent-name">{agent.label || "Unnamed agent"}</span>
            {preset && <span className="fleet-badge fleet-badge--preset">{preset}</span>}
          </span>
          <span className="fleet-agent-preview">{activityPreviewText(agent)}</span>
        </span>
      </span>

      <span className={`fleet-agent-cell-brain fleet-col-brain${brain ? "" : " fleet-cell-muted"}`}>
        {brain || "—"}
      </span>

      <span className="fleet-agent-cell-placement fleet-col-placement">
        <span className="fleet-channel-chip" title={placement.full}>{placement.display}</span>
      </span>

      <span className="fleet-agent-cell-channels fleet-col-channels">
        <ChannelCell channel={channel} />
      </span>

      <span className={`fleet-agent-cell-right fleet-col-last-active${relative ? "" : " fleet-cell-muted"}`}>
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
          onClick={stopped ? handleResumeClick : requestStop}
        >
          {stopped ? <Play size={12} strokeWidth={2} /> : <Square size={12} strokeWidth={2} />}
        </button>
        {!isOperator && (
          <button
            type="button"
            className="fleet-agent-stop-btn"
            style={{ color: "var(--offline-text)" }}
            title="Delete agent"
            aria-label={`Delete ${agent.label || "agent"}`}
            disabled={deleting}
            onClick={openDeleteConfirm}
          >
            <Trash2 size={12} strokeWidth={2} />
          </button>
        )}
      </span>

      {/* UI Contract Part 2 "Agent rows": a real two-line mobile design, not
          the desktop grid cells above squeezed sideways — hidden on
          desktop, shown in place of all the cells above under
          fleet-theme.css's mobile media query. Stop/resume lives on the
          agent detail page's own topbar action on mobile (already real,
          already reachable) rather than a third inline control competing
          for room in a 2-line row the contract didn't spec one into. */}
      <div className="fleet-agent-row-mobile">
        <div className="fleet-agent-row-mobile-line1">
          <span className="fleet-agent-row-mobile-name">{agent.label || "Unnamed agent"}</span>
          <StatusChip tone={st.tone} label={st.label} />
        </div>
        <div className="fleet-agent-row-mobile-line2">
          {[brain || "—", money(cost), relative || "never"].join(" · ")}
        </div>
      </div>
    </div>
    {stopConfirmOpen && (
      <StopAgentDialog
        agentName={agentDisplayName}
        busy={busy}
        error={stopError}
        onCancel={closeStopConfirm}
        onConfirm={confirmStop}
      />
    )}
    {confirmOpen && (
      <DeleteAgentDialog
        agentName={agentDisplayName}
        busy={deleting}
        error={deleteError}
        onCancel={closeDeleteConfirm}
        onConfirm={confirmDelete}
      />
    )}
    </>
  );
}
