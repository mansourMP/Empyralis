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
 *  · Sub-issues. The COLUMN exists now (migrations/add_task_parent.sql —
 *    `parent_task_id`, plus the subtask_count / subtask_done_count rollup
 *    project_tasks_service returns on every read), but nothing in this UI
 *    creates, lists or links one. A sub-issues section would therefore be a
 *    drawn promise for a different reason than before: the storage is real,
 *    the surface isn't built. The side note at the bottom of the properties
 *    column says exactly that rather than the older, now-false claim that
 *    neither labels nor sub-tasks were stored at all.
 *  · Rich text. `description` is a plain-text column; it is rendered with
 *    paragraph breaks preserved, not parsed as markdown it may not be.
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
 */

import { useEffect, useMemo, useRef, useState, type CSSProperties, type ReactNode } from "react";
import { useRouter } from "next/navigation";
import { Calendar, Clock3, FolderKanban, MessageSquare, SignalHigh, User } from "lucide-react";

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
import { TINTS, tintForAgent, formatDateTime, timeAgo } from "./fleet-presentation";
import { MemberAvatar } from "./MemberAvatarStack";
import type { WorkspaceMember } from "./members-data";
import {
  assigneeOptionValue,
  commentFleetTask,
  FLEET_TASK_STATUSES,
  parseAssigneeOptionValue,
  type FleetAgent,
  type FleetTask,
  type FleetTaskStatus,
  type TaskAssigneeSelection,
} from "./fleet-data";

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
 *  surrounding text (a tinted pill, same tinted-circle identity treatment
 *  as the Assignee row above: AgentSigil for an agent, MemberAvatar for a
 *  person) and distinguishable from EACH OTHER (agent vs human), matching
 *  this page's existing "agent and person are both team members, but never
 *  drawn identically" convention. Deliberately styled inline rather than
 *  via a new fleet-theme.css class -- that stylesheet is shared/load-
 *  bearing across nearly every fleet surface (docs/AGENT-OPERATING-RULES.md
 *  "All fleet UI shares files") and another agent may be editing it
 *  concurrently; every color here is one of the same CSS custom properties
 *  (--tile-bg/--tile-fg/--rail-active/--text-primary) the rest of this file
 *  already reads, so it stays on-theme (light/dark) without a new rule. */
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
    const idx = agent ? agents.indexOf(agent) : 0;
    const tint = agent ? TINTS[tintForAgent(agent, idx)] : null;
    const style = { ...chipStyle, ...(tint ? { "--tile-bg": tint.bg, "--tile-fg": tint.fg } : {}) } as CSSProperties;
    return (
      <span className="fleet-mention-chip fleet-mention-chip--agent" style={style} title={`Agent: ${label}`}>
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
  const assigneeIndex = assignee ? agents.indexOf(assignee) : 0;
  const tint = assignee ? TINTS[tintForAgent(assignee, assigneeIndex)] : null;
  const avatarStyle = (tint ? { "--tile-bg": tint.bg, "--tile-fg": tint.fg } : {}) as CSSProperties;
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

  async function submitComment() {
    const body = draft.trim();
    if (!body || posting || !workspaceId) return;
    setPosting(true);
    setCommentNotice(null);
    setPending({ id: `pending-${Date.now()}`, author_type: "human", author_id: "You", body, created_at: new Date().toISOString() });
    try {
      const { wakeError } = await commentFleetTask(workspaceId, task.id, body);
      setDraft("");
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
    <div className="fleet-task-page">
      <div className="fleet-task-page-main">
        <div className="fleet-task-page-body">
          <div className="fleet-task-page-eyebrow">{taskShortId(task.id)}</div>
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
              <form
                className="fleet-task-page-comment-form"
                onSubmit={(event) => {
                  event.preventDefault();
                  void submitComment();
                }}
              >
                <textarea
                  className="fleet-task-page-comment-input"
                  placeholder="Leave a comment for whoever picks this up next…"
                  value={draft}
                  onChange={(event) => setDraft(event.currentTarget.value)}
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
                  rows={2}
                  maxLength={4000}
                  disabled={posting}
                  aria-label="Add a comment"
                />
                {commentNotice ? <p className="fleet-task-page-comment-error">{commentNotice}</p> : null}
                <div className="fleet-task-page-comment-form-actions">
                  <button
                    type="submit"
                    className="fleet-btn fleet-btn--accent"
                    disabled={!draft.trim() || posting}
                  >
                    {posting ? "Posting…" : "Comment"}
                  </button>
                </div>
              </form>
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
                <span className="fleet-agent-avatar" style={avatarStyle} title="Agent">
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

          {/* Sub-tasks: honest, and NARROWER than the note this replaces.
              That note said labels and sub-tasks were both unstored; labels
              now are (workspace_labels / project_task_labels, edited in the
              row above), so repeating it would be a lie. The backend already
              tracks task parentage — what's missing for sub-tasks is this UI,
              not the data, and that's a different sentence. Product copy: say
              what the user gets (nothing here, yet), not the column that
              backs it. */}
          <div className="fleet-task-page-side-note">
            Sub-tasks aren't supported in this view yet.
          </div>
        </div>
      </aside>
    </div>
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
