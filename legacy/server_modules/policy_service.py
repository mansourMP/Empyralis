from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from scripts.platform_execution import capability_metadata
from server_modules.computer_action_safety import evaluate_dangerous_computer_action_policy
from server_modules.capability_registry import canonical_capability_id
from server_modules import skills_service
from server_modules import rust_runtime_kernel_client
from server_modules import safe_mode_service

def _is_local_development_mode() -> bool:
    env = str(os.getenv("ENV") or os.getenv("ENVIRONMENT") or "").strip().lower()
    if env in {"development", "dev", "local"}:
        return True
    db_url = str(os.getenv("DATABASE_URL") or "").strip()
    if not db_url:
        return True
    return False



@dataclass(slots=True)
class PolicyDecision:
    allowed: bool
    decision: str
    reason: str = ""
    approvals_required: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)


def _runtime_policy_module():
    from server_modules import runtime_policy as runtime_policy_module

    runtime_policy_module._init()
    return runtime_policy_module


def _normalize_action_id(value: Any) -> str:
    return _runtime_policy_module().normalize_action_id(value)


def normalize_execution_target(raw_value: Any) -> str:
    return _runtime_policy_module().normalize_execution_target(raw_value)


def normalize_policy_mode(raw_value: Any) -> str:
    return _runtime_policy_module().normalize_policy_mode(raw_value)


def normalize_trust_mode(raw_value: Any) -> str:
    return _runtime_policy_module().normalize_trust_mode(raw_value)


def action_policy_from_app_permissions(permissions: Optional[List[str]] = None) -> Dict[str, Any]:
    runtime_policy = _runtime_policy_module()
    perms = {str(item).strip().lower() for item in (permissions or []) if str(item).strip()}
    allowed = set()
    approval = set()
    blocked = set(runtime_policy.ACTION_RISK_LEVELS.keys())

    for perm, mapping in runtime_policy.APP_PERMISSION_ACTION_MAP.items():
        if perm not in perms:
            continue
        tool_id = _normalize_action_id(mapping.get("tool"))
        if not tool_id:
            continue
        if mapping.get("requires_approval", True):
            approval.add(tool_id)
        else:
            allowed.add(tool_id)

    blocked = blocked.difference(approval).difference(allowed)
    return {
        "blocked_actions": sorted(blocked),
        "approval_actions": sorted(approval),
        "allow_actions": sorted(allowed),
        "block_cloud_critical": True,
        "connector_cloud_readonly": True,
    }


def merge_action_policies(base: Optional[Dict[str, Any]], enforced: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    runtime_policy = _runtime_policy_module()
    base_policy = runtime_policy._action_policy_from_metadata(base or {})
    enforced_policy = runtime_policy._action_policy_from_metadata(enforced or {})

    blocked = set(base_policy.get("blocked_actions", set())) | set(enforced_policy.get("blocked_actions", set()))
    approval = set(base_policy.get("approval_actions", set())) | set(enforced_policy.get("approval_actions", set()))
    allow = set(base_policy.get("allow_actions", set())).difference(blocked)

    return {
        "blocked_actions": sorted(blocked),
        "approval_actions": sorted(approval),
        "allow_actions": sorted(allow),
        "block_cloud_critical": bool(
            enforced_policy.get(
                "block_cloud_critical",
                base_policy.get("block_cloud_critical", True),
            )
        ),
        "connector_cloud_readonly": bool(
            enforced_policy.get(
                "connector_cloud_readonly",
                base_policy.get("connector_cloud_readonly", True),
            )
        ),
    }


def resolve_runtime_policy_mode(
    metadata: Optional[Dict[str, Any]] = None,
    *,
    selected_target: Optional[str] = None,
) -> Dict[str, Any]:
    runtime_policy = _runtime_policy_module()
    clean_metadata = metadata if isinstance(metadata, dict) else {}
    target = normalize_execution_target(
        selected_target
        or clean_metadata.get("execution_target_selected")
        or clean_metadata.get("execution_target")
    )
    runtime_ids: List[str] = []
    seen: set[str] = set()
    explicit_runtime_id = str(
        clean_metadata.get("runtime_id")
        or clean_metadata.get("execution_runtime_id")
        or clean_metadata.get("execution_target_runtime_id")
        or clean_metadata.get("execution_target_preferred_runtime_id")
        or ""
    ).strip()
    if explicit_runtime_id:
        runtime_ids.append(explicit_runtime_id)
        seen.add(explicit_runtime_id)
    for raw in clean_metadata.get("execution_target_matching_runtime_ids") or []:
        token = str(raw or "").strip()
        if token and token not in seen:
            seen.add(token)
            runtime_ids.append(token)

    resolved_modes: List[str] = []
    resolved_runtime_id: Optional[str] = None
    for runtime_id in runtime_ids:
        record = (
            runtime_policy.LOCAL_WORKER_REGISTRY.get(runtime_id)
            if isinstance(runtime_policy.LOCAL_WORKER_REGISTRY.get(runtime_id), dict)
            else None
        )
        if not isinstance(record, dict):
            continue
        resolved_mode = normalize_policy_mode(record.get("policy_mode"))
        if resolved_runtime_id is None:
            resolved_runtime_id = runtime_id
        resolved_modes.append(resolved_mode)

    if resolved_modes:
        unique_modes = sorted(set(resolved_modes))
        policy_mode = (
            unique_modes[0]
            if len(unique_modes) == 1
            else runtime_policy.POLICY_MODE_LOCAL_DEFAULT
        )
        source = (
            "runtime_registration"
            if len(unique_modes) == 1
            else "runtime_registration_mixed"
        )
        return {
            "policy_mode": policy_mode,
            "runtime_id": resolved_runtime_id,
            "candidate_runtime_ids": runtime_ids,
            "source": source,
            "target": target,
        }

    explicit_policy_mode = clean_metadata.get("policy_mode")
    if explicit_policy_mode is not None:
        return {
            "policy_mode": normalize_policy_mode(explicit_policy_mode),
            "runtime_id": resolved_runtime_id,
            "candidate_runtime_ids": runtime_ids,
            "source": "metadata_fallback",
            "target": target,
        }

    return {
        "policy_mode": runtime_policy.ORION_RUNTIME_POLICY_MODE_DEFAULT,
        "runtime_id": resolved_runtime_id,
        "candidate_runtime_ids": runtime_ids,
        "source": "runtime_default",
        "target": target,
    }


def _runtime_action_policy_decision(
    classification: Dict[str, Any],
    *,
    target: str,
    policy_mode: Optional[str],
    metadata: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    runtime_policy = _runtime_policy_module()
    return runtime_policy.decide_runtime_action_execution(
        classification,
        target=target,
        policy_mode=policy_mode,
        metadata=metadata,
    )


def _emit_tool_policy_denial_audit(
    *,
    tool_id: str,
    metadata: Dict[str, Any],
    reason: str,
    target: str,
    policy_mode: Optional[str],
    classification: Dict[str, Any],
) -> None:
    tenant_id = str(metadata.get("tenant_id") or "").strip() or None
    workspace_id = str(metadata.get("workspace_id") or "").strip() or None
    if not tenant_id and not workspace_id:
        return
    try:
        from server_modules import security_audit_service

        security_audit_service.emit_security_audit_event(
            action="tool_policy.rejected",
            status="blocked",
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            run_id=str(metadata.get("run_id") or metadata.get("request_id") or "").strip() or None,
            detail=f"Tool '{tool_id}' rejected by policy.",
            metadata={
                "tool_id": tool_id,
                "reason": reason,
                "target": target,
                "policy_mode": policy_mode,
                "classification": classification if isinstance(classification, dict) else {},
            },
            idempotency_key=(
                f"tool_policy.rejected:{workspace_id or 'default'}:{tool_id}:{target}:{reason or 'blocked'}"
            ),
        )
    except Exception:
        return


def apply_execution_route_metadata(metadata: Dict[str, Any], route: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(metadata, dict):
        return metadata

    metadata["execution_target_requested"] = route.get("requested")
    metadata["execution_target_selected"] = route.get("selected")
    metadata["execution_target_reason"] = route.get("reason")
    if route.get("fallback"):
        metadata["execution_target_fallback"] = route.get("fallback")
    else:
        metadata.pop("execution_target_fallback", None)

    for route_key, metadata_key in (
        ("required_capabilities", "execution_target_required_capabilities"),
        ("missing_capabilities", "execution_target_missing_capabilities"),
        ("matching_runtime_ids", "execution_target_matching_runtime_ids"),
        ("available_runtime_ids", "execution_target_available_runtime_ids"),
        ("busy_matching_runtime_ids", "execution_target_busy_runtime_ids"),
        ("busy_runtime_labels", "execution_target_busy_runtime_labels"),
    ):
        if route.get(route_key):
            metadata[metadata_key] = list(route.get(route_key) or [])
        else:
            metadata.pop(metadata_key, None)

    for route_key, metadata_key in (
        ("preferred_runtime_id", "execution_target_preferred_runtime_id"),
        ("preferred_runtime_label", "execution_target_preferred_runtime_label"),
        ("preferred_runtime_reason", "execution_target_preferred_runtime_reason"),
    ):
        if route.get(route_key):
            metadata[metadata_key] = route.get(route_key)
        else:
            metadata.pop(metadata_key, None)

    metadata["execution_target_queued_ahead_count"] = int(route.get("queued_ahead_count") or 0)
    metadata["execution_target_estimated_wait_band"] = str(route.get("estimated_wait_band") or "unknown")
    metadata["execution_target_waiting_for_runtime"] = bool(route.get("waiting_for_runtime"))
    metadata["execution_target_waiting_for_capacity"] = bool(route.get("waiting_for_capacity"))
    if route.get("runtime_choice_requested"):
        metadata["runtime_choice_requested"] = route.get("runtime_choice_requested")
    else:
        metadata.pop("runtime_choice_requested", None)
    metadata["runtime_choice_selected"] = str(route.get("runtime_choice_selected") or runtime_policy.RUNTIME_CHOICE_LOCAL)
    metadata["runtime_choice_reason"] = str(route.get("runtime_choice_reason") or "")
    metadata["runtime_choice_source"] = str(route.get("runtime_choice_source") or "")
    metadata["runtime_choice_applied"] = bool(route.get("runtime_choice_applied"))
    if route.get("runtime_provider_requested"):
        metadata["runtime_provider_requested"] = str(route.get("runtime_provider_requested"))
    else:
        metadata.pop("runtime_provider_requested", None)
    metadata["runtime_choice_override_requested"] = bool(route.get("runtime_choice_override_requested"))
    if route.get("runtime_choice_policy_warning"):
        metadata["runtime_choice_policy_warning"] = str(route.get("runtime_choice_policy_warning"))
    else:
        metadata.pop("runtime_choice_policy_warning", None)
    if route.get("studio_runtime_options"):
        metadata["studio_runtime_options"] = list(route.get("studio_runtime_options") or [])
    else:
        metadata.pop("studio_runtime_options", None)
    if route.get("studio_template_requirements"):
        metadata["studio_template_requirements"] = dict(route.get("studio_template_requirements") or {})
    else:
        metadata.pop("studio_template_requirements", None)
    metadata["runtime_contract_interface"] = str(route.get("runtime_contract_interface") or runtime_policy.RUNTIME_CONTRACT_INTERFACE_ID)
    metadata["runtime_contract_kind"] = str(
        route.get("runtime_contract_kind")
        or runtime_policy._runtime_contract_kind_from_choice(route.get("runtime_choice_selected"))
    )
    metadata["runtime_contract_methods"] = list(route.get("runtime_contract_methods") or runtime_policy.RUNTIME_CONTRACT_METHODS)
    metadata["runtime_contract_states"] = list(route.get("runtime_contract_states") or runtime_policy.RUNTIME_CONTRACT_STATES)
    return metadata


def decide_execution_target(metadata: Dict[str, Any], schedule_id: Optional[str] = None) -> Dict[str, Any]:
    runtime_policy = _runtime_policy_module()
    clean_metadata = metadata if isinstance(metadata, dict) else {}
    requested = normalize_execution_target(clean_metadata.get("execution_target"))
    runtime_choice = runtime_policy.decide_runtime_choice(clean_metadata)
    runtime_provider_requested = (
        str(clean_metadata.get("runtime_provider_id") or clean_metadata.get("virtual_provider_id") or "").strip()
        or None
    )
    runtime_choice_applied = False

    if requested == runtime_policy.EXECUTION_TARGET_AUTO:
        requested_from_choice = runtime_policy.runtime_choice_to_execution_target(runtime_choice.get("selected"))
        if requested_from_choice == runtime_policy.EXECUTION_TARGET_AUTO:
            requested_from_choice = normalize_execution_target(runtime_choice.get("selected"))
        if requested_from_choice in {
            runtime_policy.EXECUTION_TARGET_LOCAL_COMPANION,
            runtime_policy.EXECUTION_TARGET_CLOUD,
        }:
            requested = requested_from_choice
            runtime_choice_applied = True

    if requested == runtime_policy.EXECUTION_TARGET_AUTO:
        connection_mode = str(clean_metadata.get("connection_mode") or "").strip().lower()
        if connection_mode in {"local_companion", "local"}:
            requested = runtime_policy.EXECUTION_TARGET_LOCAL_COMPANION

    capability_state = runtime_policy._local_runtime_capability_state(
        runtime_policy._predict_required_capabilities_from_metadata(clean_metadata)
    )
    required_capabilities = list(capability_state.get("required_capabilities") or [])
    missing_capabilities = list(capability_state.get("missing_capabilities") or [])
    matching_runtime_ids = list(capability_state.get("matching_runtime_ids") or [])
    available_runtime_ids = list(capability_state.get("available_runtime_ids") or [])
    busy_matching_runtime_ids = list(capability_state.get("busy_matching_runtime_ids") or [])
    waiting_for_runtime = bool(capability_state.get("waiting_for_runtime"))
    waiting_for_capacity = bool(capability_state.get("waiting_for_capacity"))
    preferred_runtime_id = str(capability_state.get("preferred_runtime_id") or "").strip() or None
    preferred_runtime_label = str(capability_state.get("preferred_runtime_label") or "").strip() or None
    preferred_runtime_reason = str(capability_state.get("preferred_runtime_reason") or "").strip() or None
    busy_runtime_labels = list(capability_state.get("busy_runtime_labels") or [])
    queued_ahead_count = int(capability_state.get("queued_ahead_count") or 0)
    estimated_wait_band = str(capability_state.get("estimated_wait_band") or "unknown")
    local_pool = runtime_policy._local_runtime_pool_state() if not required_capabilities else {}

    selected = requested
    reason = ""
    fallback = None

    if required_capabilities:
        capability_text = ", ".join(required_capabilities)
        missing_text = ", ".join(missing_capabilities or required_capabilities)
        selected = runtime_policy.EXECUTION_TARGET_LOCAL_COMPANION
        if available_runtime_ids:
            preferred_text = preferred_runtime_label or "a capable local runtime"
            reason = f"Run requires local machine capabilities ({capability_text}) and will use {preferred_text}."
            if requested == runtime_policy.EXECUTION_TARGET_CLOUD:
                fallback = "Cloud runtime cannot satisfy these local capabilities; using local machine instead."
        elif matching_runtime_ids:
            preferred_text = preferred_runtime_label or "a capable local runtime"
            if schedule_id:
                reason = f"Scheduled run requires local machine capabilities ({capability_text}) and is waiting for machine capacity on {preferred_text}."
            else:
                reason = f"Run requires local machine capabilities ({capability_text}) and is waiting for machine capacity on {preferred_text}."
            fallback = "Capable local machines are online, but they are currently busy."
            if queued_ahead_count > 0:
                fallback = f"{fallback} {queued_ahead_count} similar local run{'s are' if queued_ahead_count != 1 else ' is'} ahead in the queue."
        else:
            if schedule_id:
                reason = f"Scheduled run requires local machine capabilities ({capability_text}) and is waiting for a capable machine."
            else:
                reason = f"Run requires local machine capabilities ({capability_text}) and is waiting for a machine that reports: {missing_text}."
            fallback = f"No online local machine currently reports required capabilities: {missing_text}."
    elif selected == runtime_policy.EXECUTION_TARGET_AUTO:
        if schedule_id or str(clean_metadata.get("source") or "").strip().lower() in {"weekly_scheduler", "scheduled"}:
            selected = runtime_policy.EXECUTION_TARGET_CLOUD
            reason = "Scheduled runs default to always-on cloud execution."
        else:
            if runtime_policy.ORION_LOCAL_COMPANION_ENABLED:
                local_online_ids = list(local_pool.get("online_runtime_ids") or [])
                local_available_ids = list(local_pool.get("available_runtime_ids") or [])
                local_busy_ids = list(local_pool.get("busy_runtime_ids") or [])
                local_busy_labels = list(local_pool.get("busy_runtime_labels") or [])
                local_preferred_runtime_id = str(local_pool.get("preferred_runtime_id") or "").strip() or None
                local_preferred_runtime_label = str(local_pool.get("preferred_runtime_label") or "").strip() or None
                local_preferred_runtime_reason = str(local_pool.get("preferred_runtime_reason") or "").strip() or None
                local_queued_ahead_count = int(local_pool.get("queued_ahead_count") or 0)
                local_estimated_wait_band = str(local_pool.get("estimated_wait_band") or "unknown")
                if local_available_ids:
                    selected = runtime_policy.EXECUTION_TARGET_LOCAL_COMPANION
                    reason = f"Automatic route will use {local_preferred_runtime_label or 'a local machine'} because local execution is available now."
                    matching_runtime_ids = local_online_ids
                    available_runtime_ids = local_available_ids
                    busy_matching_runtime_ids = local_busy_ids
                    waiting_for_runtime = False
                    waiting_for_capacity = False
                    preferred_runtime_id = local_preferred_runtime_id
                    preferred_runtime_label = local_preferred_runtime_label
                    preferred_runtime_reason = local_preferred_runtime_reason
                    busy_runtime_labels = local_busy_labels
                    queued_ahead_count = local_queued_ahead_count
                    estimated_wait_band = local_estimated_wait_band
                elif local_online_ids:
                    can_reroute_to_cloud, reroute_reason = runtime_policy._auto_cloud_capacity_fallback_allowed(clean_metadata)
                    if can_reroute_to_cloud:
                        selected = runtime_policy.EXECUTION_TARGET_CLOUD
                        reason = reroute_reason
                        fallback = f"{local_preferred_runtime_label or 'The local machine'} is busy right now, so Hekor is starting this in cloud runtime."
                    else:
                        selected = runtime_policy.EXECUTION_TARGET_LOCAL_COMPANION
                        reason = f"Automatic route prefers local execution and is waiting for capacity on {local_preferred_runtime_label or 'a local machine'}."
                        fallback = reroute_reason
                        matching_runtime_ids = local_online_ids
                        available_runtime_ids = local_available_ids
                        busy_matching_runtime_ids = local_busy_ids
                        waiting_for_runtime = False
                        waiting_for_capacity = True
                        preferred_runtime_id = local_preferred_runtime_id
                        preferred_runtime_label = local_preferred_runtime_label
                        preferred_runtime_reason = local_preferred_runtime_reason
                        busy_runtime_labels = local_busy_labels
                        queued_ahead_count = local_queued_ahead_count
                        estimated_wait_band = local_estimated_wait_band
                        if queued_ahead_count > 0:
                            fallback = f"{fallback} {queued_ahead_count} similar local run{'s are' if queued_ahead_count != 1 else ' is'} ahead in the queue."
                else:
                    selected = runtime_policy.EXECUTION_TARGET_CLOUD
                    reason = "Default runtime route is cloud because no local machine is online."
                    fallback = "Bring a local machine online if you want automatic tasks to prefer local execution."
            else:
                selected = runtime_policy.EXECUTION_TARGET_CLOUD
                reason = "Default runtime route is cloud because Gateway is unavailable."
    elif selected == runtime_policy.EXECUTION_TARGET_LOCAL_COMPANION:
        if schedule_id:
            selected = runtime_policy.EXECUTION_TARGET_CLOUD
            reason = "Scheduled runs require cloud reliability."
            fallback = "Gateway route requested, but schedules run in cloud mode."
        elif runtime_policy.ORION_LOCAL_COMPANION_ENABLED:
            reason = "Run requested on Gateway."
        else:
            can_fallback_to_cloud, fallback_reason = runtime_policy._auto_cloud_capacity_fallback_allowed(clean_metadata)
            if can_fallback_to_cloud:
                selected = runtime_policy.EXECUTION_TARGET_CLOUD
                reason = "Gateway requested but not available; using cloud fallback."
                fallback = "Gateway is not enabled on this workspace; using cloud fallback."
            else:
                selected = runtime_policy.EXECUTION_TARGET_LOCAL_COMPANION
                reason = "Gateway requested but unavailable; fallback to cloud is blocked by policy."
                fallback = fallback_reason
                waiting_for_runtime = True
                waiting_for_capacity = False
    else:
        reason = "Run requested in cloud mode."
        virtual_runtime_unavailable = bool(
            clean_metadata.get("virtual_runtime_unavailable")
            or clean_metadata.get("cloud_runtime_unavailable")
            or clean_metadata.get("virtual_runtime_down")
        )
        if virtual_runtime_unavailable:
            allow_local_fallback, local_fallback_reason = runtime_policy._local_fallback_risk_allows_runtime(clean_metadata)
            if allow_local_fallback:
                selected = runtime_policy.EXECUTION_TARGET_LOCAL_COMPANION
                reason = "Virtual runtime unavailable; local fallback selected for low-risk route."
                fallback = local_fallback_reason
            else:
                selected = runtime_policy.EXECUTION_TARGET_CLOUD
                reason = "Virtual runtime unavailable and local fallback blocked by risk policy."
                fallback = local_fallback_reason
                waiting_for_runtime = True
                waiting_for_capacity = False

    return {
        "requested": requested,
        "selected": selected,
        "reason": reason,
        "fallback": fallback,
        "runtime_choice_requested": runtime_choice.get("requested"),
        "runtime_choice_selected": runtime_choice.get("selected"),
        "runtime_choice_reason": runtime_choice.get("reason"),
        "runtime_choice_source": runtime_choice.get("source"),
        "runtime_choice_applied": runtime_choice_applied,
        "runtime_choice_override_requested": bool(runtime_choice.get("override_requested")),
        "runtime_choice_policy_warning": str(runtime_choice.get("policy_warning") or ""),
        "studio_runtime_options": list(runtime_choice.get("studio_runtime_options") or []),
        "studio_template_requirements": dict(runtime_choice.get("studio_template_requirements") or {}),
        "runtime_provider_requested": runtime_provider_requested,
        "runtime_contract_interface": runtime_policy.RUNTIME_CONTRACT_INTERFACE_ID,
        "runtime_contract_kind": runtime_policy._runtime_contract_kind_from_choice(runtime_choice.get("selected")),
        "runtime_contract_methods": list(runtime_policy.RUNTIME_CONTRACT_METHODS),
        "runtime_contract_states": list(runtime_policy.RUNTIME_CONTRACT_STATES),
        "local_companion_enabled": runtime_policy.ORION_LOCAL_COMPANION_ENABLED,
        "required_capabilities": required_capabilities,
        "missing_capabilities": missing_capabilities,
        "matching_runtime_ids": matching_runtime_ids,
        "matching_runtime_count": int(capability_state.get("matching_runtime_count") or 0),
        "available_runtime_ids": available_runtime_ids,
        "available_runtime_count": int(capability_state.get("available_runtime_count") or 0),
        "busy_matching_runtime_ids": busy_matching_runtime_ids,
        "waiting_for_runtime": waiting_for_runtime,
        "waiting_for_capacity": waiting_for_capacity,
        "preferred_runtime_id": preferred_runtime_id,
        "preferred_runtime_label": preferred_runtime_label,
        "preferred_runtime_reason": preferred_runtime_reason,
        "busy_runtime_labels": busy_runtime_labels,
        "queued_ahead_count": queued_ahead_count,
        "estimated_wait_band": estimated_wait_band,
    }


def evaluate_action_policy(
    action_counts: Dict[str, int],
    policy_mode: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
    selected_target: Optional[str] = None,
) -> Dict[str, Any]:
    runtime_policy = _runtime_policy_module()
    metadata = metadata if isinstance(metadata, dict) else {}
    policy = runtime_policy._action_policy_from_metadata(metadata)
    blocked_actions = set(policy.get("blocked_actions", set()))
    approval_actions = set(policy.get("approval_actions", set()))
    allow_actions = set(policy.get("allow_actions", set()))
    block_cloud_critical = bool(policy.get("block_cloud_critical", True))

    target = normalize_execution_target(
        selected_target
        or metadata.get("execution_target_selected")
        or metadata.get("execution_target")
    )
    runtime_mode = resolve_runtime_policy_mode(metadata, selected_target=target)
    effective_policy_mode = normalize_policy_mode(policy_mode or runtime_mode.get("policy_mode"))

    evaluated: List[Dict[str, Any]] = []
    denied_actions: List[str] = []
    confirmation_required_actions: List[str] = []

    for action, count in sorted(action_counts.items()):
        if count <= 0:
            continue
        normalized = _normalize_action_id(action)
        if not normalized:
            continue
        classification = runtime_policy.classify_runtime_action(normalized, count=count, target=target)
        execution_decision = "allow"
        reasons: List[str] = []

        if normalized in blocked_actions and normalized not in allow_actions:
            execution_decision = "deny"
            reasons.append("Blocked by action policy.")
        elif normalized in approval_actions and normalized not in allow_actions:
            execution_decision = "require_confirmation"
            reasons.append("Approval required by action policy.")

        if (
            execution_decision != "deny"
            and block_cloud_critical
            and target == runtime_policy.EXECUTION_TARGET_CLOUD
            and bool(classification.get("destructive_risk"))
            and normalized not in allow_actions
        ):
            execution_decision = "deny"
            reasons.append("Critical action is blocked in cloud runtime.")

        if execution_decision != "deny":
            runtime_decision = _runtime_action_policy_decision(
                classification,
                target=target,
                policy_mode=effective_policy_mode,
                metadata=metadata,
            )
            execution_decision = str(runtime_decision.get("execution_decision") or "allow").strip().lower() or "allow"
            if runtime_decision.get("reason"):
                reasons.append(str(runtime_decision.get("reason")))

        if execution_decision == "deny":
            denied_actions.append(normalized)
        elif execution_decision == "require_confirmation":
            confirmation_required_actions.append(normalized)

        evaluated.append(
            {
                "action": normalized,
                "count": count,
                "risk": runtime_policy.ACTION_RISK_LEVELS.get(normalized, "medium"),
                "policy_mode": effective_policy_mode,
                "classification": classification,
                "execution_decision": execution_decision,
                "decision": runtime_policy._legacy_decision_label(execution_decision),
                "reason": " ".join(reasons).strip() or None,
            }
        )

    confirmation_reason = ""
    if confirmation_required_actions:
        uniq = sorted(set(confirmation_required_actions))
        confirmation_reason = f"Runtime policy requires one-time confirmation for: {', '.join(uniq)}."
    return {
        "evaluated": evaluated,
        "policy_mode": effective_policy_mode,
        "denied_actions": sorted(set(denied_actions)),
        "confirmation_required_actions": sorted(set(confirmation_required_actions)),
        "requires_confirmation": bool(confirmation_required_actions),
        "confirmation_reason": confirmation_reason,
        "blocked_actions": sorted(set(denied_actions)),
        "approval_actions": sorted(set(confirmation_required_actions)),
        "requires_approval": bool(confirmation_required_actions),
        "approval_reason": confirmation_reason,
        "target": target,
    }


def summarize_action_policy_eval(eval_data: Dict[str, Any]) -> str:
    evaluated = eval_data.get("evaluated") if isinstance(eval_data.get("evaluated"), list) else []
    blocked = eval_data.get("denied_actions") if isinstance(eval_data.get("denied_actions"), list) else []
    approval = (
        eval_data.get("confirmation_required_actions")
        if isinstance(eval_data.get("confirmation_required_actions"), list)
        else []
    )
    if not evaluated:
        return "Action policy: no actionable operations detected."
    return (
        f"Action policy: evaluated={len(evaluated)} "
        f"confirmation_required={len(approval)} denied={len(blocked)}."
    )


def _operation_capability_id_for_policy(operation: Dict[str, Any]) -> str:
    capability = str(operation.get("capability") or "").strip().lower()
    if capability:
        return capability
    tool_id = _normalize_action_id(operation.get("tool") or operation.get("action"))
    if tool_id == "computer_control":
        action_name = _normalize_action_id(operation.get("action"))
        if action_name:
            return f"computer_control.{action_name}"
    return tool_id


def _local_execution_dangerous_action_items(
    context: Dict[str, Any],
    *,
    policy_mode: str,
    target: str,
    capability_metadata_root: Optional[Path],
) -> List[Dict[str, Any]]:
    metadata = context.get("metadata") if isinstance(context.get("metadata"), dict) else {}
    pack_inputs = metadata.get("pack_inputs") if isinstance(metadata.get("pack_inputs"), dict) else {}
    operations = pack_inputs.get("operations") if isinstance(pack_inputs.get("operations"), list) else []
    if not operations and isinstance(pack_inputs, dict) and pack_inputs:
        operations = [pack_inputs]

    items: List[Dict[str, Any]] = []
    for index, raw_operation in enumerate(operations):
        if not isinstance(raw_operation, dict):
            continue
        capability_id = _operation_capability_id_for_policy(raw_operation)
        if not capability_id:
            continue
        evaluation = evaluate_dangerous_computer_action_policy(
            raw_operation,
            metadata=metadata,
            capability_id=capability_id,
            policy_mode=policy_mode,
            target=target,
        )
        dangerous_classes = list(evaluation.get("dangerous_action_classes") or [])
        if not dangerous_classes:
            continue
        detail = capability_metadata(capability_id, capability_metadata_root or Path(__file__).resolve().parents[1])
        labels = list(evaluation.get("dangerous_action_labels") or [])
        label = ", ".join(labels) if labels else capability_id.replace("_", " ")
        items.append(
            {
                "tool_id": capability_id,
                "execution_decision": str(evaluation.get("execution_decision") or "allow").strip().lower() or "allow",
                "decision": str(evaluation.get("decision") or "allow").strip().lower() or "allow",
                "reason": str(evaluation.get("reason") or "").strip() or None,
                "policy_mode": str(policy_mode or "").strip().lower() or None,
                "target": str(target or "").strip().lower() or None,
                "is_sensitive": True,
                "is_critical": True,
                "classification": {
                    "dangerous_action_classes": dangerous_classes,
                    "dangerous_action_labels": labels,
                    "action_summary": str(raw_operation.get("summary") or raw_operation.get("target_description") or raw_operation.get("target_text") or raw_operation.get("action") or capability_id).strip() or capability_id,
                },
                "approval_label": label,
                "capabilities": [detail] if isinstance(detail, dict) else None,
                "metadata": {
                    "operation_index": index,
                    "dangerous_action_classes": dangerous_classes,
                    "dangerous_action_labels": labels,
                    "owner_or_admin_mode": bool(evaluation.get("owner_or_admin_mode")),
                    "explicitly_allowed_classes": list(evaluation.get("explicitly_allowed_classes") or []),
                },
            }
        )
    return items


def _rust_capability_for_tool_policy(tool_id: str, *, known_read_only_system_probe: bool = False) -> str:
    clean_tool_id = _normalize_action_id(tool_id) or str(tool_id or "").strip().lower()
    if known_read_only_system_probe:
        return "read_files"
    mapping = {
        "shell.execute": "shell_execute",
        "filesystem.read": "filesystem_read",
        "filesystem.read_write": "filesystem_read_write",
        "browser_automation.interactive": "browser_automation_interactive",
        "http_request": "network_access",
        "external_research": "network_access",
        "draft_email": "communication_draft",
        "create_calendar_event": "calendar_write",
        "publish_content": "external_write",
        "document_create": "external_write",
        "document_update": "external_write",
        "presentation_create": "external_write",
        "presentation_update": "external_write",
        "spreadsheet_create": "external_write",
        "spreadsheet_update": "external_write",
        "spreadsheet_append": "external_write",
        "spreadsheet_read": "read_files",
        "screenshot.capture": "read_files",
        "send_message": "send_message",
        "post_message": "post_message",
        "reply_message": "reply_message",
        "transfer_funds": "transfer_funds",
        "connector.action.read": "connector_action_read",
        "connector.action.write": "external_write",
        "connector.action.execute": "external_write",
    }
    return mapping.get(clean_tool_id, clean_tool_id.replace(".", "_"))


def _rust_tool_policy_result(
    *,
    tool_id: str,
    effective_target: str,
    effective_policy_mode: str,
    metadata: Dict[str, Any],
    requested_capability_ids: List[str],
    workspace_denied_capabilities: set[str],
    workspace_allowed_capabilities: set[str],
    workspace_role: str,
    action_blocked: bool,
    action_approval_required: bool,
    uses_capability_path: bool,
    safe_raw_shell_command: bool,
    disabled_capability_state: Optional[Dict[str, Any]],
    unsupported_capability: Optional[Dict[str, Any]],
    block_cloud_critical: bool,
    runtime_decision: Optional[Dict[str, Any]],
    reason: str,
    classification: Dict[str, Any],
    known_read_only_system_probe: bool,
) -> Dict[str, Any]:
    rust_capability = _rust_capability_for_tool_policy(
        tool_id,
        known_read_only_system_probe=known_read_only_system_probe,
    )
    action_type = str(classification.get("action_type") or "").strip().lower()
    policy_payload: Dict[str, Any] = {
        "policy_id": "runtime-tool-policy",
        "policy_version": 1,
        "autonomy_mode": "cautious",
        "allowed_capabilities": [rust_capability] if (known_read_only_system_probe or action_type == "read") else [],
        "approval_required_capabilities": [],
        "blocked_capabilities": [],
        "domain_allowlist": list(metadata.get("domain_allowlist") or []),
        "filesystem_scope": list(metadata.get("filesystem_scope") or []),
        "blocked_filesystem_scope": list(metadata.get("blocked_filesystem_scope") or []),
    }
    policy_context = {
        "tool_id": tool_id,
        "requested_capability_ids": list(requested_capability_ids or []),
        "workspace_denied_capabilities": sorted(workspace_denied_capabilities),
        "workspace_allowed_capabilities": sorted(workspace_allowed_capabilities),
        "workspace_role": workspace_role,
        "action_blocked": bool(action_blocked),
        "action_approval_required": bool(action_approval_required),
        "uses_capability_path": bool(uses_capability_path),
        "safe_raw_shell_command": bool(safe_raw_shell_command),
        "disabled_capability": bool(disabled_capability_state),
        "unsupported_capability": (
            unsupported_capability.get("id") if isinstance(unsupported_capability, dict) else None
        ),
        "tool_disabled": bool(tool_id in _runtime_policy_module().TOOL_CONTRACTS and not _runtime_policy_module().is_tool_enabled(tool_id)),
        "cloud_target": bool(effective_target == _runtime_policy_module().EXECUTION_TARGET_CLOUD),
        "critical": bool(_runtime_policy_module().TOOL_POLICY.is_critical(tool_id)),
        "block_cloud_critical": bool(block_cloud_critical),
        "blocked_raw_shell_command": bool(tool_id == "shell.execute" and not safe_raw_shell_command and not uses_capability_path),
    }
    runtime_policy_context = {
        "execution_decision": (
            str(runtime_decision.get("execution_decision") or "").strip().lower()
            if isinstance(runtime_decision, dict)
            else ""
        ),
        "reason": (
            str(runtime_decision.get("reason") or "").strip()
            if isinstance(runtime_decision, dict)
            else ""
        ),
    }
    raw_shell_operations = (
        _runtime_policy_module()._iter_raw_shell_operations(metadata)
        if tool_id == "shell.execute" and not uses_capability_path
        else []
    )
    rust_execution_plan: Optional[Dict[str, Any]] = None
    try:
        if tool_id == "shell.execute" and not uses_capability_path and raw_shell_operations and safe_raw_shell_command:
            primary_operation = raw_shell_operations[0] if isinstance(raw_shell_operations[0], dict) else {}
            raw_command = _runtime_policy_module()._raw_shell_command_text(primary_operation)
            raw_argv = primary_operation.get("argv") if isinstance(primary_operation.get("argv"), list) else None
            rust_execution_plan = rust_runtime_kernel_client.run_runtime_kernel_enforced(
                "execution-plan",
                {
                    "command": raw_command,
                    "argv": raw_argv,
                    "action_class": str(classification.get("action_type") or classification.get("action_class") or "execute"),
                    "requested_path": metadata.get("target_path") or metadata.get("path"),
                    "requested_domain": metadata.get("target_domain") or metadata.get("domain"),
                },
                allow_approval_required=True,
            )
            plan_decision = str(rust_execution_plan.get("decision") or "").strip().lower()
            plan_next_action = str(rust_execution_plan.get("next_action") or "").strip()
            expected_plan_next_action = (
                "request_tool_execution_approval"
                if (bool(rust_execution_plan.get("approval_required")) or plan_decision in {"require_approval", "approval_required"})
                else "allow_tool_execution"
            )
            if plan_decision not in {"block", "denied"} and plan_next_action != expected_plan_next_action:
                return {
                    "execution_decision": "deny",
                    "decision": "blocked",
                    "reason": f"unexpected_next_action:{plan_next_action or 'missing'}",
                    "rust_authorization": dict(rust_execution_plan),
                    "rust_execution_plan": dict(rust_execution_plan),
                }
        if tool_id == "shell.execute" and not uses_capability_path and raw_shell_operations:
            primary_operation = raw_shell_operations[0] if isinstance(raw_shell_operations[0], dict) else {}
            raw_command = _runtime_policy_module()._raw_shell_command_text(primary_operation)
            raw_argv = primary_operation.get("argv") if isinstance(primary_operation.get("argv"), list) else None
            decision = rust_runtime_kernel_client.run_runtime_kernel_enforced(
                "authorize-execution",
                {
                    "policy": policy_payload,
                    "command": raw_command,
                    "argv": raw_argv,
                    "capability": rust_capability,
                    "action_class": str(classification.get("action_type") or classification.get("action_class") or "execute"),
                    "target_summary": raw_command or tool_id,
                    "requested_domain": metadata.get("target_domain") or metadata.get("domain"),
                    "requested_path": metadata.get("target_path") or metadata.get("path"),
                    "payload": {
                        "tool_id": tool_id,
                        "target": effective_target,
                        "policy_mode": effective_policy_mode,
                        "reason": reason,
                        "classification": classification,
                        "policy_context": policy_context,
                        "runtime_policy_context": runtime_policy_context,
                    },
                },
                allow_approval_required=True,
            )
        else:
            decision = rust_runtime_kernel_client.run_runtime_kernel_enforced(
                "authorize-request",
                {
                    "policy": policy_payload,
                    "capability": rust_capability,
                    "action_class": str(classification.get("action_type") or classification.get("action_class") or "execute"),
                    "target_summary": tool_id,
                    "requested_domain": metadata.get("target_domain") or metadata.get("domain"),
                    "requested_path": metadata.get("target_path") or metadata.get("path"),
                    "payload": {
                        "tool_id": tool_id,
                        "target": effective_target,
                        "policy_mode": effective_policy_mode,
                        "reason": reason,
                        "classification": classification,
                        "policy_context": policy_context,
                        "runtime_policy_context": runtime_policy_context,
                    },
                },
                allow_approval_required=True,
            )
    except rust_runtime_kernel_client.RustKernelDecisionError as exc:
        decision = dict(exc.decision)
    # Dev mode: override Rust kernel approval for all hardware actions
    if _is_local_development_mode():
        decision["approval_required"] = False
        decision["decision"] = "allow"
    rust_decision = str(decision.get("decision") or "").strip().lower()
    rust_next_action = str(decision.get("next_action") or "").strip()
    decision_reason = str(decision.get("reason") or "").strip()
    nested_authorization = decision.get("authorization") if isinstance(decision.get("authorization"), dict) else {}
    nested_authorization_reason = str(nested_authorization.get("reason") or "").strip()
    if decision_reason in {"execution_allowed", "execution_requires_approval"} and nested_authorization_reason:
        decision_reason = nested_authorization_reason
    expected_next_action = (
        "request_tool_execution_approval"
        if (bool(decision.get("approval_required")) or rust_decision in {"require_approval", "approval_required"})
        else "allow_tool_execution"
    )
    if rust_decision not in {"block", "denied"} and rust_next_action != expected_next_action:
        return {
            "execution_decision": "deny",
            "decision": "blocked",
            "reason": f"unexpected_next_action:{rust_next_action or 'missing'}",
            "rust_authorization": dict(decision),
            "rust_execution_plan": dict(rust_execution_plan) if isinstance(rust_execution_plan, dict) else None,
        }
    if rust_decision == "block" or decision.get("ok") is False:
        return {
            "execution_decision": "deny",
            "decision": "blocked",
            "reason": str(decision_reason or reason or "rust_authorization_blocked").strip(),
            "rust_authorization": dict(decision),
            "rust_execution_plan": dict(rust_execution_plan) if isinstance(rust_execution_plan, dict) else None,
        }
    if bool(decision.get("approval_required")) or rust_decision in {"require_approval", "approval_required"}:
        return {
            "execution_decision": "require_confirmation",
            "decision": "approval_required",
            "reason": str(decision_reason or reason or "rust_authorization_requires_approval").strip(),
            "rust_authorization": dict(decision),
            "rust_execution_plan": dict(rust_execution_plan) if isinstance(rust_execution_plan, dict) else None,
        }
    return {
        "execution_decision": "allow",
        "decision": "allow",
        "reason": str(decision_reason or reason or "rust_authorization_allowed").strip(),
        "rust_authorization": dict(decision),
        "rust_execution_plan": dict(rust_execution_plan) if isinstance(rust_execution_plan, dict) else None,
    }


def evaluate_tool_policy_decision(
    tool_id: str,
    trust_mode: str,
    target: str,
    metadata: Optional[Dict[str, Any]] = None,
    capability_ids: Optional[List[str]] = None,
) -> Dict[str, Any]:
    runtime_policy = _runtime_policy_module()
    clean_tool_id = _normalize_action_id(tool_id) or str(tool_id or "").strip().lower()
    effective_target = normalize_execution_target(target)
    metadata = metadata if isinstance(metadata, dict) else {}
    runtime_mode = resolve_runtime_policy_mode(metadata, selected_target=effective_target)
    effective_policy_mode = runtime_mode.get("policy_mode")
    policy = runtime_policy._action_policy_from_metadata(metadata)

    blocked_actions = policy.get("blocked_actions", set())
    approval_actions = policy.get("approval_actions", set())
    block_cloud_critical = bool(policy.get("block_cloud_critical", True))

    browser_policy = (
        metadata.get("browser_automation_policy")
        if isinstance(metadata.get("browser_automation_policy"), dict)
        else {}
    )
    browser_requires_approval = (
        bool(browser_policy.get("requires_approval"))
        if clean_tool_id == "browser_automation.interactive"
        else False
    )
    browser_profile = (
        str(browser_policy.get("profile") or "").strip().lower()
        if clean_tool_id == "browser_automation.interactive"
        else ""
    )
    browser_privileged_actions = (
        list(browser_policy.get("privileged_actions") or [])
        if clean_tool_id == "browser_automation.interactive"
        else []
    )
    workspace_capability_policy = (
        metadata.get("workspace_capability_policy")
        if isinstance(metadata.get("workspace_capability_policy"), dict)
        else {}
    )
    workspace_denied_capabilities = {
        str(item or "").strip().lower()
        for item in (workspace_capability_policy.get("deny") or [])
        if str(item or "").strip()
    }
    workspace_allowed_capabilities = {
        str(item or "").strip().lower()
        for item in (workspace_capability_policy.get("allow") or [])
        if str(item or "").strip()
    }
    workspace_role = _normalize_action_id(
        metadata.get("workspace_role")
        or metadata.get("owner_role")
        or metadata.get("role")
    )
    capability_ids = [str(item).strip().lower() for item in (capability_ids or []) if str(item).strip()]
    requested_capability_ids = capability_ids or ([clean_tool_id] if clean_tool_id else [])
    tenant_id = str(metadata.get("tenant_id") or "").strip() or None
    workspace_id = str(metadata.get("workspace_id") or "").strip() or None
    machine_id = str(
        metadata.get("machine_id")
        or metadata.get("machine_target")
        or (
            (metadata.get("computer_action_policy") or {}).get("machine_id")
            if isinstance(metadata.get("computer_action_policy"), dict)
            else ""
        )
        or ""
    ).strip() or None
    capability_details = [
        detail
        for detail in (
            capability_metadata(capability_id, Path(__file__).resolve().parents[1])
            for capability_id in capability_ids
        )
        if isinstance(detail, dict)
    ]
    uses_capability_path = clean_tool_id == "shell.execute" and bool(capability_details)
    uses_raw_command_path = clean_tool_id == "shell.execute" and not uses_capability_path
    approval_candidates = set(requested_capability_ids)
    if clean_tool_id:
        approval_candidates.add(clean_tool_id)
    requires_explicit_approval = any(
        candidate in approval_actions for candidate in approval_candidates if candidate
    )
    unsupported_capability = next(
        (
            detail
            for detail in capability_details
            if not bool(detail.get("platform_supported"))
        ),
        None,
    )

    known_read_only_system_probe = (
        uses_raw_command_path and runtime_policy._metadata_is_known_read_only_system_probe(metadata)
    )
    safe_raw_shell_command = uses_raw_command_path and runtime_policy._metadata_allows_safe_raw_shell(metadata)
    classification = runtime_policy.classify_runtime_action(clean_tool_id, count=1, target=effective_target)
    if clean_tool_id == "shell.execute" and known_read_only_system_probe:
        classification = {
            **classification,
            "action_type": runtime_policy.ACTION_TYPE_READ,
            "target_system": "local_device",
            "reversibility": True,
            "external_visibility": False,
            "destructive_risk": False,
            "requires_contract_approval": False,
            "safe_raw_shell_command": True,
            "known_read_only_system_probe": True,
        }
    elif clean_tool_id == "shell.execute" and safe_raw_shell_command:
        classification = {
            **classification,
            "action_type": runtime_policy.ACTION_TYPE_REVERSIBLE_WRITE,
            "target_system": "local_device",
            "reversibility": True,
            "external_visibility": False,
            "destructive_risk": False,
            "safe_raw_shell_command": True,
        }

    policy_blocked = False
    policy_approval_required = False
    policy_allowed = False
    reason = "policy_allow_default"
    runtime_decision: Optional[Dict[str, Any]] = None
    disabled_capability_state = next(
        (
            {
                **disable_state,
                "capability_id": capability_id,
            }
            for capability_id in requested_capability_ids
            for disable_state in [
                safe_mode_service.resolve_capability_disable_state(
                    capability_id,
                    tenant_id=tenant_id,
                    workspace_id=workspace_id,
                    machine_id=machine_id,
                )
            ]
            if bool(disable_state.get("disabled"))
        ),
        None,
    )

    if disabled_capability_state:
        policy_blocked = True
        reason = "capability_disabled_by_policy_scope"
    elif any(
        capability_id in workspace_denied_capabilities or "*" in workspace_denied_capabilities
        for capability_id in requested_capability_ids
    ):
        policy_blocked = True
        reason = "workspace_capability_denied"
    elif (
        workspace_allowed_capabilities
        and "*" not in workspace_allowed_capabilities
        and any(capability_id not in workspace_allowed_capabilities for capability_id in requested_capability_ids)
        and workspace_role != "owner"
    ):
        policy_blocked = True
        reason = "workspace_capability_not_granted"
    elif clean_tool_id in runtime_policy.TOOL_CONTRACTS and not runtime_policy.is_tool_enabled(clean_tool_id):
        policy_blocked = True
        reason = "tool_disabled"
    elif unsupported_capability:
        policy_blocked = True
        reason = "blocked_unsupported_capability"
    elif uses_raw_command_path and not safe_raw_shell_command:
        policy_blocked = True
        reason = "blocked_raw_shell_command"
    elif clean_tool_id in blocked_actions and not uses_capability_path and not safe_raw_shell_command:
        policy_blocked = True
        reason = "blocked_by_action_policy"
    elif requires_explicit_approval and not safe_raw_shell_command:
        policy_approval_required = True
        reason = "approval_required_by_action_policy"
    elif (
        effective_target == runtime_policy.EXECUTION_TARGET_CLOUD
        and runtime_policy.TOOL_POLICY.is_critical(clean_tool_id)
        and block_cloud_critical
    ):
        policy_blocked = True
        reason = "blocked_cloud_critical"
    else:
        runtime_decision = _runtime_action_policy_decision(
            classification,
            target=effective_target,
            policy_mode=effective_policy_mode,
            metadata=metadata,
        )
        runtime_execution_decision = str(runtime_decision.get("execution_decision") or "allow").strip().lower() or "allow"
        reason = str(runtime_decision.get("reason") or "").strip() or reason
        if runtime_execution_decision == "deny":
            policy_blocked = True
        elif runtime_execution_decision == "require_confirmation":
            policy_approval_required = True
        else:
            policy_allowed = True

    rust_policy_result = _rust_tool_policy_result(
        tool_id=clean_tool_id,
        effective_target=effective_target,
        effective_policy_mode=str(effective_policy_mode or ""),
        metadata=metadata,
        requested_capability_ids=requested_capability_ids,
        workspace_denied_capabilities=workspace_denied_capabilities,
        workspace_allowed_capabilities=workspace_allowed_capabilities,
        workspace_role=workspace_role,
        action_blocked=bool(clean_tool_id in blocked_actions and not uses_capability_path and not safe_raw_shell_command),
        action_approval_required=bool(requires_explicit_approval),
        uses_capability_path=uses_capability_path,
        safe_raw_shell_command=safe_raw_shell_command,
        disabled_capability_state=disabled_capability_state,
        unsupported_capability=unsupported_capability if isinstance(unsupported_capability, dict) else None,
        block_cloud_critical=block_cloud_critical,
        runtime_decision=runtime_decision if isinstance(runtime_decision, dict) else None,
        reason=reason,
        classification=classification if isinstance(classification, dict) else {},
        known_read_only_system_probe=known_read_only_system_probe,
    )
    execution_decision = str(rust_policy_result.get("execution_decision") or "allow")
    reason = str(rust_policy_result.get("reason") or reason)

    if (
        execution_decision != "deny"
        and clean_tool_id == "browser_automation.interactive"
        and effective_target == runtime_policy.EXECUTION_TARGET_LOCAL_COMPANION
        and browser_profile in {"authenticated_interactive", "authenticated_privileged"}
    ):
        execution_decision = "require_confirmation"
        reason = (
            "browser_authenticated_privileged_requires_reviewed_approval"
            if browser_privileged_actions
            else "browser_authenticated_interactive_requires_reviewed_approval"
        )

    if (
        execution_decision != "deny"
        and clean_tool_id == "browser_automation.interactive"
        and browser_requires_approval
        and not (
            effective_target == runtime_policy.EXECUTION_TARGET_LOCAL_COMPANION
            and browser_profile in {"authenticated_interactive", "authenticated_privileged"}
        )
    ):
        if effective_policy_mode == runtime_policy.POLICY_MODE_LOCAL_DEFAULT:
            execution_decision = "require_confirmation"
            reason = (
                "browser_authenticated_privileged_requires_approval"
                if browser_privileged_actions
                else "browser_authenticated_requires_approval"
            )

    result = {
        "tool_id": clean_tool_id,
        "capability_ids": capability_ids or None,
        "capabilities": capability_details or None,
        "execution_decision": execution_decision,
        "decision": runtime_policy._legacy_decision_label(execution_decision),
        "reason": reason,
        "policy_mode": effective_policy_mode,
        "runtime_policy_source": runtime_mode.get("source"),
        "target": effective_target,
        "is_sensitive": runtime_policy.TOOL_POLICY.is_sensitive(clean_tool_id) or browser_requires_approval,
        "is_critical": runtime_policy.TOOL_POLICY.is_critical(clean_tool_id),
        "classification": classification,
        "uses_capability_path": uses_capability_path,
        "uses_raw_command_path": uses_raw_command_path,
        "safe_raw_shell_command": safe_raw_shell_command,
        "known_read_only_system_probe": known_read_only_system_probe,
        "unsupported_capability": unsupported_capability.get("id") if isinstance(unsupported_capability, dict) else None,
        "browser_security_profile": browser_profile or None,
        "browser_requires_approval": browser_requires_approval,
        "browser_privileged_actions": browser_privileged_actions or None,
        "runtime_trust_zone": (
            (runtime_decision.get("runtime_trust_zone") if isinstance(runtime_decision, dict) else None)
            or _runtime_policy_module().resolve_elevated_access(
                metadata,
                selected_target=effective_target,
                policy_mode=effective_policy_mode,
            ).get("runtime_trust_zone")
        ),
        "elevated": runtime_decision.get("elevated") if isinstance(runtime_decision, dict) else None,
        "policy_scope_precedence": list(
            metadata.get("policy_scope_precedence") or ["global", "tenant", "workspace", "machine", "capability"]
        ),
        "disabled_capability_state": disabled_capability_state,
        "rust_authorization": rust_policy_result.get("rust_authorization"),
        "rust_execution_plan": rust_policy_result.get("rust_execution_plan"),
        "decision_id": (
            (rust_policy_result.get("rust_authorization") or {}).get("decision_id")
            if isinstance(rust_policy_result.get("rust_authorization"), dict)
            else None
        ),
        "rust_execution_plan_command": (
            "execution-plan"
            if clean_tool_id == "shell.execute" and uses_raw_command_path and safe_raw_shell_command
            else None
        ),
        "rust_authorization_command": (
            "authorize-execution"
            if clean_tool_id == "shell.execute" and uses_raw_command_path
            else "authorize-request"
        ),
        "reviewed_approval_required": bool(
            clean_tool_id == "browser_automation.interactive"
            and effective_target == runtime_policy.EXECUTION_TARGET_LOCAL_COMPANION
            and browser_profile in {"authenticated_interactive", "authenticated_privileged"}
        ),
    }
    if str(result.get("execution_decision") or "").strip().lower() == "deny":
        _emit_tool_policy_denial_audit(
            tool_id=clean_tool_id,
            metadata=metadata,
            reason=str(result.get("reason") or "").strip(),
            target=effective_target,
            policy_mode=effective_policy_mode,
            classification=classification if isinstance(classification, dict) else {},
        )
    return result


def compute_tool_policy_precheck(
    context: Dict[str, Any],
    *,
    derive_browser_automation_policy_fn: Callable[[Dict[str, Any]], Dict[str, Any]],
    predict_tool_ids_for_context_fn: Callable[[Dict[str, Any]], List[str]],
    build_skill_contract_from_metadata_fn: Callable[[Dict[str, Any], List[str], str, str], Dict[str, Any]],
    predict_capability_ids_for_context_fn: Callable[[Dict[str, Any]], List[str]],
    apply_agent_machine_bypass_to_tool_policy_evaluation_fn: Callable[[Dict[str, Any]], Dict[str, Any]],
    capability_metadata_root: Optional[Path] = None,
) -> Dict[str, Any]:
    runtime_policy = _runtime_policy_module()
    metadata = context.get("metadata") if isinstance(context.get("metadata"), dict) else {}
    target = normalize_execution_target(
        metadata.get("execution_target_selected") or metadata.get("execution_target")
    )
    runtime_mode = resolve_runtime_policy_mode(metadata, selected_target=target)
    policy_mode = str(runtime_mode.get("policy_mode") or runtime_policy.POLICY_MODE_LOCAL_DEFAULT)
    tool_ids = predict_tool_ids_for_context_fn(context)
    skill_contract = build_skill_contract_from_metadata_fn(metadata, tool_ids, policy_mode, target)
    enforced_undeclared = (
        set(skill_contract.get("undeclared_tools") or [])
        if skill_contract.get("policy_mode") == "enforce"
        else set()
    )
    items: List[Dict[str, Any]] = []
    denied: List[str] = []
    require_confirmation: List[str] = []
    allowed: List[str] = []

    browser_policy = derive_browser_automation_policy_fn(context)
    evaluation_metadata = dict(metadata)
    if browser_policy:
        evaluation_metadata["browser_automation_policy"] = browser_policy

    capability_ids = predict_capability_ids_for_context_fn(context)
    capability_details = [
        detail
        for detail in (
            capability_metadata(capability_id, capability_metadata_root or Path(__file__).resolve().parent)
            for capability_id in capability_ids
        )
        if isinstance(detail, dict)
    ]
    capabilities_by_tool: Dict[str, List[str]] = {}
    for detail in capability_details:
        tool_for_capability = _normalize_action_id(detail.get("tool_id"))
        capability_id = str(detail.get("id") or "").strip().lower()
        if not tool_for_capability or not capability_id:
            continue
        capabilities_by_tool.setdefault(tool_for_capability, []).append(capability_id)

    for tool_id in tool_ids:
        item = evaluate_tool_policy_decision(
            tool_id=tool_id,
            trust_mode=policy_mode,
            target=target,
            metadata=evaluation_metadata,
            capability_ids=capabilities_by_tool.get(_normalize_action_id(tool_id), []),
        )
        if tool_id in enforced_undeclared:
            item = dict(item)
            item["execution_decision"] = "deny"
            item["decision"] = "blocked"
            item["reason"] = "skill_contract_missing_runtime_tool"
        elif skill_contract.get("declared_runtime_tools"):
            item = dict(item)
            item["skill_declared"] = tool_id in set(skill_contract.get("declared_runtime_tools") or [])

        item = apply_agent_machine_bypass_to_tool_policy_evaluation_fn(item)
        items.append(item)
        execution_decision = str(item.get("execution_decision") or "").strip().lower()
        clean_tool = str(item.get("tool_id") or tool_id).strip().lower()
        if execution_decision == "deny":
            denied.append(clean_tool)
        elif execution_decision == "require_confirmation":
            require_confirmation.append(clean_tool)
        else:
            allowed.append(clean_tool)

    dangerous_action_items = _local_execution_dangerous_action_items(
        context,
        policy_mode=policy_mode,
        target=target,
        capability_metadata_root=capability_metadata_root,
    )
    for item in dangerous_action_items:
        items.append(item)
        execution_decision = str(item.get("execution_decision") or "").strip().lower()
        clean_tool = str(item.get("tool_id") or "").strip().lower()
        if execution_decision == "deny":
            denied.append(clean_tool)
        elif execution_decision == "require_confirmation":
            require_confirmation.append(clean_tool)
        else:
            allowed.append(clean_tool)

    denied = sorted(set(denied))
    require_confirmation = sorted(set(require_confirmation))
    allowed = sorted(set(allowed).difference(denied).difference(require_confirmation))

    return {
        "policy_mode": policy_mode,
        "target": target,
        "tool_ids": tool_ids,
        "capability_ids": capability_ids,
        "capabilities": capability_details,
        "denied": denied,
        "require_confirmation": require_confirmation,
        "allowed": allowed,
        "denied_count": len(denied),
        "require_confirmation_count": len(require_confirmation),
        "allow_count": len(allowed),
        "blocked": denied,
        "approval_required": require_confirmation,
        "blocked_count": len(denied),
        "approval_required_count": len(require_confirmation),
        "items": items,
        "skill_contract": skill_contract,
        "browser_automation_policy": browser_policy or None,
    }


def shell_command_requires_approval(command: str, *, compact_text: Callable[[Any], str]) -> bool:
    compact = compact_text(command)
    if not compact:
        return False
    destructive_markers = (
        "rm -rf",
        "rm -r ",
        "rm -f ",
        "sudo rm",
        "del /f",
        "del /q",
        "rmdir /s",
        "format ",
        "mkfs",
        "diskutil erase",
        "shred ",
        "dd if=",
    )
    return any(marker in compact for marker in destructive_markers)


def file_write_requires_approval(arguments: Dict[str, Any]) -> bool:
    path = str(arguments.get("path") or arguments.get("file_path") or "").strip().lower()
    if not path:
        return False
    protected_markers = (
        "/etc/",
        "/bin/",
        "/usr/",
        "/system/",
        "/library/",
        ".ssh/",
        ".gnupg/",
        ".env",
        ".git/config",
    )
    return any(marker in path for marker in protected_markers)


def local_direct_tool_requires_approval(
    connector_id: str,
    action_id: str,
    arguments: Dict[str, Any],
    *,
    compact_text: Callable[[Any], str],
) -> bool:
    normalized_connector = str(connector_id or "").strip().lower()
    normalized_action = str(action_id or "").strip().lower()
    if normalized_connector == "shell" and normalized_action == "exec":
        return shell_command_requires_approval(str(arguments.get("command") or ""), compact_text=compact_text)
    if normalized_connector == "file" and normalized_action in {"delete", "remove", "unlink", "trash"}:
        return True
    if normalized_connector == "file" and normalized_action == "write":
        return file_write_requires_approval(arguments)
    if normalized_connector == "screenshot":
        return False
    if normalized_connector == "computer":
        return False
    return False


def browser_direct_tool_requires_approval(action_id: str) -> bool:
    normalized_action = str(action_id or "").strip().lower()
    return normalized_action in {"click", "fill", "execute_js", "download_file"}


def _hardware_action_payload(arguments: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(arguments, dict):
        return {}
    input_payload: Dict[str, Any] = {}
    raw_input = arguments.get("input")
    if isinstance(raw_input, dict):
        input_payload = dict(raw_input)
    elif isinstance(raw_input, str) and raw_input.strip():
        try:
            parsed = json.loads(raw_input)
        except Exception:
            parsed = None
        if isinstance(parsed, dict):
            input_payload = dict(parsed)
    action_data_payload: Dict[str, Any] = {}
    raw_action_data = input_payload.get("action_data") or arguments.get("action_data")
    if isinstance(raw_action_data, dict):
        action_data_payload = dict(raw_action_data)
    elif isinstance(raw_action_data, str) and raw_action_data.strip():
        try:
            parsed = json.loads(raw_action_data)
        except Exception:
            parsed = None
        if isinstance(parsed, dict):
            action_data_payload = dict(parsed)
    return {**arguments, **input_payload, **action_data_payload}


def _hardware_shell_command_argument(arguments: Dict[str, Any]) -> str:
    payload = _hardware_action_payload(arguments)
    nested = payload.get("arguments") if isinstance(payload.get("arguments"), dict) else {}
    params = payload.get("params") if isinstance(payload.get("params"), dict) else {}
    payload_command = payload.get("payload") if isinstance(payload.get("payload"), str) else ""
    return str(
        nested.get("command")
        or params.get("command")
        or payload.get("command")
        or payload.get("script")
        or payload_command
        or ""
    ).strip()


def approval_required_for_direct_tool(
    connector_id: str,
    action_id: str,
    arguments: Dict[str, Any],
    tool_capabilities: List[Dict[str, Any]],
    *,
    compact_text: Callable[[Any], str],
    http_request_requires_approval: Optional[Callable[[Any, Any], bool]] = None,
) -> bool:
    normalized_connector_id = str(connector_id or "").strip().lower()
    normalized_action_id = str(action_id or "").strip()
    if http_request_requires_approval is None:
        from server_modules.tools_http import (
            http_request_requires_approval as http_request_requires_approval_fn,
        )

        http_request_requires_approval = http_request_requires_approval_fn
    if normalized_connector_id == "http" and normalized_action_id == "request":
        return http_request_requires_approval(arguments.get("method") or "GET", arguments.get("url") or "")
    if normalized_connector_id == "browser":
        return browser_direct_tool_requires_approval(normalized_action_id)
    if normalized_connector_id == "hardware":
        hardware_payload = _hardware_action_payload(arguments)
        capability_id = str(
            hardware_payload.get("capability_id")
            or hardware_payload.get("action")
            or hardware_payload.get("tool")
            or ""
        ).strip()
        if capability_id:
            normalized_hardware_capability = capability_id.lower().replace("_", ".")
            if (
                (
                    canonical_capability_id(capability_id) == "shell.execute"
                    or normalized_hardware_capability
                    in {"shell", "shell.execute", "shell.exec", "run", "run.command", "command"}
                )
                and skills_service._safe_direct_shell_command(_hardware_shell_command_argument(arguments))
            ):
                return False
            return False
        return True
    if normalized_connector_id in {"file", "shell", "screenshot", "computer"}:
        return local_direct_tool_requires_approval(
            normalized_connector_id,
            normalized_action_id,
            arguments,
            compact_text=compact_text,
        )
    if normalized_connector_id in {"telegram", "whatsapp", "slack", "discord", "email", "gmail", "imsg", "signal", "mail"}:
        if normalized_action_id.startswith(("send", "post", "reply", "delete", "update", "upload", "create_thread", "add_reaction")):
            return True
    if any(token in normalized_action_id for token in {"charge", "refund", "payment", "payout", "transfer", "invoice"}):
        return True
    if normalized_action_id in {"invoke", "execute", "run"}:
        return True
    return skills_service.tool_action_requires_approval(
        normalized_connector_id,
        normalized_action_id,
        tool_capabilities,
    )


def tool_policy_snapshot(metadata: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    runtime_policy = _runtime_policy_module()
    metadata = metadata if isinstance(metadata, dict) else {}
    policy = runtime_policy._action_policy_from_metadata(metadata)
    runtime_mode = resolve_runtime_policy_mode(metadata)
    return {
        "policy_mode": runtime_mode.get("policy_mode"),
        "blocked_actions": sorted(policy.get("blocked_actions", set())),
        "approval_actions": sorted(policy.get("approval_actions", set())),
        "allow_actions": sorted(policy.get("allow_actions", set())),
        "block_cloud_critical": bool(policy.get("block_cloud_critical", True)),
        "connector_cloud_readonly": bool(policy.get("connector_cloud_readonly", True)),
        "sensitive_tools": runtime_policy.TOOL_POLICY.sensitive_tools(),
        "critical_tools": runtime_policy.TOOL_POLICY.critical_tools(),
    }


def enforce_tool_policy(
    tool_id: str,
    trust_mode: str,
    target: str,
    metadata: Optional[Dict[str, Any]] = None,
) -> str:
    evaluation = evaluate_tool_policy_decision(
        tool_id=tool_id,
        trust_mode=trust_mode,
        target=target,
        metadata=metadata,
    )
    return str(evaluation.get("decision") or "allow")
