"use client";

import { useParams, useRouter } from "next/navigation";

import { useFleetAgents } from "@/lib/workspace/fleet/fleet-data";
import { isAgentDetailTab, type AgentDetailTabId } from "@/lib/workspace/fleet/agent-detail-tabs";
import { FleetAgentDetail } from "@/lib/workspace/fleet/FleetAgentDetail";
import { useBreadcrumbLabel } from "@/lib/workspace/fleet/Breadcrumbs";
import { agentDisplayLabel } from "@/lib/workspace/fleet/fleet-presentation";

// The accepted tab set is DERIVED, never hand-listed here. This file used
// to carry its own VALID_TABS array and its (now-deleted) project-scoped
// twin carried a second one; both had gone stale against the surface they
// gate, so the Context tab (the per-agent context grant) was offered,
// rendered, and unreachable from either route — silently coerced to
// "chat". One source now: agent-detail-tabs.ts, imported here and by
// FleetAgentDetail itself. Which tabs are deliberately ABSENT ("overview",
// "tools") and why "work" is still accepted are documented there too,
// once. An unrecognized tab still lands gracefully on "chat" below rather
// than 404ing.
type Tab = AgentDetailTabId;

/**
 * An agent's ONE real address. A project-scoped twin of this route
 * (.../projects/[projectId]/agents/[agentId]/[tab]/page.tsx) used to exist
 * alongside it; it is DELETED, not merely superseded — the founder's hard
 * rule, 2026-08-30: "an agent is completely independent of any project."
 * next.config.ts's LEGACY_REDIRECTS sends any old bookmark for the deleted
 * route straight here. No project id is resolved anywhere on this page —
 * there is nothing left that needs one.
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
  const agent = agents.find((a) => a.agent_id === agentId) || null;

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
      // "Up" from HERE is the agents list, never a project — Breadcrumbs.tsx
      // already crumbs it Agents › {agent}. Below 768px the list pane
      // collapses away (agents-split-pane.ts) and this is the ONLY way
      // back to it.
      //
      // `?list=1` (bug A, 2026-09-01): with exactly one agent, plain
      // `${base}/agents` bounces straight back here via the solo redirect
      // (agent-solo-redirect.ts) — the back control looked real but was a
      // dead end for every workspace holding exactly one agent. `?list=1`
      // is agents/page.tsx's own explicit-list-intent suppression for that
      // redirect; see its header comment there.
      backHref={`${base}/agents?list=1`}
      backLabel="Agents"
      initialTab={tab}
      onTabChange={(t) => router.replace(`${agentBase}/${t}`)}
      onChat={() => router.replace(`${agentBase}/chat`)}
      onRenamed={refreshAgents}
    />
  );
}
