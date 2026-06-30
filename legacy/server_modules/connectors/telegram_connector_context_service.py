from __future__ import annotations

import re
from typing import Any, Callable, Dict, List, Optional


class TelegramConnectorContextService:
    def __init__(
        self,
        *,
        installed_skills_enabled: bool,
        init_runtime: Callable[[], None],
        list_vault_connectors: Callable[[str], List[Dict[str, Any]]],
        resolve_vault_credential: Callable[[str, str], Dict[str, Any]],
        list_recent_connector_messages: Callable[[Dict[str, Any], int], List[Dict[str, Any]]],
        query_active_installed_skills: Callable[..., Dict[str, Any]],
    ) -> None:
        self.installed_skills_enabled = bool(installed_skills_enabled)
        self.init_runtime = init_runtime
        self.list_vault_connectors = list_vault_connectors
        self.resolve_vault_credential = resolve_vault_credential
        self.list_recent_connector_messages = list_recent_connector_messages
        self.query_active_installed_skills = query_active_installed_skills

    def connector_capability_summary(self, connector_id: str) -> str:
        provider = str(connector_id or "").strip().lower()
        if provider == "google_workspace":
            return "email, calendar, drive"
        if provider == "microsoft_365":
            return "email, calendar, files"
        if provider == "telegram_bot":
            return "telegram chat"
        if provider == "whatsapp_twilio":
            return "whatsapp chat"
        if provider == "discord_bot":
            return "discord chat"
        return "connected tool"

    def requested_recent_email_limit(self, goal: str) -> int:
        raw = str(goal or "").strip().lower()
        if not raw:
            return 0
        if not any(token in raw for token in ("email", "emails", "gmail", "inbox", "mailbox")):
            return 0
        if not any(token in raw for token in ("read", "summarize", "summary", "show", "latest", "recent", "last")):
            return 0
        match = re.search(r"\b(?:last|latest|recent)\s+(\d+)\s+emails?\b", raw)
        if match:
            try:
                return max(1, min(int(match.group(1)), 10))
            except Exception:
                return 3
        return 3

    def workspace_connector_context(
        self,
        goal: str,
        workspace_id: str,
        current_connector_id: str,
    ) -> Dict[str, Any]:
        self.init_runtime()
        raw_goal = str(goal or "").strip().lower()
        entries = self.list_vault_connectors(workspace_id)
        summaries: List[Dict[str, Any]] = []
        preferred_email_entry: Optional[Dict[str, Any]] = None
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            connector_id = str(entry.get("id") or "").strip()
            provider = str(entry.get("connector") or "").strip().lower()
            label = str(entry.get("label") or provider or "Connector").strip()
            summaries.append(
                {
                    "id": connector_id,
                    "connector": provider,
                    "label": label,
                    "capabilities": self.connector_capability_summary(provider),
                    "session_connector": connector_id == str(current_connector_id or "").strip(),
                }
            )
            if provider in {"google_workspace", "microsoft_365"} and preferred_email_entry is None:
                preferred_email_entry = entry

        email_limit = self.requested_recent_email_limit(goal)
        needs_prompt = bool(
            email_limit > 0
            or any(
                token in raw_goal
                for token in ("connector", "email", "gmail", "inbox", "calendar", "drive", "document", "spreadsheet", "sheet")
            )
        )

        prompt_lines: List[str] = []
        if summaries and needs_prompt:
            prompt_lines.append("Workspace connectors available for this request:")
            for item in summaries:
                label = str(item.get("label") or "Connector").strip()
                capabilities = str(item.get("capabilities") or "connected tool").strip()
                prompt_lines.append(f"- {label}: {capabilities}")

        selected_connector_id = ""
        selected_connector_provider = ""
        if email_limit > 0 and isinstance(preferred_email_entry, dict):
            selected_connector_id = str(preferred_email_entry.get("id") or "").strip()
            selected_connector_provider = str(preferred_email_entry.get("connector") or "").strip().lower()
            try:
                secret = self.resolve_vault_credential(selected_connector_id, workspace_id)
                messages = self.list_recent_connector_messages(secret, email_limit)
            except Exception as exc:
                prompt_lines.append(f"Connector fetch warning: {str(exc).strip()}")
                messages = []
            if messages:
                prompt_lines.append("")
                prompt_lines.append(
                    f"Recent emails fetched from {preferred_email_entry.get('label') or selected_connector_provider}:"
                )
                for idx, message in enumerate(messages, start=1):
                    subject = str(message.get("subject") or "(no subject)").strip()
                    sender = str(message.get("from") or "unknown sender").strip()
                    date = str(message.get("date") or "").strip()
                    snippet = re.sub(r"\s+", " ", str(message.get("snippet") or "").strip())
                    if len(snippet) > 280:
                        snippet = snippet[:277] + "..."
                    line = f"{idx}. From: {sender} | Subject: {subject}"
                    if date:
                        line += f" | Date: {date}"
                    prompt_lines.append(line)
                    if snippet:
                        prompt_lines.append(f"   Snippet: {snippet}")

        return {
            "channel_connectors": [
                {"connector": str(item.get("connector") or "").strip(), "credential_id": str(item.get("id") or "").strip()}
                for item in summaries
                if str(item.get("connector") or "").strip() and str(item.get("id") or "").strip()
            ],
            "available_connectors": summaries,
            "connector_credential_id": selected_connector_id or None,
            "connector_provider": selected_connector_provider or None,
            "prompt_append": "\n".join(prompt_lines).strip(),
        }

    def build_goal_with_connector_context(self, goal: str, connector_prompt: str) -> str:
        request_text = str(goal or "").strip()
        prompt = str(connector_prompt or "").strip()
        if not prompt:
            return request_text
        if not request_text:
            return prompt
        return f"{request_text}\n\n{prompt}"

    def installed_skill_query(
        self,
        *,
        goal: str,
        workspace_id: str,
        connector_id: str,
        chat_id: str,
        session_key: str,
    ) -> Dict[str, Any]:
        if not self.installed_skills_enabled:
            return {
                "handled": False,
                "response": "",
                "prompt_append": "",
                "active_skill_ids": [],
                "errors": [],
            }
        try:
            return self.query_active_installed_skills(
                query=goal,
                channel="telegram",
                workspace_id=workspace_id,
                connector_id=connector_id,
                chat_id=chat_id,
                session_key=session_key,
            )
        except Exception as exc:
            return {
                "handled": False,
                "response": "",
                "prompt_append": "",
                "active_skill_ids": [],
                "errors": [{"skill_id": "installed_skills", "error": str(exc)}],
            }
