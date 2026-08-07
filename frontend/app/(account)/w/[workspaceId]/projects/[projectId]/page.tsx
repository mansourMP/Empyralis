"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { useParams, usePathname, useRouter } from "next/navigation";

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
import { useOwnAccountId, useOwnWorkspaceRole, useWorkspaceMembers } from "@/lib/workspace/fleet/members-data";
import { deriveCanWriteProject, useProjectMembers } from "@/lib/workspace/fleet/project-members-data";
import { TasksList } from "@/lib/workspace/fleet/TasksList";
import { TasksBoard } from "@/lib/workspace/fleet/TasksBoard";
import { TasksGroupedList } from "@/lib/workspace/fleet/TasksGroupedList";
import { TaskComposer } from "@/lib/workspace/fleet/TaskComposer";
import { useFleetDocuments } from "@/lib/workspace/fleet/documents-data";
import { DocumentsList } from "@/lib/workspace/fleet/DocumentsList";
import { DocumentComposer } from "@/lib/workspace/fleet/DocumentComposer";
import { MemberAvatarStack } from "@/lib/workspace/fleet/MemberAvatarStack";
import { ProjectMemberAdd } from "@/lib/workspace/fleet/ProjectMemberAdd";
import { ProjectSettings } from "@/lib/workspace/fleet/ProjectSettings";
import { useBreadcrumbLabel, useBreadcrumbIcon, useBreadcrumbBadge, HeaderAction } from "@/lib/workspace/fleet/Breadcrumbs";
import { breadcrumbCount, formatDate, formatNumber } from "@/lib/workspace/fleet/fleet-presentation";
import { ProjectIcon } from "@/lib/workspace/fleet/fleet-project-identity";
import { UsageStat, bucketSeries, type UsageBucket } from "@/lib/workspace/fleet/fleet-sparkline";
import { AgentsList, rememberLastViewedAgent } from "@/lib/workspace/fleet/AgentsList";
import { FleetToolbar, type ToolbarFilter } from "@/lib/workspace/fleet/FleetToolbar";
import { TaskViewOptions } from "@/lib/workspace/fleet/TaskViewOptions";
import {
  DEFAULT_TASK_VIEW_OPTIONS,
  readTaskViewOptions,
  sortTasks,
  writeTaskViewOptions,
  type TaskViewOptions as TaskViewOptionsState,
} from "@/lib/workspace/fleet/task-view-options";
import { FleetRightPanel, PanelSection, PanelRow, PanelRowsSkeleton } from "@/lib/workspace/fleet/FleetRightPanel";
import { FleetCreateAgentWizard } from "@/lib/workspace/fleet/FleetCreateAgentWizard";
import { FirstAgentEmpty } from "@/lib/workspace/fleet/first-agent-empty";
import { FleetListSkeleton } from "@/lib/workspace/fleet/fleet-states";
import { ListChecks, FileText } from "lucide-react";

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
  // Drives the Agents/Tasks/Documents derivation below — see the `view`
  // const near updateFilters/projectBase.
  const pathname = usePathname() || "";
  const workspaceId = String(params?.workspaceId || "");
  const projectId = String(params?.projectId || "");
  const base = `/w/${encodeURIComponent(workspaceId)}`;

  const { agents, loading, refresh } = useFleetAgents(workspaceId);
  // MAN-64/MAN-70: the pool of valid HUMAN assignees -- the same list
  // MemberAvatarStack renders for this page's own roster stack, passed down
  // as a prop rather than fetched a second time there (GET
  // /workspaces/{id}/members) -- see MemberAvatarStack.tsx's own doc
  // comment on why that used to be a redundant round trip.
  const { members, loading: membersLoading } = useWorkspaceMembers(workspaceId);
  // This project's own project_memberships rows -- likewise the ONE fetch
  // of GET /fleet/projects/{id}/members for this page, shared by
  // deriveCanWriteProject below and passed down to ProjectMemberAdd. Used
  // to be two independent useProjectMembers calls (this page's write gate,
  // ProjectMemberAdd's own) hitting the same endpoint on every load --
  // browser-measured ~250-400ms each, doubling both requests and backend
  // load for no reason.
  const { members: projectMembers, loading: projectMembersLoading, refresh: refreshProjectMembers } =
    useProjectMembers(workspaceId, projectId);
  const { projects, refresh: refreshProjects } = useFleetProjects(workspaceId);
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
      // The CURRENT pathname, not a hardcoded route: these filters only
      // ever apply to the Agents view (see the `filters`/sortOptions wiring
      // below), which lives at `${projectBase}/agents` — a real route, not
      // client state (see the `view` const below). Replacing with
      // `projectBase` would silently bounce the reader onto the default
      // (Tasks) view every time they touched a filter.
      router.replace(`${pathname}${qs ? `?${qs}` : ""}`);
      return next;
    });
  }, [router, pathname]);

  const [wizardOpen, setWizardOpen] = useState(false);
  // Properties drawer — closed by default, an overlay over the sheet.
  const [panelOpen, setPanelOpen] = useState(false);
  // Agents | Tasks | Documents — a real ROUTE per view (`${projectBase}/agents`,
  // `${projectBase}/tasks`, `${projectBase}/documents`), not component state.
  //
  // It used to be `useState`, which is exactly why the browser's own back
  // button used to strand a reader on one view after opening a task from
  // Tasks: state lives only as long as this component instance, and
  // navigating to a task's own page (a different route) unmounts it.
  // Nothing about which sub-view you were on survived that round trip,
  // because nothing about it was ever written down anywhere durable. A real
  // route fixes it directly: leaving for a task and pressing the browser's
  // own back button lands back on the exact view (Agents/Tasks/Documents)
  // you left, because that view is a genuine history entry now.
  //
  // Overview was REMOVED (founder's call, 2026-08-07 documents review): "I
  // don't need overview tab, agents/tasks/documents are already enough, no
  // point of having this overview shit." Three views, not four. Tasks is the
  // default landing view — the bare `${projectBase}` URL (what the project
  // list and the command palette both link to) falls through to it below —
  // because it's the surface a reader opens a project to act on daily; the
  // roster/activity Overview used to show is one click away on Agents.
  const view: "agents" | "tasks" | "documents" =
    pathname === `${projectBase}/agents`
      ? "agents"
      : pathname === `${projectBase}/documents`
        ? "documents"
        : "tasks";
  // router.replace, not .push — same choice the agent detail page's own
  // sub-tabs already made (AgentDetailPage's onTabChange, one directory up).
  // Collapses Agents→Tasks→Documents clicks onto one history entry, so the
  // browser's own back button steps out of the project in one press instead
  // of walking back through every sub-tab click first.
  const viewHref = (v: "agents" | "tasks" | "documents") => `${projectBase}/${v}`;
  // TWO shapes of the same tasks, plus the options that reshape them.
  //
  // This used to be a three-way switch — Board | Grouped | List — and that
  // was a modelling error: "Grouped" is not a third view, it is the List with
  // a grouping applied. Linear models exactly this (the view is List-or-Board;
  // grouping is an option), so 2026-08-01 the switch collapsed to Board | List
  // and grouping moved into the view-options popover beside it. Nothing was
  // deleted: TasksGroupedList is what the List renders whenever a grouping is
  // selected. Board stays the default because it is what this page has always
  // opened on.
  //
  // Hydrated from localStorage in an effect, never during render, so the
  // server's markup and the client's first paint agree — the same discipline
  // useFleetPreferences and the grouped list's collapse state already follow.
  const [viewOptions, setViewOptions] = useState<TaskViewOptionsState>(DEFAULT_TASK_VIEW_OPTIONS);
  useEffect(() => {
    if (workspaceId) setViewOptions(readTaskViewOptions(workspaceId));
  }, [workspaceId]);
  // Takes an UPDATER, not a value, so two changes landing in one React batch
  // can't both start from the same snapshot and drop one of them — see the
  // note on TaskViewOptions' own `onChange` prop. The write rides inside the
  // updater because that is the only place the resolved next value exists.
  const updateViewOptions = useCallback(
    (update: (prev: TaskViewOptionsState) => TaskViewOptionsState) => {
      setViewOptions((prev) => {
        const next = update(prev);
        writeTaskViewOptions(workspaceId, next);
        return next;
      });
    },
    [workspaceId],
  );
  // The task composer (MAN-127). Held as "which status does it open on" rather
  // than a bare boolean, because a board column's `+` opens it pre-set to that
  // column — `null` is closed, an object is open.
  const [composer, setComposer] = useState<{ status?: FleetTaskStatus } | null>(null);
  // A failed wake is reported here rather than swallowed: assigning fires a
  // wake so the agent actually starts, and a silent wake failure would read
  // as "assigned, working" when nothing is running.
  const [taskNotice, setTaskNotice] = useState<string | null>(null);
  const { tasks, loading: tasksLoading, refresh: refreshTasks } = useFleetTasks(workspaceId, projectId);
  // The Documents view (fourth top-level tab, founder's own call — see the
  // `view` const above). Only fetched with a real project_id: useFleetDocuments
  // itself no-ops without one, same guard useFleetTasks's own fetcher uses.
  const { documents, loading: documentsLoading, refresh: refreshDocuments } = useFleetDocuments(workspaceId, projectId);
  const [documentComposerOpen, setDocumentComposerOpen] = useState(false);
  // Write gate: create/edit/delete controls for a document render only when
  // this resolves `true` — `null` (still loading) and `false` (a viewer, or
  // a member with no project_memberships row here) both hide them outright,
  // never a disabled control (CLAUDE.md: no dead controls). See
  // project-members-data.ts's own doc comment for the exact policy this
  // mirrors (auth.enforce_project_access). Calls deriveCanWriteProject
  // directly with this page's own projectMembers fetch above, rather than
  // useCanWriteProject (which would run its own, redundant, useProjectMembers).
  const ownWorkspaceRole = useOwnWorkspaceRole(workspaceId);
  const myAccountId = useOwnAccountId();
  const canWriteProject = deriveCanWriteProject(ownWorkspaceRole, myAccountId, projectMembers, projectMembersLoading);
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
  // The reader's ordering, applied once here rather than in each of the three
  // renderings — the board filters this list into columns and the two list
  // shapes read it straight through, so all three agree on card order without
  // any of them owning a sort. The backend's own default (created_at DESC)
  // is what `created` + `desc` reproduces, so the untouched view is
  // byte-for-byte what it was.
  const orderedTasks = useMemo(
    () => sortTasks(boardTasks, viewOptions.ordering, viewOptions.direction),
    [boardTasks, viewOptions.ordering, viewOptions.direction],
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

  // Where an agent row goes on a plain click, via `router.push` in
  // goToAgent below.
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
  // could change a status, so review attribution isn't a real code path yet.
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
  // own route, so it can be deep-linked and reached by browser back/forward.
  const taskHref = useCallback(
    (taskId: string) => `${projectBase}/tasks/${encodeURIComponent(taskId)}`,
    [projectBase],
  );
  const openTask = useCallback((taskId: string) => {
    router.push(taskHref(taskId));
  }, [router, taskHref]);

  // Same real-route treatment as taskHref above — handed to DocumentsList,
  // which renders it as a real <Link> (cmd-click/middle-click work; see
  // that file's own header for why it does NOT copy TaskRow's onClick-div
  // pattern).
  const documentHref = useCallback(
    (documentId: string) => `${projectBase}/documents/${encodeURIComponent(documentId)}`,
    [projectBase],
  );

  return (
    <main className="fleet-content fleet-content--with-panel">
      {/* MAN-145 title-dedup follow-up: this used to render the project's
          name three times (tab strip, breadcrumb, and this block's own
          shared <h1> above the Agents/Tasks/Documents tabs). The breadcrumb's
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
      {/* FILLED, 2026-08-01. This used to be the quiet hairline, on the theory
          that the centre empty-state button ("Create your first agent") was
          the real CTA and two fills would shout at once. Wrong trade: the
          empty state is a first-run condition, while this button is the
          view's PERSISTENT primary action — so the hairline meant the action
          a customer uses every day looked secondary forever to avoid a clash
          that only exists on an empty project. fleet-theme.css's own
          .fleet-btn--accent-fill comment already named "New agent" / "New
          task" / "New project" as exactly the curated set that earns the
          fill; this call site simply never matched it. See the note in that
          stylesheet about the empty-state overlap that remains. */}
      <HeaderAction>
        {view === "agents" ? (
          <button type="button" className="fleet-btn fleet-btn--accent-fill" onClick={() => setWizardOpen(true)}>
            <span className="fleet-btn-plus">+</span> New agent
          </button>
        ) : view === "tasks" ? (
          <button type="button" className="fleet-btn fleet-btn--accent-fill" onClick={() => setComposer({})}>
            <span className="fleet-btn-plus">+</span> New task
          </button>
        ) : view === "documents" && canWriteProject ? (
          // Write-gated, unlike the Agents/Tasks buttons beside it — a
          // viewer here would open a dialog whose own Create call the
          // server rejects outright (fleet_create_document's `member`
          // floor). No dead controls (CLAUDE.md): the button simply isn't
          // in the DOM for a reader who can't use it, `null` (still
          // resolving) included.
          <button type="button" className="fleet-btn fleet-btn--accent-fill" onClick={() => setDocumentComposerOpen(true)}>
            <span className="fleet-btn-plus">+</span> New document
          </button>
        ) : null}
      </HeaderAction>

      {/* The view switch lives in the content toolbar, not the topbar — the
          topbar is where the earlier mobile header-overlap bug came from, and
          this row is already proven reachable at 375px. */}
      <div className="fleet-content-toolbar">
        <div className="fleet-segmented" role="tablist" aria-label="Project view">
          {(["agents", "tasks", "documents"] as const).map((v) => (
            <button
              key={v}
              type="button"
              role="tab"
              aria-selected={view === v}
              className={`fleet-segmented-btn${view === v ? " fleet-segmented-btn--active" : ""}`}
              onClick={() => router.replace(viewHref(v))}
            >
              {v === "agents" ? "Agents" : v === "tasks" ? "Tasks" : "Documents"}
            </button>
          ))}
        </div>
        {/* Trails the view/layout switches, LEFT of centre — the people on a
            project read as context for the view you are choosing, so they sit
            with those controls rather than out at the edge (founder's call
            2026-08-01; this has now been on both sides of the row).
            MemberAvatarStack still pulls the WORKSPACE's member list (MAN-70
            placeholder ruling) — a real per-project ACL table exists now
            (MAN-115, see project-members-data.ts) but switching this stack
            to it is a bigger call than fits here: it would also need to
            union in workspace owners, who see every project via a role
            bypass and never get an explicit row in that table. Left alone;
            see that file's own header and MemberAvatarStack.tsx's for the
            full note. `tasks` is passed through only so the hover tooltip
            can surface real per-member attribution (tasks they created in
            this project) — never fetched independently. It used to be the
            first child of .fleet-content-main, which cost the board a whole
            44px band of dead space between the tab strip and the first
            card; sharing the toolbar's line is what reclaimed that. */}
        <MemberAvatarStack members={members} loading={membersLoading} tasks={tasks} />
        {/* The "+" immediately right of the stack (ProjectMemberAdd.tsx).
            Unlike the stack beside it, this reads and writes the REAL
            project_memberships table (MAN-115) — add an existing workspace
            member, or invite someone new by email straight into this
            project. Renders nothing for a non-owner: both routes it calls
            are owner-only, server-enforced, so there is no disabled state
            to design for. */}
        <ProjectMemberAdd
          workspaceId={workspaceId}
          projectId={projectId}
          workspaceMembers={members}
          projectMembers={projectMembers}
          projectMembersLoading={projectMembersLoading}
          refreshProjectMembers={refreshProjectMembers}
        />
        {/* U3-K: the same toolbar slot, same owner-only gate — rename +
            default hardware. See ProjectSettings.tsx's own header for why
            this row (not the Agents-only Properties drawer) is this
            control's home. */}
        <ProjectSettings workspaceId={workspaceId} project={project} onChanged={refreshProjects} />
        {/* Far RIGHT (margin-left:auto in the stylesheet, on BOTH
            .fleet-toolbar-actions and .fleet-view-options — see
            fleet-theme.css). Every control in here acts on the right-hand
            side of the screen — the panel toggle opens the drawer there,
            filter/sort drops its popover there — so the cluster belongs at
            that edge, visually under "New task" in the topbar above.
            FIXED 2026-08-01: TaskViewOptions's root only ever carried
            .fleet-view-options, a class the margin-left:auto rule didn't
            select until now — despite moving into this JSX position earlier
            the same day, it was still rendering wherever it fell in normal
            flow (right after the member stack) instead of at the row's
            edge. See fleet-theme.css's own note beside the fixed rule. */}
        {view === "tasks" && tasks.length > 0 ? (
          <TaskViewOptions options={viewOptions} onChange={updateViewOptions} />
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
            usageWorkspaceId={workspaceId}
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
        <div className={`fleet-content-main${view === "tasks" && viewOptions.layout === "board" ? " fleet-content-main--board" : ""}`}>
          {taskNotice ? (
            <div className="fleet-page-state-body" role="alert" style={{ color: "var(--warning-text)" }}>
              {taskNotice}
            </div>
          ) : null}

          {view === "tasks" ? (
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
            ) : viewOptions.layout === "board" ? (
              // The board's columns are statuses, always — that is what makes
              // a drop mean "move this task to In review", so grouping is not
              // offered here (see task-view-options.ts). Its own rule that a
              // column exists if and only if it holds a task is enforced
              // inside TasksBoard and nothing on this page can override it.
              <TasksBoard
                workspaceId={workspaceId}
                tasks={orderedTasks}
                agents={inProject}
                members={members}
                display={viewOptions.display}
                onSelect={openTask}
                onStatusChange={handleStatusChange}
                onCreateTask={(status) => setComposer({ status })}
              />
            ) : viewOptions.grouping !== "none" ? (
              <TasksGroupedList
                workspaceId={workspaceId}
                tasks={orderedTasks}
                agents={inProject}
                members={members}
                grouping={viewOptions.grouping}
                display={viewOptions.display}
                onSelect={openTask}
                onStatusChange={handleStatusChange}
                onCreateTask={(status) => setComposer({ status })}
              />
            ) : (
              <TasksList
                tasks={orderedTasks}
                agents={inProject}
                members={members}
                display={viewOptions.display}
                onAssign={handleAssign}
                onSelect={openTask}
              />
            )
          ) : view === "documents" ? (
            documentsLoading && documents.length === 0 ? (
              // rowHeight matches .fleet-task-row's real min-height (52px) —
              // DocumentsList reuses that exact row, so this skeleton pins
              // the same height (see FleetListSkeleton's MAN-113 note).
              <FleetListSkeleton rows={4} rowHeight={52} />
            ) : documents.length === 0 ? (
              <div className="fleet-empty">
                <div className="fleet-empty-icon">
                  <FileText size={20} strokeWidth={1.75} />
                </div>
                <div className="fleet-empty-title">No documents yet</div>
                <div className="fleet-empty-desc">
                  Documents are this project's own markdown notes — specs, decisions, anything the
                  team and its agents should read before starting work here.
                </div>
                {canWriteProject ? (
                  <div className="fleet-empty-actions">
                    <button type="button" className="fleet-btn fleet-btn--accent-fill" onClick={() => setDocumentComposerOpen(true)}>
                      <span className="fleet-btn-plus">+</span> New document
                    </button>
                  </div>
                ) : null}
              </div>
            ) : (
              <DocumentsList documents={documents} hrefFor={documentHref} />
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
            <AgentsList workspaceId={workspaceId} agents={shown} costByAgent={cost} onSelect={goToAgent} onAgentStoppedChanged={refresh} />
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

      {documentComposerOpen && (
        <DocumentComposer
          workspaceId={workspaceId}
          projectId={projectId}
          projectName={project?.name}
          onClose={() => setDocumentComposerOpen(false)}
          onCreated={() => { setDocumentComposerOpen(false); refreshDocuments(); }}
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
