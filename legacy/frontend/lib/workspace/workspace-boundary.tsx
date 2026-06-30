'use client';

import {
  type PropsWithChildren,
  createContext,
  useContext,
  useMemo,
} from 'react';

import {
  type WorkspaceBootstrapPayload,
  createWorkstationKernelKey,
} from '@/lib/workspace/workspace-bootstrap';
import {
  buildRouteManifest,
  deriveShellProfile,
  hasWorkspaceCapability,
  type WorkspaceRouteId,
  type WorkspaceRouteManifest,
  type WorkspaceShellProfile,
} from '@/lib/workspace/workspace-shell';
import { WorkstationKernelProvider } from '@/lib/workspace/workspace-services';

export type WorkspaceBoundaryState = {
  workspaceId: string;
  boundaryKey: string;
  kernelKey: string;
  shellProfileId: string;
  shellProfile: WorkspaceShellProfile;
  routeManifest: WorkspaceRouteManifest;
  bootstrap: WorkspaceBootstrapPayload;
  hasCapability: (capability: string) => boolean;
  canAccessRoute: (routeId: WorkspaceRouteId) => boolean;
};

type WorkspaceBoundaryInstanceProps = Omit<
  WorkspaceBoundaryState,
  'hasCapability' | 'canAccessRoute'
>;

const WorkspaceBoundaryContext = createContext<WorkspaceBoundaryState | null>(null);

export function WorkspaceBoundary({
  workspaceId,
  bootstrap,
  children,
}: PropsWithChildren<{
  workspaceId: string;
  bootstrap: WorkspaceBootstrapPayload;
}>) {
  const shellProfile = deriveShellProfile(bootstrap);
  const routeManifest = buildRouteManifest(shellProfile, bootstrap);
  const shellProfileId = shellProfile.id;
  const kernelKey = createWorkstationKernelKey({
    accountId: bootstrap.account.id,
    tenantId: bootstrap.workspace.tenantId,
    workspaceId,
    membershipVersion: bootstrap.membership.version,
    shellProfileId,
  });
  const boundaryKey = kernelKey;

  return (
    <WorkspaceBoundaryInstance
      key={boundaryKey}
      workspaceId={workspaceId}
      boundaryKey={boundaryKey}
      kernelKey={kernelKey}
      shellProfileId={shellProfileId}
      shellProfile={shellProfile}
      routeManifest={routeManifest}
      bootstrap={bootstrap}
    >
      {children}
    </WorkspaceBoundaryInstance>
  );
}

function WorkspaceBoundaryInstance({
  workspaceId,
  boundaryKey,
  kernelKey,
  shellProfileId,
  shellProfile,
  routeManifest,
  bootstrap,
  children,
}: PropsWithChildren<WorkspaceBoundaryInstanceProps>) {
  const value = useMemo<WorkspaceBoundaryState>(
    () => ({
      workspaceId,
      boundaryKey,
      kernelKey,
      shellProfileId,
      shellProfile,
      routeManifest,
      bootstrap,
      hasCapability: (capability) => hasWorkspaceCapability(bootstrap, capability),
      canAccessRoute: (routeId) => Boolean(routeManifest.routeIndex[routeId]),
    }),
    [bootstrap, boundaryKey, kernelKey, routeManifest, shellProfile, shellProfileId, workspaceId],
  );

  return (
    <WorkspaceBoundaryContext.Provider value={value}>
      <WorkstationKernelProvider kernelKey={kernelKey} shellProfileId={shellProfileId} bootstrap={bootstrap}>
        <div
          data-workspace-boundary={boundaryKey}
          data-workstation-kernel={kernelKey}
          data-workspace-id={workspaceId}
          data-shell-profile={shellProfileId}
        >
          {children}
        </div>
      </WorkstationKernelProvider>
    </WorkspaceBoundaryContext.Provider>
  );
}

export function useWorkspaceBoundary(): WorkspaceBoundaryState {
  const value = useContext(WorkspaceBoundaryContext);
  if (!value) {
    throw new Error('useWorkspaceBoundary must be used inside WorkspaceBoundary.');
  }
  return value;
}

export function useWorkspaceCapability(capability: string): boolean {
  return useWorkspaceBoundary().hasCapability(capability);
}

export function useWorkspaceRouteManifest(): WorkspaceRouteManifest {
  return useWorkspaceBoundary().routeManifest;
}
