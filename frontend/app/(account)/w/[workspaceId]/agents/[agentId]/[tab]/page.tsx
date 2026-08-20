"use client";

import { useMemo } from "react";
import { useParams, useRouter } from "next/navigation";

import { resolveAgentProjectId, useFleetAgents, useFleetProjects } from "@/lib/workspace/fleet/fleet-data";
import { FleetAgentDetail } from "@/lib/workspace/fleet/FleetAgentDetail";
import { useBreadcrumbLabel } from "@/lib/workspace/fleet/Breadcrumbs";

// Same tab set and same "any typo'd tab lands on chat" fallback as the
// project-scoped twin of this page — see that file's own comment for why
// "overview" is deliberately absent.
const VALID_TABS = ["general", "work", "channels", "connectors", "tools", "capabilities", "hardware", "model", "skills", "memory", "chat", "persona"] as const;
type Tab = (typeof VALID_TABS)[number];

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
      ? ((VALID_TABS as readonly string[]).includes(rawTab) ? (rawTab as Tab) : "chat")
      : undefined;

  const base = `/w/${encodeURIComponent(workspaceId)}`;
  const agentBase = `${base}/agents/${encodeURIComponent(agentId)}`;

  const { agents, refresh: refreshAgents } = useFleetAgents(workspaceId);
  const { projects, loading: projectsLoading } = useFleetProjects(workspaceId);
  const agent = agents.find((a) => a.agent_id === agentId) || null;
  const projectId = useMemo(() => resolveAgentProjectId(agent?.project_id, projects), [agent?.project_id, projects]);
  const project = projects.find((p) => p.id === projectId);
  const projectName = project?.name || (projectsLoading ? undefined : "—");

  useBreadcrumbLabel(agentId, agent?.label);

  return (
    <FleetAgentDetail
      workspaceId={workspaceId}
      agentId={agentId}
      agent={agent}
      projectId={projectId}
      projectName={projectName}
      initialTab={tab}
      onTabChange={(t) => router.replace(`${agentBase}/${t}`)}
      onChat={() => router.replace(`${agentBase}/chat`)}
      onRenamed={refreshAgents}
    />
  );
}
