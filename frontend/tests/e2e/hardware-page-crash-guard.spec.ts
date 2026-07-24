/**
 * Regression test for a live production crash: the Hardware page
 * (app/(account)/w/[workspaceId]/hardware/page.tsx) rendered fine for a
 * fresh account with zero gateways, but hit Next's route error boundary
 * ("Something went wrong — This page hit an unexpected error and couldn't
 * finish loading", app/(account)/w/[workspaceId]/error.tsx) for a real
 * account with real /api/gateway/registrations rows.
 *
 * Root cause: cloudProviderLabel() and regionOnlyLabel() in page.tsx used
 * the `(value || "").trim()` pattern, which only substitutes a fallback for
 * FALSY input. A field that is present but the WRONG TYPE (a number, an
 * object — not null/undefined, just not a string) sails straight through
 * `|| ""` unchanged and then throws on `.trim()` (numbers/objects have no
 * such method), which is exactly the "some field is
 * null/undefined/UNEXPECTED" failure mode reported. Every other helper in
 * this file (isDarwinLocal, localDeviceLocationText) and gateway-box-picker.
 * tsx already used the safe `String(value || "")` idiom — these two were the
 * inconsistent ones. This test seeds a gateway row where hardware_provider
 * and hardware_region are numbers instead of strings (the wrong-type shape,
 * not just a missing one) and asserts the page still renders instead of
 * falling into the error boundary.
 */
import { test, expect } from "@playwright/test";

const WORKSPACE_ID = "ws-1";

async function auth(page: any) {
  await page.context().clearCookies();
  const res = await page.request.post("/api/auth/login", {
    data: { email: "owner@example.com", password: "password-123", channel: "web" },
  });
  expect(res.ok()).toBeTruthy();
}

test("hardware page — wrong-type hardware_provider/hardware_region does not crash the route", async ({ page }) => {
  await auth(page);

  const malformedRegistrations = {
    workspace_id: WORKSPACE_ID,
    count: 1,
    items: [
      {
        gateway_id: "gateway_crash_guard_test",
        device_id: "device_crash_guard_test",
        tenant_id: "tenant-1",
        workspace_id: WORKSPACE_ID,
        user_id: "user-1",
        status: "active",
        device_trust_state: "verified",
        display_name: "Odd VPS",
        platform: "linux-x64",
        metadata: {},
        capabilities: [],
        llm_runtimes: {},
        journal_cursor: 0,
        checkpoint_cursor: 0,
        created_at: "2026-07-20T17:09:21.484952Z",
        updated_at: "2026-07-20T17:19:38.544063Z",
        last_seen_at: "2026-07-20T17:19:38.544063Z",
        last_heartbeat_at: "2026-07-20T17:19:38.544063Z",
        token_rotated_at: null,
        revoked_at: null,
        revoked_reason: null,
        connection_status: "offline",
        heartbeat_fresh: false,
        heartbeat_age_seconds: 300000,
        hardware_kind: "cloud_vps",
        hardware_label: "DigitalOcean · New York 3",
        // The exact wrong-type shapes: present, non-null, but NOT strings.
        hardware_provider: 12345,
        hardware_region: 67890,
        gateway_version: "0.1.0",
        latest_gateway_version: null,
        gateway_update_available: false,
        latest_gateway_artifact_url: null,
      },
    ],
  };

  await page.route("**/api/gateway/registrations*", (route: any) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(malformedRegistrations) }),
  );
  await page.route(`**/api/w/${WORKSPACE_ID}/fleet/agents*`, (route: any) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ ok: true, agents: [] }) }),
  );

  const pageErrors: string[] = [];
  page.on("pageerror", (err: Error) => pageErrors.push(err.message));

  await page.goto(`/w/${WORKSPACE_ID}/hardware`, { waitUntil: "networkidle" });
  await page.waitForTimeout(500);

  // The regression: this must NOT be the route error boundary.
  await expect(page.locator(".fleet-page-state-title", { hasText: "Something went wrong" })).toHaveCount(0);
  // The malformed row must still render (proves the guard degrades
  // gracefully — a wrong-type provider/region falls back to a label
  // instead of taking the row, or the page, down).
  await expect(page.locator(".fleet-list-row-title", { hasText: "Odd VPS" })).toBeVisible();
  expect(pageErrors, `Uncaught page errors: ${pageErrors.join("; ")}`).toHaveLength(0);
});
