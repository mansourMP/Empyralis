'use client';

import { useState } from 'react';
import { useRouter } from 'next/navigation';
import { X } from 'lucide-react';

import {
  createWorkspace,
  loadAccountShellBootstrap,
  type CreateWorkspaceInput,
} from '@/lib/account/account-workspaces-client';
import { useAccountShell } from '@/lib/shell/account-shell-context';
import {
  WorkspaceSetupForm,
  createDefaultWorkspaceSetupValues,
} from '@/lib/workspace/workspace-setup-form';

const NEW_WORKSPACE_FORM_ID = 'new-workspace';

export function NewWorkspacePageClient() {
  const router = useRouter();
  const { state, actions } = useAccountShell();
  const [submitting, setSubmitting] = useState(false);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);

  function resolveCloseHref(): string {
    const fallbackWorkspaceId =
      state.selectedWorkspaceId
      ?? state.workspaceMemberships.find(
        (membership) => membership.setupCompleted && !membership.requiresOnboarding,
      )?.workspace.id
      ?? state.workspaceMemberships[0]?.workspace.id
      ?? null;

    if (!fallbackWorkspaceId) {
      return '/';
    }

    return actions.resolveWorkspaceHref(fallbackWorkspaceId)
      ?? `/w/${encodeURIComponent(fallbackWorkspaceId)}/sage`;
  }

  function handleClose() {
    const fallbackHref = resolveCloseHref();
    if (typeof window !== 'undefined') {
      try {
        const referrer = document.referrer ? new URL(document.referrer) : null;
        if (
          referrer
          && referrer.origin === window.location.origin
          && referrer.pathname !== window.location.pathname
        ) {
          router.back();
          return;
        }
      } catch {
        // Fall through to the workspace-safe route when the browser referrer is unavailable.
      }
    }
    router.push(fallbackHref);
  }

  async function handleSubmit(values: CreateWorkspaceInput) {
    setSubmitting(true);
    setErrorMessage(null);
    try {
      const createdWorkspace = await createWorkspace(values);
      const session = await loadAccountShellBootstrap();
      actions.replaceSession(session);
      router.replace(createdWorkspace.defaultRoute);
    } catch (error) {
      setErrorMessage(
        error instanceof Error ? error.message : 'Workspace could not be created.',
      );
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <main className="app-page-shell">
      <div className="app-page-shell__content app-workspace-create-shell">
        <button
          type="button"
          className="app-workspace-create-shell__close"
          aria-label="Close workspace setup"
          onClick={handleClose}
        >
          <X size={18} aria-hidden="true" />
        </button>
        <WorkspaceSetupForm
          workspaceId={NEW_WORKSPACE_FORM_ID}
          routeMode="relative"
          title="Create a workspace"
          description="Every additional workspace needs an explicit shell profile and default route before it becomes part of the account shell."
          submitLabel="Create workspace"
          initialValues={createDefaultWorkspaceSetupValues(NEW_WORKSPACE_FORM_ID, {}, 'relative')}
          submitting={submitting}
          errorMessage={errorMessage}
          onSubmit={handleSubmit}
        />
      </div>
    </main>
  );
}
