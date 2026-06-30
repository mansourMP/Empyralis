from __future__ import annotations

from typing import Any, Callable, Dict, List

from server_modules import entitlements_service


class TelegramPollDispatchService:
    def __init__(
        self,
        *,
        sender_allowed: Callable[[Dict[str, Any], List[str]], bool],
        session_key_builder: Callable[[str], str],
        inbound_context_service: Callable[[], Any],
        sender_filter_service: Callable[[], Any],
        action_service: Callable[[], Any],
        run_action_service: Callable[[], Any],
        get_chat_profile: Callable[[str, str], Dict[str, Any]],
        explicit_run_command: Callable[[str], bool],
        help_text: Callable[[Dict[str, Any]], str],
        send_message: Callable[..., Any],
        channel_pairing_service: Callable[[], Any],
    ) -> None:
        self.sender_allowed = sender_allowed
        self.session_key_builder = session_key_builder
        self.inbound_context_service = inbound_context_service
        self.sender_filter_service = sender_filter_service
        self.action_service = action_service
        self.run_action_service = run_action_service
        self.get_chat_profile = get_chat_profile
        self.explicit_run_command = explicit_run_command
        self.help_text = help_text
        self.send_message = send_message
        self.channel_pairing_service = channel_pairing_service

    def handle_update(
        self,
        *,
        entry: Dict[str, Any],
        label: str,
        workspace_id: str,
        profile: Dict[str, Any],
        allow_from: List[str],
        connector_state: Dict[str, Any],
        connector_id: str,
        bot_token: str,
        configured_chat_id: str,
        extracted_message: Dict[str, Any],
        update_id: int,
    ) -> Dict[str, Any]:
        message = extracted_message.get("message") if isinstance(extracted_message.get("message"), dict) else {}
        chat = extracted_message.get("chat") if isinstance(extracted_message.get("chat"), dict) else {}
        sender = extracted_message.get("sender") if isinstance(extracted_message.get("sender"), dict) else {}

        chat_id = str(chat.get("id") or configured_chat_id).strip()
        sender_id = str(sender.get("id") or "").strip()
        inbound_message_id = str(message.get("message_id") or "").strip()
        raw_message_text = str(message.get("text") or "")
        sender_username = str(sender.get("username") or "").strip()
        sender_display_name = (
            str(sender.get("full_name") or "").strip()
            or " ".join(
                token
                for token in (
                    str(sender.get("first_name") or "").strip(),
                    str(sender.get("last_name") or "").strip(),
                )
                if token
            ).strip()
        )
        action_service = self.action_service()
        public_action = action_service.public_action(profile, raw_message_text)
        if isinstance(public_action, dict):
            action_service.handle_non_run_action(
                action=str(public_action.get("action") or "help"),
                routed=public_action.get("routed") if isinstance(public_action.get("routed"), dict) else {},
                profile=profile,
                chat_profile=self.get_chat_profile(workspace_id, chat_id),
                workspace_id=workspace_id,
                connector_id=connector_id,
                bot_token=bot_token,
                chat_id=chat_id,
                inbound_message_id=inbound_message_id or "",
                trace_id=f"tgstart:{chat_id}:{update_id}",
                source_event_id="",
                sender_id=sender_id or chat_id,
                sender_username=sender_username or None,
                sender_display_name=sender_display_name or None,
                message_text=raw_message_text,
            )
            return {
                "handled": True,
                "processed": True,
                "chat_id": chat_id,
                "action": str(public_action.get("action") or "help"),
                "run_id": "",
                "workspace_id": workspace_id,
            }
        pair_resolution = self.channel_pairing_service().authorize_channel_message(
            provider="telegram",
            external_subject=sender_id,
            workspace_id=workspace_id,
            message_text=raw_message_text,
            observed_metadata={
                "chat_id": chat_id or None,
                "sender_username": str(sender.get("username") or "").strip().lower() or None,
                "sender_display_name": str(sender.get("first_name") or "").strip() or None,
                "connector_id": connector_id,
                "update_id": update_id,
            },
        )
        if not bool(pair_resolution.get("authorized")):
            reply_text = str(pair_resolution.get("reply_text") or "").strip()
            if reply_text and chat_id:
                self.send_message(
                    bot_token=bot_token,
                    chat_id=chat_id,
                    text=reply_text,
                    workspace_id=workspace_id,
                    action=str(pair_resolution.get("status") or "pairing_required"),
                    connector_id=connector_id,
                    parent_message_id=inbound_message_id or None,
                    profile=profile,
                    trace_id=f"tgpair:{chat_id}:{update_id}",
                    source_event_id=None,
                )
            return {
                "handled": True,
                "processed": False,
                "reason": str(pair_resolution.get("status") or "pairing_required"),
            }

        resolved_workspace_id = str(pair_resolution.get("workspace_id") or workspace_id).strip() or workspace_id
        try:
            entitlements_service.enforce_channel_surface_access_for_workspace_id(
                "telegram",
                workspace_id=resolved_workspace_id,
            )
        except entitlements_service.EntitlementDeniedError as exc:
            if chat_id:
                self.send_message(
                    bot_token=bot_token,
                    chat_id=chat_id,
                    text=str(exc.message or "Telegram access is not included in this workspace plan."),
                    workspace_id=resolved_workspace_id,
                    action=str(exc.reason or "telegram_channel_unavailable"),
                    connector_id=connector_id,
                    parent_message_id=inbound_message_id or None,
                    profile=profile,
                    trace_id=f"tgent:{chat_id}:{update_id}",
                    source_event_id=None,
                )
            return {
                "handled": True,
                "processed": False,
                "reason": str(exc.reason or "telegram_channel_unavailable"),
            }

        inbound_context = self.inbound_context_service().build_inbound_context(
            bot_token=bot_token,
            workspace_id=resolved_workspace_id,
            connector_id=connector_id,
            profile=profile,
            configured_chat_id=configured_chat_id,
            extracted_message=message,
            extracted_chat=chat,
            extracted_sender=sender,
            update_id=update_id,
        )
        chat_id = str(inbound_context.get("chat_id") or "").strip()
        sender_id = str(inbound_context.get("sender_id") or "").strip()
        inbound_message_id = str(inbound_context.get("inbound_message_id") or "").strip()
        message_text = str(inbound_context.get("message_text") or "")
        stored_attachments = (
            inbound_context.get("stored_attachments")
            if isinstance(inbound_context.get("stored_attachments"), list)
            else []
        )
        routed = inbound_context.get("routed") if isinstance(inbound_context.get("routed"), dict) else {}
        action = str(inbound_context.get("action") or "ignore").strip().lower()
        session_key = str(inbound_context.get("session_key") or "").strip()
        trace_id = str(inbound_context.get("trace_id") or "").strip()
        source_event_id = str(inbound_context.get("source_event_id") or "").strip()

        guided_setup = self.inbound_context_service().handle_guided_setup(
            workspace_id=resolved_workspace_id,
            connector_id=connector_id,
            profile=profile,
            bot_token=bot_token,
            chat_id=chat_id,
            message_text=message_text,
            inbound_message_id=inbound_message_id,
            session_key=session_key,
            trace_id=trace_id,
            source_event_id=source_event_id,
        )
        if bool(guided_setup.get("handled")):
            return {"handled": True, "processed": False, "reason": "guided_setup"}
        if action == "ignore":
            return {"handled": True, "processed": False, "reason": "ignore"}

        run_id = ""
        chat_profile = self.get_chat_profile(resolved_workspace_id, chat_id)
        if action != "run":
            action_result = action_service.handle_non_run_action(
                action=action,
                routed=routed,
                profile=profile,
                chat_profile=chat_profile,
                workspace_id=resolved_workspace_id,
                connector_id=connector_id,
                bot_token=bot_token,
                chat_id=chat_id,
                inbound_message_id=inbound_message_id or "",
                trace_id=trace_id,
                source_event_id=source_event_id,
                sender_id=sender_id or chat_id,
                sender_username=sender_username or None,
                sender_display_name=sender_display_name or None,
                message_text=message_text,
            )
            if bool(action_result.get("handled")):
                action = str(action_result.get("action") or action)
                if isinstance(action_result.get("chat_profile"), dict):
                    chat_profile = action_result.get("chat_profile") or chat_profile
        if action == "run":
            run_result = self.run_action_service().handle_run_action(
                routed=routed,
                message_text=message_text,
                explicit_run_command=self.explicit_run_command(message_text),
                profile=profile,
                chat_profile=chat_profile,
                stored_attachments=stored_attachments,
                workspace_id=resolved_workspace_id,
                connector_id=connector_id,
                connector_entry=entry,
                bot_token=bot_token,
                chat_id=chat_id,
                sender_id=sender_id,
                update_id=update_id,
                inbound_message_id=inbound_message_id or None,
                session_key=session_key,
                trace_id=trace_id,
                source_event_id=source_event_id,
            )
            action = str(run_result.get("action") or action)
            run_id = str(run_result.get("run_id") or "")
        else:
            self.send_message(
                bot_token,
                chat_id,
                self.help_text(profile),
                workspace_id=resolved_workspace_id,
                action="help",
                connector_id=connector_id,
                parent_message_id=inbound_message_id or None,
                profile=profile,
                trace_id=trace_id,
                source_event_id=source_event_id,
            )

        return {
            "handled": True,
            "processed": True,
            "chat_id": chat_id,
            "action": action,
            "run_id": run_id,
        }
