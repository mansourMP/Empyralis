from __future__ import annotations

from dataclasses import dataclass
from typing import Dict

from server_modules import config_defaults_service


DIRECT_CHAT_PROFILE = "direct_chat"
EXTERNAL_CHANNEL_CUSTOMER_PROFILE = "external_channel_customer"
DURABLE_RUN_PROFILE = "durable_run"
SPECIALIST_PRIVATE_PROFILE = "specialist_private"
OWNER_SAGE_VIEW_PROFILE = "owner_sage_view"

CONTEXT_BUDGET_PRESET_COMPACT = "compact"
CONTEXT_BUDGET_PRESET_STANDARD = "standard"
CONTEXT_BUDGET_PRESET_EXTENDED = "extended"
CONTEXT_BUDGET_PRESET_MAX = "max"
CONTEXT_BUDGET_PRESET_BALANCED = "balanced"
CONTEXT_BUDGET_PRESET_DEEP = "deep"

RETENTION_PRESET_SHORT = "short"
RETENTION_PRESET_STANDARD = "standard"
RETENTION_PRESET_EXTENDED = "extended"


@dataclass(frozen=True, slots=True)
class MemoryRetentionContract:
    raw_transcript_days: int
    summary_days: int
    semantic_days: int
    event_memory_days: int
    local_private_sync: str = "explicit_only"
    cloud_safe_only: bool = False


@dataclass(frozen=True, slots=True)
class MemoryPolicyProfile:
    name: str
    max_prompt_tokens: int
    preserve_last_messages: int
    summary_trigger_messages: int
    summary_trigger_tokens: int
    semantic_retrieval_k: int
    max_summary_chars: int
    max_business_plan_chars: int
    max_recent_log_days: int
    max_transcript_items: int
    raw_transcript_enabled: bool
    semantic_write_enabled: bool
    semantic_read_enabled: bool
    retention: MemoryRetentionContract
    recent_message_budget_tokens: int = 0
    output_reserve_tokens: int = 0
    system_tool_reserve_tokens: int = 0
    retrieval_reserve_tokens: int = 0


_BASE_RETENTION = MemoryRetentionContract(
    raw_transcript_days=365,
    summary_days=365,
    semantic_days=365,
    event_memory_days=365,
)

_CONTEXT_BUDGET_PRESET_ALIASES = {
    CONTEXT_BUDGET_PRESET_BALANCED: CONTEXT_BUDGET_PRESET_STANDARD,
    CONTEXT_BUDGET_PRESET_DEEP: CONTEXT_BUDGET_PRESET_EXTENDED,
}

_CONTEXT_BUDGET_PRESETS = {
    CONTEXT_BUDGET_PRESET_COMPACT: {
        "window_share": 0.16,
        "max_prompt_tokens": 24_000,
        "preserve_last_messages": 6,
    },
    CONTEXT_BUDGET_PRESET_STANDARD: {
        "window_share": 0.32,
        "max_prompt_tokens": 96_000,
        "preserve_last_messages": 8,
    },
    CONTEXT_BUDGET_PRESET_EXTENDED: {
        "window_share": 0.55,
        "max_prompt_tokens": 400_000,
        "preserve_last_messages": 12,
    },
    CONTEXT_BUDGET_PRESET_MAX: {
        "window_share": 0.82,
        "max_prompt_tokens": None,
        "preserve_last_messages": 20,
    },
}

_RETENTION_PRESET_DAYS = {
    RETENTION_PRESET_SHORT: 30,
    RETENTION_PRESET_STANDARD: 365,
    RETENTION_PRESET_EXTENDED: 730,
}


def _clamp(value: int, lower: int, upper: int) -> int:
    if upper < lower:
        return int(lower)
    return max(int(lower), min(int(value), int(upper)))

_PROFILE_REGISTRY: Dict[str, MemoryPolicyProfile] = {
    DIRECT_CHAT_PROFILE: MemoryPolicyProfile(
        name=DIRECT_CHAT_PROFILE,
        max_prompt_tokens=8000,
        preserve_last_messages=10,
        summary_trigger_messages=12,
        summary_trigger_tokens=1500,
        semantic_retrieval_k=5,
        max_summary_chars=2200,
        max_business_plan_chars=3200,
        max_recent_log_days=7,
        max_transcript_items=12,
        raw_transcript_enabled=True,
        semantic_write_enabled=True,
        semantic_read_enabled=True,
        retention=_BASE_RETENTION,
    ),
    EXTERNAL_CHANNEL_CUSTOMER_PROFILE: MemoryPolicyProfile(
        name=EXTERNAL_CHANNEL_CUSTOMER_PROFILE,
        max_prompt_tokens=1100,
        preserve_last_messages=8,
        summary_trigger_messages=12,
        summary_trigger_tokens=1500,
        semantic_retrieval_k=5,
        max_summary_chars=2200,
        max_business_plan_chars=3200,
        max_recent_log_days=7,
        max_transcript_items=12,
        raw_transcript_enabled=True,
        semantic_write_enabled=False,
        semantic_read_enabled=False,
        retention=_BASE_RETENTION,
    ),
    DURABLE_RUN_PROFILE: MemoryPolicyProfile(
        name=DURABLE_RUN_PROFILE,
        max_prompt_tokens=2400,
        preserve_last_messages=8,
        summary_trigger_messages=12,
        summary_trigger_tokens=1500,
        semantic_retrieval_k=5,
        max_summary_chars=2200,
        max_business_plan_chars=2200,
        max_recent_log_days=7,
        max_transcript_items=8,
        raw_transcript_enabled=False,
        semantic_write_enabled=True,
        semantic_read_enabled=True,
        retention=_BASE_RETENTION,
    ),
    SPECIALIST_PRIVATE_PROFILE: MemoryPolicyProfile(
        name=SPECIALIST_PRIVATE_PROFILE,
        max_prompt_tokens=2400,
        preserve_last_messages=8,
        summary_trigger_messages=12,
        summary_trigger_tokens=1500,
        semantic_retrieval_k=5,
        max_summary_chars=2200,
        max_business_plan_chars=2200,
        max_recent_log_days=7,
        max_transcript_items=8,
        raw_transcript_enabled=True,
        semantic_write_enabled=True,
        semantic_read_enabled=True,
        retention=_BASE_RETENTION,
    ),
    OWNER_SAGE_VIEW_PROFILE: MemoryPolicyProfile(
        name=OWNER_SAGE_VIEW_PROFILE,
        max_prompt_tokens=2400,
        preserve_last_messages=8,
        summary_trigger_messages=12,
        summary_trigger_tokens=1500,
        semantic_retrieval_k=5,
        max_summary_chars=2200,
        max_business_plan_chars=2200,
        max_recent_log_days=7,
        max_transcript_items=4,
        raw_transcript_enabled=False,
        semantic_write_enabled=False,
        semantic_read_enabled=True,
        retention=_BASE_RETENTION,
    ),
}


def get_memory_policy_profile(profile: str | MemoryPolicyProfile) -> MemoryPolicyProfile:
    if isinstance(profile, MemoryPolicyProfile):
        if profile.name == DURABLE_RUN_PROFILE:
            return _resolve_durable_run_profile()
        return profile
    normalized_profile = str(profile or DIRECT_CHAT_PROFILE).strip().lower() or DIRECT_CHAT_PROFILE
    if normalized_profile == DURABLE_RUN_PROFILE:
        return _resolve_durable_run_profile()
    return _PROFILE_REGISTRY.get(normalized_profile, _PROFILE_REGISTRY[DIRECT_CHAT_PROFILE])


def normalize_context_budget_preset(value: str | None) -> str:
    token = str(value or "").strip().lower()
    token = _CONTEXT_BUDGET_PRESET_ALIASES.get(token, token)
    if token in _CONTEXT_BUDGET_PRESETS:
        return token
    default_token = str(config_defaults_service.default_context_budget_preset() or "").strip().lower()
    default_token = _CONTEXT_BUDGET_PRESET_ALIASES.get(default_token, default_token)
    if default_token in _CONTEXT_BUDGET_PRESETS:
        return default_token
    return CONTEXT_BUDGET_PRESET_STANDARD


def normalize_retention_preset(value: str | None) -> str:
    token = str(value or "").strip().lower()
    if token in _RETENTION_PRESET_DAYS:
        return token
    return config_defaults_service.default_retention_preset()


def _context_budget_tokens(*, preset: str, context_window_tokens: int | None) -> int:
    window = max(8_000, int(context_window_tokens or 128_000))
    output_reserve = _clamp(int(window * 0.08), 2_048, 32_000)
    system_tool_reserve = _clamp(int(window * 0.06), 2_000, 24_000)
    retrieval_reserve = _clamp(int(window * 0.10), 2_000, 64_000)
    usable_input = max(1_000, window - output_reserve - system_tool_reserve - retrieval_reserve)
    settings = _CONTEXT_BUDGET_PRESETS[normalize_context_budget_preset(preset)]
    target = int(usable_input * float(settings["window_share"]))
    cap = settings.get("max_prompt_tokens")
    if isinstance(cap, int) and cap > 0:
        target = min(target, cap)
    return _clamp(target, 800, max(800, int(window * 0.82)))


def build_external_channel_memory_profile(
    *,
    context_budget_preset: str | None = None,
    retention_preset: str | None = None,
    context_window_tokens: int | None = None,
) -> MemoryPolicyProfile:
    base = _PROFILE_REGISTRY[EXTERNAL_CHANNEL_CUSTOMER_PROFILE]
    resolved_budget_preset = normalize_context_budget_preset(context_budget_preset)
    resolved_retention_preset = normalize_retention_preset(retention_preset)
    budget = _CONTEXT_BUDGET_PRESETS[resolved_budget_preset]
    context_budget = _context_budget_tokens(
        preset=resolved_budget_preset,
        context_window_tokens=context_window_tokens,
    )
    retention_days = _RETENTION_PRESET_DAYS[resolved_retention_preset]
    preserve_last_messages = _clamp(
        int(context_budget / 600),
        int(budget["preserve_last_messages"]),
        40,
    )
    return MemoryPolicyProfile(
        name=EXTERNAL_CHANNEL_CUSTOMER_PROFILE,
        max_prompt_tokens=context_budget,
        preserve_last_messages=preserve_last_messages,
        summary_trigger_messages=max(base.summary_trigger_messages, preserve_last_messages + 2),
        summary_trigger_tokens=max(base.summary_trigger_tokens, int(context_budget * 0.80)),
        semantic_retrieval_k=base.semantic_retrieval_k,
        max_summary_chars=_clamp(int(context_budget / 4), base.max_summary_chars, 12_000),
        max_business_plan_chars=_clamp(int(context_budget / 3), base.max_business_plan_chars, 18_000),
        max_recent_log_days=base.max_recent_log_days,
        max_transcript_items=_clamp(preserve_last_messages + 2, base.max_transcript_items, 48),
        raw_transcript_enabled=base.raw_transcript_enabled,
        semantic_write_enabled=base.semantic_write_enabled,
        semantic_read_enabled=base.semantic_read_enabled,
        retention=MemoryRetentionContract(
            raw_transcript_days=retention_days,
            summary_days=retention_days,
            semantic_days=retention_days,
            event_memory_days=retention_days,
            local_private_sync=base.retention.local_private_sync,
            cloud_safe_only=base.retention.cloud_safe_only,
        ),
    )


def _resolve_durable_run_profile() -> MemoryPolicyProfile:
    try:
        from server_modules import memory_service

        read_k = max(1, min(int(memory_service.runtime_memory.ORION_MEMORY_READ_K), 20))
        retention_days = max(1, min(int(memory_service.runtime_memory.ORION_MEMORY_RETENTION_DAYS_DEFAULT), 3650))
        max_text_chars = max(400, min(int(memory_service.runtime_memory.ORION_MEMORY_MAX_TEXT_CHARS), 12000))
    except Exception:
        read_k = _PROFILE_REGISTRY[DURABLE_RUN_PROFILE].semantic_retrieval_k
        retention_days = _PROFILE_REGISTRY[DURABLE_RUN_PROFILE].retention.semantic_days
        max_text_chars = 2400

    return MemoryPolicyProfile(
        name=DURABLE_RUN_PROFILE,
        max_prompt_tokens=max_text_chars,
        preserve_last_messages=_PROFILE_REGISTRY[DURABLE_RUN_PROFILE].preserve_last_messages,
        summary_trigger_messages=_PROFILE_REGISTRY[DURABLE_RUN_PROFILE].summary_trigger_messages,
        summary_trigger_tokens=_PROFILE_REGISTRY[DURABLE_RUN_PROFILE].summary_trigger_tokens,
        semantic_retrieval_k=read_k,
        max_summary_chars=_PROFILE_REGISTRY[DURABLE_RUN_PROFILE].max_summary_chars,
        max_business_plan_chars=_PROFILE_REGISTRY[DURABLE_RUN_PROFILE].max_business_plan_chars,
        max_recent_log_days=_PROFILE_REGISTRY[DURABLE_RUN_PROFILE].max_recent_log_days,
        max_transcript_items=_PROFILE_REGISTRY[DURABLE_RUN_PROFILE].max_transcript_items,
        raw_transcript_enabled=_PROFILE_REGISTRY[DURABLE_RUN_PROFILE].raw_transcript_enabled,
        semantic_write_enabled=_PROFILE_REGISTRY[DURABLE_RUN_PROFILE].semantic_write_enabled,
        semantic_read_enabled=_PROFILE_REGISTRY[DURABLE_RUN_PROFILE].semantic_read_enabled,
        retention=MemoryRetentionContract(
            raw_transcript_days=retention_days,
            summary_days=retention_days,
            semantic_days=retention_days,
            event_memory_days=retention_days,
            local_private_sync=_PROFILE_REGISTRY[DURABLE_RUN_PROFILE].retention.local_private_sync,
            cloud_safe_only=_PROFILE_REGISTRY[DURABLE_RUN_PROFILE].retention.cloud_safe_only,
        ),
    )


def build_model_aware_memory_policy(
    base_profile: MemoryPolicyProfile,
    *,
    context_window_tokens: int | None,
    runtime_lane: str = "direct_chat",
    trust_zone: str = "shared_cloud",
) -> MemoryPolicyProfile:
    window = int(context_window_tokens or 128_000)
    window = max(8_000, window)

    output_reserve = _clamp(int(window * 0.08), 2_048, 32_000)
    system_tool_reserve = _clamp(int(window * 0.06), 2_000, 24_000)
    retrieval_reserve = _clamp(int(window * 0.10), 2_000, 64_000)

    usable_input = max(4_000, window - output_reserve)
    conversation_budget = _clamp(
        usable_input - system_tool_reserve - retrieval_reserve,
        6_000,
        int(window * 0.65),
    )

    summary_trigger_tokens = max(1_500, int(conversation_budget * 0.80))
    recent_message_budget_tokens = min(int(conversation_budget * 0.35), 24_000)
    max_summary_chars = _clamp(int(window / 80), 1_800, 8_000)
    preserve_last_messages = _clamp(int(recent_message_budget_tokens / 600), 4, 20)
    max_transcript_items = _clamp(preserve_last_messages + 2, 8, 40)
    lane_token = str(runtime_lane or "").strip().lower() or "direct_chat"
    trust_zone_token = str(trust_zone or "").strip().lower() or "shared_cloud"
    cloud_safe_only = trust_zone_token == "shared_cloud"

    return MemoryPolicyProfile(
        name=base_profile.name,
        max_prompt_tokens=conversation_budget,
        preserve_last_messages=preserve_last_messages,
        summary_trigger_messages=max(base_profile.summary_trigger_messages, preserve_last_messages + 2),
        summary_trigger_tokens=summary_trigger_tokens,
        recent_message_budget_tokens=recent_message_budget_tokens,
        semantic_retrieval_k=base_profile.semantic_retrieval_k,
        max_summary_chars=max_summary_chars,
        output_reserve_tokens=output_reserve,
        system_tool_reserve_tokens=system_tool_reserve,
        retrieval_reserve_tokens=retrieval_reserve,
        max_business_plan_chars=base_profile.max_business_plan_chars,
        max_recent_log_days=base_profile.max_recent_log_days,
        max_transcript_items=max_transcript_items,
        raw_transcript_enabled=base_profile.raw_transcript_enabled,
        semantic_write_enabled=base_profile.semantic_write_enabled,
        semantic_read_enabled=base_profile.semantic_read_enabled,
        retention=MemoryRetentionContract(
            raw_transcript_days=base_profile.retention.raw_transcript_days,
            summary_days=base_profile.retention.summary_days,
            semantic_days=base_profile.retention.semantic_days,
            event_memory_days=base_profile.retention.event_memory_days,
            local_private_sync=base_profile.retention.local_private_sync,
            cloud_safe_only=base_profile.retention.cloud_safe_only or cloud_safe_only or lane_token != "direct_chat",
        ),
    )
