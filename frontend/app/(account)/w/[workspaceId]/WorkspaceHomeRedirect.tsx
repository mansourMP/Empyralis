'use client';

import { useEffect } from 'react';
import { useRouter } from 'next/navigation';

import { useAccountShell } from '@/lib/shell/account-shell-context';
import { useWorkspaceBoundary } from '@/lib/workspace/workspace-boundary';
import { resolveRouteIdFromHref } from '@/lib/workspace/workspace-shell';

export function WorkspaceHomeRedirect({
  workspaceId,
}: {
  workspaceId: string;
}) {
  const router = useRouter();
  const { actions } = useAccountShell();
  const { routeManifest, canAccessRoute } = useWorkspaceBoundary();
  const rememberedRoute = actions.resolveWorkspaceHref(workspaceId);
  const rememberedRouteId = resolveRouteIdFromHref(workspaceId, rememberedRoute);
  // Phase UC: Fleet Home is the new workspace landing page.
  // Override routeManifest.defaultRoute to land on /fleet instead of /sage.
  const fleetRoute = `/w/${encodeURIComponent(workspaceId)}/fleet`;
  const nextRoute =
    (rememberedRouteId && canAccessRoute(rememberedRouteId) && rememberedRouteId !== 'chat'
      ? routeManifest.routeIndex[rememberedRouteId]?.href ?? null
      : null)
    ?? fleetRoute;

  useEffect(() => {
    router.replace(nextRoute);
  }, [nextRoute, router]);

  return (
    <WorkspaceScopeRedirectMessage workspaceId={workspaceId} nextRoute={nextRoute} />
  );
}

function WorkspaceScopeRedirectMessage({
  workspaceId,
  nextRoute,
}: {
  workspaceId: string;
  nextRoute: string;
}) {
  return (
    <main className="app-page-message">
      <div className="app-page-message__content">
        <h1 className="app-page-message__title">Opening workspace</h1>
        <p className="app-page-message__body">
          Resolving the safe entry route for <code>{workspaceId}</code>.
        </p>
        <p className="app-page-message__meta">
          Next route: <code>{nextRoute}</code>
        </p>
      </div>
    </main>
  );
}
