from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional


class AutopilotSkillService:
    def __init__(
        self,
        *,
        default_chat_prefix: str,
        init_runtime: Callable[[], Any],
        runtime_builtin_skills_getter: Callable[[], Any],
        runtime_skills_snapshot_getter: Callable[[], Any],
    ) -> None:
        self.default_chat_prefix = str(default_chat_prefix or "").strip() or "/empyralis"
        self.init_runtime = init_runtime
        self.runtime_builtin_skills_getter = runtime_builtin_skills_getter
        self.runtime_skills_snapshot_getter = runtime_skills_snapshot_getter

    def runtime_skills_snapshot_safe(self) -> Dict[str, Any]:
        self.init_runtime()
        getter = self.runtime_skills_snapshot_getter()
        if callable(getter):
            try:
                payload = getter()
                if isinstance(payload, dict):
                    return payload
            except Exception:
                return {}
        return {}

    def runtime_builtin_skills(self) -> List[Dict[str, Any]]:
        self.init_runtime()
        raw = self.runtime_builtin_skills_getter()
        if isinstance(raw, list):
            return [item for item in raw if isinstance(item, dict)]
        return []

    def normalize_runtime_skill_card(self, raw: Any) -> Optional[Dict[str, Any]]:
        if not isinstance(raw, dict):
            return None
        skill_id = str(raw.get("id") or "").strip().lower()
        title = str(raw.get("title") or "").strip()
        intent = str(raw.get("intent") or "").strip()
        if not skill_id or not title or not intent:
            return None
        tools_raw = raw.get("tools")
        tools: List[str] = []
        if isinstance(tools_raw, list):
            for item in tools_raw:
                token = str(item or "").strip()
                if token:
                    tools.append(token[:120])
        guardrail = str(raw.get("guardrail") or "").strip()
        return {
            "id": skill_id[:80],
            "title": title[:120],
            "intent": intent[:1200],
            "tools": tools[:30],
            "guardrail": guardrail[:1200],
        }

    def runtime_active_skills(self, scope_key: str = "assistant_defaults", limit: int = 8) -> List[Dict[str, Any]]:
        scope = "assistant_defaults" if str(scope_key or "").strip().lower() != "automation_defaults" else "automation_defaults"
        snapshot = self.runtime_skills_snapshot_safe()
        custom = snapshot.get("custom_skills") if isinstance(snapshot.get("custom_skills"), list) else []
        bindings = snapshot.get("bindings") if isinstance(snapshot.get("bindings"), dict) else {}
        selected_ids_raw = bindings.get(scope) if isinstance(bindings.get(scope), list) else []
        selected_ids = [str(item or "").strip().lower() for item in selected_ids_raw if str(item or "").strip()]
        catalog: Dict[str, Dict[str, Any]] = {}
        for item in self.runtime_builtin_skills() + [entry for entry in custom if isinstance(entry, dict)]:
            card = self.normalize_runtime_skill_card(item)
            if not card:
                continue
            catalog[card["id"]] = card
        result: List[Dict[str, Any]] = []
        if selected_ids:
            for skill_id in selected_ids:
                item = catalog.get(skill_id)
                if item:
                    result.append(item)
        else:
            result = [self.normalize_runtime_skill_card(item) for item in self.runtime_builtin_skills()]
            result = [item for item in result if isinstance(item, dict)]
        return result[: max(1, int(limit or 8))]

    def telegram_skill_goal(self, skill: Dict[str, Any]) -> str:
        title = str(skill.get("title") or "").strip() or "Assistant Skill"
        intent = str(skill.get("intent") or "").strip()
        guardrail = str(skill.get("guardrail") or "").strip()
        tools_raw = skill.get("tools") if isinstance(skill.get("tools"), list) else []
        tools = ", ".join(str(item).strip() for item in tools_raw if str(item).strip()) or "none"
        return (
            f"Apply skill '{title}' for this conversation.\n"
            f"Intent: {intent}\n"
            f"Guardrail: {guardrail or 'none'}\n"
            f"Preferred tools: {tools}\n\n"
            "Use my current chat context and give concrete next actions."
        )

    def select_skill_from_text(self, raw_text: str) -> Optional[Dict[str, Any]]:
        text = str(raw_text or "").strip()
        if not text:
            return None
        token = text.lower()
        if token.startswith("skill:"):
            token = token.split(":", 1)[1].strip()
        if token.startswith("skill "):
            token = token.split(" ", 1)[1].strip()
        active = self.runtime_active_skills("assistant_defaults", limit=20)
        for skill in active:
            skill_id = str(skill.get("id") or "").strip().lower()
            title = str(skill.get("title") or "").strip().lower()
            if token == skill_id or token == title:
                return skill
        if len(token) >= 3:
            for skill in active:
                title = str(skill.get("title") or "").strip().lower()
                if token in title:
                    return skill
        return None

    def telegram_skills_menu_text(self, profile: Dict[str, Any]) -> str:
        skills = self.runtime_active_skills("assistant_defaults", limit=8)
        prefix = str(profile.get("prefix") or self.default_chat_prefix).strip() or self.default_chat_prefix
        cmd_prefix = f"{prefix} " if bool(profile.get("require_prefix")) else ""
        lines = ["Skills Menu"]
        if not skills:
            lines.append("- No active skills found. Configure skills in the Empyralis web UI.")
        else:
            lines.append("Tap a skill button or run one directly:")
            for skill in skills:
                lines.append(f"- {cmd_prefix}skill {skill.get('id')}")
        lines.append(f"- {cmd_prefix}menu (back to main)")
        return "\n".join(lines)
