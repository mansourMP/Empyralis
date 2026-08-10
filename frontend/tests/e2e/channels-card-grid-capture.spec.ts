/**
 * Visual capture for the unified square-card channel grid.
 *
 * Not an assertion suite — a screenshot harness, run by hand against a live
 * throwaway stack (frontend/scripts/start-e2e-backend.sh) with two seeded
 * agents: one with a paired gateway (all ~26 cards) and one without (the 7
 * first-party cloud-capable cards). Light and dark, 1440px and the 900px
 * breakpoint.
 *
 * Usage:
 *   PLAYWRIGHT_DISABLE_WEBSERVER=1 PLAYWRIGHT_FRONTEND_PORT=3041 \
 *   npx playwright test tests/e2e/channels-card-grid-capture.spec.ts
 */
import { test, expect } from "@playwright/test";

const WORKSPACE_ID = "ws-1";
const OUT = "test-results/channels-cardgrid";

// A device-state stub for the ONE thing a throwaway stack cannot produce: a
// reachable OpenClaw box. The catalog, the forms, the routing and every pixel
// of the grid are the real thing — only `observed` is injected, so the pill
// variety (Ready / Needs credential / Not installed / Switched off) and the
// three-state panel can be seen at all. The unstubbed pass below captures the
// honest offline screen too.
function observedFor(channelId: string, index: number) {
  const mode = index % 4;
  return {
    channel_id: channelId,
    channel_key: `openclaw_${channelId}`,
    installed: mode !== 2,
    requires_plugin: true,
    enabled: mode === 0,
    configured: mode === 0 || mode === 3,
    fields:
      mode === 1
        ? [{ name: "appId", secret: false, type: "string", set: true }, { name: "appSecret", secret: true, type: "string", set: false }]
        : [{ name: "appId", secret: false, type: "string", set: true }, { name: "appSecret", secret: true, type: "string", set: true }],
    accounts: [],
  };
}

async function authenticateOwner(page: any) {
  await page.context().clearCookies();
  const response = await page.request.post("/api/auth/login", {
    data: { email: "owner@example.com", password: "password-123", channel: "web" },
  });
  expect(response.ok()).toBeTruthy();
}

async function setTheme(page: any, theme: "light" | "dark") {
  await page.evaluate((t: string) => {
    const key = "empyralis.account-shell.v2";
    const raw = window.localStorage.getItem(key);
    const next = raw ? JSON.parse(raw) : {};
    window.localStorage.setItem(key, JSON.stringify({ ...next, globalTheme: t }));
  }, theme);
}

async function openChannelsTab(page: any, agentName: string) {
  await page.goto(`/w/${WORKSPACE_ID}/agents`, { waitUntil: "networkidle" });
  await page.waitForTimeout(900);
  await page.locator(".fleet-agent-row", { hasText: agentName }).first().click();
  await page.waitForTimeout(900);
  // The Configure sheet's tab is a real URL segment (FleetAgentDetail's
  // `tabHref`), so navigate to it rather than driving two clicks.
  await page.goto(page.url().replace(/\/[^/]+$/, "/channels"), { waitUntil: "networkidle" });
  await page.waitForTimeout(1800);
  await expect(page.locator(".fleet-channel-grid")).toBeVisible();
}

/** The Configure sheet's own scroll container, whichever ancestor of the grid
 *  actually overflows — found rather than named, so a class rename does not
 *  silently turn these captures into duplicate top-of-grid frames. */
async function scrollSheet(page: any, to: "top" | "bottom") {
  await page.evaluate((edge: string) => {
    let node: HTMLElement | null = document.querySelector(".fleet-channel-grid");
    while (node) {
      if (node.scrollHeight > node.clientHeight + 8) {
        node.scrollTop = edge === "bottom" ? node.scrollHeight : 0;
        return;
      }
      node = node.parentElement;
    }
  }, to);
  await page.waitForTimeout(500);
}

const scrollSheetToBottom = (page: any) => scrollSheet(page, "bottom");
const scrollSheetToTop = (page: any) => scrollSheet(page, "top");

async function stubObserved(page: any) {
  await page.route("**/personal-channels/openclaw/gateways/**/setup", async (route: any) => {
    const response = await route.fetch();
    const body = await response.json();
    body.observed_error = null;
    body.observed = {
      status: "ok",
      channels: (body.channels || []).map((entry: any, index: number) => observedFor(entry.channel_id, index)),
    };
    await route.fulfill({ response, body: JSON.stringify(body) });
  });
}

for (const theme of ["light", "dark"] as const) {
  test(`unified channel card grid — gateway agent, ${theme}`, async ({ page }) => {
    // Tall enough that the Configure sheet's own scroll container shows the
    // whole grid at once — the sheet scrolls internally, so `fullPage` alone
    // captures only what fits.
    await page.setViewportSize({ width: 1440, height: 1700 });
    await authenticateOwner(page);
    await page.goto(`/w/${WORKSPACE_ID}/agents`, { waitUntil: "domcontentloaded" });
    await setTheme(page, theme);
    await stubObserved(page);
    await openChannelsTab(page, "Gateway Agent");

    const cards = page.locator(".fleet-channel-card");
    expect(await cards.count()).toBeGreaterThan(20);
    await page.screenshot({ path: `${OUT}-gateway-screen-${theme}.png` });
    // The Configure sheet scrolls internally and clips its own content, so the
    // bottom rows of a 26-card grid need a second, scrolled frame — an element
    // screenshot would be clipped by the same overflow container.
    await scrollSheetToBottom(page);
    await page.screenshot({ path: `${OUT}-gateway-screen-bottom-${theme}.png` });
    await scrollSheetToTop(page);

    // Click a transported card: its setup detail opens.
    await page.locator(".fleet-channel-card", { hasText: "Feishu" }).first().click();
    await page.waitForTimeout(400);
    await expect(page.locator(".fleet-channel-banner")).toBeVisible();
    await page.screenshot({ path: `${OUT}-gateway-panel-transported-${theme}.png` });
    await page.locator(".fleet-detail-backdrop .fleet-detail-close").first().click();
    await page.waitForTimeout(400);

    // Click a first-party card: the same shell, the existing setup flow.
    await page.locator(".fleet-channel-card", { hasText: "Telegram" }).first().click();
    await page.waitForTimeout(400);
    await expect(page.locator(".fleet-channel-banner")).toBeVisible();
    await page.screenshot({ path: `${OUT}-gateway-panel-firstparty-${theme}.png` });
    await page.locator(".fleet-detail-backdrop .fleet-detail-close").first().click();
    await page.waitForTimeout(300);

    // The 900px breakpoint: 4 across becomes 2 across.
    await page.setViewportSize({ width: 880, height: 1400 });
    await page.waitForTimeout(700);
    await page.screenshot({ path: `${OUT}-gateway-screen-narrow-${theme}.png` });
    await scrollSheetToBottom(page);
    await page.screenshot({ path: `${OUT}-gateway-screen-narrow-bottom-${theme}.png` });
  });

  test(`unified channel card grid — no-gateway agent, ${theme}`, async ({ page }) => {
    // Tall enough that the Configure sheet's own scroll container shows the
    // whole grid at once — the sheet scrolls internally, so `fullPage` alone
    // captures only what fits.
    await page.setViewportSize({ width: 1440, height: 1700 });
    await authenticateOwner(page);
    await page.goto(`/w/${WORKSPACE_ID}/agents`, { waitUntil: "domcontentloaded" });
    await setTheme(page, theme);
    await openChannelsTab(page, "Cloud Only Agent");

    const cards = page.locator(".fleet-channel-card");
    expect(await cards.count()).toBe(7);
    await page.screenshot({ path: `${OUT}-nogateway-screen-${theme}.png` });
  });
}

/** ONE PLATFORM = ONE CARD. The transport ships Zalo as three channels; the
 *  grid must show one card that opens a three-door picker, exactly like
 *  Telegram — not three cards beside a Telegram that is one. */
test("a platform's variants are one card with doors, never several cards", async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 1700 });
  await authenticateOwner(page);
  await page.goto(`/w/${WORKSPACE_ID}/agents`, { waitUntil: "domcontentloaded" });
  await setTheme(page, "light");
  await stubObserved(page);
  await openChannelsTab(page, "Gateway Agent");

  const zaloCards = page.locator(".fleet-channel-card-label", { hasText: /^Zalo/ });
  expect(await zaloCards.count()).toBe(1);
  expect((await zaloCards.first().innerText()).trim()).toBe("Zalo");

  await page.locator(".fleet-channel-card", { hasText: "Zalo" }).first().click();
  await page.waitForTimeout(400);
  await expect(page.locator(".fleet-channel-banner")).toBeVisible();
  const doors = page.locator(".fleet-channel-banner .fleet-wizard-option");
  expect(await doors.count()).toBe(3);
  await page.screenshot({ path: `${OUT}-zalo-doors-light.png` });

  // Picking a door shows that variant's own three states + its one control.
  await doors.nth(1).click();
  await page.waitForTimeout(300);
  await expect(page.locator(".fleet-door-chosen--compact")).toBeVisible();
  await page.screenshot({ path: `${OUT}-zalo-door-chosen-light.png` });
});

/** THE CHOSEN DOOR IS ONE LINE WHILE A FORM IS SHOWING. Telegram's picker,
 *  Chatbot door: the bar above the token field must be a single row, not a
 *  second card-sized block over the field being typed into. */
test("the chosen-door bar collapses to one line once the form is up", async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 1700 });
  await authenticateOwner(page);
  await page.goto(`/w/${WORKSPACE_ID}/agents`, { waitUntil: "domcontentloaded" });
  await setTheme(page, "light");
  await openChannelsTab(page, "Gateway Agent");

  await page.locator(".fleet-channel-card", { hasText: "Telegram" }).first().click();
  await page.waitForTimeout(400);
  await page.screenshot({ path: `${OUT}-telegram-picker-light.png` });
  await page.locator(".fleet-wizard-option", { hasText: "Chatbot" }).first().click();
  await page.waitForTimeout(400);

  const bar = page.locator(".fleet-door-chosen");
  await expect(bar).toBeVisible();
  await expect(page.locator(".fleet-door-chosen--compact")).toBeVisible();
  const height = (await bar.boundingBox())!.height;
  console.log("CHOSEN DOOR BAR HEIGHT", height);
  expect(height).toBeLessThan(46);
  // The risk is still on screen — collapsed, never dropped.
  expect((await bar.innerText()).toLowerCase()).toContain("ban");
  await page.screenshot({ path: `${OUT}-telegram-chosen-compact-light.png` });

  // The founder's exact scenario: the full-account door, whose setup asks for
  // a phone number. This is where two card-sized blocks used to sit above the
  // field.
  await page.locator(".fleet-door-chosen-change").first().click();
  await page.waitForTimeout(300);
  await page.locator(".fleet-wizard-option", { hasText: "Full account" }).first().click();
  await page.waitForTimeout(600);
  const fullBar = page.locator(".fleet-door-chosen--compact");
  await expect(fullBar).toBeVisible();
  console.log("FULL ACCOUNT BAR HEIGHT", (await fullBar.boundingBox())!.height);
  await page.screenshot({ path: `${OUT}-telegram-fullaccount-compact-light.png` });
  await setTheme(page, "dark");
  await page.reload({ waitUntil: "networkidle" });
  await page.waitForTimeout(1200);
  await page.locator(".fleet-channel-card", { hasText: "Telegram" }).first().click();
  await page.waitForTimeout(400);
  await page.locator(".fleet-wizard-option", { hasText: "Full account" }).first().click();
  await page.waitForTimeout(600);
  await page.screenshot({ path: `${OUT}-telegram-fullaccount-compact-dark.png` });
});

/** THE CONTROL DOES THE WORK; IT DOES NOT EXPLAIN IT. A channel whose plugin
 *  is not on the box shows its three states as chips and ONE button — never a
 *  sentence telling the customer about a package on a disk. */
test("a channel that needs setting up shows a control, not an instruction", async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 1700 });
  await authenticateOwner(page);
  await page.goto(`/w/${WORKSPACE_ID}/agents`, { waitUntil: "domcontentloaded" });
  await setTheme(page, "light");
  await stubObserved(page);
  await openChannelsTab(page, "Gateway Agent");

  // observedFor() puts every 4th channel in the not-installed state; find the
  // card whose pill says so rather than naming a channel.
  const setupCard = page.locator(".fleet-channel-card", { has: page.locator(".fleet-channel-card-pill", { hasText: "Set up" }) });
  expect(await setupCard.count()).toBeGreaterThan(0);
  await setupCard.first().click();
  await page.waitForTimeout(500);
  const panel = page.locator(".fleet-channel-banner");
  await expect(panel).toBeVisible();
  const text = (await panel.innerText()).toLowerCase();
  expect(text).not.toContain("plugin");
  // The three state CHIPS stay — "not installed" is one of the three honest
  // facts, and collapsing them is the thing this panel exists to avoid. What
  // is gone is the SENTENCE: the remediation paragraph is not rendered at all
  // when the button already says everything.
  await expect(panel.locator(".openclaw-channel-detail")).toHaveCount(0);
  await expect(panel.locator(".fleet-btn--accent-fill")).toBeVisible();
  expect((await panel.locator(".fleet-btn--accent-fill").innerText()).trim()).toBe("Set up");
  await page.screenshot({ path: `${OUT}-setup-control-light.png` });
  await setTheme(page, "dark");
  await page.reload({ waitUntil: "networkidle" });
  await page.waitForTimeout(1500);
  await setupCard.first().click();
  await page.waitForTimeout(500);
  await page.screenshot({ path: `${OUT}-setup-control-dark.png` });
});

/** NO FETCH ON CLICK. Opening a first-party card must not start a request and
 *  must not show a spinner: the tab already holds the gateway's channel state.
 *  Measured against a gateway that is NOT reachable — the founder's own case,
 *  and the one where the old per-mount fetch cost 2-3 seconds. */
test("a first-party card opens instantly, with no fetch of its own", async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 1700 });
  await authenticateOwner(page);
  await page.goto(`/w/${WORKSPACE_ID}/agents`, { waitUntil: "domcontentloaded" });
  await setTheme(page, "light");
  await openChannelsTab(page, "Gateway Agent");
  // Let the tab's own shared subscription settle first — that is the whole
  // point: the state is loaded before anything is clicked.
  await page.waitForTimeout(3000);

  const surfaceCalls: string[] = [];
  page.on("request", (req) => {
    if (/\/personal-channels\/gateways\/[^/]+\/channels/.test(req.url())) surfaceCalls.push(req.url());
  });

  const startedAt = Date.now();
  await page.locator(".fleet-channel-card", { hasText: "Signal" }).first().click();
  await page.locator(".fleet-channel-banner .fleet-channel-expand-hint").first().waitFor({ state: "visible" });
  const openMs = Date.now() - startedAt;
  console.log("SIGNAL PANEL OPEN MS", openMs, "surface calls during open:", surfaceCalls.length);
  expect(openMs).toBeLessThan(500);
  expect(surfaceCalls.length).toBe(0);
  await page.screenshot({ path: `${OUT}-signal-panel-light.png` });
});

test("unified channel card grid — gateway agent, real offline device state", async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 1700 });
  await authenticateOwner(page);
  await page.goto(`/w/${WORKSPACE_ID}/agents`, { waitUntil: "domcontentloaded" });
  await setTheme(page, "light");
  await openChannelsTab(page, "Gateway Agent");
  await page.screenshot({ path: `${OUT}-gateway-screen-unstubbed-light.png` });

  // Brand logos must actually LOAD, not merely be in the DOM.
  const logoStatuses = await page.evaluate(async () => {
    const sources = Array.from(document.querySelectorAll<HTMLImageElement>(".fleet-channel-card-icon img")).map(
      (img) => img.src,
    );
    const results: Record<string, number> = {};
    for (const src of sources) {
      const res = await fetch(src);
      results[new URL(src).pathname] = res.status;
    }
    return results;
  });
  console.log("BRAND LOGO STATUSES", JSON.stringify(logoStatuses, null, 2));
  for (const status of Object.values(logoStatuses)) {
    expect(status).toBe(200);
  }

  // The full DOM grep openclaw-channel-copy.test.ts cannot do without a
  // headless-DOM harness (see its header): the transport's name must not be
  // on the rendered screen, grid or opened panel.
  const gridText = (await page.locator(".agent-configure-content").innerText()).toLowerCase();
  expect(gridText).not.toContain("openclaw");
  await page.locator(".fleet-channel-card", { hasText: "Feishu" }).first().click();
  await page.waitForTimeout(400);
  const panelText = (await page.locator(".fleet-channel-banner").innerText()).toLowerCase();
  expect(panelText).not.toContain("openclaw");
  // NO MECHANISM IN THE COPY. The install state used to read "The channel's
  // plugin is not on this computer yet." above a button labelled "Install
  // plugin" — a fact about a package on a disk, handed to the customer as
  // something to act on. Nothing on this screen names one.
  expect(panelText).not.toContain("plugin");
  expect(gridText).not.toContain("plugin");
});
