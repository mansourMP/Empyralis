// @ts-nocheck
import { expect, test } from '@playwright/test';

import { loginAsOwner } from './support/auth';

// The legacy workstation shell (data-workstation-*) is gone -- see
// account-shell-hydration.spec.ts's CORE_SURFACES comment for the full
// picture. Chat is no longer a full-page route: '/sage' now immediately
// server-redirects to the bare workspace route (FleetHome), which is the
// real surface a cold load of the old chat route resolves to today.
// Confirmed by driving both routes in a browser, not by reading the route
// table.
const SURFACES = [
  ['/w/ws-1', '.fleet-title'],
  ['/w/ws-1/settings', '.settings-shell-body'],
];

test.describe('non-scaffold surface sweep', () => {
  test.describe.configure({ mode: 'serial' });

  test('mounts real components for canonical workspace surfaces', async ({ page }) => {
    await loginAsOwner(page);
    for (const [href, selector] of SURFACES) {
      await page.goto(href, { waitUntil: 'domcontentloaded' });
      await expect(page.locator(selector)).toBeVisible();
      // The original 'data-workstation-surface="fallback"' scaffold marker
      // no longer exists anywhere in the current UI -- it was part of the
      // same removed workstation shell, not a surviving concept with a new
      // name. Its intent (never silently render a placeholder instead of
      // the real surface) is still covered by the positive assertion above:
      // these selectors only exist inside the real FleetHome/Settings
      // components, not in any fallback state.
      await page.waitForTimeout(750);
    }
  });
});
