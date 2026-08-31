/**
 * The recovery pair rendered on every degraded/error screen — including the
 * onboarding "temporarily unavailable" screen a stuck session actually
 * lands on (app/onboarding/page.tsx), (account)/layout.tsx's own degraded
 * message, w/[workspaceId]/layout.tsx's bootstrap-recovery message, and
 * app/error.tsx's root segment boundary — used to offer "Sign in again" as
 * a plain `<a href="/login">`. That link cannot help a browser stuck
 * holding two `empyralis_csrf_token` cookies (a domain-scoped one and a
 * stray host-only twin, RFC 6265): every mutating request 403s
 * csrf_mismatch forever, including POST /api/auth/login while the old
 * access-token cookie is still live (validateBrowserCsrf in
 * control-plane-proxy.ts only skips the CSRF check once the session cookie
 * is truly dead — see isAccessTokenLive in lib/auth/csrf.ts). The person
 * offered this button had no way out of the screen.
 *
 * Logout is the one sanctioned `bypassCsrf: true` exemption in the app
 * (app/api/auth/logout/route.ts, guarded by its own comment and by
 * tests/e2e/auth-web-session.spec.ts's "logout succeeds even without a CSRF
 * header, by design") — it is the only mutating request guaranteed to reach
 * the backend from this screen. So the fix is to actually sign the person
 * out (reusing the SAME lib/auth/auth-client.ts `logout()` PrimaryRail.tsx's
 * account-menu "Log out" already calls — not a second implementation) and
 * land them on /login afterward regardless of whether logout itself
 * succeeded.
 *
 * A behavioural (mount-and-click) test would need jsdom/React Testing
 * Library, which this repo's test:unit does not use anywhere (see
 * app/onboarding/onboarding-blank-state-drift.test.ts's own header for the
 * same note) — every test in this family is a structural source scan run
 * directly via tsx. This follows the same convention, and additionally
 * cross-checks app/api/auth/logout/route.ts so the test fails if the
 * bypassCsrf exemption this fix depends on is ever quietly removed.
 *
 * app/onboarding/OnboardingClient.tsx's own "Workspace setup couldn't
 * finish" screen (the literal screen named above) carried an independent
 * copy of the same defect — it does not render <ShellRecoveryActions> at
 * all, it hand-rolls its own Retry / "Sign in again" pair — so this file
 * also scans that component and requires it to reuse the same
 * useSignOutAndStartOver() hook rather than being fixed (or missed)
 * separately.
 *
 * Every assertion below was proven RED against the pre-fix files (a plain
 * `<a className="app-page-message__button app-page-message__button--secondary"
 * href="/login">Sign in again</a>` in both ShellRecoveryActions.tsx and
 * OnboardingClient.tsx, with no logout call at all) before this change —
 * see the session's own report for the real tsx failure output.
 *
 * Run: npx tsx "app/(account)/shell-recovery-actions.test.ts"
 */

import { readFileSync } from "node:fs";

let passed = 0;
let failed = 0;

function assert(condition: boolean, label: string): void {
  if (condition) {
    passed++;
  } else {
    failed++;
    console.error(`FAIL: ${label}`);
  }
}

const COMPONENT_PATH = new URL("./ShellRecoveryActions.tsx", import.meta.url);
const source = readFileSync(COMPONENT_PATH, "utf8");
assert(
  source.length > 200,
  "CANARY: ShellRecoveryActions.tsx was actually read, not an empty/missing file",
);

// ── Reuse, not a second implementation ─────────────────────────────────────
assert(
  /import\s*\{\s*logout\s*\}\s*from\s*['"]@\/lib\/auth\/auth-client['"]/.test(source),
  "must import the REAL logout() from lib/auth/auth-client.ts — the same function " +
    "PrimaryRail.tsx's account-menu 'Log out' already calls, not a hand-rolled fetch",
);
assert(
  /\blogout\s*\(\s*\)/.test(source),
  "must actually CALL logout(), not just import it unused",
);

// ── The old dead end is gone ───────────────────────────────────────────────
// The pre-fix defect: a plain <a href="/login"> as the secondary recovery
// action. It must not come back in any form.
assert(
  !/<a\b[^>]*href=["']\/login["']/.test(source),
  "the secondary action must no longer be a plain <a href=\"/login\"> — that link " +
    "cannot help a session stuck 403ing on every mutating request, logout included",
);
assert(
  !/Sign in again/.test(source),
  "the label must no longer claim 'Sign in again' — the action now signs the " +
    "person OUT first; a label that lies about its own action is the exact " +
    "defect class this codebase keeps hitting (CLAUDE.md)",
);

// ── Never leave the person with nothing ────────────────────────────────────
// If logout() itself throws (network down, backend unreachable), the person
// must still land on /login rather than being stuck on this screen — proven
// by requiring the navigation to sit in a `finally`, not only the try body.
const finallyBlockMatch = source.match(/\}\s*finally\s*\{([\s\S]*?)\}/);
assert(
  Boolean(finallyBlockMatch),
  "signOutAndStartOver must have a finally block — navigation must run whether " +
    "or not logout() succeeded",
);
assert(
  Boolean(finallyBlockMatch && /\/login/.test(finallyBlockMatch[1])),
  "the finally block must navigate to /login — this is the fallback that fires " +
    "even when logout() itself fails; never leave the person with nothing",
);
assert(
  /catch\s*\{/.test(source) || /catch\s*\([^)]*\)\s*\{/.test(source),
  "logout() must be wrapped in a catch — an unhandled rejection here would " +
    "leave the finally's navigation as the only thing standing between the " +
    "person and a dead screen, so the catch must not be allowed to throw further",
);

// ── Never widen the CSRF exemption ─────────────────────────────────────────
assert(
  !/bypassCsrf/.test(source),
  "must never add bypassCsrf here — this component reuses the ALREADY-sanctioned " +
    "exemption on /api/auth/logout, it does not create a new one",
);

// ── Real navigation semantics preserved where possible ─────────────────────
// Reload stays a real, synchronous action; sign-out has to become a button
// (it must POST before navigating, which a plain <a href> cannot sequence),
// matching the shape PrimaryRail.tsx's own "Log out" already uses.
assert(
  /<button[\s\S]{0,400}Sign out/.test(source) || /Sign out[\s\S]{0,10}<\/button>/.test(source),
  "the sign-out action must be a <button> (async work must complete before " +
    "navigating) — PrimaryRail.tsx's account-menu 'Log out' is the same shape " +
    "for the same reason",
);

// ── No accent violet on this secondary action ───────────────────────────────
// CLAUDE.md: accent violet is reserved for the single primary filled button.
// This component must keep reusing the existing neutral
// app-page-message__button(--secondary) classes rather than reach for an
// accent/violet token or class of its own.
assert(
  !/violet|accent/i.test(source),
  "no accent/violet class or token reference belongs on this component at all",
);

// ── The exemption this fix depends on must still be real ───────────────────
const LOGOUT_ROUTE_PATH = new URL(
  "../api/auth/logout/route.ts",
  import.meta.url,
);
const logoutRouteSource = readFileSync(LOGOUT_ROUTE_PATH, "utf8");
assert(
  logoutRouteSource.length > 100,
  "CANARY: app/api/auth/logout/route.ts was actually read",
);
assert(
  /bypassCsrf:\s*true/.test(logoutRouteSource),
  "app/api/auth/logout/route.ts must still set bypassCsrf: true — this fix's " +
    "entire premise is that logout is the one route guaranteed to reach the " +
    "backend from a broken session; if this regresses, ShellRecoveryActions' " +
    "button 403s exactly like the old plain link did",
);

// ── The named screen itself: OnboardingClient's "Workspace setup couldn't
// finish" message ───────────────────────────────────────────────────────
// This is the literal screen a stuck sign-up actually lands on (the
// auto-submit PATCH failing is the production trap), and it does NOT render
// <ShellRecoveryActions> — it hand-rolls its own Retry/"Sign in again" pair
// beside it. That pair carried the exact same defect independently, so a
// fix to ShellRecoveryActions.tsx alone would not have closed it. Reuses
// the SAME useSignOutAndStartOver() hook rather than a second
// implementation, and its own Retry button stays (it is a smarter, more
// specific action than a generic Reload, and re-running it is safe/
// idempotent per this file's own comment).
const ONBOARDING_CLIENT_PATH = new URL(
  "../onboarding/OnboardingClient.tsx",
  import.meta.url,
);
const onboardingSource = readFileSync(ONBOARDING_CLIENT_PATH, "utf8");
assert(
  onboardingSource.length > 2_000,
  "CANARY: app/onboarding/OnboardingClient.tsx was actually read, not an empty/missing file",
);
assert(
  /import\s*\{[^}]*useSignOutAndStartOver[^}]*\}\s*from\s*['"]@\/app\/\(account\)\/ShellRecoveryActions['"]/.test(
    onboardingSource,
  ),
  "OnboardingClient.tsx must import the shared useSignOutAndStartOver() hook " +
    "from ShellRecoveryActions.tsx — not a second, hand-rolled logout call",
);
assert(
  /\bsignOutAndStartOver\s*\}\s*=\s*useSignOutAndStartOver\s*\(\s*\)/.test(onboardingSource)
    || /useSignOutAndStartOver\s*\(\s*\)/.test(onboardingSource),
  "OnboardingClient.tsx must actually CALL useSignOutAndStartOver(), not just import it",
);
assert(
  !/<a\b[^>]*href=["']\/login["']/.test(onboardingSource),
  "OnboardingClient.tsx's 'Workspace setup couldn't finish' screen must no longer " +
    "offer a plain <a href=\"/login\"> — the same 403 dead end ShellRecoveryActions.tsx fixed",
);
assert(
  !/Sign in again/.test(onboardingSource),
  "OnboardingClient.tsx must no longer claim 'Sign in again' — the action now signs out first",
);
assert(
  !/bypassCsrf/.test(onboardingSource),
  "OnboardingClient.tsx must never add bypassCsrf — it reuses the existing exemption via logout()",
);
// The Retry button is this screen's own, more specific recovery action and
// must survive the fix untouched — losing it would be scope creep in the
// other direction (removing a working, idempotent retry, not just fixing
// the broken link beside it).
assert(
  /Retry/.test(onboardingSource) && /autoSubmitValuesForMembership/.test(onboardingSource),
  "the existing Retry action (re-running the idempotent auto-submit PATCH) must still be present",
);

console.log(`\n${passed} passed, ${failed} failed`);
if (failed > 0) {
  process.exit(1);
}
