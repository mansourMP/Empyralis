// @ts-nocheck
import { expect, test } from '@playwright/test';

import { loginAsOwner } from './support/auth';

const SURFACES = [
  ['/w/ws-1/sage', '[data-workstation-surface="chat"]'],
  ['/w/ws-1/studio', '[data-workstation-surface="deployed-agents"]'],
  ['/w/ws-1/settings', '[data-workstation-surface="settings"]'],
];

test.describe('non-scaffold surface sweep', () => {
  test.describe.configure({ mode: 'serial' });

  test('mounts real components for canonical workspace surfaces', async ({ page }) => {
    await loginAsOwner(page);
    for (const [href, selector] of SURFACES) {
      await page.goto(href, { waitUntil: 'domcontentloaded' });
      await expect(page.locator(selector)).toBeVisible();
      await expect(page.locator('[data-workstation-surface="fallback"]')).toHaveCount(0);
      await page.waitForTimeout(750);
    }
  });
});
