/**
 * Settings -> Connections -> "Or add your own computer instead" is the entire
 * front door for connecting a machine you already own. It is one `useState`
 * toggle (HardwareSection.tsx) in front of GatewayPairPanel, so it works if and
 * only if React actually attached to that subtree — which means a unit test can
 * never cover it. `planAgentCountShape`-style pure-module tests, `tsc`, and the
 * whole `npm run test:unit` suite all stay green on a page whose React never
 * hydrates and whose every button is therefore dead.
 *
 * So this drives a REAL browser and a REAL user click, and asserts the only
 * thing that matters to a customer: the form appears, and it mints a real
 * pairing token. Nothing here reaches into the component — no evaluate(), no
 * dispatchEvent, no handler invoked directly. If the assertion below can only
 * be made to pass by calling the handler from JavaScript, the button is dead
 * and this test is doing its job by failing.
 *
 * It also fails on a React hydration error anywhere on the page (#418/#423 and
 * friends), because that is the failure mode that produces a rendered-but-inert
 * tree — a control that looks perfect in the DOM, is topmost and unobscured,
 * carries an onClick in its React props, and still does nothing when clicked.
 *
 * Runs against the default e2e stack (`ws-1`, seeded by
 * scripts/start-e2e-backend.sh). Point it at another stack with
 * E2E_WORKSPACE_ID / PLAYWRIGHT_BASE_URL when reproducing a specific report.
 */
import { test, expect, type Page } from "@playwright/test";

const WORKSPACE_ID = process.env.E2E_WORKSPACE_ID ?? "ws-1";

/** React's own hydration/render failures, minified and unminified. A page that
 *  emits one of these has a subtree that renders and never attaches. */
const HYDRATION_ERROR = /Minified React error #(418|423|425)|Hydration failed|hydrat(ed|ion).*(mismatch|did not match)|There was an error while hydrating/i;

function watchForHydrationErrors(page: Page): string[] {
  const seen: string[] = [];
  page.on("pageerror", (err) => {
    if (HYDRATION_ERROR.test(err.message)) seen.push(`pageerror: ${err.message}`);
  });
  page.on("console", (msg) => {
    if (msg.type() === "error" && HYDRATION_ERROR.test(msg.text())) seen.push(`console: ${msg.text()}`);
  });
  return seen;
}

async function loginOwner(page: Page): Promise<void> {
  await page.context().clearCookies();
  const res = await page.request.post("/api/auth/login", {
    data: { email: "owner@example.com", password: "password-123", channel: "web" },
  });
  expect(res.ok()).toBeTruthy();
}

test("Connections — 'Or add your own computer instead' opens the pairing form on a real click", async ({ page }) => {
  const hydrationErrors = watchForHydrationErrors(page);
  await loginOwner(page);

  await page.goto(`/w/${WORKSPACE_ID}/settings/connections`, { waitUntil: "domcontentloaded" });

  const toggle = page.getByRole("button", { name: /Or add your own computer instead/i });
  await expect(toggle).toBeVisible();

  // The form must not already be on screen — otherwise the click below would
  // assert nothing.
  await expect(page.locator(".gw-pair-panel")).toHaveCount(0);

  await toggle.click();

  // The four things the founder looks for: device label, platform picker,
  // full-access checkbox, generate button.
  await expect(page.locator(".gw-pair-panel")).toBeVisible();
  await expect(page.locator(".gw-pair-panel-field", { hasText: "Device label" })).toBeVisible();
  await expect(page.locator(".gw-pair-panel-field input[type=text]")).toBeVisible();
  await expect(page.locator(".gw-pair-panel-field select")).toBeVisible();
  await expect(page.locator(".gw-pair-panel-fullaccess-toggle input[type=checkbox]")).toBeVisible();
  await expect(page.getByRole("button", { name: /Generate pairing command/i })).toBeVisible();

  expect(hydrationErrors, "React hydration errors on Connections").toEqual([]);
});

test("Connections — 'Generate pairing command' mints a real token on a real click", async ({ page }) => {
  const hydrationErrors = watchForHydrationErrors(page);
  await loginOwner(page);

  await page.goto(`/w/${WORKSPACE_ID}/settings/connections`, { waitUntil: "domcontentloaded" });
  await page.getByRole("button", { name: /Or add your own computer instead/i }).click();

  const label = page.locator(".gw-pair-panel-field input[type=text]");
  await label.fill("Playwright device");

  await page.getByRole("button", { name: /Generate pairing command/i }).click();

  // A real gpair_ token in a real command block. Asserting on the token prefix
  // rather than on "the block is non-empty" is deliberate: the panel renders
  // "Pairing token unavailable…" into the SAME <pre> when the intent comes back
  // empty (see pairing-command.ts's three-way union), so a non-empty assertion
  // would pass on the exact failure this exists to catch.
  const command = page.locator(".gw-pair-panel-command");
  await expect(command).toBeVisible();
  await expect(command).toContainText(/EMPYRALIS_GATEWAY_PAIRING_TOKEN="gpair_/);
  await expect(command).toContainText("Playwright device");

  expect(hydrationErrors, "React hydration errors on Connections").toEqual([]);
});
