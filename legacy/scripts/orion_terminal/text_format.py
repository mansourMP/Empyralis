from __future__ import annotations

import re
from typing import Any


def flatten_text(value: Any) -> str:
    text = str(value or "")
    return re.sub(r"\s+", " ", text).strip()


def preview_text(value: Any, max_chars: int) -> str:
    text = flatten_text(value)
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 1].rstrip() + "…"


def compact_summary_line(value: Any) -> str:
    text = flatten_text(value).lower()
    if not text:
        return "Completed."
    if "execution plan" in text:
        return "Execution plan generated."
    if "weekly content" in text:
        return "Weekly content plan generated."
    if "competitor brief" in text:
        return "Competitor brief generated."
    if "local worker drafted" in text:
        return "Local companion completed draft output."
    if "run completed" in text:
        return "Run completed."
    return preview_text(value, 96)


def mask_credential_label(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return "credential"
    looks_sensitive = text.startswith("sk-") or bool(re.fullmatch(r"[A-Za-z0-9_\-]{24,}", text))
    if looks_sensitive or len(text) > 28:
        head = text[:6]
        tail = text[-4:] if len(text) > 10 else ""
        return f"{head}...{tail}" if tail else f"{head}..."
    return text

