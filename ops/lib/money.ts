/**
 * The one place a USD amount becomes text in this app. A standalone copy of
 * frontend/lib/ui/money.ts's `formatUsd` (same rule, same ~10 lines) rather
 * than a cross-app import -- see next.config.ts's header comment for why
 * this app never reaches across the repo boundary into frontend/.
 *
 * Two decimals, not four (founder's call, 2026-08-26: "the cost have to be
 * only two numbers after dot"). A single agent turn can genuinely cost a
 * fraction of a cent, so an amount below the display floor renders `<$0.01`
 * instead of a `$0.00` that states zero for money that was actually spent
 * ("two different facts may never share one signal", CLAUDE.md). Zero
 * itself stays `$0.00` -- that is a real, distinct fact: nothing was spent.
 */
const DISPLAY_FLOOR_USD = 0.005;

export function formatUsd(amount: number | null | undefined): string {
  const n = typeof amount === "number" && Number.isFinite(amount) ? amount : 0;
  if (n > 0 && n < DISPLAY_FLOOR_USD) return "<$0.01";
  return `$${n.toFixed(2)}`;
}

/** `credits_debited` is the platform's own ledger unit
 *  (agent_registry_models.CreditLedgerEvent.credits_debited), not USD --
 *  tier pricing (and therefore any credit-to-dollar rate) is explicitly not
 *  final (CLAUDE.md's open founder decisions). Formatting it through
 *  `formatUsd` would silently claim a fixed exchange rate this app has no
 *  authority to assert, so it gets its own plain-number formatter: grouped
 *  thousands, up to two decimals (the column is Numeric and can be
 *  fractional), no currency symbol. The caller supplies the "credits" unit
 *  label in its own copy, same as formatCount leaves its unit to the caller. */
export function formatCredits(amount: number | null | undefined): string {
  const n = typeof amount === "number" && Number.isFinite(amount) ? amount : 0;
  return n.toLocaleString("en-US", { minimumFractionDigits: 0, maximumFractionDigits: 2 });
}
