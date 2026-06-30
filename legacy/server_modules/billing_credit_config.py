from __future__ import annotations

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


DEFAULT_HOSTED_SAGE_AI_MONTHLY_CAP_USD = _env_non_negative_float(
    "EMPYRALIS_DEFAULT_HOSTED_SAGE_AI_MONTHLY_CAP_USD",
    0.50,
)

HOSTED_SAGE_AI_CREDITS_PER_USD = int(
    round(
        _env_non_negative_float(
            "EMPYRALIS_DISPLAY_CREDITS_PER_USD",
            20_000,
        )
    )
)


def _safe_float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def display_credits_for_usd(amount_usd: Any) -> int:
    return int(round(max(0.0, _safe_float(amount_usd)) * HOSTED_SAGE_AI_CREDITS_PER_USD))


def display_credit_float_for_usd(amount_usd: Any) -> float:
    return round(max(0.0, _safe_float(amount_usd)) * HOSTED_SAGE_AI_CREDITS_PER_USD, 6)
