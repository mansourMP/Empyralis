"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { useParams, useRouter } from "next/navigation";

import { resolveAgentProjectId, useFleetAgents, useFleetProjects } from "@/lib/workspace/fleet/fleet-data";
import { breadcrumbCount, findSageAgent } from "@/lib/workspace/fleet/fleet-presentation";
import { createAgentQuickly, quickCreateAgentChatPath } from "@/lib/workspace/fleet/agent-quick-create";
import { FirstAgentEmpty } from "@/lib/workspace/fleet/first-agent-empty";
import { FleetListSkeleton, FleetSurfaceError } from "@/lib/workspace/fleet/fleet-states";
import { HeaderAction, useBreadcrumbBadge } from "@/lib/workspace/fleet/Breadcrumbs";
import { planAgentCountShape } from "@/lib/workspace/fleet/agent-count-shape";

/**
 * The bare workspace /agents index. Its own job shrank sharply in the
 * 2026-08-20 redesign (see agents-conversation-list.ts's header): the
 * agent-picking surface for 2+ agents now lives in the persistent list
 * pane agents/layout.tsx renders beside this page, not here — so this
 * component only ever needs to answer three questions: is the workspace
 * still loading, does it have zero real agents (FirstAgentEmpty), or
 * exactly one (redirect straight into it, a list of one is worse than no
 * list). At 2+ agents this renders a quiet "pick a conversation" prompt —
 * the list pane beside it already IS the picker.
 *
 * The board/grouped-list/filter/sort/properties-drawer surfaces that used
 * to live here (AgentsBoard, AgentsGroupedList, AgentViewOptions,
 * FleetToolbar's filters, FleetRightPanel) are NOT deleted — they were
 * already unreachable before this change (the prior "workspace-agents
 * rail space" gate hid them at the exact same 2+ threshold this file still
 * uses, so nobody could ever see them with a real fleet) — but they are no
 * longer imported here. See AgentsList.tsx/AgentsBoard.tsx/
 * AgentsGroupedList.tsx/AgentViewOptions.tsx, now orphaned rather than
 * wired to any route; reviving fleet-wide management views (sort by cost,
 * group by project, a board) is a real, separate product decision, not
 * something to half-restore as a side effect of this redesign.
 */
export default function AgentsPage() {
  const params = useParams();
  const router = useRouter();
  const workspaceId = String(params?.workspaceId || "");
  const base = `/w/${encodeURIComponent(workspaceId)}`;

  const { agents: allAgents, loading, error, refresh } = useFleetAgents(workspaceId);
  const { projects } = useFleetProjects(workspaceId);

  // Sage is the Operator, not a listed worker — it never appears as a row
  // here (contract). Same root cause as its own detail page: Sage isn't a
  // specialist agent, so it's excluded from every agent-list surface.
  const sageAgent = useMemo(() => findSageAgent(allAgents), [allAgents]);
  const agents = useMemo(
    () => (sageAgent ? allAgents.filter((a) => a.agent_id !== sageAgent.agent_id) : allAgents),
    [allAgents, sageAgent],
  );
  // U3-E: the count lives on the breadcrumb line itself ("Agents · 4"), not
  // a second toolbar row — "Agents" is both the section and the unit, so it
  // collapses to a bare count (see breadcrumbCount's doc comment).
  useBreadcrumbBadge(
    "agents",
    useMemo(
      () => <span className="fleet-breadcrumb-count">· {breadcrumbCount(agents.length, "agent", "agents", "Agents")}</span>,
      [agents.length],
    ),
  );

  // MAN-317 — "the fleet table has one row. Decide what replaces it": with
  // exactly one real agent, THIS surface redirects straight to that
  // agent's own chat page, same as the workspace root does in
  // FleetHome.tsx — a table of one is worse than no table, whether a
  // reader lands here via the rail, a bookmark, or a direct URL.
  // Reversible for free: recomputed from the live count on every render,
  // so a second real agent appearing simply stops the redirect. Guarded on
  // `!loading` so the transient agents.length===0 during the initial fetch
  // never fires a bogus redirect.
  const agentCountMode = useMemo(() => planAgentCountShape(agents.length), [agents.length]);
  const soloAgent = agentCountMode === "solo" ? agents[0] : null;
  const soloHref = useMemo(() => {
    if (!soloAgent) return null;
    const pid = resolveAgentProjectId(soloAgent.project_id, projects);
    return pid ? `${base}/projects/${encodeURIComponent(pid)}/agents/${encodeURIComponent(soloAgent.agent_id)}/chat` : null;
  }, [soloAgent, projects, base]);
  // ?new=1 is the command palette's "New agent" target, and the list
  // pane's own "+" button (AgentConversationList.tsx) — it must still
  // create a new agent on a workspace that already has exactly one, not
  // bounce away to that existing one before the create-and-navigate effect
  // below ever runs. Read once, synchronously, at mount (matching
  // consumedNew's own one-shot style further down): the query is stripped
  // from the URL within the same render pass the create kicks off in, so
  // re-deriving this from the live URL on every render would start
  // redirecting again the instant the param is gone — before the async
  // create has finished and pushed its own destination.
  const [suppressSoloRedirect] = useState<boolean>(
    () => typeof window !== "undefined" && new URLSearchParams(window.location.search).get("new") === "1",
  );
  useEffect(() => {
    if (!loading && soloHref && !suppressSoloRedirect) router.replace(soloHref);
  }, [loading, soloHref, suppressSoloRedirect, router]);

  const [creatingAgent, setCreatingAgent] = useState(false);
  const [createAgentError, setCreateAgentError] = useState<string | null>(null);

  // Zero-decision create (2026-08-19) — replaces the old FleetCreateAgentWizard
  // modal everywhere it was reachable from, this page included. See
  // agent-quick-create.ts for why every field the wizard used to ask for is
  // safe to default silently; every other Configure tab (Model, Hardware,
  // Channels, Connectors) is fully functional the instant the agent exists.
  async function createNewAgent() {
    if (creatingAgent) return;
    setCreatingAgent(true);
    setCreateAgentError(null);
    try {
      const { agentId, projectId } = await createAgentQuickly(workspaceId, undefined, projects);
      router.push(quickCreateAgentChatPath({ workspaceId, projectId, agentId }));
    } catch (e) {
      setCreateAgentError(e instanceof Error ? e.message : "Could not create the agent.");
      setCreatingAgent(false);
    }
  }

  // Onboarding hand-off: /agents?new=1 creates straight away, no
  // intermediate screen. Read the flag client-side (no useSearchParams →
  // no Suspense boundary needed), consume it once, and clean the URL so a
  // refresh doesn't fire a second create.
  const consumedNew = useRef(false);
  useEffect(() => {
    if (consumedNew.current) return;
    if (new URLSearchParams(window.location.search).get("new") === "1") {
      consumedNew.current = true;
      router.replace(`${base}/agents`);
      void createNewAgent();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [router, base]);

  // A resolvable solo agent redirects immediately (see the effect above) —
  // this branch is only ever on screen for the one paint before that
  // commits, so it stays quiet instead of flashing the page chrome first.
  // If the project can't resolve, soloHref stays null and this falls
  // through to the ordinary render below rather than a dead screen.
  // Suppressed by ?new=1 (see suppressSoloRedirect above) so a fresh
  // create can still land on a one-agent workspace.
  if (!loading && soloHref && !suppressSoloRedirect) {
    return (
      <main className="fleet-page-state">
        <div className="fleet-page-state-body">Opening {soloAgent?.label || "your agent"}…</div>
      </main>
    );
  }

  return (
    <main className="fleet-content">
      {/* FILLED, 2026-08-01, EXCEPT WHEN THE LIST IS EMPTY (2026-08-13) —
          this was unconditionally filled, which meant a brand-new
          workspace rendered this header button AND FirstAgentEmpty's own
          centred "Create your first agent" filled at the same time:
          CLAUDE.md, "Two accent-filled buttons in one view is a bug." The
          centred empty-state CTA wins the fill while the list is empty (a
          first-run empty state is the one moment its own big button IS
          the primary action); this button earns it back the moment the
          list pane holds a row, since it's the persistent primary action
          used every day past that point. At 2+ agents this still portals
          into the shell topbar, which stays visible for the bare index
          (only an agent's own tab pages suppress it — see
          FleetContentFrame.tsx's WORKSPACE_AGENT_DETAIL_ROUTE). */}
      <HeaderAction>
        <button
          type="button"
          className={`fleet-btn${agents.length === 0 ? " fleet-btn--accent" : " fleet-btn--accent-fill"}`}
          onClick={createNewAgent}
          disabled={creatingAgent}
        >
          <span className="fleet-btn-plus">+</span>
          {creatingAgent ? "Creating…" : "New agent"}
        </button>
      </HeaderAction>

      <div className="fleet-content-main">
        {loading && agents.length === 0 ? (
          <FleetListSkeleton rows={6} rowHeight={52} />
        ) : error && agents.length === 0 ? (
          <FleetSurfaceError title="Couldn’t load agents" message={error} onRetry={refresh} />
        ) : agents.length === 0 ? (
          <>
            <FirstAgentEmpty
              title="No agents yet"
              desc="Agents do the work — they handle customer chats, run tasks, and use your tools. Create your first one to get started."
              onCreate={createNewAgent}
              busy={creatingAgent}
            />
            {createAgentError && <p className="fleet-channel-expand-error">{createAgentError}</p>}
          </>
        ) : (
          // 2+ agents: agents/layout.tsx's own list pane (beside this pane,
          // not stacked above or inside it) is the browse surface now —
          // this pane just prompts a pick, same shell and same class as
          // the project-agents space's own placeholder
          // (fleet-theme.css's .fleet-project-agents-placeholder — an
          // identical shape, reused rather than a second rule).
          <div className="fleet-project-agents-placeholder">Select a conversation to start chatting.</div>
        )}
      </div>
    </main>
  );
}
