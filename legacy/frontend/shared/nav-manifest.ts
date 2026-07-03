export type WorkspaceShellProfileId =
  | 'personal_shell'
  | 'document_workstation_shell'
  | 'operations_admin_shell';

export type WorkspaceNavDestinationId =
  | 'sage'
  | 'studio'
  | 'marketplace'
  | 'applications'
  | 'hardware'
  | 'gateway'
  | 'settings';

export type WorkspaceRouteId =
  | 'chat'
  | 'memory'
  | 'approvals'
  | 'artifacts'
  | 'notifications'
  | 'activity'
  | 'tasks'
  | 'integrations'
  | 'studioIntegrations'
  | 'studio'
  | 'channels'
  | 'inbox'
  | 'deploy'
  | 'marketplace'
  | 'applications'
  | 'hardware'
  | 'gateway'
  | 'gatewayApprovals'
  | 'gatewayActivity'
  | 'settings';

export type WorkspaceNavIconName =
  | 'message-square'
  | 'boxes'
  | 'compass'
  | 'package'
  | 'waypoints'
  | 'sliders-horizontal';

export type WorkspaceNavDestinationDefinition = {
  id: WorkspaceNavDestinationId;
  label: string;
  iconName: WorkspaceNavIconName;
  defaultRouteId: WorkspaceRouteId;
  childRouteIds: readonly WorkspaceRouteId[];
  direct: boolean;
};

export type WorkspaceNavRouteDefinition = {
  id: WorkspaceRouteId;
  label: string;
  segment: string;
  legacySegments?: readonly string[];
  destinationId: WorkspaceNavDestinationId;
  requiredCapabilities?: readonly string[];
  profileIds?: readonly WorkspaceShellProfileId[];
  web: {
    hiddenFromNavigation?: boolean;
  };
};

import {
  WORKSPACE_NAV_DESTINATIONS as runtimeWorkspaceNavDestinations,
  WORKSPACE_NAV_DESTINATION_INDEX as runtimeWorkspaceNavDestinationIndex,
  WORKSPACE_ROUTE_DEFINITIONS as runtimeWorkspaceRouteDefinitions,
  WORKSPACE_ROUTE_DEFINITION_INDEX as runtimeWorkspaceRouteDefinitionIndex,
  WORKSPACE_ROUTE_ID_SET as runtimeWorkspaceRouteIdSet,
  WORKSPACE_WEB_NAV_GROUP_LABELS as runtimeWorkspaceWebNavGroupLabels,
  WORKSPACE_WEB_ROUTE_DEFINITIONS as runtimeWorkspaceWebRouteDefinitions,
  buildWorkspaceRouteHref as runtimeBuildWorkspaceRouteHref,
  getWorkspaceNavDestinationDefinition as runtimeGetWorkspaceNavDestinationDefinition,
  getWorkspaceDestinationRouteDefinitions as runtimeGetWorkspaceDestinationRouteDefinitions,
  getWorkspaceNavRouteDefinition as runtimeGetWorkspaceNavRouteDefinition,
  resolveWorkspaceRouteIdFromSegment as runtimeResolveWorkspaceRouteIdFromSegment,
} from './nav-manifest.js';

export const WORKSPACE_NAV_DESTINATIONS =
  runtimeWorkspaceNavDestinations as readonly WorkspaceNavDestinationDefinition[];
export const WORKSPACE_NAV_DESTINATION_INDEX =
  runtimeWorkspaceNavDestinationIndex as Record<WorkspaceNavDestinationId, WorkspaceNavDestinationDefinition>;
export const WORKSPACE_WEB_NAV_GROUP_LABELS =
  runtimeWorkspaceWebNavGroupLabels as Record<WorkspaceNavDestinationId, string>;
export const WORKSPACE_ROUTE_DEFINITIONS =
  runtimeWorkspaceRouteDefinitions as readonly WorkspaceNavRouteDefinition[];
export const WORKSPACE_WEB_ROUTE_DEFINITIONS =
  runtimeWorkspaceWebRouteDefinitions as WorkspaceNavRouteDefinition[];
export const WORKSPACE_ROUTE_ID_SET =
  runtimeWorkspaceRouteIdSet as Set<WorkspaceRouteId>;
export const WORKSPACE_ROUTE_DEFINITION_INDEX =
  runtimeWorkspaceRouteDefinitionIndex as Record<WorkspaceRouteId, WorkspaceNavRouteDefinition>;

export function getWorkspaceNavRouteDefinition(routeId: WorkspaceRouteId): WorkspaceNavRouteDefinition {
  return runtimeGetWorkspaceNavRouteDefinition(routeId) as WorkspaceNavRouteDefinition;
}

export function getWorkspaceNavDestinationDefinition(
  destinationId: WorkspaceNavDestinationId,
): WorkspaceNavDestinationDefinition {
  return runtimeGetWorkspaceNavDestinationDefinition(destinationId) as WorkspaceNavDestinationDefinition;
}

export function getWorkspaceDestinationRouteDefinitions(
  destinationId: WorkspaceNavDestinationId,
): WorkspaceNavRouteDefinition[] {
  return runtimeGetWorkspaceDestinationRouteDefinitions(destinationId) as WorkspaceNavRouteDefinition[];
}

export function buildWorkspaceRouteHref(workspaceId: string, routeId: WorkspaceRouteId): string {
  return runtimeBuildWorkspaceRouteHref(workspaceId, routeId);
}

export function resolveWorkspaceRouteIdFromSegment(
  segment: string | null | undefined,
): WorkspaceRouteId | null {
  return runtimeResolveWorkspaceRouteIdFromSegment(segment) as WorkspaceRouteId | null;
}
