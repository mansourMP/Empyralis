"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { getErrorMessage } from "./view-state";

export type OperatorResource<T> = {
  data: T | null;
  status: number | null;
  loading: boolean;
  error: string | null;
  refresh: () => void;
};

/**
 * Fetches this app's OWN `/api/operator/...` route (never the backend
 * directly -- see lib/server/operator-proxy.ts for why: it is the layer
 * that carries the browser's session cookie to the real backend and
 * transparently refreshes it on an expired access token). Same
 * loading/status/error/data outcome shape as frontend's
 * `usePlatformActivationSnapshot` (platform-activation page.tsx) -- the raw
 * outcome; `planOperatorView` (view-state.ts) is what turns it into a
 * render state, kept as a separate step so this hook stays reusable across
 * every one of the app's pages instead of each one re-deriving it.
 */
export function useOperatorResource<T>(path: string): OperatorResource<T> {
  const [data, setData] = useState<T | null>(null);
  const [status, setStatus] = useState<number | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(() => {
    let cancelled = false;
    setLoading(true);
    (async () => {
      try {
        const res = await fetch(path, { cache: "no-store" });
        if (cancelled) return;
        setStatus(res.status);
        let body: unknown = null;
        try {
          body = await res.json();
        } catch {
          body = null;
        }
        if (!res.ok) {
          // "Empty" and "could not load" are different facts (CLAUDE.md) --
          // a non-ok response never becomes data, even a zeroed one.
          setData(null);
          setError(getErrorMessage(body, `HTTP ${res.status}`));
          return;
        }
        setData(body as T);
        setError(null);
      } catch (e) {
        if (cancelled) return;
        // The request never produced a response at all -- status stays
        // null, which planOperatorView reads as "error", never "forbidden"
        // (that needs a real 401/403) and never "ready".
        setStatus(null);
        setData(null);
        setError(e instanceof Error ? e.message : "Could not reach the server.");
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [path]);

  useEffect(() => refresh(), [refresh]);

  // Signing in happens on the customer app, in a DIFFERENT TAB — this console
  // has no sign-in of its own and the login page cannot redirect back across
  // subdomains (safeNextPath in the customer app only accepts same-origin
  // paths, correctly: it is an open-redirect guard). So the operator signs in
  // over there and switches back to this tab, where nothing has changed and
  // the page still says signed-out.
  //
  // Re-fetch when this tab becomes visible again, but ONLY while the last
  // answer was a 401. Signed in and working, this never fires — no polling,
  // no request storm on tab switching.
  const statusRef = useRef<number | null>(null);
  statusRef.current = status;
  useEffect(() => {
    const onVisible = () => {
      if (document.visibilityState === "visible" && statusRef.current === 401) {
        refresh();
      }
    };
    document.addEventListener("visibilitychange", onVisible);
    window.addEventListener("focus", onVisible);
    return () => {
      document.removeEventListener("visibilitychange", onVisible);
      window.removeEventListener("focus", onVisible);
    };
  }, [refresh]);

  return { data, status, loading, error, refresh: () => refresh() };
}
