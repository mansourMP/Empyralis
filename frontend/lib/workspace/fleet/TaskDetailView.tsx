"use client";

/**
 * Task detail as a PAGE, not a drawer.
 *
 * It used to be a right-hand overlay (FleetRightPanel) floating over the
 * board. That was wrong for the same reason Linear's issue view is a page:
 * a task is a destination — it has a URL, it is where you read a long
 * description and a thread, and it should be the thing the content area is
 * showing, not a 380px column pasted over the thing you were just looking at.
 *
 * SHAPE (Linear's issue view):
 *   ┌──────────────────────────────────────┬───────────────┐
 *   │ title                                │  Properties   │
 *   │ description                          │  Status       │
 *   │ Activity ─ comments                  │  Priority     │
 *   │                                      │  Assignee …   │
 *   └──────────────────────────────────────┴───────────────┘
 * The properties column is PART OF THE PAGE — a real flex sibling that the
 * main column shares width with — not a floating panel over it. The two
 * columns scroll independently.
 *
 * WHAT IS NOT HERE, on purpose:
 *  · Rich text. `description` is a plain-text column rendered through
 *    MarkdownLiteText — bold, italic, code, links, and bullet/ordered lists
 *    are supported. Headings, tables, and blockquotes are out of scope (this
 *    is a task description, not a document viewer).
 *  · An attachment/image control on the comment composer. Linear's has one;
 *    ours doesn't, because there is no upload endpoint behind a task
 *    comment — task.metadata.comments stores plain text
 *    (project_tasks_service.add_task_comment), nothing multipart. A
 *    paperclip that fails the moment someone clicks it is worse than no
 *    paperclip (CLAUDE.md: no dead controls); this is a reported backend
 *    gap, not a missed frontend affordance.
 *
 * THE COMMENT COMPOSER (MAN-64/MAN-70's human->agent channel): agents have
 * been able to write into task.metadata.comments since project_task__comment
 * / empyralis_comment_on_task; a human could not until routes_fleet.py grew
 * POST .../comments (project_tasks_service.add_human_task_comment). The
 * composer below writes through that route, then asks the page to refetch
 * (onCommentPosted) — same "write, then let the poll catch up" contract
 * TaskLabelEditor already uses for this exact reason (this page has no
 * private write channel of its own; task.metadata.comments only ever
 * changes by going through the shared, polled task list). It also may wake
 * the assigned agent (task_commented, bounded_scheduler_service) — best-
 * effort, surfaced the same way assignment's wake failure already is.
 * MAN-145 restyled it as a bordered, auto-growing surface (the same idiom
 * AgentChat's .fleet-assistant-chat-composer already uses) instead of a bare
 * textarea + wide button, but the write path and the optimistic-then-
 * refetch contract are unchanged.
 *
 * ATTRIBUTION (founder's Linear-parity gap: this page showed WHEN a task
 * changed but never WHO). Two rows, both real identity (avatar + name via
 * AgentSigil/MemberAvatar, never a raw id) and both OMITTED outright when
 * unresolvable rather than shown as a placeholder:
 *   - "Created by" -- task.created_by, resolved against whichever of
 *     `agents`/`members` it matches (routes_fleet.fleet_create_task always
 *     writes the authenticated human's id; skills_service's
 *     project_task__create tool writes the calling agent's install id
 *     instead -- same polymorphic-single-column shape commentAuthorLabel
 *     below already handles for a comment's author_id).
 *   - "Completed by" -- task.completed_by_user_id / completed_by_agent_id
 *     (migrations/add_task_completion_attribution.sql), shown ONLY while
 *     status is currently `done` (these two columns are cleared the moment
 *     a task leaves `done` again, per project_tasks_service.update_task).
 * NEITHER of these is a general "last touched by," and this page does not
 * claim one. The backend has no `updated_by` column and stamps no actor on
 * a generic PATCH -- a status flip to `in_progress`, a re-assign, a due-date
 * edit, a title change are all silent as to who did them; `updated_at`
 * (below) is a timestamp with no identity attached. "Completed by" is the
 * one write path that DOES stamp a real actor, so it is rendered as exactly
 * that -- completion, not a stand-in for "most recent edit." A comment's own
 * author is already visible per-comment in the Activity feed just above,
 * which is where "who said something most recently" already lives; this
 * page doesn't duplicate that into a third Properties row.
 *
 * PREV/NEXT NAVIGATION AND RELATED TASKS (MAN-145 items 4/5) share one read:
 * this component calls useFleetTasks(workspaceId, projectId) itself, the
 * SAME hook with the SAME cache key page.tsx already calls to find `task`
 * in the first place (fleet-data.ts's useSharedPolledResource keys on
 * `fleet-tasks:${workspaceId}:${projectId}` — a second caller with the same
 * key subscribes to the existing polled entry, it does not issue a second
 * request). That matters for correctness, not just efficiency: it is
 * PROVABLY the same array that the project page renders. It is put in the
 * same ORDER too, by reading the same per-workspace view-options preference
 * that page writes (task-view-options.ts) and applying the same sortTasks to
 * it — the project page stopped rendering the raw fetch order on 2026-08-01,
 * when the view-options popover gave the reader an ordering to choose, and an
 * ↑ that walked a different order than the list behind it would be exactly
 * the "best-effort guess" this design was built to avoid. So "N / total" and
 * the ↑/↓ targets ARE what the user saw. The one thing this page still cannot
 * know is which layout or status filter they were looking at (that state is
 * the project page's own) — the nav is hidden outright, not shown with a
 * wrong count, if this task can't be found in the read.
 */

import { useCallback, useEffect, useMemo, useRef, useState, type CSSProperties, type ReactNode } from "react";
import { useRouter } from "next/navigation";
import {
  ArrowUp,
  Calendar,
  CheckCircle2,
  ChevronDown,
  ChevronUp,
  Clock3,
  CornerDownRight,
  FolderKanban,
  Loader2,
  MessageSquare,
  Pencil,
  Plus,
  SignalHigh,
  User,
  UserPlus,
  X,
} from "lucide-react";

import { AgentSigil } from "./fleet-indicators";
import {
  TaskStatusIcon,
  TaskPriorityIcon,
  taskDisplayId,
  taskStatusLabel,
  taskPriority,
  taskWakeDeferral,
  TASK_PRIORITIES,
  TASK_PRIORITY_LABELS,
} from "./task-status";
import { TaskLabelChips, TaskLabelEditor, TaskLabelRowIcon } from "./task-labels";
import { formatDateTime, formatDueDate, timeAgo } from "./fleet-presentation";
import { MemberAvatar } from "./MemberAvatarStack";
import type { WorkspaceMember } from "./members-data";
import {
  assigneeOptionValue,
  commentFleetTask,
  createFleetTask,
  FLEET_TASK_STATUSES,
  parseAssigneeOptionValue,
  useFleetTasks,
  type FleetAgent,
  type FleetTask,
  type FleetTaskStatus,
  type TaskAssigneeSelection,
  type WorkspaceRosterEntry,
} from "./fleet-data";
import {
  DEFAULT_TASK_VIEW_OPTIONS,
  readTaskViewOptions,
  sortTasks,
  type TaskViewOptions as TaskViewOptionsState,
} from "./task-view-options";
import { MarkdownLiteText } from "../markdown-lite";
import "./task-detail.css";

/** Minute precision, not the default's seconds — no decision on this page
 *  turns on a second, and the extra characters only cost the value column
 *  width it does not have. */
function stamp(value: string): string {
  return formatDateTime(value, { dateStyle: "medium", timeStyle: "short" });
}

/** due_at is a date-only field (see formatDueDate's own doc in
 *  fleet-presentation.ts) — pin its display to UTC so it always agrees
 *  with the edit input regardless of viewer timezone, instead of routing
 *  it through stamp()'s local-timezone formatting like a real instant. */
function stampDueDate(value: string): string {
  return formatDueDate(value, { dateStyle: "medium" });
}

/** A resolved @-mention (MAN-66) -- written by
 *  project_tasks_service.add_task_comment/task_mention_service alongside
 *  the comment it was found in. `start`/`end` are character offsets into
 *  that SAME comment's `body` (including the leading `@`), so rendering is
 *  a straight slice — never a second parse of the text on this side. Only
 *  RESOLVED mentions are ever present here; an unknown or ambiguous
 *  `@name` in the raw text has no entry and just renders as plain text. */
type TaskMention = {
  raw?: string;
  start: number;
  end: number;
  kind: "agent" | "user";
  id: string;
  display_name?: string;
};

type TaskComment = {
  id?: string;
  author_type?: string;
  author_id?: string;
  body?: string;
  created_at?: string;
  mentions?: TaskMention[];
  /** Name snapshot written alongside the opaque author pair by
   *  project_tasks_service.add_task_comment, for the one author kind whose id
   *  resolves against neither the agents list nor the member list — an
   *  EXTERNAL agent. Read only as a FALLBACK: the live roster
   *  (useWorkspaceRoster) is the source of truth for a name that still
   *  exists; this covers a comment whose roster row is gone. */
  author_display_name?: string;
};

/** task.metadata.comments as written by add_task_comment. Defensive on the
 *  way in — this is free-form JSONB, so anything that is not an object with a
 *  body is skipped rather than rendered as "[object Object]". */
function readComments(task: FleetTask): TaskComment[] {
  const raw = task.metadata?.comments;
  if (!Array.isArray(raw)) return [];
  return raw
    .filter((c): c is TaskComment => Boolean(c) && typeof c === "object")
    .filter((c) => String(c.body || "").trim().length > 0);
}

type TaskActivityEvent = {
  type: string;
  actor_type?: string;
  actor_id?: string;
  actor_name?: string;
  timestamp?: string;
  details?: Record<string, unknown>;
};

function readActivity(task: FleetTask): TaskActivityEvent[] {
  const raw = task.metadata?.activity;
  if (!Array.isArray(raw)) return [];
  return raw.filter((e): e is TaskActivityEvent => Boolean(e) && typeof e === "object" && typeof (e as Record<string, unknown>).type === "string");
}

type ActivityFeedItem =
  | { kind: "comment"; comment: TaskComment; ts: string }
  | { kind: "activity"; event: TaskActivityEvent; ts: string };

function buildActivityFeed(task: FleetTask): ActivityFeedItem[] {
  const comments: ActivityFeedItem[] = readComments(task).map((c) => ({
    kind: "comment" as const,
    comment: c,
    ts: c.created_at || "",
  }));
  const events: ActivityFeedItem[] = readActivity(task).map((e) => ({
    kind: "activity" as const,
    event: e,
    ts: e.timestamp || "",
  }));
  return [...comments, ...events].sort((a, b) => b.ts.localeCompare(a.ts));
}

const ACTIVITY_LABELS: Record<string, string> = {
  status_changed: "changed status",
  priority_changed: "changed priority",
  title_edited: "edited the title",
  description_edited: "edited the description",
  due_date_changed: "changed the due date",
  assigned: "assigned this task",
  created: "created this task",
};

const STATUS_LABELS: Record<string, string> = {
  backlog: "Backlog", todo: "Todo", in_progress: "In Progress",
  awaiting_input: "Awaiting Input", blocked: "Blocked",
  in_review: "In Review", done: "Done",
};

const PRIORITY_LABELS: Record<number, string> = {
  0: "None", 1: "Urgent", 2: "High", 3: "Medium", 4: "Low",
};

/** Same "real identity, never a raw id" rule the file header's ATTRIBUTION
 *  note states for Created by/Completed by — the structured Activity events
 *  (status/priority/title/description changes) were the one place on this
 *  page that broke it: `_record_task_activity` stamps `actor_id` on every
 *  event but only a HUMAN comment's author gets a resolved display name
 *  upstream, so `e.actor_name || e.actor_id` was rendering a bare UUID for
 *  "changed status from Todo to In Progress" and "created this task" —
 *  confirmed live, 2026-08-12: the Activity feed named a task's own creator
 *  by their `user_id` while the comment two lines below it, from the same
 *  person, correctly said "E2E Owner". Resolved the same way created_by/
 *  completed_by already are (resolveEitherActor against this project's
 *  agents/members); `actor_name` is trusted as a fallback ONLY when it is
 *  not simply a copy of `actor_id` — observed on the wire, not hypothetical. */
function activityActorLabel(
  event: TaskActivityEvent,
  agents: FleetAgent[],
  members?: WorkspaceMember[],
  identityLookupFailed?: boolean,
): string {
  const resolved = resolveEitherActor(event.actor_id, agents, members || []);
  if (resolved) {
    return resolved.kind === "agent"
      ? resolved.agent.label || "Unnamed agent"
      : resolved.member.display_name || resolved.member.email;
  }
  const name = String(event.actor_name || "").trim();
  const id = String(event.actor_id || "").trim();
  if (name && name !== id) return name;
  // "Someone" claims nobody identifiable did this — true only when the
  // lookup actually ran and came up empty. When the lookup itself failed to
  // load, there IS a real actor; we just don't have the roster to name them
  // right now, which is a different, non-anonymous fact.
  if (identityLookupFailed) return "Couldn't load who";
  return "Someone";
}

function describeActivity(event: TaskActivityEvent): string {
  const label = ACTIVITY_LABELS[event.type] || event.type;
  const d = event.details || {};
  if (event.type === "status_changed") {
    const from = typeof d.from === "string" ? (STATUS_LABELS[d.from] || d.from) : "";
    const to = typeof d.to === "string" ? (STATUS_LABELS[d.to] || d.to) : "";
    if (from && to) return `${label} from ${from} to ${to}`;
    if (to) return `${label} to ${to}`;
  }
  if (event.type === "priority_changed") {
    const from = typeof d.from === "number" ? (PRIORITY_LABELS[d.from] || String(d.from)) : "";
    const to = typeof d.to === "number" ? (PRIORITY_LABELS[d.to] || String(d.to)) : "";
    if (from && to) return `${label} from ${from} to ${to}`;
  }
  if (event.type === "assigned" && d.assignee_type) {
    return `${label} to ${d.assignee_type === "agent" ? "an agent" : "a person"}`;
  }
  return label;
}

/** A resolved mention, inline in a comment body — visually distinct from
 *  surrounding text (a neutral pill, same circular identity treatment as
 *  the Assignee row above: AgentSigil for an agent, MemberAvatar for a
 *  person) and distinguishable from EACH OTHER (agent vs human), matching
 *  this page's existing "agent and person are both team members, but never
 *  drawn identically" convention. No per-agent tint (colour-discipline
 *  pass) — AgentSigil's own generated shape plus the "Agent: <name>" title
 *  already tell mentions apart; a hash-derived hue added nothing. Deliberately
 *  styled inline rather than via a new fleet-theme.css class -- that
 *  stylesheet is shared/load-bearing across nearly every fleet surface
 *  (docs/AGENT-OPERATING-RULES.md "All fleet UI shares files") and another
 *  agent may be editing it concurrently; every color here is one of the same
 *  CSS custom properties (--rail-active/--text-primary) the rest of this
 *  file already reads, so it stays on-theme (light/dark) without a new rule. */
function MentionChip({
  mention,
  agents,
  members,
}: {
  mention: TaskMention;
  agents: FleetAgent[];
  members?: WorkspaceMember[];
}) {
  const chipStyle: CSSProperties = {
    display: "inline-flex",
    alignItems: "center",
    gap: 4,
    padding: "1px 7px 1px 3px",
    borderRadius: 999,
    background: "var(--rail-active)",
    color: "var(--text-primary)",
    fontWeight: 600,
    whiteSpace: "nowrap",
  };

  if (mention.kind === "agent") {
    const agent = agents.find((a) => a.agent_id === mention.id);
    const label = agent?.label || mention.display_name || "Agent";
    return (
      <span className="fleet-mention-chip fleet-mention-chip--agent" style={chipStyle} title={`Agent: ${label}`}>
        <AgentSigil seed={mention.id} size={13} />
        {label}
      </span>
    );
  }

  const member = (members || []).find((m) => m.user_id === mention.id);
  const memberIndex = member ? (members || []).indexOf(member) : 0;
  const label = member?.display_name || member?.email || mention.display_name || "Person";
  return (
    <span className="fleet-mention-chip fleet-mention-chip--human" style={chipStyle} title={`${label}`}>
      <MemberAvatar name={label} role={member?.role} size="xs" tintIndex={memberIndex} />
      {label}
    </span>
  );
}

/** A resolved "who" for one of the two attribution rows below -- either an
 *  agent in this project or a workspace member, never a raw id. */
type ResolvedActor =
  | { kind: "agent"; agent: FleetAgent }
  | { kind: "user"; member: WorkspaceMember; memberIndex: number };

function resolveAgentActor(agentId: string | null | undefined, agents: FleetAgent[]): ResolvedActor | null {
  const trimmed = String(agentId || "").trim();
  if (!trimmed) return null;
  const agent = agents.find((a) => a.agent_id === trimmed);
  return agent ? { kind: "agent", agent } : null;
}

function resolveUserActor(userId: string | null | undefined, members: WorkspaceMember[]): ResolvedActor | null {
  const trimmed = String(userId || "").trim();
  if (!trimmed) return null;
  const memberIndex = (members || []).findIndex((m) => m.user_id === trimmed);
  return memberIndex >= 0 ? { kind: "user", member: members[memberIndex], memberIndex } : null;
}

/** `created_by` is ONE column that can hold either an agent install id or a
 *  human user_id (see the file header's ATTRIBUTION note) -- unlike
 *  completed_by_user_id/completed_by_agent_id below, which already name
 *  their own kind, this one has to be tried against both lists. Whichever
 *  resolves first wins; neither resolving (a deleted agent/member, or a
 *  legacy task with no created_by at all) returns null, and the caller
 *  omits the row rather than falling back to the raw id. */
function resolveEitherActor(
  id: string | null | undefined,
  agents: FleetAgent[],
  members: WorkspaceMember[],
): ResolvedActor | null {
  return resolveAgentActor(id, agents) || resolveUserActor(id, members);
}

/** Real identity for an attribution row -- the exact same circular-avatar
 *  treatment (AgentSigil in a `.fleet-agent-avatar` tile, or MemberAvatar)
 *  the Assignee row above already uses, so "who created this" and "who is
 *  this assigned to" read as the same kind of fact. */
function TaskActorBadge({ actor }: { actor: ResolvedActor }) {
  if (actor.kind === "agent") {
    const label = actor.agent.label || "Unnamed agent";
    return (
      <span className="fleet-task-detail-actor" title={`Agent: ${label}`}>
        <span className="fleet-agent-avatar" aria-hidden="true">
          <AgentSigil seed={actor.agent.agent_id} size={14} />
        </span>
        <span className="fleet-task-detail-actor-name">{label}</span>
      </span>
    );
  }
  const label = actor.member.display_name || actor.member.email;
  return (
    <span className="fleet-task-detail-actor" title={label}>
      <MemberAvatar name={label} role={actor.member.role} size="xs" tintIndex={actor.memberIndex} />
      <span className="fleet-task-detail-actor-name">{label}</span>
    </span>
  );
}

/** Splits a comment's body at its resolved mentions' stored offsets and
 *  substitutes a MentionChip for each — the ONLY place `comment.mentions`
 *  is read. Out-of-range/overlapping entries (should not happen; the
 *  backend already drops anything past its own 4000-char truncation, see
 *  add_task_comment) are defensively skipped rather than crashing the
 *  Activity feed on a single malformed comment. No mentions -> returns the
 *  plain body string unchanged, so a pre-MAN-66 comment renders exactly as
 *  it always did. */
function renderCommentBody(comment: TaskComment, agents: FleetAgent[], members?: WorkspaceMember[]): ReactNode {
  const body = comment.body || "";
  const mentions = (comment.mentions || [])
    .filter(
      (m) =>
        Number.isFinite(m.start) &&
        Number.isFinite(m.end) &&
        m.start >= 0 &&
        m.end > m.start &&
        m.end <= body.length
    )
    .sort((a, b) => a.start - b.start);
  if (mentions.length === 0) return body;

  const parts: ReactNode[] = [];
  let cursor = 0;
  mentions.forEach((mention, i) => {
    if (mention.start < cursor) return; // overlapping — defensive skip, never render garbage
    if (mention.start > cursor) parts.push(body.slice(cursor, mention.start));
    parts.push(<MentionChip key={`mention-${i}-${mention.id}`} mention={mention} agents={agents} members={members} />);
    cursor = mention.end;
  });
  if (cursor < body.length) parts.push(body.slice(cursor));
  return parts;
}

export function TaskDetailView({
  task,
  agents,
  members,
  externalAgents,
  workspaceId,
  projectName,
  projectHref,
  onStatusChange,
  onPriorityChange,
  onDueChange,
  onTitleChange,
  onDescriptionChange,
  onAssign,
  onSetParent,
  onSubTaskCreated,
  onLabelsChanged,
  onCommentPosted,
  identityLookupFailed,
}: {
  task: FleetTask;
  /** Agents in this project — valid AGENT assignees. */
  agents: FleetAgent[];
  /** Workspace members (MAN-64/MAN-70) — valid HUMAN assignees, and the
   *  lookup used to render a human commenter's real name. Absent → the
   *  Assignee picker offers agents only (degrades to the pre-MAN-64
   *  behavior) and human comments fall back to their raw author id. */
  members?: WorkspaceMember[];
  /** The workspace's EXTERNAL agents (useWorkspaceRoster) — MCP-connected
   *  sessions that can comment on this task. Not assignees (an external
   *  agent cannot hold a task today; see list_unified_roster's docstring),
   *  purely the lookup that turns an `ext_agent_<hex16>` comment author into
   *  its real name. Absent → an external agent's comment falls back to its
   *  stored name snapshot, and only then to "External agent <short id>". */
  externalAgents?: WorkspaceRosterEntry[];
  /** Scopes the label vocabulary — labels are per WORKSPACE, not per project
   *  (fleet-data's Labels section: "bug" means the same thing wherever the
   *  work sits). Absent → the Labels row renders read-only chips, and the
   *  comment composer below is hidden the same way (POST .../comments needs
   *  it too). */
  workspaceId?: string;
  projectName: string;
  projectHref: string;
  onStatusChange: (taskId: string, status: FleetTaskStatus) => void;
  onPriorityChange?: (taskId: string, priority: number) => void;
  /** Due-date edit (MAN-145): the route page patches the task and optimistically
   *  overlays the new value, same shape as onPriorityChange. */
  onDueChange?: (taskId: string, dueAt: string | null) => void;
  /** Title/description edit — the backend has always accepted both on the
   *  same PATCH route onStatusChange/onPriorityChange/onDueChange already use
   *  (project_tasks_service.update_task already stamps title_edited/
   *  description_edited activity events on a change); this page simply never
   *  rendered anything that could call it. Optional, same "no dead controls"
   *  gating as the rest of this column — a read-only view omits the edit
   *  affordance instead of rendering one that does nothing. */
  onTitleChange?: (taskId: string, title: string) => void;
  onDescriptionChange?: (taskId: string, description: string) => void;
  /** Assignee is agent-or-human (MAN-64/MAN-70) — the caller dispatches to
   *  assignFleetTask or assignFleetTaskToUser based on `selection.kind`. */
  onAssign: (taskId: string, selection: TaskAssigneeSelection) => void;
  /** Make this task a sub-task of another (pass a non-null id) or detach it
   *  back to top-level (pass null). Calls setFleetTaskParent through the
   *  route page, which owns all writes. */
  onSetParent?: (taskId: string, parentTaskId: string | null) => void;
  /** Refetch after a sub-task is created inline (the write goes through
   *  createFleetTask directly, same contract as the comment composer). */
  onSubTaskCreated?: () => void | Promise<void>;
  /** Refetch after a label attach/detach. Labels are not part of the task
   *  PATCH — they are their own endpoints — so the editor writes directly and
   *  then asks the page to re-read. */
  onLabelsChanged?: () => void | Promise<void>;
  /** Refetch after a human comment is posted. Same contract as
   *  onLabelsChanged, same reason: POST .../comments is its own endpoint,
   *  not part of the task PATCH, so the composer below writes directly and
   *  then asks the page to re-read the (30s-polled) task list. */
  onCommentPosted?: () => void | Promise<void>;
  /** True when the agents/members lookup itself failed to load (a network
   *  blip, an expired session) rather than loaded and genuinely found no
   *  match. Found live 2026-08-13: on a stale/expiring session, `agents`/
   *  `members` silently resolved to empty arrays and every actor on this
   *  page — including the task's own creator, moments after they created
   *  it — rendered as "Someone" / vanished from Created by, indistinguishable
   *  from a genuinely-anonymous system action. Identity attribution is
   *  central to this product's review-not-approve model, so a fetch
   *  failure must present as a fetch failure, never as an anonymous actor.
   *  See resolveEitherActor's callers below for how this changes the
   *  fallback. */
  identityLookupFailed?: boolean;
}) {
  const router = useRouter();
  const headingRef = useRef<HTMLHeadingElement | null>(null);

  // Escape returns to the board — the page equivalent of the drawer's
  // dismiss, and the same affordance the agent detail page already has.
  // Ignored while a control has focus (a <select> owns Escape to cancel its
  // own listbox) or while any overlay is open above this page.
  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key !== "Escape" || event.defaultPrevented) return;
      const el = document.activeElement as HTMLElement | null;
      const tag = el?.tagName;
      if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT" || el?.isContentEditable) return;
      if (document.querySelector("[role='dialog'], .fleet-detail-backdrop")) return;
      router.push(projectHref);
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [router, projectHref]);

  // Announce the task to a screen reader on arrival, exactly as the drawer
  // did when it opened.
  useEffect(() => {
    headingRef.current?.focus();
  }, [task.id]);

  const priority = taskPriority(task);
  // MAN-294: non-null only while `status` is lying -- see taskWakeDeferral's
  // own docstring. Recomputed every render off the polled task, so it
  // clears itself the moment a refetch shows the agent has actually woken.
  const wakeDeferral = taskWakeDeferral(task);
  const assignee = agents.find((a) => a.agent_id === task.assignee_agent_id) || null;
  // The human half of MAN-64/MAN-70 -- only looked up when there is no
  // agent assignee, matching the backend's own mutual-exclusivity
  // guarantee (project_tasks_single_assignee_check: at most one of the two
  // is ever set).
  const assignedMember = !assignee && task.assignee_user_id
    ? (members || []).find((m) => m.user_id === task.assignee_user_id) || null
    : null;
  const assignedMemberIndex = assignedMember ? (members || []).indexOf(assignedMember) : 0;
  // `agents` here is already filtered to THIS project (the page's own
  // inProject filter, matching CLAUDE.md's "an agent belongs to its
  // project" law) — so `task.assignee_agent_id` set but not resolving into
  // `assignee` means the row's real assignee lives outside this project.
  // project_tasks_service.assign_task refuses to create that state going
  // forward, but it must still be rendered HONESTLY if it is ever reached
  // any other way (stale data, a direct DB write, a future bug) — showing
  // "Unassigned" here would be the exact silent-misattribution failure this
  // fix exists to close, just moved one layer up into the UI.
  const unresolvedAgentAssigneeId = !assignee && task.assignee_agent_id ? task.assignee_agent_id : null;
  const currentAssigneeValue = assignee
    ? assigneeOptionValue({ kind: "agent", id: assignee.agent_id })
    : assignedMember
      ? assigneeOptionValue({ kind: "user", id: assignedMember.user_id })
      : task.assignee_agent_id
        ? assigneeOptionValue({ kind: "agent", id: task.assignee_agent_id })
        : task.assignee_user_id
          ? assigneeOptionValue({ kind: "user", id: task.assignee_user_id })
          : "";

  // Attribution (see the file header's ATTRIBUTION note for why these are
  // the two honest rows and not a general "last touched by"). Both resolve
  // to null -- and are simply not rendered -- when the id is empty or
  // doesn't match anyone in `agents`/`members` today.
  const createdByActor = useMemo(
    () => resolveEitherActor(task.created_by, agents, members || []),
    [task.created_by, agents, members],
  );
  // completed_by_* is only ever meaningful while the task IS `done` --
  // project_tasks_service.update_task clears both columns the instant a
  // task leaves that status, but this component still receives whatever
  // stale values happened to be on the row a moment before a refetch lands,
  // so the status check is re-asserted here rather than trusted from the
  // columns' mere presence.
  const completedByActor = useMemo(
    () =>
      task.status === "done"
        ? resolveAgentActor(task.completed_by_agent_id, agents) || resolveUserActor(task.completed_by_user_id, members || [])
        : null,
    [task.status, task.completed_by_agent_id, task.completed_by_user_id, agents, members],
  );
  const feed = useMemo(() => buildActivityFeed(task), [task]);

  // Prev/next (MAN-145 item 4) and the Parent/Sub-tasks reads (item 5) all
  // ride the SAME sibling-task array — see the file header note on why
  // re-calling useFleetTasks here with the project page's own (workspaceId,
  // projectId) is a subscribe to its existing shared-cache entry, not a
  // second request, and why its ordering is provably what TasksList shows.
  // `project_id` is on `task` itself when the server has migrations/
  // add_task_parent.sql applied; falling back to parsing it out of
  // `projectHref` (always `{base}/projects/{projectId}`, per page.tsx) means
  // this still works against an older row that omits the field.
  const projectId = useMemo(() => {
    const own = String(task.project_id || "").trim();
    if (own) return own;
    const match = /\/projects\/([^/?#]+)/.exec(projectHref);
    return match ? decodeURIComponent(match[1]) : "";
  }, [task.project_id, projectHref]);
  const { tasks: siblingTasks } = useFleetTasks(
    workspaceId || "",
    workspaceId && projectId ? projectId : null,
  );
  // The reader's own ordering, from the same localStorage preference the
  // project page writes — so ↑/↓ walk the list they were just looking at,
  // not the raw fetch order. Hydrated in an effect, never during render, so
  // the server's markup and the client's first paint agree.
  const [viewOptions, setViewOptions] = useState<TaskViewOptionsState>(DEFAULT_TASK_VIEW_OPTIONS);
  useEffect(() => {
    if (workspaceId) setViewOptions(readTaskViewOptions(workspaceId));
  }, [workspaceId]);
  const orderedSiblings = useMemo(
    () => sortTasks(siblingTasks, viewOptions.ordering, viewOptions.direction),
    [siblingTasks, viewOptions.ordering, viewOptions.direction],
  );
  const taskDetailHref = useCallback(
    (id: string) => `${projectHref}/tasks/${encodeURIComponent(id)}`,
    [projectHref],
  );
  const siblingIndex = useMemo(
    () => orderedSiblings.findIndex((t) => t.id === task.id),
    [orderedSiblings, task.id],
  );
  // Hidden outright (not shown with a wrong or single-item count) unless
  // this task was actually found in the read and there is somewhere to go
  // -- an arrow pair that jumps somewhere unexpected is worse than none.
  const showTaskNav = siblingIndex >= 0 && orderedSiblings.length > 1;
  const prevTask = siblingIndex > 0 ? orderedSiblings[siblingIndex - 1] : null;
  const nextTask = siblingIndex >= 0 && siblingIndex < orderedSiblings.length - 1
    ? orderedSiblings[siblingIndex + 1]
    : null;
  const parentTask = useMemo(
    () => (task.parent_task_id ? siblingTasks.find((t) => t.id === task.parent_task_id) || null : null),
    [siblingTasks, task.parent_task_id],
  );
  const subtasks = useMemo(
    () => siblingTasks.filter((t) => t.parent_task_id === task.id),
    [siblingTasks, task.id],
  );
  // Valid parent candidates: every task in this project minus self and
  // children of self (a child can't become a parent — cycle). The backend
  // also enforces the one-level rule, so a task that already has children
  // is rejected server-side anyway; this filter keeps the picker honest
  // without a second round-trip.
  const availableParents = useMemo(
    () => siblingTasks.filter((t) => t.id !== task.id && t.parent_task_id !== task.id),
    [siblingTasks, task.id],
  );

  // The composer: local state only, exactly TaskLabelEditor's shape
  // (writes go straight out via commentFleetTask, painted optimistically
  // first because the real comment only shows up once the caller's 30s-
  // polled task list has refetched). `pending` is appended to the real
  // list rather than replacing it, and dropped the moment the write settles
  // either way — the refetch (success) or the reverted textarea (failure)
  // is always what's left on screen, never a comment stuck mid-air.
  const [draft, setDraft] = useState("");
  const [posting, setPosting] = useState(false);
  const [commentNotice, setCommentNotice] = useState<string | null>(null);
  const [pending, setPending] = useState<TaskComment | null>(null);
  const displayFeed = useMemo(() => {
    const items = [...feed];
    if (pending) items.unshift({ kind: "comment" as const, comment: pending, ts: pending.created_at || "" });
    return items;
  }, [feed, pending]);

  // MAN-145: the composer textarea auto-grows with its content instead of
  // sitting at a fixed 2 rows or exposing a manual resize handle — same
  // idiom, same cap (200px), as AgentChat's .fleet-assistant-chat-input.
  const composerRef = useRef<HTMLTextAreaElement | null>(null);
  const autoGrow = useCallback(() => {
    const el = composerRef.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, 200)}px`;
  }, []);

  // ── Title inline edit ─────────────────────────────────────────────────────
  // Same skipBlurCommit ref pattern as AgentTitle (FleetAgentDetail.tsx) and
  // the due-date edit below: Escape sets the ref so the blur handler doesn't
  // re-commit the stale draft.
  const [editingTitle, setEditingTitle] = useState(false);
  const [titleDraft, setTitleDraft] = useState(task.title || "");
  const titleInputRef = useRef<HTMLInputElement | null>(null);
  const skipTitleBlur = useRef(false);

  // A poll tick can swap `task` out from under an open edit (assignment,
  // status, a comment landing) — only resync the draft while NOT editing, the
  // same guard PersonaEditor documents for exactly this reason, so an
  // in-progress rename is never clobbered by its own refetch.
  useEffect(() => {
    if (!editingTitle) setTitleDraft(task.title || "");
  }, [task.title, editingTitle]);

  const enterTitleEdit = useCallback(() => {
    if (!onTitleChange) return;
    setTitleDraft(task.title || "");
    setEditingTitle(true);
    requestAnimationFrame(() => {
      titleInputRef.current?.focus();
      titleInputRef.current?.select();
    });
  }, [onTitleChange, task.title]);

  const commitTitle = useCallback(() => {
    if (!onTitleChange) return;
    const next = titleDraft.trim();
    // Empty commits nowhere — the backend's own COALESCE(NULLIF($,''), title)
    // already treats a blank title as "leave it alone", so mirror that here
    // rather than sending a write that provably no-ops.
    if (!next || next === (task.title || "")) {
      setTitleDraft(task.title || "");
      setEditingTitle(false);
      return;
    }
    setEditingTitle(false);
    onTitleChange(task.id, next);
  }, [onTitleChange, task.id, task.title, titleDraft]);

  // ── Description inline edit ───────────────────────────────────────────────
  const [editingDescription, setEditingDescription] = useState(false);
  const [descriptionDraft, setDescriptionDraft] = useState(task.description || "");
  const descriptionInputRef = useRef<HTMLTextAreaElement | null>(null);
  const skipDescriptionBlur = useRef(false);

  useEffect(() => {
    if (!editingDescription) setDescriptionDraft(task.description || "");
  }, [task.description, editingDescription]);

  const autosizeDescription = useCallback((el: HTMLTextAreaElement | null) => {
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${el.scrollHeight}px`;
  }, []);

  const enterDescriptionEdit = useCallback(() => {
    if (!onDescriptionChange) return;
    setDescriptionDraft(task.description || "");
    setEditingDescription(true);
    requestAnimationFrame(() => {
      autosizeDescription(descriptionInputRef.current);
      descriptionInputRef.current?.focus();
    });
  }, [onDescriptionChange, task.description, autosizeDescription]);

  const commitDescription = useCallback(() => {
    if (!onDescriptionChange) return;
    const next = descriptionDraft.trim();
    const current = (task.description || "").trim();
    if (next === current) {
      setDescriptionDraft(task.description || "");
      setEditingDescription(false);
      return;
    }
    setEditingDescription(false);
    onDescriptionChange(task.id, next);
  }, [onDescriptionChange, task.id, task.description, descriptionDraft]);

  // ── Due-date inline edit ──────────────────────────────────────────────────
  // Same skipBlurCommit ref pattern as AgentTitle (FleetAgentDetail.tsx):
  // Escape sets the ref so the blur handler doesn't re-commit the stale draft.
  const [editingDue, setEditingDue] = useState(false);
  const [dueDraft, setDueDraft] = useState("");
  const dueInputRef = useRef<HTMLInputElement | null>(null);
  const skipDueBlur = useRef(false);

  const toDateInputValue = useCallback((iso: string | null | undefined): string => {
    if (!iso) return "";
    return iso.slice(0, 10); // "2026-08-15T00:00:00Z" → "2026-08-15"
  }, []);

  const enterDueEdit = useCallback(() => {
    setDueDraft(toDateInputValue(task.due_at));
    setEditingDue(true);
    // focus + select after the next paint, when the input exists
    requestAnimationFrame(() => {
      dueInputRef.current?.focus();
      dueInputRef.current?.select();
    });
  }, [task.due_at, toDateInputValue]);

  const commitDue = useCallback(() => {
    if (!onDueChange) return;
    const raw = dueDraft.trim();
    const next = raw || null; // empty → null (clear)
    const current = toDateInputValue(task.due_at) || null;
    if (next === current) {
      setEditingDue(false);
      return;
    }
    setEditingDue(false);
    onDueChange(task.id, next);
  }, [dueDraft, onDueChange, task.id, task.due_at, toDateInputValue]);

  const clearDue = useCallback(() => {
    if (!onDueChange) return;
    setEditingDue(false);
    skipDueBlur.current = true;
    onDueChange(task.id, null);
  }, [onDueChange, task.id]);

  // ── Inline sub-task creation ──────────────────────────────────────────────
  const [addingSubtask, setAddingSubtask] = useState(false);
  const [newSubtaskTitle, setNewSubtaskTitle] = useState("");
  const [creatingSubtask, setCreatingSubtask] = useState(false);
  const subtaskInputRef = useRef<HTMLInputElement | null>(null);
  const skipSubtaskBlur = useRef(false);

  const startAddingSubtask = useCallback(() => {
    setNewSubtaskTitle("");
    setAddingSubtask(true);
    requestAnimationFrame(() => {
      subtaskInputRef.current?.focus();
    });
  }, []);

  async function submitSubtask() {
    const title = newSubtaskTitle.trim();
    if (!title || creatingSubtask || !workspaceId) return;
    setCreatingSubtask(true);
    try {
      await createFleetTask(workspaceId, {
        project_id: projectId,
        title,
        parent_task_id: task.id,
      });
      setNewSubtaskTitle("");
      setAddingSubtask(false);
      await onSubTaskCreated?.();
    } catch {
      // Keep the form open with the draft intact so the user can retry.
    } finally {
      setCreatingSubtask(false);
    }
  }

  async function submitComment() {
    const body = draft.trim();
    if (!body || posting || !workspaceId) return;
    setPosting(true);
    setCommentNotice(null);
    setPending({ id: `pending-${Date.now()}`, author_type: "human", author_id: "You", body, created_at: new Date().toISOString() });
    try {
      const { wakeError } = await commentFleetTask(workspaceId, task.id, body);
      setDraft("");
      requestAnimationFrame(autoGrow);
      if (wakeError) {
        setCommentNotice(
          `Posted, but the agent could not be woken: ${wakeError}. It will see this the next time it runs.`,
        );
      }
      await onCommentPosted?.();
    } catch (e) {
      setCommentNotice(e instanceof Error ? e.message : "Could not post comment.");
    } finally {
      setPosting(false);
      setPending(null);
    }
  }

  return (
    <div className="fleet-task-page fleet-task-detail-page">
      {/* .fleet-task-page-main is a flex column here (task-detail.css
          override — see that file's "composer pinned to the bottom" note):
          .fleet-task-page-scroll is the ONLY thing that scrolls (title,
          description, sub-tasks, Activity/comments); the composer dock
          below it is a flex-shrink:0 sibling that always sits at the true
          bottom of the column, exactly AgentChat's own .fleet-assistant-chat /
          .fleet-assistant-chat-list / .fleet-assistant-chat-composer split — reused,
          not reinvented, so there is one "message input pinned to the
          bottom" pattern in this codebase, not two. */}
      <div className="fleet-task-page-main">
        <div className="fleet-task-page-scroll">
          <div className="fleet-task-page-body">
            <div className="fleet-task-detail-topbar">
              <div className="fleet-task-page-eyebrow">{taskDisplayId(task)}</div>
              {showTaskNav ? (
                <div className="fleet-task-detail-nav" aria-label="Task navigation">
                  <span className="fleet-task-detail-nav-count">
                    {siblingIndex + 1} / {siblingTasks.length}
                  </span>
                  <TaskNavArrow
                    direction="prev"
                    target={prevTask}
                    href={prevTask ? taskDetailHref(prevTask.id) : null}
                    router={router}
                  />
                  <TaskNavArrow
                    direction="next"
                    target={nextTask}
                    href={nextTask ? taskDetailHref(nextTask.id) : null}
                    router={router}
                  />
                </div>
              ) : null}
            </div>

            {parentTask ? (
              <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                <a
                  className="fleet-task-detail-parent-link"
                  href={taskDetailHref(parentTask.id)}
                  onClick={(event) => {
                    if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey || event.button !== 0) return;
                    event.preventDefault();
                    router.push(taskDetailHref(parentTask.id));
                  }}
                >
                  <CornerDownRight size={12} strokeWidth={2} />
                  <TaskStatusIcon status={parentTask.status} size={12} />
                  Sub-task of{" "}
                  <span className="fleet-task-detail-parent-link-title">
                    {parentTask.title || "Untitled task"}
                  </span>
                </a>
                {onSetParent ? (
                  <button
                    type="button"
                    className="fleet-task-detail-icon-btn"
                    aria-label={`Detach from ${parentTask.title || "parent task"}`}
                    title={`Detach from ${parentTask.title || "parent task"}`}
                    onClick={() => onSetParent(task.id, null)}
                  >
                    <X size={12} strokeWidth={2} />
                  </button>
                ) : null}
              </div>
            ) : null}

            {/* h2, not h1: the breadcrumb's current crumb is this page's real
                <h1> (MAN-145 title-dedup). Rendering the task title as an h1
                here too gave task pages two visible h1s — the same triplication
                that pass existed to remove. Visual size is unchanged; only the
                tag differs.
                Click-to-edit (same convention as AgentTitle in
                FleetAgentDetail.tsx): the backend has taken title/description
                on this same PATCH route since project_tasks_service.update_task
                was written — see the onTitleChange/onDescriptionChange doc
                comment above — this page simply never rendered anything that
                could call it. A <button> nested inside the <h2> keeps real
                heading semantics (CLAUDE.md: real heading structure) while
                giving keyboard/AT users a genuine control, not just a mouse
                affordance. Gated on onTitleChange like every other optional
                writer prop on this page — a read-only view gets a plain
                heading, never a click target that does nothing. */}
            <h2 className="fleet-task-page-title" tabIndex={editingTitle ? undefined : -1} ref={headingRef}>
              {editingTitle ? (
                <input
                  ref={titleInputRef}
                  className="fleet-task-page-title-input"
                  value={titleDraft}
                  maxLength={400}
                  aria-label="Task title"
                  onChange={(e) => setTitleDraft(e.currentTarget.value)}
                  onBlur={() => {
                    if (skipTitleBlur.current) {
                      skipTitleBlur.current = false;
                      return;
                    }
                    commitTitle();
                  }}
                  onKeyDown={(e) => {
                    if (e.key === "Enter") {
                      e.preventDefault();
                      e.currentTarget.blur();
                    } else if (e.key === "Escape") {
                      e.preventDefault();
                      skipTitleBlur.current = true;
                      setTitleDraft(task.title || "");
                      setEditingTitle(false);
                    }
                  }}
                />
              ) : onTitleChange ? (
                <button type="button" className="fleet-task-page-title-edit" onClick={enterTitleEdit}>
                  {task.title || "Untitled task"}
                  <Pencil size={14} strokeWidth={1.75} className="fleet-task-page-title-pencil" />
                </button>
              ) : (
                task.title || "Untitled task"
              )}
            </h2>

            {editingDescription ? (
              <textarea
                ref={descriptionInputRef}
                className="fleet-task-page-desc-input"
                value={descriptionDraft}
                maxLength={20000}
                placeholder="Add a description…"
                aria-label="Task description"
                onChange={(e) => {
                  setDescriptionDraft(e.currentTarget.value);
                  autosizeDescription(e.currentTarget);
                }}
                onBlur={() => {
                  if (skipDescriptionBlur.current) {
                    skipDescriptionBlur.current = false;
                    return;
                  }
                  commitDescription();
                }}
                onKeyDown={(e) => {
                  // No Enter-submits here (unlike the title) — a description
                  // is the one multi-line field on this page, so Enter has to
                  // stay a newline. Only Escape has a special meaning.
                  if (e.key === "Escape") {
                    e.preventDefault();
                    skipDescriptionBlur.current = true;
                    setDescriptionDraft(task.description || "");
                    setEditingDescription(false);
                  }
                }}
              />
            ) : onDescriptionChange ? (
              <div
                className={`fleet-task-page-desc fleet-task-page-desc--editable${task.description ? "" : " fleet-cell-muted"}`}
                role="button"
                tabIndex={0}
                aria-label={task.description ? "Edit description" : "Add a description"}
                onClick={enterDescriptionEdit}
                onKeyDown={(e) => {
                  if (e.key === "Enter" || e.key === " ") {
                    e.preventDefault();
                    enterDescriptionEdit();
                  }
                }}
              >
                {task.description ? <MarkdownLiteText text={task.description} /> : "Add a description…"}
              </div>
            ) : task.description ? (
              <div className="fleet-task-page-desc">
                <MarkdownLiteText text={task.description} />
              </div>
            ) : (
              <p className="fleet-task-page-desc fleet-cell-muted">No description.</p>
            )}

            {(subtasks.length > 0 || (workspaceId && onSubTaskCreated)) ? (
              <section className="fleet-task-page-section" aria-label="Sub-tasks">
                <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
                  <h2 className="fleet-task-page-section-title">
                    {subtasks.length > 0
                      ? `Sub-tasks · ${subtasks.filter((t) => t.status === "done").length}/${subtasks.length}`
                      : "Sub-tasks"}
                  </h2>
                  {workspaceId && onSubTaskCreated && !addingSubtask ? (
                    <button
                      type="button"
                      className="fleet-task-detail-icon-btn"
                      aria-label="Add sub-task"
                      title="Add sub-task"
                      onClick={startAddingSubtask}
                    >
                      <Plus size={14} strokeWidth={2} />
                    </button>
                  ) : null}
                </div>
                {subtasks.length > 0 ? (
                  <ul className="fleet-task-detail-subtask-list">
                    {subtasks.map((st) => (
                      <li key={st.id}>
                        <a
                          className="fleet-task-detail-subtask-row"
                          href={taskDetailHref(st.id)}
                          onClick={(event) => {
                            if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey || event.button !== 0) return;
                            event.preventDefault();
                            router.push(taskDetailHref(st.id));
                          }}
                        >
                          <TaskStatusIcon status={st.status} size={13} />
                          <span className="fleet-task-detail-subtask-title">{st.title || "Untitled task"}</span>
                          <span className="fleet-task-detail-subtask-id">{taskDisplayId(st)}</span>
                        </a>
                      </li>
                    ))}
                  </ul>
                ) : null}
                {addingSubtask ? (
                  <div className="fleet-task-detail-subtask-form">
                    <input
                      ref={subtaskInputRef}
                      className="fleet-task-detail-select"
                      style={{ width: "100%", boxSizing: "border-box" }}
                      placeholder="Sub-task title…"
                      value={newSubtaskTitle}
                      maxLength={400}
                      disabled={creatingSubtask}
                      onChange={(e) => setNewSubtaskTitle(e.currentTarget.value)}
                      onBlur={() => {
                        if (skipSubtaskBlur.current) {
                          skipSubtaskBlur.current = false;
                          return;
                        }
                        // Blur cancels — don't create an empty sub-task
                        if (!newSubtaskTitle.trim()) {
                          setAddingSubtask(false);
                        }
                      }}
                      onKeyDown={(e) => {
                        if (e.key === "Enter") {
                          e.preventDefault();
                          if (newSubtaskTitle.trim()) void submitSubtask();
                        } else if (e.key === "Escape") {
                          e.preventDefault();
                          skipSubtaskBlur.current = true;
                          setNewSubtaskTitle("");
                          setAddingSubtask(false);
                        }
                      }}
                    />
                    <div className="fleet-task-detail-subtask-form-actions">
                      <button
                        type="button"
                        className="fleet-btn fleet-btn--accent-fill"
                        disabled={!newSubtaskTitle.trim() || creatingSubtask}
                        onClick={() => void submitSubtask()}
                      >
                        {creatingSubtask ? (
                          <Loader2 size={13} strokeWidth={2} style={{ animation: "spin 1s linear infinite" }} />
                        ) : (
                          "Add"
                        )}
                      </button>
                      <button
                        type="button"
                        className="fleet-btn fleet-btn--neutral"
                        disabled={creatingSubtask}
                        onClick={() => {
                          skipSubtaskBlur.current = true;
                          setNewSubtaskTitle("");
                          setAddingSubtask(false);
                        }}
                      >
                        Cancel
                      </button>
                    </div>
                  </div>
                ) : null}
              </section>
            ) : null}

            <section className="fleet-task-page-section" aria-label="Activity">
              <h2 className="fleet-task-page-section-title">Activity</h2>
              {displayFeed.length === 0 ? (
                <div className="fleet-task-page-activity-empty">
                  <MessageSquare size={14} strokeWidth={1.75} />
                  <span>
                    No activity yet. Post a comment below — agents working this task can
                    leave updates here too.
                  </span>
                </div>
              ) : (
                <ul className="fleet-task-page-comments">
                  {displayFeed.map((item, i) => {
                    if (item.kind === "activity") {
                      const e = item.event;
                      const ts = e.timestamp || "";
                      return (
                        <li key={`evt-${i}`} className="fleet-task-page-activity-event">
                          <span className="fleet-activity-actor">{activityActorLabel(e, agents, members, identityLookupFailed)}</span>
                          {" "}
                          <span className="fleet-activity-action">{describeActivity(e)}</span>
                          {ts ? (
                            <span className="fleet-task-page-comment-time" title={stamp(ts)}>
                              {" · "}{timeAgo(ts) || stamp(ts)}
                            </span>
                          ) : null}
                        </li>
                      );
                    }
                    const c = item.comment;
                    return (
                      <li
                        key={c.id || i}
                        className={`fleet-task-page-comment${c === pending ? " is-pending" : ""}`}
                      >
                        <div className="fleet-task-page-comment-head">
                          <CommentAuthorLine author={resolveCommentAuthor(c, agents, members, externalAgents)} />
                          {c.created_at ? (
                            <span className="fleet-task-page-comment-time" title={stamp(c.created_at)}>
                              {timeAgo(c.created_at) || stamp(c.created_at)}
                            </span>
                          ) : null}
                        </div>
                        <div className="fleet-task-page-comment-body">{renderCommentBody(c, agents, members)}</div>
                      </li>
                    );
                  })}
                </ul>
              )}
            </section>
          </div>
        </div>

        {/* Pinned dock — outside .fleet-task-page-scroll on purpose, see the
            comment above .fleet-task-page-main. Same visibility condition
            the inline composer always had: no workspaceId, no write route,
            no composer. */}
        {workspaceId ? (
          <div className="fleet-task-detail-composer-dock">
            <form
              className="fleet-task-detail-composer"
              onSubmit={(event) => {
                event.preventDefault();
                void submitComment();
              }}
            >
              <textarea
                ref={composerRef}
                className="fleet-task-detail-composer-input"
                placeholder="Leave a comment for whoever picks this up next…"
                value={draft}
                onChange={(event) => {
                  setDraft(event.currentTarget.value);
                  autoGrow();
                }}
                onKeyDown={(event) => {
                  // Enter sends, Shift+Enter (or any IME composition) makes
                  // a newline — the same convention AgentChat's composer
                  // uses, so a comment box and a chat box don't disagree
                  // about what Enter does elsewhere in this app.
                  if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) {
                    event.preventDefault();
                    void submitComment();
                  }
                }}
                rows={1}
                maxLength={4000}
                disabled={posting}
                aria-label="Add a comment"
              />
              <button
                type="submit"
                className="fleet-task-detail-composer-send"
                disabled={!draft.trim() || posting}
                aria-label={posting ? "Posting comment" : "Post comment"}
              >
                {posting ? (
                  <Loader2 size={15} strokeWidth={2} style={{ animation: "spin 1s linear infinite" }} />
                ) : (
                  <ArrowUp size={15} strokeWidth={2} />
                )}
              </button>
            </form>
            {commentNotice ? <p className="fleet-task-page-comment-error">{commentNotice}</p> : null}
          </div>
        ) : null}
      </div>

      <aside className="fleet-task-page-side" aria-label="Properties">
        <div className="fleet-task-page-side-inner">
          <div className="fleet-task-page-side-title">Properties</div>

          <div className="fleet-panel-row">
            <span className="fleet-panel-row-label">
              <span className="fleet-panel-row-icon"><TaskStatusIcon status={task.status} size={15} /></span>
              <span>Status</span>
            </span>
            <span className="fleet-task-detail-control">
              <select
                className="fleet-task-detail-select"
                value={task.status}
                aria-label="Task status"
                onChange={(e) => {
                  const next = e.currentTarget.value as FleetTaskStatus;
                  if (next !== task.status) onStatusChange(task.id, next);
                }}
              >
                {FLEET_TASK_STATUSES.map((s) => (
                  <option key={s} value={s}>{taskStatusLabel(s)}</option>
                ))}
              </select>
            </span>
          </div>
          {/* MAN-294: the Status control above still reads "In progress" —
              that IS the task's real lifecycle state, and the dropdown
              stays the honest place to change it. But "In progress" alone
              here would claim the agent is actively working when it
              provably has not started yet (still waiting out a policy
              delay), which is the confirmed bug this note exists to close.
              Rendered as a plain caption, not a second .fleet-panel-row —
              this qualifies the row above it rather than standing as its
              own property. */}
          {wakeDeferral ? (
            <p className="fleet-task-detail-wake-note" role="status">
              Scheduled — {wakeDeferral.reasonLabel}, starts {wakeDeferral.startsLabel}
            </p>
          ) : null}

          <div className="fleet-panel-row">
            <span className="fleet-panel-row-label">
              <span className="fleet-panel-row-icon">
                {onPriorityChange ? <TaskPriorityIcon priority={priority} size={15} /> : <SignalHigh size={15} strokeWidth={1.75} />}
              </span>
              <span>Priority</span>
            </span>
            <span className="fleet-task-detail-control">
              {onPriorityChange ? (
                <select
                  className="fleet-task-detail-select"
                  value={priority}
                  aria-label="Task priority"
                  onChange={(e) => {
                    const next = Number(e.currentTarget.value);
                    if (next !== priority) onPriorityChange(task.id, next);
                  }}
                >
                  {TASK_PRIORITIES.map((p) => (
                    <option key={p} value={p}>{TASK_PRIORITY_LABELS[p]}</option>
                  ))}
                </select>
              ) : (
                <span className="fleet-cell-secondary">{TASK_PRIORITY_LABELS[priority]}</span>
              )}
            </span>
          </div>

          {/* Assignment is agent-OR-human (MAN-64/MAN-70) and is not a plain
              field write — assignFleetTask/assignFleetTaskToUser also carry
              their own side effects (a wake, for the agent path only) — so
              this hands a {kind, id} selection to the caller's existing
              handler rather than patching anything itself. The two option
              groups keep agents and people visually and structurally
              separate, exactly the "show human vs agent assignees
              distinguishably" requirement the avatar below also serves. */}
          <div className="fleet-panel-row">
            <span className="fleet-panel-row-label">
              <span className="fleet-panel-row-icon"><User size={15} strokeWidth={1.75} /></span>
              <span>Assignee</span>
            </span>
            <span className="fleet-task-detail-control">
              {assignee ? (
                <span className="fleet-agent-avatar" title="Agent">
                  <AgentSigil seed={assignee.agent_id} size={14} />
                </span>
              ) : assignedMember ? (
                <MemberAvatar
                  name={assignedMember.display_name || assignedMember.email}
                  role={assignedMember.role}
                  size="xs"
                  tintIndex={assignedMemberIndex}
                />
              ) : unresolvedAgentAssigneeId ? (
                <span className="fleet-agent-avatar" title="Assigned to an agent outside this project">
                  <AgentSigil seed={unresolvedAgentAssigneeId} size={14} />
                </span>
              ) : null}
              <select
                className="fleet-task-detail-select"
                value={currentAssigneeValue}
                aria-label="Task assignee"
                onChange={(e) => {
                  const next = parseAssigneeOptionValue(e.currentTarget.value);
                  if (next && assigneeOptionValue(next) !== currentAssigneeValue) onAssign(task.id, next);
                }}
              >
                <option value="">Unassigned</option>
                {unresolvedAgentAssigneeId ? (
                  // Not a real choice -- there is no valid "reselect this"
                  // action, only reassign-away-from-it -- so it is the sole
                  // entry in its own group and stays selected until the
                  // owner picks something else from Agents/People below.
                  <option value={assigneeOptionValue({ kind: "agent", id: unresolvedAgentAssigneeId })}>
                    Assigned to an agent outside this project
                  </option>
                ) : null}
                {agents.length > 0 ? (
                  <optgroup label="Agents">
                    {agents.map((a) => (
                      <option key={a.agent_id} value={assigneeOptionValue({ kind: "agent", id: a.agent_id })}>
                        {a.label || "Unnamed agent"}
                      </option>
                    ))}
                  </optgroup>
                ) : null}
                {(members || []).length > 0 ? (
                  <optgroup label="People">
                    {(members || []).map((m) => (
                      <option key={m.user_id} value={assigneeOptionValue({ kind: "user", id: m.user_id })}>
                        {m.display_name || m.email}
                      </option>
                    ))}
                  </optgroup>
                ) : null}
              </select>
            </span>
          </div>

          {/* Labels — a STACKED row, unlike the three above it. Status /
              Priority / Assignee each hold exactly one short value that fits
              beside its caption; a label set is a variable number of chips
              that has to wrap, and squeezing it into the ~170px left over
              beside the caption in a 300px column would put every chip on its
              own line anyway. Caption above, chips below, is what Linear does
              with the same constraint. */}
          <div className="fleet-panel-row fleet-panel-row--stack">
            <span className="fleet-panel-row-label">
              <span className="fleet-panel-row-icon"><TaskLabelRowIcon /></span>
              <span>Labels</span>
            </span>
            {workspaceId && onLabelsChanged ? (
              <TaskLabelEditor
                workspaceId={workspaceId}
                taskId={task.id}
                labels={task.labels}
                onChanged={onLabelsChanged}
              />
            ) : (task.labels || []).length > 0 ? (
              <TaskLabelChips labels={task.labels} max={99} />
            ) : (
              <span className="fleet-cell-muted">None</span>
            )}
          </div>

          <div className="fleet-panel-row">
            <span className="fleet-panel-row-label">
              <span className="fleet-panel-row-icon"><FolderKanban size={15} strokeWidth={1.75} /></span>
              <span>Project</span>
            </span>
            <span className="fleet-panel-row-value">
              <a className="fleet-task-page-side-link" href={projectHref}>
                {projectName}
              </a>
            </span>
          </div>

          {/* Parent task picker — only shown when this task has NO parent
              and the caller has wired onSetParent (a read-only view omits
              it). No dead control: hidden entirely when there are no
              candidates or the picker cannot write. */}
          {!task.parent_task_id && onSetParent && availableParents.length > 0 ? (
            <div className="fleet-panel-row">
              <span className="fleet-panel-row-label">
                <span className="fleet-panel-row-icon"><CornerDownRight size={15} strokeWidth={1.75} /></span>
                <span>Parent</span>
              </span>
              <span className="fleet-task-detail-control">
                <select
                  className="fleet-task-detail-select"
                  value=""
                  aria-label="Set parent task"
                  onChange={(e) => {
                    const next = e.currentTarget.value || null;
                    if (next) onSetParent(task.id, next);
                  }}
                >
                  <option value="">None</option>
                  {availableParents.map((p) => (
                    <option key={p.id} value={p.id}>
                      {p.title || "Untitled task"} · {taskDisplayId(p)}
                    </option>
                  ))}
                </select>
              </span>
            </div>
          ) : null}

          <div className="fleet-panel-row">
            <span className="fleet-panel-row-label">
              <span className="fleet-panel-row-icon"><Calendar size={15} strokeWidth={1.75} /></span>
              <span>Due</span>
            </span>
            {editingDue ? (
              <span className="fleet-task-detail-control" style={{ gap: 4 }}>
                <input
                  ref={dueInputRef}
                  type="date"
                  className="fleet-task-detail-select"
                  value={dueDraft}
                  disabled={!onDueChange}
                  onChange={(e) => setDueDraft(e.currentTarget.value)}
                  onBlur={() => {
                    if (skipDueBlur.current) {
                      skipDueBlur.current = false;
                      return;
                    }
                    commitDue();
                  }}
                  onKeyDown={(e) => {
                    if (e.key === "Enter") {
                      e.preventDefault();
                      e.currentTarget.blur();
                    } else if (e.key === "Escape") {
                      e.preventDefault();
                      skipDueBlur.current = true;
                      setDueDraft(toDateInputValue(task.due_at));
                      setEditingDue(false);
                    }
                  }}
                />
                {onDueChange ? (
                  <button
                    type="button"
                    className="fleet-task-detail-icon-btn"
                    aria-label="Clear due date"
                    title="Clear due date"
                    onClick={clearDue}
                  >
                    <X size={13} strokeWidth={2} />
                  </button>
                ) : null}
              </span>
            ) : (
              <span
                className={`fleet-panel-row-value${task.due_at ? "" : " fleet-panel-row-value--muted"}${onDueChange ? " fleet-panel-row-value--editable" : ""}`}
                {...(onDueChange ? {
                  role: "button",
                  tabIndex: 0,
                  "aria-label": task.due_at ? `Due ${stampDueDate(task.due_at)} — click to edit` : "Due date not set — click to edit",
                  onClick: enterDueEdit,
                  onKeyDown: (e) => {
                    if (e.key === "Enter" || e.key === " ") {
                      e.preventDefault();
                      enterDueEdit();
                    }
                  },
                } : {})}
              >
                {task.due_at ? stampDueDate(task.due_at) : "—"}
              </span>
            )}
          </div>

          {/* "Created by" — omitted outright, not shown as "Unknown", when
              task.created_by is genuinely EMPTY (see resolveEitherActor and
              the file header's ATTRIBUTION note). Real identity only, same
              avatar+name shape as Assignee above — never the raw id. But an
              empty createdByActor with a non-empty task.created_by AND a
              failed lookup is a different fact: a real creator exists and
              we simply couldn't load who — found live 2026-08-13 under an
              expiring session, where this row silently vanished for a task
              created moments earlier by the viewer's own account. Render
              that honestly instead of erasing the row. */}
          {createdByActor ? (
            <div className="fleet-panel-row">
              <span className="fleet-panel-row-label">
                <span className="fleet-panel-row-icon"><UserPlus size={15} strokeWidth={1.75} /></span>
                <span>Created by</span>
              </span>
              <span className="fleet-task-detail-control">
                <TaskActorBadge actor={createdByActor} />
              </span>
            </div>
          ) : identityLookupFailed && task.created_by ? (
            <div className="fleet-panel-row">
              <span className="fleet-panel-row-label">
                <span className="fleet-panel-row-icon"><UserPlus size={15} strokeWidth={1.75} /></span>
                <span>Created by</span>
              </span>
              <span className="fleet-panel-row-value fleet-panel-row-value--muted">Couldn't load who</span>
            </div>
          ) : null}

          <div className="fleet-panel-row">
            <span className="fleet-panel-row-label">
              <span className="fleet-panel-row-icon"><Clock3 size={15} strokeWidth={1.75} /></span>
              <span>Created</span>
            </span>
            <span className={`fleet-panel-row-value${task.created_at ? "" : " fleet-panel-row-value--muted"}`}>
              {task.created_at ? stamp(task.created_at) : "—"}
            </span>
          </div>

          {/* "Completed by" — the one honest actor this page can attach to a
              recent change (see the file header's ATTRIBUTION note for why
              this is not a general "last touched by"). Shown ONLY while the
              task is currently `done`; disappears the instant it reopens,
              matching the backend's own clear-on-reopen behavior exactly —
              never a stale "completed by X" left on a task that isn't
              complete right now. */}
          {completedByActor ? (
            <div className="fleet-panel-row">
              <span
                className="fleet-panel-row-label"
                title={task.completed_at ? `Completed ${stamp(task.completed_at)}` : "Completed"}
              >
                <span className="fleet-panel-row-icon"><CheckCircle2 size={15} strokeWidth={1.75} /></span>
                <span>Completed by</span>
              </span>
              <span className="fleet-task-detail-control">
                <TaskActorBadge actor={completedByActor} />
              </span>
            </div>
          ) : identityLookupFailed && task.status === "done" && (task.completed_by_agent_id || task.completed_by_user_id) ? (
            <div className="fleet-panel-row">
              <span className="fleet-panel-row-label">
                <span className="fleet-panel-row-icon"><CheckCircle2 size={15} strokeWidth={1.75} /></span>
                <span>Completed by</span>
              </span>
              <span className="fleet-panel-row-value fleet-panel-row-value--muted">Couldn't load who</span>
            </div>
          ) : null}

          <div className="fleet-panel-row">
            <span
              className="fleet-panel-row-label"
              title="Last write of any kind — status, assignee, or an agent comment. Not a history."
            >
              <span className="fleet-panel-row-icon"><Clock3 size={15} strokeWidth={1.75} /></span>
              <span>Updated</span>
            </span>
            <span className={`fleet-panel-row-value${task.updated_at ? "" : " fleet-panel-row-value--muted"}`}>
              {task.updated_at ? stamp(task.updated_at) : "—"}
            </span>
          </div>

          {/* The "sub-tasks aren't supported in this view yet" note this
              replaced is gone because it's no longer true: MAN-145 reads
              `parent_task_id` and the subtask rollup for real now (the
              "Sub-task of …" link above the title, the Sub-tasks list
              between the description and Activity). Nothing renders here
              when a task has neither relationship — matching every other
              empty-state convention on this page (TaskLabelChips, the
              Activity empty state) — rather than a permanent caption saying
              so, which is CLAUDE.md's "a professional tool labels, it does
              not lecture" applied to the one spot that used to lecture. */}
        </div>
      </aside>
    </div>
  );
}

/** One ↑/↓ nav control (MAN-145 item 4). A real `<a href>` when there is a
 *  target — so ⌘/Ctrl-click and middle-click get genuine browser new-tab
 *  behaviour for free, exactly like every other task link on this page,
 *  simply by being a real anchor — and a plain, inert `<span>` at a boundary
 *  (no previous/no next), never a `disabled` anchor (anchors don't support
 *  that attribute) and never a live link to nowhere. */
function TaskNavArrow({
  direction,
  target,
  href,
  router,
}: {
  direction: "prev" | "next";
  target: FleetTask | null;
  href: string | null;
  router: { push: (href: string) => void };
}) {
  const Icon = direction === "prev" ? ChevronUp : ChevronDown;
  const label = direction === "prev" ? "Previous task" : "Next task";

  if (!target || !href) {
    return (
      <span className="fleet-task-detail-nav-btn is-disabled" aria-hidden="true">
        <Icon size={14} strokeWidth={2} />
      </span>
    );
  }

  return (
    <a
      className="fleet-task-detail-nav-btn"
      href={href}
      aria-label={label}
      title={target.title ? `${label}: ${target.title}` : label}
      onClick={(event) => {
        if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey || event.button !== 0) return;
        event.preventDefault();
        router.push(href);
      }}
    >
      <Icon size={14} strokeWidth={2} />
    </a>
  );
}

type ResolvedCommentAuthor = {
  label: string;
  kind: "agent" | "external_agent" | "human" | "unattributed";
  /** The author's own id — AgentSigil's seed, so one agent draws the same
   *  mark everywhere it appears. */
  id: string;
  role?: WorkspaceMember["role"];
  memberIndex: number;
};

/** "ext_agent_5f3a2b1c9d0e4f11" -> "5f3a2b1c". Only ever reached when an
 *  external agent has NO name from either source (see below) — a short,
 *  stable discriminator beats collapsing two different agents into one
 *  anonymous label, and it is never the full opaque id. */
function externalAgentShortId(id: string): string {
  return id.replace(/^ext_agent_/, "").slice(0, 8);
}

/** Comment authors are stored as an opaque (author_type, author_id) pair.
 *  Three kinds of author resolve here, and BEFORE 2026-08-01 only two of
 *  them did:
 *
 *   · a PLATFORM agent — `author_id` is a `workspace_agent_installs` id,
 *     resolved against this project's `agents`;
 *   · a HUMAN ("user"/"human" author_type; add_human_task_comment hardcodes
 *     "human") — resolved against the workspace `members`;
 *   · an EXTERNAL agent (`author_type` "external_agent") — a Claude Code /
 *     Codex session that commented through our MCP server at /mcp. Its
 *     `ext_agent_<hex16>` id is in NEITHER list, so it fell through to the
 *     literal word "Unknown" on a board whose entire proposition is that you
 *     can see who did what. It now resolves against the workspace roster
 *     (useWorkspaceRoster -> GET .../fleet/roster).
 *
 *  Resolution order for an external agent is live-then-snapshot: the roster
 *  is current truth, `comment.author_display_name` is the name that agent
 *  had when it wrote (persisted by add_task_comment) and covers a comment
 *  whose roster row is gone. Only if BOTH are missing does it fall back —
 *  and to "External agent 5f3a2b1c", which is true and distinguishing,
 *  never to a bare "Unknown". */
function resolveCommentAuthor(
  comment: TaskComment,
  agents: FleetAgent[],
  members?: WorkspaceMember[],
  externalAgents?: WorkspaceRosterEntry[],
): ResolvedCommentAuthor {
  const id = String(comment.author_id || "").trim();
  const type = String(comment.author_type || "").trim();

  const agent = agents.find((a) => a.agent_id === id);
  if (agent) return { label: agent.label || "Unnamed agent", kind: "agent", id, memberIndex: 0 };

  const memberIndex = (members || []).findIndex((m) => m.user_id === id);
  if (memberIndex >= 0) {
    const member = (members || [])[memberIndex];
    return {
      label: member.display_name || member.email,
      kind: "human",
      id,
      role: member.role,
      memberIndex,
    };
  }

  const external = (externalAgents || []).find((e) => e.id === id);
  if (external && external.display_name.trim()) {
    return { label: external.display_name.trim(), kind: "external_agent", id, memberIndex: 0 };
  }
  if (type === "external_agent" || id.startsWith("ext_agent_")) {
    const snapshot = String(comment.author_display_name || "").trim();
    if (snapshot) return { label: snapshot, kind: "external_agent", id, memberIndex: 0 };
    const short = externalAgentShortId(id);
    return {
      label: short ? `External agent ${short}` : "External agent",
      kind: "external_agent",
      id,
      memberIndex: 0,
    };
  }

  if (type === "agent") return { label: id || "Agent", kind: "agent", id, memberIndex: 0 };
  if (type === "user" || type === "human") return { label: id || "Person", kind: "human", id, memberIndex: 0 };
  // Neither an id nor a type — there is genuinely nothing to attribute this
  // to, and saying so is honest where "Unknown" only sounds like a bug.
  return { label: id || type || "Unattributed", kind: "unattributed", id, memberIndex: 0 };
}

/** The author line of one comment: identity mark + name. The mark is not a
 *  treatment invented for external agents — it is the same AgentSigil every
 *  agent already carries in the Assignee row, the mention chips and the
 *  Team roster, and the same MemberAvatar a person carries there, applied
 *  consistently to whoever wrote the comment. An external agent reading as a
 *  real participant is the point; a badge marking it out as a special case
 *  would defeat it. */
function CommentAuthorLine({ author }: { author: ResolvedCommentAuthor }) {
  return (
    <span className="fleet-task-page-comment-author">
      {author.kind === "human" ? (
        <MemberAvatar name={author.label} role={author.role} size="xs" tintIndex={author.memberIndex} />
      ) : author.kind === "unattributed" ? null : (
        <AgentSigil seed={author.id || author.label} size={13} />
      )}
      <span>{author.label}</span>
    </span>
  );
}
