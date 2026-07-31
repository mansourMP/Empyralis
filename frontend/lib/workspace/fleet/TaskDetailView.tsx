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
 *  · Sub-issue CREATION or re-parenting. The column and the rollup are real
 *    (migrations/add_task_parent.sql — `parent_task_id`, plus the
 *    subtask_count / subtask_done_count project_tasks_service returns on
 *    every read) and MAN-145 wires this page up to READ them — a "Sub-task
 *    of …" link when this task has a parent, a Sub-tasks list when it has
 *    children (both looked up from the same sibling-task read prev/next
 *    below already does — see that note). What's still missing is a way to
 *    CREATE that relationship from here; that stays a drawn promise for a
 *    narrower reason than before; the storage and the reading surface are
 *    both real now, only the write surface isn't built.
 *  · Rich text. `description` is a plain-text column; it is rendered with
 *    paragraph breaks preserved, not parsed as markdown it may not be.
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
 * AgentChat's .fleet-sage-chat-composer already uses) instead of a bare
 * textarea + wide button, but the write path and the optimistic-then-
 * refetch contract are unchanged.
 *
 * PREV/NEXT NAVIGATION AND RELATED TASKS (MAN-145 items 4/5) share one read:
 * this component calls useFleetTasks(workspaceId, projectId) itself, the
 * SAME hook with the SAME cache key page.tsx already calls to find `task`
 * in the first place (fleet-data.ts's useSharedPolledResource keys on
 * `fleet-tasks:${workspaceId}:${projectId}` — a second caller with the same
 * key subscribes to the existing polled entry, it does not issue a second
 * request). That matters for correctness, not just efficiency: it is
 * PROVABLY the same array, in the same order, that TasksList renders
 * unmodified and TasksBoard/TasksGroupedList group without re-sorting (see
 * that page's `boardTasks` — no client-side sort is ever applied to tasks,
 * unlike the agents view). So "N / total" and the ↑/↓ targets are not a
 * best-effort guess at what the user saw — they ARE what the user saw. The
 * one thing this page cannot know is which of the three layouts (board /
 * grouped / list) or which filter the user was actually looking at, since
 * that state lives in the project page's URL query string, not in this
 * task's own payload — the nav is hidden outright (not shown with a wrong
 * count) if this task can't be found in that read, rather than guessing.
 */

import { useCallback, useEffect, useMemo, useRef, useState, type CSSProperties, type ReactNode } from "react";
import { useRouter } from "next/navigation";
import {
  ArrowUp,
  Calendar,
  ChevronDown,
  ChevronUp,
  Clock3,
  CornerDownRight,
  FolderKanban,
  Loader2,
  MessageSquare,
  SignalHigh,
  User,
} from "lucide-react";

import { AgentSigil } from "./fleet-indicators";
import {
  TaskStatusIcon,
  TaskPriorityIcon,
  taskShortId,
  taskStatusLabel,
  taskPriority,
  TASK_PRIORITIES,
  TASK_PRIORITY_LABELS,
} from "./task-status";
import { TaskLabelChips, TaskLabelEditor, TaskLabelRowIcon } from "./task-labels";
import { formatDateTime, timeAgo } from "./fleet-presentation";
import { MemberAvatar } from "./MemberAvatarStack";
import type { WorkspaceMember } from "./members-data";
import {
  assigneeOptionValue,
  commentFleetTask,
  FLEET_TASK_STATUSES,
  parseAssigneeOptionValue,
  useFleetTasks,
  type FleetAgent,
  type FleetTask,
  type FleetTaskStatus,
  type TaskAssigneeSelection,
} from "./fleet-data";
import "./task-detail.css";

/** Minute precision, not the default's seconds — no decision on this page
 *  turns on a second, and the extra characters only cost the value column
 *  width it does not have. */
function stamp(value: string): string {
  return formatDateTime(value, { dateStyle: "medium", timeStyle: "short" });
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
  workspaceId,
  projectName,
  projectHref,
  onStatusChange,
  onPriorityChange,
  onAssign,
  onLabelsChanged,
  onCommentPosted,
}: {
  task: FleetTask;
  /** Agents in this project — valid AGENT assignees. */
  agents: FleetAgent[];
  /** Workspace members (MAN-64/MAN-70) — valid HUMAN assignees, and the
   *  lookup used to render a human commenter's real name. Absent → the
   *  Assignee picker offers agents only (degrades to the pre-MAN-64
   *  behavior) and human comments fall back to their raw author id. */
  members?: WorkspaceMember[];
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
  /** Assignee is agent-or-human (MAN-64/MAN-70) — the caller dispatches to
   *  assignFleetTask or assignFleetTaskToUser based on `selection.kind`. */
  onAssign: (taskId: string, selection: TaskAssigneeSelection) => void;
  /** Refetch after a label attach/detach. Labels are not part of the task
   *  PATCH — they are their own endpoints — so the editor writes directly and
   *  then asks the page to re-read. */
  onLabelsChanged?: () => void | Promise<void>;
  /** Refetch after a human comment is posted. Same contract as
   *  onLabelsChanged, same reason: POST .../comments is its own endpoint,
   *  not part of the task PATCH, so the composer below writes directly and
   *  then asks the page to re-read the (30s-polled) task list. */
  onCommentPosted?: () => void | Promise<void>;
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
  const assignee = agents.find((a) => a.agent_id === task.assignee_agent_id) || null;
  // The human half of MAN-64/MAN-70 -- only looked up when there is no
  // agent assignee, matching the backend's own mutual-exclusivity
  // guarantee (project_tasks_single_assignee_check: at most one of the two
  // is ever set).
  const assignedMember = !assignee && task.assignee_user_id
    ? (members || []).find((m) => m.user_id === task.assignee_user_id) || null
    : null;
  const assignedMemberIndex = assignedMember ? (members || []).indexOf(assignedMember) : 0;
  const currentAssigneeValue = assignee
    ? assigneeOptionValue({ kind: "agent", id: assignee.agent_id })
    : assignedMember
      ? assigneeOptionValue({ kind: "user", id: assignedMember.user_id })
      : task.assignee_agent_id
        ? assigneeOptionValue({ kind: "agent", id: task.assignee_agent_id })
        : task.assignee_user_id
          ? assigneeOptionValue({ kind: "user", id: task.assignee_user_id })
          : "";
  const comments = useMemo(() => readComments(task), [task]);

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
  const taskDetailHref = useCallback(
    (id: string) => `${projectHref}/tasks/${encodeURIComponent(id)}`,
    [projectHref],
  );
  const siblingIndex = useMemo(
    () => siblingTasks.findIndex((t) => t.id === task.id),
    [siblingTasks, task.id],
  );
  // Hidden outright (not shown with a wrong or single-item count) unless
  // this task was actually found in the read and there is somewhere to go
  // -- an arrow pair that jumps somewhere unexpected is worse than none.
  const showTaskNav = siblingIndex >= 0 && siblingTasks.length > 1;
  const prevTask = siblingIndex > 0 ? siblingTasks[siblingIndex - 1] : null;
  const nextTask = siblingIndex >= 0 && siblingIndex < siblingTasks.length - 1
    ? siblingTasks[siblingIndex + 1]
    : null;
  const parentTask = useMemo(
    () => (task.parent_task_id ? siblingTasks.find((t) => t.id === task.parent_task_id) || null : null),
    [siblingTasks, task.parent_task_id],
  );
  const subtasks = useMemo(
    () => siblingTasks.filter((t) => t.parent_task_id === task.id),
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
  const displayComments = useMemo(() => (pending ? [...comments, pending] : comments), [comments, pending]);

  // MAN-145: the composer textarea auto-grows with its content instead of
  // sitting at a fixed 2 rows or exposing a manual resize handle — same
  // idiom, same cap (200px), as AgentChat's .fleet-sage-chat-input.
  const composerRef = useRef<HTMLTextAreaElement | null>(null);
  const autoGrow = useCallback(() => {
    const el = composerRef.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, 200)}px`;
  }, []);

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
      <div className="fleet-task-page-main">
        <div className="fleet-task-page-body">
          <div className="fleet-task-detail-topbar">
            <div className="fleet-task-page-eyebrow">{taskShortId(task.id)}</div>
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
            <a
              className="fleet-task-detail-parent-link"
              href={taskDetailHref(parentTask.id)}
              data-tab-title={parentTask.title || "Untitled task"}
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
          ) : null}

          <h1 className="fleet-task-page-title" tabIndex={-1} ref={headingRef}>
            {task.title || "Untitled task"}
          </h1>

          {task.description ? (
            <div className="fleet-task-page-desc">
              {task.description.split(/\n{2,}/).map((para, i) => (
                <p key={i}>{para}</p>
              ))}
            </div>
          ) : (
            <p className="fleet-task-page-desc fleet-cell-muted">No description.</p>
          )}

          {subtasks.length > 0 ? (
            <section className="fleet-task-page-section" aria-label="Sub-tasks">
              <h2 className="fleet-task-page-section-title">
                Sub-tasks · {subtasks.filter((t) => t.status === "done").length}/{subtasks.length}
              </h2>
              <ul className="fleet-task-detail-subtask-list">
                {subtasks.map((st) => (
                  <li key={st.id}>
                    <a
                      className="fleet-task-detail-subtask-row"
                      href={taskDetailHref(st.id)}
                      data-tab-title={st.title || "Untitled task"}
                      onClick={(event) => {
                        if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey || event.button !== 0) return;
                        event.preventDefault();
                        router.push(taskDetailHref(st.id));
                      }}
                    >
                      <TaskStatusIcon status={st.status} size={13} />
                      <span className="fleet-task-detail-subtask-title">{st.title || "Untitled task"}</span>
                      <span className="fleet-task-detail-subtask-id">{taskShortId(st.id)}</span>
                    </a>
                  </li>
                ))}
              </ul>
            </section>
          ) : null}

          <section className="fleet-task-page-section" aria-label="Activity">
            <h2 className="fleet-task-page-section-title">Activity</h2>
            {displayComments.length === 0 ? (
              <div className="fleet-task-page-activity-empty">
                <MessageSquare size={14} strokeWidth={1.75} />
                <span>
                  No comments yet. Post one below — agents working this task can leave
                  updates here too.
                </span>
              </div>
            ) : (
              <ul className="fleet-task-page-comments">
                {displayComments.map((c, i) => (
                  <li
                    key={c.id || i}
                    className={`fleet-task-page-comment${c === pending ? " is-pending" : ""}`}
                  >
                    <div className="fleet-task-page-comment-head">
                      <span className="fleet-task-page-comment-author">
                        {commentAuthorLabel(c, agents, members)}
                      </span>
                      {c.created_at ? (
                        <span className="fleet-task-page-comment-time" title={stamp(c.created_at)}>
                          {timeAgo(c.created_at) || stamp(c.created_at)}
                        </span>
                      ) : null}
                    </div>
                    <div className="fleet-task-page-comment-body">{renderCommentBody(c, agents, members)}</div>
                  </li>
                ))}
              </ul>
            )}

            {workspaceId ? (
              <>
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
              </>
            ) : null}
          </section>
        </div>
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
              <a className="fleet-task-page-side-link" href={projectHref} data-tab-title={projectName}>
                {projectName}
              </a>
            </span>
          </div>

          <div className="fleet-panel-row">
            <span className="fleet-panel-row-label">
              <span className="fleet-panel-row-icon"><Calendar size={15} strokeWidth={1.75} /></span>
              <span>Due</span>
            </span>
            <span className={`fleet-panel-row-value${task.due_at ? "" : " fleet-panel-row-value--muted"}`}>
              {task.due_at ? stamp(task.due_at) : "—"}
            </span>
          </div>

          <div className="fleet-panel-row">
            <span className="fleet-panel-row-label">
              <span className="fleet-panel-row-icon"><Clock3 size={15} strokeWidth={1.75} /></span>
              <span>Created</span>
            </span>
            <span className={`fleet-panel-row-value${task.created_at ? "" : " fleet-panel-row-value--muted"}`}>
              {task.created_at ? stamp(task.created_at) : "—"}
            </span>
          </div>

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
 *  target — so ⌘/Ctrl-click and middle-click open it in a background content
 *  tab exactly like every other task link on this page (FleetTabs reads
 *  `a[href]` directly; no `data-tab-href` needed) — and a plain, inert
 *  `<span>` at a boundary (no previous/no next), never a `disabled` anchor
 *  (anchors don't support that attribute) and never a live link to nowhere. */
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
      data-tab-title={target.title || "Untitled task"}
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

/** Comment authors are stored as an opaque (author_type, author_id) pair.
 *  An agent id resolves to its real label when that agent is in this
 *  project; a human ("user"/"human" author_type -- add_human_task_comment
 *  hardcodes "human") resolves to their real name when they're a workspace
 *  member; anything else falls back to the honest raw type. */
function commentAuthorLabel(comment: TaskComment, agents: FleetAgent[], members?: WorkspaceMember[]): string {
  const id = String(comment.author_id || "").trim();
  const type = String(comment.author_type || "").trim();
  const agent = agents.find((a) => a.agent_id === id);
  if (agent) return agent.label || "Unnamed agent";
  const member = (members || []).find((m) => m.user_id === id);
  if (member) return member.display_name || member.email;
  if (type === "agent") return id || "Agent";
  if (type === "user" || type === "human") return id || "Person";
  return id || type || "Unknown";
}
