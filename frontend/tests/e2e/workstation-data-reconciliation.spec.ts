// @ts-nocheck
import { expect, test } from '@playwright/test';

import { loginAsOwner } from './support/auth';

async function mountSurface(page, href, selector) {
  await page.goto(href, { waitUntil: 'domcontentloaded' });
  await expect(page.locator(selector)).toBeVisible();
  await expect(page.locator('[data-workstation-surface="fallback"]')).toHaveCount(0);
}

async function clickMarkAllRead(page) {
  const markAllReadButton = page.getByRole('button', { name: 'Mark all read' });
  await expect(markAllReadButton).toBeVisible();
  await expect(markAllReadButton).toBeEnabled();

  const markAllReadResponse = page.waitForResponse((response) => {
    if (response.request().method() !== 'POST') {
      return false;
    }
    return response.url().includes('/api/notifications');
  });

  await markAllReadButton.click();
  await markAllReadResponse;
}

async function seedUnreadNotification(page) {
  const seedItem = {
    id: 'notification-seed-e2e',
    title: 'Seed notification',
    event_type: 'seed',
    summary: 'Unread notification used to validate mark-all-read behavior.',
    text: 'Unread notification used to validate mark-all-read behavior.',
    channel: 'workspace',
    read_at: null,
    created_at: new Date().toISOString(),
  };

  await page.route('**/api/notifications?**', async (route) => {
    const url = new URL(route.request().url());
    const isStreamRequest = url.searchParams.get('stream') === 'true';
    const isPrimaryListRequest = url.searchParams.get('limit') === '80';
    if (isStreamRequest || !isPrimaryListRequest) {
      await route.continue();
      return;
    }

    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        items: [seedItem],
        count: 1,
      }),
    });
  });
}

test.describe('workstation data reconciliation', () => {
  test('notifications mark-all-read updates both list state and status messaging', async ({ page }) => {
    await loginAsOwner(page);
    await seedUnreadNotification(page);
    await mountSurface(page, '/w/ws-1/notifications', '[data-workstation-surface="notifications"]');

    await clickMarkAllRead(page);
    await expect(page.locator('[data-workstation-surface="notifications"]')).toContainText('Marked all visible notifications as read.');
  });

  // Three tests used to live here, all built on the pre-Fleet "workstation
  // surface" chrome: `[data-workstation-surface="..."]` root wrappers,
  // `[data-workstation-chat-composer="root"]`, `.app-chat-composer__provider-pill`,
  // a "Change model and reasoning" combined control, and `.app-chat-status-notice`
  // actionable-error cards with "Manage credits" / "Add API key" / "Choose AI
  // Model" links. None of it is live:
  //   - `WorkspaceRouteLayout` (frontend/app/(account)/w/[workspaceId]/layout.tsx)
  //     says so directly: "Phase 8: the legacy workstation shell is gone —
  //     every workspace surface now renders fleet-native inside FleetShell."
  //   - `data-workstation-surface` only survives as one unrelated dynamic
  //     prop on workspace-channel-pairing-surface.tsx (per-channel pairing
  //     cards); no page ever renders it with value "chat"/"activity-proof"/
  //     "sage-tasks" anymore.
  //   - The routes two of these tests hit — /w/ws-1/activity and
  //     /w/ws-1/approvals — don't exist at all under app/(account)/w/[workspaceId]
  //     anymore (no such directories); that surface set predates even the
  //     current nav IA (which exposes activity/approvals differently — a
  //     workspace-views nav link and a "needs your ok" badge, see
  //     account-shell-bootstrap-resilience.spec.ts).
  //   - AgentChat.tsx's real turn-failure UI is a single plain error line
  //     (`.fleet-channel-expand-error`) below the transcript — there is no
  //     live "Action needed" card with Manage-credits/Add-API-key/Choose-
  //     AI-Model action buttons to rewrite the failure-notice test against.
  // This is older, already-dead architecture than the ChatComposer cluster
  // tonight's merge removed — not something introduced or broken by that
  // merge. Deleted rather than rewritten: there's no live surface left that
  // plays any of these roles the same way.
});
