"use client";

import { useMemo } from "react";
import { useParams, useRouter } from "next/navigation";

import { resolveAgentProjectId, useFleetAgents, useFleetProjects } from "@/lib/workspace/fleet/fleet-data";
import { isAgentDetailTab, type AgentDetailTabId } from "@/lib/workspace/fleet/agent-detail-tabs";
import { FleetAgentDetail } from "@/lib/workspace/fleet/FleetAgentDetail";
import { useBreadcrumbLabel } from "@/lib/workspace/fleet/Breadcrumbs";
import { agentDisplayLabel } from "@/lib/workspace/fleet/fleet-presentation";

// The accepted tab set is DERIVED, never hand-listed here. This file used
// to carry its own VALID_TABS array and its project-scoped twin carried a
// second one; both had gone stale against the surface they gate, so the
// Context tab (the per-agent context grant) was offered, rendered, and
// unreachable from either route — silently coerced to "chat". One source,
// three consumers now: agent-detail-tabs.ts, imported here, there, and by
// FleetAgentDetail itself. Which tabs are deliberately ABSENT ("overview",
// "tools") and why "work" is still accepted are documented there too,
// once. An unrecognized tab still lands gracefully on "chat" below rather
// than 404ing.
type Tab = AgentDetailTabId;

/**
 * The workspace-level twin of .../projects/[projectId]/agents/[agentId]/
 * [tab]/page.tsx — same component, same wiring, the only real difference
 * is where the project id comes from: the URL there, resolveAgentProjectId
 * HERE, because this route's whole reason to exist is letting an agent be
 * reached without first knowing which project it lives in (the workspace
 * Agents list pane — AgentConversationList.tsx — links here, not into the
 * project-scoped URL, so that agents/layout.tsx's list pane stays mounted
 * across a switch). The project-scoped URL is untouched and still the
 * canonical way in from a project's own Agents tab; this is a second real
 * door to the same agent; FleetAgentDetail itself is not edited.
 */
export default function WorkspaceAgentDetailPage() {
  const params = useParams();
  const router = useRouter();
  const workspaceId = String(params?.workspaceId || "");
  const agentId = String(params?.agentId || "");
  const rawTab = params?.tab;
  const tab: Tab | undefined =
    typeof rawTab === "string"
      ? (isAgentDetailTab(rawTab) ? rawTab : "chat")
      : undefined;

  const base = `/w/${encodeURIComponent(workspaceId)}`;
  const agentBase = `${base}/agents/${encodeURIComponent(agentId)}`;

  const { agents, loading: agentsLoading, error: agentsError, refresh: refreshAgents } = useFleetAgents(workspaceId);
  const { projects, loading: projectsLoading } = useFleetProjects(workspaceId);
  const agent = agents.find((a) => a.agent_id === agentId) || null;
  const projectId = useMemo(() => resolveAgentProjectId(agent?.project_id, projects), [agent?.project_id, projects]);
  const project = projects.find((p) => p.id === projectId);
  const projectName = project?.name || (projectsLoading ? undefined : "—");

  // agentDisplayLabel, never agent.label — landing on the workspace
  // assistant's own route would otherwise put its stored persona name in the
  // breadcrumb (and in the document title that follows the crumb).
  useBreadcrumbLabel(agentId, agent ? agentDisplayLabel(agent) : undefined);

  return (
    <FleetAgentDetail
      workspaceId={workspaceId}
      agentId={agentId}
      agent={agent}
      agentsLoading={agentsLoading}
      agentsError={agentsError}
      projectId={projectId}
      projectName={projectName}
      // "Up" from HERE is the agents list, not the agent's project — this
      // route's whole reason to exist is reaching an agent without knowing
      // which project it lives in, and Breadcrumbs.tsx already crumbs it
      // Agents › {agent}. Below 768px the list pane collapses away
      // (agents-split-pane.ts) and this is the ONLY way back to it.
      backHref={`${base}/agents`}
      backLabel="Agents"
      initialTab={tab}
      onTabChange={(t) => router.replace(`${agentBase}/${t}`)}
      onChat={() => router.replace(`${agentBase}/chat`)}
      onRenamed={refreshAgents}
    />
  );
}
