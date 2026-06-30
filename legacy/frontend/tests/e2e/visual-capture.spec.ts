// @ts-nocheck
import path from 'node:path';
import { expect, test } from '@playwright/test';

const SNAPSHOT_LABEL = process.env.VISUAL_LABEL ?? 'snapshot';

const SURFACES: Array<{
  id: string;
  href: string;
  selector: string;
}> = [
  { id: 'sage', href: '/w/ws-1/sage', selector: '[data-workstation-surface="chat"]' },
  { id: 'studio', href: '/w/ws-1/studio', selector: '[data-workstation-surface="deployed-agents"]' },
  { id: 'settings', href: '/w/ws-1/settings', selector: '[data-workstation-surface="settings"]' },
];

async function authenticateOwner(page) {
  await page.context().clearCookies();
  const response = await page.request.post('/api/auth/login', {
    data: {
      email: 'owner@example.com',
      password: 'password-123',
      channel: 'web',
    },
  });
  expect(response.ok()).toBeTruthy();
}

async function waitForSurfaceReady(page, surface: (typeof SURFACES)[number]) {
  await page.goto(surface.href, { waitUntil: 'networkidle' });
  await page.waitForSelector(surface.selector, { state: 'visible', timeout: 20_000 });
  await expect(page.locator('[data-workstation-pane="rail"]')).toBeVisible({ timeout: 20_000 });
  await expect(page.locator('[data-workstation-titlebar="root"]')).toBeVisible({ timeout: 20_000 });
  await expect(page.locator('.app-skeleton-block')).toHaveCount(0, { timeout: 20_000 });
  await expect(page.locator('body')).not.toContainText(/\bLoading\b/i, { timeout: 20_000 });
  if (surface.id === 'sage') {
    await expect(page.locator('[data-workstation-chat-composer="root"]')).toBeVisible({ timeout: 20_000 });
    await expect(page.locator('.app-chat-empty--hero')).toHaveCount(0, { timeout: 20_000 });
    await expect(page.locator('body')).not.toContainText(/A calm space to think out loud\./i, { timeout: 20_000 });
  }
  await page.waitForTimeout(750);
  await page.evaluate(() => {
    const activeElement = document.activeElement;
    if (
      activeElement instanceof HTMLInputElement
      || activeElement instanceof HTMLTextAreaElement
      || activeElement instanceof HTMLElement
      && activeElement.isContentEditable
    ) {
      activeElement.blur();
    }
  });
}

test.describe('visual capture', () => {
  test.describe.configure({ mode: 'serial' });

  test('captures product surfaces', async ({ page }) => {
    test.setTimeout(90_000);
    await page.setViewportSize({ width: 1512, height: 982 });
    await page.emulateMedia({ colorScheme: 'dark' });
    await page.addInitScript((storageKey: string) => {
      try {
        const raw = window.localStorage.getItem(storageKey);
        const next = raw ? JSON.parse(raw) : {};
        window.localStorage.setItem(storageKey, JSON.stringify({ ...next, globalTheme: 'dark' }));
      } catch {
        window.localStorage.setItem(storageKey, JSON.stringify({ globalTheme: 'dark' }));
      }
    }, 'empyralis.account-shell.v2');
    await authenticateOwner(page);

    for (const surface of SURFACES) {
      await waitForSurfaceReady(page, surface);
      await page.screenshot({
        path: path.join(process.cwd(), 'test-results', `${SNAPSHOT_LABEL}-${surface.id}.png`),
        fullPage: true,
      });
      if (surface.id === 'sage') {
        await page.screenshot({
          path: path.join(process.cwd(), 'test-results', `${SNAPSHOT_LABEL}-main-shell.png`),
          fullPage: true,
        });
      }
    }
  });
});
