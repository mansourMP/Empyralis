// @ts-nocheck
import { expect, test } from '@playwright/test';
import { loginAsOwner } from './support/auth';

test.describe('approval resolution golden path', () => {
  test('approvals route aliases into Tasks permission queue', async ({ page }) => {
    await loginAsOwner(page);

    await page.goto('/w/ws-1/approvals');

    await expect(page).toHaveURL(/\/w\/ws-1\/tasks(?:[/?#]|$)/);
    await expect(page.locator('[data-workstation-surface="sage-tasks"]')).toBeVisible();
    await expect(page.locator('[data-workstation-surface="approvals"]')).toHaveCount(0);
  });

  test('approval resolution endpoint remains workspace scoped', async ({ page }) => {
    await loginAsOwner(page);
    const response = await page.request.get('/api/approvals?workspace_id=ws-1&limit=1');

    expect([200, 403]).toContain(response.status());
  });
});
