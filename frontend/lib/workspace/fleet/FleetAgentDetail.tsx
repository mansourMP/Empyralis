"use client";

import { useCallback, useEffect, useState } from "react";
import {
  Brain,
  Check,
  Cpu,
  ExternalLink,
  Inbox,
  LayoutGrid,
  Loader2,
  Lock,
  MessageSquare,
  PanelRight,
  Plug,
  Radio,
  Sparkles,
  Wrench,
  X,
  type LucideIcon,
} from "lucide-react";

import { WorkTab } from "./tabs/WorkTab";
import { HardwareTab } from "./tabs/HardwareTab";
import { MemoryTab } from "./tabs/MemoryTab";

import {
  useFleetAgentActivity,
  useFleetAgentChannels,
  useFleetAgentConnectors,
  useFleetAgentTools,
  type FleetAgent,
  type FleetChannel,
} from "./fleet-data";
import { deriveStatus, derivePlacement, statusClass } from "./fleet-presentation";
import { CHANNEL_ICONS } from "./fleet-icons";
import { ConnectorPicker } from "./ConnectorPicker";
import { GatewayPairPanel } from "../../gateway/GatewayPairPanel";
import { buildCookieAuthHeaders } from "@/lib/auth/csrf";

type TabId = "overview" | "work" | "channels" | "connectors" | "hardware" | "model" | "memory" | "chat";

const TABS: { id: TabId; label: string; icon: LucideIcon }[] = [
  { id: "overview", label: "Overview", icon: LayoutGrid },
  { id: "work", label: "Work", icon: Inbox },
  { id: "channels", label: "Channels", icon: Radio },
  { id: "connectors", label: "Connectors", icon: Plug },
  { id: "hardware", label: "Hardware", icon: Cpu },
  { id: "model", label: "Model", icon: Sparkles },
  { id: "memory", label: "Memory", icon: Brain },
];

/**
 * Agent detail — centered modal (~80vw) with an internal right nav (tabs),
 * collapsible via the toggle button in the content column. Every tab has
 * real data or an intentional empty state with a working next action. No
 * stubs.
 */
export function FleetAgentDetail({
  workspaceId,
  agentId,
  agent,
  onChat,
  onClose,
  variant = "modal",
  initialTab,
  onTabChange,
}: {
  workspaceId: string;
  agentId: string;
  agent: FleetAgent | null;
  onChat: (agentId: string) => void;
  onClose?: () => void;
  variant?: "modal" | "page";
  initialTab?: TabId;
  onTabChange?: (tab: TabId) => void;
}) {
  const [activeTab, setActiveTab] = useState<TabId>(initialTab || "overview");
  const [navOpen, setNavOpen] = useState(true);
  const { events, loading: activityLoading } = useFleetAgentActivity(workspaceId, agentId);

  const status = deriveStatus(agent?.hardware_status || "unknown");
  const dotClass = statusClass(status.tone);

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

  // Modal mode closes on Escape; the routed page has no overlay to close.
  useEffect(() => {
    if (variant !== "modal" || !onClose) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [variant, onClose]);

  // Bridge for the command palette (mounted separately at the shell level) —
  // it dispatches a named event instead of a prop-threaded callback.
  useEffect(() => {
    const onSwitchTab = (e: Event) => {
      const tab = (e as CustomEvent<TabId>).detail;
      if (tab) selectTab(tab);
    };
    window.addEventListener("fleet:switch-tab", onSwitchTab);
    return () => window.removeEventListener("fleet:switch-tab", onSwitchTab);
  }, [selectTab]);

  const inner = (
    <>
      {/* Content */}
      <div className="fleet-detail-main">
        <button
          type="button"
          className={`fleet-detail-nav-toggle${variant === "modal" ? " fleet-detail-nav-toggle--modal" : ""}${navOpen ? " is-active" : ""}`}
          onClick={() => setNavOpen((v) => !v)}
          aria-label={navOpen ? "Hide tabs" : "Show tabs"}
          aria-pressed={navOpen}
          title={navOpen ? "Hide tabs" : "Show tabs"}
        >
          <PanelRight size={16} strokeWidth={1.75} />
        </button>
        {variant === "modal" && onClose && (
          <button type="button" className="fleet-detail-close" onClick={onClose} aria-label="Close">
            <X size={16} strokeWidth={1.75} />
          </button>
        )}
        <div className="fleet-detail-body">
          {activeTab === "overview" && (
            <OverviewTab
              workspaceId={workspaceId}
              agentId={agentId}
              agent={agent}
              statusLabel={status.label}
              events={events}
              loading={activityLoading}
              onChat={() => onChat(agentId)}
            />
          )}
          {activeTab === "work" && <WorkTab workspaceId={workspaceId} agentId={agentId} agent={agent} />}
          {activeTab === "channels" && (
            <ChannelsTab workspaceId={workspaceId} agentId={agentId} agent={agent} />
          )}
          {activeTab === "connectors" && (
            <ConnectorsTab workspaceId={workspaceId} agentId={agentId} agent={agent} />
          )}
          {activeTab === "hardware" && <HardwareTab workspaceId={workspaceId} agentId={agentId} agent={agent} />}
          {activeTab === "model" && <ModelTab workspaceId={workspaceId} agentId={agentId} agent={agent} />}
          {activeTab === "memory" && (
            <MemoryTab workspaceId={workspaceId} agentId={agentId} agent={agent} onChat={() => onChat(agentId)} />
          )}
          {activeTab === "chat" && <ChatTab agent={agent} />}
        </div>
      </div>

      {/* Right nav — tabs live here, not on the left */}
      <div className={`fleet-detail-nav${navOpen ? "" : " fleet-detail-nav--collapsed"}`}>
        <div className="fleet-detail-nav-header">
          <div className="fleet-detail-avatar">
            <div className="fleet-detail-avatar-icon">
              {(agent?.label || "A").charAt(0).toUpperCase()}
            </div>
            <span className={`fleet-detail-dot ${dotClass}`} />
          </div>
          <div className="fleet-detail-name">{agent?.label || "Agent"}</div>
          <span className={`fleet-detail-meta-status ${dotClass}`}>{status.label}</span>
        </div>
        <nav className="fleet-detail-nav-tabs">
          {TABS.map((tab) => {
            const Icon = tab.icon;
            return (
              <button
                key={tab.id}
                type="button"
                className={`fleet-detail-nav-tab${activeTab === tab.id ? " fleet-detail-nav-tab--active" : ""}`}
                onClick={() => selectTab(tab.id)}
              >
                <Icon size={16} strokeWidth={1.75} />
                {tab.label}
              </button>
            );
          })}
        </nav>
      </div>
    </>
  );

  if (variant === "page") {
    return (
      <div className="fleet-detail fleet-detail--page" aria-label={`${agent?.label || "Agent"} details`}>
        {inner}
      </div>
    );
  }

  return (
    <div className="fleet-detail-backdrop" onClick={onClose}>
      <div
        className="fleet-detail"
        role="dialog"
        aria-modal="true"
        aria-label={`${agent?.label || "Agent"} details`}
        onClick={(e) => e.stopPropagation()}
      >
        {inner}
      </div>
    </div>
  );
}

// ── Overview ────────────────────────────────────────────────────────────────

function OverviewTab({
  workspaceId,
  agentId,
  agent,
  statusLabel,
  events,
  loading,
  onChat,
}: {
  workspaceId: string;
  agentId: string;
  agent: FleetAgent | null;
  statusLabel: string;
  events: any[];
  loading: boolean;
  onChat: () => void;
}) {
  const deployed = statusLabel !== "Not deployed";
  const placement = derivePlacement(agent?.runtime_target || "unknown", deployed);
  const { channels } = useFleetAgentChannels(workspaceId, agentId);
  const { connectors } = useFleetAgentConnectors(workspaceId, agentId);
  const [costToday, setCostToday] = useState<number | null>(null);
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
  const preset = agent?.capability_preset || "standard";
  const rows: [string, string][] = [
    ["Status", statusLabel],
    ["Placement", placement],
    ["Role", agent?.role || "agent"],
  ];

  return (
    <div className="fleet-detail-overview">
      <div className="fleet-stat-grid" style={{ marginTop: 0 }}>
        <div className="fleet-stat-card"><div className="fleet-stat-value">{connectedChannels}</div><div className="fleet-stat-label">Channels</div></div>
        <div className="fleet-stat-card"><div className="fleet-stat-value">{connectedConnectors}</div><div className="fleet-stat-label">Connectors</div></div>
        <div className="fleet-stat-card"><div className="fleet-stat-value">{costToday === null ? "…" : `$${costToday.toFixed(4)}`}</div><div className="fleet-stat-label">Cost today</div></div>
        <div className="fleet-stat-card"><div className="fleet-stat-value" style={{ textTransform: "capitalize" }}>{preset}</div><div className="fleet-stat-label">Preset</div></div>
      </div>
      <div className="fleet-config">
        {rows.map(([label, value]) => (
          <div key={label} className="fleet-config-row">
            <span className="fleet-config-label">{label}</span>
            <span className="fleet-config-value">{String(value)}</span>
          </div>
        ))}
      </div>

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
          body="Events appear here after this agent processes its first turn."
          action="Chat with this agent"
          onAction={onChat}
        />
      ) : (
        <div className="fleet-activity">
          {events.map((event) => (
            <div key={event.event_id} className="fleet-activity-item">
              <div className={`fleet-activity-dot${event.status === "logged" ? "" : " is-warn"}`} />
              <div style={{ flex: 1, minWidth: 0 }}>
                <div className="fleet-activity-title">{event.title}</div>
                <div className="fleet-activity-meta">
                  <span>{event.event_class}</span>
                  <span>·</span>
                  <span>{event.action}</span>
                  <span>·</span>
                  <span className="fleet-activity-time">
                    {new Date(event.created_at).toLocaleString()}
                  </span>
                </div>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

// ── Chat ────────────────────────────────────────────────────────────────────

// Direct chat with a fleet agent isn't built yet — no turn-execution endpoint
// scoped to agent_install_id exists. Land here honestly instead of looping
// back through onChat into this same tab, or silently falling back to
// Overview under a "Chat" breadcrumb (the previous, confusing behavior).
function ChatTab({ agent }: { agent: FleetAgent | null }) {
  return (
    <EmptyState
      icon={MessageSquare}
      title={`Chat with ${agent?.label || "this agent"} isn't available yet`}
      body="Direct chat is on the roadmap. For now, configure this agent from its other tabs — Channels, Connectors, and Model."
    />
  );
}

// ── Memory ──────────────────────────────────────────────────────────────────

// Real per-agent SOUL.md/MEMORY.md/GOALS.md/etc. — the same context-file
// store the agent's own system prompt is built from (server_modules/
// workspace_context.py), scoped by agent_id via /api/sage-context-files.
// This is a fleet-native rewrite of the old workstation-activity-pane.tsx
// file-tree browser rather than a mount of that component directly: it
// hard-depends on useWorkspaceBoundary(), a context fleet routes never
// mount (see FleetShellDecider.tsx — fleet renders without the workstation
// shell), so importing it here would throw. Same files, same API, same
// "real, editable content" — a smaller editor, not the full pane's
// preview/pin/export feature set.
// Superseded by ./tabs/MemoryTab (Phase 6 tree). Kept until the 7B cleanup.
function LegacyMemoryTab({
  workspaceId, agentId, agent, onChat,
}: { workspaceId: string; agentId: string; agent: FleetAgent | null; onChat: () => void }) {
  const [files, setFiles] = useState<{ filename: string; content: string }[]>([]);
  const [selected, setSelected] = useState<string | null>(null);
  const [draft, setDraft] = useState("");
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      setLoading(true);
      setError(null);
      try {
        const res = await fetch(
          `/api/sage-context-files?workspace_id=${encodeURIComponent(workspaceId)}&agent_id=${encodeURIComponent(agentId)}`,
          { credentials: "include" },
        );
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const data = await res.json();
        if (cancelled) return;
        const list: { filename: string; content: string }[] = Array.isArray(data.files) ? data.files : [];
        setFiles(list);
        if (list.length > 0) {
          setSelected(list[0].filename);
          setDraft(list[0].content);
        }
      } catch {
        if (!cancelled) setError("Could not load memory files.");
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => { cancelled = true; };
  }, [workspaceId, agentId]);

  function selectFile(filename: string) {
    const f = files.find((x) => x.filename === filename);
    setSelected(filename);
    setDraft(f?.content || "");
    setSaved(false);
  }

  async function save() {
    if (!selected) return;
    setSaving(true);
    setError(null);
    try {
      const res = await fetch(`/api/sage-context-files/${encodeURIComponent(selected)}`, {
        method: "PATCH",
        credentials: "include",
        headers: buildCookieAuthHeaders("PATCH", { "Content-Type": "application/json" }),
        body: JSON.stringify({ workspace_id: workspaceId, agent_id: agentId, content: draft }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok || data?.ok === false) throw new Error(data?.detail || data?.error || `HTTP ${res.status}`);
      setFiles((cur) => cur.map((f) => (f.filename === selected ? { ...f, content: draft } : f)));
      setSaved(true);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not save.");
    } finally {
      setSaving(false);
    }
  }

  if (loading) {
    return (
      <div className="fleet-activity-skeleton" aria-label="Loading memory">
        {[60, 48, 72].map((w, i) => (
          <div key={i} className="fleet-skeleton-row">
            <div className="fleet-skeleton-bar" style={{ width: 8 }} />
            <div className="fleet-skeleton-bar" style={{ width: `${w}%` }} />
          </div>
        ))}
      </div>
    );
  }

  if (files.length === 0) {
    return (
      <EmptyState
        icon={Brain}
        title={error || "Nothing here yet"}
        body="Sage will write memories here as it learns about this agent's preferences, facts, and context."
        action="Chat with this agent"
        onAction={onChat}
      />
    );
  }

  return (
    <div className="fleet-memory-browser">
      <div className="fleet-memory-file-list">
        {files.map((f) => (
          <button
            key={f.filename}
            type="button"
            className={`fleet-memory-file-item${selected === f.filename ? " is-active" : ""}`}
            onClick={() => selectFile(f.filename)}
          >
            {f.filename}
          </button>
        ))}
      </div>
      <div className="fleet-memory-editor">
        {selected && (
          <>
            <div className="fleet-memory-editor-header">
              <span>{selected}</span>
              <button type="button" className="fleet-btn fleet-btn--accent" onClick={save} disabled={saving}>
                {saving ? "Saving…" : saved ? <><Check size={14} strokeWidth={2} /> Saved</> : "Save"}
              </button>
            </div>
            <textarea
              className="fleet-memory-editor-textarea"
              value={draft}
              onChange={(e) => { setDraft(e.currentTarget.value); setSaved(false); }}
              spellCheck={false}
            />
          </>
        )}
        {error && <p className="fleet-channel-expand-error">{error}</p>}
      </div>
    </div>
  );
}

// ── Channels ────────────────────────────────────────────────────────────────

// Fixed platform order the grid renders in, mapped to the backend
// connection id that carries that platform's live status.
const CHANNEL_GRID_PLATFORMS: { label: string; id: string; mode: "hosted" | "oauth" | "gateway" }[] = [
  { label: "Telegram", id: "sage_telegram_hosted", mode: "hosted" },
  { label: "Slack", id: "slack", mode: "oauth" },
  { label: "Discord", id: "discord_bot", mode: "oauth" },
  { label: "WhatsApp", id: "whatsapp_personal", mode: "gateway" },
  { label: "Signal", id: "signal_personal", mode: "gateway" },
  { label: "iMessage", id: "imessage_personal", mode: "gateway" },
  { label: "WeChat", id: "wechat_personal", mode: "gateway" },
];

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
  const { channels, hostedTelegramConfigured, loading } = useFleetAgentChannels(workspaceId, agentId);
  const [expanded, setExpanded] = useState<string | null>(null);
  const [pairing, setPairing] = useState(false);
  const [pairingCode, setPairingCode] = useState<string | null>(null);
  const [deepLink, setDeepLink] = useState<string | null>(null);
  const [pairError, setPairError] = useState<string | null>(null);
  const [pairResult, setPairResult] = useState<string | null>(null);
  const [oauthBusy, setOauthBusy] = useState<string | null>(null);
  const [oauthError, setOauthError] = useState<string | null>(null);

  // Telegram's 3-option sheet: hosted bot (already works) / your own bot
  // token / your personal account via Gateway. Slack and Discord stay
  // single-path OAuth below — they have no BYO-bot or personal-account
  // capability in the catalog today, so a 3-option sheet for them would be
  // two fake buttons. Wire those up when those paths actually exist.
  const [telegramOption, setTelegramOption] = useState<"hosted" | "byo_bot" | "personal" | null>(null);
  const [byoBotFields, setByoBotFields] = useState<string[]>([]);
  const [byoBotValues, setByoBotValues] = useState<Record<string, string>>({});
  const [byoBotBusy, setByoBotBusy] = useState(false);
  const [byoBotError, setByoBotError] = useState<string | null>(null);
  const [byoBotSaved, setByoBotSaved] = useState(false);
  const [personalWarningAck, setPersonalWarningAck] = useState(false);

  const startByoBotSetup = useCallback(async () => {
    setByoBotBusy(true);
    setByoBotError(null);
    try {
      const res = await fetch("/api/connections/telegram_bot/setup/start", {
        method: "POST",
        credentials: "include",
        headers: buildCookieAuthHeaders("POST", { "Content-Type": "application/json" }),
        body: JSON.stringify({ workspace_id: workspaceId, surface: "sage" }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(data?.detail || data?.error || `HTTP ${res.status}`);
      const fields: string[] = data?.auth_required_fields?.length ? data.auth_required_fields : ["bot_token"];
      setByoBotFields(fields);
      setByoBotValues({});
    } catch (e) {
      setByoBotError(e instanceof Error ? e.message : "Could not start bot setup.");
    } finally {
      setByoBotBusy(false);
    }
  }, [workspaceId]);

  const saveByoBotToken = useCallback(async () => {
    setByoBotBusy(true);
    setByoBotError(null);
    try {
      const res = await fetch("/api/connectors/vault", {
        method: "POST",
        credentials: "include",
        headers: buildCookieAuthHeaders("POST", { "Content-Type": "application/json" }),
        body: JSON.stringify({
          workspace_id: workspaceId,
          connector: "telegram_bot",
          label: "Telegram Bot",
          credentials: byoBotValues,
        }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(data?.detail || data?.error || `HTTP ${res.status}`);
      setByoBotSaved(true);
    } catch (e) {
      setByoBotError(e instanceof Error ? e.message : "Could not save the bot token.");
    } finally {
      setByoBotBusy(false);
    }
  }, [workspaceId, byoBotValues]);

  const startHostedPairing = useCallback(async () => {
    setPairing(true);
    setPairError(null);
    setPairResult(null);
    try {
      const res = await fetch("/api/sage/telegram-hosted/pair/start", {
        method: "POST",
        credentials: "include",
        headers: buildCookieAuthHeaders("POST", { "Content-Type": "application/json" }),
        body: JSON.stringify({ workspace_id: workspaceId }),
      });
      if (!res.ok) {
        const body = await res.text();
        throw new Error(body || `HTTP ${res.status}`);
      }
      const data = await res.json() as { pairing_code: string; deep_link: string | null };
      setPairingCode(data.pairing_code);
      if (data.deep_link) {
        setDeepLink(data.deep_link);
        window.open(data.deep_link, "_blank");
      }
      const check = setInterval(async () => {
        try {
          const s = await fetch(
            `/api/sage/telegram-hosted/pair/status?workspace_id=${encodeURIComponent(workspaceId)}`,
            { credentials: "include" },
          );
          if (s.ok) {
            const status = await s.json();
            if (status.paired) {
              clearInterval(check);
              setPairResult(`Connected — send a message to @${status.bot_username || "the bot"}`);
              setPairingCode(null);
              setPairing(false);
            }
          }
        } catch { /* keep polling */ }
      }, 3000);
      setTimeout(() => clearInterval(check), 120_000);
    } catch (e) {
      setPairError(e instanceof Error ? e.message : "Could not start pairing. Try again.");
      setPairing(false);
    }
  }, [workspaceId]);

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

  function handleCardClick(platform: typeof CHANNEL_GRID_PLATFORMS[number]) {
    if (platform.mode === "hosted") {
      setExpanded(expanded === platform.id ? null : platform.id);
      setTelegramOption(null);
      setByoBotFields([]);
      setByoBotError(null);
      setByoBotSaved(false);
      setPersonalWarningAck(false);
      return;
    }
    if (platform.mode === "oauth") {
      setExpanded(expanded === platform.id ? null : platform.id);
      setOauthError(null);
      return;
    }
    // gateway mode
    setExpanded(expanded === platform.id ? null : platform.id);
  }

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

      {expanded === "sage_telegram_hosted" && (
        <div className="fleet-channel-expand">
          <div className="fleet-wizard-options">
            <button
              type="button"
              className={`fleet-wizard-option${telegramOption === "hosted" ? " is-selected" : ""}`}
              onClick={() => setTelegramOption(telegramOption === "hosted" ? null : "hosted")}
            >
              <span className="fleet-wizard-option-label">Empyralis-hosted bot <span className="fleet-wizard-option-tag">Recommended</span></span>
              <span className="fleet-wizard-option-body">One-click pair. No BotFather setup, no token, no Gateway.</span>
            </button>
            <button
              type="button"
              className={`fleet-wizard-option${telegramOption === "byo_bot" ? " is-selected" : ""}`}
              onClick={() => setTelegramOption(telegramOption === "byo_bot" ? null : "byo_bot")}
            >
              <span className="fleet-wizard-option-label">Your own bot</span>
              <span className="fleet-wizard-option-body">Paste a BotFather token — we handle the rest.</span>
            </button>
            <button
              type="button"
              className={`fleet-wizard-option${telegramOption === "personal" ? " is-selected" : ""}`}
              onClick={() => setTelegramOption(telegramOption === "personal" ? null : "personal")}
            >
              <span className="fleet-wizard-option-label">Your personal Telegram account</span>
              <span className="fleet-wizard-option-body">The agent acts as you. Requires the Gateway.</span>
            </button>
          </div>

          {telegramOption === "hosted" && (
            <div className="fleet-channel-expand" style={{ marginTop: 12 }}>
              {pairResult ? (
                <div className="fleet-channel-expand-success">
                  <Check size={16} strokeWidth={2} /> {pairResult}
                </div>
              ) : pairingCode ? (
                <div>
                  <div className="fleet-pair-code">
                    <span className="fleet-pair-code-label">Code</span>
                    <code className="fleet-pair-code-value">{pairingCode}</code>
                  </div>
                  {deepLink && (
                    <a href={deepLink} target="_blank" rel="noopener noreferrer" className="fleet-btn fleet-btn--accent" style={{ marginLeft: 8 }}>
                      Open Telegram <ExternalLink size={14} style={{ marginLeft: 4 }} />
                    </a>
                  )}
                  <p className="fleet-channel-expand-hint" style={{ marginTop: 8 }}>
                    Send this code to the Empyralis bot on Telegram. This tab updates automatically when paired.
                  </p>
                </div>
              ) : (
                <div>
                  <button type="button" className="fleet-btn fleet-btn--accent" onClick={startHostedPairing} disabled={pairing}>
                    {pairing ? <><Loader2 size={14} style={{ animation: "spin 1s linear infinite" }} /> Starting…</> : "Pair Telegram"}
                  </button>
                  {!hostedTelegramConfigured && (
                    <p className="fleet-channel-expand-error">The hosted Telegram bot is not configured on this server.</p>
                  )}
                  {pairError && <p className="fleet-channel-expand-error">{pairError}</p>}
                </div>
              )}
            </div>
          )}

          {telegramOption === "byo_bot" && (
            <div className="fleet-channel-expand" style={{ marginTop: 12 }}>
              {byoBotSaved ? (
                <div className="fleet-channel-expand-success">
                  <Check size={16} strokeWidth={2} /> Bot token saved. Sage will use it for this channel.
                </div>
              ) : byoBotFields.length > 0 ? (
                <>
                  <p className="fleet-channel-expand-hint">Paste the token BotFather gave you when you created the bot.</p>
                  {byoBotFields.map((field) => (
                    <label key={field} className="gw-pair-panel-field" style={{ marginBottom: 10, display: "flex" }}>
                      <span>{field.split("_").map((w) => w.charAt(0).toUpperCase() + w.slice(1)).join(" ")}</span>
                      <input
                        type="password"
                        autoComplete="off"
                        value={byoBotValues[field] || ""}
                        onChange={(e) => setByoBotValues((cur) => ({ ...cur, [field]: e.currentTarget.value }))}
                      />
                    </label>
                  ))}
                  <button type="button" className="fleet-btn fleet-btn--accent" onClick={saveByoBotToken} disabled={byoBotBusy}>
                    {byoBotBusy ? "Saving…" : "Save token"}
                  </button>
                </>
              ) : (
                <button type="button" className="fleet-btn fleet-btn--accent" onClick={startByoBotSetup} disabled={byoBotBusy}>
                  {byoBotBusy ? <Loader2 size={14} style={{ animation: "spin 1s linear infinite" }} /> : "Connect your bot"}
                </button>
              )}
              {byoBotError && <p className="fleet-channel-expand-error">{byoBotError}</p>}
            </div>
          )}

          {telegramOption === "personal" && (
            <div className="fleet-channel-expand" style={{ marginTop: 12 }}>
              {!personalWarningAck ? (
                <>
                  <p className="fleet-channel-expand-error" style={{ marginTop: 0 }}>
                    This agent will act as <strong>you</strong> on Telegram. It can read your DMs and send
                    messages under your name. This needs the Gateway paired on your machine. Are you sure?
                  </p>
                  <button type="button" className="fleet-btn fleet-btn--accent" onClick={() => setPersonalWarningAck(true)}>
                    Yes, continue
                  </button>
                </>
              ) : (
                <>
                  <p className="fleet-channel-expand-hint">
                    This channel runs through Agent Computer (Gateway) on the paired machine.
                  </p>
                  <GatewayPairPanel workspaceId={workspaceId} compact />
                </>
              )}
            </div>
          )}
        </div>
      )}

      {(expanded === "slack" || expanded === "discord_bot") && (
        <div className="fleet-channel-expand">
          <p className="fleet-channel-expand-hint">
            {expanded === "slack"
              ? "Connect Slack with OAuth to route signed mentions or DMs into Sage."
              : "Connect Discord with an app install to route signed messages into Sage."}
          </p>
          <button
            type="button"
            className="fleet-btn fleet-btn--accent"
            onClick={() => void startOAuth(expanded)}
            disabled={oauthBusy === expanded}
          >
            {oauthBusy === expanded ? <Loader2 size={14} style={{ animation: "spin 1s linear infinite" }} /> : null}
            {oauthBusy === expanded ? "Starting…" : `Connect ${expanded === "slack" ? "Slack" : "Discord"}`}
          </button>
          {oauthError && <p className="fleet-channel-expand-error">{oauthError}</p>}
        </div>
      )}

      {expanded && ["whatsapp_personal", "signal_personal", "imessage_personal", "wechat_personal"].includes(expanded) && (
        <div className="fleet-channel-expand">
          <p className="fleet-channel-expand-hint">
            This channel runs through Agent Computer (Gateway) on the paired machine. Pair one below, or open Hardware for the full view.
          </p>
          <GatewayPairPanel workspaceId={workspaceId} compact />
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

// ── Tools ───────────────────────────────────────────────────────────────────

function ToolsTab({
  workspaceId, agentId, agent, onChat,
}: { workspaceId: string; agentId: string; agent: FleetAgent | null; onChat: () => void }) {
  const { tools, loading } = useFleetAgentTools(workspaceId, agentId);

  if (loading) {
    return <div className="fleet-activity-skeleton" aria-label="Loading tools"><div className="fleet-skeleton-bar" style={{ width: "60%" }} /></div>;
  }

  if (tools.length === 0) {
    return (
      <EmptyState
        icon={Wrench}
        title="No tools enabled"
        body="This agent has no tools in its capability manifest. Chat with Sage to configure tools for this agent."
        action="Chat to configure"
        onAction={onChat}
      />
    );
  }

  return (
    <div className="fleet-config">
      <div className="fleet-detail-section-title">{tools.length} {tools.length === 1 ? "tool" : "tools"}</div>
      {tools.map((t) => (
        <div key={t.id} className="fleet-config-row">
          <div>
            <div style={{ fontSize: 13, fontWeight: 500 }}>{t.label}</div>
            <div style={{ fontSize: 12, color: "var(--text-muted)" }}>{t.description}</div>
          </div>
          <span className="fleet-config-value" style={{ fontSize: 11 }}>{t.action_class}</span>
        </div>
      ))}
    </div>
  );
}

// ── Model ───────────────────────────────────────────────────────────────────

import { BYOK_PROVIDERS, SUBSCRIPTION_PROVIDERS, LOCAL_PROVIDERS, providerLabel, MODE_LABELS, COMING_SOON_MODES, COMING_SOON_NOTE, runtimeForProvider, type ProviderMode } from "./fleet-provider-constants";
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

function ModelTab({ workspaceId, agentId, agent }: { workspaceId: string; agentId: string; agent: FleetAgent | null }) {
  const config = agent?.model_config || {};
  const [mode, setMode] = useState<ProviderMode>(resolveDisplayMode(config));
  const [provider, setProvider] = useState<string>(config.provider || "");
  const [gatewayBinding, setGatewayBinding] = useState<string>(config.gateway_binding || "");
  const [apiKey, setApiKey] = useState("");
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
          const vaultRes = await fetch("/api/connectors/vault", {
            method: "POST",
            credentials: "include",
            headers: buildCookieAuthHeaders("POST", { "Content-Type": "application/json" }),
            body: JSON.stringify({
              workspace_id: workspaceId,
              connector: provider,
              label: providerLabel(provider),
              credentials: { api_key: apiKey.trim() },
            }),
          });
          const vaultData = await vaultRes.json().catch(() => ({}));
          if (!vaultRes.ok) throw new Error(vaultData?.detail || vaultData?.error || `HTTP ${vaultRes.status}`);
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

