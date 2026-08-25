// One classification drives BOTH the heading and the body of the login
// page's auth notice. Before this file existed, login/page.tsx hardcoded a
// failure title ("Couldn't sign in") on every render of AuthErrorNotice
// while only the BODY came from a separate copy function — so the one
// branch that describes a session that already succeeded ("session not
// ready", fired when login() has already set real cookies and only the
// post-login readiness poll timed out) rendered "Couldn't sign in" over
// "You're signed in". Reproduced live, 2026-08-25: pressing Continue a
// second time went straight into the workspace, proving the first attempt
// had already authenticated. That is the exact shape CLAUDE.md's
// outcome-honesty law forbids — "failed" and "succeeded, but we lost
// track of it" must never share one screen.
//
// A single object naming both the title and the body makes that
// contradiction impossible for a future branch to reintroduce: there is
// nowhere left for a title to be typed independently of the fact it is
// attached to.
export type AuthNoticeTone = 'error' | 'notice';

export interface AuthOutcomeCopy {
  tone: AuthNoticeTone;
  title: string;
  body: string;
}

function fail(title: string, body: string): AuthOutcomeCopy {
  return { tone: 'error', title, body };
}

// fallbackTitle is the caller's default failure heading (e.g. "Couldn't
// sign in" for the sign-in form, "Auth unavailable" for provider-loading
// failures) — used for every branch below that IS a genuine failure and has
// no more specific heading of its own. The one branch that is not a
// failure (session-not-ready) ignores it entirely and supplies its own
// non-alarming title, because a fallback failure heading passed in by the
// caller must never leak onto a success-adjacent outcome.
export function classifyLoginOutcome(error: string, fallbackTitle: string): AuthOutcomeCopy {
  const unwrapped = error
    .trim()
    .replace(/^(login request failed|session readiness failed|authentication request failed):\s*/i, '');
  const normalized = unwrapped.toLowerCase();

  if (normalized.includes('control plane is unavailable') || normalized.includes('fetch failed')) {
    return fail(
      fallbackTitle,
      'This deployment cannot reach the Empyralis control plane. Open the local app or connect this deployment to a reachable backend.',
    );
  }
  if (normalized.includes('google_not_configured')) {
    return fail(fallbackTitle, 'Google sign-in is not configured for this environment yet.');
  }
  if (normalized.includes('google_origin_not_allowed')) {
    return fail(fallbackTitle, 'Google sign-in is restricted to approved Empyralis domains.');
  }
  if (normalized.includes('google_runtime_not_configured')) {
    return fail(fallbackTitle, 'Google sign-in is not fully enabled on the runtime yet. Use email for now.');
  }
  if (normalized.includes('google_rate_limited')) {
    return fail(fallbackTitle, 'Too many Google sign-in attempts. Wait a minute, then try again.');
  }
  // WorkspaceTransportAdapter.redirectToLogin (workspace-services.tsx) lands
  // here after a 401 survives a real refresh attempt — the session is
  // genuinely gone. Named explicitly so the heading matches: "Session
  // expired" is a fact of its own, not the generic sign-in failure.
  if (normalized.includes('session expired') || normalized.includes('refresh token has expired')) {
    return fail('Session expired', 'Your session expired. Sign in again to continue.');
  }
  if (normalized.includes('google_state_invalid') || normalized.includes('google_auth_failed')) {
    return fail(fallbackTitle, 'Google sign-in could not finish. Try again or use email.');
  }
  if (normalized.includes('status 401')) {
    return fail(fallbackTitle, 'Email or password was not accepted.');
  }
  // awaitBrowserAuthReady's own 403 branch (auth-client.ts) — this account
  // genuinely cannot open this workspace. Distinct from the generic
  // 'status 403' branch below (a raw HTTP status string from other
  // callers): this one has already been diagnosed by the readiness poll
  // itself, and it must NOT invite a retry — pressing Continue again can
  // never fix a permissions problem, only re-run the same poll that just
  // failed for the same reason.
  if (normalized.includes('not accessible for this account')) {
    return fail('Workspace not accessible', 'This account does not have access to this workspace. Sign in with an account that does, or ask an owner to invite you.');
  }
  // login() already succeeded and set real session cookies — only the
  // post-login readiness poll (awaitBrowserAuthReady) timed out while the
  // session was STILL WARMING (attempts exhausted, never a diagnosed
  // failure). Nothing failed here; pressing Continue again both
  // re-confirms and re-polls. This must never wear a failure heading or
  // failure styling.
  //
  // Two other things that same poll can throw are handled by OTHER
  // branches above/below and must never reach here: 3 consecutive 401s
  // ('session expired' branch — the session is actually gone, not
  // warming) and a 403 ('not accessible for this account', just above —
  // a permissions problem, not warming). Only the exhausted-attempts
  // shape belongs to this branch.
  if (
    normalized.includes('session not ready')
    || normalized.includes('auth readiness check did not complete')
    || normalized.includes('auth readiness check did not recover from status')
  ) {
    return {
      tone: 'notice',
      title: 'Almost there',
      body: "You're signed in — this device just needs a moment to finish. Press Continue again.",
    };
  }
  if (normalized.includes('csrf')) {
    return fail(
      fallbackTitle,
      "Your session looks out of date. Clear this site's cookies in your browser, then try again.",
    );
  }
  if (normalized.includes('status 403')) {
    return fail(
      fallbackTitle,
      'This session is not allowed to open the workspace yet. Sign in again or use an allowed account.',
    );
  }
  if (normalized.includes('status 404')) {
    return fail(fallbackTitle, 'The auth route is not available in this environment.');
  }
  if (normalized.includes('status 429')) {
    return fail(fallbackTitle, 'Too many sign-in attempts. Wait a minute, then try again.');
  }
  if (
    /(?:status\s*)?5\d\d/.test(normalized)
    || /internal server|bad gateway|service unavailable|gateway timeout|temporarily unavailable|warming up/.test(normalized)
  ) {
    return fail(fallbackTitle, 'The auth service is warming up or unavailable. Try again in a moment.');
  }
  if (normalized.includes('password') || normalized.includes('credential') || normalized.includes('invalid')) {
    return fail(fallbackTitle, 'Email or password was not accepted.');
  }
  return fail(fallbackTitle, 'Authentication could not finish. Try again when ready.');
}
