"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { useFleetAgents, type FleetAgent } from "./fleet-data";
import { FleetAgentDetail } from "./FleetAgentDetail";

// ── Design tokens (matching FleetHome.reference.tsx) ──
const C = {
  pageBg: "#0d0d0f",
  cardBg: "#1c1c1f",
  cardBorder: "rgba(255,255,255,0.08)",
  textPrimary: "#f4f4f5",
  textSecondary: "#a1a1aa",
  textMuted: "#71717a",
  accent: "#7c3aed",
  online: "#1D9E75",
  onlineText: "#5DCAA5",
  offline: "#E24B4A",
  offlineText: "#F09595",
};

const TINTS: Record<string, { bg: string; fg: string }> = {
  blue: { bg: "#0C447C22", fg: "#85B7EB" },
  purple: { bg: "#3C348922", fg: "#AFA9EC" },
  amber: { bg: "#854F0B22", fg: "#EF9F27" },
  teal: { bg: "#0F6E5622", fg: "#5DCAA5" },
  coral: { bg: "#993C1D22", fg: "#F0997B" },
};

// Map agent role → tint color
function tintForRole(role: string) {
  const r = (role || "").toLowerCase();
  if (r === "sage" || r === "operator") return "purple";
  if (r === "specialist") return "blue";
  if (r === "customer_facing") return "teal";
  return "blue";
}

// Map agent role → canonical preset
function presetForRole(role: string): "customer_facing" | "internal_assistant" | "operator" {
  const r = (role || "").toLowerCase();
  if (r === "sage" || r === "operator") return "operator";
  if (r === "customer_facing") return "customer_facing";
  return "internal_assistant";
}

// Map API agent → AgentSummary
function toAgentSummary(a: FleetAgent) {
  return {
    id: a.agent_id,
    name: a.label || "Unnamed Agent",
    preset: presetForRole(a.role),
    runtime_target: a.runtime_target || "unknown",
    hardware_status: a.hardware_status || "unknown",
    last_activity: a.last_activity || null,
    tint: tintForRole(a.role),
    role: a.role,
  };
}

// ── Fleet Home (landing page) ──────────────────────────────────────────────

export function FleetHome({ workspaceId }: { workspaceId: string }) {
  const { agents, loading, error } = useFleetAgents(workspaceId);
  const [selectedAgentId, setSelectedAgentId] = useState<string | null>(null);
  const router = useRouter();

  const mapped = agents.map(toAgentSummary);
  const sageAgent = mapped.find((a) => a.role === "sage" || a.role === "operator" || a.name.toLowerCase().includes("sage"));
  const otherAgents = mapped.filter((a) => a !== sageAgent);
  const onlineCount = mapped.filter((a) => a.hardware_status === "online").length;

  // ── Loading ──
  if (loading && agents.length === 0) {
    return (
      <main style={{ flex: 1, padding: "4rem 2rem", minWidth: 0, background: C.pageBg, color: C.textMuted, display: "flex", alignItems: "center", justifyContent: "center", fontFamily: "var(--font-dm-sans, system-ui)" }}>
        Loading fleet…
      </main>
    );
  }

  // ── Error ──
  if (error && agents.length === 0) {
    return (
      <main style={{ flex: 1, padding: "4rem 2rem", minWidth: 0, background: C.pageBg, color: C.offlineText, display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center", gap: 12, fontFamily: "var(--font-dm-sans, system-ui)" }}>
        <div style={{ fontSize: 17, fontWeight: 500, color: C.textPrimary }}>Could not load agents</div>
        <div style={{ fontSize: 14 }}>{error}</div>
      </main>
    );
  }

  return (
    <>
      {/* ── Main content ── */}
      <main
        style={{
          flex: 1,
          padding: "1.75rem 2rem",
          minWidth: 0,
          background: C.pageBg,
          overflowY: "auto",
          fontFamily: "var(--font-dm-sans, system-ui)",
        }}
      >
        {/* Header row */}
        <div
          style={{
            display: "flex",
            alignItems: "baseline",
            justifyContent: "space-between",
            marginBottom: "1.5rem",
          }}
        >
          <div>
            <h1 style={{ fontSize: 23, fontWeight: 500, margin: 0, color: C.textPrimary }}>
              Your fleet
            </h1>
            <p style={{ fontSize: 13, color: C.textMuted, margin: "5px 0 0" }}>
              {mapped.length} {mapped.length === 1 ? "agent" : "agents"} · {onlineCount} online
            </p>
          </div>
          <button
            onClick={() => router.push(`/w/${workspaceId}/chat`)}
            style={{
              display: "flex",
              alignItems: "center",
              gap: 7,
              fontSize: 13.5,
              padding: "8px 14px",
              borderRadius: 8,
              border: `0.5px solid ${C.cardBorder}`,
              background: "transparent",
              color: C.textPrimary,
              cursor: "pointer",
              fontFamily: "inherit",
            }}
          >
            <span style={{ fontSize: 16, lineHeight: 1 }}>+</span>
            New agent
          </button>
        </div>

        {/* Sage operator row */}
        {sageAgent && (
          <SageRow
            agent={sageAgent}
            onChat={() => router.push(`/w/${workspaceId}/chat`)}
          />
        )}

        {/* Agent cards grid or empty state */}
        {mapped.length === 0 && !loading ? (
          <EmptyFleet onChat={() => router.push(`/w/${workspaceId}/chat`)} />
        ) : (
          <div
            style={{
              display: "grid",
              gridTemplateColumns: "repeat(auto-fill, minmax(240px, 1fr))",
              gap: 12,
            }}
          >
            {/* Non-Sage agents */}
            {otherAgents.map((a) => (
              <AgentCard
                key={a.id}
                agent={a}
                onSelect={(id) => setSelectedAgentId(id)}
              />
            ))}
          </div>
        )}
      </main>

      {/* ── Detail panel ── */}
      {selectedAgentId && (
        <FleetAgentDetail
          workspaceId={workspaceId}
          agentId={selectedAgentId}
          agent={agents.find((a) => a.agent_id === selectedAgentId) || null}
          onClose={() => setSelectedAgentId(null)}
        />
      )}
    </>
  );
}

// ── Sage operator row ─────────────────────────────────────────────────────

function SageRow({
  agent,
  onChat,
}: {
  agent: ReturnType<typeof toAgentSummary>;
  onChat?: () => void;
}) {
  return (
    <div
      style={{
        display: "flex",
        alignItems: "center",
        gap: 14,
        background: C.cardBg,
        border: `0.5px solid ${C.cardBorder}`,
        borderRadius: 12,
        padding: "1rem 1.25rem",
        marginBottom: "1.25rem",
      }}
    >
      <div
        style={{
          width: 42,
          height: 42,
          borderRadius: 11,
          background: C.accent,
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          flexShrink: 0,
          fontSize: 20,
          color: "#fff",
        }}
      >
        ✦
      </div>
      <div style={{ flex: 1, minWidth: 0 }}>
        <div style={{ display: "flex", alignItems: "center", gap: 9 }}>
          <span style={{ fontSize: 15, fontWeight: 500, color: C.textPrimary }}>
            Sage
          </span>
          <span
            style={{
              fontSize: 11.5,
              color: C.textSecondary,
              background: "rgba(255,255,255,0.05)",
              border: `0.5px solid ${C.cardBorder}`,
              borderRadius: 20,
              padding: "2px 10px",
            }}
          >
            Operator
          </span>
        </div>
        <div style={{ fontSize: 13, color: C.textMuted, marginTop: 3 }}>
          Ask me to create or configure any agent for you.
        </div>
      </div>
      <button
        onClick={onChat}
        style={{
          display: "flex",
          alignItems: "center",
          gap: 7,
          fontSize: 13.5,
          flexShrink: 0,
          padding: "8px 14px",
          borderRadius: 8,
          border: `0.5px solid ${C.cardBorder}`,
          background: "transparent",
          color: C.textPrimary,
          cursor: "pointer",
          fontFamily: "inherit",
        }}
      >
        Chat with Sage
      </button>
    </div>
  );
}

// ── Agent card ────────────────────────────────────────────────────────────

function AgentCard({
  agent,
  onSelect,
}: {
  agent: ReturnType<typeof toAgentSummary>;
  onSelect?: (id: string) => void;
}) {
  const online = agent.hardware_status === "online";
  const offline = agent.hardware_status === "offline";
  const tint = TINTS[agent.tint] || TINTS.blue;

  const placementLabel = agent.runtime_target === "cloud"
    ? `cloud · ${agent.preset === "customer_facing" ? "customer-facing" : "internal"}`
    : agent.runtime_target.replace(":", " · ");

  return (
    <button
      onClick={() => onSelect?.(agent.id)}
      style={{
        textAlign: "left" as const,
        background: C.cardBg,
        border: `0.5px solid ${C.cardBorder}`,
        borderRadius: 12,
        padding: "1rem 1.15rem",
        cursor: "pointer",
        width: "100%",
        display: "flex",
        flexDirection: "column",
        fontFamily: "inherit",
        color: C.textPrimary,
      }}
    >
      {/* Top row: icon + name + status dot */}
      <div
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          marginBottom: 12,
        }}
      >
        <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
          <div
            style={{
              width: 32,
              height: 32,
              borderRadius: 8,
              background: tint.bg,
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              fontSize: 17,
              color: tint.fg,
              flexShrink: 0,
            }}
          >
            {agent.name.charAt(0).toUpperCase()}
          </div>
          <span style={{ fontSize: 14.5, fontWeight: 500, color: C.textPrimary }}>
            {agent.name}
          </span>
        </div>
        <span
          style={{
            display: "inline-flex",
            alignItems: "center",
            gap: 6,
            fontSize: 11.5,
            color: online
              ? C.onlineText
              : offline
                ? C.offlineText
                : C.textMuted,
          }}
        >
          <span
            style={{
              width: 7,
              height: 7,
              borderRadius: "50%",
              background: online
                ? C.online
                : offline
                  ? C.offline
                  : C.textMuted,
              flexShrink: 0,
            }}
          />
          {agent.hardware_status}
        </span>
      </div>

      {/* Hardware placement */}
      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: 7,
          fontSize: 12.5,
          color: C.textSecondary,
          marginBottom: 8,
        }}
      >
        {placementLabel}
      </div>

      {/* Last activity footer */}
      <div
        style={{
          fontSize: 12.5,
          color: offline ? C.offlineText : C.textMuted,
          borderTop: `0.5px solid ${C.cardBorder}`,
          paddingTop: 9,
          marginTop: 4,
        }}
      >
        {offline
          ? "Not reachable · check the connection"
          : agent.last_activity ?? "No activity yet"}
      </div>
    </button>
  );
}

// ── Empty state ───────────────────────────────────────────────────────────

function EmptyFleet({ onChat }: { onChat?: () => void }) {
  return (
    <div
      style={{
        textAlign: "center",
        padding: "4rem 1rem",
        color: C.textSecondary,
        fontFamily: "var(--font-dm-sans, system-ui)",
      }}
    >
      <div
        style={{
          fontSize: 17,
          fontWeight: 500,
          color: C.textPrimary,
          marginBottom: 8,
        }}
      >
        Start your first agent
      </div>
      <div style={{ fontSize: 14, color: C.textMuted, marginBottom: 20 }}>
        Tell Sage what you need and it&apos;ll set one up for you.
      </div>
      <button
        onClick={onChat}
        style={{
          display: "inline-flex",
          alignItems: "center",
          gap: 7,
          fontSize: 14,
          padding: "9px 16px",
          borderRadius: 8,
          border: "none",
          background: C.accent,
          color: "#fff",
          cursor: "pointer",
          fontFamily: "inherit",
        }}
      >
        Chat with Sage
      </button>
    </div>
  );
}
