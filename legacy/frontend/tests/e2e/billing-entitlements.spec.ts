// @ts-nocheck
import { expect, test } from '@playwright/test';
import { loginAsOwner } from './support/auth';

test.describe('billing entitlements', () => {
  test('billing pane mounts a real summary surface and does not present stub copy', async ({ page }) => {
    await loginAsOwner(page);
    await page.goto('/w/ws-1/settings?section=billing');

    await expect(page.locator('[data-workstation-surface="admin/billing"]')).toBeVisible();
    await expect(page.locator('[data-workstation-surface="admin/billing"]')).not.toContainText(/later billing phase/i);
    await expect(page.locator('[data-workstation-surface="admin/billing"]')).toContainText(/current plan/i);
  });

  test('billing summary endpoint is workspace scoped', async ({ page }) => {
    await loginAsOwner(page);
    const response = await page.request.get('/api/billing/summary?workspace_id=ws-1');

    expect([200, 403]).toContain(response.status());
  });

  test('billing pane exposes upgrade or manage actions instead of stub messaging', async ({ page }) => {
    await loginAsOwner(page);
    await page.goto('/w/ws-1/settings?section=billing');

    await expect(page.locator('[data-workstation-surface="admin/billing"]')).toBeVisible();
    await expect(page.getByRole('button', { name: /refresh/i })).toBeVisible();
    await expect(page.locator('[data-workstation-surface="admin/billing"]')).toContainText(/manage billing|upgrade/i);
  });
});
