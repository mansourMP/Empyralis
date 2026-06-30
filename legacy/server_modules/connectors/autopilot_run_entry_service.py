from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional

from server_modules.agent_turn import (
    AgentTurnRequest,
    bind_agent_turn_metadata,
    build_telegram_turn_request,
    build_whatsapp_turn_request,
)
from server_modules.run_service import build_run_start_request_from_turn


class AutopilotRunEntryService:
    def __init__(
        self,
        *,
        telegram_profile_fields: List[str],
        telegram_engine: str,
        whatsapp_engine: str,
        safe_path_token: Callable[[Any], str],
        assigned_agent_role: Callable[[Dict[str, Any]], str],
        normalize_trust_mode: Callable[[str], str],
        normalize_execution_target: Callable[[str], str],
        decide_execution_target: Callable[[Dict[str, Any]], Any],
        apply_execution_route_metadata: Callable[[Dict[str, Any], Any], Dict[str, Any]],
        execute_agent_turn_request: Optional[Callable[..., Dict[str, Any]]] = None,
        route_transport_channel_message: Optional[Callable[..., Dict[str, Any]]] = None,
        run_start_request_class: Optional[Callable[..., Any]] = None,
        start_run_request: Optional[Callable[[Any], Dict[str, Any]]] = None,
        create_run: Optional[Callable[..., str]] = None,
        record_channel_event: Callable[..., Any],
        telegram_session_key: Callable[[str], str],
        whatsapp_session_key: Callable[[str, str], str],
        inherit_owner_user_id: Callable[[Optional[str]], str],
        agent_machine_full_trust_enabled: Callable[[str], bool],
        telegram_runs_started: Callable[[], None],
        whatsapp_runs_started: Callable[[], None],
    ) -> None:
        self.telegram_profile_fields = list(telegram_profile_fields)
        self.telegram_engine = str(telegram_engine or "orion").strip() or "orion"
        self.whatsapp_engine = str(whatsapp_engine or "orion").strip() or "orion"
        self.safe_path_token = safe_path_token
        self.assigned_agent_role = assigned_agent_role
        self.normalize_trust_mode = normalize_trust_mode
        self.normalize_execution_target = normalize_execution_target
        self.decide_execution_target = decide_execution_target
        self.apply_execution_route_metadata = apply_execution_route_metadata
        self.execute_agent_turn_request = execute_agent_turn_request
        self.route_transport_channel_message = route_transport_channel_message
        self.run_start_request_class = run_start_request_class
        self.start_run_request = start_run_request
        self.create_run = create_run
        self.record_channel_event = record_channel_event
        self.telegram_session_key = telegram_session_key
        self.whatsapp_session_key = whatsapp_session_key
        self.inherit_owner_user_id = inherit_owner_user_id
        self.agent_machine_full_trust_enabled = agent_machine_full_trust_enabled
        self.telegram_runs_started = telegram_runs_started
        self.whatsapp_runs_started = whatsapp_runs_started

    def full_trust_for_run(self, run: Dict[str, Any]) -> bool:
        context = run.get("context") if isinstance(run.get("context"), dict) else {}
        metadata = context.get("metadata") if isinstance(context.get("metadata"), dict) else {}
        return self.agent_machine_full_trust_enabled(str(metadata.get("owner_user_id") or "").strip())

    def pending_confirmation_payload(self, run: Dict[str, Any]) -> Dict[str, Any]:
        pending = run.get("pending_confirmation")
        if isinstance(pending, dict) and pending:
            return pending
        pending = run.get("pending_approval")
        if isinstance(pending, dict):
            return pending
        return {}

    def can_auto_approve_wait(self, run: Dict[str, Any]) -> bool:
        return False

    def _connector_metadata(self, connector_entry: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        return connector_entry.get("metadata") if isinstance((connector_entry or {}).get("metadata"), dict) else {}

    def _channel_endpoint_key(
        self,
        *,
        channel_key: str,
        connector_id: str,
        connector_entry: Optional[Dict[str, Any]],
        fallback: Optional[str] = None,
    ) -> str:
        metadata = self._connector_metadata(connector_entry)
        bindings = metadata.get("channel_registry_bindings") if isinstance(metadata.get("channel_registry_bindings"), dict) else {}
        binding = bindings.get(channel_key) if isinstance(bindings.get(channel_key), dict) else {}
        candidates = (
            binding.get("endpoint_key"),
            metadata.get(f"{channel_key}_endpoint_key"),
            fallback,
            connector_id,
        )
        for candidate in candidates:
            token = str(candidate or "").strip()
            if token:
                return token
        return str(channel_key or "").strip() or "channel"

    def _canonical_owner_install(
        self,
        *,
        channel_key: str,
        connector_entry: Optional[Dict[str, Any]],
        metadata: Dict[str, Any],
        assigned_role: str,
        owner_user_id: str,
    ) -> Dict[str, Any]:
        entry = connector_entry or {}
        connector_metadata = self._connector_metadata(connector_entry)
        runtime_profile = metadata.get("runtime_profile") if isinstance(metadata.get("runtime_profile"), dict) else {}
        runtime_profile_id = str(
            metadata.get("runtime_profile_id")
            or runtime_profile.get("id")
            or connector_metadata.get("runtime_profile_id")
            or ""
        ).strip()
        install_metadata = {
            "agent_role": assigned_role or str(metadata.get("agent_role") or connector_metadata.get("agent_role") or "").strip() or None,
        }
        return {
            "id": str(
                metadata.get("active_agent_install_id")
                or metadata.get("workspace_agent_install_id")
                or connector_metadata.get("active_agent_install_id")
                or connector_metadata.get("workspace_agent_install_id")
                or entry.get("agent_install_id")
                or entry.get("id")
                or ""
            ).strip()
            or None,
            "label": str(entry.get("label") or assigned_role or channel_key.title() or "Agent").strip() or "Agent",
            "owner_user_id": owner_user_id or None,
            "owner_email": str(metadata.get("owner_email") or connector_metadata.get("owner_email") or "").strip().lower() or None,
            "runtime_mode": str(metadata.get("runtime_mode") or connector_metadata.get("runtime_mode") or "hosted_secure").strip().lower() or "hosted_secure",
            "runtime_profile_id": runtime_profile_id or None,
            "runtime_profile": {
                **(runtime_profile if isinstance(runtime_profile, dict) else {}),
                "id": runtime_profile_id or runtime_profile.get("id") or None,
                "label": str(runtime_profile.get("label") or metadata.get("runtime_profile_label") or connector_metadata.get("runtime_profile_label") or "").strip() or None,
                "runtime_id": str(runtime_profile.get("runtime_id") or metadata.get("runtime_id") or "").strip() or None,
                "machine_id": str(runtime_profile.get("machine_id") or metadata.get("machine_id") or "").strip() or None,
            },
            "metadata": install_metadata,
        }

    def _create_canonical_channel_run(
        self,
        *,
        tenant_id: str,
        workspace_id: str,
        channel_key: str,
        endpoint_key: str,
        goal: str,
        message_id: Optional[str],
        actor_id: str,
        actor_display_name: str,
        metadata: Dict[str, Any],
        owner_install: Dict[str, Any],
        owner_type: str,
    ) -> Optional[Dict[str, Any]]:
        if not callable(self.route_transport_channel_message):
            return None
        from server_modules import runtime_config

        if (
            str(getattr(runtime_config, "AGENT_MACHINE_MODE", "personal") or "").strip().lower() == "agent"
            and callable(self.start_run_request)
            and callable(self.run_start_request_class)
        ):
            return None
        result = self.route_transport_channel_message(
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            channel_key=channel_key,
            endpoint_key=endpoint_key,
            customer_message=goal,
            actor_id=actor_id,
            actor_display_name=actor_display_name,
            message_id=message_id,
            metadata=metadata,
            install=owner_install,
            manifest=None,
            owner_type=owner_type,
            master_install_id=str(
                metadata.get("master_agent_install_id")
                or metadata.get("active_agent_install_id")
                or owner_install.get("id")
                or ""
            ).strip()
            or None,
            runtime_mode=str(owner_install.get("runtime_mode") or "").strip() or None,
            runtime_profile_id=str(
                metadata.get("runtime_profile_id")
                or ((owner_install.get("runtime_profile") or {}) if isinstance(owner_install.get("runtime_profile"), dict) else {}).get("id")
                or ""
            ).strip()
            or None,
            privileged_runtime_approved=bool(metadata.get("privileged_runtime_approved")),
        )
        if not isinstance(result, dict):
            return {"result": result}
        route = result.get("metadata") if isinstance(result.get("metadata"), dict) else {}
        return {
            "run_id": str(result.get("run_id") or "").strip() or None,
            "route": route.get("route") if isinstance(route.get("route"), dict) else None,
            "result": result,
        }

    def create_telegram_run(
        self,
        *,
        goal: str,
        workspace_id: str,
        connector_id: str,
        chat_id: str,
        sender_id: str,
        update_id: int,
        message_id: Optional[str] = None,
        profile_context: Optional[Dict[str, str]] = None,
        media_attachments: Optional[List[Dict[str, Any]]] = None,
        skill_override: Optional[Dict[str, Any]] = None,
        trace_id: Optional[str] = None,
        source_event_id: Optional[str] = None,
        connector_entry: Optional[Dict[str, Any]] = None,
        connector_context: Optional[Dict[str, Any]] = None,
        media_max_items: int = 4,
        trust_mode_value: str = "",
        execution_target_value: str = "",
    ) -> Dict[str, Any]:
        resolved_trace_id = str(trace_id or "").strip() or (
            f"tg:{self.safe_path_token(chat_id)}:{self.safe_path_token(update_id)}:{self.safe_path_token(message_id or '')}".rstrip(":")
        )
        metadata: Dict[str, Any] = {
            "source": "telegram_autopilot",
            "channel": "telegram",
            "source_channel": "telegram",
            "connector_credential_id": connector_id,
            "source_connector_credential_id": connector_id,
            "trace_id": resolved_trace_id,
            "source_event_id": str(source_event_id or "").strip(),
            "delivery_status": "pending",
            "telegram": {
                "chat_id": chat_id,
                "sender_id": sender_id,
                "update_id": update_id,
                "message_id": str(message_id or "").strip(),
            },
        }
        owner_user_id = self.inherit_owner_user_id(None)
        if owner_user_id:
            metadata["owner_user_id"] = owner_user_id
        if isinstance(connector_context, dict):
            available_connectors = connector_context.get("available_connectors")
            if isinstance(available_connectors, list) and available_connectors:
                metadata["available_connectors"] = available_connectors
            channel_connectors = connector_context.get("channel_connectors")
            if isinstance(channel_connectors, list) and channel_connectors:
                metadata["channel_connectors"] = channel_connectors
            selected_connector_id = str(connector_context.get("connector_credential_id") or "").strip()
            if selected_connector_id:
                metadata["connector_credential_id"] = selected_connector_id
            selected_connector_provider = str(connector_context.get("connector_provider") or "").strip()
            if selected_connector_provider:
                metadata["connector_provider"] = selected_connector_provider
            connector_prompt_append = str(connector_context.get("prompt_append") or "").strip()
            if connector_prompt_append:
                metadata["connector_prompt_append"] = connector_prompt_append[:12000]
        if isinstance(profile_context, dict):
            profile_payload: Dict[str, str] = {}
            for field_name in self.telegram_profile_fields:
                raw_value = str(profile_context.get(field_name) or "").strip()
                if raw_value:
                    profile_payload[field_name] = raw_value[:2000]
            if profile_payload:
                metadata["telegram"]["profile_context"] = profile_payload
        if isinstance(media_attachments, list) and media_attachments:
            compact_media: List[Dict[str, Any]] = []
            for item in media_attachments[: max(1, int(media_max_items or 4))]:
                if not isinstance(item, dict):
                    continue
                path = str(item.get("relative_path") or item.get("path") or "").strip()
                if not path:
                    continue
                compact_media.append(
                    {
                        "kind": str(item.get("kind") or "").strip(),
                        "mime_type": str(item.get("mime_type") or "").strip(),
                        "path": path[:2000],
                        "bytes": int(item.get("bytes") or 0),
                    }
                )
            if compact_media:
                metadata["telegram"]["attachments"] = compact_media
        if isinstance(skill_override, dict):
            skill_id = str(skill_override.get("id") or "").strip().lower()
            skill_title = str(skill_override.get("title") or "").strip()
            skill_intent = str(skill_override.get("intent") or "").strip()
            if skill_id and skill_title and skill_intent:
                tools_raw = skill_override.get("tools") if isinstance(skill_override.get("tools"), list) else []
                tools = [str(item).strip()[:120] for item in tools_raw if str(item).strip()][:30]
                guardrail = str(skill_override.get("guardrail") or "").strip()[:1000]
                skill_payload = {
                    "id": skill_id[:80],
                    "title": skill_title[:120],
                    "intent": skill_intent[:1200],
                    "tools": tools,
                    "guardrail": guardrail,
                }
                metadata["skill_scope"] = "assistant_defaults"
                metadata["skill_bundle"] = {
                    "skill_ids": [skill_payload["id"]],
                    "skills": [skill_payload],
                }
                metadata["skill_prompt_append"] = (
                    "Active skill directives (follow unless user overrides explicitly):\n"
                    f"- {skill_payload['title']}: {skill_payload['intent']} "
                    f"Guardrail: {skill_payload['guardrail'] or 'none'}. "
                    f"Tools: {', '.join(skill_payload['tools']) or 'none'}."
                )[:6000]
        assigned_role = self.assigned_agent_role(connector_entry or {})
        if assigned_role:
            metadata["agent_role"] = assigned_role
            metadata["agent_role_source"] = "connector_assignment"
        metadata["trust_mode"] = self.normalize_trust_mode(trust_mode_value)
        metadata["execution_target"] = self.normalize_execution_target(execution_target_value)
        connector_metadata = self._connector_metadata(connector_entry)
        for field_name in (
            "tenant_id",
            "active_agent_install_id",
            "workspace_agent_install_id",
            "master_agent_install_id",
            "runtime_profile_id",
        ):
            token = str(connector_metadata.get(field_name) or "").strip()
            if token and not str(metadata.get(field_name) or "").strip():
                metadata[field_name] = token
        tenant_id = str(metadata.get("tenant_id") or connector_metadata.get("tenant_id") or "default").strip() or "default"
        endpoint_key = self._channel_endpoint_key(
            channel_key="telegram",
            connector_id=connector_id,
            connector_entry=connector_entry,
            fallback=str(connector_metadata.get("configured_chat_id") or "").strip() or None,
        )
        owner_install = self._canonical_owner_install(
            channel_key="telegram",
            connector_entry=connector_entry,
            metadata=metadata,
            assigned_role=assigned_role,
            owner_user_id=owner_user_id,
        )
        created = self._create_canonical_channel_run(
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            channel_key="telegram",
            endpoint_key=endpoint_key,
            goal=goal,
            message_id=message_id,
            actor_id=str(sender_id or chat_id or "telegram-user").strip() or "telegram-user",
            actor_display_name=str(sender_id or chat_id or "telegram-user").strip() or "telegram-user",
            metadata=metadata,
            owner_install=owner_install,
            owner_type="specialist",
        )
        if created is None:
            turn_request = build_telegram_turn_request(
                workspace_id=workspace_id,
                connector_entry=connector_entry,
                goal=goal,
                chat_id=chat_id,
                sender_id=sender_id,
                update_id=update_id,
                message_id=message_id,
                trace_id=resolved_trace_id,
                source_event_id=str(source_event_id or "").strip(),
                metadata=metadata,
            )
            created = self._create_run_record(
                engine=self.telegram_engine,
                workspace_id=workspace_id,
                goal=goal,
                metadata=metadata,
                turn_request=turn_request,
            )
        run_id = str(created.get("run_id") or "").strip()
        route = created.get("route") if isinstance(created.get("route"), dict) else {
            "selected": str(metadata.get("execution_target_selected") or metadata.get("execution_target") or "").strip() or None
        }
        result_payload = created.get("result") if isinstance(created.get("result"), dict) else {}
        if not run_id:
            return {
                "run_id": "",
                "route": route,
                "status": str(result_payload.get("status") or "failed").strip().lower() or "failed",
                "reply": str(result_payload.get("reply") or "").strip(),
            }
        self.telegram_runs_started()
        self.record_channel_event(
            channel="telegram",
            direction="system",
            event_type="run_started",
            text=f"Run started for chat {chat_id}",
            workspace_id=workspace_id,
            session_key=str(
                ((created.get("result") or {}) if isinstance(created.get("result"), dict) else {}).get("session_key")
                or self.telegram_session_key(chat_id)
            ),
            session_id=str(
                ((created.get("result") or {}) if isinstance(created.get("result"), dict) else {}).get("session_key")
                or self.telegram_session_key(chat_id)
            ),
            parent_id=str(message_id or "").strip() or None,
            run_id=run_id,
            action="run",
            metadata={
                "connector_id": connector_id,
                "sender_id": sender_id,
                "update_id": int(update_id or 0),
                "message_id": str(message_id or "").strip(),
                "trace_id": resolved_trace_id,
                "source_event_id": str(source_event_id or "").strip(),
                "delivery_status": "pending",
                "agent_role": assigned_role or None,
            },
        )
        return {"run_id": run_id, "route": route}

    def create_whatsapp_run(
        self,
        *,
        goal: str,
        workspace_id: str,
        connector_id: str,
        from_number: str,
        to_number: str,
        message_sid: str,
        account_sid: str,
        connector_entry: Optional[Dict[str, Any]] = None,
        trust_mode_value: str = "",
        execution_target_value: str = "",
    ) -> Dict[str, Any]:
        trace_id = f"wa:{self.safe_path_token(to_number or 'to')}:{self.safe_path_token(message_sid or 'msg')}"
        metadata: Dict[str, Any] = {
            "source": "whatsapp_autopilot",
            "channel": "whatsapp",
            "source_channel": "whatsapp",
            "connector_credential_id": connector_id,
            "trace_id": trace_id,
            "source_event_id": str(message_sid or "").strip(),
            "delivery_status": "pending",
            "whatsapp": {
                "from": from_number,
                "to": to_number,
                "message_sid": message_sid,
                "account_sid": account_sid,
            },
        }
        owner_user_id = self.inherit_owner_user_id(None)
        if owner_user_id:
            metadata["owner_user_id"] = owner_user_id
        assigned_role = self.assigned_agent_role(connector_entry or {})
        if assigned_role:
            metadata["agent_role"] = assigned_role
            metadata["agent_role_source"] = "connector_assignment"
        metadata["trust_mode"] = self.normalize_trust_mode(trust_mode_value)
        metadata["execution_target"] = self.normalize_execution_target(execution_target_value)
        connector_metadata = self._connector_metadata(connector_entry)
        for field_name in (
            "tenant_id",
            "active_agent_install_id",
            "workspace_agent_install_id",
            "master_agent_install_id",
            "runtime_profile_id",
        ):
            token = str(connector_metadata.get(field_name) or "").strip()
            if token and not str(metadata.get(field_name) or "").strip():
                metadata[field_name] = token
        tenant_id = str(metadata.get("tenant_id") or connector_metadata.get("tenant_id") or "default").strip() or "default"
        endpoint_key = self._channel_endpoint_key(
            channel_key="whatsapp",
            connector_id=connector_id,
            connector_entry=connector_entry,
            fallback=to_number,
        )
        owner_install = self._canonical_owner_install(
            channel_key="whatsapp",
            connector_entry=connector_entry,
            metadata=metadata,
            assigned_role=assigned_role,
            owner_user_id=owner_user_id,
        )
        created = self._create_canonical_channel_run(
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            channel_key="whatsapp",
            endpoint_key=endpoint_key,
            goal=goal,
            message_id=message_sid,
            actor_id=str(from_number or "whatsapp-user").strip() or "whatsapp-user",
            actor_display_name=str(from_number or "whatsapp-user").strip() or "whatsapp-user",
            metadata=metadata,
            owner_install=owner_install,
            owner_type="specialist",
        )
        if created is None:
            turn_request = build_whatsapp_turn_request(
                workspace_id=workspace_id,
                connector_entry=connector_entry,
                goal=goal,
                from_number=from_number,
                to_number=to_number,
                message_sid=message_sid,
                account_sid=account_sid,
                session_id=self.whatsapp_session_key(from_number, to_number),
                trace_id=trace_id,
                metadata=metadata,
            )
            created = self._create_run_record(
                engine=self.whatsapp_engine,
                workspace_id=workspace_id,
                goal=goal,
                metadata=metadata,
                turn_request=turn_request,
            )
        run_id = str(created.get("run_id") or "").strip()
        route = created.get("route") if isinstance(created.get("route"), dict) else {
            "selected": str(metadata.get("execution_target_selected") or metadata.get("execution_target") or "").strip() or None
        }
        result_payload = created.get("result") if isinstance(created.get("result"), dict) else {}
        if not run_id:
            return {
                "run_id": "",
                "route": route,
                "status": str(result_payload.get("status") or "failed").strip().lower() or "failed",
                "reply": str(result_payload.get("reply") or "").strip(),
            }
        self.whatsapp_runs_started()
        self.record_channel_event(
            channel="whatsapp",
            direction="system",
            event_type="run_started",
            text=f"Run started for WhatsApp inbound {from_number}",
            workspace_id=workspace_id,
            session_key=str(
                ((created.get("result") or {}) if isinstance(created.get("result"), dict) else {}).get("session_key")
                or self.whatsapp_session_key(from_number, to_number)
            ),
            session_id=str(
                ((created.get("result") or {}) if isinstance(created.get("result"), dict) else {}).get("session_key")
                or self.whatsapp_session_key(from_number, to_number)
            ),
            parent_id=str(message_sid or "").strip() or None,
            run_id=run_id,
            action="run",
            metadata={
                "connector_id": connector_id,
                "message_sid": message_sid,
                "account_sid": account_sid,
                "to_number": to_number,
                "trace_id": trace_id,
                "source_event_id": str(message_sid or "").strip(),
                "agent_role": assigned_role or None,
                "delivery_status": "pending",
            },
        )
        return {"run_id": run_id, "route": route}

    def _apply_route_metadata(self, metadata: Dict[str, Any]) -> Any:
        route = self.decide_execution_target(metadata)
        updated = self.apply_execution_route_metadata(metadata, route)
        updated_dict = dict(updated) if isinstance(updated, dict) else dict(metadata)
        metadata.clear()
        metadata.update(updated_dict)
        return route

    def _create_run_record(
        self,
        *,
        engine: str,
        workspace_id: str,
        goal: str,
        metadata: Dict[str, Any],
        turn_request: AgentTurnRequest,
    ) -> Dict[str, Any]:
        if callable(self.execute_agent_turn_request):
            result = self.execute_agent_turn_request(turn_request=turn_request)
            if isinstance(result, dict) and str(result.get("run_id") or "").strip():
                return dict(result)
            if result is not None:
                return {"result": result}

        if callable(self.start_run_request) and callable(self.run_start_request_class):
            base_request = self.run_start_request_class(
                engine=engine,
                workspace_id=workspace_id,
                user_goal=goal,
                metadata=metadata,
            )
            request = build_run_start_request_from_turn(turn_request, base_request=base_request)
            result = self.start_run_request(request)
            if isinstance(result, dict) and str(result.get("run_id") or "").strip():
                return dict(result)
            if result is not None:
                return {"result": result}

        raise RuntimeError(
            "Autopilot run entry requires canonical ingress callbacks. "
            "Configure route_transport_channel_message, execute_agent_turn_request, or start_run_request."
        )
