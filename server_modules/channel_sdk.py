"""Channel SDK — shared utilities for all connectors and routes.

This is the single importable layer for every connector.  Import from
here instead of copying utilities into individual files.

Mirrors the OpenClaw ``plugin-sdk`` pattern: one canonical home for
shared channel behaviour, zero duplication across connectors.
"""

from __future__ import annotations

import json
from typing import Any, Dict, Optional
from urllib import error as urlerror
from urllib import parse as urlparse
from urllib import request as urlrequest


def _http_json_request(
    url: str,
    *,
    headers: Optional[Dict[str, str]] = None,
    payload: Optional[Any] = None,
    method: Optional[str] = None,
    timeout: int = 30,
) -> Dict[str, Any]:
    """Shared HTTP JSON helper.  Replaces 5 identical copies across
    connectors (github, linear, notion, slack, discord_connector).

    Returns a dict with keys ``status``, ``json``, ``text``, ``headers``.
    HTTP errors (4xx/5xx) are *not* raised — the caller inspects ``status``.
    """
    request_headers = dict(headers or {})
    body: Optional[bytes] = None
    verb = (method or ("POST" if payload is not None else "GET")).upper()
    if payload is not None:
        if isinstance(payload, (bytes, bytearray)):
            body = bytes(payload)
        elif request_headers.get("Content-Type") == "application/x-www-form-urlencoded":
            if isinstance(payload, str):
                body = payload.encode("utf-8")
            else:
                body = urlparse.urlencode(payload, doseq=True).encode("utf-8")
        else:
            body = json.dumps(payload).encode("utf-8")
            request_headers.setdefault("Content-Type", "application/json")
    req = urlrequest.Request(url, data=body, headers=request_headers, method=verb)
    try:
        with urlrequest.urlopen(req, timeout=timeout) as resp:
            raw_bytes = resp.read()
            raw_text = raw_bytes.decode("utf-8", errors="replace")
            try:
                parsed = json.loads(raw_text) if raw_text else None
            except Exception:
                parsed = None
            return {
                "status": getattr(resp, "status", 200),
                "json": parsed,
                "text": raw_text,
                "content": raw_bytes,
                "headers": dict(getattr(resp, "headers", {}) or {}),
            }
    except urlerror.HTTPError as exc:
        raw_bytes = exc.read()
        raw_text = raw_bytes.decode("utf-8", errors="replace")
        try:
            parsed = json.loads(raw_text) if raw_text else None
        except Exception:
            parsed = None
        return {
            "status": int(getattr(exc, "code", 500) or 500),
            "json": parsed,
            "text": raw_text,
            "content": raw_bytes,
            "headers": dict(getattr(exc, "headers", {}) or {}),
        }
