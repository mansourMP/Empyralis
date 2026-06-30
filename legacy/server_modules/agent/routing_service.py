from __future__ import annotations

import re
from typing import Any, Callable, Dict, Optional


class TelegramRoutingService:
    def __init__(
        self,
        *,
        default_chat_prefix: str,
        quick_goal_templates: Dict[str, str],
        menu_goal_templates: Dict[str, str],
        normalize_profile_field: Callable[[str], str],
        select_skill_from_text: Callable[[str], Optional[Dict[str, Any]]],
        skill_goal_builder: Callable[[Dict[str, Any]], str],
        channel_origin: str = "telegram",
    ) -> None:
        self.channel_origin = channel_origin
        self.default_chat_prefix = str(default_chat_prefix or "").strip() or "/empyralis"
        self.quick_goal_templates = dict(quick_goal_templates)
        self.menu_goal_templates = dict(menu_goal_templates)
        self.normalize_profile_field = normalize_profile_field
        self.select_skill_from_text = select_skill_from_text
        self.skill_goal_builder = skill_goal_builder

    def strip_prefix(self, text: str, prefix: str) -> Dict[str, Any]:
        raw = str(text or "").strip()
        pfx = str(prefix or "").strip()
        if not pfx:
            return {"matched": False, "body": raw}
        lowered = raw.lower()
        pfx_lower = pfx.lower()
        if lowered.startswith(pfx_lower):
            remainder = raw[len(pfx) :].strip()
            remainder = re.sub(r"^[:\-\s]+", "", remainder).strip()
            return {"matched": True, "body": remainder}
        if pfx.startswith("/") and lowered.startswith(f"{pfx_lower}@"):
            parts = raw.split(" ", 1)
            remainder = parts[1] if len(parts) > 1 else ""
            remainder = re.sub(r"^[:\-\s]+", "", remainder).strip()
            return {"matched": True, "body": remainder}
        return {"matched": False, "body": raw}

    def route_message(self, raw_text: str, profile: Dict[str, Any]) -> Dict[str, Any]:
        text = str(raw_text or "").strip()
        if not text:
            return {"action": "ignore", "reason": "empty"}

        prefix = str(profile.get("prefix") or "").strip()
        require_prefix = bool(profile.get("require_prefix"))
        stripped = self.strip_prefix(text, prefix)
        prefix_matched = bool(stripped.get("matched"))
        body = str(stripped.get("body") or "").strip()

        if require_prefix and not prefix_matched:
            return {"action": "ignore", "reason": "prefix_required"}

        content = body if prefix_matched else text
        if not content:
            return {"action": "help"} if bool(profile.get("allow_help")) else {"action": "ignore", "reason": "empty_after_prefix"}

        tokens = content.split(None, 1)
        command = str(tokens[0] or "").strip().lower().lstrip("/")
        remainder = str(tokens[1] or "").strip() if len(tokens) > 1 else ""

        if command in {"start", "onboard", "setup"}:
            return {"action": "onboard_start", "payload": remainder}
        if command in {"help", "h", "?", "commands", "cmd"}:
            return {"action": "help"} if bool(profile.get("allow_help")) else {"action": "ignore", "reason": "help_disabled"}
        if command in {"home", "main"}:
            return {"action": "menu_main"}
        if command in {"status", "health"}:
            return {"action": "status"} if bool(profile.get("allow_status")) else {"action": "help"}
        if command in {"orion", "empyralis"}:
            rem = re.sub(r"\s+", " ", remainder.lower()).strip()
            if not rem or rem in {"menu", "home", "main"}:
                return {"action": "menu_main"}
            if rem in {"help", "commands"}:
                return {"action": "help"} if bool(profile.get("allow_help")) else {"action": "ignore", "reason": "help_disabled"}
            if rem in {"status", "health"}:
                return {"action": "status"} if bool(profile.get("allow_status")) else {"action": "help"}
            if rem in {"me", "profile", "context"}:
                return {"action": "profile_show"}
            if rem.startswith("run "):
                run_goal = remainder[4:].strip()
                if run_goal:
                    return {"action": "run", "goal": run_goal}
                return {"action": "help", "reason": "missing_goal"}
            return {"action": "run", "goal": remainder}
        if command in {"approvals", "approval", "pending"}:
            limit = 5
            if remainder:
                try:
                    limit = max(1, min(20, int(remainder.split()[0])))
                except Exception:
                    limit = 5
            return {"action": "approvals", "limit": limit}
        if command in {"approve", "ok"}:
            if not remainder:
                return {"action": "help", "reason": "missing_approval_id"}
            parts = remainder.split(None, 1)
            event_id = str(parts[0] or "").strip()
            note = str(parts[1] or "").strip() if len(parts) > 1 else ""
            if not event_id:
                return {"action": "help", "reason": "missing_approval_id"}
            return {"action": "approve", "event_id": event_id, "note": note}
        if command in {"reject", "deny"}:
            if not remainder:
                return {"action": "help", "reason": "missing_approval_id"}
            parts = remainder.split(None, 1)
            event_id = str(parts[0] or "").strip()
            note = str(parts[1] or "").strip() if len(parts) > 1 else ""
            if not event_id:
                return {"action": "help", "reason": "missing_approval_id"}
            return {"action": "reject", "event_id": event_id, "note": note}
        if command in {"run", "do", "task"}:
            if remainder:
                return {"action": "run", "goal": remainder}
            return {"action": "help", "reason": "missing_goal"}
        if command in {"menu", "buttons", "keyboard"}:
            menu_remainder = re.sub(r"\s+", " ", remainder.lower()).strip()
            if menu_remainder.startswith("study") or menu_remainder.startswith("work"):
                return {"action": "menu_study"}
            if menu_remainder.startswith("project"):
                return {"action": "menu_project"}
            if menu_remainder.startswith("skill"):
                return {"action": "menu_skills"}
            if menu_remainder.startswith("context"):
                return {"action": "menu_context"}
            return {"action": "menu_main"}
        if command in {"skill", "skills"}:
            if not remainder:
                return {"action": "menu_skills"}
            skill = self.select_skill_from_text(remainder)
            if not skill:
                return {"action": "help", "reason": "unknown_skill"}
            return {"action": "run", "goal": self.skill_goal_builder(skill), "source": "skill_menu", "skill": skill}
        if command in {"me", "profile", "context"}:
            if not remainder:
                return {"action": "profile_show"}
            parts = remainder.split(None, 2)
            sub = str(parts[0] or "").strip().lower().lstrip("/") if parts else ""
            if sub in {"show", "view", "list"}:
                return {"action": "profile_show"}
            if sub in {"clear", "reset", "delete", "remove"}:
                field_raw = str(parts[1] or "").strip() if len(parts) > 1 else ""
                if not field_raw:
                    return {"action": "profile_clear", "field": ""}
                field_name = self.normalize_profile_field(field_raw)
                if not field_name:
                    return {"action": "profile_help", "reason": "unknown_field"}
                return {"action": "profile_clear", "field": field_name}
            if sub in {"set", "update"}:
                if len(parts) < 3:
                    return {"action": "profile_help", "reason": "missing_field_or_value"}
                field_name = self.normalize_profile_field(str(parts[1] or "").strip())
                value = str(parts[2] or "").strip()
                if not field_name or not value:
                    return {"action": "profile_help", "reason": "missing_field_or_value"}
                return {"action": "profile_set", "field": field_name, "value": value}
            shorthand_field = self.normalize_profile_field(sub)
            if shorthand_field and len(parts) >= 2:
                value = str(remainder.split(None, 1)[1] or "").strip()
                if value:
                    return {"action": "profile_set", "field": shorthand_field, "value": value}
            if bool(profile.get("allow_free_text")):
                return {"action": "run", "goal": content}
            return {"action": "profile_help"}

        normalized_content = re.sub(r"\s+", " ", content.lower()).strip()
        if normalized_content in {"study menu", "study", "work menu", "work"}:
            return {"action": "menu_study"}
        if normalized_content in {"project menu", "project"}:
            return {"action": "menu_project"}
        if normalized_content in {"skills menu", "skills", "skill menu"}:
            return {"action": "menu_skills"}
        if normalized_content in {"context", "context menu"}:
            return {"action": "menu_context"}
        if normalized_content in {"back to main", "main menu", "back"}:
            return {"action": "menu_main"}
        if normalized_content in {
            "orion",
            "orion home",
            "orion menu",
            "/orion",
            "/orion home",
            "/orion menu",
            "empyralis",
            "empyralis home",
            "empyralis menu",
            "/empyralis",
            "/empyralis home",
            "/empyralis menu",
        }:
            return {"action": "menu_main"}
        if normalized_content in {"my context", "show my context"}:
            return {"action": "profile_show"}
        if normalized_content in {"context help"}:
            return {"action": "profile_help"}
        if normalized_content.startswith("skill:"):
            skill = self.select_skill_from_text(normalized_content)
            if skill:
                return {"action": "run", "goal": self.skill_goal_builder(skill), "source": "skill_menu", "skill": skill}
        if normalized_content in {"start", "start onboarding", "onboard me"}:
            return {"action": "onboard_start", "payload": ""}

        quick_goal = self.menu_goal_templates.get(normalized_content) or self.quick_goal_templates.get(normalized_content)
        if quick_goal:
            return {"action": "run", "goal": quick_goal, "source": "quick_goal"}

        if bool(profile.get("allow_free_text")):
            return {"action": "run", "goal": content}
        return {"action": "help", "reason": "unknown_command"}

    def help_text(self, profile: Dict[str, Any]) -> str:
        prefix = str(profile.get("prefix") or self.default_chat_prefix).strip() or self.default_chat_prefix
        require_prefix = bool(profile.get("require_prefix"))
        cmd_prefix = f"{prefix} " if require_prefix else ""
        lines = [
            "Empyralis Commands",
            f"- {cmd_prefix}run <goal>",
            f"- {cmd_prefix}onboard (quick context setup)",
            f"- {cmd_prefix}home (open main menu)",
        ]
        lines.append(f"- {cmd_prefix}commands (same as help)")
        if bool(profile.get("allow_status")):
            lines.append(f"- {cmd_prefix}status")
        lines.append(f"- {cmd_prefix}approvals [limit]")
        lines.append(f"- {cmd_prefix}approve <event_id> [note]")
        lines.append(f"- {cmd_prefix}reject <event_id> [reason]")
        lines.append(f"- {cmd_prefix}start (chat onboarding)")
        lines.append(f"- {cmd_prefix}me (show/save your context)")
        lines.append(f"- {cmd_prefix}skills (open skills menu)")
        lines.append(f"- {cmd_prefix}skill <id|name> (run with that skill)")
        lines.append(f"- {cmd_prefix}buttons (show quick actions)")
        if bool(profile.get("allow_help")):
            lines.append(f"- {cmd_prefix}help")
        if bool(profile.get("allow_free_text")):
            lines.append("- Or send plain text to start a run.")
        else:
            lines.append("- Plain text is ignored in this profile.")
        if require_prefix:
            lines.append(f"- Prefix mode is on ({prefix}).")
        return "\n".join(lines)

    def is_explicit_run_command(self, raw_text: str) -> bool:
        text = str(raw_text or "").strip()
        if not text:
            return False
        first = text.split(None, 1)[0].strip().lower().lstrip("/")
        return first in {"run", "do", "task"}
