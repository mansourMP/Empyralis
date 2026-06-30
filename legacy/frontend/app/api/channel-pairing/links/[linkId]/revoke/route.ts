import { NextRequest } from 'next/server';

import { forwardControlPlaneRequest } from '@/lib/server/control-plane-proxy';

export async function POST(
  request: NextRequest,
  { params }: { params: Promise<{ linkId: string }> },
) {
  const { linkId } = await params;
  return forwardControlPlaneRequest(
    request,
    `/api/v1/auth/channel-pairing/links/${encodeURIComponent(linkId)}/revoke`,
    { method: 'POST' },
  );
}
