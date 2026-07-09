"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import {
  Brain,
  Check,
  ChevronRight,
  Cpu,
  Inbox,
  LayoutGrid,
  Loader2,
  Lock,
  MessageSquare,
  PanelRightClose,
  PanelRightOpen,
  Pencil,
  Play,
  Plug,
  Radio,
  Sparkles,
  Square,
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
  type FleetAgent,
  type FleetAgentActivity,
  type FleetChannel,
} from "./fleet-data";
import { deriveStatus, derivePlacement, timeAgo, type AgentStatusTone } from "./fleet-presentation";
import { StatusChip } from "./fleet-indicators";
import { FleetRightPanel, PanelSection, PanelRow } from "./FleetRightPanel";
import { HeaderAction } from "./Breadcrumbs";
import { CHANNEL_ICONS } from "./fleet-icons";
import { ConnectorPicker } from "./ConnectorPicker";
import { buildCookieAuthHeaders } from "@/lib/auth/csrf";

type TabId = "overview" | "work" | "channels" | "connectors" | "hardware" | "model" | "memory" | "tools" | "chat";

const TABS: { id: TabId; label: string; icon: LucideIcon }[] = [
  { id: "overview", label: "Overview", icon: LayoutGrid },
  { id: "work", label: "Work", icon: Inbox },
  { id: "channels", label: "Channels", icon: Radio },
  { id: "connectors", label: "Connectors", icon: Plug },
  { id: "tools", label: "Tools", icon: Wrench },
  { id: "hardware", label: "Hardware", icon: Cpu },
  { id: "model", label: "Model", icon: Sparkles },
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
  // Properties drawer — closed by default (it's an overlay now, not a
  // permanent flex sibling); the toolbar toggle button owns opening it.
  const [panelOpen, setPanelOpen] = useState(false);
  const { events, loading: activityLoading } = useFleetAgentActivity(workspaceId, agentId);
  const { channels } = useFleetAgentChannels(workspaceId, agentId);
  const { connectors } = useFleetAgentConnectors(workspaceId, agentId);
  const [costToday, setCostToday] = useState<number | null>(null);

  const status = deriveStatus(agent?.hardware_status || "unknown", agent?.stopped?.active, Boolean(agent?.last_activity));

  useEffect(() => {
    let cancelled = false;
    fetch(`/api/w/${encodeURIComponent(workspaceId)}/fleet/usage?scope=agent&id=${encodeURIComponent(agentId)}&period=day`, { credentials: "include" })
      .then((r) => (r.ok ? r.json() : null))
      .then((d) => { if (!cancelled && d?.totals) setCostToday(Number(d.totals.usd_cost || 0)); })
      .catch(() => {});
    return () => { cancelled = true; };
  }, [workspaceId, agentId]);

  const connectedChannels = channels.filter((c: any) => c?.connected).length;
  const connectedConnectors = connectors.filter((c: any) => c?.connected).length;
  const preset = agent?.capability_preset || agent?.purpose_preset || "standard";
  const resolvedModel = String(
    agent?.model_config?.model
    || agent?.model_config?.resolved_model
    || (agent?.model_config?.mode === "local" ? "Local · Ollama" : "")
    || "Platform default",
  );

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

  const propertiesPanel = (
    <FleetRightPanel open={panelOpen} onClose={() => setPanelOpen(false)}>
      <PanelSection title="Properties">
        <PanelRow label="Status" value={<StatusChip tone={status.tone} label={status.label} />} />
        <PanelRow label="Preset" value={<span style={{ textTransform: "capitalize" }}>{preset}</span>} />
        <PanelRow label="Model" value={resolvedModel} />
        <PanelRow
          label="Project"
          value={projectName || <span className="fleet-skeleton-bar" style={{ width: 56, display: "inline-block" }} aria-label="Loading project…" />}
          tone={projectName ? "default" : "muted"}
        />
        <PanelRow label="Cost today" value={costToday === null ? "…" : `$${costToday.toFixed(4)}`} tone={costToday ? "accent" : "muted"} />
        <PanelRow label="Channels" value={connectedChannels} />
        <PanelRow label="Connectors" value={connectedConnectors} />
      </PanelSection>
    </FleetRightPanel>
  );

  const inner = (
    <>
      {/* Tabs live at the TOP, under the breadcrumb — one navigation only. */}
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
        <div className="fleet-detail-tabbar-actions">
          <button
            type="button"
            className={`fleet-icon-btn${panelOpen ? " is-active" : ""}`}
            onClick={() => setPanelOpen((v) => !v)}
            aria-label="Properties"
            aria-pressed={panelOpen}
            title="Properties"
          >
            {panelOpen ? <PanelRightClose size={16} strokeWidth={1.75} /> : <PanelRightOpen size={16} strokeWidth={1.75} />}
          </button>
        </div>
      </div>

      {/* Columns: main content sheet (full width, never reflows) + the
          properties drawer, an overlay layer positioned relative to this
          wrapper — never a flex sibling that steals width. */}
      <div className="fleet-detail-columns">
        <div className="fleet-detail-body">
          {activeTab === "overview" && (
            <OverviewTab
              workspaceId={workspaceId}
              agentId={agentId}
              agent={agent}
              status={status}
              events={events}
              loading={activityLoading}
              onChat={() => onChat(agentId)}
              onRenamed={onRenamed}
            />
          )}
          {activeTab === "work" && <WorkTab workspaceId={workspaceId} agentId={agentId} agent={agent} />}
          {activeTab === "channels" && (
            <ChannelsTab workspaceId={workspaceId} agentId={agentId} agent={agent} />
          )}
          {activeTab === "connectors" && (
            <ConnectorsTab workspaceId={workspaceId} agentId={agentId} agent={agent} />
          )}
          {activeTab === "tools" && (
            <ToolsTab workspaceId={workspaceId} agentId={agentId} agent={agent} onChat={() => onChat(agentId)} />
          )}
          {activeTab === "hardware" && <HardwareTab workspaceId={workspaceId} agentId={agentId} agent={agent} />}
          {activeTab === "model" && <ModelTab workspaceId={workspaceId} agentId={agentId} agent={agent} />}
          {activeTab === "memory" && (
            <MemoryTab workspaceId={workspaceId} agentId={agentId} agent={agent} onChat={() => onChat(agentId)} />
          )}
          {activeTab === "chat" && <ChatTab workspaceId={workspaceId} agentId={agentId} agent={agent} />}
        </div>
        {propertiesPanel}
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

/** One compact status line: presence/hardware + last heartbeat + whatever
 *  this agent is doing right now. Every clause is honest-when-empty — no
 *  clause claims a state the data doesn't support (e.g. a cloud text-agent
 *  has no queued-run concept at all, so it can only ever read Idle/Active,
 *  never a stale "Working on…"). A never-run agent reads Ready, same as
 *  everywhere else — "Idle" implies it has done something before and is
 *  merely between tasks, which isn't true yet. */
function NowStrip({ agent, status }: { agent: FleetAgent | null; status: { tone: AgentStatusTone; label: string } }) {
  if (!agent) return null;
  // Stopped wins over everything, including a stale current_run_id from
  // before the stop — a killed agent never reads "Working on a task".
  const working = status.tone !== "stopped" && Boolean(agent.current_run_id);
  const nowLabel = working ? "Working on a task" : status.tone === "stopped" ? status.label : status.label === "Active" ? "Idle" : status.label;
  const heartbeatText = agent.last_heartbeat ? `heartbeat ${timeAgo(agent.last_heartbeat)}` : "never heartbeat";
  return (
    <div className="fleet-now-strip" aria-label="Current status">
      <StatusChip tone={status.tone} label={nowLabel} />
      <span className="fleet-now-meta">{heartbeatText}</span>
    </div>
  );
}

// Owner-only stop control — the header-level "Stop agent" button, and once
// stopped, an honest chip naming who stopped it (never a bare "Stopped").
// kill_switch_gate.py enforces this on the backend before any turn runs;
// this is purely the control surface.
function StopAgentControl({
  workspaceId, agentId, agent, onChanged,
}: { workspaceId: string; agentId: string; agent: FleetAgent | null; onChanged?: () => void }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const stopped = agent?.stopped;

  async function handleStop() {
    setBusy(true);
    setError(null);
    const result = await stopFleetAgent(workspaceId, agentId);
    setBusy(false);
    if (result.ok) onChanged?.();
    else setError(result.error || "Could not stop this agent.");
  }

  async function handleResume() {
    setBusy(true);
    setError(null);
    const result = await resumeFleetAgent(workspaceId, agentId);
    setBusy(false);
    if (result.ok) onChanged?.();
    else setError(result.error || "Could not resume this agent.");
  }

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
      <button type="button" className="fleet-btn" disabled={busy} onClick={handleStop}>
        {busy ? <Loader2 size={14} style={{ animation: "spin 1s linear infinite" }} /> : <Square size={14} strokeWidth={1.75} />}
        Stop agent
      </button>
      {error && <span className="fleet-stop-error">{error}</span>}
    </div>
  );
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
      : parsed.toLocaleDateString(undefined, { weekday: "long", month: "long", day: "numeric" });
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
  events,
  loading,
  onRenamed,
}: {
  workspaceId: string;
  agentId: string;
  agent: FleetAgent | null;
  status: { tone: AgentStatusTone; label: string };
  events: FleetAgentActivity[];
  loading: boolean;
  onChat: () => void;
  onRenamed?: () => void;
}) {
  const deployed = status.label !== "Not deployed";
  const placement = derivePlacement(agent?.runtime_target || "unknown", deployed);
  const role = agent?.role || "agent";
  const isMaster = role === "operator";
  const dayGroups = groupActivityByDay(events);

  return (
    <div className="fleet-detail-overview">
      {agent && (
        <AgentTitle workspaceId={workspaceId} agentId={agentId} label={agent.label || ""} onRenamed={onRenamed} />
      )}
      <NowStrip agent={agent} status={status} />

      {/* The THING (status/placement/role + activity), not numbers. Counts and
          cost live in the properties panel; the "Chat" action lives top-right. */}
      <div className="fleet-config" style={{ marginTop: 0 }}>
        <div className="fleet-config-row">
          <span className="fleet-config-label">Status</span>
          <span className="fleet-config-value"><StatusChip tone={status.tone} label={status.label} /></span>
        </div>
        <div className="fleet-config-row">
          <span className="fleet-config-label">Placement</span>
          <span className="fleet-config-value">{placement}</span>
        </div>
        <div className="fleet-config-row">
          <span className="fleet-config-label">Role</span>
          <span className="fleet-config-value" style={{ textTransform: "capitalize" }}>{role}</span>
        </div>
      </div>

      {!isMaster && agent && (
        <PersonaEditor workspaceId={workspaceId} agentId={agentId} agent={agent} />
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
                  <div className={`fleet-activity-dot${event.status === "logged" ? "" : " is-warn"}`} />
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
                        {new Date(event.created_at).toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" })}
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
      <div className="fleet-detail-section-title" style={{ marginTop: 0 }}>Persona</div>
      <textarea
        className="fleet-persona-textarea"
        value={draft}
        onChange={(e) => { setDraft(e.currentTarget.value); setSaved(false); }}
        placeholder="What this agent is and how it should behave — e.g. “You handle customer refund requests. Be concise, and always confirm the order number before acting.”"
        spellCheck
      />
      <div style={{ marginTop: 8, display: "flex", alignItems: "center", gap: 12 }}>
        <button type="button" className="fleet-btn fleet-btn--accent" onClick={save} disabled={saving}>
          {saving ? "Saving…" : saved ? <><Check size={14} strokeWidth={2} /> Saved</> : "Save"}
        </button>
        {error && <span className="fleet-channel-expand-error" style={{ margin: 0 }}>{error}</span>}
      </div>
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
        emptyBody={`Talk to ${label} the way a customer would — it replies as itself, using whatever's configured on the Model and Tools tabs.`}
        placeholder={`Message ${label}…`}
        sourceTag="fleet_agent_chat"
      />
    </div>
  );
}

// ── Channels ────────────────────────────────────────────────────────────────

// Fixed platform order the grid renders in, mapped to the backend
// connection id that carries that platform's live status.
const CHANNEL_GRID_PLATFORMS: { label: string; id: string }[] = [
  { label: "Telegram", id: "sage_telegram_hosted" },
  { label: "Slack", id: "slack" },
  { label: "Discord", id: "discord_bot" },
  { label: "WhatsApp", id: "whatsapp_personal" },
  { label: "WeChat", id: "wechat_personal" },
];

type ChannelDoor = { key: string; label: string; body: string; real: boolean };

// The two-door design: a channel card opens a focused banner showing only the
// connection paths that are REAL today for that platform — never a door that
// fails. A door with real:false renders grayed and inert (disabled, no
// onClick) rather than clickable into an error (contract rule #4).
const CHANNEL_DOORS: Record<string, ChannelDoor[]> = {
  sage_telegram_hosted: [
    { key: "byo_bot", label: "Bot token", body: "Bring your own bot — paste the token BotFather gave you.", real: true },
  ],
  slack: [
    { key: "oauth", label: "OAuth workspace", body: "Connect a Slack workspace — signed mentions and DMs route to Sage.", real: true },
  ],
  discord_bot: [
    { key: "oauth", label: "Bot app", body: "Install the Discord bot app — signed messages route to Sage.", real: true },
  ],
  whatsapp_personal: [
    { key: "business", label: "Business (Twilio)", body: "Meta blocks third-party AI assistants on the WhatsApp Business API — not available yet.", real: false },
  ],
  wechat_personal: [
    { key: "business", label: "Business", body: "The local bridge runtime isn't certified yet.", real: false },
    { key: "personal", label: "Personal", body: "The local bridge runtime isn't certified yet.", real: false },
  ],
};

function channelStatePill(channel: FleetChannel | undefined): { label: string; tone: "connected" | "gateway" | "locked" | "setup" } {
  if (!channel) return { label: "Unavailable", tone: "locked" };
  if (channel.connected) return { label: "Connected", tone: "connected" };
  if (channel.requiresGateway && channel.onlineGatewayCount === 0) return { label: "Needs Gateway", tone: "gateway" };
  if (channel.nextAction === "locked" || !channel.setupAvailable) return { label: "Not configured here", tone: "locked" };
  return { label: "Set up", tone: "setup" };
}

export function ChannelsTab({
  workspaceId, agentId, agent,
}: { workspaceId: string; agentId: string; agent: FleetAgent | null }) {
  const { channels, loading } = useFleetAgentChannels(workspaceId, agentId);
  const [expanded, setExpanded] = useState<string | null>(null);
  const [oauthBusy, setOauthBusy] = useState<string | null>(null);
  const [oauthError, setOauthError] = useState<string | null>(null);

  // Which door is picked inside the open banner. Only channels with more than
  // one door need an explicit pick — a single-door channel auto-selects its
  // one real door (see `activeDoor` below).
  const [selectedDoor, setSelectedDoor] = useState<string | null>(null);
  const [byoToken, setByoToken] = useState("");
  const [byoBotBusy, setByoBotBusy] = useState(false);
  const [byoBotError, setByoBotError] = useState<string | null>(null);
  const [byoBotSaved, setByoBotSaved] = useState(false);
  const [firstContactReply, setFirstContactReply] = useState(!!agent?.telegram_first_contact_reply);
  const [firstContactSaving, setFirstContactSaving] = useState(false);
  // agent starts null and loads async — resync once the real value arrives
  // (and again if it changes, e.g. edited from another tab) without
  // clobbering an in-progress toggle on every 30s poll of an unchanged value.
  useEffect(() => {
    if (agent) setFirstContactReply(!!agent.telegram_first_contact_reply);
  }, [agent?.telegram_first_contact_reply]);

  const saveByoBotToken = useCallback(async () => {
    if (!byoToken.trim()) {
      setByoBotError("Paste the token BotFather gave you.");
      return;
    }
    setByoBotBusy(true);
    setByoBotError(null);
    try {
      const res = await fetch(
        `/api/w/${encodeURIComponent(workspaceId)}/fleet/agent-channels/telegram?agent_id=${encodeURIComponent(agentId)}`,
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
    } catch (e) {
      setByoBotError(e instanceof Error ? e.message : "Could not save the bot token.");
    } finally {
      setByoBotBusy(false);
    }
  }, [workspaceId, agentId, byoToken]);

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
        body: JSON.stringify({ workspace_id: workspaceId, surface: "sage" }),
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
  }, [workspaceId]);

  if (loading) {
    return <div className="fleet-activity-skeleton" aria-label="Loading channels"><div className="fleet-skeleton-bar" style={{ width: "80%" }} /></div>;
  }

  const byId = new Map(channels.map((c) => [c.id, c]));

  function closeBanner() {
    setExpanded(null);
    setSelectedDoor(null);
    setByoToken("");
    setByoBotError(null);
    setByoBotSaved(false);
    setOauthError(null);
  }

  function handleCardClick(platform: typeof CHANNEL_GRID_PLATFORMS[number]) {
    if (expanded === platform.id) {
      closeBanner();
      return;
    }
    setExpanded(platform.id);
    setSelectedDoor(null);
    setByoToken("");
    setByoBotError(null);
    setByoBotSaved(false);
    setOauthError(null);
  }

  const activePlatform = CHANNEL_GRID_PLATFORMS.find((p) => p.id === expanded) || null;
  const doors = expanded ? CHANNEL_DOORS[expanded] || [] : [];
  // A lone real door needs no picker — it auto-activates. Otherwise the user's
  // click on a specific (real) door decides which one is active.
  const activeDoor = doors.length === 1 && doors[0].real ? doors[0] : doors.find((d) => d.key === selectedDoor) || null;

  return (
    <div>
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
              onClick={() => handleCardClick(platform)}
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
              {doors.length >= 1 && (
                <div className="fleet-wizard-options">
                  {doors.map((door) => {
                    const isOnlyRealDoor = doors.length === 1 && door.real;
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
                      <button type="button" className="fleet-btn fleet-btn--accent" onClick={saveByoBotToken} disabled={byoBotBusy}>
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

              {/* Slack / Discord: single-path OAuth */}
              {(activePlatform.id === "slack" || activePlatform.id === "discord_bot") && activeDoor && (
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
                </div>
              )}

              {/* Gateway-based personal-channel pairing lives on Sage's own console
                   now (SageLauncher Connect tab) — not on individual agent pages. */}
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

  if (!agent) {
    return <div className="fleet-activity-skeleton" aria-label="Loading connectors"><div className="fleet-skeleton-bar" style={{ width: "70%" }} /></div>;
  }

  if (!projectId) {
    return (
      <EmptyState
        icon={Plug}
        title="No project assigned"
        body="This agent has no project — connectors are shared per project. Assign a project before connecting apps."
      />
    );
  }

  return <ConnectorPicker workspaceId={workspaceId} projectId={projectId} agentId={agentId} />;
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

function ToolsTab({
  workspaceId, agentId, agent, onChat,
}: { workspaceId: string; agentId: string; agent: FleetAgent | null; onChat: () => void }) {
  const { tools, coreTools, isMaster, loading, refresh } = useFleetAgentTools(workspaceId, agentId);
  const [pending, setPending] = useState<string | null>(null);
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

  if (loading) {
    return <div className="fleet-activity-skeleton" aria-label="Loading tools"><div className="fleet-skeleton-bar" style={{ width: "60%" }} /></div>;
  }

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
      {isMaster ? (
        <p className="fleet-channel-expand-hint" style={{ marginTop: 0 }}>
          This is the operator agent — it has unrestricted tool access, not gated by these toggles.
        </p>
      ) : (
        <div className="fleet-detail-section-title" style={{ marginTop: 0 }}>
          {enabledCount} of {tools.length} tools enabled
        </div>
      )}
      {tools.map((t) => (
        <div key={t.id} className="fleet-toggle-row">
          <div style={{ minWidth: 0 }}>
            <div className="fleet-toggle-row-label">{t.label}</div>
            {t.description && <div className="fleet-toggle-row-desc">{t.description}</div>}
          </div>
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
      ))}
      {error && <p className="fleet-channel-expand-error">{error}</p>}
      {coreTools.length > 0 && (
        <Disclosure label={`${coreTools.length} core ${coreTools.length === 1 ? "tool" : "tools"} — always on`}>
          <p className="fleet-channel-expand-hint" style={{ marginTop: 0 }}>
            Every agent has these regardless of the toggles above — memory, search, and task completion.
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
  BYOK_PROVIDERS, SUBSCRIPTION_PROVIDERS, LOCAL_PROVIDERS, providerLabel, MODE_LABELS,
  COMING_SOON_MODES, COMING_SOON_NOTE, runtimeForProvider, type ProviderMode,
  FREEFORM_MODEL_PROVIDERS, modelsForProvider, defaultModelForProvider,
} from "./fleet-provider-constants";
import { GatewayBoxPicker } from "./gateway-box-picker";

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
          {maxTok > 0 ? `${maxTok.toLocaleString()} tokens` : "model default"} → {action}
        </span>
      </div>
      <div className="fleet-config-row">
        <span className="fleet-config-label">Cost today</span>
        <span className="fleet-config-value">{cost === null ? "…" : `$${cost.toFixed(4)}`}</span>
      </div>
    </div>
  );
}

// Which paired box this agent's TOOL calls (shell/file/browser) prefer — a
// separate concept from the AI-brain gateway_binding below (which names the
// box that HOSTS the model itself, only used in local/cli_subscription mode).
// Backed by hardware_access (none/gateway column) + preferred_gateway_id
// (metadata hint _resolve_direct_tool_gateway_id checks first, falling back
// to any live gateway in the workspace when unset or offline).
function HardwareBindingSection({
  workspaceId, agentId, agent,
}: { workspaceId: string; agentId: string; agent: FleetAgent }) {
  const locked = !!agent.hardware_access_locked;
  const [wantsHardware, setWantsHardware] = useState((agent.hardware_access || "none").toLowerCase() !== "none");
  const [gatewayId, setGatewayId] = useState(agent.preferred_gateway_id || "");
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function save() {
    setSaving(true);
    setError(null);
    try {
      const res = await fetch(`/api/w/${encodeURIComponent(workspaceId)}/fleet/agents/${encodeURIComponent(agentId)}`, {
        method: "PATCH",
        credentials: "include",
        headers: buildCookieAuthHeaders("PATCH", { "Content-Type": "application/json" }),
        body: JSON.stringify({
          patch: {
            hardware_access: wantsHardware ? "gateway" : "none",
            preferred_gateway_id: wantsHardware ? gatewayId : "",
          },
        }),
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
    <div style={{ marginBottom: 20 }}>
      <div className="fleet-detail-section-title" style={{ marginTop: 0 }}>Hardware</div>
      {locked ? (
        <p className="fleet-channel-expand-hint" style={{ marginTop: 0 }}>
          This is a knowledge agent — hardware access is off and policy-locked. Change its capability
          preset to grant hardware.
        </p>
      ) : (
        <>
          <div className="fleet-wizard-options">
            <button
              type="button"
              className={`fleet-wizard-option${!wantsHardware ? " is-selected" : ""}`}
              onClick={() => { setWantsHardware(false); setSaved(false); }}
            >
              <span className="fleet-wizard-option-label">Cloud only</span>
              <span className="fleet-wizard-option-body">No computer access — runs entirely in the cloud.</span>
            </button>
            <button
              type="button"
              className={`fleet-wizard-option${wantsHardware ? " is-selected" : ""}`}
              onClick={() => { setWantsHardware(true); setSaved(false); }}
            >
              <span className="fleet-wizard-option-label">A paired computer</span>
              <span className="fleet-wizard-option-body">Shell, filesystem, and browser access on a box you've paired.</span>
            </button>
          </div>
          {wantsHardware && (
            <>
              <GatewayBoxPicker workspaceId={workspaceId} value={gatewayId} onChange={(id) => { setGatewayId(id); setSaved(false); }} />
              <p className="fleet-channel-expand-hint">Optional — leave unset to use whichever paired computer is online.</p>
            </>
          )}
          <div style={{ marginTop: 12, display: "flex", alignItems: "center", gap: 12 }}>
            <button type="button" className="fleet-btn fleet-btn--accent" onClick={save} disabled={saving}>
              {saving ? "Saving…" : saved ? <><Check size={14} strokeWidth={2} /> Saved</> : "Save"}
            </button>
            {error && <span className="fleet-channel-expand-error" style={{ margin: 0 }}>{error}</span>}
          </div>
        </>
      )}
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
        <button type="button" className="fleet-btn fleet-btn--accent" onClick={save} disabled={saving}>
          {saving ? "Saving…" : saved ? <><Check size={14} strokeWidth={2} /> Saved</> : "Save"}
        </button>
        {error && <span className="fleet-channel-expand-error" style={{ margin: 0 }}>{error}</span>}
      </div>
    </div>
  );
}

function ModelTab({ workspaceId, agentId, agent }: { workspaceId: string; agentId: string; agent: FleetAgent | null }) {
  const config = agent?.model_config || {};
  const [mode, setMode] = useState<ProviderMode>(resolveDisplayMode(config));
  const [provider, setProvider] = useState<string>(config.provider || "");
  const [gatewayBinding, setGatewayBinding] = useState<string>(config.gateway_binding || "");
  const [selectedModel, setSelectedModel] = useState<string>(config.model || "");
  const [apiKey, setApiKey] = useState("");
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
  // cli_subscription is still not dispatchable (Phase 3) — saving it would
  // resolve to a guaranteed "not yet available" turn error. Block save.
  const isComingSoon = COMING_SOON_MODES.has(mode);
  // BYO-brain Phase 2: "local" (Ollama on the paired box) is live, but a local
  // agent MUST name which box runs it, or every turn fails with "no computer
  // is bound". Require a gateway before saving.
  const localNeedsBox = mode === "local" && !gatewayBinding.trim();
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const resolvedProvider = config.provider || config.resolved_provider_label;
  const resolvedModel = config.model || config.resolved_model;
  const displayProvider = resolvedProvider || "Platform default";
  const displayModel = resolvedModel || "Platform default";
  const isPlatformDefault = !config.provider && config.mode !== "byok_api" && config.mode !== "cli_subscription" && config.mode !== "local";

  async function save() {
    if (COMING_SOON_MODES.has(mode)) {
      setError(`${COMING_SOON_NOTE}. This option can’t be saved yet.`);
      return;
    }
    if (mode === "local" && !gatewayBinding.trim()) {
      setError("Pick a computer (with Ollama) to run this agent’s local model.");
      return;
    }
    setSaving(true);
    setError(null);
    setSaved(false);
    try {
      if (mode === "byok_api") {
        if (!apiKey.trim()) {
          // Reusing existing vault key — only patch config
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
    const res = await fetch(`/api/w/${encodeURIComponent(workspaceId)}/fleet/agents/${encodeURIComponent(agentId)}`, {
      method: "PATCH",
      credentials: "include",
      headers: buildCookieAuthHeaders("PATCH", { "Content-Type": "application/json" }),
      body: JSON.stringify({ patch: { model_config: patch } }),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok || data?.ok === false) throw new Error(data?.error || `HTTP ${res.status}`);
  }

  return (
    <div>
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
          className={`fleet-wizard-option fleet-wizard-option--soon${mode === "cli_subscription" ? " is-selected" : ""}`}
          onClick={() => { setMode("cli_subscription"); setProvider(provider || "claude_code_cli"); setSaved(false); }}
        >
          <span className="fleet-wizard-option-label">Your subscription</span>
          <span className="fleet-wizard-option-body">Claude Code or Codex via Gateway.</span>
          <span className="fleet-wizard-option-note"><Lock size={11} strokeWidth={2} /> {COMING_SOON_NOTE}</span>
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
          <GatewayBoxPicker workspaceId={workspaceId} value={gatewayBinding} onChange={(id) => { setGatewayBinding(id); setSaved(false); }} />
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
          <GatewayBoxPicker workspaceId={workspaceId} value={gatewayBinding} onChange={(id) => { setGatewayBinding(id); setSaved(false); }} requireLocalModel />
        </div>
      )}

      <div style={{ marginTop: 16, display: "flex", alignItems: "center", gap: 12 }}>
        <button type="button" className="fleet-btn fleet-btn--accent" onClick={save} disabled={saving || isComingSoon || localNeedsBox}>
          {saving ? "Saving…" : saved ? "Saved ✓" : "Save"}
        </button>
        {isComingSoon && (
          <span className="fleet-channel-expand-hint" style={{ margin: 0 }}>{COMING_SOON_NOTE} — you can’t save this yet.</span>
        )}
        {localNeedsBox && !isComingSoon && (
          <span className="fleet-channel-expand-hint" style={{ margin: 0 }}>Pick a computer to run this agent’s local model.</span>
        )}
        {error && <span className="fleet-channel-expand-error" style={{ margin: 0 }}>{error}</span>}
      </div>

      {agent && (
        <div style={{ marginTop: 24, paddingTop: 20, borderTop: "1px solid var(--border)" }}>
          <HardwareBindingSection workspaceId={workspaceId} agentId={agentId} agent={agent} />
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

