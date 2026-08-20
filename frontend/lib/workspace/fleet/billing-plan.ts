"use client";

import { fleetAuthorizedFetch } from "@/lib/workspace/fleet/fleet-authorized-fetch";

import { useCallback, useEffect, useState } from "react";

import { buildCookieAuthHeaders } from "@/lib/auth/csrf";

/**
 * Plan/checkout/portal data access for the Billing page — the "Upgrade to
 * Pro" and "Manage subscription" controls. Sibling to credit-balance.ts
 * (same fleetAuthorizedFetch + plain-fetch convention every other
 * fleet-data hook uses), kept as its own file because plan checkout and
 * credit top-up are different concerns on the backend
 * (billing_service.create_workspace_checkout_session /
 * create_workspace_portal_session vs create_credit_purchase_checkout_session)
 * even though both end up on the same Polar mechanism.
 *
 * GET /api/billing/summary (routes_billing.py) is the source for
 * `subscription.is_paid` / `display_label` — the outcome-honesty fields
 * that make a workspace's real, unpaid entitlement grant read as "Free"
 * instead of the internal "Pro" tier label those entitlements are keyed
 * off (2026-08-20; see billing_service.py's own comment on
 * is_paid_subscription for why the two must never be the same field).
 */

export type BillingPlanCatalogItem = {
  plan_id: string;
  label?: string;
  checkout_enabled?: boolean;
  price_configured?: boolean;
  current?: boolean;
};

export type BillingSubscriptionSummary = {
  plan_id?: string;
  effective_plan_id?: string;
  label?: string;
  is_paid?: boolean;
  display_plan_id?: string;
  display_label?: string;
  status?: string;
};

export type BillingSummary = {
  ok?: boolean;
  provider?: string;
  configured?: boolean;
  portal_available?: boolean;
  subscription?: BillingSubscriptionSummary;
  plans?: BillingPlanCatalogItem[];
};

/** The honest plan label — "Free" for an unpaid entitlement grant, never
 *  the internal tier name (see module header). Falls back to the older
 *  `label` field only for a payload that predates the honesty fields, so a
 *  transitional/cached response never renders blank. */
export function resolvePlanDisplayLabel(subscription: BillingSubscriptionSummary | null | undefined): string {
  if (!subscription) return "Free";
  if (typeof subscription.display_label === "string" && subscription.display_label.trim()) {
    return subscription.display_label;
  }
  if (subscription.is_paid && typeof subscription.label === "string" && subscription.label.trim()) {
    return subscription.label;
  }
  return "Free";
}

export type PlanUpgradeControlPlan =
  | { kind: "none" }
  | { kind: "upgrade"; planId: string }
  | { kind: "manage" };

/** What the Billing page's plan control should be, off nothing but the
 *  billing summary — a paid workspace with a portal gets "manage", an
 *  unpaid workspace with Pro's checkout configured gets "upgrade", and
 *  everything else (not configured yet, or already offered nothing to
 *  upgrade to) renders no control at all rather than a dead one. */
export function planUpgradeControl(summary: BillingSummary | null | undefined): PlanUpgradeControlPlan {
  if (!summary) return { kind: "none" };
  const isPaid = Boolean(summary.subscription?.is_paid);
  if (isPaid) {
    return summary.portal_available ? { kind: "manage" } : { kind: "none" };
  }
  const proPlan = (summary.plans || []).find((plan) => plan.plan_id === "pro");
  if (proPlan?.checkout_enabled) {
    return { kind: "upgrade", planId: "pro" };
  }
  return { kind: "none" };
}

export function useBillingSummary(workspaceId: string) {
  const [summary, setSummary] = useState<BillingSummary | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    if (!workspaceId) {
      setLoading(false);
      return;
    }
    setLoading(true);
    try {
      const res = await fleetAuthorizedFetch(
        `/api/billing/summary?workspace_id=${encodeURIComponent(workspaceId)}`,
        { credentials: "include" },
      );
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      setSummary(data);
      setError(null);
    } catch {
      setError("Could not load billing plan");
    } finally {
      setLoading(false);
    }
  }, [workspaceId]);

  useEffect(() => {
    refresh();
  }, [refresh]);

  return { summary, loading, error, refresh };
}

export type CheckoutResult =
  | { ok: true; checkoutUrl: string }
  | { ok: false; notConfigured: true }
  | { ok: false; notConfigured: false; message: string };

/** Starts a plan-upgrade checkout session. Same 503-means-"not configured"
 *  degradation as credit-balance.ts's startCreditTopUp — the platform
 *  owner's own Polar setup, not something to error loudly about. */
export async function startPlanCheckout(workspaceId: string, planId: string): Promise<CheckoutResult> {
  try {
    const res = await fleetAuthorizedFetch("/api/billing/checkout", {
      method: "POST",
      credentials: "include",
      headers: buildCookieAuthHeaders("POST", { "Content-Type": "application/json" }),
      body: JSON.stringify({ workspace_id: workspaceId, plan_id: planId }),
    });
    if (res.status === 503) {
      return { ok: false, notConfigured: true };
    }
    if (!res.ok) {
      let message = `Upgrade could not start (HTTP ${res.status}).`;
      try {
        const payload = await res.json();
        if (typeof payload?.detail === "string") message = payload.detail;
      } catch {
        // keep default message
      }
      return { ok: false, notConfigured: false, message };
    }
    const data = await res.json();
    const checkoutUrl = typeof data?.checkout_url === "string" ? data.checkout_url : "";
    if (!checkoutUrl) {
      return { ok: false, notConfigured: false, message: "Upgrade session did not return a checkout link." };
    }
    return { ok: true, checkoutUrl };
  } catch {
    return { ok: false, notConfigured: false, message: "Upgrade could not start — check your connection." };
  }
}

export type PortalResult =
  | { ok: true; portalUrl: string }
  | { ok: false; notConfigured: true }
  | { ok: false; notConfigured: false; message: string };

export async function startPortalSession(workspaceId: string): Promise<PortalResult> {
  try {
    const res = await fleetAuthorizedFetch("/api/billing/portal", {
      method: "POST",
      credentials: "include",
      headers: buildCookieAuthHeaders("POST", { "Content-Type": "application/json" }),
      body: JSON.stringify({ workspace_id: workspaceId }),
    });
    if (res.status === 503) {
      return { ok: false, notConfigured: true };
    }
    if (!res.ok) {
      let message = `Couldn't open subscription management (HTTP ${res.status}).`;
      try {
        const payload = await res.json();
        if (typeof payload?.detail === "string") message = payload.detail;
      } catch {
        // keep default message
      }
      return { ok: false, notConfigured: false, message };
    }
    const data = await res.json();
    const portalUrl = typeof data?.portal_url === "string" ? data.portal_url : "";
    if (!portalUrl) {
      return { ok: false, notConfigured: false, message: "Portal session did not return a usable link." };
    }
    return { ok: true, portalUrl };
  } catch {
    return { ok: false, notConfigured: false, message: "Couldn't open subscription management — check your connection." };
  }
}
