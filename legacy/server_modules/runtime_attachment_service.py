from __future__ import annotations

import copy
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from server_modules import (
    agent_registry_repository,
    auth,
    control_plane_repository,
    credit_ledger_contract,
    entitlements_service,
    execution_sandbox_service,
    execution_mode_policy,
    gateway_inventory_service,
    gateway_state_repository,
    hybrid_policy_service,
    memory_service,
    run_state_repository,
    rust_runtime_kernel_client,
)


SUPPORTED_DEPLOYMENT_MODES = ("cloud_only", "local_only", "hybrid", "self_hosted_business")
SUPPORTED_ATTACHMENT_KINDS = ("managed_cloud", "cloud_computer", "local_companion", "self_hosted_business_node")
SUPPORTED_RUNTIME_MODES = {"hosted_secure", "local_secure", "privileged_device"}
SUPPORTED_RUNTIME_TARGET_IDS = ("cloud_default", "sage_cloud_computer", "local_companion", "self_host_runtime")
CANONICAL_RUNTIME_TARGET_IDS = (
    "cloud_default",
    "user_device_gateway",
    "empyralis_cloud_computer",
    "self_hosted_node",
)
CANONICAL_RUNTIME_TARGET_BY_LEGACY: dict[str, str] = {
    "cloud_default": "cloud_default",
    "local_companion": "user_device_gateway",
    "sage_cloud_computer": "empyralis_cloud_computer",
    "self_host_runtime": "self_hosted_node",
}
LEGACY_RUNTIME_TARGET_BY_CANONICAL: dict[str, str] = {
    canonical: legacy
    for legacy, canonical in CANONICAL_RUNTIME_TARGET_BY_LEGACY.items()
}
RUNTIME_TARGET_ALIAS_TO_LEGACY: dict[str, str] = {
    **{target_id: target_id for target_id in SUPPORTED_RUNTIME_TARGET_IDS},
    **LEGACY_RUNTIME_TARGET_BY_CANONICAL,
    "user_device": "local_companion",
    "gateway": "local_companion",
    "empyralis_gateway": "local_companion",
    "cloud_computer": "sage_cloud_computer",
    "cloud_desktop": "sage_cloud_computer",
    "self_hosted_runtime": "self_host_runtime",
    "self_hosted_business_node": "self_host_runtime",
}
RUNTIME_TARGET_HARDWARE_EDGE: dict[str, str] = {
    "cloud_default": "managed_cloud",
    "local_companion": "empyralis_gateway",
    "sage_cloud_computer": "empyralis_cloud_computer",
    "self_host_runtime": "self_hosted_node",
}
PUBLIC_RUNTIME_TARGET_LABEL_BY_CANONICAL: dict[str, str] = {
    "cloud_default": "Cloud",
    "user_device_gateway": "This Device",
    "empyralis_cloud_computer": "Cloud Computer",
    "self_hosted_node": "Server/VPS",
}
CLOUD_COMPUTER_RUNTIME_CLASSES = {"cloud_computer", "cloud_desktop", "cloud_sandbox", "hosted_cloud_computer"}
SELF_HOSTED_NODE_KINDS = {"mac_mini", "mac", "linux_server", "docker_host"}
SELF_HOSTED_NODE_STATUSES = {"pending", "online", "offline", "unhealthy", "revoked"}
RUNTIME_USAGE_SURFACES = ("sage", "studio", "mini_app")
RUNTIME_SENSITIVE_ACTIONS = (
    "payments",
    "login",
    "file_delete",
    "send_message",
    "purchase",
    "external_post",
)
_RUNTIME_ATTACHMENTS_CACHE: Dict[str, Dict[str, Any]] = {}
_RUNTIME_TARGETS_CACHE: Dict[str, Dict[str, Any]] = {}
_RUNTIME_CACHE_LIMIT = 128

_RUNTIME_ATTACHMENT_EXPECTED_NEXT_ACTIONS: dict[str, str] = {
    "normalize_target": "normalize_runtime_target_id",
    "build_targets": "build_workspace_runtime_targets",
    "select_attachment": "select_runtime_attachment",
    "self_hosted_gate": "ensure_self_hosted_node_gate",
    "local_companion_gate": "select_local_companion_attachment",
    "usage_credit_event": "build_runtime_usage_credit_event",
}

TRUST_MODEL_MAP: dict[str, dict[str, Any]] = {
    "hosted_secure": {
        "trust_tier": "hosted_high",
        "execution_boundary": "sandboxed_hosted_compute",
        "host_filesystem_access": "denied_by_default",
        "local_private_memory_access": "cloud_safe_summaries_only",
        "approval_mode": "policy_bound",
    },
    "cloud_computer_secure": {
        "trust_tier": "hosted_cloud_computer",
        "execution_boundary": "metered_cloud_desktop_sandbox",
        "host_filesystem_access": "cloud_workspace_volume_only",
        "local_private_memory_access": "denied_without_gateway",
        "approval_mode": "policy_bound_plus_destructive_confirmation",
    },
    "local_secure": {
        "trust_tier": "paired_local_scoped",
        "execution_boundary": "scoped_local_runtime",
        "host_filesystem_access": "approved_folders_only",
        "local_private_memory_access": "allowed_locally",
        "approval_mode": "policy_bound",
    },
    "privileged_device": {
        "trust_tier": "paired_local_privileged",
        "execution_boundary": "approved_device_runtime",
        "host_filesystem_access": "approval_gated_device_access",
        "local_private_memory_access": "allowed_locally",
        "approval_mode": "explicit_owner_approval",
    },
    "self_hosted_business_node": {
        "trust_tier": "enterprise_self_hosted",
        "execution_boundary": "customer_controlled_secure_node",
        "host_filesystem_access": "customer_policy_defined",
        "local_private_memory_access": "customer_policy_defined",
        "approval_mode": "workspace_policy_bound",
    },
}

RUNTIME_TARGET_DEFINITIONS: dict[str, dict[str, Any]] = {
    "cloud_default": {
        "label": "Cloud",
        "attachment_kind": "managed_cloud",
        "execution_target": "cloud",
        "connection_mode": "platform_cloud",
        "product_default": True,
        "description": "Cloud-hosted execution for the workspace. This remains the default path.",
    },
    "sage_cloud_computer": {
        "label": "Cloud Computer",
        "attachment_kind": "cloud_computer",
        "execution_target": "cloud_computer",
        "connection_mode": "platform_metered_cloud_computer",
        "product_default": False,
        "description": "Optional metered hosted computer for browser, terminal, and file work in an isolated workspace.",
    },
    "local_companion": {
        "label": "This Device",
        "attachment_kind": "local_companion",
        "execution_target": "local_companion",
        "connection_mode": "platform_relay",
        "product_default": False,
        "description": "Paired user device execution routed through the same workspace identity and policy model.",
    },
    "self_host_runtime": {
        "label": "Server/VPS",
        "attachment_kind": "self_hosted_business_node",
        "execution_target": "cloud",
        "connection_mode": "workspace_hosted",
        "product_default": False,
        "description": "Customer-hosted secure execution that stays under the same account and workspace contract.",
    },
}


class RuntimeAttachmentSelectionError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        reason: str = "runtime_attachment_selection_failed",
        enforcement_state: Optional[Dict[str, Any]] = None,
    ) -> None:
        super().__init__(message)
        self.reason = str(reason or "runtime_attachment_selection_failed").strip() or "runtime_attachment_selection_failed"
        self.message = str(message or "Runtime attachment selection failed.").strip() or "Runtime attachment selection failed."
        self.enforcement_state = dict(enforcement_state or {})


def _coerce_dict(value: Any) -> Dict[str, Any]:
    return dict(value or {}) if isinstance(value, dict) else {}


def _list_strings(value: Any) -> List[str]:
    out: List[str] = []
    for item in list(value or []):
        token = str(item or "").strip()
        if token:
            out.append(token)
    return out


def _enforce_rust_runtime_attachment_decision(
    *,
    operation: str,
    tenant_id: str,
    workspace_id: str,
    attachment: Optional[Dict[str, Any]] = None,
    runtime_target: Optional[str] = None,
    deployment_mode: Optional[str] = None,
    attachment_count: Optional[int] = None,
    required_capabilities: Optional[List[str]] = None,
    required_connectors: Optional[List[str]] = None,
    requested_machine_target: Optional[str] = None,
    surface: Optional[str] = None,
    session_id: Optional[str] = None,
    active_seconds: Optional[int] = None,
    billable_seconds: Optional[int] = None,
    raise_value_error: bool = False,
) -> Dict[str, Any]:
    attachment_payload = _coerce_dict(attachment)
    required_capability_tokens = _list_strings(required_capabilities)
    required_connector_tokens = _list_strings(required_connectors)
    requested_machine_token = str(requested_machine_target or "").strip()
    runtime_kind = str(attachment_payload.get("attachment_kind") or attachment_payload.get("kind") or "").strip()
    runtime_id = str(
        attachment_payload.get("attachment_id")
        or attachment_payload.get("runtime_node_id")
        or attachment_payload.get("runtime_id")
        or attachment_payload.get("machine_id")
        or ""
    ).strip()
    payload = {
        "operation": operation,
        "tenant_id": str(tenant_id or "").strip(),
        "workspace_id": str(workspace_id or "").strip(),
        "runtime_target": str(runtime_target or "").strip(),
        "deployment_mode": str(deployment_mode or "").strip(),
        "attachment_count": int(attachment_count or 0),
        "attachment_id": runtime_id,
        "runtime_id": str(attachment_payload.get("runtime_id") or "").strip(),
        "machine_id": str(attachment_payload.get("machine_id") or "").strip(),
        "runtime_node_id": str(attachment_payload.get("runtime_node_id") or "").strip(),
        "runtime_profile_id": str(attachment_payload.get("runtime_profile_id") or "").strip(),
        "attachment_kind": runtime_kind,
        "kind": runtime_kind,
        "status": str(attachment_payload.get("status") or "").strip(),
        "control_state": str(attachment_payload.get("control_state") or "").strip(),
        "online": bool(attachment_payload.get("online")),
        "healthy": bool(attachment_payload.get("healthy")),
        "capability_match": (not required_capability_tokens or _attachment_capability_match(attachment_payload, required_capability_tokens)),
        "capability_readiness_match": (
            not required_capability_tokens
            or _local_companion_ready_capability_match(attachment_payload, required_capability_tokens)
        ),
        "connector_match": (not required_connector_tokens or _attachment_connector_match(attachment_payload, required_connector_tokens)),
        "requested_machine_target": requested_machine_token,
        "machine_target_match": (not requested_machine_token or _matches_requested_machine(attachment_payload, requested_machine_token)),
        "allowed_agent_ids_present": bool(_list_strings(attachment_payload.get("allowed_agent_ids"))),
        "agent_allowed": True,
        "max_concurrent_sessions": int(attachment_payload.get("max_concurrent_sessions") or 0),
        "active_sessions": int(attachment_payload.get("active_sessions") or attachment_payload.get("active_session_count") or 0),
        "existing_session_reuse": bool(attachment_payload.get("existing_session_reuse")),
        "surface": str(surface or "").strip(),
        "session_id": str(session_id or "").strip(),
        "active_seconds": int(active_seconds or 0),
        "billable_seconds": int(billable_seconds or 0),
    }
    try:
        decision = rust_runtime_kernel_client.run_runtime_kernel_enforced("runtime-attachment-decision", payload)
        expected_next_action = _RUNTIME_ATTACHMENT_EXPECTED_NEXT_ACTIONS.get(str(operation or "").strip())
        next_action = str(decision.get("next_action") or "").strip()
        if expected_next_action and next_action != expected_next_action:
            message = f"Rust runtime-attachment gate blocked {operation}: unexpected_next_action:{next_action or 'missing'}"
            if raise_value_error:
                raise ValueError(message)
            raise RuntimeAttachmentSelectionError(
                message,
                reason="unexpected_next_action",
                enforcement_state=dict(decision),
            )
        return decision
    except rust_runtime_kernel_client.RustKernelDecisionError as exc:
        if raise_value_error:
            raise ValueError(f"Rust runtime-attachment gate blocked {operation}: {exc.reason}") from exc
        raise RuntimeAttachmentSelectionError(
            f"Rust runtime-attachment gate blocked {operation}: {exc.reason}",
            reason=exc.reason,
            enforcement_state=dict(exc.decision),
        ) from exc


def _runtime_target_token(value: Any) -> str:
    return str(value or "").strip().lower().replace("-", "_").replace(" ", "_")


def normalize_runtime_target_id(value: Any) -> str:
    token = _runtime_target_token(value)
    return RUNTIME_TARGET_ALIAS_TO_LEGACY.get(token, token)


def _rust_normalized_runtime_target_id(
    *,
    tenant_id: str,
    workspace_id: str,
    value: Any,
    raise_value_error: bool = False,
) -> str:
    normalized_target = normalize_runtime_target_id(value)
    decision = _enforce_rust_runtime_attachment_decision(
        operation="normalize_target",
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        runtime_target=normalized_target,
        raise_value_error=raise_value_error,
    )
    target = str(decision.get("runtime_target") or "").strip()
    return target or normalized_target


def canonical_runtime_target_id(value: Any) -> str:
    legacy_target = normalize_runtime_target_id(value)
    return CANONICAL_RUNTIME_TARGET_BY_LEGACY.get(legacy_target, legacy_target)


def public_runtime_target_label(value: Any) -> str:
    canonical_target = canonical_runtime_target_id(value)
    if canonical_target in PUBLIC_RUNTIME_TARGET_LABEL_BY_CANONICAL:
        return PUBLIC_RUNTIME_TARGET_LABEL_BY_CANONICAL[canonical_target]
    legacy_target = normalize_runtime_target_id(value)
    definition = RUNTIME_TARGET_DEFINITIONS.get(legacy_target)
    if definition:
        return str(definition.get("label") or legacy_target).strip() or legacy_target
    return str(value or "Runtime").strip() or "Runtime"


def _normalize_node_kind(value: Any, *, runtime_class: str, runtime_type: str, label: str) -> str:
    token = str(value or "").strip().lower().replace("-", "_").replace(" ", "_")
    if token in SELF_HOSTED_NODE_KINDS:
        return token
    class_token = str(runtime_class or "").strip().lower()
    type_token = str(runtime_type or "").strip().lower()
    label_token = str(label or "").strip().lower()
    if "docker" in class_token or "docker" in type_token or "kubernetes" in class_token or "kubernetes" in type_token:
        return "docker_host"
    if "mac" in class_token or "mac" in type_token or "mac mini" in label_token:
        return "mac_mini" if "mini" in label_token else "mac"
    return "linux_server"


def _stable_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=True, separators=(",", ":"), default=str)


def _clone_payload(value: Dict[str, Any]) -> Dict[str, Any]:
    return copy.deepcopy(value)


def _utc_iso(value: Any) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, datetime):
        parsed = value
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    token = str(value or "").strip()
    return token or None


def _float_or_raise(value: Any, *, field_name: str) -> float:
    try:
        parsed = float(value)
    except Exception as exc:
        raise ValueError(f"{field_name} must be numeric.") from exc
    if parsed < 0:
        raise ValueError(f"{field_name} must be non-negative.")
    return round(parsed, 6)


def _runtime_credit_item_type(runtime_target: str) -> str:
    runtime_target = normalize_runtime_target_id(runtime_target)
    if runtime_target == "sage_cloud_computer":
        return "virtual_desktop_minutes"
    if runtime_target == "cloud_default":
        return "virtual_code_sandbox_minutes"
    if runtime_target == "local_companion":
        return "virtual_desktop_minutes"
    if runtime_target == "self_host_runtime":
        return "virtual_code_sandbox_minutes"
    return "virtual_code_sandbox_minutes"


def build_runtime_usage_credit_event(
    *,
    tenant_id: str,
    workspace_id: str,
    surface: str,
    runtime_target: str,
    session_id: str,
    started_at: Any,
    ended_at: Any,
    active_seconds: Any,
    billable_seconds: Any,
    estimated_cost_usd: Any = 0.0,
    thread_id: Optional[str] = None,
    run_id: Optional[str] = None,
    deployed_agent_id: Optional[str] = None,
    app_id: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    tenant_token = str(tenant_id or "").strip()
    workspace_token = str(workspace_id or "").strip()
    if not tenant_token:
        raise ValueError("tenant_id is required for runtime credit events.")
    if not workspace_token:
        raise ValueError("workspace_id is required for runtime credit events.")
    surface_token = str(surface or "").strip().lower()
    if surface_token not in RUNTIME_USAGE_SURFACES:
        raise ValueError("surface must be sage, studio, or mini_app.")
    target_token = _rust_normalized_runtime_target_id(
        tenant_id=tenant_token,
        workspace_id=workspace_token,
        value=runtime_target,
        raise_value_error=True,
    )
    if target_token not in SUPPORTED_RUNTIME_TARGET_IDS:
        raise ValueError("runtime_target is unsupported.")
    canonical_target_token = canonical_runtime_target_id(target_token)
    session_token = str(session_id or "").strip()
    if not session_token:
        raise ValueError("session_id is required for runtime credit events.")
    started_token = _utc_iso(started_at)
    ended_token = _utc_iso(ended_at)
    if not started_token or not ended_token:
        raise ValueError("started_at and ended_at are required for runtime credit events.")

    active = _float_or_raise(active_seconds, field_name="active_seconds")
    billable = _float_or_raise(billable_seconds, field_name="billable_seconds")
    if active > 0 and billable > active:
        raise ValueError("billable_seconds cannot exceed active_seconds.")
    cost = _float_or_raise(estimated_cost_usd, field_name="estimated_cost_usd")
    _enforce_rust_runtime_attachment_decision(
        operation="usage_credit_event",
        tenant_id=tenant_token,
        workspace_id=workspace_token,
        runtime_target=target_token,
        surface=surface_token,
        session_id=session_token,
        active_seconds=int(active),
        billable_seconds=int(billable),
        raise_value_error=True,
    )
    billable_minutes = round(billable / 60.0, 6)
    target_definition = dict(RUNTIME_TARGET_DEFINITIONS.get(target_token) or {})
    line_item = credit_ledger_contract.build_credit_ledger_line_item(
        metadata={
            **_coerce_dict(metadata),
            "runtime_target": target_token,
            "canonical_runtime_target": canonical_target_token,
            "runtime_fabric_target": canonical_target_token,
            "runtime_type": target_definition.get("attachment_kind") or target_token,
            "credit_item_type": _runtime_credit_item_type(target_token),
            "billing_source": "empyralis_credits"
            if target_token in {"cloud_default", "sage_cloud_computer"}
            else "workspace_runtime_policy",
        },
        runtime_minutes=billable_minutes,
    )
    billing_source = str(line_item.get("billing_source") or "").strip()
    payer = "platform_credits" if billing_source == "empyralis_credits" else "local"
    unified_ledger_event = credit_ledger_contract.build_unified_credit_ledger_event(
        surface=surface_token,
        source_surface=_coerce_dict(metadata).get("source_surface") or surface_token,
        payer=payer,
        credit_type=line_item.get("credit_type") or "computer_runtime",
        runtime_target=target_token,
        workspace_id=workspace_token,
        thread_id=thread_id,
        run_id=run_id,
        agent_id=deployed_agent_id,
        app_id=app_id,
        provider_usage={
            "session_id": session_token,
            "active_seconds": active,
            "billable_seconds": billable,
            "billable_minutes": billable_minutes,
        },
        platform_cost_usd=cost if payer == "platform_credits" else 0,
        provider_reported_cost=cost,
        provider_reported_currency="USD",
        credits_debited=line_item.get("quantity") if payer == "platform_credits" else 0,
        estimation_mode="runtime_metered",
        created_at=ended_token,
    )
    event_metadata = {
        **_coerce_dict(metadata),
        "runtime_target": target_token,
        "canonical_runtime_target": canonical_target_token,
        "runtime_fabric_target": canonical_target_token,
        "runtime_type": target_definition.get("attachment_kind") or target_token,
        "runtime_connection_mode": target_definition.get("connection_mode"),
        "requires_explicit_selection": bool(target_definition.get("product_default") is False),
        "sensitive_actions_require_confirmation": list(RUNTIME_SENSITIVE_ACTIONS),
        "credit_ledger_line_item": line_item,
        "unified_credit_ledger_event": unified_ledger_event,
    }
    return {
        "tenant_id": tenant_token,
        "workspace_id": workspace_token,
        "surface": surface_token,
        "runtime_target": target_token,
        "canonical_runtime_target": canonical_target_token,
        "runtime_fabric_target": canonical_target_token,
        "runtime_type": target_definition.get("attachment_kind") or target_token,
        "session_id": session_token,
        "started_at": started_token,
        "ended_at": ended_token,
        "active_seconds": active,
        "billable_seconds": billable,
        "billable_minutes": billable_minutes,
        "estimated_cost_usd": cost,
        "credit_type": line_item.get("credit_type"),
        "credit_item_type": line_item.get("credit_item_type"),
        "credit_quantity": line_item.get("quantity"),
        "credit_quantity_unit": line_item.get("quantity_unit"),
        "billing_source": line_item.get("billing_source"),
        "thread_id": str(thread_id or "").strip() or None,
        "run_id": str(run_id or "").strip() or None,
        "deployed_agent_id": str(deployed_agent_id or "").strip() or None,
        "app_id": str(app_id or "").strip() or None,
        "metadata": event_metadata,
    }


def _cache_store(cache: Dict[str, Dict[str, Any]], key: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    if len(cache) >= _RUNTIME_CACHE_LIMIT and key not in cache:
        cache.clear()
    cache[key] = _clone_payload(payload)
    return _clone_payload(payload)


def _runtime_profile_snapshot_value(profile: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "id": str(profile.get("id") or "").strip(),
        "slug": str(profile.get("slug") or "").strip(),
        "status": str(profile.get("status") or "").strip(),
        "runtime_class": str(profile.get("runtime_class") or "").strip(),
        "runtime_id": str(profile.get("runtime_id") or "").strip(),
        "machine_id": str(profile.get("machine_id") or "").strip(),
        "supported_capabilities": list(profile.get("supported_capabilities") or []),
        "updated_at": profile.get("updated_at"),
        "last_seen_at": profile.get("last_seen_at"),
    }


def _fleet_worker_snapshot_value(worker: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "worker_id": str(worker.get("worker_id") or worker.get("runtime_id") or "").strip(),
        "runtime_id": str(worker.get("runtime_id") or "").strip(),
        "machine_id": str(worker.get("machine_id") or "").strip(),
        "runtime_type": str(worker.get("runtime_type") or "").strip(),
        "status": str(worker.get("status") or "").strip(),
        "control_state": str(worker.get("control_state") or "").strip(),
        "online": bool(worker.get("online")),
        "last_seen_at": worker.get("last_seen_at") or worker.get("last_heartbeat_at"),
        "capabilities": list(worker.get("capabilities") or []),
        "execution_targets": list(worker.get("execution_targets") or []),
    }


def _runtime_inventory_snapshot_version(
    *,
    tenant_id: str,
    workspace_id: str,
    runtime_profiles: List[Dict[str, Any]],
    fleet_workers: List[Dict[str, Any]],
    gateway_registrations: Optional[List[Dict[str, Any]]] = None,
) -> str:
    return _stable_json(
        {
            "tenant_id": str(tenant_id or "").strip(),
            "workspace_id": str(workspace_id or "").strip(),
            "runtime_profiles": [
                _runtime_profile_snapshot_value(profile)
                for profile in runtime_profiles
                if isinstance(profile, dict)
            ],
            "fleet_workers": [
                _fleet_worker_snapshot_value(worker)
                for worker in fleet_workers
                if isinstance(worker, dict)
            ],
            "gateway_registrations": [
                {
                    "gateway_id": str(item.get("gateway_id") or "").strip(),
                    "device_id": str(item.get("device_id") or "").strip(),
                    "status": str(item.get("status") or "").strip(),
                    "device_trust_state": str(item.get("device_trust_state") or "").strip(),
                    "updated_at": item.get("updated_at"),
                    "last_heartbeat_at": item.get("last_heartbeat_at"),
                }
                for item in list(gateway_registrations or [])
                if isinstance(item, dict)
            ],
        }
    )


def _inventory_snapshot_version(inventory: Dict[str, Any]) -> str:
    if str(inventory.get("_snapshot_version") or "").strip():
        return str(inventory.get("_snapshot_version") or "").strip()
    return _stable_json(
        {
            "tenant_id": str(inventory.get("tenant_id") or "").strip(),
            "workspace_id": str(inventory.get("workspace_id") or "").strip(),
            "deployment_mode": str(inventory.get("deployment_mode") or "").strip(),
            "attachments": [
                {
                    "attachment_id": str(item.get("attachment_id") or "").strip(),
                    "attachment_kind": str(item.get("attachment_kind") or "").strip(),
                    "runtime_profile_id": str(item.get("runtime_profile_id") or "").strip(),
                    "runtime_id": str(item.get("runtime_id") or "").strip(),
                    "machine_id": str(item.get("machine_id") or "").strip(),
                    "status": str(item.get("status") or "").strip(),
                    "control_state": str(item.get("control_state") or "").strip(),
                    "online": bool(item.get("online")),
                    "healthy": bool(item.get("healthy")),
                }
                for item in list(inventory.get("attachments") or [])
                if isinstance(item, dict)
            ],
        }
    )


def _attachment_capability_match(attachment: Dict[str, Any], required_capabilities: List[str]) -> bool:
    required = {str(item or "").strip().lower() for item in list(required_capabilities or []) if str(item or "").strip()}
    if not required:
        return True
    available = {str(item or "").strip().lower() for item in list(attachment.get("capabilities") or []) if str(item or "").strip()}
    if not available:
        return str(attachment.get("attachment_kind") or "").strip() in {"managed_cloud", "self_hosted_business_node"}
    return required.issubset(available)


def _local_companion_ready_capability_match(attachment: Dict[str, Any], required_capabilities: List[str]) -> bool:
    required = [str(item or "").strip() for item in list(required_capabilities or []) if str(item or "").strip()]
    if not required:
        return True
    metadata = {
        "capability_readiness": attachment.get("capability_readiness"),
    }
    for capability_id in required:
        if not gateway_inventory_service.capability_ready_from_any_metadata(capability_id, metadata):
            return False
    return True


def _attachment_connector_match(attachment: Dict[str, Any], required_connectors: List[str]) -> bool:
    required = {str(item or "").strip().lower() for item in list(required_connectors or []) if str(item or "").strip()}
    if not required:
        return True
    available = {str(item or "").strip().lower() for item in list(attachment.get("connectors") or []) if str(item or "").strip()}
    if not available:
        return str(attachment.get("attachment_kind") or "").strip() in {"managed_cloud", "self_hosted_business_node"}
    return required.issubset(available)


def _preferred_attachment_kind_rank(attachment: Dict[str, Any], preferred_kinds: List[str]) -> int:
    attachment_kind = str(attachment.get("attachment_kind") or "").strip()
    try:
        return list(preferred_kinds).index(attachment_kind)
    except ValueError:
        return len(list(preferred_kinds)) + 1


def _normalize_runtime_mode(value: Any) -> str:
    token = str(value or "").strip().lower()
    return token if token in SUPPORTED_RUNTIME_MODES else "hosted_secure"


def _normalize_runtime_class(value: Any) -> str:
    token = str(value or "").strip().lower()
    if token in {
        "cloud_worker",
        "cloud_computer",
        "cloud_desktop",
        "cloud_sandbox",
        "hosted_cloud_computer",
        "desktop_companion",
        "mobile_runtime",
        "self_hosted_business_node",
        "self_hosted_worker",
        "enterprise_node",
    }:
        return token
    return token or "cloud_worker"


def _attachment_kind_for_profile(runtime_profile: Dict[str, Any], worker: Optional[Dict[str, Any]] = None) -> str:
    payload = _coerce_dict(worker)
    runtime_class = _normalize_runtime_class(runtime_profile.get("runtime_class") or payload.get("runtime_class"))
    runtime_type = str(payload.get("runtime_type") or runtime_profile.get("runtime_type") or "").strip().lower()
    execution_targets = {
        str(item or "").strip().lower()
        for item in list(payload.get("execution_targets") or runtime_profile.get("execution_targets") or [])
        if str(item or "").strip()
    }
    capabilities = {
        str(item or "").strip().lower()
        for item in list(payload.get("capabilities") or runtime_profile.get("supported_capabilities") or [])
        if str(item or "").strip()
    }
    if runtime_class in {"self_hosted_business_node", "self_hosted_worker", "enterprise_node"}:
        return "self_hosted_business_node"
    if runtime_class in CLOUD_COMPUTER_RUNTIME_CLASSES:
        return "cloud_computer"
    if runtime_type in {"cloud_computer", "cloud_desktop", "cloud_sandbox"}:
        return "cloud_computer"
    if "cloud_computer" in execution_targets or "cloud.computer" in capabilities:
        return "cloud_computer"
    if runtime_class in {"desktop_companion", "mobile_runtime"}:
        return "local_companion"
    if runtime_type in {"local", "local_companion"}:
        return "local_companion"
    if "local" in execution_targets or "local.worker" in capabilities:
        return "local_companion"
    return "managed_cloud"


def _attachment_support(runtime_kind: str) -> List[str]:
    if runtime_kind == "managed_cloud":
        return ["hosted_secure"]
    if runtime_kind == "local_companion":
        return ["local_secure", "privileged_device"]
    return ["hosted_secure"]


def _attachment_trust(runtime_kind: str) -> Dict[str, Any]:
    if runtime_kind == "managed_cloud":
        return dict(TRUST_MODEL_MAP["hosted_secure"])
    if runtime_kind == "cloud_computer":
        return dict(TRUST_MODEL_MAP["cloud_computer_secure"])
    if runtime_kind == "local_companion":
        return {
            "local_secure": dict(TRUST_MODEL_MAP["local_secure"]),
            "privileged_device": dict(TRUST_MODEL_MAP["privileged_device"]),
        }
    return dict(TRUST_MODEL_MAP["self_hosted_business_node"])


def _parse_utc_ts(value: Any) -> Optional[datetime]:
    token = str(value or "").strip()
    if not token:
        return None
    if token.endswith("Z"):
        token = f"{token[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(token)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _worker_online_window_seconds(lease_seconds: Optional[int] = None) -> int:
    return max(20, int(lease_seconds or 30) * 2)


def _infer_attachment_online(payload: Dict[str, Any], control_state: str, status: str) -> bool:
    if bool(payload.get("online")):
        return True
    if control_state != "active" or status in {"offline", "failed", "stopped", "revoked"}:
        return False
    seen_at = _parse_utc_ts(payload.get("last_seen_at") or payload.get("last_heartbeat_at"))
    if seen_at is None:
        return False
    delta = (datetime.now(timezone.utc) - seen_at).total_seconds()
    lease_seconds = int(payload.get("lease_seconds") or 30)
    return delta <= _worker_online_window_seconds(lease_seconds)


def _attachment_health(worker: Optional[Dict[str, Any]], runtime_kind: str) -> Dict[str, Any]:
    if runtime_kind == "managed_cloud":
        return {"online": True, "healthy": True, "control_state": "active", "status": "ready"}
    payload = _coerce_dict(worker)
    control_state = str(payload.get("control_state") or "active").strip().lower() or "active"
    status = str(payload.get("status") or "offline").strip().lower() or "offline"
    online = _infer_attachment_online(payload, control_state, status)
    healthy = bool(online and control_state == "active" and status not in {"offline", "failed"})
    return {
        "online": online,
        "healthy": healthy,
        "control_state": control_state,
        "status": status,
    }


def _attachment_lifecycle(runtime_kind: str, worker: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    if runtime_kind == "managed_cloud":
        return {
            "pairable": False,
            "registered": True,
            "health_tracked": True,
            "revocable": True,
            "detachable": False,
            "migratable": True,
        }
    payload = _coerce_dict(worker)
    if runtime_kind == "cloud_computer":
        return {
            "pairable": False,
            "registered": bool(payload),
            "health_tracked": True,
            "revocable": True,
            "detachable": True,
            "migratable": True,
            "metered": True,
            "persistent_volume_opt_in": True,
        }
    return {
        "pairable": runtime_kind == "local_companion",
        "registered": bool(payload),
        "health_tracked": True,
        "revocable": True,
        "detachable": True,
        "migratable": True,
    }


def _self_hosted_node_status(payload: Dict[str, Any], health: Dict[str, Any]) -> str:
    status_token = str(payload.get("status") or "").strip().lower()
    control_token = str(payload.get("control_state") or "").strip().lower()
    if control_token in {"revoked", "blocked"} or status_token == "revoked":
        return "revoked"
    if status_token in {"pending", "registering", "enrolling", "pairing"}:
        return "pending"
    if bool(health.get("online")) and bool(health.get("healthy")):
        return "online"
    if bool(health.get("online")) and not bool(health.get("healthy")):
        return "unhealthy"
    return "offline"


def _gateway_target_selection_state(registration: Dict[str, Any]) -> Dict[str, Any]:
    from server_modules import gateway_protocol_service, gateway_registry_service

    registration_status = str(registration.get("status") or "").strip().lower()
    trust_state = str(registration.get("device_trust_state") or "").strip().lower()
    gateway_id = str(registration.get("gateway_id") or "").strip()
    try:
        public_payload = gateway_registry_service.gateway_registration_public_payload(registration)
    except Exception:
        public_payload = {}
    live_connection = bool(gateway_id and gateway_protocol_service.gateway_connection_is_live(gateway_id))
    heartbeat_fresh = bool(public_payload.get("heartbeat_fresh"))
    connection_status = str(public_payload.get("connection_status") or "").strip().lower()
    reported_health = str(public_payload.get("reported_health_state") or "").strip().lower()
    reason = ""
    if registration_status != "active":
        reason = "gateway_registration_inactive"
    elif trust_state == "revoked":
        reason = "gateway_device_revoked"
    elif not live_connection:
        reason = "gateway_offline"
    elif not heartbeat_fresh:
        reason = "gateway_heartbeat_stale"
    elif connection_status != "online":
        reason = "gateway_unhealthy"
    elif reported_health in {"blocked", "offline", "unhealthy", "error"}:
        reason = "gateway_unhealthy"
    selectable = reason == ""
    return {
        "selectable": selectable,
        "reason": reason,
        "live_connection": live_connection,
        "heartbeat_fresh": heartbeat_fresh,
        "connection_status": connection_status or "offline",
        "reported_health_state": reported_health or None,
        "heartbeat_age_seconds": public_payload.get("heartbeat_age_seconds"),
        "latest_session_status": public_payload.get("latest_session_status"),
    }


def _gateway_attachment_from_registration(registration: Dict[str, Any]) -> Dict[str, Any]:
    registration_metadata = dict(registration.get("metadata") or {})
    registration_status = str(registration.get("status") or "active").strip().lower() or "active"
    trust_state = str(registration.get("device_trust_state") or "verified").strip().lower() or "verified"
    target_selection = _gateway_target_selection_state(registration)
    capability_readiness = dict(registration_metadata.get("capability_readiness") or {})
    capabilities = sorted(
        {
            str(item or "").strip()
            for item in (
                list(registration.get("capabilities") or [])
                + list(capability_readiness.get("requested") or [])
                + list(capability_readiness.get("ready") or [])
            )
            if str(item or "").strip()
        }
    )
    online = bool(target_selection.get("selectable"))
    revoked = registration_status == "revoked" or trust_state == "revoked"
    healthy = bool(online and not revoked)
    attachment = {
        "attachment_id": f"local_companion:{str(registration.get('gateway_id') or '').strip()}",
        "attachment_kind": "local_companion",
        "tenant_id": str(registration.get("tenant_id") or "").strip(),
        "workspace_id": str(registration.get("workspace_id") or "").strip(),
        "runtime_class": "desktop_companion",
        "runtime_profile_id": None,
        "runtime_profile_slug": None,
        "runtime_profile_label": None,
        "runtime_id": str(registration.get("gateway_id") or "").strip() or None,
        "machine_id": str(registration.get("device_id") or "").strip() or None,
        "runtime_type": "local_companion",
        "label": str(registration.get("display_name") or registration.get("device_id") or registration.get("gateway_id") or "").strip()
        or "Paired Device",
        "online": online and not revoked,
        "healthy": healthy,
        "control_state": "revoked" if revoked else "active",
        "status": "revoked"
        if revoked
        else str(target_selection.get("connection_status") or registration_metadata.get("health_state") or registration_metadata.get("status") or "offline").strip().lower()
        or "offline",
        "status_reason": str(target_selection.get("reason") or "").strip() or None,
        "capabilities": capabilities,
        "capability_readiness": capability_readiness,
        "service_inventory": gateway_inventory_service.inventory_from_metadata(registration_metadata),
        "native_runtime": gateway_inventory_service.native_runtime_from_metadata(registration_metadata),
        "target_selection": target_selection,
        "connectors": [],
        "execution_targets": ["local_companion"],
        "supports_runtime_modes": _attachment_support("local_companion"),
        "trust_model": {
            **_attachment_trust("local_companion"),
            "gateway_device_trust_state": trust_state,
            "gateway_identity_bound": True,
        },
        "privacy_posture": {
            "local_private_memory_supported": True,
            "cloud_safe_summary_bridge": True,
            "cloud_sync_required": False,
        },
        "lifecycle": {
            **_attachment_lifecycle("local_companion", registration_metadata),
            "gateway_registered": True,
            "gateway_revocable": True,
        },
        "current_run_id": None,
        "last_seen_at": registration.get("last_seen_at") or registration.get("last_heartbeat_at"),
        "instance_id": str(registration.get("gateway_id") or "").strip() or None,
        "note": str(registration_metadata.get("note") or "").strip() or None,
        "gateway_identity": {
            "gateway_id": str(registration.get("gateway_id") or "").strip() or None,
            "device_id": str(registration.get("device_id") or "").strip() or None,
            "user_id": str(registration.get("user_id") or "").strip() or None,
            "tenant_id": str(registration.get("tenant_id") or "").strip() or None,
            "workspace_id": str(registration.get("workspace_id") or "").strip() or None,
            "device_trust_state": trust_state,
            "status": registration_status,
        },
    }
    return attachment


def _merge_gateway_registration_into_attachment(
    attachment: Dict[str, Any],
    registration: Dict[str, Any],
) -> Dict[str, Any]:
    merged = dict(attachment)
    gateway_attachment = _gateway_attachment_from_registration(registration)
    merged["gateway_identity"] = dict(gateway_attachment.get("gateway_identity") or {})
    merged["capabilities"] = sorted(
        {
            str(item or "").strip()
            for item in list(attachment.get("capabilities") or []) + list(gateway_attachment.get("capabilities") or [])
            if str(item or "").strip()
        }
    )
    trust_state = str((gateway_attachment.get("gateway_identity") or {}).get("device_trust_state") or "verified").strip().lower()
    if trust_state == "revoked":
        merged["online"] = False
        merged["healthy"] = False
        merged["control_state"] = "revoked"
        merged["status"] = "revoked"
    else:
        merged["online"] = bool(attachment.get("online")) or bool(gateway_attachment.get("online"))
        merged["healthy"] = bool(attachment.get("healthy")) or bool(gateway_attachment.get("healthy"))
        if bool(gateway_attachment.get("target_selection")):
            merged["online"] = bool(gateway_attachment.get("online"))
            merged["healthy"] = bool(gateway_attachment.get("healthy"))
            merged["status"] = gateway_attachment.get("status")
            merged["status_reason"] = gateway_attachment.get("status_reason")
            merged["target_selection"] = dict(gateway_attachment.get("target_selection") or {})
            merged["capability_readiness"] = dict(gateway_attachment.get("capability_readiness") or {})
            merged["service_inventory"] = list(gateway_attachment.get("service_inventory") or [])
            merged["native_runtime"] = dict(gateway_attachment.get("native_runtime") or {})
    merged["machine_id"] = str(attachment.get("machine_id") or gateway_attachment.get("machine_id") or "").strip() or None
    merged["runtime_id"] = str(attachment.get("runtime_id") or gateway_attachment.get("runtime_id") or "").strip() or None
    merged["trust_model"] = {
        **dict(attachment.get("trust_model") or {}),
        "gateway_device_trust_state": str((gateway_attachment.get("gateway_identity") or {}).get("device_trust_state") or "").strip()
        or None,
        "gateway_identity_bound": True,
    }
    return merged


def _deployment_mode(attachments: Iterable[Dict[str, Any]]) -> str:
    items = [dict(item) for item in attachments if isinstance(item, dict)]
    has_cloud = any(str(item.get("attachment_kind") or "").strip() == "managed_cloud" for item in items)
    has_cloud_computer = any(str(item.get("attachment_kind") or "").strip() == "cloud_computer" for item in items)
    has_local = any(str(item.get("attachment_kind") or "").strip() == "local_companion" for item in items)
    has_self_hosted = any(str(item.get("attachment_kind") or "").strip() == "self_hosted_business_node" for item in items)
    if (has_cloud or has_cloud_computer) and (has_local or has_self_hosted):
        return "hybrid"
    if has_self_hosted:
        return "self_hosted_business"
    if has_local:
        return "local_only"
    return "cloud_only"


def _attachments_for_kind(attachments: Iterable[Dict[str, Any]], attachment_kind: str) -> List[Dict[str, Any]]:
    token = str(attachment_kind or "").strip()
    return [
        dict(item)
        for item in attachments
        if isinstance(item, dict) and str(item.get("attachment_kind") or "").strip() == token
    ]


def _runtime_target_status(attachments: List[Dict[str, Any]]) -> str:
    if not attachments:
        return "unavailable"
    online = any(bool(item.get("online")) for item in attachments)
    healthy = any(bool(item.get("healthy")) for item in attachments)
    if healthy and online:
        return "live"
    if online:
        return "degraded"
    return "offline"


def _default_runtime_target_id(*, deployment_mode: str, attachments: List[Dict[str, Any]]) -> str:
    mode = str(deployment_mode or "").strip().lower()
    if _attachments_for_kind(attachments, "managed_cloud"):
        return "cloud_default"
    if mode == "self_hosted_business" and _attachments_for_kind(attachments, "self_hosted_business_node"):
        return "self_host_runtime"
    if mode == "local_only" and _attachments_for_kind(attachments, "local_companion"):
        return "local_companion"
    if _attachments_for_kind(attachments, "self_hosted_business_node"):
        return "self_host_runtime"
    if _attachments_for_kind(attachments, "local_companion"):
        return "local_companion"
    return "cloud_default"


def build_workspace_runtime_targets(
    *,
    tenant_id: str,
    workspace_id: str,
    inventory: Dict[str, Any],
) -> Dict[str, Any]:
    attachments = [dict(item) for item in list(inventory.get("attachments") or []) if isinstance(item, dict)]
    deployment_mode = str(inventory.get("deployment_mode") or _deployment_mode(attachments)).strip() or "cloud_only"
    _enforce_rust_runtime_attachment_decision(
        operation="build_targets",
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        deployment_mode=deployment_mode,
        attachment_count=len(attachments),
    )
    default_target_id = _default_runtime_target_id(
        deployment_mode=deployment_mode,
        attachments=attachments,
    )
    targets: List[Dict[str, Any]] = []

    for target_id in SUPPORTED_RUNTIME_TARGET_IDS:
        definition = dict(RUNTIME_TARGET_DEFINITIONS[target_id])
        canonical_target_id = canonical_runtime_target_id(target_id)
        public_label = public_runtime_target_label(target_id)
        matching = _attachments_for_kind(attachments, str(definition.get("attachment_kind") or ""))
        supports_runtime_modes = sorted(
            {
                str(mode or "").strip()
                for item in matching
                for mode in list(item.get("supports_runtime_modes") or [])
                if str(mode or "").strip()
            }
        )
        execution_modes = execution_mode_policy.mode_contract_for_target(target_id)
        if not matching:
            execution_modes = [
                {**mode, "available": False}
                for mode in execution_modes
            ]
        supports_full_access = any(
            bool(mode.get("available")) and str(mode.get("id") or "").strip() == "full_access"
            for mode in execution_modes
        )
        default_runtime_access_mode = (
            execution_mode_policy.FULL_RUNTIME_ACCESS_MODE
            if target_id in {"local_companion", "self_host_runtime"} and supports_full_access
            else execution_mode_policy.GUARDED_RUNTIME_ACCESS_MODE
        )
        target_payload = {
            "target_id": target_id,
            "canonical_target_id": canonical_target_id,
            "runtime_fabric_target": canonical_target_id,
            "legacy_target_id": target_id if canonical_target_id != target_id else None,
            "label": public_label,
            "public_label": public_label,
            "canonical_label": public_label,
            "description": definition["description"],
            "attachment_kind": definition["attachment_kind"],
            "hardware_edge": RUNTIME_TARGET_HARDWARE_EDGE.get(target_id, definition["attachment_kind"]),
            "execution_target": definition["execution_target"],
            "connection_mode": definition["connection_mode"],
            "available": bool(matching),
            "online": any(bool(item.get("online")) for item in matching),
            "healthy": any(bool(item.get("healthy")) for item in matching),
            "status": _runtime_target_status(matching),
            "attachment_count": len(matching),
            "attachment_ids": [
                str(item.get("attachment_id") or "").strip()
                for item in matching
                if str(item.get("attachment_id") or "").strip()
            ],
            "default_for_workspace": target_id == default_target_id,
            "product_default": bool(definition.get("product_default")),
            "routed_through_platform": True,
            "runtime_session_required": target_id != "cloud_default",
            "approval_boundary": "platform_policy" if target_id == "cloud_default" else "runtime_session_policy",
            "default_runtime_access_mode": default_runtime_access_mode,
            "supports_full_access": supports_full_access,
            "direct_mobile_connection_required": False,
            "workspace_scoped_identity": True,
            "supports_runtime_modes": supports_runtime_modes,
            "execution_modes": execution_modes,
            "metered": definition["attachment_kind"] == "cloud_computer",
            "requires_explicit_selection": definition["attachment_kind"] in {"cloud_computer", "self_hosted_business_node"},
        }
        if matching:
            target_payload["sample_attachment_label"] = str(matching[0].get("label") or "").strip() or None
        targets.append(target_payload)

    return {
        "tenant_id": tenant_id,
        "workspace_id": workspace_id,
        "deployment_mode": deployment_mode,
        "default_target_id": default_target_id,
        "targets": targets,
        "routing_contract": {
            "product_default_target_id": "cloud_default",
            "canonical_runtime_targets": list(CANONICAL_RUNTIME_TARGET_IDS),
            "legacy_target_aliases": dict(LEGACY_RUNTIME_TARGET_BY_CANONICAL),
            "local_hardware_edge": "empyralis_gateway",
            "public_runtime_target_labels": dict(PUBLIC_RUNTIME_TARGET_LABEL_BY_CANONICAL),
            "business_default_mode": "cloud_first",
            "runtime_placement_separate_from_computer_automation": True,
            "legacy_local_companion_deprecated_for_machine_control": True,
            "hardware_actions_require_runtime_session": True,
            "hardware_actions_emit_transparency_events": True,
            "studio_agent_computer_automation_default": "disabled",
            "registered_agents_active_agents_queue_when_over_capacity": True,
            "business_private_runtime_optional": True,
            "self_host_runtime_requires_explicit_selection": True,
            "cloud_computer_requires_explicit_selection": True,
            "cloud_computer_metered": True,
            "agent_definition_allocates_runtime": False,
            "mobile_entry_mode": "platform_first",
            "direct_mobile_lan_default": False,
            "workspace_scoped_identity": True,
            "supports_self_host_without_identity_fork": True,
            **execution_mode_policy.routing_contract_summary(),
        },
    }


def _placement_manifest(install: Dict[str, Any]) -> Dict[str, Any]:
    version = install.get("agent_definition_version") if isinstance(install.get("agent_definition_version"), dict) else {}
    return _coerce_dict(version.get("placement_manifest"))


def _allowed_runtime_classes(install: Dict[str, Any], runtime_mode: str) -> List[str]:
    placement_manifest = _placement_manifest(install)
    values = [str(item or "").strip().lower() for item in list(placement_manifest.get("allowed_runtime_classes") or []) if str(item or "").strip()]
    if values:
        return values
    if runtime_mode == "hosted_secure":
        return ["cloud_worker", "cloud_computer", "cloud_desktop", "cloud_sandbox", "self_hosted_business_node", "self_hosted_worker", "enterprise_node"]
    return ["desktop_companion", "mobile_runtime"]


def _cloud_computer_requested_by_install(install: Dict[str, Any]) -> bool:
    runtime_profile = install.get("runtime_profile") if isinstance(install.get("runtime_profile"), dict) else {}
    placement_manifest = _placement_manifest(install)
    runtime_class = _normalize_runtime_class(runtime_profile.get("runtime_class") or runtime_profile.get("runtime_type"))
    if runtime_class in CLOUD_COMPUTER_RUNTIME_CLASSES:
        return True
    runtime_type = _normalize_runtime_class(runtime_profile.get("runtime_type"))
    if runtime_type in CLOUD_COMPUTER_RUNTIME_CLASSES:
        return True
    allowed_classes = {
        _normalize_runtime_class(item)
        for item in list(placement_manifest.get("allowed_runtime_classes") or [])
        if str(item or "").strip()
    }
    if allowed_classes.intersection(CLOUD_COMPUTER_RUNTIME_CLASSES):
        return True
    target_tokens = {
        str(placement_manifest.get("preferred_runtime_slug") or "").strip().lower(),
        str(placement_manifest.get("preferred_runtime_target_id") or "").strip().lower(),
        str(placement_manifest.get("runtime_target") or "").strip().lower(),
        str(placement_manifest.get("execution_target") or "").strip().lower(),
    }
    return bool(target_tokens.intersection({"sage_cloud_computer", "sage-cloud-computer", "cloud_computer", "cloud-computer"}))


def _matches_requested_machine(attachment: Dict[str, Any], requested_machine_target: str) -> bool:
    token = str(requested_machine_target or "").strip()
    if not token:
        return True
    return token in {
        str(attachment.get("machine_id") or "").strip(),
        str(attachment.get("runtime_id") or "").strip(),
        str(attachment.get("attachment_id") or "").strip(),
    }


def _matching_attachment_score(attachment: Dict[str, Any], runtime_profile: Dict[str, Any], preferred_slug: str) -> int:
    score = 0
    if str(runtime_profile.get("id") or "").strip() and str(attachment.get("runtime_profile_id") or "").strip() == str(runtime_profile.get("id") or "").strip():
        score += 50
    runtime_profile_runtime_id = str(runtime_profile.get("runtime_id") or "").strip()
    runtime_profile_machine_id = str(runtime_profile.get("machine_id") or "").strip()
    if runtime_profile_runtime_id and str(attachment.get("runtime_id") or "").strip() == runtime_profile_runtime_id:
        score += 40
    if runtime_profile_machine_id and str(attachment.get("machine_id") or "").strip() == runtime_profile_machine_id:
        score += 35
    runtime_profile_slug = str(runtime_profile.get("slug") or "").strip()
    if runtime_profile_slug and str(attachment.get("runtime_profile_slug") or "").strip() == runtime_profile_slug:
        score += 30
    if preferred_slug and str(attachment.get("runtime_profile_slug") or "").strip() == preferred_slug:
        score += 20
    if bool(attachment.get("healthy")):
        score += 10
    if bool(attachment.get("online")):
        score += 5
    return score


def _attachment_from_profile(
    *,
    tenant_id: str,
    workspace_id: str,
    runtime_profile: Dict[str, Any],
    worker: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    profile = dict(runtime_profile or {})
    payload = _coerce_dict(worker)
    runtime_kind = _attachment_kind_for_profile(profile, worker=payload)
    runtime_class = _normalize_runtime_class(profile.get("runtime_class") or payload.get("runtime_class"))
    if runtime_kind == "local_companion" and runtime_class not in {"desktop_companion", "mobile_runtime"}:
        runtime_class = "desktop_companion"
    elif runtime_kind == "cloud_computer" and runtime_class not in CLOUD_COMPUTER_RUNTIME_CLASSES:
        runtime_class = "cloud_computer"
    elif runtime_kind == "managed_cloud" and runtime_class in {"desktop_companion", "mobile_runtime"}:
        runtime_class = "cloud_worker"
    runtime_id = str(profile.get("runtime_id") or payload.get("runtime_id") or payload.get("worker_id") or "").strip() or None
    machine_id = str(profile.get("machine_id") or payload.get("machine_id") or runtime_id or "").strip() or None
    health = _attachment_health(payload, runtime_kind)
    label = (
        str(profile.get("label") or "").strip()
        or str(payload.get("display_name") or "").strip()
        or machine_id
        or runtime_id
        or runtime_kind
    )
    profile_metadata = _coerce_dict(profile.get("metadata"))
    worker_metadata = _coerce_dict(payload.get("metadata"))
    merged_metadata = {**profile_metadata, **worker_metadata}
    self_hosted_status = _self_hosted_node_status(payload, health)
    node_kind = _normalize_node_kind(
        merged_metadata.get("node_kind"),
        runtime_class=runtime_class,
        runtime_type=str(payload.get("runtime_type") or runtime_kind).strip(),
        label=label,
    )
    supported_caps = _list_strings(payload.get("capabilities") or profile.get("supported_capabilities"))
    allowed_agent_ids = _list_strings(merged_metadata.get("allowed_agent_ids"))
    public_key = str(merged_metadata.get("public_key") or "").strip() or None
    heartbeat_at = payload.get("last_seen_at") or payload.get("last_heartbeat_at") or profile.get("last_seen_at")
    root_policy = _coerce_dict(merged_metadata.get("root_policy"))
    owner_user_id = str(merged_metadata.get("owner_user_id") or payload.get("owner_user_id") or "").strip() or None
    max_concurrent_sessions = int(
        merged_metadata.get("max_concurrent_sessions")
        or payload.get("max_concurrent_sessions")
        or 1
    )
    owner_approved_at = (
        merged_metadata.get("owner_approved_at")
        or merged_metadata.get("approved_at")
        or merged_metadata.get("ownerApprovalAt")
    )
    owner_approved_by_user_id = str(
        merged_metadata.get("owner_approved_by_user_id")
        or merged_metadata.get("approved_by_user_id")
        or ""
    ).strip() or None
    owner_approved = bool(
        merged_metadata.get("owner_approved") is True
        or owner_approved_at
    )
    return {
        "attachment_id": f"{runtime_kind}:{str(profile.get('id') or runtime_id or machine_id or label).strip()}",
        "attachment_kind": runtime_kind,
        "tenant_id": tenant_id,
        "workspace_id": workspace_id,
        "runtime_class": runtime_class,
        "runtime_profile_id": str(profile.get("id") or "").strip() or None,
        "runtime_profile_slug": str(profile.get("slug") or "").strip() or None,
        "runtime_profile_label": str(profile.get("label") or "").strip() or None,
        "runtime_id": runtime_id,
        "machine_id": machine_id,
        "runtime_type": str(payload.get("runtime_type") or runtime_kind).strip() or runtime_kind,
        "label": label,
        "online": health["online"],
        "healthy": health["healthy"],
        "control_state": health["control_state"],
        "status": health["status"],
        "capabilities": supported_caps,
        "connectors": _list_strings(payload.get("connectors") or payload.get("available_connectors") or profile.get("supported_connectors")),
        "execution_targets": _list_strings(payload.get("execution_targets")),
        "supports_runtime_modes": _attachment_support(runtime_kind),
        "trust_model": _attachment_trust(runtime_kind),
        "privacy_posture": {
            "local_private_memory_supported": runtime_kind == "local_companion",
            "cloud_safe_summary_bridge": True,
            "cloud_sync_required": runtime_kind in {"managed_cloud", "cloud_computer"},
            "personal_device_access_requires_gateway": runtime_kind == "cloud_computer",
            "persistent_workspace_volume_opt_in": runtime_kind == "cloud_computer",
        },
        "lifecycle": _attachment_lifecycle(runtime_kind, payload),
        "current_run_id": str(payload.get("current_run_id") or "").strip() or None,
        "last_seen_at": payload.get("last_seen_at") or payload.get("last_heartbeat_at"),
        "instance_id": str(payload.get("instance_id") or "").strip() or None,
        "note": str(payload.get("note") or "").strip() or None,
        "runtime_node_id": runtime_id if runtime_kind == "self_hosted_business_node" else None,
        "owner_user_id": owner_user_id,
        "node_kind": node_kind if runtime_kind == "self_hosted_business_node" else None,
        "heartbeat_at": heartbeat_at if runtime_kind == "self_hosted_business_node" else None,
        "public_key": public_key if runtime_kind == "self_hosted_business_node" else None,
        "allowed_agent_ids": allowed_agent_ids if runtime_kind == "self_hosted_business_node" else [],
        "max_concurrent_sessions": max_concurrent_sessions if runtime_kind == "self_hosted_business_node" else None,
        "root_policy": root_policy if runtime_kind == "self_hosted_business_node" else {},
        "self_hosted_node_status": self_hosted_status if runtime_kind == "self_hosted_business_node" else None,
        "owner_approved": owner_approved if runtime_kind == "self_hosted_business_node" else None,
        "owner_approved_at": owner_approved_at if runtime_kind == "self_hosted_business_node" else None,
        "owner_approved_by_user_id": owner_approved_by_user_id if runtime_kind == "self_hosted_business_node" else None,
    }


def ensure_self_hosted_node_gate(
    *,
    attachment: Dict[str, Any],
    workspace_id: str,
    required_capabilities: Optional[List[str]] = None,
) -> None:
    if str(attachment.get("attachment_kind") or "").strip() != "self_hosted_business_node":
        raise RuntimeAttachmentSelectionError(
            "Self-hosted runtime gate requires a self-hosted node attachment.",
            reason="invalid_self_hosted_attachment_kind",
        )
    if str(attachment.get("workspace_id") or "").strip() != str(workspace_id or "").strip():
        raise RuntimeAttachmentSelectionError(
            "Self-hosted node is outside the requested workspace scope.",
            reason="self_hosted_workspace_scope_mismatch",
        )
    node_status = str(attachment.get("self_hosted_node_status") or "").strip().lower()
    if node_status == "revoked":
        raise RuntimeAttachmentSelectionError(
            "Self-hosted node is revoked.",
            reason="self_hosted_node_revoked",
        )
    if node_status and node_status not in SELF_HOSTED_NODE_STATUSES:
        raise RuntimeAttachmentSelectionError(
            "Self-hosted node has unsupported status.",
            reason="self_hosted_node_status_invalid",
        )
    if not bool(attachment.get("online")):
        raise RuntimeAttachmentSelectionError(
            "Self-hosted node is offline.",
            reason="self_hosted_node_offline",
        )
    if not bool(attachment.get("healthy")):
        raise RuntimeAttachmentSelectionError(
            "Self-hosted node is unhealthy.",
            reason="self_hosted_node_unhealthy",
        )
    if not bool(attachment.get("owner_approved")):
        raise RuntimeAttachmentSelectionError(
            "Self-hosted node is not owner-approved.",
            reason="self_hosted_node_not_owner_approved",
        )
    runtime_node_id = str(attachment.get("runtime_node_id") or "").strip()
    if not runtime_node_id:
        raise RuntimeAttachmentSelectionError(
            "Self-hosted node is missing runtime_node_id.",
            reason="self_hosted_node_id_missing",
        )
    node_kind = str(attachment.get("node_kind") or "").strip().lower()
    if node_kind not in SELF_HOSTED_NODE_KINDS:
        raise RuntimeAttachmentSelectionError(
            "Self-hosted node has unsupported node_kind.",
            reason="self_hosted_node_kind_invalid",
        )
    required = {
        str(item or "").strip().lower()
        for item in list(required_capabilities or [])
        if str(item or "").strip()
    }
    available = {
        str(item or "").strip().lower()
        for item in list(attachment.get("capabilities") or [])
        if str(item or "").strip()
    }
    if required and not required.issubset(available):
        missing = sorted(required - available)
        raise RuntimeAttachmentSelectionError(
            f"Self-hosted node is missing required capabilities: {', '.join(missing)}.",
            reason="self_hosted_node_capability_mismatch",
        )
    if int(attachment.get("max_concurrent_sessions") or 0) <= 0:
        raise RuntimeAttachmentSelectionError(
            "Self-hosted node has invalid max_concurrent_sessions.",
            reason="self_hosted_node_concurrency_invalid",
        )
    _enforce_rust_runtime_attachment_decision(
        operation="self_hosted_gate",
        tenant_id=str(attachment.get("tenant_id") or "default").strip() or "default",
        workspace_id=workspace_id,
        attachment=attachment,
        required_capabilities=required_capabilities,
    )


def _managed_cloud_attachment(*, tenant_id: str, workspace_id: str, runtime_profiles: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    cloud_profiles = [
        dict(item)
        for item in runtime_profiles
        if isinstance(item, dict)
        and str(item.get("status") or "active").strip().lower() == "active"
        and _attachment_kind_for_profile(item) == "managed_cloud"
    ]
    if not cloud_profiles:
        return None
    return _attachment_from_profile(
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        runtime_profile=cloud_profiles[0],
        worker=None,
    )


async def list_workspace_runtime_attachments(
    *,
    tenant_id: str,
    workspace_id: str,
    runtime_profiles: Optional[List[Dict[str, Any]]] = None,
    fleet_workers: Optional[List[Dict[str, Any]]] = None,
    include_snapshot_version: bool = False,
) -> Dict[str, Any]:
    profiles = (
        [dict(item) for item in runtime_profiles if isinstance(item, dict)]
        if runtime_profiles is not None
        else await agent_registry_repository.list_runtime_profiles(
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            seed_if_missing=False,
        )
    )
    workers = (
        [dict(item) for item in fleet_workers if isinstance(item, dict)]
        if fleet_workers is not None
        else await run_state_repository.list_fleet_workers(
            tenant_id=tenant_id,
            workspace_id=workspace_id,
        )
    )
    if fleet_workers is None and not workers:
        # Local dev often runs without durable fleet tables; fall back to the
        # in-memory local queue view so the shell can still discover a healthy
        # paired local companion.
        try:
            from server_modules import local_queue

            local_workers_payload = local_queue.handle_get_local_workers_status()
            local_workers = local_workers_payload.get("items") if isinstance(local_workers_payload, dict) else []
            workers = [
                dict(item)
                for item in list(local_workers or [])
                if isinstance(item, dict)
                and str(item.get("tenant_id") or tenant_id).strip() == tenant_id
                and str(item.get("workspace_id") or "").strip() == workspace_id
            ]
        except Exception:
            workers = []
    gateway_db_exists = Path(gateway_state_repository.GATEWAY_STATE_DB_FILE).expanduser().exists()
    if gateway_db_exists:
        try:
            gateway_registrations = [
                dict(item)
                for item in gateway_state_repository.list_workspace_gateway_registrations(
                    workspace_id,
                    tenant_id=tenant_id,
                    include_revoked=True,
                )
                if isinstance(item, dict)
            ]
        except Exception:
            gateway_registrations = []
    else:
        gateway_registrations = []
    for registration in gateway_registrations:
        device_link = auth.get_user_device_link(str(registration.get("device_id") or "").strip())
        if device_link:
            registration["device_trust_state"] = str(device_link.get("trust_state") or registration.get("device_trust_state") or "verified").strip()
            registration["status"] = (
                "revoked"
                if str(device_link.get("status") or "").strip().lower() == "revoked"
                else str(registration.get("status") or "active").strip()
            )
    snapshot_version = _runtime_inventory_snapshot_version(
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        runtime_profiles=profiles,
        fleet_workers=workers,
        gateway_registrations=gateway_registrations,
    )
    cached_inventory = _RUNTIME_ATTACHMENTS_CACHE.get(snapshot_version)
    if cached_inventory is not None:
        payload = _clone_payload(cached_inventory)
        if include_snapshot_version:
            payload["_snapshot_version"] = snapshot_version
        return payload
    attachments: List[Dict[str, Any]] = []
    matched_worker_ids: set[str] = set()

    cloud_attachment = _managed_cloud_attachment(
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        runtime_profiles=profiles,
    )
    if cloud_attachment is not None:
        attachments.append(cloud_attachment)

    for profile in profiles:
        if not isinstance(profile, dict):
            continue
        if str(profile.get("status") or "active").strip().lower() != "active":
            continue
        kind = _attachment_kind_for_profile(profile)
        if kind == "managed_cloud":
            continue
        runtime_id = str(profile.get("runtime_id") or "").strip()
        machine_id = str(profile.get("machine_id") or "").strip()
        matched_worker = None
        for worker in workers:
            if not isinstance(worker, dict):
                continue
            worker_id = str(worker.get("worker_id") or worker.get("runtime_id") or "").strip()
            if runtime_id and str(worker.get("runtime_id") or worker.get("worker_id") or "").strip() == runtime_id:
                matched_worker = worker
            elif machine_id and str(worker.get("machine_id") or "").strip() == machine_id:
                matched_worker = worker
            if matched_worker is not None:
                if worker_id:
                    matched_worker_ids.add(worker_id)
                break
        attachments.append(
            _attachment_from_profile(
                tenant_id=tenant_id,
                workspace_id=workspace_id,
                runtime_profile=profile,
                worker=matched_worker,
            )
        )

    for worker in workers:
        if not isinstance(worker, dict):
            continue
        worker_id = str(worker.get("worker_id") or worker.get("runtime_id") or "").strip()
        if worker_id and worker_id in matched_worker_ids:
            continue
        attachments.append(
            _attachment_from_profile(
                tenant_id=tenant_id,
                workspace_id=workspace_id,
                runtime_profile={},
                worker=worker,
            )
        )

    for registration in gateway_registrations:
        if not isinstance(registration, dict):
            continue
        matched_index = next(
            (
                index
                for index, item in enumerate(attachments)
                if str(item.get("attachment_kind") or "").strip() == "local_companion"
                and (
                    str(item.get("machine_id") or "").strip() == str(registration.get("device_id") or "").strip()
                    or str(item.get("runtime_id") or "").strip() == str(registration.get("gateway_id") or "").strip()
                )
            ),
            None,
        )
        if matched_index is None and len([item for item in attachments if str(item.get("attachment_kind") or "").strip() == "local_companion"]) == 1 and len(gateway_registrations) == 1:
            matched_index = next(
                (
                    index
                    for index, item in enumerate(attachments)
                    if str(item.get("attachment_kind") or "").strip() == "local_companion"
                ),
                None,
            )
        if matched_index is not None:
            attachments[matched_index] = _merge_gateway_registration_into_attachment(attachments[matched_index], registration)
        else:
            attachments.append(_gateway_attachment_from_registration(registration))

    attachments.sort(
        key=lambda item: (
            {
                "managed_cloud": 0,
                "cloud_computer": 1,
                "local_companion": 2,
                "self_hosted_business_node": 3,
            }.get(str(item.get("attachment_kind") or ""), 4),
            0 if bool(item.get("healthy")) else 1,
            str(item.get("label") or ""),
        )
    )
    deployment_mode = _deployment_mode(attachments)
    payload = {
        "tenant_id": tenant_id,
        "workspace_id": workspace_id,
        "deployment_mode": deployment_mode,
        "supported_deployment_modes": list(SUPPORTED_DEPLOYMENT_MODES),
        "attachments": attachments,
        "selection_policy": {
            "hosted_secure_prefers": ["managed_cloud", "cloud_computer", "self_hosted_business_node"],
            "local_secure_prefers": ["local_companion"],
            "privileged_device_requires": ["local_companion"],
            "cloud_computer_requires_explicit_selection": True,
            "cloud_computer_metered": True,
            "shared_sage_identity": True,
            "specialist_scope_required": True,
        },
    }
    cached_payload = _cache_store(_RUNTIME_ATTACHMENTS_CACHE, snapshot_version, payload)
    if include_snapshot_version:
        cached_payload["_snapshot_version"] = snapshot_version
    return cached_payload


async def list_workspace_runtime_targets(
    *,
    tenant_id: str,
    workspace_id: str,
    runtime_profiles: Optional[List[Dict[str, Any]]] = None,
    fleet_workers: Optional[List[Dict[str, Any]]] = None,
    inventory: Optional[Dict[str, Any]] = None,
    include_snapshot_version: bool = False,
) -> Dict[str, Any]:
    resolved_inventory = dict(inventory) if isinstance(inventory, dict) else await list_workspace_runtime_attachments(
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        runtime_profiles=runtime_profiles,
        fleet_workers=fleet_workers,
        include_snapshot_version=True,
    )
    snapshot_version = _inventory_snapshot_version(resolved_inventory)
    cached_targets = _RUNTIME_TARGETS_CACHE.get(snapshot_version)
    if cached_targets is not None:
        payload = _clone_payload(cached_targets)
        if include_snapshot_version:
            payload["_snapshot_version"] = snapshot_version
        return payload
    payload = build_workspace_runtime_targets(
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        inventory=resolved_inventory,
    )
    cached_payload = _cache_store(_RUNTIME_TARGETS_CACHE, snapshot_version, payload)
    if include_snapshot_version:
        cached_payload["_snapshot_version"] = snapshot_version
    return cached_payload


async def resolve_install_runtime_plan(
    *,
    tenant_id: str,
    workspace_id: str,
    install: Dict[str, Any],
    requested_attachment_id: Optional[str] = None,
    requested_machine_target: Optional[str] = None,
    policy_context: Optional[Dict[str, Any]] = None,
    inventory: Optional[Dict[str, Any]] = None,
    workspace: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    runtime_inventory = inventory or await list_workspace_runtime_attachments(
        tenant_id=tenant_id,
        workspace_id=workspace_id,
    )
    attachments = [dict(item) for item in list(runtime_inventory.get("attachments") or []) if isinstance(item, dict)]
    runtime_mode = _normalize_runtime_mode(install.get("runtime_mode"))
    runtime_profile = install.get("runtime_profile") if isinstance(install.get("runtime_profile"), dict) else {}
    preferred_slug = str(_placement_manifest(install).get("preferred_runtime_slug") or "").strip()
    allowed_runtime_classes = _allowed_runtime_classes(install, runtime_mode=runtime_mode)
    policy = {
        **_coerce_dict(install.get("policy_context_overrides")),
        **_coerce_dict(policy_context),
    }
    requested_summary_payload_classes = hybrid_policy_service.normalize_summary_bridge_payload_classes(
        policy.get("summary_bridge_payloads")
        if isinstance(policy.get("summary_bridge_payloads"), (list, tuple, set))
        else policy.get("summary_payload_classes")
        if isinstance(policy.get("summary_payload_classes"), (list, tuple, set))
        else []
    )
    summary_bridge_status = memory_service.hybrid_summary_bridge_status(
        workspace_id,
        payload_classes=requested_summary_payload_classes or None,
        agent_install_id=str(install.get("id") or "").strip() or None,
    )
    if "last_safe_summary_available" not in policy:
        policy["last_safe_summary_available"] = bool(summary_bridge_status.get("last_safe_summary_available"))
    policy["summary_bridge_status"] = dict(summary_bridge_status)
    try:
        hybrid_policy = hybrid_policy_service.evaluate_hybrid_runtime_policy(
            runtime_mode=runtime_mode,
            policy_context=policy,
            attachments=attachments,
        )
    except hybrid_policy_service.HybridPolicyError as error:
        raise RuntimeAttachmentSelectionError(
            error.message,
            reason=error.reason,
            enforcement_state=error.policy_state,
        ) from error
    summary_bridge_policy = hybrid_policy.get("summary_bridge") if isinstance(hybrid_policy.get("summary_bridge"), dict) else {}
    hybrid_policy["summary_bridge"] = {
        **dict(summary_bridge_policy),
        "status": dict(summary_bridge_status),
    }
    placement_enforcement = hybrid_policy.get("placement") if isinstance(hybrid_policy.get("placement"), dict) else {}
    required_attachment_kinds = [
        str(item or "").strip()
        for item in list(placement_enforcement.get("required_attachment_kinds") or [])
        if str(item or "").strip()
    ]
    preferred_attachment_kinds = [
        str(item or "").strip()
        for item in list(placement_enforcement.get("preferred_attachment_kinds") or [])
        if str(item or "").strip()
    ]
    required_capabilities = _list_strings(placement_enforcement.get("required_capabilities"))
    required_connectors = _list_strings(placement_enforcement.get("required_connectors"))
    state_layer_policy = execution_sandbox_service.state_layer_policy(runtime_mode=runtime_mode)

    filtered: List[Dict[str, Any]] = []
    requested_attachment_token = str(requested_attachment_id or "").strip()
    requested_machine_token = str(requested_machine_target or "").strip()
    cloud_computer_explicitly_requested = bool(
        requested_attachment_token
        or requested_machine_token
        or _cloud_computer_requested_by_install(install)
    )
    for attachment in attachments:
        attachment_kind = str(attachment.get("attachment_kind") or "").strip()
        runtime_class = _normalize_runtime_class(attachment.get("runtime_class"))
        if requested_attachment_token and str(attachment.get("attachment_id") or "").strip() != requested_attachment_token:
            continue
        if requested_machine_token and not _matches_requested_machine(attachment, requested_machine_token):
            continue
        if allowed_runtime_classes and runtime_class not in allowed_runtime_classes:
            continue
        if required_attachment_kinds and attachment_kind not in required_attachment_kinds:
            continue
        if runtime_mode == "hosted_secure" and attachment_kind not in {"managed_cloud", "cloud_computer", "self_hosted_business_node"}:
            continue
        if attachment_kind == "cloud_computer" and not cloud_computer_explicitly_requested:
            continue
        if runtime_mode in {"local_secure", "privileged_device"} and attachment_kind != "local_companion":
            continue
        if not _attachment_capability_match(attachment, required_capabilities):
            continue
        if not _attachment_connector_match(attachment, required_connectors):
            continue
        filtered.append(attachment)

    filtered.sort(
        key=lambda item: (
            _preferred_attachment_kind_rank(item, preferred_attachment_kinds),
            -_matching_attachment_score(item, runtime_profile, preferred_slug),
            0 if bool(item.get("healthy")) else 1,
            0 if bool(item.get("online")) else 1,
            str(item.get("label") or ""),
        )
    )
    selected = filtered[0] if filtered else None

    if runtime_mode == "hosted_secure":
        runtime_class = _normalize_runtime_class(runtime_profile.get("runtime_class"))
        if runtime_class in {"desktop_companion", "mobile_runtime"}:
            raise RuntimeAttachmentSelectionError(
                "Hosted Secure requires a cloud or self-hosted secure runtime profile.",
                reason="invalid_hosted_runtime_profile",
                enforcement_state=hybrid_policy,
            )
        if selected is None:
            raise RuntimeAttachmentSelectionError(
                "No hosted runtime attachment is available for this workspace.",
                reason="no_hosted_runtime_attachment_available",
                enforcement_state=hybrid_policy,
            )
        workspace_record = dict(workspace) if isinstance(workspace, dict) else await control_plane_repository.get_workspace_by_id(workspace_id) or {}
        entitlement_state = entitlements_service.enforce_hosted_runtime_access(
            workspace=workspace_record,
            install=install,
            workspace_id=workspace_id,
            selected_attachment=selected,
        )
        selected_attachment_kind = str(selected.get("attachment_kind") or "").strip()
        if selected_attachment_kind == "self_hosted_business_node":
            ensure_self_hosted_node_gate(
                attachment=selected,
                workspace_id=workspace_id,
                required_capabilities=required_capabilities,
            )
        _enforce_rust_runtime_attachment_decision(
            operation="select_attachment",
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            attachment=selected,
            required_capabilities=required_capabilities,
            required_connectors=required_connectors,
            requested_machine_target=requested_machine_token,
        )
        execution_target_selected = "cloud_computer" if selected_attachment_kind == "cloud_computer" else "cloud"
        return {
            "runtime_mode": runtime_mode,
            "deployment_mode": str(runtime_inventory.get("deployment_mode") or "cloud_only"),
            "selected_attachment": selected,
            "runtime_attachment_kind": selected_attachment_kind or None,
            "machine_target": None,
            "execution_target_selected": execution_target_selected,
            "selection_reason": "Hosted execution selected a secure cloud, cloud-computer, or self-hosted runtime attachment.",
            "privacy_split": {
                "local_private_memory_stays_local": True,
                "cloud_safe_summaries_only": True,
            },
            "entitlements": entitlement_state,
            "hybrid_rules": {
                "shared_sage_identity": True,
                "local_power_not_leaked_to_hosted_specialists": True,
                "specialists_keep_scoped_runtime_policy": True,
            },
            "hybrid_policy": hybrid_policy,
            "sync_enforcement": {
                "effective_sync_class": hybrid_policy.get("effective_sync_class"),
                "requested_memory_layers": list(hybrid_policy.get("requested_memory_layers") or []),
                "layer_sync_classes": dict(hybrid_policy.get("layer_sync_classes") or {}),
                "summary_bridge": dict(hybrid_policy.get("summary_bridge") or {}),
                "summary_bridge_status": dict(summary_bridge_status),
            },
            "placement_enforcement": {
                "priority_order": list(placement_enforcement.get("priority_order") or []),
                "required_attachment_kinds": required_attachment_kinds,
                "preferred_attachment_kinds": preferred_attachment_kinds,
                "required_capabilities": required_capabilities,
                "required_connectors": required_connectors,
            },
            "degraded_mode": dict(hybrid_policy.get("degraded_mode") or {}),
            "state_layer_policy": state_layer_policy,
        }

    if selected is None:
        raise RuntimeAttachmentSelectionError(
            "No active local runtime attachment matches this specialist runtime profile.",
            reason="no_local_runtime_attachment_available",
            enforcement_state=hybrid_policy,
        )
    if str(selected.get("control_state") or "active").strip().lower() != "active":
        raise RuntimeAttachmentSelectionError(
            "Selected runtime attachment is not active.",
            reason="runtime_attachment_inactive",
            enforcement_state=hybrid_policy,
        )
    if not bool(selected.get("online")):
        target_selection = dict(selected.get("target_selection") or {})
        if str(target_selection.get("reason") or "").strip():
            raise RuntimeAttachmentSelectionError(
                "Selected local Agent Computer is not ready.",
                reason=str(target_selection.get("reason") or "").strip(),
                enforcement_state=hybrid_policy,
            )
        raise RuntimeAttachmentSelectionError(
            "Selected runtime attachment is offline.",
            reason="runtime_attachment_offline",
            enforcement_state=hybrid_policy,
        )
    if str(selected.get("attachment_kind") or "").strip() == "local_companion":
        if not _local_companion_ready_capability_match(selected, required_capabilities):
            raise RuntimeAttachmentSelectionError(
                "Selected local Agent Computer does not report the required capability as ready.",
                reason="gateway_capability_not_ready",
                enforcement_state=hybrid_policy,
            )
    if runtime_mode == "privileged_device" and not bool(policy.get("privileged_runtime_approved")):
        raise RuntimeAttachmentSelectionError(
            "Privileged device execution requires explicit owner approval.",
            reason="privileged_runtime_approval_required",
            enforcement_state=hybrid_policy,
        )
    _enforce_rust_runtime_attachment_decision(
        operation="local_companion_gate",
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        attachment=selected,
        required_capabilities=required_capabilities,
    )
    _enforce_rust_runtime_attachment_decision(
        operation="select_attachment",
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        attachment=selected,
        required_capabilities=required_capabilities,
        required_connectors=required_connectors,
        requested_machine_target=requested_machine_token,
    )
    return {
        "runtime_mode": runtime_mode,
        "deployment_mode": str(runtime_inventory.get("deployment_mode") or "local_only"),
        "selected_attachment": selected,
        "runtime_attachment_kind": str(selected.get("attachment_kind") or "").strip() or None,
        "machine_target": str(selected.get("machine_id") or selected.get("runtime_id") or "").strip() or None,
        "execution_target_selected": "local_companion",
        "selection_reason": "Local execution selected the paired local runtime attachment for scoped device work.",
        "privacy_split": {
            "local_private_memory_stays_local": True,
            "cloud_safe_summaries_only": False,
        },
        "hybrid_rules": {
            "shared_sage_identity": True,
            "local_power_not_leaked_to_hosted_specialists": True,
            "specialists_keep_scoped_runtime_policy": True,
        },
        "hybrid_policy": hybrid_policy,
        "sync_enforcement": {
            "effective_sync_class": hybrid_policy.get("effective_sync_class"),
            "requested_memory_layers": list(hybrid_policy.get("requested_memory_layers") or []),
            "layer_sync_classes": dict(hybrid_policy.get("layer_sync_classes") or {}),
            "summary_bridge": dict(hybrid_policy.get("summary_bridge") or {}),
            "summary_bridge_status": dict(summary_bridge_status),
        },
        "placement_enforcement": {
            "priority_order": list(placement_enforcement.get("priority_order") or []),
            "required_attachment_kinds": required_attachment_kinds,
            "preferred_attachment_kinds": preferred_attachment_kinds,
            "required_capabilities": required_capabilities,
            "required_connectors": required_connectors,
        },
        "degraded_mode": dict(hybrid_policy.get("degraded_mode") or {}),
        "state_layer_policy": state_layer_policy,
    }
