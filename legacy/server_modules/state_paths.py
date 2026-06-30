from __future__ import annotations

import os
from pathlib import Path
from typing import Callable


EnvGetter = Callable[[str, str], str | None]


def empyralis_state_home(env_get: EnvGetter = os.getenv) -> Path:
    default_home = Path.home() / ".empyralis" / "state"
    raw = str(env_get("EMPYRALIS_STATE_HOME", "") or "").strip()
    return Path(raw).expanduser() if raw else default_home


def resolve_state_path(
    env_name: str,
    default_relative: str,
    env_get: EnvGetter = os.getenv,
) -> Path:
    explicit = str(env_get(env_name, "") or "").strip()
    if explicit:
        return Path(explicit).expanduser()
    return (empyralis_state_home(env_get) / default_relative).expanduser()


def runtime_state_db_path(env_get: EnvGetter = os.getenv) -> Path:
    return resolve_state_path("ORION_RUNTIME_STATE_DB", "runtime/state.db", env_get)


def cognitive_db_path(env_get: EnvGetter = os.getenv) -> Path:
    return resolve_state_path("ORION_COGNITIVE_DB_PATH", "memory/agency_memory.db", env_get)
