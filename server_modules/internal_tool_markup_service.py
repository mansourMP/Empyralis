from __future__ import annotations

import html
import json
import re
from typing import Any, Dict, Iterator, List, Tuple


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
    "memory_write_private", "memory_get_private",
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


# ── Textual tool-call narration (MAN-263) ────────────────────────────────────
# DSML (above) is one shape a provider can leak a tool call in. It is not the
# only one. MAN-263's recorded incident was bare/fenced JSON — a
# hardware__action call genuinely succeeded, and the SYNTHESIS turn
# afterward (the round that is supposed to turn a real tool result into
# prose) replied:
#   "I'll actually make the call now.\n```json\n{\"tool\": \"hardware__
#   action\", \"arguments\": {\"command\": \"uname -a\"}}\n```"
# Two things distinguish this from the DSML case extract_trusted_dsml_tool_
# calls exists for: (1) the shape (plain JSON, not the DSML envelope), and
# (2) the turn (synthesis, AFTER a real success, not the original
# invocation) — a tool call already ran for real this turn, so recovering
# this text and executing it the way extract_trusted_dsml_tool_calls does
# for a genuine invocation-turn miss would double-execute a side-effecting
# command. That is a materially worse outcome than the original bug (see
# tool_honesty_guard.py's narrates_tool_call_after_success, the only caller
# of this function): it never executes anything itself, it only extracts
# what a reply DEPICTS as a call so a caller holding the turn's real tool
# trace can decide whether the reply is dishonest narration of something
# that already happened.
def _iter_balanced_json_objects(text: str) -> Iterator[str]:
    """Yield each top-level, brace-balanced `{...}`-shaped substring of
    `text`, string-aware so a brace inside a quoted JSON string value never
    desyncs the count. Candidates are not validated as JSON here — the only
    caller (extract_textual_tool_call_mentions) attempts json.loads on each
    and discards anything that doesn't parse. This is a plain text scan; it
    never executes or interprets anything, only locates plausible tool-call-
    shaped payloads a model printed as prose instead of a structured call."""
    depth = 0
    start: int | None = None
    in_string = False
    escape = False
    for index, char in enumerate(text):
        if in_string:
            if escape:
                escape = False
            elif char == "\\":
                escape = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
            continue
        if char == "{":
            if depth == 0:
                start = index
            depth += 1
        elif char == "}":
            if depth > 0:
                depth -= 1
                if depth == 0 and start is not None:
                    yield text[start : index + 1]
                    start = None


_TEXTUAL_TOOL_CALL_NAME_KEYS = ("tool", "name", "tool_name")
_TEXTUAL_TOOL_CALL_ARGS_KEYS = ("arguments", "parameters", "input", "args")


def extract_textual_tool_call_mentions(value: Any) -> List[Dict[str, Any]]:
    """Detection only — this never executes anything and is never wired to
    any executor. Finds tool-call-shaped JSON payloads sitting in plain
    reply TEXT: a fenced ```json {"tool": "...", "arguments": {...}}```
    block (the MAN-263 shape), bare inline JSON with the same keys, or an
    OpenAI-native {"type": "function", "function": {"name": ...,
    "arguments": ...}} envelope printed as prose instead of emitted as a
    real structured call. Deliberately narrow — BOTH a name-shaped string
    key AND an arguments-shaped container must be present — so ordinary
    JSON a reply might legitimately quote (a log line, a config snippet)
    does not false-positive; a person's `{"name": "Alice"}` has no
    arguments/parameters/input/args sibling and is skipped.

    Returns a list of {"name", "arguments", "raw"} dicts, name normalized
    the same way DSML tool names are (aliases, case, dashes) so a caller
    comparing against a real tool trace entry's name matches reliably.
    """
    raw = str(value or "")
    if not raw or "{" not in raw:
        return []
    mentions: List[Dict[str, Any]] = []
    seen_raw: set[str] = set()
    for candidate in _iter_balanced_json_objects(raw):
        if candidate in seen_raw:
            continue
        seen_raw.add(candidate)
        try:
            parsed = json.loads(candidate)
        except (ValueError, TypeError):
            continue
        if not isinstance(parsed, dict):
            continue
        name = ""
        function_block = parsed.get("function")
        if isinstance(function_block, dict) and isinstance(function_block.get("name"), str):
            name = function_block["name"].strip()
        if not name:
            for key in _TEXTUAL_TOOL_CALL_NAME_KEYS:
                candidate_name = parsed.get(key)
                if isinstance(candidate_name, str) and candidate_name.strip():
                    name = candidate_name.strip()
                    break
        if not name:
            continue
        has_args_shape = isinstance(function_block, dict) or any(
            key in parsed for key in _TEXTUAL_TOOL_CALL_ARGS_KEYS
        )
        if not has_args_shape:
            continue
        arguments: Dict[str, Any] = {}
        if isinstance(function_block, dict):
            raw_arguments = function_block.get("arguments")
            if isinstance(raw_arguments, dict):
                arguments = raw_arguments
            elif isinstance(raw_arguments, str):
                try:
                    parsed_arguments = json.loads(raw_arguments)
                except (ValueError, TypeError):
                    parsed_arguments = None
                if isinstance(parsed_arguments, dict):
                    arguments = parsed_arguments
        else:
            for key in _TEXTUAL_TOOL_CALL_ARGS_KEYS:
                if isinstance(parsed.get(key), dict):
                    arguments = parsed[key]
                    break
        mentions.append({
            "name": _normalize_dsml_tool_name(name),
            "arguments": arguments,
            "raw": candidate,
        })
    return mentions
