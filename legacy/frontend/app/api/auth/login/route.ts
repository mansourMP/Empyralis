import type { NextRequest } from 'next/server';

import { AUTH_REQUEST_TIMEOUT_MS } from '@/lib/auth/auth-timeouts';
import { forwardControlPlaneRequest } from '@/lib/server/control-plane-proxy';

export const dynamic = 'force-dynamic';

export async function POST(request: NextRequest) {
  return forwardControlPlaneRequest(request, '/api/v1/auth/login', {
    timeoutMs: AUTH_REQUEST_TIMEOUT_MS,
  });
}
