from __future__ import annotations

from typing import TYPE_CHECKING, Any, Dict

if TYPE_CHECKING:
    from server_modules.outbox_service import OutboxEvent


def _require_token(value: Any, *, label: str) -> str:
    token = str(value or "").strip()
    if not token:
        raise RuntimeError(f"Channel delivery outbox event is missing {label}.")
    return token


def _payload(event: "OutboxEvent") -> Dict[str, Any]:
    return dict(event.payload or {}) if isinstance(event.payload, dict) else {}


def _delivery(payload: Dict[str, Any]) -> Dict[str, Any]:
    return dict(payload.get("delivery") or {}) if isinstance(payload.get("delivery"), dict) else {}


def _notification_text(payload: Dict[str, Any]) -> str:
    return str(payload.get("notification_text") or "").strip()


def _connector_shell() -> Any:
    from server_modules.connectors.autopilot_runtime_exports import _autopilot_connector_shell_service

    shell = _autopilot_connector_shell_service()
    shell.runtime_facade_service().init_runtime()
    return shell


def _connector_state_dict(state_service: Any, connector_id: str) -> Dict[str, Any]:
    getter = getattr(state_service, "connector_state", None)
    if not callable(getter):
        return {}
    value = getter(connector_id)
    return dict(value) if isinstance(value, dict) else {}


def _defer_until_terminal(event: "OutboxEvent", *, status: str) -> None:
    from server_modules.outbox_service import OutboxRetryLater

    run_id = str(event.run_id or "").strip() or "unknown"
    raise OutboxRetryLater(
        f"channel delivery pending terminal run state for {run_id} (status={status or 'starting'})",
        retry_delay_seconds=3,
    )


def _patch_delivery_state(event: "OutboxEvent", delivery_state: Dict[str, Any]) -> None:
    from server_modules import run_state_repository

    if not isinstance(delivery_state, dict) or not delivery_state:
        return
    try:
        run_state_repository.sync_patch_outbox_event_payload(
            event.event_id,
            {"delivery": delivery_state},
        )
    except run_state_repository.RunStatePersistenceError:
        return


def _connector_delivery_receipt(connector_state: Dict[str, Any], provider_idempotency_key: str) -> Dict[str, Any] | None:
    if not provider_idempotency_key:
        return None
    if str(connector_state.get("last_delivery_idempotency_key") or "").strip() != provider_idempotency_key:
        return None
    last_status = str(connector_state.get("last_delivery_status") or "").strip().lower()
    if last_status not in {"sent", "edited", "accepted", "delivered", "receipt_recorded"}:
        return None
    provider_message_id = (
        str(connector_state.get("last_outbound_message_id") or "").strip()
        or str(connector_state.get("last_outbound_message_sid") or "").strip()
        or None
    )
    return {
        "provider_message_id": provider_message_id,
        "accepted_at": connector_state.get("last_delivery_receipt_at"),
    }


def _skip_resend_if_receipted(
    event: "OutboxEvent",
    *,
    provider: str,
    payload: Dict[str, Any],
    connector_state: Dict[str, Any],
) -> bool:
    delivery_state = _delivery(payload)
    provider_idempotency_key = str(delivery_state.get("provider_idempotency_key") or "").strip()
    receipt = delivery_state.get("receipt") if isinstance(delivery_state.get("receipt"), dict) else None
    if receipt:
        patched = dict(delivery_state)
        patched["provider"] = provider
        patched["status"] = str(patched.get("status") or "receipt_recorded").strip() or "receipt_recorded"
        patched["replay_safe"] = True
        _patch_delivery_state(event, patched)
        return True
    connector_receipt = _connector_delivery_receipt(connector_state, provider_idempotency_key)
    if not connector_receipt:
        return False
    patched = dict(delivery_state)
    patched["provider"] = provider
    patched["status"] = "receipt_recorded"
    patched["receipt"] = connector_receipt
    patched["replay_safe"] = True
    _patch_delivery_state(event, patched)
    return True


def _deliver_telegram(event: "OutboxEvent") -> bool:
    payload = _payload(event)
    delivery_state = _delivery(payload)
    connector_id = _require_token(payload.get("connector_id"), label="connector_id")
    chat_id = _require_token(payload.get("chat_id") or payload.get("recipient"), label="chat_id")
    run_id = _require_token(event.run_id, label="run_id")
    shell = _connector_shell()
    registry = shell.telegram_service_registry()
    dispatch = registry.telegram_run_dispatch_service()
    state_service = registry.telegram_autopilot_state_service()
    connector_state = _connector_state_dict(state_service, connector_id)
    if _skip_resend_if_receipted(
        event,
        provider="telegram",
        payload=payload,
        connector_state=connector_state,
    ):
        return True
    direct_notification = _notification_text(payload)
    if direct_notification:
        result = {"ready": True, "status": "completed", "summary": direct_notification}
    else:
        result = dispatch.poll_run_terminal_result(run_id, max_reply_chars=registry.max_reply_chars)
        if not bool(result.get("ready")):
            _defer_until_terminal(event, status=str(result.get("status") or "starting"))
    # Stage 6: Try cloud-session-manager path first (proactive delivery via GramJS)
    if bool(result.get("ready")):
        try:
            import asyncio as _asyncio
            from server_modules.personal_channels_service import (
                resolve_cloud_telegram_session_id,
                dispatch_cloud_channel_outbound,
            )
            _cloud_session_id = resolve_cloud_telegram_session_id()
            if _cloud_session_id:
                _cloud_notification_text = str(result.get("summary") or direct_notification or "").strip()
                if _cloud_notification_text:
                    _asyncio.run(dispatch_cloud_channel_outbound(
                        session_id=_cloud_session_id,
                        text=_cloud_notification_text,
                        remote_jid="me",
                    ))
                    # Record a minimal channel event for audit
                    record_channel_event = registry.record_channel_event
                    record_channel_event(
                        channel="telegram",
                        direction="system",
                        event_type="run_announce_cloud",
                        text=_notification_text[:500],
                        workspace_id=event.workspace_id,
                        session_key=f"cloud:{_cloud_session_id}",
                        session_id=f"cloud:{_cloud_session_id}",
                        run_id=run_id,
                        action=str(payload.get("action") or "run").strip().lower() or "run",
                        metadata={
                            "connector_id": connector_id,
                            "trace_id": str(event.trace_id or "").strip(),
                            "cloud_session_id": _cloud_session_id,
                        },
                    )
                    return True
        except Exception:
            pass  # Fall through to old path

    entry = state_service.get_connector_entry(connector_id)
    secret = registry.resolve_secret(entry)
    profile = registry.resolve_profile(entry)
    delivery_result = dispatch.deliver_final_response(
        bot_token=_require_token(secret.get("bot_token"), label="bot_token"),
        chat_id=chat_id,
        workspace_id=event.workspace_id,
        connector_id=connector_id,
        session_key=str(payload.get("session_key") or "").strip() or registry.session_key_builder(chat_id),
        profile=profile,
        run_id=run_id,
        pending_message_id=str(payload.get("pending_message_id") or "").strip() or None,
        inbound_message_id=str(payload.get("parent_message_id") or "").strip() or None,
        action=str(payload.get("action") or "run").strip().lower() or "run",
        trace_id=str(event.trace_id or "").strip(),
        source_event_id=str(payload.get("source_event_id") or "").strip(),
        status=str(result.get("status") or ""),
        summary=str(result.get("summary") or ""),
        record_channel_event=registry.record_channel_event,
        send_message=registry.send_message,
        edit_message=registry.edit_message,
        set_connector_state=state_service.set_connector_state,
        provider_idempotency_key=str(delivery_state.get("provider_idempotency_key") or "").strip() or None,
        tenant_id=str(payload.get("tenant_id") or "").strip() or None,
        thread_id=str(payload.get("thread_id") or "").strip() or None,
        owner_install_id=str(payload.get("owner_install_id") or "").strip() or None,
        owner_label=str(payload.get("owner_label") or "").strip() or None,
        owner_type=str(payload.get("owner_type") or "").strip() or None,
        deployed_agent_id=str(payload.get("deployed_agent_id") or "").strip() or None,
    )
    patched_delivery = (
        dict(delivery_result.get("delivery") or {})
        if isinstance(delivery_result, dict) and isinstance(delivery_result.get("delivery"), dict)
        else dict(delivery_state)
    )
    patched_delivery.setdefault("provider", "telegram")
    patched_delivery.setdefault("replay_safe", True)
    _patch_delivery_state(event, patched_delivery)
    return True


def _deliver_whatsapp(event: "OutboxEvent") -> bool:
    payload = _payload(event)
    delivery_state = _delivery(payload)
    connector_id = _require_token(payload.get("connector_id"), label="connector_id")
    reply_to_number = _require_token(payload.get("reply_to_number") or payload.get("recipient"), label="reply_to_number")
    run_id = _require_token(event.run_id, label="run_id")
    shell = _connector_shell()
    registry = shell.whatsapp_service_registry()
    dispatch = registry.whatsapp_run_dispatch_service()
    state_service = registry.whatsapp_autopilot_state_service()
    connector_state = _connector_state_dict(state_service, connector_id)
    if _skip_resend_if_receipted(
        event,
        provider="whatsapp",
        payload=payload,
        connector_state=connector_state,
    ):
        return True
    direct_notification = _notification_text(payload)
    if direct_notification:
        result = {"ready": True, "status": "completed", "summary": direct_notification}
    else:
        result = dispatch.poll_run_terminal_result(run_id, max_reply_chars=registry.max_reply_chars)
        if not bool(result.get("ready")):
            _defer_until_terminal(event, status=str(result.get("status") or "starting"))
    entry = state_service.get_connector_entry(connector_id)
    secret = registry.resolve_vault_credential(connector_id, event.workspace_id)
    profile = registry.resolve_profile(entry)
    delivery_result = dispatch.deliver_final_response(
        run_id=run_id,
        connector_id=connector_id,
        workspace_id=event.workspace_id,
        profile=profile,
        secret=secret,
        reply_to_number=reply_to_number,
        status=str(result.get("status") or ""),
        summary=str(result.get("summary") or ""),
        provider_idempotency_key=str(delivery_state.get("provider_idempotency_key") or "").strip() or None,
    )
    patched_delivery = (
        dict(delivery_result.get("delivery") or {})
        if isinstance(delivery_result, dict) and isinstance(delivery_result.get("delivery"), dict)
        else dict(delivery_state)
    )
    patched_delivery.setdefault("provider", "whatsapp")
    patched_delivery.setdefault("replay_safe", True)
    _patch_delivery_state(event, patched_delivery)
    return True


def deliver_channel_run_delivery_outbox_event(event: "OutboxEvent") -> bool:
    payload = _payload(event)
    channel = str(payload.get("channel") or "").strip().lower()
    if channel == "telegram":
        return _deliver_telegram(event)
    if channel == "whatsapp":
        return _deliver_whatsapp(event)
    raise RuntimeError(f"Unsupported channel delivery outbox channel: {channel or 'unknown'}")
