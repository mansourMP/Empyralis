"use client";

import { useEffect, useState } from "react";
import type { ReactNode } from "react";

/**
 * The non-`ready` render states every operator-console page shares
 * (planOperatorView in view-state.ts decides WHICH one applies; these are
 * only how each one looks). Same discipline as frontend's operator
 * activation page: `forbidden` gets its own honest copy, never a generic
 * "something went wrong" that would misdirect a signed-in non-operator
 * into thinking the console itself is broken.
 *
 * `SignedOutState` and `ForbiddenState` are deliberately two components,
 * because 401 and 403 are two facts. Only the signed-out one offers a way
 * out, and only it may: signing in fixes 401 and does nothing for 403.
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

/** The customer app that issues the session cookie. Derived from the current
 *  host rather than configured, so it is right in production
 *  (ops.empyralis.ai -> empyralis.ai) and in any deployment following the
 *  same `ops.` prefix, with no env var to forget to set.
 *
 *  Read in an effect, never during render: this component is server-rendered
 *  too, and a host-derived href computed inline would differ between the
 *  server pass and the client pass, which is a hydration mismatch. Until it
 *  resolves there is simply no link to click — an absent control, not a
 *  dead one. */
function useMainAppSignInUrl(): string | null {
  const [url, setUrl] = useState<string | null>(null);
  useEffect(() => {
    const host = window.location.host;
    const parent = host.startsWith("ops.") ? host.slice(4) : host;
    setUrl(`${window.location.protocol}//${parent}/login`);
  }, []);
  return url;
}

export function SignedOutState() {
  const signInUrl = useMainAppSignInUrl();
  return (
    <div className="ops-page-state" role="alert">
      <div className="ops-page-state-icon" aria-hidden="true">
        🔑
      </div>
      <div className="ops-page-state-title">You are not signed in</div>
      <div className="ops-page-state-body">
        This console has no sign-in of its own — it reads the session from the customer app on the shared parent
        domain. This opens the customer app in a new tab; sign in there, then switch back here and this page
        loads itself.
      </div>
      {signInUrl && (
        <a
          className="ops-btn ops-btn--primary"
          href={signInUrl}
          target="_blank"
          rel="noopener noreferrer"
          style={{ marginTop: "var(--space-2)" }}
        >
          Sign in
        </a>
      )}
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
        You are signed in, but this account is not operator-entitled. Ask for access.
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
