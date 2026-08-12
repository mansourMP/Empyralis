"use client";

import { AlertCircle } from "lucide-react";

/**
 * Shared loading / error states for fleet API surfaces. Skeletons stand in for
 * lists while they load (never a bare spinner); errors are always
 * human-readable (never raw JSON or a silent empty state).
 */

export function FleetListSkeleton({
  rows = 5,
  rowHeight,
}: {
  rows?: number;
  /** Match the real content's row height (e.g. AgentsList/TasksList's
   *  .fleet-agent-row / .fleet-task-row, both `min-height: 52px`) so this
   *  placeholder doesn't reflow the page when the fetch resolves and the
   *  real list swaps in. Every caller standing in for a *list* passes this:
   *  52 for agent/task/project rows, 44 for .fleet-inbox-row. Left unset, rows fall
   *  back to ~37px (12px bar + 12px top/bottom padding), which is right only
   *  for the plain `.fleet-list-row` shape — today that's Billing, whose
   *  ~37.8px .fleet-usage-legend-row already matches within a pixel.
   *  MAN-113: the un-set default caused a visible
   *  "renders one way, then snaps to another" jump on the project detail
   *  page — its Agents/Tasks views render into 52px grid rows, ~15px taller
   *  than this skeleton's un-pinned rows, so the whole list resized the
   *  moment the loading skeleton was replaced by real content. */
  rowHeight?: number;
}) {
  return (
    <div className="fleet-list" aria-busy="true" aria-label="Loading">
      {Array.from({ length: rows }).map((_, i) => (
        <div
          key={i}
          className="fleet-list-row"
          style={rowHeight ? { minHeight: rowHeight, boxSizing: "border-box" } : undefined}
        >
          <div className="fleet-skeleton-bar" style={{ width: `${38 + (i % 3) * 16}%`, height: 12 }} />
          <div className="fleet-skeleton-bar" style={{ width: 48, height: 12, marginLeft: "auto" }} />
        </div>
      ))}
    </div>
  );
}

/**
 * Row-shaped skeleton for icon+two-line content — activity feeds,
 * conversation history, schedules, tool/capability lists: anywhere the real
 * content is a vertical list of "small dot/icon, a title line, a quieter
 * meta line underneath". Extracted from OverviewTab's Recent Activity and
 * AgentChat's conversation-history loading state, which had this exact
 * markup duplicated inline — a third caller copying it again is the
 * bespoke-per-screen drift CLAUDE.md warns about, so this is the one
 * version every list-shaped tab now shares.
 *
 * Was previously reached for tabs whose loading state is NOT a short
 * single-line summary (Schedule/Tools/Capabilities/Connectors) via a single
 * 8px `.fleet-skeleton-bar` — visibly tiny next to the many-row `.fleet-
 * config` toggle list or card grid it gets replaced by the moment the fetch
 * resolves, the exact "small placeholder in what is actually a very big
 * area" complaint. Rows default to 4, wide enough to cover a typical tab
 * without reserving so much height an empty result snaps the page shorter.
 */
export function FleetRowsSkeleton({ rows = 4, label = "Loading" }: { rows?: number; label?: string }) {
  const widths = [68, 52, 60, 74, 46, 64];
  return (
    <div className="fleet-activity-skeleton" aria-busy="true" aria-label={label}>
      {Array.from({ length: rows }).map((_, i) => {
        const w = widths[i % widths.length];
        return (
          <div key={i} className="fleet-skeleton-row">
            <div className="fleet-skeleton-bar" style={{ width: 8 }} />
            <div style={{ flex: 1 }}>
              <div className="fleet-skeleton-bar" style={{ width: `${w}%`, marginBottom: 6 }} />
              <div className="fleet-skeleton-bar" style={{ width: `${Math.max(w - 24, 20)}%`, opacity: 0.6 }} />
            </div>
          </div>
        );
      })}
    </div>
  );
}

/**
 * Card-grid skeleton matching `.fleet-channel-grid`/`.fleet-connector-grid`
 * — the square-card-per-platform grid (icon + label + one pill, 4 across,
 * 2 at <=900px per CLAUDE.md's "square-card grid" ruling). Reuses the real
 * grid/card classes so the placeholder is pixel-identical to what replaces
 * it — no separate width/columns math to keep in sync by hand.
 */
export function FleetCardGridSkeleton({ cards = 8, label = "Loading" }: { cards?: number; label?: string }) {
  return (
    <div className="fleet-channel-grid" aria-busy="true" aria-label={label}>
      {Array.from({ length: cards }).map((_, i) => (
        <div key={i} className="fleet-channel-card" style={{ cursor: "default", pointerEvents: "none" }}>
          <div className="fleet-skeleton-bar" style={{ width: 22, height: 22, borderRadius: 6 }} />
          <div className="fleet-skeleton-bar" style={{ width: "70%", height: 11 }} />
          <div className="fleet-skeleton-bar" style={{ width: 44, height: 16, borderRadius: 999 }} />
        </div>
      ))}
    </div>
  );
}

/**
 * Kanban-shaped skeleton matching `.fleet-board`'s column layout — used
 * while the tasks fetch is in flight AND the saved view is "board", so the
 * loading placeholder doesn't reflow from a flat list into multi-column
 * cards the instant the fetch resolves (MAN-113's row-height fix solved
 * this for the list/grouped layouts; the board layout had no equivalent
 * and fell back to `FleetListSkeleton`'s flat rows). Column count/labels
 * are unknowable before the fetch returns — real columns exist only when
 * they hold a task — so this renders a generic, unlabeled set purely to
 * reserve the right SHAPE (columns of cards, not a list), never real
 * status names.
 */
export function FleetBoardSkeleton({ label = "Loading" }: { label?: string }) {
  const columns = [2, 3, 1, 2];
  return (
    <div className="fleet-board" aria-busy="true" aria-label={label}>
      {columns.map((cardCount, ci) => (
        <section key={ci} className="fleet-board-column">
          <header className="fleet-board-column-header">
            <div className="fleet-skeleton-bar" style={{ width: 14, height: 14, borderRadius: 4 }} />
            <div className="fleet-skeleton-bar" style={{ width: 64, height: 11 }} />
          </header>
          <div className="fleet-board-column-body">
            {Array.from({ length: cardCount }).map((_, i) => (
              <div key={i} className="fleet-board-card" style={{ cursor: "default" }}>
                <div className="fleet-skeleton-bar" style={{ width: `${70 - i * 8}%`, height: 12 }} />
                <div className="fleet-skeleton-bar" style={{ width: "40%", height: 10, opacity: 0.6 }} />
              </div>
            ))}
          </div>
        </section>
      ))}
    </div>
  );
}

/**
 * Chat-transcript skeleton matching `.fleet-work-msg` bubbles (WorkTab's and
 * ConversationsView's shared transcript-pane markup) — alternating
 * user/agent alignment so the placeholder reads as a conversation, not a
 * stack of identical boxes. Replaces a bare "Loading…" line that sat alone
 * in what is otherwise a large, scrollable message pane.
 */
export function FleetChatSkeleton({ bubbles = 4, label = "Loading" }: { bubbles?: number; label?: string }) {
  const shapes = [
    { side: "user", width: 58 },
    { side: "agent", width: 74 },
    { side: "user", width: 40 },
    { side: "agent", width: 62 },
  ];
  return (
    <div className="fleet-work-transcript" aria-busy="true" aria-label={label}>
      {Array.from({ length: bubbles }).map((_, i) => {
        const shape = shapes[i % shapes.length];
        return (
          <div key={i} className={`fleet-work-msg fleet-work-msg--${shape.side}`} style={{ width: `${shape.width}%` }}>
            <div className="fleet-skeleton-bar" style={{ width: "90%", height: 10, marginBottom: 6 }} />
            <div className="fleet-skeleton-bar" style={{ width: "55%", height: 10, opacity: 0.6 }} />
          </div>
        );
      })}
    </div>
  );
}

export function FleetSurfaceError({
  title = "Couldn’t load this",
  // No hardcoded "it'll keep trying" suffix — that's only true for a
  // surface that actually auto-retries in the background, which isn't every
  // caller. Say exactly what happened and let `onRetry` be the recovery
  // path; a caller can still opt back into the old, softer copy by passing
  // its own message.
  message = "Something went wrong on our side. Try again.",
  onRetry,
}: {
  title?: string;
  message?: string | null;
  onRetry?: () => void;
}) {
  return (
    <div className="fleet-page-state" role="alert">
      <AlertCircle size={22} strokeWidth={1.75} />
      <div className="fleet-page-state-title">{title}</div>
      <div className="fleet-page-state-body">{message}</div>
      {onRetry && (
        <div className="fleet-empty-actions">
          <button type="button" className="fleet-btn" onClick={onRetry}>Try again</button>
        </div>
      )}
    </div>
  );
}
