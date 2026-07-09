"use client";

import type { CSSProperties, KeyboardEvent } from "react";
import { MessageSquare, Settings2 } from "lucide-react";

import {
  type AgentSummary,
  TINTS,
  deriveStatus,
} from "./fleet-presentation";
import { StatusChip } from "./fleet-indicators";
import { resolveHardwarePlacement, type FleetGateway } from "./gateway-box-picker";

/**
 * Agent card. Whole card opens the detail overlay; hover reveals small
 * quick-actions (configure / chat) so the resting card stays clean.
 */
export function FleetCard({
  agent,
  gateways,
  onSelect,
  onChat,
}: {
  agent: AgentSummary;
  /** Workspace's paired-box list, fetched once by the caller (FleetHome) —
   *  not per-card, since a workspace can have many agent cards on screen at
   *  once. */
  gateways: FleetGateway[];
  onSelect: (id: string) => void;
  onChat: (id: string) => void;
}) {
  const status = deriveStatus(agent.hardwareStatus, agent.stopped?.active);
  const placement = resolveHardwarePlacement(agent.hardwareAccess, agent.preferredGatewayId, gateways);
  const tint = TINTS[agent.tint];

  const tileStyle: CSSProperties = {
    ["--tile-bg" as string]: tint.bg,
    ["--tile-fg" as string]: tint.fg,
  };

  const handleKey = (e: KeyboardEvent<HTMLDivElement>) => {
    if (e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      onSelect(agent.id);
    }
  };

  return (
    <div
      role="button"
      tabIndex={0}
      className="fleet-card"
      onClick={() => onSelect(agent.id)}
      onKeyDown={handleKey}
    >
      <div className="fleet-card-actions">
        <button
          type="button"
          className="fleet-card-action-btn"
          title="Configure"
          aria-label="Configure agent"
          onClick={(e) => {
            e.stopPropagation();
            onSelect(agent.id);
          }}
        >
          <Settings2 size={16} strokeWidth={1.75} />
        </button>
        <button
          type="button"
          className="fleet-card-action-btn"
          title="Open chat"
          aria-label="Open chat with agent"
          onClick={(e) => {
            e.stopPropagation();
            onChat(agent.id);
          }}
        >
          <MessageSquare size={16} strokeWidth={1.75} />
        </button>
      </div>

      <div className="fleet-card-top">
        <div className="fleet-card-id">
          <div className="fleet-card-tile" style={tileStyle}>
            {agent.name.charAt(0).toUpperCase()}
          </div>
          <span className="fleet-card-name">{agent.name}</span>
        </div>
        <StatusChip tone={status.tone} label={status.label} />
      </div>

      <div className="fleet-card-meta">{placement.label}</div>

      <div className="fleet-card-activity">
        {agent.lastActivity ?? "No activity yet"}
      </div>
    </div>
  );
}
