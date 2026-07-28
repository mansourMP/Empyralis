"use client";

/**
 * The task composer — PAPER, not a form (MAN-127 FIX 2).
 *
 * What it replaces: a centred "New task" modal with three labelled, bordered
 * fields (Title / Description (optional) / Due date (optional)) and Cancel +
 * Create. Every field announced itself twice — once in a label, once in the
 * placeholder inside the box that label sat on top of — and the two properties
 * that decide whether an agent can pick the work up (status, assignee) were
 * not offered at all.
 *
 * The five rules this is built on:
 *
 *  1. NO BORDERS ON TEXT ENTRY. Title and description are bare textareas on
 *     the panel's own surface. The caret is the focus indicator; there is no
 *     box to focus into. (The panel keeps a real focus ring for keyboard users
 *     via :focus-visible on the controls that ARE controls — chips, buttons.)
 *  2. NO FIELD LABELS. The placeholder is the label. "Task title" / "Add
 *     description…" say everything a label above a box would have said.
 *  3. TYPOGRAPHY IS THE HIERARCHY. Title 17px, description 13px body. Their
 *     size difference is what tells you which is which.
 *  4. PROPERTIES ARE INLINE CHIPS, SET BEFORE CREATION. Status, priority,
 *     assignee, labels, due — one row under the text, each a chip that opens a
 *     small menu. This matters more for us than for Linear: a task created
 *     with the right status AND assignee is picked up by an agent immediately
 *     (assigning fires a wake), whereas a badly-filed Linear issue just waits
 *     for a human. The project chip is static — the composer is always opened
 *     from inside one project and cannot move work between them.
 *  5. KEYBOARD-FIRST. Open → type → Enter. Enter in the title submits;
 *     ⌘/Ctrl+Enter submits from anywhere; Esc closes (or closes just the open
 *     chip menu, if one is open). "Create more" keeps the panel open with the
 *     chips still set, so filing five tasks is five titles and five Enters.
 *
 * WHAT THE API COSTS. The create route takes title, description, due_at and
 * priority in one call. Status is NOT creatable (a row is born 'todo'), and
 * assignment and labels are their own endpoints on purpose. So a fully
 * specified task is up to four calls, issued in that order, and each of the
 * three follow-ups reports its own failure without pretending the task didn't
 * get created — a half-filed task the reader is told about beats a silent lie.
 *
 * MOTION (MAN-126): the panel enters at --dur-2/--ease-out and exits at
 * --dur-2/--ease-in. Nothing else here animates — chips and menu items change
 * state instantly.
 */

import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { CalendarDays, Check, Plus, X } from "lucide-react";

import { AgentSigil } from "./fleet-indicators";
import {
  FLEET_TASK_STATUSES,
  LABEL_COLORS,
  assignFleetTask,
  attachFleetTaskLabel,
  createFleetLabel,
  createFleetTask,
  patchFleetTask,
  useFleetLabels,
  type FleetAgent,
  type FleetLabel,
  type FleetTaskStatus,
} from "./fleet-data";
import {
  TASK_PRIORITIES,
  TASK_PRIORITY_LABELS,
  TaskPriorityIcon,
  TaskStatusIcon,
  taskStatusLabel,
  type TaskPriority,
} from "./task-status";

/** The status a task is actually born with (project_tasks.status DEFAULT).
 *  Anything else costs a follow-up PATCH, so this is also the default the
 *  composer opens on when nobody named a column. */
const BORN_STATUS: FleetTaskStatus = "todo";

/** How long the exit animation runs, read from --dur-2 rather than hard-coded,
 *  so the unmount can never drift from the CSS the motion system owns. */
function exitDurationMs(): number {
  if (typeof window === "undefined") return 140;
  const raw = getComputedStyle(document.documentElement).getPropertyValue("--dur-2").trim();
  const ms = raw.endsWith("ms") ? parseFloat(raw) : raw.endsWith("s") ? parseFloat(raw) * 1000 : NaN;
  return Number.isFinite(ms) && ms > 0 ? ms : 140;
}

/**
 * The words you had typed when the panel last closed, per project.
 *
 * Closing throws the panel away (it unmounts), so without this a stray click
 * on the backdrop — or an Esc pressed out of habit — silently destroys a
 * half-written task. Module-level rather than state for exactly that reason,
 * and NOT localStorage: a draft should survive a mis-click, not a browser
 * restart three days later. Cleared the moment the task is actually created.
 */
const draftsByProject = new Map<string, { title: string; description: string }>();

/** "2026-08-01" → "Aug 1". Parsed as LOCAL parts, never `new Date(string)`,
 *  which reads a bare date as UTC midnight and shows the day before in every
 *  timezone west of Greenwich. */
function shortDate(value: string): string {
  const [y, m, d] = value.split("-").map(Number);
  if (!y || !m || !d) return value;
  return new Date(y, m - 1, d).toLocaleDateString(undefined, { month: "short", day: "numeric" });
}

export function TaskComposer({
  workspaceId,
  projectId,
  projectName,
  agents,
  initialStatus,
  onClose,
  onCreated,
}: {
  workspaceId: string;
  projectId: string;
  projectName?: string;
  /** Agents in this project — the only valid assignees. */
  agents: FleetAgent[];
  /** Pre-set status: what the board column's `+` passes (MAN-127 FIX 3), so a
   *  task created in Todo starts as Todo. */
  initialStatus?: FleetTaskStatus;
  onClose: () => void;
  /** Fired after a successful create. `notice` is non-null when the task was
   *  created but a follow-up step (status / label / assign) did not land. */
  onCreated: (notice: string | null) => void;
}) {
  const [title, setTitle] = useState(() => draftsByProject.get(projectId)?.title || "");
  const [description, setDescription] = useState(() => draftsByProject.get(projectId)?.description || "");
  const [status, setStatus] = useState<FleetTaskStatus>(initialStatus || BORN_STATUS);
  const [priority, setPriority] = useState<TaskPriority>(0);
  const [assigneeId, setAssigneeId] = useState<string | null>(null);
  const [labelIds, setLabelIds] = useState<string[]>([]);
  const [dueDate, setDueDate] = useState("");
  const [createMore, setCreateMore] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [createdCount, setCreatedCount] = useState(0);
  const [closing, setClosing] = useState(false);

  const titleRef = useRef<HTMLTextAreaElement | null>(null);
  const closeTimer = useRef<number | null>(null);
  const { labels, refresh: refreshLabels } = useFleetLabels(workspaceId);

  const assignee = agents.find((a) => a.agent_id === assigneeId) || null;
  const chosenLabels = useMemo(
    () => labelIds.map((id) => labels.find((l) => l.id === id)).filter(Boolean) as FleetLabel[],
    [labelIds, labels],
  );

  const requestClose = useCallback(() => {
    if (closing) return;
    // Keep whatever was typed, so reopening picks up where this left off.
    if (title.trim() || description.trim()) {
      draftsByProject.set(projectId, { title, description });
    } else {
      draftsByProject.delete(projectId);
    }
    setClosing(true);
    closeTimer.current = window.setTimeout(onClose, exitDurationMs());
  }, [closing, description, onClose, projectId, title]);

  useEffect(() => () => {
    if (closeTimer.current) window.clearTimeout(closeTimer.current);
  }, []);

  // Autofocus the title, caret at the END — a restored draft should be ready
  // to keep typing into, not ready to type in front of.
  useEffect(() => {
    const el = titleRef.current;
    if (!el) return;
    el.focus();
    el.setSelectionRange(el.value.length, el.value.length);
  }, []);
  const autosize = (el: HTMLTextAreaElement | null) => {
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${el.scrollHeight}px`;
  };

  const canCreate = title.trim().length > 0 && !busy;

  const create = useCallback(async () => {
    const clean = title.trim();
    if (!clean || busy) return;
    setBusy(true);
    setError(null);
    const problems: string[] = [];
    try {
      const task = await createFleetTask(workspaceId, {
        project_id: projectId,
        title: clean,
        description: description.trim(),
        due_at: dueDate ? new Date(`${dueDate}T12:00:00`).toISOString() : null,
        // 0 is the table default; sending it would be a no-op write.
        ...(priority > 0 ? { priority } : {}),
      });

      // Status is not settable at create time — see the header note.
      if (status !== BORN_STATUS) {
        try {
          await patchFleetTask(workspaceId, task.id, { status });
        } catch (e) {
          problems.push(
            `it stayed in ${taskStatusLabel(BORN_STATUS)} (${e instanceof Error ? e.message : "status not set"})`,
          );
        }
      }

      for (const id of labelIds) {
        try {
          await attachFleetTaskLabel(workspaceId, task.id, id);
        } catch (e) {
          const name = labels.find((l) => l.id === id)?.name || "label";
          problems.push(`the “${name}” label was not added (${e instanceof Error ? e.message : "failed"})`);
        }
      }

      // Last on purpose: assigning fires a wake, so the agent should not start
      // reading the task until its status and labels are already on it.
      if (assigneeId) {
        try {
          const { wakeError } = await assignFleetTask(workspaceId, task.id, assigneeId);
          if (wakeError) problems.push(`the agent could not be woken (${wakeError})`);
        } catch (e) {
          problems.push(`it could not be assigned (${e instanceof Error ? e.message : "failed"})`);
        }
      }

      // The words are now a real task; the draft they came from is spent.
      draftsByProject.delete(projectId);
      onCreated(problems.length ? `Task created, but ${problems.join("; ")}.` : null);

      if (createMore) {
        // Keep every chip exactly as it is: filing a run of tasks into the
        // same column, for the same agent, is the whole reason this toggle
        // exists. Only the words reset.
        setTitle("");
        setDescription("");
        setCreatedCount((n) => n + 1);
        window.requestAnimationFrame(() => {
          autosize(titleRef.current);
          titleRef.current?.focus();
        });
      } else {
        requestClose();
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not create this task.");
    } finally {
      setBusy(false);
    }
  }, [
    assigneeId,
    busy,
    createMore,
    description,
    dueDate,
    labelIds,
    labels,
    onCreated,
    priority,
    projectId,
    requestClose,
    status,
    title,
    workspaceId,
  ]);

  return (
    <div
      className={`fleet-composer-backdrop${closing ? " is-closing" : ""}`}
      onMouseDown={requestClose}
      onKeyDown={(e) => {
        if (e.key === "Escape") {
          e.stopPropagation();
          requestClose();
        } else if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) {
          e.preventDefault();
          void create();
        }
      }}
    >
      <div
        className={`fleet-composer${closing ? " is-closing" : ""}`}
        role="dialog"
        aria-modal="true"
        aria-label="New task"
        onMouseDown={(e) => e.stopPropagation()}
      >
        <div className="fleet-composer-head">
          <span className="fleet-composer-crumb">
            {projectName ? <span className="fleet-composer-crumb-project">{projectName}</span> : null}
            {projectName ? <span aria-hidden>›</span> : null}
            <span>New task</span>
          </span>
          {createdCount > 0 ? (
            <span className="fleet-composer-created" role="status">
              {createdCount} created
            </span>
          ) : null}
          <button type="button" className="fleet-composer-close" onClick={requestClose} aria-label="Close">
            <X size={14} strokeWidth={2} />
          </button>
        </div>

        {/* The paper. Two bare textareas, nothing around them. */}
        <div className="fleet-composer-paper">
          <textarea
            ref={(el) => {
              titleRef.current = el;
              autosize(el);
            }}
            className="fleet-composer-title"
            value={title}
            rows={1}
            placeholder="Task title"
            onChange={(e) => {
              setTitle(e.currentTarget.value);
              autosize(e.currentTarget);
            }}
            onKeyDown={(e) => {
              // Enter submits from the title — the fast path this whole panel
              // exists for. Shift+Enter still breaks the line for the rare
              // two-line title.
              if (e.key === "Enter" && !e.shiftKey && !e.metaKey && !e.ctrlKey) {
                e.preventDefault();
                void create();
              }
            }}
          />
          <textarea
            className="fleet-composer-desc"
            value={description}
            rows={3}
            placeholder="Add description…"
            onChange={(e) => setDescription(e.currentTarget.value)}
          />
        </div>

        <div className="fleet-composer-chips">
          <ChipMenu
            label={taskStatusLabel(status)}
            set
            icon={<TaskStatusIcon status={status} size={14} />}
            menuLabel="Status"
          >
            {(close) =>
              FLEET_TASK_STATUSES.map((s) => (
                <MenuItem
                  key={s}
                  icon={<TaskStatusIcon status={s} size={14} />}
                  label={taskStatusLabel(s)}
                  selected={s === status}
                  onSelect={() => {
                    setStatus(s);
                    close();
                  }}
                />
              ))
            }
          </ChipMenu>

          <ChipMenu
            label={priority === 0 ? "Priority" : TASK_PRIORITY_LABELS[priority]}
            set={priority !== 0}
            icon={<TaskPriorityIcon priority={priority} size={14} />}
            menuLabel="Priority"
          >
            {(close) =>
              TASK_PRIORITIES.map((p) => (
                <MenuItem
                  key={p}
                  icon={<TaskPriorityIcon priority={p} size={14} />}
                  label={TASK_PRIORITY_LABELS[p]}
                  selected={p === priority}
                  onSelect={() => {
                    setPriority(p);
                    close();
                  }}
                />
              ))
            }
          </ChipMenu>

          <ChipMenu
            label={assignee ? assignee.label || "Unnamed agent" : "Assignee"}
            set={Boolean(assignee)}
            icon={
              assignee ? (
                <span className="fleet-composer-sigil">
                  <AgentSigil seed={assignee.agent_id} size={12} />
                </span>
              ) : (
                <span className="fleet-composer-nobody" aria-hidden />
              )
            }
            menuLabel="Assignee"
          >
            {(close) => (
              <>
                <MenuItem
                  icon={<span className="fleet-composer-nobody" aria-hidden />}
                  label="No assignee"
                  selected={!assigneeId}
                  onSelect={() => {
                    setAssigneeId(null);
                    close();
                  }}
                />
                {agents.length === 0 ? (
                  <div className="fleet-composer-pop-empty">No agents in this project yet.</div>
                ) : (
                  agents.map((a) => (
                    <MenuItem
                      key={a.agent_id}
                      icon={
                        <span className="fleet-composer-sigil">
                          <AgentSigil seed={a.agent_id} size={12} />
                        </span>
                      }
                      label={a.label || "Unnamed agent"}
                      selected={a.agent_id === assigneeId}
                      onSelect={() => {
                        setAssigneeId(a.agent_id);
                        close();
                      }}
                    />
                  ))
                )}
              </>
            )}
          </ChipMenu>

          <LabelChip
            workspaceId={workspaceId}
            labels={labels}
            chosen={chosenLabels}
            selectedIds={labelIds}
            onToggle={(id) =>
              setLabelIds((cur) => (cur.includes(id) ? cur.filter((x) => x !== id) : [...cur, id]))
            }
            onCreated={async (created) => {
              await refreshLabels();
              setLabelIds((cur) => (cur.includes(created.id) ? cur : [...cur, created.id]));
            }}
          />

          <ChipMenu
            label={dueDate ? shortDate(dueDate) : "Due"}
            set={Boolean(dueDate)}
            icon={<CalendarDays size={13} strokeWidth={1.75} />}
            menuLabel="Due date"
          >
            {(close) => (
              <div className="fleet-composer-pop-pad">
                <input
                  type="date"
                  className="fleet-composer-date"
                  value={dueDate}
                  aria-label="Due date"
                  onChange={(e) => setDueDate(e.currentTarget.value)}
                />
                <button
                  type="button"
                  className="fleet-composer-pop-item"
                  onClick={() => {
                    setDueDate("");
                    close();
                  }}
                >
                  <span className="fleet-composer-pop-icon" />
                  <span className="fleet-composer-pop-label">No due date</span>
                </button>
              </div>
            )}
          </ChipMenu>

          {/* Static: this composer belongs to one project and cannot file
              elsewhere, so the chip states where the task lands rather than
              pretending to be a picker. */}
          <span className="fleet-composer-chip fleet-composer-chip--static" title="This task's project">
            {projectName || "This project"}
          </span>
        </div>

        {error ? (
          <p className="fleet-composer-error" role="alert">
            {error}
          </p>
        ) : null}

        <div className="fleet-composer-foot">
          <button
            type="button"
            role="switch"
            aria-checked={createMore}
            className={`fleet-composer-switch${createMore ? " is-on" : ""}`}
            onClick={() => setCreateMore((v) => !v)}
            title="Keep this panel open after creating, with the properties still set"
          >
            <span className="fleet-composer-switch-track" aria-hidden>
              <span className="fleet-composer-switch-knob" />
            </span>
            Create more
          </button>
          <button
            type="button"
            className="fleet-btn fleet-btn--accent-fill"
            onClick={() => void create()}
            disabled={!canCreate}
          >
            {busy ? "Creating…" : "Create task"}
          </button>
        </div>
      </div>
    </div>
  );
}

/* ── Chips ─────────────────────────────────────────────────────────────────
   One shape for every property: a small button that opens a menu directly
   under itself. Deliberately NOT a native <select> — the chip has to show a
   status ring / priority glyph / agent sigil, which a select's option list
   cannot render — so this carries the keyboard and dismissal behaviour a
   listbox would have given for free: Esc closes it (and only it), the arrow
   keys walk the items, and a click anywhere else dismisses. */

function ChipMenu({
  label,
  icon,
  set,
  menuLabel,
  children,
}: {
  label: string;
  icon: ReactNode;
  /** Whether the property actually carries a value — a set chip reads at full
   *  strength, an unset one stays muted so the row shows at a glance what has
   *  been decided. */
  set?: boolean;
  menuLabel: string;
  children: (close: () => void) => ReactNode;
}) {
  const [open, setOpen] = useState(false);
  const wrapRef = useRef<HTMLSpanElement | null>(null);
  const popRef = useRef<HTMLDivElement | null>(null);
  const close = useCallback(() => setOpen(false), []);

  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      if (!wrapRef.current?.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", onDown, true);
    return () => document.removeEventListener("mousedown", onDown, true);
  }, [open]);

  useEffect(() => {
    if (!open) return;
    popRef.current?.querySelector<HTMLElement>("input, button")?.focus();
  }, [open]);

  return (
    <span
      className="fleet-composer-chipwrap"
      ref={wrapRef}
      onKeyDown={(e) => {
        if (!open) return;
        if (e.key === "Escape") {
          // Only the menu closes — the composer behind it stays open, which is
          // what every menu in every app does.
          e.stopPropagation();
          setOpen(false);
          wrapRef.current?.querySelector<HTMLElement>(".fleet-composer-chip")?.focus();
          return;
        }
        if (e.key !== "ArrowDown" && e.key !== "ArrowUp") return;
        e.preventDefault();
        const items = Array.from(
          popRef.current?.querySelectorAll<HTMLElement>(".fleet-composer-pop-item") || [],
        );
        if (items.length === 0) return;
        const at = items.indexOf(document.activeElement as HTMLElement);
        const next = e.key === "ArrowDown" ? at + 1 : at - 1;
        items[(next + items.length) % items.length]?.focus();
      }}
    >
      <button
        type="button"
        className={`fleet-composer-chip${set ? " is-set" : ""}`}
        aria-haspopup="menu"
        aria-expanded={open}
        onClick={() => setOpen((v) => !v)}
      >
        <span className="fleet-composer-chip-icon">{icon}</span>
        {label}
      </button>
      {open ? (
        <div className="fleet-composer-pop" role="menu" aria-label={menuLabel} ref={popRef}>
          {children(close)}
        </div>
      ) : null}
    </span>
  );
}

function MenuItem({
  icon,
  label,
  selected,
  onSelect,
}: {
  icon: ReactNode;
  label: string;
  selected?: boolean;
  onSelect: () => void;
}) {
  return (
    <button
      type="button"
      className="fleet-composer-pop-item"
      role="menuitemradio"
      aria-checked={Boolean(selected)}
      onClick={onSelect}
    >
      <span className="fleet-composer-pop-icon">{icon}</span>
      <span className="fleet-composer-pop-label">{label}</span>
      {selected ? <Check size={13} strokeWidth={2} className="fleet-composer-pop-check" /> : null}
    </button>
  );
}

/**
 * The label chip. Multi-select, so it stays open as you tick, and it is the
 * one place a label can be MINTED: agents are not allowed to create labels
 * (routes_fleet.py attaches only known ones — an attach that invents a label
 * on a typo turns the vocabulary into a junk drawer), so the affordance has to
 * live on a human surface, and this is the human surface where labelling
 * actually happens.
 */
function LabelChip({
  workspaceId,
  labels,
  chosen,
  selectedIds,
  onToggle,
  onCreated,
}: {
  workspaceId: string;
  labels: FleetLabel[];
  chosen: FleetLabel[];
  selectedIds: string[];
  onToggle: (id: string) => void;
  onCreated: (label: FleetLabel) => void | Promise<void>;
}) {
  const [query, setQuery] = useState("");
  const [minting, setMinting] = useState(false);
  const [mintError, setMintError] = useState<string | null>(null);

  const q = query.trim().toLowerCase();
  const shown = q ? labels.filter((l) => l.name.toLowerCase().includes(q)) : labels;
  const exact = labels.some((l) => l.name.toLowerCase() === q);

  const summary =
    chosen.length === 0
      ? "Labels"
      : chosen.length === 1
        ? chosen[0].name
        : `${chosen[0].name} +${chosen.length - 1}`;

  async function mint() {
    const name = query.trim();
    if (!name || minting) return;
    setMinting(true);
    setMintError(null);
    try {
      // Colour is assigned round-robin from the palette rather than asked for:
      // a colour picker on the way to filing a task is a decision nobody wants
      // to make at that moment, and the label can be recoloured later.
      const color = LABEL_COLORS[labels.length % LABEL_COLORS.length];
      const created = await createFleetLabel(workspaceId, { name, color });
      await onCreated(created);
      setQuery("");
    } catch (e) {
      setMintError(e instanceof Error ? e.message : "Could not create that label.");
    } finally {
      setMinting(false);
    }
  }

  return (
    <ChipMenu
      label={summary}
      set={chosen.length > 0}
      menuLabel="Labels"
      icon={
        chosen.length > 0 ? (
          <span className="fleet-label-dot" data-color={chosen[0].color} aria-hidden />
        ) : (
          <span className="fleet-label-dot" data-color="none" aria-hidden />
        )
      }
    >
      {() => (
        <>
          <div className="fleet-composer-pop-search">
            <input
              className="fleet-composer-pop-input"
              value={query}
              placeholder="Filter or create…"
              aria-label="Filter or create a label"
              onChange={(e) => setQuery(e.currentTarget.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && !exact && query.trim()) {
                  e.preventDefault();
                  e.stopPropagation();
                  void mint();
                }
              }}
            />
          </div>
          <div className="fleet-composer-pop-list">
            {shown.map((l) => (
              <button
                key={l.id}
                type="button"
                className="fleet-composer-pop-item"
                role="menuitemcheckbox"
                aria-checked={selectedIds.includes(l.id)}
                onClick={() => onToggle(l.id)}
              >
                <span className="fleet-composer-pop-icon">
                  <span className="fleet-label-dot" data-color={l.color} aria-hidden />
                </span>
                <span className="fleet-composer-pop-label">{l.name}</span>
                {selectedIds.includes(l.id) ? (
                  <Check size={13} strokeWidth={2} className="fleet-composer-pop-check" />
                ) : null}
              </button>
            ))}
            {query.trim() && !exact ? (
              <button type="button" className="fleet-composer-pop-item" onClick={() => void mint()} disabled={minting}>
                <span className="fleet-composer-pop-icon">
                  <Plus size={13} strokeWidth={2} />
                </span>
                <span className="fleet-composer-pop-label">
                  {minting ? "Creating…" : `Create label “${query.trim()}”`}
                </span>
              </button>
            ) : null}
            {shown.length === 0 && !query.trim() ? (
              <div className="fleet-composer-pop-empty">
                No labels yet. Type a name to create the first one.
              </div>
            ) : null}
            {mintError ? <div className="fleet-composer-pop-empty">{mintError}</div> : null}
          </div>
        </>
      )}
    </ChipMenu>
  );
}
