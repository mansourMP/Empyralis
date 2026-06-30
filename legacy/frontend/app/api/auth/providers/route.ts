import type { NextRequest } from 'next/server';

import { AUTH_ACCOUNT_SHELL_TIMEOUT_MS } from '@/lib/auth/auth-timeouts';
import { forwardControlPlaneRequest } from '@/lib/server/control-plane-proxy';

export const dynamic = 'force-dynamic';

export async function GET(request: NextRequest) {
  return forwardControlPlaneRequest(request, '/api/v1/auth/providers', {
    method: 'GET',
    timeoutMs: AUTH_ACCOUNT_SHELL_TIMEOUT_MS,
  });
}
