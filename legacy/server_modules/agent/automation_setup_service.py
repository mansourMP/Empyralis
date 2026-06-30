from __future__ import annotations

import threading
from pathlib import Path
from typing import Any, Callable, Dict


VALID_CAMERA_STAGES = {"awaiting_summary_target", "awaiting_followup_target"}


class TelegramCameraSetupService:
    def __init__(
        self,
        *,
        state_file: Path,
        read_json: Callable[[Path, Dict[str, Any]], Dict[str, Any]],
        write_json: Callable[[Path, Dict[str, Any]], None],
        now_iso: Callable[[], str],
        session_key_builder: Callable[[str, str], str],
        channel_origin: str = "telegram",
    ) -> None:
        self.channel_origin = channel_origin
        self.state_file = Path(state_file)
        self.read_json = read_json
        self.write_json = write_json
        self.now_iso = now_iso
        self.session_key_builder = session_key_builder
        self._lock = threading.Lock()
        self._state: Dict[str, Any] = {"loaded": False, "items": {}}

    def camera_setup_key(self, workspace_id: str, chat_id: str) -> str:
        return self.session_key_builder(workspace_id, chat_id)

    def load_state(self) -> None:
        payload = self.read_json(self.state_file, {"version": 1, "items": {}})
        items = payload.get("items") if isinstance(payload.get("items"), dict) else {}
        parsed: Dict[str, Dict[str, Any]] = {}
        for key, value in items.items():
            if not isinstance(value, dict):
                continue
            stage = str(value.get("stage") or "").strip().lower()
            if stage not in VALID_CAMERA_STAGES:
                continue
            parsed[str(key)] = {
                "stage": stage,
                "original_prompt": str(value.get("original_prompt") or "").strip(),
                "updated_at": str(value.get("updated_at") or "").strip() or self.now_iso(),
                "intent": str(value.get("intent") or "").strip().upper(),
            }
        with self._lock:
            self._state["items"] = parsed
            self._state["loaded"] = True

    def ensure_state_loaded(self) -> None:
        with self._lock:
            loaded = bool(self._state.get("loaded"))
        if not loaded:
            self.load_state()

    def persist_state(self) -> None:
        with self._lock:
            items = self._state.get("items")
            data = dict(items) if isinstance(items, dict) else {}
        self.write_json(self.state_file, {"version": 1, "items": data})

    def get_state(self, workspace_id: str, chat_id: str) -> Dict[str, Any]:
        self.ensure_state_loaded()
        key = self.camera_setup_key(workspace_id, chat_id)
        with self._lock:
            item = self._state.get("items", {}).get(key)
            out = dict(item) if isinstance(item, dict) else {}
        return {
            "active": bool(out.get("stage")),
            "intent": str(out.get("intent") or "").strip().upper(),
            "stage": str(out.get("stage") or "").strip(),
            "original_prompt": str(out.get("original_prompt") or "").strip(),
            "updated_at": str(out.get("updated_at") or "").strip(),
        }

    def set_state(
        self,
        workspace_id: str,
        chat_id: str,
        stage: str,
        original_prompt: str,
        intent: str = "",
    ) -> Dict[str, Any]:
        self.ensure_state_loaded()
        key = self.camera_setup_key(workspace_id, chat_id)
        now = self.now_iso()
        with self._lock:
            items = self._state.get("items")
            if not isinstance(items, dict):
                items = {}
                self._state["items"] = items
            items[key] = {
                "intent": str(intent or "").strip().upper(),
                "stage": str(stage or "").strip(),
                "original_prompt": str(original_prompt or "").strip(),
                "updated_at": now,
            }
        self.persist_state()
        return self.get_state(workspace_id, chat_id)

    def clear_state(self, workspace_id: str, chat_id: str) -> None:
        self.ensure_state_loaded()
        key = self.camera_setup_key(workspace_id, chat_id)
        with self._lock:
            items = self._state.get("items")
            if isinstance(items, dict):
                items.pop(key, None)
        self.persist_state()

    def handle_guided_automation_setup(
        self,
        *,
        workspace_id: str,
        chat_id: str,
        message_text: str,
        enabled: bool,
        classify_intent: Callable[[str], str],
        workspace_connector_flags: Callable[[str], Dict[str, bool]],
        create_email_summary_visibility_record: Callable[..., Any],
        create_email_summary_execution_schedules: Callable[[str, str], int],
        email_summary_completion_text: Callable[..., str],
        create_lead_followup_visibility_record: Callable[..., Any],
        create_lead_followup_execution_schedules: Callable[[str, str], int],
        lead_followup_completion_text: Callable[..., str],
    ) -> Dict[str, Any]:
        if not enabled:
            return {"handled": False, "reply": ""}
        active_state = self.get_state(workspace_id, chat_id)
        normalized_text = str(message_text or "").strip()
        active_intent = str(active_state.get("intent") or "").strip().upper()
        connector_flags = workspace_connector_flags(workspace_id)

        if active_state.get("active"):
            stage = str(active_state.get("stage") or "").strip()
            if active_intent == "EMAIL_SUMMARY" and stage == "awaiting_summary_target":
                if not normalized_text:
                    return {"handled": True, "reply": "Which inbox should I summarize? (e.g. Work inbox, Support inbox)"}
                try:
                    workflow_id = create_email_summary_visibility_record(
                        normalized_text,
                        telegram_connected=connector_flags["telegram"],
                    )
                    schedule_count = (
                        create_email_summary_execution_schedules(workspace_id, normalized_text)
                        if connector_flags["email"]
                        else 0
                    )
                    self.clear_state(workspace_id, chat_id)
                    return {
                        "handled": True,
                        "reply": email_summary_completion_text(
                            normalized_text,
                            schedule_count=schedule_count,
                            email_connected=connector_flags["email"],
                            workflow_id=workflow_id,
                        ),
                    }
                except Exception as exc:
                    self.clear_state(workspace_id, chat_id)
                    return {
                        "handled": True,
                        "reply": f"I couldn't finish the setup.\n\n{str(exc).strip() or 'Failed to create the summary automation.'}",
                    }
            if active_intent == "LEAD_FOLLOWUP" and stage == "awaiting_followup_target":
                if not normalized_text:
                    return {"handled": True, "reply": "What should I call this lead flow? (e.g. Website leads, Telegram inquiries)"}
                try:
                    workflow_id = create_lead_followup_visibility_record(
                        normalized_text,
                        email_connected=connector_flags["email"],
                        telegram_connected=connector_flags["telegram"],
                    )
                    schedule_count = (
                        create_lead_followup_execution_schedules(workspace_id, normalized_text)
                        if connector_flags["email"]
                        else 0
                    )
                    self.clear_state(workspace_id, chat_id)
                    return {
                        "handled": True,
                        "reply": lead_followup_completion_text(
                            normalized_text,
                            schedule_count=schedule_count,
                            email_connected=connector_flags["email"],
                            workflow_id=workflow_id,
                        ),
                    }
                except Exception as exc:
                    self.clear_state(workspace_id, chat_id)
                    return {
                        "handled": True,
                        "reply": f"I couldn't finish the setup.\n\n{str(exc).strip() or 'Failed to create the follow-up automation.'}",
                    }

        detected_intent = classify_intent(normalized_text)
        if detected_intent == "EMAIL_SUMMARY":
            self.set_state(
                workspace_id,
                chat_id,
                "awaiting_summary_target",
                original_prompt=normalized_text,
                intent="EMAIL_SUMMARY",
            )
            return {
                "handled": True,
                "reply": "I'll set up a daily email summary for you.\n\nWhich inbox should I summarize?\n\n(e.g. Work inbox, Support inbox)",
            }
        if detected_intent == "LEAD_FOLLOWUP":
            self.set_state(
                workspace_id,
                chat_id,
                "awaiting_followup_target",
                original_prompt=normalized_text,
                intent="LEAD_FOLLOWUP",
            )
            return {
                "handled": True,
                "reply": "I'll set up lead follow-up for you.\n\nWhat should I call this lead flow?\n\n(e.g. Website leads, Telegram inquiries)",
            }

        return {"handled": False, "reply": ""}
