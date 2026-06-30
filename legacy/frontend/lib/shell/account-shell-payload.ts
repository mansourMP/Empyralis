import type { AccountShellBootstrap } from '@/lib/shell/account-shell-store';

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === 'object' && !Array.isArray(value);
}

function requireString(value: unknown, field: string): string {
  if (typeof value !== 'string' || !value.trim()) {
    throw new Error(`Account shell payload is missing required string field: ${field}`);
  }
  return value;
}

function requireStringArray(value: unknown, field: string): string[] {
  if (!Array.isArray(value)) {
    throw new Error(`Account shell payload is missing required array field: ${field}`);
  }
  return value.filter((entry): entry is string => typeof entry === 'string');
}

export function parseAccountShellPayload(payload: unknown): AccountShellBootstrap {
  if (!isRecord(payload)) {
    throw new Error('Account shell payload must be an object.');
  }
  if (!isRecord(payload.account) || !Array.isArray(payload.workspaceMemberships)) {
    throw new Error('Account shell payload is missing required sections.');
  }

  return {
    account: {
      id: requireString(payload.account.id, 'account.id'),
      email: requireString(payload.account.email, 'account.email'),
      displayName:
        typeof payload.account.displayName === 'string' ? payload.account.displayName : null,
    },
    workspaceMemberships: payload.workspaceMemberships.flatMap((entry, index) => {
      if (!isRecord(entry) || !isRecord(entry.workspace)) {
        throw new Error(`Account shell payload has invalid membership at index ${index}.`);
      }

      return [
        {
          workspace: {
            id: requireString(entry.workspace.id, `workspaceMemberships[${index}].workspace.id`),
            tenantId: requireString(
              entry.workspace.tenantId,
              `workspaceMemberships[${index}].workspace.tenantId`,
            ),
            label: requireString(
              entry.workspace.label,
              `workspaceMemberships[${index}].workspace.label`,
            ),
            kind: requireString(
              entry.workspace.kind,
              `workspaceMemberships[${index}].workspace.kind`,
            ),
          },
          role: requireString(entry.role, `workspaceMemberships[${index}].role`) as
            | 'viewer'
            | 'member'
            | 'owner'
            | 'admin',
          permissions: requireStringArray(
            entry.permissions,
            `workspaceMemberships[${index}].permissions`,
          ),
          membershipVersion: requireString(
            entry.membershipVersion,
            `workspaceMemberships[${index}].membershipVersion`,
          ),
          defaultRoute: requireString(
            entry.defaultRoute,
            `workspaceMemberships[${index}].defaultRoute`,
          ),
          preferredShellProfileId:
            typeof entry.preferredShellProfileId === 'string'
              ? entry.preferredShellProfileId
              : null,
          setupCompleted: Boolean(entry.setupCompleted),
          requiresOnboarding: Boolean(entry.requiresOnboarding),
        },
      ];
    }),
  };
}
