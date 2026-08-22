from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

from server_modules import activity_ledger_service, secret_redaction_service, security_audit_service
from server_modules.agent_turn_runtime_contract import SAGE_MODE, SageTurnResult


VOICE_SURFACE = "voice"
VOICE_APPROVAL_BLOCKED_ERROR = "voice_cannot_resolve_approval"
VOICE_APPROVAL_BLOCKED_MESSAGE = (
    "Voice instructions are accepted, but actions cannot be confirmed from voice. "
    "Open the request in Empyralis to review or edit it."
)

_APPROVAL_TOKEN_PATTERN = re.compile(r"\b(?:sap|apr)_[A-Za-z0-9_-]{8,}\b")
_APPROVAL_INTENT_PATTERN = re.compile(
    r"\b(approve|approved|approval|allow|allowed|confirm|confirmed|yes|send it|go ahead|do it|proceed)\b",
    re.IGNORECASE,
)
_APPROVAL_CONTEXT_PATTERN = re.compile(
    r"\b(approval|request|pending|token|action|send|email|message|payment|command|file|delete|post|publish)\b",
    re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class VoiceTaskPolicyEnvelope:
    workspace_id: str
    tenant_id: str
    transcript: str
    normalized_message: str
    source_channel: str = "mobile_voice"
    source_message_id: str = ""
    trace_id: str = ""
    approval_resolution_blocked: bool = False
    blocked_approval_tokens: tuple[str, ...] = field(default_factory=tuple)
    safety_instructions: tuple[str, ...] = field(default_factory=tuple)

    def as_policy_dict(self) -> Dict[str, Any]:
        return {
            "input_modality": "voice",
            "surface": VOICE_SURFACE,
            "source_channel": self.source_channel,
            "source_message_id": self.source_message_id or None,
            "approvals_can_be_requested": True,
            "approvals_can_be_resolved": False,
            "approval_resolution_blocked": self.approval_resolution_blocked,
            "blocked_approval_count": len(self.blocked_approval_tokens),
            "safety_instructions": list(self.safety_instructions),
        }


def _coerce_text(value: Any) -> str:
    return str(value or "").strip()


def _voice_looks_like_approval_resolution(transcript: str) -> bool:
    if _APPROVAL_TOKEN_PATTERN.search(transcript):
        return True
    return bool(_APPROVAL_INTENT_PATTERN.search(transcript) and _APPROVAL_CONTEXT_PATTERN.search(transcript))


def build_voice_task_policy_envelope(
    *,
    workspace_id: str,
    tenant_id: str = "",
    transcript: str,
    source_channel: str = "mobile_voice",
    source_message_id: Optional[str] = None,
    trace_id: Optional[str] = None,
) -> VoiceTaskPolicyEnvelope:
    workspace_token = _coerce_text(workspace_id)
    if not workspace_token:
        raise ValueError("workspace_id is required.")
    normalized_transcript = _coerce_text(transcript)
    if not normalized_transcript:
        raise ValueError("transcript must not be empty.")

    redacted_message = secret_redaction_service.redact_text(normalized_transcript)
    blocked_tokens = tuple(_APPROVAL_TOKEN_PATTERN.findall(normalized_transcript))
    approval_resolution_blocked = _voice_looks_like_approval_resolution(normalized_transcript)
    safety_instructions = (
        "Voice input may create an agent task.",
        "Voice input must not approve, reject, consume, or remember approvals.",
        "Risky actions still require the normal approval surface and Gateway policy path.",
    )
    envelope = VoiceTaskPolicyEnvelope(
        workspace_id=workspace_token,
        tenant_id=_coerce_text(tenant_id) or "default",
        transcript=redacted_message,
        normalized_message=redacted_message,
        source_channel=_coerce_text(source_channel) or "mobile_voice",
        source_message_id=_coerce_text(source_message_id),
        trace_id=_coerce_text(trace_id) or str(uuid.uuid4()),
        approval_resolution_blocked=approval_resolution_blocked,
        blocked_approval_tokens=blocked_tokens,
        safety_instructions=safety_instructions,
    )
    secret_redaction_service.assert_secrets_free(envelope.as_policy_dict(), context="voice_task_policy")
    return envelope


def build_safe_approval_notification_payload(
    *,
    workspace_id: str,
    tenant_id: str = "",
    trace_id: str = "",
    action: str = "",
    description: str = "",
    approval_token: str = "",
) -> Dict[str, Any]:
    """
    Build an approval notification that opens the approval UI but cannot approve inline.

    Notifications are intentionally not approval resolvers. They must not carry an
    approval token because mobile lock screens and notification providers are not
    a trusted approval surface.
    """
    action_token = _coerce_text(action) or "approval_required"
    summary = secret_redaction_service.redact_text(_coerce_text(description) or "Your agent needs your approval.")
    payload = {
        "title": "Approval needed",
        "body": summary,
        "data": {
            "workspaceId": _coerce_text(workspace_id),
            "tenantId": _coerce_text(tenant_id) or "default",
            "traceId": _coerce_text(trace_id) or None,
            "action": action_token,
            "approvalSurface": "open_app",
            "canApproveInline": False,
            "path": "/settings/approvals",
        },
    }
    if approval_token:
        payload["data"]["approvalRef"] = f"approval:{uuid.uuid5(uuid.NAMESPACE_URL, approval_token).hex[:12]}"
    secret_redaction_service.assert_secrets_free(payload, context="voice_safe_approval_notification")
    return payload


async def _emit_voice_policy_event(
    *,
    envelope: VoiceTaskPolicyEnvelope,
    current_user: Optional[dict] = None,
    status: str,
    action: str,
    detail: str,
) -> None:
    current_user = current_user if isinstance(current_user, dict) else {}
    actor_user_id = _coerce_text(current_user.get("user_id")) or "sage_voice"
    metadata = envelope.as_policy_dict()
    metadata["blocked_approval_tokens"] = ["[redacted-token]" for _ in envelope.blocked_approval_tokens]
    security_audit_service.emit_security_audit_event(
        action=action,
        status=status,
        tenant_id=envelope.tenant_id,
        workspace_id=envelope.workspace_id,
        current_user=current_user,
        actor_user_id=actor_user_id,
        channel=envelope.source_channel,
        trace_id=envelope.trace_id,
        detail=detail,
        metadata=metadata,
        idempotency_key=f"{action}:{envelope.trace_id}",
    )
    await activity_ledger_service.append_activity_event(
        tenant_id=envelope.tenant_id,
        workspace_id=envelope.workspace_id,
        actor_type="user",
        actor_id=actor_user_id,
        event_class="blocked_action" if status == "blocked" else "sage_activity",
        detail_level="timeline_detail",
        channel=envelope.source_channel,
        direction="inbound",
        action=action,
        trace_id=envelope.trace_id,
        title="Voice approval blocked" if status == "blocked" else "Voice task accepted",
        summary=detail,
        status=status,
        review_required=status == "blocked",
        metadata=metadata,
    )


async def execute_voice_sage_task(
    *,
    workspace_id: str,
    tenant_id: str = "",
    transcript: str,
    source_channel: str = "mobile_voice",
    source_message_id: Optional[str] = None,
    current_user: Optional[dict] = None,
) -> Dict[str, Any]:
    envelope = build_voice_task_policy_envelope(
        workspace_id=workspace_id,
        tenant_id=tenant_id,
        transcript=transcript,
        source_channel=source_channel,
        source_message_id=source_message_id,
    )
    if envelope.approval_resolution_blocked:
        await _emit_voice_policy_event(
            envelope=envelope,
            current_user=current_user,
            status="blocked",
            action="sage_voice.approval_resolution_blocked",
            detail="Voice attempted to resolve an approval; routed to app approval surface instead.",
        )
        return SageTurnResult(
            message=VOICE_APPROVAL_BLOCKED_MESSAGE,
            error=VOICE_APPROVAL_BLOCKED_ERROR,
            blocked_tools=[
                {
                    "type": "approval_resolution",
                    "surface": VOICE_SURFACE,
                    "reason": VOICE_APPROVAL_BLOCKED_ERROR,
                }
            ],
            approvals_required=[],
            trace_id=envelope.trace_id,
        ).as_dict() | {"voice_policy": envelope.as_policy_dict()}

    from server_modules.sage_turn_adapter import execute_sage_turn

    await _emit_voice_policy_event(
        envelope=envelope,
        current_user=current_user,
        status="accepted",
        action="sage_voice.task_accepted",
        detail="Voice transcript accepted as a normal agent task.",
    )
    result = await execute_sage_turn(
        workspace_id=envelope.workspace_id,
        tenant_id=envelope.tenant_id,
        message=envelope.normalized_message,
        surface=VOICE_SURFACE,
        mode=SAGE_MODE,
        current_user=current_user,
    )
    return result.as_dict() | {"voice_policy": envelope.as_policy_dict()}
