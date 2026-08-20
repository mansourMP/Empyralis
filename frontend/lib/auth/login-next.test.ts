/**
 * MAN-358 — the signed-out landing half of deep links.
 *
 * A channel deep link is opened by whoever is reading Telegram, which is
 * usually a browser with no session. `(account)/layout.tsx` used to
 * `redirect('/login')` with nothing attached, so signing in landed the reader
 * on the workspace root and the destination they tapped was gone.
 *
 * What this pins is the SAFETY rule, because `next` is a redirect target
 * built from a request header and the failure mode of getting it wrong is an
 * open redirect on the sign-in page — a phishing primitive, not a cosmetic
 * bug. Every rejection below degrades to '/', never to an error, because '/'
 * is exactly where the product used to send everybody anyway.
 *
 * Run: npx tsx lib/auth/login-next.test.ts
 */

import { REQUEST_PATHNAME_HEADER, loginHrefForPath, safeNextPath } from './login-next';

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

// ── The destination a real deep link produces ──────────────────────────────
const TASK_PATH = '/w/ws_9f2c1a4b7d8e/projects/proj_4c8a1f2e9b3d/tasks/task_69d6567ebfd3485a';

assert(safeNextPath(TASK_PATH) === TASK_PATH, 'a task deep link survives unchanged');
assert(
  loginHrefForPath(TASK_PATH) === `/login?next=${encodeURIComponent(TASK_PATH)}`,
  'the login href carries the task path, encoded',
);
assert(
  safeNextPath(decodeURIComponent(encodeURIComponent(TASK_PATH))) === TASK_PATH,
  'encode/decode round-trips, so /login gets back exactly what was redirected',
);
assert(
  loginHrefForPath(`${TASK_PATH}?comment=c_1`) === `/login?next=${encodeURIComponent(`${TASK_PATH}?comment=c_1`)}`,
  'a query string on the deep link is carried too, not truncated at the path',
);

// ── Open-redirect rejections. Each of these is a real attack shape ─────────
assert(safeNextPath('https://evil.example/steal') === '/', 'absolute URL is refused');
assert(safeNextPath('//evil.example/steal') === '/', 'protocol-relative URL is refused');
assert(safeNextPath('/\\evil.example') === '/', 'backslash disguise is refused');
assert(safeNextPath('\\\\evil.example') === '/', 'double backslash is refused');
assert(safeNextPath('javascript:alert(1)') === '/', 'a non-path scheme is refused');
assert(safeNextPath('w/ws_1/projects/p_1') === '/', 'a relative path with no leading slash is refused');
assert(safeNextPath('') === '/', 'empty is refused');
assert(safeNextPath('   ') === '/', 'whitespace is refused');

// ── '/' means "no parameter", not "?next=%2F" ──────────────────────────────
assert(loginHrefForPath('/') === '/login', 'the workspace root adds no next parameter');
assert(loginHrefForPath('') === '/login', 'a missing path adds no next parameter');
assert(
  loginHrefForPath('https://evil.example/steal') === '/login',
  'a rejected target adds no next parameter rather than one pointing at /',
);

// ── The header name is shared, not typed twice ─────────────────────────────
// proxy.ts writes it and (account)/layout.tsx reads it; if this constant ever
// stops being the single source, the redirect silently loses its destination
// with no error anywhere — the exact failure this whole module exists to fix.
assert(REQUEST_PATHNAME_HEADER === REQUEST_PATHNAME_HEADER.toLowerCase(), 'header name is lowercase (Headers normalises)');
assert(REQUEST_PATHNAME_HEADER.startsWith('x-'), 'header name is namespaced as a custom header');

// ── Structural: the two ends of the header are actually wired ─────────────
// A behavioural test cannot see this. proxy.ts sets the header and
// (account)/layout.tsx reads it; either half silently dropped leaves a
// redirect that still works, still compiles, and always lands on the
// workspace root — which is precisely the bug before this change, and is
// invisible without opening a browser signed out.
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';

const FRONTEND_ROOT = resolve(__dirname, '..', '..');

function source(relative: string): string {
  const text = readFileSync(resolve(FRONTEND_ROOT, relative), 'utf8');
  // Canary: a path that stops resolving must FAIL, never quietly assert
  // nothing against an empty string (CLAUDE.md — every source-scanning test
  // gets a canary, after exec-file-timeout-child-leak.test.ts was found
  // scanning a directory with no .ts files in it at all).
  if (text.trim().length < 200) {
    throw new Error(`source scan found no real content at ${relative} — the scan is not looking where it thinks`);
  }
  return text;
}

const proxySource = source('proxy.ts');
const layoutSource = source('app/(account)/layout.tsx');

assert(
  proxySource.includes("from '@/lib/auth/login-next'") && proxySource.includes('REQUEST_PATHNAME_HEADER'),
  'proxy.ts writes the pathname header through the shared constant',
);
assert(
  proxySource.includes('requestHeaders.set(REQUEST_PATHNAME_HEADER'),
  'proxy.ts SETS (never appends) the header, so a client-supplied one is overwritten',
);
assert(
  !proxySource.includes(`'${REQUEST_PATHNAME_HEADER}'`) && !proxySource.includes(`"${REQUEST_PATHNAME_HEADER}"`),
  'proxy.ts does not re-type the header name as a literal',
);
assert(
  layoutSource.includes('REQUEST_PATHNAME_HEADER') && layoutSource.includes('loginHrefForPath'),
  'the account layout reads the header and builds the login href through the shared helper',
);
assert(
  !/redirect\(\s*['"]\/login['"]\s*\)/.test(layoutSource),
  'the account layout no longer redirects to a bare /login, discarding the destination',
);

console.log(`login-next: ${passed} passed, ${failed} failed`);
if (failed > 0) {
  process.exit(1);
}
