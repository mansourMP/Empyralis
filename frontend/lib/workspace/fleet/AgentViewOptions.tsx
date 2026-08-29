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
 * TWO LAYOUTS, LIST LEADS. Cards — a third layout, and this page's former
 * default — is DELETED, 2026-08-30, on the founder's own reversal: *"i do not
 * want cards thing default should be list ... i only want to see list and
 * board!"* List and Board come from AGENT_LAYOUT_OPTIONS rather than being
 * typed here, so this popover and readAgentViewOptions' accepted-value list
 * cannot disagree.
 *
 * GROUPING is a List-only concept: the Board's four columns already ARE the
 * "status" grouping, always on, so a grouping picker there would be a control
 * that does nothing (the same rule task-view-options.ts states for
 * Grouping-in-Board). ORDERING and DISPLAY PROPERTIES are offered on both —
 * there is no reduced layout left to hide them for; that used to be Cards'
 * job (a card face is two facts and refuses a third, so it had nothing for
 * six display toggles to show or hide, and its own settled attention-rank
 * order — "never recency... a card's position is stable", CLAUDE.md — meant
 * a saved cost/name Ordering would have either silently overridden it or done
 * nothing). Every row below still appears exactly when the layout on screen
 * gives it something to act on, and is never rendered disabled.
 *
 * WHERE THE TRIGGER SITS: in the Agents page's own single toolbar row, pinned
 * right, with the agent search pinned left (.fleet-agent-surface-toolbar,
 * agent-cards.css). It no longer shares that row with FleetToolbar — the
 * filter/sort/properties cluster this page used to carry went with the flat
 * table in the 2026-08-22 card-grid redesign — so the `.fleet-agent-view-
 * cluster` wrapper that existed only to stop two auto-margined children
 * fighting over the row's free space is deleted with it, and this component's
 * own root carries the popover's positioning context instead.
 */

import { useEffect, useId, useRef, useState } from "react";
import { ArrowDown, ArrowUp, Columns3, Rows3, Settings2 } from "lucide-react";

import {
  AGENT_GROUPING_OPTIONS,
  AGENT_LAYOUT_OPTIONS,
  AGENT_ORDERING_DEFAULT_DIRECTION,
  AGENT_ORDERING_OPTIONS,
  agentSurfaceFor,
  displayPropertiesFor,
  isDefaultAgentViewOptions,
  orderDirectionLabel,
  resetAgentViewOptions,
  type AgentGrouping,
  type AgentLayout,
  type AgentOrdering,
  type AgentViewOptions as AgentViewOptionsState,
} from "./agent-view-options";

/** One mark per layout, keyed off the shared vocabulary so a layout added to
 *  AGENT_LAYOUT_OPTIONS without a mark here is a compile error rather than a
 *  blank button. Columns3 is the board, Rows3 the list. */
const LAYOUT_ICON: Record<AgentLayout, typeof Columns3> = {
  list: Rows3,
  board: Columns3,
};

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
  // Grouping is List-only — see the file header. Ordering has no exception
  // left to gate on now that Cards is gone, so it always renders below.
  const showGrouping = options.layout === "list";

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
              the toolbar row keeps one stable shape whichever layout is
              active. */}
          <div className="fleet-agent-view-options-layout" role="tablist" aria-label="Agent layout">
            {AGENT_LAYOUT_OPTIONS.map((option) => {
              const Icon = LAYOUT_ICON[option.value];
              return (
                <button
                  key={option.value}
                  type="button"
                  role="tab"
                  aria-selected={options.layout === option.value}
                  className={`fleet-agent-view-options-layout-btn${options.layout === option.value ? " is-active" : ""}`}
                  onClick={() => onChange((prev) => ({ ...prev, layout: option.value }))}
                >
                  <Icon size={14} strokeWidth={1.75} />
                  {option.label}
                </button>
              );
            })}
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
