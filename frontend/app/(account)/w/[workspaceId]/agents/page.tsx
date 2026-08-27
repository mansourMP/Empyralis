"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { useParams, useRouter } from "next/navigation";

import {
  resolveAgentProjectId,
  useFleetAgents,
  useFleetProjects,
  useFleetWorkspaceTasks,
} from "@/lib/workspace/fleet/fleet-data";
import { useWorkspaceGateways } from "@/lib/workspace/fleet/gateway-box-picker";
import { breadcrumbCount, findSageAgent } from "@/lib/workspace/fleet/fleet-presentation";
import { quickCreateAgentChatPath } from "@/lib/workspace/fleet/agent-quick-create";
import { AgentCreateCard } from "@/lib/workspace/fleet/AgentCreateCard";
import { AgentCards, AgentCardsSkeleton } from "@/lib/workspace/fleet/AgentCards";
import { FirstAgentEmpty } from "@/lib/workspace/fleet/first-agent-empty";
import { FleetSurfaceError } from "@/lib/workspace/fleet/fleet-states";
import { HeaderAction, useBreadcrumbBadge } from "@/lib/workspace/fleet/Breadcrumbs";
import { planAgentCountShape } from "@/lib/workspace/fleet/agent-count-shape";
import { shouldRedirectToSoloAgent } from "@/lib/workspace/fleet/agent-solo-redirect";
import { createButtonClass } from "@/lib/workspace/fleet/create-accent";

/**
 * The workspace Agents surface.
 *
 * WHAT CHANGED HERE AND WHY. This page used to render one sentence — "Pick an
 * agent to watch it work." — beside a persistent picker column that
 * agents/layout.tsx mounted (AgentConversationList). That arrangement is
 * deleted, layout and column both, for two independent reasons and either
 * alone would be enough:
 *
 *   1. The column existed because you used to CHAT with an agent in the pane
 *      beside it. Chat left the platform entirely (CLAUDE.md, "THE PLATFORM IS
 *      NOT A CHAT PRODUCT" — conversation happens in channels). What was left
 *      was a Telegram-shaped contact list for agents nobody can talk to.
 *   2. The primary rail already says "Agents". A pick-list in the content area
 *      beside it is a SECOND picker, which is the one thing "the rail is where
 *      you pick; the content is what you picked" rules out.
 *
 * The content area now shows the agents themselves, the way the workspace's
 * other first-class things are shown — the founder's own framing: *"maybe just
 * like as we show this project we would show also the agents as well."* Each
 * face carries the two facts that separate one agent from another (see
 * agent-card-face.ts), never the lifecycle verb that was there before —
 * "Created" is true of all nineteen of them and therefore says nothing.
 *
 * The agent's own routed page (agents/[agentId]/[tab]) is UNTOUCHED and still
 * carries every tab it had. It simply renders full width now, exactly as its
 * project-scoped twin at .../projects/{pid}/agents/{id}/{tab} already does —
 * that route has never had a layout of its own, so this is a configuration
 * already proven in production rather than a new one.
 *
 * The count-decided shapes are unchanged and still come from
 * planAgentCountShape, never a second rule: 0 real agents -> FirstAgentEmpty,
 * exactly 1 -> straight into that agent (a grid of one is worse than no grid,
 * the same call this codebase makes for a table of one), 2+ -> the grid.
 *
 * MAN-370, 2026-08-28 — a gear-icon "view options" popover (Board /
 * Grouped-List, wired in by 78e3eabd, 2026-08-24) was UNWIRED from this page
 * again, one day after this file's own commit history shows it landing. It
 * reintroduced exactly the surface this page's header above describes
 * replacing: AgentsGroupedList's row rendered `agentActivityPreviewText`
 * (the "Created"/"Configured"/"X chat completed" lifecycle-verb line) —
 * word for word what "THE AGENTS SURFACE IS CARDS" (CLAUDE.md, 2026-08-22)
 * documents the founder rejecting live, one day before 78e3eabd shipped it
 * back in. The founder then filed a report (three screenshots, same session,
 * same "Agents · 39" page) describing two disagreeing layouts on one screen
 * — Basalt showing real channel/cost activity in the grouped list and "No
 * channel or tasks yet" on its own card — which reads as a data bug but
 * isn't one: agent-card-face.ts's reach slot deliberately shows only the
 * agent's CURRENT task-or-channel state (agent-card-face.test.ts already
 * covers `channel: "sage_telegram_hosted"` resolving correctly), while the
 * grouped list's cost/last-active columns are independent, backward-looking
 * facts that render regardless of current reachability — two true, differently
 * scoped answers about the same agent, not a wiring defect.
 *
 * No component was deleted — AgentsBoard.tsx / AgentsGroupedList.tsx /
 * AgentViewOptions.tsx / agent-view-options.ts's grouping helpers are intact
 * and importable, same "dormant, not deleted" treatment this codebase already
 * gives `/agents`'s own legacy sibling routes — only this page's import of
 * and render branch for them is gone, so the card grid is once again the
 * ONLY reachable Agents view. `agentDisplayStatus`/`agentStatusGroup`
 * (agent-view-options.ts) are UNTOUCHED: FleetAgentDetail.tsx's own header
 * status independently depends on `agentDisplayStatus` (06c8a86a, unrelated
 * to this feature), so that fix stays regardless of whether this page ever
 * re-wires the view-options popover. Re-wiring it is a real, deliberately
 * reversible one-line-import decision — do it only on an explicit founder
 * ask, not by pattern-matching "Tasks has view options, Agents should too."
 */
export default function AgentsPage() {
  const params = useParams();
  const router = useRouter();
  const workspaceId = String(params?.workspaceId || "");
  const base = `/w/${encodeURIComponent(workspaceId)}`;

  const { agents: allAgents, loading, error, refresh } = useFleetAgents(workspaceId);
  const { projects } = useFleetProjects(workspaceId);
  // The whole workspace's tasks in ONE call, off the shared polled cache
  // PrimaryRail/Inbox/My work already subscribe to — so a card can say which
  // task an agent is on at zero extra network cost. See fleet-data.ts's
  // sharedResourceCache: same key, one fetch, one interval.
  const { tasks } = useFleetWorkspaceTasks(workspaceId);
  // Paired boxes, so deriveAgentStatus can report the HONEST status of a
  // brain-bound agent ("Needs sign-in", "Computer offline") instead of the
  // "Ready" a bare heartbeat check would give it.
  const { gateways } = useWorkspaceGateways(workspaceId);

  // Sage is the Operator, not a listed worker — it never appears as a card
  // here (contract). Same exclusion every other agent-list surface applies.
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

  // AgentCreateCard (2026-08-20) — the founder's correction to the old
  // zero-decision instant create (see agent-quick-create.ts's own
  // "CORRECTION, 2026-08-20" header for the full quote): "New agent" opens
  // ONE card showing name/model/hardware/project pre-filled, rather than
  // creating on the click itself.
  //
  // Declared HERE, ahead of the solo-redirect block below, rather than in
  // its old spot beside openCreateCard/handleAgentCreated — MAN-374 needed
  // its VALUE (not just its setter) available to that effect and its
  // early-return guard, and a `const` read before its own declaration is a
  // TDZ error, not a stale closure.
  const [cardOpen, setCardOpen] = useState(false);

  // MAN-317 — with exactly one real agent, THIS surface goes straight to that
  // agent, whether a reader lands here via the rail, a bookmark, or a direct
  // URL. The workspace root does NOT do this (CLAUDE.md's own correction on
  // this point): it always lands on Projects regardless of agent count.
  // Reversible for free: recomputed from the live count on every render, so a
  // second real agent appearing simply stops the redirect. Guarded on
  // `!loading` so the transient agents.length===0 during the initial fetch
  // never fires a bogus redirect.
  const agentCountMode = useMemo(() => planAgentCountShape(agents.length), [agents.length]);
  const soloAgent = agentCountMode === "solo" ? agents[0] : null;
  const soloHref = useMemo(() => {
    if (!soloAgent) return null;
    const pid = resolveAgentProjectId(soloAgent.project_id, projects);
    return pid ? `${base}/projects/${encodeURIComponent(pid)}/agents/${encodeURIComponent(soloAgent.agent_id)}/chat` : null;
  }, [soloAgent, projects, base]);
  // ?new=1 is the command palette's "New agent" target — it must still create
  // a new agent on a workspace that already has exactly one, not bounce away
  // to that existing one before the create-and-navigate effect below ever
  // runs. Read once, synchronously, at mount (matching consumedNew's own
  // one-shot style further down): the query is stripped from the URL within
  // the same render pass the create kicks off in, so re-deriving this from the
  // live URL on every render would start redirecting again the instant the
  // param is gone — before the async create has finished.
  const [suppressSoloRedirect] = useState<boolean>(
    () => typeof window !== "undefined" && new URLSearchParams(window.location.search).get("new") === "1",
  );
  // MAN-374 — `!cardOpen` is the real guard here, and `suppressSoloRedirect`
  // alone was never enough: it only protects the ONE entry point that sets
  // `?new=1` (the command palette). "Create your first agent" (FirstAgentEmpty
  // -> openCreateCard() directly) and the "New agent" header button both open
  // this exact same wizard with no query param at all, so this effect was
  // still live under them. Reproduced end to end on a brand-new workspace:
  // AgentCreateCard.create() -> createAgentQuickly() awaits
  // fleet-data.ts's refreshFleetAgents(workspaceId) BEFORE returning, which
  // synchronously force-refetches the shared `fleet-agents:{workspaceId}`
  // cache this page's own useFleetAgents subscribes to (mounted right here,
  // one component up from the modal) — agents.length flips 0 -> 1 while the
  // wizard is still sitting on step 2 (Brain), agentCountMode computes
  // "solo" the instant that resolves, and this effect fired router.replace
  // straight into the new agent's Chat — unmounting AgentCreateCard mid-
  // sequence and skipping the required Channels step (and Apps) entirely.
  // The founder's "you cannot have a fucking agent without channel" rule
  // (agent-create-wizard.ts's own header) has no way to hold once the
  // component enforcing it has been torn down by an unrelated redirect.
  // `cardOpen` is the one signal common to every entry point — the wizard
  // being open at all is reason enough to leave this unrelated navigation
  // alone, so gating on it (rather than chasing every current and future
  // way to open the card) closes the whole class of entry points at once.
  const redirectToSolo = shouldRedirectToSoloAgent({
    loading,
    hasSoloTarget: Boolean(soloHref),
    cardOpen,
    suppressed: suppressSoloRedirect,
  });
  useEffect(() => {
    if (redirectToSolo && soloHref) router.replace(soloHref);
  }, [redirectToSolo, soloHref, router]);

  function openCreateCard() {
    setCardOpen(true);
  }
  function handleAgentCreated(result: { agentId: string; projectId: string }) {
    setCardOpen(false);
    router.push(quickCreateAgentChatPath({ workspaceId, projectId: result.projectId, agentId: result.agentId }));
  }

  // Onboarding hand-off: /agents?new=1 opens the card straight away, no
  // extra click. Read the flag client-side (no useSearchParams → no
  // Suspense boundary needed), consume it once, and clean the URL so a
  // refresh doesn't reopen it.
  const consumedNew = useRef(false);
  useEffect(() => {
    if (consumedNew.current) return;
    if (new URLSearchParams(window.location.search).get("new") === "1") {
      consumedNew.current = true;
      router.replace(`${base}/agents`);
      openCreateCard();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [router, base]);

  // A resolvable solo agent redirects immediately (see the effect above) —
  // this branch is only ever on screen for the one paint before that
  // commits, so it stays quiet instead of flashing the page chrome first.
  // If the project can't resolve, soloHref stays null and this falls
  // through to the ordinary render below rather than a dead screen.
  // MAN-374: reuses `redirectToSolo` rather than re-typing the condition a
  // second time — this is a full-component early return, so a second copy
  // that drifted from the effect's own guard would unmount AgentCreateCard
  // (rendered further down, in the ordinary return below) the instant the
  // new agent made agentCountMode compute "solo", even on a build where the
  // effect above is correctly held off.
  if (redirectToSolo) {
    return (
      <main className="fleet-page-state">
        <div className="fleet-page-state-body">Opening {soloAgent?.label || "your agent"}…</div>
      </main>
    );
  }

  return (
    // --wide, not the plain reading column, and it is load-bearing rather
    // than a taste call. fleet-theme.css's own comment on this modifier spells
    // out the trap: .fleet-content is a flex ITEM of .fleet-shell-scroll, so
    // its margin-inline:auto is a pair of CROSS-AXIS auto margins, which per
    // the flexbox spec disable stretch and absorb the leftover space
    // themselves — the box then sizes to its content's shrink-to-fit width,
    // and a `repeat(auto-fill, …)` grid inside it cannot compute a real column
    // count against an indefinite inline size, so it collapses to ONE column
    // and compounds the narrowing. Measured live before this modifier was
    // added: 18 cards in a single 310px column, centred, with ~600px of dead
    // space either side — the exact screen CLAUDE.md records the founder
    // rejecting outright on the old workspace home. --wide zeroes the inline
    // margins so the item stretches, and the 1140px cap becomes a real ceiling
    // rather than a target never reached.
    <main className="fleet-content fleet-content--wide">
      {/* WHO OWNS THE VIEW'S ONE ACCENT FILL is decided by create-accent.ts,
          not here — three controls can create an agent and up to two are on
          screen at once (this header button, FirstAgentEmpty's own centred
          CTA, and AgentCreateCard's "Create agent" once the card is open).
          The grid below spends no accent at all, which is what lets this
          stay the single filled control in the view. */}
      <HeaderAction>
        <button
          type="button"
          className={createButtonClass("header", { listIsEmpty: agents.length === 0, composerOpen: cardOpen })}
          onClick={openCreateCard}
        >
          <span className="fleet-btn-plus">+</span>
          New agent
        </button>
      </HeaderAction>

      <div className="fleet-content-main">
        {loading && agents.length === 0 ? (
          // A card-grid skeleton, not the old row skeleton: a loading state
          // whose shape is not the shape that arrives is its own small lie,
          // and it reflows the whole pane the moment real data lands.
          <AgentCardsSkeleton cards={6} />
        ) : error && agents.length === 0 ? (
          <FleetSurfaceError title="Couldn’t load agents" message={error} onRetry={refresh} />
        ) : agents.length === 0 ? (
          <FirstAgentEmpty
            title="No agents yet"
            desc="Agents do the work — they handle customer chats, run tasks, and use your tools. Create your first one to get started."
            onCreate={openCreateCard}
            createCardOpen={cardOpen}
          />
        ) : (
          <AgentCards workspaceId={workspaceId} agents={agents} tasks={tasks} gateways={gateways} />
        )}
      </div>

      {cardOpen && (
        <AgentCreateCard
          workspaceId={workspaceId}
          projects={projects}
          onClose={() => setCardOpen(false)}
          onCreated={handleAgentCreated}
        />
      )}
    </main>
  );
}
