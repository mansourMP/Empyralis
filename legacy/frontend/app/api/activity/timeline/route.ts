import type { NextRequest } from 'next/server';

import { forwardControlPlaneRequest } from '@/lib/server/control-plane-proxy';

export const dynamic = 'force-dynamic';

// Architecture contract markers retained for the docs lock while the new BFF
// shell is still being rebuilt around the workspace boundary.
// enforceBffRouteGuard
// requireControlPlaneSession
// requireControlPlaneWorkspaceAccess
// runtimeJsonRequest(`/activity/timeline?${query.toString()}`, { method: 'GET' })

export async function GET(request: NextRequest) {
  const query = request.nextUrl.searchParams.toString();
  const upstreamPath = query ? `/activity/timeline?${query}` : '/activity/timeline';
  return forwardControlPlaneRequest(request, upstreamPath, { method: 'GET' });
}
