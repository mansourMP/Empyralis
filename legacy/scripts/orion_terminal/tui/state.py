from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional, Set


@dataclass
class ChatViewState:
    session_filter: Optional[str] = None
    channel_filter: Optional[str] = None
    manual_session_override: bool = False
    last_fetch_error: str = ""
    last_frame: str = ""
    prefix_notice_scopes: Set[str] = field(default_factory=set)


__all__ = ["ChatViewState"]

