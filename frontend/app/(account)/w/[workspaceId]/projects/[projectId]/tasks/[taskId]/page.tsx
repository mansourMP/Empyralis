"use client";

/**
 * A task's own page — /w/{ws}/projects/{projectId}/tasks/{taskId}.
 *
 * This route is the whole point of MAN-11x's "open it in the main content
 * area, not a drawer": a task now has a URL, so it can be deep-linked,
 * ⌘-clicked into a background tab, bookmarked, and reached by browser
 * back/forward like anything else. TaskDetailView draws it (see that file for
 * the layout rationale); this page owns the data and the writes.
 *
 * No new endpoint: the task is read out of the project's own polled task list
 * (useFleetTasks), the same cache the board reads, so a status change made
 * here and one made on the board can never show two different answers. The
 * cost is that a task id belonging to another project 404s here even though
 * it exists — which is correct, since its URL names this project.
 */

import { useCallback, useMemo, useState } from "react";
import { useParams, useRouter } from "next/navigation";

import {
  useFleetAgents,
  useFleetProjects,
  useFleetTasks,
  useWorkspaceRoster,
  assignFleetTask,
  assignFleetTaskToUser,
  patchFleetTask,
  setFleetTaskParent,
  type FleetTaskStatus,
  type TaskAssigneeSelection,
} from "@/lib/workspace/fleet/fleet-data";
import { useWorkspaceMembers } from "@/lib/workspace/fleet/members-data";
import { TaskDetailView } from "@/lib/workspace/fleet/TaskDetailView";
import { useBreadcrumbLabel, useBreadcrumbIcon } from "@/lib/workspace/fleet/Breadcrumbs";
import { ProjectIcon } from "@/lib/workspace/fleet/fleet-project-identity";
import { TaskStatusIcon, taskShortId } from "@/lib/workspace/fleet/task-status";
import { FleetTaskDetailSkeleton } from "@/lib/workspace/fleet/fleet-states";

export default function TaskDetailPage() {
  const params = useParams();
  const router = useRouter();
  const workspaceId = String(params?.workspaceId || "");
  const projectId = String(params?.projectId || "");
  const taskId = String(params?.taskId || "");
  const base = `/w/${encodeURIComponent(workspaceId)}`;
  const projectHref = `${base}/projects/${encodeURIComponent(projectId)}`;

  const { agents, error: agentsError } = useFleetAgents(workspaceId);
  // MAN-64/MAN-70: the pool of valid HUMAN assignees, plus the lookup
  // TaskDetailView uses to render a human commenter's real name.
  const { members, error: membersError } = useWorkspaceMembers(workspaceId);
  // 2026-08-13: both hooks above already exposed `error` — it just wasn't
  // read here, so a failed fetch degraded silently into empty arrays and
  // every actor lookup on this page (Created by, Completed by, the
  // Activity feed) rendered as an anonymous "Someone" or vanished, exactly
  // as if the lookup had succeeded and genuinely found nobody. Passed
  // through so TaskDetailView can tell the two apart.
  const identityLookupFailed = Boolean(agentsError || membersError);
  // MCP-connected external agents — the lookup that names an external
  // agent's comment instead of printing its opaque ext_agent_ id.
  const { externalAgents } = useWorkspaceRoster(workspaceId);
  const { projects } = useFleetProjects(workspaceId);
  const project = projects.find((p) => p.id === projectId);
  const { tasks, loading, refresh } = useFleetTasks(workspaceId, projectId);

  const inProject = useMemo(
    () => agents.filter((a) => (a.project_id || "").trim() === projectId),
    [agents, projectId],
  );

  // Optimistic status/priority, identical in shape to the project board's —
  // both surfaces write through the same polled cache, so both need the same
  // "paint it now, reconcile on the refetch" treatment or a change made here
  // sits stale for up to the poll interval.
  const [pendingStatus, setPendingStatus] = useState<FleetTaskStatus | null>(null);
  const [pendingPriority, setPendingPriority] = useState<number | null>(null);
  const [pendingDue, setPendingDue] = useState<string | null>(null);
  // Title/description follow the identical optimistic-overlay shape as
  // status/priority/due above — both write through the same PATCH the others
  // already do (fleet_patch_task accepts all five on one request; see
  // TaskDetailView's onTitleChange/onDescriptionChange doc comment for why
  // this pair had no writer at all until now). `null` means "no pending edit"
  // for both, matching the others' convention.
  const [pendingTitle, setPendingTitle] = useState<string | null>(null);
  const [pendingDescription, setPendingDescription] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const task = useMemo(() => {
    const found = tasks.find((t) => t.id === taskId);
    if (!found) return null;
    if (
      pendingStatus === null &&
      pendingPriority === null &&
      pendingDue === null &&
      pendingTitle === null &&
      pendingDescription === null
    ) {
      return found;
    }
    return {
      ...found,
      ...(pendingStatus !== null ? { status: pendingStatus } : {}),
      ...(pendingPriority !== null ? { priority: pendingPriority } : {}),
      ...(pendingDue !== null ? { due_at: pendingDue } : {}),
      ...(pendingTitle !== null ? { title: pendingTitle } : {}),
      ...(pendingDescription !== null ? { description: pendingDescription } : {}),
    };
  }, [tasks, taskId, pendingStatus, pendingPriority, pendingDue, pendingTitle, pendingDescription]);

  // Breadcrumb: Projects › {project} › {task}. The task crumb carries its
  // status ring, so the chain shows the same state the board column does.
  useBreadcrumbLabel(projectId, project?.name);
  useBreadcrumbIcon(
    projectId,
    useMemo(
      () => (project ? <ProjectIcon icon={project.icon} tint={project.tint} size={16} /> : null),
      [project],
    ),
  );
  useBreadcrumbLabel(taskId, task?.title || undefined);
  useBreadcrumbIcon(
    taskId,
    useMemo(
      () => (task ? <TaskStatusIcon status={task.status} size={14} /> : null),
      [task],
    ),
  );

  // Every handler below follows the same shape: the mutation's own
  // try/catch decides `notice`, and the follow-up `refresh()` — a plain GET
  // — is always awaited SEPARATELY, outside that try, swallowed with
  // `.catch(() => {})`. A mutation that succeeds and is then followed by a
  // refresh that fails must never report "Could not update this task": the
  // update already happened, and the only real cost of a failed refresh is
  // this view staying stale until the next natural reload — reporting it as
  // the mutation failing would tell the person to redo work that is already
  // done (CLAUDE.md's "reporting failure on success" law).
  const handleStatusChange = useCallback(async (id: string, status: FleetTaskStatus) => {
    setNotice(null);
    setPendingStatus(status);
    try {
      await patchFleetTask(workspaceId, id, { status });
    } catch (e) {
      setNotice(e instanceof Error ? e.message : "Could not update this task.");
      setPendingStatus(null);
      return;
    }
    refresh();
    setPendingStatus(null);
  }, [workspaceId, refresh]);

  const handlePriorityChange = useCallback(async (id: string, priority: number) => {
    setNotice(null);
    setPendingPriority(priority);
    try {
      await patchFleetTask(workspaceId, id, { priority });
    } catch (e) {
      setNotice(e instanceof Error ? e.message : "Could not update this task's priority.");
      setPendingPriority(null);
      return;
    }
    refresh();
    setPendingPriority(null);
  }, [workspaceId, refresh]);

  // MAN-64/MAN-70: assignee is agent-or-human -- dispatch to whichever of
  // assignFleetTask/assignFleetTaskToUser matches the picker's selection.
  // Only the agent path can ever report a wake failure.
  const handleAssign = useCallback(async (id: string, selection: TaskAssigneeSelection) => {
    setNotice(null);
    let wakeNotice: string | null = null;
    try {
      if (selection.kind === "agent") {
        const { wakeError } = await assignFleetTask(workspaceId, id, selection.id);
        if (wakeError) {
          wakeNotice = `Assigned, but the agent could not be woken: ${wakeError}. It will not start until it is running.`;
        }
      } else {
        await assignFleetTaskToUser(workspaceId, id, selection.id);
      }
    } catch (e) {
      setNotice(e instanceof Error ? e.message : "Could not assign this task.");
      return;
    }
    // The assign itself is done — a refresh failure here must not clobber
    // (or invent, when there's nothing to say) the notice above with a
    // false "Could not assign this task."
    refresh();
    if (wakeNotice) setNotice(wakeNotice);
  }, [workspaceId, refresh]);

  const handleDueChange = useCallback(async (id: string, dueAt: string | null) => {
    setNotice(null);
    setPendingDue(dueAt);
    try {
      // patchFleetTask's own "due_at alone does not clear -- clear_due_at
      // pairing established for a nullable field on this same kind" contract
      // (see fleet-data.ts): dueAt === null means the user cleared the due
      // date, which requires clear_due_at: true or the backend's
      // `ELSE due_at` SQL branch silently keeps the old value.
      await patchFleetTask(workspaceId, id, { due_at: dueAt, clear_due_at: dueAt === null });
    } catch (e) {
      setNotice(e instanceof Error ? e.message : "Could not update this task's due date.");
      setPendingDue(null);
      return;
    }
    refresh();
    setPendingDue(null);
  }, [workspaceId, refresh]);

  const handleTitleChange = useCallback(async (id: string, title: string) => {
    setNotice(null);
    setPendingTitle(title);
    try {
      await patchFleetTask(workspaceId, id, { title });
    } catch (e) {
      setNotice(e instanceof Error ? e.message : "Could not rename this task.");
      setPendingTitle(null);
      return;
    }
    refresh();
    setPendingTitle(null);
  }, [workspaceId, refresh]);

  const handleDescriptionChange = useCallback(async (id: string, description: string) => {
    setNotice(null);
    setPendingDescription(description);
    try {
      await patchFleetTask(workspaceId, id, { description });
    } catch (e) {
      setNotice(e instanceof Error ? e.message : "Could not update this task's description.");
      setPendingDescription(null);
      return;
    }
    refresh();
    setPendingDescription(null);
  }, [workspaceId, refresh]);

  const handleSetParent = useCallback(async (id: string, parentTaskId: string | null) => {
    setNotice(null);
    try {
      await setFleetTaskParent(workspaceId, id, parentTaskId);
    } catch (e) {
      setNotice(e instanceof Error ? e.message : "Could not set parent task.");
      return;
    }
    refresh();
  }, [workspaceId, refresh]);

  const handleSubTaskCreated = useCallback(async () => {
    await refresh();
  }, [refresh]);

  // A task that vanished (an agent deleted it, or the id is simply wrong) gets
  // an explicit page below rather than a silent redirect — a bounce back to
  // the board would look like the click did nothing.
  if (loading && !task) {
    return (
      <main className="fleet-content fleet-content--chat">
        <FleetTaskDetailSkeleton />
      </main>
    );
  }

  if (!task) {
    return (
      <main className="fleet-content">
        <div className="fleet-empty">
          <div className="fleet-empty-title">Task not found</div>
          <div className="fleet-empty-desc">
            {taskShortId(taskId)} isn’t in this project any more. It may have been deleted, or the
            link may point at another project.
          </div>
          <div className="fleet-empty-actions">
            <button type="button" className="fleet-btn fleet-btn--accent-fill" onClick={() => router.push(projectHref)}>
              Back to {project?.name || "project"}
            </button>
          </div>
        </div>
      </main>
    );
  }

  return (
    <main className="fleet-content fleet-content--chat">
      {notice ? (
        <div className="fleet-page-state-body" role="alert" style={{ color: "var(--warning-text)", padding: "12px 32px 0" }}>
          {notice}
        </div>
      ) : null}
      <TaskDetailView
        task={task}
        agents={inProject}
        members={members}
        externalAgents={externalAgents}
        workspaceId={workspaceId}
        projectName={project?.name || "Project"}
        projectHref={projectHref}
        onStatusChange={handleStatusChange}
        onPriorityChange={handlePriorityChange}
        onDueChange={handleDueChange}
        onTitleChange={handleTitleChange}
        onDescriptionChange={handleDescriptionChange}
        onAssign={handleAssign}
        onSetParent={handleSetParent}
        onSubTaskCreated={handleSubTaskCreated}
        // Labels are their own endpoints (attach/detach), not a field on the
        // task PATCH, so the editor writes directly and asks for a re-read —
        // the same polled cache the board reads, so both agree immediately.
        onLabelsChanged={refresh}
        // Comments are their own endpoint too (POST .../comments) — the
        // composer writes directly and asks for the same re-read.
        onCommentPosted={refresh}
        identityLookupFailed={identityLookupFailed}
      />
    </main>
  );
}
