/**
 * Phase U7 proof — Channels/Connectors platform grids, GatewayPairPanel,
 * Hardware page, Home density strip. Light + dark.
 *
 * Usage:
 *   PLAYWRIGHT_DISABLE_WEBSERVER=1 \
 *   npx playwright test tests/e2e/fleet-u7-proof.spec.ts
 */
import { test, expect } from "@playwright/test";

const WORKSPACE_ID = "ws-1";
const FRONTEND = "http://localhost:3000";

test.use({ baseURL: FRONTEND });

async function saveScreenshot(page: any, name: string) {
  await page.screenshot({ path: `test-results/empyralis-u7-${name}.png`, fullPage: false });
}

async function authenticateOwner(page: any) {
  await page.context().clearCookies();
  const response = await page.request.post("/api/auth/login", {
    data: { email: "owner@example.com", password: "password-123", channel: "web" },
  });
  expect(response.ok()).toBeTruthy();
}

async function setTheme(page: any, theme: "light" | "dark") {
  // Theme is account-wide (empyralis.account-shell.v2's globalTheme), not a
  // fleet-local key — see docs/UI-MODEL.md theme section.
  await page.evaluate((t: string) => {
    const storageKey = "empyralis.account-shell.v2";
    try {
      const raw = window.localStorage.getItem(storageKey);
      const next = raw ? JSON.parse(raw) : {};
      window.localStorage.setItem(storageKey, JSON.stringify({ ...next, globalTheme: t }));
    } catch {
      window.localStorage.setItem(storageKey, JSON.stringify({ globalTheme: t }));
    }
  }, theme);
}

for (const theme of ["light", "dark"] as const) {
  test(`Fleet U7 proof — ${theme}`, async ({ page }) => {
    await authenticateOwner(page);

    // Home density strip
    await page.goto(`/w/${WORKSPACE_ID}/fleet`, { waitUntil: "networkidle" });
    await setTheme(page, theme);
    await page.reload({ waitUntil: "networkidle" });
    await page.waitForTimeout(1200);
    await expect(page.locator(".fleet-status-strip")).toBeVisible();
    await saveScreenshot(page, `home-density-${theme}`);

    // Channels platform grid
    await page.locator(".fleet-card").first().click();
    await page.waitForTimeout(400);
    await page.locator(".fleet-detail-nav-tab", { hasText: "Channels" }).click();
    await page.waitForTimeout(400);
    await expect(page.locator(".fleet-channel-grid")).toBeVisible();
    await saveScreenshot(page, `channels-grid-${theme}`);

    // Connectors platform grid
    await page.locator(".fleet-detail-nav-tab", { hasText: "Connectors" }).click();
    await page.waitForTimeout(400);
    await expect(page.locator(".fleet-connector-grid")).toBeVisible();
    await saveScreenshot(page, `connectors-grid-${theme}`);
    await page.locator(".fleet-detail-close").click();

    // Hardware page + GatewayPairPanel inline
    await page.goto(`/w/${WORKSPACE_ID}/hardware`, { waitUntil: "networkidle" });
    await page.waitForTimeout(800);
    await page.locator("button", { hasText: "Connect" }).first().click();
    await page.waitForTimeout(400);
    const manualLink = page.locator(".workstation-hardware-inline-link", { hasText: "tray app" });
    if (await manualLink.count() > 0) {
      await manualLink.click();
      await page.waitForTimeout(300);
    }
    await saveScreenshot(page, `hardware-pairpanel-${theme}`);
  });
}
