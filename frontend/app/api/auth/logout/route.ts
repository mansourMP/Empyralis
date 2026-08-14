import type { NextRequest } from 'next/server';

import { AUTH_REQUEST_TIMEOUT_MS } from '@/lib/auth/auth-timeouts';
import { forwardControlPlaneRequest } from '@/lib/server/control-plane-proxy';

export const dynamic = 'force-dynamic';

export async function POST(request: NextRequest) {
  // Logout is the escape hatch for a stuck session — it must always reach
  // the backend and clear cookies, not itself 403 on the CSRF check it
  // exists to help recover from. Forging a cross-site logout only logs the
  // victim out, which is not a meaningful attack.
  //
  // This is a deliberate trade-off, not an oversight — guarded by
  // frontend/tests/e2e/auth-web-session.spec.ts's "logout succeeds even
  // without a CSRF header, by design" test, sitting right next to a second
  // test that proves a genuinely CSRF-protected route (verify-email/resend)
  // still fails closed. If this ever needs to change, update that test's
  // expectation deliberately along with it — don't let a future "fix" quietly
  // route around the intent recorded here.
  return forwardControlPlaneRequest(request, '/api/v1/auth/logout', {
    timeoutMs: AUTH_REQUEST_TIMEOUT_MS,
    bypassCsrf: true,
  });
}
