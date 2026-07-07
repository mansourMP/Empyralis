"use client";

import { useParams, useRouter } from "next/navigation";

import { useFleetAgents } from "@/lib/workspace/fleet/fleet-data";
import { findSageAgent } from "@/lib/workspace/fleet/fleet-presentation";
import { FleetAgentDetail } from "@/lib/workspace/fleet/FleetAgentDetail";

const VALID_TABS = ["overview", "work", "channels", "connectors", "hardware", "model", "memory", "chat"] as const;
type Tab = (typeof VALID_TABS)[number];

// Sage's detail view, routed at workspace scope — no project segment. Sage
// is the workspace operator, not project-scoped work, so its URL (and the
// breadcrumb it drives) skips straight from the workspace to Sage.
export default function SageDetailPage() {
  const params = useParams();
  const router = useRouter();
  const workspaceId = String(params?.workspaceId || "");
  const rawTab = String(params?.tab || "overview");
  const tab: Tab = (VALID_TABS as readonly string[]).includes(rawTab) ? (rawTab as Tab) : "overview";

  const base = `/w/${encodeURIComponent(workspaceId)}/sage`;

  const { agents } = useFleetAgents(workspaceId);
  const sageAgent = findSageAgent(agents);

  return (
    <FleetAgentDetail
      workspaceId={workspaceId}
      agentId={sageAgent?.agent_id || ""}
      agent={sageAgent}
      variant="page"
      initialTab={tab}
      onTabChange={(t) => router.replace(`${base}/${t}`)}
      onChat={() => router.replace(`${base}/chat`)}
    />
  );
}
