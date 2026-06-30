from __future__ import annotations

import json
import os
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, Iterable, Optional


_REPO_ROOT = Path(__file__).resolve().parents[1]
_CONFIG_PATH = _REPO_ROOT / ".orion-stack" / "config.json"
_SPECIAL_KEYS = {"includes", "defaults"}


def _deep_merge(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    merged = dict(base)
    for key, value in override.items():
        existing = merged.get(key)
        if isinstance(existing, dict) and isinstance(value, dict):
            merged[key] = _deep_merge(existing, value)
        else:
            merged[key] = value
    return merged


def _load_file(path: Path, seen: Optional[set[Path]] = None) -> Dict[str, Any]:
    resolved = path.expanduser().resolve()
    visited = seen or set()
    if resolved in visited or not resolved.exists():
        return {}
    visited.add(resolved)
    try:
        raw = json.loads(resolved.read_text(encoding="utf-8"))
    except Exception:
        return {}
    if not isinstance(raw, dict):
        return {}

    merged: Dict[str, Any] = {}
    includes = raw.get("includes")
    if isinstance(includes, list):
        for item in includes:
            include_path = Path(str(item or "").strip())
            if not include_path.is_absolute():
                include_path = resolved.parent / include_path
            merged = _deep_merge(merged, _load_file(include_path, visited))

    defaults = raw.get("defaults")
    if isinstance(defaults, dict):
        merged = _deep_merge(merged, defaults)

    explicit = {key: value for key, value in raw.items() if key not in _SPECIAL_KEYS}
    merged = _deep_merge(merged, explicit)
    return merged


@lru_cache(maxsize=1)
def load_runtime_config() -> Dict[str, Any]:
    return _load_file(_CONFIG_PATH)


def reload_runtime_config() -> Dict[str, Any]:
    load_runtime_config.cache_clear()
    return load_runtime_config()


def _nested_lookup(payload: Dict[str, Any], key: str) -> Any:
    current: Any = payload
    for token in str(key or "").split("."):
        if not isinstance(current, dict):
            return None
        current = current.get(token)
    return current


def _env_candidates(key: str, legacy: Optional[str] = None) -> Iterable[str]:
    normalized = str(key or "").strip()
    if normalized:
        yield normalized
        dotted = normalized.replace(".", "__")
        if dotted != normalized:
            yield dotted
    if legacy:
        yield str(legacy).strip()


def config_value(key: str, default: Any = None, *, legacy: Optional[str] = None) -> Any:
    for candidate in _env_candidates(key, legacy):
        if candidate and candidate in os.environ:
            return os.environ.get(candidate)
    payload = load_runtime_config()
    value = _nested_lookup(payload, key)
    return default if value is None else value


def config_str(key: str, default: str = "", *, legacy: Optional[str] = None) -> str:
    value = config_value(key, default, legacy=legacy)
    if value is None:
        return str(default)
    return str(value)


def config_bool(key: str, default: bool = False, *, legacy: Optional[str] = None) -> bool:
    value = config_value(key, default, legacy=legacy)
    if isinstance(value, bool):
        return value
    token = str(value or "").strip().lower()
    if token in {"1", "true", "yes", "on"}:
        return True
    if token in {"0", "false", "no", "off"}:
        return False
    return bool(default)


def config_int(key: str, default: int = 0, *, legacy: Optional[str] = None) -> int:
    value = config_value(key, default, legacy=legacy)
    try:
        return int(value)
    except Exception:
        return int(default)


def config_float(key: str, default: float = 0.0, *, legacy: Optional[str] = None) -> float:
    value = config_value(key, default, legacy=legacy)
    try:
        return float(value)
    except Exception:
        return float(default)
