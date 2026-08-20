import type { ReactNode } from 'react';

import { headers } from 'next/headers';
import { redirect } from 'next/navigation';

import {
  isDegradedAccountShellSession,
  loadAccountShellSession,
} from '@/lib/server/load-account-shell-session';
import { REQUEST_PATHNAME_HEADER, loginHrefForPath } from '@/lib/auth/login-next';
import { ShellRecoveryActions } from '@/app/(account)/ShellRecoveryActions';
import { SessionRefreshTimer } from '@/app/(account)/SessionRefreshTimer';
import { PendingWorkspaceInvitesBanner } from '@/app/(account)/PendingWorkspaceInvitesBanner';

export default async function AccountLayout({ children }: { children: ReactNode }) {
  const session = await loadAccountShellSession();

  if (!session) {
    // MAN-358: carry the destination through the sign-in, or every deep link
    // an agent puts in a Telegram/Slack message dead-ends at the workspace
    // root for the (very common) reader who has no session in this browser.
    // The path comes from proxy.ts's REQUEST_PATHNAME_HEADER -- Next exposes
    // no other way to read it in a server layout -- and is re-sanitised by
    // loginHrefForPath, so a value the proxy did not set cannot become an
    // open redirect.
    const requestedPath = (await headers()).get(REQUEST_PATHNAME_HEADER) || '';
    redirect(loginHrefForPath(requestedPath));
  }

  if (isDegradedAccountShellSession(session)) {
    return (
      <main className="app-page-message">
        <div className="app-page-message__content">
          <h1 className="app-page-message__title">Workspace is warming up</h1>
          <p className="app-page-message__body">
            We could not load your workspace yet. This can happen during a deploy or service warm-up.
          </p>
          <p className="app-page-message__meta">
            Reload this page, or sign in again if the problem persists.
          </p>
          <ShellRecoveryActions label="Workspace recovery actions" />
        </div>
      </main>
    );
  }

  return (
    <>
      <SessionRefreshTimer />
      <PendingWorkspaceInvitesBanner />
      {children}
    </>
  );
}
