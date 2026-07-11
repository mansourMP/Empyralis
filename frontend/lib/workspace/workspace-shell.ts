import type { WorkspaceBootstrapPayload } from '@/lib/workspace/workspace-bootstrap';
import {
  WORKSPACE_NAV_DESTINATIONS,
  buildWorkspaceRouteHref,
  getWorkspaceNavDestinationDefinition,
  getWorkspaceNavRouteDefinition,
  resolveWorkspaceRouteIdFromSegment,
  WORKSPACE_ROUTE_ID_SET,
  WORKSPACE_WEB_ROUTE_DEFINITIONS,
  type WorkspaceNavDestinationId,
  type WorkspaceNavRouteDefinition,
  type WorkspaceRouteId,
  type WorkspaceShellProfileId,
} from '../../../shared/nav-manifest';

export type {
  WorkspaceNavDestinationId,
  WorkspaceRouteId,
  WorkspaceShellProfileId,
} from '../../../shared/nav-manifest';

export type WorkspaceShellProfile = {
  id: WorkspaceShellProfileId;
  label: string;
  description: string;
  homeRouteId: WorkspaceRouteId;
};

export type WorkspaceRouteManifestEntry = {
  id: WorkspaceRouteId;
  label: string;
  href: string;
  destinationId: WorkspaceNavDestinationId;
  requiredCapabilities: string[];
};

export type WorkspaceRouteManifestGroup = {
  id: WorkspaceNavDestinationId;
  label: string;
  iconName: string;
  href: string;
  defaultRouteId: WorkspaceRouteId;
  direct: boolean;
  routes: WorkspaceRouteManifestEntry[];
};

export type WorkspaceRouteManifest = {
  shellProfileId: WorkspaceShellProfileId;
  defaultRoute: string;
  routeIds: WorkspaceRouteId[];
  routeIndex: Partial<Record<WorkspaceRouteId, WorkspaceRouteManifestEntry>>;
  navGroups: WorkspaceRouteManifestGroup[];
};

const SHELL_PROFILE_DEFINITIONS: Record<WorkspaceShellProfileId, WorkspaceShellProfile> = {
  personal_shell: {
    id: 'personal_shell',
    label: 'Personal Shell',
    description: 'General-purpose personal workspace layout.',
    homeRouteId: 'chat',
  },
  document_workstation_shell: {
    id: 'document_workstation_shell',
    label: 'Document Workstation Shell',
    description: 'Document-heavy split workstation layout.',
    homeRouteId: 'chat',
  },
  operations_admin_shell: {
    id: 'operations_admin_shell',
    label: 'Operations Admin Shell',
    description: 'Operations-aware shell with Sage as the default landing surface.',
    homeRouteId: 'chat',
  },
};

function normalizeShellProfileId(value: string | null | undefined): WorkspaceShellProfileId | null {
  if (value === 'personal_shell' || value === 'document_workstation_shell' || value === 'operations_admin_shell') {
    return value;
  }
  return null;
}

export function hasWorkspaceCapability(
  bootstrap: Pick<WorkspaceBootstrapPayload, 'capabilities'>,
  capability: string,
): boolean {
  return bootstrap.capabilities[capability] === true;
}

export function deriveShellProfile(
  bootstrap: WorkspaceBootstrapPayload,
): WorkspaceShellProfile {
  const preferredProfile = normalizeShellProfileId(bootstrap.shellHints.preferredProfile);
  const prefersDocumentWorkstation = hasWorkspaceCapability(bootstrap, 'document_workstation_enabled');
  const prefersOperations = hasWorkspaceCapability(bootstrap, 'workspace_admin_enabled');

  const candidates: WorkspaceShellProfileId[] = [];
  if (preferredProfile) {
    candidates.push(preferredProfile);
  }
  if (prefersDocumentWorkstation) {
    candidates.push('document_workstation_shell');
  }
  if (prefersOperations) {
    candidates.push('operations_admin_shell');
  }
  candidates.push('personal_shell');

  for (const candidate of candidates) {
    if (candidate === 'document_workstation_shell' && !prefersDocumentWorkstation) {
      continue;
    }
    if (candidate === 'operations_admin_shell' && !prefersOperations) {
      continue;
    }
    return SHELL_PROFILE_DEFINITIONS[candidate];
  }

  return SHELL_PROFILE_DEFINITIONS.personal_shell;
}

function routeMatchesProfile(
  definition: WorkspaceNavRouteDefinition,
  shellProfileId: WorkspaceShellProfileId,
): boolean {
  if (!definition.profileIds || definition.profileIds.length === 0) {
    return true;
  }
  return definition.profileIds.includes(shellProfileId);
}

function routeMatchesCapabilities(
  definition: WorkspaceNavRouteDefinition,
  bootstrap: WorkspaceBootstrapPayload,
): boolean {
  return (definition.requiredCapabilities ?? []).every((capability) =>
    hasWorkspaceCapability(bootstrap, capability),
  );
}

function routeVisibleInNavigation(routeId: WorkspaceRouteId): boolean {
  return getWorkspaceNavRouteDefinition(routeId).web.hiddenFromNavigation !== true;
}

export function resolveRouteIdFromHref(
  workspaceId: string,
  href: string | null | undefined,
): WorkspaceRouteId | null {
  if (!href) {
    return null;
  }

  const prefix = `/w/${workspaceId}/`;
  if (!href.startsWith(prefix)) {
    return null;
  }

  const routeSegment = href
    .slice(prefix.length)
    .split(/[?#]/, 1)[0] ?? '';
  const routeId = resolveWorkspaceRouteIdFromSegment(routeSegment);
  return routeId && WORKSPACE_ROUTE_ID_SET.has(routeId) ? routeId : null;
}

export function buildRouteManifest(
  shellProfile: WorkspaceShellProfile,
  bootstrap: WorkspaceBootstrapPayload,
): WorkspaceRouteManifest {
  const workspaceId = bootstrap.workspace.id;
  const allowedRoutes = WORKSPACE_WEB_ROUTE_DEFINITIONS.flatMap((definition) => {
    if (!routeMatchesProfile(definition, shellProfile.id)) {
      return [];
    }
    if (!routeMatchesCapabilities(definition, bootstrap)) {
      return [];
    }

    return [
      {
        id: definition.id,
        label: definition.label,
        href: buildWorkspaceRouteHref(workspaceId, definition.id),
        destinationId: definition.destinationId,
        requiredCapabilities: [...(definition.requiredCapabilities ?? [])],
      } satisfies WorkspaceRouteManifestEntry,
    ];
  });

  const routeIndex = allowedRoutes.reduce<Partial<Record<WorkspaceRouteId, WorkspaceRouteManifestEntry>>>(
    (accumulator, route) => {
      accumulator[route.id] = route;
      return accumulator;
    },
    {},
  );

  const visibleRoutes = allowedRoutes.filter((route) => routeVisibleInNavigation(route.id));
  const visibleRouteIndex = visibleRoutes.reduce<Partial<Record<WorkspaceRouteId, WorkspaceRouteManifestEntry>>>(
    (accumulator, route) => {
      accumulator[route.id] = route;
      return accumulator;
    },
    {},
  );

  const preferredDefaultRouteId =
    resolveRouteIdFromHref(workspaceId, bootstrap.shellHints.defaultRoute) ?? shellProfile.homeRouteId;
  const defaultRoute =
    visibleRouteIndex[preferredDefaultRouteId]?.href
    ?? visibleRouteIndex[shellProfile.homeRouteId]?.href
    ?? visibleRoutes[0]?.href
    ?? routeIndex[shellProfile.homeRouteId]?.href
    ?? allowedRoutes[0]?.href
    ?? buildWorkspaceRouteHref(workspaceId, 'chat');

  const navGroups = WORKSPACE_NAV_DESTINATIONS.flatMap((destination) => {
    const routes = destination.childRouteIds.flatMap((routeId) => {
      const route = routeIndex[routeId];
      return route && routeVisibleInNavigation(route.id) ? [route] : [];
    });

    if (routes.length === 0) {
      return [];
    }

    const destinationDefinition = getWorkspaceNavDestinationDefinition(destination.id);
    const defaultRoute = routes.find((route) => route.id === destinationDefinition.defaultRouteId) ?? routes[0];

    return [
      {
        id: destinationDefinition.id,
        label: destinationDefinition.label,
        iconName: destinationDefinition.iconName,
        href: defaultRoute.href,
        defaultRouteId: defaultRoute.id,
        direct: destinationDefinition.direct,
        routes,
      } satisfies WorkspaceRouteManifestGroup,
    ];
  });

  return {
    shellProfileId: shellProfile.id,
    defaultRoute,
    routeIds: allowedRoutes.map((route) => route.id),
    routeIndex,
    navGroups,
  };
}
