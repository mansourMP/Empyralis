// @ts-nocheck
import { expect, test } from '@playwright/test';

const FORBIDDEN_VISIBLE_TERMS = [
  'owner',
  'member',
  'default',
  'ws-1',
  'Personal Shell',
];

async function loginAsOwner(page) {
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

function normalizeVisibleText(input: string): string {
  return input.replace(/\s+/g, ' ').trim().toLowerCase();
}

test.describe('launch sage-first smoke', () => {
  test('normal login path lands in sage with clean chat-first shell', async ({ page }) => {
    await loginAsOwner(page);
    await page.goto('/', { waitUntil: 'networkidle' });
    await expect(page).toHaveURL(/\/w\/ws-1\/sage(?:[/?#]|$)/, { timeout: 20_000 });
    await expect(page.locator('[data-workstation-surface="chat"]')).toBeVisible();
    await expect(page.locator('[data-workstation-chat-composer="root"]')).toBeVisible();
    await expect(page.locator('[data-workstation-chat-composer="root"] textarea')).toBeFocused();

    await expect(page.getByText(/finish workspace setup/i)).toHaveCount(0);
    await expect(page.getByText(/workspace setup/i)).toHaveCount(0);
    await expect(page).not.toHaveURL(/\/onboarding(?:[/?#]|$)/);

    const visibleText = normalizeVisibleText(await page.locator('body').innerText());
    for (const term of FORBIDDEN_VISIBLE_TERMS) {
      expect(visibleText.includes(term.toLowerCase())).toBeFalsy();
    }
  });

  test('web chat exposes recent chats and preserves the previous chat when starting a new one', async ({ page }) => {
    await loginAsOwner(page);
    await page.goto('/', { waitUntil: 'networkidle' });

    const historyBar = page.getByLabel('Recent chats');
    await expect(historyBar).toBeVisible();
    await expect(historyBar.getByText('Recent chats')).toBeVisible();
    await expect(historyBar.getByRole('button', { name: /new chat/i })).toBeVisible();
    await expect(historyBar.locator('.app-chat-history-pill--active')).toContainText(/current/i);

    await historyBar.getByRole('button', { name: /new chat/i }).click();

    await expect(historyBar.locator('.app-chat-history-pill')).toHaveCount(2);
    await expect(historyBar.getByText('Primary thread')).toBeVisible();
    await expect(historyBar.locator('.app-chat-history-pill--active')).toContainText(/current/i);
  });
});
