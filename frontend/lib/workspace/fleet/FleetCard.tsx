"use client";

import type { CSSProperties, KeyboardEvent } from "react";
import { MessageSquare, Settings2 } from "lucide-react";

import {
  type AgentSummary,
  TINTS,
  deriveStatus,
  derivePlacement,
  statusClass,
} from "./fleet-presentation";

/**
 * Agent card. Whole card opens the detail overlay; hover reveals small
 * quick-actions (configure / chat) so the resting card stays clean.
 */
export function FleetCard({
  agent,
  onSelect,
  onChat,
}: {
  agent: AgentSummary;
  onSelect: (id: string) => void;
  onChat: (id: string) => void;
}) {
  const status = deriveStatus(agent.hardwareStatus);
  const deployed = status.tone !== "unknown";
  const placement = derivePlacement(agent.runtimeTarget, deployed);
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
        <span className={`fleet-card-status ${statusClass(status.tone)}`}>
          <span className="fleet-card-status-dot" />
          {status.label}
        </span>
      </div>

      <div className="fleet-card-meta">{placement}</div>

      <div className="fleet-card-activity">
        {agent.lastActivity ?? "No activity yet"}
      </div>
    </div>
  );
}
