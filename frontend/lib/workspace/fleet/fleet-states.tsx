"use client";

import { AlertCircle } from "lucide-react";

/**
 * Shared loading / error states for fleet API surfaces. Skeletons stand in for
 * lists while they load (never a bare spinner); errors are always
 * human-readable (never raw JSON or a silent empty state).
 */

export function FleetListSkeleton({ rows = 5 }: { rows?: number }) {
  return (
    <div className="fleet-list" aria-busy="true" aria-label="Loading">
      {Array.from({ length: rows }).map((_, i) => (
        <div key={i} className="fleet-list-row">
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
