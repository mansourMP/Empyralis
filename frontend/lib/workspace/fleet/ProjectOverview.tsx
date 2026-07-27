"use client";

/**
 * Project Overview — MAN-110 Phase 1 (a scoped first pass at "Project tab
 * needs Linear-level polish"; the Filter/Board-config/Analytics/real-time
 * co-editing panels MAN-110 also asks for are explicitly deferred to a
 * later phase, see the Linear comment on that issue).
 *
 * Two things live here, both built from data this platform already
 * collects — nothing here is invented or mocked:
 *
 *  1. A status roll-up (agents in this project, task counts by status,
 *     recent activity volume) — reusing useFleetTasks/useFleetAgents
 *     (already fetched by the caller, page.tsx) and the project's own cost
 *     rollup (GET /fleet/usage?scope=project, also already fetched by the
 *     caller).
 *
 *  2. A chronological activity feed merging THREE real sources:
 *       a. The workspace activity ledger (GET /api/activity/timeline,
 *          activity_ledger_service.list_activity_timeline_payload) — the
 *          same ledger the Inbox page already reads — filtered client-side
 *          to rows whose install_id belongs to one of this project's own
 *          agents. There is no project_id column on activity_ledger_events
 *          today (verified: runtime_events_api.py's get_activity_timeline
 *          takes workspace_id/actor_id/install_id/... filters, no
 *          project_id), so agent-authored rows are scoped to this project
 *          the only honest way available: by which agent did it.
 *       b. Task-created facts off the real project_tasks rows already on
 *          screen (created_at + created_by). project_tasks_service itself
 *          never writes to the activity ledger (grepped
 *          project_tasks_service.py + routes_fleet.py — no
 *          append_activity_event call exists there), so this is otherwise
 *          the only visible trace a task was ever created. created_at is
 *          set once and never mutated, so this timestamp is exact.
 *       c. Real task comments, off task.metadata.comments — a genuine,
 *          already-live commenting mechanism (project_tasks_service.py's
 *          add_task_comment, backing the project_task__comment tool wired
 *          in skills_service.py 2026-07-25) with no UI surface anywhere
 *          today; add_task_comment's own docstring calls a first-class
 *          comment table + UI feed "real future work". Each comment object
 *          carries its own immutable created_at/author_type/author_id, so
 *          — unlike a task's single mutable `updated_at` — this is a true
 *          per-event timestamp. Only ever agent-authored today: the only
 *          caller of add_task_comment is the project_task__comment tool: no
 *          HTTP route or frontend control lets a human post one.
 *     Deliberately NOT synthesized: "assigned" / "completed" events off a
 *     task's `updated_at`. `updated_at` is a single last-write pointer that
 *     assign_task, update_task, AND add_task_comment all bump (each sets
 *     `updated_at = NOW()` in its own UPDATE) — so once a task has had more
 *     than one of those happen to it, `updated_at` no longer safely
 *     identifies which one happened when. Rather than show a
 *     timestamp that might be wrong, this feed leaves current status/
 *     assignee to the stat grid + Tasks view (both are correct as
 *     present-state, not history) and only feeds the chronological list
 *     with facts that have their own real, immutable timestamp.
 *
 * Review attribution (the third MAN-110 Phase 1 ask): project_tasks has no
 * `reviewed_by` / `completed_by` column. A task reaching "done" today is
 * always project_task__update(status="done") called BY AN AGENT — verified
 * in skills_service.py: the tool checks only that the task belongs to the
 * caller's own project, not that the caller is the assignee, so it can be
 * the same agent self-closing its own work or a teammate agent closing it —
 * but never a human: the frontend's own PATCH-task client function
 * (patchFleetTask, fleet-data.ts) has zero callers anywhere in the UI, and
 * no button in TasksList sets status at all. So "reviewed by a human" is
 * not just untracked, it isn't a real code path yet — surfacing that
 * honestly means saying so in the UI (the caption under the stat grid
 * below) rather than drawing a reviewer chip with no real actor behind it.
 */

import { useMemo } from "react";
import { Bot, CheckCircle2, ListChecks, AlertTriangle, Clock3, Activity as ActivityIcon } from "lucide-react";

import {
  useWorkspaceActivity,
  type FleetAgent,
  type FleetTask,
  type WorkspaceActivityEvent,
} from "./fleet-data";
import { useWorkspaceMembers } from "./members-data";
import { timeAgo, formatDate } from "./fleet-presentation";
import { FleetListSkeleton } from "./fleet-states";

// How deep into the workspace-wide ledger to look before filtering down to
// this project's own agents. The ledger has no project_id to filter on
// server-side (see file header), so this is a client-side narrowing of a
// capped recent window — the "Recent activity" stat is captioned to make
// clear it is a recent window, not an all-time total.
const LEDGER_FETCH_LIMIT = 150;
const FEED_DISPLAY_CAP = 40;

type ActivityItem = {
  id: string;
  ts: string;
  kind: "agent" | "human" | "unattributed";
  title: string;
  meta: string;
  actorLabel: string;
  isWarn?: boolean;
};

function buildActivityItems(
  ledgerEvents: WorkspaceActivityEvent[],
  tasks: FleetTask[],
  agentNameByInstall: Map<string, string>,
  memberNameByUserId: Map<string, string>,
): ActivityItem[] {
  const items: ActivityItem[] = [];

  for (const e of ledgerEvents) {
    if (!e.install_id || !agentNameByInstall.has(e.install_id)) continue;
    if (!e.created_at) continue;
    items.push({
      id: e.id || `ledger-${e.install_id}-${e.created_at}`,
      ts: e.created_at,
      kind: "agent",
      title: e.title || e.action || "Agent activity",
      meta: [e.event_class, e.channel ? `via ${e.channel}` : null].filter(Boolean).join(" · "),
      actorLabel: agentNameByInstall.get(e.install_id) || "Agent",
      isWarn: e.review_required,
    });
  }

  for (const task of tasks) {
    if (task.created_at) {
      const createdByName = task.created_by ? memberNameByUserId.get(task.created_by) : undefined;
      items.push({
        id: `task-created-${task.id}`,
        ts: task.created_at,
        kind: createdByName ? "human" : "unattributed",
        title: `Created task "${task.title || "Untitled task"}"`,
        meta: "Task created",
        actorLabel: createdByName || (task.created_by ? "Unknown workspace member" : "Unknown"),
      });
    }

    // Real comments — task.metadata.comments, written by the
    // project_task__comment agent tool (project_tasks_service.add_task_comment).
    // Only ever agent-authored today (see file header), but resolved
    // generically in case a future write path adds a human author_type.
    const rawComments = task.metadata?.comments;
    if (Array.isArray(rawComments)) {
      for (const c of rawComments) {
        if (!c || typeof c !== "object") continue;
        const comment = c as { id?: string; author_type?: string; author_id?: string; body?: string; created_at?: string };
        if (!comment.created_at || !comment.body) continue;
        const authorType = String(comment.author_type || "").trim();
        const authorId = String(comment.author_id || "").trim();
        const agentName = authorId ? agentNameByInstall.get(authorId) : undefined;
        const memberName = authorId ? memberNameByUserId.get(authorId) : undefined;
        items.push({
          id: comment.id || `task-comment-${task.id}-${comment.created_at}`,
          ts: comment.created_at,
          kind: agentName ? "agent" : memberName ? "human" : "unattributed",
          title: comment.body.length > 160 ? `${comment.body.slice(0, 160)}…` : comment.body,
          meta: `Comment on "${task.title || "Untitled task"}"`,
          actorLabel: agentName || memberName || (authorType === "agent" ? "Agent" : "Unknown"),
        });
      }
    }
  }

  items.sort((a, b) => new Date(b.ts).getTime() - new Date(a.ts).getTime());
  return items;
}

function dayHeading(iso: string): string {
  const d = new Date(iso);
  const today = new Date();
  const yesterday = new Date();
  yesterday.setDate(today.getDate() - 1);
  const sameDay = (a: Date, b: Date) =>
    a.getFullYear() === b.getFullYear() && a.getMonth() === b.getMonth() && a.getDate() === b.getDate();
  if (sameDay(d, today)) return "Today";
  if (sameDay(d, yesterday)) return "Yesterday";
  return formatDate(d, { month: "long", day: "numeric" });
}

function groupByDay(items: ActivityItem[]): { day: string; items: ActivityItem[] }[] {
  const groups: { day: string; items: ActivityItem[] }[] = [];
  for (const item of items) {
    const heading = dayHeading(item.ts);
    const last = groups[groups.length - 1];
    if (last && last.day === heading) last.items.push(item);
    else groups.push({ day: heading, items: [item] });
  }
  return groups;
}

export function ProjectOverview({
  workspaceId,
  agents,
  tasks,
  tasksLoading,
  rollup,
}: {
  workspaceId: string;
  /** Agents already scoped to this project by the caller. */
  agents: FleetAgent[];
  /** Tasks already scoped to this project by the caller (useFleetTasks). */
  tasks: FleetTask[];
  tasksLoading: boolean;
  rollup: { usd_cost: number; total_tokens: number; events: number } | null;
}) {
  const { events: ledgerEvents, loading: activityLoading } = useWorkspaceActivity(workspaceId, LEDGER_FETCH_LIMIT);
  const { members } = useWorkspaceMembers(workspaceId);

  const agentNameByInstall = useMemo(
    () => new Map(agents.map((a) => [a.agent_id, a.label || "Unnamed agent"])),
    [agents],
  );
  const memberNameByUserId = useMemo(
    () => new Map(members.map((m) => [m.user_id, m.display_name || m.email])),
    [members],
  );

  const statusCounts = useMemo(() => {
    const counts: Record<string, number> = { open: 0, in_progress: 0, blocked: 0, awaiting_input: 0, done: 0 };
    for (const t of tasks) counts[t.status] = (counts[t.status] || 0) + 1;
    return counts;
  }, [tasks]);

  const items = useMemo(
    () => buildActivityItems(ledgerEvents, tasks, agentNameByInstall, memberNameByUserId),
    [ledgerEvents, tasks, agentNameByInstall, memberNameByUserId],
  );
  const dayGroups = useMemo(() => groupByDay(items.slice(0, FEED_DISPLAY_CAP)), [items]);
  const hasDoneTasks = statusCounts.done > 0;
  const loading = tasksLoading || activityLoading;
  // Built as one plain string, not mixed JSX text/expressions across lines —
  // JSX's per-line whitespace trimming drops the leading space of a text
  // node that starts a new source line right after an expression container
  // (verified live: an earlier version of this string rendered "wasclosed"
  // with no space), so this sidesteps that entirely.
  const reviewCaption = hasDoneTasks
    ? `Review attribution isn't tracked yet: the ${statusCounts.done} task${statusCounts.done === 1 ? "" : "s"} marked ` +
      `Done above ${statusCounts.done === 1 ? "was" : "were"} closed by an agent calling its own status update — this ` +
      `platform doesn't yet record a separate human review step, or distinguish an agent closing its own work ` +
      `from another agent closing a teammate's.`
    : "";

  return (
    <div className="fleet-detail-overview">
      <div className="fleet-stat-grid">
        <div className="fleet-stat-card">
          <div className="fleet-stat-value">{agents.length}</div>
          <div className="fleet-stat-label"><Bot size={12} strokeWidth={1.75} style={{ verticalAlign: -2, marginRight: 4 }} />Agents</div>
        </div>
        <div className="fleet-stat-card">
          <div className="fleet-stat-value">{statusCounts.open + statusCounts.in_progress}</div>
          <div className="fleet-stat-label"><ListChecks size={12} strokeWidth={1.75} style={{ verticalAlign: -2, marginRight: 4 }} />Open / in progress</div>
        </div>
        <div className="fleet-stat-card">
          <div className="fleet-stat-value">{statusCounts.blocked + statusCounts.awaiting_input}</div>
          <div className="fleet-stat-label"><AlertTriangle size={12} strokeWidth={1.75} style={{ verticalAlign: -2, marginRight: 4 }} />Needs input</div>
        </div>
        <div className="fleet-stat-card">
          <div className="fleet-stat-value">{statusCounts.done}</div>
          <div className="fleet-stat-label"><CheckCircle2 size={12} strokeWidth={1.75} style={{ verticalAlign: -2, marginRight: 4 }} />Done</div>
        </div>
        <div className="fleet-stat-card">
          <div className="fleet-stat-value">{items.length}</div>
          <div className="fleet-stat-label"><ActivityIcon size={12} strokeWidth={1.75} style={{ verticalAlign: -2, marginRight: 4 }} />Recent activity</div>
        </div>
        <div className="fleet-stat-card">
          <div className="fleet-stat-value">${(rollup?.usd_cost ?? 0).toFixed(2)}</div>
          <div className="fleet-stat-label"><Clock3 size={12} strokeWidth={1.75} style={{ verticalAlign: -2, marginRight: 4 }} />Cost this month</div>
        </div>
      </div>

      {hasDoneTasks && (
        <p className="fleet-cell-muted" style={{ fontSize: 12, marginTop: 10, marginBottom: 0 }}>
          {reviewCaption}
        </p>
      )}

      <div className="fleet-detail-section-title" style={{ marginTop: 20 }}>Activity</div>
      {loading && items.length === 0 ? (
        <FleetListSkeleton rows={4} />
      ) : items.length === 0 ? (
        <div className="fleet-empty">
          <div className="fleet-empty-icon">
            <ActivityIcon size={20} strokeWidth={1.75} />
          </div>
          <div className="fleet-empty-title">No activity yet</div>
          <div className="fleet-empty-desc">
            Activity from this project&apos;s agents and tasks will show up here as it happens.
          </div>
        </div>
      ) : (
        <div className="fleet-activity">
          {dayGroups.map((group) => (
            <div key={group.day} className="fleet-activity-day-group">
              <div className="fleet-activity-day-heading">{group.day}</div>
              {group.items.map((item) => (
                <div key={item.id} className="fleet-activity-item">
                  <div className={`fleet-activity-dot${item.isWarn ? " is-warn" : ""}`} />
                  <div style={{ flex: 1, minWidth: 0 }}>
                    <div className="fleet-activity-title">{item.title}</div>
                    <div className="fleet-activity-meta">
                      <span>{item.actorLabel}</span>
                      {item.meta && (
                        <>
                          <span>·</span>
                          <span>{item.meta}</span>
                        </>
                      )}
                      <span>·</span>
                      <span className="fleet-activity-time">{timeAgo(item.ts)}</span>
                    </div>
                  </div>
                </div>
              ))}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
