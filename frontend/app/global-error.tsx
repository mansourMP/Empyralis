'use client';

import { useEffect } from 'react';

/**
 * Last-resort error boundary. This is the ONLY boundary that can catch a
 * crash in the root layout itself (app/layout.tsx) — a regular error.tsx
 * (see app/error.tsx, one level down) is scoped to everything BELOW the
 * segment it sits in and never catches that segment's own layout. Next.js
 * requires global-error.tsx to render its own <html>/<body> because it
 * replaces the root layout entirely when it fires, so this deliberately
 * does NOT import any app CSS or shared component — the whole point of this
 * file is to still render something honest when the normal app shell itself
 * is what broke. Inline styles only, on purpose.
 *
 * Before this existed, a crash in the root layout (or anywhere else with no
 * boundary above it) fell through to Next's bare built-in fallback, which on
 * a production build is a blank white page with no message and no way back
 * short of guessing the URL for /login. That is the single worst outcome in
 * the product — see app/error.tsx's comment for the sibling gap this closes
 * one layer up.
 */
export default function GlobalError({
  error,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  useEffect(() => {
    console.error('[global error]', error);
  }, [error]);

  return (
    <html lang="en">
      <body
        style={{
          margin: 0,
          minHeight: '100vh',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          fontFamily:
            '-apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif',
          background: '#f7f7f5',
          color: '#1a1a1a',
        }}
      >
        <div style={{ maxWidth: 420, textAlign: 'center', padding: 24 }}>
          <h1 style={{ fontSize: 20, fontWeight: 600, margin: '0 0 8px' }}>
            Something went wrong
          </h1>
          <p style={{ fontSize: 14, lineHeight: 1.5, color: '#555', margin: '0 0 20px' }}>
            This page hit an unexpected error and couldn&apos;t finish loading. Reload the
            page, or sign in again if the problem persists.
          </p>
          <div style={{ display: 'flex', gap: 8, justifyContent: 'center' }}>
            <a
              href="/"
              style={{
                padding: '8px 16px',
                borderRadius: 6,
                border: '1px solid #d8d8d4',
                color: '#1a1a1a',
                textDecoration: 'none',
                fontSize: 14,
              }}
            >
              Reload
            </a>
            <a
              href="/login"
              style={{
                padding: '8px 16px',
                borderRadius: 6,
                border: '1px solid #d8d8d4',
                color: '#1a1a1a',
                textDecoration: 'none',
                fontSize: 14,
              }}
            >
              Sign in again
            </a>
          </div>
        </div>
      </body>
    </html>
  );
}
