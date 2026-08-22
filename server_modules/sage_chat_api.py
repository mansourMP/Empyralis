from __future__ import annotations

import mimetypes
import uuid
from pathlib import Path

from fastapi import Depends, File, HTTPException, Query, UploadFile

from server_modules import activity_ledger_service, sage_proof_log_service, security_audit_service
from server_modules import upload_content_policy, workspace_storage_service
from server_modules.auth import enforce_workspace_access, workspace_tenant_id
from server_modules.agent_turn_runtime_contract import (
    SAGE_MODE,
    normalize_sage_mode,
    normalize_sage_surface,
)
from server_modules.agent_turn_runtime_service import handle_sage_chat
from server_modules.channel_adapter import normalize_sage_inbound, filter_outbound_reply
from server_modules.inbound_envelope import (
    InboundEnvelope,
    EnvelopeSender,
    SurfaceKind,
    prepend_envelope_header,
)
from server_modules.voice_notification_policy_service import execute_voice_sage_task
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
            from server_modules.agent_command_dispatcher import dispatch_command as _dispatch_cmd
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
            from server_modules.agent_command_dispatcher import get_active_thread as _gat_web
            _active_thread_web = await _gat_web(turn.workspace_id, "web")

            # ── Canonical inbound envelope (docs/design/inbound-envelope-design.md) ──
            # This endpoint calls handle_sage_chat() directly rather than
            # routing through agent_turn_adapter.execute_sage_turn — the one
            # chokepoint that renders the envelope header — so the header is
            # prepended here by hand, using the SAME rendering function
            # execute_sage_turn calls, to stay byte-for-byte consistent with
            # every other channel. The web console is always the
            # authenticated account talking to its own agent — CONSOLE
            # surface, verified owner.
            _web_actor_id = (
                str((current_user or {}).get("user_id") or "").strip()
                or str((current_user or {}).get("email") or "").strip().lower()
                or "anonymous"
            )
            _web_actor_name = str((current_user or {}).get("email") or "").strip() or _web_actor_id
            _web_envelope = InboundEnvelope(
                platform="console",
                surface=SurfaceKind.CONSOLE,
                sender=EnvelopeSender(id=_web_actor_id, display_name=_web_actor_name, is_owner=True),
            )
            _enveloped_message = prepend_envelope_header(turn.message, _web_envelope)

            result = await handle_sage_chat(
                workspace_id=turn.workspace_id,
                tenant_id=turn.tenant_id,
                message=_enveloped_message,
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
        # Same policy as the live twin in sage_context_files_api.py, which
        # registers this exact path first and is therefore the handler
        # customers actually reach. Two registrations of one route must not
        # accept two different sets of files.
        try:
            upload_content_policy.assert_allowed_upload(
                filename=file.filename,
                data=raw,
                content_type=file.content_type,
                max_bytes=MAX_ATTACHMENT_BYTES,
            )
        except upload_content_policy.UploadRejected as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        file_id = str(uuid.uuid4())
        attach_dir = workspace_attachments_dir(resolved_workspace_id)
        safe_name = f"{file_id}_{Path(file.filename).name}"
        dest = attach_dir / safe_name
        # Same storage cap as the live twin, for the same reason the content
        # policy above is duplicated here: two registrations of one route
        # must never accept two different things. This handler is shadowed
        # (sage_context_files_api registers the path first and FastAPI serves
        # the first match), so this call is what keeps the shadow honest if
        # registration order ever changes.
        try:
            await workspace_storage_service.reserve_storage_for_upload(
                tenant_id=workspace_tenant_id(current_user, resolved_workspace_id),
                workspace_id=resolved_workspace_id,
                project_id=workspace_storage_service.WORKSPACE_LEVEL_BUCKET,
                object_id=file_id,
                surface=workspace_storage_service.SURFACE_CHAT_ATTACHMENT,
                object_key=safe_name,
                byte_size=len(raw),
                filename=file.filename,
                content_type=file.content_type,
                created_by=str((current_user or {}).get("id") or "").strip() or None,
            )
        except upload_content_policy.UploadRejected as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
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

    # Phase 2: Approval endpoints removed — agent acts on its own reasoning.
