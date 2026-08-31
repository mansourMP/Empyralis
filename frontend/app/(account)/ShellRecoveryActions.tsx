'use client';

import { useCallback, useState, useTransition } from 'react';

import { logout } from '@/lib/auth/auth-client';

type ShellRecoveryActionsProps = {
  label: string;
};

// A browser can land on any of this app's degraded/error screens holding a
// stray host-only empyralis_csrf_token cookie twin (RFC 6265 -- a
// Domain-scoped cookie and a host-only one with no Domain attribute are
// independent entries; the client reads one, the server reads the other).
// Every mutating request 403s csrf_mismatch, including POST /api/auth/login
// while the old access-token cookie is still live -- validateBrowserCsrf
// (control-plane-proxy.ts) only skips CSRF once the session cookie is truly
// dead. A plain link to /login used to send a stuck person straight into
// that same 403 with no way out. Logout is the one route in the app with a
// deliberate, already-sanctioned CSRF exemption
// (app/api/auth/logout/route.ts) -- the only action guaranteed to reach the
// backend here -- and it now also deletes the host-only twin, healing the
// split identity. That means recovery has to run a real POST before
// navigating, which a plain <a href> cannot do: hence a <button>, not a
// link (the same shape PrimaryRail.tsx's own account-menu "Log out" already
// uses, for the same reason). If logout itself fails (network down, backend
// unreachable), the `finally` still sends the person to /login rather than
// stranding them here.
//
// Exported as a hook (not folded into ShellRecoveryActions below) because
// app/onboarding/OnboardingClient.tsx's own "Workspace setup couldn't
// finish" screen needs the identical sign-out action next to its own
// "Retry" button, not the generic "Reload" this component pairs it with --
// same recovery action, one implementation, two button layouts.
export function useSignOutAndStartOver() {
  const [isSigningOut, setIsSigningOut] = useState(false);
  const signOutAndStartOver = useCallback(async () => {
    setIsSigningOut(true);
    try {
      await logout();
    } catch {
      // Logout is the escape hatch. If even it fails, /login below is still
      // the useful place to land -- never leave the person with nothing.
    } finally {
      window.location.assign('/login');
    }
  }, []);
  return { isSigningOut, signOutAndStartOver };
}

export function ShellRecoveryActions({ label }: ShellRecoveryActionsProps) {
  const [isPending, startTransition] = useTransition();
  const { isSigningOut, signOutAndStartOver } = useSignOutAndStartOver();
  const reload = useCallback(() => {
    startTransition(() => {
      window.location.reload();
    });
  }, []);

  const busy = isPending || isSigningOut;

  return (
    <div className="app-page-message__actions" aria-label={label}>
      <button
        type="button"
        className="app-page-message__button"
        onClick={reload}
        disabled={busy}
      >
        {isPending ? 'Reloading...' : 'Reload'}
      </button>
      <button
        type="button"
        className="app-page-message__button app-page-message__button--secondary"
        onClick={signOutAndStartOver}
        disabled={busy}
      >
        {isSigningOut ? 'Signing out...' : 'Sign out and start over'}
      </button>
    </div>
  );
}
