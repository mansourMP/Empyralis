"use client";

import { useCallback, useEffect, useState } from "react";
import {
  Brain,
  Check,
  Cpu,
  ExternalLink,
  LayoutGrid,
  Loader2,
  MessageSquare,
  Plug,
  Radio,
  Wrench,
  X,
  type LucideIcon,
} from "lucide-react";

import {
  useFleetAgentActivity,
  useFleetAgentChannels,
  useFleetAgentConnectors,
  useFleetAgentMemory,
  useFleetAgentTools,
  type FleetAgent,
} from "./fleet-data";
import { deriveStatus, derivePlacement, statusClass } from "./fleet-presentation";

type TabId = "overview" | "chat" | "memory" | "channels" | "connectors" | "tools" | "model";

const TABS: { id: TabId; label: string; icon: LucideIcon }[] = [
  { id: "overview", label: "Overview", icon: LayoutGrid },
  { id: "chat", label: "Chat", icon: MessageSquare },
  { id: "memory", label: "Memory", icon: Brain },
  { id: "channels", label: "Channels", icon: Radio },
  { id: "connectors", label: "Connectors", icon: Plug },
  { id: "tools", label: "Tools", icon: Wrench },
  { id: "model", label: "Model", icon: Cpu },
];

/**
 * Agent detail — centered modal (~80vw) with internal left nav.
 * Every tab has real data or an intentional empty state with a
 * working next action. No stubs.
 */
export function FleetAgentDetail({
  workspaceId,
  agentId,
  agent,
  onChat,
  onClose,
}: {
  workspaceId: string;
  agentId: string;
  agent: FleetAgent | null;
  onChat: (agentId: string) => void;
  onClose: () => void;
}) {
  const [activeTab, setActiveTab] = useState<TabId>("overview");
  const { events, loading: activityLoading } = useFleetAgentActivity(workspaceId, agentId);

  const status = deriveStatus(agent?.hardware_status || "unknown");
  const dotClass = statusClass(status.tone);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  return (
    <div className="fleet-detail-backdrop" onClick={onClose}>
      <div
        className="fleet-detail"
        role="dialog"
        aria-modal="true"
        aria-label={`${agent?.label || "Agent"} details`}
        onClick={(e) => e.stopPropagation()}
      >
        {/* Left nav */}
        <div className="fleet-detail-nav">
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
                  onClick={() => setActiveTab(tab.id)}
                >
                  <Icon size={16} strokeWidth={1.75} />
                  {tab.label}
                </button>
              );
            })}
          </nav>
        </div>

        {/* Content */}
        <div className="fleet-detail-main">
          <button type="button" className="fleet-detail-close" onClick={onClose} aria-label="Close">
            <X size={16} strokeWidth={1.75} />
          </button>
          <div className="fleet-detail-body">
            {activeTab === "overview" && (
              <OverviewTab
                agent={agent}
                statusLabel={status.label}
                events={events}
                loading={activityLoading}
                onChat={() => onChat(agentId)}
              />
            )}
            {activeTab === "chat" && <ChatTab agent={agent} onChat={() => onChat(agentId)} />}
            {activeTab === "memory" && (
              <MemoryTab workspaceId={workspaceId} agentId={agentId} agent={agent} onChat={() => onChat(agentId)} />
            )}
            {activeTab === "channels" && (
              <ChannelsTab workspaceId={workspaceId} agentId={agentId} agent={agent} />
            )}
            {activeTab === "connectors" && (
              <ConnectorsTab workspaceId={workspaceId} agentId={agentId} agent={agent} />
            )}
            {activeTab === "tools" && (
              <ToolsTab workspaceId={workspaceId} agentId={agentId} agent={agent} onChat={() => onChat(agentId)} />
            )}
            {activeTab === "model" && <ModelTab agent={agent} />}
          </div>
        </div>
      </div>
    </div>
  );
}

// ── Overview ────────────────────────────────────────────────────────────────

function OverviewTab({
  agent,
  statusLabel,
  events,
  loading,
  onChat,
}: {
  agent: FleetAgent | null;
  statusLabel: string;
  events: any[];
  loading: boolean;
  onChat: () => void;
}) {
  const deployed = statusLabel !== "Not deployed";
  const placement = derivePlacement(agent?.runtime_target || "unknown", deployed);
  const rows: [string, string][] = [
    ["Status", statusLabel],
    ["Placement", placement],
    ["Role", agent?.role || "agent"],
  ];

  return (
    <div className="fleet-detail-overview">
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

function ChatTab({ agent, onChat }: { agent: FleetAgent | null; onChat: () => void }) {
  return (
    <EmptyState
      title={`Chat with ${agent?.label || "this agent"}`}
      body="Open the shared conversation thread and talk to this agent directly."
      action="Open chat"
      onAction={onChat}
    />
  );
}

// ── Memory ──────────────────────────────────────────────────────────────────

function MemoryTab({
  workspaceId, agentId, agent, onChat,
}: { workspaceId: string; agentId: string; agent: FleetAgent | null; onChat: () => void }) {
  const { files, loading } = useFleetAgentMemory(workspaceId, agentId);

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
        title="Nothing here yet"
        body="Sage will write memories here as it learns about this agent's preferences, facts, and context."
        action="Chat with this agent"
        onAction={onChat}
      />
    );
  }

  return (
    <div className="fleet-config">
      <div className="fleet-detail-section-title">
        {files.length} {files.length === 1 ? "file" : "files"}
      </div>
      {files.map((f) => (
        <div key={f.path} className="fleet-config-row">
          <span className="fleet-config-label">{f.path}</span>
          <span className="fleet-config-value" style={{ fontSize: 11 }}>
            {formatBytes(f.size)} · {new Date(f.modified).toLocaleDateString()}
          </span>
        </div>
      ))}
    </div>
  );
}

// ── Channels ────────────────────────────────────────────────────────────────

function ChannelsTab({
  workspaceId, agentId, agent,
}: { workspaceId: string; agentId: string; agent: FleetAgent | null }) {
  const { channels, hostedTelegramConfigured, loading } = useFleetAgentChannels(workspaceId, agentId);
  const [pairing, setPairing] = useState(false);
  const [pairingCode, setPairingCode] = useState<string | null>(null);
  const [deepLink, setDeepLink] = useState<string | null>(null);
  const [pairError, setPairError] = useState<string | null>(null);
  const [pairResult, setPairResult] = useState<string | null>(null);

  const startHostedPairing = useCallback(async () => {
    setPairing(true);
    setPairError(null);
    setPairResult(null);
    try {
      const res = await fetch("/api/sage/telegram-hosted/pair/start", {
        method: "POST",
        credentials: "include",
        headers: { "Content-Type": "application/json" },
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
      // Poll for completion
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
      setTimeout(() => clearInterval(check), 120_000); // 2 min timeout
    } catch (e) {
      setPairError(e instanceof Error ? e.message : "Could not start pairing. Try again.");
      setPairing(false);
    }
  }, [workspaceId]);

  const hostedConnected = channels.some((c) => c.id === "sage_telegram_hosted" && c.connected);

  if (loading) {
    return <div className="fleet-activity-skeleton" aria-label="Loading channels"><div className="fleet-skeleton-bar" style={{ width: "80%" }} /></div>;
  }

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
      {/* Bot channel card */}
      <div className="fleet-card" style={{ cursor: "default", padding: 20 }}>
        <div style={{ display: "flex", alignItems: "center", gap: 12, marginBottom: 12 }}>
          <Radio size={20} strokeWidth={1.75} />
          <span style={{ fontWeight: 500, fontSize: 14 }}>Bot channel</span>
          <span style={{ fontSize: 11, color: "var(--text-muted)" }}>No phone, no hardware</span>
        </div>
        <p style={{ fontSize: 13, color: "var(--text-secondary)", margin: "0 0 16px" }}>
          The hosted Empyralis bot replies through Telegram. No BotFather setup, no token, no Gateway. Send one code and you're done.
        </p>

        {pairResult ? (
          <div style={{ display: "flex", alignItems: "center", gap: 8, color: "var(--online-text)", fontSize: 13, fontWeight: 500 }}>
            <Check size={16} strokeWidth={2} /> {pairResult}
          </div>
        ) : pairingCode ? (
          <div>
            <div className="fleet-pair-code" style={{ padding: "8px 12px", border: "1px solid var(--border)", borderRadius: 8, display: "inline-flex", gap: 12, alignItems: "baseline", marginBottom: 8 }}>
              <span style={{ fontSize: 11, textTransform: "uppercase", color: "var(--text-muted)", letterSpacing: "0.04em" }}>Code</span>
              <code style={{ fontSize: 18, fontWeight: 500 }}>{pairingCode}</code>
            </div>
            {deepLink && (
              <a href={deepLink} target="_blank" rel="noopener noreferrer" className="fleet-btn fleet-btn--accent" style={{ marginLeft: 8 }}>
                Open Telegram <ExternalLink size={14} style={{ marginLeft: 4 }} />
              </a>
            )}
            <p style={{ fontSize: 12, color: "var(--text-muted)", marginTop: 8 }}>
              Send this code to the Empyralis bot on Telegram. This tab will update when paired.
            </p>
          </div>
        ) : hostedConnected ? (
          <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
            <span className="fleet-card-status-dot" style={{ background: "var(--online-dot)" }} />
            <span style={{ fontSize: 13, color: "var(--online-text)", fontWeight: 500 }}>Connected</span>
          </div>
        ) : (
          <div>
            <button type="button" className="fleet-btn fleet-btn--accent" onClick={startHostedPairing} disabled={pairing}>
              {pairing ? <><Loader2 size={14} style={{ animation: "spin 1s linear infinite" }} /> Starting…</> : "Pair Telegram"}
            </button>
            {!hostedTelegramConfigured && (
              <p style={{ fontSize: 12, color: "var(--offline-text)", marginTop: 8 }}>
                The hosted Telegram bot is not configured on this server.
              </p>
            )}
            {pairError && <p style={{ fontSize: 12, color: "var(--offline-text)", marginTop: 8 }}>{pairError}</p>}
          </div>
        )}
      </div>

      {/* Personal channel card */}
      <div className="fleet-card" style={{ cursor: "default", padding: 20 }}>
        <div style={{ display: "flex", alignItems: "center", gap: 12, marginBottom: 12 }}>
          <Cpu size={20} strokeWidth={1.75} />
          <span style={{ fontWeight: 500, fontSize: 14 }}>Personal channel</span>
          <span style={{ fontSize: 11, color: "var(--text-muted)" }}>Your account, needs Gateway</span>
        </div>
        <p style={{ fontSize: 13, color: "var(--text-secondary)", margin: "0 0 16px" }}>
          Connect your personal Telegram account via Agent Computer (Gateway). Messages come from your own number. Requires the Gateway desktop app paired and online.
        </p>
        <button
          type="button"
          className="fleet-btn"
          onClick={() => window.location.href = `/w/${encodeURIComponent(workspaceId)}/hardware`}
        >
          Open Hardware to pair Gateway first
        </button>
      </div>
    </div>
  );
}

// ── Connectors ──────────────────────────────────────────────────────────────

function ConnectorsTab({
  workspaceId, agentId, agent,
}: { workspaceId: string; agentId: string; agent: FleetAgent | null }) {
  const { connectors, loading } = useFleetAgentConnectors(workspaceId, agentId);

  if (loading) {
    return <div className="fleet-activity-skeleton" aria-label="Loading connectors"><div className="fleet-skeleton-bar" style={{ width: "70%" }} /></div>;
  }

  if (connectors.length === 0) {
    return (
      <EmptyState
        title="No connectors available"
        body="No MCP or OAuth connectors are registered for this workspace. Connect the first one from the workspace Connectors page."
        action="Open connectors"
        onAction={() => window.location.href = `/w/${encodeURIComponent(workspaceId)}/integrations`}
      />
    );
  }

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
      {connectors.map((c) => (
        <div key={c.id} className="fleet-card" style={{ cursor: "default", padding: "12px 16px", display: "flex", alignItems: "center", gap: 12 }}>
          <div style={{ flex: 1, minWidth: 0 }}>
            <div style={{ fontWeight: 500, fontSize: 13 }}>{c.label}</div>
            <div style={{ fontSize: 12, color: "var(--text-muted)" }}>{c.summary}</div>
          </div>
          {c.connected ? (
            <span style={{ display: "flex", alignItems: "center", gap: 4, color: "var(--online-text)", fontSize: 12, fontWeight: 500 }}>
              <span style={{ width: 8, height: 8, borderRadius: 999, background: "var(--online-dot)", flexShrink: 0 }} />
              Connected
            </span>
          ) : (
            <button
              type="button"
              className="fleet-btn fleet-btn--accent"
              onClick={() => window.location.href = `/w/${encodeURIComponent(workspaceId)}/integrations`}
            >
              Connect
            </button>
          )}
        </div>
      ))}
    </div>
  );
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

function ModelTab({ agent }: { agent: FleetAgent | null }) {
  const config = agent?.model_config || {};
  const items: [string, string][] = [
    ["Provider", config.provider || "Platform default"],
    ["Model", config.model || "Platform default"],
  ];

  return (
    <div className="fleet-config">
      {items.map(([label, value]) => (
        <div key={label} className="fleet-config-row">
          <span className="fleet-config-label">{label}</span>
          <span className="fleet-config-value">{String(value)}</span>
        </div>
      ))}
    </div>
  );
}

// ── Shared empty state ──────────────────────────────────────────────────────

function EmptyState({
  title,
  body,
  action,
  onAction,
}: {
  title: string;
  body: string;
  action?: string;
  onAction?: () => void;
}) {
  return (
    <div className="fleet-tab-state">
      <div className="fleet-tab-state-title">{title}</div>
      <div className="fleet-tab-state-body">{body}</div>
      {action && onAction && (
        <button
          type="button"
          className="fleet-btn fleet-btn--accent"
          onClick={onAction}
          style={{ marginTop: 16 }}
        >
          {action}
        </button>
      )}
    </div>
  );
}

// ── Helpers ─────────────────────────────────────────────────────────────────

function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}
