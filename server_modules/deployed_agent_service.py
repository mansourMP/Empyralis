"""Deployed/Studio agents — FROZEN by owner decision (2026-07-09 Phase 7B).

Fleet (workspace_agent_installs) is now the one agent class. The live-channel
delivery, quota/cost-cap enforcement, and every Studio UI surface that used to
call into this file have been deleted as dead code — see
docs/DEPLOYED-AGENT-CONSOLIDATION-MAP.md for the full inventory and the
ordered strangler plan this file's cleanup came from.

What's left here is intentionally NOT deleted: the customer-facing, monetized
agent product this file implements (public marketplace listing, daily message
quotas with upsell CTAs, monthly cost caps, GDPR-style external-user deletion,
escalation-to-owner policy, the Telegram shop-assistant vertical, computer-
automation safety budgets) is novel capability Fleet doesn't have an
equivalent for, not scaffolding Fleet already replaced. It stays as a dormant,
fully-built reference implementation in case that product gets built on top
of Fleet installs in the future — do not delete, do not extend, do not treat
as dead code to clean up. Any change here needs an explicit owner decision
first, the same way this freeze was one.
"""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import quote
import uuid

from fastapi import HTTPException

_logger = logging.getLogger(__name__)

from server_modules import activity_ledger_service
from server_modules import agent_specialist_repository
from server_modules import auth as auth_module
from server_modules import config_defaults_service
from server_modules import control_plane_repository
from server_modules import conversation_memory_policy
from server_modules import deployed_agent_config_schema
from server_modules import deployed_agent_runtime_contract_service
from server_modules import deployed_agent_analytics_service
from server_modules import deployed_agent_virtual_runtime_service
from server_modules import empyralis_model_tier_routing_service
from server_modules import entitlements_service
from server_modules import external_user_privacy_service
from server_modules import knowledge_rag_service
from server_modules import provider_catalog_service
from server_modules import pricing_registry_service
from server_modules import product_catalog_live_data_service
from server_modules import runtime_attachment_service
from server_modules import rust_runtime_kernel_client
from server_modules import run_state_repository
from server_modules import session_service
from server_modules import shop_assistant_revenue_agent_service
from server_modules import usage_accounting_service
from server_modules import workspace_context
from server_modules import workspace_config_schema
from server_modules.connectors.autopilot_runtime_exports import _autopilot_connector_shell_service
from server_modules.connectors.autopilot_status_service import AutopilotStatusService
from server_modules.agent_manifest import (
    AgentManifest,
    AgentManifestBible,
    AgentManifestChannels,
    AgentManifestIdentity,
    AgentManifestRuntime,
    AgentManifestVoiceProfile,
)


DEPLOYED_AGENT_ALLOWED_STATES = frozenset(
    {
        "draft",
        "private_test",
        "ready_for_review",
        "live",
        "paused",
        "suspended",
        "archived",
    }
)
DEPLOYED_AGENT_PUBLIC_CHANNEL_ROUTABLE_STATES = frozenset({"live", "paused", "suspended"})
DEPLOYED_AGENT_NON_PUBLIC_STATES = frozenset(
    {
        "draft",
        "private_test",
        "ready_for_review",
        "archived",
    }
)
DEPLOYED_AGENT_LIVE_CHANNELS = config_defaults_service.live_deployment_channels()
LOGGER = logging.getLogger(__name__)
DEPLOYED_AGENT_PUBLIC_FIELDS = (
    "id",
    "owner_workspace_id",
    "name",
    "avatar",
    "persona",
    "system_prompt",
    "deployment_state",
    "channels",
    "knowledge_sources",
    "runtime_target",
    "billing_plan",
    "is_public",
    "quality_stars",
    "cost_tier",
    "category",
    "created_at",
    "updated_at",
)
DEPLOYED_AGENT_INTERNAL_FIELDS = (
    "tenant_id",
    "backing_install_id",
    "created_by_user_id",
    "last_deployed_at",
    "last_paused_at",
    "metadata",
)
_INLINE_KNOWLEDGE_KEYS = frozenset({"content", "text", "body", "raw_text", "raw_content", "data", "bytes"})
_REFERENCE_KEYS = frozenset({"id", "source_id", "uri", "path", "connector_key", "document_id", "record_id", "external_id"})
_MANIFEST_CHANNEL_KEYS = tuple(AgentManifestChannels.model_fields.keys())
_ESCALATION_PRESET_TRIGGERS = {
    "standard": "low confidence\nexplicit human request\npolicy conflict",
    "conservative": "low confidence\nexplicit human request\nhealth or safety concern\npolicy conflict",
    "high_touch": "low confidence\nexplicit human request\nrefund or billing exception\npolicy conflict",
}
_STUDIO_WHATSAPP_STATUS = {
    "available": False,
    "status": "out_of_scope",
    "label": "Not in scope",
    "detail": "Studio launches Telegram specialists only in this beta. WhatsApp remains intentionally unavailable.",
}
_STUDIO_TOOL_SCOPE_CATALOG = (
    {
        "id": "web_search",
        "label": "Web search",
        "description": "Search the web for current facts and public references.",
    },
    {
        "id": "http_request",
        "label": "HTTP request",
        "description": "Call approved APIs and webhooks inside the deployment boundary.",
    },
    {
        "id": "spreadsheet_read",
        "label": "Spreadsheet read",
        "description": "Read menu, availability, and daily-special data from connected spreadsheets.",
    },
    {
        "id": "spreadsheet_append",
        "label": "Spreadsheet append",
        "description": "Append confirmed orders or handoff notes into a connected order log sheet.",
    },
    {
        "id": "gmail_send",
        "label": "Send email",
        "description": "Draft or send Gmail replies when the workspace has a connected mailbox.",
    },
    {
        "id": "calendar_write",
        "label": "Calendar write",
        "description": "Create or update calendar events for customer-facing scheduling flows.",
    },
)
_PRIVACY_RETENTION_PRESET_DAYS = {
    "short": 30,
    "standard": 365,
    "extended": 730,
}
_PRIVACY_CONTRACT_REQUIRED_KEYS = frozenset(
    {
        "where_it_runs",
        "model_provider_data_access",
        "screenshots_captured",
        "files_accessible",
        "terminal_accessible",
        "connectors_accessible",
        "memory_scope",
        "retention_period",
        "export_delete_policy",
        "audit_log",
    }
)
_COMPUTER_SAFETY_REQUIRED_KEYS = frozenset(
    {
        "enabled",
        "studio_agent_mode",
        "isolation_boundary",
        "inherit_host_environment",
        "filesystem_default_access",
        "domain_allowlist",
        "download_install_policy",
        "terminal_command_policy",
        "sensitive_action_confirmation_required",
        "session_timeout_seconds",
        "max_runtime_seconds",
        "screenshot_session_recording",
        "emergency_stop_enabled",
        "required_owner_approval_actions",
    }
)
_COMPUTER_SAFETY_REQUIRED_OWNER_APPROVAL_ACTIONS = frozenset(
    {
        "send_external_messages",
        "make_purchases",
        "delete_data",
        "change_permissions",
        "enter_secrets",
        "install_software",
        "run_unknown_scripts",
    }
)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _normalize_text(value: Any, *, default: str = "") -> str:
    token = str(value or "").strip()
    return token or default


def _normalize_optional_text(value: Any) -> Optional[str]:
    token = str(value or "").strip()
    return token or None


def _normalize_optional_positive_int(value: Any) -> Optional[int]:
    if value is None:
        return None
    if isinstance(value, bool):
        raise ValueError("daily_message_limit must be a positive integer.")
    token = str(value).strip()
    if not token:
        return None
    try:
        parsed = int(token)
    except (TypeError, ValueError) as error:
        raise ValueError("daily_message_limit must be a positive integer.") from error
    if parsed <= 0:
        return None
    return parsed


def _normalize_optional_positive_usd(value: Any) -> Optional[float]:
    if value is None:
        return None
    if isinstance(value, bool):
        raise ValueError("monthly_cost_cap_usd must be a positive USD amount.")
    token = str(value).strip()
    if not token:
        return None
    try:
        parsed = round(float(token), 6)
    except (TypeError, ValueError) as error:
        raise ValueError("monthly_cost_cap_usd must be a positive USD amount.") from error
    if parsed <= 0:
        return None
    return parsed


def _normalize_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    token = str(value or "").strip().lower()
    return token in {"1", "true", "yes", "on", "enabled"}


def _normalize_runtime_target(value: Any) -> str:
    token = str(value or "").strip().lower()
    if token in {"cloud", "cloud_default", "hosted_secure", "cloud_only"}:
        return "cloud"
    if token in {"local", "local_secure", "local_only", "local_companion"}:
        return "local"
    if token in {"self_hosted", "self_hosted_business", "self_hosted_business_node", "customer_hosted"}:
        return "self_hosted"
    if token in {"device", "desktop", "privileged_device"}:
        return "device"
    return token or config_defaults_service.default_deployed_agent_runtime_target()


def _runtime_target_to_specialist_mode(runtime_target: Any) -> str:
    token = _normalize_runtime_target(runtime_target)
    if token == "local":
        return "local_secure"
    if token == "device":
        return "privileged_device"
    return "hosted_secure"


def _deployed_agent_workspace_contract(deployed_agent: Dict[str, Any]) -> Dict[str, Any]:
    return deployed_agent_runtime_contract_service.build_deployed_agent_workspace_contract(
        tenant_id=deployed_agent.get("tenant_id"),
        workspace_id=deployed_agent.get("owner_workspace_id"),
        deployed_agent_id=deployed_agent.get("id"),
    )


def _normalize_deployment_state(value: Any, *, default: str = "draft") -> str:
    token = str(value or "").strip().lower()
    return token if token in DEPLOYED_AGENT_ALLOWED_STATES else default


_DEPLOYED_AGENT_SERVICE_NEXT_ACTIONS = {
    "telegram_readiness": {"read_telegram_readiness"},
    "analytics_read": {"read_deployed_agent_analytics"},
    "admin_dashboard": {"read_deployed_agent_admin_dashboard"},
    "audit_export": {"export_deployed_agent_audit_logs"},
    "conversation_list": {"list_deployed_agent_conversations"},
    "conversation_detail": {"read_deployed_agent_conversation_detail"},
    "memory_list": {"list_deployed_agent_memory"},
    "activity_list": {"list_deployed_agent_activity"},
    "external_user_delete": {"delete_deployed_agent_external_user_data"},
    "knowledge_verify": {"verify_deployed_agent_knowledge"},
    "knowledge_upload": {"upload_deployed_agent_knowledge_reference"},
    "test_turn": {"execute_deployed_agent_test_turn"},
    "shop_evaluate": {"evaluate_shop_assistant"},
    "deploy": {"deploy_deployed_agent"},
    "pause": {"pause_deployed_agent"},
    "kill": {"kill_deployed_agent"},
    "recover": {"recover_deployed_agent"},
    "archive": {"archive_deployed_agent"},
    "recovery_action": {"apply_deployed_agent_recovery_action"},
    "runtime_session_kill": {"kill_deployed_agent_runtime_session"},
    "emergency_stop": {"emergency_stop_workspace_deployed_agents"},
}


def _deployed_agent_service_actor_role(
    current_user: Optional[Dict[str, Any]],
    workspace_id: str,
) -> str:
    user = dict(current_user or {}) if isinstance(current_user, dict) else {}
    if not user:
        return "system"
    workspace_access = user.get("workspace_access") if isinstance(user.get("workspace_access"), dict) else {}
    scoped = workspace_access.get(workspace_id) if isinstance(workspace_access.get(workspace_id), dict) else {}
    role = _normalize_text(scoped.get("role") or user.get("role")).lower()
    if role in {"owner", "workspace_owner"}:
        return "owner"
    if role in {"admin", "workspace_admin", "super_admin"}:
        return "admin"
    if role in {"member", "viewer"}:
        return "member"
    return "system" if not _normalize_optional_text(user.get("user_id")) else "member"


def _deployed_agent_service_owner_access(
    current_user: Optional[Dict[str, Any]],
    workspace_id: str,
) -> bool:
    return _deployed_agent_service_actor_role(current_user, workspace_id) in {"owner", "admin", "system"}


def _enforce_deployed_agent_service_decision(
    operation: str,
    deployed_agent: Dict[str, Any],
    tenant_id: str,
    workspace_id: str,
    current_user: Optional[Dict[str, Any]],
    approval_provided: bool = True,
    **extra: Any,
) -> Dict[str, Any]:
    config = _config_from_record(deployed_agent)
    metadata = _coerce_dict(deployed_agent.get("metadata"))
    privacy_snapshot = _coerce_dict(metadata.get("privacy_contract_snapshot"))
    computer_safety_snapshot = _coerce_dict(metadata.get("computer_safety_contract_snapshot"))
    actor_role = _deployed_agent_service_actor_role(current_user, workspace_id)
    payload = {
        "operation": operation,
        "tenant_id": tenant_id,
        "workspace_id": workspace_id,
        "deployed_agent_id": _normalize_text(deployed_agent.get("id")),
        "deployment_state": _normalize_deployment_state(deployed_agent.get("deployment_state")),
        "runtime_target": _normalize_text(config.studio_agent_mode or config.runtime_target),
        "actor_id": _normalize_optional_text((current_user or {}).get("user_id")) or "system",
        "actor_role": actor_role,
        "workspace_access": True,
        "owner_access": _deployed_agent_service_owner_access(current_user, workspace_id),
        "entitlement_ok": True,
        "budget_available": True,
        "approval_provided": approval_provided,
        "privacy_contract_complete": _validate_privacy_contract_snapshot(privacy_snapshot),
        "privacy_contract_accepted": _validate_privacy_contract_acceptance(privacy_snapshot),
        "computer_safety_contract_complete": _validate_computer_safety_contract_snapshot(computer_safety_snapshot),
        "required_fields_complete": True,
        "backing_install_present": bool(_normalize_optional_text(deployed_agent.get("backing_install_id"))),
        "runtime_eligible": True,
        "live_channel_configured": True,
        "active_run_count": 0,
        **extra,
    }
    try:
        decision = rust_runtime_kernel_client.run_runtime_kernel_enforced(
            "deployed-agent-service-decision",
            payload,
        )
    except rust_runtime_kernel_client.RustKernelDecisionError as exc:
        reason = _normalize_text(exc.reason) or "rust_deployed_agent_service_denied"
        raise _http_conflict(f"Rust deployed-agent service blocked {operation}: {reason}")
    expected_actions = _DEPLOYED_AGENT_SERVICE_NEXT_ACTIONS.get(operation)
    next_action = _normalize_text(decision.get("next_action"))
    if expected_actions is None:
        raise _http_conflict(f"Rust deployed-agent service has no next_action contract for {operation}.")
    if next_action not in expected_actions:
        expected_list = ", ".join(sorted(expected_actions))
        raise _http_conflict(
            f"Rust deployed-agent service returned unexpected next_action for {operation}: "
            f"{next_action or '<missing>'} (expected {expected_list})"
        )
    return decision


_DEPLOYED_AGENT_DATA_NEXT_ACTIONS = {
    "analytics_detail": {"read_deployed_agent_analytics"},
    "audit_export": {"export_deployed_agent_audit_log"},
    "list_conversations": {"list_deployed_agent_conversations"},
    "conversation_detail": {"read_deployed_agent_conversation_detail"},
    "list_memory": {"list_deployed_agent_memory"},
    "list_activity": {"list_deployed_agent_activity"},
    "delete_external_user_data": {"purge_deployed_agent_external_user_data"},
}


def _enforce_deployed_agent_data_decision(
    operation: str,
    *,
    tenant_id: str,
    workspace_id: str,
    deployed_agent_id: str,
    current_user: Optional[Dict[str, Any]],
    **extra: Any,
) -> Dict[str, Any]:
    actor_role = _deployed_agent_service_actor_role(current_user, workspace_id)
    payload = {
        "operation": operation,
        "tenant_id": tenant_id,
        "workspace_id": workspace_id,
        "deployed_agent_id": deployed_agent_id,
        "actor_role": actor_role,
        "audit_log_enabled": True,
        "privacy_delete_enabled": True,
        "privacy_request_verified": True,
        "operator_confirmed": True,
        **extra,
    }
    try:
        decision = rust_runtime_kernel_client.run_runtime_kernel_enforced(
            "deployed-data-decision",
            payload,
        )
    except rust_runtime_kernel_client.RustKernelDecisionError as exc:
        reason = _normalize_text(exc.reason) or "rust_deployed_agent_data_denied"
        raise _http_conflict(f"Rust deployed-agent data blocked {operation}: {reason}")
    expected_actions = _DEPLOYED_AGENT_DATA_NEXT_ACTIONS.get(operation)
    next_action = _normalize_text(decision.get("next_action"))
    if expected_actions is None:
        raise _http_conflict(f"Rust deployed-agent data has no next_action contract for {operation}.")
    if next_action not in expected_actions:
        expected_list = ", ".join(sorted(expected_actions))
        raise _http_conflict(
            f"Rust deployed-agent data returned unexpected next_action for {operation}: "
            f"{next_action or '<missing>'} (expected {expected_list})"
        )
    return decision


def _deployed_agent_service_mutation_plan(decision: Dict[str, Any]) -> Dict[str, Any]:
    plan = decision.get("mutation_plan")
    return dict(plan) if isinstance(plan, dict) else {}


def _deployed_agent_service_target_state(decision: Dict[str, Any], *, default: str) -> str:
    plan = _deployed_agent_service_mutation_plan(decision)
    return _normalize_deployment_state(plan.get("target_state"), default=default)


def _deployed_agent_service_plan_flag(
    decision: Dict[str, Any],
    key: str,
    *,
    default: bool = False,
) -> bool:
    plan = _deployed_agent_service_mutation_plan(decision)
    value = plan.get(key)
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on", "enabled"}
    if isinstance(value, (int, float)):
        return bool(value)
    return default


def deployment_state_is_publicly_routable(value: Any) -> bool:
    return _normalize_deployment_state(value, default="") in DEPLOYED_AGENT_PUBLIC_CHANNEL_ROUTABLE_STATES


def deployment_state_blocks_public_routing(value: Any) -> bool:
    return _normalize_deployment_state(value, default="") in DEPLOYED_AGENT_NON_PUBLIC_STATES


def _normalize_channels(value: Any) -> Dict[str, Dict[str, Any]]:
    return deployed_agent_config_schema.normalize_deployed_agent_channels(value)


def _normalize_knowledge_sources(value: Any) -> List[Dict[str, Any]]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError("knowledge_sources must be a list of structured references.")
    normalized: List[Dict[str, Any]] = []
    for item in value:
        if not isinstance(item, dict):
            raise ValueError("knowledge_sources entries must be objects.")
        payload = {str(key): val for key, val in item.items() if str(key or "").strip()}
        if any(payload.get(key) for key in _INLINE_KNOWLEDGE_KEYS):
            raise ValueError("knowledge_sources must store references only, not raw inline content.")
        if not any(payload.get(key) for key in _REFERENCE_KEYS):
            raise ValueError("knowledge_sources entries must contain a reference identifier.")
        normalized.append(payload)
    return normalized


def _channel_enabled(config: Any) -> bool:
    if isinstance(config, dict):
        return bool(config.get("enabled"))
    return bool(config)


def _live_channel_keys(channels: Dict[str, Any]) -> set[str]:
    return {
        str(channel_key or "").strip().lower()
        for channel_key, config in dict(channels or {}).items()
        if str(channel_key or "").strip() and _channel_enabled(config)
    }


def _channel_config_matches_endpoint(
    *,
    deployed_agent: Dict[str, Any],
    channel_key: str,
    endpoint_key: Any,
) -> bool:
    normalized_channel_key = _normalize_text(channel_key).lower()
    normalized_channels = _normalize_channels(deployed_agent.get("channels") or {})
    channel_config = normalized_channels.get(normalized_channel_key)
    if not _channel_enabled(channel_config):
        return False
    if isinstance(channel_config, dict):
        configured_endpoint = _normalize_optional_text(channel_config.get("endpoint_key"))
        requested_endpoint = _normalize_optional_text(endpoint_key)
        if configured_endpoint and requested_endpoint:
            return configured_endpoint.lower() == requested_endpoint.lower()
        if configured_endpoint and not requested_endpoint:
            return False
    return True


def _http_bad_request(detail: str) -> HTTPException:
    return HTTPException(status_code=400, detail=detail)


def _http_conflict(detail: str) -> HTTPException:
    return HTTPException(status_code=409, detail=detail)


def _coerce_dict(value: Any) -> Dict[str, Any]:
    return dict(value or {}) if isinstance(value, dict) else {}


def _coerce_list(value: Any) -> List[Any]:
    return list(value) if isinstance(value, list) else []


def _safe_float(value: Any) -> Optional[float]:
    try:
        return round(float(value), 6)
    except (TypeError, ValueError):
        return None


def _deployed_agent_activity_kind(row: Dict[str, Any]) -> str:
    event_class = _normalize_text(row.get("event_class")).lower()
    action = _normalize_text(row.get("action")).lower()
    payload = _coerce_dict(row.get("payload"))
    metadata = _coerce_dict(row.get("metadata"))
    if action.startswith("deployed_agent_") or event_class == "system_activity":
        if any(token in action for token in {"paused", "resumed", "suspended", "killed", "archived"}):
            return "lifecycle_control"
        return "lifecycle"
    if event_class == "memory_update" or "memory" in action:
        return "memory_write"
    if "connector" in action or _normalize_text(payload.get("connector")).lower() or _normalize_text(payload.get("connector_id")).lower():
        return "connector_call"
    if "tool" in action or _normalize_text(payload.get("tool_name")).lower():
        return "tool_call"
    if "browser" in action or "computer" in action:
        return "computer_session"
    if event_class in {"specialist_activity", "sage_activity"} and _normalize_text(row.get("direction")).lower() == "outbound":
        return "external_message"
    if any(token in action for token in {"budget", "cost", "credit", "spend"}):
        return "cost_spend"
    if _safe_float(payload.get("estimated_cost_usd")) is not None or _safe_float(metadata.get("estimated_cost_usd")) is not None:
        return "cost_spend"
    return "activity"


def _activity_row_matches_deployed_agent(
    *,
    row: Dict[str, Any],
    deployed_agent_id: str,
    backing_install_id: Optional[str],
) -> bool:
    payload = _coerce_dict(row.get("payload"))
    metadata = _coerce_dict(row.get("metadata"))
    actor_type = _normalize_text(row.get("actor_type")).lower()
    actor_id = _normalize_text(row.get("actor_id"))
    install_id = _normalize_optional_text(row.get("install_id"))
    resolved_deployed_agent_id = _normalize_text(deployed_agent_id)
    resolved_backing_install_id = _normalize_optional_text(backing_install_id)
    if actor_type == "deployed_agent" and actor_id == resolved_deployed_agent_id:
        return True
    if resolved_backing_install_id and install_id == resolved_backing_install_id:
        return True
    payload_deployed_agent_id = _normalize_optional_text(
        payload.get("deployed_agent_id")
        or _coerce_dict(payload.get("runtime")).get("deployed_agent_id")
    )
    metadata_deployed_agent_id = _normalize_optional_text(
        metadata.get("deployed_agent_id")
        or _coerce_dict(metadata.get("deployed_agent")).get("id")
    )
    if payload_deployed_agent_id == resolved_deployed_agent_id:
        return True
    if metadata_deployed_agent_id == resolved_deployed_agent_id:
        return True
    return False


def _deployed_agent_activity_item(row: Dict[str, Any]) -> Dict[str, Any]:
    payload = _coerce_dict(row.get("payload"))
    metadata = _coerce_dict(row.get("metadata"))
    return {
        "id": _normalize_optional_text(row.get("id")),
        "ts": _timestamp_token(row.get("created_at")),
        "event_class": _normalize_optional_text(row.get("event_class")),
        "action": _normalize_optional_text(row.get("action")),
        "title": _compact_text(row.get("title"), limit=160),
        "summary": _compact_text(row.get("summary"), limit=320),
        "status": _normalize_optional_text(row.get("status")),
        "review_required": bool(row.get("review_required")),
        "kind": _deployed_agent_activity_kind(row),
        "actor_type": _normalize_optional_text(row.get("actor_type")) or "system",
        "actor_id": _normalize_optional_text(row.get("actor_id")),
        "install_id": _normalize_optional_text(row.get("install_id")),
        "app_id": _normalize_optional_text(row.get("app_id")),
        "run_id": _normalize_optional_text(row.get("run_id")),
        "thread_id": _normalize_optional_text(row.get("thread_id")),
        "session_key": _normalize_optional_text(row.get("session_key")),
        "channel": _normalize_optional_text(row.get("channel")),
        "direction": _normalize_optional_text(row.get("direction")),
        "connector": _normalize_optional_text(
            payload.get("connector")
            or payload.get("connector_id")
            or metadata.get("connector")
            or metadata.get("connector_id")
        ),
        "tool_name": _normalize_optional_text(payload.get("tool_name") or metadata.get("tool_name")),
        "estimated_cost_usd": _safe_float(payload.get("estimated_cost_usd") or metadata.get("estimated_cost_usd")),
    }


async def _append_deployed_agent_audit_event(
    *,
    tenant_id: str,
    workspace_id: str,
    deployed_agent: Dict[str, Any],
    action: str,
    title: str,
    summary: str,
    status: str = "logged",
    event_class: str = "system_activity",
    review_required: bool = False,
    payload: Optional[Dict[str, Any]] = None,
    metadata: Optional[Dict[str, Any]] = None,
    actor_user_id: Optional[str] = None,
) -> None:
    if not isinstance(deployed_agent, dict):
        return
    deployed_agent_id = _normalize_text(deployed_agent.get("id"))
    if not deployed_agent_id:
        return
    config = _config_from_record(deployed_agent)
    snapshot_metadata = _coerce_dict(deployed_agent.get("metadata"))
    privacy_snapshot = _coerce_dict(snapshot_metadata.get("privacy_contract_snapshot"))
    computer_snapshot = _coerce_dict(snapshot_metadata.get("computer_safety_contract_snapshot"))
    event_metadata = {
        "deployed_agent_id": deployed_agent_id,
        "deployment_state": _normalize_text(deployed_agent.get("deployment_state"), default="draft"),
        "runtime_selected": {
            "studio_agent_mode": config.studio_agent_mode,
            "runtime_target": config.runtime_target,
            "runtime_placement": config.runtime_placement,
            "runtime_supplier": _normalize_optional_text(
                _coerce_dict(_coerce_dict(config.runtime_supply).get("supplier")).get("kind")
            ),
        },
        "privacy_contract_version": int(privacy_snapshot.get("schema_version") or 0) or None,
        "computer_safety_contract_version": int(computer_snapshot.get("schema_version") or 0) or None,
        "tool_policy": {"enabled_tools": list(config.tool_policy.enabled_tools or [])},
        "connectors": sorted(
            key
            for key, value in dict(config.channels or {}).items()
            if bool(getattr(value, "enabled", False))
        ),
        "memory_policy": {
            "enabled": bool(config.memory_policy.memory_enabled),
            "context_budget_preset": config.memory_policy.context_budget_preset,
            "retention_preset": config.memory_policy.retention_preset,
        },
        "actor_user_id": _normalize_optional_text(actor_user_id),
        "audit_version": 1,
    }
    event_metadata.update(_coerce_dict(metadata))
    try:
        await activity_ledger_service.append_activity_event(
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            actor_type="deployed_agent",
            actor_id=deployed_agent_id,
            install_id=_normalize_optional_text(deployed_agent.get("backing_install_id")),
            event_class=event_class,
            detail_level="audit_reference",
            action=action,
            title=title,
            summary=summary,
            status=status,
            review_required=bool(review_required),
            payload=_coerce_dict(payload),
            metadata=event_metadata,
        )
    except Exception:
        LOGGER.exception(
            "Failed to append deployed-agent audit event",
            extra={
                "workspace_id": workspace_id,
                "deployed_agent_id": deployed_agent_id,
                "action": action,
            },
        )


def _config_has_full_runtime_access(config: deployed_agent_config_schema.DeployedAgentConfig) -> bool:
    if not bool(config.computer_automation.enabled):
        return False
    return (
        deployed_agent_runtime_contract_service.normalize_runtime_access_mode(
            config.computer_automation.runtime_access_mode,
            requires_owner_approval=config.computer_automation.requires_owner_approval,
        )
        == deployed_agent_runtime_contract_service.RUNTIME_ACCESS_MODE_FULL_ACCESS
    )


async def _append_full_access_runtime_review_event(
    *,
    tenant_id: str,
    workspace_id: str,
    deployed_agent: Dict[str, Any],
    lifecycle_action: str,
    actor_user_id: Optional[str] = None,
) -> None:
    if not isinstance(deployed_agent, dict):
        return
    config = _config_from_record(deployed_agent)
    if not _config_has_full_runtime_access(config):
        return
    await _append_deployed_agent_audit_event(
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        deployed_agent=deployed_agent,
        action="deployed_agent_full_access_review_required",
        title="Full access runtime review required",
        summary=(
            f"{_normalize_text(deployed_agent.get('name'), default='Deployed agent')} "
            "uses full autonomous computer access."
        ),
        status="review_required",
        review_required=True,
        payload={
            "lifecycle_action": lifecycle_action,
            "runtime_access_mode": deployed_agent_runtime_contract_service.RUNTIME_ACCESS_MODE_FULL_ACCESS,
            "studio_agent_mode": config.studio_agent_mode,
            "runtime_target": config.runtime_target,
            "computer_automation_enabled": True,
        },
        metadata={
            "runtime_access_review": {
                "required": True,
                "reason": "full_access_runtime",
                "lifecycle_action": lifecycle_action,
            }
        },
        actor_user_id=actor_user_id,
    )


def _privacy_contract_snapshot(
    *,
    config: deployed_agent_config_schema.DeployedAgentConfig,
    existing_metadata: Optional[Dict[str, Any]] = None,
    captured_at: Optional[str] = None,
) -> Dict[str, Any]:
    metadata = _coerce_dict(existing_metadata)
    runtime_supply = _coerce_dict(config.runtime_supply)
    placement = _coerce_dict(runtime_supply.get("placement"))
    supplier = _coerce_dict(runtime_supply.get("supplier"))
    provider_binding = _coerce_dict(runtime_supply.get("provider_binding"))
    automation = config.computer_automation
    runtime_class = _normalize_optional_text(automation.runtime_class)
    screenshots_captured = bool(
        automation.enabled
        and runtime_class in {"virtual_browser", "virtual_desktop", "local_browser", "local_desktop"}
    )
    retention_preset = _normalize_text(config.memory_policy.retention_preset).lower()
    retention_days = _PRIVACY_RETENTION_PRESET_DAYS.get(retention_preset)
    enabled_channels = sorted(
        key
        for key, value in dict(config.channels or {}).items()
        if bool(getattr(value, "enabled", False))
    )
    connector_surfaces = sorted(
        {
            key.replace("_read", "").replace("_append", "").replace("_send", "").replace("_write", "")
            for key in list(config.tool_policy.enabled_tools or [])
            if "_" in key
        }
    )
    connector_scope = sorted(set(enabled_channels + connector_surfaces))
    memory_scope = {
        "read": "scoped_workspace_safe_memory" if config.memory_policy.memory_enabled else "none",
        "write": "scoped_workspace_safe_memory" if config.memory_policy.memory_enabled else "none",
        "context_budget_preset": config.memory_policy.context_budget_preset,
        "retention_preset": config.memory_policy.retention_preset,
    }
    accepted_at = _normalize_optional_text(metadata.get("privacy_contract_accepted_at"))
    accepted_by_user_id = _normalize_optional_text(metadata.get("privacy_contract_accepted_by_user_id"))
    existing_snapshot = _coerce_dict(metadata.get("privacy_contract_snapshot"))
    accepted_at = accepted_at or _normalize_optional_text(existing_snapshot.get("accepted_at"))
    accepted_by_user_id = accepted_by_user_id or _normalize_optional_text(existing_snapshot.get("accepted_by_user_id"))
    snapshot = {
        "schema_version": 1,
        "captured_at": captured_at or _utc_now_iso(),
        "where_it_runs": {
            "studio_agent_mode": config.studio_agent_mode,
            "runtime_placement": config.runtime_placement,
            "runtime_target": config.runtime_target,
            "trust_zone": _normalize_optional_text(placement.get("trust_zone")),
            "supplier_kind": _normalize_optional_text(supplier.get("kind")),
        },
        "model_provider_data_access": {
            "provider": _normalize_optional_text(config.provider)
            or _normalize_optional_text(provider_binding.get("internal_provider")),
            "model": _normalize_optional_text(config.model)
            or _normalize_optional_text(provider_binding.get("internal_model")),
            "supplier_kind": _normalize_optional_text(supplier.get("kind")),
            "processor_visibility": "workspace_scoped",
        },
        "screenshots_captured": screenshots_captured,
        "files_accessible": bool(
            config.studio_agent_mode
            in {
                deployed_agent_runtime_contract_service.STUDIO_AGENT_MODE_CLOUD_COMPUTER,
                deployed_agent_runtime_contract_service.STUDIO_AGENT_MODE_MY_COMPUTER,
                deployed_agent_runtime_contract_service.STUDIO_AGENT_MODE_SELF_HOSTED,
            }
        ),
        "terminal_accessible": bool(
            config.studio_agent_mode
            in {
                deployed_agent_runtime_contract_service.STUDIO_AGENT_MODE_CLOUD_COMPUTER,
                deployed_agent_runtime_contract_service.STUDIO_AGENT_MODE_MY_COMPUTER,
                deployed_agent_runtime_contract_service.STUDIO_AGENT_MODE_SELF_HOSTED,
            }
        ),
        "connectors_accessible": {
            "channels": enabled_channels,
            "tool_connector_surfaces": connector_surfaces,
            "effective_scope": connector_scope,
        },
        "memory_scope": memory_scope,
        "retention_period": {
            "preset": retention_preset,
            "days": retention_days,
        },
        "export_delete_policy": {
            "owner_export_supported": True,
            "external_user_delete_supported": True,
            "external_user_delete_endpoint": "delete_deployed_agent_external_user_data",
            "policy_scope": "workspace_scoped_external_user_data",
        },
        "audit_log": {
            "available": True,
            "surface": "activity_ledger",
            "scope": "workspace_specialist_events",
            "privacy_audit_events_available": bool(_coerce_dict(metadata.get("audit") or {}).get("id") or True),
        },
    }
    if accepted_at and accepted_by_user_id:
        snapshot["accepted_at"] = accepted_at
        snapshot["accepted_by_user_id"] = accepted_by_user_id
    return snapshot


def _computer_safety_contract_snapshot(
    *,
    config: deployed_agent_config_schema.DeployedAgentConfig,
    captured_at: Optional[str] = None,
) -> Dict[str, Any]:
    automation = config.computer_automation
    mode = _normalize_text(config.studio_agent_mode).lower()
    requires_computer_safety = mode in {
        deployed_agent_runtime_contract_service.STUDIO_AGENT_MODE_CLOUD_COMPUTER,
        deployed_agent_runtime_contract_service.STUDIO_AGENT_MODE_MY_COMPUTER,
    }
    approval_actions = sorted(
        {
            _normalize_text(item).lower()
            for item in list(automation.required_owner_approval_actions or [])
            if _normalize_text(item)
        }
    )
    return {
        "schema_version": 1,
        "captured_at": captured_at or _utc_now_iso(),
        "enabled": bool(automation.enabled),
        "required_for_mode": requires_computer_safety,
        "studio_agent_mode": config.studio_agent_mode,
        "isolation_boundary": _normalize_text(automation.isolation_boundary).lower(),
        "inherit_host_environment": bool(automation.inherit_host_environment),
        "filesystem_default_access": _normalize_text(automation.filesystem_default_access).lower(),
        "domain_allowlist": list(automation.allowed_domains or []),
        "download_install_policy": {
            "downloads_allowed": bool(automation.allow_downloads),
            "software_install_allowed": bool(automation.allow_software_install),
        },
        "terminal_command_policy": _normalize_text(automation.terminal_command_policy).lower(),
        "sensitive_action_confirmation_required": bool(automation.sensitive_action_confirmation_required),
        "session_timeout_seconds": int(automation.idle_timeout_seconds or 0),
        "max_runtime_seconds": int(automation.max_session_runtime_seconds or 0),
        "screenshot_session_recording": {
            "screenshots_enabled": bool(automation.screenshot_capture_enabled),
            "recording_policy": _normalize_text(automation.session_recording_policy).lower(),
        },
        "emergency_stop_enabled": bool(automation.emergency_stop_enabled),
        "required_owner_approval_actions": approval_actions,
    }


def _validate_privacy_contract_snapshot(snapshot: Any) -> bool:
    if not isinstance(snapshot, dict):
        return False
    keys = set(snapshot.keys())
    return _PRIVACY_CONTRACT_REQUIRED_KEYS.issubset(keys)


def _validate_privacy_contract_acceptance(snapshot: Any) -> bool:
    if not _validate_privacy_contract_snapshot(snapshot):
        return False
    return bool(_normalize_optional_text(snapshot.get("accepted_at"))) and bool(
        _normalize_optional_text(snapshot.get("accepted_by_user_id"))
    )


def _validate_computer_safety_contract_snapshot(
    snapshot: Any,
    *,
    require_for_mode: bool = False,
) -> bool:
    if not isinstance(snapshot, dict):
        return False
    keys = set(snapshot.keys())
    if not _COMPUTER_SAFETY_REQUIRED_KEYS.issubset(keys):
        return False
    if bool(snapshot.get("inherit_host_environment")):
        return False
    if _normalize_text(snapshot.get("filesystem_default_access")).lower() not in {"none", "session_scoped", "workspace_scoped"}:
        return False
    terminal_policy = _normalize_text(snapshot.get("terminal_command_policy")).lower()
    if terminal_policy not in {"allow", "blocked", "allowlist", "review_required"}:
        return False
    if not bool(snapshot.get("sensitive_action_confirmation_required")):
        return False
    if int(snapshot.get("session_timeout_seconds") or 0) <= 0:
        return False
    if int(snapshot.get("max_runtime_seconds") or 0) <= 0:
        return False
    if not bool(snapshot.get("emergency_stop_enabled")):
        return False
    approval_actions = {
        _normalize_text(item).lower()
        for item in list(snapshot.get("required_owner_approval_actions") or [])
        if _normalize_text(item)
    }
    if not _COMPUTER_SAFETY_REQUIRED_OWNER_APPROVAL_ACTIONS.issubset(approval_actions):
        return False
    if require_for_mode and not bool(snapshot.get("required_for_mode")):
        return False
    return True


def _deploy_readiness_blockers(
    *,
    config: deployed_agent_config_schema.DeployedAgentConfig,
    privacy_snapshot: Dict[str, Any],
    computer_safety_snapshot: Dict[str, Any],
) -> List[str]:
    blockers: List[str] = []
    if not _normalize_text(config.name):
        blockers.append("Agent name is required before deployment.")
    if not _normalize_text(config.persona):
        blockers.append("Agent persona is required before deployment.")
    if not _normalize_text(config.system_prompt):
        blockers.append("Agent system prompt is required before deployment.")
    if config.commerce_policy.monthly_cost_cap_usd is None:
        blockers.append("Monthly budget cap is required before deployment.")
    if not _normalize_text(config.escalation_policy.preset):
        blockers.append("Safety escalation preset is required before deployment.")
    if not _normalize_text(config.escalation_policy.handoff_mode):
        blockers.append("Safety handoff mode is required before deployment.")
    if not _validate_privacy_contract_snapshot(privacy_snapshot):
        blockers.append("Complete privacy contract snapshot is required before deployment.")
    elif not _validate_privacy_contract_acceptance(privacy_snapshot):
        blockers.append("Privacy contract must be accepted before deployment.")
    requires_computer_safety = config.studio_agent_mode in {
        deployed_agent_runtime_contract_service.STUDIO_AGENT_MODE_CLOUD_COMPUTER,
        deployed_agent_runtime_contract_service.STUDIO_AGENT_MODE_MY_COMPUTER,
    }
    if requires_computer_safety and not _validate_computer_safety_contract_snapshot(
        computer_safety_snapshot,
        require_for_mode=True,
    ):
        blockers.append("Complete computer safety contract snapshot is required before deployment.")
    return blockers


def _terminal_run_state(value: Any) -> bool:
    return _normalize_text(value).lower() in {
        "completed",
        "failed",
        "timeout",
        "cancelled",
        "canceled",
        "stopped",
        "aborted",
    }


def _run_belongs_to_deployed_agent(run: Dict[str, Any], deployed_agent: Dict[str, Any]) -> bool:
    if not isinstance(run, dict) or not isinstance(deployed_agent, dict):
        return False
    context = _coerce_dict(run.get("context"))
    metadata = _coerce_dict(context.get("metadata") or run.get("metadata"))
    deployed_agent_id = _normalize_optional_text(deployed_agent.get("id"))
    backing_install_id = _normalize_optional_text(deployed_agent.get("backing_install_id"))
    return bool(
        deployed_agent_id
        and _normalize_optional_text(metadata.get("deployed_agent_id")) == deployed_agent_id
    ) or bool(
        backing_install_id
        and _normalize_optional_text(metadata.get("install_id") or run.get("install_id")) == backing_install_id
    )


def _stop_deployed_agent_live_runs(
    *,
    deployed_agent: Dict[str, Any],
    reason: str,
    stopped_by_user_id: Optional[str],
) -> List[str]:
    stopped_run_ids: List[str] = []
    try:
        live_runs = list(run_state_repository.sync_list_live_runs() or [])
    except Exception:
        LOGGER.exception("Failed to list live runs for deployed-agent kill switch")
        return stopped_run_ids
    for run in live_runs:
        if not isinstance(run, dict) or not _run_belongs_to_deployed_agent(run, deployed_agent):
            continue
        run_id = _normalize_optional_text(run.get("run_id") or run.get("id"))
        if not run_id or _terminal_run_state(run.get("status") or run.get("state")):
            continue
        payload = dict(run)
        context = _coerce_dict(payload.get("context"))
        metadata = _coerce_dict(context.get("metadata"))
        metadata.update(
            {
                "deployed_agent_stop_reason": reason,
                "deployed_agent_stopped_at": _utc_now_iso(),
                "deployed_agent_stopped_by_user_id": _normalize_optional_text(stopped_by_user_id),
            }
        )
        context["metadata"] = metadata
        payload["context"] = context
        payload["status"] = "stopped"
        payload["state"] = "stopped"
        try:
            run_state_repository.sync_update_live_run_if_version_matches(
                run_id,
                _normalize_text(run.get("workspace_id"), default=_normalize_text(deployed_agent.get("owner_workspace_id"))),
                _normalize_text(run.get("tenant_id"), default=_normalize_text(deployed_agent.get("tenant_id"))),
                "stopped",
                payload,
                _normalize_text(run.get("trace_id"), default=f"deployed_agent_stop_{uuid.uuid4().hex}"),
                expected_version=int(run.get("version") or 0),
            )
            stopped_run_ids.append(run_id)
        except Exception:
            LOGGER.exception("Failed to stop deployed-agent live run", extra={"run_id": run_id})
    return stopped_run_ids


def _disabled_channels(channels: Any) -> Dict[str, Dict[str, Any]]:
    disabled: Dict[str, Dict[str, Any]] = {}
    for key, value in _normalize_channels(channels).items():
        payload = dict(value or {})
        payload["enabled"] = False
        disabled[key] = payload
    return disabled


def _raise_http_from_entitlement_error(error: Exception) -> None:
    status_code = 403
    if isinstance(error, entitlements_service.EntitlementQuotaExceededError):
        status_code = 409
    detail = _normalize_text(getattr(error, "message", ""), default=str(error))
    raise HTTPException(status_code=status_code, detail=detail) from error


def _runtime_target_by_id(payload: Any, target_id: str) -> Dict[str, Any]:
    targets = payload.get("targets") if isinstance(payload, dict) and isinstance(payload.get("targets"), list) else []
    for item in targets:
        if not isinstance(item, dict):
            continue
        if _normalize_text(item.get("target_id")).lower() == _normalize_text(target_id).lower():
            return dict(item)
    return {}


def _self_hosted_required_capabilities(config: deployed_agent_config_schema.DeployedAgentConfig) -> List[str]:
    automation = config.computer_automation
    if not bool(automation.enabled):
        return []
    runtime_class = _normalize_text(automation.runtime_class).lower()
    if runtime_class == "virtual_code_sandbox":
        return ["shell.execute"]
    if runtime_class in {"virtual_browser", "virtual_desktop", "local_browser", "local_desktop"}:
        return ["computer_control"]
    return []


def _self_hosted_attachment_by_runtime_profile_id(runtime_inventory: Any, runtime_profile_id: str) -> Dict[str, Any]:
    inventory = runtime_inventory if isinstance(runtime_inventory, dict) else {}
    for item in list(inventory.get("attachments") or []):
        if not isinstance(item, dict):
            continue
        if _normalize_text(item.get("attachment_kind")).lower() != "self_hosted_business_node":
            continue
        if _normalize_text(item.get("runtime_profile_id")) == runtime_profile_id:
            return dict(item)
    return {}


def _self_hosted_runtime_binding_payload(
    *,
    config: deployed_agent_config_schema.DeployedAgentConfig,
    runtime_attachment: Dict[str, Any],
    workspace_id: str,
) -> Dict[str, Any]:
    required_capabilities = _self_hosted_required_capabilities(config)
    attachment_capabilities = [
        _normalize_text(item).lower()
        for item in list(runtime_attachment.get("capabilities") or [])
        if _normalize_text(item)
    ]
    allowed_capabilities = (
        [cap for cap in required_capabilities if cap in attachment_capabilities]
        if required_capabilities
        else list(attachment_capabilities)
    )
    automation = config.computer_automation
    quota_max_concurrent = int(automation.max_concurrent_sessions or 0)
    attachment_max_concurrent = int(runtime_attachment.get("max_concurrent_sessions") or 0)
    if quota_max_concurrent > 0 and attachment_max_concurrent > 0:
        quota_max_concurrent = min(quota_max_concurrent, attachment_max_concurrent)
    elif quota_max_concurrent <= 0:
        quota_max_concurrent = max(attachment_max_concurrent, 1)
    return {
        "binding_version": 1,
        "runtime_target": "self_host_runtime",
        "workspace_id": workspace_id,
        "runtime_node_id": _normalize_optional_text(runtime_attachment.get("runtime_node_id")),
        "runtime_attachment_id": _normalize_optional_text(runtime_attachment.get("attachment_id")),
        "runtime_profile_id": _normalize_optional_text(runtime_attachment.get("runtime_profile_id"))
        or _normalize_optional_text(config.runtime_profile_id),
        "allowed_capabilities": allowed_capabilities,
        "filesystem_scope": _normalize_text(automation.filesystem_default_access, default="none"),
        "domain_allowlist": list(automation.allowed_domains or []),
        "approval_policy": {
            "runtime_access_mode": _normalize_text(automation.runtime_access_mode, default="default_guarded"),
            "requires_owner_approval": bool(automation.requires_owner_approval),
            "required_owner_approval_actions": list(automation.required_owner_approval_actions or []),
            "sensitive_action_confirmation_required": bool(automation.sensitive_action_confirmation_required),
            "terminal_command_policy": _normalize_text(automation.terminal_command_policy, default="blocked"),
        },
        "quota_policy": {
            "max_concurrent_sessions": int(quota_max_concurrent),
            "max_session_runtime_seconds": int(automation.max_session_runtime_seconds or 0),
            "idle_timeout_seconds": int(automation.idle_timeout_seconds or 0),
            "daily_budget_usd": float(automation.daily_budget_usd or 0.0),
            "monthly_budget_usd": float(automation.monthly_budget_usd or 0.0),
            "monthly_cost_cap_usd": float(config.commerce_policy.monthly_cost_cap_usd or 0.0),
        },
    }


def _metadata_with_self_hosted_runtime_binding(
    *,
    metadata: Dict[str, Any],
    config: deployed_agent_config_schema.DeployedAgentConfig,
    runtime_inventory: Any,
    workspace_id: str,
    require_binding: bool,
) -> Dict[str, Any]:
    payload = dict(metadata or {})
    if (
        _normalize_text(config.studio_agent_mode).lower()
        != deployed_agent_runtime_contract_service.STUDIO_AGENT_MODE_SELF_HOSTED
    ):
        payload.pop("self_hosted_runtime_binding", None)
        return payload

    runtime_profile_id = _normalize_optional_text(config.runtime_profile_id)
    if not runtime_profile_id:
        if require_binding:
            raise ValueError(
                "self_hosted_agent requires explicit runtime_profile_id agent-to-node binding."
            )
        payload.pop("self_hosted_runtime_binding", None)
        return payload

    attachment = _self_hosted_attachment_by_runtime_profile_id(runtime_inventory, runtime_profile_id)
    if not attachment:
        if require_binding:
            raise ValueError(
                "self_hosted_agent requires a registered self-hosted node matching runtime_profile_id."
            )
        payload.pop("self_hosted_runtime_binding", None)
        return payload

    runtime_attachment_service.ensure_self_hosted_node_gate(
        attachment=attachment,
        workspace_id=workspace_id,
        required_capabilities=_self_hosted_required_capabilities(config),
    )
    payload["self_hosted_runtime_binding"] = _self_hosted_runtime_binding_payload(
        config=config,
        runtime_attachment=attachment,
        workspace_id=workspace_id,
    )
    return payload


def _mode_for_deployed_agent_record(record: Dict[str, Any]) -> str:
    if not isinstance(record, dict):
        return deployed_agent_runtime_contract_service.STUDIO_AGENT_MODE_TEXT
    try:
        return _config_from_record(record).studio_agent_mode
    except Exception:
        return deployed_agent_runtime_contract_service.STUDIO_AGENT_MODE_TEXT


def _is_live_state(value: Any) -> bool:
    return deployment_state_is_publicly_routable(value)


def _mode_counts_for_deployed_agents(
    rows: List[Dict[str, Any]],
    *,
    live_only: bool = False,
    exclude_deployed_agent_id: Optional[str] = None,
) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    excluded = _normalize_optional_text(exclude_deployed_agent_id)
    for row in list(rows or []):
        if not isinstance(row, dict):
            continue
        if excluded and _normalize_optional_text(row.get("id")) == excluded:
            continue
        if live_only and not _is_live_state(row.get("deployment_state")):
            continue
        mode = _mode_for_deployed_agent_record(row)
        counts[mode] = int(counts.get(mode) or 0) + 1
    return counts


def _live_running_mode_counts(
    rows: List[Dict[str, Any]],
    *,
    exclude_deployed_agent_id: Optional[str] = None,
) -> Dict[str, int]:
    deployed_agent_mode: Dict[str, str] = {}
    excluded = _normalize_optional_text(exclude_deployed_agent_id)
    for row in list(rows or []):
        if not isinstance(row, dict):
            continue
        deployed_agent_id = _normalize_optional_text(row.get("id"))
        if not deployed_agent_id:
            continue
        if excluded and deployed_agent_id == excluded:
            continue
        deployed_agent_mode[deployed_agent_id] = _mode_for_deployed_agent_record(row)
    terminal_states = {"completed", "failed", "cancelled", "canceled", "timeout", "aborted"}
    counts: Dict[str, int] = {}
    try:
        live_runs = list(run_state_repository.sync_list_live_runs() or [])
    except Exception:
        live_runs = []
    for run in live_runs:
        if not isinstance(run, dict):
            continue
        status = _normalize_text(run.get("status")).lower()
        if status in terminal_states:
            continue
        context = _coerce_dict(run.get("context"))
        metadata = _coerce_dict(context.get("metadata"))
        deployed_agent_id = _normalize_optional_text(metadata.get("deployed_agent_id"))
        if not deployed_agent_id:
            continue
        mode = deployed_agent_mode.get(deployed_agent_id)
        if not mode:
            continue
        counts[mode] = int(counts.get(mode) or 0) + 1
    return counts


def _quota_for_mode(
    *,
    quota_policy: Dict[str, Any],
    studio_agent_mode: str,
    key: str,
    default_value: int = 0,
) -> int:
    global_value = int(quota_policy.get(key) or default_value)
    mode_limits = _coerce_dict(quota_policy.get("mode_limits")).get(studio_agent_mode)
    mode_value = int(_coerce_dict(mode_limits).get(key) or 0) if isinstance(mode_limits, dict) else 0
    if mode_value > 0:
        return mode_value
    return global_value


def _enforce_phase8_quota_controls(
    *,
    workspace: Dict[str, Any],
    config: deployed_agent_config_schema.DeployedAgentConfig,
    stage: str,
    runtime_targets: Any = None,
    existing_deployed_agents: Optional[List[Dict[str, Any]]] = None,
    subject_deployed_agent_id: Optional[str] = None,
) -> None:
    quota_policy = entitlements_service.deployed_agent_quota_defaults(
        workspace=workspace,
        runtime_targets=runtime_targets if isinstance(runtime_targets, dict) else None,
    )
    stage_token = _normalize_text(stage).lower()
    mode = config.studio_agent_mode
    rows = [dict(item) for item in list(existing_deployed_agents or []) if isinstance(item, dict)]
    created_counts = _mode_counts_for_deployed_agents(
        rows,
        live_only=False,
        exclude_deployed_agent_id=subject_deployed_agent_id if stage_token == "update" else None,
    )
    live_counts = _mode_counts_for_deployed_agents(
        rows,
        live_only=True,
        exclude_deployed_agent_id=subject_deployed_agent_id if stage_token in {"update", "deploy"} else None,
    )

    created_limit_global = int(quota_policy.get("created_agents_max") or 0)
    created_limit_mode = _quota_for_mode(
        quota_policy=quota_policy,
        studio_agent_mode=mode,
        key="created_agents_max",
        default_value=created_limit_global,
    )
    if stage_token == "create":
        if created_limit_global > 0 and len(rows) >= created_limit_global:
            raise _http_conflict("Created-agent quota reached for this workspace.")
        if created_limit_mode > 0 and int(created_counts.get(mode) or 0) >= created_limit_mode:
            raise _http_conflict(f"Created-agent quota reached for {mode}.")

    if stage_token == "deploy":
        live_limit_global = int(quota_policy.get("live_agents_max") or 0)
        live_limit_mode = _quota_for_mode(
            quota_policy=quota_policy,
            studio_agent_mode=mode,
            key="live_agents_max",
            default_value=live_limit_global,
        )
        total_live = sum(int(value or 0) for value in live_counts.values())
        mode_live = int(live_counts.get(mode) or 0)
        if live_limit_global > 0 and total_live >= live_limit_global:
            raise _http_conflict("Live-agent quota reached for this workspace.")
        if live_limit_mode > 0 and mode_live >= live_limit_mode:
            raise _http_conflict(f"Live-agent quota reached for {mode}.")

        running_counts = _live_running_mode_counts(
            rows,
            exclude_deployed_agent_id=subject_deployed_agent_id,
        )
        running_limit_global = int(quota_policy.get("concurrent_running_agents_max") or 0)
        running_limit_mode = _quota_for_mode(
            quota_policy=quota_policy,
            studio_agent_mode=mode,
            key="concurrent_running_agents_max",
            default_value=running_limit_global,
        )
        total_running = sum(int(value or 0) for value in running_counts.values())
        mode_running = int(running_counts.get(mode) or 0)
        if running_limit_global > 0 and total_running >= running_limit_global:
            raise _http_conflict("Concurrent-running-agent quota reached for this workspace.")
        if running_limit_mode > 0 and mode_running >= running_limit_mode:
            raise _http_conflict(f"Concurrent-running-agent quota reached for {mode}.")

    messages_per_day_max = int(quota_policy.get("messages_per_day_max") or 0)
    daily_message_limit = config.customer_policy.daily_message_limit
    if daily_message_limit is not None and messages_per_day_max > 0 and int(daily_message_limit) > messages_per_day_max:
        raise _http_bad_request(
            f"daily_message_limit exceeds workspace quota ({messages_per_day_max} messages/day)."
        )

    tool_calls_per_day_max = int(quota_policy.get("tool_calls_per_day_max") or 0)
    if len(list(config.tool_policy.enabled_tools or [])) > 0 and tool_calls_per_day_max <= 0:
        raise HTTPException(
            status_code=403,
            detail="Tool calls are not available for this workspace quota policy.",
        )

    monthly_spend_usd_max = float(quota_policy.get("monthly_spend_usd_max") or 0.0)
    monthly_cost_cap = config.commerce_policy.monthly_cost_cap_usd
    if monthly_cost_cap is not None and monthly_spend_usd_max > 0 and float(monthly_cost_cap) > monthly_spend_usd_max:
        raise _http_bad_request(
            f"monthly_cost_cap_usd exceeds workspace spend quota (${monthly_spend_usd_max:.2f}/month)."
        )

    runtime_minutes_monthly_max = int(quota_policy.get("runtime_minutes_monthly_max") or 0)
    if (
        config.studio_agent_mode
        == deployed_agent_runtime_contract_service.STUDIO_AGENT_MODE_CLOUD_COMPUTER
        and runtime_minutes_monthly_max <= 0
    ):
        raise HTTPException(
            status_code=403,
            detail="Runtime minutes are not available for this workspace quota policy.",
        )

    if bool(config.computer_automation.enabled):
        sessions_limit = _quota_for_mode(
            quota_policy=quota_policy,
            studio_agent_mode=mode,
            key="computer_sessions_max",
            default_value=int(quota_policy.get("computer_sessions_max") or 0),
        )
        if sessions_limit <= 0:
            raise HTTPException(
                status_code=403,
                detail="Computer sessions are not available for this workspace quota policy.",
            )
        if int(config.computer_automation.max_concurrent_sessions or 0) > sessions_limit:
            raise _http_bad_request(
                f"computer_automation.max_concurrent_sessions exceeds workspace quota ({sessions_limit})."
            )

    if bool(config.memory_policy.memory_enabled):
        storage_limit_mb = int(quota_policy.get("storage_memory_size_mb_max") or 0)
        if storage_limit_mb <= 0:
            raise HTTPException(
                status_code=403,
                detail="Cloud memory storage is not available for this workspace quota policy.",
            )


def _enforce_mode_capability_matrix(
    *,
    config: deployed_agent_config_schema.DeployedAgentConfig,
    stage: str,
    runtime_targets: Any = None,
    explicit_mode: bool = False,
) -> None:
    runtime_supply = config.runtime_supply if isinstance(config.runtime_supply, dict) else {}
    runtime_supply_supplier = (
        runtime_supply.get("supplier")
        if isinstance(runtime_supply.get("supplier"), dict)
        else {}
    )
    runtime_placement_for_validation = config.runtime_placement
    runtime_supply_for_validation: Dict[str, Any] = runtime_supply
    mode_for_validation = config.studio_agent_mode
    if not explicit_mode:
        normalized_placement = deployed_agent_runtime_contract_service.normalize_runtime_placement(
            config.runtime_placement,
            runtime_target=config.runtime_target,
        )
        normalized_target = _normalize_text(config.runtime_target).lower() or deployed_agent_runtime_contract_service.runtime_target_for_placement(
            normalized_placement
        )
        expected_target = deployed_agent_runtime_contract_service.runtime_target_for_placement(normalized_placement)
        if normalized_target != expected_target:
            runtime_placement_for_validation = deployed_agent_runtime_contract_service.normalize_runtime_placement(
                config.runtime_target
            )
            runtime_supply_for_validation = {}
        mode_for_validation = deployed_agent_runtime_contract_service.infer_studio_agent_mode(
            runtime_placement=runtime_placement_for_validation,
            runtime_target=config.runtime_target,
            runtime_supplier=runtime_supply_supplier.get("kind") if runtime_supply_for_validation else None,
        )
    try:
        deployed_agent_runtime_contract_service.validate_mode_capability_matrix(
            studio_agent_mode=mode_for_validation,
            runtime_placement=runtime_placement_for_validation,
            runtime_target=config.runtime_target,
            runtime_supply=runtime_supply_for_validation,
            computer_automation=config.computer_automation.model_dump(exclude_none=True),
            stage=stage,
            runtime_targets=runtime_targets,
        )
    except ValueError as error:
        if _normalize_text(stage).lower() == "deploy":
            raise _http_conflict(str(error)) from error
        raise _http_bad_request(str(error)) from error


def _enforce_runtime_eligibility(
    *,
    workspace: Dict[str, Any],
    workspace_id: str,
    config: deployed_agent_config_schema.DeployedAgentConfig,
    stage: str,
    runtime_targets: Any = None,
    runtime_inventory: Any = None,
    explicit_mode: bool = False,
    existing_deployed_agents: Optional[List[Dict[str, Any]]] = None,
    subject_deployed_agent_id: Optional[str] = None,
) -> None:
    stage_token = _normalize_text(stage).lower()
    entitlement_state = entitlements_service.resolve_workspace_entitlement_state(workspace=workspace)
    entitlements = dict(entitlement_state.entitlements or {})
    runtime_supply = config.runtime_supply if isinstance(config.runtime_supply, dict) else {}
    runtime_supply_supplier = (
        runtime_supply.get("supplier")
        if isinstance(runtime_supply.get("supplier"), dict)
        else {}
    )
    effective_mode = config.studio_agent_mode
    if not explicit_mode:
        normalized_placement = deployed_agent_runtime_contract_service.normalize_runtime_placement(
            config.runtime_placement,
            runtime_target=config.runtime_target,
        )
        normalized_target = _normalize_text(config.runtime_target).lower() or deployed_agent_runtime_contract_service.runtime_target_for_placement(
            normalized_placement
        )
        expected_target = deployed_agent_runtime_contract_service.runtime_target_for_placement(normalized_placement)
        effective_placement = (
            deployed_agent_runtime_contract_service.normalize_runtime_placement(config.runtime_target)
            if normalized_target != expected_target
            else normalized_placement
        )
        effective_mode = deployed_agent_runtime_contract_service.infer_studio_agent_mode(
            runtime_placement=effective_placement,
            runtime_target=config.runtime_target,
            runtime_supplier=runtime_supply_supplier.get("kind"),
        )

    if effective_mode == deployed_agent_runtime_contract_service.STUDIO_AGENT_MODE_CLOUD_COMPUTER:
        if not bool(entitlements.get("virtual_computer_runtime_enabled")):
            raise HTTPException(
                status_code=403,
                detail="Cloud Computer mode is not available for this workspace plan.",
            )

    for channel_key, channel_config in dict(config.channels or {}).items():
        if not bool(getattr(channel_config, "enabled", False)):
            continue
        try:
            entitlements_service.enforce_channel_surface_access_for_workspace_id(
                channel_key,
                workspace_id=workspace_id,
                workspace=workspace,
            )
        except entitlements_service.EntitlementError as error:
            _raise_http_from_entitlement_error(error)

    if len(list(config.tool_policy.enabled_tools or [])) > 0 and not bool(entitlements.get("premium_tools_enabled")):
        raise HTTPException(
            status_code=403,
            detail="Selected tools are not available for this workspace plan.",
        )

    if config.memory_policy.memory_enabled:
        memory_limits = entitlements_service.memory_policy_defaults(workspace=workspace)
        storage_limit_mb = int(memory_limits.get("cloud_memory_storage_mb") or 0)
        retention_limit_days = int(memory_limits.get("cloud_memory_retention_days") or 0)
        if storage_limit_mb <= 0 or retention_limit_days <= 0:
            raise HTTPException(
                status_code=403,
                detail="Memory is not available for this workspace plan.",
            )

    automation = config.computer_automation
    if bool(automation.enabled):
        if len(list(automation.allowed_domains or [])) == 0:
            raise _http_bad_request("Computer automation requires a non-empty domain allowlist.")
        if int(automation.max_concurrent_sessions or 0) <= 0:
            raise _http_bad_request("Computer automation requires max_concurrent_sessions > 0.")
        if int(automation.max_session_runtime_seconds or 0) <= 0:
            raise _http_bad_request("Computer automation requires max_session_runtime_seconds > 0.")
        daily_budget = automation.daily_budget_usd
        monthly_budget = automation.monthly_budget_usd
        if daily_budget is None:
            raise _http_bad_request("Computer automation requires daily_budget_usd.")
        if monthly_budget is None:
            raise _http_bad_request("Computer automation requires monthly_budget_usd.")
        if float(monthly_budget) < float(daily_budget):
            raise _http_bad_request("Computer automation requires monthly_budget_usd >= daily_budget_usd.")
        if config.commerce_policy.monthly_cost_cap_usd is not None:
            if float(config.commerce_policy.monthly_cost_cap_usd) < float(monthly_budget):
                raise _http_bad_request(
                    "monthly_cost_cap_usd must be greater than or equal to computer_automation.monthly_budget_usd."
                )

    expected_target_id = deployed_agent_runtime_contract_service.STUDIO_AGENT_MODE_DEPLOY_TARGETS.get(effective_mode)
    should_check_targets = stage_token == "deploy" or (
        stage_token == "create" and isinstance(runtime_targets, dict)
    )
    if should_check_targets and expected_target_id:
        target_record = _runtime_target_by_id(runtime_targets, expected_target_id)
        if not target_record:
            raise _http_conflict(
                f"{effective_mode} requires runtime target {expected_target_id}, but it is not present for this workspace."
            )
        if not bool(target_record.get("available")):
            raise _http_conflict(
                f"{effective_mode} requires runtime target {expected_target_id}, but it is unavailable."
            )
        if (
            stage_token == "deploy"
            and effective_mode == deployed_agent_runtime_contract_service.STUDIO_AGENT_MODE_SELF_HOSTED
        ):
            if not bool(target_record.get("online")):
                raise _http_conflict(
                    "self_hosted_agent requires runtime target self_host_runtime to be online before deployment."
                )
            if not bool(target_record.get("healthy")):
                raise _http_conflict(
                    "self_hosted_agent requires runtime target self_host_runtime to be healthy before deployment."
                )
            if int(target_record.get("attachment_count") or 0) <= 0:
                raise _http_conflict(
                    "self_hosted_agent requires at least one registered self-hosted node before deployment."
                )
            if not _normalize_optional_text(config.runtime_profile_id):
                raise _http_conflict(
                    "self_hosted_agent requires explicit runtime_profile_id agent-to-node binding before deployment."
                )
            node_attachment = _self_hosted_attachment_by_runtime_profile_id(
                runtime_inventory,
                _normalize_optional_text(config.runtime_profile_id) or "",
            )
            if not node_attachment:
                raise _http_conflict(
                    "self_hosted_agent requires a registered self-hosted node matching runtime_profile_id before deployment."
                )
            try:
                runtime_attachment_service.ensure_self_hosted_node_gate(
                    attachment=node_attachment,
                    workspace_id=workspace_id,
                    required_capabilities=_self_hosted_required_capabilities(config),
                )
            except runtime_attachment_service.RuntimeAttachmentSelectionError as error:
                raise _http_conflict(str(error)) from error
    _enforce_phase8_quota_controls(
        workspace=workspace,
        config=config,
        stage=stage,
        runtime_targets=runtime_targets,
        existing_deployed_agents=existing_deployed_agents,
        subject_deployed_agent_id=subject_deployed_agent_id,
    )


def _tool_toggles_from_config(
    config: deployed_agent_config_schema.DeployedAgentConfig,
) -> Dict[str, bool]:
    enabled = {
        str(item or "").strip().lower()
        for item in list(config.tool_policy.enabled_tools or [])
        if str(item or "").strip()
    }
    return {
        str(item.get("id") or "").strip().lower(): str(item.get("id") or "").strip().lower() in enabled
        for item in _STUDIO_TOOL_SCOPE_CATALOG
        if str(item.get("id") or "").strip()
    }


def _clean_mapping(value: Any) -> Dict[str, Any]:
    payload = _coerce_dict(value)
    return {
        key: item
        for key, item in payload.items()
        if item not in (None, "", [], {})
    }


def _knowledge_reference_summary(item: Any) -> Optional[Dict[str, Any]]:
    payload = _coerce_dict(item)
    if not payload:
        return None
    reference_id = (
        _normalize_optional_text(payload.get("id"))
        or _normalize_optional_text(payload.get("uri"))
        or _normalize_optional_text(payload.get("path"))
        or _normalize_optional_text(payload.get("document_id"))
    )
    label = (
        _normalize_optional_text(payload.get("label"))
        or _normalize_optional_text(payload.get("title"))
        or _normalize_optional_text(payload.get("name"))
        or reference_id
    )
    source_kind = (
        _normalize_optional_text(payload.get("source_kind"))
        or _normalize_optional_text(payload.get("kind"))
        or ("google_sheet" if "sheet" in _normalize_text(payload.get("uri")).lower() else "document")
    )
    if not label and not source_kind:
        return None
    summary = {
        "id": reference_id,
        "label": label,
        "source_kind": source_kind,
        "uri": _normalize_optional_text(payload.get("uri")),
        "path": _normalize_optional_text(payload.get("path")),
    }
    return {
        key: value
        for key, value in summary.items()
        if value is not None
    }


def _knowledge_query_tokens(query: Any) -> List[str]:
    normalized = _normalize_text(query).lower()
    tokens: List[str] = []
    current: List[str] = []
    for char in normalized:
        if char.isalnum():
            current.append(char)
            continue
        if current:
            token = "".join(current)
            if len(token) >= 2 and token not in tokens:
                tokens.append(token)
            current = []
    if current:
        token = "".join(current)
        if len(token) >= 2 and token not in tokens:
            tokens.append(token)
    return tokens


def _knowledge_reference_search_text(source: Dict[str, Any]) -> str:
    parts = [
        source.get("id"),
        source.get("label"),
        source.get("source_kind"),
        source.get("uri"),
        source.get("path"),
    ]
    return " ".join(_normalize_text(part).lower() for part in parts if _normalize_text(part))


def _score_knowledge_reference(source: Dict[str, Any], query_tokens: List[str]) -> int:
    search_text = _knowledge_reference_search_text(source)
    if not search_text:
        return 0
    return sum(1 for token in query_tokens if token in search_text)


async def verify_deployed_agent_knowledge_retrieval(
    *,
    deployed_agent_id: str,
    current_user: Optional[Dict[str, Any]],
    owner_workspace_id: str,
    query: str,
    limit: int = 5,
) -> Dict[str, Any]:
    resolved_workspace_id = require_deployed_agent_admin_access(
        current_user=current_user,
        workspace_id=owner_workspace_id,
    )
    normalized_query = _normalize_text(query)
    if not normalized_query:
        raise ValueError("query is required.")
    workspace = await control_plane_repository.get_workspace_by_id(resolved_workspace_id)
    if not isinstance(workspace, dict):
        raise _http_bad_request("Workspace is unavailable.")
    tenant_id = _normalize_text(workspace.get("tenant_id"))
    deployed_agent = await control_plane_repository.get_deployed_agent_by_id(
        deployed_agent_id,
        tenant_id=tenant_id,
        owner_workspace_id=resolved_workspace_id,
    )
    if not isinstance(deployed_agent, dict):
        raise ValueError("Deployed agent not found.")
    _enforce_deployed_agent_service_decision(
        "knowledge_verify",
        deployed_agent=deployed_agent,
        tenant_id=tenant_id,
        workspace_id=resolved_workspace_id,
        current_user=current_user,
    )
    config = _config_from_record(deployed_agent)
    sources = [
        summary
        for summary in (
            _knowledge_reference_summary(item)
            for item in list(config.knowledge_sources or [])
        )
        if isinstance(summary, dict)
    ]
    query_tokens = _knowledge_query_tokens(normalized_query)
    scored_sources = [
        {**source, "score": score}
        for source in sources
        for score in [_score_knowledge_reference(source, query_tokens)]
        if score > 0
    ]
    scored_sources.sort(
        key=lambda source: (
            -int(source.get("score") or 0),
            _normalize_text(source.get("label")).lower(),
        )
    )
    bounded_limit = max(1, min(int(limit or 5), 10))
    matched_sources = scored_sources[:bounded_limit]
    if not sources:
        return {
            "workspace_id": resolved_workspace_id,
            "tenant_id": tenant_id,
            "deployed_agent_id": deployed_agent_id,
            "query": normalized_query,
            "status": "no_sources",
            "message": "No trusted knowledge source references are configured for this agent.",
            "verification_kind": "content_retrieval",
            "content_retrieval_available": False,
            "source_count": 0,
            "matched_sources": [],
            "matched_chunks": [],
            "confidence_score": 0.0,
            "checked_at": _utc_now_iso(),
        }
    ingestion_status: Dict[str, Any] = {}
    ingestion_error: Optional[str] = None
    try:
        ingestion_status = await knowledge_rag_service.ingest_workspace_knowledge_files(
            tenant_id=tenant_id,
            workspace_id=resolved_workspace_id,
            agent_id=deployed_agent_id,
            user_id=_normalize_text((current_user or {}).get("user_id")),
        )
    except Exception as exc:
        ingestion_error = str(exc)
    try:
        retrieval = await knowledge_rag_service.retrieve_knowledge(
            tenant_id=tenant_id,
            workspace_id=resolved_workspace_id,
            query=normalized_query,
            surface="studio",
            source_surface="studio_knowledge_verify",
            agent_id=deployed_agent_id,
            user_id=_normalize_text((current_user or {}).get("user_id")),
            allowed_source_refs=knowledge_rag_service.source_reference_filter_values(sources),
            top_k=bounded_limit,
            payer="local",
        )
    except Exception as exc:
        _logger.warning("Knowledge retrieval index unavailable for agent %s in workspace %s: %s", deployed_agent_id, resolved_workspace_id, exc)
        retrieval = {
            "status": "index_missing",
            "message": "Knowledge retrieval is temporarily unavailable. Please try again.",
            "content_retrieval_available": False,
            "matched_chunks": [],
            "confidence_score": 0.0,
        }
    status = str(retrieval.get("status") or "index_missing").strip().lower() or "index_missing"
    if status == "retrieval_available":
        message = "Retrieved source-grounded knowledge chunks for this agent."
    elif status == "no_hits":
        message = "Indexed knowledge exists for this agent, but no chunk matched this query."
    elif matched_sources:
        message = "Saved knowledge references matched, but no indexed content chunks are available for citation retrieval."
    else:
        message = "No indexed content chunks are available for the saved knowledge references."
    metadata = {
        "ingestion": ingestion_status,
        "ingestion_error": ingestion_error,
        "retrieval_event": retrieval.get("retrieval_event"),
    }
    return {
        "workspace_id": resolved_workspace_id,
        "tenant_id": tenant_id,
        "deployed_agent_id": deployed_agent_id,
        "query": normalized_query,
        "status": status,
        "message": message,
        "verification_kind": "content_retrieval",
        "content_retrieval_available": bool(retrieval.get("content_retrieval_available")),
        "source_count": len(sources),
        "matched_sources": matched_sources,
        "matched_chunks": list(retrieval.get("matched_chunks") or retrieval.get("chunks") or []),
        "confidence_score": float(retrieval.get("confidence_score") or 0.0),
        "retrieved_chunk_ids": list(retrieval.get("retrieved_chunk_ids") or []),
        "metadata": metadata,
        "checked_at": _utc_now_iso(),
    }


def _safe_knowledge_filename(filename: Any) -> str:
    raw = Path(str(filename or "").replace("\\", "/")).name.strip()
    if not raw:
        raise _http_bad_request("file_name is required.")
    stem = "".join(ch if ch.isalnum() or ch in {"-", "_", "."} else "-" for ch in raw)
    stem = "-".join(part for part in stem.split("-") if part).strip(".")
    if not stem:
        raise _http_bad_request("file_name is invalid.")
    suffix = Path(stem).suffix.lower()
    if suffix not in knowledge_rag_service.SUPPORTED_KNOWLEDGE_EXTENSIONS:
        supported = ", ".join(sorted(knowledge_rag_service.SUPPORTED_KNOWLEDGE_EXTENSIONS))
        raise _http_bad_request(f"Unsupported knowledge file type. Supported files: {supported}.")
    base = stem[: -len(suffix)] if suffix else stem
    return f"{base[:150]}{suffix}"


def _unique_knowledge_file_path(root: Path, filename: str, content_hash: str) -> Path:
    candidate = root / filename
    if not candidate.exists():
        return candidate
    path = Path(filename)
    suffix = path.suffix
    stem = path.name[: -len(suffix)] if suffix else path.name
    return root / f"{stem[:120]}-{content_hash[:10]}{suffix}"


class DeployedAgentKnowledgeRustGateError(RuntimeError):
    pass


def _enforce_deployed_agent_knowledge_file_write(
    *,
    deployed_agent_id: str,
    workspace_id: str,
    destination: Path,
    safe_filename: str,
    content_hash: str,
    content_bytes: int,
) -> None:
    payload = {
        "deployed_agent_id": _normalize_text(deployed_agent_id),
        "workspace_id": _normalize_text(workspace_id),
        "destination": str(destination),
        "file_name": str(safe_filename or "").strip(),
        "content_hash_prefix": str(content_hash or "").strip()[:16],
        "content_bytes": max(0, int(content_bytes or 0)),
    }
    try:
        decision = rust_runtime_kernel_client.runtime_state_store_decision(
            operation="write_deployed_agent_knowledge_file",
            state_class="deployed_agent_knowledge_files",
            workspace_id=str(payload["workspace_id"]),
            actor_id="system",
            status="active",
            payload=payload,
            payload_bytes=int(payload["content_bytes"]),
            workspace_access=True,
            owner_access=True,
        )
        decision = rust_runtime_kernel_client.enforce_kernel_decision(
            "runtime-state-store-decision",
            decision,
        )
        if str(decision.get("next_action") or "").strip() != "write_deployed_agent_knowledge_file":
            raise DeployedAgentKnowledgeRustGateError("unexpected_next_action")
    except rust_runtime_kernel_client.RustKernelDecisionError as exc:
        raise DeployedAgentKnowledgeRustGateError(exc.reason) from exc


async def upload_deployed_agent_knowledge_file(
    *,
    deployed_agent_id: str,
    current_user: Optional[Dict[str, Any]],
    owner_workspace_id: str,
    file_name: str,
    content_text: str,
) -> Dict[str, Any]:
    resolved_workspace_id = require_deployed_agent_admin_access(
        current_user=current_user,
        workspace_id=owner_workspace_id,
    )
    workspace = await control_plane_repository.get_workspace_by_id(resolved_workspace_id)
    if not isinstance(workspace, dict):
        raise ValueError("Workspace not found.")
    tenant_id = _normalize_text(workspace.get("tenant_id"))
    deployed_agent = await control_plane_repository.get_deployed_agent_by_id(
        deployed_agent_id,
        tenant_id=tenant_id,
        owner_workspace_id=resolved_workspace_id,
    )
    if not isinstance(deployed_agent, dict):
        raise ValueError("Deployed agent not found.")
    _enforce_deployed_agent_service_decision(
        "knowledge_upload",
        deployed_agent=deployed_agent,
        tenant_id=tenant_id,
        workspace_id=resolved_workspace_id,
        current_user=current_user,
        knowledge_reference_only=True,
    )

    safe_filename = _safe_knowledge_filename(file_name)
    raw_text = str(content_text or "")
    if not raw_text.strip():
        raise _http_bad_request("Knowledge file is empty.")
    raw_bytes = raw_text.encode("utf-8")
    max_bytes = knowledge_rag_service.DEFAULT_MAX_SOURCE_FILE_BYTES
    if len(raw_bytes) > max_bytes:
        raise _http_bad_request(f"Knowledge file exceeds max size ({len(raw_bytes)} bytes > {max_bytes} bytes).")

    content_hash = hashlib.sha256(raw_bytes).hexdigest()
    relative_root = Path("business-agents") / _normalize_text(deployed_agent_id, default="agent")
    root = workspace_context.workspace_knowledge_dir(
        workspace_id=resolved_workspace_id,
        agent_install_id=None,
    ) / relative_root
    root.mkdir(parents=True, exist_ok=True)
    destination = _unique_knowledge_file_path(root, safe_filename, content_hash)
    _enforce_deployed_agent_knowledge_file_write(
        deployed_agent_id=deployed_agent_id,
        workspace_id=resolved_workspace_id,
        destination=destination,
        safe_filename=safe_filename,
        content_hash=content_hash,
        content_bytes=len(raw_bytes),
    )
    destination.write_text(raw_text, encoding="utf-8")
    relative_path = destination.relative_to(
        workspace_context.workspace_knowledge_dir(workspace_id=resolved_workspace_id, agent_install_id=None)
    ).as_posix()
    source_uri = f"knowledge://{relative_path}"
    source_ref = {
        "id": f"knowledge-{content_hash[:16]}",
        "uri": source_uri,
        "label": safe_filename,
        "kind": "file",
        "path": f"knowledge/{relative_path}",
    }

    existing_sources = _normalize_knowledge_sources(deployed_agent.get("knowledge_sources") or [])
    existing_uris = {
        _normalize_text(item.get("uri") or item.get("path") or item.get("id"))
        for item in existing_sources
        if isinstance(item, dict)
    }
    next_sources = existing_sources if source_uri in existing_uris else [*existing_sources, source_ref]
    if source_uri in existing_uris:
        updated = project_deployed_agent(deployed_agent)
    else:
        updated = await update_deployed_agent(
            deployed_agent_id=deployed_agent_id,
            current_user=current_user,
            owner_workspace_id=resolved_workspace_id,
            updates={"knowledge_sources": next_sources},
        )
    ingestion_status = await knowledge_rag_service.ingest_workspace_knowledge_files(
        tenant_id=tenant_id,
        workspace_id=resolved_workspace_id,
        agent_id=deployed_agent_id,
        user_id=_normalize_text((current_user or {}).get("user_id")),
        source_refs=[relative_path],
    )
    return {
        "workspace_id": resolved_workspace_id,
        "tenant_id": tenant_id,
        "deployed_agent_id": deployed_agent_id,
        "knowledge_source": source_ref,
        "deployed_agent": updated,
        "ingestion": ingestion_status,
    }


def _derive_studio_specialist_profile(
    *,
    deployed_agent: Optional[Dict[str, Any]],
    config: deployed_agent_config_schema.DeployedAgentConfig,
) -> Dict[str, Any]:
    metadata = _coerce_dict((deployed_agent or {}).get("metadata"))
    stored_profile = _coerce_dict(metadata.get("specialist_profile"))
    tool_ids = {
        _normalize_text(item).lower()
        for item in list(config.tool_policy.enabled_tools or [])
        if _normalize_text(item)
    }
    has_sheet_read = "spreadsheet_read" in tool_ids
    has_sheet_append = "spreadsheet_append" in tool_ids
    knowledge_sources = [
        item
        for item in (
            _knowledge_reference_summary(item)
            for item in list(config.knowledge_sources or [])
        )
        if isinstance(item, dict)
    ]
    channel_payload = _coerce_dict(_channels_payload_from_config(config).get("telegram"))
    knowledge_defaults = {
        "title": "Knowledge",
        "mode": "menu_reference",
        "accepted_sources": ["pdf_upload", "google_sheet"],
        "source_count": len(knowledge_sources),
        "sources": knowledge_sources,
        "summary": (
            f"{len(knowledge_sources)} menu source{'s' if len(knowledge_sources) != 1 else ''} connected."
            if knowledge_sources
            else "Add a menu PDF or Google Sheet so the specialist can answer menu questions accurately."
        ),
    }
    live_data_defaults = {
        "title": "Live data",
        "mode": "daily_specials_and_availability",
        "connector_family": "google_workspace" if has_sheet_read else "manual_or_future_connector",
        "sheet_sync_enabled": has_sheet_read,
        "summary": (
            "Reads daily specials and item availability from a connected spreadsheet."
            if has_sheet_read
            else "Enable spreadsheet read to keep specials and availability live."
        ),
    }
    memory_defaults = {
        "title": "Memory",
        "mode": "per_customer_order_history",
        "memory_enabled": bool(config.memory_policy.memory_enabled),
        "context_budget_preset": config.memory_policy.context_budget_preset,
        "retention_preset": config.memory_policy.retention_preset,
        "summary": (
            "Keeps per-customer order context and repeat preferences."
            if config.memory_policy.memory_enabled
            else "Memory is disabled; repeat customers will not retain order history yet."
        ),
    }
    actions_defaults = {
        "title": "Actions",
        "enabled": ["place_order", "confirm_order", "escalate_to_human"],
        "order_capture_mode": "spreadsheet_append" if has_sheet_append else "manual_confirmation",
        "summary": (
            "Can confirm and log orders to a connected sheet, then escalate edge cases to a human."
            if has_sheet_append
            else "Can place and confirm orders in chat; enable spreadsheet append to log confirmed orders automatically."
        ),
    }
    telegram_enabled = bool(channel_payload.get("enabled"))
    channel_defaults = {
        "title": "Channel",
        "primary": "telegram_bot" if telegram_enabled else "owner_test",
        "secondary": "whatsapp_business" if telegram_enabled else None,
        "telegram_enabled": telegram_enabled,
        "endpoint_key": _normalize_optional_text(channel_payload.get("endpoint_key")),
        "bot_username": _normalize_optional_text(channel_payload.get("bot_username")),
        "summary": (
            "Primary customer traffic enters through Telegram bot; WhatsApp Business stays optional and out of scope by default."
            if telegram_enabled
            else "No live customer channel is configured yet. This specialist can still be deployed for owner testing before public launch."
        ),
    }

    profile = {
        "knowledge": {**knowledge_defaults, **_clean_mapping(stored_profile.get("knowledge"))},
        "live_data": {**live_data_defaults, **_clean_mapping(stored_profile.get("live_data"))},
        "memory": {**memory_defaults, **_clean_mapping(stored_profile.get("memory"))},
        "actions": {**actions_defaults, **_clean_mapping(stored_profile.get("actions"))},
        "channel": {**channel_defaults, **_clean_mapping(stored_profile.get("channel"))},
    }
    return profile


def build_deployed_agent_customer_entry(
    *,
    deployed_agent: Optional[Dict[str, Any]],
    bot_username: Optional[str] = None,
    endpoint_key: Optional[str] = None,
) -> Dict[str, Any]:
    config = _config_from_record(deployed_agent)
    deployed_agent_id = _normalize_optional_text((deployed_agent or {}).get("id"))
    resolved_bot_username = _normalize_optional_text(bot_username)
    resolved_telegram_url = (
        f"https://t.me/{resolved_bot_username}?start={quote(deployed_agent_id, safe='')}"
        if resolved_bot_username and deployed_agent_id
        else None
    )
    entry_url = _normalize_optional_text(config.customer_policy.public_start_cta_url) or resolved_telegram_url
    if not entry_url:
        entry_url = (
            f"https://t.me/{quote(endpoint_key or '', safe='')}"
            if _normalize_optional_text(endpoint_key)
            else None
        )
    qr_image_url = (
        f"https://api.qrserver.com/v1/create-qr-code/?size=220x220&data={quote(entry_url, safe='')}"
        if entry_url
        else None
    )
    return {
        "entry_url": entry_url,
        "cta_label": _normalize_optional_text(config.customer_policy.public_start_cta_label) or "Open menu",
        "telegram_deep_link": resolved_telegram_url,
        "bot_username": resolved_bot_username,
        "qr_image_url": qr_image_url,
        "qr_target": "web" if _normalize_optional_text(config.customer_policy.public_start_cta_url) else "telegram",
    }


def _studio_issue(
    *,
    code: str,
    message: str,
    guidance: Optional[str] = None,
    severity: str = "blocker",
) -> Dict[str, Any]:
    payload: Dict[str, Any] = {
        "code": _normalize_text(code, default="studio_issue"),
        "message": _normalize_text(message, default="Studio issue"),
        "severity": _normalize_text(severity, default="blocker").lower(),
    }
    guidance_text = _normalize_optional_text(guidance)
    if guidance_text:
        payload["guidance"] = guidance_text
    return payload


def _telegram_connector_endpoint_key(entry: Dict[str, Any]) -> Optional[str]:
    metadata = _coerce_dict(entry.get("metadata"))
    registry_bindings = _coerce_dict(metadata.get("channel_registry_bindings"))
    telegram_binding = _coerce_dict(registry_bindings.get("telegram"))
    return _normalize_optional_text(
        telegram_binding.get("endpoint_key")
        or metadata.get("telegram_endpoint_key")
        or entry.get("id")
    )


def _telegram_connector_projection(
    entry: Dict[str, Any],
    status_item: Dict[str, Any],
) -> Dict[str, Any]:
    metadata = _coerce_dict(entry.get("metadata"))
    connector_id = _normalize_text(status_item.get("id") or entry.get("id"))
    return {
        "id": connector_id,
        "label": _normalize_text(status_item.get("label") or entry.get("label"), default=connector_id),
        "workspace_id": _normalize_optional_text(status_item.get("workspace_id") or entry.get("workspace_id")),
        "connector_id": connector_id,
        "credential_id": connector_id,
        "endpoint_key": _telegram_connector_endpoint_key(entry),
        "bot_username": _normalize_optional_text(metadata.get("bot_username")),
        "webhook_path": _normalize_optional_text(status_item.get("webhook_path")),
        "webhook_url": _normalize_optional_text(status_item.get("webhook_url")),
        "profile_id": _normalize_optional_text(status_item.get("profile_id")),
        "profile_status": _normalize_optional_text(status_item.get("profile_status")) or "live",
        "profile_issue_code": _normalize_optional_text(status_item.get("profile_issue_code")),
        "profile_issue": _normalize_optional_text(status_item.get("profile_issue")),
        "last_error": _normalize_optional_text(status_item.get("last_error")),
        "last_error_category": _normalize_optional_text(status_item.get("last_error_category")),
        "last_error_at": _normalize_optional_text(status_item.get("last_error_at")),
    }


def _studio_next_action(
    *,
    telegram_enabled: bool,
    blockers: List[Dict[str, Any]],
    connector_options: List[Dict[str, Any]],
) -> str:
    if not telegram_enabled:
        return "Enable Telegram when the specialist is ready for live customer traffic."
    if not connector_options:
        return "Create or unpause a Telegram bot connector for this workspace before launching Studio."
    if blockers:
        first = blockers[0]
        return _normalize_optional_text(first.get("guidance")) or _normalize_text(
            first.get("message"),
            default="Resolve the Telegram launch blockers before deploying.",
        )
    return "Telegram is ready. Review the scoped memory and tools, then deploy the specialist."


def _workspace_telegram_status_payload(owner_workspace_id: str) -> Dict[str, Any]:
    shell = _autopilot_connector_shell_service()
    shell.runtime_facade_service().init_runtime()
    state_service = shell.telegram_service_registry().telegram_autopilot_state_service()
    status_service = AutopilotStatusService(
        normalize_workspace_id=lambda value: str(value or "").strip(),
        telegram_snapshot=lambda: state_service.snapshot(include_connectors=True),
        telegram_list_entries=lambda: _workspace_telegram_connector_entries(owner_workspace_id),
        resolve_telegram_profile=lambda entry: shell.profile_service().resolve_telegram_profile(entry),
        telegram_webhook_path="/channels/telegram/webhook/{connector_id}",
        telegram_public_base_url=shell.bridge_facade_service().telegram_public_base_url_getter(),
        telegram_webhook_secret_configured=bool(shell.bridge_facade_service().telegram_configured_webhook_secret_getter()),
        telegram_delivery_mode=shell.bridge_facade_service().telegram_delivery_mode_getter(),
        whatsapp_snapshot=lambda: {"enabled": False, "connectors": {}},
        whatsapp_list_entries=lambda: [],
        resolve_whatsapp_profile=lambda _entry: {"id": "disabled", "status": "disabled"},
        whatsapp_webhook_path="/channels/whatsapp/twilio/webhook",
        whatsapp_public_base_url="",
        whatsapp_webhook_secret_configured=False,
    )
    return status_service.telegram_status_payload()


def _workspace_telegram_connector_entries(owner_workspace_id: str) -> List[Dict[str, Any]]:
    shell = _autopilot_connector_shell_service()
    shell.runtime_facade_service().init_runtime()
    state_service = shell.telegram_service_registry().telegram_autopilot_state_service()
    raw_entries = state_service.list_connector_entries(owner_workspace_id)
    if raw_entries:
        return raw_entries

    from server_modules import connectors_actions

    requested_workspace_id = _normalize_text(owner_workspace_id)
    return [
        item
        for item in _coerce_list(connectors_actions.load_vault().get("credentials"))
        if _normalize_text(_coerce_dict(item).get("provider")).lower() == "telegram_bot"
        and _normalize_text(_coerce_dict(item).get("workspace_id")) == requested_workspace_id
        and not bool(_coerce_dict(_coerce_dict(item).get("metadata")).get("paused"))
    ]


def _workspace_telegram_connector_options(owner_workspace_id: str) -> List[Dict[str, Any]]:
    raw_entries = _workspace_telegram_connector_entries(owner_workspace_id)
    raw_by_id = {
        _normalize_text(item.get("id")): item
        for item in raw_entries
        if _normalize_text(item.get("id"))
    }
    status_payload = _workspace_telegram_status_payload(owner_workspace_id)
    options: List[Dict[str, Any]] = []
    for item in _coerce_list(status_payload.get("connectors")):
        status_item = _coerce_dict(item)
        connector_id = _normalize_text(status_item.get("id"))
        if not connector_id:
            continue
        options.append(_telegram_connector_projection(raw_by_id.get(connector_id, {}), status_item))
    seen_ids = {_normalize_text(item.get("id")) for item in options if _normalize_text(item.get("id"))}
    for connector_id, raw_entry in raw_by_id.items():
        if connector_id in seen_ids:
            continue
        options.append(
            _telegram_connector_projection(
                raw_entry,
                {
                    "id": connector_id,
                    "label": raw_entry.get("label"),
                    "workspace_id": raw_entry.get("workspace_id"),
                    "profile_status": "live",
                },
            )
        )
    return options


def _config_from_record(
    record: Optional[Dict[str, Any]],
    *,
    runtime_profile_id: Any = None,
) -> deployed_agent_config_schema.DeployedAgentConfig:
    return deployed_agent_config_schema.deployed_agent_config_from_record(
        record,
        runtime_profile_id=runtime_profile_id,
    )


def _operational_state_from_record(
    record: Optional[Dict[str, Any]],
) -> deployed_agent_config_schema.DeployedAgentOperationalState:
    return deployed_agent_config_schema.deployed_agent_operational_state_from_record(record)


def _metadata_from_config(
    config: deployed_agent_config_schema.DeployedAgentConfig,
    *,
    existing_metadata: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    payload = deployed_agent_config_schema.metadata_from_deployed_agent_config(
        config,
        existing_metadata=existing_metadata,
    )
    payload["privacy_contract_snapshot"] = _privacy_contract_snapshot(
        config=config,
        existing_metadata={**_coerce_dict(existing_metadata), **payload},
    )
    payload["computer_safety_contract_snapshot"] = _computer_safety_contract_snapshot(
        config=config,
    )
    return payload


def _serialized_operational_state(
    state: deployed_agent_config_schema.DeployedAgentOperationalState,
) -> Dict[str, Any]:
    return deployed_agent_config_schema.operational_state_payload(state)


def _config_payload(
    config: deployed_agent_config_schema.DeployedAgentConfig,
) -> Dict[str, Any]:
    return config.model_dump(exclude_none=True)


def _operational_state_payload(
    state: deployed_agent_config_schema.DeployedAgentOperationalState,
) -> Dict[str, Any]:
    return state.model_dump(exclude_none=True)


def _channels_payload_from_config(
    config: deployed_agent_config_schema.DeployedAgentConfig,
) -> Dict[str, Dict[str, Any]]:
    payload = _config_payload(config)
    return dict(payload.get("channels") or {})


def _workspace_admin_defaults(
    workspace_record: Optional[Dict[str, Any]],
) -> workspace_config_schema.WorkspaceAdminDefaultsConfig:
    metadata = _coerce_dict((workspace_record or {}).get("metadata"))
    return workspace_config_schema.workspace_admin_defaults_from_metadata(metadata)


def _config_field_present(config_payload: Optional[Dict[str, Any]], *path: str) -> bool:
    current: Any = config_payload if isinstance(config_payload, dict) else {}
    for key in path:
        if not isinstance(current, dict) or key not in current:
            return False
        current = current.get(key)
    return True


def _apply_workspace_admin_defaults_to_config(
    config: deployed_agent_config_schema.DeployedAgentConfig,
    *,
    workspace_defaults: workspace_config_schema.WorkspaceAdminDefaultsConfig,
    runtime_target_supplied: bool = False,
    billing_plan_supplied: bool = False,
    config_payload: Optional[Dict[str, Any]] = None,
    legacy_metadata: Optional[Dict[str, Any]] = None,
) -> deployed_agent_config_schema.DeployedAgentConfig:
    owner_metadata = _coerce_dict(legacy_metadata)
    payload = config.model_dump(exclude_none=True)
    if (
        not runtime_target_supplied
        and not _config_field_present(config_payload, "runtime_target")
        and _config_field_present(config_payload, "runtime_placement")
    ):
        payload["runtime_target"] = deployed_agent_runtime_contract_service.runtime_target_for_placement(
            payload.get("runtime_placement")
        )
    elif not runtime_target_supplied and not _config_field_present(config_payload, "runtime_target"):
        payload["runtime_target"] = str(workspace_defaults.runtime_target or config.runtime_target).strip() or config.runtime_target
    if not billing_plan_supplied and not _config_field_present(config_payload, "billing_plan"):
        payload["billing_plan"] = str(workspace_defaults.billing_plan or config.billing_plan).strip() or config.billing_plan

    customer_policy = dict(payload.get("customer_policy") or {})
    if not _config_field_present(config_payload, "customer_policy", "public_start_cta_label"):
        if not str(customer_policy.get("public_start_cta_label") or "").strip():
            customer_policy["public_start_cta_label"] = workspace_defaults.public_start_cta_label
    if not _config_field_present(config_payload, "customer_policy", "public_start_cta_url"):
        if not str(customer_policy.get("public_start_cta_url") or "").strip():
            customer_policy["public_start_cta_url"] = workspace_defaults.public_start_cta_url
    payload["customer_policy"] = customer_policy

    memory_policy = dict(payload.get("memory_policy") or {})
    if (
        not _config_field_present(config_payload, "memory_policy", "context_budget_preset")
        and "context_budget_preset" not in owner_metadata
    ):
        memory_policy["context_budget_preset"] = conversation_memory_policy.normalize_context_budget_preset(
            workspace_defaults.context_budget_preset
        )
    if (
        not _config_field_present(config_payload, "memory_policy", "retention_preset")
        and "retention_preset" not in owner_metadata
    ):
        memory_policy["retention_preset"] = workspace_defaults.retention_preset
    payload["memory_policy"] = memory_policy

    safety_policy = dict(payload.get("safety_policy") or {})
    if (
        not _config_field_present(config_payload, "safety_policy", "health_safety_enabled")
        and "health_safety_enabled" not in owner_metadata
    ):
        safety_policy["health_safety_enabled"] = bool(workspace_defaults.health_safety_enabled)
    payload["safety_policy"] = safety_policy

    commerce_policy = dict(payload.get("commerce_policy") or {})
    if (
        not _config_field_present(config_payload, "commerce_policy", "monthly_cost_cap_usd")
        and "monthly_cost_cap_usd" not in owner_metadata
        and not commerce_policy.get("monthly_cost_cap_usd")
        and float(workspace_defaults.hosted_sage_ai_monthly_cap_usd or 0.0) > 0
    ):
        automation_policy = dict(payload.get("computer_automation") or {})
        commerce_policy["monthly_cost_cap_usd"] = max(
            float(workspace_defaults.hosted_sage_ai_monthly_cap_usd),
            float(automation_policy.get("monthly_budget_usd") or 0.0),
        )
    payload["commerce_policy"] = commerce_policy
    return deployed_agent_config_schema.DeployedAgentConfig.model_validate(payload)


def _allowed_live_channels_for_workspace(workspace_record: Optional[Dict[str, Any]]) -> frozenset[str]:
    defaults = _workspace_admin_defaults(workspace_record)
    tokens = [str(item or "").strip().lower() for item in defaults.allowed_live_channels]
    normalized = frozenset(token for token in tokens if token)
    return normalized or DEPLOYED_AGENT_LIVE_CHANNELS


def _escalation_triggers_for_config(
    config: deployed_agent_config_schema.DeployedAgentConfig,
) -> str:
    preset = _normalize_text(
        config.escalation_policy.preset,
        default=config_defaults_service.default_deployed_agent_escalation_preset(),
    ).lower()
    return _ESCALATION_PRESET_TRIGGERS.get(
        preset,
        _ESCALATION_PRESET_TRIGGERS[config_defaults_service.default_deployed_agent_escalation_preset()],
    )


def _apply_provider_model_selection_to_config(
    config: deployed_agent_config_schema.DeployedAgentConfig,
    *,
    provider: Any = None,
    model: Any = None,
    owner_workspace_id: Any = None,
) -> deployed_agent_config_schema.DeployedAgentConfig:
    runtime_supply = config.runtime_supply if isinstance(config.runtime_supply, dict) else {}
    ai_source = deployed_agent_runtime_contract_service.normalize_studio_ai_source(
        runtime_supply.get("ai_source") if isinstance(runtime_supply.get("ai_source"), dict) else None
    )
    model_tier = runtime_supply.get("model_tier") if isinstance(runtime_supply.get("model_tier"), dict) else {}
    if ai_source["kind"] == deployed_agent_runtime_contract_service.STUDIO_AI_SOURCE_EMPYRALIS_CREDITS:
        tier_route = empyralis_model_tier_routing_service.resolve_backend_provider_model_for_tier(
            model_tier.get("public_tier") or "pro",
        )
        explicit_provider = provider if provider is not None else None
        explicit_provider_id = provider_catalog_service.provider_profiles.normalize_provider_id(explicit_provider)
        if explicit_provider_id and explicit_provider_id not in {"deepseek", "empyralis"}:
            raise ValueError(
                f"{explicit_provider_id} cannot use Empyralis credits from the Studio platform route; "
                "choose workspace_api_key for explicit provider routes."
            )
        resolved = {
            "provider": tier_route.get("provider"),
            "model": tier_route.get("model"),
        }
    else:
        requested_provider = provider if provider is not None else config.provider
        resolved = provider_catalog_service.resolve_provider_model_selection(
            provider=requested_provider,
            model=model if model is not None else config.model,
            existing_provider=config.provider,
            existing_model=config.model,
            cached_models=provider_catalog_service.cached_provider_model_ids(
                workspace_id=owner_workspace_id,
                provider=requested_provider,
            ),
        )
    next_payload = config.model_dump(exclude_none=True)
    next_payload["provider"] = resolved.get("provider")
    next_payload["model"] = resolved.get("model")
    next_runtime_supply = dict(next_payload.get("runtime_supply") or {})
    provider_binding = dict(next_runtime_supply.get("provider_binding") or {})
    provider_binding["internal_provider"] = resolved.get("provider")
    provider_binding["internal_model"] = resolved.get("model")
    next_runtime_supply["provider_binding"] = provider_binding
    next_payload["runtime_supply"] = next_runtime_supply
    return deployed_agent_config_schema.DeployedAgentConfig.model_validate(next_payload)


def _enforce_ai_source_provider_runtime(
    *,
    config: deployed_agent_config_schema.DeployedAgentConfig,
) -> None:
    provider = _normalize_optional_text(config.provider)
    model = _normalize_optional_text(config.model)
    provider_entry = (
        provider_catalog_service.provider_profiles.provider_catalog_entry(provider)
        if provider
        else {}
    )
    pricing_known: Optional[bool] = None
    if provider and model:
        pricing_known = bool(
            pricing_registry_service.catalog_price_projection(provider, model).get("pricing_known")
        )
    try:
        deployed_agent_runtime_contract_service.validate_studio_ai_source_provider_runtime(
            ai_source=(
                config.runtime_supply.get("ai_source")
                if isinstance(config.runtime_supply, dict)
                else None
            ),
            provider=provider,
            model=model,
            runtime_placement=config.runtime_placement,
            runtime_target=config.runtime_target,
            runtime_supply=config.runtime_supply,
            provider_scopes=provider_entry.get("provider_scopes") if provider_entry else None,
            pricing_known=pricing_known,
        )
        ai_source = deployed_agent_runtime_contract_service.normalize_studio_ai_source(
            config.runtime_supply.get("ai_source")
            if isinstance(config.runtime_supply, dict)
            else None
        )
        if provider and model:
            provider_catalog_service.assert_model_route_policy(
                provider=provider,
                model=model,
                surface="studio",
                payer=ai_source.get("payer") or ai_source.get("kind"),
            )
    except ValueError as error:
        raise _http_bad_request(str(error)) from error


def _normalize_deployed_agent_metadata(
    value: Any,
    *,
    existing: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    if value is not None and not isinstance(value, dict):
        raise ValueError("metadata must be an object.")
    config = _config_from_record(
        {
            "name": "Draft deployed agent",
            "metadata": {
                **_coerce_dict(existing),
                **_coerce_dict(value),
            },
        }
    )
    return _metadata_from_config(config, existing_metadata=existing)


def _selected_provider(value: Any) -> Optional[str]:
    config = _config_from_record({"name": "Draft deployed agent", "metadata": _coerce_dict(value)})
    return config.provider


def _selected_model(value: Any) -> Optional[str]:
    config = _config_from_record({"name": "Draft deployed agent", "metadata": _coerce_dict(value)})
    return config.model


def _apply_provider_model_selection(
    metadata: Dict[str, Any],
    *,
    provider: Any = None,
    model: Any = None,
    owner_workspace_id: Any = None,
) -> Dict[str, Any]:
    config = _config_from_record({"name": "Draft deployed agent", "metadata": metadata})
    return _metadata_from_config(
        _apply_provider_model_selection_to_config(
            config,
            provider=provider,
            model=model,
            owner_workspace_id=owner_workspace_id,
        ),
        existing_metadata=metadata,
    )


def _compact_text(value: Any, *, limit: int = 220) -> Optional[str]:
    text = " ".join(str(value or "").strip().split())
    if not text:
        return None
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def _normalize_pagination(limit: Any, offset: Any) -> tuple[int, int]:
    return max(1, min(int(limit or 50), 100)), max(0, int(offset or 0))


def _timestamp_token(value: Any) -> str:
    return str(value or "").strip()


def _actor_summary(actor: Any) -> Dict[str, Any]:
    payload = _coerce_dict(actor)
    actor_id = (
        _normalize_optional_text(payload.get("id"))
        or _normalize_optional_text(payload.get("user_id"))
        or _normalize_optional_text(payload.get("external_id"))
        or _normalize_optional_text(payload.get("username"))
        or _normalize_optional_text(payload.get("handle"))
    )
    label = (
        _normalize_optional_text(payload.get("display_name"))
        or _normalize_optional_text(payload.get("name"))
        or _normalize_optional_text(payload.get("username"))
        or _normalize_optional_text(payload.get("handle"))
        or actor_id
    )
    summary = {
        "id": actor_id,
        "label": label,
        "type": _normalize_optional_text(payload.get("type")) or "customer",
        "username": _normalize_optional_text(payload.get("username")),
        "handle": _normalize_optional_text(payload.get("handle")),
    }
    return {key: value for key, value in summary.items() if value is not None}


def _result_connector_action(result_data: Any) -> Optional[Dict[str, Any]]:
    payload = _coerce_dict(result_data)
    connector_action = payload.get("connector_action")
    if isinstance(connector_action, dict):
        return dict(connector_action)
    last_node_data = payload.get("last_node_data")
    if isinstance(last_node_data, dict) and isinstance(last_node_data.get("connector_action"), dict):
        return dict(last_node_data.get("connector_action") or {})
    return None


def _approval_id_from_payload(payload: Dict[str, Any]) -> Optional[str]:
    candidates = [
        payload,
        _coerce_dict(payload.get("metadata")),
        _coerce_dict(payload.get("decision_payload")),
        _coerce_dict(payload.get("request_payload")),
        _coerce_dict(payload.get("outbox_payload")),
        _coerce_dict(payload.get("notification")),
        _coerce_dict(payload.get("pending_approval")),
        _coerce_dict(payload.get("pending_confirmation")),
    ]
    for candidate in candidates:
        approval_id = _normalize_optional_text(candidate.get("approval_id"))
        if approval_id:
            return approval_id
    return None


def _is_approval_activity(row: Dict[str, Any]) -> bool:
    # Phase 3: Approval system removed. No events carry event_class="approval".
    return False


def _is_escalation_activity(row: Dict[str, Any]) -> bool:
    action = _normalize_text(row.get("action")).lower()
    event_class = _normalize_text(row.get("event_class")).lower()
    summary_blob = " ".join(
        [
            _normalize_text(row.get("title")),
            _normalize_text(row.get("summary")),
        ]
    ).lower()
    payload = _coerce_dict(row.get("payload"))
    metadata = _coerce_dict(row.get("metadata"))
    resolution = _normalize_text(payload.get("resolution") or metadata.get("resolution")).lower()
    return (
        event_class == "blocked_action"
        or action in {"escalate", "escalated"}
        or resolution == "escalated"
        or bool(payload.get("escalated"))
        or bool(metadata.get("escalated"))
        or "escalat" in summary_blob
    )


def _tool_call_entry_from_activity(row: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    payload = _coerce_dict(row.get("payload"))
    metadata = _coerce_dict(row.get("metadata"))
    connector_action = None
    for candidate in (
        payload.get("connector_action"),
        metadata.get("connector_action"),
        _coerce_dict(payload.get("result_data")).get("connector_action"),
    ):
        if isinstance(candidate, dict):
            connector_action = dict(candidate)
            break
    tool_name = (
        _normalize_optional_text(metadata.get("tool_name"))
        or _normalize_optional_text(payload.get("tool_name"))
        or (
            f"{_normalize_text(connector_action.get('connector')).lower()}.{_normalize_text(connector_action.get('action_id')).lower()}"
            if isinstance(connector_action, dict)
            and _normalize_optional_text(connector_action.get("connector"))
            and _normalize_optional_text(connector_action.get("action_id"))
            else None
        )
    )
    if not tool_name and not isinstance(connector_action, dict):
        return None
    return {
        "id": str(row.get("id") or "").strip() or None,
        "kind": "tool_call",
        "ts": str(row.get("created_at") or "").strip() or None,
        "run_id": _normalize_optional_text(row.get("run_id")),
        "thread_id": _normalize_optional_text(row.get("thread_id")),
        "tool_name": tool_name,
        "status": _normalize_optional_text(row.get("status")) or "logged",
        "summary": _compact_text(row.get("summary") or row.get("title") or tool_name),
        "details": connector_action or payload or metadata,
    }


def _tool_call_entry_from_run(run_id: str, run_snapshot: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    connector_action = _result_connector_action(run_snapshot.get("result_data"))
    if not isinstance(connector_action, dict):
        return None
    connector_id = _normalize_optional_text(connector_action.get("connector"))
    action_id = _normalize_optional_text(connector_action.get("action_id"))
    tool_name = (
        f"{connector_id}.{action_id}".lower()
        if connector_id and action_id
        else connector_id
        or action_id
    )
    return {
        "id": f"run-tool:{run_id}",
        "kind": "tool_call",
        "ts": (
            _normalize_optional_text(run_snapshot.get("completed_at"))
            or _normalize_optional_text(run_snapshot.get("updated_at"))
            or _normalize_optional_text(run_snapshot.get("started_at"))
            or _normalize_optional_text(run_snapshot.get("created_at"))
        ),
        "run_id": run_id,
        "thread_id": _normalize_optional_text(run_snapshot.get("thread_id")),
        "tool_name": tool_name,
        "status": _normalize_optional_text(run_snapshot.get("status")) or "completed",
        "summary": _compact_text(
            run_snapshot.get("summary")
            or _coerce_dict(run_snapshot.get("result_data")).get("summary")
            or f"Connector action completed: {tool_name or 'tool'}."
        ),
        "details": connector_action,
    }


def _run_step_number(value: Any) -> Optional[int]:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    text = _normalize_optional_text(value)
    if not text:
        return None
    try:
        return int(text)
    except (TypeError, ValueError):
        return None


def _run_step_entry_from_activity(row: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    run_id = _normalize_optional_text(row.get("run_id"))
    if not run_id or _is_approval_activity(row) or _is_escalation_activity(row):
        return None
    payload = _coerce_dict(row.get("payload"))
    metadata = _coerce_dict(row.get("metadata"))
    event_class = _normalize_optional_text(row.get("event_class")) or "activity"
    action = (
        _normalize_optional_text(row.get("action"))
        or _normalize_optional_text(payload.get("event"))
        or _normalize_optional_text(metadata.get("event"))
        or "step"
    )
    step_id = (
        _normalize_optional_text(payload.get("step_id"))
        or _normalize_optional_text(metadata.get("step_id"))
        or _normalize_optional_text(payload.get("id"))
        or _normalize_optional_text(metadata.get("id"))
    )
    step_index = _run_step_number(payload.get("step_index"))
    if step_index is None:
        step_index = _run_step_number(metadata.get("step_index"))
    step_number = _run_step_number(payload.get("step_number"))
    if step_number is None:
        step_number = _run_step_number(metadata.get("step_number"))
    summary = _compact_text(
        row.get("summary")
        or row.get("title")
        or payload.get("message")
        or metadata.get("message")
        or action
    )
    return {
        "id": str(row.get("id") or "").strip() or f"run-step:{run_id}:{action}:{_timestamp_token(row.get('created_at'))}",
        "kind": "run_step",
        "source": "activity",
        "ts": _normalize_optional_text(row.get("created_at")),
        "run_id": run_id,
        "thread_id": _normalize_optional_text(row.get("thread_id")),
        "event_class": event_class,
        "action": action,
        "status": _normalize_optional_text(row.get("status")) or "logged",
        "step_id": step_id,
        "step_index": step_index,
        "step_number": step_number,
        "summary": summary,
        "details": payload or metadata or {"summary": summary, "event_class": event_class, "action": action},
    }


def _run_step_entries_from_run(run_id: str, run_snapshot: Dict[str, Any]) -> List[Dict[str, Any]]:
    entries: List[Dict[str, Any]] = []
    thread_id = _normalize_optional_text(run_snapshot.get("thread_id"))
    snapshot_events = run_snapshot.get("events") if isinstance(run_snapshot.get("events"), list) else []
    for index, event in enumerate(snapshot_events):
        if not isinstance(event, dict):
            continue
        action = (
            _normalize_optional_text(event.get("event"))
            or _normalize_optional_text(event.get("type"))
            or "step"
        )
        step_id = _normalize_optional_text(event.get("step_id")) or _normalize_optional_text(event.get("id"))
        step_index = _run_step_number(event.get("step_index"))
        step_number = _run_step_number(event.get("step_number"))
        ts = (
            _normalize_optional_text(event.get("ts"))
            or _normalize_optional_text(event.get("created_at"))
            or _normalize_optional_text(event.get("updated_at"))
            or _normalize_optional_text(run_snapshot.get("completed_at"))
            or _normalize_optional_text(run_snapshot.get("updated_at"))
            or _normalize_optional_text(run_snapshot.get("started_at"))
            or _normalize_optional_text(run_snapshot.get("created_at"))
        )
        summary = _compact_text(
            event.get("message")
            or event.get("label")
            or event.get("summary")
            or action
        )
        entries.append(
            {
                "id": step_id or f"run-step:{run_id}:event:{index}",
                "kind": "run_step",
                "source": "run_snapshot",
                "ts": ts,
                "run_id": run_id,
                "thread_id": thread_id,
                "event_class": "run_event",
                "action": action,
                "status": _normalize_optional_text(event.get("status"))
                or _normalize_optional_text(event.get("level"))
                or _normalize_optional_text(run_snapshot.get("status"))
                or "logged",
                "step_id": step_id,
                "step_index": step_index,
                "step_number": step_number,
                "summary": summary,
                "details": dict(event),
            }
        )
    if entries:
        return entries
    status = _normalize_optional_text(run_snapshot.get("status")) or _normalize_optional_text(run_snapshot.get("state"))
    if not status:
        return []
    summary = _compact_text(
        run_snapshot.get("summary")
        or _coerce_dict(run_snapshot.get("result_data")).get("summary")
        or f"Run {status}."
    )
    return [
        {
            "id": f"run-step:{run_id}:status",
            "kind": "run_step",
            "source": "run_snapshot",
            "ts": (
                _normalize_optional_text(run_snapshot.get("completed_at"))
                or _normalize_optional_text(run_snapshot.get("updated_at"))
                or _normalize_optional_text(run_snapshot.get("started_at"))
                or _normalize_optional_text(run_snapshot.get("created_at"))
            ),
            "run_id": run_id,
            "thread_id": thread_id,
            "event_class": "run_status",
            "action": status,
            "status": status,
            "step_id": None,
            "step_index": None,
            "step_number": None,
            "summary": summary,
            "details": {
                "status": status,
                "summary": summary,
                "result_data": _coerce_dict(run_snapshot.get("result_data")),
            },
        }
    ]


def _message_entry_from_channel_event(event: Dict[str, Any]) -> Dict[str, Any]:
    payload = _coerce_dict(event.get("payload"))
    text = _normalize_text(
        event.get("text")
        or payload.get("text")
        or payload.get("summary")
        or payload.get("message")
    )
    return {
        "id": str(event.get("id") or "").strip() or None,
        "kind": "message",
        "ts": _normalize_optional_text(event.get("created_at")),
        "direction": _normalize_optional_text(event.get("direction")) or "system",
        "event_type": _normalize_optional_text(event.get("event_type")) or "message",
        "run_id": _normalize_optional_text(event.get("run_id")),
        "thread_id": _normalize_optional_text(event.get("thread_id")),
        "channel": _normalize_optional_text(event.get("channel_key")),
        "status": _normalize_optional_text(event.get("status")) or "logged",
        "text": text,
        "actor": _coerce_dict(event.get("actor")),
        "payload": payload,
    }


def _approval_entry_from_activity(
    row: Dict[str, Any],
    *,
    approval_record: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    payload = _coerce_dict(row.get("payload"))
    metadata = _coerce_dict(row.get("metadata"))
    record = approval_record if isinstance(approval_record, dict) else {}
    resolution = (
        _normalize_optional_text(record.get("resolution"))
        or _normalize_optional_text(payload.get("resolution"))
        or _normalize_optional_text(metadata.get("resolution"))
        or (
            "requested"
            if _normalize_text(row.get("action")).lower() == "approval_requested"
            else None
        )
    )
    return {
        "id": str(row.get("id") or "").strip() or None,
        "kind": "approval",
        "ts": _normalize_optional_text(row.get("created_at")),
        "run_id": _normalize_optional_text(row.get("run_id")) or _normalize_optional_text(record.get("run_id")),
        "thread_id": _normalize_optional_text(row.get("thread_id")),
        "approval_id": _approval_id_from_payload(payload) or _approval_id_from_payload(metadata) or _normalize_optional_text(record.get("approval_id")),
        "action": _normalize_optional_text(row.get("action")) or "approval",
        "status": _normalize_optional_text(record.get("status")) or _normalize_optional_text(row.get("status")) or "logged",
        "resolution": resolution,
        "summary": _compact_text(row.get("summary") or row.get("title") or resolution or "Approval event"),
        "details": record or payload or metadata,
    }


def _escalation_entry_from_activity(row: Dict[str, Any]) -> Dict[str, Any]:
    payload = _coerce_dict(row.get("payload"))
    metadata = _coerce_dict(row.get("metadata"))
    return {
        "id": str(row.get("id") or "").strip() or None,
        "kind": "escalation",
        "ts": _normalize_optional_text(row.get("created_at")),
        "run_id": _normalize_optional_text(row.get("run_id")),
        "thread_id": _normalize_optional_text(row.get("thread_id")),
        "action": _normalize_optional_text(row.get("action")) or "escalated",
        "status": _normalize_optional_text(row.get("status")) or "logged",
        "summary": _compact_text(row.get("summary") or row.get("title") or "Escalation triggered."),
        "details": payload or metadata,
    }


def _entry_sort_key(entry: Dict[str, Any]) -> tuple[str, int, str]:
    order = {"message": 0, "run_step": 1, "tool_call": 2, "approval": 3, "escalation": 4}
    return (
        _timestamp_token(entry.get("ts")),
        order.get(str(entry.get("kind") or ""), 9),
        str(entry.get("id") or ""),
    )


def _derive_escalation_state(activity_rows: List[Dict[str, Any]]) -> str:
    latest_state = "clear"
    for row in sorted(activity_rows, key=lambda item: (_timestamp_token(item.get("created_at")), str(item.get("id") or ""))):
        if _is_escalation_activity(row):
            latest_state = "escalated"
            continue
        if _is_approval_activity(row):
            action = _normalize_text(row.get("action")).lower()
            payload = _coerce_dict(row.get("payload"))
            metadata = _coerce_dict(row.get("metadata"))
            resolution = _normalize_text(payload.get("resolution") or metadata.get("resolution")).lower()
            if action == "approval_requested":
                latest_state = "approval_requested"
            elif resolution:
                latest_state = resolution
    return latest_state


def _derive_outcome(
    *,
    activity_rows: List[Dict[str, Any]],
    run_snapshots: Dict[str, Dict[str, Any]],
) -> Optional[str]:
    ordered_activity = sorted(
        activity_rows,
        key=lambda item: (_timestamp_token(item.get("created_at")), str(item.get("id") or "")),
    )
    for row in reversed(ordered_activity):
        event_class = _normalize_text(row.get("event_class")).lower()
        action = _normalize_text(row.get("action")).lower()
        status = _normalize_text(row.get("status")).lower()
        payload = _coerce_dict(row.get("payload"))
        metadata = _coerce_dict(row.get("metadata"))
        resolution = _normalize_text(payload.get("resolution") or metadata.get("resolution")).lower()
        if event_class == "run_status" and action:
            return action
        if resolution:
            return resolution
        if status in {"completed", "failed", "timeout", "rejected", "approved", "escalated"}:
            return status
    for run_snapshot in run_snapshots.values():
        status = _normalize_optional_text(run_snapshot.get("status")) or _normalize_optional_text(run_snapshot.get("state"))
        if status:
            return status.lower()
    return None


async def _get_run_snapshot(run_id: str) -> Optional[Dict[str, Any]]:
    token = _normalize_optional_text(run_id)
    if not token:
        return None
    live_run = await run_state_repository.get_live_run(token)
    if isinstance(live_run, dict):
        return {"source": "live", "payload": live_run}
    archived_run = await run_state_repository.get_archived_run(token)
    if isinstance(archived_run, dict):
        return {"source": "archive", "payload": archived_run}
    return None


async def _load_conversation_activity(
    *,
    tenant_id: str,
    workspace_id: str,
    backing_install_id: str,
    session_id: str,
    thread_id: Optional[str],
    run_ids: List[str],
) -> List[Dict[str, Any]]:
    rows_by_id: Dict[str, Dict[str, Any]] = {}

    async def _merge(**filters: Any) -> None:
        rows = await control_plane_repository.list_activity_ledger_events(
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            install_id=backing_install_id,
            limit=300,
            **filters,
        )
        for row in rows:
            row_id = str((row or {}).get("id") or "").strip()
            if row_id and row_id not in rows_by_id:
                rows_by_id[row_id] = dict(row)

    await _merge(session_key=session_id)
    if thread_id:
        await _merge(thread_id=thread_id)
    for run_id in run_ids:
        await _merge(run_id=run_id)
    return sorted(
        rows_by_id.values(),
        key=lambda item: (_timestamp_token(item.get("created_at")), str(item.get("id") or "")),
    )


def _require_live_channel_configuration(
    channels: Dict[str, Any],
    *,
    allowed_live_channels: Optional[set[str] | frozenset[str]] = None,
) -> None:
    resolved_allowed = frozenset(
        str(channel or "").strip().lower()
        for channel in (allowed_live_channels or DEPLOYED_AGENT_LIVE_CHANNELS)
        if str(channel or "").strip()
    ) or DEPLOYED_AGENT_LIVE_CHANNELS
    live_channels = _live_channel_keys(channels)
    if not live_channels:
        raise ValueError("A Telegram inbound binding is required before a deployed agent can go live.")
    unsupported = sorted(live_channels - resolved_allowed)
    if unsupported:
        allowed_label = ", ".join(channel.title() for channel in sorted(resolved_allowed))
        raise ValueError(f"Only {allowed_label} may be activated for live deployment in Phase 2.")
    telegram_binding = dict(channels.get("telegram") or {})
    if "telegram" in resolved_allowed and not bool(telegram_binding.get("enabled")):
        raise ValueError("Telegram must be enabled before a deployed agent can go live.")
    if "telegram" in resolved_allowed and not bool(telegram_binding.get("is_inbound_owner")):
        raise ValueError("Telegram live deployments must claim inbound ownership.")
    if "telegram" in resolved_allowed and not _normalize_optional_text(telegram_binding.get("endpoint_key")):
        raise ValueError("Telegram live deployments require an endpoint_key.")


def _has_customer_live_channel_configuration(
    channels: Dict[str, Any],
    *,
    allowed_live_channels: Optional[set[str] | frozenset[str]] = None,
) -> bool:
    resolved_allowed = frozenset(
        str(channel or "").strip().lower()
        for channel in (allowed_live_channels or DEPLOYED_AGENT_LIVE_CHANNELS)
        if str(channel or "").strip()
    ) or DEPLOYED_AGENT_LIVE_CHANNELS
    return bool(_live_channel_keys(channels) & resolved_allowed)


def _base_manifest(
    *,
    name: str,
    persona: str,
    system_prompt: str,
    runtime_target: str,
    channels: Dict[str, Any],
    escalation_triggers: Optional[str] = None,
) -> AgentManifest:
    manifest_channels = {
        channel_key: bool(_channel_enabled(channels.get(channel_key)))
        for channel_key in _MANIFEST_CHANNEL_KEYS
    }
    summary = persona or system_prompt[:240] or f"{name} customer-facing specialist"
    return AgentManifest(
        manifest_id=f"deployed-agent-{name.lower().replace(' ', '-')}",
        identity=AgentManifestIdentity(
            name=name,
            role="Customer-facing service specialist",
            archetype="support_specialist",
            summary=summary,
            owner_mode_enabled=True,
            customer_mode_enabled=True,
        ),
        voice=AgentManifestVoiceProfile(
            tone=persona,
            response_style="Answer clearly, stay within configured boundaries, and escalate when uncertain.",
            service_boundaries="Never imply access beyond configured knowledge, connectors, and approvals.",
        ),
        bible=AgentManifestBible(
            mission=f"Serve as {name} for customer-facing conversations and execution requests.",
            hard_context=system_prompt,
            operational_policy="Use configured knowledge and tools, respect approval boundaries, and escalate when uncertain.",
            core_responsibilities="Answer requests, retrieve allowed data, and keep customer-facing interactions legible.",
            guardrails="Do not expose owner-only context. Do not exceed configured connector, memory, or tool scope.",
            escalation_triggers=_normalize_text(
                escalation_triggers,
                default=_ESCALATION_PRESET_TRIGGERS[config_defaults_service.default_deployed_agent_escalation_preset()],
            ),
        ),
        channels=AgentManifestChannels.model_validate(manifest_channels),
        runtime=AgentManifestRuntime(mode=_runtime_target_to_specialist_mode(runtime_target)),
    )


def require_deployed_agent_admin_access(
    *,
    current_user: Optional[Dict[str, Any]],
    workspace_id: str,
) -> str:
    resolved_workspace_id = str(workspace_id or "").strip()
    if not resolved_workspace_id:
        raise HTTPException(status_code=400, detail="workspace_id is required.")
    if auth_module.current_user_has_auth_admin_access(current_user):
        return resolved_workspace_id
    return auth_module.enforce_workspace_access(
        current_user,
        resolved_workspace_id,
        minimum_role="owner",
    )


def project_deployed_agent(
    deployed_agent: Optional[Dict[str, Any]],
    *,
    include_internal: bool = False,
) -> Optional[Dict[str, Any]]:
    if not isinstance(deployed_agent, dict):
        return None
    config = _config_from_record(deployed_agent)
    operational_state = _operational_state_from_record(deployed_agent)
    projected = {
        field: deployed_agent.get(field)
        for field in DEPLOYED_AGENT_PUBLIC_FIELDS
    }
    projected["provider"] = _normalize_optional_text(config.provider)
    projected["model"] = _normalize_optional_text(config.model)
    config_payload = _config_payload(config)
    config_payload["specialist_profile"] = _derive_studio_specialist_profile(
        deployed_agent=deployed_agent,
        config=config,
    )
    config_payload["agent_workspace"] = _deployed_agent_workspace_contract(deployed_agent)
    metadata_payload = _metadata_from_config(
        config,
        existing_metadata=_coerce_dict(deployed_agent.get("metadata")),
    )
    privacy_snapshot = _coerce_dict(metadata_payload.get("privacy_contract_snapshot"))
    computer_safety_snapshot = _coerce_dict(metadata_payload.get("computer_safety_contract_snapshot"))
    config_payload["privacy_contract"] = privacy_snapshot
    config_payload["computer_safety_contract"] = computer_safety_snapshot
    projected["config"] = config_payload
    projected["operational_state"] = _operational_state_payload(operational_state)
    if include_internal:
        for field in DEPLOYED_AGENT_INTERNAL_FIELDS:
            if field == "metadata":
                projected[field] = metadata_payload
            else:
                projected[field] = deployed_agent.get(field)
    return projected


def validate_state_transition(current_state: Any, next_state: Any) -> str:
    resolved_current = _normalize_deployment_state(current_state)
    resolved_next = _normalize_deployment_state(next_state)
    allowed_transitions = {
        "draft": {"draft", "private_test", "ready_for_review", "live", "archived"},
        "private_test": {"private_test", "ready_for_review", "paused", "archived"},
        "ready_for_review": {"ready_for_review", "private_test", "live", "paused", "archived"},
        "live": {"live", "paused", "suspended", "archived"},
        "paused": {"paused", "private_test", "ready_for_review", "live", "suspended", "archived"},
        "suspended": {"suspended", "private_test", "ready_for_review", "archived"},
        "archived": {"archived"},
    }
    if resolved_next not in allowed_transitions.get(resolved_current, set()):
        raise ValueError(f"Unsupported deployed-agent state transition: {resolved_current} -> {resolved_next}.")
    return resolved_next


def validate_can_deploy(
    *,
    deployed_agent: Optional[Dict[str, Any]],
    backing_install: Optional[Dict[str, Any]],
    allowed_live_channels: Optional[set[str] | frozenset[str]] = None,
) -> bool:
    if not isinstance(deployed_agent, dict):
        raise ValueError("Deployed agent is required.")
    if not isinstance(backing_install, dict):
        raise ValueError("Backing specialist install is required.")
    if str(deployed_agent.get("backing_install_id") or "").strip() != str(backing_install.get("id") or "").strip():
        raise ValueError("Deployed agent backing install does not match the specialist install.")
    if str(deployed_agent.get("tenant_id") or "").strip() != str(backing_install.get("tenant_id") or "").strip():
        raise ValueError("Deployed agent and backing specialist tenant scope must match.")
    if str(deployed_agent.get("owner_workspace_id") or "").strip() != str(backing_install.get("workspace_id") or "").strip():
        raise ValueError("Deployed agent and backing specialist workspace scope must match.")
    live_channels = _live_channel_keys(deployed_agent.get("channels") or {})
    resolved_allowed = frozenset(
        str(channel or "").strip().lower()
        for channel in (allowed_live_channels or DEPLOYED_AGENT_LIVE_CHANNELS)
        if str(channel or "").strip()
    ) or DEPLOYED_AGENT_LIVE_CHANNELS
    unsupported = sorted(live_channels - resolved_allowed)
    if unsupported:
        allowed_label = ", ".join(channel.title() for channel in sorted(resolved_allowed))
        raise ValueError(f"Only {allowed_label} may be activated for live deployment in Phase 1.")
    specialist_mode = str(
        backing_install.get("specialist_mode")
        or (backing_install.get("metadata") or {}).get("specialist_mode")
        or ""
    ).strip().lower()
    requires_customer_live_mode = _has_customer_live_channel_configuration(
        deployed_agent.get("channels") or {},
        allowed_live_channels=resolved_allowed,
    )
    allowed_modes = {"customer_live"} if requires_customer_live_mode else {"owner_test", "customer_live"}
    if specialist_mode not in allowed_modes:
        if requires_customer_live_mode:
            raise ValueError("Backing specialist must be in customer_live mode before deployment can go live.")
        raise ValueError("Backing specialist must be in owner_test or customer_live mode before deployment can go live.")
    if requires_customer_live_mode and specialist_mode != "customer_live":
        raise ValueError("Backing specialist must be in customer_live mode before deployment can go live.")
    metadata = _coerce_dict(deployed_agent.get("metadata"))
    privacy_snapshot = _coerce_dict(metadata.get("privacy_contract_snapshot"))
    if not _validate_privacy_contract_snapshot(privacy_snapshot):
        raise ValueError("Live deployment requires a complete privacy contract snapshot.")
    if not _validate_privacy_contract_acceptance(privacy_snapshot):
        raise ValueError("Live deployment requires privacy contract acceptance.")
    config = _config_from_record(deployed_agent)
    readiness_blockers = _deploy_readiness_blockers(
        config=config,
        privacy_snapshot=privacy_snapshot,
        computer_safety_snapshot=_coerce_dict(metadata.get("computer_safety_contract_snapshot")),
    )
    if readiness_blockers:
        raise ValueError("Deployment readiness failed: " + "; ".join(readiness_blockers))
    requires_computer_safety = config.studio_agent_mode in {
        deployed_agent_runtime_contract_service.STUDIO_AGENT_MODE_CLOUD_COMPUTER,
        deployed_agent_runtime_contract_service.STUDIO_AGENT_MODE_MY_COMPUTER,
    }
    if requires_computer_safety:
        safety_snapshot = _coerce_dict(metadata.get("computer_safety_contract_snapshot"))
        if not _validate_computer_safety_contract_snapshot(
            safety_snapshot,
            require_for_mode=True,
        ):
            raise ValueError("Live deployment requires a complete computer safety contract snapshot.")
    return True


async def mirror_deployed_agent_to_backing_specialist(
    *,
    deployed_agent: Dict[str, Any],
    backing_install: Optional[Dict[str, Any]] = None,
    updated_by_user_id: Optional[str] = None,
    specialist_mode_override: Optional[str] = None,
    deployment_state_override: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    if not isinstance(deployed_agent, dict):
        return None
    tenant_id = _normalize_text(deployed_agent.get("tenant_id"))
    workspace_id = _normalize_text(deployed_agent.get("owner_workspace_id"))
    backing_install_id = _normalize_text(deployed_agent.get("backing_install_id"))
    if not tenant_id or not workspace_id or not backing_install_id:
        raise ValueError("Deployed agent is missing required backing specialist linkage.")
    install = backing_install or await agent_specialist_repository.get_workspace_specialist(
        backing_install_id,
        tenant_id=tenant_id,
        workspace_id=workspace_id,
    )
    if not isinstance(install, dict):
        raise ValueError("Backing specialist install is unavailable.")
    config = _config_from_record(
        deployed_agent,
        runtime_profile_id=install.get("runtime_profile_id"),
    )
    manifest = agent_specialist_repository.manifest_from_install_bundle(install) or _base_manifest(
        name=config.name,
        persona=config.persona,
        system_prompt=config.system_prompt,
        runtime_target=config.runtime_target,
        channels=_channels_payload_from_config(config),
        escalation_triggers=_escalation_triggers_for_config(config),
    )
    channels = _channels_payload_from_config(config)
    runtime_target = config.runtime_target
    selected_provider = _normalize_optional_text(config.provider)
    selected_model = _normalize_optional_text(config.model)
    manifest.identity.name = _normalize_text(config.name, default=manifest.identity.name)
    manifest.identity.summary = _normalize_text(
        config.persona,
        default=manifest.identity.summary or manifest.identity.name,
    )
    manifest.voice.tone = _normalize_text(config.persona, default=manifest.voice.tone)
    manifest.bible.hard_context = _normalize_text(
        config.system_prompt,
        default=manifest.bible.hard_context,
    )
    manifest.bible.escalation_triggers = _escalation_triggers_for_config(config)
    manifest.runtime = AgentManifestRuntime(
        mode=_runtime_target_to_specialist_mode(runtime_target),
        profile_id=str(install.get("runtime_profile_id") or manifest.runtime.profile_id or "").strip() or None,
    )
    manifest.channels = AgentManifestChannels.model_validate(
        {
            channel_key: bool(_channel_enabled(channels.get(channel_key)))
            for channel_key in _MANIFEST_CHANNEL_KEYS
        }
    )
    specialist_mode = str(specialist_mode_override or install.get("specialist_mode") or "owner_edit").strip().lower() or "owner_edit"
    target_deployment_state = _normalize_deployment_state(
        deployment_state_override if deployment_state_override is not None else deployed_agent.get("deployment_state")
    )
    if target_deployment_state == "live":
        specialist_mode = (
            "customer_live"
            if _has_customer_live_channel_configuration(channels)
            else "owner_test"
        )
    install_status = "active" if specialist_mode == "customer_live" else (
        str(install.get("status") or "").strip().lower() or "draft"
    )
    metadata = {
        "source": "deployed_agent",
        "visibility": "private",
        "status": install_status,
        "specialist_mode": specialist_mode,
        "deployed_agent": {
            "id": deployed_agent.get("id"),
            "owner_workspace_id": workspace_id,
            "runtime_target": runtime_target,
            "runtime_placement": config.runtime_placement,
            "runtime_supply": config.runtime_supply,
            "computer_automation": config.computer_automation.model_dump(exclude_none=True),
            "provider": selected_provider,
            "model": selected_model,
        },
        "provider": selected_provider,
        "model": selected_model,
        "deployed_agent_knowledge_sources": deployed_agent.get("knowledge_sources") or [],
        "public_intro": config.customer_policy.public_intro,
        "public_core_value": config.customer_policy.public_core_value,
        "platform_cta_label": config.customer_policy.public_start_cta_label,
        "platform_cta_url": config.customer_policy.public_start_cta_url,
        "public_source": "deployed_agent_owner",
        "health_safety_enabled": bool(config.safety_policy.health_safety_enabled),
        "health_safety_assistant_name": config.safety_policy.assistant_name,
        "context_budget_preset": config.memory_policy.context_budget_preset,
        "retention_preset": config.memory_policy.retention_preset,
        "selected_tool_ids": list(config.tool_policy.enabled_tools or []),
        "escalation_preset": config.escalation_policy.preset,
        "handoff_mode": config.escalation_policy.handoff_mode,
        "owner_notification_destination": config.escalation_policy.owner_notification_destination,
    }
    return await agent_specialist_repository.update_workspace_specialist_manifest(
        backing_install_id,
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        manifest=manifest,
        updated_by_user_id=updated_by_user_id,
        runtime_profile_id=str(install.get("runtime_profile_id") or "").strip() or None,
        runtime_mode=_runtime_target_to_specialist_mode(runtime_target),
        tool_toggles=_tool_toggles_from_config(config),
        connector_bindings=dict(install.get("connector_bindings") or {}),
        channel_bindings=channels,
        metadata=metadata,
        write_bible_version=True,
    )


async def create_draft_deployed_agent(
    *,
    current_user: Optional[Dict[str, Any]],
    owner_workspace_id: str,
    name: str,
    avatar: Optional[str] = None,
    persona: str = "",
    system_prompt: str = "",
    channels: Optional[Dict[str, Any]] = None,
    knowledge_sources: Optional[List[Dict[str, Any]]] = None,
    runtime_target: Optional[str] = None,
    billing_plan: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
    config: Optional[Dict[str, Any]] = None,
    runtime_profile_id: Optional[str] = None,
    provider: Optional[str] = None,
    model: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    resolved_workspace_id = require_deployed_agent_admin_access(
        current_user=current_user,
        workspace_id=owner_workspace_id,
    )
    workspace = await control_plane_repository.get_workspace_by_id(resolved_workspace_id)
    if not isinstance(workspace, dict):
        raise ValueError("Workspace is unavailable.")
    tenant_id = _normalize_text(workspace.get("tenant_id"))
    if not tenant_id:
        raise ValueError("Workspace is missing a tenant binding.")
    existing_deployed_agents = await control_plane_repository.list_deployed_agents_for_workspace(
        resolved_workspace_id,
        tenant_id=tenant_id,
    )
    normalized_name = _normalize_text(name)
    if not normalized_name:
        raise ValueError("name is required.")
    workspace_defaults = _workspace_admin_defaults(workspace)
    config_payload = _coerce_dict(config)
    metadata_payload = _coerce_dict(metadata)
    normalized_channels = await _enrich_deployed_agent_channels(
        owner_workspace_id=resolved_workspace_id,
        channels=channels,
    )
    try:
        draft_config = _apply_provider_model_selection_to_config(
            _apply_workspace_admin_defaults_to_config(
                _config_from_record(
                    {
                        "name": normalized_name,
                        "avatar": avatar,
                        "persona": persona,
                        "system_prompt": system_prompt,
                        "channels": normalized_channels,
                        "knowledge_sources": _normalize_knowledge_sources(knowledge_sources),
                        "runtime_target": runtime_target,
                        "billing_plan": billing_plan,
                        "metadata": _normalize_deployed_agent_metadata(metadata),
                        "config": config_payload,
                    },
                    runtime_profile_id=runtime_profile_id,
                ),
                workspace_defaults=workspace_defaults,
                runtime_target_supplied=runtime_target is not None,
                billing_plan_supplied=billing_plan is not None,
                config_payload=config_payload,
                legacy_metadata=metadata_payload,
            ),
            provider=provider,
            model=model,
            owner_workspace_id=resolved_workspace_id,
        )
    except ValueError as error:
        raise _http_bad_request(str(error)) from error
    explicit_mode = "studio_agent_mode" in config_payload
    effective_mode = (
        draft_config.studio_agent_mode
        if explicit_mode
        else deployed_agent_runtime_contract_service.infer_studio_agent_mode(
            runtime_placement=draft_config.runtime_placement,
            runtime_target=draft_config.runtime_target,
            runtime_supplier=(
                _coerce_dict(_coerce_dict(draft_config.runtime_supply).get("supplier")).get("kind")
            ),
        )
    )
    runtime_targets = None
    runtime_inventory = None
    if effective_mode != deployed_agent_runtime_contract_service.STUDIO_AGENT_MODE_TEXT:
        if effective_mode == deployed_agent_runtime_contract_service.STUDIO_AGENT_MODE_SELF_HOSTED:
            runtime_inventory = await runtime_attachment_service.list_workspace_runtime_attachments(
                tenant_id=tenant_id,
                workspace_id=resolved_workspace_id,
            )
        runtime_targets = await runtime_attachment_service.list_workspace_runtime_targets(
            tenant_id=tenant_id,
            workspace_id=resolved_workspace_id,
            inventory=runtime_inventory,
        )
    _enforce_mode_capability_matrix(
        config=draft_config,
        stage="create",
        runtime_targets=runtime_targets,
        explicit_mode=explicit_mode,
    )
    _enforce_ai_source_provider_runtime(config=draft_config)
    _enforce_runtime_eligibility(
        workspace=workspace,
        workspace_id=resolved_workspace_id,
        config=draft_config,
        stage="create",
        runtime_targets=runtime_targets,
        runtime_inventory=runtime_inventory,
        explicit_mode=explicit_mode,
        existing_deployed_agents=[dict(item) for item in list(existing_deployed_agents or []) if isinstance(item, dict)],
    )
    draft_metadata = _metadata_from_config(draft_config)
    try:
        draft_metadata = _metadata_with_self_hosted_runtime_binding(
            metadata=draft_metadata,
            config=draft_config,
            runtime_inventory=runtime_inventory,
            workspace_id=resolved_workspace_id,
            require_binding=(
                effective_mode == deployed_agent_runtime_contract_service.STUDIO_AGENT_MODE_SELF_HOSTED
            ),
        )
    except (ValueError, runtime_attachment_service.RuntimeAttachmentSelectionError) as error:
        raise _http_conflict(str(error)) from error
    operational_state = _operational_state_from_record({"deployment_state": "draft"})
    manifest = _base_manifest(
        name=draft_config.name,
        persona=draft_config.persona,
        system_prompt=draft_config.system_prompt,
        runtime_target=draft_config.runtime_target,
        channels=_channels_payload_from_config(draft_config),
        escalation_triggers=_escalation_triggers_for_config(draft_config),
    )
    backing_install = await agent_specialist_repository.create_workspace_specialist(
        tenant_id=tenant_id,
        workspace_id=resolved_workspace_id,
        manifest=manifest,
        created_by_user_id=_normalize_optional_text((current_user or {}).get("user_id")),
        label=draft_config.name,
        runtime_profile_id=_normalize_optional_text(runtime_profile_id),
        runtime_mode=_runtime_target_to_specialist_mode(draft_config.runtime_target),
        tool_toggles=_tool_toggles_from_config(draft_config),
        channel_bindings=_channels_payload_from_config(draft_config),
        metadata={
            **draft_metadata,
            "source": "deployed_agent",
            "visibility": "private",
            "specialist_mode": "owner_edit",
            "public_intro": draft_config.customer_policy.public_intro,
            "public_core_value": draft_config.customer_policy.public_core_value,
            "platform_cta_label": draft_config.customer_policy.public_start_cta_label,
            "platform_cta_url": draft_config.customer_policy.public_start_cta_url,
            "health_safety_assistant_name": draft_config.safety_policy.assistant_name,
            "context_budget_preset": draft_config.memory_policy.context_budget_preset,
            "retention_preset": draft_config.memory_policy.retention_preset,
            "escalation_preset": draft_config.escalation_policy.preset,
            "handoff_mode": draft_config.escalation_policy.handoff_mode,
            "owner_notification_destination": draft_config.escalation_policy.owner_notification_destination,
        },
    )
    if not isinstance(backing_install, dict):
        raise ValueError("Failed to create backing specialist install.")
    deployed_agent = await control_plane_repository.create_deployed_agent(
        tenant_id=tenant_id,
        owner_workspace_id=resolved_workspace_id,
        backing_install_id=_normalize_text(backing_install.get("id")),
        created_by_user_id=_normalize_optional_text((current_user or {}).get("user_id")),
        name=draft_config.name,
        avatar=draft_config.avatar,
        persona=draft_config.persona,
        system_prompt=draft_config.system_prompt,
        deployment_state="draft",
        channels=_channels_payload_from_config(draft_config),
        knowledge_sources=draft_config.knowledge_sources,
        runtime_target=draft_config.runtime_target,
        billing_plan=draft_config.billing_plan,
        metadata=draft_metadata,
        operational_state=_serialized_operational_state(operational_state),
    )
    if not isinstance(deployed_agent, dict):
        raise ValueError("Failed to persist deployed agent.")
    await mirror_deployed_agent_to_backing_specialist(
        deployed_agent=deployed_agent,
        backing_install=backing_install,
        updated_by_user_id=_normalize_optional_text((current_user or {}).get("user_id")),
    )
    await _append_deployed_agent_audit_event(
        tenant_id=tenant_id,
        workspace_id=resolved_workspace_id,
        deployed_agent=deployed_agent,
        action="deployed_agent_created",
        title="Deployed agent created",
        summary=f"{draft_config.name} was created in draft state.",
        status="created",
        payload={
            "lifecycle_state": "draft",
            "runtime_target": draft_config.runtime_target,
            "studio_agent_mode": draft_config.studio_agent_mode,
        },
        actor_user_id=_normalize_optional_text((current_user or {}).get("user_id")),
    )
    await _append_full_access_runtime_review_event(
        tenant_id=tenant_id,
        workspace_id=resolved_workspace_id,
        deployed_agent=deployed_agent,
        lifecycle_action="created",
        actor_user_id=_normalize_optional_text((current_user or {}).get("user_id")),
    )
    return project_deployed_agent(deployed_agent, include_internal=True)


async def list_deployed_agents(
    *,
    current_user: Optional[Dict[str, Any]],
    owner_workspace_id: str,
    deployment_state: Optional[str] = None,
) -> Dict[str, Any]:
    resolved_workspace_id = require_deployed_agent_admin_access(
        current_user=current_user,
        workspace_id=owner_workspace_id,
    )
    workspace = await control_plane_repository.get_workspace_by_id(resolved_workspace_id)
    if not isinstance(workspace, dict):
        raise _http_bad_request("Workspace is unavailable.")
    tenant_id = _normalize_text(workspace.get("tenant_id"))
    items = await control_plane_repository.list_deployed_agents_for_workspace(
        resolved_workspace_id,
        tenant_id=tenant_id,
        deployment_state=_normalize_deployment_state(deployment_state, default="") if deployment_state else None,
    )
    return {
        "items": [
            project_deployed_agent(item, include_internal=True)
            for item in items
            if isinstance(item, dict)
        ]
    }


async def get_deployed_agent_detail(
    *,
    deployed_agent_id: str,
    current_user: Optional[Dict[str, Any]],
    owner_workspace_id: str,
) -> Optional[Dict[str, Any]]:
    resolved_workspace_id = require_deployed_agent_admin_access(
        current_user=current_user,
        workspace_id=owner_workspace_id,
    )
    workspace = await control_plane_repository.get_workspace_by_id(resolved_workspace_id)
    if not isinstance(workspace, dict):
        raise _http_bad_request("Workspace is unavailable.")
    tenant_id = _normalize_text(workspace.get("tenant_id"))
    deployed_agent = await control_plane_repository.get_deployed_agent_by_id(
        deployed_agent_id,
        tenant_id=tenant_id,
        owner_workspace_id=resolved_workspace_id,
    )
    if not isinstance(deployed_agent, dict):
        return None
    backing_install = await agent_specialist_repository.get_workspace_specialist(
        _normalize_text(deployed_agent.get("backing_install_id")),
        tenant_id=tenant_id,
        workspace_id=resolved_workspace_id,
    )
    return {
        **dict(project_deployed_agent(deployed_agent, include_internal=True) or {}),
        "backing_install": backing_install,
    }


async def evaluate_deployed_shop_assistant_customer_question(
    *,
    deployed_agent_id: str,
    current_user: Optional[Dict[str, Any]],
    owner_workspace_id: str,
    customer_message: str,
    connector_id: Optional[str] = None,
) -> Dict[str, Any]:
    resolved_workspace_id = require_deployed_agent_admin_access(
        current_user=current_user,
        workspace_id=owner_workspace_id,
    )
    workspace = await control_plane_repository.get_workspace_by_id(resolved_workspace_id)
    if not isinstance(workspace, dict):
        raise _http_bad_request("Workspace is unavailable.")
    tenant_id = _normalize_text(workspace.get("tenant_id"))
    deployed_agent = await control_plane_repository.get_deployed_agent_by_id(
        deployed_agent_id,
        tenant_id=tenant_id,
        owner_workspace_id=resolved_workspace_id,
    )
    if not isinstance(deployed_agent, dict):
        raise HTTPException(status_code=404, detail="Deployed agent is unavailable.")
    _enforce_deployed_agent_service_decision(
        "shop_evaluate",
        deployed_agent=deployed_agent,
        tenant_id=tenant_id,
        workspace_id=resolved_workspace_id,
        current_user=current_user,
    )
    try:
        result = shop_assistant_revenue_agent_service.evaluate_shop_assistant_customer_question(
            deployed_agent,
            workspace_id=resolved_workspace_id,
            customer_message=customer_message,
            connector_id=connector_id,
        )
    except PermissionError as error:
        raise HTTPException(status_code=403, detail=str(error)) from error
    except ValueError as error:
        raise _http_bad_request(str(error)) from error

    metadata = _coerce_dict(deployed_agent.get("metadata"))
    provider_metadata = {
        "effective_provider": metadata.get("effective_provider") or metadata.get("provider"),
        "effective_model": metadata.get("effective_model") or metadata.get("model"),
        "fallback_provider": metadata.get("fallback_provider"),
        "fallback_model": metadata.get("fallback_model"),
    }
    result["activity_billing_evidence"] = (
        shop_assistant_revenue_agent_service.build_shop_assistant_activity_billing_evidence(
            result,
            public_tier=_normalize_text(metadata.get("public_tier"), default="pro"),
            total_tokens=int(metadata.get("last_total_tokens") or metadata.get("estimated_total_tokens") or 900),
            provider_metadata={key: value for key, value in provider_metadata.items() if value},
            runtime_metadata={
                "runtime_target": _normalize_text(deployed_agent.get("runtime_target"), default="cloud"),
                "runtime_placement": _normalize_text(metadata.get("runtime_placement"), default="managed_cloud"),
                "runtime_supply": _normalize_text(metadata.get("runtime_supply"), default="empyralis"),
            },
        )
    )

    # Phase 3: Persist activity events to the ledger from every call path.
    activity_events = list(result.get("activity_events") or [])
    for event in activity_events:
        try:
            await activity_ledger_service.append_activity_event(
                tenant_id=tenant_id,
                workspace_id=resolved_workspace_id,
                actor_type=str(event.get("actor_type") or "deployed_agent"),
                actor_id=str(event.get("actor_id") or deployed_agent_id),
                event_class=str(event.get("event") or "studio.proof.shop_assistant.interaction"),
                title=str(event.get("event") or "Shop Assistant Interaction").replace("studio.proof.shop_assistant.", "").replace("_", " ").title(),
                summary=str(event.get("customer_message_summary") or customer_message),
                status=str(event.get("status") or "logged"),
                review_required=bool(event.get("approval_required")),
                payload=dict(event.get("metadata") or {}),
            )
        except Exception:
            pass

    # Phase 4: Agent acts on its own reasoning. If the action is bounded
    # and reasonable it executes; if more info is needed the agent asks
    # the customer in conversation. No approval records are created.

    # Phase 5: Persist billing evidence as activity events for the credit ledger.
    billing_evidence = _coerce_dict(result.get("activity_billing_evidence"))
    credit_line_items = list(billing_evidence.get("credit_line_items") or [])
    visible_activity = _coerce_dict(billing_evidence.get("visible_activity"))
    billing_accounting_warnings: List[str] = []
    shop_run_id = f"shop-{deployed_agent_id}-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S%f')}"
    ai_usage_records_by_index: Dict[int, Dict[str, Any]] = {}
    for index, item in enumerate(credit_line_items):
        if not isinstance(item, dict):
            continue
        credit_item_type = str(item.get("credit_item_type") or "").strip()
        quantity = max(0, int(item.get("quantity") or 0))
        billing_source = str(item.get("billing_source") or "empyralis_credits").strip()
        if not credit_item_type.startswith("ai_") or quantity <= 0:
            continue
        if not usage_accounting_service.is_platform_paid_usage_value(billing_source):
            continue
        try:
            usage_record = usage_accounting_service.build_usage_accounting_record(
                run_id=shop_run_id,
                tenant_id=tenant_id,
                workspace_id=resolved_workspace_id,
                deployed_agent_id=deployed_agent_id,
                source_surface="shop_assistant",
                effective_provider=provider_metadata.get("effective_provider"),
                effective_model=provider_metadata.get("effective_model"),
                input_tokens=quantity,
                output_tokens=0,
                total_tokens=quantity,
                allow_provider_fallback=False,
            )
        except Exception:
            usage_record = {}
        if isinstance(usage_record, dict) and usage_record.get("usage_record_id"):
            usage_record["estimation_mode"] = "provider_usage_missing"
        usage_validation_error = (
            usage_accounting_service.platform_paid_usage_validation_error(usage_record)
            if isinstance(usage_record, dict)
            else "missing_provider_token_usage"
        )
        if isinstance(usage_record, dict) and bool(usage_record.get("pricing_known")) and not usage_validation_error:
            ai_usage_records_by_index[index] = usage_record
        else:
            billing_accounting_warnings.append(
                "shop_assistant_platform_credit_ai_usage_requires_provider_measured_usage"
            )

    for index, item in enumerate(credit_line_items):
        credit_item_type = str(item.get("credit_item_type") or "").strip()
        billing_source = str(item.get("billing_source") or "empyralis_credits").strip()
        platform_ai_without_provider_usage = (
            credit_item_type.startswith("ai_")
            and usage_accounting_service.is_platform_paid_usage_value(billing_source)
            and index not in ai_usage_records_by_index
        )
        try:
            await activity_ledger_service.append_activity_event(
                tenant_id=tenant_id,
                workspace_id=resolved_workspace_id,
                actor_type="deployed_agent",
                actor_id=deployed_agent_id,
                event_class=(
                    "studio.proof.shop_assistant.credit_unmetered"
                    if platform_ai_without_provider_usage
                    else "studio.proof.shop_assistant.credit_consumed"
                ),
                title=(
                    "Shop Assistant AI usage not charged"
                    if platform_ai_without_provider_usage
                    else "Shop Assistant credit usage"
                ),
                summary=f"{visible_activity.get('tier_label', 'Pro')} tier · {item.get('credit_item_type', 'usage')}: {item.get('quantity', 0)} {item.get('quantity_unit', 'tokens')}",
                status="blocked" if platform_ai_without_provider_usage else "logged",
                review_required=False,
                payload={
                    "credit_item_type": str(item.get("credit_item_type") or ""),
                    "quantity": item.get("quantity"),
                    "quantity_unit": str(item.get("quantity_unit") or ""),
                    "billing_source": str(item.get("billing_source") or ""),
                    "public_tier": str(item.get("public_tier") or ""),
                    "credit_multiplier": item.get("credit_multiplier"),
                    "used_credits": visible_activity.get("used_credits"),
                    "charge_blocked": platform_ai_without_provider_usage,
                    "blocked_reason": (
                        "provider_measured_usage_required"
                        if platform_ai_without_provider_usage
                        else None
                    ),
                },
            )
        except Exception:
            pass
    if billing_accounting_warnings:
        result["billing_accounting_state"] = "charge_blocked"
        result["billing_accounting_warnings"] = sorted(set(billing_accounting_warnings))

    # Phase 6: Persist real usage line items to the deployed_agent_monthly_cost_ledger.
    for index, item in enumerate(credit_line_items):
        credit_item_type = str(item.get("credit_item_type") or "").strip()
        quantity = max(0, int(item.get("quantity") or 0))
        billing_source = str(item.get("billing_source") or "empyralis_credits").strip()
        if credit_item_type.startswith("ai_") and quantity > 0:
            usage_record = ai_usage_records_by_index.get(index)
            if usage_accounting_service.is_platform_paid_usage_value(billing_source) and usage_record:
                try:
                    await control_plane_repository.record_deployed_agent_monthly_cost_ledger_entry(
                        tenant_id=tenant_id,
                        workspace_id=resolved_workspace_id,
                        deployed_agent_id=deployed_agent_id,
                        run_id=shop_run_id,
                        run_status="completed",
                        provider=usage_record.get("provider") or provider_metadata.get("effective_provider"),
                        model=usage_record.get("model") or provider_metadata.get("effective_model"),
                        total_tokens=int(usage_record.get("total_tokens") or quantity),
                        estimated_cost_usd=float(usage_record.get("estimated_cost_usd") or 0.0),
                        metadata={
                            "credit_item_type": credit_item_type,
                            "credit_quantity": quantity,
                            "credit_quantity_unit": str(item.get("quantity_unit") or "tokens"),
                            "credit_multiplier": float(item.get("credit_multiplier") or 0.0),
                            "billing_source": billing_source,
                            "source_surface": "shop_assistant",
                            "pricing_source": usage_record.get("pricing_source"),
                            "pricing_known": bool(usage_record.get("pricing_known")),
                        },
                    )
                except Exception:
                    pass
                continue
            LOGGER.info(
                "Skipping heuristic shop-assistant AI cost ledger entry for non-platform billing source",
                extra={
                    "workspace_id": resolved_workspace_id,
                    "deployed_agent_id": deployed_agent_id,
                    "billing_source": billing_source,
                    "credit_item_type": credit_item_type,
                },
            )
            continue
        try:
            if credit_item_type == "connector_read":
                await control_plane_repository.record_deployed_agent_monthly_cost_ledger_entry(
                    tenant_id=tenant_id,
                    workspace_id=resolved_workspace_id,
                    deployed_agent_id=deployed_agent_id,
                    run_id=shop_run_id,
                    run_status="completed",
                    provider=provider_metadata.get("effective_provider"),
                    model=provider_metadata.get("effective_model"),
                    total_tokens=0,
                    estimated_cost_usd=0.0,
                    metadata={
                        "credit_item_type": credit_item_type,
                        "credit_quantity": quantity,
                        "credit_quantity_unit": str(item.get("quantity_unit") or "reads"),
                        "credit_multiplier": float(item.get("credit_multiplier") or 0.0),
                        "billing_source": str(item.get("billing_source") or "empyralis_credits"),
                        "connector_kind": str(item.get("connector_kind") or ""),
                        "connector_id": str(item.get("connector_id") or ""),
                        "source_surface": "shop_assistant",
                    },
                )
        except Exception:
            pass

    try:
        from server_modules import agent_completion_notification_service

        await agent_completion_notification_service.notify_completion(
            workspace_id=resolved_workspace_id,
            agent_name=f"Agent {deployed_agent_id}",
            result_summary=result,
            artifact_url=None,
        )
    except Exception:
        pass

    return result


async def get_deployed_agent_telegram_readiness(
    *,
    current_user: Optional[Dict[str, Any]],
    owner_workspace_id: str,
    deployed_agent_id: Optional[str] = None,
) -> Dict[str, Any]:
    resolved_workspace_id = require_deployed_agent_admin_access(
        current_user=current_user,
        workspace_id=owner_workspace_id,
    )
    workspace = await control_plane_repository.get_workspace_by_id(resolved_workspace_id)
    if not isinstance(workspace, dict):
        raise _http_bad_request("Workspace is unavailable.")
    tenant_id = _normalize_text(workspace.get("tenant_id"))
    deployed_agent: Optional[Dict[str, Any]] = None
    if _normalize_optional_text(deployed_agent_id):
        deployed_agent = await control_plane_repository.get_deployed_agent_by_id(
            _normalize_text(deployed_agent_id),
            tenant_id=tenant_id,
            owner_workspace_id=resolved_workspace_id,
        )
        if not isinstance(deployed_agent, dict):
            raise _http_bad_request("Deployed agent is unavailable.")
    _enforce_deployed_agent_service_decision(
        "telegram_readiness",
        deployed_agent=deployed_agent or {},
        tenant_id=tenant_id,
        workspace_id=resolved_workspace_id,
        current_user=current_user,
    )

    status_payload = _workspace_telegram_status_payload(resolved_workspace_id)
    connector_options = _workspace_telegram_connector_options(resolved_workspace_id)
    connectors_by_id = {
        _normalize_text(item.get("id")): item
        for item in connector_options
        if _normalize_text(item.get("id"))
    }
    config = _config_from_record(deployed_agent or {})
    channels = _normalize_channels((deployed_agent or {}).get("channels") or {})
    telegram_binding = _coerce_dict(channels.get("telegram"))
    telegram_enabled = bool(telegram_binding.get("enabled"))
    selected_connector_id = _normalize_optional_text(
        telegram_binding.get("connector_id")
        or telegram_binding.get("credential_id")
    )
    selected_connector = connectors_by_id.get(selected_connector_id or "") if selected_connector_id else None
    webhook = _coerce_dict(status_payload.get("webhook"))

    blockers: List[Dict[str, Any]] = []
    warnings: List[Dict[str, Any]] = []

    webhook_status = _normalize_text(webhook.get("status")).lower()
    if webhook_status and webhook_status != "live":
        blockers.append(
            _studio_issue(
                code=_normalize_text(webhook.get("issue_code"), default="telegram_webhook_not_ready"),
                message=_normalize_text(webhook.get("issue"), default="Telegram webhook delivery is not ready."),
                guidance=_normalize_optional_text(webhook.get("guidance")),
            )
        )

    for issue in _coerce_list(status_payload.get("issues")):
        payload = _coerce_dict(issue)
        severity = _normalize_text(payload.get("severity")).lower()
        target = blockers if severity == "setup_needed" else warnings
        target.append(
            _studio_issue(
                code=_normalize_text(payload.get("code"), default="telegram_status_issue"),
                message=_normalize_text(payload.get("message"), default="Telegram status issue"),
                severity="blocker" if severity == "setup_needed" else "warning",
            )
        )

    if telegram_enabled:
        if len(config.tool_policy.enabled_tools or []) == 0:
            blockers.append(
                _studio_issue(
                    code="studio_tool_scope_required",
                    message="Select at least one allowed tool before deploying this Telegram specialist.",
                    guidance="Open the Launch step and choose the smallest tool scope this specialist needs.",
                )
            )
        if not selected_connector_id:
            blockers.append(
                _studio_issue(
                    code="telegram_connector_required",
                    message="Select a Telegram bot connector before deploying this specialist.",
                    guidance="Open the Channels step and bind one of the workspace-visible Telegram connectors.",
                )
            )
        elif not isinstance(selected_connector, dict):
            blockers.append(
                _studio_issue(
                    code="telegram_connector_unavailable",
                    message="The selected Telegram connector is not available for this workspace.",
                    guidance="Choose a different connector or recreate the missing Telegram bot connector.",
                )
            )
        if selected_connector_id and not bool(telegram_binding.get("is_inbound_owner")):
            blockers.append(
                _studio_issue(
                    code="telegram_inbound_owner_missing",
                    message="Telegram inbound ownership is not bound to this specialist yet.",
                    guidance="Save the deployment again after choosing a Telegram connector so Studio can claim inbound ownership automatically.",
                )
            )
        if selected_connector_id and not _normalize_optional_text(telegram_binding.get("endpoint_key")):
            blockers.append(
                _studio_issue(
                    code="telegram_endpoint_key_missing",
                    message="Telegram routing is missing the inbound endpoint key for this specialist.",
                    guidance="Re-save the deployment after choosing a Telegram connector so Studio can derive the inbound endpoint key.",
                )
            )
    elif deployed_agent is not None:
        warnings.append(
            _studio_issue(
                code="telegram_disabled",
                message="Telegram is disabled for this specialist, so deploy will stay unavailable.",
                guidance="Enable Telegram in the Channels step when the specialist is ready for customer traffic.",
                severity="warning",
            )
        )

    if isinstance(selected_connector, dict):
        if _normalize_text(selected_connector.get("profile_status")).lower() == "setup_needed":
            blockers.append(
                _studio_issue(
                    code=_normalize_text(selected_connector.get("profile_issue_code"), default="telegram_connector_profile_setup_needed"),
                    message=_normalize_text(selected_connector.get("profile_issue"), default="The selected Telegram connector profile needs setup."),
                )
            )
        elif _normalize_optional_text(selected_connector.get("profile_issue")):
            warnings.append(
                _studio_issue(
                    code=_normalize_text(selected_connector.get("profile_issue_code"), default="telegram_connector_profile_warning"),
                    message=_normalize_text(selected_connector.get("profile_issue"), default="The selected Telegram connector profile is degraded."),
                    severity="warning",
                )
            )
        if _normalize_optional_text(selected_connector.get("last_error")):
            warnings.append(
                _studio_issue(
                    code=_normalize_text(selected_connector.get("last_error_category"), default="telegram_connector_last_error"),
                    message=_normalize_text(selected_connector.get("last_error"), default="The selected Telegram connector reported a recent runtime error."),
                    severity="warning",
                )
            )

    configured_binding = {
        "enabled": telegram_enabled,
        "connector_id": selected_connector_id,
        "credential_id": _normalize_optional_text(telegram_binding.get("credential_id")),
        "endpoint_key": _normalize_optional_text(telegram_binding.get("endpoint_key")),
        "is_inbound_owner": bool(telegram_binding.get("is_inbound_owner")),
        "bot_username": _normalize_optional_text(telegram_binding.get("bot_username"))
        or _normalize_optional_text((selected_connector or {}).get("bot_username")),
        "webhook_path": _normalize_optional_text(telegram_binding.get("webhook_path"))
        or _normalize_optional_text((selected_connector or {}).get("webhook_path")),
        "webhook_url": _normalize_optional_text((selected_connector or {}).get("webhook_url")),
        "label": _normalize_optional_text((selected_connector or {}).get("label")),
    }
    ready_for_live = telegram_enabled and len(blockers) == 0 and isinstance(selected_connector, dict)
    status = "ready" if ready_for_live else ("setup_needed" if blockers else "draft")
    return {
        "channel": "telegram",
        "workspace_id": resolved_workspace_id,
        "deployed_agent_id": _normalize_optional_text((deployed_agent or {}).get("id")),
        "ready_for_live": ready_for_live,
        "status": status,
        "blockers": blockers,
        "warnings": warnings,
        "next_action": _studio_next_action(
            telegram_enabled=telegram_enabled,
            blockers=blockers,
            connector_options=connector_options,
        ),
        "configured_binding": configured_binding,
        "tool_scope": {
            "selected_tool_ids": list(config.tool_policy.enabled_tools or []),
            "catalog": [dict(item) for item in _STUDIO_TOOL_SCOPE_CATALOG],
        },
        "connectors": connector_options,
        "webhook": webhook,
        "autopilot": _coerce_dict(status_payload.get("autopilot")),
        "whatsapp": dict(_STUDIO_WHATSAPP_STATUS),
    }


async def _enrich_deployed_agent_channels(
    *,
    owner_workspace_id: str,
    channels: Any,
) -> Dict[str, Dict[str, Any]]:
    normalized = _normalize_channels(channels)
    telegram_binding = _coerce_dict(normalized.get("telegram"))
    if not bool(telegram_binding.get("enabled")):
        return normalized
    connector_id = _normalize_optional_text(
        telegram_binding.get("connector_id")
        or telegram_binding.get("credential_id")
    )
    if not connector_id:
        return normalized
    connector_options = _workspace_telegram_connector_options(owner_workspace_id)
    selected = next((item for item in connector_options if _normalize_text(item.get("id")) == connector_id), None)
    if not isinstance(selected, dict):
        raise ValueError("The selected Telegram connector is not available for this workspace.")
    webhook = _coerce_dict(_workspace_telegram_status_payload(owner_workspace_id).get("webhook"))
    normalized["telegram"] = {
        **telegram_binding,
        "enabled": True,
        "connector_id": connector_id,
        "credential_id": _normalize_optional_text(selected.get("credential_id")) or connector_id,
        "endpoint_key": _normalize_optional_text(selected.get("endpoint_key")),
        "is_inbound_owner": True,
        "webhook_path": _normalize_optional_text(selected.get("webhook_path")),
        "bot_username": _normalize_optional_text(selected.get("bot_username")),
        "delivery_mode": _normalize_optional_text(webhook.get("delivery_mode")),
    }
    return normalized


async def list_deployed_agent_analytics(
    *,
    current_user: Optional[Dict[str, Any]],
    owner_workspace_id: str,
) -> Dict[str, Any]:
    resolved_workspace_id = require_deployed_agent_admin_access(
        current_user=current_user,
        workspace_id=owner_workspace_id,
    )
    workspace = await control_plane_repository.get_workspace_by_id(resolved_workspace_id)
    if not isinstance(workspace, dict):
        raise _http_bad_request("Workspace is unavailable.")
    tenant_id = _normalize_text(workspace.get("tenant_id"))
    deployed_agents = await control_plane_repository.list_deployed_agents_for_workspace(
        resolved_workspace_id,
        tenant_id=tenant_id,
    )
    return await deployed_agent_analytics_service.summarize_workspace_deployed_agent_analytics(
        tenant_id=tenant_id,
        workspace_id=resolved_workspace_id,
        deployed_agents=deployed_agents,
    )


async def get_deployed_agent_analytics(
    *,
    deployed_agent_id: str,
    current_user: Optional[Dict[str, Any]],
    owner_workspace_id: str,
) -> Optional[Dict[str, Any]]:
    resolved_workspace_id = require_deployed_agent_admin_access(
        current_user=current_user,
        workspace_id=owner_workspace_id,
    )
    workspace = await control_plane_repository.get_workspace_by_id(resolved_workspace_id)
    if not isinstance(workspace, dict):
        raise _http_bad_request("Workspace is unavailable.")
    tenant_id = _normalize_text(workspace.get("tenant_id"))
    deployed_agent = await control_plane_repository.get_deployed_agent_by_id(
        deployed_agent_id,
        tenant_id=tenant_id,
        owner_workspace_id=resolved_workspace_id,
    )
    if not isinstance(deployed_agent, dict):
        return None
    _enforce_deployed_agent_service_decision(
        "analytics_read",
        deployed_agent=deployed_agent,
        tenant_id=tenant_id,
        workspace_id=resolved_workspace_id,
        current_user=current_user,
    )
    return await deployed_agent_analytics_service.summarize_deployed_agent_analytics(
        tenant_id=tenant_id,
        workspace_id=resolved_workspace_id,
        deployed_agent=deployed_agent,
    )


async def update_deployed_agent(
    *,
    deployed_agent_id: str,
    current_user: Optional[Dict[str, Any]],
    owner_workspace_id: str,
    updates: Dict[str, Any],
) -> Optional[Dict[str, Any]]:
    resolved_workspace_id = require_deployed_agent_admin_access(
        current_user=current_user,
        workspace_id=owner_workspace_id,
    )
    workspace = await control_plane_repository.get_workspace_by_id(resolved_workspace_id)
    if not isinstance(workspace, dict):
        raise _http_bad_request("Workspace is unavailable.")
    tenant_id = _normalize_text(workspace.get("tenant_id"))
    existing = await control_plane_repository.get_deployed_agent_by_id(
        deployed_agent_id,
        tenant_id=tenant_id,
        owner_workspace_id=resolved_workspace_id,
    )
    if not isinstance(existing, dict):
        return None
    normalized_updates: Dict[str, Any] = {}
    candidate_record: Dict[str, Any] = dict(existing)
    if "name" in updates:
        clean_name = _normalize_text(updates.get("name"))
        if not clean_name:
            raise _http_bad_request("name cannot be empty.")
        candidate_record["name"] = clean_name
    if "avatar" in updates:
        candidate_record["avatar"] = _normalize_optional_text(updates.get("avatar"))
    if "persona" in updates:
        candidate_record["persona"] = _normalize_text(updates.get("persona"))
    if "system_prompt" in updates:
        candidate_record["system_prompt"] = _normalize_text(updates.get("system_prompt"))
    if "channels" in updates:
        try:
            candidate_record["channels"] = await _enrich_deployed_agent_channels(
                owner_workspace_id=resolved_workspace_id,
                channels=updates.get("channels"),
            )
        except ValueError as error:
            raise _http_bad_request(str(error)) from error
    if "knowledge_sources" in updates:
        try:
            candidate_record["knowledge_sources"] = _normalize_knowledge_sources(updates.get("knowledge_sources"))
        except ValueError as error:
            raise _http_bad_request(str(error)) from error
    if "runtime_target" in updates:
        candidate_record["runtime_target"] = _normalize_runtime_target(updates.get("runtime_target"))
    if "billing_plan" in updates:
        candidate_record["billing_plan"] = _normalize_text(
            updates.get("billing_plan"),
            default=config_defaults_service.default_deployed_agent_billing_plan(),
        )
    if "is_public" in updates:
        candidate_record["is_public"] = bool(updates.get("is_public"))
    if "category" in updates:
        normalized_category = _normalize_optional_text(updates.get("category"))
        candidate_record["category"] = normalized_category.lower() if normalized_category else None
    operator_fields_requested = {
        key for key in ("quality_stars", "cost_tier")
        if key in updates
    }
    if operator_fields_requested and not auth_module.current_user_has_auth_admin_access(current_user):
        raise HTTPException(
            status_code=403,
            detail="Only platform operators may update marketplace ratings and cost tiers.",
        )
    if "quality_stars" in updates:
        raw_quality_stars = updates.get("quality_stars")
        if raw_quality_stars is None:
            candidate_record["quality_stars"] = None
        else:
            try:
                parsed_quality_stars = int(raw_quality_stars)
            except (TypeError, ValueError) as error:
                raise _http_bad_request("quality_stars must be an integer between 1 and 5.") from error
            if parsed_quality_stars < 1 or parsed_quality_stars > 5:
                raise _http_bad_request("quality_stars must be between 1 and 5.")
            candidate_record["quality_stars"] = parsed_quality_stars
    if "cost_tier" in updates:
        raw_cost_tier = _normalize_optional_text(updates.get("cost_tier"))
        if raw_cost_tier is None:
            candidate_record["cost_tier"] = None
        else:
            normalized_cost_tier = raw_cost_tier.lower()
            if normalized_cost_tier not in {"free", "standard", "premium"}:
                raise _http_bad_request("cost_tier must be one of free, standard, or premium.")
            candidate_record["cost_tier"] = normalized_cost_tier
    if "metadata" in updates:
        try:
            candidate_record["metadata"] = _normalize_deployed_agent_metadata(
                updates.get("metadata"),
                existing=_coerce_dict(existing.get("metadata")),
            )
        except ValueError as error:
            raise _http_bad_request(str(error)) from error
    if "config" in updates:
        if updates.get("config") is not None and not isinstance(updates.get("config"), dict):
            raise _http_bad_request("config must be an object.")
        candidate_record["config"] = _coerce_dict(updates.get("config"))
    if "deployment_state" in updates and updates.get("deployment_state") is not None:
        next_state = str(updates.get("deployment_state") or "").strip().lower()
        try:
            validated_state = validate_state_transition(existing.get("deployment_state"), next_state)
        except ValueError as error:
            raise _http_conflict(str(error)) from error
        if validated_state in {"live", "paused", "suspended"}:
            raise _http_conflict(
                "Use dedicated lifecycle controls for live, paused, and suspended transitions."
            )
        candidate_record["deployment_state"] = validated_state
    if candidate_record == existing and "provider" not in updates and "model" not in updates:
        raise _http_bad_request("At least one deployed-agent field must be supplied.")
    try:
        next_config = _apply_provider_model_selection_to_config(
            _config_from_record(candidate_record),
            provider=updates.get("provider") if "provider" in updates else None,
            model=updates.get("model") if "model" in updates else None,
            owner_workspace_id=resolved_workspace_id,
        )
    except ValueError as error:
        raise _http_bad_request(str(error)) from error
    _enforce_mode_capability_matrix(
        config=next_config,
        stage="update",
        explicit_mode="config" in updates
        and isinstance(updates.get("config"), dict)
        and "studio_agent_mode" in dict(updates.get("config") or {}),
    )
    _enforce_ai_source_provider_runtime(config=next_config)
    update_explicit_mode = (
        "config" in updates
        and isinstance(updates.get("config"), dict)
        and "studio_agent_mode" in dict(updates.get("config") or {})
    )
    _enforce_runtime_eligibility(
        workspace=workspace,
        workspace_id=resolved_workspace_id,
        config=next_config,
        stage="update",
        explicit_mode=update_explicit_mode,
        existing_deployed_agents=await control_plane_repository.list_deployed_agents_for_workspace(
            resolved_workspace_id,
            tenant_id=tenant_id,
        ),
        subject_deployed_agent_id=deployed_agent_id,
    )
    runtime_inventory: Dict[str, Any] = {}
    if (
        _normalize_text(next_config.studio_agent_mode).lower()
        == deployed_agent_runtime_contract_service.STUDIO_AGENT_MODE_SELF_HOSTED
    ):
        runtime_inventory = await runtime_attachment_service.list_workspace_runtime_attachments(
            tenant_id=tenant_id,
            workspace_id=resolved_workspace_id,
        )
    next_state = _operational_state_from_record(
        {
            **existing,
            "deployment_state": candidate_record.get(
                "deployment_state",
                existing.get("deployment_state"),
            ),
        }
    )
    next_metadata = _metadata_from_config(
        next_config,
        existing_metadata=_coerce_dict(existing.get("metadata")),
    )
    try:
        next_metadata = _metadata_with_self_hosted_runtime_binding(
            metadata=next_metadata,
            config=next_config,
            runtime_inventory=runtime_inventory,
            workspace_id=resolved_workspace_id,
            require_binding=(
                _normalize_text(next_config.studio_agent_mode).lower()
                == deployed_agent_runtime_contract_service.STUDIO_AGENT_MODE_SELF_HOSTED
            ),
        )
    except (ValueError, runtime_attachment_service.RuntimeAttachmentSelectionError) as error:
        raise _http_conflict(str(error)) from error
    normalized_updates = {
        "name": next_config.name,
        "avatar": next_config.avatar,
        "persona": next_config.persona,
        "system_prompt": next_config.system_prompt,
        "channels": _channels_payload_from_config(next_config),
        "knowledge_sources": next_config.knowledge_sources,
        "runtime_target": next_config.runtime_target,
        "billing_plan": next_config.billing_plan,
        "is_public": bool(candidate_record.get("is_public")),
        "quality_stars": candidate_record.get("quality_stars"),
        "cost_tier": candidate_record.get("cost_tier"),
        "category": _normalize_optional_text(candidate_record.get("category")),
        "metadata": next_metadata,
        "operational_state": _serialized_operational_state(next_state),
    }
    if "deployment_state" in candidate_record:
        normalized_updates["deployment_state"] = candidate_record["deployment_state"]
    next_record = {**existing, **normalized_updates}
    updated_install = await mirror_deployed_agent_to_backing_specialist(
        deployed_agent=next_record,
        updated_by_user_id=_normalize_optional_text((current_user or {}).get("user_id")),
    )
    persisted = await control_plane_repository.update_deployed_agent(
        deployed_agent_id,
        tenant_id=tenant_id,
        owner_workspace_id=resolved_workspace_id,
        updates=normalized_updates,
    )
    if not isinstance(persisted, dict):
        return None
    previous_state = _normalize_deployment_state(existing.get("deployment_state"))
    current_state = _normalize_deployment_state(persisted.get("deployment_state"))
    state_changed = previous_state != current_state
    updated_fields = sorted(str(key) for key in normalized_updates.keys())
    await _append_deployed_agent_audit_event(
        tenant_id=tenant_id,
        workspace_id=resolved_workspace_id,
        deployed_agent=persisted,
        action="deployed_agent_updated",
        title="Deployed agent updated",
        summary=f"{_normalize_text(persisted.get('name'), default='Deployed agent')} configuration was updated.",
        status="updated",
        payload={
            "updated_fields": updated_fields,
            "state_changed": state_changed,
            "previous_state": previous_state,
            "current_state": current_state,
        },
        actor_user_id=_normalize_optional_text((current_user or {}).get("user_id")),
    )
    await _append_full_access_runtime_review_event(
        tenant_id=tenant_id,
        workspace_id=resolved_workspace_id,
        deployed_agent=persisted,
        lifecycle_action="updated",
        actor_user_id=_normalize_optional_text((current_user or {}).get("user_id")),
    )
    if state_changed and previous_state in {"paused", "suspended"} and current_state in {"private_test", "ready_for_review"}:
        await _append_deployed_agent_audit_event(
            tenant_id=tenant_id,
            workspace_id=resolved_workspace_id,
            deployed_agent=persisted,
            action="deployed_agent_resumed",
            title="Deployed agent resumed",
            summary=f"Lifecycle moved from {previous_state} to {current_state}.",
            status="resumed",
            payload={"previous_state": previous_state, "current_state": current_state},
            actor_user_id=_normalize_optional_text((current_user or {}).get("user_id")),
        )
    return {
        **dict(project_deployed_agent(persisted, include_internal=True) or {}),
        "backing_install": updated_install,
    }


async def deploy_deployed_agent(
    *,
    deployed_agent_id: str,
    current_user: Optional[Dict[str, Any]],
    owner_workspace_id: str,
) -> Optional[Dict[str, Any]]:
    resolved_workspace_id = require_deployed_agent_admin_access(
        current_user=current_user,
        workspace_id=owner_workspace_id,
    )
    workspace = await control_plane_repository.get_workspace_by_id(resolved_workspace_id)
    if not isinstance(workspace, dict):
        raise _http_bad_request("Workspace is unavailable.")
    tenant_id = _normalize_text(workspace.get("tenant_id"))
    existing = await control_plane_repository.get_deployed_agent_by_id(
        deployed_agent_id,
        tenant_id=tenant_id,
        owner_workspace_id=resolved_workspace_id,
    )
    if not isinstance(existing, dict):
        return None
    try:
        validate_state_transition(existing.get("deployment_state"), "live")
    except ValueError as error:
        raise _http_conflict(str(error)) from error
    channels = _normalize_channels(existing.get("channels") or {})
    allowed_live_channels = _allowed_live_channels_for_workspace(workspace)
    requires_customer_live_channel = _has_customer_live_channel_configuration(
        channels,
        allowed_live_channels=allowed_live_channels,
    )
    if requires_customer_live_channel:
        readiness = await get_deployed_agent_telegram_readiness(
            current_user=current_user,
            owner_workspace_id=resolved_workspace_id,
            deployed_agent_id=deployed_agent_id,
        )
        if readiness.get("ready_for_live") is not True:
            blockers = _coerce_list(readiness.get("blockers"))
            next_action = _normalize_optional_text(readiness.get("next_action"))
            first = _coerce_dict(blockers[0]) if blockers else {}
            detail = _normalize_optional_text(first.get("message")) or "Telegram launch readiness is incomplete."
            if next_action:
                detail = f"{detail} {next_action}"
            raise _http_conflict(detail)
        try:
            _require_live_channel_configuration(
                channels,
                allowed_live_channels=allowed_live_channels,
            )
        except ValueError as error:
            raise _http_conflict(str(error)) from error
    next_record = {
        **existing,
        "deployment_state": "live",
    }
    try:
        deploy_config = _config_from_record(next_record)
    except ValueError as error:
        raise _http_conflict(str(error)) from error
    runtime_inventory = await runtime_attachment_service.list_workspace_runtime_attachments(
        tenant_id=tenant_id,
        workspace_id=resolved_workspace_id,
    )
    runtime_targets = await runtime_attachment_service.list_workspace_runtime_targets(
        tenant_id=tenant_id,
        workspace_id=resolved_workspace_id,
        inventory=runtime_inventory,
    )
    existing_deployed_agents = await control_plane_repository.list_deployed_agents_for_workspace(
        resolved_workspace_id,
        tenant_id=tenant_id,
    )
    _enforce_mode_capability_matrix(
        config=deploy_config,
        stage="deploy",
        runtime_targets=runtime_targets,
    )
    _enforce_runtime_eligibility(
        workspace=workspace,
        workspace_id=resolved_workspace_id,
        config=deploy_config,
        stage="deploy",
        runtime_targets=runtime_targets,
        runtime_inventory=runtime_inventory,
        explicit_mode=True,
        existing_deployed_agents=existing_deployed_agents,
        subject_deployed_agent_id=deployed_agent_id,
    )
    privacy_metadata = _metadata_from_config(
        deploy_config,
        existing_metadata=_coerce_dict(existing.get("metadata")),
    )
    try:
        privacy_metadata = _metadata_with_self_hosted_runtime_binding(
            metadata=privacy_metadata,
            config=deploy_config,
            runtime_inventory=runtime_inventory,
            workspace_id=resolved_workspace_id,
            require_binding=(
                _normalize_text(deploy_config.studio_agent_mode).lower()
                == deployed_agent_runtime_contract_service.STUDIO_AGENT_MODE_SELF_HOSTED
            ),
        )
    except (ValueError, runtime_attachment_service.RuntimeAttachmentSelectionError) as error:
        raise _http_conflict(str(error)) from error
    privacy_snapshot = _coerce_dict(privacy_metadata.get("privacy_contract_snapshot"))
    if _validate_privacy_contract_snapshot(privacy_snapshot):
        privacy_snapshot = {
            **privacy_snapshot,
            "accepted_at": _utc_now_iso(),
            "accepted_by_user_id": _normalize_optional_text((current_user or {}).get("user_id")) or "system",
        }
        privacy_metadata["privacy_contract_snapshot"] = privacy_snapshot
        privacy_metadata["privacy_contract_accepted_at"] = privacy_snapshot["accepted_at"]
        privacy_metadata["privacy_contract_accepted_by_user_id"] = privacy_snapshot["accepted_by_user_id"]
    computer_safety_snapshot = _coerce_dict(privacy_metadata.get("computer_safety_contract_snapshot"))
    readiness_blockers = _deploy_readiness_blockers(
        config=deploy_config,
        privacy_snapshot=privacy_snapshot,
        computer_safety_snapshot=computer_safety_snapshot,
    )
    if readiness_blockers:
        raise _http_conflict("Deployment readiness failed: " + "; ".join(readiness_blockers))
    next_record["metadata"] = privacy_metadata
    metadata_persisted = await control_plane_repository.update_deployed_agent(
        deployed_agent_id,
        tenant_id=tenant_id,
        owner_workspace_id=resolved_workspace_id,
        updates={"metadata": privacy_metadata},
    )
    if not isinstance(metadata_persisted, dict):
        raise _http_conflict("Failed to persist privacy contract snapshot before live deployment.")
    updated_install = await mirror_deployed_agent_to_backing_specialist(
        deployed_agent=next_record,
        updated_by_user_id=_normalize_optional_text((current_user or {}).get("user_id")),
        specialist_mode_override="customer_live" if requires_customer_live_channel else "owner_test",
        deployment_state_override="live",
    )
    try:
        validate_can_deploy(
            deployed_agent=next_record,
            backing_install=updated_install,
            allowed_live_channels=allowed_live_channels,
        )
    except ValueError as error:
        raise _http_conflict(str(error)) from error
    _enforce_deployed_agent_service_decision(
        operation="deploy",
        deployed_agent={**existing, "metadata": privacy_metadata},
        tenant_id=tenant_id,
        workspace_id=resolved_workspace_id,
        current_user=current_user,
        backing_install_present=isinstance(updated_install, dict),
        live_channel_configured=True,
        required_fields_complete=True,
        runtime_eligible=True,
    )
    next_state = _operational_state_from_record(
        {
            **existing,
            "deployment_state": "live",
            "last_deployed_at": _utc_now_iso(),
        }
    )
    persisted = await control_plane_repository.set_deployed_agent_state(
        deployed_agent_id,
        tenant_id=tenant_id,
        owner_workspace_id=resolved_workspace_id,
        deployment_state="live",
        last_deployed_at=next_state.last_deployed_at,
        operational_state=_serialized_operational_state(next_state),
    )
    if not isinstance(persisted, dict):
        return None
    await _append_deployed_agent_audit_event(
        tenant_id=tenant_id,
        workspace_id=resolved_workspace_id,
        deployed_agent=persisted,
        action="deployed_agent_deployed_live",
        title="Deployed agent is live",
        summary=f"{_normalize_text(persisted.get('name'), default='Deployed agent')} is now live.",
        status="live",
        payload={
            "previous_state": _normalize_deployment_state(existing.get("deployment_state")),
            "current_state": "live",
            "readiness_gate": "passed",
            "privacy_contract_valid": True,
            "computer_safety_contract_valid": bool(
                deploy_config.studio_agent_mode
                in {
                    deployed_agent_runtime_contract_service.STUDIO_AGENT_MODE_CLOUD_COMPUTER,
                    deployed_agent_runtime_contract_service.STUDIO_AGENT_MODE_MY_COMPUTER,
                }
            ),
        },
        actor_user_id=_normalize_optional_text((current_user or {}).get("user_id")),
    )
    await _append_full_access_runtime_review_event(
        tenant_id=tenant_id,
        workspace_id=resolved_workspace_id,
        deployed_agent={**persisted, "metadata": privacy_metadata},
        lifecycle_action="deployed_live",
        actor_user_id=_normalize_optional_text((current_user or {}).get("user_id")),
    )
    if _normalize_deployment_state(existing.get("deployment_state")) in {"paused", "suspended"}:
        await _append_deployed_agent_audit_event(
            tenant_id=tenant_id,
            workspace_id=resolved_workspace_id,
            deployed_agent=persisted,
            action="deployed_agent_resumed",
            title="Deployed agent resumed",
            summary="Deployment resumed to live state.",
            status="resumed",
            payload={
                "previous_state": _normalize_deployment_state(existing.get("deployment_state")),
                "current_state": "live",
            },
            actor_user_id=_normalize_optional_text((current_user or {}).get("user_id")),
        )
    return {
        **dict(project_deployed_agent(persisted, include_internal=True) or {}),
        "backing_install": updated_install,
    }


async def pause_deployed_agent(
    *,
    deployed_agent_id: str,
    current_user: Optional[Dict[str, Any]],
    owner_workspace_id: str,
) -> Optional[Dict[str, Any]]:
    resolved_workspace_id = require_deployed_agent_admin_access(
        current_user=current_user,
        workspace_id=owner_workspace_id,
    )
    workspace = await control_plane_repository.get_workspace_by_id(resolved_workspace_id)
    if not isinstance(workspace, dict):
        raise ValueError("Workspace is unavailable.")
    tenant_id = _normalize_text(workspace.get("tenant_id"))
    deployed_agent = await control_plane_repository.get_deployed_agent_by_id(
        deployed_agent_id,
        tenant_id=tenant_id,
        owner_workspace_id=resolved_workspace_id,
    )
    if not isinstance(deployed_agent, dict):
        return None
    decision = _enforce_deployed_agent_service_decision(
        operation="pause",
        deployed_agent=deployed_agent,
        tenant_id=tenant_id,
        workspace_id=resolved_workspace_id,
        current_user=current_user,
    )
    target_state = _deployed_agent_service_target_state(decision, default="paused")
    next_state = _operational_state_from_record(
        {
            **deployed_agent,
            "deployment_state": target_state,
            "last_paused_at": _utc_now_iso(),
        }
    )
    paused = await control_plane_repository.set_deployed_agent_state(
        deployed_agent_id,
        tenant_id=tenant_id,
        owner_workspace_id=resolved_workspace_id,
        deployment_state=target_state,
        last_paused_at=next_state.last_paused_at,
        operational_state=_serialized_operational_state(next_state),
    )
    backing_install = await agent_specialist_repository.get_workspace_specialist(
        _normalize_text(deployed_agent.get("backing_install_id")),
        tenant_id=tenant_id,
        workspace_id=resolved_workspace_id,
    )
    if isinstance(paused, dict):
        await _append_deployed_agent_audit_event(
            tenant_id=tenant_id,
            workspace_id=resolved_workspace_id,
            deployed_agent=paused,
            action="deployed_agent_paused",
            title="Deployed agent paused",
            summary=f"{_normalize_text(paused.get('name'), default='Deployed agent')} was paused.",
            status=target_state,
            payload={
                "previous_state": _normalize_deployment_state(deployed_agent.get("deployment_state")),
                "current_state": target_state,
            },
            actor_user_id=_normalize_optional_text((current_user or {}).get("user_id")),
        )
    return {
        **dict(project_deployed_agent(paused, include_internal=True) or {}),
        "backing_install": backing_install,
    }


async def kill_deployed_agent(
    *,
    deployed_agent_id: str,
    current_user: Optional[Dict[str, Any]],
    owner_workspace_id: str,
    reason: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    resolved_workspace_id = require_deployed_agent_admin_access(
        current_user=current_user,
        workspace_id=owner_workspace_id,
    )
    workspace = await control_plane_repository.get_workspace_by_id(resolved_workspace_id)
    if not isinstance(workspace, dict):
        raise ValueError("Workspace is unavailable.")
    tenant_id = _normalize_text(workspace.get("tenant_id"))
    deployed_agent = await control_plane_repository.get_deployed_agent_by_id(
        deployed_agent_id,
        tenant_id=tenant_id,
        owner_workspace_id=resolved_workspace_id,
    )
    if not isinstance(deployed_agent, dict):
        return None
    actor_user_id = _normalize_optional_text((current_user or {}).get("user_id"))
    decision = _enforce_deployed_agent_service_decision(
        operation="kill",
        deployed_agent=deployed_agent,
        tenant_id=tenant_id,
        workspace_id=resolved_workspace_id,
        current_user=current_user,
        approval_provided=True,
    )
    target_state = _deployed_agent_service_target_state(decision, default="suspended")
    stopped_run_ids = (
        _stop_deployed_agent_live_runs(
            deployed_agent=deployed_agent,
            reason="agent_kill_switch",
            stopped_by_user_id=actor_user_id,
        )
        if _deployed_agent_service_plan_flag(decision, "stop_active_runs", default=True)
        else []
    )
    metadata = _coerce_dict(deployed_agent.get("metadata"))
    now_iso = _utc_now_iso()
    if _deployed_agent_service_plan_flag(decision, "activate_kill_switch", default=True):
        metadata["kill_switch"] = {
            "active": True,
            "scope": "agent",
            "reason": _normalize_optional_text(reason) or "Owner kill switch activated.",
            "activated_at": now_iso,
            "activated_by_user_id": actor_user_id,
            "stopped_run_ids": stopped_run_ids,
        }
    next_state = _operational_state_from_record(
        {
            **deployed_agent,
            "deployment_state": target_state,
            "last_paused_at": now_iso
            if _deployed_agent_service_plan_flag(decision, "set_last_paused_at", default=True)
            else deployed_agent.get("last_paused_at"),
        }
    )
    killed = await control_plane_repository.update_deployed_agent(
        deployed_agent_id,
        tenant_id=tenant_id,
        owner_workspace_id=resolved_workspace_id,
        updates={
            "deployment_state": target_state,
            "last_paused_at": now_iso
            if _deployed_agent_service_plan_flag(decision, "set_last_paused_at", default=True)
            else deployed_agent.get("last_paused_at"),
            "metadata": metadata,
            "operational_state": _serialized_operational_state(next_state),
        },
    )
    if isinstance(killed, dict):
        await _append_deployed_agent_audit_event(
            tenant_id=tenant_id,
            workspace_id=resolved_workspace_id,
            deployed_agent=killed,
            action="deployed_agent_killed",
            title="Deployed agent kill switch activated",
            summary=f"{_normalize_text(killed.get('name'), default='Deployed agent')} was suspended by kill switch.",
            status=target_state,
            payload={
                "previous_state": _normalize_deployment_state(deployed_agent.get("deployment_state")),
                "current_state": target_state,
                "reason": _normalize_optional_text(_coerce_dict(metadata.get("kill_switch")).get("reason")),
                "stopped_run_ids": stopped_run_ids,
            },
            actor_user_id=actor_user_id,
        )
    return dict(project_deployed_agent(killed, include_internal=True) or {})


async def recover_deployed_agent(
    *,
    deployed_agent_id: str,
    current_user: Optional[Dict[str, Any]],
    owner_workspace_id: str,
) -> Optional[Dict[str, Any]]:
    resolved_workspace_id = require_deployed_agent_admin_access(
        current_user=current_user,
        workspace_id=owner_workspace_id,
    )
    workspace = await control_plane_repository.get_workspace_by_id(resolved_workspace_id)
    if not isinstance(workspace, dict):
        raise ValueError("Workspace is unavailable.")
    tenant_id = _normalize_text(workspace.get("tenant_id"))
    deployed_agent = await control_plane_repository.get_deployed_agent_by_id(
        deployed_agent_id,
        tenant_id=tenant_id,
        owner_workspace_id=resolved_workspace_id,
    )
    if not isinstance(deployed_agent, dict):
        return None
    decision = _enforce_deployed_agent_service_decision(
        operation="recover",
        deployed_agent=deployed_agent,
        tenant_id=tenant_id,
        workspace_id=resolved_workspace_id,
        current_user=current_user,
        approval_provided=True,
    )
    next_state = _deployed_agent_service_target_state(decision, default="ready_for_review")
    metadata = _coerce_dict(deployed_agent.get("metadata"))
    kill_switch = _coerce_dict(metadata.get("kill_switch"))
    if kill_switch and _deployed_agent_service_plan_flag(decision, "clear_kill_switch", default=True):
        kill_switch["active"] = False
        kill_switch["cleared_at"] = _utc_now_iso()
        kill_switch["cleared_by_user_id"] = _normalize_optional_text((current_user or {}).get("user_id"))
        metadata["kill_switch"] = kill_switch
    recovered_state = _operational_state_from_record({**deployed_agent, "deployment_state": next_state})
    recovered = await control_plane_repository.update_deployed_agent(
        deployed_agent_id,
        tenant_id=tenant_id,
        owner_workspace_id=resolved_workspace_id,
        updates={
            "deployment_state": next_state,
            "metadata": metadata,
            "operational_state": _serialized_operational_state(recovered_state),
        },
    )
    if isinstance(recovered, dict):
        await _append_deployed_agent_audit_event(
            tenant_id=tenant_id,
            workspace_id=resolved_workspace_id,
            deployed_agent=recovered,
            action="deployed_agent_recovered",
            title="Deployed agent recovered",
            summary="Kill switch was cleared and deployment returned to review.",
            status=next_state,
            payload={
                "previous_state": _normalize_deployment_state(deployed_agent.get("deployment_state")),
                "current_state": next_state,
            },
            actor_user_id=_normalize_optional_text((current_user or {}).get("user_id")),
        )
    return dict(project_deployed_agent(recovered, include_internal=True) or {})


async def archive_deployed_agent(
    *,
    deployed_agent_id: str,
    current_user: Optional[Dict[str, Any]],
    owner_workspace_id: str,
    reason: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    resolved_workspace_id = require_deployed_agent_admin_access(
        current_user=current_user,
        workspace_id=owner_workspace_id,
    )
    workspace = await control_plane_repository.get_workspace_by_id(resolved_workspace_id)
    if not isinstance(workspace, dict):
        raise ValueError("Workspace is unavailable.")
    tenant_id = _normalize_text(workspace.get("tenant_id"))
    deployed_agent = await control_plane_repository.get_deployed_agent_by_id(
        deployed_agent_id,
        tenant_id=tenant_id,
        owner_workspace_id=resolved_workspace_id,
    )
    if not isinstance(deployed_agent, dict):
        return None
    actor_user_id = _normalize_optional_text((current_user or {}).get("user_id"))
    decision = _enforce_deployed_agent_service_decision(
        operation="archive",
        deployed_agent=deployed_agent,
        tenant_id=tenant_id,
        workspace_id=resolved_workspace_id,
        current_user=current_user,
        approval_provided=True,
    )
    target_state = _deployed_agent_service_target_state(decision, default="archived")
    stopped_run_ids = (
        _stop_deployed_agent_live_runs(
            deployed_agent=deployed_agent,
            reason="agent_archived",
            stopped_by_user_id=actor_user_id,
        )
        if _deployed_agent_service_plan_flag(decision, "stop_active_runs", default=True)
        else []
    )
    metadata = _coerce_dict(deployed_agent.get("metadata"))
    metadata["archived_at"] = _utc_now_iso()
    metadata["archived_by_user_id"] = actor_user_id
    metadata["archive_reason"] = _normalize_optional_text(reason)
    next_state = _operational_state_from_record({**deployed_agent, "deployment_state": target_state})
    archived = await control_plane_repository.update_deployed_agent(
        deployed_agent_id,
        tenant_id=tenant_id,
        owner_workspace_id=resolved_workspace_id,
        updates={
            "deployment_state": target_state,
            "channels": (
                _disabled_channels(deployed_agent.get("channels"))
                if _deployed_agent_service_plan_flag(decision, "disable_channels", default=True)
                else deployed_agent.get("channels")
            ),
            "metadata": metadata,
            "operational_state": _serialized_operational_state(next_state),
        },
    )
    if isinstance(archived, dict):
        await _append_deployed_agent_audit_event(
            tenant_id=tenant_id,
            workspace_id=resolved_workspace_id,
            deployed_agent=archived,
            action="deployed_agent_archived",
            title="Deployed agent archived",
            summary=f"{_normalize_text(archived.get('name'), default='Deployed agent')} was archived.",
            status=target_state,
            payload={"stopped_run_ids": stopped_run_ids, "reason": _normalize_optional_text(reason)},
            actor_user_id=actor_user_id,
        )
    return dict(project_deployed_agent(archived, include_internal=True) or {})


async def apply_deployed_agent_recovery_action(
    *,
    deployed_agent_id: str,
    current_user: Optional[Dict[str, Any]],
    owner_workspace_id: str,
    action: str,
) -> Optional[Dict[str, Any]]:
    resolved_workspace_id = require_deployed_agent_admin_access(
        current_user=current_user,
        workspace_id=owner_workspace_id,
    )
    workspace = await control_plane_repository.get_workspace_by_id(resolved_workspace_id)
    if not isinstance(workspace, dict):
        raise ValueError("Workspace is unavailable.")
    tenant_id = _normalize_text(workspace.get("tenant_id"))
    deployed_agent = await control_plane_repository.get_deployed_agent_by_id(
        deployed_agent_id,
        tenant_id=tenant_id,
        owner_workspace_id=resolved_workspace_id,
    )
    if not isinstance(deployed_agent, dict):
        return None
    token = _normalize_text(action).lower().replace("-", "_")
    allowed_actions = {
        "revoke_connector_access",
        "revoke_local_computer_access",
        "delete_browser_session_cookies",
        "stop_background_schedules",
    }
    if token not in allowed_actions:
        raise _http_bad_request("Unsupported deployed-agent recovery action.")
    _enforce_deployed_agent_service_decision(
        operation="recovery_action",
        deployed_agent=deployed_agent,
        tenant_id=tenant_id,
        workspace_id=resolved_workspace_id,
        current_user=current_user,
        recovery_action=token,
        approval_provided=True,
    )
    metadata = _coerce_dict(deployed_agent.get("metadata"))
    recovery = _coerce_dict(metadata.get("recovery_controls"))
    now_iso = _utc_now_iso()
    actor_user_id = _normalize_optional_text((current_user or {}).get("user_id"))
    recovery[token] = {"applied_at": now_iso, "applied_by_user_id": actor_user_id}
    metadata["recovery_controls"] = recovery
    updates: Dict[str, Any] = {"metadata": metadata}
    if token == "revoke_connector_access":
        updates["channels"] = _disabled_channels(deployed_agent.get("channels"))
    updated = await control_plane_repository.update_deployed_agent(
        deployed_agent_id,
        tenant_id=tenant_id,
        owner_workspace_id=resolved_workspace_id,
        updates=updates,
    )
    if isinstance(updated, dict):
        await _append_deployed_agent_audit_event(
            tenant_id=tenant_id,
            workspace_id=resolved_workspace_id,
            deployed_agent=updated,
            action=f"deployed_agent_{token}",
            title="Deployed agent recovery control applied",
            summary=f"Recovery control applied: {token}.",
            status="applied",
            payload={"recovery_action": token},
            actor_user_id=actor_user_id,
        )
    return dict(project_deployed_agent(updated, include_internal=True) or {})


async def kill_deployed_agent_runtime_session(
    *,
    deployed_agent_id: str,
    session_id: str,
    current_user: Optional[Dict[str, Any]],
    owner_workspace_id: str,
) -> Dict[str, Any]:
    resolved_workspace_id = require_deployed_agent_admin_access(
        current_user=current_user,
        workspace_id=owner_workspace_id,
    )
    workspace = await control_plane_repository.get_workspace_by_id(resolved_workspace_id)
    if not isinstance(workspace, dict):
        raise ValueError("Workspace is unavailable.")
    tenant_id = _normalize_text(workspace.get("tenant_id"))
    deployed_agent = await control_plane_repository.get_deployed_agent_by_id(
        deployed_agent_id,
        tenant_id=tenant_id,
        owner_workspace_id=resolved_workspace_id,
    )
    if not isinstance(deployed_agent, dict):
        raise HTTPException(status_code=404, detail="Deployed agent not found.")
    token = _normalize_text(session_id)
    if not token:
        raise _http_bad_request("Runtime session id is required.")
    session_record = await session_service.get_session(token)
    session_metadata = _coerce_dict((session_record or {}).get("metadata"))
    runtime_session_binding = _normalize_text(session_metadata.get("runtime_session_binding")).lower()
    decision = _enforce_deployed_agent_service_decision(
        operation="runtime_session_kill",
        deployed_agent=deployed_agent,
        tenant_id=tenant_id,
        workspace_id=resolved_workspace_id,
        current_user=current_user,
        session_id=token,
        runtime_session_binding=runtime_session_binding,
        approval_provided=True,
    )
    if _deployed_agent_service_plan_flag(decision, "terminate_cloud_session"):
        await deployed_agent_virtual_runtime_service.terminate_bound_cloud_runtime_session(
            session_id=token,
            tenant_id=tenant_id,
            workspace_id=resolved_workspace_id,
        )
    if _deployed_agent_service_plan_flag(decision, "terminate_self_hosted_session"):
        await deployed_agent_virtual_runtime_service.terminate_bound_self_hosted_runtime_session(
            session_id=token,
            tenant_id=tenant_id,
            workspace_id=resolved_workspace_id,
        )
    if _deployed_agent_service_plan_flag(decision, "terminate_session_record", default=True):
        await session_service.terminate_session(token)
    await _append_deployed_agent_audit_event(
        tenant_id=tenant_id,
        workspace_id=resolved_workspace_id,
        deployed_agent=deployed_agent,
        action="deployed_agent_runtime_session_killed",
        title="Runtime session killed",
        summary="Runtime session was terminated by owner control.",
        status="killed",
        payload={"session_id": token},
        actor_user_id=_normalize_optional_text((current_user or {}).get("user_id")),
    )
    return {"ok": True, "deployed_agent_id": deployed_agent_id, "session_id": token, "status": "killed"}


async def emergency_stop_workspace_deployed_agents(
    *,
    current_user: Optional[Dict[str, Any]],
    owner_workspace_id: str,
    reason: Optional[str] = None,
) -> Dict[str, Any]:
    resolved_workspace_id = require_deployed_agent_admin_access(
        current_user=current_user,
        workspace_id=owner_workspace_id,
    )
    workspace = await control_plane_repository.get_workspace_by_id(resolved_workspace_id)
    if not isinstance(workspace, dict):
        raise ValueError("Workspace is unavailable.")
    tenant_id = _normalize_text(workspace.get("tenant_id"))
    rows = await control_plane_repository.list_deployed_agents_for_workspace(
        resolved_workspace_id,
        tenant_id=tenant_id,
    )
    actor_user_id = _normalize_optional_text((current_user or {}).get("user_id"))
    stopped_agents: List[str] = []
    stopped_run_ids: List[str] = []
    now_iso = _utc_now_iso()
    for deployed_agent in list(rows or []):
        if not isinstance(deployed_agent, dict):
            continue
        state = _normalize_deployment_state(deployed_agent.get("deployment_state"))
        if state == "archived":
            continue
        decision = _enforce_deployed_agent_service_decision(
            operation="emergency_stop",
            deployed_agent=deployed_agent,
            tenant_id=tenant_id,
            workspace_id=resolved_workspace_id,
            current_user=current_user,
            approval_provided=True,
        )
        mutation_plan = _coerce_dict(decision.get("mutation_plan")) if isinstance(decision, dict) else {}
        stop_reason = _normalize_optional_text(mutation_plan.get("stop_reason")) or "workspace_emergency_stop"
        target_state = _normalize_deployment_state(mutation_plan.get("target_state") or "suspended")
        stop_active_runs = bool(mutation_plan.get("stop_active_runs", True))
        activate_workspace_emergency_stop = bool(
            mutation_plan.get("activate_workspace_emergency_stop", True)
        )
        set_last_paused_at = bool(mutation_plan.get("set_last_paused_at", True))
        agent_run_ids: List[str] = []
        if stop_active_runs:
            agent_run_ids = _stop_deployed_agent_live_runs(
                deployed_agent=deployed_agent,
                reason=stop_reason,
                stopped_by_user_id=actor_user_id,
            )
        metadata = _coerce_dict(deployed_agent.get("metadata"))
        if activate_workspace_emergency_stop:
            metadata["workspace_emergency_stop"] = {
                "active": True,
                "reason": _normalize_optional_text(reason) or "Workspace emergency stop activated.",
                "activated_at": now_iso,
                "activated_by_user_id": actor_user_id,
            }
        next_state = _operational_state_from_record(
            {
                **deployed_agent,
                "deployment_state": target_state,
                "last_paused_at": now_iso if set_last_paused_at else deployed_agent.get("last_paused_at"),
            }
        )
        updates = {
            "deployment_state": target_state,
            "metadata": metadata,
            "operational_state": _serialized_operational_state(next_state),
        }
        if set_last_paused_at:
            updates["last_paused_at"] = now_iso
        updated = await control_plane_repository.update_deployed_agent(
            _normalize_text(deployed_agent.get("id")),
            tenant_id=tenant_id,
            owner_workspace_id=resolved_workspace_id,
            updates=updates,
        )
        if isinstance(updated, dict):
            stopped_agents.append(_normalize_text(updated.get("id")))
            stopped_run_ids.extend(agent_run_ids)
            await _append_deployed_agent_audit_event(
                tenant_id=tenant_id,
                workspace_id=resolved_workspace_id,
                deployed_agent=updated,
                action="workspace_deployed_agents_emergency_stopped",
                title="Workspace emergency stop applied",
                summary="Workspace emergency stop suspended this deployed agent.",
                status="suspended",
                payload={"reason": _normalize_optional_text(reason), "stopped_run_ids": agent_run_ids},
                actor_user_id=actor_user_id,
            )
    return {
        "ok": True,
        "workspace_id": resolved_workspace_id,
        "status": "emergency_stopped",
        "stopped_agent_ids": stopped_agents,
        "stopped_run_ids": stopped_run_ids,
        "count": len(stopped_agents),
    }


async def export_deployed_agent_audit_logs(
    *,
    deployed_agent_id: str,
    current_user: Optional[Dict[str, Any]],
    owner_workspace_id: str,
    limit: int = 500,
) -> Optional[Dict[str, Any]]:
    resolved_workspace_id = require_deployed_agent_admin_access(
        current_user=current_user,
        workspace_id=owner_workspace_id,
    )
    workspace = await control_plane_repository.get_workspace_by_id(resolved_workspace_id)
    if not isinstance(workspace, dict):
        raise _http_bad_request("Workspace is unavailable.")
    tenant_id = _normalize_text(workspace.get("tenant_id"))
    deployed_agent = await control_plane_repository.get_deployed_agent_by_id(
        deployed_agent_id,
        tenant_id=tenant_id,
        owner_workspace_id=resolved_workspace_id,
    )
    if not isinstance(deployed_agent, dict):
        return None
    owner_approved = _deployed_agent_service_owner_access(current_user, resolved_workspace_id)
    _enforce_deployed_agent_service_decision(
        operation="audit_export",
        deployed_agent=deployed_agent,
        tenant_id=tenant_id,
        workspace_id=resolved_workspace_id,
        current_user=current_user,
        approval_provided=owner_approved,
        audit_export_approval=owner_approved,
    )
    _enforce_deployed_agent_data_decision(
        "audit_export",
        tenant_id=tenant_id,
        workspace_id=resolved_workspace_id,
        deployed_agent_id=_normalize_text(deployed_agent.get("id")),
        current_user=current_user,
        audit_export_approved=owner_approved,
    )
    activity = await list_deployed_agent_activity(
        deployed_agent_id=deployed_agent_id,
        current_user=current_user,
        owner_workspace_id=resolved_workspace_id,
        limit=limit,
        offset=0,
    )
    if not isinstance(activity, dict):
        return None
    return {
        "schema_version": 1,
        "exported_at": _utc_now_iso(),
        "deployed_agent_id": _normalize_text(deployed_agent_id),
        "workspace_id": _normalize_text(owner_workspace_id),
        "audit_log": activity,
    }


async def list_deployed_agent_conversations(
    *,
    deployed_agent_id: str,
    current_user: Optional[Dict[str, Any]],
    owner_workspace_id: str,
    limit: int = 50,
    offset: int = 0,
) -> Optional[Dict[str, Any]]:
    resolved_workspace_id = require_deployed_agent_admin_access(
        current_user=current_user,
        workspace_id=owner_workspace_id,
    )
    workspace = await control_plane_repository.get_workspace_by_id(resolved_workspace_id)
    if not isinstance(workspace, dict):
        raise _http_bad_request("Workspace is unavailable.")
    tenant_id = _normalize_text(workspace.get("tenant_id"))
    deployed_agent = await control_plane_repository.get_deployed_agent_by_id(
        deployed_agent_id,
        tenant_id=tenant_id,
        owner_workspace_id=resolved_workspace_id,
    )
    if not isinstance(deployed_agent, dict):
        return None
    _enforce_deployed_agent_service_decision(
        "conversation_list",
        deployed_agent=deployed_agent,
        tenant_id=tenant_id,
        workspace_id=resolved_workspace_id,
        current_user=current_user,
    )
    safe_limit, safe_offset = _normalize_pagination(limit, offset)
    session_rows = await control_plane_repository.list_deployed_agent_conversation_sessions(
        tenant_id=tenant_id,
        workspace_id=resolved_workspace_id,
        deployed_agent_id=_normalize_text(deployed_agent.get("id")),
        backing_install_id=_normalize_text(deployed_agent.get("backing_install_id")),
        limit=safe_limit + 1,
        offset=safe_offset,
    )
    has_more = len(session_rows) > safe_limit
    trimmed_rows = session_rows[:safe_limit]
    items: List[Dict[str, Any]] = []
    for row in trimmed_rows:
        latest_run_id = _normalize_optional_text(row.get("latest_run_id"))
        activity_rows: List[Dict[str, Any]] = []
        if _normalize_optional_text(row.get("session_id")):
            activity_rows = await _load_conversation_activity(
                tenant_id=tenant_id,
                workspace_id=resolved_workspace_id,
                backing_install_id=_normalize_text(deployed_agent.get("backing_install_id")),
                session_id=_normalize_text(row.get("session_id")),
                thread_id=_normalize_optional_text(row.get("thread_id")),
                run_ids=[latest_run_id] if latest_run_id else [],
            )
        items.append(
            {
                "session_id": row.get("session_id"),
                "channel": row.get("channel"),
                "last_message": _compact_text(row.get("last_message"), limit=160),
                "last_message_at": row.get("last_message_at"),
                "customer": _actor_summary(row.get("customer_actor")),
                "customer_actor": _coerce_dict(row.get("customer_actor")),
                "thread_id": row.get("thread_id"),
                "latest_run_id": latest_run_id,
                "escalation_state": _derive_escalation_state(activity_rows),
                "outcome": _derive_outcome(activity_rows=activity_rows, run_snapshots={}),
            }
        )
    return {
        "deployed_agent_id": _normalize_text(deployed_agent.get("id")),
        "items": items,
        "offset": safe_offset,
        "limit": safe_limit,
        "has_more": has_more,
    }


async def list_deployed_agent_memory_entries(
    *,
    deployed_agent_id: str,
    current_user: Optional[Dict[str, Any]],
    owner_workspace_id: str,
    limit: int = 50,
    offset: int = 0,
) -> Optional[Dict[str, Any]]:
    resolved_workspace_id = require_deployed_agent_admin_access(
        current_user=current_user,
        workspace_id=owner_workspace_id,
    )
    workspace = await control_plane_repository.get_workspace_by_id(resolved_workspace_id)
    if not isinstance(workspace, dict):
        raise _http_bad_request("Workspace is unavailable.")
    tenant_id = _normalize_text(workspace.get("tenant_id"))
    deployed_agent = await control_plane_repository.get_deployed_agent_by_id(
        deployed_agent_id,
        tenant_id=tenant_id,
        owner_workspace_id=resolved_workspace_id,
    )
    if not isinstance(deployed_agent, dict):
        return None
    _enforce_deployed_agent_service_decision(
        "memory_list",
        deployed_agent=deployed_agent,
        tenant_id=tenant_id,
        workspace_id=resolved_workspace_id,
        current_user=current_user,
    )
    safe_limit, safe_offset = _normalize_pagination(limit, offset)
    memory_rows = await control_plane_repository.list_deployed_agent_conversation_memory(
        tenant_id=tenant_id,
        workspace_id=resolved_workspace_id,
        deployed_agent_id=_normalize_text(deployed_agent.get("id")),
        limit=safe_limit + 1,
        offset=safe_offset,
    )
    has_more = len(memory_rows) > safe_limit
    trimmed_rows = memory_rows[:safe_limit]
    items = [
        {
            "id": row.get("id"),
            "channel": row.get("channel_key"),
            "external_user_id": row.get("external_user_id"),
            "session_id": row.get("session_key"),
            "summary_text": _compact_text(row.get("summary_text"), limit=280) or "",
            "recent_message_count": row.get("recent_message_count"),
            "source_message_count": row.get("source_message_count"),
            "updated_at": row.get("updated_at"),
        }
        for row in trimmed_rows
        if isinstance(row, dict)
    ]
    return {
        "deployed_agent_id": _normalize_text(deployed_agent.get("id")),
        "items": items,
        "offset": safe_offset,
        "limit": safe_limit,
        "has_more": has_more,
    }


async def list_deployed_agent_activity(
    *,
    deployed_agent_id: str,
    current_user: Optional[Dict[str, Any]],
    owner_workspace_id: str,
    limit: int = 50,
    offset: int = 0,
) -> Optional[Dict[str, Any]]:
    resolved_workspace_id = require_deployed_agent_admin_access(
        current_user=current_user,
        workspace_id=owner_workspace_id,
    )
    workspace = await control_plane_repository.get_workspace_by_id(resolved_workspace_id)
    if not isinstance(workspace, dict):
        raise _http_bad_request("Workspace is unavailable.")
    tenant_id = _normalize_text(workspace.get("tenant_id"))
    deployed_agent = await control_plane_repository.get_deployed_agent_by_id(
        deployed_agent_id,
        tenant_id=tenant_id,
        owner_workspace_id=resolved_workspace_id,
    )
    if not isinstance(deployed_agent, dict):
        return None
    _enforce_deployed_agent_service_decision(
        "activity_list",
        deployed_agent=deployed_agent,
        tenant_id=tenant_id,
        workspace_id=resolved_workspace_id,
        current_user=current_user,
    )
    safe_limit, safe_offset = _normalize_pagination(limit, offset)
    fetch_limit = min(500, max((safe_limit + safe_offset + 40), (safe_limit * 4)))
    rows = await control_plane_repository.list_activity_ledger_events(
        tenant_id=tenant_id,
        workspace_id=resolved_workspace_id,
        detail_levels=["feed_summary", "timeline_detail", "audit_reference"],
        limit=fetch_limit,
    )
    filtered_rows = [
        dict(row)
        for row in rows
        if isinstance(row, dict)
        and _activity_row_matches_deployed_agent(
            row=row,
            deployed_agent_id=_normalize_text(deployed_agent.get("id")),
            backing_install_id=_normalize_optional_text(deployed_agent.get("backing_install_id")),
        )
    ]
    total = len(filtered_rows)
    has_more = total > (safe_offset + safe_limit)
    paged_rows = filtered_rows[safe_offset : safe_offset + safe_limit]
    items = [_deployed_agent_activity_item(row) for row in paged_rows]
    by_kind: Dict[str, int] = {}
    for item in items:
        token = _normalize_text(item.get("kind"))
        if not token:
            continue
        by_kind[token] = int(by_kind.get(token) or 0) + 1
    return {
        "deployed_agent_id": _normalize_text(deployed_agent.get("id")),
        "items": items,
        "offset": safe_offset,
        "limit": safe_limit,
        "count": len(items),
        "total": total,
        "has_more": has_more,
        "summary": {
            "review_required_count": sum(1 for item in items if bool(item.get("review_required"))),
            "by_kind": by_kind,
        },
    }


async def get_deployed_agent_conversation_detail(
    *,
    deployed_agent_id: str,
    session_id: str,
    current_user: Optional[Dict[str, Any]],
    owner_workspace_id: str,
) -> Optional[Dict[str, Any]]:
    resolved_workspace_id = require_deployed_agent_admin_access(
        current_user=current_user,
        workspace_id=owner_workspace_id,
    )
    workspace = await control_plane_repository.get_workspace_by_id(resolved_workspace_id)
    if not isinstance(workspace, dict):
        raise _http_bad_request("Workspace is unavailable.")
    tenant_id = _normalize_text(workspace.get("tenant_id"))
    deployed_agent = await control_plane_repository.get_deployed_agent_by_id(
        deployed_agent_id,
        tenant_id=tenant_id,
        owner_workspace_id=resolved_workspace_id,
    )
    if not isinstance(deployed_agent, dict):
        return None
    _enforce_deployed_agent_service_decision(
        "conversation_detail",
        deployed_agent=deployed_agent,
        tenant_id=tenant_id,
        workspace_id=resolved_workspace_id,
        current_user=current_user,
        session_id=session_id,
    )
    channel_events = await control_plane_repository.list_deployed_agent_conversation_events(
        tenant_id=tenant_id,
        workspace_id=resolved_workspace_id,
        deployed_agent_id=_normalize_text(deployed_agent.get("id")),
        backing_install_id=_normalize_text(deployed_agent.get("backing_install_id")),
        session_id=session_id,
        limit=500,
    )
    if not channel_events:
        return None
    ordered_events = sorted(
        channel_events,
        key=lambda item: (_timestamp_token(item.get("created_at")), str(item.get("id") or "")),
    )
    run_ids: List[str] = []
    for event in ordered_events:
        run_id = _normalize_optional_text(event.get("run_id"))
        if run_id and run_id not in run_ids:
            run_ids.append(run_id)
    thread_id = next(
        (
            _normalize_optional_text(event.get("thread_id"))
            for event in reversed(ordered_events)
            if _normalize_optional_text(event.get("thread_id"))
        ),
        None,
    )
    activity_rows = await _load_conversation_activity(
        tenant_id=tenant_id,
        workspace_id=resolved_workspace_id,
        backing_install_id=_normalize_text(deployed_agent.get("backing_install_id")),
        session_id=_normalize_text(session_id),
        thread_id=thread_id,
        run_ids=run_ids,
    )
    run_snapshots: Dict[str, Dict[str, Any]] = {}
    for run_id in run_ids:
        snapshot = await _get_run_snapshot(run_id)
        if isinstance(snapshot, dict) and isinstance(snapshot.get("payload"), dict):
            run_snapshots[run_id] = dict(snapshot.get("payload") or {})
    message_entries = [_message_entry_from_channel_event(item) for item in ordered_events]
    run_step_entries: List[Dict[str, Any]] = []
    seen_run_step_ids: set[str] = set()
    run_steps_per_run: Dict[str, int] = {}
    for row in activity_rows:
        entry = _run_step_entry_from_activity(row)
        if not isinstance(entry, dict):
            continue
        entry_id = str(entry.get("id") or "").strip()
        if entry_id and entry_id in seen_run_step_ids:
            continue
        if entry_id:
            seen_run_step_ids.add(entry_id)
        run_id = _normalize_optional_text(entry.get("run_id")) or ""
        if run_id:
            run_steps_per_run[run_id] = int(run_steps_per_run.get(run_id, 0)) + 1
        run_step_entries.append(entry)
    for run_id, snapshot in run_snapshots.items():
        snapshot_entries = _run_step_entries_from_run(run_id, snapshot)
        if run_steps_per_run.get(run_id):
            snapshot_entries = [
                entry
                for entry in snapshot_entries
                if _normalize_text(entry.get("event_class")).lower() != "run_status"
            ]
        for entry in snapshot_entries:
            entry_id = str(entry.get("id") or "").strip()
            if entry_id and entry_id in seen_run_step_ids:
                continue
            if entry_id:
                seen_run_step_ids.add(entry_id)
            run_step_entries.append(entry)
    tool_call_entries: List[Dict[str, Any]] = []
    seen_tool_ids: set[str] = set()
    for row in activity_rows:
        entry = _tool_call_entry_from_activity(row)
        if not isinstance(entry, dict):
            continue
        entry_id = str(entry.get("id") or "").strip()
        if entry_id and entry_id not in seen_tool_ids:
            seen_tool_ids.add(entry_id)
            tool_call_entries.append(entry)
    for run_id, snapshot in run_snapshots.items():
        entry = _tool_call_entry_from_run(run_id, snapshot)
        if not isinstance(entry, dict):
            continue
        entry_id = str(entry.get("id") or "").strip()
        if entry_id and entry_id not in seen_tool_ids:
            seen_tool_ids.add(entry_id)
            tool_call_entries.append(entry)
    approval_entries: List[Dict[str, Any]] = []
    escalation_entries: List[Dict[str, Any]] = []
    approval_cache: Dict[str, Dict[str, Any]] = {}
    for row in activity_rows:
        if _is_approval_activity(row):
            approval_payload = _coerce_dict(row.get("payload"))
            approval_metadata = _coerce_dict(row.get("metadata"))
            approval_id = _approval_id_from_payload(approval_payload) or _approval_id_from_payload(approval_metadata)
            approval_record = None
            if approval_id:
                approval_record = approval_cache.get(approval_id)
                if approval_record is None:
                    fetched = await run_state_repository.get_approval_record(approval_id)
                    approval_record = dict(fetched or {}) if isinstance(fetched, dict) else {}
                    approval_cache[approval_id] = approval_record
            approval_entries.append(
                _approval_entry_from_activity(
                    row,
                    approval_record=approval_record,
                )
            )
        if _is_escalation_activity(row):
            escalation_entries.append(_escalation_entry_from_activity(row))
    transcript_entries = sorted(
        [
            *message_entries,
            *run_step_entries,
            *tool_call_entries,
            *approval_entries,
            *escalation_entries,
        ],
        key=_entry_sort_key,
    )
    return {
        "deployed_agent_id": _normalize_text(deployed_agent.get("id")),
        "session_id": _normalize_text(session_id),
        "channel": _normalize_optional_text(ordered_events[-1].get("channel_key")) or _normalize_optional_text(ordered_events[0].get("channel_key")),
        "thread_id": thread_id,
        "run_ids": run_ids,
        "messages": message_entries,
        "run_steps": sorted(run_step_entries, key=_entry_sort_key),
        "tool_calls": sorted(tool_call_entries, key=_entry_sort_key),
        "approval_events": sorted(approval_entries, key=_entry_sort_key),
        "escalation_events": sorted(escalation_entries, key=_entry_sort_key),
        "entries": transcript_entries,
        "outcome": _derive_outcome(activity_rows=activity_rows, run_snapshots=run_snapshots),
        "customer": _actor_summary(
            next(
                (
                    event.get("actor")
                    for event in ordered_events
                    if _normalize_text(event.get("direction")).lower() == "inbound"
                ),
                {},
            )
        ),
    }


async def delete_deployed_agent_external_user_data(
    *,
    deployed_agent_id: str,
    external_user_id: str,
    channel_key: str,
    current_user: Optional[Dict[str, Any]],
    owner_workspace_id: str,
    note: Optional[str] = None,
    session_id: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    resolved_workspace_id = require_deployed_agent_admin_access(
        current_user=current_user,
        workspace_id=owner_workspace_id,
    )
    workspace = await control_plane_repository.get_workspace_by_id(resolved_workspace_id)
    if not isinstance(workspace, dict):
        raise _http_bad_request("Workspace is unavailable.")
    tenant_id = _normalize_text(workspace.get("tenant_id"))
    deployed_agent = await control_plane_repository.get_deployed_agent_by_id(
        deployed_agent_id,
        tenant_id=tenant_id,
        owner_workspace_id=resolved_workspace_id,
    )
    if not isinstance(deployed_agent, dict):
        return None
    resolved_channel_key = _normalize_text(channel_key).lower()
    if not resolved_channel_key:
        raise _http_bad_request("channel is required.")
    resolved_external_user_id = _normalize_text(external_user_id)
    if not resolved_external_user_id:
        raise _http_bad_request("external_user_id is required.")
    _enforce_deployed_agent_service_decision(
        operation="external_user_delete",
        deployed_agent=deployed_agent,
        tenant_id=tenant_id,
        workspace_id=resolved_workspace_id,
        current_user=current_user,
        external_user_id=resolved_external_user_id,
        session_id=_normalize_text(session_id) or None,
        privacy_delete_verified=True,
    )
    _enforce_deployed_agent_data_decision(
        "delete_external_user_data",
        tenant_id=tenant_id,
        workspace_id=resolved_workspace_id,
        deployed_agent_id=_normalize_text(deployed_agent.get("id")),
        current_user=current_user,
        external_user_id=resolved_external_user_id,
        channel_key=resolved_channel_key,
    )
    purge_result = await external_user_privacy_service.get_external_user_privacy_service().purge_deployed_agent_external_user_data(
        tenant_id=tenant_id,
        workspace_id=resolved_workspace_id,
        deployed_agent_id=_normalize_text(deployed_agent.get("id")),
        channel_key=resolved_channel_key,
        external_user_id=resolved_external_user_id,
        actor_user_id=_normalize_text(_coerce_dict(current_user).get("user_id")) or None,
        note=_normalize_text(note) or None,
        metadata={
            "session_id": _normalize_text(session_id) or None,
            "deployed_agent_name": _normalize_text(deployed_agent.get("name")) or None,
        },
    )
    terminated_session_ids = {
        token
        for token in (
            _normalize_text(session_id),
            _normalize_text(_coerce_dict(purge_result.get("request")).get("session_key")),
        )
        if token
    }
    for token in terminated_session_ids:
        await session_service.terminate_session(token)
    return {
        "deployed_agent_id": _normalize_text(deployed_agent.get("id")),
        "channel": resolved_channel_key,
        "external_user_id": resolved_external_user_id,
        "deleted_counts": _coerce_dict(purge_result.get("deleted_counts")),
        "request": _coerce_dict(purge_result.get("request")),
        "audit": _coerce_dict(purge_result.get("audit")),
    }
