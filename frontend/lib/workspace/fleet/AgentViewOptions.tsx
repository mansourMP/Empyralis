"use client";

/**
 * The workspace Agents page's view-options popover — a PARALLEL build to
 * TaskViewOptions.tsx, not a shared/generalized version of it (see
 * agent-view-options.ts's file header for why). Mirrors that file's box,
 * dismissal contract and interaction idiom exactly — same
 * `.fleet-toolbar-popover` box (position, elevation, dismissal: outside
 * pointerdown, or Esc), same neutral-throughout treatment — but every class
 * name is its own (`.fleet-agent-view-options*`), and it imports nothing
 * from task-view-options.ts or TaskViewOptions.tsx.
 *
 * TWO DELIBERATE DIFFERENCES FROM TaskViewOptions.tsx, both because the
 * Agents page already had real, working filter/sort controls before this
 * feature existed (FleetToolbar's "Filter and sort" popover — project/
 * status/channel filters, a 5-option sort dropdown) that tasks never had:
 *
 *   1. ORDERING is hidden while the flat table (List layout, no grouping) is
 *      on screen. It has nothing to act on there — the flat table keeps
 *      using its own pre-existing "Sort by" dropdown unchanged, exactly as
 *      it did before this feature shipped. Showing this popover's Ordering
 *      row at the same time would be a second, confusing "sort" control
 *      with a different, smaller vocabulary (last active/cost/name against
 *      the legacy dropdown's five). It appears only once it has real effect
 *      — Board, or List with a grouping selected — same "no dead controls"
 *      reasoning TaskViewOptions.tsx already applies to Grouping-in-Board.
 *   2. Display properties is skipped entirely (not rendered with an empty
 *      chip row) on the flat table, for the same reason: those six columns
 *      are not toggleable there, they're the ones AgentsList.tsx has always
 *      drawn unconditionally.
 *
 * This popover's own trigger sits BESIDE FleetToolbar's, both pinned to the
 * row's right edge — see the small `.fleet-agent-view-cluster` wrapper added
 * in agents/page.tsx and its own note in fleet-theme.css for why a wrapper
 * was needed rather than reusing `.fleet-view-options`' auto-margin rule
 * (that rule assumes exactly one right-aligned cluster per row, which was
 * true on every page it already served — TaskViewOptions and FleetToolbar
 * are mutually exclusive tabs on the project page — and is no longer true
 * here, where both are visible at once).
 */

import { useEffect, useId, useRef, useState } from "react";
import { ArrowDown, ArrowUp, LayoutGrid, Rows3, Settings2 } from "lucide-react";

import {
  AGENT_GROUPING_OPTIONS,
  AGENT_ORDERING_DEFAULT_DIRECTION,
  AGENT_ORDERING_OPTIONS,
  agentSurfaceFor,
  displayPropertiesFor,
  isDefaultAgentViewOptions,
  orderDirectionLabel,
  resetAgentViewOptions,
  type AgentGrouping,
  type AgentOrdering,
  type AgentViewOptions as AgentViewOptionsState,
} from "./agent-view-options";

export function AgentViewOptions({
  options,
  onChange,
}: {
  options: AgentViewOptionsState;
  /** An UPDATER, not a value — see TaskViewOptions.tsx's identical prop for
   *  why (two changes landing in one React batch must not both start from
   *  the same stale snapshot). */
  onChange: (update: (prev: AgentViewOptionsState) => AgentViewOptionsState) => void;
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

  const surface = agentSurfaceFor(options);
  const properties = displayPropertiesFor(surface);
  const dirty = !isDefaultAgentViewOptions(options);
  // Grouping is a LIST concept — the Board's four columns already ARE the
  // "status" grouping, always on, so offering a second way to pick it there
  // would be a control that does nothing (see task-view-options.ts's own
  // identical rule for the Board/Grouping split).
  const showGrouping = options.layout === "list";
  // Ordering has nothing to act on while the flat, ungrouped table is on
  // screen — see the file header.
  const showOrdering = surface !== "list";

  return (
    <div className="fleet-agent-view-options" ref={ref}>
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
        <div className="fleet-toolbar-popover fleet-agent-view-options-popover">
          {/* Layout leads the popover, exactly like TaskViewOptions — every
              control that reshapes the view lives behind this one icon, so
              the toolbar row keeps one stable shape whether Board or List is
              active. */}
          <div className="fleet-agent-view-options-layout" role="tablist" aria-label="Agent layout">
            {(["board", "list"] as const).map((v) => (
              <button
                key={v}
                type="button"
                role="tab"
                aria-selected={options.layout === v}
                className={`fleet-agent-view-options-layout-btn${options.layout === v ? " is-active" : ""}`}
                onClick={() => onChange((prev) => ({ ...prev, layout: v }))}
              >
                {v === "board" ? <LayoutGrid size={14} strokeWidth={1.75} /> : <Rows3 size={14} strokeWidth={1.75} />}
                {v === "board" ? "Board" : "List"}
              </button>
            ))}
          </div>

          {showGrouping && (
            <div className="fleet-agent-view-options-row">
              <label className="fleet-agent-view-options-label" htmlFor={groupingId}>
                Grouping
              </label>
              <select
                id={groupingId}
                className="fleet-agent-view-options-select"
                value={options.grouping}
                onChange={(e) => {
                  const grouping = e.target.value as AgentGrouping;
                  onChange((prev) => ({ ...prev, grouping }));
                }}
              >
                {AGENT_GROUPING_OPTIONS.map((o) => (
                  <option key={o.value} value={o.value}>
                    {o.label}
                  </option>
                ))}
              </select>
            </div>
          )}

          {showOrdering && (
            <div className="fleet-agent-view-options-row">
              <label className="fleet-agent-view-options-label" htmlFor={orderingId}>
                Ordering
              </label>
              <span className="fleet-agent-view-options-control">
                <select
                  id={orderingId}
                  className="fleet-agent-view-options-select"
                  value={options.ordering}
                  onChange={(e) => {
                    const ordering = e.target.value as AgentOrdering;
                    // Picking a key also picks the direction that reads right
                    // for it — same rule TaskViewOptions.tsx applies.
                    onChange((prev) => ({
                      ...prev,
                      ordering,
                      direction: AGENT_ORDERING_DEFAULT_DIRECTION[ordering],
                    }));
                  }}
                >
                  {AGENT_ORDERING_OPTIONS.map((o) => (
                    <option key={o.value} value={o.value}>
                      {o.label}
                    </option>
                  ))}
                </select>
                <button
                  type="button"
                  className="fleet-agent-view-options-dir"
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
          )}

          {/* Only rendered when THIS rendering actually has toggleable
              properties — an empty "Display properties" heading over zero
              chips (which is what the flat table's surface would produce) is
              the same dead control a button that does nothing is. */}
          {properties.length > 0 && (
            <section className="fleet-agent-view-options-section">
              <h2 className="fleet-agent-view-options-heading">Display properties</h2>
              <div className="fleet-agent-view-options-chips">
                {properties.map((p) => {
                  const on = options.display[p.key] !== false;
                  return (
                    <button
                      key={p.key}
                      type="button"
                      className={`fleet-agent-view-options-chip${on ? " is-on" : ""}`}
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
          )}

          {/* Rendered only when there is something to reset — same rule
              TaskViewOptions.tsx applies. */}
          {dirty && (
            <div className="fleet-agent-view-options-footer">
              <button
                type="button"
                className="fleet-agent-view-options-reset"
                onClick={() => onChange(resetAgentViewOptions)}
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
