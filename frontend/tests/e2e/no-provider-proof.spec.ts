import { test, expect } from "@playwright/test";

const WORKSPACE_ID = "ws-1";
const FRONTEND = "http://localhost:3000";
const OUT = "test-results/crisp";

test.use({ baseURL: FRONTEND, viewport: { width: 1280, height: 800 } });

test("web chat shows honest no-provider message, not generic error", async ({ page }) => {
  await page.context().clearCookies();
  const res = await page.request.post("/api/auth/login", {
    data: { email: "owner@example.com", password: "password-123", channel: "web" },
  });
  expect(res.ok()).toBeTruthy();

  await page.goto(`/w/${WORKSPACE_ID}/chat`, { waitUntil: "load" });
  await page.waitForTimeout(2000);

  // Find the chat input — try common selectors used by the chat pane.
  const input = page.locator('textarea, [contenteditable="true"]').first();
  await expect(input).toBeVisible({ timeout: 15000 });
  await input.click();
  await input.fill("hello, is this working?");
  await page.keyboard.press("Enter");

  // Wait for the failure notice to render.
  await page.waitForTimeout(4000);
  await page.screenshot({ path: `${OUT}-no-provider-webchat.png`, fullPage: true });

  const bodyText = await page.locator("body").innerText();
  console.log("=== page text snippet ===");
  console.log(bodyText.slice(0, 2000));
});
