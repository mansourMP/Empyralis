"use client";

import { fleetAuthorizedFetch } from "@/lib/workspace/fleet/fleet-authorized-fetch";

import { useCallback, useEffect, useMemo, useState, type CSSProperties } from "react";
import Link from "next/link";
import { useParams, usePathname, useRouter } from "next/navigation";

import { Calendar, Zap } from "lucide-react";

import {
  useFleetAgents,
  useFleetProjects,
  useFleetTasks,
  assignFleetTask,
  assignFleetTaskToUser,
  patchFleetTask,
  type FleetTaskStatus,
  type TaskAssigneeSelection,
} from "@/lib/workspace/fleet/fleet-data";
import { useOwnAccountId, useOwnWorkspaceRole, useWorkspaceMembers } from "@/lib/workspace/fleet/members-data";
import { deriveCanWriteProject, useProjectMembers } from "@/lib/workspace/fleet/project-members-data";
import { TasksList } from "@/lib/workspace/fleet/TasksList";
import { TasksBoard } from "@/lib/workspace/fleet/TasksBoard";
import { TasksGroupedList } from "@/lib/workspace/fleet/TasksGroupedList";
import { TaskComposer } from "@/lib/workspace/fleet/TaskComposer";
import { useFleetDocuments, type FleetDocumentActivityEntry } from "@/lib/workspace/fleet/documents-data";
import { DocumentsList } from "@/lib/workspace/fleet/DocumentsList";
import { DocumentComposer } from "@/lib/workspace/fleet/DocumentComposer";
import { DocumentActivityFeed } from "@/lib/workspace/fleet/document-activity-feed";
import { MemberAvatarStack } from "@/lib/workspace/fleet/MemberAvatarStack";
import { ProjectMemberAdd } from "@/lib/workspace/fleet/ProjectMemberAdd";
import { ProjectPeople } from "@/lib/workspace/fleet/ProjectPeople";
import { ProjectSettings } from "@/lib/workspace/fleet/ProjectSettings";
import { CopyLinkButton } from "@/lib/ui/CopyLinkButton";
import { useBreadcrumbLabel, useBreadcrumbIcon, HeaderAction } from "@/lib/workspace/fleet/Breadcrumbs";
import { formatDate, formatNumber } from "@/lib/workspace/fleet/fleet-presentation";
import { ProjectIcon } from "@/lib/workspace/fleet/fleet-project-identity";
import { UsageStat, bucketSeries, type UsageBucket } from "@/lib/workspace/fleet/fleet-sparkline";
import { createButtonClass } from "@/lib/workspace/fleet/create-accent";
import { TaskViewOptions } from "@/lib/workspace/fleet/TaskViewOptions";
import {
  DEFAULT_TASK_VIEW_OPTIONS,
  readTaskViewOptions,
  sortTasks,
  writeTaskViewOptions,
  type TaskViewOptions as TaskViewOptionsState,
} from "@/lib/workspace/fleet/task-view-options";
import { FleetRightPanel, PanelSection, PanelRow } from "@/lib/workspace/fleet/FleetRightPanel";
import { FleetBoardSkeleton, FleetSurfaceError } from "@/lib/workspace/fleet/fleet-states";
import { PROJECT_TAB_LABEL, PROJECT_TAB_VIEWS } from "@/lib/workspace/fleet/project-views";
import { ListChecks, FileText } from "lucide-react";
import { formatUsd } from "@/lib/ui/money";

const money = (n: number | undefined) => formatUsd(n ?? 0);

/**
 * Flat-list skeleton reusing `.fleet-tasks-list`/`.fleet-tasks-list-header`/
 * `.fleet-task-row`'s real 5-column grid — was previously `FleetListSkeleton
 * rows={4} rowHeight={52}`, which pinned the right row HEIGHT but never
 * reserved the column-title row real TasksList/DocumentsList/AgentsList
 * always render above their rows, and used a generic 2-bar flex row instead
 * of the real per-column grid. Column headers approximate the DEFAULT
 * display columns (Assignee/Due/Updated/Status) — TasksList's own column
 * set is itself display-option-dependent, so this can't be exact for every
 * saved view, only for the common one.
 */
function TasksListSkeleton() {
  return (
    <div className="fleet-tasks-list" aria-busy="true" aria-label="Loading">
      <div className="fleet-tasks-list-header" role="row">
        <span>Task</span>
        <span>Assignee</span>
        <span className="is-right">Due</span>
        <span className="is-right">Updated</span>
        <span>Status</span>
      </div>
      {[0, 1, 2, 3].map((i) => (
        <div key={i} className="fleet-task-row" style={{ cursor: "default" }}>
          <span className="fleet-skeleton-bar" style={{ width: `${45 + (i % 3) * 12}%`, height: 13 }} />
          <span className="fleet-skeleton-bar" style={{ width: "60%", height: 12 }} />
          <span className="fleet-skeleton-bar" style={{ width: 40, height: 12, marginLeft: "auto" }} />
          <span className="fleet-skeleton-bar" style={{ width: 40, height: 12, marginLeft: "auto" }} />
          <span className="fleet-skeleton-bar" style={{ width: 64, height: 18, borderRadius: 999 }} />
        </div>
      ))}
    </div>
  );
}

/**
 * Grouped-task skeleton reusing TasksGroupedList's real `.fleet-glist*`
 * classNames (tab strip + N titled/collapsible sections) — a saved grouping
 * preference (by assignee/status/etc, `viewOptions.grouping`) is known
 * before the fetch resolves, same as `viewOptions.layout`'s existing board
 * check just above this file's tasks branch, so the flat-list skeleton
 * above was wrong for any reader whose saved view groups tasks: it opened
 * as 4 flat rows, then reflowed into a tab strip plus multiple titled
 * sections the instant the fetch landed.
 */
function TasksGroupedSkeleton() {
  return (
    <div className="fleet-glist" aria-busy="true" aria-label="Loading">
      <div className="fleet-glist-tabs" role="tablist" aria-label="Task filter">
        {["Active", "Backlog", "All"].map((t) => (
          <span key={t} className="fleet-skeleton-bar" style={{ width: 56, height: 20, borderRadius: 999 }} />
        ))}
      </div>
      {[3, 2].map((rows, si) => (
        <section key={si} className="fleet-glist-section">
          <header className="fleet-glist-header">
            <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
              <div className="fleet-skeleton-bar" style={{ width: 13, height: 13 }} />
              <div className="fleet-skeleton-bar" style={{ width: 90, height: 12 }} />
              <div className="fleet-skeleton-bar" style={{ width: 18, height: 11 }} />
            </div>
          </header>
          <div className="fleet-glist-rows">
            {Array.from({ length: rows }).map((_, i) => (
              <div key={i} className="fleet-glist-row" style={{ minHeight: 40, display: "flex", alignItems: "center", gap: 10, cursor: "default" }}>
                <div className="fleet-skeleton-bar" style={{ width: "40%", height: 12 }} />
              </div>
            ))}
          </div>
        </section>
      ))}
    </div>
  );
}

/**
 * Documents-list skeleton — TREE-shaped, matching what actually arrives
 * (DocumentsList.tsx renders an inferred folder tree, `.fleet-doc-tree*`).
 * It used to draw a two-column table header ("Document | Updated") because
 * the list itself was a table; leaving that behind would make the loading
 * state and the loaded state two different layouts, i.e. a visible reflow
 * on every open. The indents below are a plausible shape, not a prediction:
 * they exist so the placeholder occupies the same kind of space, never to
 * claim a specific tree is coming.
 */
const DOCUMENT_SKELETON_ROWS = [0, 1, 1, 0, 1];

function DocumentsListSkeleton() {
  return (
    <div className="fleet-doc-tree" aria-busy="true" aria-label="Loading">
      {DOCUMENT_SKELETON_ROWS.map((depth, i) => (
        <div
          key={i}
          className="fleet-doc-tree-row"
          style={{ "--doc-tree-depth": depth, cursor: "default" } as CSSProperties}
        >
          <span className="fleet-doc-tree-spacer" aria-hidden="true" />
          <span className="fleet-skeleton-bar" style={{ width: 14, height: 14 }} />
          <span className="fleet-skeleton-bar" style={{ width: `${34 + (i % 3) * 14}%`, height: 12 }} />
        </div>
      ))}
    </div>
  );
}

// The old flat 7-column agents table (and its own skeleton/filter/sort
// machinery) is gone from this view, and so is its successor — the primary
// rail's project-agents space, deleted 2026-08-21 because an agent belongs
// to the WORKSPACE and not to a project (founder, 2026-08-20; see
// primary-rail-space.ts's header). The project-scoped `/agents` route this
// page used to render for (`${projectBase}/agents`) is deleted outright now
// (founder's hard rule, 2026-08-30: an agent is completely independent of
// any project) — next.config.ts's LEGACY_REDIRECTS sends that URL straight
// to the agent's real, workspace-level address before this component ever
// mounts with that pathname, so `view` below can no longer resolve to
// "agents" at all. This file used to carry an honest fallback render for
// that state anyway (an empty state, a quiet solo redirect, or a plain
// list); that render is gone with the route it served.

export default function ProjectDetailPage() {
  const params = useParams();
  const router = useRouter();
  // Drives the Agents/Tasks/Documents derivation below — see the `view`
  // const near projectBase.
  const pathname = usePathname() || "";
  const workspaceId = String(params?.workspaceId || "");
  const projectId = String(params?.projectId || "");
  const base = `/w/${encodeURIComponent(workspaceId)}`;

  const { agents, error: agentsError } = useFleetAgents(workspaceId);
  // MAN-64/MAN-70: the pool of valid HUMAN assignees -- the same list
  // MemberAvatarStack renders for this page's own roster stack, passed down
  // as a prop rather than fetched a second time there (GET
  // /workspaces/{id}/members) -- see MemberAvatarStack.tsx's own doc
  // comment on why that used to be a redundant round trip.
  const { members, loading: membersLoading, error: membersError } = useWorkspaceMembers(workspaceId);
  // This project's own project_memberships rows -- likewise the ONE fetch
  // of GET /fleet/projects/{id}/members for this page, shared by
  // deriveCanWriteProject below and passed down to ProjectMemberAdd. Used
  // to be two independent useProjectMembers calls (this page's write gate,
  // ProjectMemberAdd's own) hitting the same endpoint on every load --
  // browser-measured ~250-400ms each, doubling both requests and backend
  // load for no reason.
  const { members: projectMembers, loading: projectMembersLoading, error: projectMembersError, refresh: refreshProjectMembers } =
    useProjectMembers(workspaceId, projectId);
  // include_archived: this list exists here only to resolve THIS project by
  // id, and an archived one still has a reachable detail route (get_project
  // never filtered on `archived`). Without it an archived project's page
  // renders with no name, no icon and — worse — no settings popover at all,
  // which is the only place Restore lives.
  const { projects, refresh: refreshProjects } = useFleetProjects(workspaceId, true);
  const project = projects.find((p) => p.id === projectId);
  useBreadcrumbLabel(projectId, project?.name);
  useBreadcrumbIcon(
    projectId,
    useMemo(
      () => (project ? <ProjectIcon icon={project.icon} tint={project.tint} size={16} /> : null),
      [project],
    ),
  );

  const projectBase = `${base}/projects/${encodeURIComponent(projectId)}`;

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
  //
  // PEOPLE joined as a fourth TAB on 2026-08-15 and left the tab bar on
  // 2026-08-16 (founder: "I never asked for these people... People already
  // exist on top") — the toolbar's own avatar stack + "+" ARE the people
  // surface, and a tab was the same surface twice in one bar. The ROUTE
  // stays live and unlinked (same treatment as /agents and /conversations
  // in CLAUDE.md — no redirect, no dead bookmarks), which is why `view`
  // still knows "people": a directly-typed URL still renders it. See
  // project-views.ts for the tab set itself.
  const view: "tasks" | "documents" | "people" =
    pathname === `${projectBase}/documents`
      ? "documents"
      : pathname === `${projectBase}/people`
        ? "people"
        : "tasks";
  // router.replace, not .push — same choice the agent detail page's own
  // sub-tabs already made (AgentDetailPage's onTabChange, one directory up).
  // Collapses Agents→Tasks→Documents clicks onto one history entry, so the
  // browser's own back button steps out of the project in one press instead
  // of walking back through every sub-tab click first.
  const viewHref = (v: "tasks" | "documents" | "people") => `${projectBase}/${v}`;
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

  // "C" opens the composer — Linear's own signature shortcut for "create new
  // issue". The composer itself is keyboard-first once open (Enter/Esc/
  // ⌘+Enter, see TaskComposer.tsx's own header), but reaching it required a
  // mouse click on "+ New task" with nothing else on this page offering a
  // way in. Scoped to the Tasks view only (not a global app-wide binding —
  // Agents/Documents have no "c" affordance of their own) and ignored
  // whenever a field already has focus or ANY dialog is open (composer
  // itself, the agent wizard, the document composer), so it can never fire
  // while someone is typing "c" into a title, a comment, or a search box.
  useEffect(() => {
    if (view !== "tasks") return;
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key !== "c" || event.metaKey || event.ctrlKey || event.altKey) return;
      const el = document.activeElement as HTMLElement | null;
      const tag = el?.tagName;
      if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT" || el?.isContentEditable) return;
      if (document.querySelector("[role='dialog']")) return;
      event.preventDefault();
      setComposer({});
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [view]);
  const { tasks, loading: tasksLoading, error: tasksError, refresh: refreshTasks } = useFleetTasks(workspaceId, projectId);
  // The Documents view (fourth top-level tab, founder's own call — see the
  // `view` const above). Only fetched with a real project_id: useFleetDocuments
  // itself no-ops without one, same guard useFleetTasks's own fetcher uses.
  const { documents, loading: documentsLoading, error: documentsError, refresh: refreshDocuments } = useFleetDocuments(workspaceId, projectId);
  const [documentComposerOpen, setDocumentComposerOpen] = useState(false);
  // Documents | Activity — this project's own change feed (the founder's
  // own ask, GitHub's per-repo "Commits" mapped onto this project — see
  // document-activity-feed.tsx's own header). Plain component state, not
  // persisted, same posture as viewOptions.layout's exclusion from "is
  // anything non-default" above: a display mode picked per visit, not a
  // preference worth remembering. Reset is implicit — leaving the
  // Documents view and coming back always starts on the tree.
  const [documentSurface, setDocumentSurface] = useState<"tree" | "activity">("tree");
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
    fleetAuthorizedFetch(`${base.replace("/w/", "/api/w/")}/fleet/usage?scope=project&id=${encodeURIComponent(projectId)}&period=day`, { credentials: "include" })
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
    fleetAuthorizedFetch(`/api/w/${encodeURIComponent(workspaceId)}/fleet/usage?scope=workspace&period=day`, { credentials: "include" })
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

  // `inProject` — agents grouped by project_id — is kept for two LIVE
  // consumers: ProjectSettings's delete-confirmation below (`contents.agents`
  // → "N agents will no longer belong to a project", an honest disclosure
  // that deleting no longer rehomes them anywhere — project_id is still a
  // required column on agent rows, just not a required NON-NULL one) and
  // TasksBoard/TasksGroupedList/TasksList/TaskComposer's assignee pool (a
  // separate, NOT-yet-decided question: whether that pool should stay
  // project-filtered or widen to all workspace agents — the founder has
  // been asked and explicitly deferred it). The `view === "agents"` content
  // that used to read it too — an empty state, a solo redirect, a plain
  // list, all gone with the route they served (see this file's own header)
  // — is deleted outright, not merely unreachable, and so are the
  // breadcrumb badge and the Properties drawer's "Agents"/"Cost by agent"
  // rollups, both LIVE per-project ownership claims removed earlier
  // (2026-08-30, see git history) rather than dead code riding along.
  const inProject = agents.filter((a) => (a.project_id || "").trim() === projectId);

  // MAN-64/MAN-70: assignee is agent-or-human -- dispatch to whichever of
  // assignFleetTask/assignFleetTaskToUser matches the picker's selection.
  // Only the agent path can ever report a wake failure (assigning a human
  // never schedules a wakeup at all), so `wakeError` only ever comes back
  // non-null from that branch.
  const handleAssign = async (taskId: string, selection: TaskAssigneeSelection) => {
    setTaskNotice(null);
    let wakeNotice: string | null = null;
    try {
      if (selection.kind === "agent") {
        const { wakeError } = await assignFleetTask(workspaceId, taskId, selection.id);
        if (wakeError) {
          wakeNotice = `Assigned, but the agent could not be woken: ${wakeError}. It will not start until it is running.`;
        }
      } else {
        await assignFleetTaskToUser(workspaceId, taskId, selection.id);
      }
    } catch (e) {
      setTaskNotice(e instanceof Error ? e.message : "Could not assign this task.");
      return;
    }
    // The assign itself already happened — a refresh failure here (a plain
    // GET) must not overwrite the wake-status notice above, or invent a
    // false "Could not assign this task" on a real success.
    refreshTasks();
    if (wakeNotice) setTaskNotice(wakeNotice);
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
    } catch (e) {
      setTaskNotice(e instanceof Error ? e.message : "Could not update this task.");
      setPendingStatus((prev) => {
        const next = new Map(prev);
        next.delete(taskId);
        return next;
      });
      return;
    }
    // The status change already happened — a refresh failure (a plain GET)
    // must not report it as "Could not update this task."
    refreshTasks();
    setPendingStatus((prev) => {
      const next = new Map(prev);
      next.delete(taskId);
      return next;
    });
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
  // Same route, keyed off a change-feed entry's own document_id rather than
  // a FleetDocument's id — DocumentActivityFeed's rows carry a different
  // shape than DocumentsList's.
  const documentActivityHref = useCallback(
    (entry: FleetDocumentActivityEntry) => `${projectBase}/documents/${encodeURIComponent(entry.document_id)}`,
    [projectBase],
  );

  return (
    <main className="fleet-content fleet-content--with-panel">
      {/* MAN-145 title-dedup follow-up: this used to render the project's
          name three times (tab strip, breadcrumb, and this block's own
          shared <h1> above the Agents/Tasks/Documents tabs). The breadcrumb's
          current crumb IS the page's <h1> now, on all three tabs (see
          Breadcrumbs.tsx) — it already carries the project's own icon
          (useBreadcrumbIcon above), context a plain title never had. This
          block is gone, not replaced with a styled div: the heading role
          lives one layer up, it isn't lost.

          UPDATE, 2026-08-30: the crumb used to ALSO carry a "· N agents"
          badge (useBreadcrumbBadge), derived by grouping agents on
          project_id. Removed outright, not replaced with a work-summary
          equivalent — an agent is completely independent of any project
          (founder hard rule), and this page already shows its own real
          work (the Tasks/Documents tabs a reader is already looking at),
          so a second summary of the same thing in the crumb would be
          redundant chrome, not a fact worth restoring. */}

      {/* U3-H: top row is breadcrumb + primary action only; the
          view-control cluster is its own row below, under the topbar's
          existing divider. FleetToolbar (the Properties-panel toggle) used
          to render here for the Agents view; that view and its route are
          deleted (2026-08-30 — an agent is independent of every project),
          and FleetToolbar is gone with it. */}
      {/* FILLED, 2026-08-01, EXCEPT WHEN THE LIST IS EMPTY (2026-08-12). This
          used to be the quiet hairline unconditionally, on the theory that
          the centre empty-state button ("Create your first agent") was the
          real CTA and two fills would shout at once. That was the wrong
          trade for a POPULATED view: this button is the PERSISTENT primary
          action, used every day, so a permanent hairline made the everyday
          action look secondary forever just to dodge a clash that only
          exists on an empty project.
          But filling it unconditionally reintroduced exactly that clash:
          CLAUDE.md — "Two accent-filled buttons in one view is a bug" — and
          a brand-new project (list.length === 0) rendered this header button
          AND the centred empty-state CTA filled at the same time, every
          time. fleet-theme.css's own .fleet-btn--accent-fill comment used to
          flag this as a known, undecided overlap; it is decided now: the
          centred CTA wins on an empty list — a first-run empty state is the
          one moment its own oversized button genuinely IS the thing a reader
          is looking at — so the header action drops back to the quiet
          hairline exactly then, and only then. It stays reachable the whole
          time (no dead controls); it only earns the fill once the list holds
          something and this is genuinely the button used every day. */}
      <HeaderAction>
        {/* create-accent.ts decides which of the two create controls owns
            the view's one accent fill — including the case a hand-inlined
            ternary would be blind to: a composer open in front of this
            button, both filled at once. Both views answer to one rule
            here. */}
        {view === "tasks" ? (
          <button
            type="button"
            className={createButtonClass("header", { listIsEmpty: tasks.length === 0, composerOpen: composer !== null })}
            onClick={() => setComposer({})}
          >
            <span className="fleet-btn-plus">+</span> New task
          </button>
        ) : view === "documents" && canWriteProject ? (
          // Write-gated, unlike the Tasks button beside it — a viewer here
          // would open a dialog whose own Create call the server rejects
          // outright (fleet_create_document's `member` floor). No dead
          // controls (CLAUDE.md): the button simply isn't in the DOM for a
          // reader who can't use it, `null` (still resolving) included.
          <button
            type="button"
            className={createButtonClass("header", { listIsEmpty: documents.length === 0, composerOpen: documentComposerOpen })}
            onClick={() => setDocumentComposerOpen(true)}
          >
            <span className="fleet-btn-plus">+</span> New document
          </button>
        ) : null}
      </HeaderAction>

      {/* The view switch lives in the content toolbar, not the topbar — the
          topbar is where the earlier mobile header-overlap bug came from, and
          this row is already proven reachable at 375px. */}
      <div className="fleet-content-toolbar">
        {/* ORDER IS THE FOUNDER'S OWN SKETCH: Tasks · Documents.
            Tasks leads because it is both the default landing view (the
            bare `${projectBase}` URL falls through to it) and the surface
            a project is opened to act on daily. It used to read Agents ·
            Tasks · Documents, which put the default view second — Agents
            is gone now (an agent is independent of every project).

            REAL LINKS, not buttons (CLAUDE.md: "primary navigation is real
            links, so cmd-click and middle-click work"). These were
            `router.replace` buttons — tolerable while the rail also offered
            a way in, and not tolerable now that this strip is THE way to
            reach a project's sections. `replace` keeps the original,
            deliberate history behaviour on a plain click (Agents→Tasks→
            Documents collapses to one entry, so browser-back steps out of
            the project rather than walking every tab click) while
            ⌘/middle-click get native browser semantics for free. */}
        {/* Exactly Tasks · Documents — the set lives in
            project-views.ts, whose header records why People is not here
            (the avatar stack + "+" in this same row ARE the people
            surface; its route stays live, unlinked).

            It used to be HIDDEN whenever the rail morphed into the
            project-agents space (2+ agents on the Agents route). That space
            is deleted (2026-08-21 — see primary-rail-space.ts's header: an
            agent belongs to the workspace, not to a project), so there is no
            longer a second surface claiming to be "where you pick" and this
            strip renders unconditionally again. It never was a pick-list for
            agents in the first place — it is Tasks · Documents. */}
        <div className="fleet-segmented" role="tablist" aria-label="Project view">
          {PROJECT_TAB_VIEWS.map((v) => (
            <Link
              key={v}
              href={viewHref(v)}
              replace
              role="tab"
              aria-selected={view === v}
              className={`fleet-segmented-btn${view === v ? " fleet-segmented-btn--active" : ""}`}
            >
              {PROJECT_TAB_LABEL[v]}
            </Link>
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
        <ProjectSettings
          workspaceId={workspaceId}
          project={project}
          // Real counts, so the delete confirmation names what it is about
          // to take instead of describing it vaguely. `null` while each
          // hook is still loading — the dialog omits an unconfirmed number
          // rather than printing a confident 0.
          contents={{
            tasks: tasksLoading ? null : tasks.length,
            documents: documentsLoading ? null : documents.length,
            agents: inProject.length,
          }}
          onChanged={refreshProjects}
          // Archived or deleted, this route no longer resolves to anything
          // a reader can act on — an archived project is filtered out of
          // every list, a deleted one is gone outright. .replace, not
          // .push: the dead detail URL must not stay in history for the
          // back button to land on.
          onRemoved={() => router.replace(`${base}/projects`)}
        />
        {/* Copy link — deliberately NOT bundled inside ProjectSettings
            above: that popover is owner-only (server-enforced), and copying
            a link is read-only — exactly the action a non-owner project
            member (or anyone else with access) needs too. CLAUDE.md: "the
            board is the product; nothing of value may exist only in a
            conversation" — a project has to be pasteable into Telegram. */}
        <CopyLinkButton path={`${base}/projects/${encodeURIComponent(projectId)}`} label={project?.name || "this project"} />
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
        {/* Documents | Activity — this project's own change feed (Task 2,
            founder's own ask). Same right-aligned slot TaskViewOptions
            occupies for the Tasks view (`.fleet-view-options`'s own
            margin-left:auto), shown only once there is a tree to switch
            away from — a document with zero documents has no history to
            offer either, and this mirrors TaskViewOptions' own `tasks.length
            > 0` guard immediately above. */}
        {view === "documents" && documents.length > 0 ? (
          <div className="fleet-view-options">
            <div className="fleet-segmented" role="tablist" aria-label="Documents view">
              <button
                type="button"
                role="tab"
                aria-selected={documentSurface === "tree"}
                className={`fleet-segmented-btn${documentSurface === "tree" ? " fleet-segmented-btn--active" : ""}`}
                onClick={() => setDocumentSurface("tree")}
              >
                Documents
              </button>
              <button
                type="button"
                role="tab"
                aria-selected={documentSurface === "activity"}
                className={`fleet-segmented-btn${documentSurface === "activity" ? " fleet-segmented-btn--active" : ""}`}
                onClick={() => setDocumentSurface("activity")}
              >
                Activity
              </button>
            </div>
          </div>
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
              // Which skeleton to show is decided by the SAME `viewOptions`
              // the real branches below switch on — board renders columns,
              // grouped renders a tab strip plus titled sections, flat
              // renders a plain table. A saved preference for either
              // non-default shape can already be active on first paint, so
              // picking only between "board" and "flat list" (as this used
              // to) was still wrong for a grouped view.
              viewOptions.layout === "board" ? (
                <FleetBoardSkeleton label="Loading task board" />
              ) : viewOptions.grouping !== "none" ? (
                <TasksGroupedSkeleton />
              ) : (
                <TasksListSkeleton />
              )
            ) : tasksError && tasks.length === 0 ? (
              <FleetSurfaceError title="Couldn’t load tasks" message={tasksError} onRetry={refreshTasks} />
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
                  {/* Filled only while nothing is in front of it — with
                      TaskComposer open this sits behind a 45% backdrop and
                      the composer's own Create owns the fill. */}
                  <button
                    type="button"
                    className={createButtonClass("empty_state", { listIsEmpty: true, composerOpen: composer !== null })}
                    onClick={() => setComposer({})}
                  >
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
                hrefFor={taskHref}
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
                hrefFor={taskHref}
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
                hrefFor={taskHref}
                onAssign={handleAssign}
                onSelect={openTask}
                onStatusChange={handleStatusChange}
              />
            )
          ) : view === "documents" && documentSurface === "activity" ? (
            // This project's own feed — projectId supplied, the narrower of
            // the two scopes DocumentActivityFeed serves (see that file's
            // own header; the workspace-wide reading is /context's toggle).
            <DocumentActivityFeed
              workspaceId={workspaceId}
              projectId={projectId}
              hrefForDocument={documentActivityHref}
              agents={agents}
              members={members}
              identityLookupFailed={Boolean(agentsError || membersError)}
            />
          ) : view === "documents" ? (
            documentsLoading && documents.length === 0 ? (
              <DocumentsListSkeleton />
            ) : documentsError && documents.length === 0 ? (
              <FleetSurfaceError title="Couldn’t load documents" message={documentsError} onRetry={refreshDocuments} />
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
                    <button
                      type="button"
                      className={createButtonClass("empty_state", { listIsEmpty: true, composerOpen: documentComposerOpen })}
                      onClick={() => setDocumentComposerOpen(true)}
                    >
                      <span className="fleet-btn-plus">+</span> New document
                    </button>
                  </div>
                ) : null}
              </div>
            ) : (
              <DocumentsList documents={documents} hrefFor={documentHref} />
            )
          ) : view === "people" ? (
            // Both lists are already in flight for this page (the toolbar's
            // avatar stack and its "+" read them), so this tab is a render,
            // not a fetch. Adding somebody stays the toolbar's "+" directly
            // above — one control, where it already was.
            <ProjectPeople
              workspaceMembers={members}
              projectMembers={projectMembers}
              loading={membersLoading || projectMembersLoading}
              error={membersError || projectMembersError}
            />
          ) : null}
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
            <PanelRow label="Created" value={project?.created_at ? formatDate(project.created_at) : "—"} icon={<Calendar size={15} strokeWidth={1.75} />} tone={project?.created_at ? "default" : "muted"} />
          </PanelSection>

          {/* "Agents" (PanelRow, above) and "Cost by agent" (a whole
              PanelSection) are REMOVED, 2026-08-30 — both derived from
              `inProject`/`costByAgent`, agents grouped by project_id, the
              exact ownership claim the founder's hard rule forbids: "an
              agent is completely independent of any project." "Cost this
              month"/"Tokens"/"LLM calls" above are NOT touched — they come
              from a genuinely project-scoped backend rollup
              (/fleet/usage?scope=project), never a client-side agent
              grouping, so they carry none of the same lie. */}

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
