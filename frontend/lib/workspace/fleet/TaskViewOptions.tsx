"use client";

/**
 * The tasks view-options popover — Linear's, built to our data.
 *
 * Sits beside the Board/List switch because that is what it configures. The
 * BOX is FleetToolbar's (.fleet-toolbar-popover: position, elevation,
 * dismissal), the same way UsagePopover borrows it — one popover idiom in
 * this app, not three. Dismissal is that file's contract verbatim: outside
 * pointerdown, or Esc.
 *
 * WHAT IT CONTAINS, top to bottom: Grouping · Ordering (+ direction) ·
 * Display properties · Reset. It contains only that. Linear's own footer also
 * offers "Set default for everyone"; we do not have a team-defaults model to
 * write it to, and a button that writes nowhere is the dead control CLAUDE.md
 * names. See task-view-options.ts for the full list of what was left out and
 * why.
 *
 * NEUTRAL THROUGHOUT. This opens while the view's one accent-filled action
 * ("New task") is on screen, so a lit-up chip row would be the second accent
 * surface in one view. An enabled chip is a neutral fill plus a weight bump —
 * the same treatment .fleet-toolbar-popover-option.is-selected already uses.
 */

import { useEffect, useId, useRef, useState } from "react";
import { ArrowDown, ArrowUp, Settings2 } from "lucide-react";

import {
  TASK_GROUPING_OPTIONS,
  TASK_ORDERING_DEFAULT_DIRECTION,
  TASK_ORDERING_OPTIONS,
  displayPropertiesFor,
  isDefaultTaskViewOptions,
  orderDirectionLabel,
  resetTaskViewOptions,
  taskSurfaceFor,
  type TaskGrouping,
  type TaskOrdering,
  type TaskViewOptions as TaskViewOptionsState,
} from "./task-view-options";

export function TaskViewOptions({
  options,
  onChange,
}: {
  options: TaskViewOptionsState;
  /** An UPDATER, not a value. Every control here derives its next state from
   *  the current one (a chip flips one key of `display`, the ordering select
   *  also sets a direction), and a value-based handler makes each of those
   *  derivations read the props captured at the last render. Two changes
   *  landing in one React batch would then both start from the same snapshot
   *  and the second would silently discard the first. Passing the derivation
   *  itself makes that impossible. */
  onChange: (update: (prev: TaskViewOptionsState) => TaskViewOptionsState) => void;
}) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement | null>(null);
  const groupingId = useId();
  const orderingId = useId();

  // FleetToolbar's dismissal contract, verbatim: outside pointerdown, or Esc.
  useEffect(() => {
    if (!open) return;
    const onPointerDown = (e: PointerEvent) => {
      if (ref.current?.contains(e.target as Node)) return;
      setOpen(false);
    };
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false);
    };
    window.addEventListener("pointerdown", onPointerDown);
    window.addEventListener("keydown", onKeyDown);
    return () => {
      window.removeEventListener("pointerdown", onPointerDown);
      window.removeEventListener("keydown", onKeyDown);
    };
  }, [open]);

  const surface = taskSurfaceFor(options);
  const properties = displayPropertiesFor(surface);
  const dirty = !isDefaultTaskViewOptions(options);
  // Grouping is a LIST concept: a board column is a status, which is what
  // makes a drop mean "move this task". Not rendered in board view rather
  // than rendered and inert — see task-view-options.ts's header.
  const showGrouping = options.layout === "list";

  return (
    <div className="fleet-view-options" ref={ref}>
      <button
        type="button"
        className={`fleet-icon-btn${dirty || open ? " is-active" : ""}`}
        aria-label="View options"
        aria-expanded={open}
        title="View options"
        onClick={() => setOpen((v) => !v)}
      >
        <Settings2 size={16} strokeWidth={1.75} />
      </button>

      {open && (
        <div className="fleet-toolbar-popover fleet-view-options-popover">
          {showGrouping && (
            <div className="fleet-view-options-row">
              <label className="fleet-view-options-label" htmlFor={groupingId}>
                Grouping
              </label>
              <select
                id={groupingId}
                className="fleet-view-options-select"
                value={options.grouping}
                onChange={(e) => {
                  const grouping = e.target.value as TaskGrouping;
                  onChange((prev) => ({ ...prev, grouping }));
                }}
              >
                {TASK_GROUPING_OPTIONS.map((o) => (
                  <option key={o.value} value={o.value}>
                    {o.label}
                  </option>
                ))}
              </select>
            </div>
          )}

          <div className="fleet-view-options-row">
            <label className="fleet-view-options-label" htmlFor={orderingId}>
              Ordering
            </label>
            <span className="fleet-view-options-control">
              <select
                id={orderingId}
                className="fleet-view-options-select"
                value={options.ordering}
                onChange={(e) => {
                  const ordering = e.target.value as TaskOrdering;
                  // Picking a key also picks the direction that reads right
                  // for it — "sorted by created" means newest first, "sorted
                  // by priority" means urgent first. The toggle beside this
                  // still flips whatever it lands on.
                  onChange((prev) => ({
                    ...prev,
                    ordering,
                    direction: TASK_ORDERING_DEFAULT_DIRECTION[ordering],
                  }));
                }}
              >
                {TASK_ORDERING_OPTIONS.map((o) => (
                  <option key={o.value} value={o.value}>
                    {o.label}
                  </option>
                ))}
              </select>
              <button
                type="button"
                className="fleet-view-options-dir"
                title={orderDirectionLabel(options.ordering, options.direction)}
                aria-label={`Order: ${orderDirectionLabel(options.ordering, options.direction)}`}
                onClick={() =>
                  onChange((prev) => ({ ...prev, direction: prev.direction === "asc" ? "desc" : "asc" }))
                }
              >
                {options.direction === "asc" ? (
                  <ArrowUp size={13} strokeWidth={2} />
                ) : (
                  <ArrowDown size={13} strokeWidth={2} />
                )}
              </button>
            </span>
          </div>

          {/* Only the properties THIS rendering actually draws. Switching to
              the board drops Description and Status from the wrap because a
              card has neither — a toggle that changes nothing on screen is
              the same bug as a button that does nothing. */}
          <section className="fleet-view-options-section">
            <h2 className="fleet-view-options-heading">Display properties</h2>
            <div className="fleet-view-options-chips">
              {properties.map((p) => {
                const on = options.display[p.key] !== false;
                return (
                  <button
                    key={p.key}
                    type="button"
                    className={`fleet-view-options-chip${on ? " is-on" : ""}`}
                    aria-pressed={on}
                    onClick={() =>
                      onChange((prev) => ({
                        ...prev,
                        display: { ...prev.display, [p.key]: prev.display[p.key] === false },
                      }))
                    }
                  >
                    {p.label}
                  </button>
                );
              })}
            </div>
          </section>

          {/* Rendered only when there is something to reset. A permanently
              visible Reset on an untouched view is a control whose own label
              admits it does nothing. */}
          {dirty && (
            <div className="fleet-view-options-footer">
              <button
                type="button"
                className="fleet-view-options-reset"
                onClick={() => onChange(resetTaskViewOptions)}
              >
                Reset
              </button>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
