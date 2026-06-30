"""Thin fail-closed client for the Rust runtime kernel.

The Python runtime remains the source of orchestration truth for Phase 6A.
This client lets Python services call audited Rust enforcement primitives
without deleting the existing Python implementations before parity is proven.
"""

from __future__ import annotations

import json
import os
import subprocess
import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Mapping, Optional


REPO_ROOT = Path(__file__).resolve().parents[1]
KERNEL_ENV_VAR = "EMPYRALIS_RUNTIME_KERNEL_BIN"
DEFAULT_TIMEOUT_SECONDS = 5
ALLOWED_KERNEL_COMMANDS = {
    "validate-policy",
    "policy-preset",
    "capability-manifest",
    "control-plane-decision",
    "control-plane-service-decision",
    "deployed-agent-decision",
    "deployed-agent-service-decision",
    "deployed-data-decision",
    "deployed-readiness-decision",
    "deployed-virtual-runtime-decision",
    "deployed-virtual-runtime-service-decision",
    "approval-requirement",
    "artifact-policy",
    "authorize-request",
    "authorize-execution",
    "classify-risk",
    "build-sandbox-command",
    "sandbox-execution-decision",
    "execution-plan",
    "execution-runtime-decision",
    "execution-outcome",
    "gateway-action-decision",
    "gateway-frame-decision",
    "gateway-service-decision",
    "gateway-protocol-decision",
    "gateway-state-decision",
    "heartbeat-snapshot",
    "local-worker-decision",
    "machine-lease-decision",
    "outbox-delivery-decision",
    "platform-orchestration-decision",
    "process-lifecycle-decision",
    "queue-transition-decision",
    "safe-mode-decision",
    "scheduler-decision",
    "session-lifecycle-decision",
    "session-scheduler-decision",
    "state-transition-decision",
    "thread-record-decision",
    "virtual-computer-decision",
    "check-path-containment",
    "redact-diagnostics",
    "run-api-decision",
    "run-approval-decision",
    "run-preparation-decision",
    "run-record-decision",
    "run-routing-decision",
    "run-service-decision",
    "run-trigger-decision",
    "run-orchestration-decision",
    "runtime-action-decision",
    "runtime-attachment-decision",
    "runtime-binding-decision",
    "runtime-session-api-decision",
    "runtime-state-store-decision",
    "runtime-health-decision",
    "inspect-secret-reference",
}

ACTIVE_ENFORCEMENT_COMMANDS = {
    "capability-manifest",
    "authorize-request",
    "approval-requirement",
    "artifact-policy",
    "build-sandbox-command",
    "check-path-containment",
    "classify-risk",
    "control-plane-decision",
    "control-plane-service-decision",
    "authorize-execution",
    "deployed-agent-decision",
    "deployed-agent-service-decision",
    "deployed-data-decision",
    "deployed-readiness-decision",
    "deployed-virtual-runtime-decision",
    "deployed-virtual-runtime-service-decision",
    "execution-outcome",
    "execution-plan",
    "execution-runtime-decision",
    "heartbeat-snapshot",
    "gateway-action-decision",
    "gateway-frame-decision",
    "gateway-protocol-decision",
    "gateway-state-decision",
    "gateway-service-decision",
    "inspect-secret-reference",
    "local-worker-decision",
    "machine-lease-decision",
    "outbox-delivery-decision",
    "platform-orchestration-decision",
    "policy-preset",
    "process-lifecycle-decision",
    "queue-transition-decision",
    "redact-diagnostics",
    "run-api-decision",
    "run-approval-decision",
    "run-orchestration-decision",
    "run-preparation-decision",
    "run-record-decision",
    "run-routing-decision",
    "run-service-decision",
    "run-trigger-decision",
    "runtime-attachment-decision",
    "runtime-action-decision",
    "runtime-binding-decision",
    "runtime-health-decision",
    "runtime-session-api-decision",
    "runtime-state-store-decision",
    "sandbox-execution-decision",
    "safe-mode-decision",
    "scheduler-decision",
    "session-lifecycle-decision",
    "session-scheduler-decision",
    "state-transition-decision",
    "thread-record-decision",
    "validate-policy",
    "virtual-computer-decision",
}

KERNEL_COMMAND_WRAPPERS = {
    command: command.replace("-", "_")
    for command in sorted(ALLOWED_KERNEL_COMMANDS)
}

KERNEL_COMMAND_RUST_MODULES = {
    "validate-policy": "policy",
    "policy-preset": "presets",
    "capability-manifest": "capabilities",
    "control-plane-decision": "control_plane",
    "control-plane-service-decision": "control_plane_service",
    "deployed-agent-decision": "deployed_agent",
    "deployed-agent-service-decision": "deployed_agent_service",
    "deployed-data-decision": "deployed_data",
    "deployed-readiness-decision": "deployed_readiness",
    "deployed-virtual-runtime-decision": "deployed_virtual_runtime",
    "deployed-virtual-runtime-service-decision": "deployed_virtual_runtime_service",
    "approval-requirement": "approvals",
    "artifact-policy": "artifacts",
    "authorize-request": "authorization",
    "authorize-execution": "execution_authorization",
    "classify-risk": "risk",
    "build-sandbox-command": "sandbox",
    "sandbox-execution-decision": "sandbox_execution",
    "execution-plan": "execution_plan",
    "execution-runtime-decision": "execution_runtime",
    "execution-outcome": "execution_outcome",
    "gateway-action-decision": "gateway_action",
    "gateway-frame-decision": "gateway_frame",
    "gateway-service-decision": "gateway_service",
    "gateway-protocol-decision": "gateway",
    "gateway-state-decision": "gateway_state",
    "heartbeat-snapshot": "heartbeat",
    "local-worker-decision": "local_worker",
    "machine-lease-decision": "lease",
    "outbox-delivery-decision": "outbox_delivery",
    "platform-orchestration-decision": "platform_orchestration",
    "process-lifecycle-decision": "process",
    "queue-transition-decision": "queue",
    "safe-mode-decision": "safe_mode",
    "scheduler-decision": "scheduler",
    "session-lifecycle-decision": "session",
    "session-scheduler-decision": "session_scheduler",
    "state-transition-decision": "state",
    "thread-record-decision": "thread_record",
    "virtual-computer-decision": "virtual_computer",
    "check-path-containment": "path_guard",
    "redact-diagnostics": "redaction",
    "run-api-decision": "run_api",
    "run-approval-decision": "run_approval",
    "run-preparation-decision": "run_preparation",
    "run-record-decision": "run_record",
    "run-routing-decision": "run_routing",
    "run-service-decision": "run_service",
    "run-trigger-decision": "run_triggers",
    "run-orchestration-decision": "runs",
    "runtime-action-decision": "runtime_action",
    "runtime-attachment-decision": "runtime_attachment",
    "runtime-binding-decision": "runtime_binding",
    "runtime-session-api-decision": "runtime_session_api",
    "runtime-state-store-decision": "runtime_state_store",
    "runtime-health-decision": "runtime_health",
    "inspect-secret-reference": "secrets",
}

DEFAULT_KERNEL_PYTHON_TEST_HINT = "server_modules/tests/test_rust_runtime_kernel_client.py"
PARITY_FIXTURE_VERSION = 1

KERNEL_OWNERSHIP_MANIFEST = {
    command: {
        "owner": "rust",
        "python_role": "adapter" if command in ACTIVE_ENFORCEMENT_COMMANDS else "shadow_or_parity",
        "enforcement_status": "active_enforcement" if command in ACTIVE_ENFORCEMENT_COMMANDS else "shadow_only",
        "wrapper": KERNEL_COMMAND_WRAPPERS[command],
        "rust_module": KERNEL_COMMAND_RUST_MODULES.get(command, "main"),
        "python_test_hint": DEFAULT_KERNEL_PYTHON_TEST_HINT,
        "rust_test_hint": f"empyralis-runtime-kernel/src/{KERNEL_COMMAND_RUST_MODULES.get(command, 'main')}.rs",
        "parity_fixture_family": KERNEL_COMMAND_RUST_MODULES.get(command, "main"),
    }
    for command in sorted(ALLOWED_KERNEL_COMMANDS)
}


@dataclass(frozen=True)
class RustDecision:
    ok: bool
    decision_id: str
    decision: str
    reason: str
    operation: str
    next_action: str
    audit_visibility: str
    approval_required: bool
    cacheable: bool
    payload: Dict[str, Any]


def _decision_id(command: str, payload: Mapping[str, Any]) -> str:
    digest_payload = {
        "command": str(command or "").strip(),
        "operation": str(payload.get("operation") or command or "unknown").strip(),
        "decision": str(payload.get("decision") or "").strip(),
        "reason": str(payload.get("reason") or "").strip(),
        "next_action": str(payload.get("next_action") or "").strip(),
        "approval_required": bool(payload.get("approval_required")),
    }
    digest = hashlib.sha256(
        json.dumps(digest_payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    ).hexdigest()[:16]
    return f"rkd_{digest}"


class RustKernelDecisionError(RuntimeError):
    def __init__(self, decision: Mapping[str, Any], *, command: str = "") -> None:
        self.decision = dict(decision)
        self.command = str(command or self.decision.get("operation") or "").strip()
        self.reason = str(self.decision.get("reason") or "rust_kernel_decision_blocked").strip()
        super().__init__(self.reason)


class RustKernelApprovalRequired(RustKernelDecisionError):
    pass


def runtime_kernel_binary() -> Optional[Path]:
    configured = os.environ.get(KERNEL_ENV_VAR)
    candidates = []
    if configured:
        candidates.append(Path(configured))
    candidates.extend(
        [
            REPO_ROOT
            / "empyralis-runtime-kernel"
            / "target"
            / "release"
            / "empyralis-runtime-kernel",
            REPO_ROOT
            / "empyralis-runtime-kernel"
            / "target"
            / "debug"
            / "empyralis-runtime-kernel",
        ]
    )
    for candidate in candidates:
        if candidate.exists() and os.access(candidate, os.X_OK):
            return candidate
    return None


def runtime_kernel_available() -> bool:
    return runtime_kernel_binary() is not None


def run_runtime_kernel(
    command: str,
    payload: Mapping[str, Any],
    *,
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
) -> Dict[str, Any]:
    if command not in ALLOWED_KERNEL_COMMANDS:
        return _fail_closed("runtime_kernel_command_not_allowed", command=command)

    binary = runtime_kernel_binary()
    if binary is None:
        return _fail_closed("runtime_kernel_unavailable", command=command)

    try:
        completed = subprocess.run(
            [str(binary), command],
            input=json.dumps(payload),
            text=True,
            capture_output=True,
            timeout=timeout_seconds,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return _fail_closed("runtime_kernel_timeout", command=command)
    except OSError:
        return _fail_closed("runtime_kernel_exec_failed", command=command)

    stdout = completed.stdout.strip()
    if not stdout:
        return _fail_closed("runtime_kernel_empty_response", command=command)
    try:
        response = json.loads(stdout)
    except json.JSONDecodeError:
        return _fail_closed("runtime_kernel_invalid_json", command=command)

    if not isinstance(response, dict):
        return _fail_closed("runtime_kernel_invalid_response", command=command)
    if completed.returncode != 0:
        response.setdefault("ok", False)
        response.setdefault("decision", "block")
        response.setdefault("reason", "runtime_kernel_nonzero_exit")
        if response.get("ok") is not False or response.get("decision") != "block":
            return _fail_closed("runtime_kernel_invalid_response", command=command)
        return normalize_kernel_decision(command, response)
    if not isinstance(response.get("ok"), bool) or not isinstance(response.get("decision"), str):
        return _fail_closed("runtime_kernel_invalid_response", command=command)
    return normalize_kernel_decision(command, response)


def normalize_kernel_decision(command: str, response: Mapping[str, Any]) -> Dict[str, Any]:
    payload = dict(response or {})
    decision = str(payload.get("decision") or ("allow" if payload.get("ok") is True else "block")).strip() or "block"
    operation = str(payload.get("operation") or command or "").strip() or "unknown"
    ok = bool(payload.get("ok")) and decision != "block"
    approval_required = bool(payload.get("approval_required")) or decision == "require_approval"
    audit_visibility = str(
        payload.get("audit_visibility")
        or ("security" if decision in {"block", "require_approval"} or approval_required else "standard")
    ).strip() or "standard"
    normalized = {
        **payload,
        "command": str(payload.get("command") or command or operation).strip() or "unknown",
        "ok": ok,
        "decision": decision,
        "reason": str(payload.get("reason") or ("rust_kernel_allowed" if ok else "rust_kernel_blocked")).strip(),
        "operation": operation,
        "next_action": str(payload.get("next_action") or "").strip(),
        "audit_visibility": audit_visibility,
        "approval_required": approval_required,
        "cacheable": bool(payload.get("cacheable")),
    }
    normalized["decision_id"] = str(payload.get("decision_id") or _decision_id(command, normalized)).strip()
    return normalized


def rust_decision(command: str, response: Mapping[str, Any]) -> RustDecision:
    normalized = normalize_kernel_decision(command, response)
    return RustDecision(
        ok=bool(normalized["ok"]),
        decision_id=str(normalized["decision_id"]),
        decision=str(normalized["decision"]),
        reason=str(normalized["reason"]),
        operation=str(normalized["operation"]),
        next_action=str(normalized["next_action"]),
        audit_visibility=str(normalized["audit_visibility"]),
        approval_required=bool(normalized["approval_required"]),
        cacheable=bool(normalized["cacheable"]),
        payload=dict(normalized),
    )


def build_parity_fixture(
    command: str,
    request: Mapping[str, Any],
    rust_response: Mapping[str, Any],
    *,
    legacy_response: Optional[Mapping[str, Any]] = None,
    notes: str = "",
) -> Dict[str, Any]:
    normalized = normalize_kernel_decision(command, rust_response)
    legacy_payload = dict(legacy_response or {}) if isinstance(legacy_response, Mapping) else None
    legacy_decision = None
    if legacy_payload is not None:
        legacy_decision = {
            "decision": str(
                legacy_payload.get("decision")
                or legacy_payload.get("execution_decision")
                or legacy_payload.get("status")
                or ""
            ).strip(),
            "reason": str(legacy_payload.get("reason") or legacy_payload.get("detail") or "").strip(),
            "raw": legacy_payload,
        }
    return {
        "fixture_version": PARITY_FIXTURE_VERSION,
        "command": str(command or "").strip() or "unknown",
        "parity_fixture_family": str(
            KERNEL_OWNERSHIP_MANIFEST.get(command, {}).get("parity_fixture_family") or "unknown"
        ).strip(),
        "request": dict(request or {}),
        "rust_decision": normalized,
        "legacy_decision": legacy_decision,
        "notes": str(notes or "").strip(),
    }


def enforce_kernel_decision(
    command: str,
    response: Mapping[str, Any],
    *,
    allow_approval_required: bool = False,
) -> Dict[str, Any]:
    normalized = normalize_kernel_decision(command, response)
    if normalized.get("decision") == "block" or normalized.get("ok") is False:
        raise RustKernelDecisionError(normalized, command=command)
    if normalized.get("approval_required") is True and not allow_approval_required:
        raise RustKernelApprovalRequired(normalized, command=command)
    return normalized


def run_runtime_kernel_enforced(
    command: str,
    payload: Mapping[str, Any],
    *,
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
    allow_approval_required: bool = False,
) -> Dict[str, Any]:
    return enforce_kernel_decision(
        command,
        run_runtime_kernel(command, payload, timeout_seconds=timeout_seconds),
        allow_approval_required=allow_approval_required,
    )


def validate_policy(policy: Mapping[str, Any], **request: Any) -> Dict[str, Any]:
    payload = {"policy": dict(policy), **request}
    return run_runtime_kernel("validate-policy", payload)


def policy_preset(preset: str) -> Dict[str, Any]:
    return run_runtime_kernel("policy-preset", {"preset": preset})


def capability_manifest(*, include_aliases: bool = True) -> Dict[str, Any]:
    return run_runtime_kernel(
        "capability-manifest",
        {"include_aliases": "true" if include_aliases else "false"},
    )


def control_plane_decision(**request: Any) -> Dict[str, Any]:
    return run_runtime_kernel("control-plane-decision", request)


def control_plane_service_decision(**request: Any) -> Dict[str, Any]:
    return run_runtime_kernel("control-plane-service-decision", request)


def deployed_agent_decision(**request: Any) -> Dict[str, Any]:
    return run_runtime_kernel("deployed-agent-decision", request)


def deployed_agent_service_decision(**request: Any) -> Dict[str, Any]:
    return run_runtime_kernel("deployed-agent-service-decision", request)


def deployed_data_decision(**request: Any) -> Dict[str, Any]:
    return run_runtime_kernel("deployed-data-decision", request)


def deployed_readiness_decision(**request: Any) -> Dict[str, Any]:
    return run_runtime_kernel("deployed-readiness-decision", request)


def deployed_virtual_runtime_decision(**request: Any) -> Dict[str, Any]:
    return run_runtime_kernel("deployed-virtual-runtime-decision", request)


def deployed_virtual_runtime_service_decision(**request: Any) -> Dict[str, Any]:
    return run_runtime_kernel("deployed-virtual-runtime-service-decision", request)


def approval_requirement(
    *,
    capability: str,
    decision: str = "require_approval",
    risk_level: str = "medium",
    action_class: str = "unknown",
    target_summary: Optional[str] = None,
) -> Dict[str, Any]:
    payload = {
        "capability": capability,
        "decision": decision,
        "risk_level": risk_level,
        "action_class": action_class,
    }
    if target_summary is not None:
        payload["target_summary"] = target_summary
    return run_runtime_kernel("approval-requirement", payload)


def artifact_policy(**request: Any) -> Dict[str, Any]:
    return run_runtime_kernel("artifact-policy", request)


def authorize_request(
    policy: Mapping[str, Any],
    *,
    capability: str,
    action_class: str = "unknown",
    payload: Optional[Mapping[str, Any]] = None,
    requested_domain: Optional[str] = None,
    requested_path: Optional[str] = None,
    target_summary: Optional[str] = None,
    safe_mode_enabled: Optional[bool] = None,
    forced_safe_mode: Optional[bool] = None,
    maintenance_mode: Optional[bool] = None,
    kill_switch_enabled: Optional[bool] = None,
    incident_mode: Optional[str] = None,
    health_state: Optional[str] = None,
    computer_profile: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    request_payload: Dict[str, Any] = {
        "policy": dict(policy),
        "capability": capability,
        "action_class": action_class,
        "payload": dict(payload or {}),
    }
    if requested_domain is not None:
        request_payload["requested_domain"] = requested_domain
    if requested_path is not None:
        request_payload["requested_path"] = requested_path
    if target_summary is not None:
        request_payload["target_summary"] = target_summary
    if safe_mode_enabled is not None:
        request_payload["safe_mode_enabled"] = safe_mode_enabled
    if forced_safe_mode is not None:
        request_payload["forced_safe_mode"] = forced_safe_mode
    if maintenance_mode is not None:
        request_payload["maintenance_mode"] = maintenance_mode
    if kill_switch_enabled is not None:
        request_payload["kill_switch_enabled"] = kill_switch_enabled
    if incident_mode is not None:
        request_payload["incident_mode"] = incident_mode
    if health_state is not None:
        request_payload["health_state"] = health_state
    if computer_profile is not None:
        request_payload["computer_profile"] = dict(computer_profile)
    return run_runtime_kernel("authorize-request", request_payload)


def classify_risk(
    policy: Mapping[str, Any],
    *,
    capability: str,
    action_class: str = "unknown",
    payload: Optional[Mapping[str, Any]] = None,
    **request: Any,
) -> Dict[str, Any]:
    request_payload: Dict[str, Any] = {
        "policy": dict(policy),
        "capability": capability,
        "action_class": action_class,
        "payload": dict(payload or {}),
    }
    request_payload.update(request)
    return run_runtime_kernel("classify-risk", request_payload)


def build_sandbox_command(**request: Any) -> Dict[str, Any]:
    return run_runtime_kernel("build-sandbox-command", request)


def sandbox_execution_decision(**request: Any) -> Dict[str, Any]:
    return run_runtime_kernel("sandbox-execution-decision", request)


def execution_plan(**request: Any) -> Dict[str, Any]:
    return run_runtime_kernel("execution-plan", request)


def execution_runtime_decision(**request: Any) -> Dict[str, Any]:
    return run_runtime_kernel("execution-runtime-decision", request)


def execution_outcome(**request: Any) -> Dict[str, Any]:
    return run_runtime_kernel("execution-outcome", request)


def gateway_protocol_decision(**request: Any) -> Dict[str, Any]:
    return run_runtime_kernel("gateway-protocol-decision", request)


def gateway_action_decision(**request: Any) -> Dict[str, Any]:
    return run_runtime_kernel("gateway-action-decision", request)


def gateway_frame_decision(**request: Any) -> Dict[str, Any]:
    return run_runtime_kernel("gateway-frame-decision", request)


def gateway_service_decision(**request: Any) -> Dict[str, Any]:
    return run_runtime_kernel("gateway-service-decision", request)


def gateway_state_decision(**request: Any) -> Dict[str, Any]:
    return run_runtime_kernel("gateway-state-decision", request)


def heartbeat_snapshot(**request: Any) -> Dict[str, Any]:
    return run_runtime_kernel("heartbeat-snapshot", request)


def local_worker_decision(**request: Any) -> Dict[str, Any]:
    return run_runtime_kernel("local-worker-decision", request)


def machine_lease_decision(**request: Any) -> Dict[str, Any]:
    return run_runtime_kernel("machine-lease-decision", request)


def outbox_delivery_decision(**request: Any) -> Dict[str, Any]:
    return run_runtime_kernel("outbox-delivery-decision", request)


def platform_orchestration_decision(**request: Any) -> Dict[str, Any]:
    return run_runtime_kernel("platform-orchestration-decision", request)


def process_lifecycle_decision(**request: Any) -> Dict[str, Any]:
    return run_runtime_kernel("process-lifecycle-decision", request)


def queue_transition_decision(**request: Any) -> Dict[str, Any]:
    return run_runtime_kernel("queue-transition-decision", request)


def state_transition_decision(**request: Any) -> Dict[str, Any]:
    return run_runtime_kernel("state-transition-decision", request)


def thread_record_decision(**request: Any) -> Dict[str, Any]:
    return run_runtime_kernel("thread-record-decision", request)


def virtual_computer_decision(**request: Any) -> Dict[str, Any]:
    return run_runtime_kernel("virtual-computer-decision", request)


def session_lifecycle_decision(**request: Any) -> Dict[str, Any]:
    return run_runtime_kernel("session-lifecycle-decision", request)


def scheduler_decision(**request: Any) -> Dict[str, Any]:
    return run_runtime_kernel("scheduler-decision", request)


def session_scheduler_decision(**request: Any) -> Dict[str, Any]:
    return run_runtime_kernel("session-scheduler-decision", request)


def run_api_decision(**request: Any) -> Dict[str, Any]:
    return run_runtime_kernel("run-api-decision", request)


def run_approval_decision(**request: Any) -> Dict[str, Any]:
    return run_runtime_kernel("run-approval-decision", request)


def run_preparation_decision(**request: Any) -> Dict[str, Any]:
    return run_runtime_kernel("run-preparation-decision", request)


def run_record_decision(**request: Any) -> Dict[str, Any]:
    return run_runtime_kernel("run-record-decision", request)


def run_routing_decision(**request: Any) -> Dict[str, Any]:
    return run_runtime_kernel("run-routing-decision", request)


def run_service_decision(**request: Any) -> Dict[str, Any]:
    return run_runtime_kernel("run-service-decision", request)


def run_trigger_decision(**request: Any) -> Dict[str, Any]:
    return run_runtime_kernel("run-trigger-decision", request)


def run_orchestration_decision(**request: Any) -> Dict[str, Any]:
    return run_runtime_kernel("run-orchestration-decision", request)


def runtime_action_decision(**request: Any) -> Dict[str, Any]:
    return run_runtime_kernel("runtime-action-decision", request)


def runtime_attachment_decision(**request: Any) -> Dict[str, Any]:
    return run_runtime_kernel("runtime-attachment-decision", request)


def runtime_binding_decision(**request: Any) -> Dict[str, Any]:
    return run_runtime_kernel("runtime-binding-decision", request)


def runtime_session_api_decision(**request: Any) -> Dict[str, Any]:
    return run_runtime_kernel("runtime-session-api-decision", request)


def runtime_state_store_decision(**request: Any) -> Dict[str, Any]:
    return run_runtime_kernel("runtime-state-store-decision", request)


def runtime_health_decision(**request: Any) -> Dict[str, Any]:
    return run_runtime_kernel("runtime-health-decision", request)


def authorize_execution(
    policy: Mapping[str, Any],
    *,
    command: str,
    capability: str = "shell_command",
    action_class: str = "execute",
    payload: Optional[Mapping[str, Any]] = None,
    **request: Any,
) -> Dict[str, Any]:
    request_payload: Dict[str, Any] = {
        "policy": dict(policy),
        "command": command,
        "capability": capability,
        "action_class": action_class,
        "payload": dict(payload or {}),
    }
    request_payload.update(request)
    return run_runtime_kernel("authorize-execution", request_payload)


def safe_mode_decision(**request: Any) -> Dict[str, Any]:
    return run_runtime_kernel("safe-mode-decision", request)


def check_path_containment(
    path: str,
    *,
    allowed_roots: list[str],
    blocked_roots: Optional[list[str]] = None,
) -> Dict[str, Any]:
    return run_runtime_kernel(
        "check-path-containment",
        {
            "path": path,
            "allowed_roots": allowed_roots,
            "blocked_roots": blocked_roots or [],
        },
    )


def redact_diagnostics(data: Any) -> Dict[str, Any]:
    return run_runtime_kernel("redact-diagnostics", {"data": data})


def inspect_secret_reference(
    *,
    name: Optional[str] = None,
    value: Optional[str] = None,
    action_class: str = "classify",
) -> Dict[str, Any]:
    payload = {
        "action_class": action_class,
    }
    if name is not None:
        payload["name"] = name
    if value is not None:
        payload["value"] = value
    return run_runtime_kernel("inspect-secret-reference", payload)


def _fail_closed(reason: str, *, command: str = "runtime-kernel") -> Dict[str, Any]:
    return {
        "ok": False,
        "decision": "block",
        "reason": reason,
        "error": reason,
        "operation": str(command or "runtime-kernel").strip() or "runtime-kernel",
        "next_action": "",
        "audit_visibility": "security",
        "approval_required": False,
        "cacheable": False,
    }
