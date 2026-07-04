/**
 * Regression test for the TelegramPairPanel code-display bug: the backend's
 * pair/start response can return EITHER a short, human-typeable numeric code
 * (deep_link present, built from a separate token) OR — when a pairing
 * session already exists — the long deep-link token itself in the
 * `pairing_code` field. The panel must only render the CODE box when the
 * value is actually short; otherwise only the "Open @bot" button should show.
 *
 * Mocks the API response so both shapes are covered deterministically,
 * without touching real backend pairing state.
 */
import { test, expect } from "@playwright/test";

const WORKSPACE_ID = "ws-1";
const FRONTEND = "http://localhost:3000";
const OUT = "test-results/crisp";

test.use({ baseURL: FRONTEND, viewport: { width: 1280, height: 800 } });

async function auth(page: any) {
  await page.context().clearCookies();
  const res = await page.request.post("/api/auth/login", {
    data: { email: "owner@example.com", password: "password-123", channel: "web" },
  });
  expect(res.ok()).toBeTruthy();
}

async function gotoFleet(page: any) {
  await page.goto(`/w/${WORKSPACE_ID}/fleet`, { waitUntil: "networkidle" });
  await expect(page.locator(".fleet-title")).toBeVisible();
  await page.waitForTimeout(400);
}

test("pair panel — short-code response shows CODE box", async ({ page }) => {
  await auth(page);
  await page.route("**/sage/telegram-hosted/pair/status*", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ configured: true, has_pending_code: false, paired: false }),
    }),
  );
  await page.route("**/sage/telegram-hosted/pair/start", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        pairing_code: "482913",
        deep_link: "https://t.me/EmpyralisSageBot?start=abc123",
        bot_username: "EmpyralisSageBot",
        status: "active",
      }),
    }),
  );
  await gotoFleet(page);
  await page.locator(".fleet-pair-panel button", { hasText: "Pair Telegram" }).click();
  await expect(page.locator(".fleet-pair-code-value")).toBeVisible();
  await expect(page.locator(".fleet-pair-code-value")).toHaveText("482913");
  await expect(page.locator(".fleet-btn--accent", { hasText: "Open @EmpyralisSageBot" })).toBeVisible();
  await page.screenshot({ path: `${OUT}-pair-shortcode-light.png` });
});

test("pair panel — long-token response hides CODE box (regression)", async ({ page }) => {
  await auth(page);
  await page.route("**/sage/telegram-hosted/pair/status*", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ configured: true, has_pending_code: true, paired: false }),
    }),
  );
  await page.route("**/sage/telegram-hosted/pair/start", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        // This is the exact shape that exposed the bug: the long deep-link
        // token reused as `pairing_code` because a pairing session already
        // existed for this workspace.
        pairing_code: "kQKmtZCd4tbi0sFPyfSLbRXfJoggvni1",
        deep_link: "https://t.me/EmpyralisSageBot?start=kQKmtZCd4tbi0sFPyfSLbRXfJoggvni1",
        bot_username: "EmpyralisSageBot",
        status: "active",
      }),
    }),
  );
  await gotoFleet(page);
  await page.locator(".fleet-pair-panel button", { hasText: "Pair Telegram" }).click();
  await expect(page.locator(".fleet-btn--accent", { hasText: "Open @EmpyralisSageBot" })).toBeVisible();
  // The regression: this must NOT render a garbled truncated code.
  await expect(page.locator(".fleet-pair-code-value")).toHaveCount(0);
  await page.waitForTimeout(200);
  await page.screenshot({ path: `${OUT}-pair-longtoken-fixed-light.png` });
});
