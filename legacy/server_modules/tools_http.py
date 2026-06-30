from __future__ import annotations

import json
from typing import Any, Dict, Optional
from urllib.parse import urlparse

import httpx

from server_modules import egress_policy
from server_modules.url_security import assert_safe_outbound_url


DEFAULT_TIMEOUT_SECONDS = 30
MAX_RESPONSE_BYTES = 100 * 1024
MUTATING_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
LOCALHOST_HOSTS = {"localhost", "127.0.0.1", "::1"}


def _normalize_method(value: Any) -> str:
    method = str(value or "GET").strip().upper() or "GET"
    if method not in {"GET", "POST", "PUT", "PATCH", "DELETE"}:
        raise RuntimeError(f"Unsupported HTTP method '{method}'.")
    return method


def _normalize_mapping(value: Any) -> Dict[str, str]:
    if not isinstance(value, dict):
        return {}
    normalized: Dict[str, str] = {}
    for key, item in value.items():
        token = str(key or "").strip()
        if not token:
            continue
        normalized[token] = str(item or "").strip()
    return normalized


def _is_json_content_type(content_type: str) -> bool:
    normalized = str(content_type or "").strip().lower()
    return "application/json" in normalized or normalized.endswith("+json")


def _localhost_like(url: str) -> bool:
    parsed = urlparse(str(url or "").strip())
    host = str(parsed.hostname or "").strip().lower()
    return host in LOCALHOST_HOSTS


def http_request_requires_approval(method: Any, url: Any) -> bool:
    normalized_method = _normalize_method(method)
    normalized_url = str(url or "").strip()
    if normalized_method not in MUTATING_METHODS:
        return False
    if not normalized_url:
        return False
    return not _localhost_like(normalized_url)


def _authorization(
    headers: Dict[str, str],
    auth_type: Any,
    auth_value: Any,
) -> tuple[Dict[str, str], Optional[httpx.Auth]]:
    normalized_headers = dict(headers)
    normalized_type = str(auth_type or "").strip().lower()
    normalized_value = str(auth_value or "").strip()
    if not normalized_type or normalized_type == "none":
        return normalized_headers, None
    if normalized_type == "bearer":
        if normalized_value:
            normalized_headers.setdefault("Authorization", f"Bearer {normalized_value}")
        return normalized_headers, None
    if normalized_type == "basic":
        username, separator, password = normalized_value.partition(":")
        if not separator:
            raise RuntimeError("Basic auth requires auth_value in 'user:pass' format.")
        return normalized_headers, httpx.BasicAuth(username, password)
    raise RuntimeError(f"Unsupported auth_type '{normalized_type}'.")


def _response_body(raw_bytes: bytes, content_type: str) -> tuple[Any, bool]:
    truncated = len(raw_bytes) > MAX_RESPONSE_BYTES
    payload = raw_bytes[:MAX_RESPONSE_BYTES]
    text = payload.decode("utf-8", errors="replace")
    if truncated:
        text = (
            f"{text}\n\n[truncated: original response exceeded {MAX_RESPONSE_BYTES} bytes]"
        )
    if not truncated and _is_json_content_type(content_type):
        try:
            return json.loads(text) if text else None, False
        except Exception:
            return text, False
    return text, truncated


async def http_request(
    *,
    method: Any,
    url: Any,
    headers: Any = None,
    body: Any = None,
    params: Any = None,
    timeout: Any = DEFAULT_TIMEOUT_SECONDS,
    auth_type: Any = None,
    auth_value: Any = None,
    client_factory: Any = None,
) -> Dict[str, Any]:
    normalized_url = str(url or "").strip()
    if not normalized_url:
        raise RuntimeError("http_request requires a URL.")

    normalized_method = _normalize_method(method)
    assert_safe_outbound_url(normalized_url)
    egress_policy.enforce_outbound_request(url=normalized_url, method=normalized_method)
    normalized_headers = _normalize_mapping(headers)
    normalized_params = params if isinstance(params, dict) else None
    normalized_headers, auth = _authorization(normalized_headers, auth_type, auth_value)

    request_kwargs: Dict[str, Any] = {
        "method": normalized_method,
        "url": normalized_url,
        "headers": normalized_headers or None,
        "params": normalized_params,
        "auth": auth,
    }
    if body is not None:
        if isinstance(body, (dict, list)):
            request_kwargs["json"] = body
        elif isinstance(body, (bytes, bytearray)):
            request_kwargs["content"] = bytes(body)
        else:
            request_kwargs["content"] = str(body)

    factory = client_factory or httpx.AsyncClient
    async with factory(timeout=max(1, int(timeout or DEFAULT_TIMEOUT_SECONDS)), follow_redirects=True) as client:
        response = await client.request(**request_kwargs)
        final_url = str(getattr(response, "url", "") or "").strip()
        if final_url:
            assert_safe_outbound_url(final_url)
            egress_policy.enforce_outbound_request(url=final_url, method=normalized_method)
        raw_bytes = await response.aread()

    body_payload, truncated = _response_body(raw_bytes, response.headers.get("content-type", ""))
    return {
        "status_code": int(response.status_code),
        "headers": dict(response.headers.items()),
        "body": body_payload,
        "truncated": truncated,
    }
