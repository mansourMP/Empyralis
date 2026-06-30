from __future__ import annotations

import html
import re
from typing import Any, Dict, List, Tuple


_DSML_DELIMITER_RE = r"(?:[|｜]\s*[|｜])"
_DSML_PREFIX_RE = rf"<\s*{_DSML_DELIMITER_RE}\s*DSML\s*{_DSML_DELIMITER_RE}\s*"
_DSML_CLOSE_PREFIX_RE = rf"<\s*/\s*{_DSML_DELIMITER_RE}\s*DSML\s*{_DSML_DELIMITER_RE}\s*"
_INTERNAL_TOOL_MARKUP_START_RE = re.compile(
    rf"<\s*/?\s*{_DSML_DELIMITER_RE}\s*DSML",
    re.IGNORECASE | re.DOTALL,
)
_INTERNAL_TOOL_MARKUP_RE = re.compile(
    _DSML_PREFIX_RE + r"(?:tool_calls\s*>?)?.*",
    re.IGNORECASE | re.DOTALL,
)
_DSML_TOOL_BLOCK_RE = re.compile(
    _DSML_PREFIX_RE + r"tool_calls\s*>.*",
    re.IGNORECASE | re.DOTALL,
)
_DSML_INVOKE_RE = re.compile(
    _DSML_PREFIX_RE
    + r"invoke\s+name=[\"']([^\"']+)[\"']\s*>(.*?)"
    + _DSML_CLOSE_PREFIX_RE
    + r"invoke\s*>",
    re.IGNORECASE | re.DOTALL,
)
_DSML_PARAMETER_RE = re.compile(
    _DSML_PREFIX_RE
    + r"parameter\s+name=[\"']([^\"']+)[\"'][^>]*>(.*?)"
    + _DSML_CLOSE_PREFIX_RE
    + r"parameter\s*>",
    re.IGNORECASE | re.DOTALL,
)
_TRUSTED_DSML_SOURCES = {
    "codex_cli",
    "openai_codex_backend",
    "orion_local_worker_llm",
    "local_worker",
}


def detect_internal_tool_markup(value: Any) -> bool:
    raw = str(value or "")
    return bool(_INTERNAL_TOOL_MARKUP_RE.search(raw) or _INTERNAL_TOOL_MARKUP_START_RE.search(raw))


def strip_internal_tool_markup(value: Any) -> str:
    raw = str(value or "")
    if not raw:
        return ""
    match = _INTERNAL_TOOL_MARKUP_RE.search(raw) or _INTERNAL_TOOL_MARKUP_START_RE.search(raw)
    if not match:
        return raw.strip()
    return raw[: match.start()].strip()


def _normalize_dsml_tool_name(name: Any) -> str:
    normalized = str(name or "").strip().lower().replace("-", "_")
    aliases = {
        "bash": "shell__exec",
        "sh": "shell__exec",
        "shell": "shell__exec",
        "terminal": "shell__exec",
        "zsh": "shell__exec",
    }
    return aliases.get(normalized, normalized)


def extract_trusted_dsml_tool_calls(value: Any, *, source: str) -> Tuple[str, List[Dict[str, Any]]]:
    """Extract DSML tool calls from explicitly trusted adapter output only.

    Sage web chat must not use this to promote assistant prose into execution.
    It exists for compatibility with local worker/Codex transports that can
    encode provider tool calls as DSML text.
    """
    raw = str(value or "")
    if not raw:
        return "", []
    if not _DSML_TOOL_BLOCK_RE.search(raw):
        return (strip_internal_tool_markup(raw), []) if detect_internal_tool_markup(raw) else (raw, [])
    if str(source or "").strip().lower() not in _TRUSTED_DSML_SOURCES:
        return strip_internal_tool_markup(raw), []

    tool_calls: List[Dict[str, Any]] = []
    for invoke_match in _DSML_INVOKE_RE.finditer(raw):
        tool_name = _normalize_dsml_tool_name(invoke_match.group(1))
        if not tool_name:
            continue
        body = invoke_match.group(2) or ""
        parameters: Dict[str, str] = {}
        for parameter_match in _DSML_PARAMETER_RE.finditer(body):
            parameter_name = str(parameter_match.group(1) or "").strip()
            if not parameter_name:
                continue
            parameters[parameter_name] = html.unescape(str(parameter_match.group(2) or "")).strip()
        arguments: Dict[str, Any] = {}
        if tool_name == "shell__exec":
            command = parameters.get("command") or parameters.get("cmd") or parameters.get("input") or ""
            if not command:
                continue
            arguments["command"] = command
            description = parameters.get("description")
            if description:
                arguments["description"] = description
        else:
            arguments = dict(parameters)
        tool_calls.append({"name": tool_name, "arguments": arguments})

    return strip_internal_tool_markup(raw), tool_calls
