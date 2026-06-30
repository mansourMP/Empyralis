from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from typing import Any


class _JsonLogFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        if record.stack_info:
            payload["stack_info"] = record.stack_info
        for key in ("request_id", "workspace_id", "run_id", "user_id", "path", "method", "status_code"):
            value = getattr(record, key, None)
            if value is not None:
                payload[key] = value
        return json.dumps(payload, ensure_ascii=True, separators=(",", ":"))


def _production_mode() -> bool:
    for key in ("ORION_ENV", "ENV"):
        value = str(os.getenv(key) or "").strip().lower()
        if value in {"prod", "production", "staging"}:
            return True
    return False


def _resolve_log_format() -> str:
    value = str(os.getenv("ORION_LOG_FORMAT") or "auto").strip().lower()
    if value in {"json", "text"}:
        return value
    return "json" if _production_mode() else "text"


def configure_logging() -> None:
    root = logging.getLogger()
    handler = logging.StreamHandler()
    log_format = _resolve_log_format()
    if log_format == "json":
        handler.setFormatter(_JsonLogFormatter())
    else:
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s [%(name)s] %(message)s"))

    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(getattr(logging, str(os.getenv("ORION_LOG_LEVEL") or "INFO").strip().upper(), logging.INFO))

    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        logger = logging.getLogger(name)
        logger.handlers.clear()
        logger.propagate = True

