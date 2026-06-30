from __future__ import annotations

import re
import math
from typing import Any, Dict, List, Mapping, Optional


_SENSITIVE_KEY_PARTS = (
    "api_key",
    "apikey",
    "access_token",
    "refresh_token",
    "auth_token",
    "token",
    "authorization",
    "password",
    "secret",
    "credential",
    "credentials",
    "private_key",
    "pairing_token",
    "pairing_code",
    "session_cookie",
    "cookie",
    "set_cookie",
    "gateway_token",
    "session_token",
    "phone",
    "mobile",
)

_SENSITIVE_VALUE_PATTERNS = (
    (re.compile(r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----.*?-----END [A-Z0-9 ]*PRIVATE KEY-----", re.DOTALL), "[redacted-private-key]"),
    (re.compile(r"\bBearer\s+[A-Za-z0-9._~+/=-]+\b", re.IGNORECASE), "Bearer [redacted]"),
    (re.compile(r"(?i)\b(authorization|proxy-authorization)\s*:\s*[^\r\n]+"), r"\1: [redacted]"),
    (re.compile(r"(?i)\b(cookie|set-cookie)\s*:\s*[^\r\n]+"), r"\1: [redacted]"),
    (re.compile(r"(?i)\b(x-api-key|api-key)\s*:\s*[^\r\n]+"), r"\1: [redacted]"),
    (re.compile(r"\b(?:sk|rk)_(?:live|test)_[A-Za-z0-9]{12,}\b"), "[redacted-secret]"),
    (re.compile(r"\bsk-[A-Za-z0-9][A-Za-z0-9_-]{8,}\b"), "[redacted-secret]"),
    (re.compile(r"\b\d{6,12}:[A-Za-z0-9_-]{30,}\b"), "[redacted-secret]"),
    (re.compile(r"\bmfa\.[A-Za-z0-9_-]{20,}\b"), "[redacted-secret]"),
    (re.compile(r"\b[A-Za-z0-9_-]{23,28}\.[A-Za-z0-9_-]{6,8}\.[A-Za-z0-9_-]{27,}\b"), "[redacted-secret]"),
    (re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"), "[redacted-secret]"),
    (re.compile(r"\bgpair_[A-Za-z0-9_-]+\b"), "[redacted-token]"),
    (re.compile(r"\bggt_[A-Za-z0-9_-]+\b"), "[redacted-token]"),
    (re.compile(r"\bAIza[0-9A-Za-z_-]{20,}\b"), "[redacted-secret]"),
    (re.compile(r"\bgh[pousr]_[A-Za-z0-9_]{20,}\b"), "[redacted-secret]"),
    (re.compile(r"\bxox[baprs]-[A-Za-z0-9-]+\b"), "[redacted-secret]"),
    (re.compile(r'(?i)("private_key"\s*:\s*")[^"]+(")'), r'\1[redacted-private-key]\2'),
    (re.compile(r'(?i)("auth"\s*:\s*")[A-Za-z0-9+/=]{8,}(")'), r'\1[redacted-secret]\2'),
    (re.compile(r"(?i)\b(api[_-]?key|token|secret|password)\s*=\s*[^\s&]+"), r"\1=[redacted-secret]"),
    (re.compile(r"(?<!\w)(?:\+?\d[\d\s().-]{7,}\d)(?!\w)"), "[redacted-phone]"),
)

_MAX_RECURSION_DEPTH = 10
_MAX_LIST_ITEMS = 100
_UUID_PATTERN = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.IGNORECASE)
_HEX_IDENTIFIER_PATTERN = re.compile(r"^[0-9a-f]{32,64}$", re.IGNORECASE)
_SAFE_IDENTIFIER_PATTERN = re.compile(r"^[a-z][a-z0-9]*(?:[._-][a-z0-9]+)+$")
_HIGH_ENTROPY_CANDIDATE_PATTERN = re.compile(r"(?<![A-Za-z0-9])([A-Za-z0-9._~+/=-]{20,})(?![A-Za-z0-9])")
_URLISH_TOKEN_PATTERN = re.compile(r"(?i)(?:^|[./])[a-z0-9-]+\.[a-z]{2,}(?:[/:]|$)")


def _shannon_entropy(value: str) -> float:
    token = str(value or "")
    if not token:
        return 0.0
    counts: Dict[str, int] = {}
    for char in token:
        counts[char] = counts.get(char, 0) + 1
    length = float(len(token))
    entropy = 0.0
    for count in counts.values():
        probability = count / length
        entropy -= probability * math.log2(probability)
    return entropy


def _looks_like_high_entropy_secret(value: str) -> bool:
    token = str(value or "").strip()
    if len(token) < 20:
        return False
    if _UUID_PATTERN.match(token):
        return False
    if _HEX_IDENTIFIER_PATTERN.match(token) and len(token) in {32, 40, 64}:
        return False
    if _SAFE_IDENTIFIER_PATTERN.match(token):
        return False
    if _URLISH_TOKEN_PATTERN.search(token):
        return False
    character_classes = sum(
        1
        for matched in (
            any(char.islower() for char in token),
            any(char.isupper() for char in token),
            any(char.isdigit() for char in token),
            any(not char.isalnum() for char in token),
        )
        if matched
    )
    if character_classes < 2:
        return False
    return _shannon_entropy(token) >= 3.5


def is_sensitive_key(key: Any) -> bool:
    normalized = re.sub(r"[^a-z0-9_]+", "_", str(key or "").strip().lower())
    if not normalized:
        return False
    return any(part in normalized for part in _SENSITIVE_KEY_PARTS)


def redact_text(value: Any) -> str:
    redacted = str(value or "")
    for pattern, replacement in _SENSITIVE_VALUE_PATTERNS:
        redacted = pattern.sub(replacement, redacted)
    redacted = _HIGH_ENTROPY_CANDIDATE_PATTERN.sub(
        lambda match: "[redacted-secret]" if _looks_like_high_entropy_secret(match.group(1)) else match.group(1),
        redacted,
    )
    return redacted


def sanitize_value(value: Any, *, key: Any = None, depth: int = 0) -> Any:
    if depth > _MAX_RECURSION_DEPTH:
        return "[redacted-depth]"
    if is_sensitive_key(key):
        return "[redacted]"
    if isinstance(value, Mapping):
        sanitized: Dict[str, Any] = {}
        for item_key, item_value in value.items():
            sanitized[str(item_key)] = sanitize_value(
                item_value,
                key=item_key,
                depth=depth + 1,
            )
        return sanitized
    if isinstance(value, list):
        return [
            sanitize_value(item, key=key, depth=depth + 1)
            for item in value[:_MAX_LIST_ITEMS]
        ]
    if isinstance(value, tuple):
        return [
            sanitize_value(item, key=key, depth=depth + 1)
            for item in list(value)[:_MAX_LIST_ITEMS]
        ]
    if isinstance(value, str):
        return redact_text(value)
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return redact_text(str(value))


def sanitize_mapping(mapping: Optional[Mapping[str, Any]]) -> Dict[str, Any]:
    if not isinstance(mapping, Mapping):
        return {}
    return sanitize_value(dict(mapping))


def _secret_findings(value: Any, *, key: Any = None, path: str = "$", depth: int = 0) -> List[str]:
    if depth > _MAX_RECURSION_DEPTH:
        return []
    findings: List[str] = []
    if is_sensitive_key(key) and value not in {None, "", "[redacted]", "[redacted-secret]", "[redacted-token]"}:
        findings.append(path)
    if isinstance(value, Mapping):
        for item_key, item_value in value.items():
            child_path = f"{path}.{item_key}"
            findings.extend(_secret_findings(item_value, key=item_key, path=child_path, depth=depth + 1))
        return findings
    if isinstance(value, (list, tuple)):
        for index, item in enumerate(list(value)[:_MAX_LIST_ITEMS]):
            findings.extend(_secret_findings(item, key=key, path=f"{path}[{index}]", depth=depth + 1))
        return findings
    if isinstance(value, str) and not _UUID_PATTERN.match(value.strip()) and redact_text(value) != value:
        findings.append(path)
    return findings


def assert_secrets_free(value: Any, *, context: str = "payload") -> None:
    findings = _secret_findings(value)
    if findings:
        raise ValueError(f"Secret-like value detected in {context}: {', '.join(findings[:5])}")
