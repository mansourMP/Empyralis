"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { useParams, useRouter } from "next/navigation";

import { Bot, Calendar, Zap } from "lucide-react";

import {
  useFleetAgents,
  useFleetProjects,
  useFleetTasks,
  assignFleetTask,
  assignFleetTaskToUser,
  patchFleetTask,
  type FleetAgent,
  type FleetTaskStatus,
  type TaskAssigneeSelection,
} from "@/lib/workspace/fleet/fleet-data";
import { useWorkspaceMembers } from "@/lib/workspace/fleet/members-data";
import { TasksList } from "@/lib/workspace/fleet/TasksList";
import { TasksBoard } from "@/lib/workspace/fleet/TasksBoard";
import { TasksGroupedList } from "@/lib/workspace/fleet/TasksGroupedList";
import { TaskComposer } from "@/lib/workspace/fleet/TaskComposer";
import { ProjectOverview } from "@/lib/workspace/fleet/ProjectOverview";
import { MemberAvatarStack } from "@/lib/workspace/fleet/MemberAvatarStack";
import { useBreadcrumbLabel, useBreadcrumbIcon, useBreadcrumbBadge, HeaderAction } from "@/lib/workspace/fleet/Breadcrumbs";
import { breadcrumbCount, formatDate, formatNumber } from "@/lib/workspace/fleet/fleet-presentation";
import { ProjectIcon } from "@/lib/workspace/fleet/fleet-project-identity";
import { UsageStat, bucketSeries, type UsageBucket } from "@/lib/workspace/fleet/fleet-sparkline";
import { AgentsList, rememberLastViewedAgent } from "@/lib/workspace/fleet/AgentsList";
import { FleetToolbar, type ToolbarFilter } from "@/lib/workspace/fleet/FleetToolbar";
import { FleetRightPanel, PanelSection, PanelRow, PanelRowsSkeleton } from "@/lib/workspace/fleet/FleetRightPanel";
import { FleetCreateAgentWizard } from "@/lib/workspace/fleet/FleetCreateAgentWizard";
import { FirstAgentEmpty } from "@/lib/workspace/fleet/first-agent-empty";
import { FleetListSkeleton } from "@/lib/workspace/fleet/fleet-states";
import { ListChecks } from "lucide-react";

const money = (n: number | undefined) => `$${(n ?? 0).toFixed(4)}`;

type SortMode = "last_active" | "status" | "cost" | "name";
const STATUS_RANK: Record<string, number> = { online: 0, unknown: 1, offline: 2 };
const SORT_OPTIONS = [
  { value: "last_active", label: "Last active" },
  { value: "status", label: "Status" },
  { value: "cost", label: "Cost" },
  { value: "name", label: "Name" },
];

type FilterState = { status: string; channel: string; sort: SortMode };

// Same URL-backed view state as the flat agents list (agents/page.tsx) — so
// leaving for an agent's detail and returning (including Esc-to-return's
// browser-back) restores this project's filtered/sorted view too.
function readFiltersFromLocation(): FilterState {
  if (typeof window === "undefined") return { status: "all", channel: "all", sort: "last_active" };
  const sp = new URLSearchParams(window.location.search);
  return {
    status: sp.get("status") || "all",
    channel: sp.get("channel") || "all",
    sort: (sp.get("sort") as SortMode) || "last_active",
  };
}

export default function ProjectDetailPage() {
  const params = useParams();
  const router = useRouter();
  const workspaceId = String(params?.workspaceId || "");
  const projectId = String(params?.projectId || "");
  const base = `/w/${encodeURIComponent(workspaceId)}`;

  const { agents, loading, refresh } = useFleetAgents(workspaceId);
  // MAN-64/MAN-70: the pool of valid HUMAN assignees -- the same hook
  // MemberAvatarStack already calls for this page's own roster stack, no
  // new endpoint involved (GET /workspaces/{id}/members).
  const { members } = useWorkspaceMembers(workspaceId);
  const { projects } = useFleetProjects(workspaceId);
  const project = projects.find((p) => p.id === projectId);
  useBreadcrumbLabel(projectId, project?.name);
  useBreadcrumbIcon(
    projectId,
    useMemo(
      () => (project ? <ProjectIcon icon={project.icon} tint={project.tint} size={16} /> : null),
      [project],
    ),
  );

  const [filterState, setFilterState] = useState<FilterState>(() => readFiltersFromLocation());
  const { status: statusFilter, channel: channelFilter, sort } = filterState;
  const projectBase = `${base}/projects/${encodeURIComponent(projectId)}`;

  const updateFilters = useCallback((patch: Partial<FilterState>) => {
    setFilterState((prev) => {
      const next = { ...prev, ...patch };
      const sp = new URLSearchParams();
      if (next.status !== "all") sp.set("status", next.status);
      if (next.channel !== "all") sp.set("channel", next.channel);
      if (next.sort !== "last_active") sp.set("sort", next.sort);
      const qs = sp.toString();
      router.replace(`${projectBase}${qs ? `?${qs}` : ""}`);
      return next;
    });
  }, [router, projectBase]);

  const [wizardOpen, setWizardOpen] = useState(false);
  // Properties drawer — closed by default, an overlay over the sheet.
  const [panelOpen, setPanelOpen] = useState(false);
  // Overview | Agents | Tasks. Overview (MAN-110 Phase 1) is the landing
  // summary — status roll-up + real activity feed; Agents/Tasks are the two
  // working views: who is in the project, and what they are working on.
  const [view, setView] = useState<"overview" | "agents" | "tasks">("overview");
  // Three shapes of the same tasks, and they answer different questions —
  // see TasksGroupedList's header. Board is the default (watch the work);
  // Grouped is the one you work THROUGH; List is the flat table that shows
  // every field at once. Board stays the default because it is what the
  // project page has always opened on.
  const [taskLayout, setTaskLayout] = useState<"board" | "grouped" | "list">("board");
  // The task composer (MAN-127). Held as "which status does it open on" rather
  // than a bare boolean, because a board column's `+` opens it pre-set to that
  // column — `null` is closed, an object is open.
  const [composer, setComposer] = useState<{ status?: FleetTaskStatus } | null>(null);
  // A failed wake is reported here rather than swallowed: assigning fires a
  // wake so the agent actually starts, and a silent wake failure would read
  // as "assigned, working" when nothing is running.
  const [taskNotice, setTaskNotice] = useState<string | null>(null);
  const { tasks, loading: tasksLoading, refresh: refreshTasks } = useFleetTasks(workspaceId, projectId);
  // Statuses written but not yet confirmed by a refetch — see
  // handleStatusChange. Empty in the steady state, so this is a no-op merge
  // except for the few hundred ms a PATCH is in flight.
  const [pendingStatus, setPendingStatus] = useState<Map<string, FleetTaskStatus>>(new Map());
  const boardTasks = useMemo(
    () => (pendingStatus.size === 0
      ? tasks
      : tasks.map((t) => (pendingStatus.has(t.id) ? { ...t, status: pendingStatus.get(t.id)! } : t))),
    [tasks, pendingStatus],
  );
  const [rollup, setRollup] = useState<{ usd_cost: number; total_tokens: number; events: number } | null>(null);
  const [costBuckets, setCostBuckets] = useState<UsageBucket[]>([]);
  const [cost, setCost] = useState<Map<string, number>>(new Map());

  useEffect(() => {
    let cancelled = false;
    // period=day, not month: `totals` is an all-time scope aggregate either
    // way (period only controls how `buckets` are grouped) — day gives the
    // daily granularity the cost sparkline needs; month would collapse to
    // one point.
    fetch(`${base.replace("/w/", "/api/w/")}/fleet/usage?scope=project&id=${encodeURIComponent(projectId)}&period=day`, { credentials: "include" })
      .then((r) => r.json())
      .then((d) => {
        if (cancelled) return;
        if (d?.totals) setRollup(d.totals);
        if (Array.isArray(d?.buckets)) setCostBuckets(d.buckets);
      })
      .catch(() => {});
    return () => { cancelled = true; };
  }, [base, projectId]);

  useEffect(() => {
    let cancelled = false;
    fetch(`/api/w/${encodeURIComponent(workspaceId)}/fleet/usage?scope=workspace&period=day`, { credentials: "include" })
      .then((r) => r.json())
      .then((d) => {
        if (cancelled) return;
        const m = new Map<string, number>();
        for (const a of d?.by_agent || []) m.set(a.agent_install_id, a.usd_cost);
        setCost(m);
      })
      .catch(() => {});
    return () => { cancelled = true; };
  }, [workspaceId]);

  const inProject = agents.filter((a) => (a.project_id || "").trim() === projectId);
  // U3-E: the count lives on the breadcrumb line itself ("General · 3
  // agents"), not a second toolbar row — the project name appears exactly
  // once, in the crumb this badge attaches to.
  useBreadcrumbBadge(
    projectId,
    useMemo(
      () => <span className="fleet-breadcrumb-count">· {breadcrumbCount(inProject.length, "agent", "agents", project?.name || "")}</span>,
      [inProject.length, project?.name],
    ),
  );
  const filtered = useMemo(() => inProject.filter((a) => {
    if (statusFilter !== "all" && (a.hardware_status || "unknown") !== statusFilter) return false;
    if (channelFilter === "connected" && !a.channel) return false;
    if (channelFilter === "none" && a.channel) return false;
    return true;
  }), [inProject, statusFilter, channelFilter]);
  const shown = useMemo(() => sortAgents(filtered, sort, cost), [filtered, sort, cost]);

  const costByAgent = useMemo(
    () => inProject
      .map((a) => ({ id: a.agent_id, label: a.label || "Unnamed agent", cost: cost.get(a.agent_id) || 0 }))
      .sort((a, b) => b.cost - a.cost),
    [inProject, cost],
  );

  const filters: ToolbarFilter[] = [
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

  // Same two-uses-one-definition split as taskHref below: `router.push` on a
  // plain click, and stamped on each row as `data-tab-href` so ⌘/Ctrl+click
  // and middle-click open a background content tab (see FleetTabs).
  const agentHref = (agentId: string) =>
    `${projectBase}/agents/${encodeURIComponent(agentId)}/overview`;

  const goToAgent = (agentId: string) => {
    rememberLastViewedAgent(agentId);
    router.push(agentHref(agentId));
  };

  // MAN-64/MAN-70: assignee is agent-or-human -- dispatch to whichever of
  // assignFleetTask/assignFleetTaskToUser matches the picker's selection.
  // Only the agent path can ever report a wake failure (assigning a human
  // never schedules a wakeup at all), so `wakeError` only ever comes back
  // non-null from that branch.
  const handleAssign = async (taskId: string, selection: TaskAssigneeSelection) => {
    setTaskNotice(null);
    try {
      if (selection.kind === "agent") {
        const { wakeError } = await assignFleetTask(workspaceId, taskId, selection.id);
        if (wakeError) {
          setTaskNotice(
            `Assigned, but the agent could not be woken: ${wakeError}. It will not start until it is running.`
          );
        }
      } else {
        await assignFleetTaskToUser(workspaceId, taskId, selection.id);
      }
      await refreshTasks();
    } catch (e) {
      setTaskNotice(e instanceof Error ? e.message : "Could not assign this task.");
    }
  };

  // The first place in this UI a HUMAN can move a task. patchFleetTask has
  // existed in fleet-data.ts since the tasks backend landed and had zero
  // callers anywhere — until this, only an agent calling project_task__update
  // could change a status, which is why ProjectOverview's own caption says
  // review attribution isn't a real code path yet.
  const handleStatusChange = useCallback(async (taskId: string, status: FleetTaskStatus) => {
    setTaskNotice(null);
    // Paint the move immediately and reconcile from the server right after:
    // tasks are polled on a 30s timer, so without this the card would sit in
    // its old column for a beat after a drag and read as a failed drop.
    setPendingStatus((prev) => new Map(prev).set(taskId, status));
    try {
      await patchFleetTask(workspaceId, taskId, { status });
      await refreshTasks();
    } catch (e) {
      setTaskNotice(e instanceof Error ? e.message : "Could not update this task.");
    } finally {
      setPendingStatus((prev) => {
        const next = new Map(prev);
        next.delete(taskId);
        return next;
      });
    }
  }, [workspaceId, refreshTasks]);

  // A task is a PAGE now, not a drawer over this board (MAN-11x): it has its
  // own route, so it can be deep-linked, ⌘-clicked into a background content
  // tab, and reached by browser back/forward. taskHref is handed to the board
  // and the list so each card/row also carries it as `data-tab-href` — that
  // attribute is what the tab layer reads for modifier clicks.
  const taskHref = useCallback(
    (taskId: string) => `${projectBase}/tasks/${encodeURIComponent(taskId)}`,
    [projectBase],
  );
  const openTask = useCallback((taskId: string) => {
    router.push(taskHref(taskId));
  }, [router, taskHref]);

  return (
    <main className="fleet-content fleet-content--with-panel">
      {/* MAN-145 title-dedup follow-up: this used to render the project's
          name three times (tab strip, breadcrumb, and this block's own
          shared <h1> above the Overview/Agents/Tasks tabs). The breadcrumb's
          current crumb IS the page's <h1> now, on all three tabs (see
          Breadcrumbs.tsx) — it already carries the project's own icon
          (useBreadcrumbIcon above) and its "· N agents" count
          (useBreadcrumbBadge above), context a plain title never had. This
          block is gone, not replaced with a styled div: the heading role
          lives one layer up, it isn't lost. */}

      {/* U3-H: top row is breadcrumb + primary action only; the
          view-control cluster is its own row below, under the topbar's
          existing divider. FleetToolbar always renders here (even with 0
          agents) so the Properties toggle stays reachable; filters/sort
          still hide themselves when there's nothing to filter/sort (each is
          independently optional). */}
      {/* MAN-145: one accent-FILL per view, never two. Each of these views can
          also show its own empty state (FirstAgentEmpty's centre "Create your
          first agent" for Agents; the Tasks empty state's centre "+ New task"
          below) with a filled button of its own — that's the real call to
          action when there's nothing else on screen. So the header action
          here stays the quiet .fleet-btn--accent hairline (same restrained
          treatment every other app-wide primary action uses), never the
          saturated fill, so the two are never both shouting at once. */}
      <HeaderAction>
        {view === "agents" ? (
          <button type="button" className="fleet-btn fleet-btn--accent" onClick={() => setWizardOpen(true)}>
            <span className="fleet-btn-plus">+</span> New agent
          </button>
        ) : view === "tasks" ? (
          <button type="button" className="fleet-btn fleet-btn--accent" onClick={() => setComposer({})}>
            <span className="fleet-btn-plus">+</span> New task
          </button>
        ) : null}
      </HeaderAction>

      {/* The view switch lives in the content toolbar, not the topbar — the
          topbar is where the earlier mobile header-overlap bug came from, and
          this row is already proven reachable at 375px. */}
      <div className="fleet-content-toolbar">
        {/* Top-LEFT of the control row, not a row of its own above the view.
            "project member" == "workspace member" for now (MAN-70 ruling, no
            per-project ACL table yet), so this pulls the workspace's member
            list. `tasks` is passed through only so the hover tooltip can
            surface real per-member attribution (tasks they created in this
            project) — never fetched independently.
            It used to be the first child of .fleet-content-main, which cost
            the board a whole 44px band of dead space between the tab strip
            and the first card. The toolbar row's left half was empty anyway
            and is where Linear puts exactly this. */}
        <MemberAvatarStack workspaceId={workspaceId} tasks={tasks} />
        <div className="fleet-segmented" role="tablist" aria-label="Project view">
          {(["overview", "agents", "tasks"] as const).map((v) => (
            <button
              key={v}
              type="button"
              role="tab"
              aria-selected={view === v}
              className={`fleet-segmented-btn${view === v ? " fleet-segmented-btn--active" : ""}`}
              onClick={() => setView(v)}
            >
              {v === "overview" ? "Overview" : v === "agents" ? "Agents" : "Tasks"}
            </button>
          ))}
        </div>
        {view === "tasks" && tasks.length > 0 ? (
          <div className="fleet-segmented" role="tablist" aria-label="Task layout">
            {(["board", "grouped", "list"] as const).map((v) => (
              <button
                key={v}
                type="button"
                role="tab"
                aria-selected={taskLayout === v}
                className={`fleet-segmented-btn${taskLayout === v ? " fleet-segmented-btn--active" : ""}`}
                onClick={() => setTaskLayout(v)}
              >
                {v === "board" ? "Board" : v === "grouped" ? "Grouped" : "List"}
              </button>
            ))}
          </div>
        ) : null}
        {view === "agents" ? (
          <FleetToolbar
            filters={inProject.length > 0 ? filters : undefined}
            sortOptions={inProject.length > 0 ? SORT_OPTIONS : undefined}
            sortValue={sort}
            sortDefault="last_active"
            onSortChange={(v) => updateFilters({ sort: v as SortMode })}
            panelOpen={panelOpen}
            onTogglePanel={() => setPanelOpen((v) => !v)}
            usageHref={`${base}/billing`}
          />
        ) : null}
      </div>

      {/* The sheet — full width always, whether the drawer below is open or
          closed. fleet-content-with-panel is just the relative anchor the
          drawer overlays against; it is no longer a flex row splitting width
          with a permanent sibling. */}
      <div className="fleet-content-with-panel">
        {/* The board is the one view that must own its own vertical scroll
            (columns scroll, the page does not), so the sheet becomes a flex
            column just for it — a modifier rather than a height calc, so
            nothing here has to hard-code how tall the chrome above it is. */}
        <div className={`fleet-content-main${view === "tasks" && taskLayout === "board" ? " fleet-content-main--board" : ""}`}>
          {taskNotice ? (
            <div className="fleet-page-state-body" role="alert" style={{ color: "var(--warning-text)" }}>
              {taskNotice}
            </div>
          ) : null}

          {view === "overview" ? (
            <ProjectOverview
              workspaceId={workspaceId}
              agents={inProject}
              tasks={tasks}
              tasksLoading={tasksLoading}
              rollup={rollup}
              members={members}
              taskHref={taskHref}
              agentHref={agentHref}
            />
          ) : view === "tasks" ? (
            tasksLoading && tasks.length === 0 ? (
              // rowHeight matches .fleet-task-row's real min-height (52px) —
              // see FleetListSkeleton's MAN-113 note; an un-pinned skeleton
              // row snaps taller the moment TasksList swaps in.
              <FleetListSkeleton rows={4} rowHeight={52} />
            ) : tasks.length === 0 ? (
              <div className="fleet-empty">
                <div className="fleet-empty-icon">
                  <ListChecks size={20} strokeWidth={1.75} />
                </div>
                <div className="fleet-empty-title">No tasks yet</div>
                <div className="fleet-empty-desc">
                  Tasks live inside this project and can be assigned to an agent or a person to work on.
                </div>
                <div className="fleet-empty-actions">
                  <button type="button" className="fleet-btn fleet-btn--accent-fill" onClick={() => setComposer({})}>
                    <span className="fleet-btn-plus">+</span> New task
                  </button>
                </div>
              </div>
            ) : taskLayout === "board" ? (
              <TasksBoard
                workspaceId={workspaceId}
                tasks={boardTasks}
                agents={inProject}
                members={members}
                taskHref={taskHref}
                onSelect={openTask}
                onStatusChange={handleStatusChange}
                onCreateTask={(status) => setComposer({ status })}
              />
            ) : taskLayout === "grouped" ? (
              <TasksGroupedList
                workspaceId={workspaceId}
                tasks={boardTasks}
                agents={inProject}
                members={members}
                taskHref={taskHref}
                onSelect={openTask}
                onStatusChange={handleStatusChange}
                onCreateTask={(status) => setComposer({ status })}
              />
            ) : (
              <TasksList
                tasks={boardTasks}
                agents={inProject}
                members={members}
                taskHref={taskHref}
                onAssign={handleAssign}
                onSelect={openTask}
              />
            )
          ) : loading && inProject.length === 0 ? (
            // rowHeight matches .fleet-agent-row's real min-height (52px) —
            // see FleetListSkeleton's MAN-113 note; an un-pinned skeleton row
            // snaps taller the moment AgentsList swaps in.
            <FleetListSkeleton rows={4} rowHeight={52} />
          ) : inProject.length === 0 ? (
            <FirstAgentEmpty
              title="No agents in this project"
              desc="Create one — it’ll be assigned here."
              onCreate={() => setWizardOpen(true)}
            />
          ) : shown.length === 0 ? (
            <div className="fleet-page-state-body">No agents match these filters.</div>
          ) : (
            <AgentsList workspaceId={workspaceId} agents={shown} costByAgent={cost} agentHref={agentHref} onSelect={goToAgent} onAgentStoppedChanged={refresh} />
          )}
        </div>

        <FleetRightPanel open={panelOpen} onClose={() => setPanelOpen(false)}>
          <PanelSection title="Properties">
            {project?.description && <PanelRow label="Description" value={project.description} />}
            <UsageStat
              label="Cost this month"
              total={rollup?.usd_cost ?? 0}
              formattedTotal={money(rollup?.usd_cost)}
              values={bucketSeries(costBuckets, "usd_cost")}
            />
            <UsageStat
              label="Tokens"
              total={rollup?.total_tokens ?? 0}
              formattedTotal={formatNumber(rollup?.total_tokens ?? 0)}
              values={bucketSeries(costBuckets, "total_tokens")}
            />
            <PanelRow label="LLM calls" value={formatNumber(rollup?.events ?? 0)} icon={<Zap size={15} strokeWidth={1.75} />} />
            <PanelRow label="Agents" value={inProject.length} icon={<Bot size={15} strokeWidth={1.75} />} />
            <PanelRow label="Created" value={project?.created_at ? formatDate(project.created_at) : "—"} icon={<Calendar size={15} strokeWidth={1.75} />} tone={project?.created_at ? "default" : "muted"} />
          </PanelSection>

          <PanelSection title="Cost by agent">
            {loading && agents.length === 0 ? (
              // The rows here are one-per-agent, so this section's depth isn't
              // known until the agents fetch lands. Reserve it instead of
              // showing "No agents yet." — that line is both untrue mid-fetch
              // and shorter than the rows it gets replaced by, so the drawer
              // grew under the reader as the list arrived.
              <PanelRowsSkeleton rows={3} />
            ) : costByAgent.length === 0 ? (
              <div className="fleet-panel-empty">No agents yet.</div>
            ) : (
              costByAgent.map((a) => (
                <PanelRow
                  key={a.id}
                  label={a.label}
                  // A neutral bullet, not the per-agent identity hue this used
                  // to render (TINTS[tintKeyForIndex(i)]) — this is a plain
                  // list of PanelRows, each already labelled by name, not a
                  // chart with a legend to key against. Unlike the Usage page
                  // (billing/page.tsx), there's no colored line here for the
                  // dot to match, so the hue was pure decoration.
                  icon={<span className="fleet-tint-pip" />}
                  value={a.cost > 0 ? money(a.cost) : "—"}
                  tone={a.cost > 0 ? "default" : "muted"}
                />
              ))
            )}
          </PanelSection>

        </FleetRightPanel>
      </div>

      {composer && (
        <TaskComposer
          workspaceId={workspaceId}
          projectId={projectId}
          projectName={project?.name}
          agents={inProject}
          members={members}
          initialStatus={composer.status}
          onClose={() => setComposer(null)}
          // Stays open when "Create more" is on — the composer decides that,
          // not this page, so this handler only refreshes and reports.
          onCreated={(notice) => { setTaskNotice(notice); refreshTasks(); }}
        />
      )}

      {wizardOpen && (
        <FleetCreateAgentWizard
          workspaceId={workspaceId}
          initialProjectId={projectId}
          onClose={() => setWizardOpen(false)}
          onCreated={() => { setWizardOpen(false); refresh(); }}
        />
      )}
    </main>
  );
}

function sortAgents(agents: FleetAgent[], sort: SortMode, cost: Map<string, number>): FleetAgent[] {
  const list = [...agents];
  if (sort === "name") {
    list.sort((a, b) => (a.label || "").localeCompare(b.label || ""));
  } else if (sort === "status") {
    list.sort((a, b) => (STATUS_RANK[a.hardware_status] ?? 1) - (STATUS_RANK[b.hardware_status] ?? 1));
  } else if (sort === "cost") {
    list.sort((a, b) => (cost.get(b.agent_id) || 0) - (cost.get(a.agent_id) || 0));
  } else {
    list.sort((a, b) => {
      const ta = a.last_activity ? new Date(a.last_activity).getTime() : 0;
      const tb = b.last_activity ? new Date(b.last_activity).getTime() : 0;
      return tb - ta;
    });
  }
  return list;
}
