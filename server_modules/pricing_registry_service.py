from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Dict, Optional


PRICING_REGISTRY_VERSION = "2026-05-20"
QWEN_CNY_PER_USD = 7.108
QWEN_USD_PER_CNY = 1.0 / QWEN_CNY_PER_USD


@dataclass(frozen=True)
class PricingRate:
    input_usd_per_million: Optional[float]
    output_usd_per_million: Optional[float]
    source: str
    cached_input_usd_per_million: Optional[float] = None
    cache_write_usd_per_million: Optional[float] = None
    cache_read_usd_per_million: Optional[float] = None
    reasoning_output_usd_per_million: Optional[float] = None
    thinking_output_usd_per_million: Optional[float] = None

    def as_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class PricingRegistryEntry:
    provider: str
    model: str
    rate: PricingRate
    pricing_registry_version: str = PRICING_REGISTRY_VERSION

    def as_dict(self) -> Dict[str, Any]:
        payload = asdict(self)
        payload["source"] = self.rate.source
        payload["input"] = self.rate.input_usd_per_million
        payload["output"] = self.rate.output_usd_per_million
        payload["cached_input"] = self.rate.cached_input_usd_per_million
        payload["cache_write"] = self.rate.cache_write_usd_per_million
        payload["cache_read"] = self.rate.cache_read_usd_per_million
        payload["reasoning_output"] = self.rate.reasoning_output_usd_per_million
        payload["thinking_output"] = self.rate.thinking_output_usd_per_million
        return payload


LEGACY_MODEL_ALIASES = {
    "openai": {
        "gpt-4-0613": "gpt-4",
        "gpt-4-0314": "gpt-4",
    },
    "anthropic": {
        "claude-sonnet-3.5": "claude-3-5-sonnet-20241022",
        "claude-3.5-sonnet": "claude-3-5-sonnet-20241022",
        "claude-3-5-sonnet": "claude-3-5-sonnet-20241022",
    },
    "mistral": {
        "mistral-small-3.2": "mistral-small-latest",
        "mistral-medium-3.1": "mistral-medium-latest",
        "mistral-large-3": "mistral-large-latest",
    },
}


MODEL_PRICING_USD_PER_MILLION: Dict[str, Dict[str, Dict[str, Any]]] = {
    "codex_cli": {
        "gpt-5.4": {"input": None, "output": None, "source": "subscription_cli"},
    },
    "claude_code_cli": {
        "sonnet": {"input": None, "output": None, "source": "subscription_cli"},
    },
    "openai": {
        "gpt-5.5": {"input": 5.00, "output": 30.00, "source": "https://developers.openai.com/api/docs/models"},
        "gpt-5.5-pro": {"input": 30.00, "output": 180.00, "source": "https://openai.com/index/introducing-gpt-5-5/"},
        "gpt-5.4": {"input": 2.50, "output": 15.00, "source": "https://developers.openai.com/api/docs/models/gpt-5.4/"},
        "gpt-5.4-mini": {"input": 0.75, "output": 4.50, "source": "https://developers.openai.com/api/docs/models"},
        "gpt-5.4-nano": {"input": 0.20, "output": 1.25, "source": "https://developers.openai.com/api/docs/models/gpt-5.4-nano/"},
        "gpt-5.2": {"input": 1.75, "output": 14.00, "source": "https://platform.openai.com/docs/pricing"},
        "gpt-5.1": {"input": 1.25, "output": 10.00, "source": "https://platform.openai.com/docs/pricing"},
        "gpt-5": {"input": 1.25, "output": 10.00, "source": "https://platform.openai.com/docs/pricing"},
        "gpt-5-mini": {"input": 0.25, "output": 2.00, "source": "https://platform.openai.com/docs/pricing"},
        "gpt-5-nano": {"input": 0.05, "output": 0.40, "source": "https://platform.openai.com/docs/pricing"},
        "gpt-4.1": {"input": 2.00, "output": 8.00, "source": "https://platform.openai.com/pricing"},
        "gpt-4.1-mini": {"input": 0.40, "output": 1.60, "source": "https://platform.openai.com/pricing"},
        "gpt-4.1-nano": {"input": 0.10, "output": 0.40, "source": "https://platform.openai.com/pricing"},
        "gpt-4": {"input": 30.00, "output": 60.00, "source": "https://platform.openai.com/docs/models/gpt-4"},
        "gpt-4o": {"input": 2.50, "output": 10.00, "source": "https://platform.openai.com/docs/pricing"},
        "gpt-4o-mini": {"input": 0.15, "output": 0.60, "source": "https://platform.openai.com/pricing"},
    },
    "anthropic": {
        "claude-opus-4-7": {"input": 5.00, "output": 25.00, "source": "https://platform.claude.com/docs/en/about-claude/models/overview"},
        "claude-sonnet-4-6": {"input": 3.00, "output": 15.00, "source": "https://platform.claude.com/docs/en/about-claude/models/overview"},
        "claude-haiku-4-5-20251001": {"input": 1.00, "output": 5.00, "source": "https://platform.claude.com/docs/en/about-claude/models/overview"},
        "claude-opus-4-1-20250805": {"input": 15.00, "output": 75.00, "source": "https://docs.anthropic.com/en/docs/about-claude/models/all-models"},
        "claude-opus-4-20250514": {"input": 15.00, "output": 75.00, "source": "https://docs.anthropic.com/en/docs/about-claude/models/all-models"},
        "claude-sonnet-4-20250514": {"input": 3.00, "output": 15.00, "source": "https://docs.anthropic.com/en/docs/about-claude/models/all-models"},
        "claude-3-5-sonnet-20241022": {"input": 3.00, "output": 15.00, "source": "https://docs.anthropic.com/en/docs/about-claude/models/all-models"},
        "claude-3-7-sonnet": {"input": 3.00, "output": 15.00, "source": "https://docs.anthropic.com/en/docs/about-claude/models/all-models"},
        "claude-sonnet-4": {"input": 3.00, "output": 15.00, "source": "https://docs.anthropic.com/en/docs/about-claude/models/all-models"},
        "claude-haiku-4.5": {"input": 1.00, "output": 5.00, "source": "https://www.anthropic.com/claude/haiku?m=1"},
        "claude-3-5-haiku-20241022": {"input": 0.80, "output": 4.00, "source": "https://docs.anthropic.com/en/docs/about-claude/models/all-models"},
        "claude-3-7-sonnet-latest": {"input": 3.00, "output": 15.00, "source": "https://docs.anthropic.com/en/docs/about-claude/models/all-models"},
    },
    "gemini": {
        "gemini-3-pro-preview": {"input": None, "output": None, "source": "https://ai.google.dev/gemini-api/docs/models"},
        "gemini-3-flash-preview": {"input": None, "output": None, "source": "https://ai.google.dev/gemini-api/docs/models"},
        "gemini-2.5-flash-lite": {"input": None, "output": None, "source": "https://ai.google.dev/gemini-api/docs/models"},
        "gemini-2.5-flash": {"input": 0.30, "output": 2.50, "source": "https://ai.google.dev/gemini-api/docs/pricing"},
        "gemini-2.5-pro": {"input": 1.25, "output": 10.00, "source": "https://ai.google.dev/gemini-api/docs/pricing"},
        "gemini-1.5-flash": {"input": 0.10, "output": 0.30, "source": "https://ai.google.dev/gemini-api/docs/pricing"},
        "gemini-1.5-pro": {"input": 1.25, "output": 5.00, "source": "https://ai.google.dev/gemini-api/docs/pricing"},
        "gemini-2.0-flash": {"input": 0.10, "output": 0.40, "source": "https://ai.google.dev/gemini-api/docs/pricing"},
        "gemini-2.0-flash-001": {"input": 0.10, "output": 0.40, "source": "https://ai.google.dev/gemini-api/docs/pricing"},
    },
    "ollama": {
        "llama3": {"input": None, "output": None, "source": "local_runtime"},
        "llama3.1:8b": {"input": None, "output": None, "source": "local_runtime"},
        "mistral": {"input": None, "output": None, "source": "local_runtime"},
        "gemma": {"input": None, "output": None, "source": "local_runtime"},
        "phi3": {"input": None, "output": None, "source": "local_runtime"},
        "llama3.2": {"input": None, "output": None, "source": "local_runtime"},
    },
    "ollama_cloud": {
        "gpt-oss:120b": {"input": None, "output": None, "source": "https://docs.ollama.com/cloud"},
        "gpt-oss:20b": {"input": None, "output": None, "source": "https://docs.ollama.com/cloud"},
    },
    "qwen": {
        "qwen-turbo": {
            "input": round(0.3 * QWEN_USD_PER_CNY, 6),
            "output": round(0.6 * QWEN_USD_PER_CNY, 6),
            "source": "https://help.aliyun.com/zh/model-studio/getting-started/models",
        },
        "qwen-plus": {
            "input": round(2.936 * QWEN_USD_PER_CNY, 6),
            "output": round(8.807 * QWEN_USD_PER_CNY, 6),
            "source": "https://help.aliyun.com/zh/model-studio/getting-started/models",
        },
        "qwen-max": {
            "input": round(40.0 * QWEN_USD_PER_CNY, 6),
            "output": round(120.0 * QWEN_USD_PER_CNY, 6),
            "source": "https://help.aliyun.com/zh/model-studio/getting-started/models",
        },
    },
    "deepseek": {
        "deepseek-v4-flash": {
            "input": 0.14,
            "cached_input": 0.0028,
            "cache_read": 0.0028,
            "cache_write": 0.14,
            "output": 0.28,
            "reasoning_output": 0.28,
            "thinking_output": 0.28,
            "source": "https://api-docs.deepseek.com/quick_start/pricing/",
        },
        "deepseek-v4-pro": {
            "input": 0.435,
            "cached_input": 0.003625,
            "cache_read": 0.003625,
            "cache_write": 0.435,
            "output": 0.87,
            "reasoning_output": 0.87,
            "thinking_output": 0.87,
            "source": "https://api-docs.deepseek.com/quick_start/pricing/",
        },
        "deepseek-chat": {
            "input": 0.14,
            "cached_input": 0.0028,
            "cache_read": 0.0028,
            "output": 0.28,
            "source": "https://api-docs.deepseek.com/quick_start/pricing/",
        },
        "deepseek-reasoner": {
            "input": 0.14,
            "cached_input": 0.0028,
            "cache_read": 0.0028,
            "output": 0.28,
            "reasoning_output": 0.28,
            "thinking_output": 0.28,
            "source": "https://api-docs.deepseek.com/quick_start/pricing/",
        },
    },
    "mistral": {
        "mistral-small-latest": {"input": 0.10, "output": 0.30, "source": "https://docs.mistral.ai/models/mistral-small-3-2-25-06"},
        "mistral-medium-latest": {"input": 0.40, "output": 2.00, "source": "https://docs.mistral.ai/models/mistral-medium-3-1-25-08"},
        "mistral-large-latest": {"input": 0.50, "output": 1.50, "source": "https://docs.mistral.ai/models/mistral-large-3-25-12"},
    },
    "xai": {
        "grok-4-0709": {"input": 3.00, "output": 15.00, "source": "https://docs.x.ai/docs/models/grok-4"},
        "grok-4": {"input": 3.00, "output": 15.00, "source": "https://docs.x.ai/docs/models/grok-4"},
        "grok-4-latest": {"input": 3.00, "output": 15.00, "source": "https://docs.x.ai/docs/models/grok-4"},
    },
    "local_companion": {
        "local-worker-v0": {"input": None, "output": None, "source": "local_runtime"},
    },
    "orion": {},
}


def _apply_prompt_cache_pricing_defaults() -> None:
    for payload in MODEL_PRICING_USD_PER_MILLION.get("openai", {}).values():
        input_rate = payload.get("input")
        if input_rate is None:
            continue
        cached_rate = round(float(input_rate) * 0.5, 6)
        payload.setdefault("cached_input", cached_rate)
        payload.setdefault("cache_read", cached_rate)
        payload.setdefault("cache_write", float(input_rate))
    for payload in MODEL_PRICING_USD_PER_MILLION.get("anthropic", {}).values():
        input_rate = payload.get("input")
        if input_rate is None:
            continue
        payload.setdefault("cached_input", round(float(input_rate) * 0.1, 6))
        payload.setdefault("cache_read", round(float(input_rate) * 0.1, 6))
        payload.setdefault("cache_write", round(float(input_rate) * 1.25, 6))


_apply_prompt_cache_pricing_defaults()


PROVIDER_FALLBACK_USD_PER_1K: Dict[str, Dict[str, Any]] = {
    "openai": {"input": 0.0050, "output": 0.0150, "source": "https://platform.openai.com/pricing"},
    "openai-codex": {"input": 0.0, "output": 0.0, "source": "subscription_cli"},
    "anthropic": {"input": 0.0030, "output": 0.0150, "source": "https://docs.anthropic.com/en/docs/about-claude/models/all-models"},
    "claude_code_cli": {"input": 0.0, "output": 0.0, "source": "subscription_cli"},
    "gemini": {"input": 0.0003, "output": 0.0025, "source": "https://ai.google.dev/gemini-api/docs/pricing"},
    "qwen": {"input": 0.0, "output": 0.0, "source": "https://help.aliyun.com/zh/model-studio/getting-started/models"},
    "deepseek": {"input": 0.00014, "output": 0.00028, "source": "https://api-docs.deepseek.com/quick_start/pricing/"},
    "mistral": {"input": 0.0, "output": 0.0, "source": "https://docs.mistral.ai/models/"},
    "ollama": {"input": 0.0, "output": 0.0, "source": "local_runtime"},
    "ollama_cloud": {"input": 0.0, "output": 0.0, "source": "https://docs.ollama.com/cloud"},
    "local_companion": {"input": 0.0, "output": 0.0, "source": "local_runtime"},
    "orion": {"input": 0.0, "output": 0.0, "source": "internal_runtime"},
}


def normalize_provider_id(provider: Any) -> str:
    return str(provider or "").strip().lower()


def normalize_model_id(provider: Any, model: Any) -> str:
    provider_id = normalize_provider_id(provider)
    model_id = str(model or "").strip().lower().replace(" ", "-")
    return LEGACY_MODEL_ALIASES.get(provider_id, {}).get(model_id, model_id)


def pricing_registry_version() -> str:
    return PRICING_REGISTRY_VERSION


def lookup_pricing_entry(provider: Any, model: Any) -> Optional[PricingRegistryEntry]:
    provider_id = normalize_provider_id(provider)
    model_id = normalize_model_id(provider_id, model)
    raw_entry = MODEL_PRICING_USD_PER_MILLION.get(provider_id, {}).get(model_id)
    if not isinstance(raw_entry, dict):
        return None
    return PricingRegistryEntry(
        provider=provider_id,
        model=model_id,
        rate=PricingRate(
            input_usd_per_million=raw_entry.get("input"),
            output_usd_per_million=raw_entry.get("output"),
            source=str(raw_entry.get("source") or "").strip() or "pricing_registry",
            cached_input_usd_per_million=raw_entry.get("cached_input"),
            cache_write_usd_per_million=raw_entry.get("cache_write"),
            cache_read_usd_per_million=raw_entry.get("cache_read"),
            reasoning_output_usd_per_million=raw_entry.get("reasoning_output"),
            thinking_output_usd_per_million=raw_entry.get("thinking_output"),
        ),
    )


def lookup_model_pricing(provider: Any, model: Any) -> Optional[Dict[str, Any]]:
    entry = lookup_pricing_entry(provider, model)
    return entry.as_dict() if entry is not None else None


def _token_cost(tokens: int, rate: Optional[float]) -> Optional[float]:
    if rate is None:
        return None
    return max(0, int(tokens or 0)) * float(rate) / 1_000_000.0


def estimate_cost_usd(
    provider: Any,
    model: Any,
    input_tokens: int,
    output_tokens: int,
    *,
    cached_input_tokens: int = 0,
    cache_write_tokens: int = 0,
    cache_read_tokens: int = 0,
    reasoning_tokens: int = 0,
    thinking_tokens: int = 0,
) -> Optional[float]:
    entry = lookup_pricing_entry(provider, model)
    if entry is None:
        return None
    rate = entry.rate
    input_cost = _token_cost(input_tokens, rate.input_usd_per_million)
    output_cost = _token_cost(output_tokens, rate.output_usd_per_million)
    if input_cost is None or output_cost is None:
        return None
    cached_rate = rate.cached_input_usd_per_million or rate.cache_read_usd_per_million or rate.input_usd_per_million
    cache_write_rate = rate.cache_write_usd_per_million or rate.input_usd_per_million
    cache_read_rate = rate.cache_read_usd_per_million or rate.cached_input_usd_per_million or rate.input_usd_per_million
    reasoning_rate = rate.reasoning_output_usd_per_million or rate.output_usd_per_million
    thinking_rate = rate.thinking_output_usd_per_million or rate.output_usd_per_million
    total = (
        input_cost
        + output_cost
        + (_token_cost(cached_input_tokens, cached_rate) or 0.0)
        + (_token_cost(cache_write_tokens, cache_write_rate) or 0.0)
        + (_token_cost(cache_read_tokens, cache_read_rate) or 0.0)
        + (_token_cost(reasoning_tokens, reasoning_rate) or 0.0)
        + (_token_cost(thinking_tokens, thinking_rate) or 0.0)
    )
    return round(total, 6)


def provider_fallback_rate_per_1k(provider: Any) -> Dict[str, Any]:
    provider_id = normalize_provider_id(provider)
    payload = PROVIDER_FALLBACK_USD_PER_1K.get(provider_id) or {"input": 0.0, "output": 0.0, "source": "pricing_registry_fallback"}
    return {
        "input": round(float(payload.get("input") or 0.0), 8),
        "output": round(float(payload.get("output") or 0.0), 8),
        "source": str(payload.get("source") or "").strip() or "pricing_registry_fallback",
        "pricing_registry_version": PRICING_REGISTRY_VERSION,
    }


def provider_fallback_rates_per_1k() -> Dict[str, Dict[str, Any]]:
    return {
        provider: provider_fallback_rate_per_1k(provider)
        for provider in PROVIDER_FALLBACK_USD_PER_1K.keys()
    }


# ---------------------------------------------------------------------------
# Agent Computer (provisioned VPS) droplet pricing — provider size -> USD/hr
# ---------------------------------------------------------------------------
# MAN-134's "one genuinely new piece". Unlike the token tables above, DO
# publishes no machine-readable price feed we can trust unauthenticated
# (their live /v2/sizes endpoint requires a bearer token and is a real
# network call — vps_provisioning_service.fetch_provider_plans already makes
# that call for the plan picker, but a metering sweep must never depend on a
# live provider call: this table is the offline source of truth for that
# path, deliberately DB/network-free like the rest of this module).
#
# Fetched directly from DigitalOcean's own published pricing page on
# 2026-08-13 (https://www.digitalocean.com/pricing/droplets, cross-checked
# against https://docs.digitalocean.com/products/droplets/details/pricing/):
# the "Basic" (Regular/shared-CPU) Droplet lineup — $/mo divided by 730
# hours/month equals DO's own published $/hr figure exactly, confirming
# these are not derived/rounded numbers but DO's stated prices.
#
# NOT the full DigitalOcean catalog. DO offers dozens of Premium Intel/AMD,
# CPU-Optimized, General-Purpose, Memory-Optimized and Storage-Optimized
# sizes, and nothing in this codebase curates an allowlist against them —
# vps_provisioning_service.fetch_provider_plans passes through whatever
# DO's live API returns, filtered only by vcpus/memory/price floors, not by
# slug. Only DigitalOcean is covered — it is the one provisioning provider
# actually wired to a platform-owned account today (CLAUDE.md: "DigitalOcean
# is the working provider; Google Cloud is next"; AWS deliberately unwired).
# A droplet on a size not in this table is UNPRICED:
# agent_computer_metering_service treats that as "cannot bill safely" and
# skips the box (never invents a price), reporting the gap rather than
# guessing at it — see that module's docstring.
#
# THIS TABLE GOES STALE. DigitalOcean can and does change prices; there is
# no live check that these numbers still match. Re-fetch the pricing page
# and update DROPLET_PRICING_FETCHED_AT whenever prices are rechecked — a
# stale hardcoded price silently under- or over-charges every workspace
# running that size, forever, with no error anywhere.
DROPLET_PRICING_SOURCE = "https://www.digitalocean.com/pricing/droplets"
DROPLET_PRICING_FETCHED_AT = "2026-08-13"

DROPLET_PRICING_USD_PER_HOUR: Dict[str, Dict[str, Dict[str, Any]]] = {
    "digitalocean": {
        "s-1vcpu-512mb-10gb": {"hourly": 0.00595, "monthly": 4.00, "vcpus": 1, "memory_mb": 512},
        "s-1vcpu-1gb": {"hourly": 0.00893, "monthly": 6.00, "vcpus": 1, "memory_mb": 1024},
        "s-1vcpu-2gb": {"hourly": 0.01786, "monthly": 12.00, "vcpus": 1, "memory_mb": 2048},
        "s-2vcpu-2gb": {"hourly": 0.02679, "monthly": 18.00, "vcpus": 2, "memory_mb": 2048},
        "s-2vcpu-4gb": {"hourly": 0.03571, "monthly": 24.00, "vcpus": 2, "memory_mb": 4096},
        "s-4vcpu-8gb": {"hourly": 0.07143, "monthly": 48.00, "vcpus": 4, "memory_mb": 8192},
        "s-8vcpu-16gb": {"hourly": 0.14286, "monthly": 96.00, "vcpus": 8, "memory_mb": 16384},
    },
}


def droplet_hourly_rate_usd(provider: Any, size: Any) -> Optional[float]:
    """Raw (pre-margin) USD/hr for one provider size slug, or None when the
    size is not in DROPLET_PRICING_USD_PER_HOUR -- callers must treat None as
    "cannot price safely", never as zero. Margin is NOT applied here — same
    split as the token tables above: this function returns ground truth,
    billing_credit_config.credits_for_turn_cost_usd applies
    CREDIT_COST_MARGIN_MULTIPLIER at the point of charging."""
    provider_id = normalize_provider_id(provider)
    size_id = str(size or "").strip().lower()
    if not size_id:
        return None
    entry = DROPLET_PRICING_USD_PER_HOUR.get(provider_id, {}).get(size_id)
    if not isinstance(entry, dict):
        return None
    try:
        rate = float(entry.get("hourly"))
    except (TypeError, ValueError):
        return None
    return rate if rate > 0 else None


def catalog_price_projection(provider: Any, model: Any) -> Dict[str, Any]:
    entry = lookup_pricing_entry(provider, model)
    if entry is not None and entry.rate.input_usd_per_million is not None and entry.rate.output_usd_per_million is not None:
        return {
            "input_cost_per_1k_usd": round(float(entry.rate.input_usd_per_million) / 1000.0, 8),
            "output_cost_per_1k_usd": round(float(entry.rate.output_usd_per_million) / 1000.0, 8),
            "pricing_source": entry.rate.source,
            "pricing_registry_version": entry.pricing_registry_version,
            "pricing_known": True,
        }
    fallback = provider_fallback_rate_per_1k(provider)
    return {
        "input_cost_per_1k_usd": float(fallback.get("input") or 0.0),
        "output_cost_per_1k_usd": float(fallback.get("output") or 0.0),
        "pricing_source": fallback.get("source"),
        "pricing_registry_version": fallback.get("pricing_registry_version"),
        "pricing_known": False,
    }
