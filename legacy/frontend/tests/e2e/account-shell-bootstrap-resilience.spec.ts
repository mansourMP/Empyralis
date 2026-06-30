// @ts-nocheck
import { expect, test } from '@playwright/test';

import { loginAsOwner } from './support/auth';

const SAGE_BOOTSTRAP_QUESTIONS = [
  {
    id: 'user_name',
    field: 'user_name',
    prompt: 'What should I call you?',
    placeholder: 'Example: Mansur',
  },
  {
    id: 'identity_summary',
    field: 'identity_summary',
    prompt: 'What do you do? Share your role, work, or the projects that matter most.',
    placeholder: 'Example: I run product and engineering for a mobile-first agent platform.',
  },
  {
    id: 'communication_style',
    field: 'communication_style',
    prompt: 'How should I communicate with you? Tone, style, format, or decision preferences.',
    placeholder: 'Example: Be direct, concise, and lead with the answer.',
  },
  {
    id: 'recurring_responsibility',
    field: 'recurring_responsibility',
    prompt: "What's one thing you want me to keep handling automatically?",
    placeholder: 'Example: Keep my inbox triaged and surface urgent replies.',
  },
  {
    id: 'standing_rules',
    field: 'standing_rules',
    prompt: 'Any rules I should always follow?',
    placeholder: 'Example: Never send external messages without approval.',
  },
] as const;

function buildSageProfilePayload(step = 0) {
  const answeredCount = Math.max(0, Math.min(step, SAGE_BOOTSTRAP_QUESTIONS.length));
  const currentQuestion = SAGE_BOOTSTRAP_QUESTIONS[answeredCount] ?? null;
  return {
    workspace_id: 'ws-1',
    profile: {
      user_name: answeredCount >= 1 ? 'Mansur' : '',
      identity_summary: answeredCount >= 2 ? 'I build Empyralis.' : '',
      communication_style: answeredCount >= 3 ? 'Be direct and concise.' : '',
      recurring_responsibility: answeredCount >= 4 ? 'Keep my work moving.' : '',
      standing_rules: answeredCount >= 5 ? ['Never send external messages without approval.'] : [],
      standing_rules_text: answeredCount >= 5 ? 'Never send external messages without approval.' : '',
    },
    bootstrap: {
      complete: answeredCount >= SAGE_BOOTSTRAP_QUESTIONS.length,
      current_question: currentQuestion,
      answered_count: answeredCount,
      total_count: SAGE_BOOTSTRAP_QUESTIONS.length,
      progress_label: `${answeredCount}/${SAGE_BOOTSTRAP_QUESTIONS.length}`,
    },
    storage_policy: {
      authority: 'structured_profile_cloud_canonical',
    },
    projections: {
      'USER.md': '# User Profile\n',
      'IDENTITY.md': '# Identity\n',
      'SOUL.md': '# Empyralis\n',
      'HEARTBEAT.md': '# Heartbeat\n',
    },
    updated_at: new Date().toISOString(),
  };
}

test.describe('account shell and bootstrap resilience', () => {
  test('public routes still render when account-shell bootstrap fails transiently', async ({ page }) => {
    await page.route('**/api/auth/account-shell', async (route) => {
      await route.fulfill({
        status: 500,
        contentType: 'application/json',
        body: JSON.stringify({ detail: 'transient failure' }),
      });
    });

    await page.goto('/login');
    await expect(page.getByRole('button', { name: /^continue$/i })).toBeVisible();
  });

  test('requested onboarding workspace fails closed when it is unavailable', async ({ page }) => {
    await loginAsOwner(page);
    await page.goto('/onboarding?workspaceId=ws-missing');
    await expect(page.getByText(/requested workspace is unavailable/i)).toBeVisible();
    await expect(page).toHaveURL(/\/onboarding\?workspaceId=ws-missing$/);
  });

  test('stale remembered workspace routes are discarded when shell membership state changes', async ({ page }) => {
    await page.addInitScript(() => {
      window.localStorage.setItem(
        'empyralis.account-shell.v2',
        JSON.stringify({
          accountId: 'user-1',
          selectedWorkspaceId: 'ws-1',
          lastVisitedWorkspaceRouteById: {
            'ws-1': '/w/ws-1/admin',
          },
          workspaceRouteStateById: {
            'ws-1': 'old-membership::/w/ws-1/admin::workspace.admin',
          },
          globalTheme: 'system',
          globalChromePreferences: {
            tenantSwitcherCollapsed: false,
          },
        }),
      );
    });

    await page.goto('/w/ws-1');
    await expect(page).not.toHaveURL(/\/w\/ws-1\/admin$/);
  });

  test('global account settings route resolves into workspace settings and hardware is a first-class workspace route', async ({ page }) => {
    await loginAsOwner(page);

    await page.goto('/settings/account');
    await expect(page).toHaveURL(/\/w\/ws-1\/settings\?section=account$/);
    await expect(page.getByRole('heading', { name: /^account$/i })).toBeVisible();
    await expect(page.getByText(/current account/i)).toBeVisible();
    await expect(page.getByText(/sign-in methods/i).first()).toBeVisible();
    await expect(page.getByRole('button', { name: /manage billing/i })).toBeVisible();
    await expect(page.getByRole('button', { name: /log out/i })).toBeVisible();

    await page.goto('/w/ws-1/hardware');
    await expect(page).toHaveURL(/\/w\/ws-1\/hardware$/);
    await expect(page.locator('h1').filter({ hasText: /^Hardware$/ })).toBeVisible();
  });

  test('sage top navigation exposes the five IA surfaces and moves approvals into a badge', async ({ page }) => {
    await page.route('**/api/approvals?workspace_id=ws-1&limit=24', async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          pending_count: 1,
          items: [
            {
              approval_id: 'approval-1',
              status: 'pending',
              prompt: 'Allow local action?',
            },
          ],
        }),
      });
    });

    await loginAsOwner(page);

    const nav = page.getByRole('navigation', { name: /^workspace views$/i });
    await expect(nav.getByRole('link', { name: /^chat$/i })).toBeVisible();
    await expect(nav.getByRole('link', { name: /^memory$/i })).toBeVisible();
    await expect(nav.getByRole('link', { name: /^integrations$/i })).toBeVisible();
    await expect(nav.getByRole('link', { name: /^tasks$/i })).toBeVisible();
    await expect(nav.getByRole('link', { name: /^activity$/i })).toBeVisible();

    await expect(nav.getByRole('link', { name: /^profile$/i })).toHaveCount(0);
    await expect(nav.getByRole('link', { name: /^skills$/i })).toHaveCount(0);
    await expect(nav.getByRole('link', { name: /^heartbeat$/i })).toHaveCount(0);
    await expect(nav.getByRole('link', { name: /^connected apps$/i })).toHaveCount(0);
    await expect(nav.getByRole('link', { name: /^needs your ok$/i })).toHaveCount(0);
    await expect(page.getByRole('link', { name: /needs your ok · 1/i })).toBeVisible();
  });

  test('sage setup load cannot spin forever and hosted credits stay selectable when provider catalog is degraded', async ({ page }) => {
    await page.route('**/api/sage-profile?workspace_id=ws-1', async (route) => {
      await new Promise((resolve) => {
        setTimeout(resolve, 9_500);
      });
      await route.fulfill({
        status: 504,
        contentType: 'application/json',
        body: JSON.stringify({ detail: 'profile timeout' }),
      });
    });
    await page.route('**/api/providers/catalog**', async (route) => {
      await route.fulfill({
        status: 503,
        contentType: 'application/json',
        body: JSON.stringify({ detail: 'provider catalog unavailable' }),
      });
    });
    await page.route('**/api/providers?**', async (route) => {
      await route.fulfill({
        status: 503,
        contentType: 'application/json',
        body: JSON.stringify({ detail: 'providers unavailable' }),
      });
    });
    await page.route('**/api/providers/profiles**', async (route) => {
      await route.fulfill({
        status: 503,
        contentType: 'application/json',
        body: JSON.stringify({ detail: 'profiles unavailable' }),
      });
    });

    await loginAsOwner(page);

    await expect(page.getByText(/Loading Sage setup/i)).toBeVisible();
    await expect(page.getByText(/Sage setup is temporarily unavailable/i)).toBeVisible({ timeout: 12_000 });
    await expect(page.getByRole('button', { name: /^retry$/i })).toBeVisible();
    await expect(page.getByText(/DeepSeek/i).first()).toBeVisible();
    await expect(page.getByText(/No AI model/i)).toHaveCount(0);
    await page.unrouteAll({ behavior: 'ignoreErrors' });
  });

  test('memory owns identity, rules, projections, and memory controls', async ({ page }) => {
    await page.route('**/api/sage-profile?workspace_id=ws-1', async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify(buildSageProfilePayload(SAGE_BOOTSTRAP_QUESTIONS.length)),
      });
    });

    await loginAsOwner(page);
    await page.getByRole('navigation', { name: /^workspace views$/i }).getByRole('link', { name: /^memory$/i }).click();

    await expect(page).toHaveURL(/\/w\/ws-1\/memory$/);
    await expect(page.getByRole('heading', { name: /^about me$/i })).toBeVisible();
    await expect(page.getByRole('heading', { name: /^preferences$/i })).toBeVisible();
    await expect(page.getByRole('heading', { name: /^rules$/i })).toBeVisible();
    await expect(page.getByRole('heading', { name: /^pinned$/i })).toBeVisible();
    await expect(page.getByRole('heading', { name: /^recent$/i })).toBeVisible();
    await expect(page.getByRole('heading', { name: /^sensitive$/i })).toBeVisible();
    await expect(page.getByRole('heading', { name: /^controls$/i })).toBeVisible();
    await expect(page.getByText('USER / IDENTITY / SOUL projections', { exact: true })).toBeVisible();
    await expect(page.getByRole('button', { name: /^export$/i })).toBeVisible();
    await expect(page.getByRole('button', { name: /^wipe$/i })).toBeVisible();
    await expect(page.getByRole('navigation', { name: /^workspace views$/i }).getByRole('link', { name: /^profile$/i })).toHaveCount(0);
  });

  test('integrations owns providers, communication, computer, knowledge, and tools', async ({ page }) => {
    await loginAsOwner(page);
    await page.getByRole('navigation', { name: /^workspace views$/i }).getByRole('link', { name: /^integrations$/i }).click();

    await expect(page).toHaveURL(/\/w\/ws-1\/integrations$/);
    const sectionLabels = page.locator('.sage-unified-section__label');
    await expect(sectionLabels.filter({ hasText: /^AI$/ })).toBeVisible();
    await expect(sectionLabels.filter({ hasText: /^Communication$/ })).toBeVisible();
    await expect(sectionLabels.filter({ hasText: /^This Computer$/ })).toBeVisible();
    await expect(sectionLabels.filter({ hasText: /^Knowledge$/ })).toBeVisible();
    await expect(sectionLabels.filter({ hasText: /^Tools$/ })).toBeVisible();

    await expect(page.getByText(/Active: .*through Empyralis credits/i)).toBeVisible();
    await expect(page.getByText(/^Credits$/).first()).toBeVisible();
    await expect(page.getByText(/remaining|Available|Not active/i).first()).toBeVisible();
    await expect(page.getByText(/^Backup$/)).toBeVisible();
    await expect(page.getByText(/Gemini (available|configurable)/i)).toBeVisible();
    await expect(page.getByText(/^Provider configuration$/)).toBeVisible();
    await expect(page.getByText(/^More AI choices$/)).toBeVisible();
    await expect(page.getByText(/connect another AI account or use a model on This Computer/i)).toBeVisible();
    await expect(sectionLabels.filter({ hasText: /^Communication apps$/ })).toHaveCount(0);
    await expect(sectionLabels.filter({ hasText: /^AI providers$/ })).toHaveCount(0);
    await expect(page.getByText(/^Your Telegram$/)).toBeVisible();
    await expect(page.getByText(/^Your WhatsApp$/)).toBeVisible();
    await page.getByText(/^Your Telegram$/).click();
    await expect(page.getByText(/Uses your paired computer session/i).first()).toBeVisible();
    await expect(page.getByRole('button', { name: /^Set up$/i })).toBeVisible();
    await expect(page.getByRole('button', { name: /^Audit$/i })).toHaveCount(0);
    await expect(page.getByText(/token|WSS|protocol|API key/i)).toHaveCount(0);
    await expect(page.getByText(/^Signal$/)).toBeVisible();
    await expect(page.getByText(/^Slack$/)).toBeVisible();
    await expect(page.getByText(/^Discord$/)).toBeVisible();
    await expect(page.getByText(/^This computer$/)).toBeVisible();
    await expect(page.getByText(/^My browser$/)).toBeVisible();
    await expect(page.getByText(/^Files on this computer$/)).toBeVisible();
    await expect(page.getByText(/^AI on this computer$/)).toBeVisible();
    await expect(page.getByText(/^Drive$/)).toBeVisible();
    await expect(page.getByText(/^Notion$/)).toBeVisible();
    await expect(page.getByText(/^Uploads$/)).toBeVisible();
    await expect(page.getByText(/^Websites$/)).toBeVisible();
  });

  test('legacy channels route opens Communication integrations, not the computer console', async ({ page }) => {
    await loginAsOwner(page);
    await page.goto('/w/ws-1/channels');

    await expect(page.getByText(/^Communication$/)).toBeVisible();
    await expect(page.getByText(/^Your Telegram$/)).toBeVisible();
    await expect(page.getByText(/^Your WhatsApp$/)).toBeVisible();
    await expect(page.getByRole('heading', { name: /^This Mac$/i })).toHaveCount(0);
    await expect(page.getByText(/What Sage can use on this computer/i)).toHaveCount(0);
  });

  test('sage setup load failures render a retryable setup card instead of raw backend text', async ({ page }) => {
    await page.route('**/api/sage-profile?workspace_id=ws-1', async (route) => {
      await route.fulfill({
        status: 404,
        contentType: 'application/json',
        body: JSON.stringify({ detail: 'Not Found' }),
      });
    });

    await loginAsOwner(page);

    await expect(page.locator('.app-chat-status-notice').filter({ hasText: 'Sage setup is temporarily unavailable' })).toBeVisible();
    await expect(page.getByRole('button', { name: /^retry$/i })).toBeVisible();
    await expect(page.locator('[data-workstation-chat-composer="root"] textarea')).toHaveAttribute('placeholder', 'Sage setup is temporarily unavailable.');
    await expect(page.getByText(/^Not Found$/)).toHaveCount(0);
  });

  test('sage setup answer failures stay inside the setup surface instead of leaking raw notices', async ({ page }) => {
    await page.route('**/api/sage-profile?workspace_id=ws-1', async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify(buildSageProfilePayload(0)),
      });
    });
    await page.route('**/api/sage-profile/bootstrap/answer', async (route) => {
      await route.fulfill({
        status: 404,
        contentType: 'application/json',
        body: JSON.stringify({ detail: 'Not Found' }),
      });
    });

    await loginAsOwner(page);

    await page.getByLabel(/^answer$/i).fill('Mansur');
    await page.getByRole('button', { name: /save and continue/i }).click();

    await expect(page.locator('.app-chat-status-notice').filter({ hasText: 'Sage setup is temporarily unavailable' })).toBeVisible();
    await expect(page.getByRole('button', { name: /^retry$/i })).toBeVisible();
    await expect(page.getByText(/^Not Found$/)).toHaveCount(0);
  });

  test('fresh workspace can complete Sage setup and land in normal chat without raw backend text', async ({ page }) => {
    let step = 0;

    await page.route('**/api/sage-profile?workspace_id=ws-1', async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify(buildSageProfilePayload(step)),
      });
    });
    await page.route('**/api/sage-profile/bootstrap/answer', async (route) => {
      step += 1;
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify(buildSageProfilePayload(step)),
      });
    });

    await loginAsOwner(page);

    for (const answer of [
      'Mansur',
      'I build Empyralis.',
      'Be direct and concise.',
      'Keep my work moving.',
      'Never send external messages without approval.',
    ]) {
      const input = page.getByLabel(/^answer$/i);
      await expect(input).toBeVisible();
      await input.fill(answer);
      await page.getByRole('button', { name: /save and continue/i }).click();
    }

    await expect(page.getByText(/^Set up Sage$/)).toHaveCount(0);
    await expect(page.locator('[data-workstation-chat-composer="root"] textarea')).toHaveAttribute('placeholder', 'Message Sage...');
    await expect(page.getByText(/^Not Found$/)).toHaveCount(0);
  });

  test('Sage composer accepts dropped files as context', async ({ page }) => {
    await loginAsOwner(page);
    await page.goto('/w/ws-1/sage');

    const composer = page.locator('[data-workstation-chat-composer="root"] form');
    const composerInput = page.locator('[data-workstation-chat-composer="root"] textarea');
    await expect(composer).toBeVisible();

    const dataTransfer = await page.evaluateHandle(() => {
      const transfer = new DataTransfer();
      transfer.items.add(new File(['demo image'], 'receipt.png', { type: 'image/png' }));
      return transfer;
    });

    await composer.dispatchEvent('dragenter', { dataTransfer });
    await expect(page.getByText(/^Drop files$/)).toBeVisible();
    await composer.dispatchEvent('drop', { dataTransfer });
    await expect(composerInput).toHaveValue(/Use receipt\.png as context:/);
  });
});
