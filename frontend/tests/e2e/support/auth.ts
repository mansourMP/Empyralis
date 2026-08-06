// @ts-nocheck
import { expect, type Page } from '@playwright/test';

async function waitForWorkspaceShell(page: Page, workspaceId: string): Promise<void> {
  // Phase 8 (see app/(account)/w/[workspaceId]/layout.tsx): the legacy
  // workstation shell (data-workstation-shell / data-workstation-titlebar)
  // is gone — every workspace surface now renders fleet-native inside
  // FleetShell (lib/workspace/fleet/FleetShell.tsx), whose stable root is
  // `.fleet-root` with a PrimaryRail nav always mounted. `/sage` is also no
  // longer a real page — it 307-redirects to `/agents` (see
  // app/(account)/w/[workspaceId]/sage/page.tsx) — so land on `/agents`
  // instead.
  for (let attempt = 0; attempt < 6; attempt += 1) {
    try {
      await page.goto(`/w/${workspaceId}/agents`, { waitUntil: 'domcontentloaded' });
    } catch (error) {
      if (attempt === 5) {
        throw error;
      }
      await page.waitForTimeout(150 * (attempt + 1));
      continue;
    }
    const shell = page.locator('.fleet-root');
    const rail = page.getByRole('navigation').first();
    if (await shell.count() > 0 && await rail.count() > 0 && page.url().includes(`/w/${workspaceId}/`)) {
      return;
    }
    await page.waitForTimeout(150 * (attempt + 1));
  }
  throw new Error(`Workspace shell never became healthy for ${workspaceId}.`);
}

async function loginOwnerSession(page: Page): Promise<void> {
  const response = await page.request.post('/api/auth/login', {
    data: {
      email: 'owner@example.com',
      password: 'password-123',
      channel: 'web',
    },
  });
  expect(response.ok()).toBeTruthy();
}

export async function loginAsOwner(page: Page, workspaceId = 'ws-1'): Promise<void> {
  await page.context().clearCookies();
  await loginOwnerSession(page);
  await waitForWorkspaceShell(page, workspaceId);
  await expect(page.locator('.fleet-root')).toBeVisible();
  await expect(page.locator('nav.fleet-rail-nav')).toBeVisible();
}
