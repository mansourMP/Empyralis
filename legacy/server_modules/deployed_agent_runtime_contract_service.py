from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse


RUNTIME_PLACEMENT_MANAGED_CLOUD = "managed_cloud"
RUNTIME_PLACEMENT_HOSTED_HARDWARE_POOL = "hosted_hardware_pool"
RUNTIME_PLACEMENT_CUSTOMER_LOCAL = "customer_local"
RUNTIME_PLACEMENT_CUSTOMER_HOSTED = "customer_hosted"
RUNTIME_PLACEMENTS = {
    RUNTIME_PLACEMENT_MANAGED_CLOUD,
    RUNTIME_PLACEMENT_HOSTED_HARDWARE_POOL,
    RUNTIME_PLACEMENT_CUSTOMER_LOCAL,
    RUNTIME_PLACEMENT_CUSTOMER_HOSTED,
}

RUNTIME_SUPPLIER_EMPYRALIS = "empyralis"
RUNTIME_SUPPLIER_CUSTOMER = "customer"
RUNTIME_SUPPLIER_THIRD_PARTY_CERTIFIED = "third_party_certified"
RUNTIME_SUPPLIERS = {
    RUNTIME_SUPPLIER_EMPYRALIS,
    RUNTIME_SUPPLIER_CUSTOMER,
    RUNTIME_SUPPLIER_THIRD_PARTY_CERTIFIED,
}

COMPUTER_AUTOMATION_CLASSES = {
    "virtual_browser",
    "virtual_desktop",
    "virtual_code_sandbox",
    "local_browser",
    "local_desktop",
}

RUNTIME_ACCESS_MODE_DEFAULT_GUARDED = "default_guarded"
RUNTIME_ACCESS_MODE_FULL_ACCESS = "full_access"
RUNTIME_ACCESS_MODE_CUSTOM = "custom"
RUNTIME_ACCESS_MODES = {
    RUNTIME_ACCESS_MODE_DEFAULT_GUARDED,
    RUNTIME_ACCESS_MODE_FULL_ACCESS,
    RUNTIME_ACCESS_MODE_CUSTOM,
}

COMPUTER_SAFETY_REQUIRED_OWNER_APPROVAL_ACTIONS = {
    "send_external_messages",
    "make_purchases",
    "delete_data",
    "change_permissions",
    "enter_secrets",
    "install_software",
    "run_unknown_scripts",
}
COMPUTER_SAFETY_SAFE_FILESYSTEM_DEFAULTS = {
    "none",
    "session_scoped",
    "workspace_scoped",
}
COMPUTER_SAFETY_ALLOWED_TERMINAL_POLICIES = {
    "allow",
    "blocked",
    "allowlist",
    "review_required",
}

RUNTIME_PLACEMENT_TARGETS = {
    RUNTIME_PLACEMENT_MANAGED_CLOUD: "cloud",
    RUNTIME_PLACEMENT_HOSTED_HARDWARE_POOL: "cloud",
    RUNTIME_PLACEMENT_CUSTOMER_LOCAL: "local",
    RUNTIME_PLACEMENT_CUSTOMER_HOSTED: "self_hosted",
}

STUDIO_AGENT_MODE_TEXT = "text_agent"
STUDIO_AGENT_MODE_CLOUD_COMPUTER = "cloud_computer_agent"
STUDIO_AGENT_MODE_MY_COMPUTER = "my_computer_agent"
STUDIO_AGENT_MODE_SELF_HOSTED = "self_hosted_agent"
STUDIO_AGENT_MODES = {
    STUDIO_AGENT_MODE_TEXT,
    STUDIO_AGENT_MODE_CLOUD_COMPUTER,
    STUDIO_AGENT_MODE_MY_COMPUTER,
    STUDIO_AGENT_MODE_SELF_HOSTED,
}
STUDIO_AGENT_MODE_ALIASES = {
    "text": STUDIO_AGENT_MODE_TEXT,
    "text_agent": STUDIO_AGENT_MODE_TEXT,
    "chat": STUDIO_AGENT_MODE_TEXT,
    "chat_agent": STUDIO_AGENT_MODE_TEXT,
    "cloud_computer": STUDIO_AGENT_MODE_CLOUD_COMPUTER,
    "cloud_computer_agent": STUDIO_AGENT_MODE_CLOUD_COMPUTER,
    "cloud_agent": STUDIO_AGENT_MODE_CLOUD_COMPUTER,
    "my_computer": STUDIO_AGENT_MODE_MY_COMPUTER,
    "my_computer_agent": STUDIO_AGENT_MODE_MY_COMPUTER,
    "local_agent": STUDIO_AGENT_MODE_MY_COMPUTER,
    "self_hosted": STUDIO_AGENT_MODE_SELF_HOSTED,
    "self_hosted_agent": STUDIO_AGENT_MODE_SELF_HOSTED,
    "customer_hosted_agent": STUDIO_AGENT_MODE_SELF_HOSTED,
}
STUDIO_AGENT_MODE_PROFILES = {
    STUDIO_AGENT_MODE_TEXT: {
        "placement": RUNTIME_PLACEMENT_MANAGED_CLOUD,
        "supplier_kind": RUNTIME_SUPPLIER_EMPYRALIS,
        "allowed_capabilities": [
            "chat",
            "approved_tools",
            "knowledge_sources",
            "memory",
            "channels",
        ],
        "computer_allowed": False,
    },
    STUDIO_AGENT_MODE_CLOUD_COMPUTER: {
        "placement": RUNTIME_PLACEMENT_HOSTED_HARDWARE_POOL,
        "supplier_kind": RUNTIME_SUPPLIER_EMPYRALIS,
        "allowed_capabilities": [
            "chat",
            "approved_tools",
            "knowledge_sources",
            "memory",
            "channels",
            "isolated_computer",
            "browser_automation",
            "code_execution",
            "file_artifacts",
        ],
        "computer_allowed": True,
    },
    STUDIO_AGENT_MODE_MY_COMPUTER: {
        "placement": RUNTIME_PLACEMENT_CUSTOMER_LOCAL,
        "supplier_kind": RUNTIME_SUPPLIER_CUSTOMER,
        "allowed_capabilities": [
            "chat",
            "approved_tools",
            "knowledge_sources",
            "memory",
            "channels",
            "local_companion",
            "local_files",
            "local_browser",
        ],
        "computer_allowed": True,
    },
    STUDIO_AGENT_MODE_SELF_HOSTED: {
        "placement": RUNTIME_PLACEMENT_CUSTOMER_HOSTED,
        "supplier_kind": RUNTIME_SUPPLIER_CUSTOMER,
        "allowed_capabilities": [
            "chat",
            "approved_tools",
            "knowledge_sources",
            "memory",
            "channels",
            "self_hosted_runtime",
            "remote_files",
            "remote_jobs",
        ],
        "computer_allowed": True,
    },
}

STUDIO_AI_SOURCE_EMPYRALIS_CREDITS = "empyralis_credits"
STUDIO_AI_SOURCE_WORKSPACE_API_KEY = "workspace_api_key"
STUDIO_AI_SOURCE_LOCAL_MODEL = "local_model"
STUDIO_AI_SOURCE_SUBSCRIPTION_PASSTHROUGH = "subscription_passthrough"
STUDIO_AI_SOURCES = {
    STUDIO_AI_SOURCE_EMPYRALIS_CREDITS,
    STUDIO_AI_SOURCE_WORKSPACE_API_KEY,
    STUDIO_AI_SOURCE_LOCAL_MODEL,
    STUDIO_AI_SOURCE_SUBSCRIPTION_PASSTHROUGH,
}
STUDIO_AI_SOURCE_ALIASES = {
    "empyralis": STUDIO_AI_SOURCE_EMPYRALIS_CREDITS,
    "empyralis_credit": STUDIO_AI_SOURCE_EMPYRALIS_CREDITS,
    "empyralis_credits": STUDIO_AI_SOURCE_EMPYRALIS_CREDITS,
    "hosted": STUDIO_AI_SOURCE_EMPYRALIS_CREDITS,
    "platform": STUDIO_AI_SOURCE_EMPYRALIS_CREDITS,
    "platform_credit": STUDIO_AI_SOURCE_EMPYRALIS_CREDITS,
    "platform_credits": STUDIO_AI_SOURCE_EMPYRALIS_CREDITS,
    "api_key": STUDIO_AI_SOURCE_WORKSPACE_API_KEY,
    "byok": STUDIO_AI_SOURCE_WORKSPACE_API_KEY,
    "my_api_key": STUDIO_AI_SOURCE_WORKSPACE_API_KEY,
    "my_ai_account": STUDIO_AI_SOURCE_WORKSPACE_API_KEY,
    "workspace_api_key": STUDIO_AI_SOURCE_WORKSPACE_API_KEY,
    "workspace_key": STUDIO_AI_SOURCE_WORKSPACE_API_KEY,
    "local": STUDIO_AI_SOURCE_LOCAL_MODEL,
    "local_ai": STUDIO_AI_SOURCE_LOCAL_MODEL,
    "local_model": STUDIO_AI_SOURCE_LOCAL_MODEL,
    "subscription": STUDIO_AI_SOURCE_SUBSCRIPTION_PASSTHROUGH,
    "subscription_passthrough": STUDIO_AI_SOURCE_SUBSCRIPTION_PASSTHROUGH,
    "third_party_subscription": STUDIO_AI_SOURCE_SUBSCRIPTION_PASSTHROUGH,
}

STUDIO_BYOK_FIRST_PROVIDERS = {
    "openrouter",
    "groq",
    "azure_openai",
    "custom_openai_compatible",
}
STUDIO_SUBSCRIPTION_PROVIDER_IDS = {
    "codex_cli",
    "claude_code_cli",
    "openai-codex",
}
STUDIO_LOCAL_PROVIDER_IDS = {
    "ollama",
    "local",
    "local_model",
    "local_companion",
}

STUDIO_AGENT_MODE_DEPLOY_TARGETS = {
    STUDIO_AGENT_MODE_TEXT: "cloud_default",
    STUDIO_AGENT_MODE_CLOUD_COMPUTER: "sage_cloud_computer",
    STUDIO_AGENT_MODE_MY_COMPUTER: "local_companion",
    STUDIO_AGENT_MODE_SELF_HOSTED: "self_host_runtime",
}

DEFAULT_AGENT_WORKSPACE_BASE = "~/.empyralis/agents"


def _text(value: Any, *, default: str = "") -> str:
    token = str(value or "").strip()
    return token or default


def _bool(value: Any, *, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    token = str(value or "").strip().lower()
    if not token:
        return default
    return token in {"1", "true", "yes", "on", "enabled"}


def normalize_runtime_access_mode(value: Any = None, *, requires_owner_approval: Any = None) -> str:
    token = _text(value).lower().replace("-", "_").replace(" ", "_")
    if token == RUNTIME_ACCESS_MODE_CUSTOM:
        return RUNTIME_ACCESS_MODE_CUSTOM
    if token in {"default_guarded", "guarded", "default"}:
        return RUNTIME_ACCESS_MODE_DEFAULT_GUARDED
    return RUNTIME_ACCESS_MODE_DEFAULT_GUARDED


def normalize_studio_ai_source(value: Any = None, *, fallback: Any = None) -> Dict[str, Any]:
    payload = value if isinstance(value, dict) else {}
    requested = (
        payload.get("kind")
        or payload.get("source")
        or payload.get("payer")
        or value
        or fallback
        or STUDIO_AI_SOURCE_EMPYRALIS_CREDITS
    )
    source = STUDIO_AI_SOURCE_ALIASES.get(_text(requested).lower(), STUDIO_AI_SOURCE_EMPYRALIS_CREDITS)
    payer = {
        STUDIO_AI_SOURCE_EMPYRALIS_CREDITS: "platform_credits",
        STUDIO_AI_SOURCE_WORKSPACE_API_KEY: "BYOK",
        STUDIO_AI_SOURCE_LOCAL_MODEL: "local",
        STUDIO_AI_SOURCE_SUBSCRIPTION_PASSTHROUGH: "subscription_passthrough",
    }[source]
    return {
        "kind": source,
        "payer": payer,
        "requires_vault_provider": source == STUDIO_AI_SOURCE_WORKSPACE_API_KEY,
        "requires_local_runtime": source == STUDIO_AI_SOURCE_LOCAL_MODEL,
        "uses_platform_credits": source == STUDIO_AI_SOURCE_EMPYRALIS_CREDITS,
        "uses_subscription_passthrough": source == STUDIO_AI_SOURCE_SUBSCRIPTION_PASSTHROUGH,
    }


def validate_studio_ai_source_provider_runtime(
    *,
    ai_source: Any = None,
    provider: Any = None,
    model: Any = None,
    runtime_placement: Any = None,
    runtime_target: Any = None,
    runtime_supply: Any = None,
    provider_scopes: Any = None,
    pricing_known: Optional[bool] = None,
) -> Dict[str, Any]:
    """Validate the deployed-agent AI payer/source against provider and runtime policy."""
    source = normalize_studio_ai_source(ai_source)
    source_kind = source["kind"]
    provider_id = _text(provider).lower()
    model_id = _text(model)
    scopes = {
        _text(item).lower()
        for item in list(provider_scopes or [])
        if _text(item)
    }
    supply = runtime_supply if isinstance(runtime_supply, dict) else {}
    placement = normalize_runtime_placement(runtime_placement, runtime_target=runtime_target)
    target = _text(runtime_target).lower() or runtime_target_for_placement(placement)
    supply_ai_source = normalize_studio_ai_source(supply.get("ai_source") if isinstance(supply.get("ai_source"), dict) else None)
    errors: List[str] = []

    if provider_id == "custom_openai_compatible" and not model_id:
        errors.append("custom_openai_compatible requires an explicit model id.")
    if provider_id == "azure_openai" and not model_id:
        errors.append("azure_openai requires an explicit deployment/model id.")
    if supply.get("ai_source") and supply_ai_source["kind"] != source_kind:
        errors.append(
            f"runtime_supply.ai_source must be {source_kind}, received {supply_ai_source['kind']}."
        )

    requires_local_runtime = placement == RUNTIME_PLACEMENT_CUSTOMER_LOCAL or target == "local"
    is_byok_first = provider_id in STUDIO_BYOK_FIRST_PROVIDERS
    is_subscription_provider = provider_id in STUDIO_SUBSCRIPTION_PROVIDER_IDS
    is_local_provider = provider_id in STUDIO_LOCAL_PROVIDER_IDS
    subscription_cli_override = (
        source_kind == STUDIO_AI_SOURCE_SUBSCRIPTION_PASSTHROUGH
        and is_subscription_provider
        and requires_local_runtime
    )

    if provider_id and scopes and "studio_safe" not in scopes and not subscription_cli_override:
        errors.append(f"Provider '{provider_id}' is not available for Studio agents.")

    if source_kind == STUDIO_AI_SOURCE_EMPYRALIS_CREDITS:
        if is_byok_first:
            errors.append(f"{provider_id} is BYOK/workspace-key only and cannot use Empyralis credits.")
        if is_subscription_provider:
            errors.append(f"{provider_id} requires subscription_passthrough, not Empyralis credits.")
        if is_local_provider:
            errors.append(f"{provider_id} requires local_model, not Empyralis credits.")
        if pricing_known is False and provider_id and model_id:
            errors.append(f"Empyralis-credit Studio usage requires known pricing for {provider_id}:{model_id}.")
    elif source_kind == STUDIO_AI_SOURCE_WORKSPACE_API_KEY:
        if not provider_id:
            errors.append("workspace_api_key requires a provider.")
        if scopes and "workspace_api" not in scopes:
            errors.append(f"Provider '{provider_id}' does not support workspace API keys.")
        if is_subscription_provider:
            errors.append(f"{provider_id} uses subscription passthrough, not workspace_api_key.")
        if is_local_provider:
            errors.append(f"{provider_id} uses local_model, not workspace_api_key.")
    elif source_kind == STUDIO_AI_SOURCE_LOCAL_MODEL:
        if not requires_local_runtime:
            errors.append("local_model requires local_companion runtime.")
        if provider_id and not is_local_provider:
            errors.append(f"{provider_id} is not a local model provider for Studio local_model source.")
    elif source_kind == STUDIO_AI_SOURCE_SUBSCRIPTION_PASSTHROUGH:
        if not requires_local_runtime:
            errors.append("subscription_passthrough requires local_companion runtime.")
        if provider_id and not is_subscription_provider:
            errors.append(
                "subscription_passthrough is limited to codex_cli or claude_code_cli providers."
            )

    if errors:
        raise ValueError(" ".join(errors))
    return {
        "ai_source": source,
        "provider": provider_id or None,
        "model": model_id or None,
        "runtime_placement": placement,
        "runtime_target": target,
    }


def _positive_int(value: Any, *, default: int = 0) -> int:
    if isinstance(value, bool):
        return default
    token = _text(value)
    if not token:
        return default
    try:
        parsed = int(token)
    except (TypeError, ValueError):
        return default
    return max(0, parsed)


def _positive_float(value: Any) -> Optional[float]:
    if value is None or isinstance(value, bool):
        return None
    token = _text(value)
    if not token:
        return None
    try:
        parsed = round(float(token), 6)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _list_text(value: Any) -> List[str]:
    values = value if isinstance(value, list) else []
    result: List[str] = []
    seen: set[str] = set()
    for item in values:
        token = _text(item).lower()
        if not token or token in seen:
            continue
        seen.add(token)
        result.append(token)
    return result


def _safe_component(value: Any, *, fallback: str) -> str:
    token = re.sub(r"[^A-Za-z0-9_.-]+", "_", _text(value))
    token = token.strip("._-")
    return token[:96] or fallback


def _hash_scope(value: Any) -> str:
    token = _text(value)
    if not token:
        return ""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()[:20]


def normalize_runtime_placement(value: Any, *, runtime_target: Any = None) -> str:
    token = _text(value).lower().replace("-", "_")
    if token in {"managed_cloud", "cloud", "cloud_default", "cloud_worker", "hosted_secure", "cloud_only"}:
        return RUNTIME_PLACEMENT_MANAGED_CLOUD
    if token in {
        "hosted_hardware_pool",
        "empyralis_hosted",
        "empyralis_hosted_device",
        "empyralis_hardware_pool",
        "owned_hardware_pool",
    }:
        return RUNTIME_PLACEMENT_HOSTED_HARDWARE_POOL
    if token in {
        "customer_local",
        "local",
        "local_secure",
        "local_only",
        "local_computer",
        "local_companion",
        "this_computer",
    }:
        return RUNTIME_PLACEMENT_CUSTOMER_LOCAL
    if token in {
        "customer_hosted",
        "self_hosted",
        "self_hosted_business",
        "self_host_runtime",
        "self_hosted_business_node",
        "enterprise_node",
    }:
        return RUNTIME_PLACEMENT_CUSTOMER_HOSTED
    if runtime_target is not None:
        return normalize_runtime_placement(runtime_target)
    return RUNTIME_PLACEMENT_MANAGED_CLOUD


def normalize_runtime_supplier(value: Any, *, runtime_placement: Any = None) -> str:
    token = _text(value).lower().replace("-", "_")
    if token in {"empyralis", "platform", "platform_owned", "empyralis_owned"}:
        return RUNTIME_SUPPLIER_EMPYRALIS
    if token in {"customer", "owner", "workspace_owner", "bring_your_own_runtime", "byor"}:
        return RUNTIME_SUPPLIER_CUSTOMER
    if token in {"third_party_certified", "certified_third_party", "marketplace_runtime_provider"}:
        return RUNTIME_SUPPLIER_THIRD_PARTY_CERTIFIED
    placement = normalize_runtime_placement(runtime_placement)
    if placement in {RUNTIME_PLACEMENT_CUSTOMER_LOCAL, RUNTIME_PLACEMENT_CUSTOMER_HOSTED}:
        return RUNTIME_SUPPLIER_CUSTOMER
    return RUNTIME_SUPPLIER_EMPYRALIS


def runtime_target_for_placement(value: Any) -> str:
    placement = normalize_runtime_placement(value)
    return RUNTIME_PLACEMENT_TARGETS.get(placement, "cloud")


def normalize_studio_agent_mode(
    value: Any,
    *,
    default: str = STUDIO_AGENT_MODE_TEXT,
    strict: bool = False,
) -> str:
    token = _text(value).lower().replace("-", "_").replace(" ", "_")
    resolved = STUDIO_AGENT_MODE_ALIASES.get(token)
    if resolved:
        return resolved
    if strict and token:
        raise ValueError(f"Unsupported Studio agent mode: {token}.")
    return default


def infer_studio_agent_mode(
    *,
    runtime_placement: Any = None,
    runtime_target: Any = None,
    runtime_supplier: Any = None,
) -> str:
    placement = normalize_runtime_placement(runtime_placement, runtime_target=runtime_target)
    supplier = normalize_runtime_supplier(runtime_supplier, runtime_placement=placement)
    if placement == RUNTIME_PLACEMENT_CUSTOMER_HOSTED:
        return STUDIO_AGENT_MODE_SELF_HOSTED
    if placement == RUNTIME_PLACEMENT_CUSTOMER_LOCAL:
        return STUDIO_AGENT_MODE_MY_COMPUTER
    if placement == RUNTIME_PLACEMENT_HOSTED_HARDWARE_POOL:
        return STUDIO_AGENT_MODE_CLOUD_COMPUTER
    if supplier == RUNTIME_SUPPLIER_CUSTOMER and placement == RUNTIME_PLACEMENT_MANAGED_CLOUD:
        return STUDIO_AGENT_MODE_MY_COMPUTER
    return STUDIO_AGENT_MODE_TEXT


def resolve_studio_agent_mode(
    value: Any,
    *,
    runtime_placement: Any = None,
    runtime_target: Any = None,
    runtime_supplier: Any = None,
) -> str:
    token = _text(value)
    if token:
        return normalize_studio_agent_mode(token, strict=True)
    return infer_studio_agent_mode(
        runtime_placement=runtime_placement,
        runtime_target=runtime_target,
        runtime_supplier=runtime_supplier,
    )


def studio_agent_mode_contract(mode: Any) -> Dict[str, Any]:
    resolved_mode = normalize_studio_agent_mode(mode, strict=True)
    profile = STUDIO_AGENT_MODE_PROFILES[resolved_mode]
    placement = str(profile["placement"])
    supplier = str(profile["supplier_kind"])
    return {
        "studio_agent_mode": resolved_mode,
        "placement": {
            "kind": placement,
            "runtime_target": runtime_target_for_placement(placement),
            "trust_zone": _runtime_trust_zone(supplier, placement),
        },
        "supplier": {
            "kind": supplier,
        },
        "allowed_capabilities": list(profile["allowed_capabilities"]),
        "computer_allowed": bool(profile["computer_allowed"]),
    }


def _target_record(runtime_targets: Any, target_id: str) -> Dict[str, Any]:
    payload = runtime_targets if isinstance(runtime_targets, dict) else {}
    targets = payload.get("targets") if isinstance(payload.get("targets"), list) else []
    for item in targets:
        if not isinstance(item, dict):
            continue
        token = _text(item.get("target_id")).lower()
        if token == target_id:
            return dict(item)
    return {}


def validate_mode_capability_matrix(
    *,
    studio_agent_mode: Any,
    runtime_placement: Any,
    runtime_target: Any,
    runtime_supply: Any = None,
    computer_automation: Any = None,
    stage: str = "create",
    runtime_targets: Any = None,
) -> None:
    mode_contract = studio_agent_mode_contract(studio_agent_mode)
    mode = mode_contract["studio_agent_mode"]
    expected_placement = mode_contract["placement"]["kind"]
    expected_target = mode_contract["placement"]["runtime_target"]
    expected_supplier = mode_contract["supplier"]["kind"]

    placement = normalize_runtime_placement(runtime_placement, runtime_target=runtime_target)
    target = _text(runtime_target).lower() or runtime_target_for_placement(placement)
    supply = runtime_supply if isinstance(runtime_supply, dict) else {}
    supply_supplier = _text(
        (supply.get("supplier") if isinstance(supply.get("supplier"), dict) else {}).get("kind")
    ).lower()
    supply_mode = _text(supply.get("studio_agent_mode")).lower()
    raw_automation = computer_automation if isinstance(computer_automation, dict) else {}
    full_access_requested = (
        _text(raw_automation.get("runtime_access_mode")).lower().replace("-", "_").replace(" ", "_")
        == RUNTIME_ACCESS_MODE_FULL_ACCESS
    )
    automation = normalize_computer_automation_config(computer_automation)
    errors: List[str] = []

    if placement != expected_placement:
        errors.append(
            f"{mode} requires runtime_placement={expected_placement}, received {placement or 'unknown'}."
        )
    if target != expected_target:
        errors.append(f"{mode} requires runtime_target={expected_target}, received {target or 'unknown'}.")
    if supply_supplier and supply_supplier != expected_supplier:
        errors.append(
            f"{mode} requires runtime supplier {expected_supplier}, received {supply_supplier}."
        )
    if supply_mode and supply_mode != mode:
        errors.append(f"runtime_supply.studio_agent_mode must be {mode}, received {supply_mode}.")

    runtime_access_mode = normalize_runtime_access_mode(
        automation.get("runtime_access_mode"),
        requires_owner_approval=automation.get("requires_owner_approval"),
    )
    requires_owner_approval = _bool(automation.get("requires_owner_approval"), default=True)
    owner_approval_disabled_without_autonomy = (
        automation.get("enabled")
        and not requires_owner_approval
        and runtime_access_mode != RUNTIME_ACCESS_MODE_FULL_ACCESS
    )

    if mode == STUDIO_AGENT_MODE_TEXT:
        if automation.get("enabled"):
            errors.append("text_agent cannot enable computer automation.")
    elif mode == STUDIO_AGENT_MODE_CLOUD_COMPUTER:
        if automation.get("enabled"):
            runtime_class = _text(automation.get("runtime_class")).lower()
            if runtime_class and runtime_class not in {
                "virtual_browser",
                "virtual_desktop",
                "virtual_code_sandbox",
            }:
                errors.append(
                    "cloud_computer_agent computer automation must use virtual_browser, virtual_desktop, or virtual_code_sandbox."
                )
        if owner_approval_disabled_without_autonomy:
            errors.append("cloud_computer_agent cannot disable owner approval. Use default_guarded or custom.")
    elif mode == STUDIO_AGENT_MODE_MY_COMPUTER:
        if automation.get("enabled"):
            runtime_class = _text(automation.get("runtime_class")).lower()
            if runtime_class and runtime_class not in {"local_browser", "local_desktop"}:
                errors.append(
                    "my_computer_agent computer automation must use local_browser or local_desktop."
                )
        if owner_approval_disabled_without_autonomy:
            errors.append("my_computer_agent cannot disable owner approval. Use default_guarded or custom.")
    elif mode == STUDIO_AGENT_MODE_SELF_HOSTED:
        if owner_approval_disabled_without_autonomy:
            errors.append("self_hosted_agent cannot disable owner approval. Use default_guarded or custom.")

    if automation.get("enabled"):
        if full_access_requested:
            errors.append("Studio agents cannot use Sage Full Access. Use default_guarded or custom.")
        if len(list(automation.get("allowed_domains") or [])) == 0:
            errors.append("Computer automation requires a non-empty domain allowlist.")
        if int(automation.get("max_concurrent_sessions") or 0) <= 0:
            errors.append("Computer automation requires max_concurrent_sessions > 0.")
        if int(automation.get("max_session_runtime_seconds") or 0) <= 0:
            errors.append("Computer automation requires max_session_runtime_seconds > 0.")
        daily_budget = automation.get("daily_budget_usd")
        monthly_budget = automation.get("monthly_budget_usd")
        if daily_budget is None:
            errors.append("Computer automation requires daily_budget_usd.")
        if monthly_budget is None:
            errors.append("Computer automation requires monthly_budget_usd.")
        if daily_budget is not None and monthly_budget is not None and float(monthly_budget) < float(daily_budget):
            errors.append("Computer automation requires monthly_budget_usd >= daily_budget_usd.")
        if bool(automation.get("inherit_host_environment")):
            errors.append("Computer automation cannot inherit host environment variables.")
        filesystem_default = _text(automation.get("filesystem_default_access")).lower()
        if filesystem_default not in COMPUTER_SAFETY_SAFE_FILESYSTEM_DEFAULTS:
            errors.append(
                "Computer automation must default to restricted filesystem access (none, session_scoped, or workspace_scoped)."
            )
        if bool(automation.get("allow_software_install")):
            errors.append("Computer automation cannot allow software installs by default.")
        terminal_policy = _text(automation.get("terminal_command_policy")).lower()
        if terminal_policy not in COMPUTER_SAFETY_ALLOWED_TERMINAL_POLICIES:
            errors.append("Computer automation must define a safe terminal command policy.")
        if not bool(automation.get("sensitive_action_confirmation_required")):
            errors.append("Computer automation requires explicit sensitive-action confirmation.")
        if int(automation.get("idle_timeout_seconds") or 0) <= 0:
            errors.append("Computer automation requires idle_timeout_seconds > 0.")
        if not bool(automation.get("emergency_stop_enabled")):
            errors.append("Computer automation requires emergency stop support.")
        approval_actions = {
            _text(item).lower()
            for item in list(automation.get("required_owner_approval_actions") or [])
            if _text(item)
        }
        missing_approvals = sorted(COMPUTER_SAFETY_REQUIRED_OWNER_APPROVAL_ACTIONS - approval_actions)
        if missing_approvals:
            errors.append(
                "Computer automation approval policy is missing required risky actions: "
                + ", ".join(missing_approvals)
                + "."
            )

    stage_token = _text(stage).lower()
    should_check_targets = stage_token == "deploy" or (
        stage_token == "create" and isinstance(runtime_targets, dict)
    )
    if should_check_targets:
        expected_target_id = STUDIO_AGENT_MODE_DEPLOY_TARGETS.get(mode)
        if expected_target_id:
            record = _target_record(runtime_targets, expected_target_id)
            if not record:
                errors.append(
                    f"{mode} requires runtime target {expected_target_id}, but it is not present for this workspace."
                )
            else:
                if not bool(record.get("available")):
                    errors.append(
                        f"{mode} requires runtime target {expected_target_id}, but it is unavailable."
                    )
                if stage_token == "deploy" and mode in {
                    STUDIO_AGENT_MODE_CLOUD_COMPUTER,
                    STUDIO_AGENT_MODE_MY_COMPUTER,
                    STUDIO_AGENT_MODE_SELF_HOSTED,
                }:
                    if not bool(record.get("online")):
                        errors.append(
                            f"{mode} requires runtime target {expected_target_id} to be online."
                        )
                    if not bool(record.get("healthy")):
                        errors.append(
                            f"{mode} requires runtime target {expected_target_id} to be healthy."
                        )

    if errors:
        raise ValueError(" ".join(errors))


def _runtime_trust_zone(supplier: str, placement: str) -> str:
    if supplier == RUNTIME_SUPPLIER_CUSTOMER:
        return "customer_owned"
    if supplier == RUNTIME_SUPPLIER_THIRD_PARTY_CERTIFIED:
        return "certified_third_party"
    if placement == RUNTIME_PLACEMENT_HOSTED_HARDWARE_POOL:
        return "empyralis_owned_hardware"
    return "empyralis_managed_cloud"


def normalize_computer_automation_config(value: Any) -> Dict[str, Any]:
    payload = dict(value or {}) if isinstance(value, dict) else {}
    enabled = _bool(payload.get("enabled"), default=False)
    runtime_class = _text(payload.get("runtime_class")).lower().replace("-", "_") or None
    if runtime_class not in COMPUTER_AUTOMATION_CLASSES:
        runtime_class = None
    allowed_domains = _list_text(payload.get("allowed_domains"))
    max_concurrent_sessions = _positive_int(payload.get("max_concurrent_sessions"), default=1 if enabled else 0)
    if not enabled:
        max_concurrent_sessions = 0
    daily_budget_usd = _positive_float(payload.get("daily_budget_usd"))
    monthly_budget_usd = _positive_float(payload.get("monthly_budget_usd"))
    if runtime_class in {"local_browser", "local_desktop"}:
        default_isolation_boundary = "local_companion"
    else:
        default_isolation_boundary = "isolated_sandbox"
    isolation_boundary = _text(
        payload.get("isolation_boundary"),
        default=default_isolation_boundary,
    ).lower()
    if isolation_boundary not in {"isolated_sandbox", "local_companion"}:
        isolation_boundary = default_isolation_boundary
    filesystem_default_access = _text(
        payload.get("filesystem_default_access"),
        default="none",
    ).lower()
    terminal_command_policy = _text(
        payload.get("terminal_command_policy"),
        default="allowlist" if enabled else "blocked",
    ).lower()
    if "required_owner_approval_actions" in payload:
        required_owner_approval_actions = _list_text(payload.get("required_owner_approval_actions"))
    else:
        required_owner_approval_actions = sorted(COMPUTER_SAFETY_REQUIRED_OWNER_APPROVAL_ACTIONS)
    runtime_access_mode = normalize_runtime_access_mode(
        payload.get("runtime_access_mode"),
        requires_owner_approval=payload.get("requires_owner_approval"),
    )
    return {
        "enabled": enabled,
        "runtime_class": runtime_class if enabled else None,
        "allowed_domains": allowed_domains if enabled else [],
        "max_concurrent_sessions": max_concurrent_sessions,
        "daily_budget_usd": daily_budget_usd if enabled else None,
        "monthly_budget_usd": monthly_budget_usd if enabled else None,
        "runtime_access_mode": runtime_access_mode,
        "requires_owner_approval": runtime_access_mode != RUNTIME_ACCESS_MODE_FULL_ACCESS,
        "idle_timeout_seconds": _positive_int(payload.get("idle_timeout_seconds"), default=300 if enabled else 0),
        "max_session_runtime_seconds": _positive_int(
            payload.get("max_session_runtime_seconds"),
            default=1800 if enabled else 0,
        ),
        "isolation_boundary": isolation_boundary if enabled else default_isolation_boundary,
        "inherit_host_environment": _bool(payload.get("inherit_host_environment"), default=False),
        "filesystem_default_access": filesystem_default_access if enabled else "none",
        "allow_downloads": _bool(payload.get("allow_downloads"), default=False),
        "allow_software_install": _bool(payload.get("allow_software_install"), default=False),
        "terminal_command_policy": terminal_command_policy if enabled else "blocked",
        "sensitive_action_confirmation_required": _bool(
            payload.get("sensitive_action_confirmation_required"),
            default=True,
        ),
        "screenshot_capture_enabled": _bool(
            payload.get("screenshot_capture_enabled"),
            default=runtime_class in {"virtual_browser", "virtual_desktop", "local_browser", "local_desktop"},
        ),
        "session_recording_policy": _text(
            payload.get("session_recording_policy"),
            default="metadata_only",
        ).lower() or "metadata_only",
        "emergency_stop_enabled": _bool(payload.get("emergency_stop_enabled"), default=True),
        "required_owner_approval_actions": (
            required_owner_approval_actions
            if enabled
            else sorted(COMPUTER_SAFETY_REQUIRED_OWNER_APPROVAL_ACTIONS)
        ),
    }


def normalize_runtime_supply_contract(
    value: Any = None,
    *,
    studio_agent_mode: Any = None,
    runtime_supplier: Any = None,
    runtime_placement: Any = None,
    runtime_target: Any = None,
    computer_automation: Any = None,
    public_tier: Any = None,
    billing_source: Any = None,
    provider: Any = None,
    model: Any = None,
) -> Dict[str, Any]:
    payload = dict(value or {}) if isinstance(value, dict) else {}
    supplier_payload = payload.get("supplier") if isinstance(payload.get("supplier"), dict) else {}
    placement_payload = payload.get("placement") if isinstance(payload.get("placement"), dict) else {}
    marketplace_payload = payload.get("marketplace_policy") if isinstance(payload.get("marketplace_policy"), dict) else {}
    model_tier_payload = payload.get("model_tier") if isinstance(payload.get("model_tier"), dict) else {}
    provider_binding_payload = payload.get("provider_binding") if isinstance(payload.get("provider_binding"), dict) else {}
    ai_source_payload = payload.get("ai_source") if isinstance(payload.get("ai_source"), dict) else {}

    resolved_mode = resolve_studio_agent_mode(
        studio_agent_mode,
        runtime_placement=runtime_placement if runtime_placement is not None else placement_payload.get("kind"),
        runtime_target=runtime_target or placement_payload.get("runtime_target"),
        runtime_supplier=runtime_supplier if runtime_supplier is not None else supplier_payload.get("kind"),
    )
    mode_contract = studio_agent_mode_contract(resolved_mode)
    placement = mode_contract["placement"]["kind"]
    supplier_kind = mode_contract["supplier"]["kind"]
    automation = normalize_computer_automation_config(
        computer_automation if computer_automation is not None else payload.get("computer_automation")
        if "computer_automation" in payload
        else payload.get("automation")
    )
    if not mode_contract["computer_allowed"]:
        automation = normalize_computer_automation_config({"enabled": False})
    tier = _text(public_tier or model_tier_payload.get("public_tier") or payload.get("public_tier") or "pro").lower()
    if tier not in {"light", "pro", "max", "local_ai", "my_api_key", "my_ai_account"}:
        tier = "pro"
    ai_source = normalize_studio_ai_source(
        ai_source_payload,
        fallback=billing_source or model_tier_payload.get("billing_source") or "empyralis_credits",
    )
    resolved_billing_source = ai_source["kind"]
    third_party_runtime_allowed = _bool(marketplace_payload.get("third_party_runtime_allowed"), default=False)
    visibility = _text(marketplace_payload.get("visibility"), default="private").lower()
    install_blockers: List[str] = [
        _text(item)
        for item in list(marketplace_payload.get("install_blockers") or [])
        if _text(item)
    ]
    if supplier_kind == RUNTIME_SUPPLIER_CUSTOMER and visibility == "marketplace":
        install_blockers.append("customer_runtime_requires_installer_opt_in")
    if supplier_kind == RUNTIME_SUPPLIER_THIRD_PARTY_CERTIFIED and not third_party_runtime_allowed:
        install_blockers.append("third_party_runtime_not_allowed")

    return {
        "schema_version": 1,
        "studio_agent_mode": resolved_mode,
        "allowed_capabilities": list(mode_contract["allowed_capabilities"]),
        "supplier": {
            "kind": supplier_kind,
            "id": _text(supplier_payload.get("id"), default=supplier_kind),
            "label": _text(supplier_payload.get("label"), default="Empyralis" if supplier_kind == RUNTIME_SUPPLIER_EMPYRALIS else "Customer-owned compute"),
            "owner_workspace_id": _text(supplier_payload.get("owner_workspace_id")) or None,
        },
        "placement": {
            "kind": placement,
            "runtime_target": mode_contract["placement"]["runtime_target"],
            "trust_zone": mode_contract["placement"]["trust_zone"],
        },
        "computer_automation": automation,
        "marketplace_policy": {
            "visibility": visibility,
            "third_party_runtime_allowed": third_party_runtime_allowed,
            "review_state": _text(marketplace_payload.get("review_state"), default="not_submitted"),
            "verification_status": _text(marketplace_payload.get("verification_status"), default="unverified"),
            "install_eligible": not install_blockers,
            "install_blockers": sorted(set(install_blockers)),
        },
        "model_tier": {
            "public_tier": tier,
            "public_label": _text(model_tier_payload.get("public_label"), default=tier.replace("_", " ").title()),
            "billing_source": resolved_billing_source,
        },
        "ai_source": ai_source,
        "provider_binding": {
            "internal_provider": _text(provider or provider_binding_payload.get("internal_provider")) or None,
            "internal_model": _text(model or provider_binding_payload.get("internal_model")) or None,
            "expose_provider_model_to_ordinary_ui": _bool(
                provider_binding_payload.get("expose_provider_model_to_ordinary_ui"),
                default=tier in {"local_ai", "my_api_key", "my_ai_account"},
            ),
        },
    }


def build_runtime_provider_spec(
    *,
    provider_id: Any,
    supplier_kind: Any,
    placement: Any,
    label: Any = None,
    capabilities: Any = None,
    available_slots: Any = 0,
    max_concurrency: Any = 0,
    estimated_unit_cost: Any = 0,
    trust_tier: Any = "standard",
    supports_checkpoint: Any = False,
    preemptible: Any = False,
    region: Any = None,
) -> Dict[str, Any]:
    placement_kind = normalize_runtime_placement(placement)
    supplier = normalize_runtime_supplier(supplier_kind, runtime_placement=placement_kind)
    slots = _positive_int(available_slots, default=0)
    max_slots = _positive_int(max_concurrency, default=slots)
    return {
        "provider_id": _text(provider_id, default=f"{supplier}:{placement_kind}"),
        "supplier_kind": supplier,
        "placement": placement_kind,
        "runtime_target": runtime_target_for_placement(placement_kind),
        "label": _text(label, default=f"{supplier} {placement_kind}".replace("_", " ").title()),
        "capabilities": _list_text(capabilities),
        "available_slots": slots,
        "max_concurrency": max_slots,
        "estimated_unit_cost": float(_positive_float(estimated_unit_cost) or 0.0),
        "trust_tier": _text(trust_tier, default="standard"),
        "supports_checkpoint": _bool(supports_checkpoint),
        "preemptible": _bool(preemptible),
        "region": _text(region) or None,
    }


def choose_runtime_provider_for_job(
    providers: Any,
    *,
    required_placement: Any = None,
    required_capabilities: Any = None,
    max_estimated_unit_cost: Any = None,
    allow_preemptible: bool = False,
) -> Dict[str, Any]:
    required = normalize_runtime_placement(required_placement) if required_placement else None
    capabilities = set(_list_text(required_capabilities))
    budget = _positive_float(max_estimated_unit_cost)
    candidates: List[Dict[str, Any]] = []
    for raw_provider in list(providers or []):
        provider = build_runtime_provider_spec(**raw_provider) if isinstance(raw_provider, dict) else None
        if not provider:
            continue
        if required and provider["placement"] != required:
            continue
        if capabilities and not capabilities.issubset(set(provider["capabilities"])):
            continue
        if not allow_preemptible and provider["preemptible"]:
            continue
        if budget is not None and float(provider["estimated_unit_cost"]) > budget:
            continue
        if int(provider["available_slots"]) <= 0:
            continue
        candidates.append(provider)
    if not candidates:
        return {
            "status": "queued",
            "reason": "waiting_for_capacity",
            "provider": None,
        }
    candidates.sort(
        key=lambda item: (
            float(item["estimated_unit_cost"]),
            1 if item["preemptible"] else 0,
            -int(item["available_slots"]),
            item["provider_id"],
        )
    )
    selected = candidates[0]
    return {
        "status": "selected",
        "reason": "capacity_available",
        "provider": selected,
        "estimated_unit_cost": selected["estimated_unit_cost"],
    }


def build_hardware_pool_job_contract(
    *,
    job_id: Any,
    supplier_id: Any,
    hardware_pool_id: Any,
    checkpoint_uri: Any = None,
    checkpoint_generation: Any = 0,
    preemptible: Any = False,
    preemption_deadline_at: Any = None,
    resume_target: Any = None,
) -> Dict[str, Any]:
    return {
        "job_id": _text(job_id, default="job"),
        "supplier_id": _text(supplier_id, default=RUNTIME_SUPPLIER_EMPYRALIS),
        "hardware_pool_id": _text(hardware_pool_id, default="default"),
        "checkpoint": {
            "enabled": bool(_text(checkpoint_uri)),
            "uri": _text(checkpoint_uri) or None,
            "generation": _positive_int(checkpoint_generation, default=0),
        },
        "preemption": {
            "preemptible": _bool(preemptible),
            "deadline_at": _text(preemption_deadline_at) or None,
            "resume_target": _text(resume_target) or None,
            "safe_resume_required": True,
        },
    }


def _domain_from_value(value: Any) -> str:
    token = _text(value).lower()
    if not token:
        return ""
    parsed = urlparse(token if "://" in token else f"https://{token}")
    return (parsed.hostname or token).strip(".").lower()


def computer_automation_guardrail_state(
    config: Any,
    *,
    requested_domain: Any = None,
    estimated_cost_usd: Optional[float] = None,
    active_sessions: int = 0,
) -> Dict[str, Any]:
    policy = normalize_computer_automation_config(config)
    reasons: List[str] = []
    if not policy["enabled"]:
        reasons.append("computer_automation_disabled")
    domain = _domain_from_value(requested_domain)
    allowed_domains = [_domain_from_value(item) for item in policy["allowed_domains"]]
    if policy["enabled"] and domain:
        if not any(domain == allowed or domain.endswith(f".{allowed}") for allowed in allowed_domains if allowed):
            reasons.append("domain_not_allowed")
    if policy["enabled"] and not allowed_domains:
        reasons.append("allowed_domain_required")
    if policy["enabled"] and int(active_sessions or 0) >= int(policy["max_concurrent_sessions"] or 0):
        reasons.append("concurrency_limit_reached")
    cost = float(estimated_cost_usd or 0)
    daily_budget = policy.get("daily_budget_usd")
    monthly_budget = policy.get("monthly_budget_usd")
    if policy["enabled"] and cost > 0:
        if daily_budget is not None and cost > float(daily_budget):
            reasons.append("daily_budget_exceeded")
        if monthly_budget is not None and cost > float(monthly_budget):
            reasons.append("monthly_budget_exceeded")
    return {
        "allowed": not reasons,
        "reasons": reasons,
        "policy": policy,
        "requested_domain": domain or None,
    }


def build_deployed_agent_workspace_contract(
    *,
    tenant_id: Any,
    workspace_id: Any,
    deployed_agent_id: Any,
    external_user_id: Any = None,
    session_id: Any = None,
    base_dir: Any = DEFAULT_AGENT_WORKSPACE_BASE,
) -> Dict[str, Any]:
    tenant = _safe_component(tenant_id, fallback="tenant")
    workspace = _safe_component(workspace_id, fallback="workspace")
    agent = _safe_component(deployed_agent_id, fallback="agent")
    root = Path(_text(base_dir, default=DEFAULT_AGENT_WORKSPACE_BASE)).expanduser() / tenant / workspace / agent
    customer_hash = _hash_scope(external_user_id)
    session_hash = _hash_scope(session_id)
    customer_root = root / "customers" / customer_hash if customer_hash else None
    session_root = customer_root / "sessions" / session_hash if customer_root is not None and session_hash else None
    return {
        "schema_version": 1,
        "workspace_root": str(root),
        "files_root": str(root / "files"),
        "artifacts_root": str(root / "artifacts"),
        "browser_profile_root": str(root / "browser_profile"),
        "logs_root": str(root / "logs"),
        "state_root": str(root / "state"),
        "customer_scope_key": customer_hash or None,
        "customer_root": str(customer_root) if customer_root is not None else None,
        "session_scope_key": session_hash or None,
        "session_root": str(session_root) if session_root is not None else None,
        "isolation": {
            "scope": "deployed_agent",
            "cross_agent_read": False,
            "cross_customer_read": False,
            "host_filesystem_default": "workspace_root_only",
            "sage_memory_access": False,
        },
    }
