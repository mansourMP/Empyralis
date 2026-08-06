// @ts-nocheck
import { expect, type Page } from '@playwright/test';

// The legacy "workstation shell" (data-workstation-shell / -titlebar /
// -chat-composer) is gone — FleetShell.tsx's own comment says it plainly:
// "Phase 8: the legacy workstation shell is gone — every workspace surface
// now renders fleet-native inside FleetShell." `.fleet-root` is FleetShell's
// own top-level wrapper, rendered for every workspace route regardless of
// which page loads under it, so it is the live stand-in for "the shell
// mounted and is healthy." Landing on the bare workspace route (`/w/{id}`)
// rather than `/sage` matters too: that route now redirects through /sage
// (which itself redirects to /agents) only for pre-Fleet bookmarks — the
// real landing page is Fleet Home.
async function waitForWorkspaceShell(page: Page, workspaceId: string): Promise<void> {
  for (let attempt = 0; attempt < 6; attempt += 1) {
    try {
      await page.goto(`/w/${workspaceId}`, { waitUntil: 'domcontentloaded' });
    } catch (error) {
      if (attempt === 5) {
        throw error;
      }
      await page.waitForTimeout(150 * (attempt + 1));
      continue;
    }
    const shell = page.locator('.fleet-root');
    if (await shell.count() > 0 && page.url().includes(`/w/${workspaceId}`)) {
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
}
