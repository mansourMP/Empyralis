"use client";

import { useEffect, useState } from "react";
import { X } from "lucide-react";

import { useFleetAgentActivity, type FleetAgent } from "./fleet-data";
import { deriveStatus, statusClass } from "./fleet-presentation";

const TABS = [
  { id: "activity", label: "Activity" },
  { id: "channels", label: "Channels" },
  { id: "tools", label: "Tools" },
  { id: "memory", label: "Memory" },
  { id: "model", label: "Model" },
] as const;

type TabId = (typeof TABS)[number]["id"];

export function FleetAgentDetail({
  workspaceId,
  agentId,
  agent,
  onClose,
}: {
  workspaceId: string;
  agentId: string;
  agent: FleetAgent | null;
  onClose: () => void;
}) {
  const [activeTab, setActiveTab] = useState<TabId>("activity");
  const { events, loading } = useFleetAgentActivity(workspaceId, agentId);

  const status = deriveStatus(agent?.hardware_status || "unknown");
  const dotClass = statusClass(status.tone);
  const deployed = status.tone !== "unknown";
  const placement =
    deployed && agent?.runtime_target && agent.runtime_target !== "unknown"
      ? agent.runtime_target
      : null;

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  return (
    <>
      <div className="fleet-detail-backdrop" onClick={onClose} />
      <aside className="fleet-detail" role="dialog" aria-label={`${agent?.label || "Agent"} details`}>
        {/* Header */}
        <div className="fleet-detail-header">
          <div className="fleet-detail-avatar">
            <div className="fleet-detail-avatar-icon">
              {(agent?.label || "A").charAt(0).toUpperCase()}
            </div>
            <span className={`fleet-detail-dot ${dotClass}`} />
          </div>
          <div className="fleet-detail-info">
            <div className="fleet-detail-name">{agent?.label || "Agent"}</div>
            <div className="fleet-detail-meta">
              <span className={`fleet-detail-meta-status ${dotClass}`}>{status.label}</span>
              {placement && <span>{placement}</span>}
            </div>
          </div>
          <button type="button" className="fleet-detail-close" onClick={onClose} aria-label="Close">
            <X size={18} strokeWidth={1.75} />
          </button>
        </div>

        {/* Tabs */}
        <div className="fleet-detail-tabs">
          {TABS.map((tab) => (
            <button
              key={tab.id}
              type="button"
              className={`fleet-detail-tab${activeTab === tab.id ? " fleet-detail-tab--active" : ""}`}
              onClick={() => setActiveTab(tab.id)}
            >
              {tab.label}
            </button>
          ))}
        </div>

        {/* Body */}
        <div className="fleet-detail-body">
          {activeTab === "activity" && <ActivityTab events={events} loading={loading} />}
          {activeTab === "channels" && (
            <ComingSoon title="Channels" body="Channel pairing and delivery status will live here." />
          )}
          {activeTab === "tools" && (
            <ComingSoon title="Tools" body="The agent's tool catalog and capability manifest will live here." />
          )}
          {activeTab === "memory" && (
            <ComingSoon title="Memory" body="Stored memories and the retrieval index will live here." />
          )}
          {activeTab === "model" && <ModelTab agent={agent} />}
        </div>
      </aside>
    </>
  );
}

// ── Activity tab ────────────────────────────────────────────────────────────

function ActivityTab({ events, loading }: { events: any[]; loading: boolean }) {
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
    ["Role", agent?.role || "agent"],
    ["Status", agent?.status || "active"],
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
