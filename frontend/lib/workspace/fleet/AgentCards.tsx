"use client";

import { useMemo } from "react";
import Link from "next/link";

import { type FleetAgent, type FleetTask } from "./fleet-data";
import { deriveAgentStatus, type FleetGateway } from "./gateway-box-picker";
import { AgentSigil, StatusChip } from "./fleet-indicators";
import { channelIconSrc, CHANNEL_LABELS } from "./fleet-icons";
import { groupTasksByAgent, planAgentCards } from "./agent-card-face";
import { rememberLastViewedAgent } from "./AgentsList";
import "./agent-cards.css";

/**
 * The workspace Agents surface — the agents themselves, in the content area,
 * the way the workspace's other first-class things are shown.
 *
 * See agent-card-face.ts for WHAT a face says and why; this file only renders
 * what that module decided. Nothing here computes a state, picks a colour, or
 * chooses an ordering — the same split ConnectorPicker/connector-card-face.ts
 * and ChannelsTab/channel-hardware-tier.ts already keep, and for the same
 * reason: a component that decides is a component no test can drive.
 *
 * NO SECOND PICKER. This grid is reached from the rail's own "Agents" row and
 * is the only agent-picking surface on screen; pressing a card navigates INTO
 * that agent's own page, which no longer carries a list beside it (the split
 * layout that used to wrap it is deleted). One picker at a time, which is what
 * "the rail is where you pick; the content is what you picked" actually asks
 * for — the same shape a project already has: a list, then the thing.
 *
 * NO COMPOSER, NO CHAT, NOWHERE. Conversation happens in channels.
 *
 * THE SEARCH BOX IS NOT IN HERE — the page owns it (agents/page.tsx), because
 * as of 2026-08-29 this grid is one of three layouts and a filter that only
 * exists on one of them is a control that disappears when you switch view.
 * `query` arrives as a prop and this component still applies it through the
 * same planAgentCards call it always did, so the rule that decides which
 * agents survive is unchanged and still lives in agent-card-face.ts.
 */
/**
 * The loading placeholder, in the REAL grid and the REAL card class — so the
 * pane does not reflow from one shape into another the instant the fetch
 * resolves. Same reasoning FleetCardGridSkeleton's own docstring gives for
 * reusing `.fleet-channel-grid`; that helper is not reused here because its
 * shape is the 4-up channel TILE, and a placeholder whose shape is not the
 * shape that arrives is its own small lie.
 */
export function AgentCardsSkeleton({ cards = 6, label = "Loading agents" }: { cards?: number; label?: string }) {
  return (
    <div className="fleet-agent-card-grid" aria-busy="true" aria-label={label}>
      {Array.from({ length: cards }).map((_, i) => (
        <div key={i} className="fleet-agent-card" style={{ cursor: "default", pointerEvents: "none" }}>
          <div className="fleet-agent-card-head">
            <div className="fleet-skeleton-bar" style={{ width: 28, height: 28, borderRadius: 999 }} />
            <div className="fleet-skeleton-bar" style={{ width: "55%", height: 12 }} />
          </div>
          <div className="fleet-skeleton-bar" style={{ width: "80%", height: 11 }} />
        </div>
      ))}
    </div>
  );
}

export function AgentCards({
  workspaceId,
  agents,
  tasks,
  gateways,
  query,
}: {
  workspaceId: string;
  /** Sage/the Operator already excluded by the caller — the same exclusion
   *  every agent-listing surface here applies, never re-derived. */
  agents: FleetAgent[];
  /** Every task in the workspace, from the shared useFleetWorkspaceTasks
   *  cache PrimaryRail/Inbox/My work are already polling. */
  tasks: FleetTask[];
  /** Paired boxes, so a brain-bound agent's status is the honest one
   *  (Needs sign-in / Computer offline) rather than a bare "Ready". */
  gateways: FleetGateway[];
  /** The page's own search text — see the file header. "" matches everything
   *  (matchesAgentCardQuery's own rule), so a caller that renders no search
   *  box passes "" and this grid behaves exactly as it did before. */
  query: string;
}) {
  const base = `/w/${encodeURIComponent(workspaceId)}`;

  // Bucketed ONCE for the whole grid, not per card — see groupTasksByAgent.
  const tasksByAgent = useMemo(() => groupTasksByAgent(tasks), [tasks]);
  const cards = useMemo(
    () => planAgentCards(agents, (a) => deriveAgentStatus(a, gateways), tasksByAgent, query),
    [agents, gateways, tasksByAgent, query],
  );

  return (
    <>
      {cards.length === 0 ? (
        <div className="fleet-agent-card-none">No agents match “{query.trim()}”.</div>
      ) : (
        <div className="fleet-agent-card-grid">
          {cards.map(({ agent, face }) => {
            // The WORKSPACE agent route, the same one the deleted list pane
            // linked to — an agent is reached without first knowing which
            // project it lives in.
            const href = `${base}/agents/${encodeURIComponent(agent.agent_id)}`;
            const channelIcon = face.reach.channelKey ? channelIconSrc(face.reach.channelKey) : "";
            return (
              <Link
                key={agent.agent_id}
                href={href}
                className={`fleet-agent-card${face.needsAttention ? " fleet-agent-card--attention" : ""}`}
                onClick={() => rememberLastViewedAgent(agent.agent_id)}
              >
                <span className="fleet-agent-card-head">
                  <span className="fleet-agent-card-sigil">
                    <AgentSigil seed={agent.agent_id} size={28} />
                  </span>
                  <span className="fleet-agent-card-name">{agent.label || "Unnamed agent"}</span>
                  <span className="fleet-agent-card-state">
                    <StatusChip tone={face.tone} label={face.stateLabel} />
                  </span>
                </span>

                {/* The channel mark is rendered only when the reach IS a
                    channel and that channel has a real brand asset — an
                    unmapped key still gets its line, just no picture. Never a
                    placeholder box standing in for a logo nobody has. */}
                <span className="fleet-agent-card-reach">
                  {channelIcon && (
                    <img
                      className="fleet-agent-card-reach-icon"
                      src={channelIcon}
                      alt=""
                      title={CHANNEL_LABELS[face.reach.channelKey] || face.reach.channelKey}
                    />
                  )}
                  <span className="fleet-agent-card-reach-text">{face.reach.label}</span>
                </span>
              </Link>
            );
          })}
        </div>
      )}
    </>
  );
}
