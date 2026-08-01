"use client";

/**
 * Project Overview — rebuilt around "who is doing what" (MAN-145 follow-up).
 *
 * Founder verdict on the prior version (four counters + a status
 * distribution bar + a raw activity feed): it showed things he didn't need
 * ("Configured · Lark · fleet_control · 3h ago" — an internal event name and
 * an internal subsystem name, repeated) and omitted the thing he actually
 * asked for: "I must see what people I have, who's doing what."
 *
 * Rebuilt around four questions, in priority order, every one of them
 * answerable from data this platform already collects — nothing here is
 * invented or mocked:
 *
 *  1. WHO IS ON THIS PROJECT — the new "Team" roster. Every agent already
 *     scoped to this project (the `agents` prop) plus every workspace member
 *     (`members` — "project member" == "workspace member" today, no
 *     per-project ACL table yet, same MAN-70 ruling page.tsx's own
 *     MemberAvatarStack call already relies on), each paired with its most
 *     relevant ACTIVE task in this project via `assignee_agent_id` /
 *     `assignee_user_id` (project_tasks_single_assignee_check backstops the
 *     "one or the other, never both" invariant this reads). A row with no
 *     active task says so honestly rather than going quiet.
 *  2. WHAT NEEDS THE OWNER — the existing "needs you" callout, unchanged in
 *     substance (awaiting_input/blocked/in_review — the three states that
 *     mean a human decision is required), just moved up to sit directly
 *     under the roster instead of buried under a properties line.
 *  3. WHAT IS IN FLIGHT — deliberately NOT a third section. An in_progress
 *     task is the highest-ranked "active task" a roster row can show (see
 *     ACTIVE_TASK_RANK below), so "who's working on what right now" already
 *     answers "what's in flight and who holds it" — a dedicated list would
 *     just repeat the same {avatar, name, task title} rows twice on one
 *     page, which is exactly the surface-for-its-own-sake this platform's
 *     own doctrine ("best, not most") rules out.
 *  4. RECENT ACTIVITY, MADE HUMAN — kept, but: consecutive duplicate rows
 *     collapse into one with a ×N count (the "four consecutive Configured
 *     rows" the founder screenshotted), event_class ("fleet_control") never
 *     renders — it's an internal subsystem identifier, not something a
 *     human asked to read — and a channel key humanizes via the same
 *     CHANNEL_LABELS map AgentsList/FleetAgentDetail already use. Any row
 *     whose title still doesn't read as human (a legacy pre-humanization
 *     write) is dropped rather than shown — "if an event can't be phrased
 *     for a human, don't show it."
 *
 * REMOVED FROM THE PRIOR VERSION: the six-status stacked distribution bar +
 * legend + review-attribution caveat. It answered none of the four questions
 * above on its own — "where is the work across all seven states" is exactly
 * the kind of aggregate the founder's own "shows things he shouldn't need to
 * see" verdict was about, and every number it carried is now reachable
 * either from the roster (in-flight), the needs-you callout (blocked/
 * awaiting_input/in_review), or the Tasks tab's own board (the real place to
 * see full distribution). This is a deliberate cut, flagged here rather than
 * silently dropped — reintroducing it is one section, not a redesign, if
 * that call is wrong.
 *
 * Kept, unchanged in shape: the properties line (agents · tasks · cost this
 * month · last activity) — compact reference figures, not the substance, but
 * still useful enough to earn their one line at the top.
 *
 * Every avatar/row shape here is reused, not invented: `.fleet-agent-avatar`
 * / AgentSigil and `.fleet-member-avatar` / MemberAvatar are the exact same
 * identity marks AgentsList/TasksList/MemberAvatarStack already draw, and
 * roster rows reuse `.fleet-activity-item`'s row shell (avatar + title line
 * + meta line, hairline border-bottom) — the same shape the activity feed
 * below it uses for a different kind of row. The only new CSS is
 * project-overview.css, a handful of rules making that row a real
 * (clickable-when-there's-something-to-open) Link — fleet-theme.css and
 * theme-tokens.css are untouched (owned by a parallel dark-mode pass).
 */

import { useMemo } from "react";
import Link from "next/link";
import { Bot, ListChecks, AlertTriangle, CircleDollarSign, Clock3, Activity as ActivityIcon, Users } from "lucide-react";

import "./project-overview.css";

import {
  countTasksByStatus,
  normalizeTaskStatus,
  useWorkspaceActivity,
  type FleetAgent,
  type FleetTask,
  type FleetTaskStatus,
  type WorkspaceActivityEvent,
  type WorkspaceRosterEntry,
} from "./fleet-data";
import { TaskStatusIcon, taskStatusLabel } from "./task-status";
import { MemberAvatar } from "./MemberAvatarStack";
import { AgentSigil } from "./fleet-indicators";
import type { WorkspaceMember } from "./members-data";
import { CHANNEL_LABELS } from "./fleet-icons";
import { timeAgo, formatDate } from "./fleet-presentation";
import { FleetListSkeleton } from "./fleet-states";

// How deep into the workspace-wide ledger to look before filtering down to
// this project's own agents. The ledger has no project_id to filter on
// server-side (see file header), so this is a client-side narrowing of a
// capped recent window — the "Recent activity" stat is captioned to make
// clear it is a recent window, not an all-time total.
const LEDGER_FETCH_LIMIT = 150;
const FEED_DISPLAY_CAP = 40;

// Legacy rows written before the 2026-07-09 fleet_control-title
// humanization fix (fleet_tools.py's _humanize_fleet_action) still carry a
// raw "Fleet: {action} → {id}" shape, or a bare install/workspace id as the
// title — the same guard AgentsList.tsx's activityPreviewText applies to an
// agent's own preview line. An event that still looks like this after the
// server-side fix is old enough to just skip: showing raw plumbing to a
// human is worse than showing one fewer row.
const RAW_INTERNAL_TITLE = /^Fleet:\s|ainstall_[a-z0-9]|(?:^|[\s:])ws_[a-z0-9]/i;

function humanChannel(channel: string | null): string | null {
  const key = (channel || "").trim();
  if (!key) return null;
  return CHANNEL_LABELS[key] || key.replace(/[_-]+/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
}

type ActivityItem = {
  id: string;
  ts: string;
  kind: "agent" | "external_agent" | "human" | "unattributed";
  title: string;
  meta: string;
  actorLabel: string;
  isWarn?: boolean;
  /** How many consecutive, otherwise-identical events this one row stands
   *  in for — see dedupeConsecutive below. 1 for a row with no repeats. */
  count: number;
};

/** Four consecutive "Configured · Lark · fleet_control · 3h ago" rows told a
 *  reader nothing four times over. Collapses consecutive (already
 *  time-sorted) events that share a title+actor+meta into one row carrying
 *  a ×N count and the most recent of the run's timestamps — a repeat two
 *  days apart is still two separate, worth-seeing rows; only genuinely
 *  back-to-back repeats collapse. */
function dedupeConsecutive(items: Omit<ActivityItem, "count">[]): ActivityItem[] {
  const out: ActivityItem[] = [];
  for (const item of items) {
    const prev = out[out.length - 1];
    if (prev && prev.kind === item.kind && prev.title === item.title && prev.actorLabel === item.actorLabel && prev.meta === item.meta) {
      prev.count += 1;
      continue;
    }
    out.push({ ...item, count: 1 });
  }
  return out;
}

/** "ext_agent_5f3a2b1c9d0e4f11" -> "5f3a2b1c". Only reached when an external
 *  agent has no name from either the live roster or its stored snapshot — a
 *  short, stable discriminator, never the whole opaque id. */
function externalAgentShortId(id: string): string {
  return id.replace(/^ext_agent_/, "").slice(0, 8);
}

/** Who did this, and what kind of participant are they.
 *
 *  Three kinds act on a board, and this feed only ever resolved two of them.
 *  A PLATFORM agent's id is a `workspace_agent_installs` id; a HUMAN's is a
 *  workspace member's user_id; an EXTERNAL agent — a Claude Code / Codex
 *  session connected through our MCP server at /mcp — carries an
 *  `ext_agent_<hex16>` id that is in NEITHER map, so it landed on the
 *  literal word "Unknown" on a feed whose entire job is saying who did what.
 *
 *  For an external agent the order is live-then-snapshot: the roster is
 *  current truth, `snapshotName` is the name it had when it wrote (stored by
 *  the backend at write time) and covers a row whose roster entry is gone.
 *  With neither, the fallback still names something true and distinguishing
 *  ("External agent 5f3a2b1c"), never a bare "Unknown".
 *
 *  It also fixes a quieter miss on the same line: a task CREATED by a
 *  platform agent was only ever looked up in the member map, so it read
 *  "Unknown workspace member" — an agent, described as a person we failed to
 *  find. Both maps are consulted for every actor now. */
function resolveActor(
  id: string,
  authorType: string,
  snapshotName: string,
  agentNameByInstall: Map<string, string>,
  memberNameByUserId: Map<string, string>,
  externalAgentNameById: Map<string, string>,
): { label: string; kind: ActivityItem["kind"] } {
  const actorId = (id || "").trim();
  const type = (authorType || "").trim();

  const agentName = actorId ? agentNameByInstall.get(actorId) : undefined;
  if (agentName) return { label: agentName, kind: "agent" };

  const memberName = actorId ? memberNameByUserId.get(actorId) : undefined;
  if (memberName) return { label: memberName, kind: "human" };

  const externalName = actorId ? externalAgentNameById.get(actorId) : undefined;
  if (externalName) return { label: externalName, kind: "external_agent" };

  if (type === "external_agent" || actorId.startsWith("ext_agent_")) {
    const snapshot = (snapshotName || "").trim();
    if (snapshot) return { label: snapshot, kind: "external_agent" };
    const short = externalAgentShortId(actorId);
    return { label: short ? `External agent ${short}` : "External agent", kind: "external_agent" };
  }

  if (type === "agent") return { label: actorId || "Agent", kind: "agent" };
  if (type === "user" || type === "human") return { label: actorId || "Person", kind: "human" };
  // Genuinely nothing to attribute this to — say that, rather than "Unknown",
  // which reads as a failed lookup instead of an absent one.
  return { label: actorId || "Unattributed", kind: "unattributed" };
}

function buildActivityItems(
  ledgerEvents: WorkspaceActivityEvent[],
  tasks: FleetTask[],
  agentNameByInstall: Map<string, string>,
  memberNameByUserId: Map<string, string>,
  externalAgentNameById: Map<string, string>,
): ActivityItem[] {
  const items: Omit<ActivityItem, "count">[] = [];

  for (const e of ledgerEvents) {
    if (!e.install_id || !agentNameByInstall.has(e.install_id)) continue;
    if (!e.created_at) continue;
    const title = (e.title || e.action || "").trim();
    // Never surface an internal subsystem identifier (event_class, e.g.
    // "fleet_control") or an unhumanized legacy title — if this event can't
    // be phrased for a human, skip it rather than show plumbing.
    if (!title || RAW_INTERNAL_TITLE.test(title)) continue;
    const channel = humanChannel(e.channel);
    items.push({
      id: e.id || `ledger-${e.install_id}-${e.created_at}`,
      ts: e.created_at,
      kind: "agent",
      title,
      meta: channel ? `via ${channel}` : "",
      actorLabel: agentNameByInstall.get(e.install_id) || "Agent",
      isWarn: e.review_required,
    });
  }

  for (const task of tasks) {
    if (task.created_at) {
      // The creator's name snapshot, written at INSERT time for an external
      // agent (project_tasks_service.create_task) — a fallback only; the
      // roster above is preferred whenever it still has the row.
      const createdBySnapshot = String(
        (task.metadata as { created_by_display_name?: unknown } | undefined)?.created_by_display_name || "",
      );
      const creator = resolveActor(
        String(task.created_by || ""),
        "",
        createdBySnapshot,
        agentNameByInstall,
        memberNameByUserId,
        externalAgentNameById,
      );
      items.push({
        id: `task-created-${task.id}`,
        ts: task.created_at,
        kind: creator.kind,
        title: `Created task "${task.title || "Untitled task"}"`,
        meta: "Task created",
        actorLabel: creator.label,
      });
    }

    // Real comments — task.metadata.comments, written by the
    // project_task__comment agent tool, the human composer
    // (add_human_task_comment) and the MCP tool empyralis_comment_on_task,
    // all three through project_tasks_service.add_task_comment. Author kind
    // is resolved generically, so all three read as themselves.
    const rawComments = task.metadata?.comments;
    if (Array.isArray(rawComments)) {
      for (const c of rawComments) {
        if (!c || typeof c !== "object") continue;
        const comment = c as {
          id?: string;
          author_type?: string;
          author_id?: string;
          author_display_name?: string;
          body?: string;
          created_at?: string;
        };
        if (!comment.created_at || !comment.body) continue;
        const author = resolveActor(
          String(comment.author_id || ""),
          String(comment.author_type || ""),
          String(comment.author_display_name || ""),
          agentNameByInstall,
          memberNameByUserId,
          externalAgentNameById,
        );
        items.push({
          id: comment.id || `task-comment-${task.id}-${comment.created_at}`,
          ts: comment.created_at,
          kind: author.kind,
          title: comment.body.length > 160 ? `${comment.body.slice(0, 160)}…` : comment.body,
          meta: `Comment on "${task.title || "Untitled task"}"`,
          actorLabel: author.label,
        });
      }
    }
  }

  items.sort((a, b) => new Date(b.ts).getTime() - new Date(a.ts).getTime());
  return dedupeConsecutive(items);
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

// Which of a person/agent's own active (non-done) tasks is the one worth
// showing in a single roster-row slot — in_progress ranks first because
// that IS "in flight, who holds it" (see file header, point 3): the
// roster's top-ranked task doubles as that answer without a second section.
// awaiting_input/blocked tie (both are "the agent stopped, a human is
// needed" per FLEET_TASK_NEEDS_HUMAN in fleet-data.ts) and rank just under
// in_progress; in_review (finished, parked for approval) and todo trail.
const ACTIVE_TASK_RANK: Partial<Record<FleetTaskStatus, number>> = {
  in_progress: 0,
  awaiting_input: 1,
  blocked: 1,
  in_review: 2,
  todo: 3,
  backlog: 4,
};

function pickCurrentTask(tasks: FleetTask[]): { task: FleetTask | null; extra: number } {
  const active = tasks.filter((t) => normalizeTaskStatus(t.status) !== "done");
  if (active.length === 0) return { task: null, extra: 0 };
  const sorted = [...active].sort((a, b) => {
    const ra = ACTIVE_TASK_RANK[normalizeTaskStatus(a.status)] ?? 5;
    const rb = ACTIVE_TASK_RANK[normalizeTaskStatus(b.status)] ?? 5;
    if (ra !== rb) return ra - rb;
    // Most recently touched first — ISO 8601 timestamps sort correctly as
    // plain strings, no Date parsing needed for a simple compare.
    return (b.updated_at || b.created_at || "").localeCompare(a.updated_at || a.created_at || "");
  });
  return { task: sorted[0], extra: sorted.length - 1 };
}

type RosterEntry = {
  key: string;
  kind: "agent" | "member";
  id: string;
  name: string;
  role?: WorkspaceMember["role"];
  task: FleetTask | null;
  extraActiveCount: number;
  /** Where clicking this row goes. A member with no active task has nowhere
   *  honest to send you (no per-person profile page exists), so `null` — a
   *  dead link is worse than a plain row. An agent always has its own
   *  detail page, so it's never null there. */
  href: string | null;
};

export function ProjectOverview({
  workspaceId,
  agents,
  tasks,
  tasksLoading,
  rollup,
  members,
  externalAgents,
  taskHref,
  agentHref,
}: {
  workspaceId: string;
  /** Agents already scoped to this project by the caller. */
  agents: FleetAgent[];
  /** Tasks already scoped to this project by the caller (useFleetTasks). */
  tasks: FleetTask[];
  tasksLoading: boolean;
  rollup: { usd_cost: number; total_tokens: number; events: number } | null;
  /** Workspace members — "project member" == "workspace member" today, same
   *  data page.tsx's own MemberAvatarStack call already fetches; passed
   *  down rather than re-fetched here so the two never show a different
   *  roster for a heartbeat after either poll ticks. */
  members: WorkspaceMember[];
  /** The workspace's EXTERNAL agents (useWorkspaceRoster) — MCP-connected
   *  sessions that create and comment on tasks here. NOT part of the Team
   *  roster above: an external agent is workspace-scoped with no project
   *  membership and cannot hold a task (see list_unified_roster's docstring),
   *  so a row for one would sit on every project reading "No active task"
   *  forever. It is a real actor in the ACTIVITY feed, which is where it
   *  actually appears — this is the lookup that names it there. */
  externalAgents: WorkspaceRosterEntry[];
  /** Where a task's own page lives, for the roster row + needs-you callout
   *  to link straight into it — same builder page.tsx hands TasksBoard/
   *  TasksList/TasksGroupedList. */
  taskHref: (taskId: string) => string;
  /** Where an agent's own Overview tab lives, for a roster row with no
   *  active task to still go somewhere real. */
  agentHref: (agentId: string) => string;
}) {
  const { events: ledgerEvents, loading: activityLoading } = useWorkspaceActivity(workspaceId, LEDGER_FETCH_LIMIT);

  const agentNameByInstall = useMemo(
    () => new Map(agents.map((a) => [a.agent_id, a.label || "Unnamed agent"])),
    [agents],
  );
  const memberNameByUserId = useMemo(
    () => new Map(members.map((m) => [m.user_id, m.display_name || m.email])),
    [members],
  );
  const externalAgentNameById = useMemo(
    () =>
      new Map(
        externalAgents
          .filter((e) => (e.display_name || "").trim().length > 0)
          .map((e) => [e.id, e.display_name.trim()]),
      ),
    [externalAgents],
  );

  // One shared counter (fleet-data.countTasksByStatus) for this roll-up AND
  // the board's per-column header counts, so the two can never disagree
  // about what a status means.
  const statusCounts = useMemo(() => countTasksByStatus(tasks), [tasks]);

  const items = useMemo(
    () =>
      buildActivityItems(
        ledgerEvents,
        tasks,
        agentNameByInstall,
        memberNameByUserId,
        externalAgentNameById,
      ),
    [ledgerEvents, tasks, agentNameByInstall, memberNameByUserId, externalAgentNameById],
  );
  const dayGroups = useMemo(() => groupByDay(items.slice(0, FEED_DISPLAY_CAP)), [items]);
  const loading = tasksLoading || activityLoading;

  const totalTasks = tasks.length;
  const lastActivityAt = items.length > 0 ? items[0].ts : null;

  // "Waiting on a person" — blocked + awaiting_input are the agent-stopped
  // states, and in_review is finished work that hasn't left the board. The
  // reader of this page is exactly who all three are parked on.
  const needsYouStatuses: FleetTaskStatus[] = ["in_review", "awaiting_input", "blocked"];
  const needsYouCount = needsYouStatuses.reduce((n, s) => n + statusCounts[s], 0);

  // The roster — every agent already scoped to this project, plus every
  // workspace member, each paired with its own most-relevant active task.
  const tasksByAgent = useMemo(() => {
    const m = new Map<string, FleetTask[]>();
    for (const t of tasks) {
      if (!t.assignee_agent_id) continue;
      const arr = m.get(t.assignee_agent_id) || [];
      arr.push(t);
      m.set(t.assignee_agent_id, arr);
    }
    return m;
  }, [tasks]);
  const tasksByMember = useMemo(() => {
    const m = new Map<string, FleetTask[]>();
    for (const t of tasks) {
      if (!t.assignee_user_id) continue;
      const arr = m.get(t.assignee_user_id) || [];
      arr.push(t);
      m.set(t.assignee_user_id, arr);
    }
    return m;
  }, [tasks]);

  const roster = useMemo(() => {
    const entries: RosterEntry[] = [];
    for (const a of agents) {
      const { task, extra } = pickCurrentTask(tasksByAgent.get(a.agent_id) || []);
      entries.push({
        key: `agent-${a.agent_id}`,
        kind: "agent",
        id: a.agent_id,
        name: a.label || "Unnamed agent",
        task,
        extraActiveCount: extra,
        href: task ? taskHref(task.id) : agentHref(a.agent_id),
      });
    }
    for (const m of members) {
      const { task, extra } = pickCurrentTask(tasksByMember.get(m.user_id) || []);
      entries.push({
        key: `member-${m.user_id}`,
        kind: "member",
        id: m.user_id,
        name: m.display_name || m.email,
        role: m.role,
        task,
        extraActiveCount: extra,
        href: task ? taskHref(task.id) : null,
      });
    }
    entries.sort((a, b) => {
      const ra = a.task ? (ACTIVE_TASK_RANK[normalizeTaskStatus(a.task.status)] ?? 5) : 9;
      const rb = b.task ? (ACTIVE_TASK_RANK[normalizeTaskStatus(b.task.status)] ?? 5) : 9;
      if (ra !== rb) return ra - rb;
      return a.name.localeCompare(b.name);
    });
    return entries;
  }, [agents, members, tasksByAgent, tasksByMember, taskHref, agentHref]);

  return (
    <div className="fleet-detail-overview">
      {/* Properties line — compact reference figures, not the substance
          (see file header). Unboxed, one line. */}
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

      {/* The headline: who is on this project, and what each is doing right
          now. Agents first-class alongside people — both are "who's doing
          what" to the owner of this project. */}
      <div className="fleet-overview-section-head">
        <h2 className="fleet-detail-section-title">Team</h2>
        <span className="fleet-overview-section-meta">
          {agents.length} {agents.length === 1 ? "agent" : "agents"} · {members.length} {members.length === 1 ? "person" : "people"}
        </span>
      </div>
      {roster.length === 0 ? (
        <div className="fleet-empty">
          <div className="fleet-empty-icon">
            <Users size={20} strokeWidth={1.75} />
          </div>
          <div className="fleet-empty-title">Nobody here yet</div>
          <div className="fleet-empty-desc">Add an agent to this project to see it here.</div>
        </div>
      ) : (
        <div className="fleet-activity fleet-activity--flush">
          {roster.map((entry) => {
            const avatar =
              entry.kind === "agent" ? (
                <span className="fleet-agent-avatar">
                  <AgentSigil seed={entry.id} size={16} />
                </span>
              ) : (
                <MemberAvatar name={entry.name} role={entry.role} size="sm" />
              );
            const body = (
              <div style={{ flex: 1, minWidth: 0 }}>
                <div className="fleet-activity-title">{entry.name}</div>
                <div className="fleet-activity-meta">
                  {entry.task ? (
                    <>
                      <TaskStatusIcon status={entry.task.status} size={12} />
                      <span>{entry.task.title || "Untitled task"}</span>
                      {entry.extraActiveCount > 0 && (
                        <span style={{ color: "var(--text-muted)" }}>
                          +{entry.extraActiveCount} more
                        </span>
                      )}
                    </>
                  ) : (
                    <span>{entry.kind === "agent" ? "No active task" : "No active task in this project"}</span>
                  )}
                </div>
              </div>
            );
            return entry.href ? (
              <Link key={entry.key} href={entry.href} className="fleet-activity-item fleet-overview-roster-row">
                {avatar}
                {body}
              </Link>
            ) : (
              <div key={entry.key} className="fleet-activity-item fleet-overview-roster-row fleet-overview-roster-row--static">
                {avatar}
                {body}
              </div>
            );
          })}
        </div>
      )}

      {/* The one number worth interrupting for — and only when there IS one.
          A permanent tile reading "0 needs you" is noise; this box existing
          at all is the signal. */}
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

      <div className="fleet-overview-section-head">
        <h2 className="fleet-detail-section-title">Activity</h2>
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
        // project's default landing tab, so an un-pinned 37px skeleton row
        // made the whole activity ledger jump the moment the real items swapped in.
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
                    <div className="fleet-activity-title">
                      {item.title}
                      {item.count > 1 && (
                        <span style={{ color: "var(--text-muted)", fontWeight: 400 }}> · ×{item.count}</span>
                      )}
                    </div>
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
