// @ts-nocheck
import { expect, test } from '@playwright/test';

// Regression coverage for a real bug: forwardControlPlaneRequest
// (lib/server/control-plane-proxy.ts) used to round-trip every proxied
// request body through request.text() before handing it to the upstream
// fetch() call. .text() decodes the body as UTF-8 and re-encodes it — any
// byte sequence that isn't valid UTF-8 (any binary upload: images, PDFs,
// the multipart bodies attachment uploads use) got each invalid byte
// replaced with U+FFFD, silently corrupting the file in transit while the
// upload still returned 200 OK. Confirmed live: a 71-byte fixture PNG was
// saved server-side as 193 bytes, padded with the UTF-8 encoding of U+FFFD
// wherever a non-ASCII byte had been.
//
// This exercises the real, unmocked proxy — /api/sage-chat/attachments
// falls through the catch-all app/api/[...path]/route.ts, the exact same
// forwardControlPlaneRequest() call every other /api/* route uses — so a
// regression here would also affect chat attachments, avatar uploads, and
// any other binary POST in the app, not just this one endpoint.

test.describe('attachment proxy binary fidelity', () => {
  test('a binary upload with invalid UTF-8 byte sequences survives the proxy unchanged', async ({ page }) => {
    await page.goto('/login');
    await page.getByLabel('Email').fill('owner@example.com');
    await page.getByLabel('Password').fill('password-123');
    await page.getByRole('button', { name: /^continue$/i }).click();
    await page.waitForURL(/\/(w\/|onboarding|$)/);

    const csrfCookie = (await page.context().cookies()).find(
      (cookie) => cookie.name === 'empyralis_csrf_token',
    );
    const csrfHeaders = csrfCookie ? { 'x-csrf-token': csrfCookie.value } : {};

    // PNG signature (0x89 is not a valid lead byte in UTF-8) followed by a
    // spread of bytes covering every range .text()/TextDecoder mangles: lone
    // continuation bytes (0x80-0xBF), the never-valid 0xC0/0xC1/0xFE/0xFF,
    // a surrogate-half encoding, and a truncated 4-byte sequence.
    const fixtureBytes = Buffer.from([
      0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a,
      0x00, 0x01, 0x02, 0x7f, 0x80, 0x81, 0xa0, 0xbf,
      0xc0, 0xc1, 0xfe, 0xff, 0xed, 0xa0, 0x80, 0xf4,
      0x90, 0x80, 0x80, 0xe0, 0x80,
    ]);

    const uploadResponse = await page.request.post(
      '/api/sage-chat/attachments?workspace_id=ws-1',
      {
        headers: csrfHeaders,
        multipart: {
          file: {
            name: 'binary-fixture.png',
            mimeType: 'image/png',
            buffer: fixtureBytes,
          },
        },
      },
    );
    expect(uploadResponse.ok(), await uploadResponse.text().catch(() => '')).toBeTruthy();
    const uploaded = await uploadResponse.json();
    // Corrupted bytes round-trip through the server as a longer file (each
    // invalid byte becomes the 3-byte UTF-8 encoding of U+FFFD) — checking
    // the reported size alone already proves the upload was byte-exact.
    expect(uploaded.size).toBe(fixtureBytes.length);

    const downloadResponse = await page.request.get(uploaded.url);
    expect(downloadResponse.ok()).toBeTruthy();
    const downloadedBytes = await downloadResponse.body();

    expect(downloadedBytes.length).toBe(fixtureBytes.length);
    expect(Buffer.from(downloadedBytes).equals(fixtureBytes)).toBeTruthy();
  });

  test('a plain text attachment still uploads correctly through the same proxy path', async ({ page }) => {
    await page.goto('/login');
    await page.getByLabel('Email').fill('owner@example.com');
    await page.getByLabel('Password').fill('password-123');
    await page.getByRole('button', { name: /^continue$/i }).click();
    await page.waitForURL(/\/(w\/|onboarding|$)/);

    const csrfCookie = (await page.context().cookies()).find(
      (cookie) => cookie.name === 'empyralis_csrf_token',
    );
    const csrfHeaders = csrfCookie ? { 'x-csrf-token': csrfCookie.value } : {};

    const textFixture = 'Quarterly notes: revenue is up, nothing else changed.';
    const uploadResponse = await page.request.post(
      '/api/sage-chat/attachments?workspace_id=ws-1',
      {
        headers: csrfHeaders,
        multipart: {
          file: {
            name: 'notes.txt',
            mimeType: 'text/plain',
            buffer: Buffer.from(textFixture, 'utf-8'),
          },
        },
      },
    );
    expect(uploadResponse.ok()).toBeTruthy();
    const uploaded = await uploadResponse.json();

    const downloadResponse = await page.request.get(uploaded.url);
    expect(downloadResponse.ok()).toBeTruthy();
    expect((await downloadResponse.text())).toBe(textFixture);
  });

  test('an ordinary JSON POST still round-trips correctly through the same proxy path', async ({ page }) => {
    // login itself is a JSON POST through forwardControlPlaneRequest with no
    // init.body override — the same code path attachments use. A wrong
    // password proves the request body (email/password) reached the
    // upstream byte-correct, not just that *some* response came back.
    const badLogin = await page.request.post('/api/auth/login', {
      data: { email: 'owner@example.com', password: 'not-the-real-password', channel: 'web' },
    });
    expect(badLogin.status()).toBe(401);

    const goodLogin = await page.request.post('/api/auth/login', {
      data: { email: 'owner@example.com', password: 'password-123', channel: 'web' },
    });
    expect(goodLogin.ok()).toBeTruthy();
  });
});
