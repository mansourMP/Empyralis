from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any, Callable, Dict, Optional
from urllib.parse import quote_plus


class TelegramTransportService:
    def __init__(
        self,
        *,
        poll_seconds: float,
        http_json_request: Callable[..., Dict[str, Any]],
        session_key: Callable[[str], str],
        safe_path_token: Callable[[Any], str],
        reply_keyboard: Callable[[Dict[str, Any]], Dict[str, Any]],
        append_dead_letter: Callable[..., None],
        record_channel_event: Callable[..., Any],
        utc_now_iso: Callable[[], str],
    ) -> None:
        self.poll_seconds = float(poll_seconds)
        self.http_json_request = http_json_request
        self.session_key = session_key
        self.safe_path_token = safe_path_token
        self.reply_keyboard = reply_keyboard
        self.append_dead_letter = append_dead_letter
        self.record_channel_event = record_channel_event
        self.utc_now_iso = utc_now_iso

    def api_request(
        self,
        bot_token: str,
        method_name: str,
        params: Optional[Dict[str, Any]] = None,
        payload: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        base = f"https://api.telegram.org/bot{bot_token}/{method_name}"
        if params:
            query_parts = []
            for key, value in params.items():
                if value is None:
                    continue
                query_parts.append(f"{quote_plus(str(key))}={quote_plus(str(value))}")
            if query_parts:
                base = f"{base}?{'&'.join(query_parts)}"
        headers: Dict[str, str] = {}
        if payload is not None:
            headers["Content-Type"] = "application/json"
        timeout_seconds = max(6, int(max(1.0, self.poll_seconds)) + 3)
        res = self.http_json_request(
            base,
            method="POST" if payload is not None else "GET",
            headers=headers,
            payload=payload,
            timeout=timeout_seconds,
        )
        body = res.get("json") if isinstance(res.get("json"), dict) else {}
        if not isinstance(body, dict):
            raise RuntimeError(f"Unexpected Telegram response for {method_name}.")
        if res.get("status") != 200 or not bool(body.get("ok")):
            detail = str(body.get("description") or "").strip()
            raise RuntimeError(detail or f"Telegram {method_name} failed.")
        result = body.get("result")
        return result if isinstance(result, dict) else {"result": result}

    def send_message(
        self,
        *,
        bot_token: str,
        chat_id: str,
        text: str,
        workspace_id: Optional[str] = None,
        action: Optional[str] = None,
        run_id: Optional[str] = None,
        connector_id: Optional[str] = None,
        parent_message_id: Optional[Any] = None,
        profile: Optional[Dict[str, Any]] = None,
        include_keyboard: bool = True,
        reply_markup: Optional[Dict[str, Any]] = None,
        trace_id: Optional[str] = None,
        source_event_id: Optional[str] = None,
        idempotency_key: Optional[str] = None,
    ) -> str:
        payload = {
            "chat_id": chat_id,
            "text": text,
            "disable_web_page_preview": True,
        }
        if isinstance(reply_markup, dict) and reply_markup:
            payload["reply_markup"] = reply_markup
        elif include_keyboard and isinstance(profile, dict):
            default_keyboard = self.reply_keyboard(profile)
            if default_keyboard:
                payload["reply_markup"] = default_keyboard
        resolved_trace_id = str(trace_id or "").strip()
        if not resolved_trace_id:
            resolved_trace_id = f"tg-out:{self.safe_path_token(chat_id)}:{str(uuid.uuid4())[:10]}"
        try:
            result = self.api_request(bot_token, "sendMessage", payload=payload)
        except Exception as exc:
            self.append_dead_letter(
                channel="telegram",
                direction="outbound",
                event_type="message",
                reason=str(exc),
                text=text,
                workspace_id=str(workspace_id or ""),
                session_key=self.session_key(chat_id),
                run_id=str(run_id or ""),
                action=str(action or ""),
                connector_id=str(connector_id or ""),
                trace_id=resolved_trace_id,
                source_event_id=str(source_event_id or "").strip(),
                metadata={
                    "transport": "telegram_sendMessage",
                    "provider_idempotency_key": str(idempotency_key or "").strip() or None,
                },
            )
            raise
        sent = result if isinstance(result, dict) else {}
        if not sent and isinstance(result.get("result"), dict):
            sent = result.get("result")  # type: ignore[assignment]
        sent_message_id = str(sent.get("message_id") or "").strip()
        session_key = self.session_key(chat_id)
        self.record_channel_event(
            channel="telegram",
            direction="outbound",
            event_type="message",
            text=text,
            workspace_id=workspace_id,
            session_key=session_key,
            session_id=session_key,
            message_id=sent_message_id or None,
            parent_id=str(parent_message_id or "").strip() or None,
            run_id=run_id,
            action=action,
            metadata={
                "connector_id": str(connector_id or "").strip(),
                "trace_id": resolved_trace_id,
                "source_event_id": str(source_event_id or "").strip(),
                "delivery_status": "sent",
                "delivery_transport": "telegram_sendMessage",
                "provider": "telegram",
                "provider_message_id": sent_message_id or None,
                "provider_idempotency_key": str(idempotency_key or "").strip() or None,
            },
        )
        return sent_message_id

    def send_chat_action(self, bot_token: str, chat_id: str, action: str = "typing") -> None:
        try:
            self.api_request(
                bot_token,
                "sendChatAction",
                payload={
                    "chat_id": chat_id,
                    "action": action,
                },
            )
        except Exception:
            return

    def edit_message(
        self,
        *,
        bot_token: str,
        chat_id: str,
        message_id: Any,
        text: str,
        workspace_id: Optional[str] = None,
        action: Optional[str] = None,
        run_id: Optional[str] = None,
        connector_id: Optional[str] = None,
        parent_message_id: Optional[Any] = None,
        profile: Optional[Dict[str, Any]] = None,
        include_keyboard: bool = True,
        reply_markup: Optional[Dict[str, Any]] = None,
        trace_id: Optional[str] = None,
        source_event_id: Optional[str] = None,
        idempotency_key: Optional[str] = None,
    ) -> bool:
        message_token = str(message_id or "").strip()
        if not message_token:
            return False
        payload: Dict[str, Any] = {
            "chat_id": chat_id,
            "message_id": int(message_token) if message_token.isdigit() else message_token,
            "text": text,
            "disable_web_page_preview": True,
        }
        if isinstance(reply_markup, dict) and reply_markup:
            payload["reply_markup"] = reply_markup
        elif include_keyboard and isinstance(profile, dict):
            default_keyboard = self.reply_keyboard(profile)
            if default_keyboard:
                payload["reply_markup"] = default_keyboard
        resolved_trace_id = str(trace_id or "").strip()
        if not resolved_trace_id:
            resolved_trace_id = f"tg-edit:{self.safe_path_token(chat_id)}:{str(uuid.uuid4())[:10]}"
        try:
            self.api_request(bot_token, "editMessageText", payload=payload)
        except Exception as exc:
            self.append_dead_letter(
                channel="telegram",
                direction="outbound",
                event_type="message_edit",
                reason=str(exc),
                text=text,
                workspace_id=str(workspace_id or ""),
                session_key=self.session_key(chat_id),
                run_id=str(run_id or ""),
                action=str(action or ""),
                connector_id=str(connector_id or ""),
                trace_id=resolved_trace_id,
                source_event_id=str(source_event_id or "").strip(),
                metadata={
                    "transport": "telegram_editMessageText",
                    "message_id": message_token,
                    "provider_idempotency_key": str(idempotency_key or "").strip() or None,
                },
            )
            return False
        session_key = self.session_key(chat_id)
        self.record_channel_event(
            channel="telegram",
            direction="outbound",
            event_type="message_edit",
            text=text,
            workspace_id=workspace_id,
            session_key=session_key,
            session_id=session_key,
            message_id=message_token or None,
            parent_id=str(parent_message_id or "").strip() or None,
            run_id=run_id,
            action=action,
            metadata={
                "connector_id": str(connector_id or "").strip(),
                "trace_id": resolved_trace_id,
                "source_event_id": str(source_event_id or "").strip(),
                "delivery_status": "sent",
                "delivery_transport": "telegram_editMessageText",
                "provider": "telegram",
                "provider_message_id": message_token or None,
                "provider_idempotency_key": str(idempotency_key or "").strip() or None,
            },
        )
        return True
