from __future__ import annotations

from typing import Any, Callable, Dict


class WhatsAppRunDispatchService:
    def __init__(
        self,
        *,
        default_timeout_seconds: int,
        default_max_reply_chars: int,
        send_ack: bool,
        include_run_meta: Callable[[], bool],
        truncate_one_line: Callable[[str, int], str],
        poll_run_terminal_result: Callable[..., Dict[str, Any]],
        run_reply_text: Callable[[str, str, str], str],
        emit_channel_run_delivery_event: Callable[..., Any],
        send_whatsapp_message: Callable[..., Dict[str, Any]],
        append_dead_letter: Callable[..., Any],
        record_channel_event: Callable[..., Any],
        set_connector_state: Callable[..., Any],
        utc_now_iso: Callable[[], str],
        classify_error: Callable[[str], str],
        log_error: Callable[[str], Any],
        mark_error: Callable[[str], Any],
        session_key_builder: Callable[[str, str], str],
        safe_path_token: Callable[[Any], str],
    ) -> None:
        self.default_timeout_seconds = max(30, int(default_timeout_seconds or 30))
        self.default_max_reply_chars = max(80, int(default_max_reply_chars or 80))
        self.send_ack = bool(send_ack)
        self.include_run_meta = include_run_meta
        self.truncate_one_line = truncate_one_line
        self.poll_run_terminal_result = poll_run_terminal_result
        self.run_reply_text = run_reply_text
        self.emit_channel_run_delivery_event = emit_channel_run_delivery_event
        self.send_whatsapp_message = send_whatsapp_message
        self.append_dead_letter = append_dead_letter
        self.record_channel_event = record_channel_event
        self.set_connector_state = set_connector_state
        self.utc_now_iso = utc_now_iso
        self.classify_error = classify_error
        self.log_error = log_error
        self.mark_error = mark_error
        self.session_key_builder = session_key_builder
        self.safe_path_token = safe_path_token

    def delivery_idempotency_key(
        self,
        *,
        connector_id: str,
        reply_to_number: str,
        run_id: str,
    ) -> str:
        return f"whatsapp:{connector_id}:{reply_to_number}:{run_id}:message"

    def ack_text(self, run_id: str) -> str:
        if not self.send_ack:
            return ""
        message = "⏣ Empyralis started your request."
        if run_id and self.include_run_meta():
            message += f"\nrun_id: {run_id}"
        return message

    def schedule_final_delivery(
        self,
        *,
        workspace_id: str,
        connector_id: str,
        run_id: str,
        reply_to_number: str,
        trace_id: str,
    ) -> None:
        provider_idempotency_key = self.delivery_idempotency_key(
            connector_id=connector_id,
            reply_to_number=reply_to_number,
            run_id=run_id,
        )
        self.emit_channel_run_delivery_event(
            channel="whatsapp",
            tenant_id="default",
            workspace_id=workspace_id,
            run_id=run_id,
            connector_id=connector_id,
            trace_id=trace_id,
            idempotency_key=f"channel_run_delivery:whatsapp:{connector_id}:{run_id}",
            payload={
                "reply_to_number": str(reply_to_number or "").strip(),
                "action": "run",
                "delivery": {
                    "provider": "whatsapp",
                    "transport": "twilio_messages_api",
                    "status": "pending",
                    "provider_idempotency_key": provider_idempotency_key,
                },
            },
        )

    def deliver_final_response(
        self,
        *,
        run_id: str,
        connector_id: str,
        workspace_id: str,
        profile: Dict[str, Any],
        secret: Dict[str, Any],
        reply_to_number: str,
        status: str,
        summary: str,
        provider_idempotency_key: str | None = None,
    ) -> Dict[str, Any]:
        session_key = self.session_key_builder(reply_to_number, str(secret.get("from_number") or ""))
        trace_id = f"wa:{self.safe_path_token(connector_id)}:{self.safe_path_token(run_id)[:12]}"
        resolved_delivery_id = str(provider_idempotency_key or "").strip() or self.delivery_idempotency_key(
            connector_id=connector_id,
            reply_to_number=reply_to_number,
            run_id=run_id,
        )
        try:
            resolved_status = str(status or "").strip().lower() or "completed"
            compact_summary = self.truncate_one_line(
                str(summary or "Run finished."),
                self.default_max_reply_chars,
            )
            message = self.run_reply_text(resolved_status, run_id, compact_summary)
            try:
                sent = self.send_whatsapp_message(
                    account_sid=str(secret.get("account_sid") or ""),
                    auth_token=str(secret.get("auth_token") or ""),
                    from_number=str(secret.get("from_number") or ""),
                    to_number=reply_to_number,
                    body=message,
                )
            except Exception as send_exc:
                self.append_dead_letter(
                    channel="whatsapp",
                    direction="outbound",
                    event_type="message",
                    reason=str(send_exc),
                    text=message,
                    workspace_id=workspace_id,
                    session_key=session_key,
                    run_id=run_id,
                    action="run",
                    connector_id=connector_id,
                    trace_id=trace_id,
                    metadata={
                        "transport": "twilio_messages_api",
                        "provider_idempotency_key": resolved_delivery_id,
                    },
                )
                raise
            outbound_message_id = str(sent.get("sid") or "").strip() if isinstance(sent, dict) else ""
            accepted_at = self.utc_now_iso()
            self.record_channel_event(
                channel="whatsapp",
                direction="outbound",
                event_type="message",
                text=message,
                workspace_id=workspace_id,
                session_key=session_key,
                session_id=session_key,
                message_id=outbound_message_id or None,
                run_id=run_id,
                action="run",
                metadata={
                    "connector_id": connector_id,
                    "profile_id": profile.get("id"),
                    "trace_id": trace_id,
                    "delivery_status": "sent",
                    "delivery_transport": "twilio_messages_api",
                    "provider": "whatsapp",
                    "provider_message_id": outbound_message_id or None,
                    "provider_idempotency_key": resolved_delivery_id,
                },
            )
            self.record_channel_event(
                channel="whatsapp",
                direction="system",
                event_type=(
                    f"run_{resolved_status if resolved_status in {'completed', 'failed', 'timeout'} else 'finished'}"
                ),
                text=compact_summary,
                workspace_id=workspace_id,
                session_key=session_key,
                session_id=session_key,
                run_id=run_id,
                action="run",
                metadata={
                    "connector_id": connector_id,
                    "profile_id": profile.get("id"),
                    "trace_id": trace_id,
                    "provider_idempotency_key": resolved_delivery_id,
                },
            )
            self.set_connector_state(
                connector_id,
                {
                    "workspace_id": workspace_id,
                    "profile_id": profile.get("id"),
                    "last_run_id": run_id,
                    "last_action": "run",
                    "last_error": None,
                    "last_error_category": None,
                    "last_error_at": None,
                    "last_processed_at": accepted_at,
                    "last_outbound_message_sid": outbound_message_id or None,
                    "last_delivery_status": "sent",
                    "last_delivery_receipt_at": accepted_at,
                    "last_delivery_idempotency_key": resolved_delivery_id,
                    "last_delivery_transport": "twilio_messages_api",
                },
            )
            delivery = {
                "provider": "whatsapp",
                "transport": "twilio_messages_api",
                "status": "sent",
                "provider_idempotency_key": resolved_delivery_id,
                "receipt": {
                    "provider_message_id": outbound_message_id or None,
                    "accepted_at": accepted_at,
                },
                "replay_safe": True,
            }
            return {
                "status": resolved_status,
                "summary": compact_summary,
                "message": message,
                "outbound_message_id": outbound_message_id,
                "delivery": delivery,
            }
        except Exception as exc:
            detail = str(exc)
            category = self.classify_error(detail)
            self.log_error(f"finalize error run_id={run_id}: {detail}")
            self.record_channel_event(
                channel="whatsapp",
                direction="system",
                event_type="error",
                text=detail,
                workspace_id=workspace_id,
                session_key=session_key,
                session_id=session_key,
                run_id=run_id,
                action="run",
                metadata={"connector_id": connector_id, "profile_id": profile.get("id")},
            )
            self.set_connector_state(
                connector_id,
                {
                    "workspace_id": workspace_id,
                    "profile_id": profile.get("id"),
                    "last_run_id": run_id,
                    "last_action": "run",
                    "last_error": detail,
                    "last_error_category": category,
                    "last_error_at": self.utc_now_iso(),
                    "last_processed_at": self.utc_now_iso(),
                    "last_delivery_status": "failed",
                    "last_delivery_idempotency_key": resolved_delivery_id,
                },
            )
            self.mark_error(detail)
            raise
