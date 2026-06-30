from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field

from server_modules import config_defaults_service
from server_modules import conversation_memory_policy
from server_modules import deployed_agent_runtime_contract_service


KNOWN_OWNER_METADATA_KEYS = frozenset(
    {
        "paused_message",
        "daily_message_limit",
        "upgrade_cta_url",
        "upgrade_cta_label",
        "memory_enabled",
        "context_budget_preset",
        "retention_preset",
        "health_safety_enabled",
        "health_safety_assistant_name",
        "monthly_cost_cap_usd",
        "public_intro",
        "public_core_value",
        "platform_cta_label",
        "platform_cta_url",
        "public_source",
        "escalation_preset",
        "handoff_mode",
        "owner_notification_destination",
        "provider",
        "model",
        "selected_tool_ids",
        "config_schema_version",
        "runtime_placement",
        "runtime_supply",
        "runtime_supplier",
        "computer_automation",
        "studio_agent_mode",
    }
)

STUDIO_TOOL_SCOPE_ALLOWED_IDS = (
    "web_search",
    "http_request",
    "spreadsheet_read",
    "spreadsheet_append",
    "gmail_send",
    "calendar_write",
)

KNOWN_OPERATIONAL_STATE_KEYS = frozenset(
    {
        "schema_version",
        "deployment_state",
        "last_deployed_at",
        "last_paused_at",
        "current_budget_cycle",
    }
)


def _coerce_dict(value: Any) -> Dict[str, Any]:
    return dict(value or {}) if isinstance(value, dict) else {}


def _text(value: Any, *, default: str = "") -> str:
    token = str(value or "").strip()
    return token or default


def _optional_text(value: Any) -> Optional[str]:
    token = _text(value)
    return token or None


def _normalize_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"1", "true", "yes", "on", "enabled"}


def _normalize_positive_int(value: Any, *, label: str) -> Optional[int]:
    if value is None:
        return None
    if isinstance(value, bool):
        raise ValueError(f"{label} must be a positive integer.")
    token = _text(value)
    if not token:
        return None
    try:
        parsed = int(token)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} must be a positive integer.") from exc
    return parsed if parsed > 0 else None


def _normalize_positive_usd(value: Any, *, label: str) -> Optional[float]:
    if value is None:
        return None
    if isinstance(value, bool):
        raise ValueError(f"{label} must be a positive USD amount.")
    token = _text(value)
    if not token:
        return None
    try:
        parsed = round(float(token), 6)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} must be a positive USD amount.") from exc
    return parsed if parsed > 0 else None


def _normalize_runtime_target(value: Any) -> str:
    token = _text(value).lower()
    if token in {"cloud", "cloud_default", "hosted_secure", "cloud_only"}:
        return "cloud"
    if token in {"local", "local_secure", "local_only", "local_companion"}:
        return "local"
    if token in {"self_hosted", "self_hosted_business", "self_hosted_business_node", "customer_hosted"}:
        return "self_hosted"
    if token in {"device", "desktop", "privileged_device"}:
        return "device"
    return token or config_defaults_service.default_deployed_agent_runtime_target()


def _normalize_runtime_placement(value: Any, *, runtime_target: Any = None) -> str:
    return deployed_agent_runtime_contract_service.normalize_runtime_placement(
        value,
        runtime_target=runtime_target,
    )


def _normalize_tool_ids(value: Any) -> List[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError("selected tools must be a list.")
    allowed = set(STUDIO_TOOL_SCOPE_ALLOWED_IDS)
    normalized: List[str] = []
    seen: set[str] = set()
    for item in value:
        token = _text(item).lower()
        if not token or token in seen:
            continue
        if token not in allowed:
            raise ValueError(f"Unsupported Studio tool selection: {token}.")
        seen.add(token)
        normalized.append(token)
    return normalized


def _normalize_deployment_state(value: Any) -> str:
    token = _text(value).lower()
    if token in {"draft", "private_test", "ready_for_review", "live", "paused", "suspended", "archived"}:
        return token
    return config_defaults_service.default_deployed_agent_deployment_state()


class DeployedAgentChannelConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    endpoint_key: Optional[str] = None
    is_inbound_owner: bool = False
    connector_id: Optional[str] = None
    credential_id: Optional[str] = None
    webhook_path: Optional[str] = None
    chat_id: Optional[str] = None
    bot_username: Optional[str] = None
    phone_number: Optional[str] = None
    from_number: Optional[str] = None
    to_number: Optional[str] = None
    delivery_mode: Optional[str] = None


class DeployedAgentCustomerPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    paused_message: Optional[str] = None
    daily_message_limit: Optional[int] = None
    upgrade_cta_url: Optional[str] = None
    upgrade_cta_label: Optional[str] = None
    public_intro: Optional[str] = None
    public_core_value: Optional[str] = None
    public_start_cta_label: Optional[str] = None
    public_start_cta_url: Optional[str] = None


class DeployedAgentMemoryPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    memory_enabled: bool = False
    context_budget_preset: str = Field(default_factory=config_defaults_service.default_context_budget_preset)
    retention_preset: str = Field(default_factory=config_defaults_service.default_retention_preset)


class DeployedAgentSafetyPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    health_safety_enabled: bool = False
    assistant_name: Optional[str] = None


class DeployedAgentEscalationPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    preset: str = Field(default_factory=config_defaults_service.default_deployed_agent_escalation_preset)
    handoff_mode: str = Field(default_factory=config_defaults_service.default_deployed_agent_handoff_mode)
    owner_notification_destination: Optional[str] = None


class DeployedAgentCommercePolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    monthly_cost_cap_usd: Optional[float] = None


class DeployedAgentToolPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled_tools: List[str] = Field(default_factory=list)


class DeployedAgentComputerAutomationPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    runtime_class: Optional[str] = None
    allowed_domains: List[str] = Field(default_factory=list)
    max_concurrent_sessions: int = 0
    daily_budget_usd: Optional[float] = None
    monthly_budget_usd: Optional[float] = None
    runtime_access_mode: str = "default_guarded"
    requires_owner_approval: bool = True
    idle_timeout_seconds: int = 0
    max_session_runtime_seconds: int = 0
    isolation_boundary: str = "isolated_sandbox"
    inherit_host_environment: bool = False
    filesystem_default_access: str = "none"
    allow_downloads: bool = False
    allow_software_install: bool = False
    terminal_command_policy: str = "blocked"
    sensitive_action_confirmation_required: bool = True
    screenshot_capture_enabled: bool = False
    session_recording_policy: str = "metadata_only"
    emergency_stop_enabled: bool = True
    required_owner_approval_actions: List[str] = Field(default_factory=list)


class DeployedAgentConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: int = Field(default_factory=config_defaults_service.config_schema_version)
    name: str
    avatar: Optional[str] = None
    persona: str = ""
    system_prompt: str = ""
    runtime_placement: str = Field(
        default_factory=lambda: deployed_agent_runtime_contract_service.RUNTIME_PLACEMENT_MANAGED_CLOUD
    )
    studio_agent_mode: str = Field(
        default_factory=lambda: deployed_agent_runtime_contract_service.STUDIO_AGENT_MODE_TEXT
    )
    runtime_supply: Dict[str, Any] = Field(default_factory=dict)
    runtime_target: str = Field(default_factory=config_defaults_service.default_deployed_agent_runtime_target)
    billing_plan: str = Field(default_factory=config_defaults_service.default_deployed_agent_billing_plan)
    provider: Optional[str] = None
    model: Optional[str] = None
    runtime_profile_id: Optional[str] = None
    knowledge_sources: List[Dict[str, Any]] = Field(default_factory=list)
    channels: Dict[str, DeployedAgentChannelConfig] = Field(default_factory=dict)
    customer_policy: DeployedAgentCustomerPolicy = Field(default_factory=DeployedAgentCustomerPolicy)
    memory_policy: DeployedAgentMemoryPolicy = Field(default_factory=DeployedAgentMemoryPolicy)
    safety_policy: DeployedAgentSafetyPolicy = Field(default_factory=DeployedAgentSafetyPolicy)
    escalation_policy: DeployedAgentEscalationPolicy = Field(default_factory=DeployedAgentEscalationPolicy)
    commerce_policy: DeployedAgentCommercePolicy = Field(default_factory=DeployedAgentCommercePolicy)
    tool_policy: DeployedAgentToolPolicy = Field(default_factory=DeployedAgentToolPolicy)
    computer_automation: DeployedAgentComputerAutomationPolicy = Field(default_factory=DeployedAgentComputerAutomationPolicy)


class DeployedAgentOperationalState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: int = Field(default_factory=config_defaults_service.config_schema_version)
    deployment_state: str = Field(default_factory=config_defaults_service.default_deployed_agent_deployment_state)
    last_deployed_at: Optional[str] = None
    last_paused_at: Optional[str] = None
    current_budget_cycle: Dict[str, Any] = Field(default_factory=dict)


def normalize_deployed_agent_channels(value: Any) -> Dict[str, Dict[str, Any]]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError("channels must be a keyed deployment config object.")
    normalized: Dict[str, Dict[str, Any]] = {}
    for raw_key, raw_config in value.items():
        channel_key = _text(raw_key).lower()
        if not channel_key:
            continue
        if isinstance(raw_config, bool):
            payload = {"enabled": bool(raw_config)}
        elif isinstance(raw_config, dict):
            payload = dict(raw_config)
            if "enabled" not in payload:
                payload["enabled"] = True
        else:
            raise ValueError("Each channel config must be a boolean or object.")
        normalized[channel_key] = DeployedAgentChannelConfig.model_validate(payload).model_dump(
            exclude_none=True
        )
    return normalized


def deployed_agent_config_from_record(
    record: Optional[Dict[str, Any]],
    *,
    runtime_profile_id: Any = None,
) -> DeployedAgentConfig:
    payload = dict(record or {})
    metadata = _coerce_dict(payload.get("metadata"))
    config_payload = _coerce_dict(payload.get("config"))
    customer_policy_payload = _coerce_dict(config_payload.get("customer_policy"))
    memory_policy_payload = _coerce_dict(config_payload.get("memory_policy"))
    safety_policy_payload = _coerce_dict(config_payload.get("safety_policy"))
    escalation_policy_payload = _coerce_dict(config_payload.get("escalation_policy"))
    commerce_policy_payload = _coerce_dict(config_payload.get("commerce_policy"))
    tool_policy_payload = _coerce_dict(config_payload.get("tool_policy"))
    computer_automation_payload = _coerce_dict(
        config_payload.get("computer_automation")
        if "computer_automation" in config_payload
        else metadata.get("computer_automation")
    )
    runtime_supply_payload = _coerce_dict(
        config_payload.get("runtime_supply")
        if "runtime_supply" in config_payload
        else metadata.get("runtime_supply")
    )
    runtime_target_value = (
        config_payload.get("runtime_target")
        if "runtime_target" in config_payload
        else payload.get("runtime_target")
    )
    runtime_placement_value = (
        config_payload.get("runtime_placement")
        if "runtime_placement" in config_payload
        else metadata.get("runtime_placement")
    )
    runtime_supplier_value = (
        config_payload.get("runtime_supplier")
        if "runtime_supplier" in config_payload
        else metadata.get("runtime_supplier")
    )
    explicit_studio_mode = config_payload.get("studio_agent_mode") if "studio_agent_mode" in config_payload else None
    has_runtime_hints = any(
        key in config_payload
        for key in ("runtime_placement", "runtime_target", "runtime_supplier")
    )
    studio_mode_hint = explicit_studio_mode
    if studio_mode_hint is None and not has_runtime_hints:
        studio_mode_hint = metadata.get("studio_agent_mode")
    studio_agent_mode = deployed_agent_runtime_contract_service.resolve_studio_agent_mode(
        studio_mode_hint,
        runtime_placement=runtime_placement_value,
        runtime_target=runtime_target_value,
        runtime_supplier=runtime_supplier_value,
    )
    mode_contract = deployed_agent_runtime_contract_service.studio_agent_mode_contract(studio_agent_mode)
    runtime_placement = _normalize_runtime_placement(mode_contract["placement"]["kind"])
    runtime_target = _normalize_runtime_target(mode_contract["placement"]["runtime_target"])
    customer_policy = {
        "paused_message": _optional_text(
            customer_policy_payload.get("paused_message")
            if "paused_message" in customer_policy_payload
            else metadata.get("paused_message")
        ),
        "daily_message_limit": _normalize_positive_int(
            customer_policy_payload.get("daily_message_limit")
            if "daily_message_limit" in customer_policy_payload
            else metadata.get("daily_message_limit"),
            label="daily_message_limit",
        ),
        "upgrade_cta_url": _optional_text(
            customer_policy_payload.get("upgrade_cta_url")
            if "upgrade_cta_url" in customer_policy_payload
            else metadata.get("upgrade_cta_url")
        ),
        "upgrade_cta_label": _optional_text(
            customer_policy_payload.get("upgrade_cta_label")
            if "upgrade_cta_label" in customer_policy_payload
            else metadata.get("upgrade_cta_label")
        ),
        "public_intro": _optional_text(
            customer_policy_payload.get("public_intro")
            if "public_intro" in customer_policy_payload
            else metadata.get("public_intro")
        ),
        "public_core_value": _optional_text(
            customer_policy_payload.get("public_core_value")
            if "public_core_value" in customer_policy_payload
            else metadata.get("public_core_value")
        ),
        "public_start_cta_label": _optional_text(
            customer_policy_payload.get("public_start_cta_label")
            if "public_start_cta_label" in customer_policy_payload
            else metadata.get("platform_cta_label")
        ),
        "public_start_cta_url": _optional_text(
            customer_policy_payload.get("public_start_cta_url")
            if "public_start_cta_url" in customer_policy_payload
            else metadata.get("platform_cta_url")
        ),
    }
    if customer_policy["daily_message_limit"] is not None:
        if not customer_policy["upgrade_cta_url"]:
            raise ValueError("upgrade_cta_url is required when daily_message_limit is set.")
        if not customer_policy["upgrade_cta_label"]:
            raise ValueError("upgrade_cta_label is required when daily_message_limit is set.")
    return DeployedAgentConfig.model_validate(
        {
            "schema_version": int(
                config_payload.get("schema_version")
                or metadata.get("config_schema_version")
                or config_defaults_service.config_schema_version()
            ),
            "name": _text(config_payload.get("name") if "name" in config_payload else payload.get("name")),
            "avatar": _optional_text(config_payload.get("avatar") if "avatar" in config_payload else payload.get("avatar")),
            "persona": _text(config_payload.get("persona") if "persona" in config_payload else payload.get("persona")),
            "system_prompt": _text(
                config_payload.get("system_prompt") if "system_prompt" in config_payload else payload.get("system_prompt")
            ),
            "runtime_placement": runtime_placement,
            "studio_agent_mode": studio_agent_mode,
            "runtime_supply": deployed_agent_runtime_contract_service.normalize_runtime_supply_contract(
                runtime_supply_payload,
                studio_agent_mode=studio_agent_mode,
                runtime_supplier=runtime_supplier_value,
                runtime_placement=runtime_placement,
                runtime_target=runtime_target,
                computer_automation=computer_automation_payload,
                public_tier=metadata.get("public_tier") or metadata.get("model_tier") or metadata.get("empyralis_model_tier"),
                billing_source=metadata.get("billing_source"),
                provider=config_payload.get("provider") if "provider" in config_payload else metadata.get("provider"),
                model=config_payload.get("model") if "model" in config_payload else metadata.get("model"),
            ),
            "runtime_target": runtime_target,
            "billing_plan": _text(config_payload.get("billing_plan") if "billing_plan" in config_payload else payload.get("billing_plan"))
            or config_defaults_service.default_deployed_agent_billing_plan(),
            "provider": _optional_text(config_payload.get("provider") if "provider" in config_payload else metadata.get("provider")),
            "model": _optional_text(config_payload.get("model") if "model" in config_payload else metadata.get("model")),
            "runtime_profile_id": _optional_text(
                config_payload.get("runtime_profile_id")
                if "runtime_profile_id" in config_payload
                else runtime_profile_id
            ),
            "knowledge_sources": list(
                config_payload.get("knowledge_sources")
                if "knowledge_sources" in config_payload
                else payload.get("knowledge_sources")
                or []
            ),
            "channels": normalize_deployed_agent_channels(
                config_payload.get("channels")
                if "channels" in config_payload
                else payload.get("channels")
                or {}
            ),
            "customer_policy": customer_policy,
            "memory_policy": {
                "memory_enabled": _normalize_bool(
                    memory_policy_payload.get("memory_enabled")
                    if "memory_enabled" in memory_policy_payload
                    else metadata.get("memory_enabled")
                ),
                "context_budget_preset": conversation_memory_policy.normalize_context_budget_preset(
                    memory_policy_payload.get("context_budget_preset")
                    if "context_budget_preset" in memory_policy_payload
                    else metadata.get("context_budget_preset")
                ),
                "retention_preset": conversation_memory_policy.normalize_retention_preset(
                    memory_policy_payload.get("retention_preset")
                    if "retention_preset" in memory_policy_payload
                    else metadata.get("retention_preset")
                ),
            },
            "safety_policy": {
                "health_safety_enabled": _normalize_bool(
                    safety_policy_payload.get("health_safety_enabled")
                    if "health_safety_enabled" in safety_policy_payload
                    else metadata.get("health_safety_enabled")
                ),
                "assistant_name": _optional_text(
                    safety_policy_payload.get("assistant_name")
                    if "assistant_name" in safety_policy_payload
                    else metadata.get("health_safety_assistant_name")
                ),
            },
            "escalation_policy": {
                "preset": _text(
                    escalation_policy_payload.get("preset")
                    if "preset" in escalation_policy_payload
                    else metadata.get("escalation_preset"),
                    default=config_defaults_service.default_deployed_agent_escalation_preset(),
                ),
                "handoff_mode": _text(
                    escalation_policy_payload.get("handoff_mode")
                    if "handoff_mode" in escalation_policy_payload
                    else metadata.get("handoff_mode"),
                    default=config_defaults_service.default_deployed_agent_handoff_mode(),
                ),
                "owner_notification_destination": _optional_text(
                    escalation_policy_payload.get("owner_notification_destination")
                    if "owner_notification_destination" in escalation_policy_payload
                    else metadata.get("owner_notification_destination")
                ),
            },
            "commerce_policy": {
                "monthly_cost_cap_usd": _normalize_positive_usd(
                    commerce_policy_payload.get("monthly_cost_cap_usd")
                    if "monthly_cost_cap_usd" in commerce_policy_payload
                    else metadata.get("monthly_cost_cap_usd"),
                    label="monthly_cost_cap_usd",
                ),
            },
            "tool_policy": {
                "enabled_tools": _normalize_tool_ids(
                    tool_policy_payload.get("enabled_tools")
                    if "enabled_tools" in tool_policy_payload
                    else metadata.get("selected_tool_ids")
                ),
            },
            "computer_automation": deployed_agent_runtime_contract_service.normalize_computer_automation_config(
                computer_automation_payload
            ),
        }
    )


def deployed_agent_operational_state_from_record(
    record: Optional[Dict[str, Any]],
) -> DeployedAgentOperationalState:
    payload = dict(record or {})
    raw_state = _coerce_dict(payload.get("operational_state"))
    raw_budget_cycle = _coerce_dict(raw_state.get("current_budget_cycle"))
    if not raw_budget_cycle:
        raw_budget_cycle = _coerce_dict(_coerce_dict(payload.get("metadata")).get("current_budget_cycle"))
    return DeployedAgentOperationalState.model_validate(
        {
            "schema_version": int(raw_state.get("schema_version") or config_defaults_service.config_schema_version()),
            "deployment_state": _normalize_deployment_state(
                raw_state.get("deployment_state") or payload.get("deployment_state")
            ),
            "last_deployed_at": _optional_text(
                raw_state.get("last_deployed_at") or payload.get("last_deployed_at")
            ),
            "last_paused_at": _optional_text(
                raw_state.get("last_paused_at") or payload.get("last_paused_at")
            ),
            "current_budget_cycle": raw_budget_cycle,
        }
    )


def metadata_from_deployed_agent_config(
    config: DeployedAgentConfig,
    *,
    existing_metadata: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    preserved = {
        key: value
        for key, value in _coerce_dict(existing_metadata).items()
        if key not in KNOWN_OWNER_METADATA_KEYS and key != "current_budget_cycle"
    }
    payload = {
        **preserved,
        "config_schema_version": config.schema_version,
        "runtime_placement": config.runtime_placement,
        "studio_agent_mode": config.studio_agent_mode,
        "runtime_supply": config.runtime_supply,
        "computer_automation": config.computer_automation.model_dump(exclude_none=True),
        "memory_enabled": bool(config.memory_policy.memory_enabled),
        "context_budget_preset": config.memory_policy.context_budget_preset,
        "retention_preset": config.memory_policy.retention_preset,
        "health_safety_enabled": bool(config.safety_policy.health_safety_enabled),
        "escalation_preset": config.escalation_policy.preset,
        "handoff_mode": config.escalation_policy.handoff_mode,
    }
    if config.customer_policy.paused_message:
        payload["paused_message"] = config.customer_policy.paused_message
    if config.customer_policy.daily_message_limit is not None:
        payload["daily_message_limit"] = int(config.customer_policy.daily_message_limit)
        payload["upgrade_cta_url"] = config.customer_policy.upgrade_cta_url
        payload["upgrade_cta_label"] = config.customer_policy.upgrade_cta_label
    if config.customer_policy.public_intro:
        payload["public_intro"] = config.customer_policy.public_intro
    if config.customer_policy.public_core_value:
        payload["public_core_value"] = config.customer_policy.public_core_value
    if config.customer_policy.public_start_cta_label:
        payload["platform_cta_label"] = config.customer_policy.public_start_cta_label
    if config.customer_policy.public_start_cta_url:
        payload["platform_cta_url"] = config.customer_policy.public_start_cta_url
    if config.commerce_policy.monthly_cost_cap_usd is not None:
        payload["monthly_cost_cap_usd"] = round(float(config.commerce_policy.monthly_cost_cap_usd), 6)
    if config.safety_policy.assistant_name:
        payload["health_safety_assistant_name"] = config.safety_policy.assistant_name
    if config.escalation_policy.owner_notification_destination:
        payload["owner_notification_destination"] = config.escalation_policy.owner_notification_destination
    if config.provider:
        payload["provider"] = config.provider
    if config.model:
        payload["model"] = config.model
    if config.tool_policy.enabled_tools:
        payload["selected_tool_ids"] = list(config.tool_policy.enabled_tools)
    return payload


def operational_state_payload(state: DeployedAgentOperationalState) -> Dict[str, Any]:
    return state.model_dump(exclude_none=True)
