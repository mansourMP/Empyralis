/**
 * Fleet UI — density/rail/palette/pair-panel visual proof.
 *
 * Captures:
 *   (a) Fleet home, new density — light + dark
 *   (b) Rail collapsed + expanded — light
 *   (c) Command palette open — light + dark
 *   (d) Empty/pair state with Pair Telegram panel — light
 *
 * Usage (against the already-running dev server on :3000):
 *   PLAYWRIGHT_REUSE_EXISTING_SERVER=1 PLAYWRIGHT_DISABLE_WEBSERVER=1 \
 *   npx playwright test tests/e2e/fleet-crispness-proof.spec.ts
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
  expect(res.ok(), `auth failed: ${await res.text()}`).toBeTruthy();
}

async function gotoFleet(page: any) {
  await page.goto(`/w/${WORKSPACE_ID}/fleet`, { waitUntil: "networkidle" });
  await expect(page.locator(".fleet-rail")).toBeVisible();
  await expect(page.locator(".fleet-title")).toBeVisible();
  await page.waitForTimeout(500);
}

async function setTheme(page: any, theme: "light" | "dark") {
  const current = await page.locator(".fleet-root").getAttribute("data-theme");
  if (current !== theme) {
    await page.locator('[aria-label="Toggle theme"]').click();
    await page.waitForTimeout(250);
  }
  await expect(page.locator(`.fleet-root[data-theme="${theme}"]`)).toBeVisible();
}

test("fleet crispness — density, rail, palette, pair panel", async ({ page }) => {
  await auth(page);
  await gotoFleet(page);
  await setTheme(page, "dark");

  // (a) Home — new density, dark. Should show Pair Telegram panel (unpaired ws-1).
  await page.screenshot({ path: `${OUT}-home-dark.png` });

  // (a) Home — light
  await setTheme(page, "light");
  await page.screenshot({ path: `${OUT}-home-light.png` });

  // (b) Rail expanded (default) — confirm sections render
  await expect(page.locator(".fleet-rail-section-header").first()).toBeVisible();
  await page.screenshot({ path: `${OUT}-rail-expanded-light.png` });

  // Collapse a section (Infrastructure) and confirm chevron + collapse
  const infraHeader = page.locator(".fleet-rail-section-header", { hasText: "Infrastructure" });
  await infraHeader.click();
  await page.waitForTimeout(200);
  await page.screenshot({ path: `${OUT}-rail-section-collapsed-light.png` });
  await infraHeader.click(); // restore
  await page.waitForTimeout(200);

  // (b) Rail fully collapsed (icon-only)
  await page.locator('[aria-label="Toggle rail"]').click();
  await page.waitForTimeout(300);
  await expect(page.locator(".fleet-rail--collapsed")).toBeVisible();
  await page.screenshot({ path: `${OUT}-rail-collapsed-light.png` });
  await page.locator('[aria-label="Toggle rail"]').click();
  await page.waitForTimeout(300);

  // (c) Command palette — light
  await page.keyboard.press("Meta+k");
  await expect(page.locator(".fleet-palette")).toBeVisible();
  await page.waitForTimeout(200);
  await page.screenshot({ path: `${OUT}-palette-light.png` });

  // Type to filter
  await page.keyboard.type("hardware");
  await page.waitForTimeout(150);
  await page.screenshot({ path: `${OUT}-palette-filtered-light.png` });
  await page.keyboard.press("Escape");
  await expect(page.locator(".fleet-palette")).toHaveCount(0);

  // (c) Command palette — dark
  await setTheme(page, "dark");
  await page.keyboard.press("Meta+k");
  await expect(page.locator(".fleet-palette")).toBeVisible();
  await page.waitForTimeout(200);
  await page.screenshot({ path: `${OUT}-palette-dark.png` });
  await page.keyboard.press("Escape");

  // (d) Pair Telegram panel close-up (light, still on ws-1 which is unpaired)
  // NOTE: does not click "Pair Telegram" here — that mutates real backend
  // pairing state. The pre/post-click rendering (including the response-shape
  // regression fix) is covered deterministically in
  // fleet-pair-panel-fix.spec.ts via mocked API responses.
  await setTheme(page, "light");
  await expect(page.locator(".fleet-pair-panel")).toBeVisible();
  await page.screenshot({ path: `${OUT}-pair-panel-light.png` });
});
