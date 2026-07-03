"use client";

import "./fleet.css";
import { useState } from "react";
import { useRouter } from "next/navigation";
import {
  Bot,
  MessageSquare,
  Zap,
  Circle,
  Clock,
  AlertCircle,
  ChevronRight,
  Activity,
  Wifi,
  WifiOff,
  Cloud,
  Server,
  Home as HomeIcon,
  Layers,
  Cpu,
  HardDrive,
  Brain,
  CreditCard,
  Radio,
} from "lucide-react";
import { useFleetAgents } from "./fleet-data";
import { FleetAgentDetail } from "./FleetAgentDetail";

const STATUS_DOT: Record<string, { color: string; label: string }> = {
  online: { color: "#22c55e", label: "Online" },
  offline: { color: "#ef4444", label: "Offline" },
  unknown: { color: "#94a3b8", label: "Unknown" },
};

const RUNTIME_ICON: Record<string, React.ReactNode> = {
  cloud: <Cloud size={12} />,
  gateway: <Radio size={12} />,
  vps: <Server size={12} />,
};

function runtimeIcon(target: string) {
  if (target.startsWith("gateway")) return RUNTIME_ICON.gateway;
  if (target.startsWith("vps")) return RUNTIME_ICON.vps;
  return RUNTIME_ICON.cloud;
}

const PRIMARY_RAIL_ITEMS = [
  { id: "home", label: "Home", icon: <HomeIcon size={18} />, route: "fleet" },
  { id: "agents", label: "Agents", icon: <Bot size={18} />, route: "agents" },
  { id: "channels", label: "Channels", icon: <Radio size={18} />, route: "channels" },
  { id: "connectors", label: "Connectors", icon: <Layers size={18} />, route: "integrations" },
  { id: "hardware", label: "Hardware", icon: <Cpu size={18} />, route: "hardware" },
  { id: "memory", label: "Memory", icon: <Brain size={18} />, route: "memory" },
  { id: "billing", label: "Billing", icon: <CreditCard size={18} />, route: "settings" },
];

export function FleetHome({ workspaceId }: { workspaceId: string }) {
  const { agents, loading, error } = useFleetAgents(workspaceId);
  const [selectedAgentId, setSelectedAgentId] = useState<string | null>(null);
  const [activeRailItem, setActiveRailItem] = useState("home");
  const router = useRouter();

  const sageAgent = agents.find((a) => a.role === "sage" || a.label?.toLowerCase().includes("sage"));
  const otherAgents = agents.filter((a) => a !== sageAgent);

  // ── Empty State ──
  if (!loading && agents.length === 0 && !error) {
    return (
      <div className="fleet-layout">
        <PrimaryRail active={activeRailItem} onSelect={setActiveRailItem} workspaceId={workspaceId} />
        <div className="fleet-content fleet-empty">
          <div className="fleet-empty-state">
            <Bot size={48} strokeWidth={1} />
            <h2>No agents yet</h2>
            <p>Ask Sage to create your first one</p>
            <button
              className="fleet-btn fleet-btn-primary"
              onClick={() => router.push(`/w/${workspaceId}/chat`)}
            >
              <MessageSquare size={16} />
              Open Sage Chat
            </button>
          </div>
        </div>
      </div>
    );
  }

  // ── Error State ──
  if (error && agents.length === 0) {
    return (
      <div className="fleet-layout">
        <PrimaryRail active={activeRailItem} onSelect={setActiveRailItem} workspaceId={workspaceId} />
        <div className="fleet-content fleet-empty">
          <div className="fleet-empty-state">
            <AlertCircle size={48} strokeWidth={1} />
            <h2>Could not load agents</h2>
            <p>{error}</p>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="fleet-layout">
      {/* ── Primary Rail (persistent, never swaps) ── */}
      <PrimaryRail active={activeRailItem} onSelect={setActiveRailItem} workspaceId={workspaceId} />

      {/* ── Content: Agent Cards ── */}
      <div className="fleet-content">
        <div className="fleet-header">
          <h1 className="fleet-title">Fleet</h1>
          <span className="fleet-count">{agents.length} agent{agents.length !== 1 ? "s" : ""}</span>
        </div>

        <div className="fleet-grid">
          {/* Sage pinned on top */}
          {sageAgent && (
            <div className="fleet-sage-section">
              <div className="fleet-section-label">Operator</div>
              <AgentCard
                agent={sageAgent}
                isSage
                onClick={() => setSelectedAgentId(sageAgent.agent_id)}
                onChat={() => router.push(`/w/${workspaceId}/chat`)}
              />
            </div>
          )}

          {/* Other agents */}
          {otherAgents.length > 0 && (
            <div className="fleet-agents-section">
              <div className="fleet-section-label">
                {sageAgent ? "Agents" : "All Agents"}
              </div>
              <div className="fleet-grid-cards">
                {otherAgents.map((agent) => (
                  <AgentCard
                    key={agent.agent_id}
                    agent={agent}
                    onClick={() => setSelectedAgentId(agent.agent_id)}
                  />
                ))}
              </div>
            </div>
          )}
        </div>
      </div>

      {/* ── Detail Panel ── */}
      {selectedAgentId && (
        <FleetAgentDetail
          workspaceId={workspaceId}
          agentId={selectedAgentId}
          agent={agents.find((a) => a.agent_id === selectedAgentId) || null}
          onClose={() => setSelectedAgentId(null)}
        />
      )}
    </div>
  );
}

function PrimaryRail({
  active,
  onSelect,
  workspaceId,
}: {
  active: string;
  onSelect: (id: string) => void;
  workspaceId: string;
}) {
  const router = useRouter();
  return (
    <nav className="fleet-rail">
      <div className="fleet-rail-brand">
        <Zap size={20} />
      </div>
      <div className="fleet-rail-items">
        {PRIMARY_RAIL_ITEMS.map((item) => (
          <button
            key={item.id}
            className={`fleet-rail-item ${active === item.id ? "fleet-rail-item--active" : ""}`}
            onClick={() => {
              onSelect(item.id);
              const route = item.route || item.id;
              if (route === "fleet") {
                router.push(`/w/${encodeURIComponent(workspaceId)}/fleet`);
              } else {
                router.push(`/w/${encodeURIComponent(workspaceId)}/${route}`);
              }
            }}
            title={item.label}
          >
            {item.icon}
          </button>
        ))}
      </div>
    </nav>
  );
}

function AgentCard({
  agent,
  isSage,
  onClick,
  onChat,
}: {
  agent: any;
  isSage?: boolean;
  onClick: () => void;
  onChat?: () => void;
}) {
  const dot = STATUS_DOT[agent.hardware_status] || STATUS_DOT.unknown;
  const isOffline = agent.hardware_status === "offline";

  return (
    <div
      className={`fleet-card ${isSage ? "fleet-card--sage" : ""} ${isOffline ? "fleet-card--offline" : ""}`}
      onClick={onClick}
    >
      <div className="fleet-card-top">
        <div className="fleet-card-avatar">
          <Bot size={20} />
          <span
            className="fleet-card-dot"
            style={{ background: dot.color }}
            title={dot.label}
          />
        </div>
        <div className="fleet-card-info">
          <div className="fleet-card-name">
            {agent.label || "Unnamed Agent"}
            {isSage && <span className="fleet-card-badge">Sage</span>}
          </div>
          <div className="fleet-card-meta">
            <span className="fleet-card-target">
              {runtimeIcon(agent.runtime_target)}
              <span>{agent.runtime_target || "unknown"}</span>
            </span>
            {agent.last_heartbeat && (
              <span className="fleet-card-heartbeat">
                <Clock size={10} />
                <span>{new Date(agent.last_heartbeat).toLocaleTimeString()}</span>
              </span>
            )}
          </div>
        </div>
        <ChevronRight size={16} className="fleet-card-chevron" />
      </div>

      {agent.last_activity && (
        <div className="fleet-card-activity">
          <Activity size={12} />
          <span>{agent.last_activity}</span>
        </div>
      )}

      {isOffline && (
        <div className="fleet-card-offline-banner">
          <WifiOff size={12} />
          <span>Offline — last seen {agent.last_heartbeat ? new Date(agent.last_heartbeat).toLocaleString() : "unknown"}</span>
        </div>
      )}

      {isSage && onChat && (
        <button
          className="fleet-card-chat-btn"
          onClick={(e) => {
            e.stopPropagation();
            onChat();
          }}
        >
          <MessageSquare size={14} />
          Chat with Sage
        </button>
      )}
    </div>
  );
}
