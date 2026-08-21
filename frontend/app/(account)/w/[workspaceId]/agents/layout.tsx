"use client";

import { useMemo, type ReactNode } from "react";
import { useParams } from "next/navigation";

import { useFleetAgents } from "@/lib/workspace/fleet/fleet-data";
import { findSageAgent } from "@/lib/workspace/fleet/fleet-presentation";
import { planAgentCountShape } from "@/lib/workspace/fleet/agent-count-shape";
import { AgentConversationList } from "@/lib/workspace/fleet/AgentConversationList";
import { agentsSplitClassName } from "@/lib/workspace/fleet/agents-split-pane";

/**
 * The workspace Agents surface, 2026-08-20 redesign — see
 * agents-conversation-list.ts's header for the full "why". This layout is
 * the real Next.js layout that makes the list pane PERSISTENT: it wraps
 * both the bare index (page.tsx) and every agent's own routed page
 * (`[agentId]/[tab]/page.tsx`) beneath it, so switching agents never
 * remounts or re-fetches the list — the exact non-remounting property the
 * founder's own earlier spec asked for ("it could have been smaller, it
 * could have been something compact... list stays put while the pane
 * beside it swaps"), now generalized from one project's agents to the
 * whole workspace's.
 *
 * The list pane renders ONLY at 2+ real agents (planAgentCountShape's
 * "fleet" mode) — at 0 the bare index's own FirstAgentEmpty fills the
 * whole pane (nothing to pick), and at 1 the bare index redirects straight
 * into that one agent's chat (a list of one is worse than no list, the
 * same call this codebase already makes for a table of one). Both of
 * those states pass `children` straight through, unwrapped — this layout
 * decides ONE thing, whether there is a picker to show, and leaves the
 * loading/error/empty/redirect states entirely to page.tsx, which already
 * owns them.
 */
export default function AgentsLayout({ children }: { children: ReactNode }) {
  const params = useParams();
  const workspaceId = String(params?.workspaceId || "");
  const activeAgentId = typeof params?.agentId === "string" ? params.agentId : null;

  const { agents: allAgents } = useFleetAgents(workspaceId);

  // Sage/the Operator never appears in any agent-picking surface — the
  // same exclusion every other agent list here already applies.
  const sageAgent = useMemo(() => findSageAgent(allAgents), [allAgents]);
  const agents = useMemo(
    () => (sageAgent ? allAgents.filter((a) => a.agent_id !== sageAgent.agent_id) : allAgents),
    [allAgents, sageAgent],
  );

  const showsList = planAgentCountShape(agents.length) === "fleet";

  if (!showsList) return <>{children}</>;

  return (
    // At phone width this row collapses to ONE pane — the list at the bare
    // index, the picked agent's own surface once one is selected — because
    // a 320px-floored list beside a flex:1 detail leaves the detail 55px of
    // a 375px screen. agents-split-pane.ts owns that rule (and only ever
    // feeds a max-width:768px block; the desktop row is unchanged).
    <div className={agentsSplitClassName(activeAgentId)}>
      <AgentConversationList workspaceId={workspaceId} agents={agents} activeAgentId={activeAgentId} />
      <div className="fleet-agents-detail-pane">{children}</div>
    </div>
  );
}
