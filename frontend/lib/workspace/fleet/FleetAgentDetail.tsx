"use client";

import { useState } from "react";
import {
  X,
  Activity,
  Radio,
  Wrench,
  Brain,
  Cpu,
  Clock,
  Circle,
  AlertCircle,
  Loader2,
} from "lucide-react";
import { useFleetAgentActivity } from "./fleet-data";

const TABS = [
  { id: "activity", label: "Activity", icon: <Activity size={14} /> },
  { id: "channels", label: "Channels", icon: <Radio size={14} /> },
  { id: "tools", label: "Tools", icon: <Wrench size={14} /> },
  { id: "memory", label: "Memory", icon: <Brain size={14} /> },
  { id: "model", label: "Model", icon: <Cpu size={14} /> },
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
  agent: any;
  onClose: () => void;
}) {
  const [activeTab, setActiveTab] = useState<TabId>("activity");
  const { events, loading } = useFleetAgentActivity(workspaceId, agentId);

  const hwStatus: string = agent?.hardware_status || "unknown";
  const dotMap: Record<string, { color: string; label: string }> = {
    online: { color: "#22c55e", label: "Online" },
    offline: { color: "#ef4444", label: "Offline" },
    unknown: { color: "#94a3b8", label: "Unknown" },
  };
  const dot = dotMap[hwStatus] || dotMap.unknown;

  return (
    <div className="fleet-detail">
      {/* Header */}
      <div className="fleet-detail-header">
        <div className="fleet-detail-title-row">
          <div className="fleet-detail-avatar">
            <div className="fleet-detail-avatar-icon">
              <Activity size={20} />
            </div>
            <span
              className="fleet-detail-dot"
              style={{ background: dot?.color }}
            />
          </div>
          <div className="fleet-detail-info">
            <h2>{agent?.label || "Agent"}</h2>
            <div className="fleet-detail-meta">
              <span className="fleet-detail-status">
                <Circle size={8} fill={dot?.color} color={dot?.color} />
                {dot?.label}
              </span>
              <span>{agent?.runtime_target || "unknown placement"}</span>
              {agent?.last_heartbeat && (
                <span className="fleet-detail-hb">
                  <Clock size={10} />
                  Last seen: {new Date(agent.last_heartbeat).toLocaleString()}
                </span>
              )}
            </div>
          </div>
          <button className="fleet-detail-close" onClick={onClose}>
            <X size={18} />
          </button>
        </div>
      </div>

      {/* Tabs */}
      <div className="fleet-detail-tabs">
        {TABS.map((tab) => (
          <button
            key={tab.id}
            className={`fleet-detail-tab ${activeTab === tab.id ? "fleet-detail-tab--active" : ""}`}
            onClick={() => setActiveTab(tab.id)}
          >
            {tab.icon}
            {tab.label}
          </button>
        ))}
      </div>

      {/* Tab Content */}
      <div className="fleet-detail-body">
        {activeTab === "activity" && (
          <ActivityTab events={events} loading={loading} />
        )}
        {activeTab === "channels" && (
          <PlaceholderTab
            icon={<Radio size={32} />}
            title="Channels"
            body="Channel configuration and pairing status will appear here."
          />
        )}
        {activeTab === "tools" && (
          <PlaceholderTab
            icon={<Wrench size={32} />}
            title="Tools"
            body="Tool catalog and capability manifest will appear here."
          />
        )}
        {activeTab === "memory" && (
          <PlaceholderTab
            icon={<Brain size={32} />}
            title="Memory"
            body="Memory store contents and retrieval index will appear here."
          />
        )}
        {activeTab === "model" && (
          <ModelTab agent={agent} />
        )}
      </div>
    </div>
  );
}

function ActivityTab({
  events,
  loading,
}: {
  events: any[];
  loading: boolean;
}) {
  if (loading) {
    return (
      <div className="fleet-tab-loading">
        <Loader2 size={20} className="fleet-spinner" />
        <span>Loading activity ledger...</span>
      </div>
    );
  }

  if (events.length === 0) {
    return (
      <div className="fleet-tab-empty">
        <Activity size={32} strokeWidth={1} />
        <p>No activity recorded yet</p>
        <span>Events will appear here after the agent processes a turn.</span>
      </div>
    );
  }

  return (
    <div className="fleet-activity-list">
      {events.map((event) => (
        <div key={event.event_id} className="fleet-activity-item">
          <div className="fleet-activity-marker">
            <div
              className={`fleet-activity-dot ${
                event.status === "logged" ? "fleet-activity-dot--ok" : "fleet-activity-dot--warn"
              }`}
            />
          </div>
          <div className="fleet-activity-body">
            <div className="fleet-activity-title">{event.title}</div>
            <div className="fleet-activity-meta">
              <span className="fleet-activity-class">{event.event_class}</span>
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

function ModelTab({ agent }: { agent: any }) {
  const config = agent?.model_config || {};
  return (
    <div className="fleet-tab-config">
      <div className="fleet-config-item">
        <span className="fleet-config-label">Provider</span>
        <span className="fleet-config-value">
          {config.provider || "Default"}
        </span>
      </div>
      <div className="fleet-config-item">
        <span className="fleet-config-label">Model</span>
        <span className="fleet-config-value">
          {config.model || "Default"}
        </span>
      </div>
      <div className="fleet-config-item">
        <span className="fleet-config-label">Role</span>
        <span className="fleet-config-value">
          {agent?.role || "agent"}
        </span>
      </div>
      <div className="fleet-config-item">
        <span className="fleet-config-label">Status</span>
        <span className="fleet-config-value">
          {agent?.status || "active"}
        </span>
      </div>
    </div>
  );
}

function PlaceholderTab({
  icon,
  title,
  body,
}: {
  icon: React.ReactNode;
  title: string;
  body: string;
}) {
  return (
    <div className="fleet-tab-empty">
      {icon}
      <p>{title}</p>
      <span>{body}</span>
    </div>
  );
}
