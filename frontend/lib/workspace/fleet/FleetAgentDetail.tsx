"use client";

import { useEffect, useState } from "react";
import {
  Brain,
  Cpu,
  LayoutGrid,
  MessageSquare,
  Plug,
  Radio,
  Wrench,
  X,
  type LucideIcon,
} from "lucide-react";

import { useFleetAgentActivity, type FleetAgent } from "./fleet-data";
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
 * Agent detail — centered modal over a dimmed backdrop (~80vw), with an
 * internal left nav rather than top tabs (ChatGPT-settings pattern).
 * Esc or backdrop click closes.
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
  const { events, loading } = useFleetAgentActivity(workspaceId, agentId);

  const status = deriveStatus(agent?.hardware_status || "unknown");
  const dotClass = statusClass(status.tone);
  const deployed = status.tone !== "unknown";
  const placement = derivePlacement(agent?.runtime_target || "unknown", deployed);

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
                placement={placement}
                statusLabel={status.label}
                events={events}
                loading={loading}
              />
            )}
            {activeTab === "chat" && <ChatTab agent={agent} onChat={() => onChat(agentId)} />}
            {activeTab === "memory" && (
              <ComingSoon title="Memory" body="This agent's stored memories and retrieval index will live here." />
            )}
            {activeTab === "channels" && (
              <ComingSoon title="Channels" body="This agent's channel pairing and delivery status will live here." />
            )}
            {activeTab === "connectors" && (
              <ComingSoon title="Connectors" body="This agent's connected tools and services will live here." />
            )}
            {activeTab === "tools" && (
              <ComingSoon title="Tools" body="This agent's tool catalog and capability manifest will live here." />
            )}
            {activeTab === "model" && <ModelTab agent={agent} />}
          </div>
        </div>
      </div>
    </div>
  );
}

// ── Overview tab (status/placement/role + recent activity) ────────────────

function OverviewTab({
  agent,
  placement,
  statusLabel,
  events,
  loading,
}: {
  agent: FleetAgent | null;
  placement: string;
  statusLabel: string;
  events: any[];
  loading: boolean;
}) {
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
      <ActivityList events={events} loading={loading} />
    </div>
  );
}

// ── Chat tab (real navigation, not a stub) ─────────────────────────────────

function ChatTab({ agent, onChat }: { agent: FleetAgent | null; onChat: () => void }) {
  return (
    <div className="fleet-tab-state">
      <div className="fleet-tab-state-title">Chat with {agent?.label || "this agent"}</div>
      <div className="fleet-tab-state-body">
        Open the shared conversation thread and talk to this agent directly.
      </div>
      <button
        type="button"
        className="fleet-btn fleet-btn--accent"
        onClick={onChat}
        style={{ marginTop: 16 }}
      >
        Open chat
      </button>
    </div>
  );
}

// ── Activity list (feeds Overview) ──────────────────────────────────────

function ActivityList({ events, loading }: { events: any[]; loading: boolean }) {
  if (loading) {
    return (
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
    );
  }

  if (events.length === 0) {
    return (
      <div className="fleet-tab-state">
        <div className="fleet-tab-state-title">No activity yet</div>
        <div className="fleet-tab-state-body">
          Events appear here after the agent processes its first turn.
        </div>
      </div>
    );
  }

  return (
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
  );
}

// ── Model tab ───────────────────────────────────────────────────────────────

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

// ── Coming soon ─────────────────────────────────────────────────────────────

function ComingSoon({ title, body }: { title: string; body: string }) {
  return (
    <div className="fleet-tab-state">
      <div className="fleet-tab-state-title">
        {title}
        <span className="fleet-coming-soon">Soon</span>
      </div>
      <div className="fleet-tab-state-body">{body}</div>
    </div>
  );
}
