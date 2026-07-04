"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";

import { useFleetAgents } from "./fleet-data";
import { FleetAgentDetail } from "./FleetAgentDetail";
import { FleetCard } from "./FleetCard";
import { TelegramPairPanel } from "./TelegramPairPanel";
import { isSageAgent, toAgentSummary } from "./fleet-presentation";

export function FleetHome({ workspaceId }: { workspaceId: string }) {
  const { agents, loading, error } = useFleetAgents(workspaceId);
  const [selectedAgentId, setSelectedAgentId] = useState<string | null>(null);
  const router = useRouter();

  const openChat = () => router.push(`/w/${encodeURIComponent(workspaceId)}/chat`);

  // ── Loading ──
  if (loading && agents.length === 0) {
    return (
      <main className="fleet-page-state">
        <div className="fleet-page-state-body">Loading fleet…</div>
      </main>
    );
  }

  // ── Error ──
  if (error && agents.length === 0) {
    return (
      <main className="fleet-page-state">
        <div className="fleet-page-state-title">Could not load agents</div>
        <div className="fleet-page-state-body">{error}</div>
      </main>
    );
  }

  const mapped = agents.map((a, i) => toAgentSummary(a, i));
  const sageAgent = mapped.find(isSageAgent);
  const otherAgents = mapped.filter((a) => a !== sageAgent);
  const onlineCount = mapped.filter((a) => a.hardwareStatus === "online").length;

  return (
    <>
      <main className="fleet-content">
        {/* Header */}
        <div className="fleet-header">
          <div>
            <h1 className="fleet-title">Your fleet</h1>
            <p className="fleet-subtitle">
              {mapped.length} {mapped.length === 1 ? "agent" : "agents"} · {onlineCount} online
            </p>
          </div>
          <button type="button" className="fleet-btn" onClick={openChat}>
            <span className="fleet-btn-plus">+</span>
            New agent
          </button>
        </div>

        {/* Pair Telegram — first-run CTA. Self-hides when already paired. */}
        <TelegramPairPanel workspaceId={workspaceId} />

        {/* Sage operator row */}
        {sageAgent && <SageRow onChat={openChat} />}

        {/* Grid or empty */}
        {mapped.length === 0 ? (
          <EmptyFleet onChat={openChat} />
        ) : (
          <div className="fleet-grid">
            {otherAgents.map((a) => (
              <FleetCard
                key={a.id}
                agent={a}
                onSelect={setSelectedAgentId}
                onChat={openChat}
              />
            ))}
          </div>
        )}
      </main>

      {/* Detail overlay */}
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

// ── Sage operator row (the single accent fill on the page) ─────────────────

function SageRow({ onChat }: { onChat: () => void }) {
  return (
    <div className="fleet-sage">
      <div className="fleet-sage-tile">✦</div>
      <div className="fleet-sage-body">
        <div className="fleet-sage-name-row">
          <span className="fleet-sage-name">Sage</span>
          <span className="fleet-sage-badge">Operator</span>
        </div>
        <div className="fleet-sage-desc">
          Ask me to create or configure any agent for you.
        </div>
      </div>
      <button type="button" className="fleet-btn" onClick={onChat}>
        Chat with Sage
      </button>
    </div>
  );
}

// ── Empty state ────────────────────────────────────────────────────────────

function EmptyFleet({ onChat }: { onChat: () => void }) {
  return (
    <div className="fleet-empty">
      <div className="fleet-empty-title">Start your first agent</div>
      <div className="fleet-empty-desc">
        Tell Sage what you need and it&apos;ll set one up for you.
      </div>
      <button type="button" className="fleet-btn" onClick={onChat}>
        Chat with Sage
      </button>
    </div>
  );
}
