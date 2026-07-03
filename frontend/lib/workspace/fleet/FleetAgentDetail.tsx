"use client";

import { useState } from "react";
import { X, Loader2 } from "lucide-react";
import { useFleetAgentActivity, type FleetAgent } from "./fleet-data";

// ── Design tokens ──
const C = {
  panelBg: "#161618",
  border: "rgba(255,255,255,0.08)",
  textPrimary: "#f4f4f5",
  textSecondary: "#a1a1aa",
  textMuted: "#71717a",
  accent: "#7c3aed",
  online: "#1D9E75",
  offline: "#E24B4A",
  onlineText: "#5DCAA5",
  offlineText: "#F09595",
  cardBg: "#1c1c1f",
};

const TABS = [
  { id: "activity" as const, label: "Activity" },
  { id: "channels" as const, label: "Channels" },
  { id: "tools" as const, label: "Tools" },
  { id: "memory" as const, label: "Memory" },
  { id: "model" as const, label: "Model" },
];

type TabId = (typeof TABS)[number]["id"];

const HW_STYLES: Record<string, { color: string; label: string }> = {
  online: { color: C.online, label: "Online" },
  offline: { color: C.offline, label: "Offline" },
  unknown: { color: C.textMuted, label: "Unknown" },
};

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

  const hwStatus = agent?.hardware_status || "unknown";
  const dot = HW_STYLES[hwStatus] || HW_STYLES.unknown;

  return (
    <aside
      style={{
        width: 380,
        minWidth: 380,
        borderLeft: `0.5px solid ${C.border}`,
        background: C.panelBg,
        display: "flex",
        flexDirection: "column",
        overflow: "hidden",
        fontFamily: "var(--font-dm-sans, system-ui)",
      }}
    >
      {/* Header */}
      <div style={{ padding: 20, borderBottom: `0.5px solid ${C.border}` }}>
        <div style={{ display: "flex", alignItems: "flex-start", gap: 12 }}>
          <div style={{ position: "relative", width: 40, height: 40, flexShrink: 0 }}>
            <div
              style={{
                width: 40,
                height: 40,
                display: "flex",
                alignItems: "center",
                justifyContent: "center",
                background: "rgba(124,58,237,0.08)",
                color: C.accent,
                borderRadius: 10,
                fontSize: 18,
                fontWeight: 600,
              }}
            >
              {(agent?.label || "A").charAt(0).toUpperCase()}
            </div>
            <span
              style={{
                position: "absolute",
                bottom: -2,
                right: -2,
                width: 12,
                height: 12,
                borderRadius: "50%",
                border: `2px solid ${C.panelBg}`,
                background: dot.color,
              }}
            />
          </div>
          <div style={{ flex: 1, minWidth: 0 }}>
            <h2 style={{ fontSize: 16, fontWeight: 600, margin: "0 0 4px", color: C.textPrimary }}>
              {agent?.label || "Agent"}
            </h2>
            <div style={{ fontSize: 12, color: C.textMuted, display: "flex", flexDirection: "column", gap: 2 }}>
              <span style={{ display: "inline-flex", alignItems: "center", gap: 4 }}>
                <span
                  style={{
                    width: 8,
                    height: 8,
                    borderRadius: "50%",
                    background: dot.color,
                    display: "inline-block",
                  }}
                />
                {dot.label}
              </span>
              <span>
                {(agent as any)?.runtime_target || "unknown placement"}
              </span>
            </div>
          </div>
          <button
            onClick={onClose}
            style={{
              background: "none",
              border: "none",
              color: C.textMuted,
              cursor: "pointer",
              padding: 4,
              borderRadius: 6,
              display: "flex",
            }}
          >
            <X size={18} />
          </button>
        </div>
      </div>

      {/* Tabs */}
      <div
        style={{
          display: "flex",
          borderBottom: `0.5px solid ${C.border}`,
          padding: "0 12px",
          gap: 2,
        }}
      >
        {TABS.map((tab) => (
          <button
            key={tab.id}
            onClick={() => setActiveTab(tab.id)}
            style={{
              padding: "10px 14px",
              border: "none",
              background: "transparent",
              color: activeTab === tab.id ? C.accent : C.textMuted,
              fontSize: 12,
              fontWeight: 500,
              cursor: "pointer",
              borderBottom: activeTab === tab.id ? `2px solid ${C.accent}` : "2px solid transparent",
              transition: "all 0.15s",
              fontFamily: "inherit",
            }}
          >
            {tab.label}
          </button>
        ))}
      </div>

      {/* Tab content */}
      <div style={{ flex: 1, overflowY: "auto" }}>
        {activeTab === "activity" && (
          <ActivityTab events={events} loading={loading} />
        )}
        {activeTab === "channels" && (
          <PlaceholderTab title="Channels" body="Channel configuration and pairing status will appear here." />
        )}
        {activeTab === "tools" && (
          <PlaceholderTab title="Tools" body="Tool catalog and capability manifest will appear here." />
        )}
        {activeTab === "memory" && (
          <PlaceholderTab title="Memory" body="Memory store contents and retrieval index will appear here." />
        )}
        {activeTab === "model" && (
          <ModelTab agent={agent} />
        )}
      </div>
    </aside>
  );
}

// ── Activity tab ──────────────────────────────────────────────────────────

function ActivityTab({
  events,
  loading,
}: {
  events: any[];
  loading: boolean;
}) {
  if (loading) {
    return (
      <div
        style={{
          display: "flex",
          flexDirection: "column",
          alignItems: "center",
          justifyContent: "center",
          padding: "48px 24px",
          gap: 12,
          color: C.textMuted,
          fontSize: 13,
        }}
      >
        <Loader2 size={20} style={{ animation: "spin 0.8s linear infinite" }} />
        Loading activity ledger…
      </div>
    );
  }

  if (events.length === 0) {
    return (
      <div
        style={{
          display: "flex",
          flexDirection: "column",
          alignItems: "center",
          justifyContent: "center",
          padding: "48px 24px",
          textAlign: "center" as const,
          color: C.textMuted,
        }}
      >
        <div style={{ fontSize: 14, fontWeight: 500, color: C.textPrimary, marginBottom: 4 }}>
          No activity recorded yet
        </div>
        <div style={{ fontSize: 12 }}>Events will appear here after the agent processes a turn.</div>
      </div>
    );
  }

  return (
    <div style={{ padding: "8px 0" }}>
      {events.map((event) => (
        <div
          key={event.event_id}
          style={{
            display: "flex",
            gap: 12,
            padding: "10px 20px",
            borderBottom: `0.5px solid ${C.border}`,
          }}
        >
          <div style={{ paddingTop: 4 }}>
            <div
              style={{
                width: 8,
                height: 8,
                borderRadius: "50%",
                background: event.status === "logged" ? C.online : C.offline,
              }}
            />
          </div>
          <div style={{ flex: 1, minWidth: 0 }}>
            <div style={{ fontSize: 13, fontWeight: 500, color: C.textPrimary, marginBottom: 2 }}>
              {event.title}
            </div>
            <div
              style={{
                fontSize: 11,
                color: C.textMuted,
                display: "flex",
                gap: 6,
                alignItems: "center",
              }}
            >
              <span>{event.event_class}</span>
              <span>·</span>
              <span>{event.action}</span>
              <span>·</span>
              <span>{new Date(event.created_at).toLocaleString()}</span>
            </div>
          </div>
        </div>
      ))}
    </div>
  );
}

// ── Model tab ─────────────────────────────────────────────────────────────

function ModelTab({ agent }: { agent: FleetAgent | null }) {
  const config = (agent as any)?.model_config || {};
  const items = [
    ["Provider", config.provider || "Default"],
    ["Model", config.model || "Default"],
    ["Role", (agent as any)?.role || "agent"],
    ["Status", (agent as any)?.status || "active"],
  ];

  return (
    <div style={{ padding: "16px 20px" }}>
      {items.map(([label, value]) => (
        <div
          key={label}
          style={{
            display: "flex",
            justifyContent: "space-between",
            alignItems: "center",
            padding: "10px 0",
            borderBottom: `0.5px solid ${C.border}`,
          }}
        >
          <span style={{ fontSize: 13, color: C.textMuted }}>{label}</span>
          <span style={{ fontSize: 13, fontWeight: 500, color: C.textPrimary }}>
            {String(value)}
          </span>
        </div>
      ))}
    </div>
  );
}

// ── Placeholder tab ───────────────────────────────────────────────────────

function PlaceholderTab({ title, body }: { title: string; body: string }) {
  return (
    <div
      style={{
        display: "flex",
        flexDirection: "column",
        alignItems: "center",
        justifyContent: "center",
        padding: "48px 24px",
        textAlign: "center" as const,
      }}
    >
      <div style={{ fontSize: 14, fontWeight: 500, color: C.textPrimary, marginBottom: 4 }}>
        {title}
      </div>
      <div style={{ fontSize: 12, color: C.textMuted }}>{body}</div>
    </div>
  );
}
