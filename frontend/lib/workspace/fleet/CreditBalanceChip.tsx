"use client";

import Link from "next/link";
import { Coins } from "lucide-react";

import { useCreditBalance } from "./credit-balance";

/**
 * Persistent, always-visible credit-balance indicator — lives in the one
 * persistent rail this app has (PrimaryRail; there is no separate
 * right-side rail in the current shell — see FleetShell.tsx). Present on
 * every workspace page, not just Billing, per the owner's ask. Clicking it
 * routes to the Billing page, where the full balance/top-up/usage-history
 * surface lives. Deliberately neutral (no accent color) — the accent is
 * reserved for the top-up action button itself, not this glance indicator.
 */
export function CreditBalanceChip({
  workspaceId,
  collapsed,
}: {
  workspaceId: string;
  collapsed: boolean;
}) {
  const { balance, loading, error } = useCreditBalance(workspaceId);

  // Fail silent, not broken: a balance fetch problem must never make the
  // persistent rail look broken — just omit the chip until it recovers.
  if (error) return null;

  const credits = balance?.credit_balance_credits ?? null;
  const label = loading || credits === null ? "…" : credits.toLocaleString("en-US");

  return (
    <Link
      href={`/w/${encodeURIComponent(workspaceId)}/billing`}
      className="fleet-rail-credit-chip"
      title={
        credits === null
          ? "Credit balance"
          : `${credits.toLocaleString("en-US")} credits — open Billing`
      }
    >
      <Coins size={14} strokeWidth={1.75} />
      {!collapsed && (
        <span className="fleet-rail-credit-chip-label">
          {label}
          <span className="fleet-rail-credit-chip-unit"> credits</span>
        </span>
      )}
    </Link>
  );
}
