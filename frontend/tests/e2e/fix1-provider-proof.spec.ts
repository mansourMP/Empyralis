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
  await page.request.post("/api/auth/login", {
    data: { email: "owner@example.com", password: "password-123", channel: "web" },
  });
}

async function setDark(page: any) {
  await page.evaluate(() => {
    try {
      const raw = window.localStorage.getItem("empyralis.account-shell.v2");
      const next = raw ? JSON.parse(raw) : {};
      window.localStorage.setItem("empyralis.account-shell.v2", JSON.stringify({ ...next, globalTheme: "dark" }));
      document.documentElement.setAttribute("data-theme", "dark");
    } catch {}
  });
}

test("Provider step — all 4 modes", async ({ page }) => {
  await login(page);
  await page.goto(`/w/${WORKSPACE_ID}/fleet`, { waitUntil: "domcontentloaded" });
  await setDark(page);
  await page.waitForTimeout(1200);

  // Open wizard
  await page.locator("button", { hasText: "New agent" }).click();
  await page.waitForTimeout(400);

  // Step 1 — fill name
  await page.locator(".fleet-wizard-input").first().fill("Provider Test");
  await page.locator(".fleet-wizard-footer button.fleet-btn--accent").click();
  await page.waitForTimeout(400);

  // Step 2 — click Next
  await page.locator(".fleet-wizard-footer button.fleet-btn--accent").click();
  await page.waitForTimeout(1200);

  // Step 3 — should be on Provider step now
  const title = page.locator("text=Who pays for the brain");
  const visible = await title.isVisible().catch(() => false);
  console.log("STEP 3 VISIBLE:", visible);

  if (!visible) {
    // Check for error
    const err = page.locator(".fleet-channel-expand-error");
    if (await err.isVisible().catch(() => false)) {
      console.log("ERROR:", await err.textContent());
    }
    await save(page, "provider-step-error");
    return;
  }

  // Screenshot platform credits (default)
  await save(page, "provider-platform-credits");

  // Click "Your own API key" and screenshot
  await page.locator(".fleet-wizard-option", { hasText: "Your own API key" }).click();
  await page.waitForTimeout(300);
  await save(page, "provider-byok-expanded");

  // Count provider options
  const opts = await page.locator(".fleet-wizard-input option").all();
  console.log(`BYOK PROVIDER OPTIONS: ${opts.length}`);
  for (const o of opts) {
    console.log(" ", await o.textContent());
  }

  // Click "Your subscription" and screenshot
  await page.locator(".fleet-wizard-option", { hasText: "Your subscription" }).click();
  await page.waitForTimeout(300);
  await save(page, "provider-subscription");

  // Click "Run locally" and screenshot
  await page.locator(".fleet-wizard-option", { hasText: "Run locally" }).click();
  await page.waitForTimeout(300);
  await save(page, "provider-local");

  console.log("DONE — all 4 modes captured");
});
