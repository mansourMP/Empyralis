from __future__ import annotations

import json
from typing import Any, Callable, Dict, List


class TelegramWebhookService:
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
        inbound_context_service: Callable[[], Any],
        poll_dispatch_service: Callable[[], Any],
        poll_state_service: Callable[[], Any],
        poll_cycle_service: Callable[[], Any],
    ) -> None:
        self.default_workspace_id = str(default_workspace_id or "default").strip() or "default"
        self.resolve_workspace_scope = resolve_workspace_scope
        self.get_connector_entry = get_connector_entry
        self.resolve_profile = resolve_profile
        self.resolve_allow_from = resolve_allow_from
        self.connector_state = connector_state
        self.resolve_secret = resolve_secret
        self.inbound_context_service = inbound_context_service
        self.poll_dispatch_service = poll_dispatch_service
        self.poll_state_service = poll_state_service
        self.poll_cycle_service = poll_cycle_service

    def parse_update(self, raw_body: bytes) -> Dict[str, Any]:
        try:
            decoded = raw_body.decode("utf-8", errors="strict") if raw_body else "{}"
            payload = json.loads(decoded)
        except Exception as exc:
            raise ValueError(f"Telegram webhook payload is invalid JSON: {exc}") from exc
        if not isinstance(payload, dict):
            raise ValueError("Telegram webhook payload must be a JSON object.")
        return payload

    def handle_webhook(self, *, connector_id: str, update: Dict[str, Any]) -> Dict[str, Any]:
        connector_token = str(connector_id or "").strip()
        if not connector_token:
            raise LookupError("Telegram connector id is required.")

        entry = self.get_connector_entry(connector_token)
        label = str(entry.get("label") or connector_token).strip() or connector_token
        workspace_id = ""
        try:
            workspace_id = self.resolve_workspace_scope(
                entry,
                self.default_workspace_id,
                "Telegram connector",
            )
            profile = self.resolve_profile(entry)
            allow_from = self.resolve_allow_from(entry)
            connector_state = self.connector_state(connector_token)
            last_update_id = int(connector_state.get("last_update_id") or 0)
            secret = self.resolve_secret(entry)
            bot_token = str(secret.get("bot_token") or "").strip()
            configured_chat_id = str(secret.get("chat_id") or "").strip()
            if not bot_token or not configured_chat_id:
                raise RuntimeError("Connector is missing bot_token or chat_id.")

            extracted_message = self.inbound_context_service().extract_inbound_message(
                update=update,
                configured_chat_id=configured_chat_id,
            )
            update_id = int(extracted_message.get("update_id") or int(update.get("update_id") or 0))
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

            profile_id = str(profile.get("id") or "")
            if not bool(extracted_message.get("handled")):
                if update_id > 0:
                    self.poll_state_service().record_poll_completion(
                        connector_id=connector_token,
                        label=label,
                        workspace_id=workspace_id,
                        max_seen=update_id,
                        last_update_id=last_update_id,
                        profile_id=profile_id,
                        allow_from=list(allow_from),
                        approval_state_patch={},
                    )
                return {
                    "handled": False,
                    "processed": False,
                    "reason": str(extracted_message.get("reason") or "ignored"),
                    "update_id": update_id,
                    "connector_id": connector_token,
                    "workspace_id": workspace_id,
                }

            dispatch_result = self.poll_dispatch_service().handle_update(
                entry=entry,
                label=label,
                workspace_id=workspace_id,
                profile=profile,
                allow_from=list(allow_from),
                connector_state=connector_state,
                connector_id=connector_token,
                bot_token=bot_token,
                configured_chat_id=configured_chat_id,
                extracted_message=extracted_message,
                update_id=update_id,
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
                    last_update_id=last_update_id,
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
