import type { ReactNode } from 'react';

import { redirect } from 'next/navigation';

import {
  isDegradedAccountShellSession,
  loadAccountShellSession,
} from '@/lib/server/load-account-shell-session';
import { ShellRecoveryActions } from '@/app/(account)/ShellRecoveryActions';

export default async function AccountLayout({ children }: { children: ReactNode }) {
  const session = await loadAccountShellSession();

  if (!session) {
    redirect('/login');
  }

  if (isDegradedAccountShellSession(session)) {
    return (
      <main className="app-page-message">
        <div className="app-page-message__content">
          <h1 className="app-page-message__title">Workspace is warming up</h1>
          <p className="app-page-message__body">
            Sage could not load your workspace yet. This can happen during a deploy or service warm-up.
          </p>
          <p className="app-page-message__meta">
            Reload this page, or sign in again if the problem persists.
          </p>
          <ShellRecoveryActions label="Workspace recovery actions" />
        </div>
      </main>
    );
  }

  return children;
}
