"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useParams, useRouter } from "next/navigation";

import { Bot, Radio } from "lucide-react";

import { resolveAgentProjectId, useFleetAgents, useFleetProjects, type FleetAgent, type FleetProject } from "@/lib/workspace/fleet/fleet-data";
import { breadcrumbCount, findSageAgent } from "@/lib/workspace/fleet/fleet-presentation";
import { AgentsList, rememberLastViewedAgent } from "@/lib/workspace/fleet/AgentsList";
import { AgentsBoard } from "@/lib/workspace/fleet/AgentsBoard";
import { AgentsGroupedList } from "@/lib/workspace/fleet/AgentsGroupedList";
import { AgentViewOptions } from "@/lib/workspace/fleet/AgentViewOptions";
import {
  DEFAULT_AGENT_VIEW_OPTIONS,
  agentSurfaceFor,
  readAgentViewOptions,
  sortAgentsForView,
  writeAgentViewOptions,
  type AgentViewOptions as AgentViewOptionsState,
} from "@/lib/workspace/fleet/agent-view-options";
import { useWorkspaceGateways } from "@/lib/workspace/fleet/gateway-box-picker";
import { UsageStat, bucketSeries, type UsageBucket } from "@/lib/workspace/fleet/fleet-sparkline";
import { FleetToolbar, type ToolbarFilter } from "@/lib/workspace/fleet/FleetToolbar";
import { FleetRightPanel, PanelSection, PanelRow } from "@/lib/workspace/fleet/FleetRightPanel";
import { FleetCreateAgentWizard } from "@/lib/workspace/fleet/FleetCreateAgentWizard";
import { FirstAgentEmpty } from "@/lib/workspace/fleet/first-agent-empty";
import { FleetListSkeleton, FleetSurfaceError } from "@/lib/workspace/fleet/fleet-states";
import { HeaderAction, useBreadcrumbBadge } from "@/lib/workspace/fleet/Breadcrumbs";
import { planAgentCountShape } from "@/lib/workspace/fleet/agent-count-shape";

type SortMode = "last_active" | "status" | "cost" | "name" | "group";

const STATUS_RANK: Record<string, number> = { online: 0, unknown: 1, offline: 2 };
const SORT_OPTIONS = [
  { value: "last_active", label: "Last active" },
  { value: "status", label: "Status" },
  { value: "cost", label: "Cost" },
  { value: "name", label: "Name" },
  { value: "group", label: "Group by project" },
];

type FilterState = { project: string; status: string; channel: string; sort: SortMode };

/**
 * Board-shaped skeleton reusing AgentsBoard's OWN real classNames
 * (`.fleet-agent-board`/`.fleet-agent-board-column*`/`.fleet-agent-board-card`,
 * fleet-theme.css) — a saved "board" view option persists across visits
 * (readAgentViewOptions), so the very FIRST paint on a fresh load can already
 * be in board mode. Rendering `FleetListSkeleton`'s flat 52px rows in that
 * case meant the page opened as a table and then, the instant the fetch
 * resolved, reflowed into multi-column kanban cards — the exact "loading
 * shape doesn't match the saved layout" bug this pass exists to close.
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
 * first paint" reasoning as AgentsBoardSkeleton above.
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

// View state lives in the URL, not just useState — so leaving for an agent's
// detail and coming back (including the Esc-to-return browser-back path)
// restores the exact same filtered/sorted view instead of resetting it.
function readFiltersFromLocation(): FilterState {
  if (typeof window === "undefined") return { project: "all", status: "all", channel: "all", sort: "last_active" };
  const sp = new URLSearchParams(window.location.search);
  return {
    project: sp.get("project") || "all",
    status: sp.get("status") || "all",
    channel: sp.get("channel") || "all",
    sort: (sp.get("sort") as SortMode) || "last_active",
  };
}

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
  // exactly one real agent, THIS surface (the fleet table itself) redirects
  // straight to that agent's own chat page, same as the workspace root does
  // in FleetHome.tsx — a table of one is worse than no table, whether a
  // reader lands here via the rail, a bookmark, or a direct URL. Reversible
  // for free: recomputed from the live count on every render, so a second
  // real agent appearing simply stops the redirect and the table below
  // takes over. Guarded on `!loading` so the transient agents.length===0
  // during the initial fetch never fires a bogus redirect.
  const agentCountMode = useMemo(() => planAgentCountShape(agents.length), [agents.length]);
  const soloAgent = agentCountMode === "solo" ? agents[0] : null;
  const soloHref = useMemo(() => {
    if (!soloAgent) return null;
    const pid = resolveAgentProjectId(soloAgent.project_id, projects);
    return pid ? `${base}/projects/${encodeURIComponent(pid)}/agents/${encodeURIComponent(soloAgent.agent_id)}/chat` : null;
  }, [soloAgent, projects, base]);
  // ?new=1 is the command palette's "New agent" target (`go(base + "/agents?
  // new=1")`) — it must still open the wizard on a workspace that already
  // has exactly one agent, not bounce away from it before the wizard effect
  // below ever gets to open. Read once, synchronously, at mount (matching
  // consumedNew's own one-shot style further down): the query is stripped
  // from the URL within the same render pass the wizard opens in, so
  // re-deriving this from the live URL on every render would start
  // redirecting again the instant the param is gone — before the reader
  // has done anything with the wizard that just opened.
  const [suppressSoloRedirect] = useState<boolean>(
    () => typeof window !== "undefined" && new URLSearchParams(window.location.search).get("new") === "1",
  );
  useEffect(() => {
    if (!loading && soloHref && !suppressSoloRedirect) router.replace(soloHref);
  }, [loading, soloHref, suppressSoloRedirect, router]);
  const [filterState, setFilterState] = useState<FilterState>(() => readFiltersFromLocation());
  const { project: projectFilter, status: statusFilter, channel: channelFilter, sort } = filterState;

  // Board/List layout, grouping, ordering, display properties — the new,
  // additive control. Hydrated from localStorage in an effect, never during
  // render, so the server's markup and the client's first paint agree (same
  // discipline the project page's own viewOptions state follows for tasks).
  // Its OWN namespace (fleet:agent-view:*) and its OWN engine
  // (agent-view-options.ts) — see that file's header for why this is a
  // parallel build rather than a reuse of task-view-options.ts.
  const [viewOptions, setViewOptions] = useState<AgentViewOptionsState>(DEFAULT_AGENT_VIEW_OPTIONS);
  useEffect(() => {
    if (workspaceId) setViewOptions(readAgentViewOptions(workspaceId));
  }, [workspaceId]);
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
  const surface = agentSurfaceFor(viewOptions);
  // Paired-Gateway boxes for this workspace — needed to resolve a
  // brain-bound agent's real status/placement (deriveAgentStatus /
  // agentPlacementCategory) on the Board and Grouped-list surfaces. Its own
  // independent fetch, same as AgentsList.tsx's internal call to the same
  // hook — see AgentsBoard.tsx's prop doc for why that duplication is fine.
  const { gateways } = useWorkspaceGateways(workspaceId);

  const updateFilters = useCallback((patch: Partial<FilterState>) => {
    setFilterState((prev) => {
      const next = { ...prev, ...patch };
      const sp = new URLSearchParams();
      if (next.project !== "all") sp.set("project", next.project);
      if (next.status !== "all") sp.set("status", next.status);
      if (next.channel !== "all") sp.set("channel", next.channel);
      if (next.sort !== "last_active") sp.set("sort", next.sort);
      const qs = sp.toString();
      router.replace(`${base}/agents${qs ? `?${qs}` : ""}`);
      return next;
    });
  }, [router, base]);

  const [cost, setCost] = useState<Map<string, number>>(new Map());
  // Same /fleet/usage response the per-agent cost map is built from — the
  // `totals` block is the workspace roll-up (usd_cost, total_tokens, events),
  // fed into the properties drawer below so both live off one fetch.
  const [usageTotals, setUsageTotals] = useState<{ usd_cost?: number; total_tokens?: number; events?: number } | null>(null);
  const [usageBuckets, setUsageBuckets] = useState<UsageBucket[]>([]);
  const [wizardOpen, setWizardOpen] = useState(false);
  // Properties drawer — closed by default, an overlay over the sheet. The
  // page itself shows only agents; everything else (spend, tokens, channel
  // count) lives behind this toggle instead of a permanent strip up top.
  const [panelOpen, setPanelOpen] = useState(false);

  // Onboarding hand-off: /agents?new=1 lands straight in the wizard. Read the
  // flag client-side (no useSearchParams → no Suspense boundary needed),
  // consume it once, and clean the URL so a refresh doesn't reopen it.
  const consumedNew = useRef(false);
  useEffect(() => {
    if (consumedNew.current) return;
    if (new URLSearchParams(window.location.search).get("new") === "1") {
      consumedNew.current = true;
      setWizardOpen(true);
      router.replace(`${base}/agents`);
    }
  }, [router, base]);

  useEffect(() => {
    let cancelled = false;
    fetch(`/api/w/${encodeURIComponent(workspaceId)}/fleet/usage?scope=workspace&period=day`, { credentials: "include" })
      .then((r) => r.json())
      .then((d) => {
        if (cancelled) return;
        const m = new Map<string, number>();
        for (const a of d?.by_agent || []) m.set(a.agent_install_id, a.usd_cost);
        setCost(m);
        setUsageTotals(d?.totals || null);
        if (Array.isArray(d?.buckets)) setUsageBuckets(d.buckets);
      })
      .catch(() => {});
    return () => { cancelled = true; };
  }, [workspaceId]);

  const projById = useMemo(() => new Map<string, FleetProject>(projects.map((p) => [p.id, p])), [projects]);

  const filtered = useMemo(() => {
    return agents.filter((a) => {
      if (projectFilter !== "all" && (a.project_id || "") !== projectFilter) return false;
      if (statusFilter !== "all" && (a.hardware_status || "unknown") !== statusFilter) return false;
      if (channelFilter === "connected" && !a.channel) return false;
      if (channelFilter === "none" && a.channel) return false;
      return true;
    });
  }, [agents, projectFilter, statusFilter, channelFilter]);
  const shown = useMemo(() => sortAgents(filtered, sort, cost), [filtered, sort, cost]);
  // The Board and Grouped-list surfaces order via the NEW view-options
  // popover instead of the legacy "Sort by" dropdown above — same `filtered`
  // input (the project/status/channel filters apply everywhere regardless of
  // layout), a different ordering function. The flat table (`shown`) is
  // untouched by this and keeps using the pre-existing sortAgents exactly as
  // it always has.
  const orderedForNewSurfaces = useMemo(
    () => sortAgentsForView(filtered, viewOptions.ordering, viewOptions.direction, cost),
    [filtered, viewOptions.ordering, viewOptions.direction, cost],
  );

  const filters: ToolbarFilter[] = [
    {
      key: "project", label: "Project", value: projectFilter, onChange: (v) => updateFilters({ project: v }),
      options: [{ value: "all", label: "All projects" }, ...projects.map((p) => ({ value: p.id, label: p.name || p.id }))],
    },
    {
      key: "status", label: "Status", value: statusFilter, onChange: (v) => updateFilters({ status: v }),
      options: [
        { value: "all", label: "All statuses" },
        { value: "online", label: "Online" },
        { value: "offline", label: "Offline" },
        { value: "unknown", label: "Not deployed" },
      ],
    },
    {
      key: "channel", label: "Channel", value: channelFilter, onChange: (v) => updateFilters({ channel: v }),
      options: [
        { value: "all", label: "All channels" },
        { value: "connected", label: "Connected" },
        { value: "none", label: "No channel" },
      ],
    },
  ];

  // Where an agent row goes on a plain click, via `router.push` in
  // goToAgent below — straight into Chat, the agent's front door (Overview
  // is still one click/tab away, never gone).
  const agentHref = (agentId: string, projectId: string) =>
    `${base}/projects/${encodeURIComponent(resolveAgentProjectId(projectId, projects))}/agents/${encodeURIComponent(agentId)}/chat`;

  const goToAgent = (agentId: string, projectId: string) => {
    rememberLastViewedAgent(agentId);
    router.push(agentHref(agentId, projectId));
  };

  const activeCount = agents.filter((a) => (a.hardware_status || "").toLowerCase() === "online").length;
  const channelsLiveCount = agents.filter((a) => (a.channel || "").trim().length > 0).length;
  let spendToday = 0;
  for (const v of cost.values()) spendToday += Number(v || 0);

  // A resolvable solo agent redirects immediately (see the effect above) —
  // this branch is only ever on screen for the one paint before that
  // commits, so it stays quiet instead of flashing the toolbar/table chrome
  // first. If the project can't resolve, soloHref stays null and this falls
  // through to the ordinary table render below rather than a dead screen.
  // Suppressed by ?new=1 (see suppressSoloRedirect above) so the wizard can
  // still open on a one-agent workspace.
  if (!loading && soloHref && !suppressSoloRedirect) {
    return (
      <main className="fleet-page-state">
        <div className="fleet-page-state-body">Opening {soloAgent?.label || "your agent"}…</div>
      </main>
    );
  }

  return (
    <main className="fleet-content fleet-content--with-panel">
      {/* MAN-145 title-dedup follow-up: this used to render "Agents" three
          times (tab strip, breadcrumb, and this block's own <h1>). The
          breadcrumb's current crumb IS the page's <h1> now (see
          Breadcrumbs.tsx) — it already carries the "· N" count the plain
          title never did. This block is gone, not replaced with a styled
          div: the heading role lives one layer up, it isn't lost. */}

      {/* U3-H: two rows, not one — top row is breadcrumb (with its count,
          see the useBreadcrumbBadge call above) + primary action only,
          portaled into the shell topbar. The view-control cluster is its
          OWN row below, under the topbar's existing divider (its
          border-bottom) — U3-E's "merge everything into one line" reading
          was wrong. FleetToolbar always renders here (even with 0 agents)
          so the Properties toggle stays reachable; filters/sort still hide
          themselves when there's nothing to filter/sort. */}
      {/* FILLED, 2026-08-01, EXCEPT WHEN THE LIST IS EMPTY (2026-08-13) — this
          was unconditionally filled, which meant a brand-new workspace
          rendered this header button AND FirstAgentEmpty's own centred
          "Create your first agent" filled at the same time: CLAUDE.md,
          "Two accent-filled buttons in one view is a bug." Same fix,
          same reasoning, as the project detail page's own header action
          (see that page's HeaderAction block) — the centred empty-state CTA
          wins the fill while the list is empty (a first-run empty state is
          the one moment its own big button IS the primary action); this
          button earns it back the moment the table holds a row, since it's
          the PERSISTENT primary action used every day past that point. */}
      <HeaderAction>
        <button
          type="button"
          className={`fleet-btn${agents.length === 0 ? " fleet-btn--accent" : " fleet-btn--accent-fill"}`}
          onClick={() => setWizardOpen(true)}
        >
          <span className="fleet-btn-plus">+</span>
          New agent
        </button>
      </HeaderAction>

      <div className="fleet-content-toolbar">
        {/* AgentViewOptions + FleetToolbar, pinned together at the row's
            right edge (see .fleet-agent-view-cluster in fleet-theme.css for
            why a wrapper is needed here rather than reusing the auto-margin
            rule the Tasks tab's mutually-exclusive pair relies on). The
            legacy "Sort by" dropdown inside FleetToolbar is only wired up
            while the flat, ungrouped table is what's actually on screen — it
            has nothing to act on once Board or a grouping is selected, and
            AgentViewOptions' own Ordering row takes over at that point (see
            that component's file header). Filters stay wired up always:
            project/status/channel narrow which agents are shown regardless
            of layout. */}
        <div className="fleet-agent-view-cluster">
          <AgentViewOptions options={viewOptions} onChange={updateViewOptions} />
          <FleetToolbar
            filters={agents.length > 0 ? filters : undefined}
            sortOptions={agents.length > 0 && surface === "list" ? SORT_OPTIONS : undefined}
            sortValue={sort}
            sortDefault="last_active"
            onSortChange={(v) => updateFilters({ sort: v as SortMode })}
            panelOpen={panelOpen}
            onTogglePanel={() => setPanelOpen((v) => !v)}
            usageWorkspaceId={workspaceId}
          />
        </div>
      </div>

      {/* The sheet — full width always, whether the drawer below is open or
          closed. The page shows agents; everything else is behind the
          toggle above, not a permanent strip competing with the list. */}
      <div className="fleet-content-with-panel">
        <div className={`fleet-content-main${surface === "board" ? " fleet-content-main--agent-board" : ""}`}>
          {loading && agents.length === 0 ? (
            // Which skeleton to show is decided by the SAME `surface` the
            // real branches below switch on — a saved board/grouped view
            // option can already be active on first paint (see the two
            // skeletons' own doc comments), so a flat list here would be
            // wrong for those. rowHeight on the list-surface fallback
            // matches .fleet-agent-row's real min-height (52px) — see
            // FleetListSkeleton's MAN-113 note.
            surface === "board" ? (
              <AgentsBoardSkeleton />
            ) : surface === "grouped" ? (
              <AgentsGroupedSkeleton />
            ) : (
              <FleetListSkeleton rows={6} rowHeight={52} />
            )
          ) : error && agents.length === 0 ? (
            <FleetSurfaceError title="Couldn’t load agents" message={error} onRetry={refresh} />
          ) : agents.length === 0 ? (
            <FirstAgentEmpty
              title="No agents yet"
              desc="Agents do the work — they handle customer chats, run tasks, and use your tools. Create your first one to get started."
              onCreate={() => setWizardOpen(true)}
            />
          ) : surface === "board" ? (
            filtered.length === 0 ? (
              <div className="fleet-page-state-body">No agents match these filters.</div>
            ) : (
              <AgentsBoard
                agents={orderedForNewSurfaces}
                gateways={gateways}
                costByAgent={cost}
                display={viewOptions.display}
                onSelect={goToAgent}
              />
            )
          ) : surface === "grouped" ? (
            filtered.length === 0 ? (
              <div className="fleet-page-state-body">No agents match these filters.</div>
            ) : (
              <AgentsGroupedList
                workspaceId={workspaceId}
                agents={orderedForNewSurfaces}
                gateways={gateways}
                costByAgent={cost}
                projectById={projById}
                grouping={viewOptions.grouping as Exclude<typeof viewOptions.grouping, "none">}
                display={viewOptions.display}
                onSelect={goToAgent}
              />
            )
          ) : shown.length === 0 ? (
            <div className="fleet-page-state-body">No agents match these filters.</div>
          ) : (
            <AgentsList
              workspaceId={workspaceId}
              agents={shown}
              costByAgent={cost}
              projectById={projById}
              groupByProject={sort === "group"}
              onSelect={goToAgent}
              onAgentStoppedChanged={refresh}
            />
          )}
        </div>

        <FleetRightPanel open={panelOpen} onClose={() => setPanelOpen(false)}>
          <PanelSection title="Properties">
            <PanelRow label="Total agents" value={agents.length} icon={<Bot size={15} strokeWidth={1.75} />} />
            <PanelRow label="Online" value={`${activeCount}/${agents.length}`} tone={activeCount > 0 ? "online" : "muted"} />
            <UsageStat
              label="Spend today"
              total={spendToday}
              formattedTotal={`$${spendToday.toFixed(2)}`}
              values={bucketSeries(usageBuckets, "usd_cost")}
            />
            <UsageStat
              label="Tokens today"
              total={usageTotals?.total_tokens || 0}
              formattedTotal={fmtTokens(usageTotals?.total_tokens || 0)}
              values={bucketSeries(usageBuckets, "total_tokens")}
            />
            <PanelRow label="Channels live" value={channelsLiveCount} icon={<Radio size={15} strokeWidth={1.75} />} tone={channelsLiveCount > 0 ? "default" : "muted"} />
          </PanelSection>
        </FleetRightPanel>
      </div>

      {wizardOpen && (
        <FleetCreateAgentWizard
          workspaceId={workspaceId}
          onClose={() => setWizardOpen(false)}
          onCreated={() => {
            setWizardOpen(false);
            refresh();
          }}
        />
      )}
    </main>
  );
}

// Compact absolute-token formatter — 47 → "47", 12_500 → "12.5K", 1_240_000 →
// "1.2M". Keeps the properties-panel row short.
function fmtTokens(n: number): string {
  if (!n || n <= 0) return "0";
  if (n < 1000) return String(Math.round(n));
  if (n < 1_000_000) return `${(n / 1000).toFixed(n < 10_000 ? 1 : 0)}K`;
  return `${(n / 1_000_000).toFixed(1)}M`;
}

function sortAgents(agents: FleetAgent[], sort: SortMode, cost: Map<string, number>): FleetAgent[] {
  const list = [...agents];
  if (sort === "name") {
    list.sort((a, b) => (a.label || "").localeCompare(b.label || ""));
  } else if (sort === "status") {
    list.sort((a, b) => (STATUS_RANK[a.hardware_status] ?? 1) - (STATUS_RANK[b.hardware_status] ?? 1));
  } else if (sort === "cost") {
    list.sort((a, b) => (cost.get(b.agent_id) || 0) - (cost.get(a.agent_id) || 0));
  } else if (sort === "last_active") {
    list.sort((a, b) => {
      const ta = a.last_activity ? new Date(a.last_activity).getTime() : 0;
      const tb = b.last_activity ? new Date(b.last_activity).getTime() : 0;
      return tb - ta;
    });
  } else if (sort === "group") {
    list.sort((a, b) => (a.label || "").localeCompare(b.label || ""));
  }
  return list;
}
