export type WorkspaceBootstrapAccount = {
  id: string;
  email: string;
  displayName?: string | null;
};

export type WorkspaceBootstrapWorkspace = {
  id: string;
  tenantId: string;
  label: string;
  kind: string;
};

export type WorkspaceBootstrapMembership = {
  role: string;
  permissions: string[];
  version: string;
};

export type WorkspaceBootstrapEntitlements = {
  plan: string;
  label: string;
  source: string;
  flags: Record<string, boolean>;
  limits: Record<string, number | null>;
};

export type WorkspaceBootstrapRuntimeTarget = {
  id: string;
  canonicalId?: string | null;
  runtimeFabricTarget?: string | null;
  legacyId?: string | null;
  publicLabel?: string | null;
  label: string;
  kind: string;
  online: boolean;
  preferred: boolean;
  available: boolean;
  healthy: boolean;
  status: string;
  statusLabel: string | null;
  statusReason: string | null;
  description?: string | null;
  connectionMode?: string | null;
  executionTarget?: string | null;
  trustTier?: string | null;
  approvalMode?: string | null;
  defaultRuntimeAccessMode?: string | null;
  supportsFullAccess?: boolean;
  executionModes?: WorkspaceBootstrapExecutionMode[];
  sampleAttachmentLabel?: string | null;
};

export type WorkspaceBootstrapExecutionMode = {
  id: string;
  label: string;
  description?: string | null;
  available: boolean;
  runtimeAccessMode?: string | null;
  requiresExplicitSelection?: boolean;
  requiresOwnerApproval?: boolean;
  setupWarning?: string | null;
};

export type WorkspaceBootstrapRuntime = {
  deploymentMode: string;
  runtimeTargets: WorkspaceBootstrapRuntimeTarget[];
};

export type WorkspaceBootstrapShellHints = {
  defaultRoute: string;
  preferredProfile: string;
  setupCompleted: boolean;
  requiresOnboarding: boolean;
};

export type WorkspaceBootstrapPayload = {
  account: WorkspaceBootstrapAccount;
  workspace: WorkspaceBootstrapWorkspace;
  membership: WorkspaceBootstrapMembership;
  capabilities: Record<string, boolean>;
  entitlements: WorkspaceBootstrapEntitlements;
  workspaceTraits: Record<string, unknown>;
  runtime: WorkspaceBootstrapRuntime;
  shellHints: WorkspaceBootstrapShellHints;
};

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === 'object' && !Array.isArray(value);
}

function requireString(value: unknown, field: string): string {
  if (typeof value !== 'string' || !value.trim()) {
    throw new Error(`Workspace bootstrap is missing required string field: ${field}`);
  }
  return value;
}

function requireBooleanRecord(value: unknown, field: string): Record<string, boolean> {
  if (!isRecord(value)) {
    throw new Error(`Workspace bootstrap is missing required object field: ${field}`);
  }

  const out: Record<string, boolean> = {};
  for (const [key, entry] of Object.entries(value)) {
    if (typeof entry === 'boolean') {
      out[key] = entry;
    }
  }
  return out;
}

function requireNumberLimitRecord(value: unknown, field: string): Record<string, number | null> {
  if (!isRecord(value)) {
    throw new Error(`Workspace bootstrap is missing required object field: ${field}`);
  }

  const out: Record<string, number | null> = {};
  for (const [key, entry] of Object.entries(value)) {
    if (entry === null || typeof entry === 'number') {
      out[key] = entry;
    }
  }
  return out;
}

function requireStringArray(value: unknown, field: string): string[] {
  if (!Array.isArray(value)) {
    throw new Error(`Workspace bootstrap is missing required array field: ${field}`);
  }

  return value.filter((entry): entry is string => typeof entry === 'string');
}

function parseRuntimeTargets(value: unknown): WorkspaceBootstrapRuntimeTarget[] {
  if (!Array.isArray(value)) {
    return [];
  }

  return value.flatMap((entry) => {
    if (!isRecord(entry)) {
      return [];
    }

    return [
      {
        id: requireString(entry.id, 'runtime.runtimeTargets[].id'),
        canonicalId: typeof entry.canonicalId === 'string' ? entry.canonicalId : null,
        runtimeFabricTarget: typeof entry.runtimeFabricTarget === 'string' ? entry.runtimeFabricTarget : null,
        legacyId: typeof entry.legacyId === 'string' ? entry.legacyId : null,
        publicLabel: typeof entry.publicLabel === 'string' ? entry.publicLabel : null,
        label: requireString(entry.label, 'runtime.runtimeTargets[].label'),
        kind: requireString(entry.kind, 'runtime.runtimeTargets[].kind'),
        online: Boolean(entry.online),
        preferred: Boolean(entry.preferred),
        available: Boolean(entry.available),
        healthy: Boolean(entry.healthy),
        status: typeof entry.status === 'string' && entry.status.trim() ? entry.status : 'unavailable',
        statusLabel: typeof entry.statusLabel === 'string' ? entry.statusLabel : null,
        statusReason: typeof entry.statusReason === 'string' ? entry.statusReason : null,
        description: typeof entry.description === 'string' ? entry.description : null,
        connectionMode: typeof entry.connectionMode === 'string' ? entry.connectionMode : null,
        executionTarget: typeof entry.executionTarget === 'string' ? entry.executionTarget : null,
        trustTier: typeof entry.trustTier === 'string' ? entry.trustTier : null,
        approvalMode: typeof entry.approvalMode === 'string' ? entry.approvalMode : null,
        defaultRuntimeAccessMode: typeof entry.defaultRuntimeAccessMode === 'string' ? entry.defaultRuntimeAccessMode : null,
        supportsFullAccess: Boolean(entry.supportsFullAccess),
        executionModes: Array.isArray(entry.executionModes)
          ? entry.executionModes.flatMap((mode) => {
              if (!isRecord(mode) || typeof mode.id !== 'string' || !mode.id.trim()) {
                return [];
              }
              return [{
                id: mode.id.trim(),
                label: typeof mode.label === 'string' && mode.label.trim() ? mode.label.trim() : mode.id.trim(),
                description: typeof mode.description === 'string' ? mode.description : null,
                available: Boolean(mode.available),
                runtimeAccessMode: typeof mode.runtimeAccessMode === 'string' ? mode.runtimeAccessMode : null,
                requiresExplicitSelection: Boolean(mode.requiresExplicitSelection),
                requiresOwnerApproval: Boolean(mode.requiresOwnerApproval),
                setupWarning: typeof mode.setupWarning === 'string' ? mode.setupWarning : null,
              }];
            })
          : [],
        sampleAttachmentLabel: typeof entry.sampleAttachmentLabel === 'string' ? entry.sampleAttachmentLabel : null,
      },
    ];
  });
}

export function parseWorkspaceBootstrapPayload(payload: unknown): WorkspaceBootstrapPayload {
  if (!isRecord(payload)) {
    throw new Error('Workspace bootstrap payload must be an object.');
  }

  const account = payload.account;
  const workspace = payload.workspace;
  const membership = payload.membership;
  const entitlements = payload.entitlements;
  const runtime = payload.runtime;
  const shellHints = payload.shellHints;

  if (!isRecord(account) || !isRecord(workspace) || !isRecord(membership) || !isRecord(entitlements) || !isRecord(runtime) || !isRecord(shellHints)) {
    throw new Error('Workspace bootstrap payload is missing required sections.');
  }

  return {
    account: {
      id: requireString(account.id, 'account.id'),
      email: requireString(account.email, 'account.email'),
      displayName: typeof account.displayName === 'string' ? account.displayName : null,
    },
    workspace: {
      id: requireString(workspace.id, 'workspace.id'),
      tenantId: requireString(workspace.tenantId, 'workspace.tenantId'),
      label: requireString(workspace.label, 'workspace.label'),
      kind: requireString(workspace.kind, 'workspace.kind'),
    },
    membership: {
      role: requireString(membership.role, 'membership.role'),
      permissions: requireStringArray(membership.permissions, 'membership.permissions'),
      version: requireString(membership.version, 'membership.version'),
    },
    capabilities: requireBooleanRecord(payload.capabilities, 'capabilities'),
    entitlements: {
      plan: requireString(entitlements.plan, 'entitlements.plan'),
      label: requireString(entitlements.label, 'entitlements.label'),
      source: requireString(entitlements.source, 'entitlements.source'),
      flags: requireBooleanRecord(entitlements.flags, 'entitlements.flags'),
      limits: requireNumberLimitRecord(entitlements.limits, 'entitlements.limits'),
    },
    workspaceTraits: isRecord(payload.workspaceTraits) ? payload.workspaceTraits : {},
    runtime: {
      deploymentMode: requireString(runtime.deploymentMode, 'runtime.deploymentMode'),
      runtimeTargets: parseRuntimeTargets(runtime.runtimeTargets),
    },
    shellHints: {
      defaultRoute: requireString(shellHints.defaultRoute, 'shellHints.defaultRoute'),
      preferredProfile: requireString(shellHints.preferredProfile, 'shellHints.preferredProfile'),
      setupCompleted: Boolean(shellHints.setupCompleted),
      requiresOnboarding: Boolean(shellHints.requiresOnboarding),
    },
  };
}

export type WorkstationKernelKeyParts = {
  accountId: string;
  tenantId: string;
  workspaceId: string;
  membershipVersion: string;
  shellProfileId: string;
};

export function createWorkstationKernelKey({
  accountId,
  tenantId,
  workspaceId,
  membershipVersion,
  shellProfileId,
}: WorkstationKernelKeyParts): string {
  return `${accountId}:${tenantId}:${workspaceId}:${membershipVersion}:${shellProfileId}`;
}

export function createWorkspaceBoundaryKey(
  workspaceId: string,
  membershipVersion: string,
  shellProfileId: string,
): string {
  return createWorkstationKernelKey({
    accountId: 'anonymous',
    tenantId: 'default',
    workspaceId,
    membershipVersion,
    shellProfileId,
  });
}
