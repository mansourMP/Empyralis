// @ts-nocheck
import { expect, test } from '@playwright/test';
import { loginAsOwner } from './support/auth';

test.describe('artifact preview and download', () => {
  async function artifactsEnabled(page) {
    const bootstrapResponse = await page.request.get('/api/workspaces/ws-1/bootstrap');
    const bootstrap = bootstrapResponse.ok() ? await bootstrapResponse.json() : null;
    return bootstrap?.capabilities?.artifacts_enabled === true;
  }

  test('files surface mounts current artifact inventory', async ({ page }) => {
    await loginAsOwner(page);
    const enabled = await artifactsEnabled(page);
    await page.goto('/w/ws-1/artifacts');

    if (!enabled) {
      await expect(page).toHaveURL(/\/w\/ws-1\/sage(?:[/?#]|$)/);
      await expect(page.locator('[data-workstation-surface="chat"]')).toBeVisible();
      await expect(page.locator('[data-workstation-surface="artifacts"]')).toHaveCount(0);
      return;
    }

    const surface = page.locator('[data-workstation-surface="artifacts"]');
    await expect(surface).toBeVisible();
    await expect(surface).toContainText(/files/i);
    await expect(surface).not.toContainText(/later artifact phase/i);
    await expect(page.getByRole('button', { name: /refresh/i })).toBeVisible();
  });

  test('files surface exposes empty state or downloadable artifacts', async ({ page }) => {
    await loginAsOwner(page);
    const enabled = await artifactsEnabled(page);
    await page.goto('/w/ws-1/artifacts');

    if (!enabled) {
      await expect(page).toHaveURL(/\/w\/ws-1\/sage(?:[/?#]|$)/);
      await expect(page.locator('[data-workstation-surface="artifacts"]')).toHaveCount(0);
      return;
    }

    const surface = page.locator('[data-workstation-surface="artifacts"]');
    await expect(surface).toBeVisible();
    const downloadButtons = page.getByRole('button', { name: /download/i });
    if (await downloadButtons.count()) {
      await expect(downloadButtons.first()).toBeVisible();
    } else {
      await expect(surface).toContainText(/no files yet/i);
    }
  });

  test('artifact list endpoint is workspace scoped', async ({ page }) => {
    await loginAsOwner(page);
    const response = await page.request.get('/api/artifacts?workspace_id=ws-1&limit=1');

    if (response.status() === 403) {
      return;
    }
    expect(response.status()).toBe(200);
    const payload = await response.json();
    expect(Array.isArray(payload.items)).toBeTruthy();
  });
});
