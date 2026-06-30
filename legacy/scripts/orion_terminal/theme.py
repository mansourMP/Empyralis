from __future__ import annotations

import os
import re
import sys
import shutil
from typing import List, Optional

def color_enabled() -> bool:
    if os.environ.get("NO_COLOR"):
        return False
    return bool(sys.stdout.isatty())


def tint(text: str, code: str) -> str:
    if not color_enabled():
        return text
    return f"\033[{code}m{text}\033[0m"


def terminal_theme() -> str:
    forced = os.getenv("ORION_CLI_TERMINAL_THEME", "auto").strip().lower()
    if forced in {"light", "dark"}:
        return forced

    colorfgbg = os.getenv("COLORFGBG", "")
    if colorfgbg:
        parts = [p.strip() for p in re.split(r"[;:]", colorfgbg) if p.strip()]
        for raw in reversed(parts):
            if raw.isdigit():
                bg = int(raw)
                if bg in {7, 15}:
                    return "light"
                if bg in {0, 8}:
                    return "dark"
                break
    return "auto"


def _palette_code(kind: str) -> str:
    profile = os.getenv("ORION_CLI_COLOR_PROFILE", "purple").strip().lower()

    minimal_neutral = {
        "strong": "1",
        "accent": "1;33",
        "muted": "38;5;244",
        "rail": "38;5;245",
        "active": "1;33",
        "ok": "1;32",
        "warn": "1;33",
        "err": "1;31",
    }
    minimal_dark = {
        "strong": "1;38;5;255",
        "accent": "1;38;5;214",
        "muted": "2;38;5;245",
        "rail": "38;5;110",
        "active": "1;38;5;222",
        "ok": "1;38;5;114",
        "warn": "1;38;5;216",
        "err": "1;38;5;203",
    }
    minimal_light = {
        "strong": "1;38;5;16",
        "accent": "1;38;5;130",
        "muted": "38;5;240",
        "rail": "38;5;66",
        "active": "1;38;5;131",
        "ok": "1;38;5;28",
        "warn": "1;38;5;130",
        "err": "1;38;5;124",
    }

    professional_neutral = {
        "strong": "1",
        "accent": "1;38;5;74",
        "muted": "38;5;244",
        "rail": "38;5;67",
        "active": "1;38;5;81",
        "ok": "1;38;5;34",
        "warn": "1;38;5;136",
        "err": "1;38;5;124",
    }
    professional_dark = {
        "strong": "1;38;5;255",
        "accent": "1;38;5;81",
        "muted": "2;38;5;245",
        "rail": "38;5;74",
        "active": "1;38;5;87",
        "ok": "1;38;5;71",
        "warn": "1;38;5;179",
        "err": "1;38;5;167",
    }
    professional_light = {
        "strong": "1;38;5;16",
        "accent": "1;38;5;31",
        "muted": "38;5;240",
        "rail": "38;5;67",
        "active": "1;38;5;25",
        "ok": "1;38;5;28",
        "warn": "1;38;5;130",
        "err": "1;38;5;124",
    }

    purple_neutral = {
        "strong": "1",
        "accent": "1;38;5;141",
        "muted": "38;5;244",
        "rail": "38;5;109",
        "active": "1;38;5;183",
        "ok": "1;32",
        "warn": "1;33",
        "err": "1;31",
    }
    purple_dark = {
        "strong": "1;38;5;255",
        "accent": "1;38;5;177",
        "muted": "2;38;5;245",
        "rail": "38;5;110",
        "active": "1;38;5;189",
        "ok": "1;38;5;114",
        "warn": "1;38;5;216",
        "err": "1;38;5;203",
    }
    purple_light = {
        "strong": "1;38;5;16",
        "accent": "1;38;5;98",
        "muted": "38;5;240",
        "rail": "38;5;66",
        "active": "1;38;5;98",
        "ok": "1;38;5;28",
        "warn": "1;38;5;130",
        "err": "1;38;5;124",
    }
    theme = terminal_theme()
    if profile == "purple":
        neutral = purple_neutral
        dark = purple_dark
        light = purple_light
    elif profile == "minimal":
        neutral = minimal_neutral
        dark = minimal_dark
        light = minimal_light
    else:
        neutral = professional_neutral
        dark = professional_dark
        light = professional_light
    if theme == "light":
        palette = light
    elif theme == "dark":
        palette = dark
    else:
        palette = neutral
    return palette.get(kind, dark["strong"])


def strong(text: str) -> str:
    return tint(text, _palette_code("strong"))


def accent(text: str) -> str:
    return tint(text, _palette_code("accent"))


def muted(text: str) -> str:
    return tint(text, _palette_code("muted"))


def rail(text: str) -> str:
    return tint(text, _palette_code("rail"))


def active(text: str) -> str:
    return tint(text, _palette_code("active"))


def ok(text: str) -> str:
    return tint(text, _palette_code("ok"))


def warn(text: str) -> str:
    return tint(text, _palette_code("warn"))


def err(text: str) -> str:
    return tint(text, _palette_code("err"))

def _shape_markers(ascii_mode: bool) -> tuple[str, str]:
    if ascii_mode:
        return "*", "o"
    shape = os.getenv("ORION_CLI_SHAPE", "hex").strip().lower()
    if shape == "hex":
        return "⬢", "⬡"
    if shape == "diamond":
        return "◆", "◇"
    return "●", "○"
