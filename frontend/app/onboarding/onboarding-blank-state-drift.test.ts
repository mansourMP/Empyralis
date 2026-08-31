/**
 * Guards the "white page" onboarding trap (MAN, 2026-08-31): every
 * brand-new sign-up that reached /onboarding landed on a component that
 * rendered NOTHING for two different states — `if (state.status !==
 * 'authenticated') return null;` up front, and an unconditional `return
 * null;` after the auto-submit effect at the bottom, regardless of whether
 * that auto-submit PATCH was still in flight, had just failed (the
 * production case — a CSRF 403, see ../../lib/auth/csrf-fallback-drift.test.ts
 * for that half of this fix), or hadn't fired yet. No chrome, no message,
 * no way out — a pure white screen with no diagnosis and no recovery.
 *
 * CLAUDE.md's outcome-honesty law: "still loading", "your session ended /
 * could not be loaded", and "the action failed" are different facts and
 * must never collapse into one signal — a bare `return null` is the
 * sharpest possible collapse, since it cannot even be distinguished from
 * "this component intentionally renders nothing here" by anyone reading the
 * screen.
 *
 * This also caught a SECOND, latent bug the blank-render fix could not be
 * done correctly without touching: the original `useEffect` call sat AFTER
 * two early `return`s, which is a React Rules-of-Hooks violation — this
 * component would render a DIFFERENT number of hooks on a render where
 * state.status or membership differs from the previous render, which is
 * exactly the "Rendered fewer hooks than expected" crash shape. There was
 * no test for it because behaviourally it only fires when those values
 * actually change between renders — a source scan that checks the effect
 * runs before any early return catches the shape unconditionally.
 *
 * A behavioural (mount-and-inspect-the-DOM) test would need jsdom/React
 * Testing Library, which this repo's test:unit does not use anywhere
 * (verified: no jsdom/testing-library dependency in package.json) — every
 * existing frontend unit test in this family is a structural source scan
 * run directly via tsx (see agent-detail-status-honesty.test.ts,
 * outcome-honesty-drift.test.ts). This follows the same convention.
 *
 * Every assertion below was proven RED against the pre-fix file (both bare
 * `return null`s, useEffect after the early returns) before this change —
 * see the session's own report for the exact `tsx` failure output.
 *
 * Run: npx tsx app/onboarding/onboarding-blank-state-drift.test.ts
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

const SOURCE_PATH = new URL("./OnboardingClient.tsx", import.meta.url);
const source = readFileSync(SOURCE_PATH, "utf8");
assert(source.length > 2_000, "CANARY: OnboardingClient.tsx was actually read, not an empty/missing file");

// ── No bare `return null` anywhere — every state renders something real ──

assert(
  !/\breturn null;/.test(source),
  "no bare `return null;` remains in this file — every reachable state " +
    "(degraded session, no workspace, auto-submit failed, auto-submit in " +
    "flight) must render real chrome, not a pure white screen",
);

// ── State 1: the account-shell session context reports something other
// than 'authenticated' — a real, recoverable message, matching the SAME
// idiom this component's own sibling (OnboardingPage's degraded-session
// branch, and app/(account)/layout.tsx) already uses for the identical fact.

const degradedBranchMatch = source.match(
  /if \(state\.status !== 'authenticated'\) \{([\s\S]*?)\n {2}\}/,
);
assert(Boolean(degradedBranchMatch), "a `state.status !== 'authenticated'` branch still exists");
const degradedBranchBody = degradedBranchMatch?.[1] ?? "";
assert(
  /app-page-message/.test(degradedBranchBody),
  "the degraded-session branch renders the app-page-message family (not a bespoke visual language)",
);
assert(
  /<ShellRecoveryActions/.test(degradedBranchBody),
  "the degraded-session branch offers a real recovery action (ShellRecoveryActions — Reload / Sign out and start over), not just a message with no way out",
);

// ── State 2: the auto-submit PATCH failed — the error is actually shown,
// with a retry path, distinct from both the degraded-session message above
// and the in-flight message below (three different facts, three different
// renders).

const errorBranchMatch = source.match(/if \(errorMessage\) \{([\s\S]*?)\n {2}\}/);
assert(Boolean(errorBranchMatch), "an `if (errorMessage)` branch exists");
const errorBranchBody = errorBranchMatch?.[1] ?? "";
assert(
  /\{errorMessage\}/.test(errorBranchBody),
  "the failed-submit branch actually renders the real errorMessage text, not a generic string that can't distinguish one failure from another",
);
assert(
  /onClick=\{[\s\S]*?handleSubmit\(/.test(errorBranchBody),
  "the failed-submit branch offers a real retry that calls handleSubmit again — this PATCH is idempotent and safe to retry, so silence here would be a second, avoidable trap",
);
// A plain href="/login" here used to be the recovery option offered "in
// case the real cause was session-shaped rather than a one-off request
// failure" -- but that link cannot actually help a session-shaped failure:
// POST /api/auth/login is CSRF-gated, and a browser stuck with a duplicate
// empyralis_csrf_token (domain-scoped + stray host-only twin, RFC 6265)
// 403s on it exactly like every other mutating request while the old
// access-token cookie is still live (see ShellRecoveryActions.tsx's own
// header comment). The fix reuses that file's useSignOutAndStartOver()
// hook here instead of a second, independently-broken implementation --
// signing out is the one guaranteed-to-succeed escape hatch, not a plain
// link to a page that would 403 the same way.
assert(
  /useSignOutAndStartOver/.test(source),
  "must reuse ShellRecoveryActions.tsx's useSignOutAndStartOver() hook — not a " +
    "second, hand-rolled sign-out/logout implementation",
);
assert(
  /onClick=\{signOutAndStartOver\}/.test(errorBranchBody),
  "the failed-submit branch also offers a real sign-out action, in case the real " +
    "cause was session-shaped rather than a one-off request failure — a plain " +
    "href=\"/login\" cannot help there (see this file's own comment)",
);
assert(
  !/href="\/login"/.test(errorBranchBody),
  "no plain href=\"/login\" link remains in the failed-submit branch — it cannot " +
    "help a session-shaped failure and must not come back in any form",
);

// ── State 3: nothing has failed yet (either about to auto-submit, or the
// PATCH is still in flight) — an honest in-progress message, not a blank
// screen mistaken for "done" or "broken".

const trailingReturnMatch = source.match(/return \(\n(?:(?!^\})[\s\S])*?<\/main>\n {2}\);\n\}\n?$/m);
assert(
  Boolean(trailingReturnMatch) && /app-page-message/.test(trailingReturnMatch?.[0] ?? ""),
  "the component's final return (the in-flight / about-to-submit state) renders real app-page-message content as its LAST statement, not a trailing bare return",
);

// ── Hooks correctness: useEffect must run unconditionally, i.e. its call
// site must appear BEFORE the first early `return` — not after two of them,
// which is the exact shape that crashes with "Rendered fewer hooks than
// expected" the moment state.status or membership differs between renders.

const useEffectIndex = source.indexOf("useEffect(() => {");
const firstEarlyReturnIndex = source.indexOf("if (state.status !== 'authenticated') {");
assert(
  useEffectIndex !== -1 && firstEarlyReturnIndex !== -1,
  "both the auto-submit useEffect and the state.status early return are present in this file",
);
assert(
  useEffectIndex < firstEarlyReturnIndex,
  "useEffect is called BEFORE any early return, not after — React's Rules " +
    "of Hooks require every render to call the same hooks in the same " +
    "order; gating belongs inside the effect body, not around the hook call itself",
);

// ── The auto-submit effect is gated inside its own body (not by an
// early-return above it) and guarded against firing twice — handleSubmit's
// own success path calls actions.replaceSession(), which changes the
// memoized `membership` reference the effect depends on while this
// component may still be mounted awaiting the redirect.

const effectBodyMatch = source.match(/useEffect\(\(\) => \{([\s\S]*?)\}, \[state\.status, membership\]\);/);
assert(Boolean(effectBodyMatch), "the auto-submit useEffect declares [state.status, membership] as its dependency array");
const effectBody = effectBodyMatch?.[1] ?? "";
assert(
  /if \(state\.status !== 'authenticated' \|\| !membership \|\| autoSubmitAttempted\.current\)/.test(effectBody),
  "the effect gates on session status, membership, AND a ref guard inside its own body — not via an early return above the hook",
);
assert(
  /autoSubmitAttempted\.current = true;/.test(effectBody),
  "the effect marks its ref guard before submitting, so a membership-reference change mid-flight can't trigger a second automatic PATCH",
);

console.log(`\n${passed} passed, ${failed} failed`);
if (failed > 0) {
  process.exit(1);
}
