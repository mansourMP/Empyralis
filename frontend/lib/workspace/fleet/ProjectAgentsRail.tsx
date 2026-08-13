"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

import type { FleetAgent } from "./fleet-data";
import { deriveStatus } from "./fleet-presentation";
import { AgentSigil, StatusDot } from "./fleet-indicators";
import { rememberLastViewedAgent } from "./AgentsList";

/**
 * The compact agent list rail inside a project's Agents section — the
 * founder's own spec: "if I enter into agents section do I have a left
 * rail to press a specific agent and just go straight to its... chatting
 * straight?... it could have been smaller, it could have been something
 * compact." One line per agent (sigil + name + status dot), real <Link>s so
 * cmd-click/middle-click work, no columns, no sort/filter controls — that
 * belongs to a table, this is a list you scan and pick from.
 *
 * Rendered by agents/layout.tsx, which mounts this ONCE for the whole
 * Agents section (bare index and every agent's own page underneath it) —
 * the Telegram mechanic the founder asked for: the list stays on screen
 * while the content pane beside it swaps, so picking a different agent
 * never re-fetches or re-renders the list itself.
 */
export function ProjectAgentsRail({
  workspaceId,
  projectId,
  agents,
  loading,
}: {
  workspaceId: string;
  projectId: string;
  agents: FleetAgent[];
  loading: boolean;
}) {
  const pathname = usePathname() || "";
  const base = `/w/${encodeURIComponent(workspaceId)}/projects/${encodeURIComponent(projectId)}/agents`;
  // "…/agents/{agentId}/…" → {agentId}, so the row for whichever agent's
  // page is currently open highlights — works for /chat and every other tab
  // (overview, work, memory, …) since only the id segment is matched.
  const activeAgentId = (() => {
    const m = pathname.match(/\/agents\/([^/]+)/);
    return m ? decodeURIComponent(m[1]) : null;
  })();

  return (
    <nav className="fleet-agents-rail" aria-label="Agents in this project">
      <div className="fleet-agents-rail-header">
        <span>Agents</span>
        <span className="fleet-agents-rail-count">{agents.length}</span>
      </div>
      <div className="fleet-agents-rail-list">
        {loading && agents.length === 0
          ? Array.from({ length: 4 }).map((_, i) => (
              <div key={i} className="fleet-agents-rail-row" aria-hidden="true">
                <span className="fleet-skeleton-bar" style={{ width: 20, height: 20, borderRadius: 999 }} />
                <span className="fleet-skeleton-bar" style={{ width: "60%", height: 12 }} />
              </div>
            ))
          : agents.map((a) => {
              const active = a.agent_id === activeAgentId;
              const tone = deriveStatus(a.hardware_status || "unknown", Boolean(a.stopped?.active), Boolean(a.current_run_id)).tone;
              return (
                <Link
                  key={a.agent_id}
                  href={`${base}/${encodeURIComponent(a.agent_id)}/chat`}
                  aria-current={active ? "page" : undefined}
                  className={`fleet-agents-rail-row${active ? " fleet-agents-rail-row--active" : ""}`}
                  onClick={() => rememberLastViewedAgent(a.agent_id)}
                >
                  <AgentSigil seed={a.agent_id} size={20} />
                  <span className="fleet-agents-rail-row-label">{a.label || "Unnamed agent"}</span>
                  <StatusDot tone={tone} size={7} />
                </Link>
              );
            })}
      </div>
    </nav>
  );
}
