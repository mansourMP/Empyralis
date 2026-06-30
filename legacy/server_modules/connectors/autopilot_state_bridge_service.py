from __future__ import annotations

from typing import Any, Callable, Dict


class AutopilotStateBridgeService:
    def __init__(
        self,
        *,
        telegram_state_service: Callable[[], Any],
        whatsapp_state_service: Callable[[], Any],
        telegram_runtime_service: Callable[[], Any],
        telegram_state: Dict[str, Any],
        telegram_lock: Any,
    ) -> None:
        self.telegram_state_service = telegram_state_service
        self.whatsapp_state_service = whatsapp_state_service
        self.telegram_runtime_service = telegram_runtime_service
        self.telegram_state = telegram_state
        self.telegram_lock = telegram_lock

    def load_telegram_autopilot_state(self) -> None:
        self.telegram_state_service().load_state()

    def load_whatsapp_autopilot_state(self) -> None:
        self.whatsapp_state_service().load_state()

    def telegram_autopilot_snapshot(self) -> Dict[str, Any]:
        return self.telegram_state_service().snapshot(include_connectors=True)

    def whatsapp_autopilot_snapshot(self) -> Dict[str, Any]:
        return self.whatsapp_state_service().snapshot(include_connectors=True)

    def whatsapp_autopilot_activate(self) -> None:
        self.whatsapp_state_service().activate()

    def telegram_increment_processed_updates(self) -> None:
        self.telegram_runtime_service().increment_processed_updates()

    def telegram_set_connectors_seen(self, count: int) -> None:
        self.telegram_runtime_service().set_connectors_seen(count)

    def mark_telegram_autopilot_started(self, started_at: str) -> None:
        with self.telegram_lock:
            self.telegram_state["active"] = True
            self.telegram_state["started_at"] = started_at
            self.telegram_state["enabled"] = True
