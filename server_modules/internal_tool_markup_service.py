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

# MAN-303 (production, 2026-08-04): a model on a text-tool-calling fallback
# path (deepseek-reasoner, no native tool_calls that iteration) wrote a
# literal "<memorywrite>...</memorywrite>" block into its final reply
# alongside a real, separately-issued native tool_calls entry that actually
# executed. Nothing recognized "<memorywrite>" as internal markup — it isn't
# the DSML format above — so it rendered raw to the user. This is a second,
# independent class of internal-looking markup: not a specific provider's
# transport encoding, but a model narrating (or hallucinating) one of THIS
# platform's own tool names as an XML-ish tag instead of a real structured
# call. Anchored to the platform's actual flat tool-name vocabulary (see
# direct_chat_generation_service.py's _MEMORY_TOOL_NAMES/
# _TOOL_CALL_NOTATION_RE and no_provider_service.py's tool_names) — never a
# generic "<anything>" sweep — so ordinary HTML/XML a user asked to see is
# never touched. Normalized (letters+digits only, case-insensitive) before
# comparison so "<memorywrite>" matches the real tool "memory_write" even
# though the model didn't reproduce the underscore.
_KNOWN_TOOL_IDENTIFIERS = {
    "memory_write", "memory_read", "memory_search", "memory_get", "memory_list",
    "find_workspace_memory_entry", "memory_context",
    "shell__exec", "file__read", "file__write", "file__delete",
    "http_request", "web__search", "web__fetch",
    "browser__navigate", "browser__extract_text",
    "hardware__action", "screenshot__capture",
    "task_complete", "query_tool_registry", "update_plan",
}


def _normalize_tag_identifier(value: Any) -> str:
    return re.sub(r"[^a-z0-9]", "", str(value or "").strip().lower())


_KNOWN_TOOL_IDENTIFIERS_NORMALIZED = {_normalize_tag_identifier(name) for name in _KNOWN_TOOL_IDENTIFIERS}

# A bare XML-ish tag: "<name>" or "</name>" or "<name/>". Deliberately not
# paired with its own closer here — an unclosed opening tag (generation cut
# off mid-block, exactly like the DSML start-marker case above) must still
# be caught, not just a complete open/close pair.
_GENERIC_TAG_RE = re.compile(r"<\s*/?\s*([a-zA-Z_][a-zA-Z0-9_]{2,40})\s*/?\s*>")


def _find_hallucinated_tool_tag_start(raw: str) -> int:
    for match in _GENERIC_TAG_RE.finditer(raw):
        if _normalize_tag_identifier(match.group(1)) in _KNOWN_TOOL_IDENTIFIERS_NORMALIZED:
            return match.start()
    return -1


def detect_internal_tool_markup(value: Any) -> bool:
    raw = str(value or "")
    if _INTERNAL_TOOL_MARKUP_RE.search(raw) or _INTERNAL_TOOL_MARKUP_START_RE.search(raw):
        return True
    return _find_hallucinated_tool_tag_start(raw) >= 0


def strip_internal_tool_markup(value: Any) -> str:
    raw = str(value or "")
    if not raw:
        return ""
    match = _INTERNAL_TOOL_MARKUP_RE.search(raw) or _INTERNAL_TOOL_MARKUP_START_RE.search(raw)
    cut_at = match.start() if match else -1
    tag_start = _find_hallucinated_tool_tag_start(raw)
    if tag_start >= 0 and (cut_at < 0 or tag_start < cut_at):
        cut_at = tag_start
    if cut_at < 0:
        return raw.strip()
    return raw[:cut_at].strip()


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
