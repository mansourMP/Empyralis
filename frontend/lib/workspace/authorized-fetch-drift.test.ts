/**
 * MAN-324: a raw browser `fetch()` to Empyralis's own backend never
 * survives an access-token expiry mid-flow — it just 401s and leaves
 * whatever the person was doing unsaved, exactly the "every in-flight
 * request then 401s simultaneously, with no warning" shape
 * fleet-authorized-fetch.ts's own header comment already describes (that
 * fix covered only the create-agent wizard). A context-layer agent hit this
 * for real in a browser, once mid-document-edit. The triage found ~33 raw
 * `fetch()` call sites in fleet-data.ts alone; a fuller sweep across
 * frontend/lib and frontend/app found 96 more spread across 38 other
 * client files, all fixed the same way (route through the EXISTING
 * fleetAuthorizedFetch/auth-client.refresh() mechanism — not a second
 * implementation).
 *
 * A sweep fixes today's violations and misses the next one. This is the
 * structural half: it scans every .ts/.tsx file under frontend/lib and
 * frontend/app (excluding tests and Next.js server route handlers) for a
 * raw `fetch(` call, and requires every hit to be EITHER wrapped as
 * `fleetAuthorizedFetch(` OR named in ALLOWLIST below with a written
 * reason — the same allowlist-with-a-verdict idiom this repo already uses
 * elsewhere (safe-render-url.test.ts's SEAMS list;
 * test_run_state_scope_fails_closed.py's FailOpenScopeFilterDriftTests).
 * A behavioural test cannot catch a newly-added raw fetch() — it will
 * behave perfectly until a token happens to expire mid-request, which is
 * exactly how the 38-file version of this bug went unnoticed. This test
 * fails the moment someone reintroduces the pattern, before that.
 *
 * Two structural exclusions, not part of the hand-maintained allowlist:
 *   - `app/api/**\/route.ts` — Next.js SERVER route handlers. They run on
 *     the Next.js server process, never hold the browser's session cookie,
 *     and a 401 there is a different problem entirely (verified directly:
 *     these proxy to the backend with their own service-level auth, not a
 *     browser-refreshable session).
 *   - Any file starting with `import 'server-only'` — the same reasoning,
 *     structurally enforced rather than named file-by-file, so a NEW
 *     server-only file never needs an allowlist entry to pass.
 *
 * Run: npx tsx lib/workspace/authorized-fetch-drift.test.ts
 */

import { readFileSync, readdirSync, statSync } from "node:fs";
import { dirname, join, relative } from "node:path";
import { fileURLToPath } from "node:url";

const HERE = dirname(fileURLToPath(import.meta.url));
// This file lives at lib/workspace/ — the frontend root is two levels up.
const FRONTEND_ROOT = join(HERE, "..", "..");
const SCAN_DIRS = ["lib", "app"];
const EXCLUDE_DIR_NAMES = new Set(["node_modules", ".next"]);

/**
 * file path (relative to frontend/) -> reason it's allowed to call the
 * browser's raw `fetch()` directly instead of `fleetAuthorizedFetch()`.
 * Every entry here is a DELIBERATE exception, not an oversight — adding a
 * new raw fetch() anywhere else must either route through
 * fleetAuthorizedFetch or earn its own entry here with a real reason, the
 * same discipline this repo already applies to RLS coverage exceptions and
 * CHANNEL_SAFE_CODES.
 */
const ALLOWLIST: Record<string, string> = {
  "lib/workspace/fleet/fleet-authorized-fetch.ts":
    "the wrapper's own implementation — its two raw fetch() calls (the first attempt, the post-refresh retry) ARE the mechanism.",
  "lib/auth/auth-client.ts":
    "pre-auth bootstrap (login/signup/logout/verify-email) plus refresh() itself and awaitBrowserAuthReady's readiness poll — none of these can route through fleetAuthorizedFetch without either running before a session exists or recursively calling the same refresh() they ARE.",
  "lib/workspace/workspace-services.tsx":
    "WorkspaceTransportAdapter.request() already has its own, more complete refresh-and-retry-on-401 (configurable retry count/timeout, single-flighted via the SAME auth-client.refresh()) predating fleetAuthorizedFetch — see its own request() method. Wrapping its internal performRequest()'s fetch() a second time would double the retry logic, not fix a gap.",
  "app/continue/page.tsx":
    "a best-effort, fire-and-forget marketing-attribution beacon (keepalive: true, POST /api/marketplace/upgrade-click) that may legitimately run before or without a session; retrying it via a session refresh is not the right response to a 401 here.",
  "app/invite/[code]/page.tsx":
    "invite-code validation happens BEFORE the person has an account or session (POST /api/pilot/invites/validate) — there is no session to refresh.",
  "app/preview/PublicAgentPreviewClient.tsx":
    "a public marketplace preview (GET /api/marketplace/agents) — works for anonymous visitors, credentials: 'include' is opportunistic, not required.",
  "app/global-error.tsx":
    "the last-resort root error boundary — root-error-boundary-coverage.test.ts requires it import NOTHING from " +
    "@/lib or @/app (if the root layout itself crashed, shared code cannot be assumed to have survived), so its " +
    "'Sign in again' recovery link cannot route through fleetAuthorizedFetch. The fetch() is a best-effort, " +
    "fire-and-forget POST /api/auth/logout (keepalive: true, no preventDefault) that heals a stuck CSRF cookie " +
    "twin before the anchor's own navigation to /login fires regardless of whether it succeeds — there is no " +
    "session left to refresh by the time this runs, and refreshing is not the right response to a 401 here anyway.",
};

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

type ScanHit = { file: string; line: number; text: string };

function walk(dir: string, out: string[]): string[] {
  for (const entry of readdirSync(dir)) {
    if (EXCLUDE_DIR_NAMES.has(entry)) continue;
    const full = join(dir, entry);
    const st = statSync(full);
    if (st.isDirectory()) {
      walk(full, out);
    } else if (/\.(ts|tsx)$/.test(entry)) {
      out.push(full);
    }
  }
  return out;
}

function isServerOnly(source: string): boolean {
  return /^\s*import\s+['"]server-only['"]/m.test(source);
}

function isNextRouteHandler(relPath: string): boolean {
  return /^app\/.*\/route\.ts$/.test(relPath) || /^app\/route\.ts$/.test(relPath);
}

function findRawFetchHits(source: string): { line: number; text: string }[] {
  const hits: { line: number; text: string }[] = [];
  source.split("\n").forEach((line, idx) => {
    const trimmed = line.trim();
    if (trimmed.startsWith("//") || trimmed.startsWith("*") || trimmed.startsWith("/*")) return;
    // \bfetch\( matches a bare `fetch(` call. It does NOT match
    // `fleetAuthorizedFetch(` (case-sensitive; that identifier contains
    // "Fetch(" with a capital F, not "fetch(") or `.fetch(` on some other
    // object — a real false-positive risk that's been checked against the
    // current tree (zero hits) and is cheap to re-check if this ever
    // trips: `grep -n "efetch(\|Refetch(\|prefetch(" <file>`.
    if (/\bfetch\(/.test(line)) {
      hits.push({ line: idx + 1, text: trimmed });
    }
  });
  return hits;
}

function main(): void {
  const files: string[] = [];
  for (const d of SCAN_DIRS) walk(join(FRONTEND_ROOT, d), files);
  assert(files.length > 100, `sanity: scanned a real number of files (got ${files.length})`);

  const violations: ScanHit[] = [];
  const seenAllowlistEntries = new Set<string>();

  for (const absPath of files) {
    const relPath = relative(FRONTEND_ROOT, absPath).split("\\").join("/");
    if (relPath.endsWith(".test.ts") || relPath.endsWith(".test.tsx")) continue;
    if (isNextRouteHandler(relPath)) continue;

    const source = readFileSync(absPath, "utf8");
    if (isServerOnly(source)) continue;

    const hits = findRawFetchHits(source);
    if (hits.length === 0) continue;

    if (relPath in ALLOWLIST) {
      seenAllowlistEntries.add(relPath);
      continue;
    }

    for (const hit of hits) {
      violations.push({ file: relPath, line: hit.line, text: hit.text });
    }
  }

  assert(
    violations.length === 0,
    violations.length === 0
      ? "no unwrapped raw fetch() to an authorized endpoint"
      : `${violations.length} unwrapped raw fetch() call(s) found outside the allowlist:\n` +
          violations.map((v) => `    ${v.file}:${v.line}: ${v.text}`).join("\n") +
          "\n  Route each through fleetAuthorizedFetch(), or add a reasoned entry to ALLOWLIST in this test.",
  );

  // Every allowlist entry must still be real — a stale entry (the file was
  // deleted, or its fetch() was removed/wrapped some other way) would let
  // the allowlist quietly stop meaning anything, the same "dead entry"
  // failure mode CLAUDE.md documents for CHANNEL_OWNER_SAFE_CODES.
  for (const relPath of Object.keys(ALLOWLIST)) {
    assert(seenAllowlistEntries.has(relPath), `stale allowlist entry — no raw fetch() found in ${relPath} anymore; remove it`);
  }

  // Sanity: prove this test actually exercises real files with real fetch()
  // calls, not an empty/misconfigured scan that passes vacuously.
  assert(seenAllowlistEntries.size >= 6, "the allowlist's own entries were found and matched during the scan");

  console.log(`\n${passed} passed, ${failed} failed`);
  if (failed > 0) {
    process.exit(1);
  }
}

main();
