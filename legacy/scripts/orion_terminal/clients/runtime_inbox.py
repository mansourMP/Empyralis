from __future__ import annotations

import json
import random
import time
from typing import Any, Callable, Dict, List, Optional
from urllib import error as urlerror
from urllib import parse as urlparse
from urllib import request as urlrequest

from ..core import tls_context
from ..errors import map_runtime_exception, should_retry_error


RuntimeCall = Callable[[str, str, Optional[Dict[str, Any]], Optional[str]], Dict[str, Any]]


def _dict_items(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    items = payload.get("items")
    if isinstance(items, list):
        return [item for item in items if isinstance(item, dict)]
    return []


def list_inbox_events(
    call: RuntimeCall,
    *,
    workspace_id: Optional[str],
    limit: int,
    channel: Optional[str],
    session_key: Optional[str],
    direction: Optional[str],
    action: Optional[str],
    run_id: Optional[str],
    group_errors: bool,
    error_window_seconds: Optional[int],
    include_raw_errors: bool,
) -> List[Dict[str, Any]]:
    safe_limit = max(1, min(int(limit), 500))
    query_parts: List[str] = [f"limit={safe_limit}"]
    if workspace_id:
        query_parts.append(f"workspace_id={urlparse.quote(workspace_id)}")
    if channel:
        query_parts.append(f"channel={urlparse.quote(channel)}")
    if session_key:
        query_parts.append(f"session_key={urlparse.quote(session_key)}")
    if direction:
        query_parts.append(f"direction={urlparse.quote(direction)}")
    if action:
        query_parts.append(f"action={urlparse.quote(action)}")
    if run_id:
        query_parts.append(f"run_id={urlparse.quote(run_id)}")
    if group_errors:
        query_parts.append("group_errors=1")
    if error_window_seconds is not None:
        safe_window = max(30, min(int(error_window_seconds), 86400))
        query_parts.append(f"error_window_seconds={safe_window}")
    if not include_raw_errors:
        query_parts.append("include_raw_errors=0")
    payload = call("/events/inbox?" + "&".join(query_parts), "GET", None, None)
    return _dict_items(payload)


def list_inbox_sessions(
    call: RuntimeCall,
    *,
    workspace_id: Optional[str],
    limit: int,
    channel: Optional[str],
    session_key: Optional[str],
    direction: Optional[str],
    action: Optional[str],
    run_id: Optional[str],
) -> List[Dict[str, Any]]:
    safe_limit = max(1, min(int(limit), 200))
    query_parts: List[str] = [f"limit={safe_limit}"]
    if workspace_id:
        query_parts.append(f"workspace_id={urlparse.quote(workspace_id)}")
    if channel:
        query_parts.append(f"channel={urlparse.quote(channel)}")
    if session_key:
        query_parts.append(f"session_key={urlparse.quote(session_key)}")
    if direction:
        query_parts.append(f"direction={urlparse.quote(direction)}")
    if action:
        query_parts.append(f"action={urlparse.quote(action)}")
    if run_id:
        query_parts.append(f"run_id={urlparse.quote(run_id)}")
    payload = call("/events/inbox/sessions?" + "&".join(query_parts), "GET", None, None)
    return _dict_items(payload)


def stream_inbox_events(
    *,
    api_url: str,
    api_key: str,
    cli_version: str,
    session_id: str,
    http_retries: int,
    http_backoff_seconds: float,
    http_backoff_max_seconds: float,
    workspace_id: Optional[str],
    limit: int,
    channel: Optional[str],
    session_key: Optional[str],
    direction: Optional[str],
    action: Optional[str],
    run_id: Optional[str],
    since_id: Optional[str],
    since_ts: Optional[str],
    include_backlog: bool,
    timeout_seconds: float,
    poll_seconds: float,
    heartbeat_seconds: float,
    max_events: int,
    request_cls: Callable[..., Any] = urlrequest.Request,
    urlopen_fn: Callable[..., Any] = urlrequest.urlopen,
) -> List[Dict[str, Any]]:
    safe_limit = max(1, min(int(limit), 500))
    safe_timeout = max(0.1, min(float(timeout_seconds), 30.0))
    safe_poll = max(0.05, min(float(poll_seconds), 5.0))
    safe_heartbeat = max(1.0, min(float(heartbeat_seconds), 60.0))
    safe_max_events = max(1, min(int(max_events), 500))

    query_parts: List[str] = [f"limit={safe_limit}", f"timeout_seconds={safe_timeout:.2f}", f"poll_seconds={safe_poll:.2f}"]
    if workspace_id:
        query_parts.append(f"workspace_id={urlparse.quote(workspace_id)}")
    if channel:
        query_parts.append(f"channel={urlparse.quote(channel)}")
    if session_key:
        query_parts.append(f"session_key={urlparse.quote(session_key)}")
    if direction:
        query_parts.append(f"direction={urlparse.quote(direction)}")
    if action:
        query_parts.append(f"action={urlparse.quote(action)}")
    if run_id:
        query_parts.append(f"run_id={urlparse.quote(run_id)}")
    if since_id:
        query_parts.append(f"since_id={urlparse.quote(since_id)}")
    if since_ts:
        query_parts.append(f"since_ts={urlparse.quote(since_ts)}")
    if include_backlog:
        query_parts.append("include_backlog=1")
    query_parts.append(f"heartbeat_seconds={safe_heartbeat:.2f}")

    path = "/events/inbox/stream?" + "&".join(query_parts)
    req = request_cls(
        f"{api_url}{path}",
        headers={
            "X-API-Key": api_key,
            "Accept": "text/event-stream",
            "X-Empyralis-CLI-Version": cli_version,
            "X-Empyralis-CLI-Session": session_id,
            "X-orion-cli-version": cli_version,
            "X-orion-cli-session": session_id,
        },
        method="GET",
    )

    max_attempts = max(1, int(http_retries) + 1)
    for attempt in range(max_attempts):
        try:
            out: List[Dict[str, Any]] = []
            with urlopen_fn(
                req,
                timeout=max(5, int(safe_timeout) + 5),
                context=tls_context(),
            ) as resp:
                current_event = "message"
                data_lines: List[str] = []
                for raw_line in resp:
                    line = raw_line.decode("utf-8", errors="replace").rstrip("\r\n")
                    if not line:
                        if not data_lines:
                            current_event = "message"
                            continue
                        payload_text = "\n".join(data_lines).strip()
                        data_lines = []
                        if payload_text:
                            try:
                                payload = json.loads(payload_text)
                            except Exception:
                                payload = payload_text
                            event_name = str(current_event or "message").strip().lower()
                            if event_name == "inbox_event" and isinstance(payload, dict):
                                out.append(payload)
                                if len(out) >= safe_max_events:
                                    break
                            elif event_name in {"done", "error"}:
                                break
                        current_event = "message"
                        continue
                    if line.startswith(":"):
                        continue
                    if line.startswith("event:"):
                        current_event = line.split(":", 1)[1].strip() or "message"
                        continue
                    if line.startswith("data:"):
                        data_lines.append(line.split(":", 1)[1].lstrip())
                return out
        except urlerror.HTTPError as exc:
            raw = exc.read().decode("utf-8", errors="replace") if hasattr(exc, "read") else ""
            mapped = map_runtime_exception(
                RuntimeError(f"HTTP {exc.code}: {raw or str(exc)}"),
                method="GET",
                path=path,
            )
        except urlerror.URLError as exc:
            mapped = map_runtime_exception(RuntimeError(f"Network error: {exc}"), method="GET", path=path)
        except Exception as exc:
            mapped = map_runtime_exception(exc, method="GET", path=path)
        can_retry = should_retry_error(mapped, allow_unsafe_retry=False)
        if attempt >= (max_attempts - 1) or not can_retry:
            raise mapped
        backoff = min(http_backoff_max_seconds, http_backoff_seconds * (2 ** attempt))
        jitter = random.uniform(0.0, min(0.2, backoff * 0.25))
        time.sleep(backoff + jitter)
    raise RuntimeError("Inbox stream failed after retries.")
