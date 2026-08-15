/**
 * next.config.ts's LEGACY_REDIRECTS unit test — guards against the bare
 * workspace route (`/w/:workspaceId`, no section segment) ever being
 * redirected away again.
 *
 * That redirect existed from Phase 7A (before FleetHome existed) until
 * 2026-08-13: frontend/app/(account)/w/[workspaceId]/page.tsx renders
 * FleetHome there ON PURPOSE ("workspace landing = Fleet Home, not a
 * redirect" — see that file's own comment, plus Breadcrumbs.tsx's), but
 * Next's redirects() runs ahead of the router, so the stale rule made that
 * page permanently unreachable — every fresh signup, and every visit to
 * the workspace root, was silently bounced into the Agents tab's empty
 * "No agents yet" state instead. A behavioral/e2e test would only catch
 * this by loading the real page; this catches it at the config layer,
 * where the actual regression happened.
 *
 * Run: npx tsx next.config.test.ts
 */

import nextConfig from './next.config';

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

async function main() {
  if (typeof nextConfig.redirects !== 'function') {
    throw new Error('next.config.ts must export an async redirects() function for this test to check.');
  }
  const redirects = await nextConfig.redirects();

  const bareWorkspaceRedirect = redirects.find((r) => r.source === '/w/:workspaceId');
  assert(
    bareWorkspaceRedirect === undefined,
    `the bare workspace route must not be redirected (found: ${JSON.stringify(bareWorkspaceRedirect)})`,
  );

  // Every rail destination must be reachable, for the same reason: a
  // redirect resolves ahead of the router, so a rule pointing AT one of
  // these would make a linked page unreachable with nothing in React ever
  // saying so. Derived from the REAL rail list rather than a hand-typed
  // copy, so a destination added there is covered the day it lands.
  const { RAIL_ITEMS } = await import('./lib/workspace/fleet/primary-rail-nav');
  for (const item of RAIL_ITEMS) {
    const source = `/w/:workspaceId/${item.segment}`;
    const clash = redirects.find((r) => r.source === source);
    assert(
      clash === undefined,
      `rail destination "${item.label}" (${source}) must not be a redirect source (found: ${JSON.stringify(clash)})`,
    );
  }

  // Sanity: prove this test would actually catch the regression, not just
  // pass vacuously because the list is empty or malformed.
  assert(redirects.length > 5, 'LEGACY_REDIRECTS still has its other, intentional entries');
  assert(
    redirects.some((r) => r.source === '/w/:workspaceId/fleet' && r.destination === '/w/:workspaceId/agents'),
    'a real sibling redirect (/fleet -> /agents) is still present and shaped as expected',
  );

  console.log(`\n${passed} passed, ${failed} failed`);
  if (failed > 0) {
    process.exit(1);
  }
}

void main();
