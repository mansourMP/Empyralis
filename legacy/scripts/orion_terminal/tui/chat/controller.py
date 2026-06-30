from __future__ import annotations

from typing import Optional


def resolve_session_label(*, session_filter: Optional[str], channel_filter: Optional[str]) -> str:
    if session_filter:
        return session_filter
    if channel_filter:
        return channel_filter
    return "all"


def resolve_current_scope(session_label: str, trust_key: str) -> str:
    return f"{session_label}|{str(trust_key or '').strip().lower()}"


def resolve_send_hint(route_label: str) -> str:
    _ = route_label
    return ""
