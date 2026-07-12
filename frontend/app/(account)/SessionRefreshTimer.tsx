'use client';

import { useEffect } from 'react';

import { refresh } from '@/lib/auth/auth-client';

// The access-token cookie is short-lived (ORION_JWT_EXP_SECONDS, 1h by
// default) and relies on this silent refresh to stay alive — there was no
// caller of auth-client's refresh() anywhere in the app before this, so every
// session died on the hour with no warning: any request after that point
// 401'd, and the only way back in was a full reload/re-login. Refreshing
// well inside that window (and repeatedly, not once) survives a throttled
// background tab missing a tick, since the backend mints a fresh 1h token on
// every successful call.
const REFRESH_INTERVAL_MS = 20 * 60 * 1000;

export function SessionRefreshTimer() {
  useEffect(() => {
    const interval = window.setInterval(() => {
      void refresh().catch(() => {
        // A failed background refresh isn't fatal — the next real request
        // will 401 and the user re-authenticates as before this existed.
      });
    }, REFRESH_INTERVAL_MS);
    return () => window.clearInterval(interval);
  }, []);

  return null;
}
