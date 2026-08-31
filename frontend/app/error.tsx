'use client';

import { useEffect } from 'react';

import { ShellRecoveryActions } from '@/app/(account)/ShellRecoveryActions';

/**
 * Root-segment error boundary. Next.js scopes error.tsx to everything BELOW
 * it in the tree except the root layout itself (app/layout.tsx) — see
 * global-error.tsx for that last-resort case — so this one catches a render
 * crash in app/page.tsx and in every (account) route that isn't already
 * covered by a more specific boundary (w/[workspaceId]/error.tsx exists for
 * that one segment; everything else — /, /workspaces/new, /settings — had
 * NO app-authored boundary until this file).
 *
 * Without this, a crash here fell through to Next's built-in fallback with
 * no app chrome at all: on a production build that is a blank white page,
 * not an error message — the exact "just white, nothing" report a stale
 * session (valid cookie, deleted account) can trigger if any component in
 * that path throws instead of degrading. w/[workspaceId]/error.tsx's own
 * comment documents the identical failure once already happening one layer
 * down ("the reported 'Hardware blank page'"); this closes the same gap one
 * layer up.
 *
 * Uses the same .app-page-message classes and ShellRecoveryActions
 * (Reload / Sign in again) as (account)/layout.tsx's own degraded-session
 * message, so an unexpected crash and a recognized degraded session look
 * like the same family of honest, recoverable state.
 */
export default function RootSegmentError({
  error,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  useEffect(() => {
    console.error('[root segment error]', error);
  }, [error]);

  return (
    <main className="app-page-message">
      <div className="app-page-message__content">
        <h1 className="app-page-message__title">Something went wrong</h1>
        <p className="app-page-message__body">
          This page hit an unexpected error and couldn&apos;t finish loading.
        </p>
        <p className="app-page-message__meta">
          Reload this page, or sign in again if the problem persists.
        </p>
        <ShellRecoveryActions label="Page recovery actions" />
      </div>
    </main>
  );
}
