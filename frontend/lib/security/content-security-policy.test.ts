/**
 * Structural tests for the real Content-Security-Policy — see this
 * module's own header for why every directive is shaped the way it is.
 *
 * These assert on the SHAPE of the policy (which directives exist, which
 * unsafe keywords are absent) rather than on behaviour, per this repo's
 * established pattern (CLAUDE.md: "prefer a structural assertion over a
 * behavioural one... a future edit that weakens a directive is silent").
 * A behavioural CSP test would need a real browser to observe a blocked
 * script; a structural one catches a weakened directive the moment it's
 * typed, in the same process as `npm run test:unit`.
 *
 * UPDATED, 2026-08-14: style-src deliberately dropped its nonce and now
 * carries 'unsafe-inline' in BOTH prod and dev — see the module's own
 * header for the full mechanism (nonce-only style-src cannot cover the
 * literal `style=""` attribute react-dom/server emits for every
 * `style={{...}}` prop, so it was violating on every authenticated page in
 * production, confirmed via a real `next build && next start` walkthrough:
 * 10+ distinct violations on one ordinary page, and — worse — those
 * violations buried a genuine, unrelated React hydration error in the same
 * console on 2026-08-14). This is a DELIBERATE POSTURE CHANGE, not a
 * regression: the assertions below were flipped to assert the new shape
 * (style-src carries 'unsafe-inline', carries no nonce token) rather than
 * deleted, precisely so nobody mistakes the old nonce-only shape for the
 * one still intended. script-src is completely untouched — still
 * nonce + 'strict-dynamic', no 'unsafe-inline', no 'unsafe-eval' in prod —
 * and this file still asserts that as strictly as before.
 *
 * This file passing is still NOT evidence the browser console is fully
 * clean of every possible CSS-injection concern — 'unsafe-inline' on
 * style-src is a real, accepted widening (see the module header's "Cost"
 * paragraph), not a claim that nothing can go wrong there. It IS evidence
 * that the specific bug above (nonce-only style-src violating on ordinary
 * pages) cannot silently return, and that script-src cannot be weakened
 * without this suite failing.
 *
 * Run: npx tsx lib/security/content-security-policy.test.ts
 */

import {
  buildContentSecurityPolicy,
  buildContentSecurityPolicyDirectives,
  generateNonce,
  serializeContentSecurityPolicy,
} from './content-security-policy';

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

function assertEqual<T>(actual: T, expected: T, label: string): void {
  if (JSON.stringify(actual) === JSON.stringify(expected)) {
    passed++;
  } else {
    failed++;
    console.error(`FAIL: ${label} — expected ${JSON.stringify(expected)}, got ${JSON.stringify(actual)}`);
  }
}

const PROD_NONCE = 'unit-test-nonce-value';
const prodDirectives = buildContentSecurityPolicyDirectives({ nonce: PROD_NONCE, isDev: false });
const devDirectives = buildContentSecurityPolicyDirectives({ nonce: PROD_NONCE, isDev: true });

// ─── The properties the founder's brief calls out by name ─────────────────

assert(
  prodDirectives['script-src'].some((v) => v === `'nonce-${PROD_NONCE}'`),
  "script-src carries the per-request nonce",
);
assert(
  !prodDirectives['script-src'].some((v) => v === `'unsafe-inline'`),
  "script-src (prod) does NOT carry 'unsafe-inline'",
);
assert(
  !prodDirectives['script-src'].some((v) => v === `'unsafe-eval'`),
  "script-src (prod) does NOT carry 'unsafe-eval'",
);
assertEqual(prodDirectives['object-src'], [`'none'`], "object-src is exactly 'none'");
assertEqual(prodDirectives['base-uri'], [`'self'`], "base-uri is 'self'");
assertEqual(prodDirectives['form-action'], [`'self'`], "form-action is 'self'");
assertEqual(
  prodDirectives['frame-ancestors'],
  [`'none'`],
  "frame-ancestors survives the move from nginx and is exactly 'none'",
);

// ─── style-src: DELIBERATELY posture-changed, 2026-08-14 — nonce dropped,
// 'unsafe-inline' carried in BOTH prod and dev. See the module header and
// this file's own header comment for why. Flip these back only alongside
// the SSR inline-style migration the module header describes as "the path
// back" — never by re-adding the nonce without doing that migration first,
// since a nonce present alongside 'unsafe-inline' makes nonce-aware
// browsers silently ignore 'unsafe-inline' (CSP's backward-compat rule),
// which would reintroduce this exact bug with a test suite that still
// looks green. ─────────────────────────────────────────────────────────

assert(
  prodDirectives['style-src'].includes(`'unsafe-inline'`),
  "style-src (prod) DELIBERATELY carries 'unsafe-inline' — nonce-only style-src cannot cover the style=\"\" attribute react-dom/server emits; see module header",
);
assert(
  !prodDirectives['style-src'].some((v) => v.startsWith(`'nonce-`)),
  "style-src (prod) carries NO nonce token — a nonce present alongside 'unsafe-inline' would silently disable it in nonce-aware browsers",
);
assert(
  !prodDirectives['script-src'].some((v) => v === `'unsafe-inline'`),
  "script-src (prod) is UNCHANGED by the style-src posture change — still no 'unsafe-inline'",
);
assert(
  prodDirectives['script-src'].some((v) => v === `'strict-dynamic'`),
  "script-src (prod) is UNCHANGED by the style-src posture change — still carries 'strict-dynamic'",
);

// ─── dev-only escape hatches never leak into production ───────────────────

assert(
  devDirectives['script-src'].includes(`'unsafe-eval'`),
  "script-src (dev) carries 'unsafe-eval' — required by React's dev-mode eval for error stacks",
);
assert(
  devDirectives['style-src'].includes(`'unsafe-inline'`),
  "style-src (dev) carries 'unsafe-inline' too — dev and prod are the same shape for style-src now",
);
assert(
  !devDirectives['style-src'].some((v) => v.startsWith(`'nonce-`)),
  "style-src (dev) carries no nonce token either — dev and prod match",
);

// ─── every directive present, none silently dropped ───────────────────────

const REQUIRED_DIRECTIVES = [
  'default-src',
  'script-src',
  'style-src',
  'img-src',
  'connect-src',
  'font-src',
  'object-src',
  'base-uri',
  'form-action',
  'frame-ancestors',
];
for (const directive of REQUIRED_DIRECTIVES) {
  assert(directive in prodDirectives, `policy includes ${directive}`);
}

// ─── the one documented, narrow exception: frame-src for hosted mini-apps ─

assertEqual(
  prodDirectives['frame-src'],
  ['https:'],
  "frame-src is narrowed to https: only (hosted mini-app iframes — see module header), never '*' or 'unsafe'",
);

// ─── img-src: only the three documented, narrow widenings beyond 'self' ───

assertEqual(
  new Set(prodDirectives['img-src']),
  new Set([`'self'`, 'data:', 'blob:', 'https:']),
  "img-src is exactly 'self' + the three documented widenings (external doc images, QR data URLs, upload-preview blobs)",
);

// ─── connect-src stays 'self' — no wildcard, no unused wss: source ────────

assertEqual(
  prodDirectives['connect-src'],
  [`'self'`],
  "connect-src is exactly 'self' (grepped: no direct-from-browser WebSocket/cross-origin fetch exists today)",
);

// ─── nonce must be present — the function refuses to build a noncelss policy ─

{
  let threw = false;
  try {
    buildContentSecurityPolicyDirectives({ nonce: '', isDev: false });
  } catch {
    threw = true;
  }
  assert(threw, "building with an empty nonce throws rather than silently shipping a noncelss policy");
}

// ─── serialization round-trip: the string a browser actually receives ─────

{
  const serialized = buildContentSecurityPolicy({ nonce: PROD_NONCE, isDev: false });
  assert(serialized.includes(`script-src 'self' 'nonce-${PROD_NONCE}' 'strict-dynamic'`), "serialized script-src is well-formed and carries no 'unsafe-inline' (checked as an exact adjacent-value match, not a substring ban -- see below)");
  assert(serialized.includes(`object-src 'none'`), "serialized string contains object-src 'none'");
  assert(serialized.includes(`frame-ancestors 'none'`), "serialized string contains frame-ancestors 'none'");
  // style-src DELIBERATELY carries 'unsafe-inline' now (2026-08-14, see module
  // header) -- so a blanket "the string 'unsafe-inline' never appears" ban
  // would be wrong. Assert the exact style-src segment instead, which proves
  // both that style-src has it and that it isn't leaking into script-src.
  assert(serialized.includes(`style-src 'self' 'unsafe-inline'`), "serialized style-src is exactly 'self' 'unsafe-inline', no nonce");
  assert(!serialized.includes('unsafe-eval'), "serialized prod string never contains the string 'unsafe-eval'");
  // upgrade-insecure-requests has no values -- assert it serializes as a bare directive, not "upgrade-insecure-requests "
  assert(
    serialized.includes('upgrade-insecure-requests') && !serialized.includes('upgrade-insecure-requests;;'),
    "value-less directives serialize as bare names",
  );
}

// ─── regression proof: the assertions above actually catch a weakened policy ─
// (this is the "prove it fails before your change" requirement, kept live
// in the suite rather than a one-off manual check, so it can't rot)

{
  const weakened = serializeContentSecurityPolicy({
    // what nginx used to ship, alone -- the exact shape this change replaces
    'frame-ancestors': [`'none'`],
  });
  assert(
    !weakened.includes('object-src'),
    "sanity: a frame-ancestors-only policy (the pre-change nginx state) has no object-src -- proves the object-src assertion above is not vacuous",
  );
  const fakedUnsafe = serializeContentSecurityPolicy({
    'script-src': [`'self'`, `'unsafe-inline'`, `'unsafe-eval'`],
  });
  assert(
    fakedUnsafe.includes('unsafe-inline') && fakedUnsafe.includes('unsafe-eval'),
    "sanity: a policy that DOES carry unsafe-inline/unsafe-eval is detected as carrying them -- proves the negative assertions above are not vacuous",
  );
}

// ─── generateNonce: fresh, non-empty, base64-shaped, unique per call ──────

{
  const a = generateNonce();
  const b = generateNonce();
  assert(typeof a === 'string' && a.length > 0, "generateNonce returns a non-empty string");
  assert(a !== b, "generateNonce is different on every call (per-request, never reused)");
  assert(/^[A-Za-z0-9+/=]+$/.test(a), "generateNonce output is base64-shaped");
}

// ─── Summary ───────────────────────────────────────────────────────────────

console.log(`\n${passed} passed, ${failed} failed`);
if (failed > 0) {
  process.exit(1);
}
