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
    // 2026-07-23: USER.md/IDENTITY.md/SOUL.md were removed from the root-file
    // taxonomy; onboarding now projects into memory/files/profile.md instead.
    projections: {
      'memory/files/profile.md': '# Owner Profile\n',
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
    // Rewritten: settings moved from a query-string section
    // (`/settings?section=account`) to a path segment
    // (`/settings/[section]`, see app/(account)/settings/resolve-settings-route.ts
    // and app/(account)/w/[workspaceId]/settings/[section]/page.tsx) — the
    // URL shape changed. The Account section's own content also changed:
    // "current account" / "sign-in methods" / "manage billing" / "log out"
    // never landed there (see lib/workspace/fleet/AccountSection.tsx's own
    // comment — "currently empty... nothing ever there to land on"); it's a
    // deliberate honest empty state per CLAUDE.md's "no dead controls" law,
    // not a stub standing in for removed content. The hardware assertion
    // (still a live, first-class workspace route) is unchanged.
    await loginAsOwner(page);

    await page.goto('/settings/account');
    await expect(page).toHaveURL(/\/w\/ws-1\/settings\/account$/);
    await expect(page.getByRole('heading', { name: /^account$/i }).first()).toBeVisible();
    await expect(page.getByText(/nothing here yet/i)).toBeVisible();

    await page.goto('/w/ws-1/hardware');
    await expect(page).toHaveURL(/\/w\/ws-1\/hardware$/);
    // Breadcrumbs.tsx renders two <h1>s (a desktop one and a
    // `.fleet-breadcrumb-mobile-current` one, toggled by a CSS media query
    // rather than being conditionally mounted) — scope to the desktop
    // breadcrumb class so this doesn't hit a Playwright strict-mode
    // violation from matching both.
    await expect(page.locator('h1.fleet-breadcrumb--current').filter({ hasText: /^Hardware$/ })).toBeVisible();
  });

  // Deleted (5 tests), not rewritten — the "workspace views" nav (chat /
  // memory / integrations / tasks / activity, with an approvals badge), the
  // full-page Sage bootstrap-wizard loading state, the workspace-level
  // Memory page, the workspace-level Integrations page, and the /channels
  // redirect target all belonged to the legacy workstation shell named in
  // this file's own dead code comment removal (see
  // app/(account)/w/[workspaceId]/layout.tsx: "Phase 8: the legacy
  // workstation shell is gone — every workspace surface now renders
  // fleet-native inside FleetShell"). Confirmed genuinely gone, not moved,
  // by reading current source:
  //   - lib/workspace/fleet/PrimaryRail.tsx's RAIL_ITEMS is now just
  //     Inbox/Conversations/Projects/Agents — no "workspace views" nav, no
  //     approvals badge (this product has an explicit "no approval system"
  //     law in CLAUDE.md — a badge counting pending approvals would violate
  //     it outright).
  //   - app/(account)/w/[workspaceId]/sage/page.tsx redirects straight to
  //     /agents ("Sage is now a corner console (SageLauncher), not a
  //     full-page route"); "Loading Sage setup" / "Agent setup is
  //     temporarily unavailable" appear nowhere in live source, only in
  //     this test file.
  //   - next.config.ts's LEGACY_REDIRECTS 307s /memory, /integrations, and
  //     /channels to /agents. The content these tests asserted (About
  //     me/Preferences/Rules/Pinned/Recent/Sensitive/Controls headings,
  //     "USER / IDENTITY / SOUL projections", Export/Wipe buttons, the
  //     sage-unified-section AI/Communication/This Computer/Knowledge/Tools
  //     layout, Telegram/WhatsApp/Drive/Notion rows) has no workspace-level
  //     successor — memory and channels/connectors/tools now live as
  //     per-agent detail tabs (lib/workspace/fleet/tabs/MemoryTab.tsx and
  //     FleetAgentDetail.tsx's channels/connectors/tools tabs), a
  //     structurally different surface, not a renamed one, so there is
  //     nothing here to rewrite against.

  test('sage setup load failures render a retryable setup card instead of raw backend text', async ({ page }) => {
    await page.route('**/api/sage-profile?workspace_id=ws-1', async (route) => {
      await route.fulfill({
        status: 404,
        contentType: 'application/json',
        body: JSON.stringify({ detail: 'Not Found' }),
      });
    });

    await loginAsOwner(page);

    await expect(page.locator('.app-chat-status-notice').filter({ hasText: 'Agent setup is temporarily unavailable' })).toBeVisible();
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

    await expect(page.locator('.app-chat-status-notice').filter({ hasText: 'Agent setup is temporarily unavailable' })).toBeVisible();
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
