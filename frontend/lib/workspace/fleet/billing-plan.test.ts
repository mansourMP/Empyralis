import assert from "node:assert/strict";
import { test } from "node:test";

import { planUpgradeControl, resolvePlanDisplayLabel, type BillingSummary } from "./billing-plan";

test("resolvePlanDisplayLabel reads 'Free' for a null/missing subscription", () => {
  assert.equal(resolvePlanDisplayLabel(null), "Free");
  assert.equal(resolvePlanDisplayLabel(undefined), "Free");
  assert.equal(resolvePlanDisplayLabel({}), "Free");
});

test("resolvePlanDisplayLabel reads 'Free' for an unpaid entitlement grant even when the internal label is Pro", () => {
  // The exact shape every fresh workspace's billing summary carries before
  // this fix: an entitlement tier of "pro" with no real payment behind it.
  assert.equal(
    resolvePlanDisplayLabel({
      effective_plan_id: "pro",
      label: "Pro",
      is_paid: false,
      display_plan_id: "free",
      display_label: "Free",
    }),
    "Free",
  );
});

test("resolvePlanDisplayLabel reads the real label once a workspace is actually paid", () => {
  assert.equal(
    resolvePlanDisplayLabel({
      effective_plan_id: "pro",
      label: "Pro",
      is_paid: true,
      display_plan_id: "pro",
      display_label: "Pro",
    }),
    "Pro",
  );
});

test("resolvePlanDisplayLabel falls back to the older `label` field only when the workspace is paid", () => {
  // A transitional payload that predates display_label but already carries
  // is_paid — must not silently show "Free" for a real paying customer.
  assert.equal(resolvePlanDisplayLabel({ label: "Pro", is_paid: true }), "Pro");
  // Same payload shape but unpaid must still resolve to "Free", not "Pro" —
  // this is the exact bug being fixed, so the unpaid branch must win even
  // without display_label present.
  assert.equal(resolvePlanDisplayLabel({ label: "Pro", is_paid: false }), "Free");
});

test("planUpgradeControl is none for a missing summary", () => {
  assert.deepEqual(planUpgradeControl(null), { kind: "none" });
  assert.deepEqual(planUpgradeControl(undefined), { kind: "none" });
});

test("planUpgradeControl offers upgrade when unpaid and Pro checkout is configured", () => {
  const summary: BillingSummary = {
    subscription: { is_paid: false },
    plans: [
      { plan_id: "free", checkout_enabled: false },
      { plan_id: "pilot", checkout_enabled: false },
      { plan_id: "pro", checkout_enabled: true },
    ],
  };
  assert.deepEqual(planUpgradeControl(summary), { kind: "upgrade", planId: "pro" });
});

test("planUpgradeControl renders no control when unpaid but Pro checkout isn't configured yet", () => {
  // The founder hasn't finished Polar product setup — a dead "Upgrade"
  // button that 503s is worse than no button at all.
  const summary: BillingSummary = {
    subscription: { is_paid: false },
    plans: [{ plan_id: "pro", checkout_enabled: false }],
  };
  assert.deepEqual(planUpgradeControl(summary), { kind: "none" });
});

test("planUpgradeControl offers manage when paid and a portal is available", () => {
  const summary: BillingSummary = {
    subscription: { is_paid: true },
    portal_available: true,
    plans: [{ plan_id: "pro", checkout_enabled: true, current: true }],
  };
  assert.deepEqual(planUpgradeControl(summary), { kind: "manage" });
});

test("planUpgradeControl renders no control when paid but no portal is available", () => {
  const summary: BillingSummary = {
    subscription: { is_paid: true },
    portal_available: false,
  };
  assert.deepEqual(planUpgradeControl(summary), { kind: "none" });
});
