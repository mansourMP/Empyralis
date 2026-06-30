from __future__ import annotations

import base64
import json
import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from server_modules import (
    agent_action_metering_service,
    activity_ledger_service,
    artifact_service,
    control_plane_repository,
    deployed_agent_config_schema,
    deployed_agent_runtime_contract_service,
    runtime_attachment_service,
    rust_runtime_kernel_client,
    session_service,
    virtual_computer_runtime,
)


DEFAULT_RECORDING_RETENTION_SECONDS = 30 * 24 * 60 * 60
DEFAULT_IDLE_TIMEOUT_SECONDS = 10 * 60
DEFAULT_MAX_RUNTIME_SECONDS = 60 * 60
DEFAULT_PROVIDER_COST_ESTIMATE = 1.0
DEFAULT_APPROVAL_ROLES = ["owner", "admin"]
PRIVACY_CONTRACT_REQUIRED_KEYS = {
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
COMPUTER_SAFETY_REQUIRED_KEYS = {
    "enabled",
    "required_for_mode",
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
DISABLED_RECORDING_POLICIES = {"disabled", "off", "none"}
COMPUTER_RUNTIME_MODES = {
    deployed_agent_runtime_contract_service.STUDIO_AGENT_MODE_CLOUD_COMPUTER,
    deployed_agent_runtime_contract_service.STUDIO_AGENT_MODE_MY_COMPUTER,
}
_RUNTIME_REGISTRY = virtual_computer_runtime.build_default_runtime_registry()
_FORBIDDEN_POLICY_OVERRIDE_KEYS = {
    "computer_automation",
    "cost_quota",
    "runtime_quota",
    "network_policy",
    "enterprise_controls",
    "runtime_kill_state",
    "session_recording",
    "policy_metadata",
    "runtime_provider_id",
    "runtime_choice",
    "runtime_node_id",
    "runtime_profile_id",
    "runtime_attachment_id",
    "self_hosted_runtime_binding",
}


def _risk_policy_for_runtime_access_mode(*, runtime_access_mode: Any = None, requires_owner_approval: Any = True) -> Dict[str, Any]:
    access_mode = deployed_agent_runtime_contract_service.normalize_runtime_access_mode(
        runtime_access_mode,
        requires_owner_approval=requires_owner_approval,
    )
    if access_mode == deployed_agent_runtime_contract_service.RUNTIME_ACCESS_MODE_FULL_ACCESS:
        return {
            "red_policy": "allow",
            "allow_orange_without_approval": True,
            "deny_wins": True,
            "deny_classes": [],
        }
    return {
        "red_policy": "owner_approval" if bool(requires_owner_approval) else "block",
        "allow_orange_without_approval": False,
        "deny_wins": True,
        "deny_classes": [],
    }
_RUNTIME_BINDING_CLOUD = "cloud_computer_agent"
_RUNTIME_BINDING_LOCAL_GATEWAY = "my_computer_agent"
_RUNTIME_BINDING_SELF_HOSTED = "self_hosted_agent"
_RUNTIME_CONNECTOR_IDS = {"browser", "computer", "shell"}
_SELF_HOSTED_ALLOWED_FILESYSTEM_SCOPES = {"none", "session_scoped", "workspace_scoped"}


def _text(value: Any) -> str:
    return str(value or "").strip()


def _coerce_dict(value: Any) -> Dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _parse_iso_datetime(value: Any) -> Optional[datetime]:
    text = _text(value)
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except Exception:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _runtime_target_from_cloud_metadata(metadata: Dict[str, Any]) -> str:
    if bool(metadata.get("local_gateway_bound")) or _text(metadata.get("runtime_session_binding")).lower() == _RUNTIME_BINDING_LOCAL_GATEWAY:
        return "local_companion"
    return "sage_cloud_computer"


def _runtime_billable_seconds(*, started_at: Any, ended_at: Any, max_seconds: Any = None) -> float:
    started = _parse_iso_datetime(started_at)
    ended = _parse_iso_datetime(ended_at)
    if not started or not ended or ended <= started:
        return 0.0
    duration = max(0.0, (ended - started).total_seconds())
    cap = _positive_int(max_seconds, default=0)
    if cap > 0:
        duration = min(duration, float(cap))
    return round(duration, 3)


def _runtime_cost_estimate_usd(runtime_target: str, billable_seconds: float) -> float:
    if runtime_target != "sage_cloud_computer":
        return 0.0
    return round(max(0.0, float(billable_seconds or 0.0)) / 3600.0 * DEFAULT_PROVIDER_COST_ESTIMATE, 6)


def _coerce_list(value: Any) -> list[Any]:
    return list(value) if isinstance(value, (list, tuple)) else []


def _session_ctx_payload(value: Any) -> Dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _positive_float(value: Any, default: float) -> float:
    try:
        resolved = float(value)
    except Exception:
        return float(default)
    return resolved if resolved > 0 else float(default)


def _positive_int(value: Any, default: int) -> int:
    try:
        resolved = int(value)
    except Exception:
        return int(default)
    return resolved if resolved > 0 else int(default)


def _runtime_choice_for_config(config: deployed_agent_config_schema.DeployedAgentConfig) -> str:
    runtime_class = _text(config.computer_automation.runtime_class).lower()
    if runtime_class == "virtual_browser":
        return "virtual_browser"
    if runtime_class == "virtual_desktop":
        return "virtual_desktop"
    if runtime_class == "virtual_code_sandbox":
        return "virtual_code_sandbox"
    if runtime_class in {"local_browser", "local_desktop"}:
        return "local"

    mode = _text(config.studio_agent_mode).lower()
    if mode == deployed_agent_runtime_contract_service.STUDIO_AGENT_MODE_CLOUD_COMPUTER:
        return "virtual_browser"
    if mode == deployed_agent_runtime_contract_service.STUDIO_AGENT_MODE_MY_COMPUTER:
        return "local"
    return ""


def _recording_retention_seconds(config: deployed_agent_config_schema.DeployedAgentConfig) -> int:
    automation = config.computer_automation
    policy = _text(automation.session_recording_policy).lower()
    if not automation.enabled or policy in DISABLED_RECORDING_POLICIES:
        return 0
    return DEFAULT_RECORDING_RETENTION_SECONDS


def _session_recording_enabled(config: deployed_agent_config_schema.DeployedAgentConfig) -> bool:
    automation = config.computer_automation
    policy = _text(automation.session_recording_policy).lower()
    return bool(automation.enabled) and policy not in DISABLED_RECORDING_POLICIES


def _validate_privacy_contract_snapshot(snapshot: Any) -> bool:
    return isinstance(snapshot, dict) and PRIVACY_CONTRACT_REQUIRED_KEYS.issubset(set(snapshot.keys()))


def _validate_computer_safety_contract_snapshot(snapshot: Any, *, require_for_mode: bool) -> bool:
    if not isinstance(snapshot, dict):
        return False
    if not COMPUTER_SAFETY_REQUIRED_KEYS.issubset(set(snapshot.keys())):
        return False
    if bool(snapshot.get("inherit_host_environment")):
        return False
    filesystem_default_access = _text(snapshot.get("filesystem_default_access")).lower()
    if filesystem_default_access not in {"none", "session_scoped", "workspace_scoped"}:
        return False
    terminal_policy = _text(snapshot.get("terminal_command_policy")).lower()
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
    if require_for_mode and not bool(snapshot.get("required_for_mode")):
        return False
    return True


def _require(value: bool, message: str) -> None:
    if not value:
        raise ValueError(message)


def _validated_contract_snapshots(
    record: Dict[str, Any],
    config: deployed_agent_config_schema.DeployedAgentConfig,
) -> tuple[Dict[str, Any], Dict[str, Any]]:
    metadata = _coerce_dict(record.get("metadata"))
    privacy_snapshot = _coerce_dict(metadata.get("privacy_contract_snapshot"))
    computer_safety_snapshot = _coerce_dict(metadata.get("computer_safety_contract_snapshot"))
    mode = _text(config.studio_agent_mode).lower()
    requires_computer_runtime = mode in COMPUTER_RUNTIME_MODES

    _require(
        _validate_privacy_contract_snapshot(privacy_snapshot),
        "Complete privacy_contract_snapshot is required for deployed runtime policy.",
    )
    if requires_computer_runtime:
        _require(
            _validate_computer_safety_contract_snapshot(
                computer_safety_snapshot,
                require_for_mode=True,
            ),
            "Complete computer_safety_contract_snapshot is required for computer runtime policy.",
        )
    return privacy_snapshot, computer_safety_snapshot


def _validate_required_runtime_policy_fields(
    config: deployed_agent_config_schema.DeployedAgentConfig,
    computer_safety_snapshot: Dict[str, Any],
) -> None:
    mode = _text(config.studio_agent_mode).lower()
    if mode not in COMPUTER_RUNTIME_MODES:
        return

    automation = config.computer_automation
    allowed_domains = list(automation.allowed_domains or [])
    _require(len(allowed_domains) > 0, "computer_automation.allowed_domains is required for computer runtime policy.")
    _require(automation.daily_budget_usd is not None, "computer_automation.daily_budget_usd is required for computer runtime policy.")
    _require(
        automation.monthly_budget_usd is not None,
        "computer_automation.monthly_budget_usd is required for computer runtime policy.",
    )
    _require(
        config.commerce_policy.monthly_cost_cap_usd is not None,
        "commerce_policy.monthly_cost_cap_usd is required for computer runtime policy.",
    )
    recording_policy = _text(automation.session_recording_policy).lower()
    _require(
        recording_policy not in DISABLED_RECORDING_POLICIES,
        "computer_automation.session_recording_policy must enable session recording for computer runtime policy.",
    )

    snapshot_allowlist = [str(item).strip() for item in _coerce_list(computer_safety_snapshot.get("domain_allowlist")) if str(item).strip()]
    snapshot_recording = _coerce_dict(computer_safety_snapshot.get("screenshot_session_recording"))
    snapshot_recording_policy = _text(snapshot_recording.get("recording_policy")).lower()

    _require(len(snapshot_allowlist) > 0, "computer_safety_contract_snapshot.domain_allowlist is required for computer runtime policy.")
    _require(
        snapshot_recording_policy not in DISABLED_RECORDING_POLICIES,
        "computer_safety_contract_snapshot.screenshot_session_recording.recording_policy must enable session recording.",
    )
    _require(
        int(computer_safety_snapshot.get("max_runtime_seconds") or 0) > 0,
        "computer_safety_contract_snapshot.max_runtime_seconds is required for computer runtime policy.",
    )
    _require(
        int(computer_safety_snapshot.get("session_timeout_seconds") or 0) > 0,
        "computer_safety_contract_snapshot.session_timeout_seconds is required for computer runtime policy.",
    )


def _runtime_kill_state(deployed_agent: Dict[str, Any]) -> Dict[str, Any]:
    metadata = _coerce_dict(deployed_agent.get("metadata"))
    kill_switch = _coerce_dict(metadata.get("kill_switch"))
    workspace_emergency_stop = _coerce_dict(metadata.get("workspace_emergency_stop"))
    deployment_state = _text(deployed_agent.get("deployment_state")).lower() or "draft"
    return {
        "deployment_state": deployment_state,
        "kill_switch_active": bool(kill_switch.get("active")),
        "workspace_emergency_stop_active": bool(workspace_emergency_stop.get("active")),
        "kill_switch_reason": _text(kill_switch.get("reason")) or None,
        "workspace_emergency_stop_reason": _text(workspace_emergency_stop.get("reason")) or None,
    }


async def _append_cloud_runtime_audit_event(
    *,
    deployed_agent: Dict[str, Any],
    tenant_id: Any,
    workspace_id: Any,
    run_id: Any = None,
    thread_id: Any = None,
    session_key: Any = None,
    channel: Any = None,
    trace_id: Any = None,
    artifacts: Optional[List[Dict[str, Any]]] = None,
    action: str,
    title: str,
    summary: str,
    status: str = "logged",
    event_class: str = "system_activity",
    review_required: bool = False,
    payload: Optional[Dict[str, Any]] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> None:
    if not isinstance(deployed_agent, dict):
        return
    deployed_agent_id = _text(deployed_agent.get("id"))
    if not deployed_agent_id:
        return
    config = deployed_agent_config_schema.deployed_agent_config_from_record(deployed_agent)
    snapshot_metadata = _coerce_dict(deployed_agent.get("metadata"))
    privacy_snapshot = _coerce_dict(snapshot_metadata.get("privacy_contract_snapshot"))
    computer_snapshot = _coerce_dict(snapshot_metadata.get("computer_safety_contract_snapshot"))
    event_metadata = {
        "deployed_agent_id": deployed_agent_id,
        "deployment_state": _text(deployed_agent.get("deployment_state")).lower() or "draft",
        "runtime_selected": {
            "studio_agent_mode": config.studio_agent_mode,
            "runtime_target": config.runtime_target,
            "runtime_placement": config.runtime_placement,
        },
        "privacy_contract_version": int(privacy_snapshot.get("schema_version") or 0) or None,
        "computer_safety_contract_version": int(computer_snapshot.get("schema_version") or 0) or None,
        "audit_version": 1,
    }
    event_metadata.update(_coerce_dict(metadata))
    await activity_ledger_service.append_activity_event(
        tenant_id=_text(tenant_id),
        workspace_id=_text(workspace_id),
        actor_type="deployed_agent",
        actor_id=deployed_agent_id,
        install_id=_text(deployed_agent.get("backing_install_id")) or None,
        run_id=_text(run_id) or None,
        thread_id=_text(thread_id) or None,
        session_key=_text(session_key) or None,
        channel=_text(channel).lower() or None,
        trace_id=_text(trace_id) or None,
        event_class=event_class,
        detail_level="audit_reference",
        action=action,
        title=title,
        summary=summary,
        status=status,
        review_required=bool(review_required),
        artifacts=list(artifacts or []),
        payload=_coerce_dict(payload),
        metadata=event_metadata,
    )


async def _assert_self_hosted_runtime_gate(
    *,
    deployed_agent: Dict[str, Any],
    tenant_id: Any,
    workspace_id: Any,
    runtime_session_id: Any = None,
    phase: str = "session_init",
) -> Dict[str, Any]:
    config = deployed_agent_config_schema.deployed_agent_config_from_record(deployed_agent)
    if _text(config.studio_agent_mode).lower() != deployed_agent_runtime_contract_service.STUDIO_AGENT_MODE_SELF_HOSTED:
        return {}
    runtime_profile_id = _text(config.runtime_profile_id)
    if not runtime_profile_id:
        raise ValueError(
            "self_hosted_agent requires explicit runtime_profile_id agent-to-node binding before session creation."
        )
    binding = _self_hosted_runtime_binding_contract(deployed_agent)
    required_capabilities = [
        _text(item).lower()
        for item in _coerce_list(binding.get("allowed_capabilities"))
        if _text(item)
    ]
    workspace_token = _text(workspace_id)
    tenant_token = _text(tenant_id)
    inventory = await runtime_attachment_service.list_workspace_runtime_attachments(
        tenant_id=tenant_token,
        workspace_id=workspace_token,
    )
    attachments = [
        dict(item)
        for item in list((inventory or {}).get("attachments") or [])
        if isinstance(item, dict)
        and _text(item.get("attachment_kind")).lower() == "self_hosted_business_node"
        and _text(item.get("runtime_profile_id")) == runtime_profile_id
    ]
    if not attachments:
        raise ValueError(
            "self_hosted_agent requires a registered self-hosted node matching runtime_profile_id before session creation."
        )
    attachment = attachments[0]
    try:
        runtime_attachment_service.ensure_self_hosted_node_gate(
            attachment=attachment,
            workspace_id=workspace_token,
            required_capabilities=required_capabilities,
        )
    except runtime_attachment_service.RuntimeAttachmentSelectionError as error:
        raise ValueError(str(error)) from error
    allowed_agent_ids = {
        _text(item)
        for item in _coerce_list(attachment.get("allowed_agent_ids"))
        if _text(item)
    }
    if allowed_agent_ids:
        deployed_agent_id = _text(deployed_agent.get("id"))
        backing_install_id = _text(deployed_agent.get("backing_install_id"))
        if deployed_agent_id not in allowed_agent_ids and backing_install_id not in allowed_agent_ids:
            raise ValueError("self_hosted_agent is not allowed on the selected self-hosted node.")

    max_concurrent_sessions = int(attachment.get("max_concurrent_sessions") or 0)
    if max_concurrent_sessions > 0:
        pool = await control_plane_repository.ensure_control_plane_schema()
        if pool is None:
            raise ValueError("Unable to verify self-hosted runtime concurrency quota.")
        runtime_node_id = _text(attachment.get("runtime_node_id"))
        exclude_session_id = _text(runtime_session_id)
        if exclude_session_id:
            active_sessions = await pool.fetchval(
                """
                SELECT COUNT(*)
                FROM agent_sessions
                WHERE tenant_id = $1
                  AND workspace_id = $2
                  AND status = 'active'
                  AND (expires_at IS NULL OR expires_at > NOW())
                  AND COALESCE(metadata->>'runtime_session_binding', '') = $3
                  AND COALESCE(metadata->>'runtime_node_id', '') = $4
                  AND id != $5
                """,
                tenant_token,
                workspace_token,
                _RUNTIME_BINDING_SELF_HOSTED,
                runtime_node_id,
                exclude_session_id,
            )
        else:
            active_sessions = await pool.fetchval(
                """
                SELECT COUNT(*)
                FROM agent_sessions
                WHERE tenant_id = $1
                  AND workspace_id = $2
                  AND status = 'active'
                  AND (expires_at IS NULL OR expires_at > NOW())
                  AND COALESCE(metadata->>'runtime_session_binding', '') = $3
                  AND COALESCE(metadata->>'runtime_node_id', '') = $4
                """,
                tenant_token,
                workspace_token,
                _RUNTIME_BINDING_SELF_HOSTED,
                runtime_node_id,
            )
        active_count = int(active_sessions or 0)
        phase_token = _text(phase).lower() or "session_init"
        if phase_token == "session_init":
            if active_count >= max_concurrent_sessions:
                raise ValueError("self_hosted_agent node concurrency quota exceeded.")
        else:
            if active_count > max_concurrent_sessions:
                raise ValueError("self_hosted_agent node concurrency quota exceeded.")
    return attachment


async def _assert_my_computer_runtime_gate(
    *,
    deployed_agent: Dict[str, Any],
    tenant_id: Any,
    workspace_id: Any,
) -> Dict[str, Any]:
    config = deployed_agent_config_schema.deployed_agent_config_from_record(deployed_agent)
    if _text(config.studio_agent_mode).lower() != deployed_agent_runtime_contract_service.STUDIO_AGENT_MODE_MY_COMPUTER:
        return {}
    inventory = await runtime_attachment_service.list_workspace_runtime_attachments(
        tenant_id=_text(tenant_id),
        workspace_id=_text(workspace_id),
    )
    attachments = [
        dict(item)
        for item in list((inventory or {}).get("attachments") or [])
        if isinstance(item, dict)
        and _text(item.get("attachment_kind")).lower() == "local_companion"
        and _text(item.get("workspace_id")) == _text(workspace_id)
        and bool(item.get("online"))
        and bool(item.get("healthy"))
        and _text(item.get("control_state")).lower() != "revoked"
    ]
    if not attachments:
        raise ValueError(
            "my_computer_agent requires a healthy paired Gateway before runtime session creation."
        )
    return attachments[0]


def _known_virtual_runtime_provider_ids() -> set[str]:
    return {
        virtual_computer_runtime.PROVIDER_ID_BROWSERBASE,
        virtual_computer_runtime.PROVIDER_ID_E2B,
        virtual_computer_runtime.PROVIDER_ID_DAYTONA,
        virtual_computer_runtime.PROVIDER_ID_AWS_WORKSPACES,
        virtual_computer_runtime.PROVIDER_ID_AZURE_VIRTUAL_DESKTOP,
        virtual_computer_runtime.PROVIDER_ID_DOCKER_KUBERNETES,
        virtual_computer_runtime.PROVIDER_ID_DIGITALOCEAN_SSH,
    }


def _runtime_provider_id_for_config(config: Any) -> Optional[str]:
    supply = _coerce_dict(getattr(config, "runtime_supply", {}))
    binding = _coerce_dict(supply.get("provider_binding"))
    known = {_text(item).lower() for item in _known_virtual_runtime_provider_ids()}
    for key in (
        "runtime_provider_id",
        "runtime_provider",
        "virtual_runtime_provider",
        "virtual_computer_provider",
        "cloud_computer_provider",
        "computer_runtime_provider",
        "internal_runtime_provider",
    ):
        candidate = _text(binding.get(key) or supply.get(key)).lower()
        if candidate and candidate in known:
            return candidate
    internal_provider = _text(binding.get("internal_provider")).lower()
    if internal_provider in known:
        return internal_provider
    for env_key in ("EMPYRALIS_DEFAULT_CLOUD_COMPUTER_PROVIDER", "EMPYRALIS_DEFAULT_VIRTUAL_COMPUTER_PROVIDER"):
        candidate = _text(os.environ.get(env_key)).lower()
        if candidate and candidate in known:
            return candidate
    return None


def _runtime_money_cents(value: Any) -> int:
    try:
        return int(round(float(value or 0) * 100))
    except Exception:
        return 0


def _deployed_virtual_runtime_service_payload(
    *,
    operation: str,
    deployed_agent: Dict[str, Any],
    config: Any,
    privacy_snapshot: Dict[str, Any],
    computer_safety_snapshot: Dict[str, Any],
    **extra: Any,
) -> Dict[str, Any]:
    automation = config.computer_automation
    recording_snapshot = _coerce_dict(computer_safety_snapshot.get("screenshot_session_recording"))
    allowed_domains = [
        str(item).strip()
        for item in _coerce_list(computer_safety_snapshot.get("domain_allowlist"))
        if str(item).strip()
    ] or list(automation.allowed_domains or [])
    daily_budget = (
        automation.daily_budget_usd
        or automation.monthly_budget_usd
        or config.commerce_policy.monthly_cost_cap_usd
        or 0
    )
    payload: Dict[str, Any] = {
        "operation": operation,
        "tenant_id": _text(deployed_agent.get("tenant_id")),
        "workspace_id": _text(deployed_agent.get("workspace_id") or deployed_agent.get("owner_workspace_id")),
        "deployed_agent_id": _text(deployed_agent.get("id")),
        "studio_agent_mode": _text(config.studio_agent_mode).lower(),
        "runtime_target": _text(config.runtime_target),
        "runtime_choice": _runtime_choice_for_config(config),
        "runtime_provider_id": _runtime_provider_id_for_config(config) or "provider_policy_default",
        "runtime_profile_id": _text(config.runtime_profile_id),
        "privacy_contract_valid": _validate_privacy_contract_snapshot(privacy_snapshot),
        "computer_safety_contract_valid": _validate_computer_safety_contract_snapshot(
            computer_safety_snapshot,
            require_for_mode=_text(config.studio_agent_mode).lower() in COMPUTER_RUNTIME_MODES,
        ),
        "allowed_domain_count": len(allowed_domains),
        "daily_budget_usd_cents": _runtime_money_cents(daily_budget),
        "session_timeout_seconds": int(
            computer_safety_snapshot.get("session_timeout_seconds")
            or automation.idle_timeout_seconds
            or DEFAULT_IDLE_TIMEOUT_SECONDS
        ),
        "max_runtime_seconds": int(
            computer_safety_snapshot.get("max_runtime_seconds")
            or automation.max_session_runtime_seconds
            or DEFAULT_MAX_RUNTIME_SECONDS
        ),
        "session_recording_enabled": _session_recording_enabled(config),
        "session_recording_policy": _text(
            recording_snapshot.get("recording_policy") or automation.session_recording_policy
        ).lower(),
        "filesystem_scope": _text(computer_safety_snapshot.get("filesystem_default_access")) or "none",
        "terminal_command_policy": _text(computer_safety_snapshot.get("terminal_command_policy")) or "blocked",
        "sensitive_action_confirmation_required": bool(
            computer_safety_snapshot.get("sensitive_action_confirmation_required")
        ),
        "owner_approval_provided": True,
    }
    payload.update({key: value for key, value in extra.items() if value is not None})
    return payload


def _enforce_deployed_virtual_runtime_service_decision(
    *,
    operation: str,
    deployed_agent: Dict[str, Any],
    config: Any,
    privacy_snapshot: Dict[str, Any],
    computer_safety_snapshot: Dict[str, Any],
    **extra: Any,
) -> Dict[str, Any]:
    allowed_next_actions = {
        "build_policy_payload": {"build_deployed_agent_virtual_runtime_payload"},
        "execute_cloud_tool": {"execute_bound_runtime_tool_call"},
        "terminate_cloud_session": {"terminate_and_meter_runtime_session"},
        "terminate_self_hosted_session": {"terminate_and_meter_runtime_session"},
    }.get(str(operation or "").strip())
    payload = _deployed_virtual_runtime_service_payload(
        operation=operation,
        deployed_agent=deployed_agent,
        config=config,
        privacy_snapshot=privacy_snapshot,
        computer_safety_snapshot=computer_safety_snapshot,
        **extra,
    )
    try:
        decision = rust_runtime_kernel_client.run_runtime_kernel_enforced(
            "deployed-virtual-runtime-service-decision",
            payload,
        )
        if allowed_next_actions is not None:
            next_action = _text(decision.get("next_action"))
            if next_action not in allowed_next_actions:
                raise RuntimeError(f"unexpected_next_action:{next_action or 'missing'}")
        return decision
    except rust_runtime_kernel_client.RustKernelDecisionError as exc:
        reason = _text(exc.reason) or "rust_runtime_service_denied"
        raise RuntimeError(f"Rust deployed virtual-runtime service blocked {operation}: {reason}") from exc


def _enforce_deployed_virtual_runtime_decision(
    *,
    operation: str,
    deployed_agent: Dict[str, Any],
    config: Any,
    privacy_snapshot: Dict[str, Any],
    computer_safety_snapshot: Dict[str, Any],
    **extra: Any,
) -> Dict[str, Any]:
    allowed_next_actions = {
        "build_policy_payload": {"build_deployed_agent_virtual_runtime_payload"},
    }.get(str(operation or "").strip())
    automation = getattr(config, "computer_automation", None)
    allowed_domains = [
        str(item).strip()
        for item in _coerce_list(computer_safety_snapshot.get("domain_allowlist"))
        if str(item).strip()
    ] or list(getattr(automation, "allowed_domains", None) or [])
    payload = {
        "operation": operation,
        "tenant_id": _text(deployed_agent.get("tenant_id")),
        "workspace_id": _text(deployed_agent.get("workspace_id")),
        "deployed_agent_id": _text(deployed_agent.get("id")),
        "deployment_state": _text(deployed_agent.get("deployment_state")),
        "studio_agent_mode": config.studio_agent_mode,
        "privacy_contract_valid": bool(privacy_snapshot),
        "computer_safety_contract_valid": bool(computer_safety_snapshot),
        "domain_count": len(allowed_domains),
        "daily_budget_usd_cents": int(round(float(getattr(automation, "daily_budget_usd", 0) or 0) * 100)),
        "monthly_budget_usd_cents": int(round(float(getattr(automation, "monthly_budget_usd", 0) or 0) * 100)),
        "cost_cap_usd_cents": int(round(float(getattr(config.commerce_policy, "monthly_cost_cap_usd", 0) or 0) * 100)),
        "session_recording_policy": _text(
            _coerce_dict(computer_safety_snapshot.get("screenshot_session_recording")).get("recording_policy")
            or getattr(automation, "session_recording_policy", None)
        ).lower(),
        "kill_switch_active": bool(_coerce_dict(_coerce_dict(deployed_agent.get("metadata")).get("kill_switch")).get("active")),
        "workspace_emergency_stop_active": bool(
            _coerce_dict(_coerce_dict(deployed_agent.get("metadata")).get("workspace_emergency_stop")).get("active")
        ),
        **extra,
    }
    try:
        decision = rust_runtime_kernel_client.run_runtime_kernel_enforced(
            "deployed-virtual-runtime-decision",
            payload,
        )
        if allowed_next_actions is not None:
            next_action = _text(decision.get("next_action"))
            if next_action not in allowed_next_actions:
                raise RuntimeError(f"unexpected_next_action:{next_action or 'missing'}")
        return decision
    except rust_runtime_kernel_client.RustKernelDecisionError as exc:
        reason = _text(exc.reason) or "rust_virtual_runtime_denied"
        raise RuntimeError(f"Rust deployed virtual-runtime blocked {operation}: {reason}") from exc


def _enforce_runtime_binding_decision(**payload: Any) -> Dict[str, Any]:
    operation = _text(payload.get("operation")) or "ensure_runtime_session"
    raw_mode = _text(payload.get("studio_agent_mode")).lower()
    mode_aliases = {
        "cloud": "cloud_computer",
        "cloud_computer_agent": "cloud_computer",
        "sage_cloud_computer": "cloud_computer",
        "local_gateway": "my_computer",
        "local_companion": "my_computer",
        "my_computer_agent": "my_computer",
        "self_host_runtime": "self_hosted",
        "self_hosted_agent": "self_hosted",
    }
    if raw_mode in mode_aliases:
        payload["studio_agent_mode"] = mode_aliases[raw_mode]
    if not _text(payload.get("studio_agent_mode")):
        if operation == "ensure_cloud_runtime_session":
            payload["studio_agent_mode"] = "cloud_computer"
        elif operation == "ensure_self_hosted_runtime_session":
            payload["studio_agent_mode"] = "self_hosted"
        elif operation == "ensure_my_computer_runtime_session":
            payload["studio_agent_mode"] = "my_computer"
    try:
        decision = rust_runtime_kernel_client.run_runtime_kernel_enforced(
            "runtime-binding-decision",
            payload,
        )
        next_action = _text(decision.get("next_action"))
        allowed_next_actions = {
            "ensure_cloud_runtime_session": {
                "create_cloud_runtime_session",
                "create_local_gateway_runtime_session",
                "reuse_runtime_session",
                "skip_runtime_binding",
            },
            "ensure_self_hosted_runtime_session": {
                "create_self_hosted_runtime_session",
                "reuse_runtime_session",
                "skip_self_hosted_binding",
            },
        }.get(operation)
        if allowed_next_actions is None:
            raise RuntimeError(f"unexpected runtime binding operation: {operation}")
        if next_action not in allowed_next_actions:
            raise RuntimeError(f"unexpected_next_action:{next_action or 'missing'}")
        return decision
    except rust_runtime_kernel_client.RustKernelDecisionError as exc:
        reason = _text(exc.reason) or "runtime_binding_denied"
        raise RuntimeError(f"Rust runtime binding blocked {operation}: {reason}") from exc


def _enforce_runtime_action_decision(**payload: Any) -> Dict[str, Any]:
    operation = _text(payload.get("operation")) or "execute_runtime_action"
    try:
        decision = rust_runtime_kernel_client.run_runtime_kernel_enforced(
            "runtime-action-decision",
            payload,
        )
        next_action = _text(decision.get("next_action"))
        runtime_binding = _text(payload.get("runtime_session_binding")).lower()
        allowed_next_actions = {
            _RUNTIME_BINDING_CLOUD: {
                "execute_cloud_runtime_action",
                "skip_runtime_action",
            },
            _RUNTIME_BINDING_SELF_HOSTED: {
                "execute_self_hosted_runtime_action",
                "skip_runtime_action",
            },
        }.get(runtime_binding)
        if allowed_next_actions is None:
            raise RuntimeError(f"unexpected runtime action binding: {runtime_binding or 'missing'}")
        if next_action not in allowed_next_actions:
            raise RuntimeError(f"unexpected_next_action:{next_action or 'missing'}")
        return decision
    except rust_runtime_kernel_client.RustKernelDecisionError as exc:
        reason = _text(exc.reason) or "runtime_action_denied"
        raise RuntimeError(f"Rust runtime action blocked {operation}: {reason}") from exc


def _runtime_binding_metadata_updates(
    decision: Dict[str, Any],
    *,
    fallback_binding: str,
    fallback_session_id: str,
    extra: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    metadata = {
        "runtime_session_id": _text(decision.get("runtime_session_id")) or fallback_session_id,
        "runtime_session_binding": _text(decision.get("runtime_session_binding")).lower() or fallback_binding,
    }
    metadata.update({key: value for key, value in dict(extra or {}).items() if value is not None})
    return metadata


def build_deployed_agent_virtual_runtime_payload(
    deployed_agent: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    record = dict(deployed_agent or {})
    config = deployed_agent_config_schema.deployed_agent_config_from_record(record)
    mode = _text(config.studio_agent_mode).lower()
    if mode == deployed_agent_runtime_contract_service.STUDIO_AGENT_MODE_SELF_HOSTED:
        raise ValueError(
            "self_hosted_agent cannot use cloud runtime payload builder; dedicated self-hosted runtime binding is required."
    )
    privacy_snapshot, computer_safety_snapshot = _validated_contract_snapshots(record, config)
    _validate_required_runtime_policy_fields(config, computer_safety_snapshot)
    _enforce_deployed_virtual_runtime_service_decision(
        operation="build_policy_payload",
        deployed_agent=record,
        config=config,
        privacy_snapshot=privacy_snapshot,
        computer_safety_snapshot=computer_safety_snapshot,
    )
    _enforce_deployed_virtual_runtime_decision(
        operation="build_policy_payload",
        deployed_agent=record,
        config=config,
        privacy_snapshot=privacy_snapshot,
        computer_safety_snapshot=computer_safety_snapshot,
    )
    automation = config.computer_automation
    runtime_choice = _runtime_choice_for_config(config)
    allowed_domains = [str(item).strip() for item in _coerce_list(computer_safety_snapshot.get("domain_allowlist")) if str(item).strip()] or list(automation.allowed_domains or [])
    concurrency_limit = _positive_int(automation.max_concurrent_sessions, 1)
    idle_timeout_seconds = _positive_int(
        computer_safety_snapshot.get("session_timeout_seconds") or automation.idle_timeout_seconds,
        DEFAULT_IDLE_TIMEOUT_SECONDS,
    )
    max_runtime_seconds = _positive_int(
        computer_safety_snapshot.get("max_runtime_seconds") or automation.max_session_runtime_seconds,
        DEFAULT_MAX_RUNTIME_SECONDS,
    )
    per_session_cost_limit = _positive_float(
        automation.daily_budget_usd
        or automation.monthly_budget_usd
        or config.commerce_policy.monthly_cost_cap_usd,
        DEFAULT_PROVIDER_COST_ESTIMATE,
    )
    workspace_monthly_budget_limit = _positive_float(
        config.commerce_policy.monthly_cost_cap_usd
        or automation.monthly_budget_usd
        or automation.daily_budget_usd,
        per_session_cost_limit,
    )
    agent_monthly_budget_limit = _positive_float(
        automation.monthly_budget_usd
        or config.commerce_policy.monthly_cost_cap_usd
        or automation.daily_budget_usd,
        per_session_cost_limit,
    )
    long_task_cost_estimate_threshold = min(
        float(agent_monthly_budget_limit),
        max(float(per_session_cost_limit) * 0.25, 1.0),
    )
    recording_policy = _text(_coerce_dict(computer_safety_snapshot.get("screenshot_session_recording")).get("recording_policy")).lower()
    recording_retention_seconds = _recording_retention_seconds(config)
    risk_policy = _risk_policy_for_runtime_access_mode(
        runtime_access_mode=automation.runtime_access_mode,
        requires_owner_approval=automation.requires_owner_approval,
    )
    network_policy = {
        "allowed_domains": allowed_domains,
        "denied_domains": [],
        "detect_phishing_pages": True,
        "block_auto_download_without_approval": True,
        "enforce_domain_bound_credential_injection": True,
    }
    enterprise_controls = {
        "workspace_admin_policy": {
            "require_workspace_admin_for_runtime": False,
        },
        "domain_allowlist": allowed_domains,
        "data_residency": _text(_coerce_dict(privacy_snapshot.get("where_it_runs")).get("trust_zone")) or "unspecified",
        "disable_public_internet_mode": bool(allowed_domains),
        "allow_audit_export": bool(_coerce_dict(privacy_snapshot.get("audit_log")).get("available")),
        "sso_required": False,
        "per_team_approval_roles": list(DEFAULT_APPROVAL_ROLES),
        "session_recording_retention_seconds": int(recording_retention_seconds),
    }
    session_recording = {
        "enabled": recording_policy not in DISABLED_RECORDING_POLICIES,
        "provider": "deployed_agent_runtime_policy",
        "policy": recording_policy or _text(automation.session_recording_policy).lower() or "metadata_only",
        "screenshots_enabled": bool(
            _coerce_dict(computer_safety_snapshot.get("screenshot_session_recording")).get("screenshots_enabled")
        ),
    }
    policy_metadata = {
        "source": "deployed_agent_virtual_runtime_service",
        "policy_mode": "strict",
        "deployed_agent_id": _text(record.get("id")) or None,
        "backing_install_id": _text(record.get("backing_install_id")) or None,
        "studio_agent_mode": config.studio_agent_mode,
        "runtime_target": config.runtime_target,
        "runtime_profile_id": _text(config.runtime_profile_id) or None,
        "provider": _text(config.provider) or None,
        "model": _text(config.model) or None,
        "memory_enabled": bool(config.memory_policy.memory_enabled),
        "enabled_tools": list(config.tool_policy.enabled_tools or []),
        "privacy_contract_snapshot_version": privacy_snapshot.get("schema_version"),
        "computer_safety_contract_snapshot_version": computer_safety_snapshot.get("schema_version"),
        "privacy_contract_accepted_at": privacy_snapshot.get("accepted_at"),
        "virtual_computer_risk_policy": risk_policy,
        "computer_action_policy": {
            "owner_machine_trusted": False,
            "trusted_owner_machine_ids": [],
        },
    }
    cost_quota = {
        "cost_unit": "usd",
        "per_session_cost_limit": float(per_session_cost_limit),
        "workspace_budget_limit": float(workspace_monthly_budget_limit),
        "workspace_monthly_budget_limit": float(workspace_monthly_budget_limit),
        "agent_monthly_budget_limit": float(agent_monthly_budget_limit),
        "per_session_runtime_seconds": int(max_runtime_seconds),
        "agent_runtime_budget_seconds": int(max_runtime_seconds * max(concurrency_limit, 1)),
        "provider_concurrency_limit": int(concurrency_limit),
        "workspace_concurrency_limit": int(concurrency_limit),
        "agent_concurrency_limit": int(concurrency_limit),
        "idle_timeout_seconds": int(idle_timeout_seconds),
        "estimated_create_cost": float(DEFAULT_PROVIDER_COST_ESTIMATE),
        "estimated_action_cost": float(DEFAULT_PROVIDER_COST_ESTIMATE),
        "long_task_cost_estimate_threshold": float(long_task_cost_estimate_threshold),
        "budget_threshold_ratio": 0.90,
        "budget_threshold_action": "pause",
    }
    runtime_quota = {
        "max_concurrent_sessions": int(concurrency_limit),
        "provider_concurrency_limit": int(concurrency_limit),
        "workspace_concurrency_limit": int(concurrency_limit),
        "agent_concurrency_limit": int(concurrency_limit),
        "max_session_runtime_seconds": int(max_runtime_seconds),
        "idle_timeout_seconds": int(idle_timeout_seconds),
    }
    payload = {
        "runtime_choice": runtime_choice,
        "runtime_provider_id": _runtime_provider_id_for_config(config),
        "computer_automation": automation.model_dump(exclude_none=True),
        "cost_quota": cost_quota,
        "runtime_quota": runtime_quota,
        "network_policy": network_policy,
        "network_browser_policy": dict(network_policy),
        "enterprise_controls": enterprise_controls,
        "runtime_kill_state": _runtime_kill_state(record),
        "session_recording": session_recording,
        "session_recording_enabled": bool(session_recording["enabled"]),
        "policy_metadata": policy_metadata,
    }
    return payload


def get_runtime_registry() -> virtual_computer_runtime.VirtualComputerRuntimeRegistry:
    return _RUNTIME_REGISTRY


def _cloud_runtime_tool_action(
    *,
    connector_id: Any,
    action_id: Any,
    argument_payload: Any,
) -> tuple[str, Dict[str, Any]]:
    connector = _text(connector_id).lower()
    action = _text(action_id).lower()
    arguments = _coerce_dict(argument_payload)
    if connector == "browser":
        if action in {"navigate", "new_tab"}:
            url = _text(arguments.get("url"))
            if not url:
                raise RuntimeError(f"browser__{action} requires url.")
            return "open_url", {"url": url}
        if action == "download_file":
            url = _text(arguments.get("url"))
            if not url:
                raise RuntimeError("browser__download_file requires url.")
            return "download_artifact", {"url": url}
        if action == "screenshot":
            if _text(arguments.get("selector")):
                raise RuntimeError("Cloud Computer browser__screenshot does not support selector targeting.")
            return "screenshot", {}
        raise RuntimeError(
            f"Cloud Computer runtime does not support browser__{action}. Use runtime-safe computer/browser actions instead."
        )
    if connector == "computer":
        if action == "click":
            if arguments.get("x") is None or arguments.get("y") is None:
                raise RuntimeError("Cloud Computer computer__click requires x and y coordinates.")
            return "click", {"x": arguments.get("x"), "y": arguments.get("y")}
        if action == "type":
            text = str(arguments.get("text") or arguments.get("input") or "")
            if not text:
                raise RuntimeError("Cloud Computer computer__type requires text.")
            return "type", {"text": text}
        raise RuntimeError(
            f"Cloud Computer runtime does not support computer__{action}. Use supported runtime-safe actions only."
        )
    if connector == "shell" and action == "exec":
        command = str(arguments.get("command") or "")
        if not command.strip():
            raise RuntimeError("shell_exec requires command.")
        return "run_command", {"command": command}
    raise RuntimeError(f"Cloud Computer runtime does not support {connector}__{action}.")


def _cloud_runtime_result_text(
    *,
    connector_id: Any,
    action_id: Any,
    response: Dict[str, Any],
) -> str:
    connector = _text(connector_id).lower()
    action = _text(action_id).lower()
    if connector == "browser" and action == "screenshot":
        artifacts = response.get("artifacts") if isinstance(response.get("artifacts"), list) else []
        if artifacts:
            return json.dumps({"artifacts": artifacts, "action_result": response.get("action_result")}, ensure_ascii=False)
    return json.dumps(response, ensure_ascii=False)


def _runtime_artifact_type(artifact: Dict[str, Any]) -> str:
    return _text(artifact.get("artifact_type") or artifact.get("type") or artifact.get("kind")).lower()


def _runtime_artifacts_from_response(response: Dict[str, Any]) -> List[Dict[str, Any]]:
    artifacts: List[Dict[str, Any]] = []
    seen: set[str] = set()
    for candidate in [response.get("artifact"), *list(response.get("artifacts") or [])]:
        if not isinstance(candidate, dict):
            continue
        artifact_id = _text(candidate.get("artifact_id")) or f"artifact_{len(artifacts)}"
        if artifact_id in seen:
            continue
        seen.add(artifact_id)
        artifacts.append(dict(candidate))
    return artifacts


def _without_inline_artifact_content(artifact: Dict[str, Any]) -> Dict[str, Any]:
    safe = dict(artifact or {})
    safe.pop("content_base64", None)
    safe.pop("content", None)
    safe.pop("bytes", None)
    return safe


async def _canonicalize_cloud_runtime_artifacts(
    *,
    runtime: Any,
    deployed_agent: Dict[str, Any],
    response: Dict[str, Any],
    runtime_session_id: str,
    run_id: str,
    tenant_id: str,
    workspace_id: str,
    runtime_action: str,
) -> List[Dict[str, Any]]:
    canonical_artifacts: List[Dict[str, Any]] = []
    for artifact in _runtime_artifacts_from_response(response):
        artifact_id = _text(artifact.get("artifact_id"))
        artifact_type = _runtime_artifact_type(artifact)
        canonical = _without_inline_artifact_content(artifact)
        if artifact_id and artifact_type == "screenshot":
            try:
                collected = await runtime.collect_artifact(
                    {
                        "session_id": runtime_session_id,
                        "browser_session_id": runtime_session_id,
                        "artifact_id": artifact_id,
                        "include_content_base64": True,
                    }
                )
                collected_artifact = _coerce_dict(collected.get("artifact"))
                content_base64 = _text(collected_artifact.get("content_base64"))
                if content_base64:
                    content = base64.b64decode(content_base64, validate=True)
                    file_name = (
                        _text(collected_artifact.get("file_name"))
                        or _text(canonical.get("file_name"))
                        or f"{artifact_id}.png"
                    )
                    content_type = (
                        _text(collected_artifact.get("content_type"))
                        or _text(canonical.get("content_type"))
                        or "image/png"
                    )
                    metadata = {
                        "source": "cloud_computer_runtime",
                        "runtime_session_id": runtime_session_id,
                        "runtime_action": runtime_action,
                        "provider_id": _text(response.get("provider_id") or canonical.get("provider_id")) or None,
                        "runtime_kind": _text(response.get("runtime_kind")) or None,
                        "remote_path": _text(collected_artifact.get("remote_path") or collected_artifact.get("path") or canonical.get("remote_path") or canonical.get("path")) or None,
                        "url": _text(collected_artifact.get("url") or canonical.get("url")) or None,
                        "title": _text(collected_artifact.get("title") or canonical.get("title")) or None,
                        "proof_envelope": _coerce_dict(
                            collected_artifact.get("proof_envelope") or canonical.get("proof_envelope")
                        ),
                    }
                    record = artifact_service.store_artifact_bytes(
                        content,
                        run_id=run_id,
                        kind="screenshot",
                        tenant_id=tenant_id,
                        workspace_id=workspace_id,
                        agent_install_id=_text(deployed_agent.get("backing_install_id")) or None,
                        file_name=file_name,
                        label=file_name,
                        content_type=content_type,
                        artifact_id=artifact_id,
                        metadata=metadata,
                    )
                    canonical_payload = record.as_payload()
                    proof_envelope = {
                        **_coerce_dict(canonical.get("proof_envelope")),
                        "canonical_artifact_id": canonical_payload.get("artifact_id"),
                        "uri": canonical_payload.get("uri"),
                        "byte_size": canonical_payload.get("byte_size"),
                    }
                    canonical = {
                        **canonical,
                        **canonical_payload,
                        "artifact_type": artifact_type,
                        "type": artifact_type,
                        "remote_path": metadata.get("remote_path"),
                        "url": metadata.get("url"),
                        "title": metadata.get("title"),
                        "proof_envelope": proof_envelope,
                    }
            except Exception as exc:
                canonical["canonicalization_error"] = _text(exc) or "artifact canonicalization failed"
        canonical_artifacts.append(_without_inline_artifact_content(canonical))
    return canonical_artifacts


def _latest_cloud_computer_proof(
    *,
    response: Dict[str, Any],
    runtime_session_id: str,
    artifacts: List[Dict[str, Any]],
) -> Optional[Dict[str, Any]]:
    latest = next(
        (item for item in reversed(list(artifacts or [])) if _runtime_artifact_type(item) == "screenshot"),
        None,
    )
    if not isinstance(latest, dict):
        return None
    streaming_ui = _coerce_dict(response.get("streaming_ui"))
    current_context = _coerce_dict(streaming_ui.get("current_context"))
    return {
        "runtime_session_id": runtime_session_id,
        "provider_id": _text(response.get("provider_id") or latest.get("provider_id")) or None,
        "runtime_kind": _text(response.get("runtime_kind")) or None,
        "state": _text(response.get("state")) or None,
        "status": _text(response.get("status")) or None,
        "current_url": _text(latest.get("url") or current_context.get("url") or streaming_ui.get("current_url")) or None,
        "app_title": _text(latest.get("title") or current_context.get("app_title") or streaming_ui.get("app_title")) or None,
        "artifact_id": _text(latest.get("artifact_id")) or None,
        "artifact_uri": _text(latest.get("uri") or latest.get("uri_or_path")) or None,
        "content_type": _text(latest.get("content_type") or latest.get("mime_type")) or None,
        "byte_size": latest.get("byte_size") if isinstance(latest.get("byte_size"), int) else None,
        "captured_at_epoch": latest.get("created_at_epoch"),
        "proof_envelope": _coerce_dict(latest.get("proof_envelope")),
    }


def _bound_runtime_metadata_from_session_ctx(session_ctx: Any) -> Dict[str, Any]:
    payload = _session_ctx_payload(session_ctx)
    turn_request = _session_ctx_payload(payload.get("agent_turn_request"))
    turn_context_hints = _session_ctx_payload(turn_request.get("context_hints"))
    turn_metadata = _coerce_dict(turn_context_hints.get("metadata"))
    turn_session = _coerce_dict(turn_context_hints.get("session"))
    turn_session_metadata = _coerce_dict(turn_session.get("metadata"))
    merged = dict(payload)
    merged.update({key: value for key, value in turn_session_metadata.items() if value is not None})
    merged.update({key: value for key, value in turn_metadata.items() if value is not None})
    return merged


def has_cloud_runtime_session_binding(session_ctx: Any) -> bool:
    metadata = _bound_runtime_metadata_from_session_ctx(session_ctx)
    return _text(metadata.get("runtime_session_binding")).lower() == _RUNTIME_BINDING_CLOUD


def has_self_hosted_runtime_session_binding(session_ctx: Any) -> bool:
    metadata = _bound_runtime_metadata_from_session_ctx(session_ctx)
    return _text(metadata.get("runtime_session_binding")).lower() == _RUNTIME_BINDING_SELF_HOSTED


def _forbidden_policy_override_keys(argument_payload: Any) -> list[str]:
    payload = _coerce_dict(argument_payload)
    return sorted(key for key in _FORBIDDEN_POLICY_OVERRIDE_KEYS if key in payload)


def _runtime_connector_action(connector_id: Any, action_id: Any) -> bool:
    connector = _text(connector_id).lower()
    action = _text(action_id).lower()
    if connector in {"browser", "computer"}:
        return True
    return connector == "shell" and action == "exec"


def _self_hosted_runtime_binding_contract(deployed_agent: Dict[str, Any]) -> Dict[str, Any]:
    metadata = _coerce_dict(deployed_agent.get("metadata"))
    binding = _coerce_dict(metadata.get("self_hosted_runtime_binding"))
    if not binding:
        raise ValueError("self_hosted_agent requires persisted self_hosted_runtime_binding metadata contract.")
    if _text(binding.get("runtime_target")).lower() != "self_host_runtime":
        raise ValueError("self_hosted_agent binding contract must target self_host_runtime.")
    if not _text(binding.get("runtime_node_id")):
        raise ValueError("self_hosted_agent binding contract is missing runtime_node_id.")
    if not _text(binding.get("runtime_profile_id")):
        raise ValueError("self_hosted_agent binding contract is missing runtime_profile_id.")
    if not _text(binding.get("runtime_attachment_id")):
        raise ValueError("self_hosted_agent binding contract is missing runtime_attachment_id.")
    if not _text(binding.get("workspace_id")):
        raise ValueError("self_hosted_agent binding contract is missing workspace_id.")
    return binding


def build_deployed_agent_self_hosted_runtime_payload(
    deployed_agent: Optional[Dict[str, Any]],
    *,
    runtime_attachment: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    record = dict(deployed_agent or {})
    config = deployed_agent_config_schema.deployed_agent_config_from_record(record)
    mode = _text(config.studio_agent_mode).lower()
    if mode != deployed_agent_runtime_contract_service.STUDIO_AGENT_MODE_SELF_HOSTED:
        raise ValueError("self_hosted runtime payload builder requires self_hosted_agent studio mode.")
    binding = _self_hosted_runtime_binding_contract(record)
    attachment = dict(runtime_attachment or {})
    runtime_choice = _runtime_choice_for_config(config) or "virtual_code_sandbox"
    provider_binding = _coerce_dict(_coerce_dict(config.runtime_supply).get("provider_binding"))
    provider_id = (
        _text(provider_binding.get("internal_provider"))
        or virtual_computer_runtime.PROVIDER_ID_DOCKER_KUBERNETES
    )
    filesystem_scope = _text(binding.get("filesystem_scope")).lower() or "none"
    if filesystem_scope not in _SELF_HOSTED_ALLOWED_FILESYSTEM_SCOPES:
        raise ValueError("self_hosted_agent binding contract has unsupported filesystem_scope.")
    domain_allowlist = [str(item).strip() for item in _coerce_list(binding.get("domain_allowlist")) if str(item).strip()]
    if not domain_allowlist:
        raise ValueError("self_hosted_agent binding contract requires a non-empty domain_allowlist.")
    approval_policy = _coerce_dict(binding.get("approval_policy"))
    quota_policy = _coerce_dict(binding.get("quota_policy"))
    recording_policy = _text(config.computer_automation.session_recording_policy).lower() or "metadata_only"
    if recording_policy in DISABLED_RECORDING_POLICIES:
        raise ValueError("self_hosted_agent requires session recording policy to be enabled.")
    max_concurrent_sessions = max(
        1,
        int(
            quota_policy.get("max_concurrent_sessions")
            or config.computer_automation.max_concurrent_sessions
            or attachment.get("max_concurrent_sessions")
            or 1
        ),
    )
    max_session_runtime_seconds = max(
        60,
        int(
            quota_policy.get("max_session_runtime_seconds")
            or config.computer_automation.max_session_runtime_seconds
            or DEFAULT_MAX_RUNTIME_SECONDS
        ),
    )
    idle_timeout_seconds = max(
        60,
        int(
            quota_policy.get("idle_timeout_seconds")
            or config.computer_automation.idle_timeout_seconds
            or DEFAULT_IDLE_TIMEOUT_SECONDS
        ),
    )
    daily_budget = _positive_float(
        quota_policy.get("daily_budget_usd") or config.computer_automation.daily_budget_usd,
        DEFAULT_PROVIDER_COST_ESTIMATE,
    )
    monthly_budget = _positive_float(
        quota_policy.get("monthly_budget_usd")
        or config.computer_automation.monthly_budget_usd
        or config.commerce_policy.monthly_cost_cap_usd,
        daily_budget,
    )
    monthly_cap = _positive_float(
        quota_policy.get("monthly_cost_cap_usd")
        or config.commerce_policy.monthly_cost_cap_usd
        or monthly_budget,
        monthly_budget,
    )
    required_owner_approval_actions = [
        _text(item)
        for item in _coerce_list(approval_policy.get("required_owner_approval_actions"))
        if _text(item)
    ] or list(config.computer_automation.required_owner_approval_actions or [])
    per_team_roles = list(DEFAULT_APPROVAL_ROLES)
    root_policy = _coerce_dict(attachment.get("root_policy"))
    if isinstance(root_policy.get("approval_roles"), list):
        parsed_roles = [str(item).strip() for item in list(root_policy.get("approval_roles") or []) if str(item).strip()]
        if parsed_roles:
            per_team_roles = parsed_roles
    if filesystem_scope == "workspace_scoped":
        filesystem_quota_bytes = 1024 * 1024 * 1024
    elif filesystem_scope == "session_scoped":
        filesystem_quota_bytes = 256 * 1024 * 1024
    else:
        filesystem_quota_bytes = 16 * 1024 * 1024
    enterprise_domain_allowlist = list(domain_allowlist)
    if isinstance(root_policy.get("domain_allowlist"), list):
        for item in list(root_policy.get("domain_allowlist") or []):
            token = str(item).strip()
            if token and token not in enterprise_domain_allowlist:
                enterprise_domain_allowlist.append(token)
    enterprise_domain_allowlist = [str(item).strip() for item in enterprise_domain_allowlist if str(item).strip()]
    approved_working_directories = [
        str(item).strip()
        for item in _coerce_list(root_policy.get("approved_working_directories"))
        if str(item).strip()
    ]
    if not approved_working_directories:
        workspace_root = _text(root_policy.get("workspace_root")) or "/workspace"
        approved_working_directories = [workspace_root]
    allow_software_install = bool(root_policy.get("allow_software_install"))
    allow_downloads = bool(root_policy.get("allow_downloads"))
    allow_workspace_scoped_filesystem = bool(root_policy.get("allow_workspace_scoped_filesystem"))
    terminal_command_policy = (
        _text(approval_policy.get("terminal_command_policy")).lower()
        or _text(config.computer_automation.terminal_command_policy).lower()
        or "blocked"
    )
    terminal_command_allowlist = [
        str(item).strip()
        for item in _coerce_list(root_policy.get("terminal_command_allowlist"))
        if str(item).strip()
    ]
    payload = {
        "runtime_choice": runtime_choice,
        "runtime_provider_id": provider_id,
        "runtime_node_id": _text(binding.get("runtime_node_id")),
        "runtime_profile_id": _text(binding.get("runtime_profile_id")),
        "runtime_attachment_id": _text(binding.get("runtime_attachment_id")),
        "workspace_id": _text(binding.get("workspace_id")),
        "domain_allowlist": list(domain_allowlist),
        "allowed_capabilities": list(_coerce_list(binding.get("allowed_capabilities"))),
        "computer_automation": config.computer_automation.model_dump(exclude_none=True),
        "isolation_profile": {
            "filesystem_quota_bytes": int(filesystem_quota_bytes),
            "file_transfer_enabled": filesystem_scope != "none",
            "allow_private_lan": False,
            "block_metadata_endpoints": True,
            "kill_switch_enabled": True,
        },
        "network_browser_policy": {
            "allowed_domains": list(enterprise_domain_allowlist),
            "denied_domains": [],
            "detect_phishing_pages": True,
            "block_auto_download_without_approval": True,
            "enforce_domain_bound_credential_injection": True,
        },
        "risk_policy": _risk_policy_for_runtime_access_mode(
            runtime_access_mode=approval_policy.get("runtime_access_mode"),
            requires_owner_approval=approval_policy.get("requires_owner_approval", True),
        ),
        "runtime_quota": {
            "max_concurrent_sessions": int(max_concurrent_sessions),
            "provider_concurrency_limit": int(max_concurrent_sessions),
            "workspace_concurrency_limit": int(max_concurrent_sessions),
            "agent_concurrency_limit": int(max_concurrent_sessions),
            "max_session_runtime_seconds": int(max_session_runtime_seconds),
            "idle_timeout_seconds": int(idle_timeout_seconds),
        },
        "cost_quota": {
            "per_session_cost_limit": float(daily_budget),
            "workspace_monthly_budget_limit": float(monthly_cap),
            "agent_monthly_budget_limit": float(monthly_budget),
            "agent_runtime_budget_seconds": float(max_session_runtime_seconds),
            "provider_concurrency_limit": int(max_concurrent_sessions),
            "workspace_concurrency_limit": int(max_concurrent_sessions),
            "agent_concurrency_limit": int(max_concurrent_sessions),
            "estimated_create_cost": float(daily_budget),
            "estimated_action_cost": max(float(daily_budget) / 100.0, 0.01),
            "long_task_cost_estimate_threshold": max(float(daily_budget) / 10.0, 1.0),
        },
        "session_recording_enabled": True,
        "session_recording": {
            "enabled": True,
            "provider": "self_hosted_runtime_policy",
            "policy": recording_policy,
        },
        "enterprise_controls": {
            "workspace_admin_policy": {
                "require_workspace_admin_for_runtime": False,
            },
            "domain_allowlist": list(enterprise_domain_allowlist),
            "disable_public_internet_mode": True,
            "allow_audit_export": True,
            "sso_required": False,
            "per_team_approval_roles": list(per_team_roles),
            "session_recording_retention_seconds": int(DEFAULT_RECORDING_RETENTION_SECONDS),
        },
        "local_sandbox_policy": {
            "enabled": True,
            "approved_working_directories": list(approved_working_directories),
            "allow_host_env_inheritance": False,
            "filesystem_scope": filesystem_scope,
            "allow_workspace_scoped_filesystem": bool(allow_workspace_scoped_filesystem),
            "terminal_command_policy": terminal_command_policy,
            "terminal_command_allowlist": list(terminal_command_allowlist),
            "allow_software_install": bool(allow_software_install),
            "allow_downloads": bool(allow_downloads),
            "session_timeout_seconds": int(idle_timeout_seconds),
            "max_runtime_seconds": int(max_session_runtime_seconds),
            "recording_required": True,
            "emergency_stop_enabled": True,
        },
        "runtime_kill_state": _runtime_kill_state(record),
        "policy_metadata": {
            "source": "deployed_agent_virtual_runtime_service",
            "policy_mode": "strict",
            "deployed_agent_id": _text(record.get("id")) or None,
            "backing_install_id": _text(record.get("backing_install_id")) or None,
            "studio_agent_mode": config.studio_agent_mode,
            "runtime_target": config.runtime_target,
            "runtime_profile_id": _text(binding.get("runtime_profile_id")),
            "runtime_attachment_id": _text(binding.get("runtime_attachment_id")),
            "runtime_node_id": _text(binding.get("runtime_node_id")),
            "filesystem_scope": filesystem_scope,
            "required_owner_approval_actions": list(required_owner_approval_actions),
            "approval_policy": dict(approval_policy),
            "local_sandbox_policy": {
                "enabled": True,
                "approved_working_directories": list(approved_working_directories),
                "allow_host_env_inheritance": False,
                "filesystem_scope": filesystem_scope,
                "allow_workspace_scoped_filesystem": bool(allow_workspace_scoped_filesystem),
                "terminal_command_policy": terminal_command_policy,
                "terminal_command_allowlist": list(terminal_command_allowlist),
                "allow_software_install": bool(allow_software_install),
                "allow_downloads": bool(allow_downloads),
                "session_timeout_seconds": int(idle_timeout_seconds),
                "max_runtime_seconds": int(max_session_runtime_seconds),
                "recording_required": True,
                "emergency_stop_enabled": True,
            },
            "self_hosted_runtime_binding": dict(binding),
        },
    }
    return payload


async def ensure_cloud_runtime_session_binding(
    *,
    deployed_agent_id: Any,
    tenant_id: Any,
    workspace_id: Any,
    session_id: Any,
    thread_id: Any,
    channel: Any,
    actor: Any,
    session_metadata: Any = None,
    turn_metadata: Any = None,
    machine_target: Any = None,
) -> Optional[Dict[str, Any]]:
    resolved_deployed_agent_id = _text(deployed_agent_id)
    resolved_session_id = _text(session_id)
    resolved_workspace_id = _text(workspace_id)
    resolved_tenant_id = _text(tenant_id)
    if not resolved_deployed_agent_id or not resolved_session_id or not resolved_workspace_id or not resolved_tenant_id:
        return None

    deployed_agent = await control_plane_repository.get_deployed_agent_by_id(
        resolved_deployed_agent_id,
        tenant_id=resolved_tenant_id,
        owner_workspace_id=resolved_workspace_id,
    )
    if not isinstance(deployed_agent, dict):
        raise ValueError("Deployed agent not found for runtime session binding.")
    config = deployed_agent_config_schema.deployed_agent_config_from_record(deployed_agent)
    mode = _text(config.studio_agent_mode).lower()
    if mode == deployed_agent_runtime_contract_service.STUDIO_AGENT_MODE_SELF_HOSTED:
        await _assert_self_hosted_runtime_gate(
            deployed_agent=deployed_agent,
            tenant_id=resolved_tenant_id,
            workspace_id=resolved_workspace_id,
        )
        raise ValueError(
            "self_hosted_agent requires dedicated self-hosted runtime session binding; cloud runtime binding is blocked."
        )
    if mode not in {
        deployed_agent_runtime_contract_service.STUDIO_AGENT_MODE_CLOUD_COMPUTER,
        deployed_agent_runtime_contract_service.STUDIO_AGENT_MODE_MY_COMPUTER,
    }:
        raise ValueError(
            f"Deployed agent studio_agent_mode '{mode or 'unknown'}' is not allowed to create runtime session binding."
        )
    runtime_session_binding = (
        _RUNTIME_BINDING_LOCAL_GATEWAY
        if mode == deployed_agent_runtime_contract_service.STUDIO_AGENT_MODE_MY_COMPUTER
        else _RUNTIME_BINDING_CLOUD
    )
    local_gateway_attachment: Dict[str, Any] = {}
    if mode == deployed_agent_runtime_contract_service.STUDIO_AGENT_MODE_MY_COMPUTER:
        local_gateway_attachment = await _assert_my_computer_runtime_gate(
            deployed_agent=deployed_agent,
            tenant_id=resolved_tenant_id,
            workspace_id=resolved_workspace_id,
        )

    current_session_metadata = _coerce_dict(session_metadata)
    current_turn_metadata = _coerce_dict(turn_metadata)
    existing_runtime_session_id = _text(
        current_session_metadata.get("runtime_session_id") or current_turn_metadata.get("runtime_session_id")
    )
    existing_runtime_binding = _text(
        current_session_metadata.get("runtime_session_binding") or current_turn_metadata.get("runtime_session_binding")
    ).lower()
    binding_decision = _enforce_runtime_binding_decision(
        operation="ensure_cloud_runtime_session",
        deployed_agent_id=resolved_deployed_agent_id,
        tenant_id=resolved_tenant_id,
        workspace_id=resolved_workspace_id,
        session_id=resolved_session_id,
        thread_id=_text(thread_id) or resolved_session_id,
        studio_agent_mode=mode,
        existing_runtime_session_id=existing_runtime_session_id or None,
        existing_runtime_binding=existing_runtime_binding or None,
        local_gateway_attachment_present=bool(local_gateway_attachment),
        local_gateway_online=bool(local_gateway_attachment),
        local_gateway_healthy=bool(local_gateway_attachment),
        local_gateway_revoked=False,
        runtime_attachment_id=_text(local_gateway_attachment.get("attachment_id")) or None,
        gateway_id=_text((_coerce_dict(local_gateway_attachment.get("gateway_identity"))).get("gateway_id") or local_gateway_attachment.get("runtime_id")) or None,
        runtime_node_id=_text(local_gateway_attachment.get("runtime_node_id") or local_gateway_attachment.get("runtime_id")) or None,
    )
    if _text(binding_decision.get("next_action")) == "reuse_runtime_session":
        return {
            "deployed_agent": None,
            "runtime_payload": None,
            "runtime_response": None,
            "metadata_updates": _runtime_binding_metadata_updates(
                binding_decision,
                fallback_binding=runtime_session_binding,
                fallback_session_id=existing_runtime_session_id,
            ),
        }

    payload = build_deployed_agent_virtual_runtime_payload(deployed_agent)
    payload.update(
        {
            "tenant_id": resolved_tenant_id,
            "workspace_id": resolved_workspace_id,
            "agent_id": resolved_deployed_agent_id,
            "run_id": resolved_session_id,
            "session_id": resolved_session_id,
            "thread_id": _text(thread_id) or resolved_session_id,
            "channel": _text(channel) or "channel",
            "actor": dict(actor) if isinstance(actor, dict) else {},
            "machine_target": _text(machine_target) or None,
            "app_title": _text(deployed_agent.get("name")) or (
                "My Computer Agent"
                if mode == deployed_agent_runtime_contract_service.STUDIO_AGENT_MODE_MY_COMPUTER
                else "Cloud Computer Agent"
            ),
            "metadata": {
                "deployed_agent_id": resolved_deployed_agent_id,
                "session_id": resolved_session_id,
                "thread_id": _text(thread_id) or resolved_session_id,
                "runtime_session_binding": runtime_session_binding,
            },
        }
    )
    if local_gateway_attachment:
        payload["local_gateway_attachment"] = {
            "attachment_id": _text(local_gateway_attachment.get("attachment_id")) or None,
            "gateway_id": _text(
                (_coerce_dict(local_gateway_attachment.get("gateway_identity"))).get("gateway_id")
                or local_gateway_attachment.get("runtime_id")
            )
            or None,
            "device_id": _text(
                (_coerce_dict(local_gateway_attachment.get("gateway_identity"))).get("device_id")
                or local_gateway_attachment.get("machine_id")
            )
            or None,
        }
    runtime = get_runtime_registry().resolve(
        payload.get("runtime_choice"),
        preferred_provider_id=payload.get("runtime_provider_id"),
    )
    response = await runtime.create_session(payload)
    browser_session = response.get("browser_session") if isinstance(response.get("browser_session"), dict) else {}
    runtime_session_id = _text(browser_session.get("browser_session_id") or response.get("session_id") or payload.get("session_id"))
    if not runtime_session_id:
        raise ValueError("Virtual runtime session did not return a session id.")

    metadata_updates = {
        "deployed_agent_id": resolved_deployed_agent_id,
        "runtime_session_id": runtime_session_id,
        "runtime_session_binding": runtime_session_binding,
        "runtime_session_started_at": _utc_now_iso(),
        "runtime_target": "local_companion" if mode == deployed_agent_runtime_contract_service.STUDIO_AGENT_MODE_MY_COMPUTER else "sage_cloud_computer",
        "runtime_max_session_seconds": int(_positive_int(payload.get("max_session_runtime_seconds"), default=DEFAULT_MAX_RUNTIME_SECONDS)),
        "runtime_choice": _text(payload.get("runtime_choice")) or None,
        "runtime_provider_id": _text(response.get("provider_id") or payload.get("runtime_provider_id")) or None,
        "runtime_provider_kind": _text(response.get("provider_kind")) or None,
        "virtual_runtime_bound": mode == deployed_agent_runtime_contract_service.STUDIO_AGENT_MODE_CLOUD_COMPUTER,
        "local_gateway_bound": mode == deployed_agent_runtime_contract_service.STUDIO_AGENT_MODE_MY_COMPUTER,
    }
    if local_gateway_attachment:
        metadata_updates.update(
            {
                "runtime_attachment_id": _text(local_gateway_attachment.get("attachment_id")) or None,
                "gateway_id": _text(
                    (_coerce_dict(local_gateway_attachment.get("gateway_identity"))).get("gateway_id")
                    or local_gateway_attachment.get("runtime_id")
                )
                or None,
            }
        )
    is_my_computer = mode == deployed_agent_runtime_contract_service.STUDIO_AGENT_MODE_MY_COMPUTER
    await _append_cloud_runtime_audit_event(
        deployed_agent=deployed_agent,
        tenant_id=resolved_tenant_id,
        workspace_id=resolved_workspace_id,
        action=(
            "deployed_agent_local_gateway_runtime_session_created"
            if is_my_computer
            else "deployed_agent_cloud_runtime_session_created"
        ),
        title="My Computer runtime session created" if is_my_computer else "Cloud Computer session created",
        summary=(
            "My Computer runtime session was created through a paired local Gateway."
            if is_my_computer
            else "Cloud Computer runtime session was created for deployed agent execution."
        ),
        payload={
            "session_id": resolved_session_id,
            "runtime_session_id": runtime_session_id,
            "runtime_choice": _text(payload.get("runtime_choice")) or None,
            "runtime_provider_id": _text(response.get("provider_id") or payload.get("runtime_provider_id")) or None,
        },
        metadata={
            "runtime_session_binding": runtime_session_binding,
            "runtime_provider_kind": _text(response.get("provider_kind")) or None,
        },
    )
    return {
        "deployed_agent": deployed_agent,
        "runtime_payload": payload,
        "runtime_response": response,
        "metadata_updates": {key: value for key, value in metadata_updates.items() if value is not None},
    }


async def ensure_self_hosted_runtime_session_binding(
    *,
    deployed_agent_id: Any,
    tenant_id: Any,
    workspace_id: Any,
    session_id: Any,
    thread_id: Any,
    channel: Any,
    actor: Any,
    session_metadata: Any = None,
    turn_metadata: Any = None,
    machine_target: Any = None,
) -> Optional[Dict[str, Any]]:
    resolved_deployed_agent_id = _text(deployed_agent_id)
    resolved_session_id = _text(session_id)
    resolved_workspace_id = _text(workspace_id)
    resolved_tenant_id = _text(tenant_id)
    if not resolved_deployed_agent_id or not resolved_session_id or not resolved_workspace_id or not resolved_tenant_id:
        return None

    deployed_agent = await control_plane_repository.get_deployed_agent_by_id(
        resolved_deployed_agent_id,
        tenant_id=resolved_tenant_id,
        owner_workspace_id=resolved_workspace_id,
    )
    if not isinstance(deployed_agent, dict):
        raise ValueError("Deployed agent not found for runtime session binding.")
    config = deployed_agent_config_schema.deployed_agent_config_from_record(deployed_agent)
    mode = _text(config.studio_agent_mode).lower()
    if mode != deployed_agent_runtime_contract_service.STUDIO_AGENT_MODE_SELF_HOSTED:
        return None

    binding_contract = _self_hosted_runtime_binding_contract(deployed_agent)
    if _text(binding_contract.get("workspace_id")) != resolved_workspace_id:
        raise ValueError("self_hosted_agent binding contract workspace_id does not match requested workspace scope.")

    current_session_metadata = _coerce_dict(session_metadata)
    current_turn_metadata = _coerce_dict(turn_metadata)
    existing_runtime_session_id = _text(
        current_session_metadata.get("runtime_session_id") or current_turn_metadata.get("runtime_session_id")
    )
    existing_runtime_binding = _text(
        current_session_metadata.get("runtime_session_binding") or current_turn_metadata.get("runtime_session_binding")
    ).lower()
    binding_decision = _enforce_runtime_binding_decision(
        operation="ensure_self_hosted_runtime_session",
        deployed_agent_id=resolved_deployed_agent_id,
        tenant_id=resolved_tenant_id,
        workspace_id=resolved_workspace_id,
        session_id=resolved_session_id,
        thread_id=_text(thread_id) or resolved_session_id,
        studio_agent_mode=mode,
        existing_runtime_session_id=existing_runtime_session_id or None,
        existing_runtime_binding=existing_runtime_binding or None,
        self_hosted_binding_present=bool(binding_contract),
        runtime_node_id=_text(binding_contract.get("runtime_node_id")) or None,
        runtime_profile_id=_text(binding_contract.get("runtime_profile_id")) or None,
        runtime_attachment_id=_text(binding_contract.get("runtime_attachment_id")) or None,
        binding_workspace_id=_text(binding_contract.get("workspace_id")) or None,
        self_hosted_node_registered=True,
        self_hosted_node_gate_passed=True,
        agent_allowed_on_node=True,
        active_sessions=0,
        max_concurrent_sessions=0,
        filesystem_scope=_text(binding_contract.get("filesystem_scope")) or "none",
        workspace_scoped_filesystem_allowed=_text(binding_contract.get("filesystem_scope")).lower() != "workspace_scoped"
        or _text(binding_contract.get("workspace_id")) == resolved_workspace_id,
        domain_allowlist_present=bool(
            _coerce_list(binding_contract.get("domain_allowlist"))
            or _coerce_list(binding_contract.get("allowed_domains"))
        ),
        session_recording_enabled=bool(_coerce_dict(binding_contract.get("approval_policy")) or True),
    )
    if _text(binding_decision.get("next_action")) == "reuse_runtime_session":
        return {
            "deployed_agent": None,
            "runtime_payload": None,
            "runtime_response": None,
            "metadata_updates": _runtime_binding_metadata_updates(
                binding_decision,
                fallback_binding=_RUNTIME_BINDING_SELF_HOSTED,
                fallback_session_id=existing_runtime_session_id,
                extra={
                    "runtime_node_id": _text(binding_contract.get("runtime_node_id")),
                    "runtime_attachment_id": _text(binding_contract.get("runtime_attachment_id")),
                    "runtime_profile_id": _text(binding_contract.get("runtime_profile_id")),
                },
            ),
        }

    runtime_attachment = await _assert_self_hosted_runtime_gate(
        deployed_agent=deployed_agent,
        tenant_id=resolved_tenant_id,
        workspace_id=resolved_workspace_id,
        runtime_session_id=None,
        phase="session_init",
    )
    _enforce_runtime_binding_decision(
        operation="ensure_self_hosted_runtime_session",
        deployed_agent_id=resolved_deployed_agent_id,
        tenant_id=resolved_tenant_id,
        workspace_id=resolved_workspace_id,
        session_id=resolved_session_id,
        thread_id=_text(thread_id) or resolved_session_id,
        studio_agent_mode=mode,
        existing_runtime_session_id=existing_runtime_session_id or None,
        existing_runtime_binding=existing_runtime_binding or None,
        self_hosted_binding_present=bool(binding_contract),
        runtime_node_id=_text(binding_contract.get("runtime_node_id")) or None,
        runtime_profile_id=_text(binding_contract.get("runtime_profile_id")) or None,
        runtime_attachment_id=_text(binding_contract.get("runtime_attachment_id")) or None,
        binding_workspace_id=_text(binding_contract.get("workspace_id")) or None,
        self_hosted_node_registered=True,
        self_hosted_node_gate_passed=True,
        agent_allowed_on_node=True,
        active_sessions=0,
        max_concurrent_sessions=0,
        filesystem_scope=_text(binding_contract.get("filesystem_scope")) or "none",
        workspace_scoped_filesystem_allowed=_text(binding_contract.get("filesystem_scope")).lower() != "workspace_scoped"
        or _text(binding_contract.get("workspace_id")) == resolved_workspace_id,
        domain_allowlist_present=bool(
            _coerce_list(binding_contract.get("domain_allowlist"))
            or _coerce_list(binding_contract.get("allowed_domains"))
        ),
        session_recording_enabled=bool(_coerce_dict(binding_contract.get("approval_policy")) or True),
    )
    payload = build_deployed_agent_self_hosted_runtime_payload(
        deployed_agent,
        runtime_attachment=runtime_attachment,
    )
    payload.update(
        {
            "tenant_id": resolved_tenant_id,
            "workspace_id": resolved_workspace_id,
            "agent_id": resolved_deployed_agent_id,
            "run_id": resolved_session_id,
            "session_id": resolved_session_id,
            "thread_id": _text(thread_id) or resolved_session_id,
            "channel": _text(channel) or "channel",
            "actor": dict(actor) if isinstance(actor, dict) else {},
            "machine_target": _text(machine_target) or "self_host_runtime",
            "app_title": _text(deployed_agent.get("name")) or "Self-hosted Agent",
            "metadata": {
                "deployed_agent_id": resolved_deployed_agent_id,
                "session_id": resolved_session_id,
                "thread_id": _text(thread_id) or resolved_session_id,
                "runtime_session_binding": _RUNTIME_BINDING_SELF_HOSTED,
            },
        }
    )
    runtime = get_runtime_registry().resolve(
        payload.get("runtime_choice"),
        preferred_provider_id=payload.get("runtime_provider_id"),
    )
    response = await runtime.create_session(payload)
    browser_session = response.get("browser_session") if isinstance(response.get("browser_session"), dict) else {}
    runtime_session_id = _text(
        browser_session.get("browser_session_id")
        or response.get("session_id")
        or payload.get("session_id")
    )
    if not runtime_session_id:
        raise ValueError("Self-hosted runtime session did not return a session id.")

    metadata_updates = {
        "deployed_agent_id": resolved_deployed_agent_id,
        "runtime_session_id": runtime_session_id,
        "runtime_session_binding": _RUNTIME_BINDING_SELF_HOSTED,
        "runtime_choice": _text(payload.get("runtime_choice")) or None,
        "runtime_provider_id": _text(response.get("provider_id") or payload.get("runtime_provider_id")) or None,
        "runtime_provider_kind": _text(response.get("provider_kind")) or None,
        "runtime_node_id": _text(binding_contract.get("runtime_node_id")) or None,
        "runtime_attachment_id": _text(binding_contract.get("runtime_attachment_id")) or None,
        "runtime_profile_id": _text(binding_contract.get("runtime_profile_id")) or None,
        "virtual_runtime_bound": True,
    }
    await _append_cloud_runtime_audit_event(
        deployed_agent=deployed_agent,
        tenant_id=resolved_tenant_id,
        workspace_id=resolved_workspace_id,
        action="deployed_agent_self_hosted_runtime_session_created",
        title="Self-hosted runtime session created",
        summary="Self-hosted runtime session was created for deployed agent execution.",
        payload={
            "session_id": resolved_session_id,
            "runtime_session_id": runtime_session_id,
            "runtime_choice": _text(payload.get("runtime_choice")) or None,
            "runtime_provider_id": _text(response.get("provider_id") or payload.get("runtime_provider_id")) or None,
            "runtime_node_id": _text(binding_contract.get("runtime_node_id")) or None,
        },
        metadata={
            "runtime_session_binding": _RUNTIME_BINDING_SELF_HOSTED,
            "runtime_provider_kind": _text(response.get("provider_kind")) or None,
        },
    )
    return {
        "deployed_agent": deployed_agent,
        "runtime_payload": payload,
        "runtime_response": response,
        "metadata_updates": {key: value for key, value in metadata_updates.items() if value is not None},
    }


async def ensure_runtime_session_binding(
    *,
    deployed_agent_id: Any,
    tenant_id: Any,
    workspace_id: Any,
    session_id: Any,
    thread_id: Any,
    channel: Any,
    actor: Any,
    session_metadata: Any = None,
    turn_metadata: Any = None,
    machine_target: Any = None,
) -> Optional[Dict[str, Any]]:
    resolved_deployed_agent_id = _text(deployed_agent_id)
    if not resolved_deployed_agent_id:
        return None
    resolved_workspace_id = _text(workspace_id)
    resolved_tenant_id = _text(tenant_id)
    deployed_agent = await control_plane_repository.get_deployed_agent_by_id(
        resolved_deployed_agent_id,
        tenant_id=resolved_tenant_id,
        owner_workspace_id=resolved_workspace_id,
    )
    if not isinstance(deployed_agent, dict):
        raise ValueError("Deployed agent not found for runtime session binding.")
    mode = _text(
        deployed_agent_config_schema.deployed_agent_config_from_record(deployed_agent).studio_agent_mode
    ).lower()
    if mode == deployed_agent_runtime_contract_service.STUDIO_AGENT_MODE_TEXT:
        return None
    if mode == deployed_agent_runtime_contract_service.STUDIO_AGENT_MODE_SELF_HOSTED:
        return await ensure_self_hosted_runtime_session_binding(
            deployed_agent_id=deployed_agent_id,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            session_id=session_id,
            thread_id=thread_id,
            channel=channel,
            actor=actor,
            session_metadata=session_metadata,
            turn_metadata=turn_metadata,
            machine_target=machine_target,
        )
    return await ensure_cloud_runtime_session_binding(
        deployed_agent_id=deployed_agent_id,
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        session_id=session_id,
        thread_id=thread_id,
        channel=channel,
        actor=actor,
        session_metadata=session_metadata,
        turn_metadata=turn_metadata,
        machine_target=machine_target,
    )


async def execute_bound_cloud_runtime_tool_call(
    *,
    connector_id: Any,
    action_id: Any,
    argument_payload: Any,
    workspace_id: Any,
    thread_id: Any,
    session_ctx: Any = None,
) -> Optional[str]:
    metadata = _bound_runtime_metadata_from_session_ctx(session_ctx)
    if _text(metadata.get("runtime_session_binding")).lower() != _RUNTIME_BINDING_CLOUD:
        return None

    deployed_agent_id = _text(metadata.get("deployed_agent_id"))
    runtime_session_id = _text(metadata.get("runtime_session_id"))
    tenant_id = _text(metadata.get("tenant_id"))
    resolved_workspace_id = _text(workspace_id) or _text(metadata.get("workspace_id"))
    resolved_thread_id = _text(thread_id) or _text(metadata.get("thread_id"))
    resolved_run_id = _text(metadata.get("run_id")) or runtime_session_id
    if not deployed_agent_id:
        raise RuntimeError("Cloud Computer tool execution is missing deployed_agent_id.")
    if not runtime_session_id:
        raise RuntimeError("Cloud Computer tool execution is missing runtime_session_id.")
    if not tenant_id or not resolved_workspace_id:
        raise RuntimeError("Cloud Computer tool execution is missing runtime scope.")

    deployed_agent = await control_plane_repository.get_deployed_agent_by_id(
        deployed_agent_id,
        tenant_id=tenant_id,
        owner_workspace_id=resolved_workspace_id,
    )
    if not isinstance(deployed_agent, dict):
        raise RuntimeError("Cloud Computer tool execution could not load deployed agent.")

    config = deployed_agent_config_schema.deployed_agent_config_from_record(deployed_agent)
    if _text(config.studio_agent_mode).lower() != deployed_agent_runtime_contract_service.STUDIO_AGENT_MODE_CLOUD_COMPUTER:
        return None

    rejected_override_keys = _forbidden_policy_override_keys(argument_payload)
    if rejected_override_keys:
        await _append_cloud_runtime_audit_event(
            deployed_agent=deployed_agent,
            tenant_id=tenant_id,
            workspace_id=resolved_workspace_id,
            run_id=resolved_run_id,
            thread_id=resolved_thread_id or runtime_session_id,
            session_key=runtime_session_id,
            action="deployed_agent_cloud_runtime_policy_override_rejected",
            title="Cloud Computer policy override rejected",
            summary="Raw tool-call payload attempted to override backend Cloud Computer policy.",
            status="blocked",
            event_class="blocked_action",
            review_required=True,
            payload={
                "runtime_session_id": runtime_session_id,
                "rejected_keys": list(rejected_override_keys),
                "connector_id": _text(connector_id).lower() or None,
                "action_id": _text(action_id).lower() or None,
            },
            metadata={"runtime_session_binding": _RUNTIME_BINDING_CLOUD},
        )
        raise RuntimeError(
            f"Cloud Computer runtime rejected raw policy override payload: {', '.join(rejected_override_keys)}."
        )

    privacy_snapshot, computer_safety_snapshot = _validated_contract_snapshots(deployed_agent, config)
    _enforce_deployed_virtual_runtime_service_decision(
        operation="execute_cloud_tool",
        deployed_agent=deployed_agent,
        config=config,
        privacy_snapshot=privacy_snapshot,
        computer_safety_snapshot=computer_safety_snapshot,
        session_id=runtime_session_id,
        runtime_session_id=runtime_session_id,
        run_id=resolved_run_id,
        thread_id=resolved_thread_id or runtime_session_id,
        runtime_session_binding=_RUNTIME_BINDING_CLOUD,
        runtime_session_bound=True,
        runtime_session_healthy=True,
        connector_id=_text(connector_id).lower(),
        action_id=_text(action_id).lower(),
        action_supported=True,
        forbidden_policy_override=False,
    )
    kill_state = _runtime_kill_state(deployed_agent)
    try:
        action_decision = _enforce_runtime_action_decision(
            operation="execute_runtime_action",
            deployed_agent_id=deployed_agent_id,
            tenant_id=tenant_id,
            workspace_id=resolved_workspace_id,
            runtime_session_id=runtime_session_id,
            run_id=resolved_run_id,
            thread_id=resolved_thread_id or runtime_session_id,
            studio_agent_mode=_text(config.studio_agent_mode).lower(),
            runtime_session_binding=_RUNTIME_BINDING_CLOUD,
            connector_id=_text(connector_id).lower(),
            action_id=_text(action_id).lower(),
            raw_policy_override_present=False,
            forbidden_override_key_count=0,
            kill_switch_active=bool(kill_state.get("kill_switch_active")),
            workspace_emergency_stop=bool(kill_state.get("workspace_emergency_stop_active")),
            agent_paused=kill_state.get("deployment_state") == "paused",
            agent_suspended=kill_state.get("deployment_state") == "suspended",
            agent_archived=kill_state.get("deployment_state") == "archived",
            url=_coerce_dict(argument_payload).get("url"),
            selector=_coerce_dict(argument_payload).get("selector"),
            x=_coerce_dict(argument_payload).get("x"),
            y=_coerce_dict(argument_payload).get("y"),
            text=_coerce_dict(argument_payload).get("text"),
            input=_coerce_dict(argument_payload).get("input"),
            command=_coerce_dict(argument_payload).get("command"),
        )
    except RuntimeError as exc:
        reason = str(exc)
        await _append_cloud_runtime_audit_event(
            deployed_agent=deployed_agent,
            tenant_id=tenant_id,
            workspace_id=resolved_workspace_id,
            run_id=resolved_run_id,
            thread_id=resolved_thread_id or runtime_session_id,
            session_key=runtime_session_id,
            action="deployed_agent_cloud_runtime_action_denied",
            title="Cloud Computer action denied",
            summary="Cloud Computer runtime action was denied before execution.",
            status="blocked",
            event_class="blocked_action",
            review_required=True,
            payload={
                "runtime_session_id": runtime_session_id,
                "connector_id": _text(connector_id).lower() or None,
                "action_id": _text(action_id).lower() or None,
                "reason": reason,
            },
            metadata={"runtime_session_binding": _RUNTIME_BINDING_CLOUD},
        )
        lowered_reason = reason.lower()
        if (
            kill_state.get("deployment_state") in {"paused", "suspended", "archived"}
            or bool(kill_state.get("kill_switch_active"))
            or bool(kill_state.get("workspace_emergency_stop_active"))
            or any(
            token in lowered_reason
            for token in (
                "agent_paused",
                "agent_suspended",
                "agent_archived",
                "kill_switch_active",
                "workspace_emergency_stop",
            )
            )
        ):
            await _append_cloud_runtime_audit_event(
                deployed_agent=deployed_agent,
                tenant_id=tenant_id,
                workspace_id=resolved_workspace_id,
                run_id=resolved_run_id,
                thread_id=resolved_thread_id or runtime_session_id,
                session_key=runtime_session_id,
                action="deployed_agent_cloud_runtime_kill_state_rejected",
                title="Cloud Computer kill-state rejected action",
                summary="Cloud Computer runtime rejected an action because the deployed agent is paused, suspended, archived, or emergency-stopped.",
                status="blocked",
                event_class="blocked_action",
                review_required=True,
                payload={
                    "runtime_session_id": runtime_session_id,
                    "connector_id": _text(connector_id).lower() or None,
                    "action_id": _text(action_id).lower() or None,
                    "reason": reason,
                },
                metadata={"runtime_session_binding": _RUNTIME_BINDING_CLOUD},
            )
        raise
    python_runtime_action, runtime_action_args = _cloud_runtime_tool_action(
        connector_id=connector_id,
        action_id=action_id,
        argument_payload=argument_payload,
    )
    runtime_action = _text(action_decision.get("runtime_action")) or python_runtime_action
    payload = build_deployed_agent_virtual_runtime_payload(deployed_agent)
    payload.update(
        {
            "tenant_id": tenant_id,
            "workspace_id": resolved_workspace_id,
            "agent_id": deployed_agent_id,
            "run_id": resolved_run_id,
            "thread_id": resolved_thread_id or runtime_session_id,
            "session_id": runtime_session_id,
            "browser_session_id": runtime_session_id,
            "runtime_session_id": runtime_session_id,
            "action": runtime_action,
            "action_args": runtime_action_args,
            "metadata": {
                "deployed_agent_id": deployed_agent_id,
                "runtime_session_binding": _RUNTIME_BINDING_CLOUD,
                "runtime_session_id": runtime_session_id,
                "thread_id": resolved_thread_id or runtime_session_id,
            },
        }
    )
    runtime = get_runtime_registry().resolve(
        payload.get("runtime_choice"),
        preferred_provider_id=payload.get("runtime_provider_id"),
    )
    try:
        response = await runtime.execute_action(payload)
    except Exception as exc:
        reason = str(exc)
        await _append_cloud_runtime_audit_event(
            deployed_agent=deployed_agent,
            tenant_id=tenant_id,
            workspace_id=resolved_workspace_id,
            run_id=resolved_run_id,
            thread_id=resolved_thread_id or runtime_session_id,
            session_key=runtime_session_id,
            action="deployed_agent_cloud_runtime_action_denied",
            title="Cloud Computer action denied",
            summary="Cloud Computer runtime denied an action before execution.",
            status="blocked",
            event_class="blocked_action",
            review_required=True,
            payload={
                "runtime_session_id": runtime_session_id,
                "runtime_action": runtime_action,
                "runtime_action_args": dict(runtime_action_args),
                "connector_id": _text(connector_id).lower() or None,
                "action_id": _text(action_id).lower() or None,
                "reason": reason,
            },
            metadata={"runtime_session_binding": _RUNTIME_BINDING_CLOUD},
        )
        lowered_reason = reason.lower()
        if any(
            token in lowered_reason
            for token in (
                "agent_paused",
                "agent_suspended",
                "agent_archived",
                "kill_switch_active",
                "workspace_emergency_stop",
            )
        ):
            await _append_cloud_runtime_audit_event(
                deployed_agent=deployed_agent,
                tenant_id=tenant_id,
                workspace_id=resolved_workspace_id,
                run_id=resolved_run_id,
                thread_id=resolved_thread_id or runtime_session_id,
                session_key=runtime_session_id,
                action="deployed_agent_cloud_runtime_kill_state_rejected",
                title="Cloud Computer kill-state rejected action",
                summary="Cloud Computer runtime rejected an action because the deployed agent is paused, suspended, archived, or emergency-stopped.",
                status="blocked",
                event_class="blocked_action",
                review_required=True,
                payload={
                    "runtime_session_id": runtime_session_id,
                    "runtime_action": runtime_action,
                    "reason": reason,
                },
                metadata={"runtime_session_binding": _RUNTIME_BINDING_CLOUD},
            )
        raise
    canonical_artifacts = await _canonicalize_cloud_runtime_artifacts(
        runtime=runtime,
        deployed_agent=deployed_agent,
        response=response,
        runtime_session_id=runtime_session_id,
        run_id=resolved_run_id,
        tenant_id=tenant_id,
        workspace_id=resolved_workspace_id,
        runtime_action=runtime_action,
    )
    if canonical_artifacts:
        response["artifacts"] = canonical_artifacts
        artifact_id = _text(_coerce_dict(response.get("artifact")).get("artifact_id"))
        if artifact_id:
            replacement = next(
                (item for item in canonical_artifacts if _text(item.get("artifact_id")) == artifact_id),
                None,
            )
            if isinstance(replacement, dict):
                response["artifact"] = replacement
    computer_proof = _latest_cloud_computer_proof(
        response=response,
        runtime_session_id=runtime_session_id,
        artifacts=canonical_artifacts,
    )
    if computer_proof:
        response["computer_proof"] = computer_proof
        await session_service.extend_session(
            runtime_session_id,
            metadata_updates={
                "latest_computer_proof": computer_proof,
                "runtime_provider_id": _text(response.get("provider_id") or payload.get("runtime_provider_id")) or None,
                "runtime_kind": _text(response.get("runtime_kind")) or None,
                "runtime_session_binding": _RUNTIME_BINDING_CLOUD,
            },
        )
    await _append_cloud_runtime_audit_event(
        deployed_agent=deployed_agent,
        tenant_id=tenant_id,
        workspace_id=resolved_workspace_id,
        run_id=resolved_run_id,
        thread_id=resolved_thread_id or runtime_session_id,
        session_key=runtime_session_id,
        artifacts=canonical_artifacts,
        action="deployed_agent_cloud_runtime_action_admitted",
        title="Cloud Computer action admitted",
        summary=(
            "Cloud Computer runtime admitted and executed an action with screenshot proof."
            if canonical_artifacts
            else "Cloud Computer runtime admitted and executed an action."
        ),
        status="success",
        payload={
            "runtime_session_id": runtime_session_id,
            "runtime_action": runtime_action,
            "runtime_action_args": dict(runtime_action_args),
            "connector_id": _text(connector_id).lower() or None,
            "action_id": _text(action_id).lower() or None,
            "computer_proof": computer_proof,
        },
        metadata={"runtime_session_binding": _RUNTIME_BINDING_CLOUD},
    )
    return _cloud_runtime_result_text(
        connector_id=connector_id,
        action_id=action_id,
        response=response,
    )


async def execute_bound_self_hosted_runtime_tool_call(
    *,
    connector_id: Any,
    action_id: Any,
    argument_payload: Any,
    workspace_id: Any,
    thread_id: Any,
    session_ctx: Any = None,
) -> Optional[str]:
    metadata = _bound_runtime_metadata_from_session_ctx(session_ctx)
    if not _runtime_connector_action(connector_id, action_id):
        return None

    deployed_agent_id = _text(metadata.get("deployed_agent_id"))
    tenant_id = _text(metadata.get("tenant_id"))
    resolved_workspace_id = _text(workspace_id) or _text(metadata.get("workspace_id"))
    resolved_thread_id = _text(thread_id) or _text(metadata.get("thread_id"))
    runtime_binding = _text(metadata.get("runtime_session_binding")).lower()
    runtime_session_id = _text(metadata.get("runtime_session_id"))

    if not deployed_agent_id:
        if runtime_binding == _RUNTIME_BINDING_SELF_HOSTED:
            raise RuntimeError("Self-hosted runtime tool execution is missing deployed_agent_id.")
        return None
    if not tenant_id or not resolved_workspace_id:
        raise RuntimeError("Self-hosted runtime tool execution is missing runtime scope.")

    deployed_agent = await control_plane_repository.get_deployed_agent_by_id(
        deployed_agent_id,
        tenant_id=tenant_id,
        owner_workspace_id=resolved_workspace_id,
    )
    if not isinstance(deployed_agent, dict):
        raise RuntimeError("Self-hosted runtime tool execution could not load deployed agent.")

    config = deployed_agent_config_schema.deployed_agent_config_from_record(deployed_agent)
    if _text(config.studio_agent_mode).lower() != deployed_agent_runtime_contract_service.STUDIO_AGENT_MODE_SELF_HOSTED:
        return None
    if runtime_binding != _RUNTIME_BINDING_SELF_HOSTED:
        raise RuntimeError(
            f"self_hosted_agent runtime action '{_text(connector_id).lower()}__{_text(action_id).lower()}' requires dedicated node runtime session binding."
        )
    if not runtime_session_id:
        raise RuntimeError("Self-hosted runtime tool execution is missing runtime_session_id.")
    try:
        runtime_attachment = await _assert_self_hosted_runtime_gate(
            deployed_agent=deployed_agent,
            tenant_id=tenant_id,
            workspace_id=resolved_workspace_id,
            runtime_session_id=runtime_session_id,
            phase="action",
        )
    except ValueError as exc:
        await _append_cloud_runtime_audit_event(
            deployed_agent=deployed_agent,
            tenant_id=tenant_id,
            workspace_id=resolved_workspace_id,
            action="deployed_agent_self_hosted_runtime_action_denied",
            title="Self-hosted runtime action denied",
            summary="Self-hosted runtime denied an action before execution.",
            status="blocked",
            event_class="blocked_action",
            review_required=True,
            payload={
                "runtime_session_id": runtime_session_id,
                "connector_id": _text(connector_id).lower() or None,
                "action_id": _text(action_id).lower() or None,
                "reason": str(exc),
            },
            metadata={"runtime_session_binding": _RUNTIME_BINDING_SELF_HOSTED},
        )
        raise RuntimeError(str(exc)) from exc

    rejected_override_keys = _forbidden_policy_override_keys(argument_payload)
    if rejected_override_keys:
        await _append_cloud_runtime_audit_event(
            deployed_agent=deployed_agent,
            tenant_id=tenant_id,
            workspace_id=resolved_workspace_id,
            action="deployed_agent_self_hosted_runtime_policy_override_rejected",
            title="Self-hosted runtime policy override rejected",
            summary="Raw tool-call payload attempted to override backend self-hosted runtime policy.",
            status="blocked",
            event_class="blocked_action",
            review_required=True,
            payload={
                "runtime_session_id": runtime_session_id,
                "rejected_keys": list(rejected_override_keys),
                "connector_id": _text(connector_id).lower() or None,
                "action_id": _text(action_id).lower() or None,
            },
            metadata={"runtime_session_binding": _RUNTIME_BINDING_SELF_HOSTED},
        )
        raise RuntimeError(
            f"Self-hosted runtime rejected raw policy override payload: {', '.join(rejected_override_keys)}."
        )
    kill_state = _runtime_kill_state(deployed_agent)
    try:
        action_decision = _enforce_runtime_action_decision(
            operation="execute_runtime_action",
            deployed_agent_id=deployed_agent_id,
            tenant_id=tenant_id,
            workspace_id=resolved_workspace_id,
            runtime_session_id=runtime_session_id,
            run_id=_text(metadata.get("run_id")) or runtime_session_id,
            thread_id=resolved_thread_id or runtime_session_id,
            studio_agent_mode=_text(config.studio_agent_mode).lower(),
            runtime_session_binding=_RUNTIME_BINDING_SELF_HOSTED,
            connector_id=_text(connector_id).lower(),
            action_id=_text(action_id).lower(),
            raw_policy_override_present=False,
            forbidden_override_key_count=0,
            kill_switch_active=bool(kill_state.get("kill_switch_active")),
            workspace_emergency_stop=bool(kill_state.get("workspace_emergency_stop_active")),
            agent_paused=kill_state.get("deployment_state") == "paused",
            agent_suspended=kill_state.get("deployment_state") == "suspended",
            agent_archived=kill_state.get("deployment_state") == "archived",
            self_hosted_node_gate_passed=True,
            self_hosted_node_concurrency_exceeded=False,
            url=_coerce_dict(argument_payload).get("url"),
            selector=_coerce_dict(argument_payload).get("selector"),
            x=_coerce_dict(argument_payload).get("x"),
            y=_coerce_dict(argument_payload).get("y"),
            text=_coerce_dict(argument_payload).get("text"),
            input=_coerce_dict(argument_payload).get("input"),
            command=_coerce_dict(argument_payload).get("command"),
        )
    except RuntimeError as exc:
        await _append_cloud_runtime_audit_event(
            deployed_agent=deployed_agent,
            tenant_id=tenant_id,
            workspace_id=resolved_workspace_id,
            action="deployed_agent_self_hosted_runtime_action_denied",
            title="Self-hosted runtime action denied",
            summary="Self-hosted runtime action was denied before execution.",
            status="blocked",
            event_class="blocked_action",
            review_required=True,
            payload={
                "runtime_session_id": runtime_session_id,
                "connector_id": _text(connector_id).lower() or None,
                "action_id": _text(action_id).lower() or None,
                "reason": str(exc),
            },
            metadata={"runtime_session_binding": _RUNTIME_BINDING_SELF_HOSTED},
        )
        raise
    python_runtime_action, runtime_action_args = _cloud_runtime_tool_action(
        connector_id=connector_id,
        action_id=action_id,
        argument_payload=argument_payload,
    )
    runtime_action = _text(action_decision.get("runtime_action")) or python_runtime_action
    payload = build_deployed_agent_self_hosted_runtime_payload(
        deployed_agent,
        runtime_attachment=runtime_attachment,
    )
    payload.update(
        {
            "tenant_id": tenant_id,
            "workspace_id": resolved_workspace_id,
            "agent_id": deployed_agent_id,
            "run_id": _text(metadata.get("run_id")) or runtime_session_id,
            "thread_id": resolved_thread_id or runtime_session_id,
            "session_id": runtime_session_id,
            "browser_session_id": runtime_session_id,
            "runtime_session_id": runtime_session_id,
            "action": runtime_action,
            "action_args": runtime_action_args,
            "metadata": {
                "deployed_agent_id": deployed_agent_id,
                "runtime_session_binding": _RUNTIME_BINDING_SELF_HOSTED,
                "runtime_session_id": runtime_session_id,
                "thread_id": resolved_thread_id or runtime_session_id,
            },
        }
    )
    runtime = get_runtime_registry().resolve(
        payload.get("runtime_choice"),
        preferred_provider_id=payload.get("runtime_provider_id"),
    )
    try:
        response = await runtime.execute_action(payload)
    except Exception as exc:
        await _append_cloud_runtime_audit_event(
            deployed_agent=deployed_agent,
            tenant_id=tenant_id,
            workspace_id=resolved_workspace_id,
            action="deployed_agent_self_hosted_runtime_action_denied",
            title="Self-hosted runtime action denied",
            summary="Self-hosted runtime denied an action before execution.",
            status="blocked",
            event_class="blocked_action",
            review_required=True,
            payload={
                "runtime_session_id": runtime_session_id,
                "runtime_action": runtime_action,
                "runtime_action_args": dict(runtime_action_args),
                "connector_id": _text(connector_id).lower() or None,
                "action_id": _text(action_id).lower() or None,
                "reason": str(exc),
            },
            metadata={"runtime_session_binding": _RUNTIME_BINDING_SELF_HOSTED},
        )
        raise

    await _append_cloud_runtime_audit_event(
        deployed_agent=deployed_agent,
        tenant_id=tenant_id,
        workspace_id=resolved_workspace_id,
        action="deployed_agent_self_hosted_runtime_action_admitted",
        title="Self-hosted runtime action admitted",
        summary="Self-hosted runtime admitted and executed an action.",
        status="success",
        payload={
            "runtime_session_id": runtime_session_id,
            "runtime_action": runtime_action,
            "runtime_action_args": dict(runtime_action_args),
            "connector_id": _text(connector_id).lower() or None,
            "action_id": _text(action_id).lower() or None,
        },
        metadata={"runtime_session_binding": _RUNTIME_BINDING_SELF_HOSTED},
    )
    return _cloud_runtime_result_text(
        connector_id=connector_id,
        action_id=action_id,
        response=response,
    )


async def terminate_bound_cloud_runtime_session(
    *,
    session_id: Any,
    tenant_id: Any = None,
    workspace_id: Any = None,
) -> Optional[Dict[str, Any]]:
    token = _text(session_id)
    if not token:
        return None
    session_record = await session_service.get_session(token)
    metadata = _coerce_dict((session_record or {}).get("metadata"))
    if _text(metadata.get("runtime_session_binding")).lower() != _RUNTIME_BINDING_CLOUD:
        return None
    resolved_tenant_id = _text(tenant_id) or _text((session_record or {}).get("tenant_id"))
    resolved_workspace_id = _text(workspace_id) or _text((session_record or {}).get("workspace_id"))
    resolved_deployed_agent_id = _text(metadata.get("deployed_agent_id"))
    if resolved_deployed_agent_id and resolved_tenant_id and resolved_workspace_id:
        deployed_agent = await control_plane_repository.get_deployed_agent_by_id(
            resolved_deployed_agent_id,
            tenant_id=resolved_tenant_id,
            owner_workspace_id=resolved_workspace_id,
        )
        if isinstance(deployed_agent, dict):
            config = deployed_agent_config_schema.deployed_agent_config_from_record(deployed_agent)
            record_metadata = _coerce_dict(deployed_agent.get("metadata"))
            privacy_snapshot = _coerce_dict(record_metadata.get("privacy_contract_snapshot"))
            computer_safety_snapshot = _coerce_dict(record_metadata.get("computer_safety_contract_snapshot"))
            _enforce_deployed_virtual_runtime_service_decision(
                operation="terminate_cloud_session",
                deployed_agent=deployed_agent,
                config=config,
                privacy_snapshot=privacy_snapshot,
                computer_safety_snapshot=computer_safety_snapshot,
                session_id=token,
                runtime_session_id=_text(metadata.get("runtime_session_id")) or token,
                runtime_session_binding=_RUNTIME_BINDING_CLOUD,
                actor_id="system",
            )
    runtime_session_id = _text(metadata.get("runtime_session_id")) or token
    runtime_choice = _text(metadata.get("runtime_choice")) or "virtual_browser"
    runtime_provider_id = _text(metadata.get("runtime_provider_id")) or None
    runtime = get_runtime_registry().resolve(
        runtime_choice,
        preferred_provider_id=runtime_provider_id,
    )
    payload = {
        "tenant_id": resolved_tenant_id,
        "workspace_id": resolved_workspace_id,
        "session_id": runtime_session_id,
        "browser_session_id": runtime_session_id,
        "runtime_provider_id": runtime_provider_id,
        "manual_terminate": True,
        "metadata": {
            "deployed_agent_id": _text(metadata.get("deployed_agent_id")) or None,
            "runtime_session_binding": _RUNTIME_BINDING_CLOUD,
            "runtime_session_id": runtime_session_id,
        },
    }
    result = await runtime.terminate_session({key: value for key, value in payload.items() if value is not None})
    ended_at = _utc_now_iso()
    await _record_bound_cloud_runtime_usage_event(
        session_record=session_record or {},
        metadata=metadata,
        tenant_id=resolved_tenant_id,
        workspace_id=resolved_workspace_id,
        runtime_session_id=runtime_session_id,
        ended_at=ended_at,
    )
    return result


async def _record_bound_cloud_runtime_usage_event(
    *,
    session_record: Dict[str, Any],
    metadata: Dict[str, Any],
    tenant_id: Any,
    workspace_id: Any,
    runtime_session_id: str,
    ended_at: str,
) -> Optional[Dict[str, Any]]:
    if _text(metadata.get("runtime_metered_at")):
        return None
    tenant_token = _text(tenant_id)
    workspace_token = _text(workspace_id)
    if not tenant_token or not workspace_token or not _text(runtime_session_id):
        return None
    started_at = (
        _text(metadata.get("runtime_session_started_at"))
        or _text(session_record.get("created_at"))
        or _text(session_record.get("updated_at"))
    )
    runtime_target = _text(metadata.get("runtime_target")) or _runtime_target_from_cloud_metadata(metadata)
    active_seconds = _runtime_billable_seconds(
        started_at=started_at,
        ended_at=ended_at,
        max_seconds=metadata.get("runtime_max_session_seconds"),
    )
    if active_seconds <= 0:
        return None
    billable_seconds = active_seconds
    estimated_cost_usd = _runtime_cost_estimate_usd(runtime_target, billable_seconds)
    event = runtime_attachment_service.build_runtime_usage_credit_event(
        tenant_id=tenant_token,
        workspace_id=workspace_token,
        surface="studio",
        runtime_target=runtime_target,
        session_id=runtime_session_id,
        started_at=started_at,
        ended_at=ended_at,
        active_seconds=active_seconds,
        billable_seconds=billable_seconds,
        estimated_cost_usd=estimated_cost_usd,
        thread_id=_text(metadata.get("thread_id")) or _text(session_record.get("thread_id")) or runtime_session_id,
        run_id=_text(metadata.get("run_id")) or _text(session_record.get("run_id")) or runtime_session_id,
        deployed_agent_id=_text(metadata.get("deployed_agent_id")) or None,
        app_id=None,
        metadata={
            "source_surface": "studio",
            "runtime_session_binding": _text(metadata.get("runtime_session_binding")) or _RUNTIME_BINDING_CLOUD,
            "runtime_metering_reason": "runtime_session_terminated",
        },
    )
    activity = await activity_ledger_service.append_activity_event(
        tenant_id=tenant_token,
        workspace_id=workspace_token,
        actor_type="deployed_agent",
        actor_id=_text(metadata.get("deployed_agent_id")) or "runtime",
        event_class="system_activity",
        action="studio_runtime_computer_runtime_metered",
        title="Runtime usage metered",
        summary="Computer runtime usage was metered when the session ended.",
        status="logged",
        payload={
            "runtime_session_id": runtime_session_id,
            "runtime_target": runtime_target,
            "active_seconds": active_seconds,
            "billable_seconds": billable_seconds,
            "estimated_cost_usd": estimated_cost_usd,
        },
        metadata=event.get("metadata") if isinstance(event.get("metadata"), dict) else {},
        thread_id=_text(metadata.get("thread_id")) or _text(session_record.get("thread_id")) or runtime_session_id,
        run_id=_text(metadata.get("run_id")) or _text(session_record.get("run_id")) or runtime_session_id,
        session_key=runtime_session_id,
    )
    unified_event = _coerce_dict(_coerce_dict(event.get("metadata")).get("unified_credit_ledger_event"))
    if unified_event:
        await control_plane_repository.record_credit_ledger_event(
            tenant_id=tenant_token,
            workspace_id=workspace_token,
            event=unified_event,
            source_table="activity_ledger_events",
            source_event_id=_text((activity or {}).get("id")) or runtime_session_id,
        )
        await agent_action_metering_service.record_completed(
            tenant_id=tenant_token,
            workspace_id=workspace_token,
            surface="studio",
            source_surface="studio_runtime_session",
            action_domain="runtime",
            action_type="meter",
            action_name="runtime.session_terminated",
            tool_kind="computer_runtime",
            runtime_target=runtime_target,
            payer=unified_event.get("payer") or "platform_credits",
            billing_mode="transparency",
            credit_type=unified_event.get("credit_type") or "computer_runtime",
            credits_debited=0.0,
            platform_cost_usd=0.0,
            usage_ref={"credit_ledger_source_table": "activity_ledger_events", "source_event_id": _text((activity or {}).get("id")) or runtime_session_id},
            thread_id=_text(metadata.get("thread_id")) or _text(session_record.get("thread_id")) or runtime_session_id,
            run_id=_text(metadata.get("run_id")) or _text(session_record.get("run_id")) or runtime_session_id,
            agent_id=_text(metadata.get("deployed_agent_id")) or None,
            input_summary=runtime_session_id,
            output_summary="Computer runtime usage was metered when the session ended.",
            source_table="runtime_sessions",
            source_event_id=runtime_session_id,
            project_activity=False,
            metadata={
                "active_seconds": active_seconds,
                "billable_seconds": billable_seconds,
                "estimated_cost_usd": estimated_cost_usd,
            },
        )
    await session_service.extend_session(
        _text(session_record.get("session_id")) or runtime_session_id,
        metadata_updates={
            "runtime_metered_at": ended_at,
            "runtime_metered_event_id": _text((activity or {}).get("id")) or None,
        },
    )
    return activity


async def terminate_bound_self_hosted_runtime_session(
    *,
    session_id: Any,
    tenant_id: Any = None,
    workspace_id: Any = None,
) -> Optional[Dict[str, Any]]:
    token = _text(session_id)
    if not token:
        return None
    session_record = await session_service.get_session(token)
    metadata = _coerce_dict((session_record or {}).get("metadata"))
    if _text(metadata.get("runtime_session_binding")).lower() != _RUNTIME_BINDING_SELF_HOSTED:
        return None
    resolved_tenant_id = _text(tenant_id) or _text((session_record or {}).get("tenant_id"))
    resolved_workspace_id = _text(workspace_id) or _text((session_record or {}).get("workspace_id"))
    resolved_deployed_agent_id = _text(metadata.get("deployed_agent_id"))
    if resolved_deployed_agent_id and resolved_tenant_id and resolved_workspace_id:
        deployed_agent = await control_plane_repository.get_deployed_agent_by_id(
            resolved_deployed_agent_id,
            tenant_id=resolved_tenant_id,
            owner_workspace_id=resolved_workspace_id,
        )
        if isinstance(deployed_agent, dict):
            config = deployed_agent_config_schema.deployed_agent_config_from_record(deployed_agent)
            record_metadata = _coerce_dict(deployed_agent.get("metadata"))
            privacy_snapshot = _coerce_dict(record_metadata.get("privacy_contract_snapshot"))
            computer_safety_snapshot = _coerce_dict(record_metadata.get("computer_safety_contract_snapshot"))
            _enforce_deployed_virtual_runtime_service_decision(
                operation="terminate_self_hosted_session",
                deployed_agent=deployed_agent,
                config=config,
                privacy_snapshot=privacy_snapshot,
                computer_safety_snapshot=computer_safety_snapshot,
                session_id=token,
                runtime_session_id=_text(metadata.get("runtime_session_id")) or token,
                runtime_session_binding=_RUNTIME_BINDING_SELF_HOSTED,
                actor_id="system",
            )
    runtime_session_id = _text(metadata.get("runtime_session_id")) or token
    runtime_choice = _text(metadata.get("runtime_choice")) or "virtual_code_sandbox"
    runtime_provider_id = _text(metadata.get("runtime_provider_id")) or virtual_computer_runtime.PROVIDER_ID_DOCKER_KUBERNETES
    runtime = get_runtime_registry().resolve(
        runtime_choice,
        preferred_provider_id=runtime_provider_id,
    )
    payload = {
        "tenant_id": resolved_tenant_id,
        "workspace_id": resolved_workspace_id,
        "session_id": runtime_session_id,
        "browser_session_id": runtime_session_id,
        "runtime_provider_id": runtime_provider_id,
        "runtime_node_id": _text(metadata.get("runtime_node_id")) or None,
        "runtime_attachment_id": _text(metadata.get("runtime_attachment_id")) or None,
        "runtime_profile_id": _text(metadata.get("runtime_profile_id")) or None,
        "manual_terminate": True,
        "metadata": {
            "deployed_agent_id": _text(metadata.get("deployed_agent_id")) or None,
            "runtime_session_binding": _RUNTIME_BINDING_SELF_HOSTED,
            "runtime_session_id": runtime_session_id,
        },
    }
    return await runtime.terminate_session({key: value for key, value in payload.items() if value is not None})
