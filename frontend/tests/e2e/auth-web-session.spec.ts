// @ts-nocheck
import { expect, test } from '@playwright/test';

test.describe('browser auth session', () => {
  test('signup creates a browser session and survives reload', async ({ page }) => {
    const signupEmail = `owner+${Date.now()}@example.com`;
    await page.goto('/signup?channel_attribution=tg-signup-token');
    const signupRequest = page.waitForRequest((request) => (
      request.method() === 'POST'
      && request.url().includes('/api/auth/signup')
    ));
    await page.getByLabel('Name').fill('Owner Example');
    await page.getByLabel('Email').fill(signupEmail);
    await page.getByLabel('Password').fill('password-123');
    await page.getByRole('button', { name: /create account/i }).click();
    const postedSignupRequest = await signupRequest;
    expect(JSON.parse(postedSignupRequest.postData() || '{}').acquisition_token).toBe('tg-signup-token');

    // Signup always lands on the "check your email" screen before it ever
    // sees the app -- signup/page.tsx's handleSubmit unconditionally calls
    // window.location.replace('/verify-email') in its `finally` block,
    // whether or not the post-signup readiness poll succeeds. There is no
    // '/sage' page in this flow and never a hop through the workspace shell
    // -- '/sage' is now just a redirect for stale bookmarks straight back to
    // the bare workspace route (app/(account)/w/[workspaceId]/sage/page.tsx),
    // and signup never visits it even transiently. Confirmed by driving the
    // real flow in a browser, not by reading the route table -- see
    // CLAUDE.md's own documented trap about next.config redirects resolving
    // ahead of the router.
    await page.waitForURL(/\/verify-email(?:[/?#]|$)/);
    await expect(page).not.toHaveURL(/\/onboarding(?:[/?#]|$)/);
    await page.reload();
    await expect(page).not.toHaveURL(/\/login$/);
  });

  // MAN-343: found live on production — signup rendered "Couldn't create the
  // account — Email or password was not accepted." and created the account
  // anyway (real workspace, real verification email delivered). Root cause:
  // signup/page.tsx's handleSubmit used to wrap BOTH signup() and the
  // post-signup awaitBrowserAuthReady() readiness poll in one try/catch, so
  // a hiccup in the poll -- which runs only AFTER the account already
  // exists and its session cookies are already set on signup()'s own
  // response -- was reported identically to signup() itself failing.
  // Forcing every /api/auth/account-shell call to 401 reproduces exactly
  // that poll failure deterministically (no dependency on real network
  // timing), while the underlying POST /api/auth/signup is untouched and
  // must still succeed for real.
  test('a failed post-signup readiness poll never reports the signup itself as failed', async ({ page }) => {
    const signupEmail = `owner+manfail+${Date.now()}@example.com`;
    let signupResponseOk: boolean | null = null;

    await page.route('**/api/auth/account-shell', async (route) => {
      await route.fulfill({
        status: 401,
        contentType: 'application/json',
        body: JSON.stringify({ detail: 'not ready yet' }),
      });
    });

    page.on('response', (response) => {
      if (response.url().includes('/api/auth/signup') && response.request().method() === 'POST') {
        signupResponseOk = response.ok();
      }
    });

    await page.goto('/signup');
    await page.getByLabel('Name').fill('Owner Man343');
    await page.getByLabel('Email').fill(signupEmail);
    await page.getByLabel('Password').fill('password-123');
    await page.getByRole('button', { name: /create account/i }).click();

    // The real, network-observed signup response must have succeeded --
    // this is not a test that merely asserts the UI stayed quiet while the
    // account silently failed to be created.
    await expect.poll(() => signupResponseOk).toBe(true);

    // The account-shell poll is failing on every attempt (forced above), so
    // if the old single-catch behavior regressed, the error banner would
    // appear here. It must not, no matter how long the poll keeps failing.
    await expect(page.getByRole('alert')).toHaveCount(0);
    await expect(page.getByText(/couldn.t create the account/i)).toHaveCount(0);

    // The account is real, so the flow must still move the person forward
    // to the verification screen rather than stranding them on a signup
    // form claiming nothing happened.
    await page.waitForURL(/\/verify-email(?:[/?#]|$)/);
    await expect(page.getByRole('alert')).toHaveCount(0);
  });

  test('login establishes a browser session that survives reload', async ({ page }) => {
    await page.goto('/login?channel_attribution=tg-login-token');
    const loginRequest = page.waitForRequest((request) => (
      request.method() === 'POST'
      && request.url().includes('/api/auth/login')
    ));
    await page.getByLabel('Email').fill('owner@example.com');
    await page.getByLabel('Password').fill('password-123');
    await page.getByRole('button', { name: /^continue$/i }).click();
    const postedLoginRequest = await loginRequest;
    expect(JSON.parse(postedLoginRequest.postData() || '{}').acquisition_token).toBe('tg-login-token');

    // login/page.tsx redirects to '/' by default, and app/page.tsx's root
    // route lands on the workspace's own stored default_route or, absent
    // one, the bare workspace shell (/w/{id}) -- never '/sage', which is now
    // only a redirect target for stale bookmarks and is not on this path at
    // all. Confirmed by driving the real flow in a browser: this seeded
    // account lands directly on the bare workspace route with zero agents,
    // never on '/sage' even transiently.
    await page.waitForURL(/\/w\/[^/?#]+\/?(?:[?#]|$)/);
    await expect(page).not.toHaveURL(/\/onboarding(?:[/?#]|$)/);
    await page.reload();
    await expect(page).not.toHaveURL(/\/login$/);
  });

  // CSRF protection (validateBrowserCsrf in control-plane-proxy.ts) applies
  // to every unsafe request that carries a live session cookie -- exercised
  // here against verify-email/resend, a route with no bypass. This test used
  // to hit /api/auth/logout instead, which happened to also return 403 only
  // because it predated logout's deliberate bypassCsrf grant (see the next
  // test) -- it was accidentally asserting the one exception rather than the
  // rule, so a real regression in the general case would have gone
  // undetected while this test stayed green.
  test('unsafe cookie-auth requests fail closed without a CSRF header', async ({ page }) => {
    await page.goto('/login');
    await page.getByLabel('Email').fill('owner@example.com');
    await page.getByLabel('Password').fill('password-123');
    await page.getByRole('button', { name: /^continue$/i }).click();
    await page.waitForURL(/\/(w\/|onboarding|$)/);

    const response = await page.request.post('/api/auth/verify-email/resend');
    expect(response.status()).toBe(403);
  });

  // Deliberate exception to the rule above, not a gap. logout/route.ts sets
  // bypassCsrf: true -- it is the ONLY route in the app that does -- because
  // logout is the guaranteed escape hatch from a stuck session and must
  // always reach the backend and clear cookies, even when the browser's CSRF
  // cookie is missing, stale, or was never fetched. A forged cross-site
  // logout only logs the victim out, which is not a meaningful attack, so
  // the trade favors "logout always works" over "logout is CSRF-protected
  // like everything else." If this test starts failing, the fix is almost
  // certainly to restore the bypass, not to weaken this assertion.
  test('logout succeeds even without a CSRF header, by design', async ({ page }) => {
    await page.goto('/login');
    await page.getByLabel('Email').fill('owner@example.com');
    await page.getByLabel('Password').fill('password-123');
    await page.getByRole('button', { name: /^continue$/i }).click();
    await page.waitForURL(/\/(w\/|onboarding|$)/);

    const response = await page.request.post('/api/auth/logout');
    expect(response.ok()).toBeTruthy();
  });

  test('logout clears the browser session', async ({ page }) => {
    await page.goto('/login');
    await page.getByLabel('Email').fill('owner@example.com');
    await page.getByLabel('Password').fill('password-123');
    await page.getByRole('button', { name: /^continue$/i }).click();
    await page.waitForURL(/\/(w\/|onboarding|$)/);

    const csrfCookie = (await page.context().cookies()).find(
      (cookie) => cookie.name === 'empyralis_csrf_token',
    );
    const response = await page.request.post('/api/auth/logout', {
      headers: csrfCookie ? { 'x-csrf-token': csrfCookie.value } : {},
    });
    expect(response.ok()).toBeTruthy();

    await page.goto('/w/ws-1/chat');
    await expect(page).toHaveURL(/\/login$/);
  });

  test('google auth completion redirects the original auth tab into the workspace', async ({ browser }) => {
    const context = await browser.newContext();
    const loginPage = await context.newPage();
    await loginPage.goto('/login');
    await loginPage.evaluate(() => {
      window.localStorage.setItem(
        'empyralis.external-auth.pending',
        JSON.stringify({ provider: 'google', startedAt: Date.now() }),
      );
    });

    const signupEmail = `google-handoff+${Date.now()}@example.com`;
    const browserAuthResult = await loginPage.evaluate(async ({ signupEmail }) => {
      const response = await fetch('/api/auth/signup', {
        method: 'POST',
        credentials: 'include',
        headers: {
          'content-type': 'application/json',
        },
        body: JSON.stringify({
          email: signupEmail,
          password: 'password-123',
          name: 'Google Handoff',
          channel: 'web',
        }),
      });

      return {
        ok: response.ok,
        status: response.status,
      };
    }, { signupEmail });
    expect(
      browserAuthResult.ok,
      `synthetic browser signup failed with status ${browserAuthResult.status}`,
    ).toBeTruthy();

    const callbackPage = await context.newPage();
    await callbackPage.goto('/auth/complete?provider=google&next=/', { waitUntil: 'domcontentloaded' });

    // '/auth/complete' replaces to `next` ('/'), and app/page.tsx's root
    // route resolves that to the bare workspace shell for an account with no
    // configured default_route -- never '/sage'. Confirmed by driving this
    // exact synthetic-signup-then-callback flow in a browser.
    await callbackPage.waitForURL(/\/w\/[^/?#]+\/?(?:[?#]|$)/);
    await loginPage.waitForURL(/\/w\/[^/?#]+\/?(?:[?#]|$)/);
    await expect(loginPage).not.toHaveURL(/\/login(?:[/?#]|$)/);
  });
});
