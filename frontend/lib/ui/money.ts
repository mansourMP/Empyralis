/**
 * The ONE place a USD amount becomes text.
 *
 * Before this module existed, `const money = (n) => `$${n.toFixed(4)}`` was
 * copy-pasted into five files (PrimaryRail, AgentsList, agent-view-options,
 * projects/page, billing/page, projects/[projectId]/page) plus three inline
 * `.toFixed(4)` call sites. Ten places, one decision, and no way to change it
 * once — the same "a list copied into a third place" shape this codebase
 * already treats as a defect in its channel/connector surfaces.
 *
 * TWO decimals, not four. Founder's call, 2026-08-26: "the cost have to be
 * only two numbers after dot." Four decimals read as unfinished on a money
 * column, and every row on a fresh workspace showed `$0.0000`.
 *
 * THE SUB-CENT CASE IS WHY THIS IS A FUNCTION AND NOT A `.toFixed(2)`.
 * A single agent turn can genuinely cost a fraction of a cent. Rounding that
 * to `$0.00` states ZERO for money that was actually spent — a small lie, and
 * exactly the class this codebase has a standing law about ("two different
 * facts may never share one signal"): "cost nothing" and "cost less than we
 * can show at this precision" are different facts. So a real amount below the
 * display floor renders `<$0.01` instead.
 *
 * Zero stays `$0.00`. Callers that want a dash for zero (PrimaryRail does)
 * keep making that choice themselves — that is a presentation decision about
 * empty rows, not a formatting one, and folding it in here would force it on
 * every surface.
 */

/** Amounts at or above this render as an ordinary two-decimal figure. */
const DISPLAY_FLOOR_USD = 0.005;

export function formatUsd(amount: number | null | undefined): string {
  const n = typeof amount === "number" && Number.isFinite(amount) ? amount : 0;
  // Below the floor but genuinely spent — never claim $0.00 for real money.
  if (n > 0 && n < DISPLAY_FLOOR_USD) return "<$0.01";
  return `$${n.toFixed(2)}`;
}
