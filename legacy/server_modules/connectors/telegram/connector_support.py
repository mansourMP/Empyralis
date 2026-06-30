from __future__ import annotations

import re
from typing import Any, Callable, Dict, List


class TelegramConnectorSupportService:
    def __init__(
        self,
        *,
        normalize_workspace_id: Callable[[Any], str],
        resolve_vault_credential: Callable[[str, str], Dict[str, Any]],
        normalize_agent_role: Callable[[Any], str],
        allow_any_chat: bool,
    ) -> None:
        self.normalize_workspace_id = normalize_workspace_id
        self.resolve_vault_credential = resolve_vault_credential
        self.normalize_agent_role = normalize_agent_role
        self.allow_any_chat = bool(allow_any_chat)

    def bool_from_any(self, value: Any, default: bool = False) -> bool:
        if value is None:
            return default
        if isinstance(value, bool):
            return value
        raw = str(value).strip().lower()
        if raw in {"1", "true", "yes", "y", "on"}:
            return True
        if raw in {"0", "false", "no", "n", "off"}:
            return False
        return default

    def connector_metadata(self, entry: Dict[str, Any]) -> Dict[str, Any]:
        return entry.get("metadata") if isinstance(entry.get("metadata"), dict) else {}

    def connector_assigned_agent_role(self, entry: Dict[str, Any]) -> str:
        metadata = self.connector_metadata(entry)
        try:
            return str(self.normalize_agent_role(metadata.get("agent_role")) or "").strip().lower()
        except Exception:
            return str(metadata.get("agent_role") or "").strip().lower()

    def connector_paused(self, entry: Dict[str, Any]) -> bool:
        return self.bool_from_any(self.connector_metadata(entry).get("paused"), False)

    def get_secret(self, entry: Dict[str, Any]) -> Dict[str, Any]:
        credential_id = str(entry.get("id") or "").strip()
        workspace_id = self.normalize_workspace_id(entry.get("workspace_id"))
        if not credential_id:
            raise RuntimeError("Connector entry is missing id.")
        return self.resolve_vault_credential(credential_id, workspace_id)

    def chat_matches(self, configured_chat_id: str, chat: Dict[str, Any]) -> bool:
        if not isinstance(chat, dict):
            return False
        if self.allow_any_chat:
            return bool(str(chat.get("id") or chat.get("username") or "").strip())
        expected = str(configured_chat_id or "").strip()
        if not expected:
            return False
        if expected.lower() in {"*", "any", "all"}:
            return True
        chat_id = str(chat.get("id") or "").strip()
        chat_username = str(chat.get("username") or "").strip().lower()
        if expected.startswith("@"):
            return chat_username == expected[1:].lower()
        return chat_id == expected

    def parse_allow_from(self, value: Any) -> List[str]:
        tokens: List[str] = []
        if isinstance(value, list):
            raw_items = value
        elif isinstance(value, str):
            raw_items = value.split(",")
        else:
            raw_items = []
        for item in raw_items:
            token = str(item or "").strip().lower()
            if token:
                tokens.append(token)
        out: List[str] = []
        for token in tokens:
            normalized = token
            if normalized.startswith("id:"):
                normalized = normalized[3:].strip()
            elif normalized.startswith("user:"):
                normalized = f"@{normalized[5:].strip()}"
            if not normalized:
                continue
            if normalized in {"*", "any", "all"}:
                return ["*"]
            if normalized.startswith("@"):
                normalized = f"@{normalized[1:].strip().lower()}"
                if normalized == "@":
                    continue
            elif re.fullmatch(r"-?\d+", normalized):
                pass
            else:
                normalized = f"@{normalized}"
            if normalized and normalized not in out:
                out.append(normalized)
        return out

    def resolve_allow_from(self, entry: Dict[str, Any], env_value: str = "") -> List[str]:
        metadata = self.connector_metadata(entry)
        merged: List[str] = []
        for candidate in (
            metadata.get("allow_from"),
            metadata.get("telegram_allow_from"),
            metadata.get("autopilot_allow_from"),
            env_value,
        ):
            parsed = self.parse_allow_from(candidate)
            for token in parsed:
                if token == "*":
                    return ["*"]
                if token not in merged:
                    merged.append(token)
        return merged

    def sender_allowed(self, sender: Dict[str, Any], allow_from: List[str]) -> bool:
        if not allow_from or "*" in allow_from:
            return True
        sender_id = str(sender.get("id") or "").strip()
        sender_username = str(sender.get("username") or "").strip().lower()
        if sender_id and sender_id in allow_from:
            return True
        if sender_username:
            normalized_username = f"@{sender_username}"
            if normalized_username in allow_from:
                return True
        return False
