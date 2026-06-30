'use client';

import { buildCookieAuthHeaders } from '@/lib/auth/csrf';
import type { AccountShellBootstrap } from '@/lib/shell/account-shell-store';
import { parseAccountShellPayload } from '@/lib/shell/account-shell-payload';
import { resolveWorkspaceApiBaseUrl, type WorkspaceApiEnv } from '@/lib/workspace/workspace-services';

export type WorkspaceSetupType = 'personal' | 'professional' | 'team';
export type WorkspaceShellProfileId =
  | 'personal_shell'
  | 'document_workstation_shell'
  | 'operations_admin_shell';

export type AccountWorkspaceSummary = {
  workspace: {
    id: string;
    tenantId: string;
    label: string;
    kind: string;
  };
  role?: 'viewer' | 'member' | 'owner' | 'admin';
  defaultRoute: string;
  preferredShellProfileId: string | null;
  setupCompleted: boolean;
  requiresOnboarding: boolean;
};

export type CreateWorkspaceInput = {
  name: string;
  workspaceType: WorkspaceSetupType;
  preferredShellProfileId: WorkspaceShellProfileId;
  defaultRoute: string;
};

export type UpdateWorkspaceInput = Partial<CreateWorkspaceInput> & {
  setupCompleted?: boolean;
};

function apiBaseUrl(): string {
  return resolveWorkspaceApiBaseUrl(
    process.env as WorkspaceApiEnv,
    typeof window !== 'undefined' ? window.location.origin : undefined,
  );
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === 'object' && !Array.isArray(value);
}

function requireString(value: unknown, field: string): string {
  if (typeof value !== 'string' || !value.trim()) {
    throw new Error(`Workspace payload is missing required string field: ${field}`);
  }
  return value;
}

function parseWorkspaceSummary(payload: unknown): AccountWorkspaceSummary {
  if (!isRecord(payload) || !isRecord(payload.workspace)) {
    throw new Error('Workspace payload is missing required workspace data.');
  }

  return {
    workspace: {
      id: requireString(payload.workspace.id, 'workspace.id'),
      tenantId: requireString(payload.workspace.tenantId, 'workspace.tenantId'),
      label: requireString(payload.workspace.label, 'workspace.label'),
      kind: requireString(payload.workspace.kind, 'workspace.kind'),
    },
    role:
      payload.role === 'viewer'
      || payload.role === 'member'
      || payload.role === 'owner'
      || payload.role === 'admin'
        ? payload.role
        : undefined,
    defaultRoute: requireString(payload.defaultRoute, 'defaultRoute'),
    preferredShellProfileId:
      typeof payload.preferredShellProfileId === 'string'
        ? payload.preferredShellProfileId
        : null,
    setupCompleted: Boolean(payload.setupCompleted),
    requiresOnboarding: Boolean(payload.requiresOnboarding),
  };
}

async function parseJsonResponse(response: Response): Promise<unknown> {
  if (response.status === 204) {
    return null;
  }
  return response.json();
}

async function requestJson<T>(
  path: string,
  init: RequestInit,
  parser: (payload: unknown) => T,
): Promise<T> {
  const response = await fetch(`${apiBaseUrl()}${path}`, {
    ...init,
    credentials: 'include',
    headers: buildCookieAuthHeaders(init.method ?? 'GET', {
      accept: 'application/json',
      ...(init.body ? { 'content-type': 'application/json' } : {}),
      ...(init.headers ?? {}),
    }),
  });

  if (!response.ok) {
    throw new Error(`Workspace request failed with status ${response.status}.`);
  }

  return parser(await parseJsonResponse(response));
}

export async function listWorkspaces(): Promise<AccountWorkspaceSummary[]> {
  return requestJson('/api/workspaces', { method: 'GET' }, (payload) => {
    if (!isRecord(payload) || !Array.isArray(payload.items)) {
      throw new Error('Workspace list payload is invalid.');
    }
    return payload.items.map((entry) => parseWorkspaceSummary(entry));
  });
}

export async function createWorkspace(input: CreateWorkspaceInput): Promise<AccountWorkspaceSummary> {
  return requestJson('/api/workspaces', {
    method: 'POST',
    body: JSON.stringify({
      name: input.name,
      workspace_type: input.workspaceType,
      preferred_shell_profile: input.preferredShellProfileId,
      default_route: input.defaultRoute,
    }),
  }, parseWorkspaceSummary);
}

export async function updateWorkspace(
  workspaceId: string,
  input: UpdateWorkspaceInput,
): Promise<AccountWorkspaceSummary> {
  const body: Record<string, unknown> = {};
  if (typeof input.name === 'string') {
    body.name = input.name;
  }
  if (typeof input.workspaceType === 'string') {
    body.workspace_type = input.workspaceType;
  }
  if (typeof input.preferredShellProfileId === 'string') {
    body.preferred_shell_profile = input.preferredShellProfileId;
  }
  if (typeof input.defaultRoute === 'string') {
    body.default_route = input.defaultRoute;
  }
  if (typeof input.setupCompleted === 'boolean') {
    body.setup_completed = input.setupCompleted;
  }

  return requestJson(
    `/api/workspaces/${encodeURIComponent(workspaceId)}`,
    {
      method: 'PATCH',
      body: JSON.stringify(body),
    },
    parseWorkspaceSummary,
  );
}

export async function loadAccountShellBootstrap(): Promise<AccountShellBootstrap> {
  return requestJson('/api/auth/account-shell', { method: 'GET' }, parseAccountShellPayload);
}
