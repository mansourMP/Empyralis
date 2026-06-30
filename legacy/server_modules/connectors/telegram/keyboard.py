"""
Telegram-specific inline keyboard JSON builder.
Extracted from telegram_menu_service.py — channel adapter layer only.
"""

from __future__ import annotations

from typing import Any, Callable, Dict, List


class TelegramKeyboardBuilder:
    """Builds Telegram inline keyboard JSON from menu content decisions."""

    def __init__(
        self,
        *,
        default_chat_prefix: str,
        show_buttons: bool,
        runtime_active_skills: Callable[[str, int], List[Dict[str, Any]]],
    ) -> None:
        self.default_chat_prefix = str(default_chat_prefix or "").strip()
        self.show_buttons = bool(show_buttons)
        self.runtime_active_skills = runtime_active_skills

    def _prefixed_command(self, profile: Dict[str, Any], command_text: str) -> str:
        clean = str(command_text or "").strip()
        if not clean:
            return ""
        prefix = str(profile.get("prefix") or self.default_chat_prefix).strip() or self.default_chat_prefix
        return f"{prefix} {clean}" if prefix else clean

    def menu_keyboard(self, profile: Dict[str, Any], menu_id: str = "main") -> Dict[str, Any]:
        if not self.show_buttons:
            return {"remove_keyboard": True}
        require_prefix = bool(profile.get("require_prefix"))
        status_cmd = self._prefixed_command(profile, "status") if require_prefix else "Status"
        approvals_cmd = self._prefixed_command(profile, "approvals") if require_prefix else "Approvals"
        help_cmd = self._prefixed_command(profile, "help") if require_prefix else "Help"
        me_cmd = self._prefixed_command(profile, "me") if require_prefix else "My context"

        if menu_id == "study":
            if require_prefix:
                keyboard = [
                    [{"text": self._prefixed_command(profile, "run inbox triage")}, {"text": self._prefixed_command(profile, "run draft message")}],
                    [{"text": self._prefixed_command(profile, "run today priorities")}, {"text": self._prefixed_command(profile, "run task breakdown")}],
                    [{"text": self._prefixed_command(profile, "menu")}],
                ]
            else:
                keyboard = [
                    [{"text": "Inbox triage"}, {"text": "Draft message"}],
                    [{"text": "Today priorities"}, {"text": "Task breakdown"}],
                    [{"text": "Back to main"}],
                ]
        elif menu_id == "project":
            if require_prefix:
                keyboard = [
                    [{"text": self._prefixed_command(profile, "run project update")}, {"text": self._prefixed_command(profile, "run next steps")}],
                    [{"text": self._prefixed_command(profile, "run meeting prep")}, {"text": self._prefixed_command(profile, "run write follow-up")}],
                    [{"text": self._prefixed_command(profile, "menu")}],
                ]
            else:
                keyboard = [
                    [{"text": "Project update"}, {"text": "Next steps"}],
                    [{"text": "Meeting prep"}, {"text": "Write follow-up"}],
                    [{"text": "Back to main"}],
                ]
        elif menu_id == "context":
            if require_prefix:
                keyboard = [
                    [{"text": self._prefixed_command(profile, "me")}, {"text": self._prefixed_command(profile, "help")}],
                    [{"text": self._prefixed_command(profile, "menu")}],
                ]
            else:
                keyboard = [
                    [{"text": "My context"}, {"text": "Context help"}],
                    [{"text": "Back to main"}],
                ]
        elif menu_id == "skills":
            active_skills = self.runtime_active_skills("assistant_defaults", 8)
            if require_prefix:
                keyboard: List[List[Dict[str, str]]] = []
                row: List[Dict[str, str]] = []
                for skill in active_skills:
                    row.append({"text": self._prefixed_command(profile, f"skill {skill.get('id')}")})
                    if len(row) >= 2:
                        keyboard.append(row)
                        row = []
                if row:
                    keyboard.append(row)
                keyboard.append([{"text": self._prefixed_command(profile, "menu")}])
            else:
                keyboard = []
                row = []
                for skill in active_skills:
                    label = f"Skill: {str(skill.get('title') or '').strip()}"[:48]
                    row.append({"text": label})
                    if len(row) >= 2:
                        keyboard.append(row)
                        row = []
                if row:
                    keyboard.append(row)
                keyboard.append([{"text": "Back to main"}])
        else:
            if require_prefix:
                keyboard = [
                    [{"text": self._prefixed_command(profile, "menu work")}, {"text": self._prefixed_command(profile, "menu project")}],
                    [{"text": self._prefixed_command(profile, "menu skills")}, {"text": self._prefixed_command(profile, "menu context")}],
                    [{"text": status_cmd}],
                    [{"text": approvals_cmd}, {"text": help_cmd}],
                ]
            else:
                keyboard = [
                    [{"text": "Work menu"}, {"text": "Project menu"}],
                    [{"text": "Skills"}, {"text": "Context"}],
                    [{"text": status_cmd}],
                    [{"text": approvals_cmd}, {"text": help_cmd}],
                ]

        return {
            "keyboard": keyboard if keyboard else [[{"text": me_cmd}]],
            "resize_keyboard": True,
            "is_persistent": True,
            "one_time_keyboard": False,
            "input_field_placeholder": "Message Empyralis...",
        }

    def reply_keyboard(self, profile: Dict[str, Any]) -> Dict[str, Any]:
        if not self.show_buttons:
            return {"remove_keyboard": True}
        return self.menu_keyboard(profile, "main")
