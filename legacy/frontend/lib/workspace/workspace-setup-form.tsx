'use client';

import { type FormEvent, useEffect, useId, useState } from 'react';

import { AppButton, AppInput, AppSelect } from '@/lib/ui/primitives';

import type {
  CreateWorkspaceInput,
  WorkspaceSetupType,
  WorkspaceShellProfileId,
} from '@/lib/account/account-workspaces-client';

export type WorkspaceSetupFormValues = CreateWorkspaceInput;
export type WorkspaceRouteMode = 'absolute' | 'relative';

export const DEFAULT_ROUTE_BY_PROFILE: Record<WorkspaceShellProfileId, string> = {
  personal_shell: '/sage',
  document_workstation_shell: '/sage',
  operations_admin_shell: '/sage',
};

const WORKSPACE_TYPE_OPTIONS: Array<{ value: WorkspaceSetupType; label: string }> = [
  { value: 'personal', label: 'Personal' },
  { value: 'professional', label: 'Professional' },
  { value: 'team', label: 'Team' },
];

const SHELL_PROFILE_OPTIONS: Array<{ value: WorkspaceShellProfileId; label: string }> = [
  { value: 'personal_shell', label: 'Personal Shell' },
  { value: 'document_workstation_shell', label: 'Document Workstation Shell' },
  { value: 'operations_admin_shell', label: 'Operations Admin Shell' },
];

const DEFAULT_ROUTE_OPTIONS = [
  { value: '/sage', label: 'Sage' },
  { value: '/studio', label: 'Build' },
  { value: '/settings', label: 'Settings' },
];

function normalizeWorkspaceRoute(
  workspaceId: string,
  relativeRoute: string,
  routeMode: WorkspaceRouteMode,
): string {
  const cleanRoute = relativeRoute.startsWith('/') ? relativeRoute : `/${relativeRoute}`;
  if (routeMode === 'relative') {
    return cleanRoute;
  }
  return `/w/${encodeURIComponent(workspaceId)}${cleanRoute}`;
}

export function createDefaultWorkspaceSetupValues(
  workspaceId: string,
  overrides: Partial<WorkspaceSetupFormValues> = {},
  routeMode: WorkspaceRouteMode = 'absolute',
): WorkspaceSetupFormValues {
  const preferredShellProfileId = overrides.preferredShellProfileId ?? 'personal_shell';
  const relativeDefaultRoute = DEFAULT_ROUTE_BY_PROFILE[preferredShellProfileId];
  return {
    name: overrides.name ?? '',
    workspaceType: overrides.workspaceType ?? 'personal',
    preferredShellProfileId,
    defaultRoute:
      overrides.defaultRoute ?? normalizeWorkspaceRoute(workspaceId, relativeDefaultRoute, routeMode),
  };
}

export function WorkspaceSetupForm({
  workspaceId,
  title,
  description,
  submitLabel,
  initialValues,
  submitting = false,
  errorMessage = null,
  routeMode = 'absolute',
  onSubmit,
}: {
  workspaceId: string;
  title: string;
  description: string;
  submitLabel: string;
  initialValues: WorkspaceSetupFormValues;
  submitting?: boolean;
  errorMessage?: string | null;
  routeMode?: WorkspaceRouteMode;
  onSubmit: (values: WorkspaceSetupFormValues) => Promise<void> | void;
}) {
  const nameId = useId();
  const typeId = useId();
  const shellId = useId();
  const routeId = useId();
  const [isHydrated, setIsHydrated] = useState(false);
  const [values, setValues] = useState<WorkspaceSetupFormValues>(initialValues);

  useEffect(() => {
    setIsHydrated(true);
  }, []);

  useEffect(() => {
    setValues(initialValues);
  }, [workspaceId]);

  function update<K extends keyof WorkspaceSetupFormValues>(
    key: K,
    nextValue: WorkspaceSetupFormValues[K],
  ) {
    setValues((current) => ({ ...current, [key]: nextValue }));
  }

  function handleShellProfileChange(nextProfile: WorkspaceShellProfileId) {
    const nextDefaultRoute = normalizeWorkspaceRoute(
      workspaceId,
      DEFAULT_ROUTE_BY_PROFILE[nextProfile],
      routeMode,
    );
    setValues((current) => {
      const currentDefaultRoutes = new Set(
        SHELL_PROFILE_OPTIONS.map((option) =>
          normalizeWorkspaceRoute(workspaceId, DEFAULT_ROUTE_BY_PROFILE[option.value], routeMode),
        ),
      );
      return {
        ...current,
        preferredShellProfileId: nextProfile,
        defaultRoute: currentDefaultRoutes.has(current.defaultRoute)
          ? nextDefaultRoute
          : current.defaultRoute,
      };
    });
  }

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!isHydrated) {
      return;
    }
    await onSubmit(values);
  }

  const controlsDisabled = submitting || !isHydrated;

  return (
    <form
      onSubmit={handleSubmit}
      data-workspace-setup-form={workspaceId}
      className="app-auth-card app-auth-card--wide app-setup-form"
    >
      <div className="app-auth-header">
        <span className="app-auth-kicker">Workspace setup</span>
        <h1 className="app-auth-title">{title}</h1>
        <p className="app-auth-subtitle">{description}</p>
      </div>

      <div className="app-setup-form__body">
        <label className="app-auth-field" htmlFor={nameId}>
          <span className="app-auth-field__label">Workspace name</span>
          <AppInput
            id={nameId}
            name="workspace-name"
            type="text"
            value={values.name}
            onChange={(event) => update('name', event.target.value)}
            disabled={controlsDisabled}
            placeholder="Acme Deal Room"
            required
          />
        </label>

        <label className="app-auth-field" htmlFor={typeId}>
          <span className="app-auth-field__label">Workspace type</span>
          <AppSelect
            id={typeId}
            value={values.workspaceType}
            onChange={(event) => update('workspaceType', event.target.value as WorkspaceSetupType)}
            disabled={controlsDisabled}
          >
            {WORKSPACE_TYPE_OPTIONS.map((option) => (
              <option key={option.value} value={option.value}>
                {option.label}
              </option>
            ))}
          </AppSelect>
        </label>

        <label className="app-auth-field" htmlFor={shellId}>
          <span className="app-auth-field__label">Shell profile</span>
          <AppSelect
            id={shellId}
            value={values.preferredShellProfileId}
            onChange={(event) =>
              handleShellProfileChange(event.target.value as WorkspaceShellProfileId)
            }
            disabled={controlsDisabled}
          >
            {SHELL_PROFILE_OPTIONS.map((option) => (
              <option key={option.value} value={option.value}>
                {option.label}
              </option>
            ))}
          </AppSelect>
        </label>

        <label className="app-auth-field" htmlFor={routeId}>
          <span className="app-auth-field__label">Default route</span>
          <AppSelect
            id={routeId}
            value={values.defaultRoute}
            onChange={(event) => update('defaultRoute', event.target.value)}
            disabled={controlsDisabled}
          >
            {DEFAULT_ROUTE_OPTIONS.map((option) => (
              <option
                key={option.value}
                value={normalizeWorkspaceRoute(workspaceId, option.value, routeMode)}
              >
                {option.label}
              </option>
            ))}
          </AppSelect>
        </label>
      </div>

      {errorMessage ? (
        <div role="alert" className="app-auth-error">
          {errorMessage}
        </div>
      ) : null}

      <div className="app-setup-form__actions">
        <AppButton type="submit" disabled={controlsDisabled}>
          {submitting ? 'Saving…' : submitLabel}
        </AppButton>
      </div>
    </form>
  );
}
