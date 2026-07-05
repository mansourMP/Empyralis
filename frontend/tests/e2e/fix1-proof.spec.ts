/**
 * Phase Fix-1 proof — full 5-step wizard walkthrough after fixing the 405
 * blocker (F1) and the SQLite registry-seeding gap (F2). Captures every
 * step for the first time, plus the F4 (plain-language presets) and F5
 * (resolved model) fixes.
 *
 * Usage:
 *   PLAYWRIGHT_DISABLE_WEBSERVER=1 \
 *   npx playwright test tests/e2e/fix1-proof.spec.ts --reporter=list
 */
import { test, expect } from "@playwright/test";
import path from "path";
import fs from "fs";

const WORKSPACE_ID = "ws-1";
const FRONTEND = "http://localhost:3000";
const OUT_DIR = path.resolve(__dirname, "../../../docs/ui-proof");

test.use({ baseURL: FRONTEND });

function save(page: any, name: string) {
  fs.mkdirSync(OUT_DIR, { recursive: true });
  return page.screenshot({ path: path.join(OUT_DIR, `empyralis-fix1-${name}.png`), fullPage: false });
}

async function login(page: any) {
  await page.context().clearCookies();
  const res = await page.request.post("/api/auth/login", {
    data: { email: "owner@example.com", password: "password-123", channel: "web" },
  });
  expect(res.ok()).toBeTruthy();
}

async function setDark(page: any) {
  await page.evaluate(() => {
    window.localStorage.setItem("empyralis.account-shell.v2", JSON.stringify({ globalTheme: "dark" }));
  });
}

test("Fix-1 — full wizard walkthrough, all 5 steps", async ({ page }) => {
  await login(page);
  await page.goto(`/w/${WORKSPACE_ID}/fleet`, { waitUntil: "domcontentloaded" });
  await setDark(page);
  await page.reload({ waitUntil: "domcontentloaded" });
  await page.waitForTimeout(1200);

  await page.locator("button", { hasText: "New agent" }).click();
  await page.waitForTimeout(400);

  // Step 1: Name
  await save(page, "step1-name");
  await page.locator(".fleet-wizard-input").first().fill("Fix-1 Playwright Agent");
  await page.locator(".fleet-wizard-footer button.fleet-btn--accent").click();
  await page.waitForTimeout(400);

  // Step 2: Purpose (plain-language labels from F4)
  await save(page, "step2-purpose-plain-language");
  const purposeLabels = await page.locator(".fleet-wizard-option-label").allTextContents();
  console.log("PURPOSE LABELS:", purposeLabels);

  // Click Next — this is the exact action that previously threw HTTP 405
  await page.locator(".fleet-wizard-footer button.fleet-btn--accent").click();
  await page.waitForTimeout(1500);

  // Step 3: Provider — should render with NO error text
  const errorVisible = await page.locator(".fleet-channel-expand-error").isVisible().catch(() => false);
  console.log("STEP 2->3 ERROR VISIBLE (should be false):", errorVisible);
  await save(page, "step3-provider-no-405");
  await expect(page.locator("text=Who pays for the brain")).toBeVisible();

  await page.locator(".fleet-wizard-footer button.fleet-btn--accent").click();
  await page.waitForTimeout(800);

  // Step 4: Channels
  await save(page, "step4-channels");
  await expect(page.locator("text=Connect channels")).toBeVisible();

  await page.locator(".fleet-wizard-footer button.fleet-btn--accent").click();
  await page.waitForTimeout(500);

  // Step 5: Hardware
  await save(page, "step5-hardware");
  await expect(page.locator("text=Where does it run")).toBeVisible();

  // Finish
  await page.locator(".fleet-wizard-footer button.fleet-btn--accent").click();
  await page.waitForTimeout(800);

  // Agent detail modal should auto-open
  await save(page, "finish-agent-detail-opened");
  await expect(page.locator(".fleet-detail-name")).toHaveText("Fix-1 Playwright Agent");

  // Click Model tab — F5 proof
  await page.locator(".fleet-detail-nav-tab", { hasText: "Model" }).click();
  await page.waitForTimeout(400);
  await save(page, "model-tab-resolved");
  const modelRowText = await page.locator(".fleet-config-row").allTextContents();
  console.log("MODEL TAB ROWS:", modelRowText);

  // Close and confirm the agent appears in the fleet grid
  await page.locator(".fleet-detail-close").click();
  await page.waitForTimeout(500);
  await save(page, "fleet-grid-with-new-agent");
  await expect(page.locator("text=Fix-1 Playwright Agent")).toBeVisible();
});
