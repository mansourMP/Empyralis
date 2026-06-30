from __future__ import annotations

import re
import threading
from pathlib import Path
from typing import Any, Callable, Dict, List


TELEGRAM_PROFILE_FIELDS: Dict[str, str] = {
    "about": "About me",
    "project": "Project",
    "exam": "Exam prep",
    "preferences": "Preferences",
}

TELEGRAM_PROFILE_FIELD_ALIASES: Dict[str, str] = {
    "about": "about",
    "bio": "about",
    "me": "about",
    "project": "project",
    "work": "project",
    "business": "project",
    "startup": "project",
    "exam": "exam",
    "study": "exam",
    "school": "exam",
    "prep": "exam",
    "preferences": "preferences",
    "preference": "preferences",
    "prefs": "preferences",
    "style": "preferences",
    "tone": "preferences",
}

TELEGRAM_ONBOARDING_STEPS: List[Dict[str, str]] = [
    {
        "field": "about",
        "label": "About me",
        "question": "What's your name, and what are you working on right now?",
        "example": "Example: I'm Mansur, building an AI ops platform for my business.",
    },
    {
        "field": "project",
        "label": "Project",
        "question": "What does a great day look like for you? What should I help with most?",
        "example": "Example: End the day with top priorities executed and client follow-ups sent.",
    },
    {
        "field": "preferences",
        "label": "Preferences",
        "question": "Anything I should always prioritize or never touch?",
        "example": "Example: Prioritize urgent revenue tasks. Never send external messages without asking me first.",
    },
]


class TelegramProfileService:
    def __init__(
        self,
        *,
        profile_state_file: Path,
        onboarding_state_file: Path,
        default_chat_prefix: str,
        read_json: Callable[[Path, Dict[str, Any]], Dict[str, Any]],
        write_json: Callable[[Path, Dict[str, Any]], None],
        now_iso: Callable[[], str],
        truncate_one_line: Callable[[str, int], str],
        channel_origin: str = "telegram",
    ) -> None:
        self.channel_origin = channel_origin
        self.profile_state_file = Path(profile_state_file)
        self.onboarding_state_file = Path(onboarding_state_file)
        self.default_chat_prefix = str(default_chat_prefix or "").strip() or "/empyralis"
        self.read_json = read_json
        self.write_json = write_json
        self.now_iso = now_iso
        self.truncate_one_line = truncate_one_line
        self._profile_lock = threading.Lock()
        self._profile_state: Dict[str, Any] = {"loaded": False, "profiles": {}}
        self._onboarding_lock = threading.Lock()
        self._onboarding_state: Dict[str, Any] = {"loaded": False, "items": {}}

    def telegram_profile_key(self, workspace_id: str, chat_id: str) -> str:
        ws = str(workspace_id or "").strip() or "default"
        cid = str(chat_id or "").strip() or "unknown"
        return f"{ws}:{cid}"

    def telegram_onboarding_key(self, workspace_id: str, chat_id: str) -> str:
        return self.telegram_profile_key(workspace_id, chat_id)

    def load_profile_state(self) -> None:
        payload = self.read_json(self.profile_state_file, {"version": 1, "items": {}})
        items = payload.get("items") if isinstance(payload.get("items"), dict) else {}
        parsed_profiles: Dict[str, Dict[str, str]] = {}
        for key, value in items.items():
            if not isinstance(value, dict):
                continue
            cleaned: Dict[str, str] = {}
            for field_name in TELEGRAM_PROFILE_FIELDS:
                raw_value = str(value.get(field_name) or "").strip()
                if raw_value:
                    cleaned[field_name] = raw_value[:2000]
            updated_at = str(value.get("updated_at") or "").strip()
            if cleaned:
                cleaned["updated_at"] = updated_at or self.now_iso()
                parsed_profiles[str(key)] = cleaned
        with self._profile_lock:
            self._profile_state["profiles"] = parsed_profiles
            self._profile_state["loaded"] = True

    def ensure_profile_state_loaded(self) -> None:
        with self._profile_lock:
            loaded = bool(self._profile_state.get("loaded"))
        if not loaded:
            self.load_profile_state()

    def persist_profile_state(self) -> None:
        with self._profile_lock:
            profiles = self._profile_state.get("profiles")
            data = dict(profiles) if isinstance(profiles, dict) else {}
        self.write_json(self.profile_state_file, {"version": 1, "items": data})

    def normalize_profile_field(self, raw_value: str) -> str:
        token = str(raw_value or "").strip().lower()
        if not token:
            return ""
        token = token.lstrip("/")
        return TELEGRAM_PROFILE_FIELD_ALIASES.get(token, "")

    def get_profile(self, workspace_id: str, chat_id: str) -> Dict[str, str]:
        self.ensure_profile_state_loaded()
        key = self.telegram_profile_key(workspace_id, chat_id)
        with self._profile_lock:
            item = self._profile_state.get("profiles", {}).get(key)
            profile = dict(item) if isinstance(item, dict) else {}
        out: Dict[str, str] = {}
        for field_name in TELEGRAM_PROFILE_FIELDS:
            out[field_name] = str(profile.get(field_name) or "").strip()
        out["updated_at"] = str(profile.get("updated_at") or "").strip()
        return out

    def set_profile_field(self, workspace_id: str, chat_id: str, field_name: str, value: str) -> Dict[str, str]:
        self.ensure_profile_state_loaded()
        key = self.telegram_profile_key(workspace_id, chat_id)
        cleaned_value = re.sub(r"\s+", " ", str(value or "")).strip()
        if not cleaned_value:
            raise RuntimeError("Value is required.")
        cleaned_value = cleaned_value[:2000]
        with self._profile_lock:
            profiles = self._profile_state.get("profiles")
            if not isinstance(profiles, dict):
                profiles = {}
                self._profile_state["profiles"] = profiles
            current = profiles.get(key)
            record = dict(current) if isinstance(current, dict) else {}
            record[field_name] = cleaned_value
            record["updated_at"] = self.now_iso()
            profiles[key] = record
        self.persist_profile_state()
        return self.get_profile(workspace_id, chat_id)

    def clear_profile(self, workspace_id: str, chat_id: str, field_name: str = "") -> Dict[str, str]:
        self.ensure_profile_state_loaded()
        key = self.telegram_profile_key(workspace_id, chat_id)
        with self._profile_lock:
            profiles = self._profile_state.get("profiles")
            if not isinstance(profiles, dict):
                profiles = {}
                self._profile_state["profiles"] = profiles
            record = profiles.get(key)
            if not isinstance(record, dict):
                record = {}
            if field_name:
                record.pop(field_name, None)
                if any(str(record.get(name) or "").strip() for name in TELEGRAM_PROFILE_FIELDS):
                    record["updated_at"] = self.now_iso()
                    profiles[key] = record
                else:
                    profiles.pop(key, None)
            else:
                profiles.pop(key, None)
        self.persist_profile_state()
        return self.get_profile(workspace_id, chat_id)

    def load_onboarding_state(self) -> None:
        payload = self.read_json(self.onboarding_state_file, {"version": 1, "items": {}})
        items = payload.get("items") if isinstance(payload.get("items"), dict) else {}
        parsed: Dict[str, Dict[str, Any]] = {}
        for key, value in items.items():
            if not isinstance(value, dict):
                continue
            active = bool(value.get("active"))
            step_index = int(value.get("step_index") or 0)
            started_at = str(value.get("started_at") or "").strip()
            updated_at = str(value.get("updated_at") or "").strip()
            if not active and step_index <= 0:
                continue
            parsed[str(key)] = {
                "active": active,
                "step_index": max(0, min(step_index, len(TELEGRAM_ONBOARDING_STEPS))),
                "started_at": started_at or self.now_iso(),
                "updated_at": updated_at or self.now_iso(),
            }
        with self._onboarding_lock:
            self._onboarding_state["items"] = parsed
            self._onboarding_state["loaded"] = True

    def ensure_onboarding_state_loaded(self) -> None:
        with self._onboarding_lock:
            loaded = bool(self._onboarding_state.get("loaded"))
        if not loaded:
            self.load_onboarding_state()

    def persist_onboarding_state(self) -> None:
        with self._onboarding_lock:
            items = self._onboarding_state.get("items")
            data = dict(items) if isinstance(items, dict) else {}
        self.write_json(self.onboarding_state_file, {"version": 1, "items": data})

    def get_onboarding_state(self, workspace_id: str, chat_id: str) -> Dict[str, Any]:
        self.ensure_onboarding_state_loaded()
        key = self.telegram_onboarding_key(workspace_id, chat_id)
        with self._onboarding_lock:
            item = self._onboarding_state.get("items", {}).get(key)
            out = dict(item) if isinstance(item, dict) else {}
        return {
            "active": bool(out.get("active")),
            "step_index": int(out.get("step_index") or 0),
            "started_at": str(out.get("started_at") or "").strip(),
            "updated_at": str(out.get("updated_at") or "").strip(),
        }

    def next_onboarding_step_index(self, profile_data: Dict[str, str]) -> int:
        for idx, step in enumerate(TELEGRAM_ONBOARDING_STEPS):
            field_name = str(step.get("field") or "").strip().lower()
            if not field_name:
                continue
            if not str(profile_data.get(field_name) or "").strip():
                return idx
        return len(TELEGRAM_ONBOARDING_STEPS)

    def start_onboarding(self, workspace_id: str, chat_id: str) -> Dict[str, Any]:
        self.ensure_onboarding_state_loaded()
        key = self.telegram_onboarding_key(workspace_id, chat_id)
        profile_data = self.get_profile(workspace_id, chat_id)
        start_index = self.next_onboarding_step_index(profile_data)
        should_activate = start_index < len(TELEGRAM_ONBOARDING_STEPS)
        now = self.now_iso()
        with self._onboarding_lock:
            items = self._onboarding_state.get("items")
            if not isinstance(items, dict):
                items = {}
                self._onboarding_state["items"] = items
            items[key] = {
                "active": should_activate,
                "step_index": start_index,
                "started_at": now,
                "updated_at": now,
            }
        self.persist_onboarding_state()
        return self.get_onboarding_state(workspace_id, chat_id)

    def advance_onboarding(self, workspace_id: str, chat_id: str, step_index: int, active: bool) -> Dict[str, Any]:
        self.ensure_onboarding_state_loaded()
        key = self.telegram_onboarding_key(workspace_id, chat_id)
        now = self.now_iso()
        with self._onboarding_lock:
            items = self._onboarding_state.get("items")
            if not isinstance(items, dict):
                items = {}
                self._onboarding_state["items"] = items
            if active:
                current = items.get(key)
                record = dict(current) if isinstance(current, dict) else {}
                record["active"] = True
                record["step_index"] = max(0, min(int(step_index), len(TELEGRAM_ONBOARDING_STEPS)))
                record["started_at"] = str(record.get("started_at") or now)
                record["updated_at"] = now
                items[key] = record
            else:
                items.pop(key, None)
        self.persist_onboarding_state()
        return self.get_onboarding_state(workspace_id, chat_id)

    def profile_has_context(self, profile_data: Dict[str, str]) -> bool:
        return any(str(profile_data.get(field_name) or "").strip() for field_name in TELEGRAM_PROFILE_FIELDS)

    def is_low_quality_onboarding_answer(self, raw_value: str) -> bool:
        text = re.sub(r"\s+", " ", str(raw_value or "").strip())
        if not text:
            return True
        lower = text.lower()
        if lower in {"idk", "dont know", "don't know", "none", "nothing", "n/a", "na"}:
            return True
        if len(text) < 8:
            return True
        words = [w for w in re.split(r"\s+", lower) if w]
        if len(words) < 2 and len(text) < 12:
            return True
        letters_only = re.sub(r"[^a-z]", "", lower)
        if letters_only and len(set(letters_only)) <= 3 and len(letters_only) <= 8:
            return True
        return False

    def onboarding_prompt(self, step_index: int, retry: bool = False) -> str:
        idx = max(0, min(int(step_index), len(TELEGRAM_ONBOARDING_STEPS) - 1))
        step = TELEGRAM_ONBOARDING_STEPS[idx]
        lead = "Let's set your context (quick 3 questions)." if idx == 0 and not retry else "Thanks. Next question."
        if retry:
            lead = "I need a bit more detail for that answer."
        return "\n".join(
            [
                lead,
                f"{idx + 1}/{len(TELEGRAM_ONBOARDING_STEPS)} {step.get('question')}",
                str(step.get("example") or "").strip(),
                "Reply with your answer, or type 'skip'.",
            ]
        ).strip()

    def profile_lines(self, profile_data: Dict[str, str]) -> List[str]:
        lines: List[str] = []
        for field_name, label in TELEGRAM_PROFILE_FIELDS.items():
            value = str(profile_data.get(field_name) or "").strip()
            if value:
                lines.append(f"- {label}: {self.truncate_one_line(value, 140)}")
        if not lines:
            lines.append("- No context saved yet.")
        return lines

    def profile_text(self, profile: Dict[str, Any], profile_data: Dict[str, str]) -> str:
        prefix = str(profile.get("prefix") or self.default_chat_prefix).strip() or self.default_chat_prefix
        cmd_prefix = f"{prefix} " if bool(profile.get("require_prefix")) else ""
        lines = ["Saved context", *self.profile_lines(profile_data), ""]
        lines.extend(
            [
                f"Review or update later with `{cmd_prefix}me`.",
                f"Quick edit: `{cmd_prefix}me set project <details>`",
                f"Clear a field: `{cmd_prefix}me clear [project|exam|about|preferences]`",
            ]
        )
        if bool(profile.get("deployed_agent_id")):
            lines.extend(
                [
                    f"Privacy policy: `{cmd_prefix}privacy`",
                    f"Request data deletion: `{cmd_prefix}delete`",
                ]
            )
        return "\n".join(lines).strip()

    def profile_help_text(self, profile: Dict[str, Any]) -> str:
        prefix = str(profile.get("prefix") or self.default_chat_prefix).strip() or self.default_chat_prefix
        cmd_prefix = f"{prefix} " if bool(profile.get("require_prefix")) else ""
        return "\n".join(
            [
                "Profile commands",
                f"- {cmd_prefix}me",
                f"- {cmd_prefix}me set project <details>",
                f"- {cmd_prefix}me set exam <details>",
                f"- {cmd_prefix}me set about <details>",
                f"- {cmd_prefix}me set preferences <details>",
                f"- {cmd_prefix}me clear [project|exam|about|preferences]",
                *(
                    [
                        f"- {cmd_prefix}privacy",
                        f"- {cmd_prefix}delete",
                    ]
                    if bool(profile.get("deployed_agent_id"))
                    else []
                ),
            ]
        )

    def build_goal_with_profile(self, goal: str, profile_data: Dict[str, str]) -> str:
        request = str(goal or "").strip()
        context_lines = self.profile_lines(profile_data)
        meaningful_context = [line for line in context_lines if not line.endswith("No context saved yet.")]
        if not meaningful_context:
            return request
        return "\n".join(["User context", *meaningful_context, "", "User request", request]).strip()

    def onboarding_consume_answer(self, workspace_id: str, chat_id: str, answer_text: str) -> Dict[str, Any]:
        state = self.get_onboarding_state(workspace_id, chat_id)
        if not bool(state.get("active")):
            state = self.start_onboarding(workspace_id, chat_id)
        idx = max(0, min(int(state.get("step_index") or 0), len(TELEGRAM_ONBOARDING_STEPS) - 1))
        step = TELEGRAM_ONBOARDING_STEPS[idx]
        field_name = str(step.get("field") or "").strip().lower()
        answer = re.sub(r"\s+", " ", str(answer_text or "").strip())
        answer_lower = answer.lower()
        if answer_lower not in {"skip", "/skip", "later"} and self.is_low_quality_onboarding_answer(answer):
            return {
                "completed": False,
                "saved": False,
                "reply": self.onboarding_prompt(idx, retry=True),
                "next_state": state,
            }
        saved = False
        if answer_lower not in {"skip", "/skip", "later"} and field_name in TELEGRAM_PROFILE_FIELDS:
            self.set_profile_field(workspace_id, chat_id, field_name, answer)
            saved = True
        next_idx = idx + 1
        if next_idx >= len(TELEGRAM_ONBOARDING_STEPS):
            self.advance_onboarding(workspace_id, chat_id, next_idx, active=False)
            profile_data = self.get_profile(workspace_id, chat_id)
            return {
                "completed": True,
                "saved": saved,
                "reply": "Onboarding complete.\n\n"
                + self.profile_text({"prefix": self.default_chat_prefix, "require_prefix": False}, profile_data),
                "next_state": {"active": False, "step_index": next_idx},
            }
        next_state = self.advance_onboarding(workspace_id, chat_id, next_idx, active=True)
        return {
            "completed": False,
            "saved": saved,
            "reply": self.onboarding_prompt(next_idx, retry=False),
            "next_state": next_state,
        }
