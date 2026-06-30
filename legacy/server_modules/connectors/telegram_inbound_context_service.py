from __future__ import annotations

from typing import Any, Callable, Dict, List


class TelegramInboundContextService:
    def __init__(
        self,
        *,
        media_max_items: int,
        extract_message: Callable[[Dict[str, Any]], Dict[str, Any] | None],
        chat_matches: Callable[[str, Dict[str, Any]], bool],
        store_attachments: Callable[..., List[Dict[str, Any]]],
        route_message: Callable[[str, Dict[str, Any]], Dict[str, Any]],
        session_key_builder: Callable[[str], str],
        trace_id_builder: Callable[[str, Any, str], str],
        record_channel_event: Callable[..., Dict[str, Any] | None],
        guided_setup_handler: Callable[..., Dict[str, Any]],
        send_message: Callable[..., Any],
    ) -> None:
        self.media_max_items = max(1, int(media_max_items or 1))
        self.extract_message = extract_message
        self.chat_matches = chat_matches
        self.store_attachments = store_attachments
        self.route_message = route_message
        self.session_key_builder = session_key_builder
        self.trace_id_builder = trace_id_builder
        self.record_channel_event = record_channel_event
        self.guided_setup_handler = guided_setup_handler
        self.send_message = send_message

    def extract_inbound_message(
        self,
        *,
        update: Dict[str, Any],
        configured_chat_id: str,
    ) -> Dict[str, Any]:
        update_id = int(update.get("update_id") or 0)
        message = self.extract_message(update)
        if not isinstance(message, dict):
            return {"handled": False, "reason": "missing_message", "update_id": update_id}
        chat = message.get("chat") if isinstance(message.get("chat"), dict) else {}
        sender = message.get("from") if isinstance(message.get("from"), dict) else {}
        if bool(sender.get("is_bot")):
            return {"handled": False, "reason": "bot_sender", "update_id": update_id}
        if not self.chat_matches(configured_chat_id, chat):
            return {"handled": False, "reason": "chat_mismatch", "update_id": update_id}
        return {
            "handled": True,
            "update_id": update_id,
            "message": message,
            "chat": chat,
            "sender": sender,
        }

    def build_inbound_context(
        self,
        *,
        bot_token: str,
        workspace_id: str,
        connector_id: str,
        profile: Dict[str, Any],
        configured_chat_id: str,
        extracted_message: Dict[str, Any],
        extracted_chat: Dict[str, Any],
        extracted_sender: Dict[str, Any],
        update_id: int,
    ) -> Dict[str, Any]:
        chat_id = str(extracted_chat.get("id") or configured_chat_id).strip()
        sender_id = str(extracted_sender.get("id") or "").strip()
        inbound_message_id = str(extracted_message.get("message_id") or "").strip()
        inbound_parent_id = str(extracted_message.get("reply_to_message_id") or "").strip()
        message_text = str(extracted_message.get("text") or "")
        raw_attachments = (
            extracted_message.get("attachments")
            if isinstance(extracted_message.get("attachments"), list)
            else []
        )
        stored_attachments = self.store_attachments(
            bot_token=bot_token,
            workspace_id=workspace_id,
            chat_id=chat_id,
            update_id=update_id,
            message_id=inbound_message_id or "",
            attachments=raw_attachments,
        )
        routed = self.route_message(message_text, profile)
        action = str(routed.get("action") or "ignore").strip().lower()
        if action == "ignore" and stored_attachments:
            routed = {
                "action": "run",
                "goal": "Analyze the attached image(s) and help me with what they contain.",
                "source": "image_only",
            }
            action = "run"
        session_key = self.session_key_builder(chat_id)
        trace_id = self.trace_id_builder(chat_id, update_id, inbound_message_id or "")
        inbound_event = self.record_channel_event(
            channel="telegram",
            direction="inbound",
            event_type="message",
            text=message_text,
            workspace_id=workspace_id,
            session_key=session_key,
            session_id=session_key,
            message_id=inbound_message_id or None,
            parent_id=inbound_parent_id or None,
            action=action,
            metadata={
                "connector_id": connector_id,
                "sender_id": sender_id,
                "update_id": update_id,
                "profile_id": profile.get("id"),
                "attachments_count": len(stored_attachments),
                "trace_id": trace_id,
                "delivery_status": "received",
                "attachments": [
                    {
                        "kind": str(item.get("kind") or "").strip(),
                        "mime_type": str(item.get("mime_type") or "").strip(),
                        "path": str(item.get("relative_path") or item.get("path") or "").strip(),
                        "bytes": int(item.get("bytes") or 0),
                    }
                    for item in stored_attachments[: self.media_max_items]
                    if isinstance(item, dict)
                ],
            },
        )
        return {
            "chat_id": chat_id,
            "sender_id": sender_id,
            "inbound_message_id": inbound_message_id,
            "inbound_parent_id": inbound_parent_id,
            "message_text": message_text,
            "stored_attachments": stored_attachments,
            "routed": routed,
            "action": action,
            "session_key": session_key,
            "trace_id": trace_id,
            "source_event_id": str((inbound_event or {}).get("id") or "").strip(),
        }

    def handle_guided_setup(
        self,
        *,
        workspace_id: str,
        connector_id: str,
        profile: Dict[str, Any],
        bot_token: str,
        chat_id: str,
        message_text: str,
        inbound_message_id: str,
        session_key: str,
        trace_id: str,
        source_event_id: str,
    ) -> Dict[str, Any]:
        guided_setup = self.guided_setup_handler(
            workspace_id=workspace_id,
            chat_id=chat_id,
            message_text=message_text,
        )
        if not bool(guided_setup.get("handled")):
            return {"handled": False}
        reply_text = str(guided_setup.get("reply") or "").strip()
        self.record_channel_event(
            channel="telegram",
            direction="system",
            event_type="automation_setup_reply",
            text=reply_text,
            workspace_id=workspace_id,
            session_key=session_key,
            session_id=session_key,
            parent_id=inbound_message_id or None,
            action="automation_setup",
            metadata={
                "connector_id": connector_id,
                "profile_id": profile.get("id"),
                "trace_id": trace_id,
                "source_event_id": source_event_id,
            },
        )
        self.send_message(
            bot_token=bot_token,
            chat_id=chat_id,
            text=reply_text,
            workspace_id=workspace_id,
            action="automation_setup",
            connector_id=connector_id,
            parent_message_id=inbound_message_id or None,
            profile=profile,
            trace_id=trace_id,
            source_event_id=source_event_id,
        )
        return {"handled": True, "action": "automation_setup", "reply": reply_text}
