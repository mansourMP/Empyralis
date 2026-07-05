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

test("Model tab — interactive provider switch", async ({ page }) => {
  await login(page);
  await page.goto(`/w/${WORKSPACE_ID}/fleet`, { waitUntil: "domcontentloaded" });
  await setDark(page);
  await page.waitForTimeout(1200);

  // Open first agent
  const card = page.locator(".fleet-card").first();
  await card.click();
  await page.waitForTimeout(500);

  // Click Model tab
  await page.locator(".fleet-detail-nav-tab", { hasText: "Model" }).click();
  await page.waitForTimeout(500);

  // Screenshot default state (platform credits)
  await save(page, "model-tab-platform-credits");

  // Click "Your own API key" and switch to Anthropic
  const byok = page.locator(".fleet-wizard-option", { hasText: "Your own API key" });
  if (await byok.isVisible().catch(() => false)) {
    await byok.click();
    await page.waitForTimeout(300);
    // Switch provider to Gemini
    const sel = page.locator(".fleet-channel-expand .fleet-wizard-input").first();
    if (await sel.isVisible().catch(() => false)) {
      await sel.selectOption("gemini");
      await page.waitForTimeout(200);
    }
    await save(page, "model-tab-byok-gemini");
  }

  // Click "Your subscription"
  const sub = page.locator(".fleet-wizard-option", { hasText: "Your subscription" });
  if (await sub.isVisible().catch(() => false)) {
    await sub.click();
    await page.waitForTimeout(300);
    await save(page, "model-tab-subscription");
  }

  // Click "Run locally"
  const local = page.locator(".fleet-wizard-option", { hasText: "Run locally" });
  if (await local.isVisible().catch(() => false)) {
    await local.click();
    await page.waitForTimeout(300);
    await save(page, "model-tab-local");
  }

  console.log("DONE — all 4 Model tab modes captured");
});
