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
import secrets
import time as _time
from pathlib import Path
from typing import Any, Dict, List, Optional

from server_modules.state_paths import resolve_state_path

LOGGER = logging.getLogger(__name__)

# 2026-08-14 incident (found by preflight's new state-home-resolution scan,
# server_modules/preflight.py's _check_local_stack_state_home_resolution):
# this used to hardcode os.path.expanduser('~') as its fallback base and
# ignore EMPYRALIS_STATE_HOME entirely — the same shape as
# sage_telegram_hosted_service.py's hardcoded pairing-state path, except
# what this file stores is hashed per-workspace MCP API KEYS, not pairing
# metadata. A throwaway stack that set EMPYRALIS_STATE_HOME but not the
# more specific EMPYRALIS_MCP_KEYS_FILE would still read (and, on the
# first key creation, WRITE) the founder's real MCP key registry from a
# process that believed it was isolated. resolve_state_path() (state_paths.
# py) is the same explicit-override-then-EMPYRALIS_STATE_HOME-then-home
# resolver every properly-scoped module already uses; the explicit
# EMPYRALIS_MCP_KEYS_FILE override still wins when set, unchanged.
#
# Resolved via a FUNCTION, not a module-level constant — the same "don't
# bake at import time" fix, so a test/harness that sets EMPYRALIS_STATE_HOME
# or EMPYRALIS_MCP_KEYS_FILE AFTER this module was first imported still
# gets the correct path rather than whatever resolved at import time.
def _mcp_api_keys_file() -> Path:
    return resolve_state_path("EMPYRALIS_MCP_KEYS_FILE", "runtime/mcp_api_keys.json")


_KEYS_CACHE: Optional[Dict[str, Any]] = None
_KEYS_CACHE_MTIME: float = 0.0


def _load_keys() -> Dict[str, Any]:
    """Load the API key registry from disk (cached by mtime)."""
    global _KEYS_CACHE, _KEYS_CACHE_MTIME
    keys_file = _mcp_api_keys_file()
    try:
        mtime = keys_file.stat().st_mtime if keys_file.exists() else 0.0
    except OSError:
        mtime = 0.0
    if _KEYS_CACHE is not None and mtime == _KEYS_CACHE_MTIME:
        return _KEYS_CACHE
    if not keys_file.exists():
        _KEYS_CACHE = {"keys": {}}
        _KEYS_CACHE_MTIME = 0.0
        return _KEYS_CACHE
    try:
        data = json.loads(keys_file.read_text(encoding="utf-8"))
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
    keys_file = _mcp_api_keys_file()
    keys_file.parent.mkdir(parents=True, exist_ok=True)
    keys_file.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")
    _KEYS_CACHE = data
    try:
        _KEYS_CACHE_MTIME = keys_file.stat().st_mtime
    except OSError:
        _KEYS_CACHE_MTIME = _time.time()


def _hash_key(plaintext: str) -> str:
    """SHA-256 hash of the plaintext key."""
    return hashlib.sha256(plaintext.encode()).hexdigest()


def _generate_key() -> str:
    """Generate a cryptographically random API key."""
    return f"empyralis_mcp_{secrets.token_urlsafe(32)}"


# ── Public API ──────────────────────────────────────────────────────────


async def _mint_external_agent_roster_entry(
    *, workspace_id: str, key_hash: str, mcp_key_id: str, label: str,
) -> Dict[str, Any]:
    """Mint this key's external-agent identity (Step 2 of "Mentions +
    identity for platform AND external agents"). Best-effort: key creation
    must succeed even when this fails (e.g. Postgres unreachable) — but the
    failure is never swallowed silently, it's logged loudly here AND
    surfaced to the caller (create_workspace_mcp_api_key puts it in the
    response as ``roster_warning``) so an operator sees it immediately
    rather than discovering a missing identity only when "list my tasks"
    quietly returns nothing later. resolve_workspace_from_api_key retries
    this same mint (idempotently, via ON CONFLICT) on every future call
    until it succeeds, so a transient failure here is self-healing, not
    permanent.
    """
    try:
        from server_modules import control_plane_repository as cpr
        from server_modules import mcp_external_agent_roster_service as roster

        tenant_id = await cpr.resolve_tenant_id_for_workspace(workspace_id, default="default")
        result = await roster.register_external_agent(
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            key_hash=key_hash,
            mcp_key_id=mcp_key_id,
            display_name=label if label and label != mcp_key_id else "",
        )
        if not result.get("ok"):
            LOGGER.warning(
                "MCP external-agent roster mint failed for key_id=%s workspace=%s: %s",
                mcp_key_id, workspace_id, result.get("error"),
            )
        return result
    except Exception as exc:  # noqa: BLE001 — never let roster minting break key creation
        LOGGER.warning(
            "MCP external-agent roster mint raised for key_id=%s workspace=%s",
            mcp_key_id, workspace_id, exc_info=True,
        )
        return {"ok": False, "error": str(exc)}


async def create_workspace_mcp_api_key(
    *,
    workspace_id: str,
    label: str = "",
    writes_enabled: bool = False,
) -> Dict[str, Any]:
    """Generate a new MCP API key for *workspace_id*.

    Returns ``{ok, key, key_id, workspace_id, label, writes_enabled,
    created_at, external_agent_id, external_agent_display_name}``. The
    ``key`` field contains the plaintext — it is NOT stored and cannot be
    retrieved later.

    *writes_enabled* gates the workspace-CONFIGURATION write tools on a
    per-key basis — create_project, create_agent, configure_agent,
    message_agent, assign_channel_bot, release_channel_bot,
    connect_connector, trigger_test_turn.  Defaults to ``False``.

    It does NOT gate the task and document tools; those are bounded to work
    already visible through this same key and are argued separately in
    ``mcp_server.py``'s module docstring ("Write-gate decision").  The
    authoritative list is ``mcp_server.EMPYRALIST_MCP_TOOLS`` and the
    ``_check_write`` call sites beside it — this sentence used to name
    ``memory_write``, a tool deleted long enough ago that the name was the
    only trace of it left.

    Every key mints an external-agent roster identity in the same call (THE
    IDENTITY RULE: identity is minted by the platform at the connection
    boundary, not by the brain that later connects with it) — see
    ``_mint_external_agent_roster_entry``. That mint is best-effort; a
    failure is reported via ``roster_warning`` on the response rather than
    failing key creation, and self-heals on the key's first resolved request.
    """
    ws = str(workspace_id or "").strip()
    if not ws:
        return {"ok": False, "error": "workspace_id is required."}

    data = _load_keys()
    keys = data.setdefault("keys", {})

    plaintext = _generate_key()
    key_id = f"mcp_key_{secrets.token_hex(8)}"
    created_at = _time.strftime("%Y-%m-%dT%H:%M:%SZ", _time.gmtime())
    key_hash = _hash_key(plaintext)

    keys[key_id] = {
        "key_id": key_id,
        "workspace_id": ws,
        "label": str(label or "").strip() or key_id,
        "hash": key_hash,
        "writes_enabled": bool(writes_enabled),
        "created_at": created_at,
        "revoked": False,
    }
    _save_keys(data)

    LOGGER.info(
        "MCP API key created: key_id=%s workspace=%s writes_enabled=%s",
        key_id, ws, writes_enabled,
    )

    roster_result = await _mint_external_agent_roster_entry(
        workspace_id=ws, key_hash=key_hash, mcp_key_id=key_id, label=keys[key_id]["label"],
    )

    response: Dict[str, Any] = {
        "ok": True,
        "key": plaintext,
        "key_id": key_id,
        "workspace_id": ws,
        "label": keys[key_id]["label"],
        "writes_enabled": writes_enabled,
        "created_at": created_at,
        "external_agent_id": roster_result.get("id") if roster_result.get("ok") else None,
        "external_agent_display_name": roster_result.get("display_name") if roster_result.get("ok") else None,
    }
    if not roster_result.get("ok"):
        response["roster_warning"] = (
            "External-agent roster identity could not be minted for this key "
            f"({roster_result.get('error')}). It will self-heal — an identity is "
            "minted lazily the first time this key is used — but 'my tasks' will "
            "resolve to unassigned-only work until then."
        )
    return response


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

    # Best-effort mirror into the external-agent roster so its "who's
    # currently active" listing stays honest. The key itself is already
    # revoked above regardless of whether this side-channel succeeds — auth
    # never depends on the roster row.
    try:
        from server_modules import control_plane_repository as cpr
        from server_modules import mcp_external_agent_roster_service as roster

        entry_workspace_id = str(entry.get("workspace_id") or "").strip()
        entry_tenant_id = await cpr.resolve_tenant_id_for_workspace(entry_workspace_id, default="default")
        await roster.set_external_agent_revoked(
            tenant_id=entry_tenant_id,
            workspace_id=entry_workspace_id,
            key_hash=str(entry.get("hash") or ""),
            revoked=True,
        )
    except Exception:
        LOGGER.warning("Failed to mirror MCP key revoke into external-agent roster", exc_info=True)

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
    """Validate a bearer token and return ``{workspace_id, writes_enabled,
    external_agent_id, external_agent_display_name}``, or None.

    The token may be the full ``empyralis_mcp_...`` key or a
    ``Bearer empyralis_mcp_...`` header value.

    Also resolves this key's external-agent roster identity (Step 2 of
    "Mentions + identity for platform AND external agents") — and, if it's
    missing, mints it lazily right here. That backfill covers any key
    created before Step 2 shipped, or whose mint-time roster insert failed
    (see ``_mint_external_agent_roster_entry``): self-healing instead of
    leaving a session identity-less for its whole lifetime. ``ON CONFLICT
    (key_hash) DO NOTHING`` inside the mint makes concurrent backfills for
    the same key safe.

    ``external_agent_id`` is ``None`` only when Postgres itself is
    unreachable or the key's roster row was revoked independently of the
    key — traced via a WARNING log, never silently absorbed. Every MCP task
    tool that depends on this degrades to an explicit "no identity" state
    (still functional for unassigned/backlog work) rather than crashing.
    """
    raw = str(bearer_token or "").strip()
    if raw.lower().startswith("bearer "):
        raw = raw[7:].strip()
    if not raw:
        return None

    key_hash = _hash_key(raw)
    data = _load_keys()
    matched_entry: Optional[Dict[str, Any]] = None
    for entry in data.get("keys", {}).values():
        if entry.get("hash") == key_hash and not entry.get("revoked"):
            matched_entry = entry
            break
    if matched_entry is None:
        return None
    ws = str(matched_entry.get("workspace_id") or "").strip() or None
    if ws is None:
        return None

    external_agent_id: Optional[str] = None
    external_agent_display_name: Optional[str] = None
    try:
        from server_modules import mcp_external_agent_roster_service as roster

        roster_entry = await roster.get_external_agent_by_key_hash(key_hash=key_hash)
        if roster_entry is None:
            minted = await _mint_external_agent_roster_entry(
                workspace_id=ws,
                key_hash=key_hash,
                mcp_key_id=str(matched_entry.get("key_id") or ""),
                label=str(matched_entry.get("label") or ""),
            )
            if minted.get("ok"):
                external_agent_id = minted.get("id")
                external_agent_display_name = minted.get("display_name")
            else:
                LOGGER.warning(
                    "MCP key resolved with no external-agent identity (workspace=%s): %s",
                    ws, minted.get("error"),
                )
        elif not roster_entry.get("revoked"):
            external_agent_id = roster_entry.get("id")
            external_agent_display_name = roster_entry.get("display_name")
    except Exception:
        LOGGER.warning("Failed to resolve external-agent roster identity for an MCP key", exc_info=True)

    return {
        "workspace_id": ws,
        "writes_enabled": bool(matched_entry.get("writes_enabled", False)),
        "external_agent_id": external_agent_id,
        "external_agent_display_name": external_agent_display_name,
    }
