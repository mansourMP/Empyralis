"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";

import { Radio, Plug, Cpu } from "lucide-react";

import { useFleetAgents, useWorkspaceActivity, useWorkspaceStatusStrip } from "./fleet-data";
import { FleetAgentDetail } from "./FleetAgentDetail";
import { FleetCard } from "./FleetCard";
import { FleetCreateAgentWizard } from "./FleetCreateAgentWizard";
import { TelegramPairPanel } from "./TelegramPairPanel";
import { isSageAgent, toAgentSummary } from "./fleet-presentation";

export function FleetHome({ workspaceId }: { workspaceId: string }) {
  const { agents, loading, error, refresh } = useFleetAgents(workspaceId);
  const [selectedAgentId, setSelectedAgentId] = useState<string | null>(null);
  const [wizardOpen, setWizardOpen] = useState(false);
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
          <button type="button" className="fleet-btn fleet-btn--accent" onClick={() => setWizardOpen(true)}>
            <span className="fleet-btn-plus">+</span>
            New agent
          </button>
        </div>

        {/* Pair Telegram — first-run CTA. Self-hides when already paired. */}
        <TelegramPairPanel workspaceId={workspaceId} />

        {/* Sage operator row */}
        {sageAgent && (
          <SageRow agentId={sageAgent.id} onChat={openChat} onSelect={setSelectedAgentId} />
        )}

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

        {/* Workspace status strip — real counts, each jumps to its page */}
        <StatusStrip workspaceId={workspaceId} />

        {/* Recent activity across the whole fleet */}
        <ActivityFeed workspaceId={workspaceId} />
      </main>

      {/* Detail overlay */}
      {selectedAgentId && (
        <FleetAgentDetail
          workspaceId={workspaceId}
          agentId={selectedAgentId}
          agent={agents.find((a) => a.agent_id === selectedAgentId) || null}
          onChat={openChat}
          onClose={() => setSelectedAgentId(null)}
        />
      )}

      {/* Create-agent wizard — on finish, refresh the list and open the new
          agent's detail modal at Overview, already configured. */}
      {wizardOpen && (
        <FleetCreateAgentWizard
          workspaceId={workspaceId}
          onClose={() => setWizardOpen(false)}
          onCreated={(newAgentId) => {
            setWizardOpen(false);
            refresh();
            setSelectedAgentId(newAgentId);
          }}
        />
      )}
    </>
  );
}

// ── Sage operator row (the single accent fill on the page) ─────────────────

function SageRow({
  agentId,
  onChat,
  onSelect,
}: {
  agentId: string;
  onChat: () => void;
  onSelect: (id: string) => void;
}) {
  return (
    <div
      role="button"
      tabIndex={0}
      className="fleet-sage"
      onClick={() => onSelect(agentId)}
      onKeyDown={(e) => {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          onSelect(agentId);
        }
      }}
    >
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
      <button
        type="button"
        className="fleet-btn"
        onClick={(e) => {
          e.stopPropagation();
          onChat();
        }}
      >
        Chat with Sage
      </button>
    </div>
  );
}

// ── Workspace status strip ──────────────────────────────────────────────────

function StatusStrip({ workspaceId }: { workspaceId: string }) {
  const status = useWorkspaceStatusStrip(workspaceId);
  const base = `/w/${encodeURIComponent(workspaceId)}`;

  if (status.loading) {
    return (
      <div className="fleet-status-strip">
        {[0, 1, 2].map((i) => <div key={i} className="fleet-status-strip-skeleton" />)}
      </div>
    );
  }

  return (
    <div className="fleet-status-strip">
      <a href={`${base}/channels`} className="fleet-status-strip-item">
        <Radio size={16} strokeWidth={1.75} />
        <span className="fleet-status-strip-value">{status.channelsConnected}/{status.channelsTotal}</span>
        <span className="fleet-status-strip-label">Channels connected</span>
      </a>
      <a href={`${base}/integrations`} className="fleet-status-strip-item">
        <Plug size={16} strokeWidth={1.75} />
        <span className="fleet-status-strip-value">{status.connectorsConnected}/{status.connectorsTotal}</span>
        <span className="fleet-status-strip-label">Connectors connected</span>
      </a>
      <a href={`${base}/hardware`} className="fleet-status-strip-item">
        <Cpu size={16} strokeWidth={1.75} />
        <span className="fleet-status-strip-value">{status.hardwareOnline}/{status.hardwareTotal}</span>
        <span className="fleet-status-strip-label">Computers online</span>
      </a>
    </div>
  );
}

// ── Recent activity across the fleet ────────────────────────────────────────

function ActivityFeed({ workspaceId }: { workspaceId: string }) {
  const { events, loading } = useWorkspaceActivity(workspaceId, 8);

  if (loading) {
    return null;
  }

  if (events.length === 0) {
    return null;
  }

  return (
    <div className="fleet-home-activity">
      <div className="fleet-detail-section-title">Recent activity</div>
      <div className="fleet-activity">
        {events.map((event) => (
          <div key={event.id || event.created_at} className="fleet-activity-item">
            <div className={`fleet-activity-dot${event.status === "logged" ? "" : " is-warn"}`} />
            <div style={{ flex: 1, minWidth: 0 }}>
              <div className="fleet-activity-title">{event.title || event.action || "Event"}</div>
              <div className="fleet-activity-meta">
                <span>{event.event_class}</span>
                {event.action && <><span>·</span><span>{event.action}</span></>}
                <span>·</span>
                <span className="fleet-activity-time">
                  {event.created_at ? new Date(event.created_at).toLocaleString() : ""}
                </span>
              </div>
            </div>
          </div>
        ))}
      </div>
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
