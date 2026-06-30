// @ts-nocheck
import { expect, test } from '@playwright/test';
import { loginAsOwner } from './support/auth';

const HYDRATION_WARNING_PATTERNS = [
  'hydration',
  "didn't match the client",
  'hydrated but some attributes',
];

const CORE_SURFACES = [
  { href: '/w/ws-1/sage', selector: '[data-workstation-surface="chat"]' },
  { href: '/w/ws-1/studio', selector: '[data-workstation-surface="deployed-agents"]' },
  { href: '/w/ws-1/settings', selector: '[data-workstation-surface="settings"]' },
];
const ACCOUNT_SHELL_STORAGE_KEY = 'empyralis.account-shell.v2';

function createHydrationWarningCollector(page) {
  const messages: string[] = [];
  const onConsole = (message) => {
    const text = message.text();
    const normalized = text.toLowerCase();
    if (HYDRATION_WARNING_PATTERNS.some((pattern) => normalized.includes(pattern))) {
      messages.push(text);
    }
  };

  page.on('console', onConsole);
  return {
    messages,
    stop: () => page.off('console', onConsole),
  };
}

test.describe('account shell hydration', () => {
  test('cold loads sage, studio, and settings without hydration warnings', async ({ page }) => {
    const collector = createHydrationWarningCollector(page);
    try {
      await loginAsOwner(page);
      for (const surface of CORE_SURFACES) {
        await page.goto(surface.href, { waitUntil: 'domcontentloaded' });
        await expect(page.locator(surface.selector)).toBeVisible();
      }
      expect(collector.messages).toEqual([]);
    } finally {
      collector.stop();
    }
  });

  test('hard refreshes sage, studio, and settings without hydration warnings', async ({ page }) => {
    const collector = createHydrationWarningCollector(page);
    try {
      await loginAsOwner(page);
      for (const surface of CORE_SURFACES) {
        await page.goto(surface.href, { waitUntil: 'domcontentloaded' });
        await expect(page.locator(surface.selector)).toBeVisible();
        await page.reload({ waitUntil: 'domcontentloaded' });
        await expect(page.locator(surface.selector)).toBeVisible();
      }
      expect(collector.messages).toEqual([]);
    } finally {
      collector.stop();
    }
  });

  test('hydrates the account shell after login and shows real memberships', async ({ page }) => {
    await loginAsOwner(page);
    await expect(page.locator('[data-workstation-switcher="rail"]')).toBeVisible();
    await expect(page.locator('[data-workstation-titlebar="root"]')).toBeVisible();
  });

  test('hard refresh on a workspace route preserves the server-hydrated shell session', async ({ page }) => {
    await loginAsOwner(page);
    await page.goto('/w/ws-1/sage', { waitUntil: 'domcontentloaded' });
    await page.reload({ waitUntil: 'domcontentloaded' });

    await expect(page.locator('[data-workstation-switcher="rail"]')).toBeVisible();
    await expect(page.locator('[data-workstation-titlebar="root"]')).toBeVisible();
  });

  test('anonymous users are redirected out of account routes', async ({ page }) => {
    await page.goto('/w/ws-1/chat');
    await expect(page).toHaveURL(/\/login$/);
  });

  test('switching authenticated accounts discards the previous membership snapshot before render', async ({ page, context }) => {
    await loginAsOwner(page);
    await page.evaluate((storageKey) => {
      window.localStorage.setItem(storageKey, JSON.stringify({
        accountId: 'stale-account-id',
        selectedWorkspaceId: 'ws-first',
        lastVisitedWorkspaceRouteById: {
          'ws-first': '/w/ws-first/chat',
        },
        workspaceRouteStateById: {
          'ws-first': 'stale-state-token',
        },
        globalTheme: 'system',
        globalChromePreferences: {
          tenantSwitcherCollapsed: false,
        },
      }));
    }, ACCOUNT_SHELL_STORAGE_KEY);
    await context.clearCookies();

    await page.goto('/login');
    await page.getByLabel('Email').fill('owner@example.com');
    await page.getByLabel('Password').fill('password-123');
    await page.getByRole('button', { name: /log in/i }).click();

    await expect(page.locator('a[href^="/w/ws-first/"]')).toHaveCount(0);
  });
});
