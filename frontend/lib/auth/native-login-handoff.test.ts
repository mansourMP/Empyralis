/**
 * Native app login handoff -- the pure decision half.
 *
 * See native-login-handoff.ts's own header for the design; this pins the
 * two things that would otherwise only be caught by opening a real iOS
 * simulator: (a) a well-formed `?native=ios&code_challenge=...&state=...`
 * visit is recognised and a malformed one safely degrades to the ordinary
 * web-login behaviour, never an error; (b) the redirect URL a mint response
 * turns into is built correctly and never fabricated when the backend
 * returned nothing usable.
 *
 * Run: npx tsx lib/auth/native-login-handoff.test.ts
 */

import {
  isPlausibleCodeChallenge,
  nativeHandoffRedirectUrl,
  planNativeLoginHandoff,
  planNativeLoginHandoffFromRecord,
} from './native-login-handoff';

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

// A real S256 challenge is exactly 43 base64url characters.
const REAL_CHALLENGE = 'E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM';
const REAL_STATE = 'a1b2c3-random-state-token';

// ── The well-formed native request is recognised ───────────────────────────
assert(
  (() => {
    const plan = planNativeLoginHandoff({ native: 'ios', codeChallenge: REAL_CHALLENGE, state: REAL_STATE });
    return plan.kind === 'native' && plan.target === 'ios' && plan.codeChallenge === REAL_CHALLENGE && plan.state === REAL_STATE;
  })(),
  'native=ios with a real challenge and state is planned as a native handoff',
);
assert(
  planNativeLoginHandoff({ native: 'IOS', codeChallenge: REAL_CHALLENGE, state: REAL_STATE }).kind === 'native',
  'the native target is matched case-insensitively',
);

// ── Ordinary web login is untouched ─────────────────────────────────────────
assert(
  planNativeLoginHandoff({ native: null, codeChallenge: null, state: null }).kind === 'web',
  'no native param at all is an ordinary web login',
);
assert(
  planNativeLoginHandoff({ native: '', codeChallenge: '', state: '' }).kind === 'web',
  'empty strings (as URLSearchParams.get returns for an absent param handled upstream) are an ordinary web login',
);

// ── Every malformed shape degrades to web, never throws, never half-plans ──
assert(
  planNativeLoginHandoff({ native: 'android', codeChallenge: REAL_CHALLENGE, state: REAL_STATE }).kind === 'web',
  'an unrecognized native target falls back to web -- there is no android redirect target to hand off to yet',
);
assert(
  planNativeLoginHandoff({ native: 'ios', codeChallenge: null, state: REAL_STATE }).kind === 'web',
  'native=ios with no code_challenge at all falls back to web, not a crash',
);
assert(
  planNativeLoginHandoff({ native: 'ios', codeChallenge: 'too-short', state: REAL_STATE }).kind === 'web',
  'a code_challenge of the wrong length is rejected (not a real SHA-256 digest)',
);
assert(
  planNativeLoginHandoff({ native: 'ios', codeChallenge: `${REAL_CHALLENGE}x`, state: REAL_STATE }).kind === 'web',
  'a code_challenge one character too long is rejected',
);
assert(
  planNativeLoginHandoff({ native: 'ios', codeChallenge: 'https://evil.example/x'.padEnd(43, 'a'), state: REAL_STATE }).kind
    === 'web',
  'a code_challenge carrying characters outside the base64url alphabet is rejected even at the right length',
);
assert(
  planNativeLoginHandoff({ native: 'ios', codeChallenge: REAL_CHALLENGE, state: null }).kind === 'web',
  'native=ios with no state falls back to web',
);
assert(
  planNativeLoginHandoff({ native: 'ios', codeChallenge: REAL_CHALLENGE, state: '' }).kind === 'web',
  'native=ios with an empty state falls back to web',
);

// ── isPlausibleCodeChallenge is the same rule the plan function uses ───────
assert(isPlausibleCodeChallenge(REAL_CHALLENGE), 'a real S256 challenge passes the shape check');
assert(!isPlausibleCodeChallenge('not-a-challenge'), 'an arbitrary string fails the shape check');
assert(!isPlausibleCodeChallenge(''), 'an empty string fails the shape check');

// ── The sessionStorage-relay entry point (the /auth/complete side of the
//    Google return -- see native-login-handoff.ts's header) applies the
//    SAME rule, not a looser copy of it ─────────────────────────────────────
assert(
  planNativeLoginHandoffFromRecord({ native: 'ios', codeChallenge: REAL_CHALLENGE, state: REAL_STATE }).kind === 'native',
  'a well-formed stored record is planned as a native handoff',
);
assert(planNativeLoginHandoffFromRecord(null).kind === 'web', 'no stored record at all is an ordinary web login');
assert(planNativeLoginHandoffFromRecord(undefined).kind === 'web', 'undefined is an ordinary web login');
assert(planNativeLoginHandoffFromRecord('a string, not an object').kind === 'web', 'a non-object record is an ordinary web login');
assert(
  planNativeLoginHandoffFromRecord({ native: 'ios', codeChallenge: 123, state: REAL_STATE }).kind === 'web',
  'a record with the wrong field TYPE (not just a bad value) is an ordinary web login, not a crash',
);
assert(
  planNativeLoginHandoffFromRecord({ native: 'ios', codeChallenge: REAL_CHALLENGE }).kind === 'web',
  'a record missing the state field entirely is an ordinary web login',
);

// ── The redirect URL is built from the backend's OWN redirect_uri, never a
//    scheme this file invents ───────────────────────────────────────────────
assert(
  nativeHandoffRedirectUrl('empyralis://auth', 'the-code', REAL_STATE)
    === `empyralis://auth?code=the-code&state=${REAL_STATE}`,
  'code and state are appended as ordinary query params',
);
assert(
  nativeHandoffRedirectUrl('empyralis://auth', 'a code/with+special&chars', 'a state?with=chars')
    === 'empyralis://auth?code=a+code%2Fwith%2Bspecial%26chars&state=a+state%3Fwith%3Dchars',
  'code and state are percent-encoded, so a code containing URL-meaningful characters cannot corrupt the query string',
);
assert(
  nativeHandoffRedirectUrl('empyralis://auth', 'the-code', '') === 'empyralis://auth?code=the-code',
  'an empty state is omitted rather than appended as state=',
);

// ── A backend contract violation (nothing usable) never fabricates a URL ───
assert(nativeHandoffRedirectUrl('', 'the-code', REAL_STATE) === null, 'an empty redirect_uri yields no URL, never a bare "?code=..."');
assert(nativeHandoffRedirectUrl('empyralis://auth', '', REAL_STATE) === null, 'an empty code yields no URL');
assert(nativeHandoffRedirectUrl('  ', '   ', REAL_STATE) === null, 'whitespace-only inputs are treated as empty, not as real values');

console.log(`native-login-handoff: ${passed} passed, ${failed} failed`);
if (failed > 0) {
  process.exit(1);
}
