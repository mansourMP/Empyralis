"""Single source of truth for the Empyralis hosted-AI credit economy.

Every dollar-to-credit conversion, every free-allowance size, and every
margin multiplier used anywhere in the platform (backend billing/
entitlements, the credit-debit reconnect in ``sage_agent_runtime_service``,
and the frontend billing page) is derived from the constants in this one
file. If a number governing "how many credits does X cost" or "how
generous is the free tier" needs tuning, it is tuned HERE — nowhere else
should hardcode a dollar-to-credit rate or a free-allowance size.

── THE MODEL ────────────────────────────────────────────────────────────

1. Retail exchange rate — ``HOSTED_SAGE_AI_CREDITS_PER_USD``.
   This is the ONE conversion between real dollars and the "credits" a
   workspace sees: it prices both what a Polar top-up buys
   (``create_credit_purchase_checkout_session``) and what the balance/
   usage UI displays (``credit_balance_for_workspace``,
   ``workspace_billing_summary_for_workspace_id``). $1 buys
   ``HOSTED_SAGE_AI_CREDITS_PER_USD`` credits; a credit is worth
   ``1 / HOSTED_SAGE_AI_CREDITS_PER_USD`` dollars.

2. Per-turn charge, WITH margin — ``credits_for_turn_cost_usd()``.
   Every real hosted-AI turn logs a ground-truth provider cost in USD
   (``usage_events``, via ``pricing_registry_service`` — e.g. DeepSeek's
   published $0.14/$0.28 per-million-token input/output rate). We do not
   charge the workspace's credit balance that raw cost 1:1 — we apply
   ``CREDIT_COST_MARGIN_MULTIPLIER`` first, THEN convert to credits at the
   retail rate above. That multiplier is the platform's margin over the
   underlying provider cost (covers orchestration, storage, support, and
   gross margin — not just the token bill).

   Worked example (the reference "short hello → hello turn"): ~500 input
   + 150 output tokens on deepseek-chat ($0.14 / $0.28 per 1M tokens):
       raw_cost  = 500/1e6*0.14 + 150/1e6*0.28  ≈ $0.000112
       billed    = raw_cost * CREDIT_COST_MARGIN_MULTIPLIER (3x) ≈ $0.000336
       credits   = billed * HOSTED_SAGE_AI_CREDITS_PER_USD (100/$) ≈ 0.034
       charged   = ceil(0.034), floored at MIN_CREDITS_CHARGED_PER_TURN  = 1 credit
   A heavier turn (5,000 in / 1,500 out) still floors to 1 credit under the
   same formula, and a very large-context turn (30,000 in / 5,000 out)
   costs about 2 credits. The unit stays legible at both ends: a normal
   exchange reads as "1", a heavy one reads as a small number, never a
   fraction and never an opaque cost in micro-dollars. (At the retired
   2,000-credits/$ rate this same worked example produced ~7 and ~34
   credits respectively — those numbers no longer apply post-lean-grant.)

3. Free allowance — ``NEW_ACCOUNT_SIGNUP_CREDIT_USD``.
   Every workspace (new AND pre-existing) is guaranteed at least this
   many dollars of credit balance before any real per-turn debiting can
   bring it below that floor — see
   ``control_plane_repository._ensure_workspace_credit_balance_floor``,
   applied lazily and idempotently the first time a workspace's balance
   is touched by a real turn. At the default $1.00 (100 credits) and
   ~1 credit/turn, that is roughly a hundred free turns — a small,
   legible starter allowance (not a giant pile), but still enough that
   nobody should see a "0 credits" wall on their first few real turns
   or a demo.

4. Non-blocking, by construction.
   The per-turn debit (``control_plane_repository.
   debit_workspace_credits_for_turn_atomic``) clamps at zero: it always
   debits ``min(current_balance, credits_owed)`` and never raises for an
   insufficient balance. A turn that would drive the balance negative
   still completes; the shortfall is only logged (see
   ``sage_agent_runtime_service``'s reconnect call site). This module
   intentionally does NOT gate turns on remaining balance — that hard-
   stop concern belongs to the pre-existing, separately-tested
   ``entitlements_service.hosted_sage_ai_access_state`` policy gate
   (unrelated dial, left untouched by this reconnect).

── TUNING ───────────────────────────────────────────────────────────────

Every constant below has an ``EMPYRALIS_*`` environment-variable override,
so ops can retune the economy without a code change.
"""

from __future__ import annotations

import math
import os
from typing import Any


def _env_non_negative_float(name: str, fallback: float) -> float:
    raw = os.getenv(name)
    if raw is None:
        return float(fallback)
    try:
        parsed = float(raw)
    except (TypeError, ValueError):
        return float(fallback)
    return max(0.0, parsed)


def _env_positive_int(name: str, fallback: int) -> int:
    raw = os.getenv(name)
    if raw is None:
        return int(fallback)
    try:
        parsed = int(raw)
    except (TypeError, ValueError):
        return int(fallback)
    return max(1, parsed)


# ── 1. Retail exchange rate ─────────────────────────────────────────────
# $1 buys this many credits (Polar top-up grant rate AND the display
# rate for any "credits" figure shown anywhere in the product).
HOSTED_SAGE_AI_CREDITS_PER_USD = int(
    round(
        _env_non_negative_float(
            "EMPYRALIS_DISPLAY_CREDITS_PER_USD",
            100,
        )
    )
)

# ── Legacy dormant-system fallback cap (kept for the pre-existing,
#    separately-tested hosted_sage_ai_monthly_cap_usd entitlement gate —
#    see entitlements_service.py. Bumped alongside the rest of the
#    allowance for consistency even though real traffic does not
#    currently feed this gate's ledger; see module docstring point 4.) ──
DEFAULT_HOSTED_SAGE_AI_MONTHLY_CAP_USD = _env_non_negative_float(
    "EMPYRALIS_DEFAULT_HOSTED_SAGE_AI_MONTHLY_CAP_USD",
    5.00,
)

# ── MAN-144: default per-run cost ceiling ───────────────────────────────
# Applies to a SINGLE run/turn (checked before each model call inside it),
# not the monthly cap above (which only settles after a run finishes and
# only pauses future runs). Deliberately a small fraction of the monthly
# default: one runaway turn should never be able to spend anywhere near a
# full month's allowance before something notices. Per-agent override lives
# in DeployedAgentCommercePolicy.per_run_cost_ceiling_usd; this is the floor
# every run gets when nothing more specific is configured.
DEFAULT_RUN_COST_CEILING_USD = _env_non_negative_float(
    "EMPYRALIS_DEFAULT_RUN_COST_CEILING_USD",
    1.00,
)

# ── 3. Free allowance ───────────────────────────────────────────────────
# One-time (new workspaces) / floor top-up (pre-existing workspaces) grant
# in USD. At the default rate (100 credits/$) this is 1.00 * 100 = 100 credits
# — a lean, non-inflated free allowance (~one message per credit), in the
# spirit of a small starter grant rather than a giant credit pile.
NEW_ACCOUNT_SIGNUP_CREDIT_USD = _env_non_negative_float(
    "EMPYRALIS_NEW_ACCOUNT_SIGNUP_CREDIT_USD",
    1.00,
)

# Kept distinct from NEW_ACCOUNT_SIGNUP_CREDIT_USD (own env var) even
# though both currently default to the same figure, so the two concepts
# (a monthly $ cap on the legacy gate vs. a one-time/floor balance grant)
# can be tuned independently later without a code change.
NEW_ACCOUNT_HOSTED_SAGE_AI_MONTHLY_CAP_USD = _env_non_negative_float(
    "EMPYRALIS_NEW_ACCOUNT_HOSTED_SAGE_AI_MONTHLY_CAP_USD",
    5.00,
)

# ── 2. Per-turn margin ──────────────────────────────────────────────────
# Multiplier applied to a turn's ground-truth provider cost before it is
# converted to credits and debited. > 1.0 means the platform charges more
# credits than a 1:1 cost pass-through would imply — the margin.
CREDIT_COST_MARGIN_MULTIPLIER = _env_non_negative_float(
    "EMPYRALIS_CREDIT_COST_MARGIN_MULTIPLIER",
    3.0,
) or 1.0  # never let a misconfigured 0 zero out billed cost entirely

# Never charge (or display) 0 credits for a turn that did real work — the
# unit stays legible ("this turn cost credits", never "this turn cost
# nothing" for a turn that plainly consumed tokens).
MIN_CREDITS_CHARGED_PER_TURN = _env_positive_int(
    "EMPYRALIS_MIN_CREDITS_CHARGED_PER_TURN",
    1,
)

# ── 5. BYO (BYOK / subscription_passthrough / local) usage metering ────
# The platform's own dollar outlay for BYO-paid usage is genuinely $0 (the
# workspace's own API key or subscription covers the provider bill), so
# `platform_cost_usd` correctly stays 0 for these payers — that field means
# "what Empyralis itself paid," and Empyralis paid nothing. But the
# workspace still gets its BYO usage run through Empyralis's orchestration,
# storage, observability, and cap enforcement, so its own Empyralis credit
# balance IS now debited for it — at this configurable rate against the
# real off-platform cost already captured in `provider_reported_cost`
# (the ground-truth dollar cost of the call, recorded regardless of payer).
# Default 1.0 = bill 1:1 at the recorded provider cost, no markup. Raise
# above 1.0 later to add a BYO margin WITHOUT another code change.
EMPYRALIS_BYO_BILLING_RATE = _env_non_negative_float(
    "EMPYRALIS_BYO_BILLING_RATE",
    1.0,
)


def _safe_float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def display_credits_for_usd(amount_usd: Any) -> int:
    """Whole-credit DISPLAY conversion at the retail rate — no margin.
    Used for balance/purchase-grant display, not per-turn charging."""
    return int(round(max(0.0, _safe_float(amount_usd)) * HOSTED_SAGE_AI_CREDITS_PER_USD))


def display_credit_float_for_usd(amount_usd: Any) -> float:
    return round(max(0.0, _safe_float(amount_usd)) * HOSTED_SAGE_AI_CREDITS_PER_USD, 6)


def billed_cost_usd_for_turn(raw_cost_usd: Any) -> float:
    """Ground-truth provider cost -> billed cost, margin applied."""
    raw = max(0.0, _safe_float(raw_cost_usd))
    return round(raw * CREDIT_COST_MARGIN_MULTIPLIER, 8)


def credits_for_turn_cost_usd(raw_cost_usd: Any) -> int:
    """Ground-truth provider cost -> whole credits to charge for one turn.

    Applies the margin multiplier, converts at the retail rate, rounds UP
    to the next whole credit, and floors at MIN_CREDITS_CHARGED_PER_TURN
    so any turn that did real work costs a legible, non-zero number of
    credits. This is the ONLY function that should compute "how many
    credits does this turn cost" — see sage_agent_runtime_service's
    reconnect call site.
    """
    raw = max(0.0, _safe_float(raw_cost_usd))
    if raw <= 0:
        return 0
    billed_usd = billed_cost_usd_for_turn(raw)
    exact_credits = billed_usd * HOSTED_SAGE_AI_CREDITS_PER_USD
    return max(MIN_CREDITS_CHARGED_PER_TURN, int(math.ceil(exact_credits)))


def billed_cost_usd_for_byo_usage(provider_reported_cost_usd: Any) -> float:
    """Real off-platform provider cost -> billed cost, BYO rate applied.

    Mirrors ``billed_cost_usd_for_turn`` but uses ``EMPYRALIS_BYO_BILLING_RATE``
    (default 1.0, i.e. cost pass-through) instead of the hosted-AI margin —
    BYO usage is not run on Empyralis's own provider account, so the hosted
    margin multiplier does not apply to it by default.
    """
    raw = max(0.0, _safe_float(provider_reported_cost_usd))
    return round(raw * EMPYRALIS_BYO_BILLING_RATE, 8)


def credits_for_byo_usage_cost_usd(provider_reported_cost_usd: Any) -> float:
    """Real off-platform provider cost -> Empyralis credits to debit for
    BYO-paid usage (BYOK / subscription_passthrough / local payers).

    This is the BYO counterpart to ``credits_for_turn_cost_usd``. It differs
    in two ways that matter for correctness, not just style:
      - It does NOT floor at ``MIN_CREDITS_CHARGED_PER_TURN``: BYO usage with
        no recorded provider cost (a local Ollama call, or a flat-fee CLI
        subscription turn with no per-call price attached) legitimately
        debits 0 credits — there is no real dollar cost to meter.
      - It returns a float rather than a `ceil`'d whole credit, so small BYO
        turns accumulate fractional credit debits instead of always rounding
        up to at least 1 (which would over-charge relative to the
        1:1-by-default policy).
    """
    raw = max(0.0, _safe_float(provider_reported_cost_usd))
    if raw <= 0:
        return 0.0
    billed_usd = billed_cost_usd_for_byo_usage(raw)
    return round(billed_usd * HOSTED_SAGE_AI_CREDITS_PER_USD, 6)


# ── 6. PLAN LIMITS (not the credit economy — the resource caps) ─────────
#
# These are here rather than beside the code that enforces them for the
# reason this module's own header already states: a number governing "how
# much of X does a workspace get" is tuned in ONE file, never hardcoded at
# the call site. Two dials today, both with an ``EMPYRALIS_*`` override so
# ops can retune without a code change.
#
# WHY THESE TWO AND NOT A PROJECT/DOCUMENT/TASK COUNT: bytes on disk and
# member seats have a real marginal cost per unit. A project row and a
# document row do not — and CLAUDE.md's positioning entry is explicit that
# context (projects, documents, tasks) is the product and is NEVER the
# paywall. Capping a row count would be charging for the thing being sold.
#
# The storage cap is applied PER PROJECT (founder's decision), against the
# byte totals ``workspace_storage_service`` accounts for. See that module's
# docstring for exactly which bytes are counted and which are deliberately
# not.

# Bytes of stored files one project may hold. 1 GiB. A single file is
# separately capped at ``upload_content_policy.MAX_UPLOAD_BYTES`` (32MB), so
# this is a total, not a per-file ceiling.
PROJECT_STORAGE_CAP_BYTES = _env_positive_int(
    "EMPYRALIS_PROJECT_STORAGE_CAP_BYTES",
    1024 * 1024 * 1024,
)

# Active members one workspace may hold, INCLUDING its owner. Default 10.
WORKSPACE_MEMBER_LIMIT = _env_positive_int(
    "EMPYRALIS_WORKSPACE_MEMBER_LIMIT",
    10,
)


def project_storage_cap_bytes() -> int:
    """Read through a function, never the constant directly, so a test (or a
    future per-tier lookup) has ONE place to intercept."""
    return int(PROJECT_STORAGE_CAP_BYTES)


def workspace_member_limit() -> int:
    """Same posture as ``project_storage_cap_bytes`` — one interception
    point for the number, so no call site grows its own copy."""
    return int(WORKSPACE_MEMBER_LIMIT)
