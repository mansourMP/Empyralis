export type WorkspaceRole = 'viewer' | 'member' | 'owner' | 'admin';

export type AccountRecord = {
  id: string;
  email: string;
  displayName?: string | null;
};

export type WorkspaceRecord = {
  id: string;
  tenantId: string;
  label: string;
  kind: 'personal' | 'team' | 'enterprise' | 'side_business' | string;
};

export type WorkspaceMembershipRecord = {
  workspace: WorkspaceRecord;
  role: WorkspaceRole;
  permissions: string[];
  membershipVersion: string;
  defaultRoute: string;
  preferredShellProfileId?: string | null;
  setupCompleted: boolean;
  requiresOnboarding: boolean;
};

export type WorkspaceMembershipMap = Record<string, WorkspaceMembershipRecord>;

function extractWorkspaceIdFromRoute(route: string | null | undefined): string | null {
  if (!route || typeof route !== 'string') {
    return null;
  }

  const normalizedRoute = route.trim();
  if (!normalizedRoute.startsWith('/w/')) {
    return null;
  }

  const segments = normalizedRoute.split('/').filter(Boolean);
  if (segments[0] !== 'w' || !segments[1]) {
    return null;
  }

  return decodeURIComponent(segments[1]);
}

function normalizeWorkspaceRoute(route: string): string {
  if (route.endsWith('/workstation')) {
    return `${route.slice(0, -12)}/home`;
  }
  if (route === '/workstation') {
    return '/home';
  }
  if (route.endsWith('/dashboard')) {
    return `${route.slice(0, -10)}/admin`;
  }
  return route;
}

export function indexWorkspaceMemberships(
  memberships: WorkspaceMembershipRecord[],
): WorkspaceMembershipMap {
  return memberships.reduce<WorkspaceMembershipMap>((accumulator, membership) => {
    accumulator[membership.workspace.id] = membership;
    return accumulator;
  }, {});
}

export function resolvePrimaryWorkspaceId(
  memberships: WorkspaceMembershipRecord[],
): string | null {
  if (memberships.length === 0) {
    return null;
  }

  const personalMembership = memberships.find((membership) => membership.workspace.kind === 'personal');
  return personalMembership?.workspace.id ?? memberships[0].workspace.id;
}

export function isWorkspaceReadyForProduct(membership: WorkspaceMembershipRecord): boolean {
  return membership.setupCompleted && !membership.requiresOnboarding;
}

export function resolvePrimaryReadyWorkspaceId(
  memberships: WorkspaceMembershipRecord[],
): string | null {
  if (memberships.length === 0) {
    return null;
  }

  const readyOwnerOrAdmin = memberships.find(
    (membership) => isWorkspaceReadyForProduct(membership)
      && (membership.role === 'owner' || membership.role === 'admin'),
  );
  if (readyOwnerOrAdmin) {
    return readyOwnerOrAdmin.workspace.id;
  }

  const readyMembership = memberships.find((membership) => isWorkspaceReadyForProduct(membership));
  return readyMembership?.workspace.id ?? null;
}

export function resolvePrimaryProductWorkspaceId(
  memberships: WorkspaceMembershipRecord[],
): string | null {
  const readyWorkspaceId = resolvePrimaryReadyWorkspaceId(memberships);
  if (readyWorkspaceId) {
    return readyWorkspaceId;
  }

  return resolvePrimaryWorkspaceId(memberships);
}

export function resolveRouteWorkspaceId(
  memberships: WorkspaceMembershipRecord[],
  routeWorkspaceId: string | null,
): string | null {
  if (!routeWorkspaceId) {
    return null;
  }

  if (memberships.some((membership) => membership.workspace.id === routeWorkspaceId)) {
    return routeWorkspaceId;
  }

  return null;
}

export function sanitizeWorkspaceRoute(route: string | null | undefined, fallbackRoute: string): string {
  if (!route || typeof route !== 'string') {
    return normalizeWorkspaceRoute(fallbackRoute);
  }

  const normalizedRoute = route.trim();
  if (!normalizedRoute.startsWith('/')) {
    return normalizeWorkspaceRoute(fallbackRoute);
  }

  if (normalizedRoute.startsWith('//')) {
    return normalizeWorkspaceRoute(fallbackRoute);
  }

  const fallbackWorkspaceId = extractWorkspaceIdFromRoute(fallbackRoute);
  const routeWorkspaceId = extractWorkspaceIdFromRoute(normalizedRoute);

  if (normalizedRoute.startsWith('/w/') && !routeWorkspaceId) {
    return normalizeWorkspaceRoute(fallbackRoute);
  }

  if (routeWorkspaceId) {
    if (fallbackWorkspaceId && routeWorkspaceId !== fallbackWorkspaceId) {
      return normalizeWorkspaceRoute(fallbackRoute);
    }
    return normalizeWorkspaceRoute(normalizedRoute);
  }

  if (fallbackWorkspaceId) {
    const suffix = normalizedRoute === '/' ? '' : normalizedRoute;
    return normalizeWorkspaceRoute(`/w/${encodeURIComponent(fallbackWorkspaceId)}${suffix}`);
  }

  return normalizeWorkspaceRoute(normalizedRoute);
}

export function getWorkspaceMembership(
  memberships: WorkspaceMembershipMap,
  workspaceId: string | null,
): WorkspaceMembershipRecord | null {
  if (!workspaceId) {
    return null;
  }

  return memberships[workspaceId] ?? null;
}
