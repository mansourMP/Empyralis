// @ts-nocheck
import { expect, test } from '@playwright/test';

import { loginAsOwner } from './support/auth';

const TEST_WORKSPACE_ID = 'ws-1';
const CHAT_FALLBACK_ROUTES = /\/w\/ws-1\/(?:sage|chat)(?:[/?#]|$)/;

function workspacePath(path: string): string {
  return `/w/${TEST_WORKSPACE_ID}/${path}`;
}

async function loginAsWorkspaceMember(page) {
  await loginAsOwner(page);

  const memberEmail = `member-${Date.now()}-${Math.random().toString(36).slice(2, 8)}@example.com`;
  const csrfCookie = (await page.context().cookies()).find(
    (cookie) => cookie.name === 'empyralis_csrf_token',
  );

  const inviteResponse = await page.request.post(`/api/workspaces/${TEST_WORKSPACE_ID}/members/invites`, {
    headers: csrfCookie ? { 'x-csrf-token': csrfCookie.value } : {},
    data: {
      email: memberEmail,
      role: 'member',
    },
  });
  expect([200, 201]).toContain(inviteResponse.status());

  await page.context().clearCookies();

  const registerResponse = await page.request.post('/api/auth/register', {
    data: {
      email: memberEmail,
      password: 'password-123',
      name: 'Member Demo User',
      channel: 'web',
      workspace_id: TEST_WORKSPACE_ID,
    },
  });
  expect(registerResponse.ok()).toBeTruthy();
}

test.describe('admin surfaces', () => {
  test('owner can open applications surface directly', async ({ page }) => {
    await loginAsOwner(page);
    await page.goto(workspacePath('applications'));
    await expect(page).toHaveURL(/\/w\/ws-1\/applications(?:[/?#]|$)/);
    await expect(page.getByText(/apps/i).first()).toBeVisible();
  });

  test('non-admin workspace cannot render direct hosted mini-app route', async ({ page }) => {
    await loginAsWorkspaceMember(page);

    await page.goto(workspacePath('applications/demo-test-app'));
    await expect(page).toHaveURL(CHAT_FALLBACK_ROUTES);
  });
});
