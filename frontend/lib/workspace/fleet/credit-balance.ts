"use client";

import { fleetAuthorizedFetch } from "@/lib/workspace/fleet/fleet-authorized-fetch";

import { useCallback, useEffect, useState } from "react";

import { buildCookieAuthHeaders } from "@/lib/auth/csrf";

/**
 * Credit balance + usage-history data access for the persistent rail chip
 * (PrimaryRail) and the Billing page. Hits the same backend routes the
 * (currently uncalled) workstation-client.ts bindings target
 * (routes_billing.py: GET /api/billing/credits/balance,
 * GET /api/billing/credits/usage-history, POST /api/billing/credits/purchase)
 * via the app's plain-fetch + /api/[...path] proxy convention every other
 * fleet-data hook already uses — see fleet-data.ts's useFleetWorkspace for
 * the same shape.
 */

export type CreditBalance = {
  ok: boolean;
  workspace_id: string;
  credit_balance_usd: number;
  credit_balance_credits: number;
  transactions: Array<Record<string, unknown>>;
};

export type CreditUsageHistoryItem = {
  id: string;
  kind: string;
  source: string;
  label: string;
  credits: number;
  amount_usd: number;
  created_at: string | null;
};

export type CreditUsageHistory = {
  ok: boolean;
  plan?: Record<string, unknown>;
  hosted_sage_ai?: Record<string, unknown>;
  items: CreditUsageHistoryItem[];
};

export function useCreditBalance(workspaceId: string) {
  const [balance, setBalance] = useState<CreditBalance | null>(null);
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
        `/api/billing/credits/balance?workspace_id=${encodeURIComponent(workspaceId)}`,
        { credentials: "include" },
      );
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      setBalance(data);
      setError(null);
    } catch {
      setError("Could not load credit balance");
    } finally {
      setLoading(false);
    }
  }, [workspaceId]);

  useEffect(() => {
    refresh();
  }, [refresh]);

  return { balance, loading, error, refresh };
}

export function useCreditUsageHistory(workspaceId: string, limit = 100) {
  const [history, setHistory] = useState<CreditUsageHistory | null>(null);
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
        `/api/billing/credits/usage-history?workspace_id=${encodeURIComponent(workspaceId)}&limit=${limit}`,
        { credentials: "include" },
      );
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      setHistory(data);
      setError(null);
    } catch {
      setError("Could not load usage history");
    } finally {
      setLoading(false);
    }
  }, [workspaceId, limit]);

  useEffect(() => {
    refresh();
  }, [refresh]);

  return { history, loading, error, refresh };
}

export type DailyCreditSpend = { date: string; credits: number };

/** UTC day keys for the last `days` days, oldest→newest — the same window
 *  CreditsPanel charts (14d), derived here so the toolbar's Usage popover and
 *  the Billing page can't drift apart on what "recent" means. */
export function lastCreditDays(days: number): string[] {
  const out: string[] = [];
  const now = new Date();
  for (let i = days - 1; i >= 0; i--) {
    const d = new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), now.getUTCDate() - i));
    out.push(d.toISOString().slice(0, 10));
  }
  return out;
}

export function creditDayLabel(dayKey: string): string {
  const d = new Date(`${dayKey}T00:00:00Z`);
  if (Number.isNaN(d.getTime())) return dayKey;
  return d.toLocaleDateString("en-US", { month: "short", day: "numeric", timeZone: "UTC" });
}

/** Per-day debited credits over the last `days` days, oldest→newest. Every day
 *  in the window is present (zero-filled) so a chart can render a real
 *  calendar rather than only the days that happened to have activity. Only
 *  `usage_debit` rows count — top-ups and grants are not spend. */
export function dailyCreditSpend(history: CreditUsageHistory | null, days: number): DailyCreditSpend[] {
  const byDate = new Map<string, number>();
  for (const item of history?.items || []) {
    if (item.kind !== "usage_debit") continue;
    const key = (item.created_at || "").slice(0, 10);
    if (!key) continue;
    byDate.set(key, (byDate.get(key) || 0) + Math.abs(item.credits || 0));
  }
  return lastCreditDays(days).map((date) => ({ date, credits: byDate.get(date) || 0 }));
}

export type TopUpResult =
  | { ok: true; checkoutUrl: string }
  | { ok: false; notConfigured: true }
  | { ok: false; notConfigured: false; message: string };

/**
 * Starts a credit top-up checkout session. Degrades gracefully when Stripe
 * isn't configured server-side (billing_service._stripe_configured() ==
 * False) instead of surfacing a raw error — the payment rails themselves
 * are the platform owner's own secure setup step, same as any other OAuth/
 * API-key secret in this app (see billing_credit_config.py's module docs
 * and this file's Billing page caller).
 */
export async function startCreditTopUp(workspaceId: string, amountUsd: number): Promise<TopUpResult> {
  try {
    const res = await fleetAuthorizedFetch("/api/billing/credits/purchase", {
      method: "POST",
      credentials: "include",
      headers: buildCookieAuthHeaders("POST", { "Content-Type": "application/json" }),
      body: JSON.stringify({ workspace_id: workspaceId, amount_usd: amountUsd }),
    });
    if (res.status === 503) {
      return { ok: false, notConfigured: true };
    }
    if (!res.ok) {
      let message = `Top-up could not start (HTTP ${res.status}).`;
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
      return { ok: false, notConfigured: false, message: "Top-up session did not return a checkout link." };
    }
    return { ok: true, checkoutUrl };
  } catch {
    return { ok: false, notConfigured: false, message: "Top-up could not start — check your connection." };
  }
}
