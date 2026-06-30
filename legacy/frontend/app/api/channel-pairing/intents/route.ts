import { NextRequest } from 'next/server';

import { forwardControlPlaneRequest } from '@/lib/server/control-plane-proxy';

export async function POST(request: NextRequest) {
  return forwardControlPlaneRequest(
    request,
    '/api/v1/auth/channel-pairing/intents',
    { method: 'POST' },
  );
}
