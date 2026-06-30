// @ts-nocheck
import { expect, test } from '@playwright/test';

import { loginAsOwner } from './support/auth';

const LOADING_COPY_PATTERNS = [
  /opening workspace/i,
  /loading conversation history/i,
  /loading notifications/i,
  /loading notification detail/i,
  /loading briefing/i,
  /loading latest signal/i,
  /loading workspace posture/i,
];

async function mountSurface(page, href, selector) {
  await page.goto(href, { waitUntil: 'domcontentloaded' });
  await expect(page.locator(selector)).toBeVisible();
  await expect(page.locator('[data-workstation-surface="fallback"]')).toHaveCount(0);
}

async function expectNoVisibleLoadingCopy(page) {
  for (const pattern of LOADING_COPY_PATTERNS) {
    await expect(page.getByText(pattern)).toHaveCount(0);
  }
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

async function resolveApprovalsSurfaceSelector(page) {
  const bootstrapResponse = await page.request.get('/api/workspaces/ws-1/bootstrap');
  expect(bootstrapResponse.ok()).toBeTruthy();
  const payload = await bootstrapResponse.json();
  const approvalsEnabled = payload?.capabilities?.approvals_enabled === true;
  return approvalsEnabled
    ? '[data-workstation-surface="approvals"]'
    : '[data-workstation-surface="chat"]';
}

async function stubSurfaceDataRequests(page) {
  await page.route('**/api/sage-profile?workspace_id=ws-1', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        profile: {
          user_name: 'Owner',
          identity_summary: 'Runs the workspace.',
          communication_style: 'Direct',
          recurring_responsibility: 'Keep operations moving.',
          standing_rules: [],
        },
        bootstrap: {
          complete: true,
          answered_count: 5,
          total_count: 5,
          progress_label: '5/5',
          current_question: null,
        },
      }),
    });
  });

  await page.route('**/api/workspaces/ws-1/sage/tool-policy', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ items: [], summary: {} }),
    });
  });

  await page.route('**/api/connectors/vault?workspace_id=ws-1', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ items: [] }),
    });
  });

  await page.route('**/api/threads/primary**', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        id: 'primary',
        thread_id: 'primary',
        title: 'Primary thread',
        turns: [],
      }),
    });
  });

  await page.route('**/api/runs**', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ items: [] }),
    });
  });

  await page.route('**/api/approvals**', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ items: [] }),
    });
  });

  await page.route('**/api/sage-memory**', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ items: [], categories: [], summary: {} }),
    });
  });

  await page.route('**/activity/timeline**', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ items: [] }),
    });
  });

  await page.route('**/api/events/inbox/stream**', async (route) => {
    await route.fulfill({
      status: 200,
      headers: { 'content-type': 'text/event-stream' },
      body: 'event: ping\ndata: {}\n\n',
    });
  });

  await page.route('**/api/notifications**', async (route) => {
    const url = new URL(route.request().url());
    if (url.searchParams.get('stream') === 'true') {
      await route.fulfill({
        status: 200,
        headers: { 'content-type': 'text/event-stream' },
        body: 'event: ping\ndata: {}\n\n',
      });
      return;
    }
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ items: [] }),
    });
  });
}

async function stubChatTrustProviderRequests(page) {
  await page.addInitScript(() => {
    const originalFetch = window.fetch.bind(window);
    const state = {
      persistedTurns: [] as Array<Record<string, unknown>>,
      persistCallCount: 0,
      turnCallCount: 0,
    };
    (window as typeof window & { __e2eChatTrustState?: typeof state }).__e2eChatTrustState = state;

    const jsonResponse = (payload: unknown, status = 200) => new Response(JSON.stringify(payload), {
      status,
      headers: {
        'content-type': 'application/json',
      },
    });

    window.fetch = async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = typeof input === 'string'
        ? input
        : input instanceof URL
          ? input.toString()
          : input.url;

      if (url.includes('/api/providers/catalog')) {
        return jsonResponse({
          providers: [
            {
              id: 'deepseek',
              label: 'DeepSeek',
              kind: 'provider',
              usable: true,
              active: true,
              credential_plane: 'platform_runtime',
              platform_runtime_allowed: true,
              provider_scopes: ['sage_personal'],
              models: [
                {
                  id: 'deepseek-chat',
                  label: 'DeepSeek Chat',
                  provider: 'deepseek',
                  supports_reasoning: true,
                  reasoning_levels: ['low', 'medium', 'high'],
                },
              ],
            },
          ],
        });
      }

      if (url.includes('/api/providers/profiles')) {
        return jsonResponse({ items: [] });
      }

      if (url.includes('/api/sessions')) {
        return jsonResponse({
          session_id: 'session-e2e-chat-trust',
          tenant_id: 't-1',
          workspace_id: 'ws-1',
          thread_id: 'primary',
        });
      }

      if (url.includes('/api/threads/primary/turns')) {
        state.persistCallCount += 1;
        const payload = init?.body && typeof init.body === 'string' ? JSON.parse(init.body) : {};
        state.persistedTurns.splice(0, state.persistedTurns.length, {
          id: 'turn-user-1',
          role: 'user',
          status: 'completed',
          content: payload?.content ?? '',
          created_at: new Date().toISOString(),
          metadata: {
            request_id: payload?.client_request_id ?? 'req-1',
            client_request_id: payload?.client_request_id ?? 'req-1',
          },
        });
        return jsonResponse({
          id: 'primary',
          thread_id: 'primary',
          title: 'Primary thread',
          turns: state.persistedTurns,
        });
      }

      if (url.includes('/api/threads/primary')) {
        return jsonResponse({
          id: 'primary',
          thread_id: 'primary',
          title: 'Primary thread',
          turns: state.persistedTurns,
        });
      }

      if (url.includes('/api/turn')) {
        state.turnCallCount += 1;
        return jsonResponse({
          detail: 'The selected AI path is not ready. Use Empyralis credits, add your own API key, connect My Computer, or choose another model in Connected Apps.',
        }, 400);
      }

      return originalFetch(input, init);
    };
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

  test('sage chat mounts composer and clears loading copy after route settles', async ({ page }) => {
    await loginAsOwner(page);
    await mountSurface(page, '/w/ws-1/sage', '[data-workstation-surface="chat"]');

    await expect(page.locator('[data-workstation-surface="chat"]')).toBeVisible();
    await expect(page.locator('[data-workstation-chat-composer="root"]')).toBeVisible();
    await expect(page.locator('[data-workstation-surface="chat"]').getByText(/loading conversation history/i)).toHaveCount(0);
    await expectNoVisibleLoadingCopy(page);
  });

  test('sage chat collapses pending user turns and shows provider failures as notices', async ({ page }) => {
    await loginAsOwner(page);
    await stubSurfaceDataRequests(page);
    await stubChatTrustProviderRequests(page);
    await mountSurface(page, '/w/ws-1/sage', '[data-workstation-surface="chat"]');

    await expect(page.locator('.app-chat-composer__provider-pill')).toContainText('DeepSeek');
    await page.getByRole('button', { name: 'Change model and reasoning' }).click();
    await page.getByRole('button', { name: 'DeepSeek Chat' }).click();
    const composer = page.locator('[data-workstation-chat-composer="root"] textarea');
    await expect(composer).toBeVisible();
    await composer.fill('hello');
    await expect(composer).toHaveValue('hello');
    await composer.press('Enter');

    await expect(page.locator('.app-chat-status-notice')).toContainText('Action needed');
    await expect(page.locator('.app-chat-status-notice')).toContainText(/Sage needs an AI path|The selected AI path is not ready\./);
    await expect(page.locator('.app-chat-status-notice')).toContainText('Manage credits');
    await expect(page.locator('.app-chat-status-notice')).toContainText('Add API key');
    await expect(page.locator('.app-chat-status-notice')).toContainText('Choose AI Model');
    const userBubbleCount = await page.locator('[data-chat-role="user"]').filter({ hasText: 'hello' }).count();
    expect(userBubbleCount).toBeLessThanOrEqual(1);
    await expect(
      page.locator('[data-chat-role="assistant"]').filter({ hasText: /Sage needs an AI path|The selected AI path is not ready\./ }),
    ).toHaveCount(0);
  });

  test('runs, approvals, activity, and home surfaces leave loading states and mount live data surfaces', async ({ page }) => {
    await loginAsOwner(page);
    await stubSurfaceDataRequests(page);

    const routeExpectations: Array<[string, string]> = [
      ['/w/ws-1', '[data-workstation-surface="chat"]'],
      ['/w/ws-1/activity', '[data-workstation-surface="activity-proof"]'],
      ['/w/ws-1/approvals', '[data-workstation-surface="sage-tasks"]'],
    ];

    for (const [route, selector] of routeExpectations) {
      await mountSurface(page, route, selector);
      await expectNoVisibleLoadingCopy(page);
      await page.waitForTimeout(1200);
    }
  });
});
