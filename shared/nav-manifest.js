export const WORKSPACE_NAV_DESTINATIONS = [
  {
    id: 'sage',
    label: 'Ask AI',
    iconName: 'message-square',
    defaultRouteId: 'chat',
    childRouteIds: ['chat', 'memory', 'integrations', 'channels', 'tasks', 'artifacts', 'approvals', 'notifications'],
    direct: true,
  },
  {
    id: 'studio',
    label: 'Agents',
    iconName: 'boxes',
    defaultRouteId: 'studio',
    childRouteIds: ['studio', 'inbox', 'deploy', 'studioIntegrations'],
    direct: false,
  },
  {
    id: 'marketplace',
    label: 'Discover',
    iconName: 'compass',
    defaultRouteId: 'marketplace',
    childRouteIds: ['marketplace'],
    direct: true,
  },
  {
    id: 'applications',
    label: 'Applications',
    iconName: 'package',
    defaultRouteId: 'applications',
    childRouteIds: ['applications'],
    direct: true,
  },
  {
    id: 'hardware',
    label: 'Hardware',
    iconName: 'waypoints',
    defaultRouteId: 'hardware',
    childRouteIds: ['hardware'],
    direct: true,
  },
  {
    id: 'gateway',
    label: 'Agent Computer',
    iconName: 'waypoints',
    defaultRouteId: 'gateway',
    childRouteIds: ['gateway', 'gatewayApprovals', 'gatewayActivity'],
    direct: false,
  },
  {
    id: 'settings',
    label: 'Settings',
    iconName: 'sliders-horizontal',
    defaultRouteId: 'settings',
    childRouteIds: ['settings'],
    direct: true,
  },
];

export const WORKSPACE_NAV_DESTINATION_INDEX = WORKSPACE_NAV_DESTINATIONS.reduce((accumulator, destination) => {
  accumulator[destination.id] = destination;
  return accumulator;
}, {});

export const WORKSPACE_WEB_NAV_GROUP_LABELS = WORKSPACE_NAV_DESTINATIONS.reduce((accumulator, destination) => {
  accumulator[destination.id] = destination.label;
  return accumulator;
}, {});

export const WORKSPACE_ROUTE_DEFINITIONS = [
  {
    id: 'chat',
    label: 'Ask AI',
    segment: 'sage',
    legacySegments: ['chat'],
    destinationId: 'sage',
    web: {},
  },
  {
    id: 'memory',
    label: 'Memory',
    segment: 'memory',
    destinationId: 'sage',
    web: {},
  },
  {
    id: 'approvals',
    label: 'Approvals',
    segment: 'approvals',
    destinationId: 'sage',
    web: {
      hiddenFromNavigation: true,
    },
  },
  {
    id: 'artifacts',
    label: 'Library',
    segment: 'artifacts',
    destinationId: 'sage',
    web: {
      hiddenFromNavigation: true,
    },
  },
  {
    id: 'notifications',
    label: 'Activity',
    segment: 'notifications',
    destinationId: 'sage',
    web: {
      hiddenFromNavigation: true,
    },
  },
  {
    id: 'activity',
    label: 'Activity',
    segment: 'activity',
    destinationId: 'sage',
    web: {
      hiddenFromNavigation: true,
    },
  },
  {
    id: 'tasks',
    label: 'Tasks',
    segment: 'tasks',
    destinationId: 'sage',
    web: {},
  },
  {
    id: 'integrations',
    label: 'Connections',
    segment: 'integrations',
    destinationId: 'sage',
    web: {},
  },
  {
    id: 'studio',
    label: 'Agents',
    segment: 'studio',
    legacySegments: ['deployed-agents'],
    destinationId: 'studio',
    requiredCapabilities: ['workspace_admin_enabled'],
    web: {},
  },
  {
    id: 'studioIntegrations',
    label: 'Integrations',
    segment: 'studio-integrations',
    destinationId: 'studio',
    requiredCapabilities: ['workspace_admin_enabled'],
    web: {
      hiddenFromNavigation: true,
    },
  },
  {
    id: 'channels',
    label: 'Connections',
    segment: 'channels',
    destinationId: 'sage',
    web: {
      hiddenFromNavigation: true,
    },
  },
  {
    id: 'inbox',
    label: 'Messages',
    segment: 'inbox',
    legacySegments: ['agents'],
    destinationId: 'studio',
    requiredCapabilities: ['workspace_admin_enabled'],
    web: {},
  },
  {
    id: 'deploy',
    label: 'Go Live',
    segment: 'deploy',
    destinationId: 'studio',
    requiredCapabilities: ['workspace_admin_enabled'],
    web: {},
  },
  {
    id: 'settings',
    label: 'Settings',
    segment: 'settings',
    destinationId: 'settings',
    web: {},
  },
  {
    id: 'marketplace',
    label: 'Discover',
    segment: 'marketplace',
    destinationId: 'marketplace',
    web: {},
  },
  {
    id: 'applications',
    label: 'Applications',
    segment: 'applications',
    destinationId: 'applications',
    requiredCapabilities: ['workspace_admin_enabled'],
    web: {},
  },
  {
    id: 'hardware',
    label: 'Hardware',
    segment: 'hardware',
    destinationId: 'hardware',
    web: {},
  },
  {
    id: 'gateway',
    label: 'Agent Computer',
    segment: 'gateway',
    destinationId: 'gateway',
    web: {},
  },
  {
    id: 'gatewayApprovals',
    label: 'Approvals',
    segment: 'gateway-approvals',
    destinationId: 'gateway',
    web: {
      hiddenFromNavigation: true,
    },
  },
  {
    id: 'gatewayActivity',
    label: 'Computer Activity',
    segment: 'gateway-activity',
    destinationId: 'gateway',
    web: {
      hiddenFromNavigation: true,
    },
  },
];

export const WORKSPACE_WEB_ROUTE_DEFINITIONS = [...WORKSPACE_ROUTE_DEFINITIONS];

export const WORKSPACE_ROUTE_ID_SET = new Set(
  WORKSPACE_ROUTE_DEFINITIONS.map((definition) => definition.id),
);

export const WORKSPACE_ROUTE_DEFINITION_INDEX = WORKSPACE_ROUTE_DEFINITIONS.reduce((accumulator, definition) => {
  accumulator[definition.id] = definition;
  return accumulator;
}, {});

const WORKSPACE_ROUTE_SEGMENT_INDEX = WORKSPACE_ROUTE_DEFINITIONS.reduce((accumulator, definition) => {
  accumulator[definition.segment] = definition.id;
  for (const legacySegment of definition.legacySegments ?? []) {
    accumulator[legacySegment] = definition.id;
  }
  return accumulator;
}, {});

export function getWorkspaceNavRouteDefinition(routeId) {
  return WORKSPACE_ROUTE_DEFINITION_INDEX[routeId];
}

export function getWorkspaceNavDestinationDefinition(destinationId) {
  return WORKSPACE_NAV_DESTINATION_INDEX[destinationId];
}

export function getWorkspaceDestinationRouteDefinitions(destinationId) {
  return getWorkspaceNavDestinationDefinition(destinationId).childRouteIds.map((routeId) =>
    getWorkspaceNavRouteDefinition(routeId),
  );
}

export function buildWorkspaceRouteHref(workspaceId, routeId) {
  const definition = getWorkspaceNavRouteDefinition(routeId);
  return `/w/${encodeURIComponent(workspaceId)}/${definition.segment}`;
}

export function resolveWorkspaceRouteIdFromSegment(segment) {
  if (!segment) {
    return null;
  }

  const normalizedSegment = segment
    .trim()
    .replace(/^\/+/, '')
    .replace(/\/+$/, '');

  if (!normalizedSegment) {
    return null;
  }

  if (normalizedSegment.startsWith('applications/')) {
    return 'applications';
  }

  return WORKSPACE_ROUTE_SEGMENT_INDEX[normalizedSegment] ?? null;
}
