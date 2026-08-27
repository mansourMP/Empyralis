"use client";

import { useMemo } from "react";
import { useParams, useRouter } from "next/navigation";

import { useFleetAgents, useFleetProjects } from "@/lib/workspace/fleet/fleet-data";
import { isAgentDetailTab, type AgentDetailTabId } from "@/lib/workspace/fleet/agent-detail-tabs";
import { FleetAgentDetail } from "@/lib/workspace/fleet/FleetAgentDetail";
import { useBreadcrumbLabel, useBreadcrumbIcon } from "@/lib/workspace/fleet/Breadcrumbs";
import { ProjectIcon } from "@/lib/workspace/fleet/fleet-project-identity";

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

export default function AgentDetailPage() {
  const params = useParams();
  const router = useRouter();
  const workspaceId = String(params?.workspaceId || "");
  const projectId = String(params?.projectId || "");
  const agentId = String(params?.agentId || "");
  // `[tab]` is a required segment of this exact route, so a genuinely
  // missing tab never lands here — that's .../agents/[agentId]/page.tsx's
  // job, which redirects to /chat (the agent's front door) before this
  // component ever mounts. A falsy params.tab here only ever means the
  // client router hasn't resolved this navigation's params yet
  // (mid-transition on a slow connection, or clicking a second tab while
  // the first is still loading). Coercing that transient gap to the STRING
  // "chat" used to be exactly what stomped a just-clicked tab back
  // downstream in FleetAgentDetail, which treats any non-empty initialTab
  // as a real instruction — so leave it `undefined` instead and let
  // FleetAgentDetail keep showing whatever tab it last knew about until
  // params catch up. Only an actually-present-but-unrecognized tab string
  // (a bad/typo'd URL) defaults to "chat".
  const rawTab = params?.tab;
  const tab: Tab | undefined =
    typeof rawTab === "string"
      ? (isAgentDetailTab(rawTab) ? rawTab : "chat")
      : undefined;

  const base = `/w/${encodeURIComponent(workspaceId)}`;
  const agentBase = `${base}/projects/${encodeURIComponent(projectId)}/agents/${encodeURIComponent(agentId)}`;

  const { agents, loading: agentsLoading, error: agentsError, refresh: refreshAgents } = useFleetAgents(workspaceId);
  const { projects, loading: projectsLoading } = useFleetProjects(workspaceId);
  const agent = agents.find((a) => a.agent_id === agentId) || null;
  const project = projects.find((p) => p.id === projectId);
  // undefined while projects are still loading (shows a placeholder, never the
  // raw id); "—" once loaded if this project genuinely isn't found (deleted).
  const projectName = project?.name || (projectsLoading ? undefined : "—");

  // Real names in the breadcrumb chain instead of raw ids.
  useBreadcrumbLabel(projectId, project?.name);
  useBreadcrumbLabel(agentId, agent?.label);
  useBreadcrumbIcon(
    projectId,
    useMemo(
      () => (project ? <ProjectIcon icon={project.icon} tint={project.tint} size={16} /> : null),
      [project],
    ),
  );

  return (
    <FleetAgentDetail
      workspaceId={workspaceId}
      agentId={agentId}
      agent={agent}
      agentsLoading={agentsLoading}
      agentsError={agentsError}
      projectId={projectId}
      projectName={projectName}
      initialTab={tab}
      onTabChange={(t) => router.replace(`${agentBase}/${t}`)}
      onChat={() => router.replace(`${agentBase}/chat`)}
      onRenamed={refreshAgents}
    />
  );
}
