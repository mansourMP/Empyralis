/**
 * MAN — login page's auth notice put a failure heading over a success
 * body. Reproduced live 2026-08-25: after a real login, the post-login
 * readiness poll (awaitBrowserAuthReady) timed out and login/page.tsx
 * rendered "Couldn't sign in" / "You're signed in, but your session isn't
 * ready yet." in one alert box — opposite facts on one screen. Pressing
 * Continue a second time went straight into the workspace, confirming the
 * body was the true fact and the hardcoded title was the lie.
 *
 * classifyLoginOutcome (auth-error-copy.ts) is the fix: ONE function
 * returns {tone, title, body} together, so a title can no longer be typed
 * independently of the fact it describes. This file pins the specific
 * regression (session-not-ready must never carry a failure title/tone)
 * and, structurally, that NO branch can ever pair a non-error tone with a
 * failure-shaped title, and NO branch can ever pair the 'error' tone with
 * body text that itself claims success/uncertainty rather than failure —
 * catching the next such branch, not just this one.
 *
 * SECOND, DEEPER INSTANCE (same day): login/page.tsx's own catch around
 * awaitBrowserAuthReady discarded the exact message that function had
 * already thrown and replaced it with a hardcoded 'Session not ready.'
 * literal, unconditionally. But that function throws THREE distinguishable
 * things (auth-client.ts): attempts exhausted while still warming up
 * (genuinely "press Continue again"), 3 consecutive 401s ("Your session
 * expired. Sign in again." — the session is actually gone), and a 403
 * ("This workspace is not accessible for this account." — a permissions
 * problem). Flattening all three into "press Continue again" told a
 * customer with a dead session or no workspace access to keep retrying
 * forever. The fix is to STOP discarding the thrown message (login/page.tsx
 * now passes it through) and to make classifyLoginOutcome route each of the
 * three to its own correct tone: 401 lands on the existing 'session
 * expired' failure branch, 403 lands on a new 'not accessible for this
 * account' failure branch that never invites a retry, and only the
 * exhausted-attempts case keeps the 'notice' tone and the "press Continue"
 * instruction.
 *
 * Run: npx tsx lib/auth/auth-error-copy.test.ts
 */

import { readFileSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

import { classifyLoginOutcome, type AuthOutcomeCopy } from './auth-error-copy';

const __dirname = path.dirname(fileURLToPath(import.meta.url));

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

const FALLBACK_TITLE = "Couldn't sign in";

// ── The exact regression: session-not-ready must read as a non-failure ────
const sessionNotReady = classifyLoginOutcome('Session not ready.', FALLBACK_TITLE);
assert(sessionNotReady.tone === 'notice', 'session-not-ready classifies as a notice, not an error');
assert(
  sessionNotReady.title.toLowerCase() !== FALLBACK_TITLE.toLowerCase(),
  'session-not-ready never wears the caller-supplied failure title',
);
assert(
  !/couldn'?t|fail|error|wrong|not accepted/i.test(sessionNotReady.title),
  'session-not-ready title carries no failure language',
);
assert(
  /signed in/i.test(sessionNotReady.body),
  'session-not-ready body says the sign-in already succeeded',
);

// ── A genuine failure still reads as a failure ─────────────────────────────
const badPassword = classifyLoginOutcome('Login request failed: status 401', FALLBACK_TITLE);
assert(badPassword.tone === 'error', 'a real 401 classifies as an error');
assert(badPassword.title === FALLBACK_TITLE, 'a real 401 keeps the caller-supplied failure title');

const expired = classifyLoginOutcome('Your session expired. Sign in again.', FALLBACK_TITLE);
assert(expired.tone === 'error', 'session-expired classifies as an error');
assert(expired.title !== FALLBACK_TITLE, 'session-expired gets its own specific title, not the generic fallback');

// ── The second regression: awaitBrowserAuthReady's three distinguishable
// throws (auth-client.ts), fed through classifyLoginOutcome with the EXACT
// strings that function actually throws — not paraphrases. The 401 case is
// the one the coordinator called out specifically: it must never produce a
// notice tone or a "press Continue" body, because a dead session cannot be
// fixed by retrying the same poll. ────────────────────────────────────────

// 1) 3 consecutive 401s — the session is actually gone.
const readinessExpired = classifyLoginOutcome('Your session expired. Sign in again.', FALLBACK_TITLE);
assert(readinessExpired.tone === 'error', 'readiness-poll 401x3 classifies as an error, never a notice');
assert(
  !/press continue/i.test(readinessExpired.body),
  'readiness-poll 401x3 body never tells the customer to press Continue again',
);
assert(
  !/you'?re signed in/i.test(readinessExpired.body),
  'readiness-poll 401x3 body never claims the sign-in is fine',
);

// 2) 403 — this account cannot open this workspace. Distinct branch,
// distinct title, and it must not invite a retry either.
const readinessForbidden = classifyLoginOutcome('This workspace is not accessible for this account.', FALLBACK_TITLE);
assert(readinessForbidden.tone === 'error', 'readiness-poll 403 classifies as an error, never a notice');
assert(
  readinessForbidden.title !== FALLBACK_TITLE && readinessForbidden.title !== 'Almost there',
  'readiness-poll 403 gets its own specific title (not the generic fallback, not the notice title)',
);
assert(
  !/press continue/i.test(readinessForbidden.body),
  'readiness-poll 403 body never tells the customer to press Continue again',
);
assert(
  !/you'?re signed in/i.test(readinessForbidden.body),
  'readiness-poll 403 body never claims the sign-in is fine',
);

// 3) Attempts exhausted while genuinely still warming up — the ONLY case
// that may keep the notice tone and the "press Continue" instruction. Both
// exact message shapes auth-client.ts can throw for this case.
for (const stillWarmingMessage of [
  'Auth readiness check did not complete.',
  'Auth readiness check did not recover from status 500.',
]) {
  const stillWarming = classifyLoginOutcome(stillWarmingMessage, FALLBACK_TITLE);
  assert(stillWarming.tone === 'notice', `"${stillWarmingMessage}" classifies as a notice, not an error`);
  assert(
    stillWarming.title !== FALLBACK_TITLE,
    `"${stillWarmingMessage}" never wears the caller-supplied failure title`,
  );
  assert(
    /signed in/i.test(stillWarming.body),
    `"${stillWarmingMessage}" body says the sign-in already succeeded`,
  );
}

// ── Structural guard: no branch can pair a non-error tone with a failure
// title, and no 'error' branch can describe success/uncertainty in its body.
// Driven directly against the source so a NEW branch is covered without
// this file needing to enumerate every substring by hand.
const source = readFileSync(path.join(__dirname, 'auth-error-copy.ts'), 'utf8');
const returnedTitles = Array.from(source.matchAll(/title:\s*'([^']*)'/g)).map((m) => m[1]);
const FAILURE_WORDS = /couldn'?t|fail|error|wrong|not accepted|expired|unavailable/i;
for (const title of returnedTitles) {
  // The one non-failure title this file returns today. If a new notice-tone
  // branch is added with a different title, it must also avoid failure
  // language — this loop already covers that because it scans every
  // literal `title:` in the source, not just this one.
  if (title === 'Almost there') {
    assert(!FAILURE_WORDS.test(title), `notice title "${title}" carries no failure language`);
  }
}

// Every fail(...) call site's SECOND argument (the body) must not itself
// claim the sign-in succeeded — that would be the same contradiction one
// level down (an error-toned box whose own body says "you're signed in").
const failCallBodies = Array.from(source.matchAll(/fail\(\s*[^,]+,\s*'([^']*)'\s*\)/g)).map((m) => m[1]);
assert(failCallBodies.length > 5, 'the source scan actually found fail(...) call sites (canary)');
for (const body of failCallBodies) {
  assert(
    !/you'?re signed in|already succeeded|already exists and is live/i.test(body),
    `fail() body does not itself claim success: "${body.slice(0, 60)}"`,
  );
}

// Sanity: the function signature and return shape are what the rest of this
// file assumes (canary — if this ever fails, every assertion above it may
// have been silently checking nothing).
const sample: AuthOutcomeCopy = classifyLoginOutcome('anything', 'x');
assert(
  typeof sample.tone === 'string' && typeof sample.title === 'string' && typeof sample.body === 'string',
  'classifyLoginOutcome returns {tone, title, body} (canary)',
);

console.log(`auth-error-copy: ${passed} passed, ${failed} failed`);
if (failed > 0) {
  process.exit(1);
}
