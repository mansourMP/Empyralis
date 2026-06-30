from __future__ import annotations

from typing import Any, Callable, Dict, List


class TelegramSenderFilterService:
    def __init__(
        self,
        *,
        record_channel_event_throttled: Callable[..., Any],
        set_connector_state: Callable[[str, Dict[str, Any]], Any],
        utc_now_iso: Callable[[], str],
    ) -> None:
        self.record_channel_event_throttled = record_channel_event_throttled
        self.set_connector_state = set_connector_state
        self.utc_now_iso = utc_now_iso

    def handle_denied_sender(
        self,
        *,
        connector_id: str,
        label: str,
        workspace_id: str,
        update_id: int,
        profile_id: str,
        allow_from: List[str],
        sender_id: str,
        sender_username: str,
        chat_id: str,
        dropped_count: int,
        dedupe_seconds: float,
    ) -> None:
        self.record_channel_event_throttled(
            channel="telegram",
            direction="system",
            event_type="drop_sender_not_allowed",
            text=(
                f"Dropped Telegram inbound from sender_id={sender_id or '-'} "
                f"username=@{sender_username or '-'} (not in allow_from)."
            ),
            workspace_id=workspace_id,
            session_key=chat_id,
            action="drop",
            metadata={
                "connector_id": connector_id,
                "chat_id": chat_id,
                "sender_id": sender_id,
                "sender_username": sender_username,
                "allow_from": list(allow_from),
            },
            dedupe_seconds=dedupe_seconds,
        )
        self.set_connector_state(
            connector_id,
            {
                "label": label,
                "workspace_id": workspace_id,
                "last_update_id": update_id,
                "last_poll_at": self.utc_now_iso(),
                "last_error": None,
                "last_error_category": None,
                "last_error_at": None,
                "profile_id": profile_id,
                "allow_from": list(allow_from),
                "dropped_sender_count": dropped_count,
                "last_dropped_sender_id": sender_id,
                "last_dropped_sender_username": sender_username,
                "last_dropped_at": self.utc_now_iso(),
            },
        )
