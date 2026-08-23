"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useParams, useRouter } from "next/navigation";

import {
  resolveAgentProjectId,
  useFleetAgents,
  useFleetProjects,
  useFleetWorkspaceTasks,
  type FleetProject,
} from "@/lib/workspace/fleet/fleet-data";
import { fleetAuthorizedFetch } from "@/lib/workspace/fleet/fleet-authorized-fetch";
import { useWorkspaceGateways } from "@/lib/workspace/fleet/gateway-box-picker";
import { breadcrumbCount, findSageAgent } from "@/lib/workspace/fleet/fleet-presentation";
import { quickCreateAgentChatPath } from "@/lib/workspace/fleet/agent-quick-create";
import { AgentCreateCard } from "@/lib/workspace/fleet/AgentCreateCard";
import { AgentCards, AgentCardsSkeleton } from "@/lib/workspace/fleet/AgentCards";
import { groupTasksByAgent } from "@/lib/workspace/fleet/agent-card-face";
import { rememberLastViewedAgent } from "@/lib/workspace/fleet/AgentsList";
import { AgentsBoard } from "@/lib/workspace/fleet/AgentsBoard";
import { AgentsGroupedList } from "@/lib/workspace/fleet/AgentsGroupedList";
import { AgentViewOptions } from "@/lib/workspace/fleet/AgentViewOptions";
import {
  DEFAULT_AGENT_VIEW_OPTIONS,
  readAgentViewOptions,
  sortAgentsForView,
  writeAgentViewOptions,
  type AgentViewOptions as AgentViewOptionsState,
} from "@/lib/workspace/fleet/agent-view-options";
import { FirstAgentEmpty } from "@/lib/workspace/fleet/first-agent-empty";
import { FleetSurfaceError } from "@/lib/workspace/fleet/fleet-states";
import { HeaderAction, useBreadcrumbBadge } from "@/lib/workspace/fleet/Breadcrumbs";
import { planAgentCountShape } from "@/lib/workspace/fleet/agent-count-shape";
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
 * VIEW OPTIONS (2026-08-23) — Board/Grouped-List are wired in as an OPT-IN,
 * popover-driven view, exactly the relationship Tasks already has between its
 * default board and TaskViewOptions: a gear icon (AgentViewOptions.tsx) opens
 * a popover offering Board or List, grouping (status/project/placement),
 * ordering and the six display properties (Brain/Placement/Channels/Last
 * active/Cost/Status) AgentsBoard.tsx/AgentsGroupedList.tsx already draw. The
 * DEFAULT is untouched — DEFAULT_AGENT_VIEW_OPTIONS is layout:"list",
 * grouping:"none", which resolves to the exact <AgentCards> render above,
 * unchanged, for anyone who never opens the popover. Choosing Board or a
 * grouping swaps in AgentsBoard/AgentsGroupedList instead; the choice
 * persists per workspace under its own `fleet:agent-view:*` localStorage key
 * (agent-view-options.ts), never touching Tasks' `fleet:task-view:*`.
 *
 * This retires the narrower half of primary-rail-space.test.ts's 2026-08-20
 * reintroduction guard, which banned importing AgentsBoard/AgentsGroupedList/
 * AgentViewOptions outright — that guard was written when the only way these
 * three could reappear was behind the deleted rail-morphing pick-list this
 * page used to gate behind (an accidental resurrection of dead code, never a
 * deliberate feature). This is not that: the deleted rail space stays dead
 * (still asserted there), and these three are now a deliberate, tested,
 * popover-gated feature that never touches the rail and never displaces the
 * card-grid default. See that test's own updated assertion for the invariant
 * that replaced it.
 *
 * DRIFT FOUND AND FIXED WHILE WIRING THIS IN: agent-view-options.ts's own
 * agentStatusGroup (the Board's four columns, and the List's "status"
 * grouping) predates agent-card-face.ts's 2026-08-22 build and read
 * deriveAgentStatus's raw tone alone — which means "Working" depended
 * entirely on `current_run_id`, de-facto always null for a real fleet agent
 * (CLAUDE.md's own documented finding). A seeded agent with a genuinely
 * in-progress task read "Ready" on its own Board card while sitting one
 * click away from a card grid that correctly called it "Working" — the
 * exact "two surfaces disagree about the same fleet on the same screen" bug
 * CLAUDE.md already records fixing for PrimaryRail's footer pulse ("Working"
 * now has ONE definition, in agent-card-face.ts"). Fixed the same way:
 * agentDisplayStatus (agent-view-options.ts) folds an in-progress task into
 * the same {tone:"working", label:"Working"} pair agent-card-face.ts uses,
 * and Board/Grouped-List both now read it instead of the bare tone — see
 * that function's own doc comment for the full reasoning and the live
 * before/after this pass measured.
 */

/**
 * Board-shaped skeleton reusing AgentsBoard's OWN real classNames
 * (`.fleet-agent-board`/`.fleet-agent-board-column*`/`.fleet-agent-board-card`,
 * fleet-theme.css) — a saved "board" view option persists across visits
 * (readAgentViewOptions), so the very first paint on a fresh load can already
 * be in board mode. Falling back to AgentCardsSkeleton's grid shape there
 * would mean the page opens as a card grid and then, the instant the fetch
 * resolves, reflows into multi-column kanban — the exact "loading shape
 * doesn't match the saved layout" bug this file's own AgentCardsSkeleton
 * comment already names for the default case. Ported verbatim from this
 * page's own pre-2026-08-20 history (git show c352a305, before the
 * conversation-list redesign trimmed it as dead code) rather than rewritten.
 */
function AgentsBoardSkeleton() {
  const columns = [3, 2, 4];
  return (
    <div className="fleet-agent-board" aria-busy="true" aria-label="Loading">
      {columns.map((count, ci) => (
        <section key={ci} className="fleet-agent-board-column">
          <header className="fleet-agent-board-column-header">
            <div className="fleet-skeleton-bar" style={{ width: 60, height: 11 }} />
            <div className="fleet-skeleton-bar" style={{ width: 16, height: 11 }} />
          </header>
          <div className="fleet-agent-board-column-body">
            {Array.from({ length: count }).map((_, i) => (
              <article key={i} className="fleet-agent-board-card" style={{ cursor: "default" }}>
                <div className="fleet-agent-board-card-head">
                  <div className="fleet-skeleton-bar" style={{ width: 20, height: 20, borderRadius: 999 }} />
                  <div className="fleet-skeleton-bar" style={{ width: "60%", height: 12 }} />
                </div>
                <div className="fleet-skeleton-bar" style={{ width: "80%", height: 11, marginTop: 8, opacity: 0.7 }} />
              </article>
            ))}
          </div>
        </section>
      ))}
    </div>
  );
}

/**
 * Grouped-list-shaped skeleton reusing AgentsGroupedList's real classNames
 * (`.fleet-agent-glist*`) — same "saved layout can already be non-default on
 * first paint" reasoning as AgentsBoardSkeleton above. Also ported from this
 * page's pre-2026-08-20 history.
 */
function AgentsGroupedSkeleton() {
  return (
    <div className="fleet-agent-glist" aria-busy="true" aria-label="Loading">
      {[3, 2].map((rows, si) => (
        <section key={si} className="fleet-agent-glist-section">
          <header className="fleet-agent-glist-header">
            <div className="fleet-agent-glist-header-btn" style={{ display: "flex", alignItems: "center", gap: 6 }}>
              <div className="fleet-skeleton-bar" style={{ width: 13, height: 13 }} />
              <div className="fleet-skeleton-bar" style={{ width: 100, height: 12 }} />
              <div className="fleet-skeleton-bar" style={{ width: 18, height: 11 }} />
            </div>
          </header>
          <div className="fleet-agent-glist-rows">
            {Array.from({ length: rows }).map((_, i) => (
              <div key={i} className="fleet-agent-glist-row" style={{ display: "flex", alignItems: "center", gap: 10, minHeight: 52, cursor: "default" }}>
                <div className="fleet-skeleton-bar" style={{ width: 20, height: 20, borderRadius: 999 }} />
                <div className="fleet-skeleton-bar" style={{ width: "35%", height: 12 }} />
              </div>
            ))}
          </div>
        </section>
      ))}
    </div>
  );
}

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

  // Bucketed ONCE for Board/Grouped-List — same shape AgentCards.tsx already
  // builds for itself off this same `tasks` fetch (see that file's own
  // "Bucketed ONCE for the whole grid" comment). Needed so
  // agentDisplayStatus/agentStatusGroup (agent-view-options.ts) can fold an
  // in-progress task into "Working" the same way the card grid and
  // PrimaryRail's footer pulse already do — without it, Board/Grouped-List
  // would be a THIRD surface reading `current_run_id` alone, which
  // CLAUDE.md documents as de-facto always null in practice.
  const tasksByAgent = useMemo(() => groupTasksByAgent(tasks), [tasks]);

  // Per-agent spend, for the Board/Grouped-List "Cost" column and ordering.
  // Same /fleet/usage response this page's own pre-2026-08-20 history built
  // this map from (git show c352a305) — the endpoint and response shape are
  // unchanged (verified against routes_fleet.fleet_usage /
  // usage_events_repository.summarize_usage before reusing this verbatim).
  // AgentCards' own default render never reads this — only Board/Grouped-List
  // do — so this fetch is pure addition, never a regression to the default.
  const [cost, setCost] = useState<Map<string, number>>(new Map());
  useEffect(() => {
    let cancelled = false;
    fleetAuthorizedFetch(`/api/w/${encodeURIComponent(workspaceId)}/fleet/usage?scope=workspace&period=day`, {
      credentials: "include",
    })
      .then((r) => r.json())
      .then((d) => {
        if (cancelled) return;
        const m = new Map<string, number>();
        for (const a of d?.by_agent || []) m.set(a.agent_install_id, a.usd_cost);
        setCost(m);
      })
      .catch(() => {
        /* Cost is a display extra, not a page-blocking fetch — a failure here
           just means every card reads $0.0000 until the next mount, same
           posture the pre-2026-08-20 version of this page took. */
      });
    return () => {
      cancelled = true;
    };
  }, [workspaceId]);

  const projectById = useMemo(() => new Map<string, FleetProject>(projects.map((p) => [p.id, p])), [projects]);

  // Board/List view options (agent-view-options.ts) — hydrated from
  // localStorage in an effect, never during render, so the server's markup
  // and the client's first paint agree (same discipline TaskViewOptions'
  // own wiring on the project page already follows).
  const [viewOptions, setViewOptions] = useState<AgentViewOptionsState>(DEFAULT_AGENT_VIEW_OPTIONS);
  useEffect(() => {
    if (workspaceId) setViewOptions(readAgentViewOptions(workspaceId));
  }, [workspaceId]);
  // An UPDATER, not a value — see AgentViewOptions.tsx's identical prop doc:
  // two changes landing in one React batch must not both start from the same
  // stale snapshot. The write rides inside the updater, the only place the
  // resolved next value exists.
  const updateViewOptions = useCallback(
    (update: (prev: AgentViewOptionsState) => AgentViewOptionsState) => {
      setViewOptions((prev) => {
        const next = update(prev);
        writeAgentViewOptions(workspaceId, next);
        return next;
      });
    },
    [workspaceId],
  );

  // Ordering applies ONLY to Board/Grouped-List — neither sorts internally
  // (see AgentsBoard.tsx/AgentsGroupedList.tsx's own doc comments: "the page
  // passes already-sorted agents"). AgentCards is deliberately NOT re-sorted
  // by this: its own planAgentCards ranks by attention (blocked > working >
  // unfinished setup > stopped > healthy) rather than by recency or name,
  // CLAUDE.md's own settled call for that surface ("never recency... a
  // card's position is stable") — running the view-options ordering over it
  // too would silently override a deliberate, already-shipped decision the
  // instant a saved "cost" or "name" ordering happened to be active.
  const orderedAgents = useMemo(
    () => sortAgentsForView(agents, viewOptions.ordering, viewOptions.direction, cost),
    [agents, viewOptions.ordering, viewOptions.direction, cost],
  );

  // Board/Grouped-List navigate the same way AgentCards' own card Link does
  // (rememberLastViewedAgent + the workspace-scoped agent route, no project
  // id in the URL) — onSelect's `projectId` parameter predates the
  // workspace-scoped route and is intentionally unused here.
  const goToAgent = useCallback(
    (agentId: string) => {
      rememberLastViewedAgent(agentId);
      router.push(`${base}/agents/${encodeURIComponent(agentId)}`);
    },
    [router, base],
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
  useEffect(() => {
    if (!loading && soloHref && !suppressSoloRedirect) router.replace(soloHref);
  }, [loading, soloHref, suppressSoloRedirect, router]);

  // AgentCreateCard (2026-08-20) — the founder's correction to the old
  // zero-decision instant create (see agent-quick-create.ts's own
  // "CORRECTION, 2026-08-20" header for the full quote): "New agent" opens
  // ONE card showing name/model/hardware/project pre-filled, rather than
  // creating on the click itself.
  const [cardOpen, setCardOpen] = useState(false);
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
  if (!loading && soloHref && !suppressSoloRedirect) {
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

      {/* U3-H shape, mirrored from the Tasks tab: the view-control cluster is
          its own row below the header, not squeezed into it beside "+ New
          agent". Hidden with nothing to view-option over — same "no dead
          controls" gate TaskViewOptions' own row uses (`tasks.length > 0`). */}
      {agents.length > 0 && (
        <div className="fleet-content-toolbar">
          <div className="fleet-agent-view-cluster">
            <AgentViewOptions options={viewOptions} onChange={updateViewOptions} />
          </div>
        </div>
      )}

      <div
        className={`fleet-content-main${viewOptions.layout === "board" ? " fleet-content-main--agent-board" : ""}`}
      >
        {loading && agents.length === 0 ? (
          // Which skeleton to show is decided by the SAME viewOptions the real
          // branches below switch on, for the reason AgentsBoardSkeleton's own
          // comment gives: a saved non-default layout can already be active on
          // the very first paint.
          viewOptions.layout === "board" ? (
            <AgentsBoardSkeleton />
          ) : viewOptions.grouping !== "none" ? (
            <AgentsGroupedSkeleton />
          ) : (
            <AgentCardsSkeleton cards={6} />
          )
        ) : error && agents.length === 0 ? (
          <FleetSurfaceError title="Couldn’t load agents" message={error} onRetry={refresh} />
        ) : agents.length === 0 ? (
          <FirstAgentEmpty
            title="No agents yet"
            desc="Agents do the work — they handle customer chats, run tasks, and use your tools. Create your first one to get started."
            onCreate={openCreateCard}
            createCardOpen={cardOpen}
          />
        ) : viewOptions.layout === "board" ? (
          <AgentsBoard
            agents={orderedAgents}
            gateways={gateways}
            costByAgent={cost}
            tasksByAgent={tasksByAgent}
            display={viewOptions.display}
            onSelect={goToAgent}
          />
        ) : viewOptions.grouping !== "none" ? (
          <AgentsGroupedList
            workspaceId={workspaceId}
            agents={orderedAgents}
            gateways={gateways}
            costByAgent={cost}
            tasksByAgent={tasksByAgent}
            projectById={projectById}
            grouping={viewOptions.grouping}
            display={viewOptions.display}
            onSelect={goToAgent}
          />
        ) : (
          // The unchanged default: everyone who never opens the view-options
          // popover sees exactly this, exactly as before this feature landed.
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
