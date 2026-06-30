from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, Tuple

from server_modules import internal_tool_markup_service
from server_modules import secret_redaction_service


_RED_MEMORY_PATTERNS: Tuple[tuple[str, re.Pattern[str]], ...] = (
    ("red_sensitivity_label", re.compile(r"(?im)^\s*(red\s*[:=-]|sensitivity\s*:\s*red\b|classification\s*:\s*red\b)")),
    ("private_memory_marker", re.compile(r"\b(RED_MEMORY|PRIVATE_MEMORY|SAGE_PRIVATE_CONTEXT|RAW_MEMORY)\b", re.I)),
    ("secret_instruction_leak", re.compile(r"\b(system prompt|developer instructions?|hidden instructions?|internal policy)\b\s*[:=-]", re.I)),
)
_REDACTED_PLACEHOLDER_RE = re.compile(
    r"\[redacted-(?:secret|token|private-key|phone|private-context)\]|\[redacted\]",
    re.I,
)
_REDACTED_PLACEHOLDER_FLOOD_LIMIT = 24


def _collapse_redacted_placeholder_flood(value: str) -> Tuple[str, bool]:
    if len(_REDACTED_PLACEHOLDER_RE.findall(value)) < _REDACTED_PLACEHOLDER_FLOOD_LIMIT:
        return value, False
    return "[redacted-sensitive-output]", True


@dataclass(frozen=True)
class ResponseLeakGuardResult:
    text: str
    redacted: bool = False
    blocked: bool = False
    findings: Tuple[str, ...] = field(default_factory=tuple)

    def metadata(self) -> Dict[str, Any]:
        return {
            "redacted": self.redacted,
            "blocked": self.blocked,
            "findings": list(self.findings),
        }


def guard_model_response(value: Any) -> ResponseLeakGuardResult:
    raw = str(value or "")
    removed_internal_tool_markup = internal_tool_markup_service.detect_internal_tool_markup(raw)
    without_internal_tool_markup = (
        internal_tool_markup_service.strip_internal_tool_markup(raw)
        if removed_internal_tool_markup
        else raw
    )
    findings = [name for name, pattern in _RED_MEMORY_PATTERNS if pattern.search(without_internal_tool_markup)]
    redacted = secret_redaction_service.redact_text(without_internal_tool_markup)
    redacted_changed = redacted != without_internal_tool_markup
    redacted, collapsed_redacted_flood = _collapse_redacted_placeholder_flood(redacted)
    if findings:
        guarded = without_internal_tool_markup
        for _name, pattern in _RED_MEMORY_PATTERNS:
            guarded = pattern.sub("[redacted-private-context]", guarded)
        guarded = secret_redaction_service.redact_text(guarded)
        guarded, guarded_collapsed_redacted_flood = _collapse_redacted_placeholder_flood(guarded)
        return ResponseLeakGuardResult(
            text=guarded,
            redacted=True,
            blocked=False,
            findings=tuple(sorted(set([
                *findings,
                *([] if not redacted_changed else ["secret_pattern"]),
                *(["redacted_placeholder_flood"] if collapsed_redacted_flood or guarded_collapsed_redacted_flood else []),
                *(["internal_tool_markup"] if removed_internal_tool_markup else []),
            ]))),
        )
    return ResponseLeakGuardResult(
        text=redacted,
        redacted=redacted_changed or removed_internal_tool_markup or collapsed_redacted_flood,
        blocked=False,
        findings=tuple([
            *(["internal_tool_markup"] if removed_internal_tool_markup else []),
            *(["secret_pattern"] if redacted_changed else []),
            *(["redacted_placeholder_flood"] if collapsed_redacted_flood else []),
        ]),
    )


def guard_stream_delta(value: Any) -> str:
    return guard_model_response(value).text
