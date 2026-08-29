"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useParams, useRouter } from "next/navigation";
import Link from "next/link";

import {
  resolveAgentProjectId,
  useFleetAgents,
  useFleetProjects,
  useFleetWorkspaceTasks,
  type FleetProject,
} from "@/lib/workspace/fleet/fleet-data";
import { fleetAuthorizedFetch } from "@/lib/workspace/fleet/fleet-authorized-fetch";
import { deriveAgentStatus, useWorkspaceGateways } from "@/lib/workspace/fleet/gateway-box-picker";
import { breadcrumbCount, findSageAgent } from "@/lib/workspace/fleet/fleet-presentation";
import { quickCreateAgentChatPath } from "@/lib/workspace/fleet/agent-quick-create";
import { AgentCreateCard } from "@/lib/workspace/fleet/AgentCreateCard";
import { AgentCards, AgentCardsSkeleton } from "@/lib/workspace/fleet/AgentCards";
import { AgentsBoard } from "@/lib/workspace/fleet/AgentsBoard";
import { AgentsGroupedList } from "@/lib/workspace/fleet/AgentsGroupedList";
import { AgentViewOptions } from "@/lib/workspace/fleet/AgentViewOptions";
import { groupTasksByAgent, planAgentCards } from "@/lib/workspace/fleet/agent-card-face";
import {
  DEFAULT_AGENT_VIEW_OPTIONS,
  readAgentViewOptions,
  sortAgentsForView,
  writeAgentViewOptions,
  type AgentViewOptions as AgentViewOptionsState,
} from "@/lib/workspace/fleet/agent-view-options";
import { rememberLastViewedAgent } from "@/lib/workspace/fleet/AgentsList";
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
 * VIEW OPTIONS — CARDS · BOARD · LIST (2026-08-29, on the founder's own ask).
 * *"inside this projects I have this thing that shows board and list,
 * ordering, display properties etcetera. But in agents I had it as well, but
 * you just removed it... There should be also two different user interfaces —
 * the style I mean, board and list."* So the gear beside the search opens the
 * same popover shape Tasks has (AgentViewOptions.tsx), and the choice
 * persists per workspace under its own `fleet:agent-view:*` key, never
 * touching Tasks' `fleet:task-view:*`.
 *
 * THE CARD GRID IS STILL THE DEFAULT and is a REAL layout value, not the
 * absence of one. Board and List are somewhere you switch to and can switch
 * back from. That is the correction to how this shipped in 78e3eabd: it
 * spelled the default as `layout: "list", grouping: "none"`, which was
 * accurate when that branch rendered the flat table and became a lie the day
 * it rendered cards — the popover's "List" chip lit up over a grid.
 *
 * IT WAS UNWIRED ONCE (MAN-370, 2026-08-28) AND THE REASON WAS REAL: both
 * new renderings drew `agentActivityPreviewText`, the
 * "Created"/"Configured" lifecycle-verb line that "THE AGENTS SURFACE IS
 * CARDS" (CLAUDE.md, 2026-08-22) records the founder rejecting live. A
 * column of "Created" is true of every agent that has ever existed and
 * therefore says nothing, and putting it back one click from the grid built
 * to replace it is what produced his report of two disagreeing layouts.
 * Fixed at the source rather than by hiding the views: both now render
 * agent-card-face.ts's own REACH line (the task it is on > tasks waiting >
 * where it answers > neither), and `agentActivityPreviewText` is DELETED so
 * it cannot be reached for again. The three renderings therefore cannot
 * disagree about any agent — they share one status vocabulary
 * (agentDisplayStatus) and one reach rule.
 *
 * WHAT THIS RESTORATION DELIBERATELY DID NOT DO is revert 69a01d81. Since
 * that commit this page gained `.fleet-content--cards` (the 1560px cap that
 * closed a measured 354px gap between the grid and its own "+ New agent"
 * button, 3 columns -> 4), lost a doubled page shell that was stacking padding
 * and nesting a second scrollbar, and dropped the amber from a reach line 25
 * of 40 cards carried. All of that is intact; the view options are wired on
 * top of it.
 */
/**
 * Board- and list-shaped skeletons, in those surfaces' OWN real classNames.
 * A saved layout persists across visits (readAgentViewOptions), so the very
 * first paint on a fresh load can already be Board or List — falling back to
 * the card-grid skeleton there means the page opens as a grid and reflows into
 * a different shape the instant the fetch resolves, which is the same small
 * lie AgentCardsSkeleton's own comment names for the default case.
 */
const SHELL_MODIFIER: Record<AgentViewOptionsState["layout"], string> = {
  cards: "fleet-content--cards",
  board: "fleet-content--agent-board",
  list: "fleet-content--cards",
};

function AgentsBoardSkeleton() {
  return (
    <div className="fleet-agent-board" aria-busy="true" aria-label="Loading agents">
      {[3, 2, 4].map((count, ci) => (
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

function AgentsListSkeleton() {
  return (
    <div className="fleet-agent-glist" aria-busy="true" aria-label="Loading agents">
      <div className="fleet-agent-glist-rows">
        {Array.from({ length: 6 }).map((_, i) => (
          <div
            key={i}
            className="fleet-agent-glist-row"
            style={{ display: "flex", alignItems: "center", gap: 10, minHeight: 52, cursor: "default" }}
          >
            <div className="fleet-skeleton-bar" style={{ width: 20, height: 20, borderRadius: 999 }} />
            <div className="fleet-skeleton-bar" style={{ width: "35%", height: 12 }} />
          </div>
        ))}
      </div>
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
  // ── View options (agent-view-options.ts) ────────────────────────────────
  // Hydrated from localStorage in an EFFECT, never during render, so the
  // server's markup and the client's first paint agree — the same discipline
  // TaskViewOptions' own wiring on the project page follows.
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

  // ONE search, shared by all three layouts. It used to live inside
  // AgentCards, which made it a filter that vanished the moment you switched
  // view — and a control that exists on one layout and not the others is the
  // same defect as a control that does nothing.
  const [query, setQuery] = useState("");

  // Bucketed ONCE for every layout — see groupTasksByAgent. Board and List
  // need it so agentDisplayStatus/agentStatusGroup fold an in-progress task
  // into "Working" the way the card grid and PrimaryRail's footer pulse
  // already do, and so each row's reach line reads the same tasks the card
  // face reads. Without it they would be a third surface reading
  // `current_run_id` alone, which CLAUDE.md documents as de-facto always null.
  const tasksByAgent = useMemo(() => groupTasksByAgent(tasks), [tasks]);

  // Per-agent spend, for the Board/List "Cost" field and the cost ordering.
  // The card grid never reads it (a face is two facts and refuses a third),
  // so this is pure addition to the default view rather than a cost it pays.
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
           just means every row reads $0.0000 until the next mount. */
      });
    return () => {
      cancelled = true;
    };
  }, [workspaceId]);

  const projectById = useMemo(() => new Map<string, FleetProject>(projects.map((p) => [p.id, p])), [projects]);

  // Board and List get the SAME filtered set the card grid would show, run
  // through planAgentCards — one query rule for the whole page, and it is the
  // shared one (agent-card-face.ts's matchesAgentCardQuery, which matches the
  // name and the reach line, i.e. exactly what these surfaces now draw).
  // Computed only for those two layouts; AgentCards runs the identical call
  // on the identical inputs for itself, so the two can never disagree.
  const listAgents = useMemo(() => {
    if (viewOptions.layout === "cards") return [];
    const matching = planAgentCards(agents, (a) => deriveAgentStatus(a, gateways), tasksByAgent, query).map(
      (c) => c.agent,
    );
    return sortAgentsForView(matching, viewOptions.ordering, viewOptions.direction, cost);
  }, [agents, gateways, tasksByAgent, query, viewOptions.layout, viewOptions.ordering, viewOptions.direction, cost]);

  // Board and List navigate the same way a card's own <Link> does
  // (rememberLastViewedAgent + the workspace-scoped agent route, no project id
  // in the URL) — onSelect's `projectId` parameter predates that route and is
  // deliberately unused here.
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
    // --cards rides ON TOP of --wide rather than replacing it: --wide's
    // `margin-inline: 0` is the load-bearing half (see its own comment and
    // the paragraph just above), and --cards only raises the ceiling that
    // half made reachable. Measured at 1680x1050 before it: the grid stopped
    // at x=1317 while the shell — and this page's own "+ New agent" button —
    // ran to x=1671, so 354px of the content area was empty and the primary
    // action floated 354px right of everything it acts on.
    //
    // THE PAGE HAS ONE WIDTH, and only the Board is shaped differently:
    //   cards + list  --cards' 1560px ceiling. Capping the list at the
    //                 narrower --content-max-wide instead was measured and
    //                 rejected — the gear jumped 290px sideways on a layout
    //                 switch and 290px of content area sat empty beside every
    //                 row (see that rule's own comment).
    //   board         full-bleed and full-height: the columns scroll, the page
    //                 does not, or a tall column pushes its own heading off
    //                 the top of the screen.
    <main className={`fleet-content fleet-content--wide ${SHELL_MODIFIER[viewOptions.layout]}`}>
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

      {/* NO `.fleet-content-main` WRAPPER, and its absence is the fix rather
          than an omission. That class is `.fleet-content-with-panel`'s child
          (Projects' shape): it carries `padding: 28px 32px`, its own
          `max-width: var(--content-max-wide)`, and `height: 100%; overflow-y:
          auto` — all three correct INSIDE a `position:relative; overflow:
          hidden; flex:1` parent, and all three wrong inside `.fleet-content`,
          which already supplies the padding and the cap and is not a scroll
          box. Nesting them stacked both, and measured live at 1680x1050:

            padding      28+28 = 56px above the search field, 32+32 = 64px
                         each side, so the grid started 64px in from the rail
                         and lost 64px of the width it lays columns out
                         against
            scrolling    .fleet-content-main became a SECOND scroller
                         (scrollHeight 1616 / clientHeight 994) nested inside
                         the page's own — the exact "second, mis-placed
                         scrollbar ... floated at the column's right edge in
                         the middle of the screen" that .fleet-content's own
                         comment documents having fixed once already, back
                         because of the nesting rather than because of that
                         rule

          The branches below are `.fleet-content`'s own children now, so the
          page has ONE padded shell and ONE scroller. */}
      {/* ONE toolbar row: search left, view options right. Rendered only once
          there is something to act on — with no agents at all, the empty state
          below is the whole page and a filter plus a layout switch over
          nothing is two controls with nothing to do.

          The search itself has a HIGHER bar than the gear: it appears at 8+
          agents, keyed on the REAL agent count and never on the filtered
          result, or typing a query that matches nothing would delete the only
          control that can undo it. Below that threshold the spacer holds the
          gear at the row's right edge on its own. */}
      {agents.length > 0 && (
        <div className="fleet-agent-surface-toolbar">
          {agents.length >= 8 ? (
            <div className="fleet-agent-card-search">
              <input
                type="text"
                className="fleet-wizard-input"
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                placeholder="Search agents"
                aria-label="Search agents"
              />
            </div>
          ) : null}
          <div className="fleet-agent-surface-toolbar-spacer" />
          {/* One level below Agents (CLAUDE.md: "a surface must earn its
              place"), never a rail item — the workspace-wide reader for
              agent_traces (WorkLedgerView.tsx / GET /api/agent-traces),
              which had zero UI callers before this. A real <a> (next/link),
              not a button + router.push, so cmd-click still opens a new
              tab — and .fleet-link, never a filled button: this is a
              secondary destination sharing the row with the view-options
              gear, not the page's one primary action. */}
          <Link href={`${base}/agents/work`} className="fleet-link">
            Work
          </Link>
          <AgentViewOptions options={viewOptions} onChange={updateViewOptions} />
        </div>
      )}

      {loading && agents.length === 0 ? (
        // The skeleton's shape is decided by the SAME viewOptions the real
        // branches below switch on: a loading state whose shape is not the
        // shape that arrives is its own small lie, and it reflows the whole
        // pane the moment real data lands. A saved layout can already be
        // Board or List on the very first paint.
        viewOptions.layout === "board" ? (
          <AgentsBoardSkeleton />
        ) : viewOptions.layout === "list" ? (
          <AgentsListSkeleton />
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
      ) : listAgents.length === 0 && viewOptions.layout !== "cards" ? (
        // A QUERY THAT MATCHED NOTHING IS NOT AN EMPTY WORKSPACE, and both of
        // these components return null on an empty list — a blank pane with no
        // sentence in it. The card grid already says this (AgentCards' own
        // .fleet-agent-card-none); Board and List say it in the same words, on
        // the same class, so the three never disagree about what happened.
        <div className="fleet-agent-card-none">No agents match “{query.trim()}”.</div>
      ) : viewOptions.layout === "board" ? (
        <AgentsBoard
          agents={listAgents}
          gateways={gateways}
          costByAgent={cost}
          tasksByAgent={tasksByAgent}
          display={viewOptions.display}
          onSelect={goToAgent}
        />
      ) : viewOptions.layout === "list" ? (
        <AgentsGroupedList
          workspaceId={workspaceId}
          agents={listAgents}
          gateways={gateways}
          costByAgent={cost}
          tasksByAgent={tasksByAgent}
          projectById={projectById}
          grouping={viewOptions.grouping}
          display={viewOptions.display}
          onSelect={goToAgent}
        />
      ) : (
        // The default, and unchanged: everyone who never opens the popover
        // sees exactly the grid agent-card-face.ts decides.
        <AgentCards workspaceId={workspaceId} agents={agents} tasks={tasks} gateways={gateways} query={query} />
      )}

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
