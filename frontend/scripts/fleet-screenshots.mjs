/**
 * Phase UX-A: Fleet UI screenshot capture.
 * Usage: node scripts/fleet-screenshots.mjs
 */
import { chromium } from "@playwright/test";

const FRONTEND = "http://localhost:3000";
const WORKSPACE_ID = "ws-1";
const OUT_DIR = "test-results";

async function main() {
  const browser = await chromium.launch({ headless: true });
  const context = await browser.newContext({
    viewport: { width: 1512, height: 982 },
    colorScheme: "light",
  });

  const page = await context.newPage();

  // Login via UI (form submit)
  console.log("→ Logging in via UI...");
  await page.goto(`${FRONTEND}/login`, { waitUntil: "networkidle", timeout: 30000 });
  await page.waitForTimeout(1000);

  await page.fill('input[name="email"]', "owner@example.com");
  await page.fill('input[name="password"]', "password-123");

  // Enable submit button via JS (bypasses React disabled state)
  const btn = page.locator('button[type="submit"]');
  await btn.evaluate(el => { el.disabled = false; });
  await btn.click();
  await page.waitForTimeout(4000);

  console.log(`  Login result URL: ${page.url()}`);
  const bodyText = await page.locator("body").innerText();
  console.log(`  Body: ${bodyText.substring(0, 100)}`);

  // If still on login page, login failed
  if (page.url().includes("/login")) {
    console.error("Login failed — still on login page");
    await page.screenshot({ path: `${OUT_DIR}/debug-login-failed.png` });
    await browser.close();
    process.exit(1);
  }

  console.log("  ✓ Logged in!");

  // (a) Fleet Home
  console.log("\n→ (a) Fleet Home...");
  await page.goto(`${FRONTEND}/w/${WORKSPACE_ID}/fleet`, {
    waitUntil: "networkidle", timeout: 30000,
  });
  await page.waitForTimeout(3000);

  console.log(`  URL: ${page.url()}`);
  console.log(`  Title: ${await page.title()}`);

  const railVisible = await page.locator(".fleet-rail").isVisible().catch(() => false);
  const cardCount = await page.locator(".fleet-card").count();
  const emptyState = await page.locator(".fleet-empty-state").isVisible().catch(() => false);
  const bodyPreview = (await page.locator("body").innerText().catch(() => "")).substring(0, 200);

  console.log(`  Rail: ${railVisible ? "✓" : "✗"}  Cards: ${cardCount}  Empty: ${emptyState}`);
  console.log(`  Body: ${bodyPreview}`);

  if (cardCount > 0) {
    await page.locator(".fleet-card").first().click();
    await page.waitForTimeout(500);
  }

  await page.screenshot({ path: `${OUT_DIR}/empyralis-fleet-01-home.png`, fullPage: true });
  console.log("  ✓ empyralis-fleet-01-home.png");

  // (b) Detail panel
  if (cardCount > 0) {
    const detailVisible = await page.locator(".fleet-detail").isVisible().catch(() => false);
    console.log(`\n→ (b) Detail panel: ${detailVisible ? "visible" : "hidden"}`);
    if (detailVisible) {
      await page.locator(".fleet-detail-tab").nth(4).click();
      await page.waitForTimeout(500);
      await page.screenshot({ path: `${OUT_DIR}/empyralis-fleet-02-detail-model.png` });
      console.log("  ✓ empyralis-fleet-02-detail-model.png");

      await page.locator(".fleet-detail-tab").first().click();
      await page.waitForTimeout(500);
      await page.screenshot({ path: `${OUT_DIR}/empyralis-fleet-03-detail-activity.png` });
      console.log("  ✓ empyralis-fleet-03-detail-activity.png");
    }
  }

  // (d) Sage chat
  console.log("\n→ (d) Sage chat...");
  await page.goto(`${FRONTEND}/w/${WORKSPACE_ID}/chat`, {
    waitUntil: "networkidle", timeout: 30000,
  });
  await page.waitForTimeout(3000);
  console.log(`  URL: ${page.url()}`);
  await page.screenshot({ path: `${OUT_DIR}/empyralis-fleet-04-sage-chat.png` });
  console.log("  ✓ empyralis-fleet-04-sage-chat.png");

  await browser.close();
  console.log(`\n✓ All screenshots saved to ${OUT_DIR}/`);
}

main().catch((err) => {
  console.error("Fatal error:", err.message);
  process.exit(1);
});
