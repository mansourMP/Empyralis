from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional


class TelegramConnectorPollService:
    def __init__(
        self,
        *,
        default_workspace_id: str,
        normalize_workspace_id: Callable[[Any], str],
        resolve_workspace_scope: Callable[[Dict[str, Any], str | None, str], str],
        resolve_profile: Callable[[Dict[str, Any]], Dict[str, Any]],
        resolve_allow_from: Callable[[Dict[str, Any]], List[str]],
        connector_state: Callable[[str], Dict[str, Any]],
        resolve_secret: Callable[[Dict[str, Any]], Dict[str, Any]],
        poll_cycle_service: Callable[[], Any],
        inbound_context_service: Callable[[], Any],
        poll_dispatch_service: Callable[[], Any],
        poll_state_service: Callable[[], Any],
        ingress_service: Optional[Callable[[], Any]] = None,
    ) -> None:
        self.default_workspace_id = str(default_workspace_id or "default").strip() or "default"
        self.normalize_workspace_id = normalize_workspace_id
        self.resolve_workspace_scope = resolve_workspace_scope
        self.resolve_profile = resolve_profile
        self.resolve_allow_from = resolve_allow_from
        self.connector_state = connector_state
        self.resolve_secret = resolve_secret
        self.poll_cycle_service = poll_cycle_service
        self.ingress_service = ingress_service
        self.inbound_context_service = inbound_context_service
        self.poll_dispatch_service = poll_dispatch_service
        self.poll_state_service = poll_state_service

    def poll_connector(self, entry: Dict[str, Any]) -> None:
        connector_id = str(entry.get("id") or "").strip()
        if not connector_id:
            return
        label = str(entry.get("label") or connector_id)
        workspace_id = ""

        try:
            workspace_id = self.resolve_workspace_scope(entry, self.default_workspace_id, "Telegram connector")
            profile = self.resolve_profile(entry)
            allow_from = self.resolve_allow_from(entry)
            connector_state = self.connector_state(connector_id)
            last_update_id = int(connector_state.get("last_update_id") or 0)
            secret = self.resolve_secret(entry)
            bot_token = str(secret.get("bot_token") or "").strip()
            configured_chat_id = str(secret.get("chat_id") or "").strip()
            if not bot_token or not configured_chat_id:
                raise RuntimeError("Connector is missing bot_token or chat_id.")

            poll_begin = self.poll_cycle_service().begin_poll(
                connector_state=connector_state,
                bot_token=bot_token,
                configured_chat_id=configured_chat_id,
                workspace_id=workspace_id,
                profile=profile,
                connector_id=connector_id,
                label=label,
                last_update_id=last_update_id,
            )
            if bool(poll_begin.get("skipped")):
                return

            approval_state_patch = (
                poll_begin.get("approval_state_patch")
                if isinstance(poll_begin.get("approval_state_patch"), dict)
                else {}
            )
            updates = poll_begin.get("updates") if isinstance(poll_begin.get("updates"), list) else []

            max_seen = last_update_id
            for update in updates:
                if not isinstance(update, dict):
                    continue
                update_id = int(update.get("update_id") or 0)
                if update_id <= max_seen:
                    continue
                max_seen = update_id
                if self.ingress_service is not None:
                    dispatch_result = self.ingress_service().ingest_update(
                        source="polling",
                        entry=entry,
                        update=update,
                    )
                    if not bool(dispatch_result.get("processed")):
                        continue
                    continue
                extracted_message = self.inbound_context_service().extract_inbound_message(
                    update=update,
                    configured_chat_id=configured_chat_id,
                )
                update_id = int(extracted_message.get("update_id") or 0)
                if not bool(extracted_message.get("handled")):
                    continue
                dispatch_result = self.poll_dispatch_service().handle_update(
                    entry=entry,
                    label=label,
                    workspace_id=workspace_id,
                    profile=profile,
                    allow_from=list(allow_from),
                    connector_state=connector_state,
                    connector_id=connector_id,
                    bot_token=bot_token,
                    configured_chat_id=configured_chat_id,
                    extracted_message=extracted_message,
                    update_id=update_id,
                )
                if not bool(dispatch_result.get("processed")):
                    continue
                chat_id = str(dispatch_result.get("chat_id") or "").strip()
                action = str(dispatch_result.get("action") or "").strip().lower()
                run_id = str(dispatch_result.get("run_id") or "")

                self.poll_state_service().record_processed_update(
                    connector_id=connector_id,
                    label=label,
                    workspace_id=workspace_id,
                    update_id=update_id,
                    chat_id=chat_id,
                    action=action,
                    profile_id=str(profile.get("id") or ""),
                    allow_from=list(allow_from),
                    run_id=run_id,
                    approval_state_patch=approval_state_patch,
                )

            self.poll_cycle_service().complete_poll(
                connector_id=connector_id,
                label=label,
                workspace_id=workspace_id,
                max_seen=max_seen,
                last_update_id=last_update_id,
                profile_id=str(profile.get("id") or ""),
                allow_from=list(allow_from),
                approval_state_patch=approval_state_patch,
            )
        except Exception as exc:
            self.poll_cycle_service().handle_connector_error(
                connector_id=connector_id,
                label=label,
                workspace_id=workspace_id,
                detail=str(exc),
            )
            raise
