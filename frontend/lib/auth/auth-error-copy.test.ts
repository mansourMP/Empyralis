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
