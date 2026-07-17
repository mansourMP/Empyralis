"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import {
  Brain,
  Check,
  ChevronRight,
  Clock,
  Cpu,
  Inbox,
  LayoutGrid,
  Loader2,
  Lock,
  MessageSquare,
  PanelRightOpen,
  Pencil,
  Play,
  Plug,
  Radio,
  Sparkles,
  Square,
  Trash2,
  Wrench,
  X,
  type LucideIcon,
} from "lucide-react";

import { WorkTab } from "./tabs/WorkTab";
import { HardwareTab } from "./tabs/HardwareTab";
import { MemoryTab } from "./tabs/MemoryTab";
import { AgentChat } from "./AgentChat";

import {
  resumeFleetAgent,
  stopFleetAgent,
  useFleetAgentActivity,
  useFleetAgentChannels,
  useFleetAgentConnectors,
  useFleetAgentTools,
  useFleetAgentSchedule,
  previewFleetAgentSchedule,
  createFleetAgentSchedule,
  deleteFleetAgentSchedule,
  type FleetAgent,
  type FleetAgentActivity,
  type FleetChannel,
  type FleetTool,
  type FleetScheduleItem,
} from "./fleet-data";
import { deriveStatus, timeAgo, formatDate, formatDateTime, formatTime, formatNumber, type AgentStatusTone } from "./fleet-presentation";
import { StatusChip, StatusDot } from "./fleet-indicators";
import { PanelSection, PanelRow, FleetRightPanel, type PanelValueTone } from "./FleetRightPanel";
import { UsageStat, bucketSeries, type UsageBucket } from "./fleet-sparkline";
import { HeaderAction } from "./Breadcrumbs";
import { CHANNEL_ICONS } from "./fleet-icons";
import { ConnectorPicker } from "./ConnectorPicker";
import { buildCookieAuthHeaders } from "@/lib/auth/csrf";
import { providerLabel } from "./fleet-provider-constants";
import { RUNTIME_LABELS } from "./gateway-box-picker";

/** Single source of truth for "what should this agent's Model summary say" —
 *  used by both the sidebar's permanent one-line Model row and the Model
 *  tab's own Provider/Model fields, so they can't independently drift the
 *  way they did before: the sidebar had no cli_subscription case at all and
 *  fell straight through to "Platform default" even when Codex was
 *  correctly bound to a paired Gateway. */
function resolveAgentModelSummary(modelConfig: Record<string, any> | undefined | null): {
  provider: string;
  model: string;
  isPlatformDefault: boolean;
  /** model_config.reasoning_effort, or "" when unset OR when the mode
   *  doesn't apply it (cli_subscription/local — see REASONING_EFFORT_
   *  SUPPORTED_MODES). Kept off the summary for those two modes so it
   *  never implies an effect that isn't real yet. */
  reasoningEffort: string;
} {
  const config = modelConfig || {};
  const mode = config.mode;
  const reasoningEffort = REASONING_EFFORT_SUPPORTED_MODES.has(mode) ? String(config.reasoning_effort || "") : "";
  if (mode === "cli_subscription") {
    const runtime: "claude_code" | "codex" = config.runtime === "codex" ? "codex" : "claude_code";
    const provider = config.provider ? providerLabel(config.provider) : RUNTIME_LABELS[runtime];
    return { provider, model: config.model || "CLI default", isPlatformDefault: false, reasoningEffort };
  }
  if (mode === "local") {
    return { provider: "Local", model: config.model || "Ollama", isPlatformDefault: false, reasoningEffort };
  }
  if (config.provider || config.model || config.resolved_model) {
    return {
      provider: config.provider ? providerLabel(config.provider) : (config.resolved_provider_label || "Platform default"),
      model: config.model || config.resolved_model || "Default",
      isPlatformDefault: false,
      reasoningEffort,
    };
  }
  return { provider: "Platform default", model: "Platform default", isPlatformDefault: true, reasoningEffort };
}

function formatModelSummaryLine(summary: ReturnType<typeof resolveAgentModelSummary>): string {
  const base = summary.isPlatformDefault
    ? "Platform default"
    : summary.provider === summary.model
      ? summary.model
      : `${summary.provider} · ${summary.model}`;
  return summary.reasoningEffort ? `${base} · ${reasoningEffortLabel(summary.reasoningEffort)} reasoning` : base;
}

// Properties panel's Placement row used to render placement.label with no
// tone at all — the one row on that panel that never went red/green, even
// though the adjacent Status row does. PanelRow's tone vocabulary is
// smaller than HardwarePlacementTone's (no "degraded"), so this collapses
// onto the closest existing bucket the same way HardwareTab's own
// placement dot already does (see gateway-box-picker.tsx's
// resolveHardwarePlacement + HardwareTab.tsx's dotClass): cloud/online read
// as healthy, everything else — degraded, offline, or never-paired — reads
// as offline so a disconnected/unpaired box is visually distinct.
const HARDWARE_PLACEMENT_PANEL_TONE: Record<HardwarePlacementTone, PanelValueTone> = {
  cloud: "online",
  online: "online",
  degraded: "offline",
  offline: "offline",
  unpaired: "offline",
};

// The backend's channels[].connected for Slack (routes_fleet.py's
// fleet_agent_channels) reflects OAuth app install only — a workspace can
// have the Slack app installed with no channel bound to THIS agent yet.
// Every reader of "is this platform really connected" (the Properties
// count below, the channel grid pill, the "already connected" banner
// prefill) needs to agree that Slack isn't connected until it owns a
// channel — slackChannelBinding, this same fetch's endpoint_key equivalent
// — not merely OAuth-installed.
//
// Telegram has the mirror-image gap: sage_telegram_hosted's own `connected`
// only reflects the hosted-bot door (Sage's shared bot, deep-linked to this
// agent). A BYO bot token (this agent's own bot, see CHANNEL_DOORS'
// sage_telegram_hosted.byo_bot) lives entirely in telegramBotConnected —
// same "rides along on this fetch instead of the channel's own row" shape
// as slackChannelBinding — so a live BYO-bot agent read as connected: false
// everywhere here: undercounted in the Properties "Channels" total, and the
// grid pill showed "Set up" instead of "Connected".
function isChannelConnected(
  channel: Pick<FleetChannel, "id" | "connected">,
  slackChannelBinding: string | null,
  telegramBotConnected: boolean,
): boolean {
  if (channel.id === "slack") return Boolean(slackChannelBinding);
  if (channel.id === "sage_telegram_hosted") return channel.connected || telegramBotConnected;
  return channel.connected;
}

type TabId = "overview" | "work" | "channels" | "connectors" | "hardware" | "model" | "memory" | "tools" | "chat";

// "model" sits right after "overview" (was 7th of 8, second-to-last) — a
// live complaint was "where is the button that says choose the model and
// its reasoning??" .fleet-detail-toptabs scrolls horizontally with NO
// visible scrollbar (fleet-theme.css — only a fade cue on mobile), so a tab
// this far right was easy to miss entirely, not just easy to overlook.
// Model/brain choice is foundational setup, not a deep-cut settings page —
// it belongs near the front, same tier as Overview.
const TABS: { id: TabId; label: string; icon: LucideIcon }[] = [
  { id: "overview", label: "Overview", icon: LayoutGrid },
  { id: "model", label: "Model", icon: Sparkles },
  { id: "work", label: "Work", icon: Inbox },
  { id: "channels", label: "Channels", icon: Radio },
  { id: "connectors", label: "Connectors", icon: Plug },
  { id: "tools", label: "Tools", icon: Wrench },
  { id: "hardware", label: "Hardware", icon: Cpu },
  { id: "memory", label: "Memory", icon: Brain },
];

/**
 * Agent detail — a routed page (top tabs + a permanent properties panel that
 * never reflows the content column). Every tab has real data or an
 * intentional empty state with a working next action. No stubs.
 */
export function FleetAgentDetail({
  workspaceId,
  agentId,
  agent,
  projectName,
  onChat,
  initialTab,
  onTabChange,
  onRenamed,
}: {
  workspaceId: string;
  agentId: string;
  agent: FleetAgent | null;
  /** Resolved project display name — passed by the routed page (from the URL's
   *  projectId, so it's available on first paint independent of the agents
   *  fetch). undefined = still resolving (shows a loading placeholder); pass
   *  "—" explicitly when there genuinely is no project (e.g. Sage). NEVER
   *  fall back to the raw project_id here — that's the "raw ids on first
   *  paint" bug. */
  projectName?: string;
  onChat: (agentId: string) => void;
  initialTab?: TabId;
  onTabChange?: (tab: TabId) => void;
  /** Called after a successful inline rename (Overview title) so the caller
   *  can refresh whatever list/breadcrumb sources agent.label. */
  onRenamed?: () => void;
}) {
  const [activeTab, setActiveTab] = useState<TabId>(initialTab || "overview");
  // Chat tab's mobile-only properties drawer (see propertiesContent below) —
  // every other tab keeps the permanent column, so this stays false and unused there.
  const [mobilePropertiesOpen, setMobilePropertiesOpen] = useState(false);
  const { events, loading: activityLoading } = useFleetAgentActivity(workspaceId, agentId);
  const { channels, refresh: refreshChannels, telegramBotConnected, slackChannelBinding } = useFleetAgentChannels(workspaceId, agentId);
  const { connectors } = useFleetAgentConnectors(workspaceId, agentId);
  const [costToday, setCostToday] = useState<number | null>(null);
  const [costBuckets, setCostBuckets] = useState<UsageBucket[]>([]);

  const status = deriveStatus(agent?.hardware_status || "unknown", agent?.stopped?.active, Boolean(agent?.current_run_id));

  useEffect(() => {
    let cancelled = false;
    fetch(`/api/w/${encodeURIComponent(workspaceId)}/fleet/usage?scope=agent&id=${encodeURIComponent(agentId)}&period=day`, { credentials: "include" })
      .then((r) => (r.ok ? r.json() : null))
      .then((d) => {
        if (cancelled || !d) return;
        if (d.totals) setCostToday(Number(d.totals.usd_cost || 0));
        if (Array.isArray(d.buckets)) setCostBuckets(d.buckets);
      })
      .catch(() => {});
    return () => { cancelled = true; };
  }, [workspaceId, agentId]);

  const connectedChannels = channels.filter((c) => isChannelConnected(c, slackChannelBinding, telegramBotConnected)).length;
  const connectedConnectors = connectors.filter((c: any) => c?.connected).length;
  const resolvedModel = formatModelSummaryLine(resolveAgentModelSummary(agent?.model_config));
  // Lives in the permanent properties column now, so it's computed once
  // here rather than per-tab — every tab shows the same placement/role,
  // not just Overview. Brain placement (model_config.gateway_binding, for
  // cli_subscription/local agents) wins over tool-hardware placement
  // (hardware_access + preferred_gateway_id) — see resolveHardwarePlacement.
  // Never runtime_target, which stays pinned to a runtime_profile FK real
  // Fleet agents never update (see docs/HARDWARE-BRAIN-REALITY-REPORT.md).
  const { gateways } = useWorkspaceGateways(workspaceId);
  const placement = resolveHardwarePlacement(agent?.hardware_access, agent?.preferred_gateway_id, gateways, agent?.model_config);
  const role = agent?.role || "agent";
  const isMaster = role === "operator";
  const { tools: agentTools } = useFleetAgentTools(workspaceId, agentId);
  // Truth Map B1 (mirrors ToolsTab's identical requiredConnector/
  // connectorMissing check): a tool bound behind requires_connector does
  // nothing until that connector is actually connected, so it shouldn't
  // count toward "customer access" just because it's enabled+granted.
  const connectorById = new Map(connectors.map((c) => [c.id, c]));
  const customerAccessCount = agentTools.filter((t) => {
    if (!(t.enabled && (t.audience_safe || t.mandate_granted))) return false;
    const requiredConnector = t.requires_connector ? connectorById.get(t.requires_connector) : undefined;
    const connectorMissing = Boolean(t.requires_connector) && !requiredConnector?.connected;
    return !connectorMissing;
  }).length;

  // Page mode: keep the active tab in sync with the URL {tab} segment.
  useEffect(() => {
    if (initialTab) setActiveTab(initialTab);
  }, [initialTab]);

  const selectTab = useCallback(
    (tab: TabId) => {
      setActiveTab(tab);
      onTabChange?.(tab);
    },
    [onTabChange],
  );

  // Esc returns to wherever the user came from (browser back) — not a
  // hardcoded destination, so the flat /agents list, a project's list, or
  // any other referrer all restore correctly. Skipped while typing (so it
  // never discards an in-progress edit) and while a nested dialog is
  // capturing the key itself (see ChannelsTab's banner, which stops
  // propagation before this ever sees it).
  const router = useRouter();
  const onPageKeyDown = useCallback(
    (e: React.KeyboardEvent) => {
      if (e.key !== "Escape") return;
      const el = document.activeElement as HTMLElement | null;
      const isTyping = Boolean(el && (el.tagName === "INPUT" || el.tagName === "TEXTAREA" || el.isContentEditable));
      if (isTyping) return;
      router.back();
    },
    [router],
  );

  // Land keyboard/SR focus somewhere deliberate on mount rather than leaving
  // it on <body> — the active tab pill, since it's already the natural next
  // stop for arrow/tab navigation. Only on first mount, not on every tab
  // switch (selectTab already moves visible state; native click/router
  // navigation already handles focus for those).
  const activeTabRef = useRef<HTMLButtonElement | null>(null);
  useEffect(() => {
    activeTabRef.current?.focus();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Permanent — space is ALWAYS reserved, a real flex sibling of the tab
  // body, never an overlay/toggle (that pattern stays on LIST pages only;
  // see FleetToolbar's panelOpen/onTogglePanel props). Fills what used to be
  // a blank right half at every viewport width instead of hiding behind a
  // click. The one exception is the Chat tab's mobile layout (<=768px):
  // that tab needs its full viewport height for the pinned composer (see
  // fleet-theme.css's Chat-tab mobile override), so there isn't room for a
  // stacked-below column like every other tab gets. Rather than hide
  // Properties with no access route at all, that combination alone swaps
  // to the toggle-opened drawer below (mobilePropertiesOpen) — same content,
  // reused via propertiesContent so both surfaces can never drift apart.
  const propertiesContent = (
    <PanelSection title="Properties">
      <PanelRow label="Status" value={<StatusChip tone={status.tone} label={status.label} />} />
      <PanelRow label="Placement" value={placement.label} tone={HARDWARE_PLACEMENT_PANEL_TONE[placement.tone]} />
      <PanelRow label="Role" value={<span style={{ textTransform: "capitalize" }}>{role}</span>} />
      {!isMaster && (
        <PanelRow
          label="Customer access"
          value={`${customerAccessCount} ${customerAccessCount === 1 ? "tool" : "tools"}`}
          tone={customerAccessCount > 0 ? "default" : "muted"}
          hint="Everyone who messages this agent is a customer at support-tier — they can request, not command. You, the owner, keep full access."
        />
      )}
      <PanelRow label="Model" value={resolvedModel} />
      <UsageStat
        label="Cost today"
        total={costToday ?? 0}
        formattedTotal={costToday === null ? "…" : `$${costToday.toFixed(4)}`}
        values={bucketSeries(costBuckets, "usd_cost")}
      />
      <PanelRow label="Channels" value={connectedChannels} />
      <PanelRow label="Connectors" value={connectedConnectors} />
    </PanelSection>
  );
  const propertiesPanel = (
    <aside className="fleet-detail-properties" aria-label="Properties">
      {propertiesContent}
    </aside>
  );

  const inner = (
    <>
      {/* Tabs live at the TOP, under the breadcrumb — one navigation only.
          No trailing action here on desktop: the properties column to the
          right is permanent on detail pages, never a toggle (that pattern is
          LIST pages only — see FleetToolbar). The lone exception is a
          mobile-only Properties toggle on the Chat tab (see propertiesContent
          above) — CSS keeps it hidden except at <=768px. */}
      <div className="fleet-detail-tabbar">
        <nav className="fleet-detail-toptabs" aria-label="Agent sections">
          {TABS.map((tab) => {
            const Icon = tab.icon;
            const isActive = activeTab === tab.id;
            return (
              <button
                key={tab.id}
                ref={isActive ? activeTabRef : undefined}
                type="button"
                className={`fleet-detail-toptab${isActive ? " is-active" : ""}`}
                onClick={() => selectTab(tab.id)}
                aria-current={isActive ? "page" : undefined}
              >
                <Icon size={15} strokeWidth={1.75} />
                <span>{tab.label}</span>
              </button>
            );
          })}
        </nav>
        {activeTab === "chat" && (
          <button
            type="button"
            className="fleet-icon-btn fleet-detail-properties-toggle"
            onClick={() => setMobilePropertiesOpen(true)}
            aria-label="Show properties"
            title="Properties"
          >
            <PanelRightOpen size={16} strokeWidth={1.75} />
          </button>
        )}
      </div>

      {/* Columns: main content sheet + the permanent properties column, a
          real flex sibling that always reserves its width — it never opens,
          closes, or reflows the sheet next to it (desktop/tablet, and every
          mobile tab except Chat — see propertiesContent above). */}
      <div className="fleet-detail-columns">
        <div className="fleet-detail-body">
          {activeTab === "overview" && (
            <OverviewTab
              workspaceId={workspaceId}
              agentId={agentId}
              agent={agent}
              status={status}
              isMaster={isMaster}
              events={events}
              loading={activityLoading}
              onChat={() => onChat(agentId)}
              onRenamed={onRenamed}
            />
          )}
          {activeTab === "work" && <WorkTab workspaceId={workspaceId} agentId={agentId} agent={agent} />}
          {activeTab === "channels" && (
            <ChannelsTab workspaceId={workspaceId} agentId={agentId} agent={agent} onChannelsChanged={refreshChannels} />
          )}
          {activeTab === "connectors" && (
            <ConnectorsTab workspaceId={workspaceId} agentId={agentId} agent={agent} />
          )}
          {activeTab === "tools" && (
            <ToolsTab workspaceId={workspaceId} agentId={agentId} agent={agent} onChat={() => onChat(agentId)} />
          )}
          {activeTab === "hardware" && (
            <HardwareTab workspaceId={workspaceId} agentId={agentId} agent={agent} onSaved={onRenamed} />
          )}
          {activeTab === "model" && <ModelTab workspaceId={workspaceId} agentId={agentId} agent={agent} onSaved={onRenamed} />}
          {activeTab === "memory" && (
            <MemoryTab workspaceId={workspaceId} agentId={agentId} agent={agent} onChat={() => onChat(agentId)} />
          )}
          {activeTab === "chat" && <ChatTab workspaceId={workspaceId} agentId={agentId} agent={agent} />}
        </div>
        {propertiesPanel}
        {activeTab === "chat" && (
          <FleetRightPanel open={mobilePropertiesOpen} onClose={() => setMobilePropertiesOpen(false)}>
            {propertiesContent}
          </FleetRightPanel>
        )}
      </div>
    </>
  );

  return (
    <>
      {activeTab === "overview" && (
        <HeaderAction>
          <StopAgentControl workspaceId={workspaceId} agentId={agentId} agent={agent} onChanged={onRenamed} />
          <button type="button" className="fleet-btn fleet-btn--accent" onClick={() => onChat(agentId)}>
            <MessageSquare size={14} strokeWidth={1.75} />
            Chat with this agent
          </button>
        </HeaderAction>
      )}
      <div
        className="fleet-detail fleet-detail--page"
        aria-label={`${agent?.label || "Agent"} details`}
        onKeyDown={onPageKeyDown}
      >
        {inner}
      </div>
    </>
  );
}

// ── Overview ────────────────────────────────────────────────────────────────

/** One compact status LINE, one sentence, one status vocabulary
 *  (Ready / Working / Stopped / Offline / Error) — the exact same `status`
 *  the Agents list, Project rows, the inline config row, and the Properties
 *  panel all derive from, so this never reads differently from any of them.
 *  No second chip here: a dot (color) plus a sentence (words), never two
 *  competing labels for the same field. Every clause is honest-when-empty —
 *  a never-run agent reads "Ready · no activity yet", not a stale claim. */
function NowStrip({ agent, status }: { agent: FleetAgent | null; status: { tone: AgentStatusTone; label: string } }) {
  if (!agent) return null;
  const sentence = (() => {
    if (status.tone === "working") return "Working on a task";
    if (status.tone === "stopped") return `Stopped by ${agent.stopped?.stopped_by_label || "an owner"}`;
    if (status.tone === "ready") {
      return agent.last_activity ? `Ready · last active ${timeAgo(agent.last_activity)}` : "Ready · no activity yet";
    }
    if (status.tone === "offline") {
      return agent.last_heartbeat ? `Offline · last heartbeat ${timeAgo(agent.last_heartbeat)}` : "Offline · never heartbeat";
    }
    return status.label; // Error, Not deployed
  })();
  return (
    <div className="fleet-now-strip" aria-label="Current status">
      <StatusDot tone={status.tone} size={7} />
      <span className="fleet-now-meta">{sentence}</span>
    </div>
  );
}

// Owner-only stop control — the header-level "Stop agent" button, and once
// stopped, an honest chip naming who stopped it (never a bare "Stopped").
// kill_switch_gate.py enforces this on the backend before any turn runs;
// this is purely the control surface.
//
// Stopping is a real, disruptive action (the agent goes dark on every
// channel until resumed) so it's gated behind a confirm dialog — a blurred/
// dimmed backdrop (.fleet-detail-backdrop, the same primitive the channel-
// connect banner below uses) plus a small centered card (.fleet-small-dialog),
// Cancel (neutral) + Stop agent (.fleet-btn--danger, solid red). Resume is
// affirmative, not destructive — it stays a plain one-click button, no confirm.
function StopAgentControl({
  workspaceId, agentId, agent, onChanged,
}: { workspaceId: string; agentId: string; agent: FleetAgent | null; onChanged?: () => void }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [confirmOpen, setConfirmOpen] = useState(false);
  const stopped = agent?.stopped;

  async function handleStop() {
    setBusy(true);
    setError(null);
    const result = await stopFleetAgent(workspaceId, agentId);
    setBusy(false);
    if (result.ok) {
      setConfirmOpen(false);
      onChanged?.();
    } else {
      setError(result.error || "Could not stop this agent.");
    }
  }

  async function handleResume() {
    setBusy(true);
    setError(null);
    const result = await resumeFleetAgent(workspaceId, agentId);
    setBusy(false);
    if (result.ok) onChanged?.();
    else setError(result.error || "Could not resume this agent.");
  }

  // Close the confirm dialog on Escape (own the key so it doesn't bubble to
  // the page's own "Esc goes back" handler — same reasoning as the channel
  // banner below).
  useEffect(() => {
    if (!confirmOpen) return;
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Escape" && !busy) {
        e.stopPropagation();
        setConfirmOpen(false);
      }
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [confirmOpen, busy]);

  if (stopped?.active) {
    return (
      <div className="fleet-stop-control">
        <span className="fleet-stop-chip" title={stopped.reason || undefined}>
          <Square size={12} strokeWidth={2} />
          Stopped by {stopped.stopped_by_label || "an owner"}
        </span>
        <button type="button" className="fleet-btn" disabled={busy} onClick={handleResume}>
          {busy ? <Loader2 size={14} style={{ animation: "spin 1s linear infinite" }} /> : <Play size={14} strokeWidth={1.75} />}
          Resume
        </button>
        {error && <span className="fleet-stop-error">{error}</span>}
      </div>
    );
  }

  return (
    <div className="fleet-stop-control">
      <button
        type="button"
        className="fleet-btn"
        disabled={busy}
        onClick={() => { setError(null); setConfirmOpen(true); }}
      >
        <Square size={14} strokeWidth={1.75} />
        Stop agent
      </button>
      {error && !confirmOpen && <span className="fleet-stop-error">{error}</span>}

      {confirmOpen && (
        <div
          className="fleet-detail-backdrop"
          onClick={() => { if (!busy) setConfirmOpen(false); }}
        >
          <div
            role="alertdialog"
            aria-modal="true"
            aria-labelledby="fleet-stop-agent-title"
            className="fleet-small-dialog"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="fleet-small-dialog-header">
              <span id="fleet-stop-agent-title" className="fleet-title">Stop agent</span>
            </div>
            <div className="fleet-small-dialog-body">
              <p style={{ margin: 0, fontSize: 13, color: "var(--text-primary)", lineHeight: 1.5 }}>
                Are you sure you want to stop <strong>{agent?.label || "this agent"}</strong>?
                It stops responding on every channel until you resume it.
              </p>
              {error && (
                <p style={{ margin: 0, fontSize: 12, color: "var(--offline-text)" }}>{error}</p>
              )}
            </div>
            <div className="fleet-small-dialog-footer">
              <button type="button" className="fleet-btn" onClick={() => setConfirmOpen(false)} disabled={busy}>
                Cancel
              </button>
              <button type="button" className="fleet-btn fleet-btn--danger" onClick={handleStop} disabled={busy}>
                {busy ? <Loader2 size={14} style={{ animation: "spin 1s linear infinite" }} /> : <Square size={14} strokeWidth={1.75} />}
                {busy ? "Stopping…" : "Stop agent"}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

// U3-H: the warn dot used to fire on anything status !== "logged" — backwards
// for a routine "completed" event (specialist/sage turns log "logged";
// nothing else does, so every other successful status read as a warning).
// An explicit bad-status allowlist instead: only genuinely bad outcomes turn
// the dot red.
const BAD_ACTIVITY_STATUSES = new Set(["error", "failed", "blocked"]);
function isBadActivityStatus(status: string): boolean {
  return BAD_ACTIVITY_STATUSES.has(status.toLowerCase());
}

/** activity_ledger_events rows are already ORDER BY created_at DESC from the
 *  backend — grouping by day in iteration order preserves that ordering
 *  without a separate sort. */
function groupActivityByDay(events: FleetAgentActivity[]): { day: string; items: FleetAgentActivity[] }[] {
  const groups: { day: string; items: FleetAgentActivity[] }[] = [];
  const byDay = new Map<string, FleetAgentActivity[]>();
  for (const event of events) {
    const parsed = new Date(event.created_at);
    const day = Number.isNaN(parsed.getTime())
      ? "Unknown date"
      : formatDate(parsed, { weekday: "long", month: "long", day: "numeric" });
    if (!byDay.has(day)) {
      byDay.set(day, []);
      groups.push({ day, items: byDay.get(day)! });
    }
    byDay.get(day)!.push(event);
  }
  return groups;
}

function OverviewTab({
  workspaceId,
  agentId,
  agent,
  status,
  isMaster,
  events,
  loading,
  onRenamed,
}: {
  workspaceId: string;
  agentId: string;
  agent: FleetAgent | null;
  status: { tone: AgentStatusTone; label: string };
  isMaster: boolean;
  events: FleetAgentActivity[];
  loading: boolean;
  onChat: () => void;
  onRenamed?: () => void;
}) {
  const dayGroups = groupActivityByDay(events);

  return (
    <div className="fleet-detail-overview">
      {agent && (
        <AgentTitle workspaceId={workspaceId} agentId={agentId} label={agent.label || ""} onRenamed={onRenamed} />
      )}
      <NowStrip agent={agent} status={status} />

      {/* Minimal main column — Status/Placement/Role/Customer access now
          live in the permanent properties column (see FleetAgentDetail's
          propertiesPanel); this column is just the THING itself: persona,
          schedule, activity. */}
      {!isMaster && agent && (
        <PersonaEditor workspaceId={workspaceId} agentId={agentId} agent={agent} />
      )}

      {!isMaster && agent && (
        <ScheduleSection workspaceId={workspaceId} agentId={agentId} />
      )}

      <div className="fleet-detail-section-title">Recent activity</div>
      {loading ? (
        <div className="fleet-activity-skeleton" aria-label="Loading activity">
          {[68, 52, 60].map((w, i) => (
            <div key={i} className="fleet-skeleton-row">
              <div className="fleet-skeleton-bar" style={{ width: 8 }} />
              <div style={{ flex: 1 }}>
                <div className="fleet-skeleton-bar" style={{ width: `${w}%`, marginBottom: 6 }} />
                <div className="fleet-skeleton-bar" style={{ width: `${w - 24}%`, opacity: 0.6 }} />
              </div>
            </div>
          ))}
        </div>
      ) : events.length === 0 ? (
        <EmptyState
          icon={Inbox}
          title="No activity yet"
          body="Events appear here after this agent processes its first turn. Use “Chat with this agent” to send the first one."
        />
      ) : (
        <div className="fleet-activity">
          {dayGroups.map((group) => (
            <div key={group.day} className="fleet-activity-day-group">
              <div className="fleet-activity-day-heading">{group.day}</div>
              {group.items.map((event) => (
                <div key={event.event_id} className="fleet-activity-item">
                  <div className={`fleet-activity-dot${isBadActivityStatus(event.status) ? " is-warn" : ""}`} />
                  <div style={{ flex: 1, minWidth: 0 }}>
                    <div className="fleet-activity-title">{event.title}</div>
                    <div className="fleet-activity-meta">
                      <span>{event.event_class}</span>
                      <span>·</span>
                      <span>{event.action}</span>
                      {event.channel && (
                        <>
                          <span>·</span>
                          <span>via {event.channel}</span>
                        </>
                      )}
                      <span>·</span>
                      <span className="fleet-activity-time">
                        {formatTime(event.created_at, { hour: "numeric", minute: "2-digit" })}
                      </span>
                    </div>
                  </div>
                </div>
              ))}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

// The agent's name — click-to-edit, save on blur/Enter, cancel on Escape.
// NAME IS NOT A CREATE-AGENT STEP: this is the only place an agent is
// renamed after creation, PATCHing display_name (fleet_configure_agent
// writes it to the same `label` column the create route and the name pool
// seed).
function AgentTitle({
  workspaceId, agentId, label, onRenamed,
}: { workspaceId: string; agentId: string; label: string; onRenamed?: () => void }) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(label);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const inputRef = useRef<HTMLInputElement | null>(null);
  const skipBlurCommit = useRef(false);

  useEffect(() => {
    if (!editing) setDraft(label);
  }, [label, editing]);

  useEffect(() => {
    if (editing) {
      inputRef.current?.focus();
      inputRef.current?.select();
    }
  }, [editing]);

  async function commit() {
    if (saving) return;
    const next = draft.trim();
    if (!next || next === label) {
      setDraft(label);
      setEditing(false);
      setError(null);
      return;
    }
    setSaving(true);
    setError(null);
    try {
      const res = await fetch(`/api/w/${encodeURIComponent(workspaceId)}/fleet/agents/${encodeURIComponent(agentId)}`, {
        method: "PATCH",
        credentials: "include",
        headers: buildCookieAuthHeaders("PATCH", { "Content-Type": "application/json" }),
        body: JSON.stringify({ patch: { display_name: next } }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok || data?.ok === false) throw new Error(data?.error || data?.detail || `HTTP ${res.status}`);
      setEditing(false);
      onRenamed?.();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not rename.");
    } finally {
      setSaving(false);
    }
  }

  if (editing) {
    return (
      <div style={{ marginBottom: 12 }}>
        <input
          ref={inputRef}
          className="fleet-overview-title-input"
          value={draft}
          disabled={saving}
          maxLength={200}
          onChange={(e) => setDraft(e.currentTarget.value)}
          onBlur={() => {
            if (skipBlurCommit.current) {
              skipBlurCommit.current = false;
              return;
            }
            void commit();
          }}
          onKeyDown={(e) => {
            if (e.key === "Enter") {
              e.preventDefault();
              e.currentTarget.blur();
            } else if (e.key === "Escape") {
              e.preventDefault();
              skipBlurCommit.current = true;
              setDraft(label);
              setEditing(false);
              setError(null);
            }
          }}
        />
        {error && <p className="fleet-channel-expand-error" style={{ margin: "4px 0 0" }}>{error}</p>}
      </div>
    );
  }

  return (
    <button type="button" className="fleet-overview-title" onClick={() => setEditing(true)} style={{ marginBottom: 12 }} aria-label={`Rename ${label || "this agent"}`}>
      <span>{label || "Untitled agent"}</span>
      <Pencil size={14} strokeWidth={1.75} className="fleet-overview-title-pencil" />
    </button>
  );
}

// Persona / system-prompt editor — the biggest pre-existing customization gap:
// instructions were only ever settable at creation (wizard step 1's one-liner),
// with no way to see or change what an agent IS after it exists.
function PersonaEditor({
  workspaceId, agentId, agent,
}: { workspaceId: string; agentId: string; agent: FleetAgent }) {
  const [draft, setDraft] = useState(agent.instructions || "");
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // Compact by default (~3 rows) — persona text is usually read once, not
  // sat in front of; expand for the rare full-rewrite edit.
  const [expanded, setExpanded] = useState(false);

  // Cmd+K palette / browser Back can swap which agent this tab shows
  // without unmounting this component (only agentId/agent change) — the
  // `useState(agent.instructions || "")` above only seeds `draft` on the
  // very first mount, so without this, `draft` keeps showing the PREVIOUS
  // agent's persona (or an in-progress unsaved edit for it), and Save would
  // PATCH that stale text onto the NEW agent's instructions. Mirrors
  // AgentTitle's own reset effect above (`if (!editing) setDraft(label)`),
  // keyed on agentId — not on `agent` itself, which gets a new object
  // reference on every ~30s poll tick for the SAME agent and would clobber
  // an in-progress, unsaved edit if used here instead.
  useEffect(() => {
    setDraft(agent.instructions || "");
    setSaved(false);
    setError(null);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [agentId]);

  async function save() {
    setSaving(true);
    setError(null);
    try {
      const res = await fetch(`/api/w/${encodeURIComponent(workspaceId)}/fleet/agents/${encodeURIComponent(agentId)}`, {
        method: "PATCH",
        credentials: "include",
        headers: buildCookieAuthHeaders("PATCH", { "Content-Type": "application/json" }),
        body: JSON.stringify({ patch: { instructions: draft } }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok || data?.ok === false) throw new Error(data?.error || data?.detail || `HTTP ${res.status}`);
      setSaved(true);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not save.");
    } finally {
      setSaving(false);
    }
  }

  return (
    <div style={{ marginTop: 4 }}>
      {/* Save top-right of the section header — same placement as
          ScheduleSection's own primary action, just below. */}
      <div className="fleet-detail-section-title" style={{ marginTop: 0, display: "flex", alignItems: "center", justifyContent: "space-between" }}>
        <span>Persona</span>
        <button type="button" className="fleet-btn fleet-btn--accent" onClick={save} disabled={saving}>
          {saving ? "Saving…" : saved ? <><Check size={14} strokeWidth={2} /> Saved</> : "Save"}
        </button>
      </div>
      <textarea
        className={`fleet-persona-textarea${expanded ? " fleet-persona-textarea--expanded" : ""}`}
        value={draft}
        onChange={(e) => { setDraft(e.currentTarget.value); setSaved(false); }}
        placeholder="What this agent is and how it should behave — e.g. “You handle customer refund requests. Be concise, and always confirm the order number before acting.”"
        spellCheck
      />
      <div style={{ marginTop: 8, display: "flex", alignItems: "center", gap: 12 }}>
        <button type="button" className="fleet-link" onClick={() => setExpanded((v) => !v)}>
          {expanded ? "Show less" : "Expand"}
        </button>
        {error && <span className="fleet-channel-expand-error" style={{ margin: 0 }}>{error}</span>}
      </div>
    </div>
  );
}

// ── Schedule (Part U2) — when this agent wakes on its own ──────────────────

function formatDueAt(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return formatDateTime(d, { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" });
}

function ScheduleTierBadge({ tier }: { tier: FleetScheduleItem["authority_tier"] }) {
  if (tier === "owner") {
    return <span className="fleet-badge fleet-badge--lock" style={{ marginLeft: 0 }}>Owner</span>;
  }
  if (tier === "system") {
    return <span className="fleet-badge" style={{ marginLeft: 0 }}>System</span>;
  }
  return <span className="fleet-badge" style={{ marginLeft: 0 }}>Audience</span>;
}

function ScheduleSection({ workspaceId, agentId }: { workspaceId: string; agentId: string }) {
  const { schedule, loading, refresh } = useFleetAgentSchedule(workspaceId, agentId);
  const [creating, setCreating] = useState(false);
  const [when, setWhen] = useState("");
  const [instruction, setInstruction] = useState("");
  const [previewText, setPreviewText] = useState<string | null>(null);
  const [previewError, setPreviewError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [deletingId, setDeletingId] = useState<string | null>(null);

  // Show the resolved next run before confirming — reparsed server-side via
  // the same _parse_when schedule_task itself uses, so there's no drift
  // between what's previewed and what actually gets created.
  useEffect(() => {
    if (!creating || !when.trim()) { setPreviewText(null); setPreviewError(null); return; }
    let cancelled = false;
    const handle = setTimeout(() => {
      void previewFleetAgentSchedule(workspaceId, agentId, when.trim()).then((result) => {
        if (cancelled) return;
        if (result.ok && result.due_at) {
          setPreviewText(formatDueAt(result.due_at));
          setPreviewError(null);
        } else {
          setPreviewText(null);
          setPreviewError(result.error || "Couldn't parse that time.");
        }
      });
    }, 400);
    return () => { cancelled = true; clearTimeout(handle); };
  }, [creating, when, workspaceId, agentId]);

  function cancelCreate() {
    setCreating(false);
    setWhen("");
    setInstruction("");
    setPreviewText(null);
    setPreviewError(null);
    setError(null);
  }

  async function handleCreate() {
    setBusy(true);
    setError(null);
    const result = await createFleetAgentSchedule(workspaceId, agentId, when.trim(), instruction.trim());
    setBusy(false);
    if (result.ok) {
      cancelCreate();
      await refresh();
    } else {
      setError(result.error || "Could not schedule this wake-up.");
    }
  }

  async function handleDelete(id: string) {
    setDeletingId(id);
    setError(null);
    const result = await deleteFleetAgentSchedule(workspaceId, agentId, id);
    setDeletingId(null);
    if (result.ok) await refresh();
    else setError(result.error || "Could not cancel this wake-up.");
  }

  return (
    <div style={{ marginTop: 4 }}>
      <div className="fleet-detail-section-title" style={{ marginTop: 0, display: "flex", alignItems: "center", justifyContent: "space-between" }}>
        <span>Schedule</span>
        {!creating && (
          <button type="button" className="fleet-btn" onClick={() => setCreating(true)}>
            <Clock size={14} strokeWidth={1.75} /> Schedule a wake-up
          </button>
        )}
      </div>

      {creating && (
        <div className="fleet-card" style={{ padding: "var(--space-3)", marginBottom: 12 }}>
          <div className="fleet-wizard-label" style={{ marginTop: 0 }}>When</div>
          <input
            className="fleet-wizard-input"
            placeholder="in 2 hours, or 2026-07-10T09:00:00Z"
            value={when}
            onChange={(e) => setWhen(e.target.value)}
          />
          {previewText && <p className="fleet-subtitle" style={{ marginTop: 4 }}>→ {previewText}</p>}
          {previewError && <p className="fleet-channel-expand-error" style={{ marginTop: 4 }}>{previewError}</p>}
          <div className="fleet-wizard-label">What should it do</div>
          <textarea
            className="fleet-wizard-input"
            placeholder="Check the inbox and follow up on anything unanswered."
            value={instruction}
            onChange={(e) => setInstruction(e.target.value)}
            rows={2}
            style={{ height: "auto", padding: "8px 12px", resize: "vertical" }}
          />
          {error && <p className="fleet-channel-expand-error" style={{ marginTop: 4 }}>{error}</p>}
          <div style={{ display: "flex", gap: 8, marginTop: 10 }}>
            <button type="button" className="fleet-btn" disabled={busy} onClick={cancelCreate}>
              Cancel
            </button>
            <button
              type="button"
              className="fleet-btn fleet-btn--accent"
              disabled={busy || !when.trim() || !instruction.trim() || !previewText}
              onClick={handleCreate}
            >
              {busy ? "Scheduling…" : "Confirm"}
            </button>
          </div>
        </div>
      )}

      {loading ? (
        <div className="fleet-activity-skeleton" aria-label="Loading schedule"><div className="fleet-skeleton-bar" style={{ width: "50%" }} /></div>
      ) : schedule.length === 0 ? (
        <p className="fleet-subtitle" style={{ marginTop: 0 }}>
          No scheduled wake-ups. This agent only acts when messaged.
        </p>
      ) : (
        <div className="fleet-config" style={{ padding: 0 }}>
          {schedule.map((item) => (
            <div key={item.id} className="fleet-toggle-row">
              <div style={{ minWidth: 0 }}>
                <div className="fleet-toggle-row-label">{item.description || "Scheduled wake-up"}</div>
                <div className="fleet-toggle-row-desc" style={{ display: "flex", alignItems: "center", gap: 6 }}>
                  <span>{formatDueAt(item.due_at)} · One-time</span>
                  <ScheduleTierBadge tier={item.authority_tier} />
                </div>
              </div>
              <button
                type="button"
                className="fleet-btn"
                disabled={deletingId === item.id}
                onClick={() => handleDelete(item.id)}
                title="Cancel this scheduled wake-up"
              >
                <Trash2 size={14} strokeWidth={1.75} /> {deletingId === item.id ? "Cancelling…" : "Cancel"}
              </button>
            </div>
          ))}
        </div>
      )}
      {!creating && error && <p className="fleet-channel-expand-error" style={{ marginTop: 8 }}>{error}</p>}
    </div>
  );
}

// ── Chat ────────────────────────────────────────────────────────────────────

// Runs as this specific agent — its own persona, model binding, and memory
// scope — over a per-agent thread ("thread_agent_{agentId}"), the same
// convention channel-bound turns use. See AgentChat's context_hints.metadata.
// active_agent_install_id, read by specialist_runtime_context.resolve_
// specialist_runtime_context.
function ChatTab({ workspaceId, agentId, agent }: { workspaceId: string; agentId: string; agent: FleetAgent | null }) {
  const label = agent?.label || "this agent";
  return (
    <div className="fleet-agent-chat-panel">
      <AgentChat
        workspaceId={workspaceId}
        threadId={`thread_agent_${agentId}`}
        agentInstallId={agentId}
        emptyIcon={MessageSquare}
        emptyTitle={`Message ${label}`}
        emptyBody={`Your owner test chat with ${label} — full access, not what a real customer would see (this tab always runs at owner tier). It replies as itself, using whatever's configured on the Model and Tools tabs.`}
        placeholder={`Message ${label}…`}
        sourceTag="fleet_agent_chat"
      />
    </div>
  );
}

// ── Channels ────────────────────────────────────────────────────────────────

import { PersonalChannelConnectPanel } from "./PersonalChannelConnectPanel";
import {
  isPersonalChannelStatusActive,
  useGatewayPersonalChannelSurfaces,
  usePersonalChannelStatus,
  type PersonalChannelKey,
} from "./personal-channel-pairing";

// Fixed platform order the grid renders in, mapped to the backend
// connection id that carries that platform's live status.
const CHANNEL_GRID_PLATFORMS: { label: string; id: string }[] = [
  { label: "Telegram", id: "sage_telegram_hosted" },
  { label: "Slack", id: "slack" },
  { label: "Discord", id: "discord_bot" },
  { label: "WhatsApp", id: "whatsapp_personal" },
  { label: "iMessage", id: "imessage_personal" },
  { label: "WeChat", id: "wechat_personal" },
];

type ChannelDoor = { key: string; label: string; body: string; real: boolean };

// Each channel shows only the connection MODES that are real, safe, and built
// today for that platform — never a door that fails, and never a second mode
// standing in for one that doesn't exist yet (contract rule #4). Telegram is
// the one channel with a genuine two-mode choice (Chatbot vs. Full account);
// every other channel has exactly one real path, so its door auto-selects
// with no picker step. "Full account" doors bind to THIS agent's own
// preferred_gateway_id (see ChannelsTab's `agentGatewayId`), never to a
// workspace-wide/Sage-routed session.
const CHANNEL_DOORS: Record<string, ChannelDoor[]> = {
  sage_telegram_hosted: [
    { key: "byo_bot", label: "Chatbot", body: "Agent replies as a separate bot — paste the token BotFather gave you. No control of your own account.", real: true },
    { key: "full_account", label: "Full account", body: "This agent's own Telegram number — phone, code, and 2FA if enabled — running on this agent's own gateway.", real: true },
  ],
  slack: [
    { key: "oauth", label: "App", body: "Connect a Slack workspace — signed mentions and DMs route to Sage.", real: true },
  ],
  discord_bot: [
    { key: "byo_bot", label: "Bot", body: "Give this agent its own Discord bot — paste the token from Discord's developer portal. Discord's Terms forbid automating a real user account, so this is the only path.", real: true },
  ],
  whatsapp_personal: [
    { key: "full_account", label: "Full account", body: "This agent's own WhatsApp number — scan a QR code or use a pairing code — running on this agent's own gateway. There is no chatbot/business-API mode.", real: true },
  ],
  imessage_personal: [
    { key: "full_account", label: "Full account", body: "This agent's own iMessage, via a Mac running BlueBubbles Server as this agent's gateway. Requires a real Mac — there is no cloud path for iMessage.", real: true },
  ],
  wechat_personal: [
    { key: "full_account", label: "Full account", body: "This agent's own WeChat, via a real session on this agent's gateway. WeChat has no official API to build against, so this bridge is rougher than the others and may not hold over time.", real: true },
  ],
};

// Local-bridge channels (iMessage today; WeChat has no bridge to check at
// all) have no in-app pairing step — the bridge runs on hardware the user
// configures themselves. This shows the REAL health snapshot for one
// channel_key on one gateway; there is no client-invented "connected" state,
// and no button that claims to "connect" anything.
function LocalBridgeChannelStatus({ channelKey, gatewayId }: { channelKey: string; gatewayId: string | null }) {
  const { items, loading } = useGatewayPersonalChannelSurfaces(gatewayId);

  if (!gatewayId) {
    return (
      <p className="fleet-channel-expand-hint">
        This agent has no computer of its own yet — set one up on the Hardware tab first, then point it at a
        BlueBubbles Server on that Mac.
      </p>
    );
  }
  if (loading) {
    return <Loader2 size={14} style={{ animation: "spin 1s linear infinite" }} />;
  }

  const item = items.find((i) => i.channel_key === channelKey) || null;
  if (item?.connected) {
    return (
      <div className="fleet-channel-expand-success">
        <Check size={16} strokeWidth={2} /> Connected{item.connected_identity ? ` — ${item.connected_identity}` : ""}
      </div>
    );
  }
  return (
    <>
      <p className="fleet-channel-expand-hint">{item?.detail || "This channel runs through a local bridge on this agent's own gateway."}</p>
      <p className="fleet-channel-expand-hint">{item?.next_step || "Configure the bridge on this agent's gateway, then this status updates on its own."}</p>
      <p className="fleet-channel-expand-hint" style={{ color: "var(--text-tertiary)" }}>{item?.status_label || "Not connected yet"}</p>
    </>
  );
}

function channelStatePill(channel: FleetChannel | undefined): { label: string; tone: "connected" | "gateway" | "locked" | "setup" } {
  if (!channel) return { label: "Unavailable", tone: "locked" };
  if (channel.connected) return { label: "Connected", tone: "connected" };
  if (channel.requiresGateway && channel.onlineGatewayCount === 0) return { label: "Needs Gateway", tone: "gateway" };
  if (channel.nextAction === "locked" || !channel.setupAvailable) return { label: "Not configured here", tone: "locked" };
  return { label: "Set up", tone: "setup" };
}

export function ChannelsTab({
  workspaceId, agentId, agent, onChannelsChanged,
}: {
  workspaceId: string;
  agentId: string;
  agent: FleetAgent | null;
  /** Called (in addition to this tab's own internal refresh) after a
   *  channel connects/binds — lets a caller that holds its OWN separate
   *  useFleetAgentChannels instance (FleetAgentDetail's Properties column,
   *  which needs a live "Channels" count) catch up in place instead of
   *  waiting for a remount. Optional and unused by FleetCreateAgentWizard's
   *  standalone embed of this same tab, which has no such sidebar to sync. */
  onChannelsChanged?: () => void;
}) {
  const { channels, loading, refresh: refreshChannels, telegramBotConnected, slackChannelBinding } = useFleetAgentChannels(workspaceId, agentId);
  // Every connect-success path below should notify both this tab's own
  // hook instance AND (when present) the caller's separate one.
  const handleChannelsChanged = useCallback(() => {
    void refreshChannels();
    onChannelsChanged?.();
  }, [refreshChannels, onChannelsChanged]);
  const [expanded, setExpanded] = useState<string | null>(null);
  const [oauthBusy, setOauthBusy] = useState<string | null>(null);
  const [oauthError, setOauthError] = useState<string | null>(null);

  // Full-account channels (Telegram/WhatsApp/iMessage/WeChat "full_account"
  // doors) bind to THIS agent's own gateway, never a workspace-wide/Sage
  // one — this is that binding. `undefined` would fall back to legacy
  // workspace-wide behavior in PersonalChannelConnectPanel; passing a
  // possibly-empty string here always keeps it agent-scoped.
  const agentGatewayId = agent?.preferred_gateway_id?.trim() || null;

  // Which door is picked inside the open banner. Only channels with more than
  // one door need an explicit pick — a single-door channel auto-selects its
  // one real door (see `activeDoor` below).
  const [selectedDoor, setSelectedDoor] = useState<string | null>(null);
  const [byoToken, setByoToken] = useState("");
  const [byoBotBusy, setByoBotBusy] = useState(false);
  const [byoBotError, setByoBotError] = useState<string | null>(null);
  const [byoBotSaved, setByoBotSaved] = useState(false);
  const [slackChannelId, setSlackChannelId] = useState("");
  const [slackBindBusy, setSlackBindBusy] = useState(false);
  const [slackBindError, setSlackBindError] = useState<string | null>(null);
  const [slackBindSaved, setSlackBindSaved] = useState(false);
  const [firstContactReply, setFirstContactReply] = useState(!!agent?.telegram_first_contact_reply);
  const [firstContactSaving, setFirstContactSaving] = useState(false);
  // agent starts null and loads async — resync once the real value arrives
  // (and again if it changes, e.g. edited from another tab) without
  // clobbering an in-progress toggle on every 30s poll of an unchanged value.
  useEffect(() => {
    if (agent) setFirstContactReply(!!agent.telegram_first_contact_reply);
  }, [agent?.telegram_first_contact_reply]);

  const saveByoBotToken = useCallback(async (channel: "telegram" | "discord") => {
    if (!byoToken.trim()) {
      setByoBotError(channel === "discord" ? "Paste the bot token from Discord's developer portal." : "Paste the token BotFather gave you.");
      return;
    }
    setByoBotBusy(true);
    setByoBotError(null);
    try {
      const res = await fetch(
        `/api/w/${encodeURIComponent(workspaceId)}/fleet/agent-channels/${channel}?agent_id=${encodeURIComponent(agentId)}`,
        {
          method: "POST",
          credentials: "include",
          headers: buildCookieAuthHeaders("POST", { "Content-Type": "application/json" }),
          body: JSON.stringify({ token: byoToken.trim() }),
        },
      );
      const data = await res.json().catch(() => ({}));
      if (!res.ok || data?.ok === false) throw new Error(data?.error || data?.detail || `HTTP ${res.status}`);
      setByoBotSaved(true);
      handleChannelsChanged();
    } catch (e) {
      setByoBotError(e instanceof Error ? e.message : "Could not save the bot token.");
    } finally {
      setByoBotBusy(false);
    }
  }, [workspaceId, agentId, byoToken, handleChannelsChanged]);

  const saveFirstContactReply = useCallback(async (next: boolean) => {
    setFirstContactReply(next);
    setFirstContactSaving(true);
    try {
      await fetch(`/api/w/${encodeURIComponent(workspaceId)}/fleet/agents/${encodeURIComponent(agentId)}`, {
        method: "PATCH",
        credentials: "include",
        headers: buildCookieAuthHeaders("PATCH", { "Content-Type": "application/json" }),
        body: JSON.stringify({ patch: { telegram_first_contact_reply: next } }),
      });
    } catch {
      // Best-effort — the toggle re-syncs from the agent record on next load.
    } finally {
      setFirstContactSaving(false);
    }
  }, [workspaceId, agentId]);

  const startOAuth = useCallback(async (id: string) => {
    setOauthBusy(id);
    setOauthError(null);
    try {
      const res = await fetch(`/api/connections/${encodeURIComponent(id)}/setup/start`, {
        method: "POST",
        credentials: "include",
        headers: buildCookieAuthHeaders("POST", { "Content-Type": "application/json" }),
        // metadata.agent_install_id: the backend's shared OAuth pipeline
        // (routes_connections.py) already threads this through the redirect
        // round-trip and files the resulting credential + binding at THIS
        // agent's scope instead of a bare workspace-wide one — it just needs
        // a caller to actually send it. Every other caller of this endpoint
        // is unaffected (it's optional there).
        body: JSON.stringify({ workspace_id: workspaceId, surface: "sage", metadata: { agent_install_id: agentId } }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        throw new Error(data?.detail || data?.error || `HTTP ${res.status}`);
      }
      if (data?.authorization_url) {
        window.location.href = data.authorization_url;
        return;
      }
      throw new Error("No OAuth redirect was returned for this connection.");
    } catch (e) {
      setOauthError(e instanceof Error ? e.message : "Could not start OAuth setup.");
    } finally {
      setOauthBusy(null);
    }
  }, [workspaceId, agentId]);

  const saveSlackChannelBinding = useCallback(async () => {
    if (!slackChannelId.trim()) {
      setSlackBindError("Paste the Slack channel ID (visible in the channel's details).");
      return;
    }
    setSlackBindBusy(true);
    setSlackBindError(null);
    try {
      const res = await fetch(
        `/api/w/${encodeURIComponent(workspaceId)}/fleet/agent-channels/slack?agent_id=${encodeURIComponent(agentId)}`,
        {
          method: "POST",
          credentials: "include",
          headers: buildCookieAuthHeaders("POST", { "Content-Type": "application/json" }),
          body: JSON.stringify({ slack_channel_id: slackChannelId.trim() }),
        },
      );
      const data = await res.json().catch(() => ({}));
      if (!res.ok || data?.ok === false) throw new Error(data?.error || data?.detail || `HTTP ${res.status}`);
      setSlackBindSaved(true);
      handleChannelsChanged();
    } catch (e) {
      setSlackBindError(e instanceof Error ? e.message : "Could not save the channel binding.");
    } finally {
      setSlackBindBusy(false);
    }
  }, [workspaceId, agentId, slackChannelId, handleChannelsChanged]);

  const activePlatform = CHANNEL_GRID_PLATFORMS.find((p) => p.id === expanded) || null;
  const doors = expanded ? CHANNEL_DOORS[expanded] || [] : [];
  // A lone real door needs no picker — it auto-activates. Otherwise the user's
  // click on a specific (real) door decides which one is active.
  const activeDoor = doors.length === 1 && doors[0].real ? doors[0] : doors.find((d) => d.key === selectedDoor) || null;

  // Live pairing status for whichever full-account door is on screen right
  // now. PersonalChannelConnectPanel (rendered below when activeDoor is
  // full_account) polls this exact same endpoint internally to drive its own
  // phone/code/QR/password steps — this is a second, parallel read of it,
  // used only to gate the doors picker just below. Must be called
  // unconditionally on every render (rules of hooks), so every branch that
  // isn't "a full_account door is currently active" passes a null gatewayId,
  // which short-circuits usePersonalChannelStatus into a no-op — no fetch,
  // no poll, view stays null. alwaysPoll: true because this instance has no
  // onRefresh() of its own to ride in on (unlike PersonalChannelConnectPanel,
  // which re-fetches right after every phone/code/password submit) — without
  // it, this poll would fetch "idle" once, stop (idle isn't an
  // ACTIVE_STATUSES status), and never notice the user going on to actually
  // pair from inside the panel below.
  const pairingChannelKey: PersonalChannelKey | null =
    activeDoor?.key !== "full_account" ? null
      : expanded === "sage_telegram_hosted" ? "telegram_personal"
      : expanded === "whatsapp_personal" ? "whatsapp_personal"
      : null;
  const { view: pairingView } = usePersonalChannelStatus(
    workspaceId,
    pairingChannelKey || "telegram_personal",
    pairingChannelKey ? agentGatewayId : null,
    agentId,
    { alwaysPoll: true },
  );
  // True only while the door on screen right now is full_account AND its
  // pairing is actively mid-flight (a code/QR/password step with real typed
  // input at stake — the ACTIVE_STATUSES a fresh poll loop is worth running
  // for, see personal-channel-pairing.ts). Switching the picker to a
  // different door at that exact moment would unmount
  // PersonalChannelConnectPanel and silently discard whatever's been typed
  // so far — the founder-reported bug this gates. Once pairing settles
  // (connected, or back to idle/disconnected) the picker returns to normal.
  const pairingActive = !!pairingChannelKey && isPersonalChannelStatusActive(pairingView?.state?.status);
  // The doors picker's actual render list: collapsed to just the in-flight
  // door while pairingActive, otherwise the full set (unchanged behavior).
  const displayDoors = pairingActive && activeDoor ? [activeDoor] : doors;

  // Only the TRUE first load (no channels fetched yet) gets the full-tab
  // skeleton — refresh() (called after every connect-success, see
  // handleChannelsChanged above) also flips `loading` true/false, and
  // gating on `loading` alone replaced whatever the user just saw (a
  // "Saved"/"Connected" confirmation, an open banner) with this skeleton on
  // every single action.
  if (loading && channels.length === 0) {
    return <div className="fleet-activity-skeleton" aria-label="Loading channels"><div className="fleet-skeleton-bar" style={{ width: "80%" }} /></div>;
  }

  // Slack's channel.connected from the backend means "OAuth app installed",
  // not "this agent owns a channel" — see isChannelConnected. Normalizing
  // it here means every reader below (grid pill via channelStatePill, and
  // the "already connected" banner prefill) agrees with the Properties
  // panel's own Channels count.
  const byId = new Map(channels.map((c) => [c.id, { ...c, connected: isChannelConnected(c, slackChannelBinding, telegramBotConnected) }]));

  function closeBanner() {
    setExpanded(null);
    setSelectedDoor(null);
    setByoToken("");
    setByoBotError(null);
    setByoBotSaved(false);
    setOauthError(null);
    setSlackChannelId("");
    setSlackBindError(null);
    setSlackBindSaved(false);
  }

  function handleCardClick(platform: typeof CHANNEL_GRID_PLATFORMS[number], pill: { tone: string }) {
    // "Not configured here" must be a dead end before the click, matching the
    // Connectors tab — not a click-through to a banner whose only path ends
    // in a raw env-var error (e.g. "Set SLACK_CLIENT_ID..."). "Needs Gateway"
    // stays clickable — pairing a Gateway is a real, actionable next step.
    if (pill.tone === "locked") return;
    if (expanded === platform.id) {
      closeBanner();
      return;
    }
    setExpanded(platform.id);
    setSelectedDoor(null);
    setByoToken("");
    setByoBotError(null);
    // Initialize from the real backend state instead of always blank — a
    // door that's already connected (from a previous session, or another
    // tab) should say so on open, not show an empty form that looks like
    // nothing was ever saved. Discord has exactly one real door, so its
    // grid-level `connected` already means "byo bot token saved"; Telegram's
    // byo_bot door is a separate catalog item from the grid pill's own
    // sage_telegram_hosted status, so it rides in on telegramBotConnected
    // instead (see routes_fleet.py's fleet_agent_channels).
    setByoBotSaved(
      platform.id === "discord_bot" ? Boolean(byId.get("discord_bot")?.connected)
        : platform.id === "sage_telegram_hosted" ? telegramBotConnected
        : false,
    );
    setOauthError(null);
    setSlackChannelId("");
    setSlackBindError(null);
    setSlackBindSaved(platform.id === "slack" ? Boolean(slackChannelBinding) : false);
  }

  return (
    <div>
      {/* Channels vs. Connectors reads as one undifferentiated "integrations"
          blob otherwise — this one-liner is the whole fix: it's how people
          reach the agent, not what the agent can use. */}
      <p className="fleet-tab-subtitle">Where people can message this agent</p>
      <div className="fleet-channel-grid">
        {CHANNEL_GRID_PLATFORMS.map((platform) => {
          const channel = byId.get(platform.id);
          const pill = channelStatePill(channel);
          const icon = CHANNEL_ICONS[platform.id];
          const isExpanded = expanded === platform.id;
          return (
            <button
              key={platform.id}
              type="button"
              className={`fleet-channel-card${isExpanded ? " fleet-channel-card--active" : ""}`}
              onClick={() => handleCardClick(platform, pill)}
              disabled={pill.tone === "locked"}
            >
              <span className="fleet-channel-card-icon">
                {icon ? <img src={icon} alt="" width={32} height={32} /> : platform.label.charAt(0)}
              </span>
              <span className="fleet-channel-card-label">{platform.label}</span>
              <span className={`fleet-channel-card-pill fleet-channel-card-pill--${pill.tone}`}>
                {pill.tone === "connected" ? <span className="fleet-channel-card-dot" /> : null}
                {pill.label}
              </span>
            </button>
          );
        })}
      </div>

      {activePlatform && (
        <div
          className="fleet-detail-backdrop"
          onClick={closeBanner}
          onKeyDown={(e) => {
            // Own the Escape key here — otherwise it bubbles up to the page
            // wrapper's "Esc returns to the previous view" handler and
            // navigates the user away instead of just closing this banner.
            if (e.key === "Escape") {
              e.stopPropagation();
              closeBanner();
            }
          }}
        >
          <div className="fleet-channel-banner" role="dialog" aria-modal="true" onClick={(e) => e.stopPropagation()}>
            <div className="fleet-channel-banner-header">
              <span className="fleet-channel-banner-icon">
                {CHANNEL_ICONS[activePlatform.id]
                  ? <img src={CHANNEL_ICONS[activePlatform.id]} alt="" width={24} height={24} />
                  : activePlatform.label.charAt(0)}
              </span>
              <span className="fleet-channel-banner-title">{activePlatform.label}</span>
              <button type="button" className="fleet-detail-close fleet-detail-close--inline" onClick={closeBanner} aria-label="Close">
                <X size={16} strokeWidth={2} />
              </button>
            </div>

            <div className="fleet-channel-banner-body">
              {/* While a full_account pairing is actively mid-flight (a
                   code/QR/password step with real typed input at stake —
                   see pairingActive/displayDoors above), the picker collapses
                   to JUST the active door, rendered the same inert way a
                   single-door channel already is below (no onClick). Tapping
                   an alternate-door button right now would unmount
                   PersonalChannelConnectPanel below and silently discard
                   whatever's been entered — this is what stops that. */}
              {displayDoors.length >= 1 && (
                <div className="fleet-wizard-options">
                  {displayDoors.map((door) => {
                    const isOnlyRealDoor = displayDoors.length === 1 && door.real;
                    if (!door.real) {
                      return (
                        <button key={door.key} type="button" disabled className="fleet-wizard-option fleet-wizard-option--soon">
                          <span className="fleet-wizard-option-label">{door.label}</span>
                          <span className="fleet-wizard-option-body">{door.body}</span>
                          <span className="fleet-wizard-option-note"><Lock size={11} strokeWidth={2} /> Coming soon</span>
                        </button>
                      );
                    }
                    if (isOnlyRealDoor) {
                      return (
                        <div key={door.key} className="fleet-wizard-option is-selected" style={{ cursor: "default" }}>
                          <span className="fleet-wizard-option-label">{door.label}</span>
                          <span className="fleet-wizard-option-body">{door.body}</span>
                        </div>
                      );
                    }
                    return (
                      <button
                        key={door.key}
                        type="button"
                        className={`fleet-wizard-option${selectedDoor === door.key ? " is-selected" : ""}`}
                        onClick={() => setSelectedDoor(selectedDoor === door.key ? null : door.key)}
                      >
                        <span className="fleet-wizard-option-label">{door.label}</span>
                        <span className="fleet-wizard-option-body">{door.body}</span>
                      </button>
                    );
                  })}
                </div>
              )}

              {/* Telegram: BYO bot token */}
              {activePlatform.id === "sage_telegram_hosted" && activeDoor?.key === "byo_bot" && (
                <div style={{ marginTop: 12 }}>
                  {byoBotSaved ? (
                    <div className="fleet-channel-expand-success">
                      <Check size={16} strokeWidth={2} /> Bot token saved — this agent's own bot is live.
                    </div>
                  ) : (
                    <>
                      <p className="fleet-channel-expand-hint">Paste the token BotFather gave you when you created the bot.</p>
                      <label className="gw-pair-panel-field" style={{ marginBottom: 10, display: "flex" }}>
                        <span>Bot Token</span>
                        <input
                          type="password"
                          autoComplete="off"
                          value={byoToken}
                          onChange={(e) => { setByoToken(e.currentTarget.value); setByoBotError(null); }}
                        />
                      </label>
                      <button type="button" className="fleet-btn fleet-btn--accent" onClick={() => void saveByoBotToken("telegram")} disabled={byoBotBusy}>
                        {byoBotBusy ? <><Loader2 size={14} style={{ animation: "spin 1s linear infinite" }} /> Saving…</> : "Save token"}
                      </button>
                    </>
                  )}
                  {byoBotError && <p className="fleet-channel-expand-error">{byoBotError}</p>}

                  {agent && (
                    <div style={{ marginTop: 16, paddingTop: 12, borderTop: "1px solid var(--border)" }}>
                      <label style={{ display: "flex", alignItems: "center", gap: 10, cursor: "pointer" }}>
                        <button
                          type="button"
                          role="switch"
                          aria-checked={firstContactReply}
                          aria-label="Reply to new contacts with an intro message"
                          className={`fleet-toggle${firstContactReply ? " is-on" : ""}`}
                          disabled={firstContactSaving}
                          onClick={() => void saveFirstContactReply(!firstContactReply)}
                        />
                        <span style={{ fontSize: 13 }}>
                          Reply to new contacts with an intro message
                          <span style={{ display: "block", fontSize: 12, color: "var(--text-muted)" }}>
                            The first time a stranger messages this bot, it identifies itself as an AI agent with a link. Off by default.
                          </span>
                        </span>
                      </label>
                    </div>
                  )}
                </div>
              )}

              {/* Discord: BYO bot token — one bot binds to exactly one agent
                   (discord_bot_provisioning_service.py), never a workspace-wide
                   credential. No OAuth path exists (or is needed): Discord's
                   Terms forbid automating a real user account, so a bot token
                   pasted from the developer portal is the only path. */}
              {activePlatform.id === "discord_bot" && activeDoor?.key === "byo_bot" && (
                <div style={{ marginTop: 12 }}>
                  {byoBotSaved ? (
                    <div className="fleet-channel-expand-success">
                      <Check size={16} strokeWidth={2} /> Bot token saved — this agent's own bot is live.
                    </div>
                  ) : (
                    <>
                      <p className="fleet-channel-expand-hint">Paste the bot token from Discord's developer portal (Bot tab).</p>
                      <label className="gw-pair-panel-field" style={{ marginBottom: 10, display: "flex" }}>
                        <span>Bot Token</span>
                        <input
                          type="password"
                          autoComplete="off"
                          value={byoToken}
                          onChange={(e) => { setByoToken(e.currentTarget.value); setByoBotError(null); }}
                        />
                      </label>
                      <button type="button" className="fleet-btn fleet-btn--accent" onClick={() => void saveByoBotToken("discord")} disabled={byoBotBusy}>
                        {byoBotBusy ? <><Loader2 size={14} style={{ animation: "spin 1s linear infinite" }} /> Saving…</> : "Save token"}
                      </button>
                    </>
                  )}
                  {byoBotError && <p className="fleet-channel-expand-error">{byoBotError}</p>}
                </div>
              )}

              {/* Slack: OAuth connects the workspace's Slack app (now agent-
                   scoped via metadata.agent_install_id — see startOAuth),
                   then this agent claims one channel within it. Slack's app
                   install is workspace-wide and can serve many agents,
                   unlike Discord's dedicated-bot-per-agent model, so
                   ownership here is per-channel, not per-connection. */}
              {activePlatform.id === "slack" && activeDoor && (
                <div style={{ marginTop: 12 }}>
                  <button
                    type="button"
                    className="fleet-btn fleet-btn--accent"
                    onClick={() => void startOAuth(activePlatform.id)}
                    disabled={oauthBusy === activePlatform.id}
                  >
                    {oauthBusy === activePlatform.id ? <Loader2 size={14} style={{ animation: "spin 1s linear infinite" }} /> : null}
                    {oauthBusy === activePlatform.id ? "Starting…" : `Connect ${activePlatform.label}`}
                  </button>
                  {oauthError && <p className="fleet-channel-expand-error">{oauthError}</p>}

                  <div style={{ marginTop: 16, paddingTop: 12, borderTop: "1px solid var(--border)" }}>
                    {slackBindSaved ? (
                      <div className="fleet-channel-expand-success">
                        <Check size={16} strokeWidth={2} /> Channel bound — this agent owns it.
                      </div>
                    ) : (
                      <>
                        <p className="fleet-channel-expand-hint">Give this agent one Slack channel to own (paste the channel ID from the channel&apos;s details).</p>
                        <label className="gw-pair-panel-field" style={{ marginBottom: 10, display: "flex" }}>
                          <span>Slack Channel ID</span>
                          <input
                            type="text"
                            autoComplete="off"
                            placeholder="C0123ABC456"
                            value={slackChannelId}
                            onChange={(e) => { setSlackChannelId(e.currentTarget.value); setSlackBindError(null); }}
                          />
                        </label>
                        <button type="button" className="fleet-btn fleet-btn--accent" onClick={() => void saveSlackChannelBinding()} disabled={slackBindBusy}>
                          {slackBindBusy ? <><Loader2 size={14} style={{ animation: "spin 1s linear infinite" }} /> Saving…</> : "Bind channel"}
                        </button>
                      </>
                    )}
                    {slackBindError && <p className="fleet-channel-expand-error">{slackBindError}</p>}
                  </div>
                </div>
              )}

              {/* Telegram / WhatsApp: full-account (real MTProto / Baileys session),
                   bound to this agent's own gateway via agentGatewayId — not
                   Sage's workspace-wide Connect tab. */}
              {activePlatform.id === "sage_telegram_hosted" && activeDoor?.key === "full_account" && (
                <div style={{ marginTop: 12 }}>
                  <PersonalChannelConnectPanel
                    workspaceId={workspaceId}
                    channelKey="telegram_personal"
                    label="Telegram"
                    agentGatewayId={agentGatewayId}
                    agentId={agentId}
                    onConnected={handleChannelsChanged}
                  />
                </div>
              )}
              {activePlatform.id === "whatsapp_personal" && activeDoor?.key === "full_account" && (
                <div style={{ marginTop: 12 }}>
                  <PersonalChannelConnectPanel
                    workspaceId={workspaceId}
                    channelKey="whatsapp_personal"
                    label="WhatsApp"
                    agentGatewayId={agentGatewayId}
                    agentId={agentId}
                    onConnected={handleChannelsChanged}
                  />
                </div>
              )}

              {/* iMessage: no phone/code/QR step of its own — the bridge lives on
                   a Mac the user runs themselves, configured via env vars on this
                   agent's gateway. The only honest thing to show is real bridge
                   health, not a fake "connect" button. */}
              {activePlatform.id === "imessage_personal" && activeDoor?.key === "full_account" && (
                <div style={{ marginTop: 12 }}>
                  <LocalBridgeChannelStatus channelKey="imessage_personal" gatewayId={agentGatewayId} />
                </div>
              )}

              {/* WeChat: same local-bridge shape as iMessage, but there is no
                   protocol client at all yet (no official API to build one
                   against) — rails only, said honestly, no live status to check. */}
              {activePlatform.id === "wechat_personal" && activeDoor?.key === "full_account" && (
                <div style={{ marginTop: 12 }}>
                  <p className="fleet-channel-expand-hint">
                    WeChat has no official API. This bridge is a best-effort rail on this agent's own gateway,
                    not a certified integration — it may break without warning.
                  </p>
                </div>
              )}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

// ── Connectors ──────────────────────────────────────────────────────────────

function ConnectorsTab({
  workspaceId, agentId, agent,
}: { workspaceId: string; agentId: string; agent: FleetAgent | null }) {
  const projectId = agent?.project_id || "";

  // Same one-liner treatment as ChannelsTab's own subtitle just above, kept
  // visible across every body state (loading/empty/picker) — the whole
  // point is telling the two tabs apart at a glance, not just once loaded.
  const subtitle = <p className="fleet-tab-subtitle">Apps this agent can use</p>;

  if (!agent) {
    return (
      <div>
        {subtitle}
        <div className="fleet-activity-skeleton" aria-label="Loading connectors"><div className="fleet-skeleton-bar" style={{ width: "70%" }} /></div>
      </div>
    );
  }

  if (!projectId) {
    return (
      <div>
        {subtitle}
        <EmptyState
          icon={Plug}
          title="No project assigned"
          body="This agent has no project — connectors are shared per project. Assign a project before connecting apps."
        />
      </div>
    );
  }

  return (
    <div>
      {subtitle}
      <ConnectorPicker workspaceId={workspaceId} projectId={projectId} agentId={agentId} />
    </div>
  );
}

// ── Shared: collapsed-by-default "Advanced" section ─────────────────────────

function Disclosure({
  label, defaultOpen = false, children,
}: { label: string; defaultOpen?: boolean; children: React.ReactNode }) {
  const [open, setOpen] = useState(defaultOpen);
  return (
    <div className={`fleet-disclosure${open ? " is-open" : ""}`}>
      <button
        type="button"
        className="fleet-disclosure-trigger"
        onClick={() => setOpen((o) => !o)}
        aria-expanded={open}
      >
        <ChevronRight size={13} strokeWidth={2} className="fleet-disclosure-chevron" />
        {label}
      </button>
      {open && <div className="fleet-disclosure-body">{children}</div>}
    </div>
  );
}

// ── Tools ───────────────────────────────────────────────────────────────────

function ToolCustomerAccess({
  tool, busy, onGrant, onRevoke,
}: { tool: FleetTool; busy: boolean; onGrant: () => void; onRevoke: () => void }) {
  const muted = !tool.enabled;
  if (tool.audience_safe) {
    return (
      <span
        className={`fleet-badge${muted ? " fleet-badge--muted" : ""}`}
        style={{ marginLeft: 0 }}
        title={muted ? "Enabled required to run" : "The platform marks this tool safe for anyone to trigger."}
      >
        Safe by default
      </span>
    );
  }
  if (tool.mandate_granted) {
    return (
      <button
        type="button"
        className={`fleet-badge fleet-badge--lock fleet-badge--action${muted ? " fleet-badge--muted" : ""}`}
        style={{ marginLeft: 0 }}
        disabled={busy}
        onClick={onRevoke}
        title={muted ? "Enabled required to run — click to revoke customer access" : "Customers can trigger this. Click to revoke."}
      >
        Granted by you
      </button>
    );
  }
  return (
    <button
      type="button"
      className="fleet-badge fleet-badge--action"
      style={{ marginLeft: 0 }}
      disabled={busy}
      onClick={onGrant}
      title="Only the workspace owner can trigger this. Click to grant customer access."
    >
      Owner only
    </button>
  );
}

function ToolsTab({
  workspaceId, agentId, agent, onChat,
}: { workspaceId: string; agentId: string; agent: FleetAgent | null; onChat: () => void }) {
  const { tools, coreTools, isMaster, loading, refresh } = useFleetAgentTools(workspaceId, agentId);
  // Truth Map B1: a handful of tools (Calendar/Task Runner/Email/CRM) are
  // real but execution_mode="manual" with no direct executor — the toggle
  // above does nothing until the connector named in requires_connector is
  // actually connected. Cross-referencing the same connector list the
  // Connectors tab already fetches, so this stays accurate if a deployment
  // configures Google Workspace OAuth later.
  const { connectors: toolConnectors, loading: connectorsLoading } = useFleetAgentConnectors(workspaceId, agentId);
  const [pending, setPending] = useState<string | null>(null);
  const [mandateBusy, setMandateBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function toggle(toolId: string, next: boolean) {
    setPending(toolId);
    setError(null);
    try {
      const res = await fetch(`/api/w/${encodeURIComponent(workspaceId)}/fleet/agents/${encodeURIComponent(agentId)}`, {
        method: "PATCH",
        credentials: "include",
        headers: buildCookieAuthHeaders("PATCH", { "Content-Type": "application/json" }),
        body: JSON.stringify({ patch: { tool_toggles: { [toolId]: next } } }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok || data?.ok === false) throw new Error(data?.error || `HTTP ${res.status}`);
      await refresh();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not update this tool.");
    } finally {
      setPending(null);
    }
  }

  async function setMandate(toolId: string, grant: boolean) {
    setMandateBusy(toolId);
    setError(null);
    // mandate.audience_tools is replace-semantics server-side, so reconstruct
    // the full desired set from what's currently visible on this tab.
    const nextGranted = new Set(tools.filter((t) => t.mandate_granted).map((t) => t.id));
    if (grant) nextGranted.add(toolId);
    else nextGranted.delete(toolId);
    try {
      const res = await fetch(`/api/w/${encodeURIComponent(workspaceId)}/fleet/agents/${encodeURIComponent(agentId)}`, {
        method: "PATCH",
        credentials: "include",
        headers: buildCookieAuthHeaders("PATCH", { "Content-Type": "application/json" }),
        body: JSON.stringify({ patch: { mandate: { audience_tools: Array.from(nextGranted) } } }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok || data?.ok === false) throw new Error(data?.error || `HTTP ${res.status}`);
      await refresh();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not update customer access for this tool.");
    } finally {
      setMandateBusy(null);
    }
  }

  if (loading || connectorsLoading) {
    return <div className="fleet-activity-skeleton" aria-label="Loading tools"><div className="fleet-skeleton-bar" style={{ width: "60%" }} /></div>;
  }

  const connectorById = new Map(toolConnectors.map((c) => [c.id, c]));

  if (tools.length === 0 && coreTools.length === 0) {
    return (
      <EmptyState
        icon={Wrench}
        title="No tools available"
        body="Chat with Sage to configure tools for this agent."
        action="Chat to configure"
        onAction={onChat}
      />
    );
  }

  const enabledCount = tools.filter((t) => t.enabled).length;

  return (
    <div className="fleet-config">
      {/* Legibility primer (frontend-only, no logic change): the model
          itself is unconditional and per-message (see
          authority_mandate_service.py / triage_service.py) — every sender
          who isn't positively the owner is "audience", regardless of which
          agent they messaged. Shown above both branches below since the
          badges on every tool row (Safe by default / Granted by you /
          Owner only) apply the same way whether this is Sage or a
          specialist. */}
      <p className="fleet-subtitle" style={{ marginTop: 0 }}>
        Everyone who messages this agent is a customer at support-tier — they can request, not command.
        You, the owner, keep full access.
      </p>
      <div
        className="fleet-subtitle"
        style={{ marginTop: 0, marginBottom: 12, display: "flex", flexWrap: "wrap", gap: "6px 16px", alignItems: "center" }}
      >
        <span style={{ display: "inline-flex", alignItems: "center", gap: 6 }}>
          <span className="fleet-badge" style={{ marginLeft: 0 }}>Safe by default</span> anyone can use it
        </span>
        <span style={{ display: "inline-flex", alignItems: "center", gap: 6 }}>
          <span className="fleet-badge fleet-badge--lock" style={{ marginLeft: 0 }}>Granted by you</span> you opened it up
        </span>
        <span style={{ display: "inline-flex", alignItems: "center", gap: 6 }}>
          <span className="fleet-badge" style={{ marginLeft: 0 }}>Owner only</span> the default — click to grant
        </span>
      </div>
      {isMaster ? (
        <p className="fleet-channel-expand-hint" style={{ marginTop: 0 }}>
          This is the operator agent — it has unrestricted tool access, not gated by these toggles.
        </p>
      ) : (
        <>
          <div className="fleet-detail-section-title" style={{ marginTop: 0 }}>
            {enabledCount} of {tools.length} tools enabled
          </div>
          <p className="fleet-subtitle" style={{ marginTop: 0 }}>
            By default customers can only use read/support tools; everything else is owner-only until you grant it.
          </p>
        </>
      )}
      {tools.map((t) => {
        const requiredConnector = t.requires_connector ? connectorById.get(t.requires_connector) : undefined;
        const connectorMissing = Boolean(t.requires_connector) && !requiredConnector?.connected;
        return (
        <div key={t.id} className="fleet-toggle-row">
          <div style={{ minWidth: 0 }}>
            <div className="fleet-toggle-row-label">{t.label}</div>
            {t.description && <div className="fleet-toggle-row-desc">{t.description}</div>}
            {connectorMissing && (
              <div className="fleet-toggle-row-desc">
                Needs {requiredConnector?.label || "a connector"} connected — this toggle has no effect until then.
              </div>
            )}
            {!connectorMissing && !t.enabled && (t.audience_safe || t.mandate_granted) && (
              <div className="fleet-toggle-row-desc">Enabled required to run</div>
            )}
          </div>
          <div style={{ display: "flex", alignItems: "center", gap: 10, flexShrink: 0 }}>
            {!isMaster && (
              <ToolCustomerAccess
                tool={t}
                busy={mandateBusy === t.id}
                onGrant={() => setMandate(t.id, true)}
                onRevoke={() => setMandate(t.id, false)}
              />
            )}
            <button
              type="button"
              role="switch"
              aria-checked={t.enabled}
              aria-label={`${t.enabled ? "Disable" : "Enable"} ${t.label}`}
              className={`fleet-toggle${t.enabled ? " is-on" : ""}`}
              disabled={isMaster || pending === t.id}
              onClick={() => toggle(t.id, !t.enabled)}
            />
          </div>
        </div>
        );
      })}
      {error && <p className="fleet-channel-expand-error">{error}</p>}
      {coreTools.length > 0 && (
        <Disclosure label={`${coreTools.length} core ${coreTools.length === 1 ? "tool" : "tools"} — always on`}>
          <p className="fleet-channel-expand-hint" style={{ marginTop: 0 }}>
            Plumbing every agent needs to function — there&apos;s no toggle for these because there&apos;s nothing to turn off. Anything with its own real toggle is listed above instead.
          </p>
          <div style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
            {coreTools.map((name) => (
              <span key={name} className="fleet-badge" style={{ marginLeft: 0 }}>{name}</span>
            ))}
          </div>
        </Disclosure>
      )}
    </div>
  );
}

// ── Model ───────────────────────────────────────────────────────────────────

import {
  BYOK_PROVIDERS, SUBSCRIPTION_PROVIDERS, LOCAL_PROVIDERS, MODE_LABELS,
  COMING_SOON_MODES, COMING_SOON_NOTE, runtimeForProvider, type ProviderMode,
  FREEFORM_MODEL_PROVIDERS, modelsForProvider, defaultModelForProvider,
  REASONING_EFFORT_OPTIONS, REASONING_EFFORT_SUPPORTED_MODES, reasoningEffortLabel,
} from "./fleet-provider-constants";
import {
  GatewayBoxPicker,
  gatewayRuntimeReady,
  resolveHardwarePlacement,
  useWorkspaceGateways,
  type FleetGateway,
  type HardwarePlacementTone,
} from "./gateway-box-picker";

function resolveDisplayMode(config: Record<string, any>): ProviderMode {
  const mode = config.mode;
  if (mode === "byok_api") return "byok_api";
  if (mode === "cli_subscription") return "cli_subscription";
  if (mode === "local") return "local";
  return "platform_credits";
}

// Phase 7B: preset / hardware-lock / context-policy / today's cost, shown at
// the top of the Model tab so the agent's governance + spend are visible.
function AgentModelSummary({ workspaceId, agentId, agent }: { workspaceId: string; agentId: string; agent: FleetAgent | null }) {
  const [cost, setCost] = useState<number | null>(null);
  useEffect(() => {
    let cancelled = false;
    fetch(`/api/w/${encodeURIComponent(workspaceId)}/fleet/usage?scope=agent&id=${encodeURIComponent(agentId)}&period=day`, { credentials: "include" })
      .then((r) => (r.ok ? r.json() : null))
      .then((d) => { if (!cancelled && d?.totals) setCost(Number(d.totals.usd_cost || 0)); })
      .catch(() => {});
    return () => { cancelled = true; };
  }, [workspaceId, agentId]);
  const preset = (agent?.capability_preset || "standard").toLowerCase();
  const locked = !!agent?.hardware_access_locked;
  const pol = agent?.context_policy || {};
  const maxTok = Number(pol.max_context_tokens || 0);
  const action = pol.on_context_full === "fresh_session" ? "fresh session" : "compact";
  return (
    <div className="fleet-config" style={{ marginBottom: 20 }}>
      <div className="fleet-config-row">
        <span className="fleet-config-label">Capability preset</span>
        <span className="fleet-config-value">
          <span className="fleet-badge fleet-badge--preset">{preset}</span>
          {locked && <span className="fleet-badge fleet-badge--lock"><Lock size={11} strokeWidth={2} /> hardware locked</span>}
        </span>
      </div>
      <div className="fleet-config-row">
        <span className="fleet-config-label">Context policy</span>
        <span className="fleet-config-value">
          {maxTok > 0 ? `${formatNumber(maxTok)} tokens` : "model default"} → {action}
        </span>
      </div>
      <div className="fleet-config-row">
        <span className="fleet-config-label">Cost today</span>
        <span className="fleet-config-value">{cost === null ? "…" : `$${cost.toFixed(4)}`}</span>
      </div>
    </div>
  );
}

const _CONTEXT_FULL_OPTIONS: { value: string; label: string }[] = [
  { value: "compact", label: "Compact — summarize and continue" },
  { value: "fresh_session", label: "Fresh session — start a new thread" },
];

function ContextPolicySection({
  workspaceId, agentId, agent,
}: { workspaceId: string; agentId: string; agent: FleetAgent }) {
  const pol = agent.context_policy || {};
  const [maxTokens, setMaxTokens] = useState(String(pol.max_context_tokens || 0));
  const [onFull, setOnFull] = useState(pol.on_context_full === "fresh_session" ? "fresh_session" : "compact");
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function save() {
    const parsed = parseInt(maxTokens, 10);
    if (Number.isNaN(parsed) || parsed < 0) {
      setError("Max tokens must be 0 (model default) or a positive number.");
      return;
    }
    setSaving(true);
    setError(null);
    try {
      const res = await fetch(`/api/w/${encodeURIComponent(workspaceId)}/fleet/agents/${encodeURIComponent(agentId)}`, {
        method: "PATCH",
        credentials: "include",
        headers: buildCookieAuthHeaders("PATCH", { "Content-Type": "application/json" }),
        body: JSON.stringify({ patch: { context_policy: { max_context_tokens: parsed, on_context_full: onFull } } }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok || data?.ok === false) throw new Error(data?.error || data?.detail || `HTTP ${res.status}`);
      setSaved(true);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not save.");
    } finally {
      setSaving(false);
    }
  }

  return (
    <div>
      <label className="fleet-wizard-label" style={{ marginTop: 0 }}>Max context tokens</label>
      <input
        className="fleet-wizard-input"
        type="number"
        min={0}
        value={maxTokens}
        onChange={(e) => { setMaxTokens(e.currentTarget.value); setSaved(false); }}
        placeholder="0 = model default"
      />
      <label className="fleet-wizard-label">When context fills up</label>
      <select className="fleet-wizard-input" value={onFull} onChange={(e) => { setOnFull(e.currentTarget.value); setSaved(false); }}>
        {_CONTEXT_FULL_OPTIONS.map((o) => (
          <option key={o.value} value={o.value}>{o.label}</option>
        ))}
      </select>
      <div style={{ marginTop: 12, display: "flex", alignItems: "center", gap: 12 }}>
        {/* Distinct label is load-bearing, not cosmetic: this save is scoped
            to ONLY context_policy — it does not touch model/provider/gateway
            at all. It used to just say "Save" like the unrelated Model-tab
            save button above it (ModelTab's own save(), a separate PATCH
            call) — same label, same style, stacked on the same page, so a
            model/subscription change made above was silently never
            persisted by clicking this one. Confirmed live: selecting "Your
            subscription" -> Codex -> a Gateway, then clicking only this
            button, left model_config completely unchanged. */}
        <button type="button" className="fleet-btn fleet-btn--accent" onClick={save} disabled={saving}>
          {saving ? "Saving…" : saved ? <><Check size={14} strokeWidth={2} /> Saved</> : "Save context settings"}
        </button>
        {error && <span className="fleet-channel-expand-error" style={{ margin: 0 }}>{error}</span>}
      </div>
    </div>
  );
}

/** channelStatePill-style dynamic hint for the "Your subscription" option —
 *  real hardware state instead of a static note. cli_subscription is real
 *  and savable (BYO-brain Phase 3), but still needs a paired Gateway with
 *  the CLI installed and signed in — this names what's actually missing
 *  rather than a generic lock message. We don't know which CLI (Claude Code
 *  vs Codex) they'll pick until the option is expanded, so this checks for
 *  either. */
function cliSubscriptionHint(gateways: FleetGateway[]): string {
  if (gateways.length === 0) return "Needs a paired computer — none paired yet";
  const anyReady = gateways.some(
    (g) => gatewayRuntimeReady(g, "claude_code") || gatewayRuntimeReady(g, "codex"),
  );
  return anyReady ? "A paired computer has a CLI ready" : "No paired computer has Claude Code or Codex ready";
}

function ModelTab({
  workspaceId, agentId, agent, onSaved,
}: {
  workspaceId: string;
  agentId: string;
  agent: FleetAgent | null;
  /** Called after a successful save so the caller can refetch — the
   *  Properties column's "Model" row and this tab's own "Current state"
   *  block both read model_config off the SAME `agent` prop, which
   *  otherwise doesn't catch up until the next ~30s poll (see HardwareTab's
   *  identical onSaved for its own placement row). */
  onSaved?: () => void;
}) {
  const config = agent?.model_config || {};
  const [mode, setMode] = useState<ProviderMode>(resolveDisplayMode(config));
  const [provider, setProvider] = useState<string>(config.provider || "");
  const [gatewayBinding, setGatewayBinding] = useState<string>(config.gateway_binding || "");
  const { gateways: cliGateways } = useWorkspaceGateways(workspaceId);
  const cliRuntime = runtimeForProvider(provider) === "codex" ? "codex" : "claude_code";
  const [selectedModel, setSelectedModel] = useState<string>(config.model || "");
  const [apiKey, setApiKey] = useState("");
  const [reasoningEffort, setReasoningEffort] = useState<string>(config.reasoning_effort || "");
  // Re-default the model choice when the provider changes AFTER mount (so an
  // id from the previous provider doesn't linger in a <select> that no longer
  // has it) — but never on first render, which would clobber the agent's
  // actual current model.
  const skipNextModelReset = useRef(true);
  useEffect(() => {
    if (skipNextModelReset.current) { skipNextModelReset.current = false; return; }
    if (mode === "byok_api") {
      setSelectedModel(FREEFORM_MODEL_PROVIDERS.has(provider) ? "" : defaultModelForProvider(provider));
    } else if (mode === "local") {
      setSelectedModel(defaultModelForProvider(provider || "ollama"));
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [provider]);
  // Every field above is seeded from `config` via a useState INITIALIZER,
  // which only runs on the component's very first render. That's fine when
  // `agent` is already loaded by the time this tab mounts (the common path:
  // Overview loads first, the user clicks over) — but FleetAgentDetail
  // renders this tab unconditionally as soon as activeTab === "model", and
  // a direct link or hard refresh landing straight on this tab (initialTab
  // from the URL) mounts it with agent still null, before its fetch
  // resolves. Every field then silently seeds from `{}` and stays there
  // forever once `agent` populates a beat later — confirmed live: reload on
  // this tab left the Reasoning-effort <select> stuck on "Model default"
  // while the read-only "Current state" block (which reads `config` fresh
  // on every render, not through useState) correctly showed the real saved
  // value. Runs ONCE, the first time real agent data arrives — not on every
  // later change, since unlike HardwareTab's identical resync effect (which
  // auto-persists each click, so there's never an unsaved edit to protect)
  // this tab holds edits locally until an explicit Save; resyncing after
  // that first hydration would silently discard whatever the user is
  // mid-editing. skipNextModelReset is primed here too so this hydration's
  // own setProvider call doesn't trip the model-reset effect just above and
  // clobber the model this same hydration just set.
  const hydratedFromAgent = useRef(false);
  useEffect(() => {
    if (hydratedFromAgent.current || !agent) return;
    hydratedFromAgent.current = true;
    const freshConfig = agent.model_config || {};
    skipNextModelReset.current = true;
    setMode(resolveDisplayMode(freshConfig));
    setProvider(freshConfig.provider || "");
    setGatewayBinding(freshConfig.gateway_binding || "");
    setSelectedModel(freshConfig.model || "");
    setReasoningEffort(freshConfig.reasoning_effort || "");
  }, [agent]);
  // COMING_SOON_MODES is empty today (cli_subscription and local both
  // shipped) — kept as a live check, not deleted, so gating a future mode
  // that isn't ready yet needs no new plumbing here.
  const isComingSoon = COMING_SOON_MODES.has(mode);
  // BYO-brain Phase 2/3: "local" (Ollama) and "cli_subscription" (Claude
  // Code/Codex) both dispatch on a paired Gateway box — either one MUST name
  // which box runs it, or every turn fails at turn time instead of save
  // time ("no computer is bound" / "cli_subscription requires a Gateway").
  const localNeedsBox = mode === "local" && !gatewayBinding.trim();
  const cliSubscriptionNeedsBox = mode === "cli_subscription" && !gatewayBinding.trim();
  // cli_subscription/local dispatch to the paired Gateway, which has no
  // reasoning_effort plumbing today (see REASONING_EFFORT_SUPPORTED_MODES'
  // own comment) — the picker is hidden for those two modes instead of
  // saving a setting that silently does nothing at turn time.
  const reasoningEffortSupported = REASONING_EFFORT_SUPPORTED_MODES.has(mode);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const modelSummary = resolveAgentModelSummary(config);
  const displayProvider = modelSummary.provider;
  const displayModel = modelSummary.model;
  const isPlatformDefault = modelSummary.isPlatformDefault;

  async function save() {
    if (COMING_SOON_MODES.has(mode)) {
      setError(`${COMING_SOON_NOTE}. This option can’t be saved yet.`);
      return;
    }
    if (mode === "local" && !gatewayBinding.trim()) {
      setError("Pick a computer (with Ollama) to run this agent’s local model.");
      return;
    }
    if (mode === "cli_subscription" && !gatewayBinding.trim()) {
      setError("Pick a computer to run this agent’s subscription CLI.");
      return;
    }
    // A blank key is only safe to save when THIS provider already has a
    // credential in the vault — i.e. byok_api was already persisted for
    // this exact provider. Otherwise (switching into byok_api for the
    // first time, or switching to a different provider than the one
    // that's actually saved) there is no known credential, and patching
    // mode=byok_api anyway would silently persist a broken config: the
    // Properties panel and this tab's own "Current state" block would both
    // read back "Ready" with no way to actually run a turn.
    const hasExistingCredentialForProvider = config.mode === "byok_api" && config.provider === provider;
    if (mode === "byok_api" && !apiKey.trim() && !hasExistingCredentialForProvider) {
      setError("Enter your API key for this provider — none is saved yet.");
      return;
    }
    setSaving(true);
    setError(null);
    setSaved(false);
    try {
      if (mode === "byok_api") {
        if (!apiKey.trim()) {
          // Reusing existing vault key (hasExistingCredentialForProvider
          // guaranteed true above) — only patch config
          await patchModelConfig();
        } else {
          // See the identical comment in FleetCreateAgentWizard.tsx's
          // submitBrain(): /credentials/vault stores + validates the secret
          // against the real provider adapter and returns a credential_id;
          // /providers/profiles is the separate routing layer that makes it
          // discoverable at turn time. /api/connectors/vault (used here
          // previously) is the unrelated third-party-app connector vault and
          // 400s "Unsupported connector" for every LLM provider.
          const label = `${providerLabel(provider)} — ${agent?.label || "agent"}`;
          const credRes = await fetch("/api/credentials/vault", {
            method: "POST",
            credentials: "include",
            headers: buildCookieAuthHeaders("POST", { "Content-Type": "application/json" }),
            body: JSON.stringify({
              workspace_id: workspaceId,
              provider,
              label,
              mode: "byok",
              credentials: { api_key: apiKey.trim() },
            }),
          });
          const credData = await credRes.json().catch(() => ({}));
          if (!credRes.ok) throw new Error(credData?.detail || credData?.error || `HTTP ${credRes.status}`);

          const profileRes = await fetch("/api/providers/profiles", {
            method: "POST",
            credentials: "include",
            headers: buildCookieAuthHeaders("POST", { "Content-Type": "application/json" }),
            body: JSON.stringify({
              workspace_id: workspaceId,
              provider,
              label,
              credential_id: credData?.id,
              enabled: true,
            }),
          });
          const profileData = await profileRes.json().catch(() => ({}));
          if (!profileRes.ok) throw new Error(profileData?.detail || profileData?.error || `HTTP ${profileRes.status}`);
          await patchModelConfig();
        }
      } else if (mode === "cli_subscription" || mode === "local") {
        await patchModelConfig();
      } else {
        // platform_credits
        await patchModelConfig();
      }
      setSaved(true);
      onSaved?.();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not save.");
    } finally {
      setSaving(false);
    }
  }

  async function patchModelConfig() {
    const patch: Record<string, any> = { mode };
    if (mode === "byok_api" || mode === "cli_subscription" || mode === "local") {
      patch.provider = provider;
    }
    if ((mode === "byok_api" || mode === "local") && selectedModel.trim()) {
      patch.model = selectedModel.trim();
    }
    // BYO-brain Phase 0: forward-wire which box + runtime. (Save is blocked for
    // these modes today; this keeps the persisted shape correct once it opens.)
    if (mode === "cli_subscription" || mode === "local") {
      if (gatewayBinding) patch.gateway_binding = gatewayBinding;
      const rt = runtimeForProvider(provider);
      if (rt) patch.runtime = rt;
    }
    // Only for the two modes that actually consume it at turn time (see
    // REASONING_EFFORT_SUPPORTED_MODES) — this patch REPLACES model_config
    // wholesale (fleet_tools.py's fleet_configure_agent does `meta[
    // "model_config"] = dict(patch)`, not a merge), so switching to
    // cli_subscription/local and saving correctly drops any previously-set
    // reasoning_effort instead of leaving a stale, inert value behind.
    if (reasoningEffortSupported && reasoningEffort) {
      patch.reasoning_effort = reasoningEffort;
    }
    const res = await fetch(`/api/w/${encodeURIComponent(workspaceId)}/fleet/agents/${encodeURIComponent(agentId)}`, {
      method: "PATCH",
      credentials: "include",
      headers: buildCookieAuthHeaders("PATCH", { "Content-Type": "application/json" }),
      body: JSON.stringify({ patch: { model_config: patch } }),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok || data?.ok === false) throw new Error(data?.error || `HTTP ${res.status}`);
  }

  // Shared between the platform_credits and byok_api blocks below (the two
  // modes reasoningEffortSupported allows) so the picker can't drift between
  // them — see REASONING_EFFORT_SUPPORTED_MODES for why cli_subscription/
  // local get a disabled note instead (renderReasoningEffortUnsupportedNote).
  function renderReasoningEffortPicker() {
    return (
      <>
        <label className="fleet-wizard-label">Reasoning effort</label>
        <select
          className="fleet-wizard-input"
          value={reasoningEffort}
          onChange={(e) => { setReasoningEffort(e.currentTarget.value); setSaved(false); }}
        >
          {REASONING_EFFORT_OPTIONS.map((o) => (
            <option key={o.value || "unset"} value={o.value}>{o.label}</option>
          ))}
        </select>
        <p className="fleet-channel-expand-hint">
          Higher effort can solve harder problems but costs more and replies slower. Models that support it natively use it directly; others get it as a strong instruction instead.
        </p>
      </>
    );
  }

  function renderReasoningEffortUnsupportedNote() {
    return (
      <p className="fleet-channel-expand-hint">
        Reasoning effort isn’t available for this mode yet — it only applies to platform credits and your own API key.
      </p>
    );
  }

  return (
    <div>
      {/* Save at the top, consistent with Persona/Schedule's own primary
          action placement — same fleet-detail-section-title
          space-between header the rest of Overview already uses. */}
      <div className="fleet-detail-section-title" style={{ marginTop: 0, display: "flex", alignItems: "center", justifyContent: "space-between" }}>
        <span>Model</span>
        <button type="button" className="fleet-btn fleet-btn--accent" onClick={save} disabled={saving || isComingSoon || localNeedsBox || cliSubscriptionNeedsBox}>
          {saving ? "Saving…" : saved ? "Saved ✓" : "Save"}
        </button>
      </div>
      {(isComingSoon || (localNeedsBox && !isComingSoon) || (cliSubscriptionNeedsBox && !isComingSoon) || error) && (
        <div style={{ marginBottom: 12, display: "flex", alignItems: "center", gap: 12 }}>
          {isComingSoon && (
            <span className="fleet-channel-expand-hint" style={{ margin: 0 }}>{COMING_SOON_NOTE} — you can’t save this yet.</span>
          )}
          {localNeedsBox && !isComingSoon && (
            <span className="fleet-channel-expand-hint" style={{ margin: 0 }}>Pick a computer to run this agent’s local model.</span>
          )}
          {cliSubscriptionNeedsBox && !isComingSoon && (
            <span className="fleet-channel-expand-hint" style={{ margin: 0 }}>Pick a computer to run this agent’s subscription CLI.</span>
          )}
          {error && <span className="fleet-channel-expand-error" style={{ margin: 0 }}>{error}</span>}
        </div>
      )}

      <AgentModelSummary workspaceId={workspaceId} agentId={agentId} agent={agent} />
      {/* Current state — always visible */}
      <div className="fleet-config" style={{ marginBottom: 20 }}>
        <div className="fleet-config-row">
          <span className="fleet-config-label">Provider</span>
          <span className="fleet-config-value">{displayProvider}{isPlatformDefault ? " (platform default)" : ""}</span>
        </div>
        <div className="fleet-config-row">
          <span className="fleet-config-label">Model</span>
          <span className="fleet-config-value">{displayModel}{isPlatformDefault ? " (platform default)" : ""}</span>
        </div>
        {config.mode && (
          <div className="fleet-config-row">
            <span className="fleet-config-label">Payment</span>
            <span className="fleet-config-value">{MODE_LABELS[config.mode as ProviderMode] || config.mode}</span>
          </div>
        )}
        {modelSummary.reasoningEffort && (
          <div className="fleet-config-row">
            <span className="fleet-config-label">Reasoning</span>
            <span className="fleet-config-value">{reasoningEffortLabel(modelSummary.reasoningEffort)}</span>
          </div>
        )}
      </div>

      {/* Editor */}
      <div className="fleet-detail-section-title">Change provider</div>
      <div className="fleet-wizard-options" style={{ marginTop: 8 }}>
        <button
          type="button"
          className={`fleet-wizard-option${mode === "platform_credits" ? " is-selected" : ""}`}
          onClick={() => { setMode("platform_credits"); setSaved(false); }}
        >
          <span className="fleet-wizard-option-label">Platform credits</span>
          <span className="fleet-wizard-option-body">DeepSeek. Empyralis pays.</span>
        </button>
        <button
          type="button"
          className={`fleet-wizard-option${mode === "byok_api" ? " is-selected" : ""}`}
          onClick={() => { setMode("byok_api"); setProvider(provider || "anthropic"); setSaved(false); }}
        >
          <span className="fleet-wizard-option-label">Your own API key</span>
          <span className="fleet-wizard-option-body">Use your key for any provider.</span>
        </button>
        <button
          type="button"
          className={`fleet-wizard-option${mode === "cli_subscription" ? " is-selected" : ""}`}
          onClick={() => { setMode("cli_subscription"); setProvider(provider || "claude_code_cli"); setSaved(false); }}
        >
          <span className="fleet-wizard-option-label">Your subscription</span>
          <span className="fleet-wizard-option-body">Claude Code or Codex via Gateway.</span>
          <span className="fleet-wizard-option-note fleet-wizard-option-note--gateway">
            {cliSubscriptionHint(cliGateways)}
          </span>
        </button>
        <button
          type="button"
          className={`fleet-wizard-option${mode === "local" ? " is-selected" : ""}`}
          onClick={() => { setMode("local"); setProvider(provider || "ollama"); setSaved(false); }}
        >
          <span className="fleet-wizard-option-label">Run locally</span>
          <span className="fleet-wizard-option-body">Ollama on your own machine, via the Gateway.</span>
        </button>
      </div>

      {/* platform_credits has no provider/model fields (fixed to the
          platform default) — reasoning effort is the one thing left to
          configure here, so it gets its own expand block instead of living
          bare under the mode buttons. */}
      {mode === "platform_credits" && (
        <div className="fleet-channel-expand">
          {renderReasoningEffortPicker()}
        </div>
      )}

      {mode === "byok_api" && (
        <div className="fleet-channel-expand">
          <label className="fleet-wizard-label">Provider</label>
          <select className="fleet-wizard-input" value={provider} onChange={(e) => { setProvider(e.currentTarget.value); setSaved(false); }}>
            {BYOK_PROVIDERS.map((p) => (
              <option key={p.id} value={p.id}>{p.label}</option>
            ))}
          </select>
          <label className="fleet-wizard-label">API key {apiKey ? "" : "(leave blank to keep existing)"}</label>
          <input
            className="fleet-wizard-input"
            type="password"
            autoComplete="off"
            value={apiKey}
            onChange={(e) => { setApiKey(e.currentTarget.value); setSaved(false); }}
            placeholder="sk-..."
          />
          <p className="fleet-channel-expand-hint">Stored in this workspace's vault.</p>
          {FREEFORM_MODEL_PROVIDERS.has(provider) ? (
            <>
              <label className="fleet-wizard-label">Model ID</label>
              <input
                className="fleet-wizard-input"
                value={selectedModel}
                onChange={(e) => { setSelectedModel(e.currentTarget.value); setSaved(false); }}
                placeholder={provider === "azure_openai" ? "e.g. my-gpt4-deployment" : "e.g. llama-3-70b"}
              />
            </>
          ) : (
            <>
              <label className="fleet-wizard-label">Model</label>
              <select className="fleet-wizard-input" value={selectedModel} onChange={(e) => { setSelectedModel(e.currentTarget.value); setSaved(false); }}>
                {modelsForProvider(provider).map((m) => <option key={m} value={m}>{m}</option>)}
              </select>
            </>
          )}
          {renderReasoningEffortPicker()}
        </div>
      )}

      {mode === "cli_subscription" && (
        <div className="fleet-channel-expand">
          <label className="fleet-wizard-label">Subscription</label>
          <select className="fleet-wizard-input" value={provider} onChange={(e) => { setProvider(e.currentTarget.value); setSaved(false); }}>
            {SUBSCRIPTION_PROVIDERS.map((p) => (
              <option key={p.id} value={p.id}>{p.label}</option>
            ))}
          </select>
          <p className="fleet-channel-expand-hint">{SUBSCRIPTION_PROVIDERS.find((p) => p.id === provider)?.detail}</p>
          {renderReasoningEffortUnsupportedNote()}
          <div className="fleet-detail-section-title" style={{ marginTop: 16 }}>Brain runs on</div>
          <GatewayBoxPicker
            workspaceId={workspaceId}
            value={gatewayBinding}
            onChange={(id) => { setGatewayBinding(id); setSaved(false); }}
            requireRuntime={cliRuntime}
          />
        </div>
      )}

      {mode === "local" && (
        <div className="fleet-channel-expand">
          <label className="fleet-wizard-label">Runtime</label>
          <select className="fleet-wizard-input" value={provider} onChange={(e) => { setProvider(e.currentTarget.value); setSaved(false); }}>
            {LOCAL_PROVIDERS.map((p) => (
              <option key={p.id} value={p.id}>{p.label}</option>
            ))}
          </select>
          <p className="fleet-channel-expand-hint">{LOCAL_PROVIDERS.find((p) => p.id === provider)?.detail}</p>
          <label className="fleet-wizard-label">Ollama model</label>
          <select className="fleet-wizard-input" value={selectedModel} onChange={(e) => { setSelectedModel(e.currentTarget.value); setSaved(false); }}>
            {modelsForProvider("ollama").map((m) => <option key={m} value={m}>{m}</option>)}
          </select>
          <p className="fleet-channel-expand-hint">
            This turn only works if this model is actually pulled on the box below — run{" "}
            <code>ollama pull {selectedModel || "llama3.2"}</code> there first if you haven't.
          </p>
          {renderReasoningEffortUnsupportedNote()}
          <div className="fleet-detail-section-title" style={{ marginTop: 16 }}>Brain runs on</div>
          <GatewayBoxPicker workspaceId={workspaceId} value={gatewayBinding} onChange={(id) => { setGatewayBinding(id); setSaved(false); }} requireLocalModel />
        </div>
      )}

      {agent && (
        <Disclosure label="Advanced — context policy">
          <ContextPolicySection workspaceId={workspaceId} agentId={agentId} agent={agent} />
        </Disclosure>
      )}
    </div>
  );
}

// ── Shared empty state ──────────────────────────────────────────────────────

function EmptyState({
  icon: Icon,
  title,
  body,
  action,
  onAction,
}: {
  icon?: LucideIcon;
  title: string;
  body: string;
  action?: string;
  onAction?: () => void;
}) {
  return (
    <div className="fleet-tab-state">
      {Icon && (
        <div className="fleet-empty-icon">
          <Icon size={20} strokeWidth={1.75} />
        </div>
      )}
      <div className="fleet-tab-state-title">{title}</div>
      <div className="fleet-tab-state-body">{body}</div>
      {action && onAction && (
        <div className="fleet-empty-actions">
          <button type="button" className="fleet-btn fleet-btn--accent" onClick={onAction}>
            {action}
          </button>
        </div>
      )}
    </div>
  );
}

