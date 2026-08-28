from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import logging
import os
from pathlib import Path
import threading
import time
from typing import Any, Callable, Dict, Mapping, Optional, Sequence
import uuid

from server_modules import rust_runtime_kernel_client
from server_modules.runtime_state_store import replace_local_runtime_state


LOGGER = logging.getLogger(__name__)
OUTBOX_DELIVERY_RETRY_BACKOFF_SECONDS: tuple[int, ...] = (5, 15, 30, 60, 120)
OUTBOX_MAX_DELIVERY_ATTEMPTS = len(OUTBOX_DELIVERY_RETRY_BACKOFF_SECONDS)

_OUTBOX_DELIVERY_EXPECTED_NEXT_ACTIONS: dict[str, str] = {
    "persist_event": "persist_outbox_event",
    "list_undelivered": "list_undelivered_outbox_events",
    "claim_due": "claim_due_outbox_events",
    "patch_payload": "patch_outbox_event_payload",
    "mark_delivered": "mark_outbox_event_delivered",
    "record_failure": "record_outbox_delivery_failure",
    "list_poisoned": "list_poisoned_outbox_events",
    "delivery_status": "get_outbox_delivery_status",
}

_RUN_RECORD_OUTBOX_EXPECTED_NEXT_ACTIONS: dict[str, set[str]] = {
    "emit_transition_outbox": {"emit_run_transition_event", "noop"},
    "emit_artifact_outbox": {"emit_artifact_created_events", "noop"},
}


@dataclass(slots=True)
class OutboxEvent:
    event_id: str
    event_type: str
    tenant_id: str
    workspace_id: str
    run_id: Optional[str] = None
    machine_id: Optional[str] = None
    trace_id: str = ""
    idempotency_key: str = ""
    payload: Dict[str, Any] = field(default_factory=dict)
    created_at: Optional[str] = None
    retry_count: int = 0
    last_delivery_error: Optional[str] = None
    last_attempted_at: Optional[str] = None
    next_attempt_at: Optional[str] = None
    poisoned_at: Optional[str] = None
    claim_token: Optional[str] = None
    claimed_by: Optional[str] = None
    claimed_at: Optional[str] = None
    claim_expires_at: Optional[str] = None


@dataclass(slots=True)
class LocalRuntimeStateSnapshot:
    pending_run_ids: list[str] = field(default_factory=list)
    claimed_runs: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    runtime_registrations: Dict[str, Dict[str, Any]] = field(default_factory=dict)


class OutboxRetryLater(RuntimeError):
    def __init__(self, message: str, *, retry_delay_seconds: int = 5) -> None:
        super().__init__(str(message or "retry later"))
        self.retry_delay_seconds = max(1, int(retry_delay_seconds or 1))


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _default_outbox_claimed_by() -> str:
    return f"outbox-delivery:{os.getpid()}:{threading.get_ident()}"


def _normalized_scope_token(value: Any, *, default: str = "default") -> str:
    token = str(value or "").strip()
    return token or default


def _build_event_id(event_type: str, idempotency_key: str) -> str:
    normalized_key = str(idempotency_key or "").strip()
    if normalized_key:
        return f"{event_type}:{normalized_key}"
    return f"{event_type}:{uuid.uuid4().hex}"


def _enforce_outbox_delivery_decision(operation: str, **request: Any) -> Dict[str, Any]:
    payload = {
        "operation": operation,
        "event_id": str(request.get("event_id") or "").strip(),
        "event_type": str(request.get("event_type") or "").strip(),
        "tenant_id": str(request.get("tenant_id") or "").strip(),
        "workspace_id": str(request.get("workspace_id") or "").strip(),
        "run_id": str(request.get("run_id") or "").strip(),
        "machine_id": str(request.get("machine_id") or "").strip(),
        "claim_token": str(request.get("claim_token") or "").strip(),
        "claimed_by": str(request.get("claimed_by") or "").strip(),
        "limit": int(request.get("limit") or 200),
        "claim_ttl_seconds": int(request.get("claim_ttl_seconds") or 30),
        "retry_count": int(request.get("retry_count") or 0),
        "max_retries": int(request.get("max_retries") or OUTBOX_MAX_DELIVERY_ATTEMPTS),
        "retry_delay_seconds": request.get("retry_delay_seconds"),
        "poison": bool(request.get("poison")),
        "error_text": str(request.get("error_text") or "").strip(),
        "payload_patch_present": bool(request.get("payload_patch_present")),
        "repository_read_only": bool(request.get("repository_read_only")),
    }
    decision = rust_runtime_kernel_client.run_runtime_kernel_enforced(
        "outbox-delivery-decision",
        payload,
    )
    expected_next_action = _OUTBOX_DELIVERY_EXPECTED_NEXT_ACTIONS.get(str(operation or "").strip())
    next_action = str(decision.get("next_action") or "").strip()
    if expected_next_action and next_action != expected_next_action:
        raise RuntimeError(f"unexpected next_action: {next_action or 'missing'}")
    return decision


def _enforce_run_record_outbox_decision(operation: str, **request: Any) -> Dict[str, Any]:
    payload = {
        "operation": operation,
        "run_id": str(request.get("run_id") or "").strip(),
        "tenant_id": str(request.get("tenant_id") or "").strip() or "default",
        "workspace_id": str(request.get("workspace_id") or "").strip() or "default",
        "from_state": str(request.get("from_state") or "").strip(),
        "to_state": str(request.get("to_state") or "").strip(),
        "artifact_count": int(request.get("artifact_count") or 0),
        "version": int(request.get("version") or 0),
    }
    try:
        decision = rust_runtime_kernel_client.run_runtime_kernel_enforced(
            "run-record-decision",
            payload,
        )
    except rust_runtime_kernel_client.RustKernelDecisionError as exc:
        reason = str(getattr(exc, "reason", "") or "run_record_denied").strip()
        raise RuntimeError(f"Rust run-record kernel blocked {operation}: {reason}") from exc
    expected_actions = _RUN_RECORD_OUTBOX_EXPECTED_NEXT_ACTIONS.get(str(operation or "").strip())
    next_action = str(decision.get("next_action") or "").strip()
    if expected_actions is None:
        raise RuntimeError(f"unexpected run_record_outbox operation: {operation}")
    if next_action not in expected_actions:
        expected_list = ", ".join(sorted(expected_actions))
        raise RuntimeError(
            f"unexpected next_action for run-record-decision: {next_action or 'missing'} "
            f"(expected {expected_list})"
        )
    return decision


def _persist_outbox_event(
    event: OutboxEvent,
    *,
    persist_outbox_event_fn: Optional[Callable[..., Any]] = None,
) -> None:
    _enforce_outbox_delivery_decision(
        "persist_event",
        event_id=event.event_id,
        event_type=event.event_type,
        tenant_id=event.tenant_id,
        workspace_id=event.workspace_id,
        run_id=event.run_id,
        machine_id=event.machine_id,
    )
    if persist_outbox_event_fn is None:
        try:
            from server_modules import run_state_repository

            persist_outbox_event_fn = run_state_repository.sync_persist_outbox_event
        except Exception:
            persist_outbox_event_fn = None
    if not callable(persist_outbox_event_fn):
        return
    try:
        persist_outbox_event_fn(
            event_id=event.event_id,
            event_type=event.event_type,
            tenant_id=event.tenant_id,
            workspace_id=event.workspace_id,
            run_id=event.run_id,
            machine_id=event.machine_id,
            trace_id=event.trace_id,
            idempotency_key=event.idempotency_key,
            payload=dict(event.payload),
        )
    except Exception as exc:
        from server_modules import durability_signal  # noqa: PLC0415

        durability_signal.capture_durability_failure(
            f"outbox event persistence {event.event_id}",
            exc,
            workspace_id=str(getattr(event, "workspace_id", "") or "").strip() or None,
            tenant_id=str(getattr(event, "tenant_id", "") or "").strip() or None,
            run_id=str(getattr(event, "run_id", "") or "").strip() or None,
            event_class="outbox_dead_letter",
        )


def emit_runtime_event(
    *,
    event_type: str,
    tenant_id: str,
    workspace_id: str,
    run_id: Optional[str] = None,
    machine_id: Optional[str] = None,
    trace_id: str = "",
    idempotency_key: str = "",
    payload: Optional[Dict[str, Any]] = None,
    event_id: Optional[str] = None,
    persist_outbox_event_fn: Optional[Callable[..., Any]] = None,
) -> OutboxEvent:
    event = OutboxEvent(
        event_id=str(event_id or "").strip() or _build_event_id(event_type, idempotency_key),
        event_type=str(event_type or "").strip() or "runtime_event",
        tenant_id=_normalized_scope_token(tenant_id),
        workspace_id=_normalized_scope_token(workspace_id),
        run_id=str(run_id or "").strip() or None,
        machine_id=str(machine_id or "").strip() or None,
        trace_id=str(trace_id or "").strip(),
        idempotency_key=str(idempotency_key or "").strip(),
        payload=dict(payload or {}),
        created_at=_utc_now_iso(),
    )
    _persist_outbox_event(event, persist_outbox_event_fn=persist_outbox_event_fn)
    return event


def emit_run_transition_event(
    *,
    run_id: str,
    tenant_id: str,
    workspace_id: str,
    from_state: str,
    to_state: str,
    actor: str,
    trace_id: str = "",
    agent_install_id: str = "",
    failure_reason: str = "",
    persist_outbox_event_fn: Optional[Callable[..., Any]] = None,
) -> OutboxEvent:
    run_token = str(run_id or "").strip()
    from_token = str(from_state or "").strip() or "unknown"
    to_token = str(to_state or "").strip() or "unknown"
    decision = _enforce_run_record_outbox_decision(
        "emit_transition_outbox",
        run_id=run_token,
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        from_state=from_token,
        to_state=to_token,
    )
    if str(decision.get("next_action") or "").strip() == "noop":
        return None
    return emit_runtime_event(
        event_type="run_transition",
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        run_id=run_token,
        trace_id=trace_id,
        idempotency_key=f"run_transition:{run_token}:{from_token}:{to_token}",
        payload={
            "run_id": run_token,
            "from_state": from_token,
            "to_state": to_token,
            "actor": str(actor or "").strip() or "runtime",
            "emitted_at": _utc_now_iso(),
            # Identity and cause travel WITH the transition.
            #
            # This payload used to be exactly the five keys above, so every
            # downstream row said "Run <uuid> failed." and nothing else: the
            # ledger's install_id was NULL (activity_ledger_service reads it
            # from metadata["agent_install_id"], which nothing ever set), and
            # the reason — which existed on the run record at this very call
            # site — was dropped. A person saw "Run failed", could not tell
            # which agent, and had no route to why.
            #
            # metadata is the key notification_service already merges through
            # to the ledger writer, so nothing between here and the row needs
            # to learn a new field name.
            "metadata": _run_transition_metadata(
                agent_install_id=agent_install_id,
                failure_reason=failure_reason,
            ),
        },
        persist_outbox_event_fn=persist_outbox_event_fn,
    )


def _run_transition_metadata(
    *,
    agent_install_id: str,
    failure_reason: str,
) -> Dict[str, Any]:
    """Only facts that are actually known. An absent key is not an empty one.

    A "" install id written into the row is indistinguishable from an agent
    that could not be resolved, and a consumer would render an empty name
    rather than fall back. Omit instead.
    """
    metadata: Dict[str, Any] = {}
    install_token = str(agent_install_id or "").strip()
    if install_token:
        metadata["agent_install_id"] = install_token
    reason_token = str(failure_reason or "").strip()
    if reason_token:
        metadata["failure_reason"] = reason_token[:1000]
    return metadata


def emit_artifact_created_event(
    *,
    run_id: str,
    tenant_id: str,
    workspace_id: str,
    artifact: Mapping[str, Any],
    trace_id: str = "",
    index: int = 0,
    persist_outbox_event_fn: Optional[Callable[..., Any]] = None,
) -> OutboxEvent:
    artifact_payload = dict(artifact) if isinstance(artifact, Mapping) else {}
    artifact_path = str(
        artifact_payload.get("uri")
        or artifact_payload.get("uri_or_path")
        or artifact_payload.get("path")
        or artifact_payload.get("file_path")
        or artifact_payload.get("url")
        or f"index:{max(0, int(index))}"
    ).strip()
    artifact_kind = str(artifact_payload.get("kind") or artifact_payload.get("type") or "artifact").strip()
    decision = _enforce_run_record_outbox_decision(
        "emit_artifact_outbox",
        run_id=str(run_id or "").strip(),
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        artifact_count=1,
    )
    if str(decision.get("next_action") or "").strip() == "noop":
        return None
    return emit_runtime_event(
        event_type="artifact_created",
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        run_id=run_id,
        trace_id=trace_id,
        idempotency_key=f"artifact_created:{run_id}:{artifact_path}:{index}",
        payload={
            "run_id": str(run_id or "").strip(),
            "artifact_index": max(0, int(index)),
            "artifact_kind": artifact_kind,
            "artifact_path": artifact_path,
            "artifact_uri": str(artifact_payload.get("uri") or artifact_payload.get("uri_or_path") or "").strip() or None,
            "artifact_id": str(artifact_payload.get("artifact_id") or "").strip() or None,
            "artifact": artifact_payload,
            "emitted_at": _utc_now_iso(),
        },
        persist_outbox_event_fn=persist_outbox_event_fn,
    )


def emit_machine_event(
    *,
    machine_id: str,
    tenant_id: str,
    workspace_id: str,
    action: str,
    machine_payload: Optional[Dict[str, Any]] = None,
    trace_id: str = "",
    error: Optional[str] = None,
    persist_outbox_event_fn: Optional[Callable[..., Any]] = None,
) -> OutboxEvent:
    machine_token = str(machine_id or "").strip()
    action_token = str(action or "").strip() or "updated"
    payload = dict(machine_payload or {})
    if error:
        payload["error"] = str(error)
    payload.setdefault("machine_id", machine_token)
    payload.setdefault("action", action_token)
    payload.setdefault("emitted_at", _utc_now_iso())
    return emit_runtime_event(
        event_type="machine_event",
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        machine_id=machine_token,
        trace_id=trace_id,
        idempotency_key=f"machine_event:{machine_token}:{action_token}:{payload.get('enrollment_state') or payload.get('status') or ''}",
        payload=payload,
        persist_outbox_event_fn=persist_outbox_event_fn,
    )


def emit_notification_event(
    *,
    tenant_id: str,
    workspace_id: str,
    action: str,
    text: str,
    run_id: Optional[str] = None,
    machine_id: Optional[str] = None,
    trace_id: str = "",
    metadata: Optional[Dict[str, Any]] = None,
    persist_outbox_event_fn: Optional[Callable[..., Any]] = None,
) -> OutboxEvent:
    action_token = str(action or "").strip() or "notification"
    return emit_runtime_event(
        event_type="notification",
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        run_id=run_id,
        machine_id=machine_id,
        trace_id=trace_id,
        idempotency_key=f"notification:{action_token}:{run_id or machine_id or workspace_id}:{str(text or '').strip()}",
        payload={
            "action": action_token,
            "text": str(text or "").strip(),
            "metadata": dict(metadata or {}),
            "emitted_at": _utc_now_iso(),
        },
        persist_outbox_event_fn=persist_outbox_event_fn,
    )


def emit_channel_run_delivery_event(
    *,
    channel: str,
    tenant_id: str,
    workspace_id: str,
    run_id: str,
    connector_id: str,
    payload: Optional[Dict[str, Any]] = None,
    trace_id: str = "",
    idempotency_key: str = "",
    persist_outbox_event_fn: Optional[Callable[..., Any]] = None,
) -> OutboxEvent:
    channel_token = str(channel or "").strip().lower()
    connector_token = str(connector_id or "").strip()
    run_token = str(run_id or "").strip()
    if not channel_token or not connector_token or not run_token:
        raise ValueError("Channel delivery outbox events require channel, connector_id, and run_id.")
    resolved_payload = dict(payload or {})
    resolved_payload.setdefault("channel", channel_token)
    resolved_payload.setdefault("connector_id", connector_token)
    resolved_payload.setdefault("run_id", run_token)
    resolved_payload.setdefault("emitted_at", _utc_now_iso())
    delivery = dict(resolved_payload.get("delivery") or {}) if isinstance(resolved_payload.get("delivery"), Mapping) else {}
    delivery.setdefault("provider", channel_token)
    delivery.setdefault("status", "pending")
    delivery.setdefault(
        "provider_idempotency_key",
        str(idempotency_key or "").strip() or f"{channel_token}:{connector_token}:{run_token}",
    )
    resolved_payload["delivery"] = delivery
    return emit_runtime_event(
        event_type="channel_run_delivery",
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        run_id=run_token,
        trace_id=trace_id,
        idempotency_key=idempotency_key or f"channel_run_delivery:{channel_token}:{connector_token}:{run_token}",
        payload=resolved_payload,
        persist_outbox_event_fn=persist_outbox_event_fn,
    )


def _outbox_event_from_item(item: Mapping[str, Any]) -> Optional[OutboxEvent]:
    if not isinstance(item, Mapping):
        return None
    event_id = str(item.get("event_id") or "").strip()
    if not event_id:
        return None
    payload = dict(item.get("payload") or {}) if isinstance(item.get("payload"), Mapping) else {}
    return OutboxEvent(
        event_id=event_id,
        event_type=str(item.get("event_type") or "").strip() or "runtime_event",
        tenant_id=_normalized_scope_token(item.get("tenant_id")),
        workspace_id=_normalized_scope_token(item.get("workspace_id")),
        run_id=str(item.get("run_id") or "").strip() or None,
        machine_id=str(item.get("machine_id") or "").strip() or None,
        trace_id=str(item.get("trace_id") or "").strip(),
        idempotency_key=str(item.get("idempotency_key") or "").strip(),
        payload=payload,
        created_at=str(item.get("created_at") or "").strip() or None,
        retry_count=int(item.get("retry_count") or 0),
        last_delivery_error=str(item.get("last_delivery_error") or "").strip() or None,
        last_attempted_at=str(item.get("last_attempted_at") or "").strip() or None,
        next_attempt_at=str(item.get("next_attempt_at") or "").strip() or None,
        poisoned_at=str(item.get("poisoned_at") or "").strip() or None,
        claim_token=str(item.get("claim_token") or "").strip() or None,
        claimed_by=str(item.get("claimed_by") or "").strip() or None,
        claimed_at=str(item.get("claimed_at") or "").strip() or None,
        claim_expires_at=str(item.get("claim_expires_at") or "").strip() or None,
    )


def _compat_claim_items(
    items: Sequence[Mapping[str, Any]],
    *,
    claimed_by: str,
) -> list[Dict[str, Any]]:
    claimed_items: list[Dict[str, Any]] = []
    claimed_at = _utc_now_iso()
    for item in items or []:
        payload = dict(item or {}) if isinstance(item, Mapping) else {}
        payload.setdefault("claim_token", f"compat-claim:{payload.get('event_id')}")
        payload.setdefault("claimed_by", claimed_by)
        payload.setdefault("claimed_at", claimed_at)
        payload.setdefault("claim_expires_at", claimed_at)
        claimed_items.append(payload)
    return claimed_items


def _mark_outbox_event_delivered_with_claim(
    mark_outbox_event_delivered_fn: Callable[..., Any],
    event_id: str,
    claim_token: str,
) -> Any:
    try:
        return mark_outbox_event_delivered_fn(event_id, claim_token=claim_token)
    except TypeError:
        return mark_outbox_event_delivered_fn(event_id)


def _record_outbox_delivery_failure_with_claim(
    record_outbox_delivery_failure_fn: Callable[..., Any],
    event_id: str,
    *,
    claim_token: str,
    error_text: str,
    retry_delay_seconds: Optional[int],
    poison: bool,
    increment_retry: bool,
) -> Any:
    try:
        return record_outbox_delivery_failure_fn(
            event_id,
            claim_token=claim_token,
            error_text=error_text,
            retry_delay_seconds=retry_delay_seconds,
            poison=poison,
            increment_retry=increment_retry,
        )
    except TypeError:
        return record_outbox_delivery_failure_fn(
            event_id,
            error_text=error_text,
            retry_delay_seconds=retry_delay_seconds,
            poison=poison,
            increment_retry=increment_retry,
        )


def _delivery_text_for_event(event: OutboxEvent) -> str:
    payload = event.payload if isinstance(event.payload, dict) else {}
    if event.event_type == "channel_run_delivery":
        channel = str(payload.get("channel") or "").strip() or "channel"
        return f"Pending {channel} delivery for run {event.run_id or 'unknown'}."
    if event.event_type == "approval_requested":
        prompt = str(payload.get("prompt") or "").strip()
        return prompt or f"Approval requested for run {event.run_id or 'unknown'}."
    if event.event_type == "approval_resolved":
        resolution = str(payload.get("resolution") or "approved").strip()
        return f"Approval {resolution} for run {event.run_id or 'unknown'}."
    if event.event_type == "run_transition":
        to_state = str(payload.get("to_state") or "").strip() or "updated"
        return f"Run {event.run_id or 'unknown'} moved to {to_state}."
    if event.event_type == "artifact_created":
        artifact_path = str(payload.get("artifact_path") or "").strip()
        return artifact_path or f"Artifact created for run {event.run_id or 'unknown'}."
    if event.event_type == "machine_event":
        action = str(payload.get("action") or "updated").strip()
        machine_id = str(payload.get("machine_id") or event.machine_id or "").strip() or "unknown"
        return f"Machine {machine_id} {action.replace('_', ' ')}."
    if event.event_type == "notification":
        return str(payload.get("text") or "").strip() or "Notification"
    return str(payload.get("text") or payload.get("summary") or event.event_type).strip() or event.event_type


def deliver_outbox_event(
    event: OutboxEvent,
    *,
    append_channel_event_item_fn: Optional[Callable[..., Any]] = None,
) -> bool:
    if event.event_type == "channel_run_delivery":
        try:
            from server_modules.connectors.channel_delivery_outbox_service import (
                deliver_channel_run_delivery_outbox_event,
            )
        except Exception as exc:
            raise RuntimeError(f"channel delivery outbox service unavailable: {exc}") from exc
        return bool(deliver_channel_run_delivery_outbox_event(event))
    if append_channel_event_item_fn is None:
        try:
            from server_modules import runtime_events

            append_channel_event_item_fn = runtime_events.append_channel_event_item
        except Exception:
            append_channel_event_item_fn = None
    if not callable(append_channel_event_item_fn):
        return False
    payload = dict(event.payload or {})
    session_key = (
        f"run:{event.run_id}"
        if event.run_id
        else f"machine:{event.machine_id}"
        if event.machine_id
        else f"workspace:{event.workspace_id}"
    )
    action = str(payload.get("action") or event.event_type).strip().lower()
    text = _delivery_text_for_event(event)
    metadata = {
        "outbox_event_id": event.event_id,
        "outbox_event_type": event.event_type,
        "outbox_idempotency_key": event.idempotency_key,
        "tenant_id": event.tenant_id,
        "workspace_id": event.workspace_id,
        **payload,
    }
    append_channel_event_item_fn(
        channel="runtime",
        direction="system",
        event_type=event.event_type,
        text=text,
        tenant_id=event.tenant_id,
        workspace_id=event.workspace_id,
        session_key=session_key,
        session_id=session_key,
        message_id=event.event_id,
        run_id=event.run_id,
        trace_id=event.trace_id,
        action=action,
        metadata=metadata,
        event_id=event.event_id,
        occurred_at=event.created_at or payload.get("emitted_at"),
    )
    try:
        from server_modules import notification_service

        notification_service.deliver_notification_from_outbox_event(event)
    except Exception:
        raise
    return True


def _delivery_retry_delay_for_attempt(attempt_number: int) -> Optional[int]:
    if attempt_number >= OUTBOX_MAX_DELIVERY_ATTEMPTS:
        return None
    return OUTBOX_DELIVERY_RETRY_BACKOFF_SECONDS[max(0, attempt_number - 1)]


def deliver_due_outbox_events_once(
    *,
    older_than_seconds: int = 0,
    limit: int = 200,
    tenant_id: Optional[str] = None,
    workspace_id: Optional[str] = None,
    run_id: Optional[str] = None,
    event_type: Optional[str] = None,
    claim_due_outbox_events_fn: Optional[Callable[..., Sequence[Mapping[str, Any]]]] = None,
    list_undelivered_outbox_events_fn: Optional[Callable[..., Sequence[Mapping[str, Any]]]] = None,
    mark_outbox_event_delivered_fn: Optional[Callable[[str], Any]] = None,
    record_outbox_delivery_failure_fn: Optional[Callable[..., Any]] = None,
    deliver_event_fn: Optional[Callable[[OutboxEvent], Any]] = None,
    get_outbox_delivery_status_fn: Optional[Callable[[], Dict[str, Any]]] = None,
    claimed_by: Optional[str] = None,
    claim_ttl_seconds: int = 30,
) -> Dict[str, Any]:
    if claim_due_outbox_events_fn is None and callable(list_undelivered_outbox_events_fn):
        def _compat_claim_due_outbox_events(**kwargs: Any) -> Sequence[Mapping[str, Any]]:
            list_kwargs: dict[str, Any] = {
                "older_than_seconds": kwargs.get("older_than_seconds", 0),
                "limit": kwargs.get("limit", 200),
            }
            for key in ("tenant_id", "workspace_id", "run_id", "event_type"):
                value = str(kwargs.get(key) or "").strip()
                if value:
                    list_kwargs[key] = value
            return _compat_claim_items(
                list_undelivered_outbox_events_fn(**list_kwargs),
                claimed_by=str(kwargs.get("claimed_by") or claimed_by or _default_outbox_claimed_by()),
            )

        claim_due_outbox_events_fn = _compat_claim_due_outbox_events
    if (
        claim_due_outbox_events_fn is None
        or mark_outbox_event_delivered_fn is None
        or record_outbox_delivery_failure_fn is None
        or get_outbox_delivery_status_fn is None
    ):
        try:
            from server_modules import run_state_repository

            claim_due_outbox_events_fn = (
                claim_due_outbox_events_fn
                or run_state_repository.sync_claim_due_outbox_events
            )
            mark_outbox_event_delivered_fn = (
                mark_outbox_event_delivered_fn
                or run_state_repository.sync_mark_outbox_event_delivered
            )
            record_outbox_delivery_failure_fn = (
                record_outbox_delivery_failure_fn
                or run_state_repository.sync_record_outbox_delivery_failure
            )
            get_outbox_delivery_status_fn = (
                get_outbox_delivery_status_fn
                or run_state_repository.sync_get_outbox_delivery_status
            )
        except Exception:
            return {
                "attempted": 0,
                "delivered_ids": [],
                "failed_ids": [],
                "poisoned_ids": [],
                "status": {
                    "undelivered_count": 0,
                    "poisoned_count": 0,
                    "claimed_count": 0,
                    "repeated_failure_count": 0,
                    "stuck_count": 0,
                    "total_retry_count": 0,
                    "max_retry_count": 0,
                    "last_delivery_error": None,
                },
            }
    if deliver_event_fn is None:
        deliver_event_fn = deliver_outbox_event
    claimed_by_token = str(claimed_by or "").strip() or _default_outbox_claimed_by()
    if not callable(claim_due_outbox_events_fn):
        return {
            "attempted": 0,
            "delivered_ids": [],
            "failed_ids": [],
            "poisoned_ids": [],
            "status": {},
        }

    delivered_ids: list[str] = []
    failed_ids: list[str] = []
    deferred_ids: list[str] = []
    poisoned_ids: list[str] = []
    claim_kwargs: dict[str, Any] = {
        "older_than_seconds": max(0, int(older_than_seconds or 0)),
        "limit": max(1, int(limit or 0)),
        "claimed_by": claimed_by_token,
        "claim_ttl_seconds": max(1, int(claim_ttl_seconds or 0)),
    }
    if str(tenant_id or "").strip():
        claim_kwargs["tenant_id"] = str(tenant_id or "").strip()
    if str(workspace_id or "").strip():
        claim_kwargs["workspace_id"] = str(workspace_id or "").strip()
    if str(run_id or "").strip():
        claim_kwargs["run_id"] = str(run_id or "").strip()
    if str(event_type or "").strip():
        claim_kwargs["event_type"] = str(event_type or "").strip()
    _enforce_outbox_delivery_decision(
        "claim_due",
        tenant_id=claim_kwargs.get("tenant_id"),
        workspace_id=claim_kwargs.get("workspace_id"),
        event_type=claim_kwargs.get("event_type"),
        run_id=claim_kwargs.get("run_id"),
        claimed_by=claim_kwargs.get("claimed_by"),
        claim_ttl_seconds=claim_kwargs.get("claim_ttl_seconds"),
        limit=claim_kwargs.get("limit"),
    )
    items = claim_due_outbox_events_fn(**claim_kwargs)
    for item in items or []:
        event = _outbox_event_from_item(item)
        if event is None:
            continue
        claim_token = str(event.claim_token or "").strip()
        if not claim_token:
            LOGGER.warning("Skipping outbox event %s without claim token.", event.event_id)
            continue
        try:
            delivered = bool(deliver_event_fn(event))
            if not delivered:
                raise RuntimeError("delivery sink returned false")
        except OutboxRetryLater as exc:
            retry_delay_seconds = max(1, int(exc.retry_delay_seconds or 1))
            error_message = str(exc or "retry later").strip() or "retry later"
            try:
                _enforce_outbox_delivery_decision(
                    "record_failure",
                    event_id=event.event_id,
                    event_type=event.event_type,
                    tenant_id=event.tenant_id,
                    workspace_id=event.workspace_id,
                    run_id=event.run_id,
                    machine_id=event.machine_id,
                    claim_token=claim_token,
                    retry_count=event.retry_count,
                    retry_delay_seconds=retry_delay_seconds,
                    poison=False,
                    error_text=error_message,
                )
                _record_outbox_delivery_failure_with_claim(
                    record_outbox_delivery_failure_fn,
                    event.event_id,
                    claim_token=claim_token,
                    error_text=error_message,
                    retry_delay_seconds=retry_delay_seconds,
                    poison=False,
                    increment_retry=False,
                )
            except Exception as failure_exc:
                LOGGER.warning("Failed to defer outbox event %s: %s", event.event_id, failure_exc)
            deferred_ids.append(event.event_id)
            continue
        except Exception as exc:
            next_attempt = event.retry_count + 1
            retry_delay_seconds = _delivery_retry_delay_for_attempt(next_attempt)
            poison = retry_delay_seconds is None
            error_message = str(exc or "delivery failed").strip() or "delivery failed"
            try:
                _enforce_outbox_delivery_decision(
                    "record_failure",
                    event_id=event.event_id,
                    event_type=event.event_type,
                    tenant_id=event.tenant_id,
                    workspace_id=event.workspace_id,
                    run_id=event.run_id,
                    machine_id=event.machine_id,
                    claim_token=claim_token,
                    retry_count=event.retry_count,
                    retry_delay_seconds=retry_delay_seconds,
                    poison=poison,
                    error_text=error_message,
                )
                _record_outbox_delivery_failure_with_claim(
                    record_outbox_delivery_failure_fn,
                    event.event_id,
                    claim_token=claim_token,
                    error_text=error_message,
                    retry_delay_seconds=retry_delay_seconds,
                    poison=poison,
                    increment_retry=True,
                )
            except Exception as failure_exc:
                LOGGER.warning("Failed to record outbox delivery failure for %s: %s", event.event_id, failure_exc)
            failed_ids.append(event.event_id)
            if poison:
                poisoned_ids.append(event.event_id)
            continue
        try:
            _enforce_outbox_delivery_decision(
                "mark_delivered",
                event_id=event.event_id,
                event_type=event.event_type,
                tenant_id=event.tenant_id,
                workspace_id=event.workspace_id,
                run_id=event.run_id,
                machine_id=event.machine_id,
                claim_token=claim_token,
            )
            marked = _mark_outbox_event_delivered_with_claim(
                mark_outbox_event_delivered_fn,
                event.event_id,
                claim_token,
            )
            if bool(marked) or marked is None:
                delivered_ids.append(event.event_id)
            else:
                LOGGER.warning("Failed to fence delivered outbox event %s with active claim.", event.event_id)
        except Exception as exc:
            LOGGER.warning("Failed to mark outbox event %s delivered: %s", event.event_id, exc)
    status = (
        get_outbox_delivery_status_fn()
        if callable(get_outbox_delivery_status_fn)
        else {
            "undelivered_count": 0,
            "poisoned_count": 0,
            "claimed_count": 0,
            "repeated_failure_count": 0,
            "stuck_count": 0,
            "total_retry_count": 0,
            "max_retry_count": 0,
            "last_delivery_error": None,
        }
    )
    return {
        "attempted": len(delivered_ids) + len(failed_ids) + len(deferred_ids),
        "delivered_ids": delivered_ids,
        "failed_ids": failed_ids,
        "deferred_ids": deferred_ids,
        "poisoned_ids": poisoned_ids,
        "status": status,
    }


def replay_undelivered_events_on_startup(
    *,
    older_than_seconds: int = 30,
    limit: int = 200,
    claim_due_outbox_events_fn: Optional[Callable[..., Sequence[Mapping[str, Any]]]] = None,
    list_undelivered_outbox_events_fn: Optional[Callable[..., Sequence[Mapping[str, Any]]]] = None,
    mark_outbox_event_delivered_fn: Optional[Callable[[str], Any]] = None,
    record_outbox_delivery_failure_fn: Optional[Callable[..., Any]] = None,
    deliver_event_fn: Optional[Callable[[OutboxEvent], Any]] = None,
    claimed_by: Optional[str] = None,
    claim_ttl_seconds: int = 30,
) -> list[str]:
    result = deliver_due_outbox_events_once(
        older_than_seconds=max(0, int(older_than_seconds or 0)),
        limit=max(1, int(limit or 0)),
        claim_due_outbox_events_fn=claim_due_outbox_events_fn,
        list_undelivered_outbox_events_fn=list_undelivered_outbox_events_fn,
        mark_outbox_event_delivered_fn=mark_outbox_event_delivered_fn,
        record_outbox_delivery_failure_fn=record_outbox_delivery_failure_fn,
        deliver_event_fn=deliver_event_fn,
        claimed_by=claimed_by,
        claim_ttl_seconds=claim_ttl_seconds,
    )
    return list(result.get("delivered_ids") or [])


def run_outbox_delivery_forever(
    *,
    poll_seconds: float = 2.0,
    limit: int = 200,
    older_than_seconds: int = 0,
    stop_event: Optional[threading.Event] = None,
    deliver_once_fn: Callable[..., Dict[str, Any]] = deliver_due_outbox_events_once,
    claimed_by: Optional[str] = None,
    claim_ttl_seconds: int = 30,
) -> None:
    interval = max(0.25, float(poll_seconds or 0.0))
    while True:
        if stop_event is not None and stop_event.is_set():
            return
        try:
            deliver_once_fn(
                older_than_seconds=max(0, int(older_than_seconds or 0)),
                limit=max(1, int(limit or 0)),
                claimed_by=claimed_by,
                claim_ttl_seconds=claim_ttl_seconds,
            )
        except Exception as exc:
            LOGGER.warning("Outbox delivery loop failed: %s", exc)
        time.sleep(interval)


def get_outbox_delivery_status(
    *,
    get_outbox_delivery_status_fn: Optional[Callable[[], Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    if get_outbox_delivery_status_fn is None:
        try:
            from server_modules import run_state_repository

            get_outbox_delivery_status_fn = run_state_repository.sync_get_outbox_delivery_status
        except Exception:
            get_outbox_delivery_status_fn = None
    if not callable(get_outbox_delivery_status_fn):
        return {
            "undelivered_count": 0,
            "poisoned_count": 0,
            "claimed_count": 0,
            "repeated_failure_count": 0,
            "stuck_count": 0,
            "total_retry_count": 0,
            "max_retry_count": 0,
            "last_delivery_error": None,
        }
    try:
        return dict(get_outbox_delivery_status_fn() or {})
    except Exception:
        return {
            "undelivered_count": 0,
            "poisoned_count": 0,
            "claimed_count": 0,
            "repeated_failure_count": 0,
            "stuck_count": 0,
            "total_retry_count": 0,
            "max_retry_count": 0,
            "last_delivery_error": None,
        }


def build_local_runtime_state_snapshot(
    *,
    pending_run_ids: Sequence[Any],
    claimed_runs: Mapping[str, Any],
    runtime_registrations: Mapping[str, Any],
) -> LocalRuntimeStateSnapshot:
    return LocalRuntimeStateSnapshot(
        pending_run_ids=[str(run_id) for run_id in pending_run_ids],
        claimed_runs={
            str(run_id): dict(info)
            for run_id, info in claimed_runs.items()
            if isinstance(info, dict)
        },
        runtime_registrations={
            str(runtime_id): dict(record)
            for runtime_id, record in runtime_registrations.items()
            if isinstance(record, dict)
        },
    )


def replace_local_runtime_state_snapshot(
    db_path: Path,
    snapshot: LocalRuntimeStateSnapshot,
    *,
    replace_local_runtime_state_fn: Callable[..., Any] = replace_local_runtime_state,
) -> bool:
    try:
        replace_local_runtime_state_fn(
            db_path,
            pending_run_ids=list(snapshot.pending_run_ids),
            claimed_runs=dict(snapshot.claimed_runs),
            runtime_registrations=dict(snapshot.runtime_registrations),
        )
    except Exception:
        return False
    return True


def persist_local_runtime_state(
    *,
    db_path: Path,
    pending_run_ids: Sequence[Any],
    claimed_runs: Mapping[str, Any],
    runtime_registrations: Mapping[str, Any],
    replace_local_runtime_state_fn: Callable[..., Any] = replace_local_runtime_state,
) -> bool:
    snapshot = build_local_runtime_state_snapshot(
        pending_run_ids=pending_run_ids,
        claimed_runs=claimed_runs,
        runtime_registrations=runtime_registrations,
    )
    return replace_local_runtime_state_snapshot(
        db_path,
        snapshot,
        replace_local_runtime_state_fn=replace_local_runtime_state_fn,
    )


def enqueue_local_companion_run(
    run_id: str,
    *,
    runs_by_id: Mapping[str, Any],
    set_run_status_fn: Callable[[str, str], Any],
    utc_now_iso_fn: Callable[[], str],
    local_queue_lock: Any,
    pending_run_ids: list[str],
    claimed_runs: Mapping[str, Any],
    runtime_registrations: Mapping[str, Any],
    db_path: Path,
    lease_seconds: int,
    emit_log_fn: Callable[..., Any],
    message: str = "Run queued for Local Companion execution.",
    event: str = "local_queued",
    replace_local_runtime_state_fn: Callable[..., Any] = replace_local_runtime_state,
) -> bool:
    run = runs_by_id.get(run_id)
    if not isinstance(run, dict):
        return False
    context = run.get("context") if isinstance(run.get("context"), dict) else {}
    metadata = context.get("metadata") if isinstance(context.get("metadata"), dict) else {}
    waiting_reason = str(metadata.get("execution_target_reason") or "").strip()
    if message == "Run queued for Local Companion execution." and waiting_reason:
        if bool(metadata.get("execution_target_waiting_for_runtime")) or bool(
            metadata.get("execution_target_waiting_for_capacity")
        ):
            message = waiting_reason

    set_run_status_fn(run_id, "queued_local")
    run["updated_at"] = utc_now_iso_fn()

    snapshot: Optional[LocalRuntimeStateSnapshot] = None
    with local_queue_lock:
        if run_id not in pending_run_ids:
            pending_run_ids.append(run_id)
        snapshot = build_local_runtime_state_snapshot(
            pending_run_ids=pending_run_ids,
            claimed_runs=claimed_runs,
            runtime_registrations=runtime_registrations,
        )

    if snapshot is not None:
        replace_local_runtime_state_snapshot(
            db_path,
            snapshot,
            replace_local_runtime_state_fn=replace_local_runtime_state_fn,
        )

    emit_log_fn(
        run["logs"],
        "info",
        message,
        event=event,
        data={
            "run_id": run_id,
            "lease_seconds": lease_seconds,
            "waiting_for_runtime": bool(metadata.get("execution_target_waiting_for_runtime")),
            "required_capabilities": list(metadata.get("execution_target_required_capabilities") or []),
            "missing_capabilities": list(metadata.get("execution_target_missing_capabilities") or []),
            "matching_runtime_ids": list(metadata.get("execution_target_matching_runtime_ids") or []),
            "available_runtime_ids": list(metadata.get("execution_target_available_runtime_ids") or []),
            "busy_runtime_ids": list(metadata.get("execution_target_busy_runtime_ids") or []),
            "preferred_runtime_id": metadata.get("execution_target_preferred_runtime_id"),
            "preferred_runtime_label": metadata.get("execution_target_preferred_runtime_label"),
            "waiting_for_capacity": bool(metadata.get("execution_target_waiting_for_capacity")),
        },
    )
    return True
