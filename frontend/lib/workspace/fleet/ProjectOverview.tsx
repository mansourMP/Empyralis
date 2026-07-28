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
 *     SHAPE OF THAT ROLL-UP (2026-07-29 redesign). It used to be six
 *     identical bordered tiles, each a big number over a caption — the most
 *     generic dashboard pattern there is, and one that spent a sixth of the
 *     page telling you "Done: 0". Linear's own project overview has no stat
 *     tiles at all: a quiet inline properties row, then content. This follows
 *     that, and the numbers are ranked by how actionable they actually are:
 *
 *       · A properties LINE (agents · tasks · cost this month · last
 *         activity) — reference figures, inline, unboxed, one line. Cost in
 *         particular is reference information, not a headline.
 *       · A "needs you" CALLOUT that only exists when the count is non-zero.
 *         A permanent tile reading 0 is noise; a box that appears only when
 *         there is something to do is a signal.
 *       · A single stacked STATUS BAR over all seven statuses, plus a legend.
 *         One element answers "where is the work" — which six separate
 *         numbers genuinely could not. Colour and the ring glyphs both come
 *         from task-status.tsx (the --task-* tokens + TaskStatusIcon), the
 *         same vocabulary the board columns and task rows use, so a segment
 *         and its column are recognisably the same thing. No new colours.
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
 * `reviewed_by` / `completed_by` column, so a task's status carries no
 * record of WHO set it. Until the kanban board landed, "done" could only
 * ever be project_task__update(status="done") called BY AN AGENT — the
 * frontend's own patchFleetTask had zero callers and no control in the UI
 * set a status at all. A person can now move a card (TasksBoard, and the
 * task detail drawer's Status row), which makes the actor genuinely
 * ambiguous rather than merely unrecorded: agent-self-closing,
 * agent-closing-a-teammate, and human-approving are indistinguishable after
 * the fact. That's what the note under the status legend says out loud,
 * rather than drawing a reviewer chip with no real actor behind it. It sits
 * directly under the legend on purpose: that legend is where the Done count
 * is now stated, so the caveat is attached to the number it qualifies.
 */

import { useMemo } from "react";
import { Bot, ListChecks, AlertTriangle, CircleDollarSign, Clock3, Activity as ActivityIcon } from "lucide-react";

import {
  countTasksByStatus,
  useWorkspaceActivity,
  FLEET_TASK_STATUSES,
  type FleetAgent,
  type FleetTask,
  type FleetTaskStatus,
  type WorkspaceActivityEvent,
} from "./fleet-data";
import { TaskStatusIcon, taskStatusLabel, taskStatusVisual } from "./task-status";
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

  // One shared counter (fleet-data.countTasksByStatus) for this roll-up AND
  // the board's per-column header counts, so the two can never disagree
  // about what a status means or how a legacy `open` row is bucketed.
  const statusCounts = useMemo(() => countTasksByStatus(tasks), [tasks]);

  const items = useMemo(
    () => buildActivityItems(ledgerEvents, tasks, agentNameByInstall, memberNameByUserId),
    [ledgerEvents, tasks, agentNameByInstall, memberNameByUserId],
  );
  const dayGroups = useMemo(() => groupByDay(items.slice(0, FEED_DISPLAY_CAP)), [items]);
  const hasDoneTasks = statusCounts.done > 0;
  const loading = tasksLoading || activityLoading;

  const totalTasks = tasks.length;
  const donePct = totalTasks > 0 ? Math.round((statusCounts.done / totalTasks) * 100) : 0;
  // Only statuses that actually have tasks get a bar segment AND a legend
  // entry — the two are built from the same list so a stripe and its label
  // always correspond one-to-one. A status with nothing in it is not news.
  const presentStatuses = useMemo(
    () => FLEET_TASK_STATUSES.filter((s) => statusCounts[s] > 0),
    [statusCounts],
  );
  // "Waiting on a person", same definition the old Needs-you tile used:
  // blocked + awaiting_input are the agent-has-stopped states, and in_review
  // is finished work that has NOT left the board — the reader of this page is
  // exactly who it is parked on. Listed review-first because that is the
  // cheapest of the three to clear.
  const needsYouStatuses: FleetTaskStatus[] = ["in_review", "awaiting_input", "blocked"];
  const needsYouCount = needsYouStatuses.reduce((n, s) => n + statusCounts[s], 0);
  const lastActivityAt = items.length > 0 ? items[0].ts : null;
  // Built as one plain string, not mixed JSX text/expressions across lines —
  // JSX's per-line whitespace trimming drops the leading space of a text
  // node that starts a new source line right after an expression container
  // (verified live: an earlier version of this string rendered "wasclosed"
  // with no space), so this sidesteps that entirely.
  const reviewCaption = hasDoneTasks
    ? `Review attribution isn't tracked yet: nothing records who moved ${statusCounts.done === 1 ? "the task" : `the ${statusCounts.done} tasks`} ` +
      `marked Done above. project_tasks has no reviewed_by or completed_by column, so an agent closing its own work, ` +
      `another agent closing a teammate's, and a person moving the card on the board all look identical afterwards.`
    : "";

  return (
    <div className="fleet-detail-overview">
      {/* Properties line — Linear's inline chip row. Reference figures, not
          headlines: unboxed, one line, and quiet enough that the callout and
          the bar below own the page. */}
      <div className="fleet-overview-props">
        <span className="fleet-overview-prop">
          <Bot size={13} strokeWidth={1.75} />
          <span className="fleet-overview-num">{agents.length}</span>
          {agents.length === 1 ? "agent" : "agents"}
        </span>
        <span className="fleet-overview-prop">
          <ListChecks size={13} strokeWidth={1.75} />
          <span className="fleet-overview-num">{totalTasks}</span>
          {totalTasks === 1 ? "task" : "tasks"}
        </span>
        <span className="fleet-overview-prop">
          <CircleDollarSign size={13} strokeWidth={1.75} />
          <span className="fleet-overview-num">${(rollup?.usd_cost ?? 0).toFixed(2)}</span>
          this month
        </span>
        <span className="fleet-overview-prop">
          <Clock3 size={13} strokeWidth={1.75} />
          {lastActivityAt ? (
            <>
              last activity
              <span className="fleet-overview-num">{timeAgo(lastActivityAt)}</span>
            </>
          ) : (
            "no activity yet"
          )}
        </span>
      </div>

      {/* The one number worth interrupting for — and only when there IS one.
          A permanent tile reading "0 needs you" is noise; this box existing at
          all is the signal. */}
      {needsYouCount > 0 && (
        <div className="fleet-overview-callout">
          <AlertTriangle className="fleet-overview-callout-icon" size={15} strokeWidth={1.75} />
          <div className="fleet-overview-callout-body">
            <div className="fleet-overview-callout-title">
              {needsYouCount === 1 ? "1 task is waiting on you" : `${needsYouCount} tasks are waiting on you`}
            </div>
            <div className="fleet-overview-callout-breakdown">
              {needsYouStatuses
                .filter((s) => statusCounts[s] > 0)
                .map((s) => (
                  <span key={s} className="fleet-overview-callout-chip">
                    <TaskStatusIcon status={s} size={13} />
                    <span className="fleet-overview-num">{statusCounts[s]}</span>
                    {taskStatusLabel(s).toLowerCase()}
                  </span>
                ))}
            </div>
          </div>
        </div>
      )}

      {/* Where the work actually sits, in one element. Segment widths are
          flex-grow ratios off the raw counts, so the bar IS the distribution
          rather than a picture of it; min-width keeps a 1-of-40 status from
          collapsing to an invisible sliver. */}
      <div className="fleet-overview-progress">
        <div className="fleet-overview-progress-head">
          <span className="fleet-overview-progress-label">Status</span>
          {totalTasks > 0 && (
            <span className="fleet-overview-progress-count">
              <span className="fleet-overview-num">{statusCounts.done}</span> of{" "}
              <span className="fleet-overview-num">{totalTasks}</span> done · {donePct}%
            </span>
          )}
        </div>

        {totalTasks > 0 ? (
          <div
            className="fleet-status-bar"
            role="img"
            aria-label={presentStatuses
              .map((s) => `${taskStatusLabel(s)}: ${statusCounts[s]}`)
              .join(", ")}
          >
            {presentStatuses.map((s) => (
              <span
                key={s}
                className={`fleet-status-bar-seg${s === "backlog" ? " fleet-status-bar-seg--backlog" : ""}`}
                style={{ flexGrow: statusCounts[s], background: `var(${taskStatusVisual(s).colorVar})` }}
                title={`${taskStatusLabel(s)} — ${statusCounts[s]}`}
              />
            ))}
          </div>
        ) : (
          // Empty AND loading both render the same flat track, so the block
          // never changes height when tasks arrive — only what sits under it
          // does. An empty project gets an explanation, not a row of zeroes.
          <div className="fleet-status-bar fleet-status-bar--empty" aria-hidden />
        )}

        {totalTasks > 0 ? (
          <div className="fleet-overview-legend">
            {presentStatuses.map((s) => (
              <span key={s} className="fleet-overview-legend-item">
                <TaskStatusIcon status={s} size={13} />
                <span className="fleet-overview-num">{statusCounts[s]}</span>
                {taskStatusLabel(s)}
              </span>
            ))}
          </div>
        ) : (
          <p className="fleet-overview-note">
            {tasksLoading
              ? "Loading tasks…"
              : "No tasks in this project yet. Once there are, this bar shows how they sit across backlog, todo, in progress, needs input, blocked, in review and done."}
          </p>
        )}

        {/* Attached to the legend, which is where the Done count is now
            stated — the caveat belongs next to the number it qualifies. */}
        {hasDoneTasks && <p className="fleet-overview-note">{reviewCaption}</p>}
      </div>

      <div className="fleet-overview-section-head">
        <span className="fleet-detail-section-title">Activity</span>
        {items.length > 0 && (
          <span className="fleet-overview-section-meta">
            {items.length > FEED_DISPLAY_CAP
              ? `Showing ${FEED_DISPLAY_CAP} of ${items.length} recent events`
              : `${items.length} recent ${items.length === 1 ? "event" : "events"}`}
          </span>
        )}
      </div>
      {loading && items.length === 0 ? (
        // rowHeight matches .fleet-activity-item's real height (12px top/bottom
        // padding + a 13px title, 4px gap and an 11px meta line + the 1px
        // divider = ~64px) — see FleetListSkeleton's MAN-113 note. This is the
        // project's default landing tab, so an un-pinned 37px skeleton row made
        // the whole activity ledger jump the moment the real items swapped in.
        <FleetListSkeleton rows={4} rowHeight={64} />
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
        <div className="fleet-activity fleet-activity--flush">
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
