/**
 * Phase UX-A: Fleet UI visual proof — real screenshots.
 *
 * Captures:
 *   (a) Fleet Home — agent card with real status dot + placement label
 *   (b) Agent detail panel — Activity tab + Model tab
 *   (d) Sage chat — one click from Home → chat
 *
 * Usage:
 *   PLAYWRIGHT_REUSE_EXISTING_SERVER=1 PLAYWRIGHT_DISABLE_WEBSERVER=1 \
 *   npx playwright test tests/e2e/fleet-visual-capture.spec.ts --headed
 */
import { test, expect } from "@playwright/test";

const VISUAL_LABEL = process.env.VISUAL_LABEL || "empyralis";
const WORKSPACE_ID = "ws-1";
const FRONTEND = "http://localhost:3000";

// Override baseURL to use localhost instead of Playwright's 127.0.0.1:3100 default
test.use({ baseURL: FRONTEND });

async function saveScreenshot(page: any, name: string) {
  await page.screenshot({
    path: `test-results/${VISUAL_LABEL}-fleet-${name}.png`,
    fullPage: false,
  });
}

async function authenticateOwner(page: any) {
  await page.context().clearCookies();
  const response = await page.request.post("/api/auth/login", {
    data: { email: "owner@example.com", password: "password-123", channel: "web" },
  });
  const body = await response.text();
  console.log(`Auth response: ${response.status()} — ${body.substring(0, 200)}`);
  expect(response.ok(), `Auth failed: ${body}`).toBeTruthy();
}

test.describe("Fleet UI — Visual Proof", () => {
  test("(a) Fleet Home — real agent card", async ({ page }) => {
    await authenticateOwner(page);
    await page.goto(`/w/${WORKSPACE_ID}/fleet`, { waitUntil: "networkidle" });
    await page.waitForTimeout(3000);

    // Verify the rail is visible (Phase UX-C)
    await expect(page.locator(".fleet-rail")).toBeVisible();

    const cardCount = await page.locator(".fleet-card").count();
    console.log(`Fleet Home: ${cardCount} agent cards`);

    if (cardCount > 0) {
      await expect(page.locator(".fleet-card-status-dot").first()).toBeVisible();
      await page.locator(".fleet-card").first().click();
      await page.waitForTimeout(500);
    }

    await saveScreenshot(page, "01-fleet-home");
  });

  test("(b) Agent detail modal — Overview + Model", async ({ page }) => {
    await authenticateOwner(page);
    await page.goto(`/w/${WORKSPACE_ID}/fleet`, { waitUntil: "networkidle" });
    await page.waitForTimeout(3000);

    const cards = page.locator(".fleet-card");
    if ((await cards.count()) > 0) {
      await cards.first().click();
      await page.waitForTimeout(800);

      const detail = page.locator(".fleet-detail");
      if (await detail.isVisible()) {
        // Model tab (real data from agent config) — nav order:
        // Overview, Chat, Memory, Channels, Connectors, Tools, Model
        await page.locator(".fleet-detail-nav-tab").nth(6).click();
        await page.waitForTimeout(500);
        await saveScreenshot(page, "02-detail-model");

        // Overview tab (status/placement/role + recent activity)
        await page.locator(".fleet-detail-nav-tab").first().click();
        await page.waitForTimeout(500);
        await saveScreenshot(page, "03-detail-overview");
      }
    }
  });

  test("(d) Sage chat — one click from Home", async ({ page }) => {
    await authenticateOwner(page);
    await page.goto(`/w/${WORKSPACE_ID}/fleet`, { waitUntil: "networkidle" });
    await page.waitForTimeout(3000);

    const chatBtn = page.locator('[aria-label="Open chat with agent"]');
    if ((await chatBtn.count()) > 0) {
      await chatBtn.first().click();
      await page.waitForTimeout(3000);
      const url = page.url();
      console.log(`Chat navigation URL: ${url}`);
      expect(url).toContain("chat");
      await saveScreenshot(page, "04-sage-chat");
    } else {
      console.log("No Sage chat button — direct navigation");
      await page.goto(`/w/${WORKSPACE_ID}/chat`, { waitUntil: "networkidle" });
      await page.waitForTimeout(3000);
      await saveScreenshot(page, "04-sage-chat-direct");
    }
  });
});
