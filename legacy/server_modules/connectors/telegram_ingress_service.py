from __future__ import annotations

import json
from typing import Any, Callable, Dict, List, Optional

from server_modules.direct_tool_config_service import run_async_tool_call


def _route_inbound_channel_message(**kwargs: Any) -> Dict[str, Any]:
    from server_modules import agent_channel_router

    return run_async_tool_call(agent_channel_router.route_inbound_channel_message(**kwargs))


def _entitlements_module():
    from server_modules import entitlements_service

    return entitlements_service


def _acquisition_service():
    from server_modules import channel_user_acquisition_service

    return channel_user_acquisition_service.get_channel_user_acquisition_service()


def _privacy_service():
    from server_modules import external_user_privacy_service

    return external_user_privacy_service.get_external_user_privacy_service()


def _workspace_payload(workspace_id: str) -> Dict[str, Any]:
    from server_modules import control_plane_repository

    payload = run_async_tool_call(control_plane_repository.get_workspace_by_id(workspace_id))
    return payload if isinstance(payload, dict) else {}


def _coerce_dict(value: Any) -> Dict[str, Any]:
    return dict(value or {}) if isinstance(value, dict) else {}


def _scoped_telegram_session_key(*, connector_id: str, chat_id: str) -> str:
    from server_modules.channel_identity_service import safe_slug

    connector_token = safe_slug(connector_id, fallback="connector")
    chat_token = safe_slug(chat_id, fallback="chat")
    return f"telegram:{connector_token}:{chat_token}"


class TelegramIngressService:
    def __init__(
        self,
        *,
        default_workspace_id: str,
        resolve_workspace_scope: Callable[[Dict[str, Any], str | None, str], str],
        get_connector_entry: Callable[[str], Dict[str, Any]],
        resolve_profile: Callable[[Dict[str, Any]], Dict[str, Any]],
        resolve_allow_from: Callable[[Dict[str, Any]], List[str]],
        connector_state: Callable[[str], Dict[str, Any]],
        resolve_secret: Callable[[Dict[str, Any]], Dict[str, Any]],
        poll_cycle_service: Callable[[], Any],
        poll_state_service: Callable[[], Any],
        extract_message: Callable[[Dict[str, Any]], Dict[str, Any] | None],
        chat_matches: Callable[[str, Dict[str, Any]], bool],
        store_attachments: Callable[..., List[Dict[str, Any]]],
        route_message: Callable[[str, Dict[str, Any]], Dict[str, Any]],
        session_key_builder: Callable[[str], str],
        trace_id_builder: Callable[[str, Any, str], str],
        record_channel_event: Callable[..., Dict[str, Any] | None],
        guided_setup_handler: Callable[..., Dict[str, Any]],
        send_message: Callable[..., Any],
        send_chat_action: Callable[..., Any],
        run_dispatch_service: Callable[[], Any],
        action_service: Callable[[], Any],
        run_action_service: Callable[[], Any],
        get_chat_profile: Callable[[str, str], Dict[str, Any]],
        explicit_run_command: Callable[[str], bool],
        help_text: Callable[[Dict[str, Any]], str],
        channel_pairing_service: Callable[[], Any],
        media_max_items: int,
    ) -> None:
        self.default_workspace_id = str(default_workspace_id or "default").strip() or "default"
        self.resolve_workspace_scope = resolve_workspace_scope
        self.get_connector_entry = get_connector_entry
        self.resolve_profile = resolve_profile
        self.resolve_allow_from = resolve_allow_from
        self.connector_state = connector_state
        self.resolve_secret = resolve_secret
        self.poll_cycle_service = poll_cycle_service
        self.poll_state_service = poll_state_service
        self.extract_message = extract_message
        self.chat_matches = chat_matches
        self.store_attachments = store_attachments
        self.route_message = route_message
        self.session_key_builder = session_key_builder
        self.trace_id_builder = trace_id_builder
        self.record_channel_event = record_channel_event
        self.guided_setup_handler = guided_setup_handler
        self.send_message = send_message
        self.send_chat_action = send_chat_action
        self.run_dispatch_service = run_dispatch_service
        self.action_service = action_service
        self.run_action_service = run_action_service
        self.get_chat_profile = get_chat_profile
        self.explicit_run_command = explicit_run_command
        self.help_text = help_text
        self.channel_pairing_service = channel_pairing_service
        self.media_max_items = max(1, int(media_max_items or 1))

    def parse_update(self, raw_body: bytes) -> Dict[str, Any]:
        try:
            decoded = raw_body.decode("utf-8", errors="strict") if raw_body else "{}"
            payload = json.loads(decoded)
        except Exception as exc:
            raise ValueError(f"Telegram webhook payload is invalid JSON: {exc}") from exc
        if not isinstance(payload, dict):
            raise ValueError("Telegram webhook payload must be a JSON object.")
        return payload

    def ingest_update(
        self,
        *,
        source: str,
        update: Dict[str, Any],
        connector_id: Optional[str] = None,
        entry: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        delivery_source = str(source or "").strip().lower() or "polling"
        if delivery_source not in {"webhook", "polling"}:
            raise ValueError("Unsupported Telegram delivery source.")

        connector_entry = entry or self.get_connector_entry(str(connector_id or "").strip())
        connector_token = str(connector_id or connector_entry.get("id") or "").strip()
        if not connector_token:
            raise LookupError("Telegram connector id is required.")
        label = str(connector_entry.get("label") or connector_token).strip() or connector_token
        workspace_id = ""

        try:
            workspace_id = self.resolve_workspace_scope(
                connector_entry,
                self.default_workspace_id,
                "Telegram connector",
            )
            profile = self.resolve_profile(connector_entry)
            allow_from = self.resolve_allow_from(connector_entry)
            connector_state = self.connector_state(connector_token)
            last_update_id = int(connector_state.get("last_update_id") or 0)
            secret = self.resolve_secret(connector_entry)
            bot_token = str(secret.get("bot_token") or "").strip()
            configured_chat_id = str(secret.get("chat_id") or "").strip()
            if not bot_token or not configured_chat_id:
                raise RuntimeError("Connector is missing bot_token or chat_id.")

            envelope = self.normalize_telegram_update(
                source=delivery_source,
                entry=connector_entry,
                connector_id=connector_token,
                workspace_id=workspace_id,
                profile=profile,
                bot_token=bot_token,
                configured_chat_id=configured_chat_id,
                update=update,
            )
            update_id = int(envelope.get("update_id") or int(update.get("update_id") or 0))
            if update_id and update_id <= last_update_id:
                return {
                    "handled": True,
                    "processed": False,
                    "duplicate": True,
                    "reason": "duplicate_update",
                    "update_id": update_id,
                    "connector_id": connector_token,
                    "workspace_id": workspace_id,
                }

            profile_id = str(profile.get("id") or "").strip()
            if not bool(envelope.get("handled")):
                if update_id > 0:
                    self.poll_state_service().record_poll_completion(
                        connector_id=connector_token,
                        label=label,
                        workspace_id=workspace_id,
                        max_seen=update_id,
                        profile_id=profile_id,
                        allow_from=list(allow_from),
                        approval_state_patch={},
                    )
                return {
                    "handled": False,
                    "processed": False,
                    "reason": str(envelope.get("reason") or "ignored"),
                    "update_id": update_id,
                    "connector_id": connector_token,
                    "workspace_id": workspace_id,
                }

            dispatch_result = self.dispatch_telegram_envelope(
                entry=connector_entry,
                workspace_id=workspace_id,
                profile=profile,
                allow_from=list(allow_from),
                connector_state=connector_state,
                connector_id=connector_token,
                bot_token=bot_token,
                configured_chat_id=configured_chat_id,
                envelope=envelope,
            )
            if bool(dispatch_result.get("processed")):
                self.poll_state_service().record_processed_update(
                    connector_id=connector_token,
                    label=label,
                    workspace_id=str(dispatch_result.get("workspace_id") or workspace_id).strip() or workspace_id,
                    update_id=update_id,
                    chat_id=str(dispatch_result.get("chat_id") or "").strip(),
                    action=str(dispatch_result.get("action") or "").strip().lower(),
                    profile_id=profile_id,
                    allow_from=list(allow_from),
                    run_id=str(dispatch_result.get("run_id") or ""),
                    approval_state_patch={},
                )
            elif update_id > 0:
                self.poll_state_service().record_poll_completion(
                    connector_id=connector_token,
                    label=label,
                    workspace_id=workspace_id,
                    max_seen=update_id,
                    profile_id=profile_id,
                    allow_from=list(allow_from),
                    approval_state_patch={},
                )
            return {
                "handled": True,
                "processed": bool(dispatch_result.get("processed")),
                "duplicate": False,
                "reason": str(dispatch_result.get("reason") or "").strip() or None,
                "action": str(dispatch_result.get("action") or "").strip().lower() or None,
                "run_id": str(dispatch_result.get("run_id") or "").strip() or None,
                "chat_id": str(dispatch_result.get("chat_id") or "").strip() or None,
                "update_id": update_id,
                "connector_id": connector_token,
                "workspace_id": str(dispatch_result.get("workspace_id") or workspace_id).strip() or workspace_id,
            }
        except Exception as exc:
            self.poll_cycle_service().handle_connector_error(
                connector_id=connector_token,
                label=label,
                workspace_id=workspace_id,
                detail=str(exc),
            )
            raise

    def normalize_telegram_update(
        self,
        *,
        source: str,
        entry: Dict[str, Any],
        connector_id: str,
        workspace_id: str,
        profile: Dict[str, Any],
        bot_token: str,
        configured_chat_id: str,
        update: Dict[str, Any],
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

        chat_id = str(chat.get("id") or configured_chat_id).strip()
        sender_id = str(sender.get("id") or "").strip()
        inbound_message_id = str(message.get("message_id") or "").strip()
        inbound_parent_id = str(message.get("reply_to_message_id") or "").strip()
        message_text = str(message.get("text") or "")
        sender_username = str(sender.get("username") or "").strip() or None
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
            or None
        )
        raw_attachments = message.get("attachments") if isinstance(message.get("attachments"), list) else []
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
        session_key = _scoped_telegram_session_key(
            connector_id=connector_id,
            chat_id=chat_id,
        )
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
                "delivery_source": source,
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
            "handled": True,
            "delivery_source": source,
            "update_id": update_id,
            "connector_id": connector_id,
            "workspace_id": workspace_id,
            "chat_id": chat_id,
            "sender_id": sender_id,
            "sender_username": sender_username,
            "sender_display_name": sender_display_name,
            "inbound_message_id": inbound_message_id,
            "inbound_parent_id": inbound_parent_id,
            "message_text": message_text,
            "stored_attachments": stored_attachments,
            "routed": routed,
            "action": action,
            "session_key": session_key,
            "trace_id": trace_id,
            "source_event_id": str((inbound_event or {}).get("id") or "").strip(),
            "raw_update": update,
            "entry": entry,
        }

    def dispatch_telegram_envelope(
        self,
        *,
        entry: Dict[str, Any],
        workspace_id: str,
        profile: Dict[str, Any],
        allow_from: List[str],
        connector_state: Dict[str, Any],
        connector_id: str,
        bot_token: str,
        configured_chat_id: str,
        envelope: Dict[str, Any],
    ) -> Dict[str, Any]:
        public_profile = self._build_public_profile(
            entry=entry,
            profile=profile,
            workspace_id=workspace_id,
            connector_id=connector_id,
        )
        if self._is_public_deployed_agent_traffic(entry=entry, profile=public_profile):
            return self._dispatch_public_deployed_agent_envelope(
                entry=entry,
                workspace_id=workspace_id,
                connector_id=connector_id,
                bot_token=bot_token,
                configured_chat_id=configured_chat_id,
                profile=public_profile,
                envelope=envelope,
            )
        return self._dispatch_operator_envelope(
            entry=entry,
            workspace_id=workspace_id,
            profile=profile,
            allow_from=allow_from,
            connector_state=connector_state,
            connector_id=connector_id,
            bot_token=bot_token,
            configured_chat_id=configured_chat_id,
            envelope=envelope,
        )

    def _dispatch_public_deployed_agent_envelope(
        self,
        *,
        entry: Dict[str, Any],
        workspace_id: str,
        connector_id: str,
        bot_token: str,
        configured_chat_id: str,
        profile: Dict[str, Any],
        envelope: Dict[str, Any],
    ) -> Dict[str, Any]:
        profile = self._build_public_profile(
            entry=entry,
            profile=profile,
            workspace_id=workspace_id,
            connector_id=connector_id,
        )
        public_action = self._public_command_action(profile, str(envelope.get("message_text") or ""))
        if isinstance(public_action, dict):
            return self._handle_public_command(
                workspace_id=workspace_id,
                connector_id=connector_id,
                bot_token=bot_token,
                profile=profile,
                envelope=envelope,
                public_action=public_action,
            )

        tenant_id = self._resolve_tenant_id(entry=entry, workspace_id=workspace_id)
        endpoint_key = self._channel_endpoint_key(
            entry=entry,
            connector_id=connector_id,
            fallback=configured_chat_id,
        )
        chat_id = str(envelope.get("chat_id") or "").strip()
        if chat_id:
            self.send_chat_action(bot_token, chat_id, action="typing")
        route_result = _route_inbound_channel_message(
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            channel_key="telegram",
            endpoint_key=endpoint_key,
            customer_message=str(envelope.get("message_text") or ""),
            session_key=str(envelope.get("session_key") or "") or None,
            message_id=str(envelope.get("inbound_message_id") or "") or None,
            actor_id=str(envelope.get("sender_id") or "") or None,
            actor_display_name=str(envelope.get("sender_display_name") or "") or None,
            metadata={
                "connector_id": connector_id,
                "delivery_source": str(envelope.get("delivery_source") or "").strip() or None,
                "telegram_update_id": int(envelope.get("update_id") or 0) or None,
                "telegram_chat_id": str(envelope.get("chat_id") or "").strip() or None,
                "source_event_id": str(envelope.get("source_event_id") or "").strip() or None,
            },
            allow_master_fallback=False,
        )
        route_status = str((route_result or {}).get("status") or "").strip().lower()
        route_run_id = str((route_result or {}).get("run_id") or "").strip()
        reply_text = str((route_result or {}).get("reply") or "").strip()
        reply_markup = (route_result or {}).get("reply_markup") if isinstance((route_result or {}).get("reply_markup"), dict) else None
        notices = [
            dict(item)
            for item in list((route_result or {}).get("notices") or [])
            if isinstance(item, dict)
        ]
        for notice in notices:
            notice_text = str(notice.get("text") or "").strip()
            if not notice_text:
                continue
            self.send_message(
                bot_token=bot_token,
                chat_id=str(envelope.get("chat_id") or ""),
                text=notice_text,
                workspace_id=workspace_id,
                action="quota_warning",
                connector_id=connector_id,
                parent_message_id=str(envelope.get("inbound_message_id") or "") or None,
                profile=profile,
                include_keyboard=False,
                reply_markup=notice.get("reply_markup") if isinstance(notice.get("reply_markup"), dict) else None,
                trace_id=str(envelope.get("trace_id") or "") or None,
                source_event_id=str(envelope.get("source_event_id") or "") or None,
            )
        if route_run_id and self._is_pending_durable_status(route_status):
            pending_message_id = self.send_message(
                bot_token=bot_token,
                chat_id=str(envelope.get("chat_id") or ""),
                text="Thinking...",
                workspace_id=workspace_id,
                action="thinking",
                run_id=route_run_id,
                connector_id=connector_id,
                parent_message_id=str(envelope.get("inbound_message_id") or "") or None,
                profile=profile,
                include_keyboard=False,
                reply_markup=reply_markup,
                trace_id=str(envelope.get("trace_id") or "") or None,
                source_event_id=str(envelope.get("source_event_id") or "") or None,
            )
            self.run_dispatch_service().schedule_final_delivery(
                workspace_id=workspace_id,
                connector_id=connector_id,
                chat_id=str(envelope.get("chat_id") or ""),
                run_id=route_run_id,
                session_key=str((route_result or {}).get("session_key") or "") or None,
                pending_message_id=str(pending_message_id or "").strip(),
                inbound_message_id=str(envelope.get("inbound_message_id") or "") or None,
                action="run",
                trace_id=str(envelope.get("trace_id") or ""),
                source_event_id=str(envelope.get("source_event_id") or ""),
                tenant_id=str((route_result or {}).get("tenant_id") or "") or None,
                thread_id=str((route_result or {}).get("thread_id") or "") or None,
                owner_install_id=str((((route_result or {}).get("owner") or {}).get("install_id") or "")) or None,
                owner_label=str((((route_result or {}).get("owner") or {}).get("label") or "")) or None,
                owner_type=str((((route_result or {}).get("owner") or {}).get("type") or "")) or None,
                deployed_agent_id=str((route_result or {}).get("deployed_agent_id") or "") or None,
            )
        elif reply_text:
            self.send_message(
                bot_token=bot_token,
                chat_id=str(envelope.get("chat_id") or ""),
                text=reply_text,
                workspace_id=workspace_id,
                action=str((route_result or {}).get("status") or "channel_reply"),
                run_id=route_run_id or None,
                connector_id=connector_id,
                parent_message_id=str(envelope.get("inbound_message_id") or "") or None,
                profile=profile,
                include_keyboard=False,
                reply_markup=reply_markup,
                trace_id=str(envelope.get("trace_id") or "") or None,
                source_event_id=str(envelope.get("source_event_id") or "") or None,
            )
        return {
            "handled": True,
            "processed": True,
            "chat_id": str(envelope.get("chat_id") or "").strip(),
            "action": str((route_result or {}).get("status") or "channel_reply"),
            "run_id": route_run_id,
            "workspace_id": workspace_id,
        }

    def _is_pending_durable_status(self, status: str) -> bool:
        return str(status or "").strip().lower() in {
            "accepted",
            "queued",
            "queued_local",
            "running",
            "running_local",
            "waiting_for_input",
        }

    def _handle_public_command(
        self,
        *,
        workspace_id: str,
        connector_id: str,
        bot_token: str,
        profile: Dict[str, Any],
        envelope: Dict[str, Any],
        public_action: Dict[str, Any],
    ) -> Dict[str, Any]:
        action = str(public_action.get("action") or "help").strip().lower() or "help"
        routed = public_action.get("routed") if isinstance(public_action.get("routed"), dict) else {}
        external_user_id = str(envelope.get("sender_id") or envelope.get("chat_id") or "").strip()
        privacy_service = _privacy_service()
        if action == "public_start":
            start_payload = str(routed.get("campaign_token") or "").strip() or None
            prepared = _acquisition_service().prepare_public_start_response(
                profile=profile,
                workspace_id=workspace_id,
                external_user_id=external_user_id,
                sender_username=str(envelope.get("sender_username") or "").strip() or None,
                sender_display_name=str(envelope.get("sender_display_name") or "").strip() or None,
                campaign_token=start_payload,
            )
            reply_text = privacy_service.append_privacy_policy_line(
                str(prepared.get("text") or "").strip(),
                include_delete_hint=True,
                workspace_id=workspace_id,
                profile=profile,
            )
            self.send_message(
                bot_token=bot_token,
                chat_id=str(envelope.get("chat_id") or ""),
                text=reply_text,
                workspace_id=workspace_id,
                action=action,
                connector_id=connector_id,
                parent_message_id=str(envelope.get("inbound_message_id") or "") or None,
                profile=profile,
                include_keyboard=False,
                reply_markup=prepared.get("reply_markup") if isinstance(prepared.get("reply_markup"), dict) else None,
                trace_id=str(envelope.get("trace_id") or "") or None,
                source_event_id=str(envelope.get("source_event_id") or "") or None,
            )
        elif action == "privacy_policy":
            reply_text = privacy_service.privacy_policy_message(
                deployed_agent_name=str(profile.get("deployed_agent_name") or profile.get("label") or "").strip() or None,
                include_delete_hint=True,
                workspace_id=workspace_id,
                profile=profile,
            )
            self.send_message(
                bot_token=bot_token,
                chat_id=str(envelope.get("chat_id") or ""),
                text=reply_text,
                workspace_id=workspace_id,
                action=action,
                connector_id=connector_id,
                parent_message_id=str(envelope.get("inbound_message_id") or "") or None,
                profile=profile,
                include_keyboard=False,
                trace_id=str(envelope.get("trace_id") or "") or None,
                source_event_id=str(envelope.get("source_event_id") or "") or None,
            )
        elif action == "privacy_delete_request":
            deletion_request = privacy_service.create_channel_deletion_request(
                profile=profile,
                workspace_id=workspace_id,
                external_user_id=external_user_id,
                channel_key="telegram",
                session_key=str(envelope.get("session_key") or "") or None,
                requested_event_id=str(envelope.get("source_event_id") or "") or None,
                note=str(routed.get("note") or "").strip() or None,
                sender_username=str(envelope.get("sender_username") or "").strip() or None,
                sender_display_name=str(envelope.get("sender_display_name") or "").strip() or None,
            )
            reply_text = (
                "We recorded your data deletion request. The deployment owner can now remove your saved conversation data for this channel."
                if isinstance(deletion_request, dict) and str(deletion_request.get("id") or "").strip()
                else "The deletion request could not be recorded right now. Please try again shortly."
            )
            reply_text = privacy_service.append_privacy_policy_line(
                reply_text,
                workspace_id=workspace_id,
                profile=profile,
            )
            self.send_message(
                bot_token=bot_token,
                chat_id=str(envelope.get("chat_id") or ""),
                text=reply_text,
                workspace_id=workspace_id,
                action=action,
                connector_id=connector_id,
                parent_message_id=str(envelope.get("inbound_message_id") or "") or None,
                profile=profile,
                include_keyboard=False,
                trace_id=str(envelope.get("trace_id") or "") or None,
                source_event_id=str(envelope.get("source_event_id") or "") or None,
            )
        return {
            "handled": True,
            "processed": True,
            "chat_id": str(envelope.get("chat_id") or "").strip(),
            "action": action,
            "run_id": "",
            "workspace_id": workspace_id,
        }

    def _dispatch_operator_envelope(
        self,
        *,
        entry: Dict[str, Any],
        workspace_id: str,
        profile: Dict[str, Any],
        allow_from: List[str],
        connector_state: Dict[str, Any],
        connector_id: str,
        bot_token: str,
        configured_chat_id: str,
        envelope: Dict[str, Any],
    ) -> Dict[str, Any]:
        del allow_from, connector_state
        chat_id = str(envelope.get("chat_id") or configured_chat_id).strip()
        sender_id = str(envelope.get("sender_id") or "").strip()
        inbound_message_id = str(envelope.get("inbound_message_id") or "").strip()
        message_text = str(envelope.get("message_text") or "")
        action_service = self.action_service()
        pair_resolution = self.channel_pairing_service().authorize_channel_message(
            provider="telegram",
            external_subject=sender_id,
            workspace_id=workspace_id,
            message_text=message_text,
            observed_metadata={
                "chat_id": chat_id or None,
                "sender_username": str(envelope.get("sender_username") or "").strip().lower() or None,
                "sender_display_name": str(envelope.get("sender_display_name") or "").strip() or None,
                "connector_id": connector_id,
                "update_id": int(envelope.get("update_id") or 0),
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
                    trace_id=str(envelope.get("trace_id") or "") or None,
                    source_event_id=str(envelope.get("source_event_id") or "") or None,
                )
            return {
                "handled": True,
                "processed": False,
                "reason": str(pair_resolution.get("status") or "pairing_required"),
            }

        resolved_workspace_id = str(pair_resolution.get("workspace_id") or workspace_id).strip() or workspace_id
        try:
            _entitlements_module().enforce_channel_surface_access_for_workspace_id(
                "telegram",
                workspace_id=resolved_workspace_id,
            )
        except _entitlements_module().EntitlementDeniedError as exc:
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
                    trace_id=f"tgent:{chat_id}:{int(envelope.get('update_id') or 0)}",
                    source_event_id=None,
                )
            return {
                "handled": True,
                "processed": False,
                "reason": str(exc.reason or "telegram_channel_unavailable"),
            }

        guided_setup = self._handle_guided_setup(
            workspace_id=resolved_workspace_id,
            connector_id=connector_id,
            profile=profile,
            bot_token=bot_token,
            chat_id=chat_id,
            message_text=message_text,
            inbound_message_id=inbound_message_id,
            session_key=str(envelope.get("session_key") or ""),
            trace_id=str(envelope.get("trace_id") or ""),
            source_event_id=str(envelope.get("source_event_id") or ""),
        )
        if bool(guided_setup.get("handled")):
            return {"handled": True, "processed": False, "reason": "guided_setup"}

        action = str(envelope.get("action") or "ignore").strip().lower()
        if action == "ignore":
            return {"handled": True, "processed": False, "reason": "ignore"}

        routed = envelope.get("routed") if isinstance(envelope.get("routed"), dict) else {}
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
                trace_id=str(envelope.get("trace_id") or ""),
                source_event_id=str(envelope.get("source_event_id") or ""),
                sender_id=sender_id or chat_id,
                sender_username=str(envelope.get("sender_username") or "").strip() or None,
                sender_display_name=str(envelope.get("sender_display_name") or "").strip() or None,
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
                stored_attachments=envelope.get("stored_attachments") if isinstance(envelope.get("stored_attachments"), list) else [],
                workspace_id=resolved_workspace_id,
                connector_id=connector_id,
                connector_entry=entry,
                bot_token=bot_token,
                chat_id=chat_id,
                sender_id=sender_id,
                update_id=int(envelope.get("update_id") or 0),
                inbound_message_id=inbound_message_id or None,
                session_key=str(envelope.get("session_key") or ""),
                trace_id=str(envelope.get("trace_id") or ""),
                source_event_id=str(envelope.get("source_event_id") or ""),
            )
            action = str(run_result.get("action") or action)
            run_id = str(run_result.get("run_id") or "")
        else:
            self.send_message(
                bot_token=bot_token,
                chat_id=chat_id,
                text=self.help_text(profile),
                workspace_id=resolved_workspace_id,
                action="help",
                connector_id=connector_id,
                parent_message_id=inbound_message_id or None,
                profile=profile,
                trace_id=str(envelope.get("trace_id") or ""),
                source_event_id=str(envelope.get("source_event_id") or ""),
            )

        return {
            "handled": True,
            "processed": True,
            "chat_id": chat_id,
            "action": action,
            "run_id": run_id,
            "workspace_id": resolved_workspace_id,
        }

    def _handle_guided_setup(
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

    def _public_command_action(self, profile: Dict[str, Any], raw_message_text: str) -> Optional[Dict[str, Any]]:
        acquisition_service = _acquisition_service()
        privacy_service = _privacy_service()
        if acquisition_service.public_start_enabled(profile):
            payload = acquisition_service.start_payload(raw_message_text)
            if payload is not None:
                return {"action": "public_start", "routed": {"campaign_token": payload}}
        return privacy_service.public_command_action(profile, raw_message_text)

    def _build_public_profile(
        self,
        *,
        entry: Dict[str, Any],
        profile: Dict[str, Any],
        workspace_id: str,
        connector_id: str,
    ) -> Dict[str, Any]:
        metadata = _coerce_dict(entry.get("metadata"))
        deployed_agent = _coerce_dict(metadata.get("deployed_agent"))
        bindings = _coerce_dict(metadata.get("channel_registry_bindings"))
        telegram_binding = _coerce_dict(bindings.get("telegram"))
        payload = _coerce_dict(profile)
        payload.setdefault("tenant_id", self._resolve_tenant_id(entry=entry, workspace_id=workspace_id))
        payload.setdefault("workspace_id", workspace_id)
        payload.setdefault(
            "endpoint_key",
            self._channel_endpoint_key(
                entry=entry,
                connector_id=connector_id,
                fallback=str(telegram_binding.get("endpoint_key") or ""),
            ),
        )
        payload.setdefault(
            "deployed_agent_id",
            str(
                metadata.get("deployed_agent_id")
                or deployed_agent.get("id")
                or telegram_binding.get("deployed_agent_id")
                or ""
            ).strip(),
        )
        payload.setdefault(
            "deployed_agent_name",
            str(
                metadata.get("deployed_agent_name")
                or deployed_agent.get("name")
                or entry.get("label")
                or profile.get("label")
                or ""
            ).strip(),
        )
        for key in ("public_intro", "public_core_value", "platform_cta_label", "platform_cta_url", "public_source"):
            if key not in payload and key in metadata:
                payload[key] = metadata.get(key)
        return payload

    def _channel_endpoint_key(
        self,
        *,
        entry: Dict[str, Any],
        connector_id: str,
        fallback: str = "",
    ) -> str:
        metadata = entry.get("metadata") if isinstance(entry.get("metadata"), dict) else {}
        bindings = metadata.get("channel_registry_bindings") if isinstance(metadata.get("channel_registry_bindings"), dict) else {}
        binding = bindings.get("telegram") if isinstance(bindings.get("telegram"), dict) else {}
        candidates = (
            binding.get("endpoint_key"),
            metadata.get("telegram_endpoint_key"),
            fallback,
            connector_id,
        )
        for candidate in candidates:
            token = str(candidate or "").strip()
            if token:
                return token
        return "telegram"

    def _is_public_deployed_agent_traffic(self, *, entry: Dict[str, Any], profile: Dict[str, Any]) -> bool:
        if str(profile.get("deployed_agent_id") or "").strip():
            return True
        metadata = entry.get("metadata") if isinstance(entry.get("metadata"), dict) else {}
        if str(metadata.get("source") or "").strip().lower() == "deployed_agent":
            return True
        if isinstance(metadata.get("deployed_agent"), dict):
            return True
        bindings = metadata.get("channel_registry_bindings") if isinstance(metadata.get("channel_registry_bindings"), dict) else {}
        telegram_binding = bindings.get("telegram") if isinstance(bindings.get("telegram"), dict) else {}
        return bool(str(telegram_binding.get("endpoint_key") or "").strip())

    def _resolve_tenant_id(self, *, entry: Dict[str, Any], workspace_id: str) -> str:
        metadata = entry.get("metadata") if isinstance(entry.get("metadata"), dict) else {}
        for candidate in (entry.get("tenant_id"), metadata.get("tenant_id")):
            token = str(candidate or "").strip()
            if token:
                return token
        tenant_id = str(_workspace_payload(workspace_id).get("tenant_id") or "").strip()
        if tenant_id:
            return tenant_id
        raise RuntimeError("Telegram connector is not scoped to a tenant.")
