"use client";

/**
 * MY WORK — "what is assigned to me, across every project."
 *
 * The one genuinely new destination in the 2026-08-15 rail (see
 * primary-rail-nav.ts's header for the whole shape and why the rail went
 * flat). Before it, this question had no surface: a person opened each
 * project in turn and scanned its board. The rail's Projects list says where
 * work lives; nothing said what was theirs.
 *
 * TWO SECTIONS, NEVER ONE LIST. Founder's decision, made before this was
 * built: agent-assigned work shows here too, CLEARLY MARKED — "a person
 * still needs to see what their agents owe them; hiding it would make My
 * work a lie by omission." Merging the two would claim an agent's task is
 * something the reader has to go do; dropping the second would report
 * "nothing outstanding" while an agent is mid-job. The rule deciding which
 * task lands in which section is my-work.ts, a pure module with its own
 * plain test — never re-derived here.
 *
 * NO NEW BACKEND. `GET /api/w/{ws}/fleet/tasks` with no `project_id` already
 * returns every task in every project the caller can see, project-visibility
 * filtered server-side (routes_fleet.fleet_list_tasks' `_visible_project_ids`
 * branch). The backend's own `list_my_tasks` was checked first, per the
 * brief, and is the wrong function despite the name — it scopes on
 * `assignee_agent_id` and has no human-assignee clause at all; it answers
 * "what should this AGENT pick up", which is a different question.
 *
 * Rows are the SAME TasksList the project board renders, so a task looks and
 * behaves identically wherever it is read — including its status control and
 * its assignee picker. Its `hrefFor` points at the task's own page inside
 * its own project, which is the only home a task has: this surface is a
 * lens, never a second place a task lives.
 */

import { useCallback, useMemo, useState } from "react";
import { useParams } from "next/navigation";
import Link from "next/link";
import { ListChecks } from "lucide-react";

import {
  assignFleetTask,
  assignFleetTaskToUser,
  patchFleetTask,
  useFleetAgents,
  useFleetProjects,
  useFleetWorkspaceTasks,
  type FleetTask,
  type FleetTaskStatus,
  type TaskAssigneeSelection,
} from "@/lib/workspace/fleet/fleet-data";
import { useOwnAccountId, useWorkspaceMembers } from "@/lib/workspace/fleet/members-data";
import { isOpenMyWork, selectMyWork } from "@/lib/workspace/fleet/my-work";
import { TasksList } from "@/lib/workspace/fleet/TasksList";
import { FleetRowsSkeleton, FleetSurfaceError } from "@/lib/workspace/fleet/fleet-states";
import { useBreadcrumbBadge } from "@/lib/workspace/fleet/Breadcrumbs";
import { breadcrumbCount } from "@/lib/workspace/fleet/fleet-presentation";

export default function MyWorkPage() {
  const params = useParams();
  const workspaceId = String(params?.workspaceId || "");
  const base = `/w/${encodeURIComponent(workspaceId)}`;

  const { tasks, loading, error, refresh } = useFleetWorkspaceTasks(workspaceId);
  const { agents } = useFleetAgents(workspaceId);
  const { projects } = useFleetProjects(workspaceId);
  const { members } = useWorkspaceMembers(workspaceId);
  const myAccountId = useOwnAccountId();

  const { mine, agent } = useMemo(() => selectMyWork(tasks, myAccountId), [tasks, myAccountId]);

  // Done work stays reachable but never leads: an ownership list that opens
  // on finished items is answering yesterday's question. Same "show it,
  // don't shout it" posture the project board's own Backlog grouping takes.
  const [showDone, setShowDone] = useState(false);
  const openMine = useMemo(() => mine.filter(isOpenMyWork), [mine]);
  const openAgent = useMemo(() => agent.filter(isOpenMyWork), [agent]);
  const doneCount = mine.length + agent.length - openMine.length - openAgent.length;
  const shownMine = showDone ? mine : openMine;
  const shownAgent = showDone ? agent : openAgent;

  useBreadcrumbBadge(
    "my-work",
    useMemo(
      () =>
        loading && tasks.length === 0 ? null : (
          <span className="fleet-breadcrumb-count">
            · {breadcrumbCount(openMine.length + openAgent.length, "open item", "open items", "Nothing open")}
          </span>
        ),
      [loading, tasks.length, openMine.length, openAgent.length],
    ),
  );

  // A task's home is its project — every row links there, so this page never
  // becomes a second place a task can be opened from and drift.
  const projectOf = useCallback(
    (task: FleetTask) => String(task.project_id || "").trim(),
    [],
  );
  const hrefFor = useCallback(
    (taskId: string) => {
      const task = tasks.find((t) => t.id === taskId);
      const projectId = task ? projectOf(task) : "";
      return projectId
        ? `${base}/projects/${encodeURIComponent(projectId)}/tasks/${encodeURIComponent(taskId)}`
        : `${base}/projects`;
    },
    [base, tasks, projectOf],
  );

  // A failed wake is reported, never swallowed — assigning fires a wake so
  // the agent actually starts, and a silent wake failure would read as
  // "assigned, working" when nothing is running. Same posture the project
  // board takes at its own assign call site.
  // Structured exactly like the project board's own handleAssign, and for
  // its reason: the REFRESH sits outside the try, because it is a plain GET
  // whose failure must never overwrite a real wake-status notice or invent
  // "could not assign" on top of an assignment that already committed.
  const [notice, setNotice] = useState<string | null>(null);
  const handleAssign = useCallback(
    async (taskId: string, selection: TaskAssigneeSelection) => {
      setNotice(null);
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
        setNotice(e instanceof Error ? e.message : "Could not assign this task.");
        return;
      }
      void refresh();
      if (wakeNotice) setNotice(wakeNotice);
    },
    [workspaceId, refresh],
  );

  const handleStatusChange = useCallback(
    async (taskId: string, status: FleetTaskStatus) => {
      setNotice(null);
      try {
        await patchFleetTask(workspaceId, taskId, { status });
      } catch (e) {
        setNotice(e instanceof Error ? e.message : "Could not update this task.");
        return;
      }
      void refresh();
    },
    [workspaceId, refresh],
  );

  const nothingAtAll = !loading && mine.length === 0 && agent.length === 0;

  return (
    // --wide, not the default reading column. This page renders TasksList's
    // real 5-column table, which needs ~824px; `.fleet-content`'s plain
    // `--content-max` is 820px and its own comment calls it a READING width.
    // Measured in a browser before changing it: the table wanted 824px, got
    // 716px, and `.fleet-content-main`'s `overflow-x: hidden` CLIPPED the
    // Status column with no scrollbar — content silently cut off with no way
    // to reach it. Same call Hardware and Billing already made for their own
    // dense/table content.
    <main className="fleet-content fleet-content--wide">
      <div className="fleet-content-main fleet-mywork">
        {notice ? (
          <div className="fleet-page-state-body" role="alert" style={{ color: "var(--warning-text)" }}>
            {notice}
          </div>
        ) : null}

        {/* "Could not load" and "nothing assigned" are DIFFERENT FACTS and
            never share a screen (CLAUDE.md's standing rule) — an unreachable
            backend must never render as a confident empty state telling
            somebody they have no work. */}
        {error && tasks.length === 0 ? (
          <FleetSurfaceError
            title="Couldn’t load your work"
            message={error}
            onRetry={() => void refresh()}
          />
        ) : loading && tasks.length === 0 ? (
          <FleetRowsSkeleton rows={4} label="Loading your work" />
        ) : nothingAtAll ? (
          // The empty state TEACHES, which CLAUDE.md allows precisely
          // because there is nothing else to show — and it names the two
          // things that put a row here, so "why is this empty" is answered
          // without anyone having to guess the rule.
          <div className="fleet-mywork-empty">
            <ListChecks size={20} strokeWidth={1.5} aria-hidden="true" />
            <h2>Nothing assigned to you</h2>
            <p>
              Tasks show up here when someone assigns one to you, or when you hand one to an agent.
            </p>
            <Link className="fleet-btn fleet-btn--accent" href={`${base}/projects`}>
              Open a project
            </Link>
          </div>
        ) : (
          <>
            <MyWorkSection
              id="mywork-mine"
              title="Assigned to me"
              tasks={shownMine}
              emptyNote="Nothing assigned to you right now."
              agents={agents}
              members={members}
              projects={projects}
              hrefFor={hrefFor}
              onAssign={handleAssign}
              onStatusChange={handleStatusChange}
            />
            <MyWorkSection
              id="mywork-agents"
              title="With my agents"
              // The mark the founder asked for, and the only sentence on
              // this page: without it the second table reads as more work
              // the reader owes, which is the opposite of what it is.
              subtitle="Work you handed over. Your agents own these."
              tasks={shownAgent}
              emptyNote="You haven't handed anything to an agent yet."
              agents={agents}
              members={members}
              projects={projects}
              hrefFor={hrefFor}
              onAssign={handleAssign}
              onStatusChange={handleStatusChange}
            />
            {doneCount > 0 ? (
              <button
                type="button"
                className="fleet-mywork-donetoggle"
                aria-expanded={showDone}
                onClick={() => setShowDone((v) => !v)}
              >
                {showDone ? "Hide" : "Show"} {doneCount} done
              </button>
            ) : null}
          </>
        )}
      </div>
    </main>
  );
}

/** One titled table. A section with no rows still renders its heading and a
 *  one-line note rather than vanishing: silently dropping "With my agents"
 *  when it is empty would make the page's own structure change shape
 *  underneath the reader, and would hide the fact that the section exists at
 *  all from anyone who has never used it. */
function MyWorkSection({
  id,
  title,
  subtitle,
  tasks,
  emptyNote,
  agents,
  members,
  projects,
  hrefFor,
  onAssign,
  onStatusChange,
}: {
  id: string;
  title: string;
  subtitle?: string;
  tasks: FleetTask[];
  emptyNote: string;
  agents: Parameters<typeof TasksList>[0]["agents"];
  members: Parameters<typeof TasksList>[0]["members"];
  projects: { id: string; name: string }[];
  hrefFor: (taskId: string) => string;
  onAssign: (taskId: string, selection: TaskAssigneeSelection) => void;
  onStatusChange: (taskId: string, status: FleetTaskStatus) => void;
}) {
  const projectName = useMemo(
    () => new Map(projects.map((p) => [p.id, p.name])),
    [projects],
  );
  return (
    <section className="fleet-mywork-section" aria-labelledby={id}>
      <header className="fleet-mywork-section-head">
        {/* A real h2 — the page's h1 is the breadcrumb's current crumb
            (Breadcrumbs.tsx), so these are its genuine children, not styled
            divs pretending to be headings. */}
        <h2 id={id}>
          {title}
          <span className="fleet-mywork-section-count">{tasks.length}</span>
        </h2>
        {subtitle ? <p className="fleet-mywork-section-note">{subtitle}</p> : null}
      </header>
      {tasks.length === 0 ? (
        <p className="fleet-mywork-section-empty">{emptyNote}</p>
      ) : (
        <>
          {/* Which project a row belongs to is the one fact a cross-project
              list needs that the project's own board never does — TasksList
              has no column for it (correctly: inside a project it would be
              the same word on every row), so it rides above each row's
              group here instead of forking that component. */}
          {groupByProject(tasks).map(([projectId, rows]) => (
            <div key={projectId || "none"} className="fleet-mywork-group">
              <div className="fleet-mywork-group-label">{projectName.get(projectId) || "Unfiled"}</div>
              {/* The table scrolls inside its OWN container rather than
                  widening the page or being clipped by an ancestor's
                  overflow:hidden — the standing responsive rule for wide
                  content. At a narrow viewport the Status column is reached
                  by scrolling this strip, never lost. */}
              <div className="fleet-mywork-table">
                <TasksList
                  tasks={rows}
                  agents={agents}
                  members={members}
                  hrefFor={hrefFor}
                  onAssign={onAssign}
                  onStatusChange={onStatusChange}
                />
              </div>
            </div>
          ))}
        </>
      )}
    </section>
  );
}

/** Groups rows by project, first-seen order — the list arrives already
 *  ordered by the backend, so this preserves that ordering rather than
 *  imposing a second one. */
function groupByProject(tasks: FleetTask[]): [string, FleetTask[]][] {
  const groups = new Map<string, FleetTask[]>();
  for (const task of tasks) {
    const key = String(task.project_id || "").trim();
    const bucket = groups.get(key);
    if (bucket) bucket.push(task);
    else groups.set(key, [task]);
  }
  return Array.from(groups.entries());
}
