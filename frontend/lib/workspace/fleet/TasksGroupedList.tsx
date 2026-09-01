"use client";

/**
 * The grouped list — Linear's Issues page. This is the LIST view with a
 * grouping applied, not a view of its own.
 *
 * IT USED TO BE A THIRD PILL ("Board | Grouped | List"), and that was the
 * modelling error: "Grouped" is not a shape of the data beside Board and
 * List, it is the list with a grouping switched on. Linear gets this right —
 * the view is List-or-Board and grouping is an OPTION — so 2026-08-01 the
 * switch collapsed to Board | List and this component became what the List
 * renders whenever the view-options popover has a grouping other than "No
 * grouping" (see task-view-options.ts). Nothing about how a section reads
 * changed; only what decides to show one.
 *
 * The two shapes now answer:
 *   · Board — "what is the shape of the work right now". Spatial; seven
 *     columns side by side; you read it without reading it.
 *   · List — one line per task. Flat (TasksList) when nothing is grouping it,
 *     sectioned (this) when something is: the whole backlog in one vertical
 *     scroll instead of seven horizontal ones, sections you can collapse, and
 *     a tab strip narrowing the statuses down to the ones that are live.
 *
 * GROUPING BY. Status is the original and the one with the most affordances
 * (a status section can seed the composer, so it keeps its "+"). Assignee,
 * priority and label are the other three the data genuinely supports; each
 * renders its own glyph in the section header so a heading is never just
 * text. groupTasks() in task-view-options.ts owns which sections exist and in
 * what order — including the rule that a task with two labels appears under
 * both, which is what grouping by label means.
 *
 * TABS. Active / Backlog / All, derived from FLEET_TASK_STATUSES rather than
 * re-listed — Active is "everything that is neither parked before the work
 * (backlog) nor finished (done)", which stays true if an eighth status is
 * ever added, where a hand-copied list would quietly stop including it. They
 * are a filter on the TASKS, so they compose with every grouping, not just
 * the status one.
 *
 * ZERO-COUNT SECTIONS are hidden outside "All". A working view should not
 * make you scroll past four empty headings to reach the twelve things you
 * have to do. "All" keeps them — for the status grouping, where the set of
 * possible sections is a fixed vocabulary and "is Blocked genuinely empty?"
 * is a real question. For the other three groupings a section only exists
 * because a task put it there, so there is no such thing as an empty one.
 *
 * COLLAPSE STATE persists per workspace (`fleet:glist-collapsed:v1:<id>`,
 * the same `fleet:*` localStorage convention as the rail's width). Per
 * workspace, not per project: "I never look at Done" is a statement about how
 * a team works. The stored value is a flat list of GROUP KEYS; a status
 * group's key is the status itself, so blobs written before grouping existed
 * still read correctly.
 *
 * MOTION. The chevron rotates over --dur-2. THE BODY DOES NOT ANIMATE — it
 * unmounts. Animating max-height is a layout-triggering property (reflow
 * every frame, not just composite) and is exactly what fleet-theme.css's
 * .fleet-rail-section-items rule already documents removing; a section
 * holding forty rows would jank on every toggle. Unmounting is also why the
 * collapsed rows cost nothing to keep collapsed.
 */

import { useCallback, useEffect, useMemo, useState, type CSSProperties } from "react";
import { ChevronRight, Plus } from "lucide-react";

import { CopyLinkButton } from "@/lib/ui/CopyLinkButton";
import { dueLabel } from "./TasksList";
import { timeAgo } from "./fleet-presentation";
import { AgentSigil } from "./fleet-indicators";
import { MemberAvatar } from "./MemberAvatarStack";
import type { WorkspaceMember } from "./members-data";
import { TaskLabelChips } from "./task-labels";
import {
  TaskStatusIcon,
  TaskPriorityIcon,
  TaskWakeDeferralIcon,
  taskStatusLabel,
  taskPriority,
  taskDisplayId,
  TASK_PRIORITY_LABELS,
} from "./task-status";
import {
  FLEET_TASK_STATUSES,
  countTasksByStatus,
  normalizeTaskStatus,
  type FleetAgent,
  type FleetTask,
  type FleetTaskStatus,
} from "./fleet-data";
import {
  DEFAULT_TASK_VIEW_OPTIONS,
  groupTasks,
  type TaskDisplayState,
  type TaskGroup,
  type TaskGrouping,
} from "./task-view-options";

type GroupTab = "active" | "backlog" | "all";

/** Everything that is neither parked before the work nor finished. Derived,
 *  not re-listed — see the file header. Resolves today to
 *  todo · in_progress · awaiting_input · blocked · in_review, in board order. */
const ACTIVE_STATUSES: FleetTaskStatus[] = FLEET_TASK_STATUSES.filter(
  (s) => s !== "backlog" && s !== "done",
);

const TABS: { key: GroupTab; label: string; statuses: FleetTaskStatus[] }[] = [
  { key: "active", label: "Active", statuses: ACTIVE_STATUSES },
  { key: "backlog", label: "Backlog", statuses: ["backlog"] },
  { key: "all", label: "All", statuses: FLEET_TASK_STATUSES },
];

/** localStorage key for this workspace's collapsed sections — `fleet:*`,
 *  versioned, workspace-scoped, exactly like the rail's width. */
function collapsedKey(workspaceId: string): string {
  return `fleet:glist-collapsed:v1:${workspaceId}`;
}

/** Sanity bound on a persisted list a human grows one click at a time. A blob
 *  longer than this is corrupt or hand-edited, not a preference. */
const MAX_COLLAPSED = 200;

function readCollapsed(workspaceId: string): string[] {
  try {
    const raw = window.localStorage.getItem(collapsedKey(workspaceId));
    if (!raw) return [];
    const parsed = JSON.parse(raw);
    if (!Array.isArray(parsed) || parsed.length > MAX_COLLAPSED) return [];
    // Group keys, not statuses: this list now holds keys from four different
    // groupings. A status group's key IS the status string, so a blob written
    // before grouping existed still reads correctly — which is why this
    // stayed on v1 rather than migrating everyone's folded sections away.
    return parsed.filter((s): s is string => typeof s === "string" && s.length > 0 && s.length < 128);
  } catch {
    return [];
  }
}

/** The seven-track grid `.fleet-glist-row` uses, rebuilt from whatever the
 *  reader left switched on. A hidden cell loses its TRACK as well as its
 *  contents — otherwise the four right-hand columns would still be lined up
 *  against gaps where the hidden ones used to be. Title and status are always
 *  present: a row with no title is not a row, and the status ring is the only
 *  keyboard control for moving a task off this view. */
function gridTracks(display: TaskDisplayState, mobile: boolean): string {
  return [
    display.priority ? "16px" : null,
    // Mobile drops id/due/updated wholesale (see the media query) — it always
    // did, and at 375px the seven tracks were unreadable.
    !mobile && display.id ? "56px" : null,
    "16px",
    "minmax(0, 1fr)",
    !mobile && display.due ? "54px" : null,
    !mobile && display.updated ? "58px" : null,
    display.assignee ? "20px" : null,
    // Copy link — always on (not a display-property toggle, same "permanent
    // page control, not a data column" reasoning as TasksList's own trailing
    // track) and desktop-only, matching every other track this file already
    // drops at 768px (mobile is out of scope for this pass — see the file
    // header). .fleet-glist-cell-copy is display:none under that same media
    // query, so the track and the cell agree.
    !mobile ? "20px" : null,
  ]
    .filter(Boolean)
    .join(" ");
}

export function TasksGroupedList({
  workspaceId,
  tasks,
  agents,
  members,
  grouping = "status",
  display = DEFAULT_TASK_VIEW_OPTIONS.display,
  hrefFor,
  onSelect,
  onStatusChange,
  onCreateTask,
}: {
  /** Scopes the collapse preference. Absent → collapse is session-only. */
  workspaceId?: string;
  tasks: FleetTask[];
  /** Agents in this project — valid AGENT assignees, and what names an
   *  assignee section. */
  agents: FleetAgent[];
  /** Workspace members (MAN-64/MAN-70) — resolves a HUMAN assignee's avatar
   *  on a row, and names their assignee section. Without this a
   *  human-assigned task would render as "unassigned" here, which is exactly
   *  the display bug this rollout closes. */
  members?: WorkspaceMember[];
  /** What the sections are. "none" never reaches this component — the page
   *  renders the flat TasksList for it — so the fallback is the original
   *  behaviour, status. */
  grouping?: Exclude<TaskGrouping, "none">;
  /** View-options "Display properties". Defaults to everything on. */
  display?: TaskDisplayState;
  /** The task's own route (`{projectHref}/tasks/{id}`) — same prop name and
   *  shape as DocumentsList's `hrefFor`. Makes the row a real `<a href>`
   *  instead of an onClick div, per CLAUDE.md: "Primary navigation is real
   *  links, so cmd-click and middle-click work." */
  hrefFor: (taskId: string) => string;
  onSelect: (taskId: string) => void;
  /** Optional: makes the row's status ring a real control. Omit and the ring
   *  is a read-only glyph. */
  onStatusChange?: (taskId: string, status: FleetTaskStatus) => void;
  /** Section `+`: open the composer with that section's status pre-set — the
   *  same handler the board columns' `+` already uses. Only a STATUS section
   *  can offer it (a new task is born with a status; it cannot be born with
   *  an assignee or a label), so it is not rendered on the other groupings
   *  rather than rendered and ignoring which section it was clicked in. */
  onCreateTask?: (status: FleetTaskStatus) => void;
}) {
  const [tab, setTab] = useState<GroupTab>("active");
  // Hydrated in an effect, never during render, so the server's markup and
  // the client's first paint agree (same discipline as useFleetPreferences).
  const [collapsed, setCollapsed] = useState<string[]>([]);
  useEffect(() => {
    if (workspaceId) setCollapsed(readCollapsed(workspaceId));
  }, [workspaceId]);

  const toggleCollapsed = useCallback(
    (key: string) => {
      setCollapsed((cur) => {
        const next = cur.includes(key) ? cur.filter((s) => s !== key) : [...cur, key];
        if (workspaceId) {
          try {
            window.localStorage.setItem(collapsedKey(workspaceId), JSON.stringify(next));
          } catch {
            /* localStorage unavailable — the toggle still works this session */
          }
        }
        return next;
      });
    },
    [workspaceId],
  );

  // One shared helper for the counts (fleet-data.countTasksByStatus), the same
  // one the board headers and the Overview roll-up read, so the tab counts and
  // a stat card cannot disagree about what a status means.
  const counts = useMemo(() => countTasksByStatus(tasks), [tasks]);
  const activeTab = TABS.find((t) => t.key === tab) || TABS[0];
  const tabTotal = activeTab.statuses.reduce((sum, s) => sum + counts[s], 0);

  // The tab is a filter on TASKS, applied before grouping — which is what
  // lets Active/Backlog/All compose with an assignee or label grouping
  // instead of only making sense over status sections.
  const visibleTasks = useMemo(
    () => tasks.filter((t) => activeTab.statuses.includes(normalizeTaskStatus(t.status))),
    [tasks, activeTab],
  );

  const sections = useMemo(
    () =>
      groupTasks(visibleTasks, grouping, {
        agents,
        members: members || [],
        // "All" is the one place you go to confirm a status is genuinely
        // empty rather than filtered away. Only meaningful for the status
        // grouping — every other grouping's sections exist only because a
        // task created them.
        includeEmptyStatuses: grouping === "status" && tab === "all",
      }),
    [visibleTasks, grouping, agents, members, tab],
  );

  const rowStyle = useMemo(
    () =>
      ({
        "--fleet-glist-grid": gridTracks(display, false),
        "--fleet-glist-grid-mobile": gridTracks(display, true),
      }) as CSSProperties,
    [display],
  );

  return (
    <div className="fleet-glist">
      {/* Underlined tabs, not another .fleet-segmented: a segmented control
          right below the Board/List segmented control would read as two
          halves of one switch. These are a filter ON the view, one level
          down, and Linear draws exactly this distinction the same way. */}
      <div className="fleet-glist-tabs" role="tablist" aria-label="Task filter">
        {TABS.map((t) => {
          const total = t.statuses.reduce((sum, s) => sum + counts[s], 0);
          return (
            <button
              key={t.key}
              type="button"
              role="tab"
              aria-selected={tab === t.key}
              className={`fleet-glist-tab${tab === t.key ? " is-active" : ""}`}
              onClick={() => setTab(t.key)}
            >
              {t.label}
              <span className="fleet-glist-tab-count">{total}</span>
            </button>
          );
        })}
      </div>

      {tabTotal === 0 ? (
        <div className="fleet-glist-empty">
          {tab === "active"
            ? "Nothing active. Everything here is either in the backlog or done."
            : tab === "backlog"
              ? "The backlog is empty."
              : "No tasks in this project."}
        </div>
      ) : (
        sections.map((section) => {
          const label = sectionLabel(section);
          const isCollapsed = collapsed.includes(section.key);
          return (
            <section key={section.key} className="fleet-glist-section" aria-label={label}>
              <header className="fleet-glist-header">
                <button
                  type="button"
                  className="fleet-glist-header-btn"
                  aria-expanded={!isCollapsed}
                  onClick={() => toggleCollapsed(section.key)}
                >
                  {/* Rotation is the whole animation, and it is a transform —
                      the one property this surface lets motion touch. */}
                  <ChevronRight size={13} strokeWidth={2.25} className="fleet-glist-chevron" />
                  <SectionGlyph section={section} agents={agents} members={members} />
                  <span className="fleet-glist-header-title">{label}</span>
                  <span className="fleet-glist-header-count">{section.count}</span>
                </button>
                <span className="fleet-glist-header-spacer" />
                {onCreateTask && section.createStatus ? (
                  <button
                    type="button"
                    className="fleet-glist-header-add"
                    title={`New task in ${label}`}
                    aria-label={`New task in ${label}`}
                    onClick={() => onCreateTask(section.createStatus as FleetTaskStatus)}
                  >
                    <Plus size={14} strokeWidth={2} />
                  </button>
                ) : null}
              </header>

              {/* Unmounted, not hidden — see the file header on why nothing
                  here animates height. */}
              {isCollapsed ? null : (
                <div className="fleet-glist-rows">
                  {section.tasks.length === 0 ? (
                    <div className="fleet-glist-section-empty">No tasks in {label.toLowerCase()}.</div>
                  ) : (
                    section.tasks.map((task, index) => (
                      // Keyed by SECTION + task: grouping by label puts a task
                      // carrying two labels in two sections, and a bare task
                      // id would collide across them.
                      <GroupedRow
                        key={`${section.key}:${task.id}`}
                        task={task}
                        agents={agents}
                        members={members}
                        index={index}
                        display={display}
                        rowStyle={rowStyle}
                        href={hrefFor(task.id)}
                        onSelect={onSelect}
                        onStatusChange={onStatusChange}
                      />
                    ))
                  )}
                </div>
              )}
            </section>
          );
        })
      )}
    </div>
  );
}

function sectionLabel(section: TaskGroup): string {
  return section.status ? taskStatusLabel(section.status) : section.label;
}

/** A heading is never just text: each grouping brings its own mark — the
 *  status ring, the priority bars, the assignee's sigil/avatar, the label's
 *  colour dot. All four already exist elsewhere in this directory; nothing
 *  new is drawn here. */
function SectionGlyph({
  section,
  agents,
  members,
}: {
  section: TaskGroup;
  agents: FleetAgent[];
  members?: WorkspaceMember[];
}) {
  if (section.status) return <TaskStatusIcon status={section.status} size={14} />;
  if (section.priority !== undefined) return <TaskPriorityIcon priority={section.priority} size={14} />;
  if (section.labelColor) {
    return <span className="fleet-label-dot" data-color={section.labelColor} aria-hidden />;
  }
  if (section.key.startsWith("agent:")) {
    const agent = agents.find((a) => a.agent_id === section.key.slice("agent:".length));
    return (
      <span className="fleet-agent-avatar">
        <AgentSigil seed={agent?.agent_id || section.key} size={12} />
      </span>
    );
  }
  if (section.key.startsWith("user:")) {
    const member = (members || []).find((m) => m.user_id === section.key.slice("user:".length));
    return (
      <MemberAvatar
        name={member?.display_name || member?.email || section.label}
        role={member?.role}
        size="xs"
      />
    );
  }
  // The catch-all buckets (Unassigned / No label). Their own name says what
  // they are; a placeholder glyph would only say it a second time.
  return null;
}

/**
 * One task, one line. EMPHASIS DISCIPLINE, the same rule the flat list and the
 * Projects table document: exactly one cell carries full emphasis — the title.
 * Everything else is muted or secondary, and the only colour on the row is the
 * status ring, the priority glyph's urgent tile, and the label dots — all three
 * of which are information rather than decoration (fleet-theme.css:12).
 */
function GroupedRow({
  task,
  agents,
  members,
  index,
  display,
  rowStyle,
  href,
  onSelect,
  onStatusChange,
}: {
  task: FleetTask;
  agents: FleetAgent[];
  members?: WorkspaceMember[];
  index: number;
  display: TaskDisplayState;
  /** The grid tracks for the currently-visible cells, computed once by the
   *  parent rather than per row — every row on the page has the same set. */
  rowStyle: CSSProperties;
  href: string;
  onSelect: (taskId: string) => void;
  onStatusChange?: (taskId: string, status: FleetTaskStatus) => void;
}) {
  const priority = taskPriority(task);
  const assignee = agents.find((a) => a.agent_id === task.assignee_agent_id) || null;
  const assignedMember = !assignee && task.assignee_user_id
    ? (members || []).find((m) => m.user_id === task.assignee_user_id) || null
    : null;
  const due = dueLabel(task.due_at);
  const updated = timeAgo(task.updated_at || task.created_at);

  return (
    // A real link (CLAUDE.md: "Primary navigation is real links, so
    // cmd-click and middle-click work") in place of the old
    // role="button" div, same pattern as TasksBoard's card and
    // DocumentsList's row. Plain clicks still run through onSelect for SPA
    // navigation; modified/non-primary clicks get real browser behaviour.
    <a
      href={href}
      className="fleet-glist-row"
      style={rowStyle}
      aria-label={`${task.title || "Untitled task"} — open details`}
      onClick={(e) => {
        if (e.metaKey || e.ctrlKey || e.shiftKey || e.altKey || e.button !== 0) return;
        e.preventDefault();
        onSelect(task.id);
      }}
      onKeyDown={(e) => {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          onSelect(task.id);
        }
      }}
    >
      {display.priority ? (
        <span className="fleet-glist-cell-prio" title={TASK_PRIORITY_LABELS[priority]}>
          <TaskPriorityIcon priority={priority} size={14} />
        </span>
      ) : null}

      {display.id ? <span className="fleet-glist-cell-id">{taskDisplayId(task)}</span> : null}

      {/* The status ring is the same transparent-native-<select>-over-a-glyph
          control the board card uses, reusing its CSS verbatim: HTML5 drag has
          no keyboard equivalent and neither does a list, so this is how a task
          moves forward from the view you work through a backlog in. NOT
          switchable from the display properties for exactly that reason — see
          task-view-options.ts. */}
      {onStatusChange ? (
        <span className="fleet-board-card-statuspick fleet-glist-cell-status">
          <TaskStatusIcon status={task.status} size={14} />
          <select
            className="fleet-board-card-status"
            value={task.status}
            aria-label={`Status of ${task.title || "Untitled task"}`}
            title={taskStatusLabel(task.status)}
            // The row is a real <a> now (see GroupedRow below): stopPropagation
            // alone no longer stops the click from following the link, since
            // link-following is resolved from the nearest <a> ancestor
            // independent of JS bubbling. preventDefault is what actually
            // cancels it.
            onClick={(e) => {
              e.preventDefault();
              e.stopPropagation();
            }}
            onKeyDown={(e) => e.stopPropagation()}
            onChange={(e) => {
              const next = e.currentTarget.value as FleetTaskStatus;
              if (next !== task.status) onStatusChange(task.id, next);
            }}
          >
            {FLEET_TASK_STATUSES.map((s) => (
              <option key={s} value={s}>
                {taskStatusLabel(s)}
              </option>
            ))}
          </select>
        </span>
      ) : (
        <span className="fleet-glist-cell-status" title={taskStatusLabel(task.status)}>
          <TaskStatusIcon status={task.status} size={14} />
        </span>
      )}

      {/* Title and labels share ONE grid track on purpose. As their own
          `auto` track the labels would be a different width on every row —
          each row is its own grid — so Due/Updated/Assignee would zig-zag
          down the page instead of lining up. In one track the title takes the
          slack and ellipses first (min-width:0), the chips never shrink, and
          the four right-hand columns stay dead straight.
          Cap 3 + "+N" here, against 2 on a board card: the row is several
          times wider. */}
      <span className="fleet-glist-cell-title">
        <span className="fleet-glist-cell-name">{task.title || "Untitled task"}</span>
        {/* MAN-294: the status cell above (ring or ring+<select>) is a fixed
            16px column with no room for a second glyph, so this rides in the
            one flexible track instead — still fixed-width/flex-shrink:0, so
            it doesn't reopen the "title and labels share one track" zig-zag
            problem the comment above describes. Renders nothing on every
            task that isn't deferred. */}
        <TaskWakeDeferralIcon task={task} size={12} />
        {display.labels ? <TaskLabelChips labels={task.labels} max={3} /> : null}
      </span>

      {display.due ? (
        <span className={`fleet-glist-cell-due${due ? "" : " fleet-cell-muted"}`} title={due ? "Due date" : "No due date"}>
          {due || "—"}
        </span>
      ) : null}
      {display.updated ? (
        <span className="fleet-glist-cell-updated" title="Last updated">
          {updated || "—"}
        </span>
      ) : null}

      {!display.assignee ? null : assignee ? (
        <span className="fleet-agent-avatar fleet-glist-cell-assignee" title={assignee.label || "Unnamed agent"}>
          <AgentSigil seed={assignee.agent_id} size={12} />
        </span>
      ) : assignedMember ? (
        <span className="fleet-glist-cell-assignee">
          <MemberAvatar
            name={assignedMember.display_name || assignedMember.email}
            role={assignedMember.role}
            size="xs"
            tintIndex={index}
          />
        </span>
      ) : (
        // An empty slot rather than a dash: a column of "—" where the avatars
        // aren't is noise, and "unassigned" reads fine from the absence.
        <span className="fleet-glist-cell-assignee fleet-glist-cell-unassigned" title="Unassigned" aria-hidden />
      )}

      {/* Copy link — same CopyLinkButton every row/detail surface in this
          codebase shares, in the trailing 20px track gridTracks() always
          reserves for it on desktop. Its own click handler stops this row's
          <a> from also navigating, same as the status <select> above. */}
      <span className="fleet-glist-cell-copy">
        <CopyLinkButton path={href} label={task.title || "this task"} iconSize={13} />
      </span>
    </a>
  );
}
