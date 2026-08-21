import type { ReactNode } from 'react';
import { notFound, redirect } from 'next/navigation';

import { ShellRecoveryActions } from '@/app/(account)/ShellRecoveryActions';
import { DesktopGatewayPairing } from '@/lib/desktop/desktop-gateway-pairing';
import { loadAccountShellSessionSafely } from '@/lib/server/load-account-shell-session';
import { resolvePrimaryReadyWorkspaceId } from '@/lib/shell/workspace-membership-model';
import {
  WorkspaceBootstrapError,
  loadWorkspaceBootstrap,
} from '@/lib/workspace/server-workspace-bootstrap';
import { FleetShell } from '@/lib/workspace/fleet/FleetShell';

const RECOVERABLE_BOOTSTRAP_STATUSES = new Set([429, 500, 502, 503, 504]);

export default async function WorkspaceRouteLayout({
  children,
  params,
}: {
  children: ReactNode;
  params: Promise<{ workspaceId: string }>;
}) {
  const { workspaceId } = await params;
  let bootstrap;
  try {
    bootstrap = await loadWorkspaceBootstrap(workspaceId);
  } catch (error) {
    if (error instanceof WorkspaceBootstrapError) {
      if (error.status === 401) {
        redirect('/login');
      }
      if (error.status === 403) {
        redirect('/');
      }
      if (error.status === 404) {
        notFound();
      }
      if (RECOVERABLE_BOOTSTRAP_STATUSES.has(error.status)) {
        return (
          <main className="app-page-message app-page-message--workspace-bootstrap">
            <div className="app-page-message__content">
              <h1 className="app-page-message__title">Workspace is warming up</h1>
              <p className="app-page-message__body">
                We could not load this workspace yet. This usually clears after a deploy or service restart.
              </p>
              <p className="app-page-message__meta">
                Reload the workspace, or sign in again if it keeps happening.
              </p>
              <ShellRecoveryActions label="Workspace recovery actions" />
            </div>
          </main>
        );
      }
    }
    throw error;
  }

  if (bootstrap.shellHints.requiresOnboarding) {
    const session = await loadAccountShellSessionSafely();
    const readyWorkspaceId = session
      ? resolvePrimaryReadyWorkspaceId(session.workspaceMemberships)
      : null;
    if (readyWorkspaceId && readyWorkspaceId !== bootstrap.workspace.id) {
      redirect(`/w/${encodeURIComponent(readyWorkspaceId)}/sage`);
    }
    redirect(`/onboarding?workspaceId=${encodeURIComponent(bootstrap.workspace.id)}`);
  }

  const resolvedWorkspaceId = bootstrap.workspace.id;

  // Phase 8: the legacy workstation shell is gone — every workspace surface now
  // renders fleet-native inside FleetShell. The old fall-through `shellSlot`
  // (DesktopStartupScreen → WorkstationShellFrame → WorkspaceBoundary →
  // WorkstationKernelShell) drove only the legacy segments, which are all now
  // 307-redirected to fleet routes, so nothing falls through. Pass null.
  return (
    <>
      {/*
        Renders nothing — see desktop-gateway-pairing.tsx's own doc comment.
        Outside the desktop app (window.empyralisDesktop absent) this is a
        no-op on every ordinary browser tab.
      */}
      <DesktopGatewayPairing
        workspaceId={resolvedWorkspaceId}
        workspaceLabel={bootstrap.workspace.label}
      />
      <FleetShell
        workspaceId={resolvedWorkspaceId}
        shellSlot={null}
      // ownerDisplayName is a real display name or nothing — it must never
      // fall back to the account email here. bootstrap.account.email is
      // already XOR-obfuscated (see ssr-safe-email.ts); PrimaryRail decodes
      // it client-side, after hydration, via useRevealedEmail, and uses it
      // as the name fallback there instead. Folding email into ownerName at
      // this server boundary would bake the obfuscated form straight into
      // visible rail text before that decode ever runs.
        ownerDisplayName={bootstrap.account.displayName || undefined}
        ownerEmailObfuscated={bootstrap.account.email}
        ownerRole={bootstrap.membership.role}
      >
        {children}
      </FleetShell>
    </>
  );
}
