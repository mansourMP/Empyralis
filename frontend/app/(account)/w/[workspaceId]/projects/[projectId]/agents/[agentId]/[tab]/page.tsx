"use client";

import { useParams, useRouter } from "next/navigation";

import { useFleetAgents, useFleetProjects } from "@/lib/workspace/fleet/fleet-data";
import { FleetAgentDetail } from "@/lib/workspace/fleet/FleetAgentDetail";
import { useBreadcrumbLabel } from "@/lib/workspace/fleet/Breadcrumbs";

const VALID_TABS = ["overview", "work", "channels", "connectors", "hardware", "model", "memory", "chat"] as const;
type Tab = (typeof VALID_TABS)[number];

export default function AgentDetailPage() {
  const params = useParams();
  const router = useRouter();
  const workspaceId = String(params?.workspaceId || "");
  const projectId = String(params?.projectId || "");
  const agentId = String(params?.agentId || "");
  const rawTab = String(params?.tab || "overview");
  const tab: Tab = (VALID_TABS as readonly string[]).includes(rawTab) ? (rawTab as Tab) : "overview";

  const base = `/w/${encodeURIComponent(workspaceId)}`;
  const agentBase = `${base}/projects/${encodeURIComponent(projectId)}/agents/${encodeURIComponent(agentId)}`;

  const { agents } = useFleetAgents(workspaceId);
  const { projects } = useFleetProjects(workspaceId);
  const agent = agents.find((a) => a.agent_id === agentId) || null;
  const project = projects.find((p) => p.id === projectId);

  // Real names in the breadcrumb chain instead of raw ids.
  useBreadcrumbLabel(projectId, project?.name);
  useBreadcrumbLabel(agentId, agent?.label);

  return (
    <FleetAgentDetail
      workspaceId={workspaceId}
      agentId={agentId}
      agent={agent}
      variant="page"
      initialTab={tab}
      onTabChange={(t) => router.replace(`${agentBase}/${t}`)}
      onChat={() => router.replace(`${agentBase}/chat`)}
    />
  );
}
