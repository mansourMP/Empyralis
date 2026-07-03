/**
 * Phase U4: Fleet UI screenshot capture.
 * Usage: node frontend/scripts/fleet-screenshots.mjs
 */
import { chromium } from "@playwright/test";

const FRONTEND = "http://localhost:3000";
const WORKSPACE_ID = "ws-1";
const OUT_DIR = "test-results";

async function main() {
  const browser = await chromium.launch({ headless: true });
  const context = await browser.newContext({
    viewport: { width: 1512, height: 982 },
    colorScheme: "dark",
  });
  const page = await context.newPage();

  // Login
  console.log("→ Logging in...");
  await page.goto(`${FRONTEND}/login`, { waitUntil: "networkidle", timeout: 30000 });
  await page.waitForTimeout(1500);
  await page.fill('input[name="email"]', "owner@example.com");
  await page.fill('input[name="password"]', "password-123");
  const btn = page.locator('button[type="submit"]');
  await btn.evaluate(el => { el.disabled = false; });
  await btn.click();
  await page.waitForTimeout(4000);
  if (page.url().includes("/login")) {
    console.error("Login failed:", (await page.locator("body").innerText()).substring(0, 200));
    process.exit(1);
  }
  console.log("  ✓ Logged in");

  // (a) Fleet Home — landing page
  console.log("\n→ (a) Fleet Home landing...");
  await page.goto(`${FRONTEND}/w/${WORKSPACE_ID}`, {
    waitUntil: "networkidle", timeout: 30000,
  });
  await page.waitForTimeout(3000);
  console.log(`  URL: ${page.url()}`);
  console.log(`  Title: ${await page.title()}`);

  const bodyText = await page.locator("body").innerText();
  console.log(`  Body: ${bodyText.substring(0, 300)}`);

  // Check for agent cards (inline style buttons, not .fleet-card class)
  const hasSage = bodyText.includes("Sage") && bodyText.includes("Operator");
  const hasYourFleet = bodyText.includes("Your fleet");
  console.log(`  Has "Your fleet": ${hasYourFleet}  Has Sage operator: ${hasSage}`);

  await page.screenshot({ path: `${OUT_DIR}/empyralis-u4-01-fleet-home.png`, fullPage: true });
  console.log("  ✓ empyralis-u4-01-fleet-home.png");

  // Click "Chat with Sage" to prove one-click to chat
  const chatBtns = page.locator('button');
  const allBtns = await chatBtns.all();
  for (const b of allBtns) {
    const text = await b.innerText();
    if (text.includes("Chat with Sage")) {
      await b.click();
      break;
    }
  }
  await page.waitForTimeout(3000);
  console.log(`  Chat URL: ${page.url()}`);
  await page.screenshot({ path: `${OUT_DIR}/empyralis-u4-04-sage-chat.png`, fullPage: true });
  console.log("  ✓ empyralis-u4-04-sage-chat.png");

  await browser.close();
  console.log(`\n✓ Screenshots saved to ${OUT_DIR}/`);
}

main().catch((err) => {
  console.error("Fatal error:", err.message);
  process.exit(1);
});
