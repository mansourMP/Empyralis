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
   *  52 for agent/task/project rows, 44 for .fleet-inbox-row, 64 for
   *  ProjectOverview's two-line .fleet-activity-item. Left unset, rows fall
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

export function FleetSurfaceError({
  title = "Couldn’t load this",
  message,
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
      <div className="fleet-page-state-body">
        {message || "Something went wrong on our side."} It’ll keep trying — refresh if it doesn’t clear.
      </div>
      {onRetry && (
        <div className="fleet-empty-actions">
          <button type="button" className="fleet-btn" onClick={onRetry}>Try again</button>
        </div>
      )}
    </div>
  );
}
