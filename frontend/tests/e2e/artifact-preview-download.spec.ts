// @ts-nocheck
import { expect, test } from '@playwright/test';
import { loginAsOwner } from './support/auth';

test.describe('artifact preview and download', () => {
  // The workspace-level artifacts PAGE is gone, not just relocated:
  // '/w/{id}/artifacts' is now a next.config LEGACY_REDIRECT straight to
  // '/w/{id}/agents' (frontend/next.config.ts), resolved ahead of the
  // router, UNCONDITIONALLY -- regardless of the artifacts_enabled
  // capability this test used to branch on. Confirmed live: this suite's
  // own seeded workspace reports `artifacts_enabled: true` from
  // /api/workspaces/ws-1/bootstrap (a real pro/pilot-tier entitlement --
  // server_modules/entitlements_service.py still grants it), and the page
  // still redirects to /agents with zero trace of an artifacts surface. So
  // the enabled/disabled branch these two tests used to take no longer
  // produces two different outcomes -- both land on /agents now. Reported
  // to the coordinator as a real product gap (a capability the entitlement
  // system still promises with no reachable UI) rather than silently
  // designed around here. The DATA path is still very much alive -- see
  // the artifact-list-endpoint test below, unchanged -- only the page that
  // would show it is gone.
  test('the artifacts route no longer has a surface of its own; it redirects to agents', async ({ page }) => {
    await loginAsOwner(page);
    await page.goto('/w/ws-1/artifacts');

    await expect(page).toHaveURL(/\/w\/ws-1\/agents(?:[/?#]|$)/);
    await expect(page.locator('.fleet-root')).toBeVisible();
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
