from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import threading
import uuid
from datetime import datetime, timezone
from ipaddress import ip_address
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional
from urllib.parse import urlparse

import httpx

from server_modules import agent_action_metering_service, rust_runtime_kernel_client
from server_modules.config_loader import config_str
from server_modules.url_security import assert_safe_outbound_url

try:
    from mcp import ClientSession
    from mcp.client.streamable_http import create_mcp_http_client, streamable_http_client
except Exception:  # pragma: no cover - optional until dependency is installed
    ClientSession = None  # type: ignore[assignment]
    streamable_http_client = None  # type: ignore[assignment]
    create_mcp_http_client = None  # type: ignore[assignment]

_log = logging.getLogger(__name__)

McpTransport = Literal["streamable_http"]
_STATE_HOME = Path(
    config_str("EMPYRALIS_STATE_HOME", str(Path.home() / ".empyralis" / "state"))
).expanduser()
MCP_SERVER_REGISTRY_FILE = Path(
    config_str(
        "EMPYRALIS_MCP_SERVERS_FILE",
        str(_STATE_HOME / "runtime" / "mcp_servers.json"),
    )
).expanduser()
_SUPPORTED_RUNTIME_MODES = {"hosted_secure", "local_secure", "privileged_device"}
_SUPPORTED_ACTION_CLASSES = {"read", "write", "execute"}
_SUPPORTED_RISK_LEVELS = {"low", "medium", "high", "critical"}
_SUPPORTED_COST_CLASSES = {"free", "standard", "metered", "external"}
_DANGEROUS_TOKENS = {"delete", "remove", "destroy", "drop", "truncate", "reset", "shutdown"}

# MAN-92/MAN-104 (tiered autonomy for MCP tools): real remote MCP servers
# essentially never self-report action_class/risk_level/requires_approval —
# that's an Empyralis-only manifest extension, not part of the MCP tool
# schema — so raw.get("action_class")/raw.get("risk_level") fall back to
# "read"/"low" for nearly every dynamically-discovered tool regardless of
# what it actually does. Name/description keyword matching is the only
# signal available for THIS catalog (unlike skills_service.py's first-party
# tools, which get a risk_level/requires_approval hand-authored per tool at
# definition time). Scoped to the same three "genuinely hard-to-reverse or
# externally-reaching" categories MAN-68's doctrine now teaches the model to
# pause on: money movement, irreversible deletes, and messaging/acting on a
# third party on the owner's behalf. Deliberately NOT a blanket "any write
# needs approval" net — that's the wall-of-toggles behavior this fixes.
_MONEY_MOVEMENT_TOKENS = {
    "pay", "payment", "charge", "refund", "transfer", "withdraw", "payout",
    "invoice", "checkout", "purchase", "subscribe", "billing",
}
_THIRD_PARTY_SEND_TOKENS = {
    "send", "reply", "post", "publish", "share",
    "invite", "notify", "dm", "tweet",
}
_HIGH_STAKES_TOKENS = _DANGEROUS_TOKENS | _MONEY_MOVEMENT_TOKENS | _THIRD_PARTY_SEND_TOKENS
# Deliberately NOT in _THIRD_PARTY_SEND_TOKENS: "email"/"message". Those are
# content-type nouns, not send actions — a plain read tool named "read_email"
# or "list_messages" (exactly what Gmail/Slack/Discord discovery produces,
# the literal MAN-104 example) legitimately contains those words without
# doing anything third-party-reaching. The verb tokens above ("send", "post",
# "reply", ...) already catch the actual send action on those same services
# (e.g. "send_email", "chat_postMessage").

_WORD_SPLIT_RE = re.compile(r"[^a-z0-9]+")


def _keyword_tokens(text: str) -> set:
    """Whole-word tokenizer for _looks_high_stakes: lowercases, splits
    camelCase boundaries (so "chat_postMessage" yields "post"/"message"
    instead of one opaque blob), then splits on any non-alnum separator.
    Whole-word SET MEMBERSHIP (not substring) is required so that plurals
    and unrelated words that merely contain a token as a substring don't
    false-positive — e.g. "shared" must not match "share", "tweets" must not
    match "tweet", and "admin" must not match "dm"."""
    camel_split = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", text)
    return {token for token in _WORD_SPLIT_RE.split(camel_split.lower()) if token}


def _looks_high_stakes(name: str, description: str) -> bool:
    """Name/description keyword match for the money/delete/third-party-send
    categories — see _HIGH_STAKES_TOKENS above for why this exists instead of
    trusting a declared action_class/risk_level for third-party MCP tools.
    Matches whole words only (see _keyword_tokens) so read tools that merely
    mention a high-stakes-adjacent noun aren't swept into the approval gate."""
    tokens = _keyword_tokens(name) | _keyword_tokens(description)
    return bool(tokens & _HIGH_STAKES_TOKENS)
_ARGUMENT_KEY_CANDIDATES = ("arguments", "args", "input", "payload")

_BLOCKED_MCP_HOSTS = {
    "localhost",
    "127.0.0.1",
    "::1",
    "0.0.0.0",
    "[::]",
}


def _validate_mcp_endpoint(endpoint: str) -> None:
    """Validate an MCP server endpoint URL for safety.

    Raises ValueError if the URL is unsafe or unsupported.
    """
    raw = str(endpoint or "").strip()
    if not raw:
        raise ValueError("MCP endpoint URL is required.")
    parsed = urlparse(raw)
    scheme = (parsed.scheme or "").lower()
    if scheme not in ("https", "http"):
        raise ValueError(f"MCP endpoint must use https:// (or http:// in dev). Got scheme: {scheme!r}")
    if scheme == "http" and not os.getenv("EMPYRALIS_DEV_ALLOW_HTTP_MCP"):
        raise ValueError("MCP endpoint must use https://. Set EMPYRALIS_DEV_ALLOW_HTTP_MCP=1 for local dev.")
    hostname = (parsed.hostname or "").strip().lower()
    if not hostname:
        raise ValueError("MCP endpoint URL has no parseable hostname.")
    if hostname in _BLOCKED_MCP_HOSTS:
        raise ValueError(f"MCP endpoint hostname {hostname!r} is blocked.")
    if hostname.endswith(".local") or hostname.endswith(".internal"):
        raise ValueError(f"MCP endpoint hostname {hostname!r} uses a reserved TLD.")
    try:
        addr = ip_address(hostname)
    except ValueError:
        addr = None
    if addr is not None and (
        addr.is_loopback
        or addr.is_link_local
        or addr.is_multicast
        or addr.is_private
        or addr.is_unspecified
        or addr.is_reserved
    ):
        raise ValueError(f"MCP endpoint IP address {hostname!r} is not allowed.")
    assert_safe_outbound_url(raw)
    if not parsed.path or parsed.path == "/":
        _log.info("MCP endpoint %s has no explicit path; this may be intentional.", raw)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _normalize_server_id(value: Any) -> str:
    raw = str(value or "").strip().lower()
    if not raw:
        return ""
    cleaned = "".join(ch if ch.isalnum() or ch in {"-", "_"} else "-" for ch in raw)
    while "--" in cleaned:
        cleaned = cleaned.replace("--", "-")
    return cleaned.strip("-_")


def _normalize_tool_name(value: Any) -> str:
    return str(value or "").strip()


def _normalize_transport(value: Any) -> McpTransport:
    token = str(value or "streamable_http").strip().lower() or "streamable_http"
    return "streamable_http"


def _normalize_list_of_strings(value: Any) -> List[str]:
    if not isinstance(value, list):
        return []
    items: List[str] = []
    seen: set[str] = set()
    for item in value:
        token = str(item or "").strip()
        if not token or token in seen:
            continue
        seen.add(token)
        items.append(token)
    return items


def _normalize_runtime_modes(value: Any) -> List[str]:
    modes = []
    for token in _normalize_list_of_strings(value):
        compact = token.lower()
        if compact in _SUPPORTED_RUNTIME_MODES and compact not in modes:
            modes.append(compact)
    return modes or ["hosted_secure", "local_secure", "privileged_device"]


def _normalize_action_class(value: Any) -> str:
    token = str(value or "read").strip().lower() or "read"
    return token if token in _SUPPORTED_ACTION_CLASSES else "read"


def _risk_level_for_action(action_class: str) -> str:
    if action_class == "execute":
        return "critical"
    if action_class == "write":
        return "medium"
    return "low"


def _normalize_risk_level(value: Any, *, action_class: str) -> str:
    token = str(value or "").strip().lower()
    if token in _SUPPORTED_RISK_LEVELS:
        return token
    return _risk_level_for_action(action_class)


def _normalize_cost_class(value: Any) -> str:
    token = str(value or "").strip().lower()
    return token if token in _SUPPORTED_COST_CLASSES else "standard"


def _normalize_input_schema(value: Any) -> Dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    return dict(value)


def _read_json(path: Path) -> Dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else {}
    except Exception:
        return {}


def _write_json(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


class McpRegistryRustGateError(RuntimeError):
    pass


def _enforce_mcp_registry_state_decision(payload: Dict[str, Any]) -> Dict[str, Any]:
    normalized_payload = payload if isinstance(payload, dict) else {}
    try:
        payload_bytes = len(
            json.dumps(
                normalized_payload,
                ensure_ascii=False,
                sort_keys=True,
                default=str,
            ).encode("utf-8")
        )
        decision = rust_runtime_kernel_client.runtime_state_store_decision(
            operation="save_mcp_server_registry",
            state_class="mcp_server_registry",
            actor_id="system",
            status="active",
            payload=normalized_payload,
            payload_bytes=payload_bytes,
            workspace_access=True,
            owner_access=True,
        )
        rust_runtime_kernel_client.enforce_kernel_decision(
            "runtime-state-store-decision",
            decision,
        )
        next_action = str(decision.get("next_action") or "").strip()
        if next_action != "save_mcp_server_registry":
            raise McpRegistryRustGateError("unexpected_next_action")
        return decision
    except rust_runtime_kernel_client.RustKernelDecisionError as exc:
        raise McpRegistryRustGateError(exc.reason) from exc


def _default_registry() -> Dict[str, Any]:
    return {
        "version": 1,
        "workspaces": {},
        "updated_at": None,
    }


def load_mcp_server_registry() -> Dict[str, Any]:
    payload = _read_json(MCP_SERVER_REGISTRY_FILE)
    workspaces = payload.get("workspaces") if isinstance(payload.get("workspaces"), dict) else {}
    return {
        "version": int(payload.get("version") or 1),
        "workspaces": workspaces,
        "updated_at": payload.get("updated_at"),
    }


def save_mcp_server_registry(payload: Dict[str, Any]) -> Dict[str, Any]:
    data = _default_registry()
    data["workspaces"] = payload.get("workspaces") if isinstance(payload.get("workspaces"), dict) else {}
    data["updated_at"] = payload.get("updated_at")
    _enforce_mcp_registry_state_decision(data)
    _write_json(MCP_SERVER_REGISTRY_FILE, data)
    return data


# ═══════════════════════════════════════════════════════════════════════════
# §1.3 containment (docs: Multiplayer Projects plan) — the registry key below
# is (workspace_id, server_id) with server_id a STATIC per-provider slug from
# APP_MCP_SERVER_MAP (e.g. "google-gmail" — see connection_oauth_service.py).
# It carries no account/credential discriminator, so a second agent
# connecting its OWN account of the same provider in the same workspace
# would silently overwrite the row every agent's tool calls resolve through
# (_resolve_mcp_credential below reads only this one row per server_id) —
# every other agent's next MCP tool call would silently start hitting a
# DIFFERENT mailbox, with no error and nothing the owner would see in time.
#
# Making the key itself account-aware is the real fix, but it ripples into
# mcp_skill_id()/mcp_tool_name() (which embed server_id into every skill id
# and model-facing tool name), the tool-listing/approval UI, and the
# specialist/primary tool-injection paths in agent_turn_runtime_service.py —
# too invasive to land safely in one pass without a migration. The
# containment below is the smallest correct fix instead: refuse the upsert
# loudly (McpServerCredentialCollisionError) instead of silently swapping
# whose credential the row resolves to. Existing single-account and
# workspace-shared setups (no agent_install_id anywhere in play) are
# byte-for-byte unaffected — see _assert_no_cross_agent_credential_collision.
# ═══════════════════════════════════════════════════════════════════════════


def credential_owner_agent_install_id(credential_id: Any) -> Optional[str]:
    """Best-effort: the agent_install_id a vault credential row is scoped to
    (via vault_helpers.credential_agent_scope), or None if the credential is
    workspace-shared (no agent_install_id set) or no longer resolvable (e.g.
    deleted -- treated as "no current owner" so a stale registry slot can
    still be reclaimed). Never raises. Deferred imports mirror
    _resolve_mcp_credential's pattern below (vault_store/vault_helpers are
    heavier modules this file only needs for this one lookup)."""
    normalized_id = str(credential_id or "").strip()
    if not normalized_id:
        return None
    try:
        from server_modules.vault_store import get_credential
        from server_modules.vault_helpers import credential_agent_scope
        entry = get_credential(normalized_id)
    except Exception:
        return None
    if not isinstance(entry, dict):
        return None
    return credential_agent_scope(entry) or None


class McpServerCredentialCollisionError(RuntimeError):
    """Raised instead of silently overwriting an MCP server registry row's
    credential_id when the incoming credential is owned by a DIFFERENT agent
    than whichever agent the existing row's credential belongs to. See the
    module banner above for the full rationale."""

    def __init__(self, server_id: str, workspace_id: str, existing_owner: Optional[str], new_owner: Optional[str]) -> None:
        self.server_id = server_id
        self.workspace_id = workspace_id
        self.existing_owner = existing_owner
        self.new_owner = new_owner
        detail = (
            f"MCP server '{server_id}' in workspace '{workspace_id}' is already connected "
            f"under a different account (owned by agent '{existing_owner}'). Connecting "
            f"agent '{new_owner or 'unassigned'}' would silently redirect every agent's "
            f"tool calls for this provider to a different account, so this connection was "
            f"refused rather than applied. Disconnect the existing connection first if you "
            f"intend to replace it."
        )
        super().__init__(detail)


def _assert_no_cross_agent_credential_collision(
    *,
    existing: Optional[Dict[str, Any]],
    new_credential_id: Any,
    workspace_id: str,
    server_id: str,
) -> None:
    """Fires ONLY when there is an existing row with a credential_id, a NEW
    credential_id is being set that differs from it, AND the existing
    credential is scoped to one specific agent while the incoming credential
    belongs to a different agent (or is unscoped) -- see
    McpServerCredentialCollisionError / the module banner above.

    Deliberately permissive in every other case, to keep this migration-safe:
      - No existing row, or credential_id isn't being touched -> no-op.
      - Existing row's credential is workspace-shared/unassigned (today's
        single-account default) -> still silently overwritable, unchanged.
      - Same agent reconnecting / re-authing its own account -> allowed.
    """
    new_id = str(new_credential_id or "").strip()
    if not new_id or not isinstance(existing, dict):
        return  # nothing being set, or no prior row to collide with
    existing_id = str(existing.get("credential_id") or "").strip()
    if not existing_id or existing_id == new_id:
        return  # first assignment to this slot, or re-saving the same credential

    existing_owner = credential_owner_agent_install_id(existing_id)
    if not existing_owner:
        return  # existing row is workspace-shared, or its credential is gone -- reclaimable

    new_owner = credential_owner_agent_install_id(new_id)
    if new_owner == existing_owner:
        return  # same agent reconnecting / re-authing its own account

    raise McpServerCredentialCollisionError(server_id, workspace_id, existing_owner, new_owner)


def _workspace_bucket(workspace_id: str, registry: Dict[str, Any]) -> Dict[str, Any]:
    workspaces = registry.get("workspaces") if isinstance(registry.get("workspaces"), dict) else {}
    bucket = workspaces.get(workspace_id)
    return dict(bucket) if isinstance(bucket, dict) else {"servers": {}}


def _save_workspace_bucket(workspace_id: str, registry: Dict[str, Any], bucket: Dict[str, Any]) -> None:
    workspaces = registry.get("workspaces") if isinstance(registry.get("workspaces"), dict) else {}
    workspaces[workspace_id] = {"servers": bucket.get("servers") if isinstance(bucket.get("servers"), dict) else {}}
    registry["workspaces"] = workspaces
    registry["updated_at"] = _utc_now_iso()


def _normalize_tool_payload(raw: Dict[str, Any], *, server_id: str) -> Dict[str, Any]:
    name = _normalize_tool_name(raw.get("name"))
    if not name:
        raise ValueError("MCP tool name is required.")
    label = str(raw.get("label") or name).strip() or name
    connector_scopes = _normalize_list_of_strings(raw.get("connector_scopes"))
    default_server_scope = f"mcp:{server_id}"
    normalized_scopes = ["mcp"]
    if default_server_scope not in normalized_scopes:
        normalized_scopes.append(default_server_scope)
    for scope in connector_scopes:
        compact = scope.lower()
        if compact not in normalized_scopes:
            normalized_scopes.append(compact)
    trigger_terms = [token.lower() for token in _normalize_list_of_strings(raw.get("trigger_terms"))]
    action_class = _normalize_action_class(raw.get("action_class"))
    risk_level = _normalize_risk_level(raw.get("risk_level"), action_class=action_class)
    description = str(raw.get("description") or "").strip()
    high_stakes = _looks_high_stakes(name, description)
    if high_stakes and risk_level not in {"high", "critical"}:
        # Keep the risk badge honest for the UI: a declared-safe tool whose
        # name/description says "send"/"delete"/"charge" etc. shouldn't show
        # as "low risk" just because the remote server didn't self-report.
        risk_level = "high"
    permission_scopes = _normalize_list_of_strings(raw.get("permission_scopes")) or list(normalized_scopes)
    requires_approval = bool(raw.get("requires_approval")) or risk_level in {"high", "critical"} or high_stakes
    allowed_runtime_modes = _normalize_runtime_modes(raw.get("allowed_runtime_modes"))
    cost_class = _normalize_cost_class(raw.get("cost_class"))
    audit_event_type = str(raw.get("audit_event_type") or f"mcp.tool.{action_class}").strip() or f"mcp.tool.{action_class}"
    return {
        "name": name,
        "label": label[:160],
        "description": description[:500],
        "input_schema": _normalize_input_schema(raw.get("input_schema")),
        "action_class": action_class,
        "risk_level": risk_level,
        "connector_scopes": normalized_scopes,
        "permission_scopes": permission_scopes,
        "trigger_terms": trigger_terms,
        "allowed_runtime_modes": allowed_runtime_modes,
        "requires_approval": requires_approval,
        "audit_event_type": audit_event_type,
        "cost_class": cost_class,
        "permission_manifest": {
            "action_class": action_class,
            "risk_level": risk_level,
            "scopes": permission_scopes,
            "requires_approval": requires_approval,
            "allowed_runtime_modes": allowed_runtime_modes,
            "cost_class": cost_class,
            "audit_event_type": audit_event_type,
        },
        "enabled": bool(raw.get("enabled", True)),
        # MAN-92/MAN-104: auto-approve by default — the wall-of-toggles this
        # replaces required a manual click per tool regardless of risk. Only
        # tools flagged requires_approval (money/delete/third-party-send,
        # or declared high/critical risk) still default to unapproved; the
        # explicit approve/deny routes remain the only way to change either
        # state, and an explicit raw["approved"] (e.g. a stored value being
        # re-normalized) always wins over this default.
        "approved": bool(raw.get("approved", not requires_approval)),
    }


def _tool_items_from_list_result(result: Any, *, server_id: str) -> List[Dict[str, Any]]:
    tool_items = []
    raw_tools = None
    if isinstance(result, list):
        raw_tools = result
    elif isinstance(result, dict):
        raw_tools = result.get("tools")
    else:
        raw_tools = getattr(result, "tools", None)
    if not isinstance(raw_tools, list):
        return []
    for raw in raw_tools:
        if isinstance(raw, dict):
            tool = dict(raw)
        else:
            tool = {
                "name": getattr(raw, "name", None),
                "description": getattr(raw, "description", None),
                "input_schema": getattr(raw, "inputSchema", None) or getattr(raw, "input_schema", None),
            }
        try:
            tool_items.append(_normalize_tool_payload(tool, server_id=server_id))
        except ValueError:
            continue
    return tool_items


def _resolve_mcp_credential(server: Dict[str, Any], workspace_id: str) -> Optional[Dict[str, Any]]:
    """Resolve the credential dict for an MCP server from the vault, if credential_id is set.

    Also triggers an OAuth token refresh if the credential is expired or about to expire.
    """
    credential_id = str(server.get("credential_id") or "").strip()
    if not credential_id:
        return None
    try:
        # Attempt token refresh before resolving (no-op if not needed)
        try:
            from server_modules.connection_oauth_service import refresh_oauth_token_if_needed
            refresh_oauth_token_if_needed(credential_id)
        except Exception:
            pass  # refresh is best-effort; proceed with existing credential
        from server_modules.vault_store import _openssl_decrypt, load_vault
        from server_modules.vault_helpers import resolve_vault_credential
        return resolve_vault_credential(load_vault, _openssl_decrypt, credential_id, workspace_id=workspace_id)
    except Exception:
        _log.warning("Failed to resolve credential %s for MCP server %s", credential_id, server.get("id"))
        return None


def mcp_result_is_error(result: Any) -> bool:
    """The MCP protocol's OWN failure flag (`CallToolResult.isError`).

    This is the explicit, unambiguous signal — the spec's way for a server to
    say "the tool ran and failed" without raising a transport error — and it
    used to be dropped on the floor: _mcp_result_payload read only
    structuredContent/content, so a server replying isError=true with the text
    "Error: repository not found" produced a result stamped `status: "ok"`,
    a green activity row, and a tool-honesty trace entry claiming a real
    success. Anything downstream that wants to know whether an MCP call failed
    should read this rather than inferring from the reply text.
    """
    if isinstance(result, dict):
        flag = result.get("isError", result.get("is_error"))
    else:
        flag = getattr(result, "isError", None)
        if flag is None:
            flag = getattr(result, "is_error", None)
    return flag is True


def _mcp_result_payload(result: Any) -> Any:
    if isinstance(result, dict):
        return result
    structured = getattr(result, "structuredContent", None)
    if structured is not None:
        return structured
    content = getattr(result, "content", None)
    if isinstance(content, list):
        text_parts: List[str] = []
        for item in content:
            text = getattr(item, "text", None)
            if isinstance(text, str) and text.strip():
                text_parts.append(text.strip())
        if len(text_parts) == 1:
            try:
                return json.loads(text_parts[0])
            except Exception:
                return {"text": text_parts[0]}
        if text_parts:
            return {"text": "\n".join(text_parts)}
    return {}


def _run_async_from_sync(coro_factory: Any) -> Any:
    try:
        return asyncio.run(coro_factory())
    except RuntimeError as exc:
        if "asyncio.run() cannot be called from a running event loop" not in str(exc):
            raise

    result: Dict[str, Any] = {}
    failure: Dict[str, BaseException] = {}

    def _runner() -> None:
        try:
            result["value"] = asyncio.run(coro_factory())
        except BaseException as err:  # pragma: no cover
            failure["error"] = err

    thread = threading.Thread(target=_runner, daemon=True)
    thread.start()
    thread.join()
    if "error" in failure:
        raise failure["error"]
    return result.get("value")


def _is_transient_mcp_error(exc: BaseException) -> bool:
    """Return True if the error is likely transient and worth retrying.

    Retryable: httpx connection/timeout/protocol errors, HTTP 5xx/429,
    asyncio TimeoutError, OSError (connection reset/refused).
    Non-retryable: HTTP 4xx (auth, bad request, not found), and our own
    RuntimeError messages for permanent failures.
    """
    # httpx transport errors — always transient
    if isinstance(exc, httpx.ConnectError):
        return True
    if isinstance(exc, (httpx.ConnectTimeout, httpx.ReadTimeout, httpx.WriteTimeout)):
        return True
    if isinstance(exc, httpx.RemoteProtocolError):
        return True
    # httpx HTTPStatusError — retry only 5xx and 429
    if isinstance(exc, httpx.HTTPStatusError):
        status = getattr(exc.response, "status_code", 0)
        return status in {408, 425, 429, 500, 502, 503, 504}
    # asyncio timeout and OS-level network errors
    if isinstance(exc, (TimeoutError, ConnectionRefusedError, ConnectionResetError, OSError)):
        return True
    # RuntimeError — retry only our "timed out" wrapper; never retry permanent failures
    if isinstance(exc, RuntimeError):
        msg = str(exc).lower()
        permanent = (
            "not installed" in msg
            or "not approved" in msg
            or "not found" in msg
            or "disabled" in msg
            or "not supported" in msg
        )
        if permanent:
            return False
        return "timed out" in msg or "timeout" in msg
    return False


# ═══════════════════════════════════════════════════════════════════════════
# Phase D — auth durability for unattended agents
# (docs/design/mcp-applications-plan.md, Phase D). An agent may run for hours
# or days unattended; if a connected MCP server's OAuth token expires and the
# owner is never told, the agent just silently starts failing that tool.
# This section: (1) detects the 401/invalid_token shape, (2) attempts exactly
# one refresh + retry through the EXISTING oauth refresh machinery
# (connection_oauth_service — no second refresh implementation), and
# (3) on refresh failure, marks the server "reauth_required" in the registry
# and alerts the workspace owner exactly once (never silently retries
# forever), while giving the agent a short, honest error it can act on.
# ═══════════════════════════════════════════════════════════════════════════


class McpReauthRequiredError(RuntimeError):
    """Raised internally when an MCP auth failure survives the one-shot
    refresh+retry — the workspace owner must reconnect the server. Carries a
    short machine-readable *reason* code and a human *detail* string so the
    caller can mark registry status and build the owner alert without
    re-deriving context from the original transport exception."""

    def __init__(self, reason: str, detail: str) -> None:
        self.reason = str(reason or "refresh_failed")
        self.detail = str(detail or "Token refresh failed.")
        super().__init__(self.detail)


def _iter_exception_chain(exc: BaseException):
    """Yield *exc* and everything reachable from it via __cause__,
    __context__, and (Python 3.11+) ExceptionGroup.exceptions. The mcp SDK's
    streamable_http transport runs inside anyio task groups, which wrap the
    underlying httpx.HTTPStatusError in a BaseExceptionGroup rather than
    letting it propagate directly — a plain isinstance() check on the
    top-level exception misses it."""
    seen: set[int] = set()
    stack: List[BaseException] = [exc]
    while stack:
        current = stack.pop()
        if current is None or id(current) in seen:
            continue
        seen.add(id(current))
        yield current
        sub_exceptions = getattr(current, "exceptions", None)
        if isinstance(sub_exceptions, (list, tuple)):
            stack.extend(item for item in sub_exceptions if isinstance(item, BaseException))
        if isinstance(current.__cause__, BaseException):
            stack.append(current.__cause__)
        if isinstance(current.__context__, BaseException) and current.__context__ is not current.__cause__:
            stack.append(current.__context__)


_MCP_AUTH_HTTP_STATUS_CODES = frozenset({401, 403})
# 401 is the MCP-mandated shape for an invalid/expired bearer token
# (mcp/server/auth/middleware/bearer_auth.py: 401 + WWW-Authenticate:
# Bearer error="invalid_token", per RFC 6750). 403 covers insufficient_scope
# servers send for the same "reconnect with the right grant" outcome.
_MCP_AUTH_MESSAGE_TOKENS = ("invalid_token", "unauthorized", "insufficient_scope", " 401 ")


def _is_mcp_auth_error(exc: BaseException) -> bool:
    """True if *exc* (or anything in its cause/context/sub-exception chain,
    see _iter_exception_chain) is the auth-failure shape the MCP SDK/HTTP
    layer actually raises for an expired or revoked OAuth token."""
    for candidate in _iter_exception_chain(exc):
        if isinstance(candidate, httpx.HTTPStatusError):
            status = getattr(candidate.response, "status_code", 0)
            if status in _MCP_AUTH_HTTP_STATUS_CODES:
                return True
        message = f" {candidate!s} ".lower()
        if any(token in message for token in _MCP_AUTH_MESSAGE_TOKENS):
            return True
    return False


async def _retry_mcp_call_after_auth_refresh_async(
    *,
    server: Dict[str, Any],
    tool_name: str,
    arguments: Dict[str, Any],
    original_exc: BaseException,
    client_session_cls: Any = ClientSession,
    streamable_http_client_fn: Any = streamable_http_client,
    call_timeout_seconds: float = 60.0,
) -> Any:
    """Called after the first MCP tool-call attempt failed with an auth
    error (_is_mcp_auth_error). Attempts exactly one token refresh through
    the EXISTING oauth refresh machinery (connection_oauth_service.
    refresh_oauth_token_now — the forced variant of the same
    refresh_oauth_token_if_needed() codepath _resolve_mcp_credential already
    calls opportunistically) and retries the call exactly once with the
    refreshed credential.

    Returns the successful result. Raises McpReauthRequiredError — never a
    bare exception — if refresh failed or the retried call still auth-fails,
    so the caller can distinguish "needs owner reconnect" from an ordinary
    transient error.
    """
    credential_id = str(server.get("credential_id") or "").strip()
    if not credential_id:
        raise McpReauthRequiredError(
            "no_credential",
            "This server has no OAuth credential on file to refresh.",
        ) from original_exc

    from server_modules.connection_oauth_service import refresh_oauth_token_now

    outcome = refresh_oauth_token_now(credential_id)
    if not outcome.get("ok"):
        raise McpReauthRequiredError(
            str(outcome.get("reason") or "refresh_failed"),
            str(outcome.get("detail") or "Token refresh failed."),
        ) from original_exc

    refreshed_credential = outcome.get("credential") if isinstance(outcome.get("credential"), dict) else None
    retry_http_client = _build_mcp_http_client(refreshed_credential)
    try:
        return await _call_streamable_http_tool_async(
            endpoint=str(server.get("endpoint") or "").strip(),
            tool_name=tool_name,
            arguments=arguments,
            http_client=retry_http_client,
            client_session_cls=client_session_cls,
            streamable_http_client_fn=streamable_http_client_fn,
            call_timeout_seconds=call_timeout_seconds,
        )
    except Exception as retry_exc:
        if _is_mcp_auth_error(retry_exc):
            raise McpReauthRequiredError(
                "refresh_did_not_fix_auth",
                "Token refresh succeeded but the retried call still failed authentication.",
            ) from retry_exc
        raise


def _mark_mcp_server_reauth_required(
    *,
    workspace_id: str,
    server: Dict[str, Any],
    reason: str,
    detail: str,
) -> None:
    """Flip the server's registry-recorded status to "reauth_required" (with
    a timestamp) so the Settings -> MCP servers UI's derived status can
    surface it. Goes through the same upsert_workspace_mcp_server() choke
    point approve_mcp_tool()/deny_mcp_tool() already use for single-field
    server mutations (discover_tools=False — this never makes a network
    call), so status persists consistently with every other field. Never
    raises — this is a best-effort side effect of a failure path."""
    normalized_workspace_id = str(workspace_id or "").strip()
    server_id = _normalize_server_id(server.get("id"))
    if not normalized_workspace_id or not server_id:
        return
    try:
        upsert_workspace_mcp_server(
            workspace_id=normalized_workspace_id,
            server_id=server_id,
            label=server.get("label"),
            transport=server.get("transport"),
            endpoint=server.get("endpoint"),
            enabled=server.get("enabled", True),
            tools=server.get("tools"),
            metadata=server.get("metadata"),
            discover_tools=False,
            credential_id=server.get("credential_id"),
            status="reauth_required",
            status_detail=f"{reason}: {detail}" if reason else detail,
        )
    except Exception:
        _log.warning("Failed to mark MCP server %s as reauth_required", server_id, exc_info=True)


async def _alert_owner_mcp_reauth_required_async(
    *,
    workspace_id: str,
    tenant_id: str,
    server: Dict[str, Any],
    tool_name: str,
    reauth_exc: McpReauthRequiredError,
    run_id: Optional[str],
    thread_id: Optional[str],
    agent_id: Optional[str],
) -> RuntimeError:
    """Side effects for an unrecoverable MCP auth failure (Phase D). Marks
    the server reauth_required in the registry, alerts the workspace owner
    exactly once through the existing notification path — the same pairing
    deployed_agent_cost_cap_service.py uses for its "owner must act" alerts:
    outbox_service.emit_notification_event() (deployed_agent_cost_cap_
    service.py:212, feeds the push/inbox notification feed via
    notification_service.build_notification_from_outbox_event's "notification"
    branch) plus activity_ledger_service.append_activity_event(review_
    required=True) (deployed_agent_cost_cap_service.py:331-359, the durable
    audit-reference record) — and returns (does NOT raise) the short, honest
    RuntimeError the caller should propagate to the agent."""
    server_id = _normalize_server_id(server.get("id"))
    server_label = str(server.get("label") or server_id).strip() or server_id

    _mark_mcp_server_reauth_required(
        workspace_id=workspace_id,
        server=server,
        reason=reauth_exc.reason,
        detail=reauth_exc.detail,
    )

    try:
        from server_modules import outbox_service

        outbox_service.emit_notification_event(
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            action="mcp_server_reauth_required",
            text=(
                f"{server_label} needs to be reconnected. Its connection expired and "
                "could not refresh automatically — reconnect it to keep this agent's tools working."
            ),
            run_id=run_id,
            metadata={
                "title": "Reconnect required",
                "priority": "high",
                "server_id": server_id,
                "server_label": server_label,
                "tool_name": tool_name,
                "reason": reauth_exc.reason,
                "path": f"/w/{workspace_id}/settings",
            },
        )
    except Exception:
        _log.warning("mcp reauth alert: emit_notification_event failed for server %s", server_id, exc_info=True)

    try:
        from server_modules import activity_ledger_service

        await activity_ledger_service.append_activity_event(
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            actor_type="system",
            actor_id=f"mcp:{server_id}",
            event_class="blocked_action",
            detail_level="audit_reference",
            action="mcp_reauth_required",
            run_id=run_id,
            thread_id=thread_id,
            title=f"Reconnect required: {server_label}",
            summary=(
                f"MCP server '{server_label}' auth refresh failed ({reauth_exc.reason}) "
                f"while calling '{tool_name}'. The workspace owner must reconnect it."
            ),
            status="blocked",
            review_required=True,
            metadata={
                "connector_id": f"mcp:{server_id}",
                "server_id": server_id,
                "tool_name": tool_name,
                "reason": reauth_exc.reason,
                "detail": reauth_exc.detail,
                "agent_id": agent_id,
            },
        )
    except Exception:
        _log.warning("mcp reauth alert: append_activity_event failed for server %s", server_id, exc_info=True)

    return RuntimeError(f"{server_label} needs the owner to reconnect it — they've been notified.")


async def _invoke_mcp_tool_with_auth_recovery_async(
    *,
    server: Dict[str, Any],
    workspace_id: str,
    tenant_id: str,
    tool_name: str,
    arguments: Dict[str, Any],
    http_client: Any,
    client_session_cls: Any = ClientSession,
    streamable_http_client_fn: Any = streamable_http_client,
    run_id: Optional[str] = None,
    thread_id: Optional[str] = None,
    agent_id: Optional[str] = None,
) -> Any:
    """The Phase D auth-durability seam. Every invoke_workspace_mcp_* entry
    point (sync and async) calls this instead of _call_streamable_http_tool_
    async() directly:
      1. First attempt uses *http_client* (already built from the credential
         the caller resolved).
      2. On a 401/invalid_token-shaped failure (_is_mcp_auth_error), force a
         token refresh through the EXISTING oauth refresh machinery and
         retry once with the refreshed credential
         (_retry_mcp_call_after_auth_refresh_async). If that retry succeeds,
         this returns the result normally — the agent never notices.
      3. If refresh fails (revoked/expired grant, no credential on file,
         etc.) or the retry itself still auth-fails: mark the server
         reauth_required, alert the workspace owner once, and raise a short,
         honest RuntimeError naming the server (never a bare transport
         error) so the agent can adapt instead of retrying blindly.
    Non-auth errors are re-raised completely untouched by step 2's check —
    this function only ever intercepts the auth-error shape; every other
    failure (timeout, 5xx, malformed args, ...) behaves exactly as before.

    Sync callers run this via asyncio.run(...) exactly like they already run
    _call_streamable_http_tool_async(...) — no separate sync implementation
    of the recovery logic exists.
    """
    try:
        return await _call_streamable_http_tool_async(
            endpoint=str(server.get("endpoint") or "").strip(),
            tool_name=tool_name,
            arguments=arguments,
            http_client=http_client,
            client_session_cls=client_session_cls,
            streamable_http_client_fn=streamable_http_client_fn,
        )
    except Exception as exc:
        if not _is_mcp_auth_error(exc):
            raise
        try:
            return await _retry_mcp_call_after_auth_refresh_async(
                server=server,
                tool_name=tool_name,
                arguments=arguments,
                original_exc=exc,
                client_session_cls=client_session_cls,
                streamable_http_client_fn=streamable_http_client_fn,
            )
        except McpReauthRequiredError as reauth_exc:
            honest_exc = await _alert_owner_mcp_reauth_required_async(
                workspace_id=workspace_id,
                tenant_id=tenant_id,
                server=server,
                tool_name=tool_name,
                reauth_exc=reauth_exc,
                run_id=run_id,
                thread_id=thread_id,
                agent_id=agent_id,
            )
            raise honest_exc from reauth_exc


async def _list_tools_streamable_http_async(
    *,
    endpoint: str,
    http_client: Any = None,
    client_session_cls: Any = ClientSession,
    streamable_http_client_fn: Any = streamable_http_client,
    discover_timeout_seconds: float = 30.0,
) -> List[Dict[str, Any]]:
    if client_session_cls is None or streamable_http_client_fn is None:
        raise RuntimeError("The MCP client dependency is not installed.")
    try:
        max_attempts = 2
        base_delay = 1.0
        for attempt in range(1, max_attempts + 1):
            try:
                async with asyncio.timeout(discover_timeout_seconds):
                    async with streamable_http_client_fn(endpoint, http_client=http_client) as (read_stream, write_stream, _):
                        async with client_session_cls(read_stream, write_stream) as session:
                            await session.initialize()
                            result = await session.list_tools()
                break
            except TimeoutError:
                if attempt < max_attempts:
                    _log.warning(
                        "MCP tool discovery attempt %d/%d timed out after %.0fs for endpoint %s, retrying...",
                        attempt, max_attempts, discover_timeout_seconds, endpoint,
                    )
                    await asyncio.sleep(base_delay * attempt)
                    continue
                raise RuntimeError(
                    f"MCP tool discovery timed out after {max_attempts} attempts "
                    f"({discover_timeout_seconds:.0f}s each) for endpoint {endpoint}"
                ) from None
            except Exception as exc:
                if attempt < max_attempts and _is_transient_mcp_error(exc):
                    _log.warning(
                        "MCP tool discovery attempt %d/%d failed for endpoint %s: %s, retrying...",
                        attempt, max_attempts, endpoint, exc,
                    )
                    await asyncio.sleep(base_delay * attempt)
                    continue
                raise
        return _tool_items_from_list_result(result, server_id="temporary")
    finally:
        await _maybe_close_mcp_http_client(http_client)


def _build_mcp_auth_headers(credential: Optional[Dict[str, Any]]) -> Dict[str, str]:
    """Build HTTP Authorization headers from a credential dict for MCP client injection.

    Supports:
    - OAuth2: credential with ``access_token`` → ``Authorization: Bearer <token>``
    - API key: credential with ``api_key`` → ``Authorization: ApiKey <key>``
    - Bot token: credential with ``bot_token`` → ``Authorization: Bot <token>``
    """
    if not isinstance(credential, dict) or not credential:
        return {}
    access_token = str(credential.get("access_token") or "").strip()
    if access_token:
        return {"Authorization": f"Bearer {access_token}"}
    api_key = str(credential.get("api_key") or "").strip()
    if api_key:
        return {"Authorization": f"ApiKey {api_key}"}
    bot_token = str(credential.get("bot_token") or "").strip()
    if bot_token:
        return {"Authorization": f"Bot {bot_token}"}
    return {}


async def _validate_mcp_request_url_async(url: str) -> None:
    """Async wrapper around _validate_mcp_endpoint's synchronous checks
    (scheme, blocked hosts, reserved TLDs, blocked IP ranges, and the
    assert_safe_outbound_url DNS-resolution check) so it can be awaited from
    an httpx event hook without blocking the event loop on the DNS lookup
    inside assert_safe_outbound_url."""
    await asyncio.to_thread(_validate_mcp_endpoint, url)


async def _mcp_redirect_guard_request_hook(request: Any) -> None:
    """MAN-109: httpx "request" event hook attached to every client built by
    _build_mcp_http_client below. httpx re-invokes "request" hooks for EACH
    request it sends, including the redirect-target request on every hop of
    a redirect chain (see httpx.AsyncClient._send_handling_redirects: the
    hook loop runs again each time around the `while True` before the next
    request — built from the prior response's Location header — is sent).

    This is the actual fix for the "validate-once, connect-many" gap: the
    MCP SDK's create_mcp_http_client() hardcodes follow_redirects=True, and
    _validate_mcp_endpoint() only ran once, at server-registration time
    (upsert_workspace_mcp_server{,_async}). Without this hook, a workspace
    owner could register an innocuous-looking public MCP endpoint that later
    302s to a loopback/link-local/private/cloud-metadata address, and every
    subsequent tool-discovery or tool-call connection would follow that
    redirect transparently. Raising here (ValueError from
    _validate_mcp_endpoint, or RuntimeError from assert_safe_outbound_url)
    aborts the request before it is ever sent — httpx does not catch
    exceptions raised from a "request" hook, so this propagates straight out
    of the streamable_http_client(...) call.
    """
    await _validate_mcp_request_url_async(str(request.url))


async def _maybe_close_mcp_http_client(http_client: Any) -> None:
    """Best-effort close for an httpx.AsyncClient built by
    _build_mcp_http_client and owned for the lifetime of a single discover/
    tool-call operation (including its internal retry attempts).

    The streamable_http transport only closes a client it created itself
    (the http_client=None case) -- a client WE pass in is treated as
    caller-owned and never closed by the SDK (see streamable_http_client's
    `client_provided` branch). Since _build_mcp_http_client now ALWAYS
    builds a client (see below), every discover/call would otherwise leak
    that client's connection pool. Swallows close failures -- cleanup must
    never mask the operation's real outcome.
    """
    if http_client is None:
        return
    aclose = getattr(http_client, "aclose", None)
    if not callable(aclose):
        return
    try:
        await aclose()
    except Exception:
        _log.debug("Failed to close MCP http client", exc_info=True)


def _build_mcp_http_client(
    credential: Optional[Dict[str, Any]],
    timeout_seconds: float = 60.0,
) -> Any:
    """Build an httpx.AsyncClient with MCP defaults, auth headers from a
    credential (if any), and the MAN-109 per-hop SSRF re-validation hook
    (_mcp_redirect_guard_request_hook).

    Returns None only if the MCP SDK is not installed. Unlike before
    MAN-109, this ALWAYS returns a client -- even with no credential/no auth
    headers -- because the redirect guard must cover every outbound MCP
    connection, not only authenticated ones: an unauthenticated MCP server
    can redirect an outbound connection just as easily as an authenticated
    one. Callers get a client they own for one discover/call operation (see
    _maybe_close_mcp_http_client, which every internal caller uses to close
    it once that operation -- including its retries -- is finished).
    """
    if create_mcp_http_client is None:
        return None
    headers = _build_mcp_auth_headers(credential)
    timeout = httpx.Timeout(timeout_seconds) if timeout_seconds > 0 else None
    client = create_mcp_http_client(headers=headers or None, timeout=timeout)
    client.event_hooks = {"request": [_mcp_redirect_guard_request_hook]}
    return client


def discover_mcp_server_tools(
    *,
    transport: McpTransport,
    endpoint: str,
    server_id: str,
    credential: Optional[Dict[str, Any]] = None,
    client_session_cls: Any = ClientSession,
    streamable_http_client_fn: Any = streamable_http_client,
) -> List[Dict[str, Any]]:
    if transport != "streamable_http":
        raise RuntimeError(f"Unsupported MCP transport '{transport}'.")
    mcp_http_client = _build_mcp_http_client(credential)
    tools = _run_async_from_sync(
        lambda: _list_tools_streamable_http_async(
            endpoint=endpoint,
            http_client=mcp_http_client,
            client_session_cls=client_session_cls,
            streamable_http_client_fn=streamable_http_client_fn,
        )
    )
    return [_normalize_tool_payload(tool, server_id=server_id) for tool in tools]


async def discover_mcp_server_tools_async(
    *,
    transport: McpTransport,
    endpoint: str,
    server_id: str,
    credential: Optional[Dict[str, Any]] = None,
    client_session_cls: Any = ClientSession,
    streamable_http_client_fn: Any = streamable_http_client,
) -> List[Dict[str, Any]]:
    if transport != "streamable_http":
        raise RuntimeError(f"Unsupported MCP transport '{transport}'.")
    mcp_http_client = _build_mcp_http_client(credential)
    tools = await _list_tools_streamable_http_async(
        endpoint=endpoint,
        http_client=mcp_http_client,
        client_session_cls=client_session_cls,
        streamable_http_client_fn=streamable_http_client_fn,
    )
    return [_normalize_tool_payload(tool, server_id=server_id) for tool in tools]


def _normalize_server_payload(
    *,
    server_id: str,
    label: Any,
    transport: Any,
    endpoint: Any,
    enabled: Any,
    tools: Any,
    metadata: Any,
    existing: Optional[Dict[str, Any]] = None,
    credential_id: Any = None,
    status: Any = None,
    status_detail: Any = None,
) -> Dict[str, Any]:
    normalized_server_id = _normalize_server_id(server_id)
    if not normalized_server_id:
        raise ValueError("MCP server id is required.")
    normalized_endpoint = str(endpoint or "").strip()
    if not normalized_endpoint:
        raise ValueError("MCP endpoint is required.")
    normalized_tools: List[Dict[str, Any]] = []
    if isinstance(tools, list):
        for raw_tool in tools:
            if not isinstance(raw_tool, dict):
                continue
            normalized_tools.append(_normalize_tool_payload(raw_tool, server_id=normalized_server_id))
    current = dict(existing) if isinstance(existing, dict) else {}
    normalized_credential_id = str(credential_id or "").strip() or None if credential_id is not None else current.get("credential_id")
    # Phase D (auth durability, docs/design/mcp-applications-plan.md):
    # `status` is an explicit-set sentinel just like `credential_id` above —
    # None means "don't touch it". _mark_mcp_server_reauth_required() passes
    # status="reauth_required" explicitly. Absent that, a *changed*
    # credential_id (initial connect, or the owner reconnecting after a
    # reauth_required alert) gets a clean "ok" slate — the next call
    # re-flags it if it's still broken. Every other save (enable/disable
    # toggle, tool approve/deny, plain re-save) leaves status untouched.
    credential_id_changed = credential_id is not None and normalized_credential_id != current.get("credential_id")
    if status is not None:
        normalized_status = str(status).strip().lower() or "ok"
        normalized_status_detail = str(status_detail or "").strip() or None
        status_updated_at = _utc_now_iso()
    elif credential_id_changed:
        normalized_status = "ok"
        normalized_status_detail = None
        status_updated_at = _utc_now_iso()
    else:
        normalized_status = str(current.get("status") or "ok").strip().lower() or "ok"
        normalized_status_detail = current.get("status_detail")
        status_updated_at = current.get("status_updated_at")
    return {
        "id": normalized_server_id,
        "label": str(label or normalized_server_id).strip()[:160] or normalized_server_id,
        "transport": _normalize_transport(transport),
        "endpoint": normalized_endpoint,
        "enabled": bool(enabled if enabled is not None else current.get("enabled", True)),
        "advanced_only": True,
        "credential_id": normalized_credential_id,
        "status": normalized_status,
        "status_detail": normalized_status_detail,
        "status_updated_at": status_updated_at,
        "tools": normalized_tools or (current.get("tools") if isinstance(current.get("tools"), list) else []),
        "metadata": dict(metadata) if isinstance(metadata, dict) else {},
        "last_synced_at": current.get("last_synced_at"),
        "created_at": str(current.get("created_at") or _utc_now_iso()),
        "updated_at": _utc_now_iso(),
    }


def list_workspace_mcp_servers(workspace_id: str) -> List[Dict[str, Any]]:
    normalized_workspace_id = str(workspace_id or "").strip()
    if not normalized_workspace_id:
        return []
    registry = load_mcp_server_registry()
    bucket = _workspace_bucket(normalized_workspace_id, registry)
    servers = bucket.get("servers") if isinstance(bucket.get("servers"), dict) else {}
    items: List[Dict[str, Any]] = []
    for server_id, raw in sorted(servers.items()):
        if not isinstance(raw, dict):
            continue
        payload = dict(raw)
        payload["id"] = _normalize_server_id(payload.get("id") or server_id)
        payload["tool_count"] = len(payload.get("tools") if isinstance(payload.get("tools"), list) else [])
        payload["skill_ids"] = [
            mcp_skill_id(payload["id"], str(tool.get("name") or ""))
            for tool in (payload.get("tools") if isinstance(payload.get("tools"), list) else [])
            if str(tool.get("name") or "").strip()
        ]
        items.append(payload)
    return items


def get_workspace_mcp_server(workspace_id: str, server_id: str) -> Optional[Dict[str, Any]]:
    normalized_workspace_id = str(workspace_id or "").strip()
    normalized_server_id = _normalize_server_id(server_id)
    if not normalized_workspace_id or not normalized_server_id:
        return None
    registry = load_mcp_server_registry()
    bucket = _workspace_bucket(normalized_workspace_id, registry)
    servers = bucket.get("servers") if isinstance(bucket.get("servers"), dict) else {}
    payload = servers.get(normalized_server_id)
    return dict(payload) if isinstance(payload, dict) else None


def upsert_workspace_mcp_server(
    *,
    workspace_id: str,
    server_id: str,
    label: Any,
    transport: Any,
    endpoint: Any,
    enabled: Any = True,
    tools: Any = None,
    metadata: Any = None,
    discover_tools: bool = False,
    credential_id: Any = None,
    status: Any = None,
    status_detail: Any = None,
) -> Dict[str, Any]:
    normalized_workspace_id = str(workspace_id or "").strip()
    if not normalized_workspace_id:
        raise ValueError("workspace_id is required.")
    registry = load_mcp_server_registry()
    bucket = _workspace_bucket(normalized_workspace_id, registry)
    servers = bucket.get("servers") if isinstance(bucket.get("servers"), dict) else {}
    normalized_server_id = _normalize_server_id(server_id)
    existing = servers.get(normalized_server_id) if isinstance(servers.get(normalized_server_id), dict) else None

    _assert_no_cross_agent_credential_collision(
        existing=existing,
        new_credential_id=credential_id,
        workspace_id=normalized_workspace_id,
        server_id=normalized_server_id,
    )

    _validate_mcp_endpoint(str(endpoint or "").strip())

    payload = _normalize_server_payload(
        server_id=normalized_server_id,
        label=label,
        transport=transport,
        endpoint=endpoint,
        enabled=enabled,
        tools=tools,
        metadata=metadata,
        existing=existing,
        credential_id=credential_id,
        status=status,
        status_detail=status_detail,
    )
    if discover_tools:
        existing_approvals = {
            _normalize_tool_name(tool.get("name")): bool(tool.get("approved", True))
            for tool in (existing.get("tools") if isinstance(existing, dict) and isinstance(existing.get("tools"), list) else [])
            if isinstance(tool, dict) and _normalize_tool_name(tool.get("name"))
        }
        discovery_credential = _resolve_mcp_credential(payload, normalized_workspace_id)
        try:
            discovered = discover_mcp_server_tools(
                transport=payload["transport"],
                endpoint=payload["endpoint"],
                server_id=payload["id"],
                credential=discovery_credential,
            )
        except Exception as exc:
            # MAN-111/MAN-124: a raised discovery error used to abort this
            # function before `servers[payload["id"]] = payload` ran, so a
            # failed FIRST registration left no trace anywhere queryable —
            # not in this registry, not in the catalog, nothing. The caller
            # (e.g. connection_oauth_service._register_mcp_servers_for_provider)
            # still learns about the failure via this re-raise, but now a
            # server row is also persisted with an honest status so anything
            # that reads the registry later (health checks, the catalog,
            # a human debugging) can see it too instead of the credential
            # looking connected with zero explanation for zero tools.
            payload["status"] = "discovery_failed"
            payload["status_detail"] = str(exc) or exc.__class__.__name__
            payload["status_updated_at"] = _utc_now_iso()
            payload["last_synced_at"] = _utc_now_iso()
            payload["updated_at"] = payload["last_synced_at"]
            servers[payload["id"]] = payload
            bucket["servers"] = servers
            _save_workspace_bucket(normalized_workspace_id, registry, bucket)
            save_mcp_server_registry(registry)
            raise
        if discovered:
            for tool in discovered:
                normalized_name = _normalize_tool_name(tool.get("name"))
                # MAN-92/MAN-104: a prior explicit approve/deny always wins;
                # absent one, keep whatever _normalize_tool_payload already
                # decided above (auto-approved unless requires_approval —
                # money/delete/third-party-send, or declared high/critical
                # risk) instead of unconditionally forcing every rediscovered
                # tool back to unapproved.
                tool["approved"] = bool(existing_approvals.get(normalized_name, tool.get("approved", False)))
            _needs_approval = sum(1 for t in discovered if t.get("requires_approval") and not t.get("approved"))
            _log.info(
                "MCP server %s: %d tools discovered, %d auto-enabled, %d awaiting approval (money/destructive/third-party actions).",
                payload["id"],
                len(discovered),
                len(discovered) - _needs_approval,
                _needs_approval,
            )
            payload["tools"] = discovered
        payload["last_synced_at"] = _utc_now_iso()
        payload["updated_at"] = payload["last_synced_at"]
    servers[payload["id"]] = payload
    bucket["servers"] = servers
    _save_workspace_bucket(normalized_workspace_id, registry, bucket)
    save_mcp_server_registry(registry)
    return payload


async def upsert_workspace_mcp_server_async(
    *,
    workspace_id: str,
    server_id: str,
    label: Any,
    transport: Any,
    endpoint: Any,
    enabled: Any = True,
    tools: Any = None,
    metadata: Any = None,
    discover_tools: bool = False,
    credential_id: Any = None,
    status: Any = None,
    status_detail: Any = None,
) -> Dict[str, Any]:
    normalized_workspace_id = str(workspace_id or "").strip()
    if not normalized_workspace_id:
        raise ValueError("workspace_id is required.")
    registry = load_mcp_server_registry()
    bucket = _workspace_bucket(normalized_workspace_id, registry)
    servers = bucket.get("servers") if isinstance(bucket.get("servers"), dict) else {}
    normalized_server_id = _normalize_server_id(server_id)
    existing = servers.get(normalized_server_id) if isinstance(servers.get(normalized_server_id), dict) else None

    _assert_no_cross_agent_credential_collision(
        existing=existing,
        new_credential_id=credential_id,
        workspace_id=normalized_workspace_id,
        server_id=normalized_server_id,
    )

    _validate_mcp_endpoint(str(endpoint or "").strip())

    payload = _normalize_server_payload(
        server_id=normalized_server_id,
        label=label,
        transport=transport,
        endpoint=endpoint,
        enabled=enabled,
        tools=tools,
        metadata=metadata,
        existing=existing,
        credential_id=credential_id,
        status=status,
        status_detail=status_detail,
    )
    if discover_tools:
        existing_approvals = {
            _normalize_tool_name(tool.get("name")): bool(tool.get("approved", True))
            for tool in (existing.get("tools") if isinstance(existing, dict) and isinstance(existing.get("tools"), list) else [])
            if isinstance(tool, dict) and _normalize_tool_name(tool.get("name"))
        }
        discovery_credential = _resolve_mcp_credential(payload, normalized_workspace_id)
        try:
            discovered = await discover_mcp_server_tools_async(
                transport=payload["transport"],
                endpoint=payload["endpoint"],
                server_id=payload["id"],
                credential=discovery_credential,
            )
        except Exception as exc:
            # See the sync twin (upsert_workspace_mcp_server) above for why
            # this persists before re-raising — MAN-111/MAN-124.
            payload["status"] = "discovery_failed"
            payload["status_detail"] = str(exc) or exc.__class__.__name__
            payload["status_updated_at"] = _utc_now_iso()
            payload["last_synced_at"] = _utc_now_iso()
            payload["updated_at"] = payload["last_synced_at"]
            servers[payload["id"]] = payload
            bucket["servers"] = servers
            _save_workspace_bucket(normalized_workspace_id, registry, bucket)
            save_mcp_server_registry(registry)
            raise
        if discovered:
            for tool in discovered:
                normalized_name = _normalize_tool_name(tool.get("name"))
                # MAN-92/MAN-104: a prior explicit approve/deny always wins;
                # absent one, keep whatever _normalize_tool_payload already
                # decided above (auto-approved unless requires_approval —
                # money/delete/third-party-send, or declared high/critical
                # risk) instead of unconditionally forcing every rediscovered
                # tool back to unapproved.
                tool["approved"] = bool(existing_approvals.get(normalized_name, tool.get("approved", False)))
            _needs_approval = sum(1 for t in discovered if t.get("requires_approval") and not t.get("approved"))
            _log.info(
                "MCP server %s: %d tools discovered, %d auto-enabled, %d awaiting approval (money/destructive/third-party actions).",
                payload["id"],
                len(discovered),
                len(discovered) - _needs_approval,
                _needs_approval,
            )
            payload["tools"] = discovered
        payload["last_synced_at"] = _utc_now_iso()
        payload["updated_at"] = payload["last_synced_at"]
    servers[payload["id"]] = payload
    bucket["servers"] = servers
    _save_workspace_bucket(normalized_workspace_id, registry, bucket)
    save_mcp_server_registry(registry)
    return payload


def approve_mcp_tool(*, workspace_id: str, server_id: str, tool_name: str) -> Dict[str, Any]:
    """Approve a single discovered-but-unapproved MCP tool for execution."""
    normalized_workspace_id = str(workspace_id or "").strip()
    normalized_server_id = _normalize_server_id(server_id)
    normalized_tool_name = _normalize_tool_name(tool_name)
    if not normalized_workspace_id or not normalized_server_id or not normalized_tool_name:
        raise ValueError("workspace_id, server_id, and tool_name are required.")
    server = get_workspace_mcp_server(normalized_workspace_id, normalized_server_id)
    if server is None:
        raise FileNotFoundError(f"MCP server '{server_id}' not found in workspace.")
    tools = server.get("tools") if isinstance(server.get("tools"), list) else []
    found = False
    for tool in tools:
        if _normalize_tool_name(tool.get("name")) == normalized_tool_name:
            tool["approved"] = True
            found = True
            break
    if not found:
        raise FileNotFoundError(f"MCP tool '{tool_name}' not found on server '{server_id}'.")
    return upsert_workspace_mcp_server(
        workspace_id=normalized_workspace_id,
        server_id=normalized_server_id,
        label=server.get("label"),
        transport=server.get("transport"),
        endpoint=server.get("endpoint"),
        enabled=server.get("enabled", True),
        tools=tools,
        metadata=server.get("metadata"),
        discover_tools=False,
        credential_id=server.get("credential_id"),
    )


def deny_mcp_tool(*, workspace_id: str, server_id: str, tool_name: str) -> Dict[str, Any]:
    """Deny (revoke approval of) a single MCP tool for execution."""
    normalized_workspace_id = str(workspace_id or "").strip()
    normalized_server_id = _normalize_server_id(server_id)
    normalized_tool_name = _normalize_tool_name(tool_name)
    if not normalized_workspace_id or not normalized_server_id or not normalized_tool_name:
        raise ValueError("workspace_id, server_id, and tool_name are required.")
    server = get_workspace_mcp_server(normalized_workspace_id, normalized_server_id)
    if server is None:
        raise FileNotFoundError(f"MCP server '{server_id}' not found in workspace.")
    tools = server.get("tools") if isinstance(server.get("tools"), list) else []
    found = False
    for tool in tools:
        if _normalize_tool_name(tool.get("name")) == normalized_tool_name:
            tool["approved"] = False
            found = True
            break
    if not found:
        raise FileNotFoundError(f"MCP tool '{tool_name}' not found on server '{server_id}'.")
    return upsert_workspace_mcp_server(
        workspace_id=normalized_workspace_id,
        server_id=normalized_server_id,
        label=server.get("label"),
        transport=server.get("transport"),
        endpoint=server.get("endpoint"),
        enabled=server.get("enabled", True),
        tools=tools,
        metadata=server.get("metadata"),
        discover_tools=False,
        credential_id=server.get("credential_id"),
    )


def list_mcp_server_tools(*, workspace_id: str, server_id: str) -> List[Dict[str, Any]]:
    """Return all discovered tools for a server with their approval status."""
    normalized_workspace_id = str(workspace_id or "").strip()
    normalized_server_id = _normalize_server_id(server_id)
    if not normalized_workspace_id or not normalized_server_id:
        return []
    server = get_workspace_mcp_server(normalized_workspace_id, normalized_server_id)
    if server is None:
        raise FileNotFoundError(f"MCP server '{server_id}' not found in workspace.")
    tools = server.get("tools") if isinstance(server.get("tools"), list) else []
    return [
        {
            "name": _normalize_tool_name(tool.get("name")),
            "label": str(tool.get("label") or tool.get("name") or "").strip(),
            "description": str(tool.get("description") or "").strip(),
            "action_class": _normalize_action_class(tool.get("action_class")),
            "risk_level": _normalize_risk_level(tool.get("risk_level"), action_class=_normalize_action_class(tool.get("action_class"))),
            "approved": bool(tool.get("approved", False)),
            "enabled": bool(tool.get("enabled", True)),
            "input_schema": _normalize_input_schema(tool.get("input_schema")),
        }
        for tool in tools
        if isinstance(tool, dict)
    ]


def refresh_workspace_mcp_server_tools(*, workspace_id: str, server_id: str) -> Dict[str, Any]:
    current = get_workspace_mcp_server(workspace_id, server_id)
    if current is None:
        raise FileNotFoundError(f"MCP server '{server_id}' is not registered for this workspace.")
    return upsert_workspace_mcp_server(
        workspace_id=workspace_id,
        server_id=server_id,
        label=current.get("label"),
        transport=current.get("transport"),
        endpoint=current.get("endpoint"),
        enabled=current.get("enabled", True),
        tools=current.get("tools"),
        metadata=current.get("metadata"),
        discover_tools=True,
    )


async def refresh_workspace_mcp_server_tools_async(*, workspace_id: str, server_id: str) -> Dict[str, Any]:
    current = get_workspace_mcp_server(workspace_id, server_id)
    if current is None:
        raise FileNotFoundError(f"MCP server '{server_id}' is not registered for this workspace.")
    return await upsert_workspace_mcp_server_async(
        workspace_id=workspace_id,
        server_id=server_id,
        label=current.get("label"),
        transport=current.get("transport"),
        endpoint=current.get("endpoint"),
        enabled=current.get("enabled", True),
        tools=current.get("tools"),
        metadata=current.get("metadata"),
        discover_tools=True,
    )


def delete_workspace_mcp_server(*, workspace_id: str, server_id: str) -> Dict[str, Any]:
    normalized_workspace_id = str(workspace_id or "").strip()
    normalized_server_id = _normalize_server_id(server_id)
    registry = load_mcp_server_registry()
    bucket = _workspace_bucket(normalized_workspace_id, registry)
    servers = bucket.get("servers") if isinstance(bucket.get("servers"), dict) else {}
    if normalized_server_id not in servers:
        raise FileNotFoundError(f"MCP server '{server_id}' is not registered for this workspace.")
    removed = dict(servers.pop(normalized_server_id))
    bucket["servers"] = servers
    _save_workspace_bucket(normalized_workspace_id, registry, bucket)
    save_mcp_server_registry(registry)
    return removed


def mcp_skill_id(server_id: str, tool_name: str) -> str:
    return f"mcp:{_normalize_server_id(server_id)}:{_normalize_tool_name(tool_name)}"


# ── Phase A wiring: live tool-calling loop integration ────────────────────────
# docs/design/mcp-applications-plan.md (Phase A) / docs/design/mcp-current-state.md.
# Everything below this banner (mcp_tools_enabled, mcp_tool_name /
# parse_mcp_tool_name, list_workspace_mcp_direct_tool_payloads,
# invoke_workspace_mcp_tool{,_async}, format_mcp_tool_result) exists to wire
# this already-built MCP client engine into the live, structured
# tool-calling loop so a model can discover and call a workspace's connected
# MCP tools via ordinary function-calling — as opposed to mcp_skill_id() /
# invoke_workspace_mcp_skill{,_async}() above, which is the older
# natural-language "goal" based skill-invocation abstraction (still used by
# the /skills mcp:server:tool slash command and skill_registry.py) and is
# left untouched.


def mcp_tools_enabled() -> bool:
    """Kill switch for MCP tool discovery + dispatch in the live tool-calling
    loop. Default ON. Mirrors the EMPYRALIS_CONTINUOUS_WORK_ENABLED pattern in
    direct_chat_generation_service._continuous_work_enabled(). Flag OFF means
    zero behavior change: no MCP registry entries are built and no MCP
    dispatch branch fires anywhere in the tool-calling loop.
    """
    return str(os.environ.get("EMPYRALIS_MCP_TOOLS_ENABLED", "1")).strip().lower() in {
        "1", "true", "yes", "on",
    }


def mcp_tool_name(server_id: str, tool_name: str) -> str:
    """Model-facing namespaced tool name for direct structured tool-calling.

    Distinct from mcp_skill_id() (colon-delimited, e.g. "mcp:server:tool")
    which addresses the goal-based skill abstraction — this is
    double-underscore delimited ("mcp__server__tool") to match every other
    connector's "<connector_id>__<action_id>" naming convention, so
    direct_chat_operator_binding_service.parse_tool_name() — which splits on
    the FIRST "__" only — parses it for free into
    connector_id="mcp", action_id="<server_id>__<tool_name>".
    """
    return f"mcp__{_normalize_server_id(server_id)}__{_normalize_tool_name(tool_name)}"


def parse_mcp_tool_name(tool_name: str) -> Optional[Dict[str, str]]:
    """Reverse of mcp_tool_name(): "mcp__<server_id>__<tool_name>" -> dict.

    Splits on the FIRST "__" after the "mcp__" prefix. A server_id
    containing "__" would break this — server ids are short admin-chosen
    slugs (see _normalize_server_id), so this is an accepted edge case, not
    a general-purpose parser.
    """
    raw = str(tool_name or "").strip()
    if not raw.startswith("mcp__"):
        return None
    remainder = raw[len("mcp__"):]
    if "__" not in remainder:
        return None
    server_id, tool = remainder.split("__", 1)
    normalized_server_id = _normalize_server_id(server_id)
    normalized_tool_name = _normalize_tool_name(tool)
    if not normalized_server_id or not normalized_tool_name:
        return None
    return {"server_id": normalized_server_id, "tool_name": normalized_tool_name}


def list_workspace_mcp_direct_tool_payloads(workspace_id: str) -> List[Dict[str, Any]]:
    """Build Tier-2 tool-registry payloads for a workspace's connected MCP
    tools.

    Consumed by server_modules/tool_registry_service.build_registry_entries()
    as its 4th source (see the call site in agent_turn_runtime_service.py's
    _direct_tool_bundle(), which injects this list into the availability
    payload under the "mcp_tools" key rather than adding a new parameter to
    build_registry_entries() — see the comment there for why). Each payload
    is already shaped like tool_registry_service._build_registry_entry_from_
    tool_payload() expects: name/description/connector_id/parameters.

    Only ENABLED servers and ENABLED+APPROVED tools are included — an agent
    can never discover (let alone call) an MCP tool that hasn't been
    explicitly approved for this workspace. The model-facing "name" is
    mcp_tool_name(server_id, tool_name); the server label and tool label are
    folded into "description" so tool_registry_service's keyword extractor
    (which only reads name/description/connector_id) picks them up too.
    """
    if not mcp_tools_enabled():
        return []
    payloads: List[Dict[str, Any]] = []
    for server in list_workspace_mcp_servers(workspace_id):
        if not bool(server.get("enabled", True)):
            continue
        server_id = str(server.get("id") or "").strip()
        if not server_id:
            continue
        server_label = str(server.get("label") or server_id).strip() or server_id
        raw_tools = server.get("tools") if isinstance(server.get("tools"), list) else []
        for raw_tool in raw_tools:
            if not isinstance(raw_tool, dict):
                continue
            if not bool(raw_tool.get("enabled", True)) or not bool(raw_tool.get("approved", False)):
                continue
            tool_name = _normalize_tool_name(raw_tool.get("name"))
            if not tool_name:
                continue
            tool_label = str(raw_tool.get("label") or tool_name).strip() or tool_name
            tool_description = str(raw_tool.get("description") or "").strip()
            combined_description = (
                f"[{server_label}] {tool_label}: {tool_description}"
                if tool_description
                else f"[{server_label}] {tool_label}"
            ).strip()
            payloads.append(
                {
                    "name": mcp_tool_name(server_id, tool_name),
                    "description": combined_description[:500],
                    "label": f"{server_label}: {tool_label}"[:160],
                    "connector_id": "mcp",
                    "parameters": _normalize_input_schema(raw_tool.get("input_schema")),
                    # Round-trip fields — not read by tool_registry_service,
                    # only by our own callers/tests.
                    "mcp_server_id": server_id,
                    "mcp_tool_name": tool_name,
                }
            )
    return payloads


def build_mcp_trace_detail(
    *,
    server_id: str,
    tool_name: str,
    server_label: Optional[str] = None,
    error: Any = None,
    error_code: Optional[str] = None,
) -> Dict[str, Any]:
    """The MCP-specific block attached to a `tool.result` trace event.

    MAN-125 item 3: invoke_workspace_mcp_tool{,_async} record the real error
    code and message via agent_action_metering_service.record_failed — into
    the METERING LEDGER, which is billing. The trace stream the UI reads got
    nothing but a generic "connector_action_failed", so the platform knew
    exactly why an MCP call failed and showed the user none of it. This is the
    same facts, shaped for the trace.

    Every value is run through secret_redaction_service the same way
    `args_preview` already is (sanitize_mapping -> redact_text on each string,
    sensitive keys blanked) — third-party MCP servers put bearer tokens,
    cookies and user data in their error strings, and this block is rendered
    to the user and persisted to agent_trace_events.
    """
    from server_modules import secret_redaction_service

    resolved_code = str(error_code or "").strip()
    if not resolved_code and isinstance(error, BaseException):
        resolved_code = type(error).__name__
    detail: Dict[str, Any] = {
        "server_id": _normalize_server_id(server_id),
        "tool_name": _normalize_tool_name(tool_name),
    }
    label = str(server_label or "").strip()
    if label:
        detail["server_label"] = label
    if resolved_code:
        detail["error_code"] = resolved_code
    message = str(error or "").strip()
    if message:
        detail["error"] = message[:2000]
    return secret_redaction_service.sanitize_mapping(detail)


def annotate_mcp_tool_error(
    exc: BaseException,
    *,
    server_id: str,
    tool_name: str,
    server_label: Optional[str] = None,
) -> BaseException:
    """Stamp an in-flight MCP failure with the detail the trace layer needs,
    then hand the exception back for the caller to re-raise.

    Attribute rather than a new exception subclass on purpose: every existing
    `except Exception` handler along the direct-chat tool path already does
    the right thing with the message, and a new type would have changed which
    handlers catch what. Readers use `getattr(exc, "mcp_detail", None)`.
    """
    try:
        exc.mcp_detail = build_mcp_trace_detail(  # type: ignore[attr-defined]
            server_id=server_id,
            tool_name=tool_name,
            server_label=server_label,
            error=exc,
        )
    except Exception:  # pragma: no cover - annotation must never mask the real error
        pass
    return exc


def format_mcp_tool_result(result: Any) -> str:
    """Format an invoke_workspace_mcp_tool{,_async}() return dict as the same
    plain tool-result string shape every other direct-chat tool call
    returns (skills_service.execute_single_direct_tool_call{,_async}
    branches, direct_chat_operator_binding_service.execute_single_direct_
    tool_call's custom-connector branch, etc. all return `str`).

    When the server flagged the call as failed (CallToolResult.isError, see
    mcp_result_is_error) the envelope carries an explicit `"ok": false` and
    `"error"`, so tool_result_status classifies it off a stated flag instead
    of inferring anything from the reply text.
    """
    if not isinstance(result, dict):
        return str(result or "").strip()
    reply = str(result.get("reply") or "").strip()
    mcp_block = result.get("mcp") if isinstance(result.get("mcp"), dict) else {}
    payload = mcp_block.get("payload")
    is_error = bool(result.get("is_error")) or str(result.get("status") or "").strip().lower() == "error"
    if payload not in (None, {}, []):
        envelope: Dict[str, Any] = {"reply": reply, "result": payload}
        if is_error:
            # First key in the dump so it survives any downstream truncation.
            envelope = {"ok": False, "error": reply or "The MCP tool reported an error.", **envelope}
        try:
            return json.dumps(envelope, ensure_ascii=False, indent=2)[:8000]
        except Exception:
            pass
    if is_error:
        try:
            return json.dumps(
                {"ok": False, "error": reply or "The MCP tool reported an error."},
                ensure_ascii=False,
            )
        except Exception:
            pass
    return reply or "MCP tool executed successfully."


async def invoke_workspace_mcp_tool_async(
    *,
    workspace_id: str,
    server_id: str,
    tool_name: str,
    arguments: Dict[str, Any],
    agent_label: str = "Agent",
    tenant_id: str = "default",
    surface: str = "sage",
    source_surface: str = "mcp_tool_call",
    user_id: Optional[str] = None,
    thread_id: Optional[str] = None,
    run_id: Optional[str] = None,
    agent_id: Optional[str] = None,
    app_id: Optional[str] = None,
    client_session_cls: Any = ClientSession,
    streamable_http_client_fn: Any = streamable_http_client,
) -> Dict[str, Any]:
    """Invoke an MCP tool with structured JSON *arguments* supplied directly
    by the calling model — the entrypoint used by the direct tool-calling
    loop when the model calls an mcp__<server>__<tool> function, as opposed
    to invoke_workspace_mcp_skill_async() above which accepts a
    natural-language *goal* string and infers arguments from it via
    _parse_goal_arguments(). Mirrors invoke_workspace_mcp_skill_async()'s
    structure exactly, minus the goal-parsing step.
    """
    normalized_server_id = _normalize_server_id(server_id)
    normalized_tool_name = _normalize_tool_name(tool_name)
    skill_id = mcp_skill_id(normalized_server_id, normalized_tool_name)
    server = get_workspace_mcp_server(workspace_id, normalized_server_id)
    if server is None:
        raise FileNotFoundError(f"MCP server '{normalized_server_id}' is not registered for this workspace.")
    if not bool(server.get("enabled", True)):
        raise RuntimeError(f"MCP server '{normalized_server_id}' is disabled for this workspace.")
    tool_payload = _tool_from_server(server, normalized_tool_name)
    if tool_payload is None or not bool(tool_payload.get("enabled", True)):
        raise FileNotFoundError(f"MCP tool '{normalized_tool_name}' is not available on server '{normalized_server_id}'.")
    execution_call_id = run_id or thread_id or f"mcp_{uuid.uuid4().hex}"
    input_arguments = arguments if isinstance(arguments, dict) else {}
    input_summary = json.dumps(input_arguments, ensure_ascii=False)[:2000] if input_arguments else ""
    source_event_id = agent_action_metering_service.build_source_event_id(
        source_surface=source_surface,
        run_id=run_id,
        thread_id=thread_id,
        tool_call_id=execution_call_id,
        action_name=normalized_tool_name,
    )
    try:
        _assert_tool_approved_for_execution(
            tool_payload,
            server_id=normalized_server_id,
            tool_name=normalized_tool_name,
        )
    except Exception as exc:
        await agent_action_metering_service.record_blocked(
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            surface=surface,
            source_surface=source_surface,
            action_domain="mcp",
            action_type=_normalize_action_class(tool_payload.get("action_class")),
            action_name=normalized_tool_name,
            tool_kind="mcp_tool",
            mcp_server_id=normalized_server_id,
            mcp_tool_id=normalized_tool_name,
            connector_id=f"mcp:{normalized_server_id}",
            skill_id=skill_id,
            risk_level=str(tool_payload.get("risk_level") or "").strip() or None,
            policy_decision="blocked",
            error_code=type(exc).__name__,
            payer="platform_credits",
            billing_mode="none",
            user_id=user_id,
            thread_id=thread_id,
            run_id=run_id,
            agent_id=agent_id,
            app_id=app_id,
            input_summary=input_summary,
            output_summary=str(exc),
            source_table="mcp_tool_calls",
            source_event_id=source_event_id,
        )
        raise
    validated_arguments = _validate_mcp_arguments(
        input_arguments,
        tool_payload.get("input_schema"),
        tool_name=normalized_tool_name,
    )
    credential = _resolve_mcp_credential(server, workspace_id)
    mcp_http_client = _build_mcp_http_client(credential)
    common_event = {
        "tenant_id": tenant_id,
        "workspace_id": workspace_id,
        "surface": surface,
        "source_surface": source_surface,
        "action_domain": "mcp",
        "action_type": _normalize_action_class(tool_payload.get("action_class")),
        "action_name": normalized_tool_name,
        "tool_kind": "mcp_tool",
        "mcp_server_id": normalized_server_id,
        "mcp_tool_id": normalized_tool_name,
        "connector_id": f"mcp:{normalized_server_id}",
        "skill_id": skill_id,
        "risk_level": str(tool_payload.get("risk_level") or "").strip() or None,
        "policy_decision": "allowed",
        "payer": "platform_credits",
        "billing_mode": "transparency",
        "user_id": user_id,
        "thread_id": thread_id,
        "run_id": run_id,
        "agent_id": agent_id,
        "app_id": app_id,
        "input_summary": input_summary,
        "source_table": "mcp_tool_calls",
        "source_event_id": source_event_id,
        "metadata": {"server_label": server.get("label"), "argument_keys": sorted(validated_arguments.keys())},
    }
    await agent_action_metering_service.record_started(**common_event)
    try:
        result = await _invoke_mcp_tool_with_auth_recovery_async(
            server=server,
            workspace_id=workspace_id,
            tenant_id=tenant_id,
            tool_name=normalized_tool_name,
            arguments=validated_arguments,
            http_client=mcp_http_client,
            client_session_cls=client_session_cls,
            streamable_http_client_fn=streamable_http_client_fn,
            run_id=run_id,
            thread_id=thread_id,
            agent_id=agent_id,
        )
    except Exception as exc:
        await agent_action_metering_service.record_failed(
            **common_event,
            error_code=type(exc).__name__,
            output_summary=str(exc),
        )
        # Same facts the ledger line above just captured, stamped onto the
        # exception so the trace stream the USER reads can carry them too.
        raise annotate_mcp_tool_error(
            exc,
            server_id=normalized_server_id,
            tool_name=normalized_tool_name,
            server_label=str(server.get("label") or ""),
        )
    payload = _mcp_result_payload(result)
    is_error = mcp_result_is_error(result)
    reply_text = _mcp_reply(payload, agent_label=agent_label, tool_name=normalized_tool_name)
    if is_error:
        # A tool that ran and failed — no exception to catch, just the
        # protocol's isError flag. Bill it as the failure it is, and hand the
        # caller a result that says so.
        await agent_action_metering_service.record_failed(
            **common_event,
            error_code="mcp_tool_error",
            output_summary=reply_text,
        )
    else:
        await agent_action_metering_service.record_completed(
            **common_event,
            output_summary=reply_text,
        )
    return {
        "status": "error" if is_error else "ok",
        "is_error": is_error,
        "mcp_detail": build_mcp_trace_detail(
            server_id=normalized_server_id,
            tool_name=normalized_tool_name,
            server_label=str(server.get("label") or ""),
            error=reply_text if is_error else None,
            error_code="mcp_tool_error" if is_error else None,
        ),
        "reply": _mcp_reply(payload, agent_label=agent_label, tool_name=normalized_tool_name),
        "artifact": {
            "label": str(tool_payload.get("label") or normalized_tool_name).strip() or normalized_tool_name,
            "kind": _tool_kind(tool_payload),
            "summary": f"MCP tool {normalized_tool_name} on server {server.get('label') or normalized_server_id}",
            "media_type": "application/json",
            "preview_content": json.dumps(
                {
                    "server_id": normalized_server_id,
                    "tool_name": normalized_tool_name,
                    "arguments": validated_arguments,
                    "payload": payload,
                },
                ensure_ascii=False,
                indent=2,
            )[:12000],
        },
        "steps": [
            {"label": "Resolving MCP server", "detail": str(server.get("label") or normalized_server_id).strip() or normalized_server_id, "status": "done", "kind": "thinking"},
            {"label": "Invoking MCP tool", "detail": normalized_tool_name, "status": "done", "kind": "connector"},
        ],
        "mcp": {
            "server_id": normalized_server_id,
            "tool_name": normalized_tool_name,
            "arguments": validated_arguments,
            "payload": payload,
        },
    }


def invoke_workspace_mcp_tool(
    *,
    workspace_id: str,
    server_id: str,
    tool_name: str,
    arguments: Dict[str, Any],
    agent_label: str = "Agent",
    tenant_id: str = "default",
    surface: str = "sage",
    source_surface: str = "mcp_tool_call",
    user_id: Optional[str] = None,
    thread_id: Optional[str] = None,
    run_id: Optional[str] = None,
    agent_id: Optional[str] = None,
    app_id: Optional[str] = None,
    client_session_cls: Any = ClientSession,
    streamable_http_client_fn: Any = streamable_http_client,
) -> Dict[str, Any]:
    """Sync twin of invoke_workspace_mcp_tool_async() — for dispatch call
    sites that run inside a plain worker thread with no asyncio event loop of
    their own (e.g. direct_chat_operator_binding_service.execute_single_
    direct_tool_call, which is invoked via a bare ThreadPoolExecutor, the
    same shape invoke_workspace_mcp_skill() (sync) already mirrors
    invoke_workspace_mcp_skill_async()).
    """
    normalized_server_id = _normalize_server_id(server_id)
    normalized_tool_name = _normalize_tool_name(tool_name)
    skill_id = mcp_skill_id(normalized_server_id, normalized_tool_name)
    server = get_workspace_mcp_server(workspace_id, normalized_server_id)
    if server is None:
        raise FileNotFoundError(f"MCP server '{normalized_server_id}' is not registered for this workspace.")
    if not bool(server.get("enabled", True)):
        raise RuntimeError(f"MCP server '{normalized_server_id}' is disabled for this workspace.")
    tool_payload = _tool_from_server(server, normalized_tool_name)
    if tool_payload is None or not bool(tool_payload.get("enabled", True)):
        raise FileNotFoundError(f"MCP tool '{normalized_tool_name}' is not available on server '{normalized_server_id}'.")
    execution_call_id = run_id or thread_id or f"mcp_{uuid.uuid4().hex}"
    input_arguments = arguments if isinstance(arguments, dict) else {}
    input_summary = json.dumps(input_arguments, ensure_ascii=False)[:2000] if input_arguments else ""
    source_event_id = agent_action_metering_service.build_source_event_id(
        source_surface=source_surface,
        run_id=run_id,
        thread_id=thread_id,
        tool_call_id=execution_call_id,
        action_name=normalized_tool_name,
    )
    try:
        _assert_tool_approved_for_execution(
            tool_payload,
            server_id=normalized_server_id,
            tool_name=normalized_tool_name,
        )
    except Exception as exc:
        agent_action_metering_service.record_blocked_sync(
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            surface=surface,
            source_surface=source_surface,
            action_domain="mcp",
            action_type=_normalize_action_class(tool_payload.get("action_class")),
            action_name=normalized_tool_name,
            tool_kind="mcp_tool",
            mcp_server_id=normalized_server_id,
            mcp_tool_id=normalized_tool_name,
            connector_id=f"mcp:{normalized_server_id}",
            skill_id=skill_id,
            risk_level=str(tool_payload.get("risk_level") or "").strip() or None,
            policy_decision="blocked",
            error_code=type(exc).__name__,
            payer="platform_credits",
            billing_mode="none",
            user_id=user_id,
            thread_id=thread_id,
            run_id=run_id,
            agent_id=agent_id,
            app_id=app_id,
            input_summary=input_summary,
            output_summary=str(exc),
            source_table="mcp_tool_calls",
            source_event_id=source_event_id,
        )
        raise
    validated_arguments = _validate_mcp_arguments(
        input_arguments,
        tool_payload.get("input_schema"),
        tool_name=normalized_tool_name,
    )
    credential = _resolve_mcp_credential(server, workspace_id)
    mcp_http_client = _build_mcp_http_client(credential)
    common_event = {
        "tenant_id": tenant_id,
        "workspace_id": workspace_id,
        "surface": surface,
        "source_surface": source_surface,
        "action_domain": "mcp",
        "action_type": _normalize_action_class(tool_payload.get("action_class")),
        "action_name": normalized_tool_name,
        "tool_kind": "mcp_tool",
        "mcp_server_id": normalized_server_id,
        "mcp_tool_id": normalized_tool_name,
        "connector_id": f"mcp:{normalized_server_id}",
        "skill_id": skill_id,
        "risk_level": str(tool_payload.get("risk_level") or "").strip() or None,
        "policy_decision": "allowed",
        "payer": "platform_credits",
        "billing_mode": "transparency",
        "user_id": user_id,
        "thread_id": thread_id,
        "run_id": run_id,
        "agent_id": agent_id,
        "app_id": app_id,
        "input_summary": input_summary,
        "source_table": "mcp_tool_calls",
        "source_event_id": source_event_id,
        "metadata": {"server_label": server.get("label"), "argument_keys": sorted(validated_arguments.keys())},
    }
    agent_action_metering_service.record_started_sync(**common_event)
    try:
        result = asyncio.run(
            _invoke_mcp_tool_with_auth_recovery_async(
                server=server,
                workspace_id=workspace_id,
                tenant_id=tenant_id,
                tool_name=normalized_tool_name,
                arguments=validated_arguments,
                http_client=mcp_http_client,
                client_session_cls=client_session_cls,
                streamable_http_client_fn=streamable_http_client_fn,
                run_id=run_id,
                thread_id=thread_id,
                agent_id=agent_id,
            )
        )
    except Exception as exc:
        agent_action_metering_service.record_failed_sync(
            **common_event,
            error_code=type(exc).__name__,
            output_summary=str(exc),
        )
        # See the matching branch in invoke_workspace_mcp_tool_async.
        raise annotate_mcp_tool_error(
            exc,
            server_id=normalized_server_id,
            tool_name=normalized_tool_name,
            server_label=str(server.get("label") or ""),
        )
    payload = _mcp_result_payload(result)
    is_error = mcp_result_is_error(result)
    reply_text = _mcp_reply(payload, agent_label=agent_label, tool_name=normalized_tool_name)
    if is_error:
        agent_action_metering_service.record_failed_sync(
            **common_event,
            error_code="mcp_tool_error",
            output_summary=reply_text,
        )
    else:
        agent_action_metering_service.record_completed_sync(
            **common_event,
            output_summary=reply_text,
        )
    return {
        "status": "error" if is_error else "ok",
        "is_error": is_error,
        "mcp_detail": build_mcp_trace_detail(
            server_id=normalized_server_id,
            tool_name=normalized_tool_name,
            server_label=str(server.get("label") or ""),
            error=reply_text if is_error else None,
            error_code="mcp_tool_error" if is_error else None,
        ),
        "reply": _mcp_reply(payload, agent_label=agent_label, tool_name=normalized_tool_name),
        "artifact": {
            "label": str(tool_payload.get("label") or normalized_tool_name).strip() or normalized_tool_name,
            "kind": _tool_kind(tool_payload),
            "summary": f"MCP tool {normalized_tool_name} on server {server.get('label') or normalized_server_id}",
            "media_type": "application/json",
            "preview_content": json.dumps(
                {
                    "server_id": normalized_server_id,
                    "tool_name": normalized_tool_name,
                    "arguments": validated_arguments,
                    "payload": payload,
                },
                ensure_ascii=False,
                indent=2,
            )[:12000],
        },
        "steps": [
            {"label": "Resolving MCP server", "detail": str(server.get("label") or normalized_server_id).strip() or normalized_server_id, "status": "done", "kind": "thinking"},
            {"label": "Invoking MCP tool", "detail": normalized_tool_name, "status": "done", "kind": "connector"},
        ],
        "mcp": {
            "server_id": normalized_server_id,
            "tool_name": normalized_tool_name,
            "arguments": validated_arguments,
            "payload": payload,
        },
    }


def parse_mcp_skill_id(skill_id: str) -> Optional[Dict[str, str]]:
    raw = str(skill_id or "").strip()
    if not raw.startswith("mcp:"):
        return None
    _prefix, server_id, tool_name = raw.split(":", 2) if raw.count(":") >= 2 else ("", "", "")
    normalized_server_id = _normalize_server_id(server_id)
    normalized_tool_name = _normalize_tool_name(tool_name)
    if not normalized_server_id or not normalized_tool_name:
        return None
    return {"server_id": normalized_server_id, "tool_name": normalized_tool_name}


def list_workspace_mcp_skill_entries(workspace_id: str) -> List[Dict[str, Any]]:
    entries: List[Dict[str, Any]] = []
    for server in list_workspace_mcp_servers(workspace_id):
        if not bool(server.get("enabled", True)):
            continue
        server_id = str(server.get("id") or "").strip()
        for raw_tool in server.get("tools") if isinstance(server.get("tools"), list) else []:
            if not isinstance(raw_tool, dict) or not bool(raw_tool.get("enabled", True)):
                continue
            if not bool(raw_tool.get("approved", True)):
                continue
            tool_name = _normalize_tool_name(raw_tool.get("name"))
            if not tool_name:
                continue
            entries.append(
                {
                    "id": mcp_skill_id(server_id, tool_name),
                    "server_id": server_id,
                    "tool_name": tool_name,
                    "label": str(raw_tool.get("label") or tool_name).strip() or tool_name,
                    "description": str(raw_tool.get("description") or "").strip(),
                    "skill_class": "specialist_local",
                    "permission_label": f"MCP server {server.get('label') or server_id}",
                    "execution_mode": "live",
                    "action_class": _normalize_action_class(raw_tool.get("action_class")),
                    "connector_scopes": _normalize_list_of_strings(raw_tool.get("connector_scopes")) or ["mcp", f"mcp:{server_id}"],
                    "trigger_terms": [token.lower() for token in _normalize_list_of_strings(raw_tool.get("trigger_terms"))],
                    "allowed_runtime_modes": _normalize_runtime_modes(raw_tool.get("allowed_runtime_modes")),
                    "requires_approval": bool(raw_tool.get("requires_approval")),
                    "execution_adapter": "mcp_tool",
                    "source": "mcp_registry",
                    "path": str(server.get("endpoint") or "").strip(),
                    "enabled": True,
                    "metadata": {
                        "server_label": str(server.get("label") or server_id).strip() or server_id,
                        "transport": str(server.get("transport") or "streamable_http").strip() or "streamable_http",
                        "endpoint": str(server.get("endpoint") or "").strip(),
                        "input_schema": _normalize_input_schema(raw_tool.get("input_schema")),
                    },
                }
            )
    return entries


def _tool_from_server(server: Dict[str, Any], tool_name: str) -> Optional[Dict[str, Any]]:
    for raw_tool in server.get("tools") if isinstance(server.get("tools"), list) else []:
        if not isinstance(raw_tool, dict):
            continue
        if _normalize_tool_name(raw_tool.get("name")) == tool_name:
            return dict(raw_tool)
    return None


def _assert_tool_approved_for_execution(tool_payload: Dict[str, Any], *, server_id: str, tool_name: str) -> None:
    if bool(tool_payload.get("approved", False)):
        return
    raise PermissionError(f"MCP tool '{tool_name}' on server '{server_id}' is not approved for execution.")


def _parse_goal_arguments(goal: str, tool_payload: Dict[str, Any]) -> Dict[str, Any]:
    compact = str(goal or "").strip()
    if not compact:
        return {}
    try:
        parsed = json.loads(compact)
        if isinstance(parsed, dict):
            for key in _ARGUMENT_KEY_CANDIDATES:
                candidate = parsed.get(key)
                if isinstance(candidate, dict):
                    return dict(candidate)
            return parsed
    except Exception:
        pass
    schema = _normalize_input_schema(tool_payload.get("input_schema"))
    properties = schema.get("properties") if isinstance(schema.get("properties"), dict) else {}
    required = [
        str(item).strip()
        for item in (schema.get("required") if isinstance(schema.get("required"), list) else [])
        if str(item).strip()
    ]
    candidate_keys = required or [str(key).strip() for key in properties.keys() if str(key).strip()]
    for key in candidate_keys:
        if key in {"question", "query", "goal", "input", "text", "prompt", "message", "url"}:
            return {key: compact}
    if len(candidate_keys) == 1:
        return {candidate_keys[0]: compact}
    if not candidate_keys:
        return {}
    return {"input": compact}


_MAX_ARGUMENT_STRING_LENGTH = 10_000


def _validate_mcp_arguments(
    arguments: Dict[str, Any],
    input_schema: Dict[str, Any],
    *,
    tool_name: str = "",
) -> Dict[str, Any]:
    """Validate and sanitize arguments against the tool's input_schema.

    Returns a cleaned arguments dict.  When *input_schema* is empty or
    missing, the original arguments are returned unchanged.
    """
    if not isinstance(arguments, dict) or not arguments:
        return dict(arguments) if isinstance(arguments, dict) else {}

    schema = _normalize_input_schema(input_schema)
    if not schema:
        return dict(arguments)

    properties: Dict[str, Any] = (
        schema.get("properties") if isinstance(schema.get("properties"), dict) else {}
    )
    required: List[str] = [
        str(item).strip()
        for item in (schema.get("required") if isinstance(schema.get("required"), list) else [])
        if str(item).strip()
    ]

    cleaned: Dict[str, Any] = {}

    # 1. Strip unknown keys
    for key, value in arguments.items():
        key_str = str(key).strip()
        if properties and key_str not in properties:
            _log.warning(
                "MCP argument validation: stripping unknown key '%s' for tool '%s'",
                key_str,
                tool_name or "(unknown)",
            )
            continue
        cleaned[key_str] = value

    # 2. Check required keys are present
    for req_key in required:
        if req_key not in cleaned:
            raise RuntimeError(
                f"Missing required parameter: {req_key}"
                + (f" for tool {tool_name}" if tool_name else "")
            )

    # 3. Type-check and convert basic types
    for key, value in list(cleaned.items()):
        prop_schema = properties.get(key)
        if not isinstance(prop_schema, dict):
            continue
        expected_type = str(prop_schema.get("type") or "").strip().lower()
        if not expected_type:
            continue

        if expected_type == "string":
            if not isinstance(value, str):
                cleaned[key] = str(value)
            # 4. Cap string length
            if isinstance(cleaned[key], str) and len(cleaned[key]) > _MAX_ARGUMENT_STRING_LENGTH:
                _log.warning(
                    "MCP argument validation: truncating string argument '%s' (%d chars) for tool '%s'",
                    key,
                    len(cleaned[key]),
                    tool_name or "(unknown)",
                )
                cleaned[key] = cleaned[key][:_MAX_ARGUMENT_STRING_LENGTH]

        elif expected_type in ("integer", "number"):
            if isinstance(value, bool):
                cleaned[key] = int(value)
            elif isinstance(value, str):
                stripped = value.strip()
                try:
                    if expected_type == "integer":
                        cleaned[key] = int(stripped)
                    else:
                        cleaned[key] = float(stripped)
                except (ValueError, TypeError):
                    pass  # keep original string if conversion fails
            elif isinstance(value, (int, float)):
                if expected_type == "integer":
                    cleaned[key] = int(value)
                # else keep as-is (already a number)

    return cleaned


def _mcp_reply(payload: Any, *, agent_label: str, tool_name: str) -> str:
    if isinstance(payload, dict):
        for key in ("reply", "response", "answer", "text", "message", "summary"):
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        return f"{agent_label} called MCP tool {tool_name} successfully."
    if isinstance(payload, list):
        return json.dumps(payload, ensure_ascii=False)[:4000]
    if isinstance(payload, str) and payload.strip():
        return payload.strip()
    return f"{agent_label} called MCP tool {tool_name} successfully."


def _tool_kind(tool_payload: Dict[str, Any]) -> str:
    tool_name = str(tool_payload.get("name") or "").lower()
    action_class = _normalize_action_class(tool_payload.get("action_class"))
    if action_class != "read" or any(token in tool_name for token in _DANGEROUS_TOKENS):
        return "mcp-tool-result"
    return "mcp-live-data"


async def _call_streamable_http_tool_async(
    *,
    endpoint: str,
    tool_name: str,
    arguments: Dict[str, Any],
    http_client: Any = None,
    client_session_cls: Any = ClientSession,
    streamable_http_client_fn: Any = streamable_http_client,
    call_timeout_seconds: float = 60.0,
) -> Any:
    if client_session_cls is None or streamable_http_client_fn is None:
        raise RuntimeError("The MCP client dependency is not installed.")
    try:
        max_attempts = 3
        base_delay = 1.0
        for attempt in range(1, max_attempts + 1):
            try:
                async with asyncio.timeout(call_timeout_seconds):
                    async with streamable_http_client_fn(endpoint, http_client=http_client) as (read_stream, write_stream, _):
                        async with client_session_cls(read_stream, write_stream) as session:
                            await session.initialize()
                            return await session.call_tool(tool_name, arguments)
            except TimeoutError:
                if attempt < max_attempts:
                    _log.warning(
                        "MCP tool call %s attempt %d/%d timed out after %.0fs, retrying...",
                        tool_name, attempt, max_attempts, call_timeout_seconds,
                    )
                    await asyncio.sleep(base_delay * attempt)
                    continue
                raise RuntimeError(
                    f"MCP tool call timed out after {max_attempts} attempts "
                    f"({call_timeout_seconds:.0f}s each): {tool_name} on {endpoint}"
                ) from None
            except Exception as exc:
                if attempt < max_attempts and _is_transient_mcp_error(exc):
                    _log.warning(
                        "MCP tool call %s attempt %d/%d failed: %s, retrying...",
                        tool_name, attempt, max_attempts, exc,
                    )
                    await asyncio.sleep(base_delay * attempt)
                    continue
                raise
    finally:
        await _maybe_close_mcp_http_client(http_client)


def invoke_workspace_mcp_skill(
    *,
    workspace_id: str,
    skill_id: str,
    goal: str,
    agent_label: str,
    tenant_id: str = "default",
    surface: str = "sage",
    source_surface: str = "mcp_skill",
    user_id: Optional[str] = None,
    thread_id: Optional[str] = None,
    run_id: Optional[str] = None,
    agent_id: Optional[str] = None,
    app_id: Optional[str] = None,
    client_session_cls: Any = ClientSession,
    streamable_http_client_fn: Any = streamable_http_client,
) -> Dict[str, Any]:
    parsed = parse_mcp_skill_id(skill_id)
    if parsed is None:
        raise RuntimeError("Invalid MCP skill id.")
    server = get_workspace_mcp_server(workspace_id, parsed["server_id"])
    if server is None:
        raise FileNotFoundError(f"MCP server '{parsed['server_id']}' is not registered for this workspace.")
    if not bool(server.get("enabled", True)):
        raise RuntimeError(f"MCP server '{parsed['server_id']}' is disabled for this workspace.")
    tool_payload = _tool_from_server(server, parsed["tool_name"])
    if tool_payload is None or not bool(tool_payload.get("enabled", True)):
        raise FileNotFoundError(f"MCP tool '{parsed['tool_name']}' is not available on server '{parsed['server_id']}'.")
    execution_call_id = run_id or thread_id or f"mcp_{uuid.uuid4().hex}"
    source_event_id = agent_action_metering_service.build_source_event_id(
        source_surface=source_surface,
        run_id=run_id,
        thread_id=thread_id,
        tool_call_id=execution_call_id,
        action_name=parsed["tool_name"],
    )
    try:
        _assert_tool_approved_for_execution(
            tool_payload,
            server_id=parsed["server_id"],
            tool_name=parsed["tool_name"],
        )
    except Exception as exc:
        agent_action_metering_service.record_blocked_sync(
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            surface=surface,
            source_surface=source_surface,
            action_domain="mcp",
            action_type=_normalize_action_class(tool_payload.get("action_class")),
            action_name=parsed["tool_name"],
            tool_kind="mcp_tool",
            mcp_server_id=parsed["server_id"],
            mcp_tool_id=parsed["tool_name"],
            connector_id=f"mcp:{parsed['server_id']}",
            skill_id=skill_id,
            risk_level=str(tool_payload.get("risk_level") or "").strip() or None,
            policy_decision="blocked",
            error_code=type(exc).__name__,
            payer="platform_credits",
            billing_mode="none",
            user_id=user_id,
            thread_id=thread_id,
            run_id=run_id,
            agent_id=agent_id,
            app_id=app_id,
            input_summary=goal,
            output_summary=str(exc),
            source_table="mcp_tool_calls",
            source_event_id=source_event_id,
        )
        raise
    arguments = _parse_goal_arguments(goal, tool_payload)
    arguments = _validate_mcp_arguments(
        arguments,
        tool_payload.get("input_schema"),
        tool_name=parsed["tool_name"],
    )
    credential = _resolve_mcp_credential(server, workspace_id)
    mcp_http_client = _build_mcp_http_client(credential)
    common_event = {
        "tenant_id": tenant_id,
        "workspace_id": workspace_id,
        "surface": surface,
        "source_surface": source_surface,
        "action_domain": "mcp",
        "action_type": _normalize_action_class(tool_payload.get("action_class")),
        "action_name": parsed["tool_name"],
        "tool_kind": "mcp_tool",
        "mcp_server_id": parsed["server_id"],
        "mcp_tool_id": parsed["tool_name"],
        "connector_id": f"mcp:{parsed['server_id']}",
        "skill_id": skill_id,
        "risk_level": str(tool_payload.get("risk_level") or "").strip() or None,
        "policy_decision": "allowed",
        "payer": "platform_credits",
        "billing_mode": "transparency",
        "user_id": user_id,
        "thread_id": thread_id,
        "run_id": run_id,
        "agent_id": agent_id,
        "app_id": app_id,
        "input_summary": goal,
        "source_table": "mcp_tool_calls",
        "source_event_id": source_event_id,
        "metadata": {"server_label": server.get("label"), "argument_keys": sorted(arguments.keys())},
    }
    agent_action_metering_service.record_started_sync(**common_event)
    try:
        result = asyncio.run(
            _invoke_mcp_tool_with_auth_recovery_async(
                server=server,
                workspace_id=workspace_id,
                tenant_id=tenant_id,
                tool_name=parsed["tool_name"],
                arguments=arguments,
                http_client=mcp_http_client,
                client_session_cls=client_session_cls,
                streamable_http_client_fn=streamable_http_client_fn,
                run_id=run_id,
                thread_id=thread_id,
                agent_id=agent_id,
            )
        )
    except Exception as exc:
        agent_action_metering_service.record_failed_sync(
            **common_event,
            error_code=type(exc).__name__,
            output_summary=str(exc),
        )
        raise
    payload = _mcp_result_payload(result)
    agent_action_metering_service.record_completed_sync(
        **common_event,
        output_summary=_mcp_reply(payload, agent_label=agent_label, tool_name=parsed["tool_name"]),
    )
    return {
        "status": "ok",
        "reply": _mcp_reply(payload, agent_label=agent_label, tool_name=parsed["tool_name"]),
        "artifact": {
            "label": str(tool_payload.get("label") or parsed["tool_name"]).strip() or parsed["tool_name"],
            "kind": _tool_kind(tool_payload),
            "summary": f"MCP tool {parsed['tool_name']} on server {server.get('label') or parsed['server_id']}",
            "media_type": "application/json",
            "preview_content": json.dumps(
                {
                    "server_id": parsed["server_id"],
                    "tool_name": parsed["tool_name"],
                    "arguments": arguments,
                    "payload": payload,
                },
                ensure_ascii=False,
                indent=2,
            )[:12000],
        },
        "steps": [
            {"label": "Resolving MCP server", "detail": str(server.get("label") or parsed["server_id"]).strip() or parsed["server_id"], "status": "done", "kind": "thinking"},
            {"label": "Invoking MCP tool", "detail": parsed["tool_name"], "status": "done", "kind": "connector"},
        ],
        "mcp": {
            "server_id": parsed["server_id"],
            "tool_name": parsed["tool_name"],
            "arguments": arguments,
            "payload": payload,
        },
    }


async def invoke_workspace_mcp_skill_async(
    *,
    workspace_id: str,
    skill_id: str,
    goal: str,
    agent_label: str,
    tenant_id: str = "default",
    surface: str = "sage",
    source_surface: str = "mcp_skill",
    user_id: Optional[str] = None,
    thread_id: Optional[str] = None,
    run_id: Optional[str] = None,
    agent_id: Optional[str] = None,
    app_id: Optional[str] = None,
    client_session_cls: Any = ClientSession,
    streamable_http_client_fn: Any = streamable_http_client,
) -> Dict[str, Any]:
    parsed = parse_mcp_skill_id(skill_id)
    if parsed is None:
        raise RuntimeError("Invalid MCP skill id.")
    server = get_workspace_mcp_server(workspace_id, parsed["server_id"])
    if server is None:
        raise FileNotFoundError(f"MCP server '{parsed['server_id']}' is not registered for this workspace.")
    if not bool(server.get("enabled", True)):
        raise RuntimeError(f"MCP server '{parsed['server_id']}' is disabled for this workspace.")
    tool_payload = _tool_from_server(server, parsed["tool_name"])
    if tool_payload is None or not bool(tool_payload.get("enabled", True)):
        raise FileNotFoundError(f"MCP tool '{parsed['tool_name']}' is not available on server '{parsed['server_id']}'.")
    execution_call_id = run_id or thread_id or f"mcp_{uuid.uuid4().hex}"
    source_event_id = agent_action_metering_service.build_source_event_id(
        source_surface=source_surface,
        run_id=run_id,
        thread_id=thread_id,
        tool_call_id=execution_call_id,
        action_name=parsed["tool_name"],
    )
    try:
        _assert_tool_approved_for_execution(
            tool_payload,
            server_id=parsed["server_id"],
            tool_name=parsed["tool_name"],
        )
    except Exception as exc:
        await agent_action_metering_service.record_blocked(
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            surface=surface,
            source_surface=source_surface,
            action_domain="mcp",
            action_type=_normalize_action_class(tool_payload.get("action_class")),
            action_name=parsed["tool_name"],
            tool_kind="mcp_tool",
            mcp_server_id=parsed["server_id"],
            mcp_tool_id=parsed["tool_name"],
            connector_id=f"mcp:{parsed['server_id']}",
            skill_id=skill_id,
            risk_level=str(tool_payload.get("risk_level") or "").strip() or None,
            policy_decision="blocked",
            error_code=type(exc).__name__,
            payer="platform_credits",
            billing_mode="none",
            user_id=user_id,
            thread_id=thread_id,
            run_id=run_id,
            agent_id=agent_id,
            app_id=app_id,
            input_summary=goal,
            output_summary=str(exc),
            source_table="mcp_tool_calls",
            source_event_id=source_event_id,
        )
        raise
    arguments = _parse_goal_arguments(goal, tool_payload)
    arguments = _validate_mcp_arguments(
        arguments,
        tool_payload.get("input_schema"),
        tool_name=parsed["tool_name"],
    )
    credential = _resolve_mcp_credential(server, workspace_id)
    mcp_http_client = _build_mcp_http_client(credential)
    common_event = {
        "tenant_id": tenant_id,
        "workspace_id": workspace_id,
        "surface": surface,
        "source_surface": source_surface,
        "action_domain": "mcp",
        "action_type": _normalize_action_class(tool_payload.get("action_class")),
        "action_name": parsed["tool_name"],
        "tool_kind": "mcp_tool",
        "mcp_server_id": parsed["server_id"],
        "mcp_tool_id": parsed["tool_name"],
        "connector_id": f"mcp:{parsed['server_id']}",
        "skill_id": skill_id,
        "risk_level": str(tool_payload.get("risk_level") or "").strip() or None,
        "policy_decision": "allowed",
        "payer": "platform_credits",
        "billing_mode": "transparency",
        "user_id": user_id,
        "thread_id": thread_id,
        "run_id": run_id,
        "agent_id": agent_id,
        "app_id": app_id,
        "input_summary": goal,
        "source_table": "mcp_tool_calls",
        "source_event_id": source_event_id,
        "metadata": {"server_label": server.get("label"), "argument_keys": sorted(arguments.keys())},
    }
    await agent_action_metering_service.record_started(**common_event)
    try:
        result = await _invoke_mcp_tool_with_auth_recovery_async(
            server=server,
            workspace_id=workspace_id,
            tenant_id=tenant_id,
            tool_name=parsed["tool_name"],
            arguments=arguments,
            http_client=mcp_http_client,
            client_session_cls=client_session_cls,
            streamable_http_client_fn=streamable_http_client_fn,
            run_id=run_id,
            thread_id=thread_id,
            agent_id=agent_id,
        )
    except Exception as exc:
        await agent_action_metering_service.record_failed(
            **common_event,
            error_code=type(exc).__name__,
            output_summary=str(exc),
        )
        raise
    payload = _mcp_result_payload(result)
    await agent_action_metering_service.record_completed(
        **common_event,
        output_summary=_mcp_reply(payload, agent_label=agent_label, tool_name=parsed["tool_name"]),
    )
    return {
        "status": "ok",
        "reply": _mcp_reply(payload, agent_label=agent_label, tool_name=parsed["tool_name"]),
        "artifact": {
            "label": str(tool_payload.get("label") or parsed["tool_name"]).strip() or parsed["tool_name"],
            "kind": _tool_kind(tool_payload),
            "summary": f"MCP tool {parsed['tool_name']} on server {server.get('label') or parsed['server_id']}",
            "media_type": "application/json",
            "preview_content": json.dumps(
                {
                    "server_id": parsed["server_id"],
                    "tool_name": parsed["tool_name"],
                    "arguments": arguments,
                    "payload": payload,
                },
                ensure_ascii=False,
                indent=2,
            )[:12000],
        },
        "steps": [
            {"label": "Resolving MCP server", "detail": str(server.get("label") or parsed["server_id"]).strip() or parsed["server_id"], "status": "done", "kind": "thinking"},
            {"label": "Invoking MCP tool", "detail": parsed["tool_name"], "status": "done", "kind": "connector"},
        ],
        "mcp": {
            "server_id": parsed["server_id"],
            "tool_name": parsed["tool_name"],
            "arguments": arguments,
            "payload": payload,
        },
    }
