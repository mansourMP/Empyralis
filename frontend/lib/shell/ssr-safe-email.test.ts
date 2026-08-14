/**
 * ssr-safe-email.ts unit tests, plus a structural sweep of the boundary
 * seams it exists to protect.
 *
 * 2026-08-14 — see that file's own header for the mechanism (Cloudflare's
 * email-obfuscation rewrite corrupting Next's RSC payload and breaking
 * hydration). Three things this file checks that a behavioural round-trip
 * test alone cannot:
 *
 *   1. The encoding genuinely defeats an email-shaped regex — the whole
 *      point of obfuscateEmailForSsr is that its output never matches one,
 *      so this is asserted directly rather than assumed from "it's hex".
 *   2. Both server-side parsers that hand an account's email to a Client
 *      Component (account-shell-payload.ts, workspace-bootstrap.ts) call
 *      obfuscateEmailForSsr on it, and PrimaryRail.tsx never renders its
     *  obfuscated owner-email prop directly — only through
 *      useRevealedEmail(). A behavioural test only covers the shape of
 *      data that exists today; a source scan is what catches the next
 *      call site that skips the encode/decode and puts a raw address back
 *      in the response body (same idiom as safe-render-url.test.ts's own
 *      SEAMS sweep).
 *
 * Run: npx tsx lib/shell/ssr-safe-email.test.ts
 */

import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

import { obfuscateEmailForSsr, revealEmail } from "./ssr-safe-email";

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
  if (actual === expected) {
    passed++;
  } else {
    failed++;
    console.error(`FAIL: ${label} — expected ${JSON.stringify(expected)}, got ${JSON.stringify(actual)}`);
  }
}

// ─── Round trip ─────────────────────────────────────────────────────────────

const SAMPLE_EMAILS = [
  "owner@example.com",
  "a@b.co",
  "first.last+tag@sub.example.co.uk",
  "unicode-dömain@exämple.com",
  "x@y.z",
];

for (const email of SAMPLE_EMAILS) {
  const obfuscated = obfuscateEmailForSsr(email);
  assertEqual(revealEmail(obfuscated), email, `round trip: ${email}`);
}

// ─── The encoded form is never email-shaped ────────────────────────────────
// This is the load-bearing property — an email-pattern scanner (Cloudflare's
// or any other CDN's) keys on "@" plus a "." in the domain part. If the
// obfuscated output ever contained either, the fix would not fix anything.

const EMAIL_SHAPED_PATTERN = /[^\s]+@[^\s]+\.[^\s]+/;

for (const email of SAMPLE_EMAILS) {
  const obfuscated = obfuscateEmailForSsr(email);
  assert(!obfuscated.includes("@"), `obfuscated form has no "@": ${email}`);
  assert(!obfuscated.includes("."), `obfuscated form has no ".": ${email}`);
  assert(!EMAIL_SHAPED_PATTERN.test(obfuscated), `obfuscated form does not match an email-shaped pattern: ${email}`);
  assert(/^[0-9a-f]+$/i.test(obfuscated), `obfuscated form is pure hex: ${email}`);
}

// ─── Malformed input decodes to empty rather than throwing ────────────────

assertEqual(revealEmail(""), "", "revealEmail: empty string");
assertEqual(revealEmail("not-hex-zz"), "", "revealEmail: non-hex input");
assertEqual(revealEmail("abc"), "", "revealEmail: odd-length input (not a multiple of 4)");

// ─── Structural: both server-side parsers obfuscate before handing the
// email to a Client Component, and the rail never renders it un-decoded ───

const __dirname = dirname(fileURLToPath(import.meta.url));
const repoRoot = join(__dirname, "..", "..");

type ParserSeam = { file: string; rawFieldPattern: RegExp; guardedPattern: RegExp; label: string };

const PARSER_SEAMS: ParserSeam[] = [
  {
    // Feeds AccountShellProvider's `initialSession` prop on every page
    // (app/layout.tsx) — the RSC payload carrier this whole fix exists for.
    file: "lib/shell/account-shell-payload.ts",
    rawFieldPattern: /email:\s*requireString\(payload\.account\.email/,
    guardedPattern: /email:\s*obfuscateEmailForSsr\(requireString\(payload\.account\.email/,
    label: "account-shell-payload.ts: account.email is obfuscated before leaving the parser",
  },
  {
    // Feeds FleetShell's ownerEmailObfuscated prop -> PrimaryRail's owner row.
    file: "lib/workspace/workspace-bootstrap.ts",
    rawFieldPattern: /email:\s*requireString\(account\.email/,
    guardedPattern: /email:\s*obfuscateEmailForSsr\(requireString\(account\.email/,
    label: "workspace-bootstrap.ts: account.email is obfuscated before leaving the parser",
  },
];

for (const seam of PARSER_SEAMS) {
  const source = readFileSync(join(repoRoot, seam.file), "utf8");
  assert(seam.guardedPattern.test(source), `${seam.label} (guard present)`);
  // The raw (unobfuscated) assignment shape must not appear anywhere in the
  // file — if it does, either the guard was reverted, or a second,
  // unguarded assignment was added alongside it. This pattern requires
  // "email:" to be followed IMMEDIATELY (past whitespace) by
  // "requireString(" with nothing in between, so it does not accidentally
  // match inside the guarded call (which has obfuscateEmailForSsr( in
  // between) — verified by the guard-present assertion above passing on
  // the same source.
  assert(!seam.rawFieldPattern.test(source), `${seam.label} (no unguarded raw assignment remains)`);
}

// PrimaryRail.tsx: the obfuscated prop must only ever reach JSX text
// through useRevealedEmail()'s resolved value, never directly. This is the
// component that renders visible DOM text (the confirmed original bug —
// the rail's owner row), unlike the AccountShellProvider path where
// nothing renders account.email as text at all.
{
  const file = "lib/workspace/fleet/PrimaryRail.tsx";
  const source = readFileSync(join(repoRoot, file), "utf8");
  assert(
    /from ["']@\/lib\/shell\/use-revealed-email["']/.test(source),
    `${file}: imports useRevealedEmail`,
  );
  assert(
    /useRevealedEmail\(ownerEmailObfuscated\)/.test(source),
    `${file}: calls useRevealedEmail on the obfuscated prop`,
  );
  // The two owner-row render sites must read the resolved/derived text
  // variables, never the raw prop. Anchored on ">{" (a JSX text-node
  // position) rather than a bare "{ownerEmailObfuscated}", which would
  // also match the legitimate prop-forwarding call further up
  // (<AccountMenu ownerEmailObfuscated={ownerEmailObfuscated} />).
  assert(
    !/>\{\s*ownerEmailObfuscated\b/.test(source),
    `${file}: ownerEmailObfuscated is never interpolated directly into JSX text`,
  );
}

// ─── Summary ────────────────────────────────────────────────────────────────

console.log(`\n${passed} passed, ${failed} failed`);
if (failed > 0) {
  process.exit(1);
}
