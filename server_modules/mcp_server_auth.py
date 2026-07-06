"""Per-workspace API key management for the Empyralis MCP server.

Keys are stored hashed (SHA-256) in a JSON registry file.  The plaintext
key is returned ONCE at creation time and never stored.

Usage::

    # Create a key
    result = await create_workspace_mcp_api_key(workspace_id="ws-1", label="Claude Code")
    plaintext = result["key"]  # only available here

    # Validate on every MCP request
    workspace_id = await resolve_workspace_from_api_key(bearer_token)
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import secrets
import time as _time
from pathlib import Path
from typing import Any, Dict, List, Optional

LOGGER = logging.getLogger(__name__)

_MCP_API_KEYS_FILE = Path(
    os.getenv("EMPYRALIS_MCP_KEYS_FILE")
    or os.path.join(os.path.expanduser("~"), ".empyralis", "state", "runtime", "mcp_api_keys.json")
)

_KEYS_CACHE: Optional[Dict[str, Any]] = None
_KEYS_CACHE_MTIME: float = 0.0


def _load_keys() -> Dict[str, Any]:
    """Load the API key registry from disk (cached by mtime)."""
    global _KEYS_CACHE, _KEYS_CACHE_MTIME
    try:
        mtime = _MCP_API_KEYS_FILE.stat().st_mtime if _MCP_API_KEYS_FILE.exists() else 0.0
    except OSError:
        mtime = 0.0
    if _KEYS_CACHE is not None and mtime == _KEYS_CACHE_MTIME:
        return _KEYS_CACHE
    if not _MCP_API_KEYS_FILE.exists():
        _KEYS_CACHE = {"keys": {}}
        _KEYS_CACHE_MTIME = 0.0
        return _KEYS_CACHE
    try:
        data = json.loads(_MCP_API_KEYS_FILE.read_text(encoding="utf-8"))
        _KEYS_CACHE = data if isinstance(data, dict) else {"keys": {}}
        _KEYS_CACHE_MTIME = mtime
        return _KEYS_CACHE
    except Exception:
        _KEYS_CACHE = {"keys": {}}
        _KEYS_CACHE_MTIME = 0.0
        return _KEYS_CACHE


def _save_keys(data: Dict[str, Any]) -> None:
    """Persist the API key registry to disk."""
    global _KEYS_CACHE, _KEYS_CACHE_MTIME
    _MCP_API_KEYS_FILE.parent.mkdir(parents=True, exist_ok=True)
    _MCP_API_KEYS_FILE.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")
    _KEYS_CACHE = data
    try:
        _KEYS_CACHE_MTIME = _MCP_API_KEYS_FILE.stat().st_mtime
    except OSError:
        _KEYS_CACHE_MTIME = _time.time()


def _hash_key(plaintext: str) -> str:
    """SHA-256 hash of the plaintext key."""
    return hashlib.sha256(plaintext.encode()).hexdigest()


def _generate_key() -> str:
    """Generate a cryptographically random API key."""
    return f"empyralis_mcp_{secrets.token_urlsafe(32)}"


# ── Public API ──────────────────────────────────────────────────────────


async def create_workspace_mcp_api_key(
    *,
    workspace_id: str,
    label: str = "",
    writes_enabled: bool = False,
) -> Dict[str, Any]:
    """Generate a new MCP API key for *workspace_id*.

    Returns ``{ok, key, key_id, workspace_id, label, writes_enabled, created_at}``.
    The ``key`` field contains the plaintext — it is NOT stored and
    cannot be retrieved later.

    *writes_enabled* gates write tools (create_agent, configure_agent,
    message_agent, memory_write) on a per-key basis.  Defaults to ``False``.
    """
    ws = str(workspace_id or "").strip()
    if not ws:
        return {"ok": False, "error": "workspace_id is required."}

    data = _load_keys()
    keys = data.setdefault("keys", {})

    plaintext = _generate_key()
    key_id = f"mcp_key_{secrets.token_hex(8)}"
    created_at = _time.strftime("%Y-%m-%dT%H:%M:%SZ", _time.gmtime())

    keys[key_id] = {
        "key_id": key_id,
        "workspace_id": ws,
        "label": str(label or "").strip() or key_id,
        "hash": _hash_key(plaintext),
        "writes_enabled": bool(writes_enabled),
        "created_at": created_at,
        "revoked": False,
    }
    _save_keys(data)

    LOGGER.info(
        "MCP API key created: key_id=%s workspace=%s writes_enabled=%s",
        key_id, ws, writes_enabled,
    )
    return {
        "ok": True,
        "key": plaintext,
        "key_id": key_id,
        "workspace_id": ws,
        "label": keys[key_id]["label"],
        "writes_enabled": writes_enabled,
        "created_at": created_at,
    }


async def get_mcp_api_key_workspace(key_id: str) -> Optional[str]:
    """Return the workspace that owns *key_id*, or None if the key is unknown.

    Used by the revoke route to scope-check the caller before revoking, so a key
    cannot be revoked cross-workspace.
    """
    data = _load_keys()
    entry = data.get("keys", {}).get(key_id)
    if not entry:
        return None
    return str(entry.get("workspace_id") or "").strip() or None


async def revoke_workspace_mcp_api_key(
    key_id: str, *, workspace_id: Optional[str] = None
) -> Dict[str, Any]:
    """Revoke an MCP API key by its ID.

    When *workspace_id* is provided the key must belong to it — defense-in-depth
    so a key is never revoked cross-workspace even if the caller check is missed.
    """
    data = _load_keys()
    keys = data.get("keys", {})
    entry = keys.get(key_id)
    if not entry:
        return {"ok": False, "error": f"Key '{key_id}' not found."}
    if workspace_id is not None and str(entry.get("workspace_id") or "").strip() != str(workspace_id).strip():
        return {"ok": False, "error": f"Key '{key_id}' not found."}
    if entry.get("revoked"):
        return {"ok": False, "error": f"Key '{key_id}' is already revoked."}

    entry["revoked"] = True
    entry["revoked_at"] = _time.strftime("%Y-%m-%dT%H:%M:%SZ", _time.gmtime())
    _save_keys(data)

    LOGGER.info("MCP API key revoked: key_id=%s workspace=%s", key_id, entry.get("workspace_id"))
    return {"ok": True, "key_id": key_id, "revoked": True}


async def list_workspace_mcp_api_keys(workspace_id: str) -> List[Dict[str, Any]]:
    """List all (non-revoked) API keys for a workspace."""
    ws = str(workspace_id or "").strip()
    data = _load_keys()
    return [
        {"key_id": k["key_id"], "label": k["label"], "writes_enabled": k.get("writes_enabled", False), "created_at": k.get("created_at", "")}
        for k in data.get("keys", {}).values()
        if k.get("workspace_id") == ws and not k.get("revoked")
    ]


async def resolve_workspace_from_api_key(bearer_token: str) -> Optional[Dict[str, Any]]:
    """Validate a bearer token and return ``{workspace_id, writes_enabled}``, or None.

    The token may be the full ``empyralis_mcp_...`` key or a
    ``Bearer empyralis_mcp_...`` header value.
    """
    raw = str(bearer_token or "").strip()
    if raw.lower().startswith("bearer "):
        raw = raw[7:].strip()
    if not raw:
        return None

    key_hash = _hash_key(raw)
    data = _load_keys()
    for entry in data.get("keys", {}).values():
        if entry.get("hash") == key_hash and not entry.get("revoked"):
            ws = str(entry.get("workspace_id") or "").strip() or None
            if ws is None:
                return None
            return {
                "workspace_id": ws,
                "writes_enabled": bool(entry.get("writes_enabled", False)),
            }
    return None
