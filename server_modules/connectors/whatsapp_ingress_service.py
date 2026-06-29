from __future__ import annotations

from typing import Any, Callable, Dict, Optional
from urllib.parse import parse_qs

from server_modules import entitlements_service
from server_modules.direct_tool_config_service import run_async_tool_call


def _route_inbound_channel_message(**kwargs: Any) -> Dict[str, Any]:
    from server_modules import agent_channel_router

    return run_async_tool_call(agent_channel_router.route_inbound_channel_message(**kwargs))


def _privacy_service():
    from server_modules import external_user_privacy_service

    return external_user_privacy_service.get_external_user_privacy_service()


def _workspace_payload(workspace_id: str) -> Dict[str, Any]:
    from server_modules import control_plane_repository

    payload = run_async_tool_call(control_plane_repository.get_workspace_by_id(workspace_id))
    return payload if isinstance(payload, dict) else {}


class WhatsAppIngressService:
    def __init__(
        self,
        *,
        normalize_number: Callable[[Optional[str]], str],
        session_key_builder: Callable[[str, str], str],
        safe_path_token: Callable[[Any], str],
        connector_match: Callable[[str, str, str], Optional[Dict[str, Any]]],
        resolve_profile: Callable[[Dict[str, Any]], Dict[str, Any]],
        route_message: Callable[[str, Dict[str, Any]], Dict[str, Any]],
        help_text: Callable[[Dict[str, Any]], str],
        runtime_status_text: Callable[[str], str],
        approvals_list: Callable[[int, Optional[str]], Dict[str, Any]],
        approvals_text: Callable[[Dict[str, Any], str], str],
        approval_resolve: Callable[[str, bool, str, Optional[str]], Dict[str, Any]],
        approval_result_text: Callable[[Dict[str, Any], bool], str],
        create_run: Callable[..., Dict[str, Any]],
        run_dispatch_service: Callable[[], Any],
        record_channel_event: Callable[..., Any],
        set_connector_state: Callable[[str, Dict[str, Any]], Any],
        persist_state: Callable[[], Any],
        mark_processed_message: Callable[[str, str], bool],
        increment_processed: Callable[[], Any],
        autopilot_activate: Callable[[], Any],
        mark_inbound: Callable[..., Any],
        mark_error: Callable[[str], Any],
        utc_now_iso: Callable[[], str],
        default_chat_prefix: str,
        require_explicit_opt_in: bool,
        redact_event_text: bool,
        retention_days: int,
        channel_pairing_service: Callable[[], Any],
    ) -> None:
        self.normalize_number = normalize_number
        self.session_key_builder = session_key_builder
        self.safe_path_token = safe_path_token
        self.connector_match = connector_match
        self.resolve_profile = resolve_profile
        self.route_message = route_message
        self.help_text = help_text
        self.runtime_status_text = runtime_status_text
        self.approvals_list = approvals_list
        self.approvals_text = approvals_text
        self.approval_resolve = approval_resolve
        self.approval_result_text = approval_result_text
        self.create_run = create_run
        self.run_dispatch_service = run_dispatch_service
        self.record_channel_event = record_channel_event
        self.set_connector_state = set_connector_state
        self.persist_state = persist_state
        self.mark_processed_message = mark_processed_message
        self.increment_processed = increment_processed
        self.autopilot_activate = autopilot_activate
        self.mark_inbound = mark_inbound
        self.mark_error = mark_error
        self.utc_now_iso = utc_now_iso
        self.default_chat_prefix = default_chat_prefix
        self.require_explicit_opt_in = bool(require_explicit_opt_in)
        self.redact_event_text = bool(redact_event_text)
        self.retention_days = max(1, int(retention_days or 30))
        self.channel_pairing_service = channel_pairing_service

    def _event_text(self, text: Any) -> str:
        clean = str(text or "").strip()
        if not clean:
            return ""
        if not self.redact_event_text:
            return clean
        return "[redacted]"

    def _event_metadata(self, metadata: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        payload = dict(metadata or {})
        payload["privacy_mode"] = "channel_redacted" if self.redact_event_text else "channel_plaintext"
        payload["retention_days"] = self.retention_days
        payload["redacted_text"] = self.redact_event_text
        return payload

    def _link_scopes_allow_chat(self, link: Optional[Dict[str, Any]]) -> bool:
        if not isinstance(link, dict):
            return False
        scopes = {str(item or "").strip().lower() for item in (link.get("scopes") or [])}
        required = {"chat", "whatsapp:chat"}
        if not required.issubset(scopes):
            return False
        if self.require_explicit_opt_in and "whatsapp:opt_in" not in scopes:
            return False
        return True

    def parse_form_urlencoded(self, raw: bytes) -> Dict[str, str]:
        decoded = raw.decode("utf-8", errors="ignore") if raw else ""
        parsed = parse_qs(decoded, keep_blank_values=True)
        out: Dict[str, str] = {}
        for key, values in parsed.items():
            if isinstance(values, list) and values:
                out[str(key)] = str(values[-1])
            else:
                out[str(key)] = ""
        return out

    def ingest_webhook(
        self,
        form: Dict[str, str],
        *,
        matched: Optional[Dict[str, Any]] = None,
    ) -> str:
        self.autopilot_activate()
        envelope = self.normalize_whatsapp_webhook(form, matched=matched)
        if not bool(envelope.get("handled")):
            return str(envelope.get("reply_text") or "")
        result = self.dispatch_whatsapp_envelope(envelope=envelope)
        return str(result.get("reply_text") or "")

    def normalize_whatsapp_webhook(
        self,
        form: Dict[str, str],
        *,
        matched: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        payload = dict(form or {})
        account_sid = str(payload.get("AccountSid") or "").strip()
        message_sid = str(payload.get("MessageSid") or "").strip()
        inbound_from = self.normalize_number(payload.get("From"))
        inbound_to = self.normalize_number(payload.get("To"))
        message_text = str(payload.get("Body") or "").strip()
        session_key = self.session_key_builder(inbound_from, inbound_to)
        trace_id = f"wa:{self.safe_path_token(inbound_to or 'to')}:{self.safe_path_token(message_sid or 'msg')}"

        self.mark_inbound(clear_error=True)
        resolved_match = matched or self.connector_match(account_sid, inbound_from, inbound_to)
        if not isinstance(resolved_match, dict):
            detail = (
                f"No matching WhatsApp connector for inbound message "
                f"(account_sid={account_sid or 'unknown'}, to={inbound_to or 'unknown'})."
            )
            self.record_channel_event(
                channel="whatsapp",
                direction="inbound",
                event_type="message",
                text=self._event_text(message_text),
                session_key=session_key,
                session_id=session_key,
                message_id=message_sid or None,
                action="unmatched",
                metadata=self._event_metadata(
                    {
                        "account_sid": account_sid,
                        "message_sid": message_sid,
                        "to_number": inbound_to,
                        "trace_id": trace_id,
                        "delivery_status": "received",
                    }
                ),
            )
            self.record_channel_event(
                channel="whatsapp",
                direction="system",
                event_type="error",
                text=self._event_text(detail),
                session_key=session_key,
                session_id=session_key,
                parent_id=message_sid or None,
                action="match_connector",
                metadata=self._event_metadata(
                    {
                        "account_sid": account_sid,
                        "message_sid": message_sid,
                        "to_number": inbound_to,
                        "trace_id": trace_id,
                    }
                ),
            )
            self.mark_error(detail)
            return {
                "handled": False,
                "reason": "unmatched_connector",
                "reply_text": "Empyralis is not configured for this WhatsApp number.",
            }

        entry = resolved_match.get("entry") if isinstance(resolved_match.get("entry"), dict) else {}
        secret = resolved_match.get("secret") if isinstance(resolved_match.get("secret"), dict) else {}
        connector_id = str(resolved_match.get("connector_id") or "").strip()
        workspace_id = str(resolved_match.get("workspace_id") or "default").strip() or "default"
        if message_sid and not self.mark_processed_message(connector_id, message_sid):
            self.record_channel_event(
                channel="whatsapp",
                direction="system",
                event_type="duplicate",
                text=self._event_text("Duplicate WhatsApp inbound ignored."),
                workspace_id=workspace_id,
                session_key=session_key,
                session_id=session_key,
                parent_id=message_sid or None,
                action="duplicate_ignored",
                metadata=self._event_metadata(
                    {
                        "connector_id": connector_id,
                        "message_sid": message_sid,
                        "trace_id": trace_id,
                    }
                ),
            )
            return {"handled": False, "reason": "duplicate_message", "reply_text": ""}

        return {
            "handled": True,
            "delivery_source": "webhook",
            "account_sid": account_sid,
            "message_sid": message_sid,
            "from_number": inbound_from,
            "to_number": inbound_to,
            "message_text": message_text,
            "session_key": session_key,
            "trace_id": trace_id,
            "raw_form": payload,
            "entry": entry,
            "secret": secret,
            "connector_id": connector_id,
            "workspace_id": workspace_id,
        }

    def dispatch_whatsapp_envelope(self, *, envelope: Dict[str, Any]) -> Dict[str, Any]:
        entry = envelope.get("entry") if isinstance(envelope.get("entry"), dict) else {}
        workspace_id = str(envelope.get("workspace_id") or "default").strip() or "default"
        connector_id = str(envelope.get("connector_id") or "").strip()
        base_profile = self.resolve_profile(entry)
        public_profile = self._build_public_profile(
            entry=entry,
            profile=base_profile,
            workspace_id=workspace_id,
            connector_id=connector_id,
            fallback_endpoint=str(envelope.get("to_number") or ""),
        )
        if self._is_public_deployed_agent_traffic(entry=entry, profile=public_profile):
            return self._dispatch_public_deployed_agent_envelope(
                entry=entry,
                workspace_id=workspace_id,
                connector_id=connector_id,
                profile=public_profile,
                envelope=envelope,
            )
        return self._dispatch_operator_envelope(
            entry=entry,
            workspace_id=workspace_id,
            connector_id=connector_id,
            profile=base_profile,
            envelope=envelope,
        )

    def _dispatch_public_deployed_agent_envelope(
        self,
        *,
        entry: Dict[str, Any],
        workspace_id: str,
        connector_id: str,
        profile: Dict[str, Any],
        envelope: Dict[str, Any],
    ) -> Dict[str, Any]:
        public_action = self._public_command_action(profile, str(envelope.get("message_text") or ""))
        action = (
            str(public_action.get("action") or "").strip().lower()
            if isinstance(public_action, dict)
            else "channel_message"
        )
        source_event_id = self._record_inbound_event(
            envelope=envelope,
            workspace_id=workspace_id,
            action=action,
            profile=profile,
        )
        local_envelope = dict(envelope)
        local_envelope["source_event_id"] = source_event_id
        if isinstance(public_action, dict):
            return self._handle_public_command(
                workspace_id=workspace_id,
                connector_id=connector_id,
                profile=profile,
                envelope=local_envelope,
                public_action=public_action,
            )

        route_result = _route_inbound_channel_message(
            tenant_id=self._resolve_tenant_id(entry=entry, workspace_id=workspace_id),
            workspace_id=workspace_id,
            channel_key="whatsapp",
            endpoint_key=self._channel_endpoint_key(
                entry=entry,
                connector_id=connector_id,
                fallback=str(envelope.get("to_number") or ""),
            ),
            customer_message=str(envelope.get("message_text") or ""),
            session_key=str(envelope.get("session_key") or "") or None,
            message_id=str(envelope.get("message_sid") or "") or None,
            actor_id=str(envelope.get("from_number") or "") or None,
            actor_display_name=str(envelope.get("from_number") or "") or None,
            metadata={
                "connector_id": connector_id,
                "delivery_source": str(envelope.get("delivery_source") or "").strip() or None,
                "account_sid": str(envelope.get("account_sid") or "").strip() or None,
                "to_number": str(envelope.get("to_number") or "").strip() or None,
                "whatsapp_message_sid": str(envelope.get("message_sid") or "").strip() or None,
                "source_event_id": source_event_id or None,
            },
            allow_master_fallback=False,
        )
        reply_text = str((route_result or {}).get("reply") or "").strip()
        final_action = str((route_result or {}).get("status") or "channel_reply").strip().lower() or "channel_reply"
        run_id = str((route_result or {}).get("run_id") or "").strip()
        self._finalize_processed_message(
            envelope=local_envelope,
            workspace_id=workspace_id,
            action=final_action,
            response_text=reply_text,
            profile=profile,
            run_id=run_id,
        )
        return {
            "handled": True,
            "processed": True,
            "action": final_action,
            "run_id": run_id,
            "workspace_id": workspace_id,
            "reply_text": reply_text,
        }

    def _handle_public_command(
        self,
        *,
        workspace_id: str,
        connector_id: str,
        profile: Dict[str, Any],
        envelope: Dict[str, Any],
        public_action: Dict[str, Any],
    ) -> Dict[str, Any]:
        action = str(public_action.get("action") or "help").strip().lower() or "help"
        routed = public_action.get("routed") if isinstance(public_action.get("routed"), dict) else {}
        privacy_service = _privacy_service()
        external_user_id = str(envelope.get("from_number") or "").strip()
        if action == "privacy_policy":
            reply_text = privacy_service.privacy_policy_message(
                deployed_agent_name=str(profile.get("deployed_agent_name") or profile.get("label") or "").strip() or None,
                include_delete_hint=True,
                workspace_id=workspace_id,
                profile=profile,
            )
        elif action == "privacy_delete_request":
            deletion_request = privacy_service.create_channel_deletion_request(
                profile=profile,
                workspace_id=workspace_id,
                external_user_id=external_user_id,
                channel_key="whatsapp",
                session_key=str(envelope.get("session_key") or "") or None,
                requested_event_id=str(envelope.get("source_event_id") or envelope.get("message_sid") or "") or None,
                note=str(routed.get("note") or "").strip() or None,
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
        else:
            reply_text = privacy_service.append_privacy_policy_line(
                "Privacy controls are available in this chat.",
                workspace_id=workspace_id,
                profile=profile,
            )
            action = "privacy_help"
        self._finalize_processed_message(
            envelope=envelope,
            workspace_id=workspace_id,
            action=action,
            response_text=reply_text,
            profile=profile,
            run_id="",
        )
        return {
            "handled": True,
            "processed": True,
            "action": action,
            "run_id": "",
            "workspace_id": workspace_id,
            "reply_text": reply_text,
        }

    def _dispatch_operator_envelope(
        self,
        *,
        entry: Dict[str, Any],
        workspace_id: str,
        connector_id: str,
        profile: Dict[str, Any],
        envelope: Dict[str, Any],
    ) -> Dict[str, Any]:
        message_text = str(envelope.get("message_text") or "")
        from_number = str(envelope.get("from_number") or "").strip()
        to_number = str(envelope.get("to_number") or "").strip()
        message_sid = str(envelope.get("message_sid") or "").strip()
        account_sid = str(envelope.get("account_sid") or "").strip()
        session_key = str(envelope.get("session_key") or "")
        trace_id = str(envelope.get("trace_id") or "")

        pair_resolution = self.channel_pairing_service().authorize_channel_message(
            provider="whatsapp",
            external_subject=from_number,
            workspace_id=workspace_id,
            message_text=message_text,
            observed_metadata={
                "to_number": to_number or None,
                "account_sid": account_sid or None,
                "connector_id": connector_id,
                "message_sid": message_sid or None,
            },
        )
        if not bool(pair_resolution.get("authorized")):
            return {
                "handled": True,
                "processed": False,
                "action": str(pair_resolution.get("status") or "pairing_required"),
                "run_id": "",
                "workspace_id": workspace_id,
                "reply_text": str(pair_resolution.get("reply_text") or "").strip(),
            }

        active_link = pair_resolution.get("link") if isinstance(pair_resolution.get("link"), dict) else {}
        if not self._link_scopes_allow_chat(active_link):
            connect_url = self.channel_pairing_service().platform_connect_url(
                provider="whatsapp",
                workspace_id=workspace_id,
            )
            return {
                "handled": True,
                "processed": False,
                "action": "pairing_opt_in_required",
                "run_id": "",
                "workspace_id": workspace_id,
                "reply_text": (
                    "This WhatsApp number is not opted in for Empyralis chat yet. "
                    f"Open Empyralis to reconnect it: {connect_url}"
                ),
            }

        resolved_workspace_id = str(pair_resolution.get("workspace_id") or workspace_id).strip() or workspace_id
        try:
            entitlements_service.enforce_channel_surface_access_for_workspace_id(
                "whatsapp",
                workspace_id=resolved_workspace_id,
            )
        except entitlements_service.EntitlementDeniedError as exc:
            self.record_channel_event(
                channel="whatsapp",
                direction="system",
                event_type="error",
                text=self._event_text(str(exc.message or "WhatsApp access is unavailable.")),
                workspace_id=resolved_workspace_id,
                session_key=session_key,
                session_id=session_key,
                parent_id=message_sid or None,
                action="entitlement_denied",
                metadata=self._event_metadata(
                    {
                        "connector_id": connector_id,
                        "message_sid": message_sid,
                        "trace_id": trace_id,
                        "reason": str(exc.reason or "whatsapp_channel_unavailable"),
                    }
                ),
            )
            return {
                "handled": True,
                "processed": False,
                "action": str(exc.reason or "whatsapp_channel_unavailable"),
                "run_id": "",
                "workspace_id": resolved_workspace_id,
                "reply_text": str(exc.message or "WhatsApp access is not included in this workspace plan."),
            }

        routed = self.route_message(message_text, profile)
        action = str(routed.get("action") or "ignore").strip().lower()
        source_event_id = self._record_inbound_event(
            envelope=envelope,
            workspace_id=resolved_workspace_id,
            action=action,
            profile=profile,
            extra_metadata={"link_id": active_link.get("link_id")},
        )
        local_envelope = dict(envelope)
        local_envelope["source_event_id"] = source_event_id

        if action == "ignore":
            return {
                "handled": True,
                "processed": False,
                "action": "ignore",
                "run_id": "",
                "workspace_id": resolved_workspace_id,
                "reply_text": "",
            }

        run_id = ""
        response_text = ""
        if action == "help":
            response_text = self.help_text(profile)
        elif action == "status":
            response_text = self.runtime_status_text(resolved_workspace_id)
        elif action == "approvals":
            limit = int(routed.get("limit") or 5)
            payload = self.approvals_list(limit, resolved_workspace_id)
            response_text = self.approvals_text(payload, prefix=str(profile.get("prefix") or self.default_chat_prefix))
        elif action == "approve":
            event_id = str(routed.get("event_id") or "").strip()
            note = str(routed.get("note") or "").strip()
            payload = self.approval_resolve(event_id, True, note, resolved_workspace_id)
            response_text = self.approval_result_text(payload, True)
        elif action == "reject":
            event_id = str(routed.get("event_id") or "").strip()
            note = str(routed.get("note") or "").strip()
            payload = self.approval_resolve(event_id, False, note, resolved_workspace_id)
            response_text = self.approval_result_text(payload, False)
        elif action == "run":
            goal = str(routed.get("goal") or "").strip()
            if not goal:
                response_text = self.help_text(profile)
                action = "help"
            else:
                run_info = self.create_run(
                    goal=goal,
                    workspace_id=resolved_workspace_id,
                    connector_id=connector_id,
                    from_number=from_number,
                    to_number=to_number,
                    message_sid=message_sid,
                    account_sid=account_sid,
                    connector_entry=entry,
                )
                run_id = str(run_info.get("run_id") or "")
                dispatch_service = self.run_dispatch_service()
                if run_id:
                    response_text = dispatch_service.ack_text(run_id)
                    dispatch_service.schedule_final_delivery(
                        workspace_id=resolved_workspace_id,
                        connector_id=connector_id,
                        run_id=run_id,
                        reply_to_number=from_number,
                        trace_id=trace_id,
                    )
                else:
                    response_text = dispatch_service.ack_text("")
        else:
            action = "help"
            response_text = self.help_text(profile)

        self._finalize_processed_message(
            envelope=local_envelope,
            workspace_id=resolved_workspace_id,
            action=action,
            response_text=response_text,
            profile=profile,
            run_id=run_id,
            extra_metadata={"link_id": active_link.get("link_id")},
        )
        return {
            "handled": True,
            "processed": True,
            "action": action,
            "run_id": run_id,
            "workspace_id": resolved_workspace_id,
            "reply_text": response_text,
        }

    def _record_inbound_event(
        self,
        *,
        envelope: Dict[str, Any],
        workspace_id: str,
        action: str,
        profile: Dict[str, Any],
        extra_metadata: Optional[Dict[str, Any]] = None,
    ) -> str:
        event = self.record_channel_event(
            channel="whatsapp",
            direction="inbound",
            event_type="message",
            text=self._event_text(envelope.get("message_text")),
            workspace_id=workspace_id,
            session_key=str(envelope.get("session_key") or ""),
            session_id=str(envelope.get("session_key") or ""),
            message_id=str(envelope.get("message_sid") or "") or None,
            action=str(action or "message").strip().lower() or "message",
            metadata=self._event_metadata(
                {
                    "connector_id": str(envelope.get("connector_id") or "").strip() or None,
                    "profile_id": profile.get("id"),
                    "account_sid": str(envelope.get("account_sid") or "").strip() or None,
                    "message_sid": str(envelope.get("message_sid") or "").strip() or None,
                    "to_number": str(envelope.get("to_number") or "").strip() or None,
                    "trace_id": str(envelope.get("trace_id") or "").strip() or None,
                    "delivery_status": "received",
                    **dict(extra_metadata or {}),
                }
            ),
        )
        return str((event or {}).get("id") or "").strip()

    def _finalize_processed_message(
        self,
        *,
        envelope: Dict[str, Any],
        workspace_id: str,
        action: str,
        response_text: str,
        profile: Dict[str, Any],
        run_id: str,
        extra_metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        connector_id = str(envelope.get("connector_id") or "").strip()
        message_sid = str(envelope.get("message_sid") or "").strip()
        from_number = str(envelope.get("from_number") or "").strip()
        to_number = str(envelope.get("to_number") or "").strip()
        state_patch: Dict[str, Any] = {
            "label": (envelope.get("entry") if isinstance(envelope.get("entry"), dict) else {}).get("label"),
            "workspace_id": workspace_id,
            "profile_id": profile.get("id"),
            "last_action": action,
            "last_error": None,
            "last_error_category": None,
            "last_error_at": None,
            "last_message_sid": message_sid or None,
            "last_from_number": from_number or None,
            "last_to_number": to_number or None,
            "last_processed_at": self.utc_now_iso(),
        }
        if run_id:
            state_patch["last_run_id"] = run_id
        self.set_connector_state(connector_id, state_patch)
        self.increment_processed()
        self.persist_state()
        if response_text:
            self.record_channel_event(
                channel="whatsapp",
                direction="outbound",
                event_type="message",
                text=self._event_text(response_text),
                workspace_id=workspace_id,
                session_key=str(envelope.get("session_key") or ""),
                run_id=run_id or "",
                action=str(action or "message").strip().lower() or "message",
                metadata=self._event_metadata(
                    {
                        "connector_id": connector_id or None,
                        "profile_id": profile.get("id"),
                        "message_sid": message_sid or None,
                        "trace_id": str(envelope.get("trace_id") or "").strip() or None,
                        "delivery_status": "sent",
                        **dict(extra_metadata or {}),
                    }
                ),
            )

    def _public_command_action(self, profile: Dict[str, Any], raw_message_text: str) -> Optional[Dict[str, Any]]:
        return _privacy_service().public_command_action(profile, raw_message_text)

    def _build_public_profile(
        self,
        *,
        entry: Dict[str, Any],
        profile: Dict[str, Any],
        workspace_id: str,
        connector_id: str,
        fallback_endpoint: str,
    ) -> Dict[str, Any]:
        metadata = entry.get("metadata") if isinstance(entry.get("metadata"), dict) else {}
        deployed_agent = metadata.get("deployed_agent") if isinstance(metadata.get("deployed_agent"), dict) else {}
        bindings = metadata.get("channel_registry_bindings") if isinstance(metadata.get("channel_registry_bindings"), dict) else {}
        whatsapp_binding = bindings.get("whatsapp") if isinstance(bindings.get("whatsapp"), dict) else {}
        payload = dict(profile or {})
        payload.setdefault("tenant_id", self._resolve_tenant_id(entry=entry, workspace_id=workspace_id))
        payload.setdefault("workspace_id", workspace_id)
        payload.setdefault(
            "endpoint_key",
            self._channel_endpoint_key(
                entry=entry,
                connector_id=connector_id,
                fallback=str(whatsapp_binding.get("endpoint_key") or fallback_endpoint or ""),
            ),
        )
        payload.setdefault(
            "deployed_agent_id",
            str(
                metadata.get("deployed_agent_id")
                or deployed_agent.get("id")
                or whatsapp_binding.get("deployed_agent_id")
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

    def _is_public_deployed_agent_traffic(self, *, entry: Dict[str, Any], profile: Dict[str, Any]) -> bool:
        if str(profile.get("deployed_agent_id") or "").strip():
            return True
        metadata = entry.get("metadata") if isinstance(entry.get("metadata"), dict) else {}
        if str(metadata.get("source") or "").strip().lower() == "deployed_agent":
            return True
        bindings = metadata.get("channel_registry_bindings") if isinstance(metadata.get("channel_registry_bindings"), dict) else {}
        binding = bindings.get("whatsapp") if isinstance(bindings.get("whatsapp"), dict) else {}
        return bool(str(binding.get("deployed_agent_id") or "").strip())

    def _channel_endpoint_key(
        self,
        *,
        entry: Dict[str, Any],
        connector_id: str,
        fallback: str = "",
    ) -> str:
        metadata = entry.get("metadata") if isinstance(entry.get("metadata"), dict) else {}
        bindings = metadata.get("channel_registry_bindings") if isinstance(metadata.get("channel_registry_bindings"), dict) else {}
        binding = bindings.get("whatsapp") if isinstance(bindings.get("whatsapp"), dict) else {}
        candidates = (
            binding.get("endpoint_key"),
            metadata.get("whatsapp_endpoint_key"),
            fallback,
            connector_id,
        )
        for candidate in candidates:
            token = str(candidate or "").strip()
            if token:
                return token
        return connector_id

    def _resolve_tenant_id(self, *, entry: Dict[str, Any], workspace_id: str) -> str:
        metadata = entry.get("metadata") if isinstance(entry.get("metadata"), dict) else {}
        for candidate in (entry.get("tenant_id"), metadata.get("tenant_id")):
            token = str(candidate or "").strip()
            if token:
                return token
        workspace = _workspace_payload(workspace_id)
        return str(workspace.get("tenant_id") or "default").strip() or "default"
