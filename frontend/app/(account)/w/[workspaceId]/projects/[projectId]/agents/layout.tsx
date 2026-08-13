"use client";

import { useMemo, type ReactNode } from "react";
import { useParams } from "next/navigation";

import { useFleetAgents } from "@/lib/workspace/fleet/fleet-data";
import { showsProjectAgentsRail } from "@/lib/workspace/fleet/project-agents-rail-shape";
import { ProjectAgentsRail } from "@/lib/workspace/fleet/ProjectAgentsRail";

/**
 * Wraps EVERY route under this project's /agents section — the bare index
 * (agents/page.tsx, which still renders the project's own Agents/Tasks/
 * Documents chrome via ../page.tsx) and every agent's own page beneath it
 * (agents/[agentId]/[tab]/page.tsx). A Next.js layout is exactly the right
 * primitive for the founder's ask: it persists across a navigation between
 * `{children}` values instead of remounting, which is what makes the rail
 * stay on screen — and stay SCROLLED where you left it — while switching
 * which agent's chat fills the pane beside it.
 *
 * Whether the rail renders at all is the SAME count rule the rest of the
 * fleet UI already uses (agent-count-shape.ts, via
 * project-agents-rail-shape.ts) — 0 or 1 agents render `{children}` bare,
 * with no split wrapper at all, so those states are byte-for-byte what they
 * were before this layout existed.
 */
export default function ProjectAgentsLayout({ children }: { children: ReactNode }) {
  const params = useParams();
  const workspaceId = String(params?.workspaceId || "");
  const projectId = String(params?.projectId || "");

  const { agents: allAgents, loading } = useFleetAgents(workspaceId);
  const agents = useMemo(
    () => allAgents.filter((a) => (a.project_id || "").trim() === projectId),
    [allAgents, projectId],
  );

  if (!showsProjectAgentsRail(agents.length)) {
    return <>{children}</>;
  }

  return (
    <div className="fleet-project-agents-split">
      <ProjectAgentsRail workspaceId={workspaceId} projectId={projectId} agents={agents} loading={loading} />
      <div className="fleet-project-agents-content">{children}</div>
    </div>
  );
}
