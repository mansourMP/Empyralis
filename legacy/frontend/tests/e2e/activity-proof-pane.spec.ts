// @ts-nocheck
import { expect, test } from '@playwright/test';

import { loginAsOwner } from './support/auth';

async function stubActivityProofRequests(page) {
  await page.route('**/api/threads**', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        items: [
          {
            id: 'thread-launch',
            title: 'Primary thread',
            last_turn_at: '2026-05-06T11:00:00Z',
            turns: [
              {
                role: 'user',
                content: 'Review launch blockers',
                created_at: '2026-05-06T11:00:00Z',
              },
            ],
          },
        ],
      }),
    });
  });

  await page.route('**/api/activity/timeline**', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        items: [
          {
            id: 'evt-channel',
            created_at: '2026-05-06T11:10:00Z',
            event_class: 'channel_message',
            channel: 'telegram',
            action: 'send_message',
            summary: 'Sent Telegram update to Alex.',
          },
          {
            id: 'evt-gateway',
            created_at: '2026-05-06T11:09:00Z',
            event_class: 'system_activity',
            action: 'gateway_reconnect',
            summary: 'This Mac reconnected.',
          },
          {
            id: 'evt-provider',
            created_at: '2026-05-06T11:08:00Z',
            event_class: 'provider_failure',
            action: 'model_error',
            summary: 'Gemini fallback failed gracefully.',
          },
          {
            id: 'evt-browser',
            created_at: '2026-05-06T11:07:00Z',
            event_class: 'browser_action',
            action: 'browser_open',
            summary: 'Opened browser session.',
          },
          {
            id: 'evt-outcome',
            created_at: '2026-05-06T11:06:00Z',
            event_class: 'run_status',
            status: 'completed',
            summary: 'Run finished successfully.',
          },
          {
            id: 'evt-debug',
            created_at: '2026-05-06T11:05:00Z',
            event_class: 'system_activity',
            summary: '{"activity_event_id":"evt-123","trace_id":"debug"}',
          },
        ],
      }),
    });
  });

  await page.route('**/api/runs**', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        items: [
          {
            id: 'run-typecheck',
            title: 'Shell check',
            status: 'completed',
            summary: 'Typecheck completed.',
            thread_id: 'thread-launch',
            updated_at: '2026-05-06T11:04:00Z',
          },
        ],
      }),
    });
  });

  await page.route('**/api/approvals**', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        items: [
          {
            approval_id: 'approval-send',
            prompt: 'Approve sending the update?',
            status: 'pending',
            created_at: '2026-05-06T11:03:00Z',
          },
        ],
      }),
    });
  });
}

test.describe('Activity proof timeline', () => {
  test('summarizes proof events with filters and hides raw debug payloads', async ({ page }) => {
    await loginAsOwner(page);
    await stubActivityProofRequests(page);

    await page.goto('/w/ws-1/activity', { waitUntil: 'domcontentloaded' });

    await expect(page.locator('[data-workstation-surface="activity-proof"]')).toBeVisible();
    await expect(page.getByText('What happened?')).toBeVisible();
    await expect(page.getByText('Raw debug blobs and hidden reasoning stay out of this surface.')).toBeVisible();

    for (const label of ['All', 'Chat', 'Tools', 'Approvals', 'Channels', 'Gateway', 'Providers', 'Files', 'Outcomes']) {
      await expect(page.getByRole('button', { name: label, exact: true })).toBeVisible();
    }

    await expect(page.getByText('Review launch blockers')).toBeVisible();
    await expect(page.getByText('Typecheck completed.')).toBeVisible();
    await expect(page.getByText('Approve sending the update?')).toBeVisible();
    await expect(page.getByText('Sent Telegram update to Alex.')).toBeVisible();
    await expect(page.getByText('This Mac reconnected.')).toBeVisible();
    await expect(page.getByText('Gemini fallback failed gracefully.')).toBeVisible();
    await expect(page.getByText('Opened browser session.')).toBeVisible();
    await expect(page.getByText('Run finished successfully.')).toBeVisible();

    await expect(page.getByText('activity_event_id')).toHaveCount(0);
    await expect(page.getByText('trace_id')).toHaveCount(0);

    await page.getByRole('button', { name: 'Providers', exact: true }).click();
    await expect(page.getByText('Gemini fallback failed gracefully.')).toBeVisible();
    await expect(page.getByText('Sent Telegram update to Alex.')).toHaveCount(0);
  });
});
