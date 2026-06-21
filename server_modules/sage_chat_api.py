from __future__ import annotations

import mimetypes
import uuid
from pathlib import Path

from fastapi import Depends, File, HTTPException, Query, UploadFile

from server_modules import activity_ledger_service, sage_proof_log_service, security_audit_service
from server_modules.auth import enforce_workspace_access, workspace_tenant_id
from server_modules.sage_agent_runtime_contract import (
    SAGE_MODE,
    normalize_sage_mode,
    normalize_sage_surface,
)
from server_modules.sage_agent_runtime_service import handle_sage_chat
from server_modules.channel_adapter import normalize_sage_inbound, filter_outbound_reply
from server_modules.voice_notification_policy_service import execute_voice_sage_task
from server_modules.sage_approval_service import (
    resolve_approval,
    consume_approval,
)
from server_modules.schemas import SageChatRequest, SageVoiceTaskRequest, SageApprovalResolveRequest
from server_modules.workspace_context import workspace_attachments_dir

MAX_ATTACHMENT_BYTES = 32 * 1024 * 1024  # 32 MB


def _coerce_text(value) -> str:
    return str(value or "").strip()


def _resolve_tenant_id(current_user: dict | None, workspace_id: str) -> str:
    try:
        tenant_id = _coerce_text(workspace_tenant_id(current_user or {}, workspace_id))
    except Exception:
        tenant_id = ""
    if tenant_id:
        return tenant_id
    workspace_access = (current_user or {}).get("workspace_access")
    if isinstance(workspace_access, dict):
        workspace_entry = workspace_access.get(workspace_id)
        if isinstance(workspace_entry, dict):
            entry_tenant = _coerce_text(workspace_entry.get("tenant_id"))
            if entry_tenant:
                return entry_tenant
        for value in workspace_access.values():
            if isinstance(value, dict):
                entry_tenant = _coerce_text(value.get("tenant_id"))
                if entry_tenant:
                    return entry_tenant
    for key in ("current_tenant_id", "default_tenant_id"):
        candidate = _coerce_text((current_user or {}).get(key))
        if candidate:
            return candidate
    tenant_ids = (current_user or {}).get("tenant_ids")
    if isinstance(tenant_ids, list):
        for value in tenant_ids:
            candidate = _coerce_text(value)
            if candidate:
                return candidate
    return "default"


def _emit_approval_audit(
    *,
    action: str,
    status: str,
    approval_token: str,
    tenant_id: str,
    workspace_id: str,
    actor_user_id: str,
    trace_id: str,
    detail: str = "",
    metadata: dict | None = None,
) -> None:
    try:
        security_audit_service.emit_security_audit_event(
            action=action,
            status=status,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            actor_user_id=actor_user_id or None,
            trace_id=trace_id,
            detail=detail,
            metadata=metadata or {},
            idempotency_key=f"{action}:{approval_token}",
        )
    except Exception:
        pass


def _execute_approved_sage_action(*, workspace_id: str, approval_record) -> dict:
    # Phase 3 minimal path: support only channel_send_draft.
    if _coerce_text(approval_record.action) != "channel_send_draft":
        raise ValueError("Unsupported approved action.")
    consumed = consume_approval(
        approval_token=approval_record.approval_token,
        workspace_id=workspace_id,
    )
    payload = approval_record.action_payload if isinstance(approval_record.action_payload, dict) else {}
    return {
        "status": "executed",
        "consumed_status": consumed.status,
        "action": approval_record.action,
        "execution": {
            "channel": _coerce_text(payload.get("channel")),
            "recipient": _coerce_text(payload.get("recipient")),
            "message_text": _coerce_text(payload.get("message_text")),
        },
    }


async def _emit_approval_activity(
    *,
    action: str,
    status: str,
    approval_token: str,
    tenant_id: str,
    workspace_id: str,
    actor_user_id: str,
    trace_id: str,
    review_required: bool = False,
    summary: str = "",
    payload: dict | None = None,
) -> None:
    try:
        await activity_ledger_service.append_activity_event(
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            actor_type="user",
            actor_id=actor_user_id or "owner",
            event_class="approval",
            detail_level="timeline_detail",
            action=action,
            trace_id=trace_id,
            status=status,
            review_required=review_required,
            summary=summary or action,
            payload={
                "source": "sage_chat_surface",
                "approval_token": approval_token,
                **(payload or {}),
            },
            metadata={"surface": "chat"},
        )
    except Exception:
        pass


def register_sage_chat_routes(app) -> None:
    import server as _server

    module_globals = globals()
    for key, value in _server.__dict__.items():
        if key not in module_globals:
            module_globals[key] = value

    member_dependency = getattr(_server, "require_api_key")

    @app.post("/api/sage/chat", dependencies=[Depends(member_dependency)])
    async def sage_chat(
        body: SageChatRequest,
        current_user=Depends(member_dependency),
    ):
        if not body.workspace_id or not str(body.workspace_id).strip():
            raise HTTPException(status_code=400, detail="workspace_id is required.")
        if not body.message or not str(body.message).strip():
            raise HTTPException(status_code=400, detail="message must not be empty.")

        try:
            normalized_mode = normalize_sage_mode(body.mode)
            normalized_surface = normalize_sage_surface(body.surface)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))

        resolved_workspace_id = enforce_workspace_access(
            current_user,
            body.workspace_id,
            minimum_role="member",
        )
        tenant_id = _resolve_tenant_id(current_user, resolved_workspace_id)

        try:
            # ── Shared command dispatcher ──
            _msg_text = str(body.message).strip()
            from server_modules.sage_command_dispatcher import dispatch_command as _dispatch_cmd
            _cmd_reply = await _dispatch_cmd(
                command=_msg_text,
                workspace_id=resolved_workspace_id,
                thread_id="sage-main",
                channel_origin="web",
            )
            if _cmd_reply is not None:
                return {
                    "workspace_id": resolved_workspace_id,
                    "tenant_id": tenant_id,
                    "message": _cmd_reply,
                    "error": None,
                    "used_context": [],
                    "tool_calls": [],
                    "available_tools": [],
                    "blocked_tools": [],
                    "approvals_required": [],
                    "memory_updates": [],
                    "trace_id": "",
                    "provider": "",
                    "model": None,
                    "suppressed": False,
                }

            turn = normalize_sage_inbound(
                workspace_id=resolved_workspace_id,
                tenant_id=tenant_id,
                message=_msg_text,
                surface=normalized_surface,
                mode=normalized_mode,
                attachments=[a.model_dump() for a in body.attachments],
                current_user=current_user,
                channel_origin="web",
            )
            # Resolve active thread (may be task thread if /new was used)
            from server_modules.sage_command_dispatcher import get_active_thread as _gat_web
            _active_thread_web = await _gat_web(turn.workspace_id, "web")
            result = await handle_sage_chat(
                workspace_id=turn.workspace_id,
                tenant_id=turn.tenant_id,
                message=turn.message,
                surface=turn.surface,
                mode=turn.mode,
                attachments=turn.attachments,
                current_user=turn.current_user,
                channel_origin=turn.channel_origin,
                thread_id=_active_thread_web,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        except RuntimeError as exc:
            _msg = str(exc).lower()
            # Credit exhaustion / provider unavailable → 402 so the frontend
            # can display the hard-stop message with a link to AI & Setup.
            if (
                "reached your ai limit" in _msg or "ai limit" in _msg
                or "not available" in _msg
                or "no cloud provider" in _msg
                or "not configured" in _msg
                or "needs attention" in _msg
            ):
                raise HTTPException(status_code=402, detail=str(exc))
            raise HTTPException(status_code=502, detail=str(exc))

        # Filter silent/empty replies via shared adapter function
        filtered = filter_outbound_reply(result.get("message"))
        if filtered is None:
            result["message"] = ""
            result["suppressed"] = True
        return {
            "workspace_id": resolved_workspace_id,
            "tenant_id": tenant_id,
            **result,
        }

    @app.post("/api/sage-chat/attachments", dependencies=[Depends(member_dependency)])
    async def upload_sage_chat_attachment(
        workspace_id: str = Query(default=""),
        file: UploadFile = File(...),
        current_user=Depends(member_dependency),
    ):
        if not workspace_id:
            raise HTTPException(status_code=400, detail="workspace_id is required.")
        resolved_workspace_id = enforce_workspace_access(
            current_user,
            workspace_id,
            minimum_role="member",
        )
        if not file.filename:
            raise HTTPException(status_code=400, detail="filename is required.")
        raw = await file.read()
        if len(raw) > MAX_ATTACHMENT_BYTES:
            raise HTTPException(status_code=413, detail="File exceeds maximum size.")
        file_id = str(uuid.uuid4())
        attach_dir = workspace_attachments_dir(resolved_workspace_id)
        safe_name = f"{file_id}_{Path(file.filename).name}"
        dest = attach_dir / safe_name
        dest.write_bytes(raw)
        content_type = str(file.content_type or mimetypes.guess_type(file.filename)[0] or "application/octet-stream")
        return {
            "file_id": file_id,
            "filename": file.filename,
            "safe_filename": safe_name,
            "content_type": content_type,
            "size": len(raw),
            "url": f"/api/sage-chat/attachments/{file_id}/{safe_name}",
        }

    @app.post("/api/sage/voice-task", dependencies=[Depends(member_dependency)])
    async def sage_voice_task(
        body: SageVoiceTaskRequest,
        current_user=Depends(member_dependency),
    ):
        if not body.workspace_id or not _coerce_text(body.workspace_id):
            raise HTTPException(status_code=400, detail="workspace_id is required.")
        if not body.transcript or not _coerce_text(body.transcript):
            raise HTTPException(status_code=400, detail="transcript must not be empty.")

        resolved_workspace_id = enforce_workspace_access(
            current_user,
            body.workspace_id,
            minimum_role="member",
        )
        tenant_id = workspace_tenant_id(current_user, resolved_workspace_id)
        try:
            result = await execute_voice_sage_task(
                workspace_id=resolved_workspace_id,
                tenant_id=tenant_id,
                transcript=_coerce_text(body.transcript),
                source_channel=_coerce_text(body.source_channel) or "mobile_voice",
                source_message_id=_coerce_text(body.source_message_id),
                current_user=current_user,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        except RuntimeError as exc:
            _msg = str(exc).lower()
            # Credit exhaustion / provider unavailable → 402 so the frontend
            # can display the hard-stop message with a link to AI & Setup.
            if (
                "reached your ai limit" in _msg or "ai limit" in _msg
                or "not available" in _msg
                or "no cloud provider" in _msg
                or "not configured" in _msg
                or "needs attention" in _msg
            ):
                raise HTTPException(status_code=402, detail=str(exc))
            raise HTTPException(status_code=502, detail=str(exc))

        # Filter silent/empty replies via shared adapter function
        filtered = filter_outbound_reply(result.get("message"))
        if filtered is None:
            result["message"] = ""
            result["suppressed"] = True
        return {
            "workspace_id": resolved_workspace_id,
            "tenant_id": tenant_id,
            **result,
        }

    @app.get("/api/sage/proof-logs", dependencies=[Depends(member_dependency)])
    async def list_sage_proof_logs(
        workspace_id: str,
        status: str = "",
        surface: str = "",
        limit: int = sage_proof_log_service.PROOF_LOG_DEFAULT_LIMIT,
        current_user=Depends(member_dependency),
    ):
        if not workspace_id or not _coerce_text(workspace_id):
            raise HTTPException(status_code=400, detail="workspace_id is required.")
        resolved_workspace_id = enforce_workspace_access(
            current_user,
            workspace_id,
            minimum_role="viewer",
        )
        tenant_id = _resolve_tenant_id(current_user, resolved_workspace_id)
        payload = sage_proof_log_service.list_proof_logs(
            workspace_id=resolved_workspace_id,
            tenant_id=tenant_id,
            status=status,
            surface=surface,
            limit=limit,
        )
        return {
            "workspace_id": resolved_workspace_id,
            "tenant_id": tenant_id,
            **payload,
        }

    @app.get("/api/sage/proof-logs/summary", dependencies=[Depends(member_dependency)])
    async def summarize_sage_proof_logs(
        workspace_id: str,
        current_user=Depends(member_dependency),
    ):
        if not workspace_id or not _coerce_text(workspace_id):
            raise HTTPException(status_code=400, detail="workspace_id is required.")
        resolved_workspace_id = enforce_workspace_access(
            current_user,
            workspace_id,
            minimum_role="viewer",
        )
        tenant_id = _resolve_tenant_id(current_user, resolved_workspace_id)
        payload = sage_proof_log_service.summarize_proof_logs(
            workspace_id=resolved_workspace_id,
            tenant_id=tenant_id,
        )
        return {
            "workspace_id": resolved_workspace_id,
            "tenant_id": tenant_id,
            **payload,
        }

    @app.get("/api/sage/proof-logs/{proof_id}", dependencies=[Depends(member_dependency)])
    async def get_sage_proof_log(
        proof_id: str,
        workspace_id: str,
        current_user=Depends(member_dependency),
    ):
        if not workspace_id or not _coerce_text(workspace_id):
            raise HTTPException(status_code=400, detail="workspace_id is required.")
        if not proof_id or not _coerce_text(proof_id):
            raise HTTPException(status_code=400, detail="proof_id is required.")
        resolved_workspace_id = enforce_workspace_access(
            current_user,
            workspace_id,
            minimum_role="viewer",
        )
        tenant_id = _resolve_tenant_id(current_user, resolved_workspace_id)
        record = sage_proof_log_service.get_proof_log(
            workspace_id=resolved_workspace_id,
            tenant_id=tenant_id,
            proof_id=proof_id,
        )
        if record is None:
            raise HTTPException(status_code=404, detail="proof log not found.")
        return {
            "workspace_id": resolved_workspace_id,
            "tenant_id": tenant_id,
            "proof_log": record,
        }

    @app.post("/api/sage/approvals/approve", dependencies=[Depends(member_dependency)])
    async def sage_approve(
        body: SageApprovalResolveRequest,
        current_user=Depends(member_dependency),
    ):
        if not body.workspace_id or not _coerce_text(body.workspace_id):
            raise HTTPException(status_code=400, detail="workspace_id is required.")
        if not body.approval_token or not _coerce_text(body.approval_token):
            raise HTTPException(status_code=400, detail="approval_token is required.")

        resolved_workspace_id = enforce_workspace_access(
            current_user,
            body.workspace_id,
            minimum_role="member",
        )
        tenant_id = _resolve_tenant_id(current_user, resolved_workspace_id)
        actor_user_id = _coerce_text((current_user or {}).get("user_id"))

        try:
            record = resolve_approval(
                approval_token=_coerce_text(body.approval_token),
                workspace_id=resolved_workspace_id,
                status="approved",
                resolution_actor=actor_user_id,
                resolution_reason="Owner approved the action.",
            )
        except ValueError as exc:
            _emit_approval_audit(
                action="approval.approve_failed",
                status="failed",
                approval_token=_coerce_text(body.approval_token),
                tenant_id=tenant_id,
                workspace_id=resolved_workspace_id,
                actor_user_id=actor_user_id,
                trace_id="",
                detail=str(exc),
            )
            raise HTTPException(status_code=400, detail=str(exc))
        await _emit_approval_activity(
            action="sage_approval_resolved",
            status="approved",
            approval_token=record.approval_token,
            tenant_id=tenant_id,
            workspace_id=resolved_workspace_id,
            actor_user_id=actor_user_id,
            trace_id=record.trace_id,
            summary=f"Approved action: {record.action}",
            payload={"resolved_action": record.action},
        )

        try:
            execution = _execute_approved_sage_action(
                workspace_id=resolved_workspace_id,
                approval_record=record,
            )
            _emit_approval_audit(
                action="approval.executed",
                status="success",
                approval_token=record.approval_token,
                tenant_id=tenant_id,
                workspace_id=resolved_workspace_id,
                actor_user_id=actor_user_id,
                trace_id=record.trace_id,
                detail=f"Executed approved action: {record.action}",
                metadata={"action": record.action},
            )
            await _emit_approval_activity(
                action="sage_approval_executed",
                status="completed",
                approval_token=record.approval_token,
                tenant_id=tenant_id,
                workspace_id=resolved_workspace_id,
                actor_user_id=actor_user_id,
                trace_id=record.trace_id,
                summary=f"Executed approved action: {record.action}",
                payload={"executed_action": record.action},
            )
        except Exception as exc:
            _emit_approval_audit(
                action="approval.execute_failed",
                status="failed",
                approval_token=record.approval_token,
                tenant_id=tenant_id,
                workspace_id=resolved_workspace_id,
                actor_user_id=actor_user_id,
                trace_id=record.trace_id,
                detail=str(exc),
                metadata={"action": record.action},
            )
            await _emit_approval_activity(
                action="sage_approval_execution_failed",
                status="failed",
                approval_token=record.approval_token,
                tenant_id=tenant_id,
                workspace_id=resolved_workspace_id,
                actor_user_id=actor_user_id,
                trace_id=record.trace_id,
                review_required=True,
                summary=f"Failed approved action: {record.action}",
                payload={"failed_action": record.action},
            )
            raise HTTPException(status_code=400, detail=str(exc))

        _emit_approval_audit(
            action="approval.approved",
            status="success",
            approval_token=record.approval_token,
            tenant_id=tenant_id,
            workspace_id=resolved_workspace_id,
            actor_user_id=actor_user_id,
            trace_id=record.trace_id,
            detail=f"Approved action: {record.action}",
            metadata={"action": record.action, "action_payload_snapshot": record.action_payload_snapshot},
        )

        return {
            "workspace_id": resolved_workspace_id,
            "tenant_id": tenant_id,
            "approval_token": record.approval_token,
            "status": record.status,
            "action": record.action,
            "trace_id": record.trace_id,
            "execution": execution,
        }

    @app.post("/api/sage/approvals/reject", dependencies=[Depends(member_dependency)])
    async def sage_reject(
        body: SageApprovalResolveRequest,
        current_user=Depends(member_dependency),
    ):
        if not body.workspace_id or not _coerce_text(body.workspace_id):
            raise HTTPException(status_code=400, detail="workspace_id is required.")
        if not body.approval_token or not _coerce_text(body.approval_token):
            raise HTTPException(status_code=400, detail="approval_token is required.")

        resolved_workspace_id = enforce_workspace_access(
            current_user,
            body.workspace_id,
            minimum_role="member",
        )
        tenant_id = _resolve_tenant_id(current_user, resolved_workspace_id)
        actor_user_id = _coerce_text((current_user or {}).get("user_id"))

        try:
            record = resolve_approval(
                approval_token=_coerce_text(body.approval_token),
                workspace_id=resolved_workspace_id,
                status="rejected",
                resolution_actor=actor_user_id,
                resolution_reason="Owner rejected the action.",
            )
        except ValueError as exc:
            _emit_approval_audit(
                action="approval.reject_failed",
                status="failed",
                approval_token=_coerce_text(body.approval_token),
                tenant_id=tenant_id,
                workspace_id=resolved_workspace_id,
                actor_user_id=actor_user_id,
                trace_id="",
                detail=str(exc),
            )
            raise HTTPException(status_code=400, detail=str(exc))
        await _emit_approval_activity(
            action="sage_approval_resolved",
            status="rejected",
            approval_token=record.approval_token,
            tenant_id=tenant_id,
            workspace_id=resolved_workspace_id,
            actor_user_id=actor_user_id,
            trace_id=record.trace_id,
            review_required=True,
            summary=f"Rejected action: {record.action}",
            payload={"resolved_action": record.action},
        )

        _emit_approval_audit(
            action="approval.rejected",
            status="success",
            approval_token=record.approval_token,
            tenant_id=tenant_id,
            workspace_id=resolved_workspace_id,
            actor_user_id=actor_user_id,
            trace_id=record.trace_id,
            detail=f"Rejected action: {record.action}",
            metadata={"action": record.action},
        )

        return {
            "workspace_id": resolved_workspace_id,
            "tenant_id": tenant_id,
            "approval_token": record.approval_token,
            "status": record.status,
            "action": record.action,
            "trace_id": record.trace_id,
        }
