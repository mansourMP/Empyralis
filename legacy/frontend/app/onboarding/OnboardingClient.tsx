'use client';

import Link from 'next/link';
import { useEffect, useMemo, useState } from 'react';
import { useRouter } from 'next/navigation';

import {
  loadAccountShellBootstrap,
  updateWorkspace,
  type CreateWorkspaceInput,
} from '@/lib/account/account-workspaces-client';
import { useAccountShell } from '@/lib/shell/account-shell-context';
import {
  sanitizeWorkspaceRoute,
  type WorkspaceMembershipRecord,
} from '@/lib/shell/workspace-membership-model';
import {
  DEFAULT_ROUTE_BY_PROFILE,
  WorkspaceSetupForm,
  createDefaultWorkspaceSetupValues,
} from '@/lib/workspace/workspace-setup-form';

function initialValuesForMembership(
  membership: WorkspaceMembershipRecord,
): CreateWorkspaceInput {
  return createDefaultWorkspaceSetupValues(membership.workspace.id, {
    name: membership.workspace.label,
    workspaceType:
      membership.workspace.kind === 'team'
      || membership.workspace.kind === 'professional'
      || membership.workspace.kind === 'personal'
        ? membership.workspace.kind
        : 'personal',
    preferredShellProfileId:
      membership.preferredShellProfileId === 'document_workstation_shell'
      || membership.preferredShellProfileId === 'operations_admin_shell'
        ? membership.preferredShellProfileId
        : 'personal_shell',
    defaultRoute: membership.defaultRoute,
  });
}

export function OnboardingClient({
  targetWorkspaceId,
  requestedWorkspaceId,
}: {
  targetWorkspaceId: string | null;
  requestedWorkspaceId: string | null;
}) {
  const router = useRouter();
  const { state, actions } = useAccountShell();
  const [submitting, setSubmitting] = useState(false);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);

  const membership = useMemo(
    () =>
      state.workspaceMemberships.find((item) => item.workspace.id === targetWorkspaceId)
      ?? null,
    [state.workspaceMemberships, targetWorkspaceId],
  );

  async function handleSubmit(values: CreateWorkspaceInput) {
    if (!membership) {
      return;
    }
    setSubmitting(true);
    setErrorMessage(null);
    try {
      await updateWorkspace(membership.workspace.id, {
        name: values.name,
        workspaceType: values.workspaceType,
        preferredShellProfileId: values.preferredShellProfileId,
        defaultRoute: values.defaultRoute,
        setupCompleted: true,
      });
      let nextRoute = sanitizeWorkspaceRoute(values.defaultRoute, membership.defaultRoute);
      try {
        const session = await loadAccountShellBootstrap();
        actions.replaceSession(session);
        const nextMembership = session.workspaceMemberships.find(
          (item) => item.workspace.id === membership.workspace.id,
        );
        nextRoute = nextMembership?.defaultRoute ?? nextRoute;
      } catch {
        // Continue with a workspace-scoped route even if the session refresh is transiently unavailable.
      }
      router.replace(nextRoute);
      router.refresh();
    } catch (error) {
      setErrorMessage(
        error instanceof Error ? error.message : 'Workspace setup could not be saved.',
      );
    } finally {
      setSubmitting(false);
    }
  }

  if (state.status !== 'authenticated') {
    return null;
  }

  if (!membership) {
    return (
      <main className="app-auth-page">
        <div className="app-message-card">
          <div className="app-auth-header">
            <span className="app-auth-kicker">Onboarding</span>
            <h1 className="app-auth-title">
            {requestedWorkspaceId
              ? 'Requested workspace is unavailable'
              : 'No workspace is ready for onboarding'}
            </h1>
            <p className="app-auth-subtitle">
            {requestedWorkspaceId
              ? 'The requested workspace was not found in your current account shell. Choose another workspace from account home instead of silently retargeting onboarding.'
              : 'The requested workspace was not found in your account shell. Create a new workspace to continue.'}
            </p>
          </div>
          <Link
            href={requestedWorkspaceId ? '/' : '/workspaces/new'}
            className="app-button app-button--primary"
          >
            {requestedWorkspaceId ? 'Return home' : 'Create workspace'}
          </Link>
        </div>
      </main>
    );
  }

  // Auto-submit with defaults for single-user platform — skip the setup form.
  useEffect(() => {
    handleSubmit(createDefaultWorkspaceSetupValues(membership.workspace.id, {
      name: membership.workspace.label || 'My Workspace',
      workspaceType: 'personal',
      preferredShellProfileId: 'personal_shell',
      defaultRoute: membership.defaultRoute || `/w/${encodeURIComponent(membership.workspace.id)}/sage/chat`,
    }));
  }, []);

  return null;
}
