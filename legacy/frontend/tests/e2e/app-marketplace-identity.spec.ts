// @ts-nocheck
import { expect, test } from '@playwright/test';

import { loginAsOwner } from './support/auth';

async function apiRequest(page, path, method = 'GET', body = undefined) {
  const result = await page.evaluate(
    async ({ requestPath, requestMethod, requestBody }) => {
      const token = document.cookie
        .split(';')
        .map((part) => part.trim())
        .find((part) => part.startsWith('empyralis_csrf_token='))
        ?.split('=')
        .slice(1)
        .join('=') ?? null;
      const response = await fetch(requestPath, {
        method: requestMethod,
        credentials: 'include',
        headers: {
          accept: 'application/json',
          ...(requestBody ? { 'content-type': 'application/json' } : {}),
          ...(token ? { 'x-csrf-token': decodeURIComponent(token) } : {}),
        },
        body: requestBody ? JSON.stringify(requestBody) : undefined,
      });
      return {
        ok: response.ok,
        status: response.status,
        body: await response.json().catch(() => null),
      };
    },
    { requestPath: path, requestMethod: method, requestBody: body },
  );
  expect(result.ok, JSON.stringify(result.body)).toBeTruthy();
  return result.body;
}

async function upsertMiniApp(page, appId, payload) {
  return apiRequest(
    page,
    `/api/workspaces/ws-1/mini-apps/${encodeURIComponent(appId)}`,
    'PUT',
    payload,
  );
}

test.describe('simplified app marketplace', () => {
  test.describe.configure({ mode: 'serial' });

  test('apps launcher has no seeded web-link apps and Browse apps opens the app store', async ({ page }) => {
    await loginAsOwner(page);
    await page.goto('/w/ws-1/applications', { waitUntil: 'domcontentloaded' });
    await expect(page.getByLabel('Open Telegram Web')).toHaveCount(0);
    await expect(page.getByLabel('Open WhatsApp Web')).toHaveCount(0);
    await expect(page.getByLabel('Open Notion')).toHaveCount(0);
    await expect(page.getByLabel('Open Linear')).toHaveCount(0);
    await expect(page.getByLabel('Open Gmail')).toHaveCount(0);

    await page.getByRole('button', { name: 'Browse apps' }).click();
    await expect(page).toHaveURL(/\/w\/ws-1\/applications\/store$/);
    await expect(page.getByText('No apps available yet.')).toBeVisible();
    await expect(page.getByText('Submit yours')).toBeVisible();
  });

  test('discover navigation contains no app categories', async ({ page }) => {
    await loginAsOwner(page);
    await page.goto('/w/ws-1/marketplace?category=link', { waitUntil: 'domcontentloaded' });
    await expect(page).toHaveURL(/category=link/);
    await expect(page.locator('[aria-label="Marketplace filters"]')).toHaveCount(0);
    await expect(page.getByText('Submit app')).toHaveCount(0);
    await expect(page.getByText('Browse curated links')).toHaveCount(0);

    await page.getByRole('button', { name: 'Discover' }).click();
    const sidebar = page.locator('[data-workstation-shell-panel-content="discover"]');
    await expect(sidebar).toBeVisible();
    await expect(sidebar.getByText('Agent templates')).toBeVisible();
    await expect(sidebar.getByText('Skills')).toBeVisible();
    await expect(sidebar.getByText('MCP')).toBeVisible();
    await expect(sidebar.getByText('Bundles')).toBeVisible();
    await expect(sidebar.getByText('Link apps')).toHaveCount(0);
    await expect(sidebar.getByText('Community apps')).toHaveCount(0);
    await expect(sidebar.getByText('Platform apps')).toHaveCount(0);
  });

  test('private app without icon renders initials and stays out of public marketplace', async ({ page }) => {
    await loginAsOwner(page);
    const appId = `private_app_${Date.now()}`;
    const label = `Private Portal ${Date.now()}`;

    await upsertMiniApp(page, appId, {
      label,
      description: 'Private workspace app without icon.',
      runtime_type: 'private',
      delivery_mode: 'hosted',
      hosted_url: 'https://private.example/app',
      allowed_origins: ['https://private.example'],
      permissions: [],
      bridge_contracts: {},
      visibility: 'workspace_private',
      install_status: 'installed',
    });

    await page.goto('/w/ws-1/applications', { waitUntil: 'domcontentloaded' });
    const tile = page.getByLabel(`Open ${label}`);
    await expect(tile).toBeVisible();
    await expect(tile).toHaveAttribute('href', `/w/ws-1/applications/${appId}`);
    await expect(tile.locator('img')).toHaveCount(0);
    await expect(tile).toContainText('PP');

    await tile.click();
    await expect(page).toHaveURL(new RegExp(`/w/ws-1/applications/${appId}$`));
    await expect(page.locator('iframe')).toBeVisible();

    await page.goto(`/w/ws-1/applications/${appId}?details=1`, { waitUntil: 'domcontentloaded' });
    await expect(page.getByText('Runtime type')).toBeVisible();
    await expect(page.getByText('Category')).toBeVisible();
    await expect(page.getByText('Memory boundary')).toHaveCount(0);
    await expect(page.getByText('Bridge contracts')).toHaveCount(0);
    await expect(page.getByText('Permissions and denials')).toHaveCount(0);

    await page.goto('/w/ws-1/marketplace?category=community', { waitUntil: 'domcontentloaded' });
    await expect(page.getByText(label)).toHaveCount(0);
  });

  test('new app bridge remains denied by default', async ({ page }) => {
    await loginAsOwner(page);
    const appId = `bridge_denied_${Date.now()}`;

    await upsertMiniApp(page, appId, {
      label: 'Bridge Denied App',
      description: 'Hosted app with no bridge contracts.',
      runtime_type: 'private',
      delivery_mode: 'hosted',
      hosted_url: 'https://bridge-denied.example/app',
      allowed_origins: ['https://bridge-denied.example'],
      permissions: [],
      bridge_contracts: {},
      visibility: 'workspace_private',
      install_status: 'installed',
    });

    const manifest = await apiRequest(
      page,
      `/api/workspaces/ws-1/mini-apps/${encodeURIComponent(appId)}/hosted-manifest`,
    );
    expect(manifest.memory_scope).toBe('none_by_default');
    expect(manifest.hosted_app.bridge.allowed_contracts).toEqual({});
    expect(manifest.hosted_app.bridge.permissions).toEqual([]);
    expect(manifest.hosted_app.bridge.denied_by_default).toContain('read_sage_memory');
  });
});
