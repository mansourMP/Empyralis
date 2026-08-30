/**
 * Structural guard for the blank-shell trap FleetShellDecider.tsx's own
 * header comment already documents but nothing enforces: a route under
 * app/(account)/w/[workspaceId]/{segment}/... whose top-level segment is
 * missing from SHELL_SEGMENTS (RAIL_ITEMS + NON_RAIL_SHELL_SEGMENTS) falls
 * through to `shellSlot`, which is `null` in production (see the workspace
 * layout's own comment). The page then renders the rail and the topbar and
 * NOTHING ELSE — no error, no console warning, no failed network request.
 * A completely blank content area that looks exactly like a slow load.
 *
 * Confirmed instances, all found by a human noticing a blank page rather
 * than by a test, all now fixed by hand-listing the segment in
 * NON_RAIL_SHELL_SEGMENTS:
 *   - "settings" (2026-08-16)
 *   - "context"  (2026-08-20 / re-caught and actually fixed 2026-08-28)
 *   - "operator" (2026-08-30, commit 7b8dfe26) — caught only by loading the
 *     page in a real browser; tsc and the whole unit suite were green with
 *     the page rendering blank.
 *
 * Documenting a trap is not guarding it — CLAUDE.md is explicit that a rule
 * with a possible drift test belongs in the test. This file is that test.
 *
 * WHAT IT SCANS vs WHAT IT PARSES — deliberately two different sources, so
 * this cannot become a check that derives its own expectations from the
 * thing it checks and reports "passed" while blind (CLAUDE.md):
 *   - ACTUAL: every top-level segment directory under
 *     app/(account)/w/[workspaceId]/ that resolves to a real route (bears a
 *     page.tsx or layout.tsx, directly or nested) — read straight off the
 *     filesystem with readdirSync, independent of any source file's claims.
 *   - EXPECTED: RAIL_ITEMS is a real import of the same array
 *     PrimaryRail.tsx renders (primary-rail-nav.test.ts's own discipline);
 *     NON_RAIL_SHELL_SEGMENTS has no export, so its entries are parsed out
 *     of FleetShellDecider.tsx's own source text instead of being re-typed
 *     by hand here — the exact shape of list this whole bug class is about
 *     drifting out of sync with.
 *
 * Run: npx tsx lib/workspace/fleet/shell-segment-coverage.test.ts
 */

import { readFileSync, readdirSync, statSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

import { RAIL_ITEMS } from "./primary-rail-nav";

const HERE = dirname(fileURLToPath(import.meta.url));
// This file lives at lib/workspace/fleet/ — the frontend root is three levels up.
const FRONTEND_ROOT = join(HERE, "..", "..", "..");
const WORKSPACE_ROUTE_ROOT = join(FRONTEND_ROOT, "app", "(account)", "w", "[workspaceId]");

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

/**
 * Segments that are real route directories on disk but are deliberately NOT
 * required to appear in SHELL_SEGMENTS, each with the reason inline — an
 * unexplained exclusion is how this guard rots (CLAUDE.md). Keep tiny.
 *
 * All three entries below are next.config.ts LEGACY_REDIRECTS targets,
 * verified directly against next.config.ts rather than assumed: the redirect
 * resolves BEFORE the Next.js router ever reaches FleetShellDecider (CLAUDE.md,
 * "Recurring failure modes" — "A next.config redirect resolves BEFORE the
 * router, so it can make a real page unreachable with no React error
 * anywhere"), so the page.tsx files these directories contain are dead code
 * that can never actually hit the blank-shell trap this file guards against.
 */
const EXCLUDED_SEGMENTS: Record<string, string> = {
  applications:
    "next.config.ts redirects /w/:workspaceId/applications and /w/:workspaceId/applications/:appId " +
    "to /w/:workspaceId/agents before the router reaches FleetShellDecider — the real page.tsx under " +
    "app/(account)/w/[workspaceId]/applications/[appId] is unreachable by any live request.",
  gateway:
    "next.config.ts redirects /w/:workspaceId/gateway to /w/:workspaceId/hardware before the router " +
    "reaches FleetShellDecider — same unreachable-real-page shape as \"applications\" above.",
  "gateway-activity":
    "next.config.ts redirects /w/:workspaceId/gateway-activity to /w/:workspaceId/hardware before the " +
    "router reaches FleetShellDecider — same shape as \"gateway\" above.",
};

/** True if `dir`, or anything nested inside it, bears a page.tsx or
 *  layout.tsx — so a pure grouping folder with no route of its own does not
 *  produce a false failure. */
function hasRouteFile(dir: string): boolean {
  for (const entry of readdirSync(dir)) {
    const full = join(dir, entry);
    const st = statSync(full);
    if (st.isDirectory()) {
      if (hasRouteFile(full)) return true;
    } else if (entry === "page.tsx" || entry === "layout.tsx") {
      return true;
    }
  }
  return false;
}

/** Every top-level segment directory under `root` that actually resolves to
 *  a route. A route-group directory (wrapped in parens) contributes no URL
 *  segment of its own and is skipped defensively — none exists at this
 *  level today, but app/(account)/ one level up is exactly that shape. */
function topLevelRouteSegments(root: string): string[] {
  const segments: string[] = [];
  for (const entry of readdirSync(root)) {
    if (entry.startsWith("(")) continue; // route group — not a URL segment
    const full = join(root, entry);
    if (!statSync(full).isDirectory()) continue;
    if (hasRouteFile(full)) segments.push(entry);
  }
  return segments;
}

// ── EXPECTED, source 1: the rail's real segments ───────────────────────────
// The same RAIL_ITEMS array FleetShellDecider.tsx itself derives its rail
// half from — never re-typed here.
const railSegments = RAIL_ITEMS.map((item) => item.segment);

// ── EXPECTED, source 2: NON_RAIL_SHELL_SEGMENTS, parsed from FleetShellDecider.tsx ──
const deciderPath = join(HERE, "FleetShellDecider.tsx");
const deciderSource = readFileSync(deciderPath, "utf8");
const nonRailArrayMatch = /const NON_RAIL_SHELL_SEGMENTS = \[([\s\S]*?)\n\];/.exec(deciderSource);
assert(
  nonRailArrayMatch !== null,
  "FleetShellDecider.tsx still declares a `const NON_RAIL_SHELL_SEGMENTS = [ ... ];` array in the shape this parser expects — " +
    "if this fails, the array was renamed or reshaped and this test's parser needs updating alongside it",
);
const nonRailArrayBody = nonRailArrayMatch ? nonRailArrayMatch[1] : "";
// Strip `//` line comments before matching quoted strings, so a segment name
// mentioned in PROSE inside a comment (this array's own comments explain,
// in quotes, why "agents", "sage", "settings" and "context" are or aren't
// hand-listed) is never mistaken for an actual array entry.
const nonRailArrayCode = nonRailArrayBody
  .split("\n")
  .map((line) => {
    const idx = line.indexOf("//");
    return idx === -1 ? line : line.slice(0, idx);
  })
  .join("\n");
const nonRailSegments = [...nonRailArrayCode.matchAll(/"([a-zA-Z0-9_-]+)"/g)].map((m) => m[1]);

const coveredSegments = new Set([...railSegments, ...nonRailSegments]);

// ── Canaries ────────────────────────────────────────────────────────────
// "A check that derives its expectations from the thing it checks is blind
// and reports 'passed'. Give every source-scanning test a canary that fails
// loudly when the scan reaches nothing." (CLAUDE.md) Each of the three
// independent scans above gets its own canary, because any one of them can
// break (a renamed export, a reshaped array, a moved directory) while the
// others stay healthy and would otherwise mask the break with a vacuous
// "0 violations found" pass.
assert(
  railSegments.length > 0 && railSegments.includes("agents"),
  "canary: RAIL_ITEMS import found real rail segments, including the known 'agents' destination",
);
assert(
  nonRailSegments.length > 0 && nonRailSegments.includes("settings"),
  "canary: parsed NON_RAIL_SHELL_SEGMENTS out of FleetShellDecider.tsx's own source, including the known 'settings' entry",
);
assert(coveredSegments.size > 0, "canary: the combined covered-segment set is non-empty");

// ── ACTUAL: every real route segment on disk ───────────────────────────────
const routeSegments = topLevelRouteSegments(WORKSPACE_ROUTE_ROOT);
assert(
  routeSegments.length > 0 && routeSegments.includes("agents"),
  "canary: the filesystem scan reached app/(account)/w/[workspaceId]/ and found the known 'agents' segment — " +
    "a scan that finds nothing must fail loudly here, never pass vacuously with zero segments checked",
);

// ── The guard proper ────────────────────────────────────────────────────
for (const segment of routeSegments) {
  if (segment in EXCLUDED_SEGMENTS) continue;
  assert(
    coveredSegments.has(segment),
    `"${segment}" is a real route under app/(account)/w/[workspaceId]/${segment}/ but is in neither ` +
      `RAIL_ITEMS (primary-rail-nav.ts) nor NON_RAIL_SHELL_SEGMENTS (FleetShellDecider.tsx). Every visit to ` +
      `/w/{workspaceId}/${segment} renders the rail and the topbar and NOTHING ELSE — shellSlot is null in ` +
      `production, so there is no error, no console warning, and no failed network request, only a blank pane ` +
      `that looks exactly like a slow load. FIX: add "${segment}" to NON_RAIL_SHELL_SEGMENTS in ` +
      `frontend/lib/workspace/fleet/FleetShellDecider.tsx, with a reason next to the existing entries — unless ` +
      `this segment genuinely belongs on the rail, in which case add it to RAIL_ITEMS in primary-rail-nav.ts instead.`,
  );
}

// The exclusion list rots the same way the segment list does: guard it too.
for (const excluded of Object.keys(EXCLUDED_SEGMENTS)) {
  assert(
    routeSegments.includes(excluded),
    `EXCLUDED_SEGMENTS entry "${excluded}" no longer matches a real route directory on disk — remove the stale exclusion`,
  );
}

console.log(`\n${passed} passed, ${failed} failed`);
if (failed > 0) {
  process.exit(1);
}
