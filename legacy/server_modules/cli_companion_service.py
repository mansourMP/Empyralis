from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, Optional

from server_modules import rust_runtime_kernel_client

_DEFAULT_API_URL = "http://localhost:8000"
_CONFIG_DIR = Path.home() / ".empyralis"
_CONFIG_FILE = _CONFIG_DIR / "config.json"
_SESSION_FILE = _CONFIG_DIR / "session.json"


def _ensure_config_dir() -> None:
    _CONFIG_DIR.mkdir(parents=True, exist_ok=True)


def _read_json(path: Path, fallback: Dict[str, Any] = None) -> Dict[str, Any]:
    if fallback is None:
        fallback = {}
    try:
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        pass
    return fallback


def _write_json(path: Path, data: Dict[str, Any]) -> None:
    _enforce_cli_companion_state_decision(path=path, data=data)
    _ensure_config_dir()
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


class CliCompanionRustGateError(RuntimeError):
    pass


def _enforce_cli_companion_state_decision(*, path: Path, data: Dict[str, Any]) -> None:
    safe_payload = {
        "path": str(path),
        "keys": sorted(str(key) for key in dict(data or {}).keys()),
        "workspace_id": str(dict(data or {}).get("workspace_id") or "").strip(),
        "api_url": str(dict(data or {}).get("api_url") or "").strip(),
        "channel": str(dict(data or {}).get("channel") or "").strip(),
        "has_api_key": bool(str(dict(data or {}).get("api_key") or "").strip()),
        "has_session_id": bool(str(dict(data or {}).get("session_id") or "").strip()),
        "has_thread_id": bool(str(dict(data or {}).get("thread_id") or "").strip()),
    }
    try:
        decision = rust_runtime_kernel_client.runtime_state_store_decision(
            operation="save_cli_companion_state",
            state_class="cli_companion_state",
            actor_id="system",
            status="active",
            payload=safe_payload,
            payload_bytes=len(json.dumps(safe_payload, sort_keys=True).encode("utf-8")),
            workspace_access=True,
            owner_access=True,
        )
        rust_runtime_kernel_client.enforce_kernel_decision(
            "runtime-state-store-decision",
            decision,
        )
        next_action = str(decision.get("next_action") or "").strip()
        if next_action != "save_cli_companion_state":
            raise CliCompanionRustGateError("unexpected_next_action")
    except rust_runtime_kernel_client.RustKernelDecisionError as exc:
        raise CliCompanionRustGateError(exc.reason) from exc


def load_config() -> Dict[str, Any]:
    return _read_json(_CONFIG_FILE, {
        "api_url": os.environ.get("EMPYRALIS_API_URL", _DEFAULT_API_URL),
        "api_key": os.environ.get("EMPYRALIS_API_KEY", ""),
        "workspace_id": os.environ.get("EMPYRALIS_WORKSPACE_ID", "default"),
    })


def save_config(config: Dict[str, Any]) -> None:
    _write_json(_CONFIG_FILE, config)


def load_session() -> Dict[str, Any]:
    return _read_json(_SESSION_FILE, {"session_id": "", "thread_id": "", "channel": "cli"})


def save_session(session: Dict[str, Any]) -> None:
    _write_json(_SESSION_FILE, session)


def _api_headers(config: Dict[str, Any]) -> Dict[str, str]:
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    api_key = str(config.get("api_key", "")).strip()
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    return headers


def _api_url(config: Dict[str, Any], path: str) -> str:
    base = str(config.get("api_url", _DEFAULT_API_URL)).rstrip("/")
    return f"{base}{path}"


def send_turn(
    message: str,
    *,
    config: Optional[Dict[str, Any]] = None,
    session: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    import urllib.request

    if config is None:
        config = load_config()
    if session is None:
        session = load_session()

    payload = {
        "workspace_id": str(config.get("workspace_id", "default")).strip(),
        "message": str(message).strip(),
        "channel": session.get("channel", "cli"),
        "session_id": str(session.get("session_id", "")).strip() or None,
        "thread_id": str(session.get("thread_id", "")).strip() or None,
    }

    try:
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            _api_url(config, "/api/runs/turn"),
            data=data,
            headers=_api_headers(config),
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=60) as resp:
            result = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return {"error": f"HTTP {e.code}", "detail": str(e)}
    except Exception as e:
        return {"error": "connection_failed", "detail": str(e)}

    if isinstance(result, dict):
        sid = str(result.get("session_id", "")).strip()
        tid = str(result.get("thread_id", "")).strip()
        if sid or tid:
            if sid:
                session["session_id"] = sid
            if tid:
                session["thread_id"] = tid
            save_session(session)

    return result


def format_reply(result: Dict[str, Any]) -> str:
    if result.get("error"):
        return f"Error: {result.get('error')} — {result.get('detail', '')}"
    reply = str(result.get("reply") or result.get("message") or "").strip()
    if not reply:
        pp = result.get("payload", {})
        if isinstance(pp, dict):
            reply = str(pp.get("reply") or pp.get("message") or "").strip()
    if not reply and "final" in str(result.get("type", "")):
        payload = result.get("payload", {})
        if isinstance(payload, dict):
            reply = str(payload.get("reply", "")).strip()
    return reply or json.dumps(result, indent=2, default=str)[:2000]
