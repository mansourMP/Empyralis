/**
 * Drives the REAL callback gate, never a copy of its allowlist.
 *
 * The assertions that matter are the NEGATIVE ones. Every accepted case below
 * also passes under a naive `callback.includes('127.0.0.1')` implementation;
 * only the lookalike-host cases can tell the two apart, and those are exactly
 * the ones that would hand a live pairing token to somebody else's server.
 *
 * Red-before-green proof for the whole file: replace `checkPairCallback`'s
 * host test with `url.hostname.includes('127.0.0.1')` and
 * `a_lookalike_host_is_refused` fails while everything else stays green.
 */

import assert from 'node:assert/strict';

import {
  DESKTOP_PAIR_CALLBACK_PATH,
  buildPairCallbackUrl,
  buildPairDenialUrl,
  checkPairCallback,
  isValidPairState,
  pairCallbackRefusalDetail,
} from './desktop-pair-callback';

const STATE = 's7Qk3Vb0aZnEXAMPLEstate-value';
const OK = `http://127.0.0.1:53411${DESKTOP_PAIR_CALLBACK_PATH}`;

function refused(raw: unknown): string {
  const result = checkPairCallback(raw);
  assert.equal(result.ok, false, `expected a refusal for ${String(raw)}`);
  return (result as { ok: false; reason: string }).reason;
}

function accepted(raw: string): string {
  const result = checkPairCallback(raw);
  assert.equal(result.ok, true, `expected an acceptance for ${raw}`);
  return (result as { ok: true; callback: string }).callback;
}

// ── the two shapes the desktop shell actually produces ────────────────────
assert.equal(accepted(OK), OK);
assert.equal(
  accepted(`http://localhost:1455${DESKTOP_PAIR_CALLBACK_PATH}`),
  `http://localhost:1455${DESKTOP_PAIR_CALLBACK_PATH}`,
);

// ── THE HOSTILE ONE. The whole reason this module exists. ─────────────────
assert.equal(refused(`https://evil.example${DESKTOP_PAIR_CALLBACK_PATH}`), 'scheme');
assert.equal(refused(`http://evil.example${DESKTOP_PAIR_CALLBACK_PATH}`), 'host');
assert.equal(refused('https://evil.example/'), 'scheme');

// A lookalike host is an ordinary public hostname. Any substring, prefix or
// suffix check accepts these; only equality refuses them.
for (const host of [
  'localhost.evil.example',
  '127.0.0.1.evil.example',
  'evil.example.localhost.co',
  'notlocalhost',
  'xlocalhost',
  // A DIFFERENT machine on the loopback /8 is still not this machine's own
  // listener — and unlike the three below, the parser does not fold it in.
  '127.0.0.11',
]) {
  assert.equal(refused(`http://${host}:53411${DESKTOP_PAIR_CALLBACK_PATH}`), 'host', host);
}

// MEASURED, and the reason the equality check is safe rather than merely
// tidy: WHATWG `new URL()` CANONICALISES obfuscated IPv4 literals before
// `hostname` is ever read — `0177.0.0.1` (octal), `2130706433` (decimal) and
// `127.1` (short form) all parse to the string `127.0.0.1`. So these are
// genuinely this machine's own loopback and are genuinely accepted. Note what
// that means for anyone tempted to "simplify" this module: a regex or a
// string comparison against the RAW callback would see three hosts it does
// not recognise and refuse three legitimate addresses — and, far worse, the
// same shortcut in the other direction (matching the raw string) would accept
// `http://127.0.0.1.evil.example`. Parse first, then compare.
for (const host of ['0177.0.0.1', '2130706433', '127.1']) {
  assert.equal(
    accepted(`http://${host}:53411${DESKTOP_PAIR_CALLBACK_PATH}`),
    `http://127.0.0.1:53411${DESKTOP_PAIR_CALLBACK_PATH}`,
    host,
  );
}

// Binding anywhere but loopback is the other half of the same rule.
assert.equal(refused(`http://0.0.0.0:53411${DESKTOP_PAIR_CALLBACK_PATH}`), 'host');
assert.equal(refused(`http://192.168.1.20:53411${DESKTOP_PAIR_CALLBACK_PATH}`), 'host');
assert.equal(refused(`http://[::1]:53411${DESKTOP_PAIR_CALLBACK_PATH}`), 'host');

// `http://evil.example@127.0.0.1:1234/` really does parse to hostname
// 127.0.0.1 — a userinfo section has to be refused deliberately.
assert.equal(refused(`http://someone@127.0.0.1:53411${DESKTOP_PAIR_CALLBACK_PATH}`), 'userinfo');
assert.equal(refused(`http://a:b@localhost:53411${DESKTOP_PAIR_CALLBACK_PATH}`), 'userinfo');

// ── shape ─────────────────────────────────────────────────────────────────
assert.equal(refused(''), 'missing');
assert.equal(refused(undefined), 'missing');
assert.equal(refused(null), 'missing');
assert.equal(refused(42), 'missing');
assert.equal(refused('not a url'), 'unparseable');
assert.equal(refused(`//127.0.0.1:53411${DESKTOP_PAIR_CALLBACK_PATH}`), 'unparseable');
assert.equal(refused('javascript:alert(1)'), 'scheme');
assert.equal(refused('file:///etc/passwd'), 'scheme');
assert.equal(refused(`http://127.0.0.1${DESKTOP_PAIR_CALLBACK_PATH}`), 'port');
assert.equal(refused('http://127.0.0.1:53411/'), 'path');
assert.equal(refused('http://127.0.0.1:53411/anything-else'), 'path');
assert.equal(refused(`http://127.0.0.1:53411${DESKTOP_PAIR_CALLBACK_PATH}?already=here`), 'extras');
assert.equal(refused(`http://127.0.0.1:53411${DESKTOP_PAIR_CALLBACK_PATH}#frag`), 'extras');

// ── the state nonce ───────────────────────────────────────────────────────
assert.equal(isValidPairState(STATE), true);
assert.equal(isValidPairState(''), false);
assert.equal(isValidPairState('short'), false);
assert.equal(isValidPairState(undefined), false);
assert.equal(isValidPairState('a'.repeat(129)), false);
// Anything outside base64url is a place to hide a payload in a URL we echo.
assert.equal(isValidPairState('has spaces in it here'), false);
assert.equal(isValidPairState('<script>alert(1)</script>abc'), false);
assert.equal(isValidPairState('abcdefghijklmnop&x=1'), false);

// ── building the redirect ─────────────────────────────────────────────────
{
  const url = new URL(
    buildPairCallbackUrl(OK, {
      state: STATE,
      pairingToken: 'pt_abc123',
      workspaceId: 'ws_9f3c',
      workspaceLabel: 'Acme Ltd',
    }),
  );
  assert.equal(url.origin, 'http://127.0.0.1:53411');
  assert.equal(url.pathname, DESKTOP_PAIR_CALLBACK_PATH);
  assert.equal(url.searchParams.get('state'), STATE);
  assert.equal(url.searchParams.get('pairing_token'), 'pt_abc123');
  assert.equal(url.searchParams.get('workspace_id'), 'ws_9f3c');
  assert.equal(url.searchParams.get('workspace_label'), 'Acme Ltd');
}

// A blank label is omitted rather than sent empty — the menu bar falls back
// to a truthful generic line, and an empty string would defeat that.
{
  const url = new URL(
    buildPairCallbackUrl(OK, {
      state: STATE,
      pairingToken: 'pt_abc123',
      workspaceId: 'ws_9f3c',
      workspaceLabel: '   ',
    }),
  );
  assert.equal(url.searchParams.has('workspace_label'), false);
}

// THE BUILDERS RE-CHECK. A caller that skipped the gate must not be able to
// launder a hostile address through them.
assert.throws(() =>
  buildPairCallbackUrl('https://evil.example/', {
    state: STATE,
    pairingToken: 'pt_abc123',
    workspaceId: 'ws_9f3c',
  }),
);
assert.throws(() => buildPairDenialUrl('https://evil.example/', STATE));

{
  const url = new URL(buildPairDenialUrl(OK, STATE));
  assert.equal(url.origin, 'http://127.0.0.1:53411');
  assert.equal(url.searchParams.get('error'), 'denied');
  assert.equal(url.searchParams.get('state'), STATE);
  // A denial carries no credential, by construction.
  assert.equal(url.searchParams.has('pairing_token'), false);
}

// ── the refusal we render never echoes what the link said ─────────────────
for (const reason of ['missing', 'scheme', 'host', 'userinfo', 'port', 'path', 'extras']) {
  const detail = pairCallbackRefusalDetail(reason);
  assert.ok(detail.trim().length > 0, reason);
  assert.ok(!detail.includes('evil'), reason);
  assert.ok(!/https?:\/\//.test(detail), reason);
}

console.log('desktop-pair-callback.test.ts: all assertions passed');
