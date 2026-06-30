from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any, Iterable


class AutopilotChannelSupportService:
    def __init__(
        self,
        *,
        error_category_hints: Iterable[tuple[str, str]],
        utc_now_iso,
        normalize_whatsapp_number,
        safe_path_token,
        env_get,
    ) -> None:
        self.error_category_hints = tuple((str(a), str(b)) for a, b in error_category_hints)
        self.utc_now_iso = utc_now_iso
        self.normalize_whatsapp_number = normalize_whatsapp_number
        self.safe_path_token = safe_path_token
        self.env_get = env_get

    def classify_error(self, detail: Any) -> str:
        text = str(detail or "").strip().lower()
        if not text:
            return "unknown"
        for hint, category in self.error_category_hints:
            if hint in text:
                return category
        return "unknown"

    def iso_from_epoch(self, ts: float) -> str:
        value = float(ts)
        return datetime.fromtimestamp(value, timezone.utc).isoformat().replace("+00:00", "Z")

    def telegram_autopilot_log(self, message: str) -> None:
        ts = self.utc_now_iso()
        print(f"[telegram-autopilot {ts}] {message}", flush=True)

    def telegram_session_key(self, chat_id: str) -> str:
        cid = str(chat_id or "").strip()
        return f"telegram:{cid}" if cid else "telegram:unknown"

    def telegram_trace_id(self, chat_id: str, update_id: Any, message_id: Any = "") -> str:
        cid = self.safe_path_token(chat_id or "unknown")
        upid = self.safe_path_token(update_id or "0")
        mid = self.safe_path_token(message_id or "")
        if mid:
            return f"tg:{cid}:{upid}:{mid}"
        return f"tg:{cid}:{upid}"

    def whatsapp_session_key(self, from_number: str, to_number: str) -> str:
        sender = self.normalize_whatsapp_number(from_number) or "whatsapp:unknown"
        receiver = self.normalize_whatsapp_number(to_number) or "whatsapp:unknown"
        return f"whatsapp:{sender}->{receiver}"

    def truncate_one_line(self, text: str, limit: int) -> str:
        flat = re.sub(r"\s+", " ", str(text or "")).strip()
        cap = max(120, limit)
        if len(flat) <= cap:
            return flat
        return flat[: cap - 1].rstrip() + "…"

    def include_run_meta(self) -> bool:
        raw = str(self.env_get("ORION_AUTOPILOT_INCLUDE_RUN_META", "0") or "").strip().lower()
        return raw in {"1", "true", "yes", "on"}
