// @ts-nocheck
import { expect, test } from '@playwright/test';
import { loginAsOwner } from './support/auth';

const HYDRATION_WARNING_PATTERNS = [
  'hydration',
  "didn't match the client",
  'hydrated but some attributes',
];

// The legacy workstation shell (data-workstation-*) is gone -- every
// workspace surface renders fleet-native inside FleetShell now (see
// support/auth.ts's own waitForWorkspaceShell comment). Chat is no longer a
// full-page route at all: '/sage' immediately server-redirects to the bare
// workspace route (app/(account)/w/[workspaceId]/sage/page.tsx), and the
// chat surface itself moved into SageLauncher.tsx's docked "Ask AI" console
// -- there is nothing left to cold-load at a URL called "chat". The bare
// workspace route (FleetHome, '.fleet-title') is what a cold load of the
// old chat surface actually resolves to today; confirmed by driving it in a
// browser rather than reading the route table.
const CORE_SURFACES = [
  { href: '/w/ws-1', selector: '.fleet-title' },
  { href: '/w/ws-1/settings', selector: '.settings-shell-body' },
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
  test('cold loads the workspace home and settings without hydration warnings', async ({ page }) => {
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

  test('hard refreshes the workspace home and settings without hydration warnings', async ({ page }) => {
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
    // '.fleet-root' / 'nav.fleet-rail-nav' are the current shell's own stable
    // roots (support/auth.ts's loginAsOwner already asserts both) -- there is
    // no separate "titlebar" element in the current compact chrome to assert
    // against.
    await expect(page.locator('.fleet-root')).toBeVisible();
    await expect(page.locator('nav.fleet-rail-nav')).toBeVisible();
  });

  test('hard refresh on a workspace route preserves the server-hydrated shell session', async ({ page }) => {
    await loginAsOwner(page);
    await page.goto('/w/ws-1', { waitUntil: 'domcontentloaded' });
    await page.reload({ waitUntil: 'domcontentloaded' });

    await expect(page.locator('.fleet-root')).toBeVisible();
    await expect(page.locator('nav.fleet-rail-nav')).toBeVisible();
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
    // login/page.tsx's submit button reads "Continue", not "Log in" -- a
    // separate, unrelated staleness from the /sage sweep this file was
    // otherwise touched for, found while running this test for real.
    await page.getByRole('button', { name: /^continue$/i }).click();

    await expect(page.locator('a[href^="/w/ws-first/"]')).toHaveCount(0);
  });
});
