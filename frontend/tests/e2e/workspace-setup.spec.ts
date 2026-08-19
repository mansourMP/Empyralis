// @ts-nocheck
import { expect, test } from '@playwright/test';
import { loginAsOwner } from './support/auth';

test.describe('workspace setup', () => {
  // FLAGGED FOR THE COORDINATOR, NOT FIXED HERE -- this test's premise looks
  // broken for a reason well beyond the '/sage' picker this pass was scoped
  // to, and guessing at a replacement risks inventing behavior. Confirmed
  // live: signup/page.tsx now unconditionally sends every signup to
  // '/verify-email' (handleSubmit's `finally` block always does
  // `window.location.replace(verifyUrl)`, whatever the readiness poll
  // does) -- so `await expect(page).toHaveURL(/\/(?:onboarding|workspaces\/
  // new)/)` right after clicking "sign up" can never match today; the
  // 'Workspace name' / 'Shell profile' / 'Default route' fields below it
  // are unreachable from this flow. Separately, register_user's own
  // control-plane call (server_modules/auth.py -> create_local_password_
  // account) auto-provisions a `ws_{user_id[:12]}` personal workspace for
  // EVERY new account, unconditionally -- so it's unclear whether the
  // onboarding/workspaces-new form this test drives is reachable from any
  // current signup path at all, or whether it now only serves some other
  // entry point (an invite flow? an admin-provisioned account?) this test
  // was never written to exercise. Needs a decision on what this test
  // should actually verify before it's rewritten, not a mechanical
  // assertion swap.
  test('fresh signup lands on onboarding and enters the configured workspace', async ({ page }) => {
    const uniqueEmail = `owner-${Date.now()}@example.com`;
    await page.goto('/signup');
    await page.getByLabel('Email').fill(uniqueEmail);
    await page.getByLabel('Password').fill('password-123');
    await page.getByLabel('Name').fill('Owner Example');
    await page.getByRole('button', { name: /sign up/i }).click();

    await expect(page).toHaveURL(/\/(?:onboarding|workspaces\/new)/);
    await expect(page.getByRole('heading', { name: /finish workspace setup|create workspace/i })).toBeVisible();
    await page.getByLabel('Workspace name').fill('Acme Deal Room');
    await page.getByLabel('Shell profile').selectOption('document_workstation_shell');
    await page.getByLabel('Default route').selectOption(/sage$/);
    await page.getByRole('button', { name: /save workspace setup|create workspace/i }).click();

    await page.waitForURL(/\/w\/[^/]+\/sage$/);
  });

  test('existing users can create a second workspace and see it in the switcher', async ({ page }) => {
    await loginAsOwner(page);
    await page.goto('/workspaces/new');
    await page.getByLabel('Workspace name').fill('Second Workspace');
    await page.getByLabel('Workspace type').selectOption('team');
    await page.getByLabel('Shell profile').selectOption('operations_admin_shell');
    // '/sage' is no longer one of the Default Route options at all --
    // workspace-setup-form.tsx's own DEFAULT_ROUTE_OPTIONS is
    // Workspace / Agents / Settings, and its comment says why: "'/sage' is
    // a redirect into '/agents' now, not a page of its own." This isn't a
    // dead control left behind by accident -- the option was already
    // removed on the product side; only this test still referenced it.
    await page.getByLabel('Default route').selectOption('/agents');
    await page.getByRole('button', { name: /create workspace/i }).click();

    await page.waitForURL(/\/w\/[^/]+\/agents$/);
    // "see it in the switcher" is the rail's own "Switch workspace" menu now
    // -- there's no "Open {name}" link anywhere in the current UI (that was
    // never real; confirmed by driving this exact flow in a browser). The
    // menu items are `menuitemradio`s, not links, and only render once the
    // switcher button is opened.
    await page.getByRole('button', { name: /switch workspace/i }).click();
    // `checked: true` scopes to the CURRENTLY ACTIVE workspace row -- the one
    // this test just created and navigated into. Without it, re-running this
    // non-idempotent test against a persisted dev database (it creates a new
    // "Second Workspace" every run, with no cleanup) leaves more than one
    // row with that name and getByRole's default match hits Playwright's
    // strict-mode violation; scoping to the active row is also the more
    // precise assertion of what this test actually means to prove.
    await expect(
      page.getByRole('menuitemradio', { name: /second workspace/i, checked: true }),
    ).toBeVisible();
  });

  test('direct navigation to an unavailable workspace falls back to the primary ready workspace', async ({ page }) => {
    await loginAsOwner(page);
    await page.goto('/w/ws-incomplete/chat');
    // Falls back to whichever membership resolvePrimaryProductWorkspaceId
    // picks (lib/shell/workspace-membership-model.ts), never a hardcoded
    // 'ws-1' -- confirmed live, this seeded account actually falls back to
    // a DIFFERENT workspace than 'ws-1': register_user auto-provisions its
    // own personal workspace for every account (server_modules/auth.py),
    // created moments before this suite's seed script separately grants
    // 'ws-1' access, so the auto-provisioned one resolves as primary. That
    // ordering is a registration-time detail this test has no business
    // depending on -- assert the outcome (landed on some real workspace's
    // fleet shell) instead of the exact id or path. The old
    // '[data-workstation-surface="chat"]' selector no longer exists either
    // -- see account-shell-hydration.spec.ts's CORE_SURFACES comment.
    await expect(page).toHaveURL(/\/w\/[^/?#]+\/?(?:[?#]|$)/);
    await expect(page.locator('.fleet-root')).toBeVisible();
  });
});
