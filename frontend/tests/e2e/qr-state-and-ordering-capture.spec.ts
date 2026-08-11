/**
 * Visual + behavioural proof for the three things fix/channel-qr-state-and-ordering
 * delivers. Run by hand against a throwaway stack brought up with
 * frontend/scripts/start-e2e-backend.sh, with two seeded agents ("Gateway
 * Agent", paired to a registered gateway; "Cloud Only Agent", not).
 *
 *   1. THE QR PANEL AGREES WITH ITSELF. The founder's bug was a spinner drawn
 *      above a button reading "Generate QR code" — the screen telling him to
 *      start what it had already started. Asserted as the invariant, on the
 *      real DOM, at every step; and the DISCONNECT -> REOPEN path is walked end
 *      to end, because the auto-start is the thing this fix could most easily
 *      have broken.
 *   2. THE GRID LEADS WITH THE MESSENGERS PEOPLE USE and ends with the ones
 *      they have not heard of.
 *   3. A CARD WITH MORE THAN ONE WAY IN SAYS SO, on its face, without a second
 *      pill competing with the status one.
 *
 * THE DEVICE IS STUBBED; THE PANEL IS NOT.
 * ----------------------------------------
 * A throwaway stack cannot produce a real WhatsApp runtime on a real box — a
 * registered-but-absent gateway reports `connecting` forever, and the QR states
 * are then unreachable on any screen. So the two personal-channel endpoints are
 * scripted (the same technique channels-card-grid-capture.spec.ts uses for
 * `observed`) and NOTHING ELSE IS: the panel, its auto-start, its 2s polling,
 * its QR rendering and every pixel are the real thing. The scripted device is
 * what makes the interesting sequence — accept, then a code 2.5s later, exactly
 * the gap the bug lived in — happen on demand instead of never.
 *
 * Usage:
 *   PLAYWRIGHT_DISABLE_WEBSERVER=1 PLAYWRIGHT_FRONTEND_PORT=3011 \
 *   PLAYWRIGHT_BACKEND_PORT=8011 \
 *   npx playwright test tests/e2e/qr-state-and-ordering-capture.spec.ts
 */
import { test, expect } from "@playwright/test";

const WORKSPACE_ID = "ws-1";
const OUT = "test-results/qr-state-and-ordering";

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
  await page.goto(page.url().replace(/\/[^/]+$/, "/channels"), { waitUntil: "networkidle" });
  await page.waitForTimeout(1800);
  await expect(page.locator(".fleet-channel-grid")).toBeVisible();
}

/** A device that behaves the way a real one does on the axis that matters:
 *  /setup returns 200 the instant it ACCEPTS, and the code shows up later on a
 *  status poll. That gap is where the contradictory frame lived.
 *
 *  `answers: false` models the other real device — one that accepts and then
 *  produces nothing, which is what leaves the runtime sitting in a RESTING
 *  status (`disconnected`) with our request already accepted. That is the exact
 *  founder's-screen combination, and it is also why the panel's own bounded
 *  wait matters: `usePersonalChannelStatus` only polls while the status is
 *  ACTIVE, so a box that never leaves `disconnected` will never be asked again
 *  and the panel would otherwise spin on its own forever. */
function scriptDevice(page: any) {
  const state = {
    setupCalls: 0,
    phase: "disconnected" as "disconnected" | "connecting" | "qr",
    codeDelayMs: 2500,
    answers: true,
  };

  // NOTE the path: the status view and the setup call both live under
  // `/personal-channels/whatsapp/...` (channelPath() maps the `whatsapp_personal`
  // channel key to `whatsapp`), and the status view is the BARE gateway path —
  // `/setup` and `/disconnect` are siblings of it, not of a `.../channels` list.
  page.route("**/personal-channels/whatsapp/gateways/**", async (route: any) => {
    const request = route.request();
    const path = new URL(request.url()).pathname;

    if (request.method() === "POST" && path.endsWith("/setup")) {
      state.setupCalls += 1;
      // 200 means ACCEPTED, and says nothing about a code — this is the exact
      // response that used to end `busy` while no QR existed yet.
      await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ ok: true }) });
      if (!state.answers) return; // accepted, and never heard from again
      state.phase = "connecting";
      const at = state.setupCalls;
      setTimeout(() => {
        if (state.setupCalls === at && state.phase === "connecting") state.phase = "qr";
      }, state.codeDelayMs);
      return;
    }
    if (request.method() === "POST" && path.endsWith("/disconnect")) {
      state.phase = "disconnected";
      await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ ok: true }) });
      return;
    }
    // The polled status view.
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        gateway_id: "stub",
        channel_key: "whatsapp_personal",
        state: {
          status: state.phase === "qr" ? "qr_required" : state.phase === "connecting" ? "connecting" : "disconnected",
          qr_code: state.phase === "qr" ? "2@seededqrpayloadforverification,abc123==" : null,
          metadata: {},
        },
      }),
    });
  });

  return state;
}

/** Every element the QR panel can show, counted off the LIVE DOM. Short
 *  explicit timeouts throughout: this config sets no `actionTimeout`, so a
 *  bare `innerText()` on an element that is legitimately absent would wait
 *  forever rather than report the absence this function exists to record. */
/** ONE `evaluate`, not six locator round-trips.
 *
 *  This is the whole point of the check: the invariant is that the panel's
 *  elements agree WITH EACH OTHER AT AN INSTANT. Reading them one Playwright
 *  call at a time samples a live, 2s-polling DOM at six different instants, so
 *  a perfectly coherent panel that changes state mid-read reports an
 *  incoherent snapshot — the check would be manufacturing the very
 *  contradiction it exists to detect. (It did: a run caught the "ready" hint
 *  beside a QR count of 0, purely because the image landed between two
 *  round-trips.) A single synchronous pass over the DOM cannot straddle a
 *  render.
 *
 *  The two spinner shapes are counted together — the QR frame's, and the
 *  panel's own "Connecting…" step, which is a separate branch of
 *  PersonalChannelConnectPanel. To a customer they are one fact ("something is
 *  happening"), and the invariant is about that fact, not about which element
 *  carries it. */
async function readPanel(page: any, moment: string) {
  const view = await page.evaluate(() => {
    const panel = document.querySelector(".fleet-channel-banner");
    const q = (sel: string) => (panel ? panel.querySelectorAll(sel).length : 0);
    const buttons = panel ? Array.from(panel.querySelectorAll("button.fleet-btn")) : [];
    const text = (el: Element) => (el.textContent || "").trim();
    return {
      panel: panel ? 1 : 0,
      spinner: q(".pc-connect-qr--pending") + q(".pc-connect-step--waiting"),
      generate: buttons.filter((b) => /generate qr code/i.test(text(b))).length,
      retry: buttons.filter((b) => /^try again$/i.test(text(b))).length,
      qr: q("img.pc-connect-qr"),
      failure: q(".fleet-channel-expand-error"),
      hint: panel ? text(panel.querySelector(".fleet-channel-expand-hint") || document.createElement("i")) : "",
    };
  });
  const snapshot = { ...view, moment };
  console.log(`[${moment}] ${JSON.stringify(snapshot)}`);
  return snapshot;
}

/** THE INVARIANT, read off the live DOM rather than off the module that
 *  decides it. Both directions — the bug is symmetric. */
function assertCoherent(v: Awaited<ReturnType<typeof readPanel>>) {
  const m = v.moment;
  expect(v.spinner + v.generate, `${m}: a spinner and "Generate QR code" must never coexist`).toBeLessThan(2);
  expect(v.spinner + v.retry, `${m}: a spinner and "Try again" must never coexist`).toBeLessThan(2);
  expect(v.qr + v.spinner, `${m}: a QR and a spinner are the same slot`).toBeLessThan(2);
  expect(v.generate + v.retry, `${m}: at most one control, and it says which thing it does`).toBeLessThan(2);
  // The scan instruction is only correct once there is something to scan.
  if (!v.qr) expect(v.hint.endsWith("scan:"), `${m}: nothing to scan, so it does not say "scan:"`).toBeFalsy();
  // A resting panel that is not showing a code offers exactly one way forward;
  // a working one offers none, because it is already doing the work.
  if (v.spinner) expect(v.generate + v.retry, `${m}: a working panel asks for nothing`).toBe(0);
  // A failure always says what happened — never a stopped spinner with no words.
  if (v.retry) expect(v.failure, `${m}: a retry control comes with an explanation`).toBeGreaterThan(0);
}

async function openWhatsAppPanel(page: any) {
  await page.locator(".fleet-channel-card", { hasText: "WhatsApp" }).first().click();
  await page.waitForTimeout(350);
  await expect(page.locator(".fleet-channel-banner")).toBeVisible();
}

/* ------------------------------------------------------------------ 1. QR */

for (const theme of ["light", "dark"] as const) {
  test(`WhatsApp: disconnect -> reopen reaches a QR, and never contradicts itself — ${theme}`, async ({ page }) => {
    test.setTimeout(180_000);
    await page.setViewportSize({ width: 1440, height: 1400 });
    await authenticateOwner(page);
    const device = scriptDevice(page);
    await page.goto(`/w/${WORKSPACE_ID}/agents`, { waitUntil: "domcontentloaded" });
    await setTheme(page, theme);
    await openChannelsTab(page, "Gateway Agent");
    await openWhatsAppPanel(page);

    // FIRST OPEN. The panel auto-starts, so nothing ever asks the customer to.
    const opened = await readPanel(page, `${theme} first open`);
    assertCoherent(opened);
    expect(opened.generate, "the panel starts itself; it does not ask").toBe(0);
    expect(device.setupCalls, "opening an idle channel fires the setup call").toBeGreaterThan(0);
    await page.screenshot({ path: `${OUT}-qr-1-autostart-${theme}.png` });

    // THE WHOLE ACCEPT -> CODE WINDOW. This is the band the bug lived in: our
    // fetch resolved in ~100ms and the code took seconds, so the old panel spun
    // above a "Generate QR code" button for the entire gap. Sampled densely
    // because a single screenshot would step over it.
    for (let i = 0; i < 10; i += 1) {
      assertCoherent(await readPanel(page, `${theme} +${i * 300}ms`));
      await page.waitForTimeout(300);
    }

    // ...and it reaches a QR.
    await expect(page.locator("img.pc-connect-qr")).toBeVisible({ timeout: 25_000 });
    const ready = await readPanel(page, `${theme} qr ready`);
    assertCoherent(ready);
    expect(ready.qr).toBe(1);
    expect(ready.generate + ready.retry, "a rendered QR needs no control at all").toBe(0);
    expect(ready.spinner, "and no spinner beside it").toBe(0);
    expect(ready.hint.endsWith("scan:"), "the scan instruction arrives with the thing to scan").toBeTruthy();
    await page.screenshot({ path: `${OUT}-qr-2-ready-${theme}.png` });

    // THE REGRESSION PATH. Close the panel, drop the runtime back to idle (what
    // disconnect() does), reopen. A QR must appear again with no restart and no
    // click — that gap is exactly why the auto-start exists and must survive.
    const before = device.setupCalls;
    await page.locator(".fleet-detail-backdrop .fleet-detail-close").first().click();
    await page.waitForTimeout(400);
    device.phase = "disconnected";
    await page.waitForTimeout(1500);
    await openWhatsAppPanel(page);

    const reopened = await readPanel(page, `${theme} reopened after disconnect`);
    assertCoherent(reopened);
    expect(reopened.generate, "reopening auto-starts too — it does not sit idle behind a button").toBe(0);
    expect(device.setupCalls, "reopening after a disconnect fires a FRESH setup call").toBeGreaterThan(before);
    await page.screenshot({ path: `${OUT}-qr-3-reautostart-${theme}.png` });

    await expect(page.locator("img.pc-connect-qr")).toBeVisible({ timeout: 25_000 });
    const readyAgain = await readPanel(page, `${theme} qr ready again`);
    assertCoherent(readyAgain);
    expect(readyAgain.qr, "disconnect -> reopen reaches a QR").toBe(1);
    await page.screenshot({ path: `${OUT}-qr-4-ready-again-${theme}.png` });

    // The 900px breakpoint, on the same live panel.
    await page.setViewportSize({ width: 880, height: 1400 });
    await page.waitForTimeout(700);
    assertCoherent(await readPanel(page, `${theme} narrow`));
    await page.screenshot({ path: `${OUT}-qr-5-narrow-${theme}.png` });
  });
}

/** A BOX THAT ACCEPTS AND THEN SAYS NOTHING must not leave a spinner running
 *  forever. The wait is bounded (QR_WAIT_MS), and what is on the other side of
 *  it is a control and an explanation, never a stopped spinner. */
test("a box that never answers ends in a control and an explanation", async ({ page }) => {
  test.setTimeout(180_000);
  await page.setViewportSize({ width: 1440, height: 1400 });
  await authenticateOwner(page);
  const device = scriptDevice(page);
  device.answers = false; // accepts the request, then never says another word
  await page.goto(`/w/${WORKSPACE_ID}/agents`, { waitUntil: "domcontentloaded" });
  await setTheme(page, "light");
  await openChannelsTab(page, "Gateway Agent");
  await openWhatsAppPanel(page);

  const working = await readPanel(page, "accepted, waiting");
  assertCoherent(working);
  expect(working.spinner, "an accepted request spins").toBe(1);
  expect(working.generate + working.retry, "...and offers nothing to start what is running").toBe(0);
  await page.screenshot({ path: `${OUT}-qr-wait-1-spinning-light.png` });

  // QR_WAIT_MS is 30s; sample across the boundary.
  for (let i = 0; i < 12; i += 1) {
    assertCoherent(await readPanel(page, `waiting +${i * 3}s`));
    await page.waitForTimeout(3000);
  }
  const timedOut = await readPanel(page, "wait ran out");
  assertCoherent(timedOut);
  expect(timedOut.spinner, "the spinner stops — a wait with no end is a hang").toBe(0);
  expect(timedOut.retry, "and a retry control comes back").toBe(1);
  expect(timedOut.failure, "with words saying what happened").toBeGreaterThan(0);
  await page.screenshot({ path: `${OUT}-qr-wait-2-failed-light.png` });
});

/* --------------------------------------------------------------- 2. ORDER */

for (const theme of ["light", "dark"] as const) {
  test(`the grid is ordered by real-world use — ${theme}`, async ({ page }) => {
    test.setTimeout(120_000);
    await page.setViewportSize({ width: 1440, height: 1700 });
    await authenticateOwner(page);
    await page.goto(`/w/${WORKSPACE_ID}/agents`, { waitUntil: "domcontentloaded" });
    await setTheme(page, theme);
    await openChannelsTab(page, "Gateway Agent");

    const labels = (await page.locator(".fleet-channel-card-label").allInnerTexts()).map((t) => t.trim());
    console.log(`[grid ${theme}] ${labels.length} cards: ${labels.join(" | ")}`);
    expect(labels.length).toBeGreaterThan(20);

    // The founder's own examples, off the live screen: alphabetical put
    // ClickClack above Discord and Nostr above WhatsApp.
    const at = (name: string) => labels.findIndex((l) => l === name);
    for (const [popular, obscure] of [
      ["WhatsApp", "Nostr"],
      ["Discord", "ClickClack"],
      ["Telegram", "Tlon"],
    ] as const) {
      if (at(popular) >= 0 && at(obscure) >= 0) {
        expect(at(popular), `${popular} must lead ${obscure}`).toBeLessThan(at(obscure));
      }
    }
    expect(labels[0], "the grid opens with the messenger most people use").toBe("WhatsApp");

    await page.screenshot({ path: `${OUT}-grid-top-${theme}.png` });
    await page.evaluate(() => {
      let node: HTMLElement | null = document.querySelector(".fleet-channel-grid");
      while (node) {
        if (node.scrollHeight > node.clientHeight + 8) {
          node.scrollTop = node.scrollHeight;
          return;
        }
        node = node.parentElement;
      }
    });
    await page.waitForTimeout(500);
    await page.screenshot({ path: `${OUT}-grid-bottom-${theme}.png` });

    await page.setViewportSize({ width: 880, height: 1400 });
    await page.waitForTimeout(700);
    await page.screenshot({ path: `${OUT}-grid-narrow-${theme}.png` });
  });
}

/** The no-gateway agent: the same ordering rule over the first-party seven. */
test("the cloud-only grid is ordered by the same rule", async ({ page }) => {
  test.setTimeout(120_000);
  await page.setViewportSize({ width: 1440, height: 1200 });
  await authenticateOwner(page);
  await page.goto(`/w/${WORKSPACE_ID}/agents`, { waitUntil: "domcontentloaded" });
  await setTheme(page, "light");
  await openChannelsTab(page, "Cloud Only Agent");

  const labels = (await page.locator(".fleet-channel-card-label").allInnerTexts()).map((t) => t.trim());
  console.log(`[cloud-only grid] ${labels.length} cards: ${labels.join(" | ")}`);
  expect(labels.length).toBe(7);
  expect(labels[0]).toBe("WhatsApp");
  expect(labels.indexOf("Telegram")).toBeLessThan(labels.indexOf("Slack"));
  await page.screenshot({ path: `${OUT}-grid-cloudonly-light.png` });
});

/* ------------------------------------------------------- 3. THE SIGNAL */

for (const theme of ["light", "dark"] as const) {
  test(`a multi-door card carries one small secondary signal — ${theme}`, async ({ page }) => {
    test.setTimeout(120_000);
    await page.setViewportSize({ width: 1440, height: 1700 });
    await authenticateOwner(page);
    await page.goto(`/w/${WORKSPACE_ID}/agents`, { waitUntil: "domcontentloaded" });
    await setTheme(page, theme);
    await openChannelsTab(page, "Gateway Agent");

    const cards = page.locator(".fleet-channel-card");
    const total = await cards.count();
    const withNote: string[] = [];
    for (let i = 0; i < total; i += 1) {
      const card = cards.nth(i);
      if ((await card.locator(".fleet-channel-card-ways").count()) > 0) {
        withNote.push((await card.locator(".fleet-channel-card-label").innerText()).trim());
      }
      // ONE PILL PER FACE. The secondary signal must not have become a second
      // competing chip — that is the card-face discipline the founder approved.
      expect(await card.locator(".fleet-channel-card-pill").count(), "one status pill per card face").toBe(1);
    }
    console.log(`[ways ${theme}] ${withNote.length}/${total} cards carry a choice note: ${withNote.join(" | ")}`);
    expect(withNote.length, "some cards genuinely offer a choice").toBeGreaterThan(0);
    expect(withNote.length, "...and most do not — a note on every card is furniture").toBeLessThan(total / 2);

    const noted = page.locator(".fleet-channel-card", { has: page.locator(".fleet-channel-card-ways") }).first();
    const noteText = (await noted.locator(".fleet-channel-card-ways").innerText()).trim();
    expect(noteText).toMatch(/^\d+ ways to connect$/);
    expect(noteText).not.toMatch(/openclaw|plugin|npm|package|binary/i);

    // A footnote, not a chip: smaller than the pill above it and unbacked.
    const note = await noted.locator(".fleet-channel-card-ways").evaluate((el: Element) => {
      const s = getComputedStyle(el as HTMLElement);
      return { fontSize: parseFloat(s.fontSize), background: s.backgroundColor, color: s.color };
    });
    const pill = await noted.locator(".fleet-channel-card-pill").evaluate((el: Element) => {
      const s = getComputedStyle(el as HTMLElement);
      return { fontSize: parseFloat(s.fontSize), color: s.color };
    });
    console.log(`[ways ${theme}] note ${JSON.stringify(note)} vs pill ${JSON.stringify(pill)}`);
    expect(note.fontSize).toBeLessThan(pill.fontSize);
    expect(note.background, "the note carries no chip background").toMatch(/rgba\(0, 0, 0, 0\)|transparent/);

    // ...and the face's count is the panel's count.
    await noted.click();
    await page.waitForTimeout(500);
    await expect(page.locator(".fleet-channel-banner")).toBeVisible();
    const doorCount = await page.locator(".fleet-channel-banner .fleet-wizard-option").count();
    console.log(`[ways ${theme}] the noted card opened ${doorCount} doors; its face said ${JSON.stringify(noteText)}`);
    expect(String(doorCount), "the face and the panel behind it cannot disagree").toBe(noteText.split(" ")[0]);
    await page.screenshot({ path: `${OUT}-ways-panel-${theme}.png` });
  });
}
