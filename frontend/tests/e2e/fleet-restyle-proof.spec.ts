/**
 * Fleet UI restyle — visual proof.
 *
 * Captures real, authed screenshots of the restyled fleet surface:
 *   (a) Fleet Home — LIGHT
 *   (b) Fleet Home — DARK
 *   (c) Agent detail panel open over dimmed background
 *   (d) Empty / not-deployed state
 *
 * Usage (against the already-running dev server on :3000):
 *   PLAYWRIGHT_REUSE_EXISTING_SERVER=1 PLAYWRIGHT_DISABLE_WEBSERVER=1 \
 *   npx playwright test tests/e2e/fleet-restyle-proof.spec.ts
 */
import { test, expect } from "@playwright/test";

const WORKSPACE_ID = "ws-1";
const FRONTEND = "http://localhost:3000";
const OUT = "test-results/restyle";

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
  // Wait for client-side agent fetch to render at least the header.
  await expect(page.locator(".fleet-title")).toBeVisible();
  await page.waitForTimeout(600);
}

async function setTheme(page: any, theme: "light" | "dark") {
  await page.evaluate((t: "light" | "dark") => {
    const root = document.querySelector(".fleet-root");
    return root?.getAttribute("data-theme") === t;
  }, theme);
  const current = await page.locator(".fleet-root").getAttribute("data-theme");
  if (current !== theme) {
    await page.locator('[aria-label="Toggle theme"]').click();
    await page.waitForTimeout(250);
  }
  await expect(page.locator(`.fleet-root[data-theme="${theme}"]`)).toBeVisible();
}

test("fleet restyle — light, dark, detail, empty", async ({ page }) => {
  await auth(page);
  await gotoFleet(page);

  // (b) DARK home (default)
  await setTheme(page, "dark");
  await page.screenshot({ path: `${OUT}-home-dark.png` });

  // (c) Detail overlay over dimmed bg (dark)
  await page.locator(".fleet-card").first().click();
  await expect(page.locator(".fleet-detail")).toBeVisible();
  await expect(page.locator(".fleet-detail-backdrop")).toBeVisible();
  await page.waitForTimeout(250);
  await page.screenshot({ path: `${OUT}-detail-dark.png` });
  await page.keyboard.press("Escape");
  await expect(page.locator(".fleet-detail")).toHaveCount(0);

  // (a) LIGHT home
  await setTheme(page, "light");
  await page.screenshot({ path: `${OUT}-home-light.png` });

  // Detail overlay (light) for contrast proof
  await page.locator(".fleet-card").first().click();
  await expect(page.locator(".fleet-detail")).toBeVisible();
  await page.waitForTimeout(250);
  await page.screenshot({ path: `${OUT}-detail-light.png` });
  await page.keyboard.press("Escape");

  // (d) Empty / not-deployed state — force zero agents via API mock.
  await page.route("**/fleet/agents", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ ok: true, agents: [] }) })
  );
  await page.reload({ waitUntil: "networkidle" });
  await expect(page.locator(".fleet-empty")).toBeVisible();
  await page.waitForTimeout(300);
  await page.screenshot({ path: `${OUT}-empty-light.png` });

  // Collapsed-rail proof (light)
  await page.unroute("**/fleet/agents");
  await page.reload({ waitUntil: "networkidle" });
  await gotoFleet(page);
  await setTheme(page, "light");
  await page.locator('[aria-label="Toggle rail"]').click();
  await page.waitForTimeout(300);
  await expect(page.locator(".fleet-rail--collapsed")).toBeVisible();
  await page.screenshot({ path: `${OUT}-collapsed-light.png` });
});
