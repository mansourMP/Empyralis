import { NextRequest } from 'next/server';

import { forwardControlPlaneRequest } from '@/lib/server/control-plane-proxy';

export async function GET(
  request: NextRequest,
  context: { params: Promise<{ workspaceId: string }> },
) {
  const { workspaceId } = await context.params;
  return forwardControlPlaneRequest(
    request,
    `/api/workspaces/${encodeURIComponent(workspaceId)}/channel-operations`,
    { method: 'GET' },
  );
}
