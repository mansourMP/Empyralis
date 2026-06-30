from __future__ import annotations

import os
import re


_VERSION_PARTS_RE = re.compile(r"\d+")


def env_int(name: str, default: int) -> int:
    raw = str(os.getenv(name, str(default))).strip()
    try:
        return int(raw)
    except Exception:
        return int(default)


def env_float(name: str, default: float) -> float:
    raw = str(os.getenv(name, str(default))).strip()
    try:
        return float(raw)
    except Exception:
        return float(default)


def env_bool(name: str, default: bool = False) -> bool:
    raw = str(os.getenv(name, "1" if default else "0")).strip().lower()
    return raw in {"1", "true", "yes", "on"}


def version_tuple(value: str) -> tuple[int, int, int]:
    parts = [int(p) for p in _VERSION_PARTS_RE.findall(str(value or ""))]
    while len(parts) < 3:
        parts.append(0)
    return (parts[0], parts[1], parts[2])


def version_lt(left: str, right: str) -> bool:
    return version_tuple(left) < version_tuple(right)
