from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Dict


@dataclass
class SessionRuntimeHandle:
    session_id: str
    actor_key: str
    workspace_id: str = ""
    user_id: str = ""
    cwd: str = ""
    runtime_options: Dict[str, Any] = field(default_factory=dict)
    meta: Dict[str, Any] = field(default_factory=dict)
    browser: Any = None
    last_touched_at: float = field(default_factory=time.time)


@dataclass
class CachedRuntimeSnapshot:
    actor_key: str
    state: SessionRuntimeHandle
    last_touched_at: float
    idle_ms: float
