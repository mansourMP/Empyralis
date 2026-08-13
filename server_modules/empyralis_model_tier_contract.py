from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Dict, List, Optional


EMPYRALIS_MODEL_TIER_VERSION = "2026-06-05.v2"

EMPYRALIS_HOSTED_TIERS = ("light", "pro")
LEGACY_EMPYRALIS_HOSTED_TIERS = ("max",)
USER_OWNED_TIERS = ("local_ai", "my_api_key", "my_ai_account")
EMPYRALIS_MODEL_TIERS = (*EMPYRALIS_HOSTED_TIERS, *USER_OWNED_TIERS)


@dataclass(frozen=True)
class EmpyralisModelTierContract:
    public_tier: str
    public_label: str
    internal_provider: Optional[str]
    internal_model: Optional[str]
    reasoning_effort: Optional[str]
    max_output_tier: str
    agent_budget_tier: str
    billing_source: str
    credit_multiplier: float
    fallback_tier: Optional[str]
    user_owned: bool
    expose_provider_model_to_ordinary_ui: bool
    ordinary_ui_subtitle: str

    @property
    def thinking_mode(self) -> Optional[str]:
        if self.user_owned:
            return None
        if self.reasoning_effort in {"high", "max"}:
            return self.reasoning_effort
        return "off"

    def to_dict(self, *, include_internal_route: bool = False) -> Dict[str, Any]:
        payload = asdict(self)
        payload["thinking_mode"] = self.thinking_mode
        if not include_internal_route and not self.expose_provider_model_to_ordinary_ui:
            payload["internal_provider"] = None
            payload["internal_model"] = None
        return payload


MODEL_TIER_CONTRACTS: Dict[str, EmpyralisModelTierContract] = {
    "light": EmpyralisModelTierContract(
        public_tier="light",
        public_label="Light",
        internal_provider="deepseek",
        # "deepseek-chat" was DeepSeek's own model id here until this fix —
        # DeepSeek retired it 2026-07-24 (provider_profiles.py's own comment
        # on its "deepseek" catalog entry documents the retirement date and
        # DeepSeek's undocumented behavior of silently substituting
        # deepseek-v4-flash under the old name rather than rejecting it).
        # So every "light" tier turn had been silently served by
        # deepseek-v4-flash for three weeks while the request wire carried
        # the dead name — exactly the substitution
        # sage_agent_runtime_service._resolve_served_model_from_usage now
        # detects and bills correctly, but there is no reason to keep
        # SENDING a retired id when the real one is known. "pro"/"max"
        # below already point at the current name; this completes that
        # migration. provider_profiles.PROVIDER_MODEL_ALIASES["deepseek"]
        # still maps the retired names forward for any other caller/stored
        # config that has not been updated — this is the one place that
        # generates FRESH traffic and gets fixed at the source.
        internal_model="deepseek-v4-flash",
        reasoning_effort=None,
        max_output_tier="standard",
        agent_budget_tier="standard",
        billing_source="empyralis_credits",
        credit_multiplier=0.5,
        fallback_tier=None,
        user_owned=False,
        expose_provider_model_to_ordinary_ui=False,
        ordinary_ui_subtitle="Empyralis credits",
    ),
    "pro": EmpyralisModelTierContract(
        public_tier="pro",
        public_label="Pro",
        internal_provider="deepseek",
        internal_model="deepseek-v4-pro",
        reasoning_effort="high",
        max_output_tier="expanded",
        agent_budget_tier="expanded",
        billing_source="empyralis_credits",
        credit_multiplier=1.0,
        fallback_tier=None,
        user_owned=False,
        expose_provider_model_to_ordinary_ui=False,
        ordinary_ui_subtitle="Empyralis credits",
    ),
    "max": EmpyralisModelTierContract(
        public_tier="max",
        public_label="Max",
        internal_provider="deepseek",
        internal_model="deepseek-v4-pro",
        reasoning_effort="max",
        max_output_tier="maximum",
        agent_budget_tier="maximum",
        billing_source="empyralis_credits",
        credit_multiplier=2.0,
        fallback_tier="pro",
        user_owned=False,
        expose_provider_model_to_ordinary_ui=False,
        ordinary_ui_subtitle="Empyralis credits",
    ),
    "local_ai": EmpyralisModelTierContract(
        public_tier="local_ai",
        public_label="Local AI",
        internal_provider=None,
        internal_model=None,
        reasoning_effort=None,
        max_output_tier="runtime_defined",
        agent_budget_tier="runtime_defined",
        billing_source="local_runtime",
        credit_multiplier=0.0,
        fallback_tier=None,
        user_owned=True,
        expose_provider_model_to_ordinary_ui=True,
        ordinary_ui_subtitle="This Computer",
    ),
    "my_api_key": EmpyralisModelTierContract(
        public_tier="my_api_key",
        public_label="My API Key",
        internal_provider=None,
        internal_model=None,
        reasoning_effort=None,
        max_output_tier="provider_defined",
        agent_budget_tier="standard",
        billing_source="user_api_key",
        credit_multiplier=0.0,
        fallback_tier=None,
        user_owned=True,
        expose_provider_model_to_ordinary_ui=True,
        ordinary_ui_subtitle="Your provider key",
    ),
    "my_ai_account": EmpyralisModelTierContract(
        public_tier="my_ai_account",
        public_label="My AI Account",
        internal_provider=None,
        internal_model=None,
        reasoning_effort=None,
        max_output_tier="account_defined",
        agent_budget_tier="standard",
        billing_source="user_ai_account",
        credit_multiplier=0.0,
        fallback_tier=None,
        user_owned=True,
        expose_provider_model_to_ordinary_ui=True,
        ordinary_ui_subtitle="Your signed-in AI account",
    ),
}


def normalize_model_tier(value: Any, *, fallback: str = "pro") -> str:
    tier = str(value or "").strip().lower().replace("-", "_")
    if tier == "max":
        return "pro"
    if tier in MODEL_TIER_CONTRACTS:
        return tier
    if fallback == "":
        return ""
    if fallback in MODEL_TIER_CONTRACTS:
        return fallback
    return "pro"


def model_tier_contract(tier: Any, *, fallback: str = "pro") -> EmpyralisModelTierContract:
    return MODEL_TIER_CONTRACTS[normalize_model_tier(tier, fallback=fallback)]


def list_model_tier_contracts(*, include_internal_routes: bool = False) -> List[Dict[str, Any]]:
    return [
        MODEL_TIER_CONTRACTS[tier].to_dict(include_internal_route=include_internal_routes)
        for tier in EMPYRALIS_MODEL_TIERS
    ]


def ordinary_ui_model_tier_contracts() -> List[Dict[str, Any]]:
    return list_model_tier_contracts(include_internal_routes=False)


def admin_model_tier_contracts() -> List[Dict[str, Any]]:
    return list_model_tier_contracts(include_internal_routes=True)


def user_owned_tier_allows_provider_picker(tier: Any) -> bool:
    return model_tier_contract(tier).expose_provider_model_to_ordinary_ui
