"""Empyralis MCP server — exposes the platform as tools for external AI clients.

Phase U2: per-workspace API key auth, read+chat tools live, write tools
gated behind ``EMPYRALIS_MCP_WRITE_ENABLED``.

Architecture
------------
Every tool resolves the workspace from an ``Authorization: Bearer <key>``
header.  The workspace is NEVER taken from tool arguments — the key IS
the workspace binding.  All calls are ledgered with
``event_class="mcp_inbound"`` and ``actor="external_mcp_client"``.

Tools
-----
Read (always live):
  - ``empyralis_list_agents`` → fleet_list_agents
  - ``empyralis_get_agent_activity`` → fleet_get_agent_activity
  - ``empyralis_memory_read`` → memory_read
  - ``empyralis_memory_list`` → memory_list
  - ``empyralis_chat`` → full turn through normal chat path

Write (gated behind EMPYRALIS_MCP_WRITE_ENABLED=true):
  - ``empyralis_create_agent`` → fleet_create_agent
  - ``empyralis_configure_agent`` → fleet_configure_agent
  - ``empyralis_message_agent`` → fleet_message_agent
  - ``empyralis_memory_write`` → memory_write
"""

from __future__ import annotations

import importlib.util
import json
import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, AsyncIterator, Dict, List, Optional

from fastapi import FastAPI

try:
    from mcp.server.fastmcp import Context, FastMCP
except Exception:  # pragma: no cover
    FastMCP = None  # type: ignore[assignment]
    Context = None  # type: ignore[assignment]

LOGGER = logging.getLogger(__name__)

EMPYRALIST_MCP_PATH = "/mcp"
EMPYRALIST_MCP_NAME = "empyralist"
EMPYRALIST_MCP_ENDPOINT = "http://127.0.0.1:8001/mcp"
EMPYRALIST_MCP_TOOLS = [
    "list_spaces",
    "get_space_status",
    "get_recent_alerts",
    "ask_space",
    # Phase U2 platform tools
    "empyralis_list_agents",
    "empyralis_get_agent_activity",
    "empyralis_memory_read",
    "empyralis_memory_list",
    "empyralis_chat",
    "empyralis_create_agent",
    "empyralis_configure_agent",
    "empyralis_message_agent",
    "empyralis_memory_write",
]

PROJECT_ROOT = Path(__file__).resolve().parent
SPACES_ROOT = PROJECT_ROOT / "spaces"
VISION_SKILL_DIR = PROJECT_ROOT / "skills" / "vision-monitor"

_WRITE_ENABLED = os.getenv("EMPYRALIS_MCP_WRITE_ENABLED", "").strip().lower() in {
    "1", "true", "yes",
}


# ── API key resolution ──────────────────────────────────────────────────


async def _resolve_workspace(ctx: Any) -> str:
    """Extract workspace_id from the MCP request's Authorization header.

    Raises ``RuntimeError`` with a clear message if the key is missing,
    invalid, or revoked — FastMCP surfaces this as a tool error to the
    MCP client.
    """
    try:
        headers = getattr(getattr(ctx, "request_context", None), "request", None)
        if headers is None:
            # Fallback: try to get the starlette Request from the session
            raise RuntimeError("No request context available.")
        auth = getattr(headers, "headers", {}).get("authorization", "")
        if not auth:
            # Try accessing via starlette Request directly
            try:
                scope = getattr(headers, "scope", {})
                for h in scope.get("headers", []):
                    if h[0] == b"authorization":
                        auth = h[1].decode()
                        break
            except Exception:
                pass
    except Exception:
        auth = ""

    if not auth:
        raise RuntimeError(
            "Missing MCP API key. Add an Authorization header: "
            '"Bearer empyralis_mcp_..." — create a key at /api/mcp/keys.'
        )

    from server_modules.mcp_server_auth import resolve_workspace_from_api_key

    workspace_id = await resolve_workspace_from_api_key(auth)
    if not workspace_id:
        raise RuntimeError(
            "Invalid or revoked MCP API key. Create a new key at /api/mcp/keys."
        )

    return workspace_id


async def _ledger_mcp_call(
    workspace_id: str,
    tool_name: str,
    ok: bool,
    **extra: Any,
) -> None:
    """Write an mcp_inbound ledger event for every MCP tool call."""
    try:
        from server_modules.runs_core import emit_log

        emit_log(
            workspace_id=workspace_id,
            event_class="mcp_inbound",
            action=tool_name,
            actor_id="external_mcp_client",
            metadata={"ok": ok, **extra},
        )
    except Exception:
        LOGGER.debug("Failed to ledger MCP call %s", tool_name, exc_info=True)


# ── Vision tools (unchanged) ─────────────────────────────────────────────


def _build_mcp_server() -> FastMCP | None:
    if FastMCP is None:
        return None
    return FastMCP(EMPYRALIST_MCP_NAME)


empyralist_mcp = _build_mcp_server()

_VISION_MODULE_CACHE: Dict[str, Any] = {}


def _load_python_module(module_name: str, path: Path):
    cached = _VISION_MODULE_CACHE.get(module_name)
    if cached is not None:
        return cached
    spec = importlib.util.spec_from_file_location(module_name, str(path))
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load module from {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    _VISION_MODULE_CACHE[module_name] = module
    return module


def _vision_common():
    return _load_python_module("empyralist_vision_common", VISION_SKILL_DIR / "common.py")


def _vision_model_router():
    return _load_python_module("empyralist_vision_model_router", VISION_SKILL_DIR / "model_router.py")


def _vision_config() -> Dict[str, Any]:
    common = _vision_common()
    return common.load_config()


def _spaces_root() -> Path:
    config = _vision_config()
    common = _vision_common()
    return common.spaces_root(config)


def _space_dirs() -> List[Path]:
    root = _spaces_root()
    if not root.exists():
        return []
    return sorted(
        [item for item in root.iterdir() if item.is_dir() and not item.name.startswith("_")],
        key=lambda item: item.name.lower(),
    )


def _space_dir(space_id: str) -> Path:
    target = str(space_id or "").strip()
    if not target:
        raise RuntimeError("space_id is required.")
    path = _spaces_root() / target
    if not path.exists() or not path.is_dir():
        raise FileNotFoundError(f"Space '{target}' was not found.")
    return path


def _read_json(path: Path) -> Dict[str, Any]:
    try:
        parsed = json.loads(path.read_text(encoding="utf-8"))
        return parsed if isinstance(parsed, dict) else {}
    except Exception:
        return {}


def _read_jsonl(path: Path) -> List[Dict[str, Any]]:
    if not path.exists():
        return []
    items: List[Dict[str, Any]] = []
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        try:
            parsed = json.loads(line)
            if isinstance(parsed, dict):
                items.append(parsed)
        except Exception:
            continue
    return items


def _load_current_state(space_id: str) -> Dict[str, Any]:
    return _read_json(_space_dir(space_id) / "current_state.json")


def _load_space_config(space_id: str) -> Dict[str, Any]:
    return _read_json(_space_dir(space_id) / "config.json")


def _state_prompt(space_id: str, question: str, state: Dict[str, Any]) -> str:
    summary_lines = state.get("summary_lines") if isinstance(state.get("summary_lines"), list) else []
    anomalies = state.get("anomalies") if isinstance(state.get("anomalies"), list) else []
    state_snapshot = {
        "space_id": space_id,
        "status": str(state.get("status") or "unknown").strip() or "unknown",
        "occupancy_count": int(state.get("occupancy_count") or 0),
        "timestamp": str(state.get("timestamp") or "").strip(),
        "confidence": float(state.get("confidence") or 0),
        "summary_lines": [str(item).strip() for item in summary_lines if str(item).strip()],
        "anomalies": [str(item).strip() for item in anomalies if str(item).strip()],
    }
    return (
        "You are answering a question about a monitored physical space.\n"
        "Use only the provided structured state.\n"
        "If the state is unclear, say so plainly.\n"
        "Answer in plain English in 1-3 short sentences.\n\n"
        f"Question: {str(question or '').strip()}\n"
        f"State JSON: {json.dumps(state_snapshot, ensure_ascii=False)}"
    )


# ── Register all tools ───────────────────────────────────────────────────


if empyralist_mcp is not None:

    # ── Vision tools (existing) ───────────────────────────────────────

    @empyralist_mcp.tool()
    def list_spaces() -> List[str]:
        """List available monitored space IDs."""
        return [space_dir.name for space_dir in _space_dirs()]

    @empyralist_mcp.tool()
    def get_space_status(space_id: str) -> Dict[str, Any]:
        """Return the latest state for one monitored space."""
        state = _load_current_state(space_id)
        config = _load_space_config(space_id)
        return {
            "space_id": str(state.get("space_id") or space_id).strip() or str(space_id).strip(),
            "space_name": str(config.get("space_name") or state.get("space_id") or space_id).strip() or str(space_id).strip(),
            "status": str(state.get("status") or "unknown").strip() or "unknown",
            "occupancy_count": int(state.get("occupancy_count") or 0),
            "timestamp": str(state.get("timestamp") or "").strip(),
            "summary_lines": [str(item).strip() for item in (state.get("summary_lines") if isinstance(state.get("summary_lines"), list) else []) if str(item).strip()],
            "anomalies": [str(item).strip() for item in (state.get("anomalies") if isinstance(state.get("anomalies"), list) else []) if str(item).strip()],
            "confidence": float(state.get("confidence") or 0),
        }

    @empyralist_mcp.tool()
    def get_recent_alerts(space_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """Return the latest unresolved alerts for one space or all spaces."""
        space_ids = [str(space_id).strip()] if str(space_id or "").strip() else [space_dir.name for space_dir in _space_dirs()]
        alerts: List[Dict[str, Any]] = []
        for current_space_id in space_ids:
            config = _load_space_config(current_space_id)
            space_name = str(config.get("space_name") or current_space_id).strip() or current_space_id
            for item in _read_jsonl(_space_dir(current_space_id) / "alerts.jsonl"):
                if bool(item.get("resolved")):
                    continue
                alerts.append({
                    "id": str(item.get("id") or "").strip(),
                    "space_id": current_space_id,
                    "space_name": space_name,
                    "ts": str(item.get("ts") or "").strip(),
                    "severity": str(item.get("severity") or "warning").strip() or "warning",
                    "code": str(item.get("code") or "").strip(),
                    "message": str(item.get("message") or "").strip(),
                    "resolved": False,
                })
        return sorted(alerts, key=lambda item: str(item.get("ts") or ""), reverse=True)[:5]

    @empyralist_mcp.tool()
    def ask_space(space_id: str, question: str) -> Dict[str, Any]:
        """Answer a natural-language question about one monitored space."""
        state = _load_current_state(space_id)
        if not state:
            raise FileNotFoundError(f"No current state is available for '{space_id}'.")
        config = _vision_config()
        vlm_config = config.get("vlm") if isinstance(config.get("vlm"), dict) else {}
        prompt = _state_prompt(space_id, question, state)
        answer = _vision_model_router().complete_text(prompt, vlm_config)
        return {
            "space_id": str(space_id).strip(),
            "answer": str(answer.get("text") or "").strip(),
            "provider": str(answer.get("provider") or "").strip(),
            "model": str(answer.get("model") or "").strip(),
        }

    # ── Phase U2: Platform tools (read + chat) ───────────────────────

    @empyralist_mcp.tool()
    async def empyralis_list_agents(ctx: Context) -> Dict[str, Any]:
        """List all agents in your Empyralis workspace."""
        ws = await _resolve_workspace(ctx)
        try:
            from server_modules.fleet_tools import fleet_list_agents
            result = await fleet_list_agents(workspace_id=ws, actor_id="external_mcp_client")
            await _ledger_mcp_call(ws, "empyralis_list_agents", True, agent_count=len(result.get("agents", [])))
            return {"ok": True, **result}
        except Exception as exc:
            await _ledger_mcp_call(ws, "empyralis_list_agents", False, error=str(exc)[:200])
            raise RuntimeError(f"Failed to list agents: {exc}") from exc

    @empyralist_mcp.tool()
    async def empyralis_get_agent_activity(
        agent_id: str,
        limit: int = 20,
        ctx: Context = None,
    ) -> Dict[str, Any]:
        """Get recent activity for a specific agent (ledger events)."""
        ws = await _resolve_workspace(ctx)
        try:
            from server_modules.fleet_tools import fleet_get_agent_activity
            result = await fleet_get_agent_activity(
                workspace_id=ws,
                agent_id=agent_id,
                limit=limit,
                actor_id="external_mcp_client",
            )
            await _ledger_mcp_call(ws, "empyralis_get_agent_activity", True, agent_id=agent_id)
            return {"ok": True, **result}
        except Exception as exc:
            await _ledger_mcp_call(ws, "empyralis_get_agent_activity", False, error=str(exc)[:200])
            raise RuntimeError(f"Failed to get agent activity: {exc}") from exc

    @empyralist_mcp.tool()
    async def empyralis_memory_read(key: str, ctx: Context = None) -> Dict[str, Any]:
        """Read a memory entry by key from your workspace."""
        ws = await _resolve_workspace(ctx)
        try:
            from server_modules.agent_memory_tools import memory_read
            result = await memory_read(workspace_id=ws, key=key)
            await _ledger_mcp_call(ws, "empyralis_memory_read", True, key=key)
            return {"ok": True, "key": key, "value": result}
        except Exception as exc:
            await _ledger_mcp_call(ws, "empyralis_memory_read", False, error=str(exc)[:200])
            raise RuntimeError(f"Failed to read memory: {exc}") from exc

    @empyralist_mcp.tool()
    async def empyralis_memory_list(ctx: Context = None) -> Dict[str, Any]:
        """List all memory entries in your workspace."""
        ws = await _resolve_workspace(ctx)
        try:
            from server_modules.agent_memory_tools import memory_list
            entries = await memory_list(workspace_id=ws)
            await _ledger_mcp_call(ws, "empyralis_memory_list", True, entry_count=len(entries or []))
            return {"ok": True, "entries": entries or []}
        except Exception as exc:
            await _ledger_mcp_call(ws, "empyralis_memory_list", False, error=str(exc)[:200])
            raise RuntimeError(f"Failed to list memory: {exc}") from exc

    @empyralist_mcp.tool()
    async def empyralis_chat(
        message: str,
        agent_id: str = "",
        ctx: Context = None,
    ) -> Dict[str, Any]:
        """Send a message through the full Empyralis chat turn (triage, ledger, AI).

        This is the same path used by Telegram, web chat, and all other
        channels — full triage, scope checks, and ledger audit apply.
        """
        ws = await _resolve_workspace(ctx)
        try:
            from server_modules.direct_chat_runtime_entry_facade_service import collect_direct_operator_reply
            from server_modules.direct_chat_operator_binding_service import build_direct_chat_module_export_map_from_namespace
            from server_modules.direct_chat_runtime_exports import build_operator_namespace

            namespace = build_operator_namespace(workspace_id=ws)
            export_map = build_direct_chat_module_export_map_from_namespace(namespace=namespace)
            collect_fn = export_map.get("collect_direct_operator_reply")

            if collect_fn is None:
                raise RuntimeError("Chat runtime not available — export map missing collector.")

            payload = await collect_fn(message=message)
            reply = str(payload.get("reply") or "").strip()

            await _ledger_mcp_call(ws, "empyralis_chat", True,
                                   message_len=len(message), reply_len=len(reply))
            return {"ok": True, "reply": reply, "agent_id": agent_id or "(sage)"}
        except Exception as exc:
            await _ledger_mcp_call(ws, "empyralis_chat", False, error=str(exc)[:200])
            raise RuntimeError(f"Chat failed: {exc}") from exc

    # ── Phase U2: Write tools (flag-gated) ───────────────────────────

    @empyralist_mcp.tool()
    async def empyralis_create_agent(
        name: str,
        ctx: Context = None,
    ) -> Dict[str, Any]:
        """Create a new specialist agent in your workspace. Requires write access."""
        if not _WRITE_ENABLED:
            raise RuntimeError(
                "MCP write tools are not enabled on this deployment. "
                "Set EMPYRALIS_MCP_WRITE_ENABLED=true to enable."
            )
        ws = await _resolve_workspace(ctx)
        try:
            from server_modules.fleet_tools import fleet_create_agent
            result = await fleet_create_agent(
                workspace_id=ws,
                agent_label=name,
                actor_id="external_mcp_client",
            )
            await _ledger_mcp_call(ws, "empyralis_create_agent", result.get("ok", False),
                                   agent_label=name)
            return result
        except Exception as exc:
            await _ledger_mcp_call(ws, "empyralis_create_agent", False, error=str(exc)[:200])
            raise RuntimeError(f"Failed to create agent: {exc}") from exc

    @empyralist_mcp.tool()
    async def empyralis_configure_agent(
        agent_id: str,
        patch: Dict[str, Any],
        ctx: Context = None,
    ) -> Dict[str, Any]:
        """Configure an agent's settings. Requires write access."""
        if not _WRITE_ENABLED:
            raise RuntimeError(
                "MCP write tools are not enabled on this deployment. "
                "Set EMPYRALIS_MCP_WRITE_ENABLED=true to enable."
            )
        ws = await _resolve_workspace(ctx)
        try:
            from server_modules.fleet_tools import fleet_configure_agent
            result = await fleet_configure_agent(
                workspace_id=ws,
                agent_id=agent_id,
                patch_dict=patch,
                actor_id="external_mcp_client",
            )
            await _ledger_mcp_call(ws, "empyralis_configure_agent", result.get("ok", False),
                                   agent_id=agent_id)
            return result
        except Exception as exc:
            await _ledger_mcp_call(ws, "empyralis_configure_agent", False, error=str(exc)[:200])
            raise RuntimeError(f"Failed to configure agent: {exc}") from exc

    @empyralist_mcp.tool()
    async def empyralis_message_agent(
        agent_id: str,
        message: str,
        ctx: Context = None,
    ) -> Dict[str, Any]:
        """Send a message to a specific agent's fleet inbox. Requires write access."""
        if not _WRITE_ENABLED:
            raise RuntimeError(
                "MCP write tools are not enabled on this deployment. "
                "Set EMPYRALIS_MCP_WRITE_ENABLED=true to enable."
            )
        ws = await _resolve_workspace(ctx)
        try:
            from server_modules.fleet_tools import fleet_message_agent
            result = await fleet_message_agent(
                workspace_id=ws,
                agent_id=agent_id,
                message=message,
                actor_id="external_mcp_client",
            )
            await _ledger_mcp_call(ws, "empyralis_message_agent", result.get("ok", False),
                                   agent_id=agent_id)
            return result
        except Exception as exc:
            await _ledger_mcp_call(ws, "empyralis_message_agent", False, error=str(exc)[:200])
            raise RuntimeError(f"Failed to message agent: {exc}") from exc

    @empyralist_mcp.tool()
    async def empyralis_memory_write(
        key: str,
        value: str,
        ctx: Context = None,
    ) -> Dict[str, Any]:
        """Write a memory entry to your workspace. Requires write access."""
        if not _WRITE_ENABLED:
            raise RuntimeError(
                "MCP write tools are not enabled on this deployment. "
                "Set EMPYRALIS_MCP_WRITE_ENABLED=true to enable."
            )
        ws = await _resolve_workspace(ctx)
        try:
            from server_modules.agent_memory_tools import memory_write
            result = await memory_write(workspace_id=ws, key=key, value=value)
            await _ledger_mcp_call(ws, "empyralis_memory_write", True, key=key)
            return {"ok": True, "key": key, "result": result}
        except Exception as exc:
            await _ledger_mcp_call(ws, "empyralis_memory_write", False, error=str(exc)[:200])
            raise RuntimeError(f"Failed to write memory: {exc}") from exc


# ── Mount + lifespan ─────────────────────────────────────────────────────


def mount_empyralist_mcp(app: FastAPI) -> None:
    if empyralist_mcp is None:
        return
    app.mount(EMPYRALIST_MCP_PATH, empyralist_mcp.streamable_http_app())


@asynccontextmanager
async def empyralist_mcp_lifespan() -> AsyncIterator[None]:
    if empyralist_mcp is None:
        yield
        return
    async with empyralist_mcp.session_manager.run():
        yield
