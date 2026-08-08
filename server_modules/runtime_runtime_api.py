from __future__ import annotations

from typing import Any, AsyncIterator, Dict, List, Literal, Optional
import uuid

import httpx
from fastapi import Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sse_starlette.sse import EventSourceResponse
from starlette.concurrency import run_in_threadpool

from server_modules.auth import (
    allowed_workspace_ids,
    enforce_workspace_access,
    grant_workspace_owner_machine_trust,
    has_platform_fleet_operator_access,
    revoke_workspace_owner_machine_trust,
    workspace_machine_enrollment_scope,
    workspace_role,
    workspace_tenant_id,
)
from server_modules.runtime_common import require_api_key
from server_modules import (
    agent_registry_repository,
    demo_workflows,
    hardware_action_broker_service,
    local_queue,
    machine_capability_check,
)
from server_modules import outbox_service
from server_modules import run_state_repository, runs_output, shared, telemetry
from server_modules import billing_service, entitlements_service
from server_modules import security_audit_service
from server_modules import multimodal_provider_service
from server_modules import rust_runtime_kernel_client

SUPPORTED_STT_CONTENT_TYPES = multimodal_provider_service.SUPPORTED_STT_CONTENT_TYPES
TTS_MAX_CHARS = multimodal_provider_service.TTS_MAX_CHARS
TTS_VOICES = multimodal_provider_service.TTS_VOICES

_RUNTIME_SESSION_API_EXPECTED_NEXT_ACTIONS: dict[str, str] = {
    "stt_request": "transcribe_audio",
    "tts_request": "synthesize_tts",
    "machine_control": "apply_machine_control",
    "run_hard_kill": "hard_kill_runtime_run",
    "runtime_register": "register_runtime",
    "runtime_bootstrap": "bootstrap_runtime_companion",
    "self_hosted_command_enqueue": "enqueue_self_hosted_command",
    "self_hosted_command_claim": "claim_self_hosted_command",
    "self_hosted_command_result": "record_self_hosted_command_result",
    "hardware_action_execute": "execute_hardware_action",
    "hardware_action_stop": "stop_hardware_action",
    "runtime_start": "start_runtime_session",
    "runtime_heartbeat": "touch_runtime_session",
    "runtime_stop": "stop_runtime_session",
    "runtime_revoke": "revoke_runtime_session",
    "runtime_recover": "recover_runtime_session",
    "runtime_control_stream": "stream_runtime_control",
    "runtime_task_claim": "claim_runtime_task",
    "runtime_task_heartbeat": "record_runtime_task_heartbeat",
    "runtime_task_control_state": "read_runtime_task_control_state",
    "runtime_task_complete": "complete_runtime_task",
    "runtime_task_pause": "pause_runtime_task",
    "runtime_task_fail": "fail_runtime_task",
}


class RuntimeRegisterPayload(BaseModel):
    runtime_type: str = "local"
    enrollment_token: Optional[str] = None
    display_name: Optional[str] = None
    platform: Optional[str] = None
    policy_mode: str = "local_default"
    capabilities: List[str] = Field(default_factory=list)
    execution_targets: List[str] = Field(default_factory=list)
    instance_id: Optional[str] = None
    capability_digest: Optional[str] = None
    prewarm_state: Optional[str] = None
    warm_pool: Optional[str] = None
    current_run_id: Optional[str] = None
    note: Optional[str] = None
    permission_probe: Dict[str, Dict[str, Any]] = Field(default_factory=dict)
    runtime_role: Optional[str] = None
    install_id: Optional[str] = None
    specialist_key: Optional[str] = None
    summary_channel: Optional[str] = None
    artifact_channel: Optional[str] = None
    local_private_memory_only: Optional[bool] = None
    summary_text: Optional[str] = None
    artifacts: Optional[List[Dict[str, Any]]] = None
    health_state: Optional[str] = None


class RuntimeCompanionBootstrapPayload(RuntimeRegisterPayload):
    runtime_type: str = "local_companion"
    enrollment_token: str = Field(min_length=1)


class SelfHostedNodeEnrollPayload(BaseModel):
    enrollment_token: str = Field(min_length=1)
    public_key: str = Field(min_length=8)
    node_kind: Optional[Literal["mac_mini", "mac", "linux_server", "docker_host"]] = None
    capabilities: List[str] = Field(default_factory=list)
    max_concurrent_sessions: Optional[int] = Field(default=None, ge=1, le=64)
    root_policy: Dict[str, Any] = Field(default_factory=dict)
    display_name: Optional[str] = None


class SelfHostedNodeHeartbeatPayload(BaseModel):
    node_session_token: str = Field(min_length=1)
    status: Optional[str] = None
    note: Optional[str] = None
    capabilities: List[str] = Field(default_factory=list)
    health_state: Optional[str] = None


class SelfHostedNodeCommandEnqueuePayload(BaseModel):
    workspace_id: str = Field(min_length=1)
    runtime_node_id: str = Field(min_length=1)
    agent_id: str = Field(min_length=1)
    command_type: str = Field(default="runtime_action", min_length=1)
    command_payload: Dict[str, Any] = Field(default_factory=dict)
    ttl_seconds: Optional[int] = Field(default=None, ge=30, le=3600)


class SelfHostedNodeCommandClaimPayload(BaseModel):
    node_session_token: str = Field(min_length=1)
    max_commands: int = Field(default=1, ge=1, le=50)
    lease_seconds: int = Field(default=120, ge=15, le=600)


class SelfHostedNodeCommandResultPayload(BaseModel):
    node_session_token: str = Field(min_length=1)
    status: Literal["completed", "failed"]
    result_payload: Dict[str, Any] = Field(default_factory=dict)
    artifacts: List[Dict[str, Any]] = Field(default_factory=list)
    audit_references: List[Dict[str, Any]] = Field(default_factory=list)
    error: Optional[str] = None


class SelfHostedNodeVaultBackupPutPayload(BaseModel):
    node_session_token: str = Field(min_length=1)
    vault_blob_base64: str = Field(min_length=1)
    environment: str = Field(default="dev")
    backup_version: str = Field(default="vault_blob_v1")


class SelfHostedNodeVaultBackupClaimPayload(BaseModel):
    node_session_token: str = Field(min_length=1)


class RuntimeHardwareActionExecutePayload(BaseModel):
    workspace_id: str = Field(min_length=1)
    action_id: str = Field(min_length=1)
    runtime_target: str = "cloud_default"
    capability_id: Optional[str] = None
    arguments: Dict[str, Any] = Field(default_factory=dict)
    gateway_id: Optional[str] = None
    device_id: Optional[str] = None
    node_id: Optional[str] = None
    run_id: Optional[str] = None
    trace_id: Optional[str] = None
    thread_id: Optional[str] = None
    request_id: Optional[str] = None
    session_id: Optional[str] = None
    timeout_seconds: Optional[int] = Field(default=None, ge=1, le=120)
    runtime_access_mode: Optional[str] = None
    execution_mode: Optional[str] = None
    require_approval: Optional[bool] = None
    cost_metadata: Dict[str, Any] = Field(default_factory=dict)


class RuntimeHardwareActionStopPayload(BaseModel):
    workspace_id: str = Field(min_length=1)
    run_id: str = Field(min_length=1)
    runtime_target: str = "user_device_gateway"
    gateway_id: Optional[str] = None
    node_id: Optional[str] = None
    trace_id: Optional[str] = None
    target_request_id: Optional[str] = None
    request_id: Optional[str] = None
    thread_id: Optional[str] = None
    session_id: Optional[str] = None
    reason: Optional[str] = None
    timeout_seconds: Optional[int] = Field(default=None, ge=1, le=120)


class RuntimeHeartbeatPayload(BaseModel):
    session_token: Optional[str] = None
    instance_id: Optional[str] = None
    current_run_id: Optional[str] = None
    note: Optional[str] = None
    permission_probe: Dict[str, Dict[str, Any]] = Field(default_factory=dict)
    runtime_role: Optional[str] = None
    install_id: Optional[str] = None
    specialist_key: Optional[str] = None
    summary_channel: Optional[str] = None
    artifact_channel: Optional[str] = None
    local_private_memory_only: Optional[bool] = None
    summary_text: Optional[str] = None
    artifacts: Optional[List[Dict[str, Any]]] = None
    health_state: Optional[str] = None
    lifecycle_reason: Optional[str] = None


class LocalClusterLifecyclePayload(BaseModel):
    session_token: Optional[str] = None
    instance_id: Optional[str] = None
    current_run_id: Optional[str] = None
    note: Optional[str] = None
    reason: Optional[str] = None
    permission_probe: Dict[str, Dict[str, Any]] = Field(default_factory=dict)
    runtime_role: Optional[str] = None
    install_id: Optional[str] = None
    specialist_key: Optional[str] = None
    summary_channel: Optional[str] = None
    artifact_channel: Optional[str] = None
    local_private_memory_only: Optional[bool] = None
    summary_text: Optional[str] = None
    artifacts: Optional[List[Dict[str, Any]]] = None
    health_state: Optional[str] = None


class MachineEnrollPayload(BaseModel):
    tenant_id: Optional[str] = None
    workspace_id: Optional[str] = None
    machine_id: Optional[str] = None
    runtime_type: str = "local_companion"
    display_name: Optional[str] = None
    platform: Optional[str] = None
    policy_mode: Optional[str] = None
    capabilities: List[str] = Field(default_factory=list)
    execution_targets: List[str] = Field(default_factory=lambda: ["local_companion"])
    note: Optional[str] = None


def _workspace_entitlement_payload(
    cache: Dict[str, Dict[str, Any]],
    workspace_id: str,
) -> Dict[str, Any]:
    token = str(workspace_id or "default").strip() or "default"
    payload = cache.get(token)
    if payload is None:
        payload = entitlements_service.workspace_entitlement_payload_for_workspace_id(workspace_id=token)
        cache[token] = payload
    return payload


def _ensure_advanced_features_access(workspace_id: str) -> None:
    workspace_token = str(workspace_id or "default").strip() or "default"
    payload = entitlements_service.workspace_entitlement_payload_for_workspace_id(
        workspace_id=workspace_token,
    )
    capabilities = payload.get("capabilities") if isinstance(payload.get("capabilities"), dict) else {}
    payload_source = str(payload.get("source") or "").strip().lower()
    if payload_source == "default":
        return
    if not bool(capabilities.get("advanced_features_enabled")):
        if payload_source == "workspace_billing":
            try:
                billing_summary = billing_service.workspace_billing_summary_for_workspace_id(workspace_token)
            except HTTPException:
                billing_summary = {}
            subscription = (
                billing_summary.get("subscription")
                if isinstance(billing_summary, dict) and isinstance(billing_summary.get("subscription"), dict)
                else {}
            )
            billing_metadata = subscription.get("metadata") if isinstance(subscription.get("metadata"), dict) else {}
            if str(billing_metadata.get("source") or "").strip().lower() == "workspace_default":
                return
        raise HTTPException(
            status_code=403,
            detail="Advanced runtime controls are not included in this workspace plan.",
        )


def _ensure_runtime_action_operator(current_user: Dict[str, Any], workspace_id: str) -> None:
    role = workspace_role(current_user, workspace_id)
    if role in {"owner", "admin"} or bool((current_user or {}).get("is_admin")):
        return
    raise HTTPException(status_code=403, detail="Workspace owner or admin role is required.")


def _require_runtime_registration_enrollment_token(payload: RuntimeRegisterPayload) -> str:
    token = str(getattr(payload, "enrollment_token", None) or "").strip()
    if not token:
        raise HTTPException(
            status_code=403,
            detail="Runtime registration requires a machine enrollment token.",
        )
    return token


def _enforce_runtime_session_api_decision(
    *,
    operation: str,
    tenant_id: str,
    workspace_id: str,
    current_user: Optional[Dict[str, Any]] = None,
    action_id: Optional[str] = None,
    agent_id: Optional[str] = None,
    run_id: Optional[str] = None,
    runtime_target: Optional[str] = None,
    runtime_id: Optional[str] = None,
    runtime_type: Optional[str] = None,
    runtime_role: Optional[str] = None,
    node_id: Optional[str] = None,
    instance_id: Optional[str] = None,
    session_token: Optional[str] = None,
    enrollment_token_present: Optional[bool] = None,
    timeout_seconds: Optional[int] = None,
    required_capabilities: Optional[List[str]] = None,
    status: Optional[str] = None,
    current_run_bound: Optional[bool] = None,
    runtime_session_valid: Optional[bool] = None,
    command_type: Optional[str] = None,
    command_payload_present: Optional[bool] = None,
    max_commands: Optional[int] = None,
    lease_seconds: Optional[int] = None,
    artifact_count: Optional[int] = None,
    content_type: Optional[str] = None,
    audio_bytes: Optional[int] = None,
    text_length: Optional[int] = None,
) -> Dict[str, Any]:
    user_payload = current_user or {}
    role = workspace_role(user_payload, workspace_id) if current_user is not None else "runtime"
    operator_approved = role in {"owner", "admin"} or bool(user_payload.get("is_admin"))
    payload = {
        "operation": operation,
        "tenant_id": tenant_id,
        "workspace_id": workspace_id,
        "workspace_access": True,
        "entitlement_ok": True,
        "operator_role": "admin" if bool((current_user or {}).get("is_admin")) else role,
        "operator_approved": operator_approved,
        "action_id": action_id,
        "agent_id": agent_id,
        "run_id": run_id,
        "runtime_target": runtime_target,
        "runtime_id": runtime_id,
        "runtime_type": runtime_type,
        "runtime_role": runtime_role,
        "node_id": node_id,
        "instance_id": instance_id,
        "session_token": session_token,
        "enrollment_token": True if enrollment_token_present is True else None,
        "timeout_seconds": timeout_seconds or 30,
        "capabilities": list(required_capabilities or []),
        "required_capabilities": list(required_capabilities or []),
        "status": status,
        "current_run_bound": current_run_bound if current_run_bound is not None else bool(run_id),
        "runtime_session_valid": bool(session_token) if runtime_session_valid is None else bool(runtime_session_valid),
        "command_type": command_type,
        "command_payload": True if command_payload_present is True else None,
        "max_commands": max_commands,
        "lease_seconds": lease_seconds,
        "artifacts": [{} for _ in range(max(0, int(artifact_count or 0)))],
        "content_type": content_type,
        "audio_bytes": audio_bytes,
        "text_length": text_length,
    }
    try:
        decision = rust_runtime_kernel_client.run_runtime_kernel_enforced(
            "runtime-session-api-decision",
            payload,
        )
        expected_next_action = _RUNTIME_SESSION_API_EXPECTED_NEXT_ACTIONS.get(str(operation or "").strip())
        next_action = str(decision.get("next_action") or "").strip()
        if expected_next_action and next_action != expected_next_action:
            raise HTTPException(
                status_code=403,
                detail=f"unexpected_next_action:{next_action or 'missing'}",
            )
        return decision
    except (
        rust_runtime_kernel_client.RustKernelApprovalRequired,
        rust_runtime_kernel_client.RustKernelDecisionError,
    ) as error:
        raise HTTPException(status_code=403, detail=str(error)) from error


class MachineEnrollmentStatePayload(BaseModel):
    enrollment_token: str = Field(min_length=1)
    state: Literal[
        "awaiting_local_acceptance",
        "installing",
        "starting",
        "registering",
        "healthy",
        "failed",
    ]
    error: Optional[str] = None


class MachineBootstrapCompletePayload(BaseModel):
    enrollment_token: str = Field(min_length=1)


class MachineControlPayload(BaseModel):
    reason: Optional[str] = None


class DesktopSetupPayload(BaseModel):
    workspace_id: Optional[str] = None


class RuntimeTaskClaimRequest(BaseModel):
    runtime_id: Optional[str] = None
    session_token: Optional[str] = None
    instance_id: Optional[str] = None
    execution_target: str = "local"
    required_capabilities: List[str] = Field(default_factory=list)


class RuntimeTaskHeartbeatPayload(BaseModel):
    runtime_id: Optional[str] = None
    session_token: Optional[str] = None
    instance_id: Optional[str] = None
    note: Optional[str] = None
    event: Optional[Dict[str, Any]] = None


class RuntimeTaskControlStatePayload(BaseModel):
    runtime_id: Optional[str] = None
    session_token: Optional[str] = None
    instance_id: Optional[str] = None


class RuntimeTaskCompletePayload(BaseModel):
    runtime_id: Optional[str] = None
    session_token: Optional[str] = None
    instance_id: Optional[str] = None
    result_text: Optional[str] = None
    result_data: Optional[Dict[str, Any]] = None
    usage_masked: Optional[Dict[str, Any]] = None


class RuntimeTaskPausePayload(BaseModel):
    runtime_id: Optional[str] = None
    session_token: Optional[str] = None
    instance_id: Optional[str] = None
    result_text: Optional[str] = None
    result_data: Optional[Dict[str, Any]] = None
    browser_checkpoint: Optional[Dict[str, Any]] = None
    wait_reason: Optional[str] = None


class RuntimeTaskFailPayload(BaseModel):
    runtime_id: Optional[str] = None
    session_token: Optional[str] = None
    instance_id: Optional[str] = None
    error: str


class RuntimeTtsPayload(BaseModel):
    text: str = Field(min_length=1)
    voice: Literal["alloy", "echo", "fable", "onyx", "nova", "shimmer"] = "alloy"
    speed: float = Field(default=1.0, ge=0.25, le=4.0)


def _normalized_openai_api_key() -> str:
    return multimodal_provider_service._normalized_openai_api_key()


async def _transcribe_with_openai(audio_bytes: bytes, content_type: str) -> Dict[str, Any]:
    return await multimodal_provider_service._transcribe_with_openai(audio_bytes, content_type)


def _transcribe_with_google(audio_bytes: bytes, content_type: str) -> Dict[str, Any]:
    return multimodal_provider_service._transcribe_with_google(audio_bytes, content_type)


async def _transcribe_audio_bytes(audio_bytes: bytes, content_type: str) -> Dict[str, Any]:
    return await multimodal_provider_service.transcribe_audio_bytes(audio_bytes, content_type)


def _split_tts_text(text: str, max_chars: int = TTS_MAX_CHARS) -> List[str]:
    return multimodal_provider_service.split_tts_text(text, max_chars=max_chars)


async def _synthesize_tts_chunk(text: str, voice: str, speed: float) -> bytes:
    return await multimodal_provider_service._synthesize_with_openai(text, voice, speed)


async def _synthesize_tts_chunks(text: str, voice: str, speed: float) -> List[bytes]:
    return await multimodal_provider_service.synthesize_speech_chunks(text, voice, speed)


async def _iter_audio_chunks(chunks: List[bytes]) -> AsyncIterator[bytes]:
    for chunk in chunks:
        yield chunk


def _runtime_summary_from_worker_item(item: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "tenant_id": str(item.get("tenant_id") or "default").strip() or "default",
        "workspace_id": str(item.get("workspace_id") or "default").strip() or "default",
        "machine_id": str(item.get("machine_id") or item.get("runtime_id") or item.get("worker_id") or ""),
        "runtime_id": str(item.get("runtime_id") or item.get("worker_id") or ""),
        "runtime_type": str(item.get("runtime_type") or "local"),
        "connection_mode": str(item.get("connection_mode") or "direct_runtime_api").strip().lower() or "direct_runtime_api",
        "runtime_role": str(item.get("runtime_role") or "generic").strip().lower() or "generic",
        "install_id": str(item.get("install_id") or "").strip() or None,
        "specialist_key": str(item.get("specialist_key") or "").strip() or None,
        "display_name": str(item.get("display_name") or item.get("worker_id") or ""),
        "platform": item.get("platform"),
        "policy_mode": str(item.get("policy_mode") or "local_default"),
        "capabilities": list(item.get("capabilities") or []),
        "execution_targets": list(item.get("execution_targets") or []),
        "status": item.get("status"),
        "online": bool(item.get("online")),
        "current_task_id": item.get("current_run_id"),
        "current_lease_holder": item.get("current_lease_holder"),
        "last_seen_at": item.get("last_seen_at"),
        "registered_at": item.get("registered_at"),
        "last_registered_at": item.get("last_registered_at"),
        "session_issued_at": item.get("session_issued_at"),
        "instance_id": item.get("instance_id"),
        "capability_digest": item.get("capability_digest"),
        "trust_state": item.get("trust_state") or "unverified",
        "note": item.get("note"),
        "permission_probe": item.get("permission_probe") if isinstance(item.get("permission_probe"), dict) else {},
        "permission_probe_updated_at": item.get("permission_probe_updated_at"),
        "lifecycle_state": item.get("lifecycle_state") or "registered",
        "lifecycle_state_updated_at": item.get("lifecycle_state_updated_at"),
        "lifecycle_reason": item.get("lifecycle_reason"),
        "started_at": item.get("started_at"),
        "last_started_at": item.get("last_started_at"),
        "stopped_at": item.get("stopped_at"),
        "recovery_requested_at": item.get("recovery_requested_at"),
        "last_recovered_at": item.get("last_recovered_at"),
        "last_recovered_run_ids": list(item.get("last_recovered_run_ids") or []),
        "last_resumed_run_ids": list(item.get("last_resumed_run_ids") or []),
        "health_state": item.get("health_state") or "unknown",
        "health_updated_at": item.get("health_updated_at"),
        "summary_channel": item.get("summary_channel"),
        "artifact_channel": item.get("artifact_channel"),
        "local_private_memory_only": bool(item.get("local_private_memory_only", True)),
        "last_summary": item.get("last_summary"),
        "last_summary_at": item.get("last_summary_at"),
        "last_artifacts": list(item.get("last_artifacts") or []),
        "last_artifact_at": item.get("last_artifact_at"),
        "control_state": item.get("control_state") or "active",
        "control_state_updated_at": item.get("control_state_updated_at"),
        "suspended_at": item.get("suspended_at"),
        "suspended_reason": item.get("suspended_reason"),
        "revoked_at": item.get("revoked_at"),
        "revoked_reason": item.get("revoked_reason"),
        "safe_mode_status": item.get("safe_mode_status") if isinstance(item.get("safe_mode_status"), dict) else {},
        "kill_switch_status": item.get("kill_switch_status") if isinstance(item.get("kill_switch_status"), dict) else {},
        "enrollment_state": item.get("enrollment_state"),
        "enrollment_requested_at": item.get("enrollment_requested_at"),
        "enrollment_updated_at": item.get("enrollment_updated_at"),
        "bootstrap_error": item.get("bootstrap_error"),
        "machine_enrollment_scope": item.get("machine_enrollment_scope") or "workspace",
        "prewarm_state": item.get("prewarm_state"),
        "warm_pool": item.get("warm_pool"),
        "queue_shard": item.get("queue_shard"),
    }


def _local_cluster_worker_payload(
    payload: Optional[LocalClusterLifecyclePayload | RuntimeHeartbeatPayload],
) -> local_queue.LocalWorkerHeartbeatPayload:
    body = payload or LocalClusterLifecyclePayload()
    return local_queue.LocalWorkerHeartbeatPayload(
        current_run_id=body.current_run_id,
        note=body.note,
        permission_probe=dict(body.permission_probe or {}),
        runtime_role=body.runtime_role,
        install_id=body.install_id,
        specialist_key=body.specialist_key,
        summary_channel=body.summary_channel,
        artifact_channel=body.artifact_channel,
        local_private_memory_only=body.local_private_memory_only,
        summary_text=body.summary_text,
        artifacts=body.artifacts,
        health_state=body.health_state,
        lifecycle_reason=getattr(body, "lifecycle_reason", None) or getattr(body, "reason", None),
    )


def _task_summary_from_local_claim(run: Dict[str, Any]) -> Dict[str, Any]:
    context = run.get("context") if isinstance(run.get("context"), dict) else {}
    metadata = context.get("metadata") if isinstance(context.get("metadata"), dict) else {}
    precheck = metadata.get("tool_policy_precheck") if isinstance(metadata.get("tool_policy_precheck"), dict) else {}
    return {
        "task_id": run.get("run_id"),
        "execution_target": "local",
        "status": run.get("status"),
        "lease_seconds": run.get("lease_seconds"),
        "machine_id": run.get("machine_id") or run.get("local_worker_id"),
        "machine_lease_id": run.get("machine_lease_id"),
        "prompt": str(context.get("user_goal") or ""),
        "created_at": run.get("created_at"),
        "required_capabilities": list(precheck.get("capability_ids") or []) if isinstance(precheck, dict) else [],
        "policy_mode": metadata.get("policy_mode"),
        "context": context,
        "metadata": metadata,
        "run": run,
    }


def runtime_status_payload() -> Dict[str, Any]:
    """The GLOBAL, cross-tenant fleet view.

    Internal machinery only.  Never return this straight to an HTTP caller --
    go through `scoped_runtime_status_payload`, which is the tenant boundary.
    """
    local_queue.recover_orphaned_local_runs_on_startup()
    payload = local_queue.handle_get_local_workers_status()
    items = payload.get("items") if isinstance(payload.get("items"), list) else []
    return {
        "scope": "local_companion_bridge",
        "summary": payload.get("summary") if isinstance(payload.get("summary"), dict) else {},
        "capability_queue": payload.get("capability_queue") if isinstance(payload.get("capability_queue"), dict) else {},
        "outbox": outbox_service.get_outbox_delivery_status(),
        "items": [_runtime_summary_from_worker_item(item) for item in items if isinstance(item, dict)],
    }


def _worker_item_scope_tokens(item: Dict[str, Any]) -> tuple[str, str]:
    tenant_token = str(item.get("tenant_id") or "default").strip() or "default"
    workspace_token = str(item.get("workspace_id") or "default").strip() or "default"
    return tenant_token, workspace_token


def _worker_item_identity_tokens(item: Dict[str, Any]) -> set[str]:
    return {
        str(item.get(field) or "").strip()
        for field in ("worker_id", "runtime_id", "machine_id")
        if str(item.get(field) or "").strip()
    }


def scoped_runtime_status_payload(current_user: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """`runtime_status_payload` narrowed to what this caller may actually see.

    `require_api_key` proves only that somebody is logged in, so the fleet view
    behind it used to hand every tenant's machine ids, hostnames, workspace ids
    and current task ids to any authenticated user.  Global visibility now
    needs `has_platform_fleet_operator_access`; everyone else sees only
    machines in workspaces they belong to, with the summary and the capability
    queue re-derived from that narrowed set.
    """
    payload = runtime_status_payload()
    if has_platform_fleet_operator_access(current_user):
        payload["view"] = "global_operator"
        return payload

    # `allowed_workspace_ids` returns None to mean "unrestricted". Operators are
    # already handled above, so a None here is an unexpected grant shape and
    # must fail CLOSED rather than fall through to the whole fleet.
    allowed = allowed_workspace_ids(current_user)
    allowed_workspaces = set(allowed) if allowed is not None else set()
    allowed_tenants: set[str] = set()
    for workspace_id in allowed_workspaces:
        try:
            allowed_tenants.add(workspace_tenant_id(current_user, workspace_id))
        except HTTPException:
            # Orphaned membership -- drop it rather than widening the view.
            continue

    items = payload.get("items") if isinstance(payload.get("items"), list) else []
    scoped_items: List[Dict[str, Any]] = []
    visible_identities: set[str] = set()
    for item in items:
        if not isinstance(item, dict):
            continue
        tenant_token, workspace_token = _worker_item_scope_tokens(item)
        if workspace_token not in allowed_workspaces:
            continue
        if allowed_tenants and tenant_token not in allowed_tenants:
            continue
        scoped_items.append(item)
        visible_identities |= _worker_item_identity_tokens(item)

    summary = payload.get("summary") if isinstance(payload.get("summary"), dict) else {}
    capability_queue = payload.get("capability_queue") if isinstance(payload.get("capability_queue"), dict) else {}
    scoped_capability_queue: Dict[str, Any] = {}
    for capability, holders in capability_queue.items():
        if not isinstance(holders, list):
            continue
        visible = [holder for holder in holders if str(holder or "").strip() in visible_identities]
        if visible:
            scoped_capability_queue[str(capability)] = visible

    payload["view"] = "workspace_scoped"
    payload["items"] = scoped_items
    payload["capability_queue"] = scoped_capability_queue
    payload["summary"] = local_queue.summarize_worker_items(
        scoped_items,
        pending_runs=int(summary.get("pending_runs") or 0),
        claimed_runs=int(summary.get("claimed_runs") or 0),
    )
    return payload


def _run_snapshot_workspace_id(item: Dict[str, Any]) -> str:
    """Best-effort workspace of a run snapshot, across its three shapes.

    Live rows carry `workspace_id` at the top level, archive payloads and
    in-memory history items sometimes carry it only under `context`.
    """
    direct = str(item.get("workspace_id") or "").strip()
    if direct:
        return direct
    context = item.get("context") if isinstance(item.get("context"), dict) else {}
    return str(context.get("workspace_id") or "").strip()


def _recent_failed_run_snapshots(
    limit: int = 100,
    *,
    workspace_ids: Optional[set[str]] = None,
) -> List[Dict[str, Any]]:
    """`workspace_ids=None` means every workspace; a set means only those.

    An EMPTY set means "this caller may see nothing" and returns nothing --
    it is never widened back to the whole fleet.
    """
    snapshots: List[Dict[str, Any]] = []
    seen: set[str] = set()

    def _in_scope(item: Dict[str, Any]) -> bool:
        if workspace_ids is None:
            return True
        return _run_snapshot_workspace_id(item) in workspace_ids

    if workspace_ids is not None and not workspace_ids:
        return snapshots
    try:
        live_failed = run_state_repository.sync_list_live_runs_by_state(
            ["failed", "timeout"],
            workspace_ids=None if workspace_ids is None else sorted(workspace_ids),
        )
    except Exception:
        live_failed = []
    for item in live_failed:
        if not isinstance(item, dict) or not _in_scope(item):
            continue
        run_id = str(item.get("run_id") or "").strip()
        if not run_id or run_id in seen:
            continue
        try:
            snapshots.append(runs_output._serialize_run_snapshot(run_id, item))
            seen.add(run_id)
        except Exception:
            continue
        if len(snapshots) >= limit:
            return snapshots
    try:
        archived = run_state_repository.sync_list_run_archive(
            limit=limit,
            workspace_ids=None if workspace_ids is None else sorted(workspace_ids),
        )
    except Exception:
        archived = []
    for item in archived:
        if not isinstance(item, dict) or not _in_scope(item):
            continue
        run_id = str(item.get("run_id") or "").strip()
        if not run_id or run_id in seen:
            continue
        snapshots.append(dict(item))
        seen.add(run_id)
        if len(snapshots) >= limit:
            return snapshots
    with shared.RUN_HISTORY_LOCK:
        history_items = list(shared.RUN_HISTORY)
    for item in history_items:
        if not isinstance(item, dict) or not _in_scope(item):
            continue
        run_id = str(item.get("run_id") or "").strip()
        if not run_id or run_id in seen:
            continue
        snapshots.append(dict(item))
        seen.add(run_id)
        if len(snapshots) >= limit:
            break
    return snapshots


def runtime_reliability_payload() -> Dict[str, Any]:
    """The GLOBAL reliability snapshot -- internal callers only.

    HTTP callers must go through `scoped_runtime_reliability_payload`.
    """
    payload = telemetry.get_reliability_snapshot(failed_run_snapshots=_recent_failed_run_snapshots())
    payload["outbox"] = outbox_service.get_outbox_delivery_status()
    return payload


def _caller_workspace_scope(current_user: Optional[Dict[str, Any]]) -> set[str]:
    """The workspaces this non-operator caller may see. Fails closed."""
    allowed = allowed_workspace_ids(current_user)
    return set(allowed) if allowed is not None else set()


def scoped_runtime_reliability_payload(current_user: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """`runtime_reliability_payload` narrowed to the caller's own workspaces.

    The failed-run scan behind this route reads `live_runs` and `run_archive`
    with no workspace predicate, so `incomplete_run_ids` used to hand out other
    tenants' run ids to any authenticated user.
    """
    if has_platform_fleet_operator_access(current_user):
        payload = runtime_reliability_payload()
        payload["view"] = "global_operator"
        return payload
    payload = telemetry.get_reliability_snapshot(
        failed_run_snapshots=_recent_failed_run_snapshots(
            workspace_ids=_caller_workspace_scope(current_user),
        )
    )
    payload["outbox"] = outbox_service.get_outbox_delivery_status()
    payload["view"] = "workspace_scoped"
    return payload


def _legacy_local_workers_status_from(payload: Dict[str, Any]) -> Dict[str, Any]:
    items = payload.get("items") if isinstance(payload.get("items"), list) else []
    summary = payload.get("summary") if isinstance(payload.get("summary"), dict) else {}
    return {
        "enabled": True,
        "scope": payload.get("scope"),
        "summary": summary,
        "items": items,
        "known": int(summary.get("known") or len(items)),
        "online": int(summary.get("online") or 0),
        "idle": int(summary.get("idle") or 0),
        "busy": int(summary.get("busy") or 0),
        "offline": int(summary.get("offline") or 0),
        "online_workers": int(summary.get("online") or 0),
    }


def legacy_local_workers_status_payload() -> Dict[str, Any]:
    """GLOBAL. Internal callers only -- see `scoped_legacy_local_workers_status_payload`."""
    return _legacy_local_workers_status_from(runtime_status_payload())


def scoped_legacy_local_workers_status_payload(current_user: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    return _legacy_local_workers_status_from(scoped_runtime_status_payload(current_user))


def register_runtime_routes(app) -> None:
    @app.post("/stt", dependencies=[Depends(require_api_key)])
    async def transcribe_audio(request: Request):
        raw_content_type = str(request.headers.get("content-type") or "").split(";", 1)[0].strip().lower()
        if raw_content_type not in SUPPORTED_STT_CONTENT_TYPES:
            raise HTTPException(status_code=415, detail="Unsupported audio format.")
        audio_bytes = await request.body()
        if not audio_bytes:
            raise HTTPException(status_code=400, detail="Audio payload is required.")
        _enforce_runtime_session_api_decision(
            operation="stt_request",
            tenant_id="default",
            workspace_id="default",
            content_type=raw_content_type,
            audio_bytes=len(audio_bytes),
        )
        try:
            result = await _transcribe_audio_bytes(audio_bytes, raw_content_type)
        except httpx.HTTPStatusError as exc:
            detail = exc.response.text.strip() if exc.response is not None else "Speech transcription failed."
            raise HTTPException(status_code=502, detail=detail or "Speech transcription failed.") from exc
        except RuntimeError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        return {
            "transcript": str(result.get("transcript") or "").strip(),
            "confidence": float(result.get("confidence") or 0.0),
        }

    @app.post("/tts", dependencies=[Depends(require_api_key)])
    async def synthesize_speech(payload: RuntimeTtsPayload):
        if payload.voice not in TTS_VOICES:
            raise HTTPException(status_code=400, detail="Unsupported voice.")
        _enforce_runtime_session_api_decision(
            operation="tts_request",
            tenant_id="default",
            workspace_id="default",
            text_length=len(payload.text or ""),
        )
        try:
            chunks = await _synthesize_tts_chunks(payload.text, payload.voice, payload.speed)
        except httpx.HTTPStatusError as exc:
            detail = exc.response.text.strip() if exc.response is not None else "Text-to-speech synthesis failed."
            raise HTTPException(status_code=502, detail=detail or "Text-to-speech synthesis failed.") from exc
        except RuntimeError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        return StreamingResponse(_iter_audio_chunks(chunks), media_type="audio/mpeg")

    @app.get("/runtime/runtimes/status", dependencies=[Depends(require_api_key)])
    async def get_runtime_status(current_user=Depends(require_api_key)):
        return scoped_runtime_status_payload(current_user)

    @app.get("/runtime/runtimes/reliability", dependencies=[Depends(require_api_key)])
    async def get_runtime_reliability(current_user=Depends(require_api_key)):
        return scoped_runtime_reliability_payload(current_user)

    @app.get("/desktop/setup/status", dependencies=[Depends(require_api_key)])
    async def get_desktop_setup_status(
        workspace_id: Optional[str] = None,
        current_user=Depends(require_api_key),
    ):
        requested_workspace_id = enforce_workspace_access(
            current_user,
            workspace_id or "default",
            minimum_role="viewer",
        )
        return machine_capability_check.desktop_setup_status(workspace_id=requested_workspace_id)

    @app.post("/desktop/setup/complete", dependencies=[Depends(require_api_key)])
    async def complete_desktop_setup(
        payload: Optional[DesktopSetupPayload] = None,
        current_user=Depends(require_api_key),
    ):
        body = payload or DesktopSetupPayload()
        requested_workspace_id = enforce_workspace_access(
            current_user,
            body.workspace_id or "default",
            minimum_role="member",
        )
        status_payload = machine_capability_check.desktop_setup_status(workspace_id=requested_workspace_id)
        if not bool(status_payload.get("can_continue")):
            raise HTTPException(status_code=409, detail="Desktop setup cannot be completed until all required checks pass.")
        record = machine_capability_check.mark_desktop_setup_completed(
            metadata={
                "workspace_id": requested_workspace_id,
                "actor_type": str((current_user or {}).get("auth_type") or "").strip() or "api_key",
                "user_id": str((current_user or {}).get("user_id") or "").strip() or None,
            }
        )
        return {
            "ok": True,
            "workspace_id": requested_workspace_id,
            "desktop_setup_completed": True,
            "updated_at": record.get("updated_at"),
        }

    @app.post("/desktop/demo/screenshot-description", dependencies=[Depends(require_api_key)])
    async def start_desktop_demo(
        payload: Optional[DesktopSetupPayload] = None,
        current_user=Depends(require_api_key),
    ):
        body = payload or DesktopSetupPayload()
        requested_workspace_id = enforce_workspace_access(
            current_user,
            body.workspace_id or "default",
            minimum_role="member",
        )
        return await demo_workflows.start_first_run_demo(
            workspace_id=requested_workspace_id,
            current_user=current_user,
        )

    @app.get("/desktop/demo/{run_id}", dependencies=[Depends(require_api_key)])
    async def get_desktop_demo_status(
        run_id: str,
        workspace_id: Optional[str] = None,
        current_user=Depends(require_api_key),
    ):
        requested_workspace_id = enforce_workspace_access(
            current_user,
            workspace_id or "default",
            minimum_role="viewer",
        )
        payload = demo_workflows.first_run_demo_status(run_id)
        if str(payload.get("workspace_id") or "default").strip() != requested_workspace_id:
            raise HTTPException(status_code=404, detail="Demo run not found in this workspace.")
        return payload

    @app.get("/machines", dependencies=[Depends(require_api_key)])
    async def get_machines(
        tenant_id: Optional[str] = None,
        workspace_id: Optional[str] = None,
        current_user=Depends(require_api_key),
    ):
        requested_workspace_id = (
            enforce_workspace_access(current_user, workspace_id, tenant_id=tenant_id, minimum_role="viewer")
            if workspace_id
            else None
        )
        entitlement_cache: Dict[str, Dict[str, Any]] = {}
        if requested_workspace_id:
            _ensure_advanced_features_access(requested_workspace_id)
        payload = runtime_status_payload()
        items = payload.get("items") if isinstance(payload.get("items"), list) else []
        allowed = allowed_workspace_ids(current_user)
        filtered = []
        for item in items:
            if not isinstance(item, dict):
                continue
            item_tenant_id = str(item.get("tenant_id") or "default").strip() or "default"
            item_workspace_id = str(item.get("workspace_id") or "default").strip() or "default"
            if tenant_id and item_tenant_id != str(tenant_id).strip():
                continue
            if requested_workspace_id and item_workspace_id != requested_workspace_id:
                continue
            if allowed is not None and item_workspace_id not in allowed:
                continue
            item_entitlements = _workspace_entitlement_payload(entitlement_cache, item_workspace_id)
            item_capabilities = item_entitlements.get("capabilities") if isinstance(item_entitlements.get("capabilities"), dict) else {}
            if not bool(item_capabilities.get("advanced_features_enabled")):
                continue
            filtered.append(item)
        payload["items"] = filtered
        payload["summary"] = {
            **(payload.get("summary") if isinstance(payload.get("summary"), dict) else {}),
            "known": len(filtered),
            "online": sum(1 for item in filtered if bool(item.get("online"))),
            "busy": sum(1 for item in filtered if bool(item.get("current_run_id"))),
            "idle": sum(1 for item in filtered if bool(item.get("online")) and not bool(item.get("current_run_id"))),
            "offline": sum(1 for item in filtered if not bool(item.get("online"))),
            "suspended": sum(1 for item in filtered if str(item.get("control_state") or "").strip().lower() == "suspended"),
            "revoked": sum(1 for item in filtered if str(item.get("control_state") or "").strip().lower() == "revoked"),
        }
        return payload

    @app.post("/machines/enroll", dependencies=[Depends(require_api_key)])
    async def enroll_machine(
        payload: Optional[MachineEnrollPayload] = None,
        current_user=Depends(require_api_key),
    ):
        body = payload or MachineEnrollPayload()
        workspace_id = enforce_workspace_access(
            current_user,
            body.workspace_id,
            tenant_id=body.tenant_id,
            minimum_role="member",
            capability_id="machines.manage",
        )
        _ensure_advanced_features_access(workspace_id)
        tenant_id = workspace_tenant_id(current_user, workspace_id)
        enrollment_scope = workspace_machine_enrollment_scope(current_user, workspace_id)
        if enrollment_scope == "tenant" and workspace_role(current_user, workspace_id) != "owner":
            raise HTTPException(status_code=403, detail="Owner role required for tenant-scoped machine enrollment.")
        if enrollment_scope == "global" and not bool((current_user or {}).get("is_admin")):
            raise HTTPException(status_code=403, detail="Admin role required for global machine enrollment.")
        result = local_queue.handle_enroll_local_runtime(
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            machine_id=body.machine_id,
            runtime_type=body.runtime_type,
            display_name=body.display_name,
            platform=body.platform,
            policy_mode=body.policy_mode,
            capabilities=body.capabilities,
            execution_targets=body.execution_targets,
            note=body.note,
            machine_enrollment_scope=enrollment_scope,
        )
        if workspace_role(current_user, workspace_id) == "owner":
            grant_workspace_owner_machine_trust(workspace_id, str(result.get("machine_id") or "").strip())
        security_audit_service.emit_security_audit_event(
            action="runtime_target.machine_enrolled",
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            current_user=current_user,
            machine_id=str(result.get("machine_id") or body.machine_id or "").strip() or None,
            trace_id=str(result.get("machine_id") or "").strip(),
            idempotency_key=f"runtime_target.machine_enrolled:{workspace_id}:{str(result.get('machine_id') or body.machine_id or '').strip()}",
            metadata={
                "runtime_type": body.runtime_type,
                "display_name": body.display_name,
                "machine_enrollment_scope": enrollment_scope,
            },
        )
        return result

    @app.post("/machines/enrollment-intents", dependencies=[Depends(require_api_key)])
    async def create_machine_enrollment_intent(
        payload: Optional[MachineEnrollPayload] = None,
        current_user=Depends(require_api_key),
    ):
        body = payload or MachineEnrollPayload()
        workspace_id = enforce_workspace_access(
            current_user,
            body.workspace_id,
            tenant_id=body.tenant_id,
            minimum_role="member",
            capability_id="machines.manage",
        )
        _ensure_advanced_features_access(workspace_id)
        tenant_id = workspace_tenant_id(current_user, workspace_id)
        enrollment_scope = workspace_machine_enrollment_scope(current_user, workspace_id)
        if enrollment_scope == "tenant" and workspace_role(current_user, workspace_id) != "owner":
            raise HTTPException(status_code=403, detail="Owner role required for tenant-scoped machine enrollment.")
        if enrollment_scope == "global" and not bool((current_user or {}).get("is_admin")):
            raise HTTPException(status_code=403, detail="Admin role required for global machine enrollment.")
        result = local_queue.create_machine_enrollment_intent(
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            machine_id=body.machine_id,
            runtime_type=body.runtime_type,
            display_name=body.display_name,
            platform=body.platform,
            policy_mode=body.policy_mode,
            capabilities=body.capabilities,
            execution_targets=body.execution_targets,
            note=body.note,
            machine_enrollment_scope=enrollment_scope,
        )
        if workspace_role(current_user, workspace_id) == "owner":
            grant_workspace_owner_machine_trust(workspace_id, str(result.get("machine_id") or "").strip())
        security_audit_service.emit_security_audit_event(
            action="runtime_target.machine_enrollment_intent_created",
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            current_user=current_user,
            machine_id=str(result.get("machine_id") or body.machine_id or "").strip() or None,
            trace_id=str(result.get("machine_id") or "").strip(),
            idempotency_key=f"runtime_target.machine_enrollment_intent_created:{workspace_id}:{str(result.get('machine_id') or body.machine_id or '').strip()}",
            metadata={
                "runtime_type": body.runtime_type,
                "display_name": body.display_name,
                "machine_enrollment_scope": enrollment_scope,
            },
        )
        return result

    @app.post("/machines/{machine_id}/enrollment-state", dependencies=[Depends(require_api_key)])
    async def update_machine_enrollment_state(machine_id: str, payload: MachineEnrollmentStatePayload):
        return local_queue.update_machine_enrollment_state(
            machine_id,
            enrollment_token=payload.enrollment_token,
            state=payload.state,
            error=payload.error,
        )

    @app.post("/machines/{machine_id}/bootstrap-complete", dependencies=[Depends(require_api_key)])
    async def complete_machine_bootstrap(machine_id: str, payload: MachineBootstrapCompletePayload):
        return local_queue.complete_machine_bootstrap(
            machine_id,
            enrollment_token=payload.enrollment_token,
        )

    @app.delete("/machines/{machine_id}", dependencies=[Depends(require_api_key)])
    async def delete_machine(machine_id: str, current_user=Depends(require_api_key)):
        status_payload = local_queue.handle_get_local_workers_status()
        items = status_payload.get("items") if isinstance(status_payload.get("items"), list) else []
        machine = next(
            (
                item
                for item in items
                if isinstance(item, dict)
                and str(item.get("machine_id") or item.get("runtime_id") or "").strip() == str(machine_id or "").strip()
            ),
            None,
        )
        workspace_id = enforce_workspace_access(
            current_user,
            (machine or {}).get("workspace_id") or "default",
            tenant_id=(machine or {}).get("tenant_id"),
            minimum_role="member",
            capability_id="machines.manage",
        )
        _ensure_advanced_features_access(workspace_id)
        tenant_id = str((machine or {}).get("tenant_id") or "default").strip() or "default"
        _enforce_runtime_session_api_decision(
            operation="machine_control",
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            current_user=current_user,
            runtime_id=str(machine_id or "").strip(),
        )
        result = local_queue.handle_delete_local_runtime(machine_id)
        revoke_workspace_owner_machine_trust(workspace_id, str(machine_id or "").strip())
        security_audit_service.emit_security_audit_event(
            action="runtime_target.machine_deleted",
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            current_user=current_user,
            machine_id=str(machine_id or "").strip() or None,
            trace_id=str(machine_id or "").strip(),
            idempotency_key=f"runtime_target.machine_deleted:{workspace_id}:{str(machine_id or '').strip()}",
        )
        return result

    @app.post("/machines/{machine_id}/suspend", dependencies=[Depends(require_api_key)])
    async def suspend_machine(
        machine_id: str,
        payload: Optional[MachineControlPayload] = None,
        current_user=Depends(require_api_key),
    ):
        status_payload = local_queue.handle_get_local_workers_status()
        items = status_payload.get("items") if isinstance(status_payload.get("items"), list) else []
        machine = next(
            (
                item
                for item in items
                if isinstance(item, dict)
                and str(item.get("machine_id") or item.get("runtime_id") or "").strip() == str(machine_id or "").strip()
            ),
            None,
        )
        workspace_id = enforce_workspace_access(
            current_user,
            (machine or {}).get("workspace_id") or "default",
            tenant_id=(machine or {}).get("tenant_id"),
            minimum_role="member",
            capability_id="machines.manage",
        )
        _ensure_advanced_features_access(workspace_id)
        body = payload or MachineControlPayload()
        tenant_id = str((machine or {}).get("tenant_id") or "default").strip() or "default"
        _enforce_runtime_session_api_decision(
            operation="machine_control",
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            current_user=current_user,
            runtime_id=str(machine_id or "").strip(),
        )
        result = local_queue.handle_set_local_runtime_control(machine_id, action="suspend", reason=body.reason)
        security_audit_service.emit_security_audit_event(
            action="runtime_target.machine_suspended",
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            current_user=current_user,
            machine_id=str(machine_id or "").strip() or None,
            trace_id=str(machine_id or "").strip(),
            idempotency_key=f"runtime_target.machine_suspended:{workspace_id}:{str(machine_id or '').strip()}:{str(body.reason or '').strip()}",
            metadata={"reason": str(body.reason or "").strip() or None},
        )
        return result

    @app.post("/machines/{machine_id}/resume", dependencies=[Depends(require_api_key)])
    async def resume_machine(
        machine_id: str,
        payload: Optional[MachineControlPayload] = None,
        current_user=Depends(require_api_key),
    ):
        status_payload = local_queue.handle_get_local_workers_status()
        items = status_payload.get("items") if isinstance(status_payload.get("items"), list) else []
        machine = next(
            (
                item
                for item in items
                if isinstance(item, dict)
                and str(item.get("machine_id") or item.get("runtime_id") or "").strip() == str(machine_id or "").strip()
            ),
            None,
        )
        workspace_id = enforce_workspace_access(
            current_user,
            (machine or {}).get("workspace_id") or "default",
            tenant_id=(machine or {}).get("tenant_id"),
            minimum_role="member",
            capability_id="machines.manage",
        )
        _ensure_advanced_features_access(workspace_id)
        body = payload or MachineControlPayload()
        tenant_id = str((machine or {}).get("tenant_id") or "default").strip() or "default"
        _enforce_runtime_session_api_decision(
            operation="machine_control",
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            current_user=current_user,
            runtime_id=str(machine_id or "").strip(),
        )
        result = local_queue.handle_set_local_runtime_control(machine_id, action="resume", reason=body.reason)
        security_audit_service.emit_security_audit_event(
            action="runtime_target.machine_resumed",
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            current_user=current_user,
            machine_id=str(machine_id or "").strip() or None,
            trace_id=str(machine_id or "").strip(),
            idempotency_key=f"runtime_target.machine_resumed:{workspace_id}:{str(machine_id or '').strip()}:{str(body.reason or '').strip()}",
            metadata={"reason": str(body.reason or "").strip() or None},
        )
        return result

    @app.post("/machines/{machine_id}/hard-kill", dependencies=[Depends(require_api_key)])
    async def hard_kill_machine(
        machine_id: str,
        payload: Optional[MachineControlPayload] = None,
        current_user=Depends(require_api_key),
    ):
        status_payload = local_queue.handle_get_local_workers_status()
        items = status_payload.get("items") if isinstance(status_payload.get("items"), list) else []
        machine = next(
            (
                item
                for item in items
                if isinstance(item, dict)
                and str(item.get("machine_id") or item.get("runtime_id") or "").strip() == str(machine_id or "").strip()
            ),
            None,
        )
        workspace_id = enforce_workspace_access(
            current_user,
            (machine or {}).get("workspace_id") or "default",
            tenant_id=(machine or {}).get("tenant_id"),
            minimum_role="member",
            capability_id="machines.manage",
        )
        _ensure_advanced_features_access(workspace_id)
        body = payload or MachineControlPayload()
        requested_by = str((current_user or {}).get("user_id") or (current_user or {}).get("auth_type") or "operator").strip() or "operator"
        tenant_id = str((machine or {}).get("tenant_id") or "default").strip() or "default"
        _enforce_runtime_session_api_decision(
            operation="machine_control",
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            current_user=current_user,
            runtime_id=str(machine_id or "").strip(),
        )
        result = local_queue.handle_request_local_runtime_hard_kill(
            machine_id,
            reason=body.reason,
            requested_by=requested_by,
        )
        security_audit_service.emit_security_audit_event(
            action="runtime_target.machine_hard_killed",
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            current_user=current_user,
            machine_id=str(machine_id or "").strip() or None,
            trace_id=str(machine_id or "").strip(),
            idempotency_key=f"runtime_target.machine_hard_killed:{workspace_id}:{str(machine_id or '').strip()}:{requested_by}",
            metadata={"reason": str(body.reason or "").strip() or None},
        )
        return result

    @app.post("/runs/{run_id}/hard-kill", dependencies=[Depends(require_api_key)])
    async def hard_kill_run(
        run_id: uuid.UUID,
        payload: Optional[MachineControlPayload] = None,
        current_user=Depends(require_api_key),
    ):
        local_queue._init()
        run = local_queue._server.runs.get(str(run_id))
        if not isinstance(run, dict):
            raise HTTPException(status_code=404, detail="Run ID not found")
        context = run.get("context") if isinstance(run.get("context"), dict) else {}
        metadata = context.get("metadata") if isinstance(context.get("metadata"), dict) else {}
        workspace_id = enforce_workspace_access(
            current_user,
            context.get("workspace_id") or metadata.get("workspace_id") or "default",
            tenant_id=context.get("tenant_id") or metadata.get("tenant_id"),
            minimum_role="member",
        )
        body = payload or MachineControlPayload()
        requested_by = str((current_user or {}).get("user_id") or (current_user or {}).get("auth_type") or "operator").strip() or "operator"
        tenant_id = str(context.get("tenant_id") or metadata.get("tenant_id") or "default").strip() or "default"
        _enforce_runtime_session_api_decision(
            operation="run_hard_kill",
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            current_user=current_user,
            run_id=str(run_id),
            runtime_id=str(run.get("machine_id") or run.get("local_worker_id") or "").strip() or None,
        )
        result = local_queue.handle_request_local_run_hard_kill(
            str(run_id),
            reason=body.reason,
            requested_by=requested_by,
        )
        result["workspace_id"] = workspace_id
        security_audit_service.emit_security_audit_event(
            action="runtime_target.run_hard_killed",
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            current_user=current_user,
            run_id=str(run_id),
            machine_id=str(run.get("machine_id") or run.get("local_worker_id") or "").strip() or None,
            trace_id=str(run_id),
            idempotency_key=f"runtime_target.run_hard_killed:{workspace_id}:{str(run_id)}:{requested_by}",
            metadata={"reason": str(body.reason or "").strip() or None},
        )
        return result

    @app.get("/local/workers/status", dependencies=[Depends(require_api_key)])
    async def get_legacy_local_workers_status(current_user=Depends(require_api_key)):
        return await run_in_threadpool(scoped_legacy_local_workers_status_payload, current_user)

    @app.post("/runtime/runtimes/{runtime_id}/register", dependencies=[Depends(require_api_key)])
    async def register_runtime(runtime_id: str, payload: Optional[RuntimeRegisterPayload] = None):
        runtime_token = str(runtime_id or "").strip()
        if not runtime_token:
            raise HTTPException(status_code=400, detail="runtime_id is required.")
        body = payload or RuntimeRegisterPayload()
        enrollment_token = _require_runtime_registration_enrollment_token(body)
        _enforce_runtime_session_api_decision(
            operation="runtime_register",
            tenant_id="default",
            workspace_id="default",
            runtime_id=runtime_token,
            runtime_type=body.runtime_type,
            runtime_role=body.runtime_role,
            instance_id=body.instance_id,
            enrollment_token_present=True,
            required_capabilities=list(body.capabilities or []),
        )

        def _register_runtime() -> Dict[str, Any]:
            return local_queue.handle_bootstrap_enrolled_local_companion_runtime(
                runtime_token,
                enrollment_token=enrollment_token,
                runtime_type=body.runtime_type,
                display_name=body.display_name,
                platform=body.platform,
                policy_mode=body.policy_mode,
                capabilities=body.capabilities,
                execution_targets=body.execution_targets or ["local"],
                instance_id=body.instance_id,
                capability_digest=body.capability_digest,
                prewarm_state=body.prewarm_state,
                warm_pool=body.warm_pool,
                permission_probe=body.permission_probe,
                runtime_role=body.runtime_role,
                install_id=body.install_id,
                specialist_key=body.specialist_key,
                summary_channel=body.summary_channel,
                artifact_channel=body.artifact_channel,
                local_private_memory_only=body.local_private_memory_only,
                note=body.note,
                current_run_id=body.current_run_id,
                summary_text=body.summary_text,
                artifacts=body.artifacts,
                health_state=body.health_state,
            )

        result = await run_in_threadpool(_register_runtime)
        return {
            "ok": True,
            "runtime": _runtime_summary_from_worker_item(result.get("runtime") or {}),
            "session_token": result.get("session_token"),
            "machine_id": result.get("machine_id") or runtime_token,
            "instance_id": result.get("instance_id"),
            "capability_digest": result.get("capability_digest"),
            "session_issued_at": result.get("session_issued_at"),
            "enrollment_bootstrap": True,
            "connection_mode": result.get("connection_mode") or "platform_relay",
        }

    @app.post("/runtime/companions/{runtime_id}/bootstrap")
    async def bootstrap_runtime_companion(runtime_id: str, payload: RuntimeCompanionBootstrapPayload):
        runtime_token = str(runtime_id or "").strip()
        if not runtime_token:
            raise HTTPException(status_code=400, detail="runtime_id is required.")
        body = payload
        _enforce_runtime_session_api_decision(
            operation="runtime_bootstrap",
            tenant_id="default",
            workspace_id="default",
            runtime_id=runtime_token,
            runtime_type=body.runtime_type or "local_companion",
            runtime_role=body.runtime_role,
            instance_id=body.instance_id,
            enrollment_token_present=bool(body.enrollment_token),
            required_capabilities=list(body.capabilities or []),
        )

        def _bootstrap_runtime() -> Dict[str, Any]:
            return local_queue.handle_bootstrap_enrolled_local_companion_runtime(
                runtime_token,
                enrollment_token=body.enrollment_token,
                runtime_type=body.runtime_type or "local_companion",
                display_name=body.display_name,
                platform=body.platform,
                policy_mode=body.policy_mode,
                capabilities=body.capabilities,
                execution_targets=body.execution_targets or ["local_companion"],
                instance_id=body.instance_id,
                capability_digest=body.capability_digest,
                prewarm_state=body.prewarm_state,
                warm_pool=body.warm_pool,
                permission_probe=body.permission_probe,
                runtime_role=body.runtime_role,
                install_id=body.install_id,
                specialist_key=body.specialist_key,
                summary_channel=body.summary_channel,
                artifact_channel=body.artifact_channel,
                local_private_memory_only=body.local_private_memory_only,
                note=body.note,
                current_run_id=body.current_run_id,
                summary_text=body.summary_text,
                artifacts=body.artifacts,
                health_state=body.health_state,
            )

        result = await run_in_threadpool(_bootstrap_runtime)
        return {
            "ok": True,
            "runtime": _runtime_summary_from_worker_item(result.get("runtime") or {}),
            "session_token": result.get("session_token"),
            "machine_id": result.get("machine_id") or runtime_token,
            "instance_id": result.get("instance_id"),
            "capability_digest": result.get("capability_digest"),
            "session_issued_at": result.get("session_issued_at"),
            "enrollment_bootstrap": True,
            "connection_mode": result.get("connection_mode") or "platform_relay",
        }

    @app.post("/runtime/self-hosted-nodes/{runtime_profile_id}/enroll")
    async def enroll_self_hosted_node(runtime_profile_id: str, payload: SelfHostedNodeEnrollPayload):
        try:
            result = await agent_registry_repository.enroll_self_hosted_runtime_profile(
                runtime_profile_id=runtime_profile_id,
                enrollment_token=payload.enrollment_token,
                public_key=payload.public_key,
                node_kind=payload.node_kind,
                capabilities=payload.capabilities,
                max_concurrent_sessions=payload.max_concurrent_sessions,
                root_policy=payload.root_policy,
                label=payload.display_name,
            )
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
        return {
            "ok": True,
            "runtime_profile_id": result.get("runtime_profile_id"),
            "runtime_node_id": result.get("runtime_node_id"),
            "workspace_id": result.get("workspace_id"),
            "node_session_token": result.get("node_session_token"),
            "owner_approval_required": True,
            "runtime_profile": result.get("runtime_profile"),
        }

    @app.post("/runtime/self-hosted-nodes/{runtime_profile_id}/heartbeat")
    async def heartbeat_self_hosted_node(runtime_profile_id: str, payload: SelfHostedNodeHeartbeatPayload):
        try:
            result = await agent_registry_repository.resolve_self_hosted_runtime_heartbeat(
                runtime_profile_id=runtime_profile_id,
                node_session_token=payload.node_session_token,
                status=payload.status or "idle",
                note=payload.note,
                capabilities=payload.capabilities,
                health_state=payload.health_state or "healthy",
            )
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
        return result

    @app.post("/runtime/self-hosted-nodes/{runtime_profile_id}/vault-backup")
    async def put_self_hosted_node_vault_backup(runtime_profile_id: str, payload: SelfHostedNodeVaultBackupPutPayload):
        """Accept an encrypted vault blob from a self-hosted node for off-box backup.

        The blob is encrypted at rest (Fernet+PBKDF2) before transmission.
        Without the vault key (CREDENTIAL_VAULT_KEY, box-only), the blob is
        unreadable — safe to store on the cloud side.
        """
        _enforce_runtime_session_api_decision(
            operation="self_hosted_vault_backup_put",
            tenant_id="default",
            workspace_id="default",
            runtime_id=runtime_profile_id,
            session_token=payload.node_session_token,
            runtime_session_valid=True,
        )
        try:
            result = await agent_registry_repository.store_vault_backup_blob(
                runtime_profile_id=runtime_profile_id,
                vault_blob_base64=payload.vault_blob_base64,
                environment=payload.environment,
                backup_version=payload.backup_version,
            )
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
        return result

    @app.post("/runtime/self-hosted-nodes/{runtime_profile_id}/vault-backup/claim")
    async def claim_self_hosted_node_vault_backup(runtime_profile_id: str, payload: SelfHostedNodeVaultBackupClaimPayload):
        """Return the encrypted vault blob to a self-hosted node for disaster recovery.

        The node must present a valid session token.  The blob is returned
        encrypted — only a node with the correct CREDENTIAL_VAULT_KEY can
        decrypt it.
        """
        _enforce_runtime_session_api_decision(
            operation="self_hosted_vault_backup_claim",
            tenant_id="default",
            workspace_id="default",
            runtime_id=runtime_profile_id,
            session_token=payload.node_session_token,
            runtime_session_valid=True,
        )
        try:
            result = await agent_registry_repository.retrieve_vault_backup_blob(
                runtime_profile_id=runtime_profile_id,
            )
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
        return result

    @app.post("/runtime/self-hosted-nodes/{runtime_profile_id}/commands", dependencies=[Depends(require_api_key)])
    async def enqueue_self_hosted_node_command(
        runtime_profile_id: str,
        payload: SelfHostedNodeCommandEnqueuePayload,
        current_user: Dict[str, Any] = Depends(require_api_key),
    ):
        workspace_id = enforce_workspace_access(current_user, payload.workspace_id)
        role = workspace_role(current_user, workspace_id)
        if role not in {"owner", "admin"} and not bool((current_user or {}).get("is_admin")):
            raise HTTPException(status_code=403, detail="Workspace owner or admin role is required.")
        _ensure_advanced_features_access(workspace_id)
        tenant_id = workspace_tenant_id(current_user, workspace_id)
        _enforce_runtime_session_api_decision(
            operation="self_hosted_command_enqueue",
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            current_user=current_user,
            runtime_id=runtime_profile_id,
            node_id=payload.runtime_node_id,
            agent_id=payload.agent_id,
            command_type=payload.command_type,
            command_payload_present=bool(payload.command_payload),
            timeout_seconds=payload.ttl_seconds,
        )
        try:
            result = await agent_registry_repository.enqueue_self_hosted_runtime_command(
                runtime_profile_id=runtime_profile_id,
                tenant_id=tenant_id,
                workspace_id=workspace_id,
                runtime_node_id=payload.runtime_node_id,
                agent_id=payload.agent_id,
                command_type=payload.command_type,
                command_payload=payload.command_payload,
                requested_by_user_id=str((current_user or {}).get("user_id") or "").strip() or None,
                ttl_seconds=payload.ttl_seconds or agent_registry_repository.SELF_HOSTED_NODE_COMMAND_DEFAULT_TTL_SECONDS,
            )
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
        return result

    @app.post("/runtime/self-hosted-nodes/{runtime_profile_id}/commands/claim")
    async def claim_self_hosted_node_commands(runtime_profile_id: str, payload: SelfHostedNodeCommandClaimPayload):
        _enforce_runtime_session_api_decision(
            operation="self_hosted_command_claim",
            tenant_id="default",
            workspace_id="default",
            runtime_id=runtime_profile_id,
            session_token=payload.node_session_token,
            runtime_session_valid=True,
            max_commands=payload.max_commands,
            lease_seconds=payload.lease_seconds,
        )
        try:
            return await agent_registry_repository.claim_self_hosted_runtime_commands(
                runtime_profile_id=runtime_profile_id,
                node_session_token=payload.node_session_token,
                max_commands=payload.max_commands,
                lease_seconds=payload.lease_seconds,
            )
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error

    @app.post("/runtime/self-hosted-nodes/{runtime_profile_id}/commands/{command_id}/result")
    async def complete_self_hosted_node_command(
        runtime_profile_id: str,
        command_id: str,
        payload: SelfHostedNodeCommandResultPayload,
    ):
        _enforce_runtime_session_api_decision(
            operation="self_hosted_command_result",
            tenant_id="default",
            workspace_id="default",
            runtime_id=runtime_profile_id,
            session_token=payload.node_session_token,
            runtime_session_valid=True,
            status=payload.status,
            artifact_count=len(payload.artifacts or []),
        )
        try:
            result = await agent_registry_repository.complete_self_hosted_runtime_command(
                runtime_profile_id=runtime_profile_id,
                node_session_token=payload.node_session_token,
                command_id=command_id,
                status=payload.status,
                result_payload=payload.result_payload,
                artifacts=payload.artifacts,
                audit_references=payload.audit_references,
                error=payload.error,
            )
            return await hardware_action_broker_service.record_self_hosted_command_completion(result)
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error

    @app.post("/runtime/hardware/actions/execute", dependencies=[Depends(require_api_key)])
    async def execute_runtime_hardware_action(
        payload: RuntimeHardwareActionExecutePayload,
        current_user: Dict[str, Any] = Depends(require_api_key),
    ):
        workspace_id = enforce_workspace_access(
            current_user,
            payload.workspace_id,
            minimum_role="member",
        )
        _ensure_runtime_action_operator(current_user, workspace_id)
        _ensure_advanced_features_access(workspace_id)
        tenant_id = workspace_tenant_id(current_user, workspace_id)
        _enforce_runtime_session_api_decision(
            operation="hardware_action_execute",
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            current_user=current_user,
            action_id=payload.action_id,
            run_id=payload.run_id,
            runtime_target=payload.runtime_target,
            runtime_id=payload.gateway_id or payload.device_id or payload.session_id,
            node_id=payload.node_id,
            session_token=payload.session_id,
            timeout_seconds=payload.timeout_seconds,
        )
        try:
            return await hardware_action_broker_service.execute_hardware_action(
                tenant_id=tenant_id,
                workspace_id=workspace_id,
                user_id=str((current_user or {}).get("user_id") or "").strip() or None,
                action_id=payload.action_id,
                arguments=payload.arguments,
                runtime_target=payload.runtime_target,
                capability_id=payload.capability_id,
                gateway_id=payload.gateway_id,
                device_id=payload.device_id,
                node_id=payload.node_id,
                run_id=payload.run_id,
                trace_id=payload.trace_id,
                thread_id=payload.thread_id,
                request_id=payload.request_id,
                session_id=payload.session_id,
                timeout_seconds=payload.timeout_seconds,
                runtime_access_mode=payload.runtime_access_mode,
                execution_mode=payload.execution_mode,
                require_approval=payload.require_approval,
                cost_metadata=payload.cost_metadata,
            )
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error

    @app.post("/runtime/hardware/actions/stop", dependencies=[Depends(require_api_key)])
    async def stop_runtime_hardware_action(
        payload: RuntimeHardwareActionStopPayload,
        current_user: Dict[str, Any] = Depends(require_api_key),
    ):
        workspace_id = enforce_workspace_access(
            current_user,
            payload.workspace_id,
            minimum_role="member",
        )
        _ensure_runtime_action_operator(current_user, workspace_id)
        _ensure_advanced_features_access(workspace_id)
        tenant_id = workspace_tenant_id(current_user, workspace_id)
        _enforce_runtime_session_api_decision(
            operation="hardware_action_stop",
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            current_user=current_user,
            run_id=payload.run_id,
            runtime_target=payload.runtime_target,
            runtime_id=payload.gateway_id or payload.session_id,
            node_id=payload.node_id,
            session_token=payload.session_id,
            timeout_seconds=payload.timeout_seconds,
        )
        try:
            return await hardware_action_broker_service.stop_hardware_action(
                tenant_id=tenant_id,
                workspace_id=workspace_id,
                run_id=payload.run_id,
                runtime_target=payload.runtime_target,
                gateway_id=payload.gateway_id,
                node_id=payload.node_id,
                trace_id=payload.trace_id,
                target_request_id=payload.target_request_id,
                request_id=payload.request_id,
                thread_id=payload.thread_id,
                reason=payload.reason,
                session_id=payload.session_id,
                timeout_seconds=payload.timeout_seconds,
            )
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error

    @app.post("/runtime/runtimes/{runtime_id}/heartbeat")
    async def heartbeat_runtime(runtime_id: str, payload: Optional[RuntimeHeartbeatPayload] = None):
        runtime_token = str(runtime_id or "").strip()
        if not runtime_token:
            raise HTTPException(status_code=400, detail="runtime_id is required.")
        body = payload or RuntimeHeartbeatPayload()
        local_payload = _local_cluster_worker_payload(body)
        if not str(local_payload.note or "").strip():
            local_payload.note = "runtime_heartbeat"
        def _heartbeat_runtime() -> Dict[str, Any]:
            local_queue._assert_runtime_session(runtime_token, body.session_token, instance_id=body.instance_id)
            _enforce_runtime_session_api_decision(
                operation="runtime_start",
                tenant_id="default",
                workspace_id="default",
                runtime_id=runtime_token,
                instance_id=body.instance_id,
                session_token=body.session_token,
                runtime_session_valid=True,
                run_id=body.current_run_id,
            )
            return local_queue.handle_heartbeat_local_runtime(runtime_token, local_payload)

        result = await run_in_threadpool(_heartbeat_runtime)
        return {
            "ok": True,
            "runtime_id": runtime_token,
            "current_task_id": result.get("current_run_id"),
            "last_seen_at": result.get("last_seen_at"),
            "runtime": _runtime_summary_from_worker_item(result.get("runtime") or {}),
        }

    @app.post("/runtime/local-cluster/{runtime_id}/register", dependencies=[Depends(require_api_key)])
    async def register_local_cluster_runtime(runtime_id: str, payload: Optional[RuntimeRegisterPayload] = None):
        runtime_token = str(runtime_id or "").strip()
        if not runtime_token:
            raise HTTPException(status_code=400, detail="runtime_id is required.")
        body = payload or RuntimeRegisterPayload()
        enrollment_token = _require_runtime_registration_enrollment_token(body)
        _enforce_runtime_session_api_decision(
            operation="runtime_register",
            tenant_id="default",
            workspace_id="default",
            runtime_id=runtime_token,
            runtime_type=body.runtime_type,
            runtime_role=body.runtime_role,
            instance_id=body.instance_id,
            enrollment_token_present=True,
            required_capabilities=list(body.capabilities or []),
        )
        result = await run_in_threadpool(
            local_queue.handle_bootstrap_enrolled_local_companion_runtime,
            runtime_token,
            enrollment_token=enrollment_token,
            runtime_type=body.runtime_type,
            display_name=body.display_name,
            platform=body.platform,
            policy_mode=body.policy_mode,
            capabilities=body.capabilities,
            execution_targets=body.execution_targets or ["local_companion"],
            instance_id=body.instance_id,
            capability_digest=body.capability_digest,
            prewarm_state=body.prewarm_state,
            warm_pool=body.warm_pool,
            permission_probe=body.permission_probe,
            runtime_role=body.runtime_role,
            install_id=body.install_id,
            specialist_key=body.specialist_key,
            summary_channel=body.summary_channel,
            artifact_channel=body.artifact_channel,
            local_private_memory_only=body.local_private_memory_only,
            note=body.note,
            current_run_id=body.current_run_id,
            summary_text=body.summary_text,
            artifacts=body.artifacts,
            health_state=body.health_state,
        )
        result["runtime"] = _runtime_summary_from_worker_item(result.get("runtime") or {})
        return result

    @app.post("/runtime/local-cluster/{runtime_id}/start")
    async def start_local_cluster_runtime(runtime_id: str, payload: Optional[LocalClusterLifecyclePayload] = None):
        runtime_token = str(runtime_id or "").strip()
        if not runtime_token:
            raise HTTPException(status_code=400, detail="runtime_id is required.")
        body = payload or LocalClusterLifecyclePayload()
        def _start_runtime() -> Dict[str, Any]:
            local_queue._assert_runtime_session(runtime_token, body.session_token, instance_id=body.instance_id)
            _enforce_runtime_session_api_decision(
                operation="runtime_heartbeat",
                tenant_id="default",
                workspace_id="default",
                runtime_id=runtime_token,
                instance_id=body.instance_id,
                session_token=body.session_token,
                runtime_session_valid=True,
                run_id=body.current_run_id,
            )
            return local_queue.handle_start_local_runtime(runtime_token, _local_cluster_worker_payload(body))

        result = await run_in_threadpool(_start_runtime)
        result["runtime"] = _runtime_summary_from_worker_item(result.get("runtime") or {})
        return result

    @app.get("/runtime/local-cluster/{runtime_id}/health", dependencies=[Depends(require_api_key)])
    async def get_local_cluster_runtime_health(runtime_id: str):
        runtime_token = str(runtime_id or "").strip()
        if not runtime_token:
            raise HTTPException(status_code=400, detail="runtime_id is required.")
        result = await run_in_threadpool(local_queue.handle_get_local_runtime_health, runtime_token)
        result["runtime"] = _runtime_summary_from_worker_item(result.get("runtime") or {})
        return result

    @app.post("/runtime/local-cluster/{runtime_id}/heartbeat")
    async def heartbeat_local_cluster_runtime(runtime_id: str, payload: Optional[LocalClusterLifecyclePayload] = None):
        runtime_token = str(runtime_id or "").strip()
        if not runtime_token:
            raise HTTPException(status_code=400, detail="runtime_id is required.")
        body = payload or LocalClusterLifecyclePayload()
        def _heartbeat_local_cluster_runtime() -> Dict[str, Any]:
            local_queue._assert_runtime_session(runtime_token, body.session_token, instance_id=body.instance_id)
            _enforce_runtime_session_api_decision(
                operation="runtime_heartbeat",
                tenant_id="default",
                workspace_id="default",
                runtime_id=runtime_token,
                instance_id=body.instance_id,
                session_token=body.session_token,
                runtime_session_valid=True,
                run_id=body.current_run_id,
            )
            return local_queue.handle_heartbeat_local_runtime(runtime_token, _local_cluster_worker_payload(body))

        result = await run_in_threadpool(_heartbeat_local_cluster_runtime)
        result["runtime"] = _runtime_summary_from_worker_item(result.get("runtime") or {})
        return result

    @app.post("/runtime/local-cluster/{runtime_id}/stop")
    async def stop_local_cluster_runtime(runtime_id: str, payload: Optional[LocalClusterLifecyclePayload] = None):
        runtime_token = str(runtime_id or "").strip()
        if not runtime_token:
            raise HTTPException(status_code=400, detail="runtime_id is required.")
        body = payload or LocalClusterLifecyclePayload()
        def _stop_runtime() -> Dict[str, Any]:
            local_queue._assert_runtime_session(runtime_token, body.session_token, instance_id=body.instance_id)
            _enforce_runtime_session_api_decision(
                operation="runtime_stop",
                tenant_id="default",
                workspace_id="default",
                runtime_id=runtime_token,
                instance_id=body.instance_id,
                session_token=body.session_token,
                runtime_session_valid=True,
            )
            return local_queue.handle_stop_local_runtime(
                runtime_token,
                reason=body.reason,
                note=body.note,
                summary_text=body.summary_text,
                artifacts=body.artifacts,
                health_state=body.health_state,
            )

        result = await run_in_threadpool(_stop_runtime)
        result["runtime"] = _runtime_summary_from_worker_item(result.get("runtime") or {})
        return result

    @app.post("/runtime/local-cluster/{runtime_id}/revoke", dependencies=[Depends(require_api_key)])
    async def revoke_local_cluster_runtime(runtime_id: str, payload: Optional[LocalClusterLifecyclePayload] = None):
        runtime_token = str(runtime_id or "").strip()
        if not runtime_token:
            raise HTTPException(status_code=400, detail="runtime_id is required.")
        body = payload or LocalClusterLifecyclePayload()
        _enforce_runtime_session_api_decision(
            operation="runtime_revoke",
            tenant_id="default",
            workspace_id="default",
            runtime_id=runtime_token,
        )
        result = await run_in_threadpool(local_queue.handle_revoke_local_runtime, runtime_token, reason=body.reason)
        result["machine"] = _runtime_summary_from_worker_item(result.get("machine") or {})
        return result

    @app.post("/runtime/local-cluster/{runtime_id}/recover")
    async def recover_local_cluster_runtime(runtime_id: str, payload: Optional[LocalClusterLifecyclePayload] = None):
        runtime_token = str(runtime_id or "").strip()
        if not runtime_token:
            raise HTTPException(status_code=400, detail="runtime_id is required.")
        body = payload or LocalClusterLifecyclePayload()
        def _recover_runtime() -> Dict[str, Any]:
            local_queue._assert_runtime_session(runtime_token, body.session_token, instance_id=body.instance_id)
            _enforce_runtime_session_api_decision(
                operation="runtime_recover",
                tenant_id="default",
                workspace_id="default",
                runtime_id=runtime_token,
                instance_id=body.instance_id,
                session_token=body.session_token,
                runtime_session_valid=True,
                run_id=body.current_run_id,
            )
            return local_queue.handle_recover_local_runtime(runtime_token, _local_cluster_worker_payload(body))

        result = await run_in_threadpool(_recover_runtime)
        result["runtime"] = _runtime_summary_from_worker_item(result.get("runtime") or {})
        return result

    @app.get("/runtime/runtimes/{runtime_id}/control/stream")
    async def stream_runtime_control(
        runtime_id: str,
        session_token: Optional[str] = None,
        instance_id: Optional[str] = None,
        since_sequence: int = 0,
        include_backlog: bool = True,
        heartbeat_seconds: float = 5.0,
        timeout_seconds: float = 30.0,
    ):
        runtime_token = str(runtime_id or "").strip()
        if not runtime_token:
            raise HTTPException(status_code=400, detail="runtime_id is required.")
        _enforce_runtime_session_api_decision(
            operation="runtime_control_stream",
            tenant_id="default",
            workspace_id="default",
            runtime_id=runtime_token,
            instance_id=instance_id,
            session_token=session_token,
            runtime_session_valid=True,
        )
        local_queue._assert_runtime_session(runtime_token, session_token, instance_id=instance_id)
        safe_heartbeat = max(1.0, min(float(heartbeat_seconds), 60.0))
        return EventSourceResponse(
            local_queue.aiter_runtime_control_stream(
                runtime_token,
                since_sequence=max(0, int(since_sequence or 0)),
                include_backlog=bool(include_backlog),
                heartbeat_seconds=safe_heartbeat,
                timeout_seconds=max(safe_heartbeat, min(float(timeout_seconds or 30.0), 300.0)),
            ),
            ping=max(3, int(safe_heartbeat)),
        )

    @app.post("/runtime/tasks/claim")
    async def claim_runtime_task(body: Optional[RuntimeTaskClaimRequest] = None):
        payload = body or RuntimeTaskClaimRequest()
        runtime_token = str(payload.runtime_id or "").strip()
        if not runtime_token:
            raise HTTPException(status_code=400, detail="runtime_id is required.")
        requested_target = str(payload.execution_target or "local").strip().lower()
        if requested_target not in {"local", "local_companion"}:
            raise HTTPException(status_code=400, detail="Only local execution_target is supported by this runtime bridge.")
        def _claim_task() -> Dict[str, Any]:
            local_queue._assert_runtime_session(runtime_token, payload.session_token, instance_id=payload.instance_id)
            _enforce_runtime_session_api_decision(
                operation="runtime_task_claim",
                tenant_id="default",
                workspace_id="default",
                runtime_id=runtime_token,
                instance_id=payload.instance_id,
                session_token=payload.session_token,
                runtime_session_valid=True,
                runtime_target=requested_target,
                required_capabilities=list(payload.required_capabilities or []),
            )
            return local_queue.handle_claim_local_run(
                local_queue.LocalRunClaimRequest(
                    worker_id=runtime_token,
                    required_capabilities=list(payload.required_capabilities or []),
                )
            )

        result = await run_in_threadpool(_claim_task)
        run = result.get("run") if isinstance(result.get("run"), dict) else None
        return {
            "ok": True,
            "runtime_id": result.get("worker_id") or runtime_token,
            "task": _task_summary_from_local_claim(run) if isinstance(run, dict) else None,
        }

    @app.post("/runtime/tasks/{task_id}/heartbeat")
    async def heartbeat_runtime_task(task_id: uuid.UUID, payload: Optional[RuntimeTaskHeartbeatPayload] = None):
        body = payload or RuntimeTaskHeartbeatPayload()
        runtime_token = str(body.runtime_id or "").strip()
        local_payload = local_queue.LocalRunHeartbeatPayload(
            worker_id=runtime_token or None,
            note=body.note or "runtime_task_heartbeat",
            event=(dict(body.event) if isinstance(body.event, dict) else None),
        )
        def _heartbeat_task() -> Dict[str, Any]:
            local_queue._assert_runtime_session(runtime_token, body.session_token, instance_id=body.instance_id)
            _enforce_runtime_session_api_decision(
                operation="runtime_task_heartbeat",
                tenant_id="default",
                workspace_id="default",
                runtime_id=runtime_token,
                instance_id=body.instance_id,
                session_token=body.session_token,
                runtime_session_valid=True,
                run_id=str(task_id),
                status="running",
                current_run_bound=True,
            )
            return local_queue.handle_heartbeat_local_run(task_id, local_payload)

        result = await run_in_threadpool(_heartbeat_task)
        return {
            "ok": True,
            "task_id": str(task_id),
            "last_heartbeat_at": result.get("last_heartbeat_at"),
        }

    @app.post("/runtime/tasks/{task_id}/control-state")
    async def get_runtime_task_control_state(task_id: uuid.UUID, payload: Optional[RuntimeTaskControlStatePayload] = None):
        body = payload or RuntimeTaskControlStatePayload()
        runtime_token = str(body.runtime_id or "").strip()
        def _get_control_state() -> Dict[str, Any]:
            local_queue._assert_runtime_session(runtime_token, body.session_token, instance_id=body.instance_id)
            _enforce_runtime_session_api_decision(
                operation="runtime_task_control_state",
                tenant_id="default",
                workspace_id="default",
                runtime_id=runtime_token,
                instance_id=body.instance_id,
                session_token=body.session_token,
                runtime_session_valid=True,
                run_id=str(task_id),
                status="running",
                current_run_bound=True,
            )
            return local_queue.handle_get_local_run_control_state(
                task_id,
                local_queue.LocalRunControlStatePayload(worker_id=runtime_token or None),
            )

        result = await run_in_threadpool(_get_control_state)
        return {"ok": True, "task_id": str(task_id), **result}

    @app.post("/runtime/tasks/{task_id}/complete")
    async def complete_runtime_task(task_id: uuid.UUID, payload: RuntimeTaskCompletePayload):
        def _complete_task() -> Dict[str, Any]:
            runtime_token = str(payload.runtime_id or "").strip()
            local_queue._assert_runtime_session(runtime_token, payload.session_token, instance_id=payload.instance_id)
            _enforce_runtime_session_api_decision(
                operation="runtime_task_complete",
                tenant_id="default",
                workspace_id="default",
                runtime_id=runtime_token,
                instance_id=payload.instance_id,
                session_token=payload.session_token,
                runtime_session_valid=True,
                run_id=str(task_id),
                status="running",
                current_run_bound=True,
            )
            return local_queue.handle_complete_local_run(
                task_id,
                local_queue.LocalRunCompletePayload(
                    worker_id=(str(payload.runtime_id or "").strip() or None),
                    result_text=payload.result_text,
                    result_data=payload.result_data,
                    usage_masked=payload.usage_masked,
                ),
            )

        result = await run_in_threadpool(_complete_task)
        return {"ok": True, "task_id": str(task_id), **result}

    @app.post("/runtime/tasks/{task_id}/pause")
    async def pause_runtime_task(task_id: uuid.UUID, payload: RuntimeTaskPausePayload):
        def _pause_task() -> Dict[str, Any]:
            runtime_token = str(payload.runtime_id or "").strip()
            local_queue._assert_runtime_session(runtime_token, payload.session_token, instance_id=payload.instance_id)
            _enforce_runtime_session_api_decision(
                operation="runtime_task_pause",
                tenant_id="default",
                workspace_id="default",
                runtime_id=runtime_token,
                instance_id=payload.instance_id,
                session_token=payload.session_token,
                runtime_session_valid=True,
                run_id=str(task_id),
                status="running",
                current_run_bound=True,
            )
            return local_queue.handle_pause_local_run(
                task_id,
                local_queue.LocalRunPausePayload(
                    worker_id=(str(payload.runtime_id or "").strip() or None),
                    result_text=payload.result_text,
                    result_data=payload.result_data,
                    browser_checkpoint=payload.browser_checkpoint,
                    wait_reason=payload.wait_reason,
                ),
            )

        result = await run_in_threadpool(_pause_task)
        return {"ok": True, "task_id": str(task_id), **result}

    @app.post("/runtime/tasks/{task_id}/fail")
    async def fail_runtime_task(task_id: uuid.UUID, payload: RuntimeTaskFailPayload):
        def _fail_task() -> Dict[str, Any]:
            runtime_token = str(payload.runtime_id or "").strip()
            local_queue._assert_runtime_session(runtime_token, payload.session_token, instance_id=payload.instance_id)
            _enforce_runtime_session_api_decision(
                operation="runtime_task_fail",
                tenant_id="default",
                workspace_id="default",
                runtime_id=runtime_token,
                instance_id=payload.instance_id,
                session_token=payload.session_token,
                runtime_session_valid=True,
                run_id=str(task_id),
                status="running",
                current_run_bound=True,
            )
            return local_queue.handle_fail_local_run(
                task_id,
                local_queue.LocalRunFailPayload(
                    worker_id=(str(payload.runtime_id or "").strip() or None),
                    error=payload.error,
                ),
            )

        result = await run_in_threadpool(_fail_task)
        return {"ok": True, "task_id": str(task_id), **result}
