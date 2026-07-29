"""Did this tool call actually fail? — one structured verdict, every consumer.

Replaces the substring sniff this used to be (direct_chat_generation_service.py
had `result_summary[:80].lstrip().startswith('{"error"') or '"error":' in
str(tool_result)[:120]`), which had two symmetrical defects:

  * FALSE NEGATIVES — it only recognised one failure shape out of the dozen
    this codebase actually produces. A survey of every producer that reaches
    the direct-chat tool loop found `{"ok": false, "error": "..."}` (subagent,
    fleet), `{"status": "error" | "not_found", "error": "..."}` (filesystem),
    `{"status": "offline" | "degraded" | "failed", "reason": "..."}`
    (hardware/gateway), `{"exit_code": 1, ...}` with no error key at all
    (shell), `{"status": "incomplete", "errors": [...]}` (memory search) and
    MCP's `isError` — every one of which rendered a green "completed" row and
    let the honesty guard treat the reply as anchored on a real result.

  * FALSE POSITIVES — it read the first 120 characters of the result as TEXT.
    `local_tool_executor.filesystem_read` puts file `content` first, so
    reading a log or a JSON config whose opening bytes contain `"error":`
    marked a perfectly successful read as failed. That is the worse of the
    two errors (it makes the guard challenge an honest reply), and it is why
    this module inspects PARSED TOP-LEVEL KEYS ONLY and never scans result
    text for error-ish words. A tool that summarises a bug report, reads a
    stack trace, or greps a log is indistinguishable from a working tool as
    far as this module is concerned — by design.

PRECEDENCE, in order:
  1. explicit failure flags   (isError true, ok/success false)
  2. structured failure fields (truthy top-level `error`, failure `status`,
                                nonzero `exit_code`, HTTP-ish `code` >= 400)
  3. explicit success flags   (ok/success true, success-vocabulary `status`)
  4. one bounded unwrap into a nested `result`/`payload` envelope
  5. ambiguous -> NOT failed  (conservative: never invent a failure)

Explicit beats inferred, and failure beats success within the explicit tier —
so `{"status": "completed", "exit_code": 1}` (the gateway shell envelope,
which hardcodes its status) is correctly a failure.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Optional

# How many `{"reply": ..., "result": {...}}`-style envelopes to unwrap before
# giving up. MCP's format_mcp_tool_result is the one real producer of these
# and needs exactly one level; the cap stops a pathological payload from
# turning classification into a deep tree walk.
_MAX_UNWRAP_DEPTH = 2

# Only the OUTERMOST value is ever parsed as JSON. A nested JSON *string* is
# left alone — it is content, not envelope.
_MAX_PARSE_BYTES = 2_000_000

_FAILURE_STATUS_TOKENS = frozenset(
    {
        "error",
        "errored",
        "fail",
        "failed",
        "failure",
        "timeout",
        "timed_out",
        "not_found",
        "offline",
        "degraded",
        "unavailable",
        "denied",
        "blocked",
        "rejected",
        "refused",
        "unauthorized",
        "forbidden",
        "aborted",
        "cancelled",
        "canceled",
        "incomplete",
    }
)

# Deliberately includes the PENDING states (`waiting_approval`, `queued`,
# `running`). A hardware action parked on an approval has not failed — the
# activity row should not go red and the guard should not treat the turn as
# tool-less — so they resolve as "not failed" rather than falling through to
# the ambiguous branch by accident.
_SUCCESS_STATUS_TOKENS = frozenset(
    {
        "ok",
        "okay",
        "success",
        "succeeded",
        "successful",
        "complete",
        "completed",
        "done",
        "finished",
        "truncated",
        "matches_found",
        "no_matches",
        "not_searched",
        "waiting_approval",
        "pending",
        "queued",
        "running",
        "in_progress",
        "accepted",
    }
)

_OK_FLAG_KEYS = ("ok", "success", "succeeded", "successful")
_ERROR_FLAG_KEYS = ("iserror", "is_error", "error_flag")
_STATUS_KEYS = ("status", "state", "outcome")
_EXIT_CODE_KEYS = ("exit_code", "exitcode", "returncode", "return_code")
# `error` is the only key that both DETECTS a failure and carries its text.
# The rest are consulted for a human-readable message once a failure is
# already established — never to establish one, because "message"/"reason"/
# "detail" are perfectly ordinary keys on successful payloads.
_ERROR_KEYS = ("error",)
_ERROR_TEXT_KEYS = ("error", "error_message", "errormessage", "reason", "detail", "message", "stderr")
_NESTED_PAYLOAD_KEYS = ("result", "payload", "data", "response")


@dataclass(frozen=True, slots=True)
class ToolOutcome:
    """failed: the verdict every consumer uses.
    signal: which tier decided it (see the module docstring's precedence list),
        or "none" when nothing in the result said either way.
    reason: short machine token for logs/tests, e.g. "status_failed".
    error_text: best-effort human message. NOT redacted — callers that put it
        in front of a user must run it through secret_redaction_service, the
        same way args_preview is handled.
    """

    failed: bool
    signal: str
    reason: str
    error_text: str = ""


_AMBIGUOUS = ToolOutcome(failed=False, signal="none", reason="no_signal")


def _lower_keyed(value: dict[Any, Any]) -> dict[str, Any]:
    """Case-insensitive top-level view. MCP uses `isError`, our own producers
    use snake_case; matching on a normalized key avoids hand-listing both."""
    out: dict[str, Any] = {}
    for key, item in value.items():
        out.setdefault(str(key).strip().lower(), item)
    return out


def _is_truthy_error(value: Any) -> bool:
    """A truthy `error` field. `None`, `""`, `[]`, `{}`, `False` and `0` all
    mean "no error" — `{"ok": true, "error": null}` is a success payload, and
    treating it as a failure was one of the shapes that made the old sniff
    unusable in reverse."""
    if value is None or value is False:
        return False
    if isinstance(value, (str, bytes)):
        return bool(str(value).strip())
    if isinstance(value, (list, tuple, dict, set)):
        return bool(value)
    if isinstance(value, (int, float)):
        return bool(value)
    return True


def _error_text_from(mapping: dict[str, Any]) -> str:
    for key in _ERROR_TEXT_KEYS:
        value = mapping.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
        if isinstance(value, dict):
            nested = value.get("message") or value.get("detail") or value.get("error")
            if isinstance(nested, str) and nested.strip():
                return nested.strip()
        if isinstance(value, list) and value:
            first = value[0]
            if isinstance(first, str) and first.strip():
                return first.strip()
            if isinstance(first, dict):
                nested = first.get("message") or first.get("reason") or first.get("detail")
                if isinstance(nested, str) and nested.strip():
                    return nested.strip()
    return ""


def _classify_mapping(value: dict[Any, Any], depth: int) -> ToolOutcome:
    mapping = _lower_keyed(value)
    error_text = _error_text_from(mapping)

    # --- Tier 1: explicit flags the producer set on purpose ----------------
    for key in _ERROR_FLAG_KEYS:
        if mapping.get(key) is True:
            return ToolOutcome(True, "explicit_flag", f"{key}_true", error_text)
    for key in _OK_FLAG_KEYS:
        if mapping.get(key) is False:
            return ToolOutcome(True, "explicit_flag", f"{key}_false", error_text)
    if mapping.get("failed") is True:
        return ToolOutcome(True, "explicit_flag", "failed_true", error_text)

    # --- Tier 2: structured failure fields ---------------------------------
    for key in _ERROR_KEYS:
        if key in mapping and _is_truthy_error(mapping.get(key)):
            return ToolOutcome(True, "error_field", f"{key}_present", error_text)

    for key in _STATUS_KEYS:
        token = mapping.get(key)
        if isinstance(token, str) and token.strip().lower() in _FAILURE_STATUS_TOKENS:
            return ToolOutcome(True, "status_field", f"status_{token.strip().lower()}", error_text)

    for key in _EXIT_CODE_KEYS:
        code = mapping.get(key)
        # bool is an int subclass in Python — exclude it so {"exit_code": True}
        # (nobody's real shape) can't masquerade as exit code 1.
        if isinstance(code, int) and not isinstance(code, bool) and code != 0:
            return ToolOutcome(True, "exit_code", f"exit_code_{code}", error_text or f"exit code {code}")

    # HTTP/JSON-RPC-style envelope. `status_code` is deliberately NOT consulted:
    # browser__navigate returns {"url", "title", "status_code": 404} for a
    # navigation that SUCCEEDED to a page that 404s — the tool worked, and
    # painting that red would be exactly the false positive this module exists
    # to avoid. A bare top-level `code` has no such benign meaning here.
    code = mapping.get("code")
    if isinstance(code, int) and not isinstance(code, bool) and code >= 400:
        return ToolOutcome(True, "http_code", f"code_{code}", error_text or f"HTTP {code}")

    # --- Tier 3: explicit success stops the walk ---------------------------
    for key in _OK_FLAG_KEYS:
        if mapping.get(key) is True:
            return ToolOutcome(False, "explicit_flag", f"{key}_true")
    for key in _STATUS_KEYS:
        token = mapping.get(key)
        if isinstance(token, str) and token.strip().lower() in _SUCCESS_STATUS_TOKENS:
            return ToolOutcome(False, "status_field", f"status_{token.strip().lower()}")

    # --- Tier 4: one bounded unwrap of a {"reply": ..., "result": {...}}
    # envelope (MCP's format_mcp_tool_result shape). Only reached when the
    # OUTER dict said nothing either way, so an explicit outer success can
    # never be overturned by something inside an upstream payload.
    if depth < _MAX_UNWRAP_DEPTH:
        for key in _NESTED_PAYLOAD_KEYS:
            nested = mapping.get(key)
            if isinstance(nested, (dict, list)):
                inner = _classify_value(nested, depth + 1)
                if inner.signal != "none":
                    return ToolOutcome(inner.failed, inner.signal, f"{key}.{inner.reason}", inner.error_text)

    return _AMBIGUOUS


def _classify_sequence(value: list[Any], depth: int) -> ToolOutcome:
    """Lists are mostly legitimate result collections (`[]` from list_issues
    is a success), so a list is only ever a verdict when it is the
    single-element wrapper MCP content arrays use. Anything longer stays
    ambiguous rather than letting one error-ish record in a batch condemn the
    whole call."""
    if len(value) == 1 and isinstance(value[0], (dict, list)) and depth < _MAX_UNWRAP_DEPTH:
        return _classify_value(value[0], depth + 1)
    return _AMBIGUOUS


def _classify_value(value: Any, depth: int) -> ToolOutcome:
    if isinstance(value, dict):
        return _classify_mapping(value, depth)
    if isinstance(value, list):
        return _classify_sequence(value, depth)
    return _AMBIGUOUS


def classify_tool_result(result: Any) -> ToolOutcome:
    """The verdict. `result` is whatever the tool layer handed back — a JSON
    string (the usual case in the direct-chat loop), an already-parsed dict or
    list, or None.

    Plain, non-JSON text is ALWAYS ambiguous (-> not failed). Several real
    failures are bare prose ("agent_computer_offline", a gateway readiness
    reason) and are genuinely undetectable here; guessing from wording is the
    false-positive trap this module refuses to walk into. Fix those at the
    producer by giving them a structured shape, not with a word list.
    """
    if result is None:
        return ToolOutcome(True, "empty", "no_result", "The tool returned no result.")

    if isinstance(result, (bytes, bytearray)):
        try:
            result = bytes(result).decode("utf-8", "replace")
        except Exception:
            return _AMBIGUOUS

    if isinstance(result, str):
        token = result.strip()
        if not token:
            return ToolOutcome(True, "empty", "empty_result", "The tool returned an empty result.")
        if len(token) > _MAX_PARSE_BYTES or token[0] not in "{[":
            return _AMBIGUOUS
        try:
            parsed = json.loads(token)
        except Exception:
            # Truncated/invalid JSON (format_mcp_tool_result slices its output
            # at 8000 chars and can cut mid-object) — unparseable is ambiguous,
            # not failed.
            return _AMBIGUOUS
        return _classify_value(parsed, 0)

    return _classify_value(result, 0)


def tool_result_failed(result: Any) -> bool:
    """Boolean convenience wrapper for call sites that only need the verdict."""
    return classify_tool_result(result).failed


def failure_summary(result: Any, *, fallback: str = "") -> Optional[str]:
    """The human-readable reason a call failed, or None if it didn't. NOT
    redacted — see ToolOutcome.error_text."""
    outcome = classify_tool_result(result)
    if not outcome.failed:
        return None
    return outcome.error_text or fallback or outcome.reason
