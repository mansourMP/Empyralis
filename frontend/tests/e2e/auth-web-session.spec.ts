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

    await page.waitForURL(/\/w\/.+\/sage(?:[/?#]|$)/);
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

    await page.waitForURL(/\/w\/.+\/sage(?:[/?#]|$)/);
    await expect(page).not.toHaveURL(/\/onboarding(?:[/?#]|$)/);
    await page.reload();
    await expect(page).not.toHaveURL(/\/login$/);
  });

  test('unsafe cookie-auth requests fail closed without a CSRF header', async ({ page }) => {
    await page.goto('/login');
    await page.getByLabel('Email').fill('owner@example.com');
    await page.getByLabel('Password').fill('password-123');
    await page.getByRole('button', { name: /^continue$/i }).click();
    await page.waitForURL(/\/(w\/|onboarding|$)/);

    const response = await page.request.post('/api/auth/logout');
    expect(response.status()).toBe(403);
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

  test('google auth completion redirects the original auth tab into Sage', async ({ browser }) => {
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

    await callbackPage.waitForURL(/\/w\/.+\/sage(?:[/?#]|$)/);
    await loginPage.waitForURL(/\/w\/.+\/sage(?:[/?#]|$)/);
    await expect(loginPage).not.toHaveURL(/\/login(?:[/?#]|$)/);
  });
});
