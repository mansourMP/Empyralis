import type { ReactNode } from "react";

/**
 * The three non-`ready` render states every operator-console page shares
 * (planOperatorView in view-state.ts decides WHICH one applies; these are
 * only how each one looks). Same discipline as frontend's operator
 * activation page: `forbidden` gets its own honest copy, never a generic
 * "something went wrong" that would misdirect a signed-in non-operator
 * into thinking the console itself is broken.
 */

export function LoadingState({ rows = 2 }: { rows?: number }) {
  return (
    <div aria-busy="true" aria-label="Loading">
      {Array.from({ length: rows }).map((_, i) => (
        <div key={i} className="ops-stat-grid" style={{ marginBottom: "var(--space-3)" }}>
          {Array.from({ length: 4 }).map((__, j) => (
            <div key={j} className="ops-stat-card">
              <div className="ops-skeleton-bar" style={{ width: "45%", height: 16 }} />
              <div className="ops-skeleton-bar" style={{ width: "70%", height: 10, marginTop: 8, opacity: 0.7 }} />
            </div>
          ))}
        </div>
      ))}
    </div>
  );
}

export function ForbiddenState() {
  return (
    <div className="ops-page-state" role="alert">
      <div className="ops-page-state-icon" aria-hidden="true">
        🔒
      </div>
      <div className="ops-page-state-title">Operator access required</div>
      <div className="ops-page-state-body">
        This console shows every workspace on the platform, not just one — only a platform operator account can see it.
        Sign in with an operator-entitled account, or ask for access.
      </div>
    </div>
  );
}

export function ErrorState({ title = "Couldn't load this page", message, onRetry }: { title?: string; message: string; onRetry: () => void }) {
  return (
    <div className="ops-page-state" role="alert">
      <div className="ops-page-state-icon" aria-hidden="true">
        ⚠
      </div>
      <div className="ops-page-state-title">{title}</div>
      <div className="ops-page-state-body">{message}</div>
      <button type="button" className="ops-btn ops-btn--primary" style={{ marginTop: "var(--space-3)" }} onClick={onRetry}>
        Retry
      </button>
    </div>
  );
}

export function EmptyState({ children }: { children: ReactNode }) {
  return <div className="ops-table-empty">{children}</div>;
}
