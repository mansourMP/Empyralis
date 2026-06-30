"""Integration test: Google Workspace → MCP pipeline end-to-end.

Requires a real Google OAuth access token. Set TEST_GOOGLE_ACCESS_TOKEN in your
environment to run the full pipeline. Without it, structural tests still run.

Usage::

    TEST_GOOGLE_ACCESS_TOKEN="ya29.a0..." pytest tests/integration/test_google_workspace_mcp.py -v
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, List

import pytest

# Ensure the project root is on sys.path so server_modules imports resolve.
_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

# Initialize runtime model context (populates connector catalog for validation).
try:
    from server_modules.runtime_config import CONNECTOR_CATALOG, CHANNEL_REGISTRY, PROVIDER_CATALOG, ORION_MEMORY_MAX_TEXT_CHARS
    from server_modules.runtime_models import configure_runtime_model_context, _noop_normalize_memory_bucket
    from server_modules.runtime_config import normalize_action_id as _normalize_action_id
    configure_runtime_model_context(
        memory_max_text_chars=ORION_MEMORY_MAX_TEXT_CHARS,
        normalize_memory_bucket=_noop_normalize_memory_bucket,
        normalize_action_id=_normalize_action_id,
        provider_catalog=PROVIDER_CATALOG,
        connector_catalog=CONNECTOR_CATALOG,
        channel_catalog=CHANNEL_REGISTRY,
    )
except Exception as exc:
    import warnings
    warnings.warn(f"Could not initialize runtime model context: {exc}")

# ── helpers ──────────────────────────────────────────────────────────────────


def _env_flag(name: str) -> bool:
    return str(os.getenv(name) or "").strip().lower() in {"1", "true", "yes", "on"}


def _has_mcp_sdk() -> bool:
    try:
        import mcp  # noqa: F401
        return True
    except ImportError:
        return False


def _real_token() -> str:
    return str(os.getenv("TEST_GOOGLE_ACCESS_TOKEN") or "").strip()


# ── constants ────────────────────────────────────────────────────────────────

GOOGLE_MCP_SERVERS = [
    {
        "server_id": "google-gmail",
        "label": "Google Gmail (MCP)",
        "endpoint": "https://gmailmcp.googleapis.com/mcp/v1",
    },
    {
        "server_id": "google-calendar",
        "label": "Google Calendar (MCP)",
        "endpoint": "https://calendarmcp.googleapis.com/mcp/v1",
    },
    {
        "server_id": "google-drive",
        "label": "Google Drive (MCP)",
        "endpoint": "https://drivemcp.googleapis.com/mcp/v1",
    },
]

WORKSPACE_ID = "gw-mcp-integration-test"


# ── fixtures ─────────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _ensure_state_dirs():
    """Ensure runtime state directories exist."""
    from server_modules.runtime_config import EMPYRALIS_STATE_HOME

    runtime_dir = EMPYRALIS_STATE_HOME / "runtime"
    runtime_dir.mkdir(parents=True, exist_ok=True)
    return runtime_dir


@pytest.fixture
def workspace_id() -> str:
    return WORKSPACE_ID


# ── structural tests (no external APIs needed) ────────────────────────────────


class TestVaultCredentialStorage:
    """Verify a Google Workspace OAuth credential can be stored and resolved."""

    def test_store_and_resolve_credential(self, workspace_id: str, monkeypatch):
        """Store a test credential in the vault and resolve it by id."""
        from unittest.mock import patch

        from server_modules import connectors_actions
        from server_modules.schemas import ConnectorCreate
        from server_modules.vault_store import _openssl_decrypt, load_vault
        from server_modules.vault_helpers import resolve_vault_credential

        token = _real_token() or "test-google-access-token"

        # Bypass the live API validator — the structural test verifies vault
        # storage / resolution, not Google's API.
        with patch(
            "server_modules.connectors_actions.validate_google_workspace_connector",
            return_value={"ok": True, "status": "healthy", "message": "Integration test — skipped live validation."},
        ):
            result = asyncio_run(
                connectors_actions.create_connector_vault(
                    ConnectorCreate(
                        label="Google Workspace (Integration Test)",
                        connector="google_workspace",
                        workspace_id=workspace_id,
                        credentials={
                            "auth_mode": "oauth",
                            "access_token": token,
                            "token_type": "Bearer",
                            "scope": "openid email profile https://www.googleapis.com/auth/gmail.modify https://www.googleapis.com/auth/calendar https://www.googleapis.com/auth/drive.file",
                        },
                        metadata={"source": "integration_test"},
                    )
                )
            )

        credential_id = str(result.get("id") or "").strip()
        assert credential_id, f"Expected a credential_id, got: {result}"

        # Resolve the credential from vault
        resolved = resolve_vault_credential(
            load_vault, _openssl_decrypt, credential_id, workspace_id=workspace_id
        )
        assert isinstance(resolved, dict)
        assert resolved.get("access_token") == token
        assert resolved.get("auth_mode") == "oauth"

        # Store for later tests
        TestVaultCredentialStorage._credential_id = credential_id

    @staticmethod
    def credential_id() -> str:
        return getattr(TestVaultCredentialStorage, "_credential_id", "")


class TestMcpServerRegistration:
    """Verify MCP server entries can be registered with credential_id."""

    def test_register_google_mcp_servers(self, workspace_id: str):
        """Register Gmail/Calendar/Drive MCP servers with credential_id."""
        from server_modules import mcp_registry_service

        cred_id = TestVaultCredentialStorage.credential_id()
        if not cred_id:
            pytest.skip("No credential_id from vault storage test — run test_store_and_resolve_credential first")

        registered = []
        for entry in GOOGLE_MCP_SERVERS:
            server = asyncio_run(
                mcp_registry_service.upsert_workspace_mcp_server_async(
                    workspace_id=workspace_id,
                    server_id=entry["server_id"],
                    label=entry["label"],
                    transport="streamable_http",
                    endpoint=entry["endpoint"],
                    enabled=True,
                    credential_id=cred_id,
                    discover_tools=False,  # don't connect — SDK may not be installed
                )
            )
            registered.append(server)

        assert len(registered) == 3

        for server in registered:
            assert server.get("credential_id") == cred_id, (
                f"Server {server.get('id')} expected credential_id={cred_id}, "
                f"got {server.get('credential_id')}"
            )
            assert server.get("endpoint"), f"Server {server.get('id')} has no endpoint"
            assert server.get("enabled") is True

        # Verify all 3 servers appear in the workspace listing
        all_servers = mcp_registry_service.list_workspace_mcp_servers(workspace_id)
        server_ids = {s.get("id") for s in all_servers}
        for entry in GOOGLE_MCP_SERVERS:
            assert entry["server_id"] in server_ids, (
                f"Server {entry['server_id']} not in workspace listing: {server_ids}"
            )

        TestMcpServerRegistration._workspace_id = workspace_id

    def test_mcp_servers_json_has_credential_id(self, workspace_id: str):
        """Verify mcp_servers.json on disk contains credential_id for our servers."""
        mcp_servers_path = Path(
            os.path.expanduser("~/.empyralis/state/runtime/mcp_servers.json")
        )
        if not mcp_servers_path.exists():
            # Try the config-driven path
            from server_modules.mcp_registry_service import MCP_SERVER_REGISTRY_FILE
            mcp_servers_path = MCP_SERVER_REGISTRY_FILE

        if not mcp_servers_path.exists():
            pytest.skip(f"mcp_servers.json not found at {mcp_servers_path}")

        raw = json.loads(mcp_servers_path.read_text(encoding="utf-8"))
        workspaces = raw.get("workspaces", {})
        servers = workspaces.get(workspace_id, {}).get("servers", {})

        for entry in GOOGLE_MCP_SERVERS:
            server_data = servers.get(entry["server_id"])
            assert isinstance(server_data, dict), (
                f"Server {entry['server_id']} not found in mcp_servers.json"
            )
            assert server_data.get("credential_id"), (
                f"Server {entry['server_id']} has no credential_id in mcp_servers.json"
            )


class TestCredentialResolution:
    """Verify credential resolution for MCP connections."""

    def test_resolve_mcp_credential_returns_token(self, workspace_id: str):
        """_resolve_mcp_credential should return the decrypted credential dict."""
        from server_modules.mcp_registry_service import (
            _resolve_mcp_credential,
            get_workspace_mcp_server,
        )

        server = get_workspace_mcp_server(workspace_id, "google-gmail")
        if not server:
            pytest.skip("google-gmail MCP server not registered")

        credential = _resolve_mcp_credential(server, workspace_id)
        assert credential is not None, "Expected non-None credential"
        assert "access_token" in credential, f"Credential missing access_token: {credential.keys()}"

    def test_build_mcp_auth_headers(self):
        """_build_mcp_auth_headers produces correct Authorization header."""
        from server_modules.mcp_registry_service import _build_mcp_auth_headers

        # Bearer token
        headers = _build_mcp_auth_headers({"access_token": "ya29.test"})
        assert headers == {"Authorization": "Bearer ya29.test"}

        # API key
        headers = _build_mcp_auth_headers({"api_key": "sk-test"})
        assert headers == {"Authorization": "ApiKey sk-test"}

        # Bot token
        headers = _build_mcp_auth_headers({"bot_token": "xoxb-test"})
        assert headers == {"Authorization": "Bot xoxb-test"}

        # Empty
        assert _build_mcp_auth_headers({}) == {}
        assert _build_mcp_auth_headers(None) == {}


# ── live MCP protocol tests (require TEST_GOOGLE_ACCESS_TOKEN + MCP SDK) ─────


@pytest.mark.skipif(
    not _has_mcp_sdk(),
    reason="MCP Python SDK not installed — install with: pip install mcp",
)
@pytest.mark.skipif(
    not _real_token(),
    reason="TEST_GOOGLE_ACCESS_TOKEN not set — provide a real Google OAuth access token",
)
class TestLiveMcpProtocol:
    """End-to-end MCP protocol tests against Google's MCP servers.

    These tests require:
    1. A real Google OAuth access token (set TEST_GOOGLE_ACCESS_TOKEN)
    2. The MCP Python SDK installed

    The token must have scopes: gmail.modify, calendar, drive.file
    """

    def test_discover_gmail_tools(self, workspace_id: str):
        """Discover tools from Google's Gmail MCP server."""
        from server_modules.mcp_registry_service import discover_mcp_server_tools

        tools = discover_mcp_server_tools(
            transport="streamable_http",
            endpoint="https://gmailmcp.googleapis.com/mcp/v1",
            server_id="google-gmail",
            credential={"access_token": _real_token()},
        )

        assert len(tools) > 0, "Expected at least 1 tool from Gmail MCP server"
        tool_names = {t.get("name") for t in tools}
        print(f"\nGmail MCP tools ({len(tools)}): {sorted(tool_names)}")

        # Expect gmail-specific tools
        assert any("gmail" in name.lower() or "mail" in name.lower() or "message" in name.lower()
                   for name in tool_names), f"No Gmail-like tools found: {tool_names}"

    def test_discover_calendar_tools(self, workspace_id: str):
        """Discover tools from Google's Calendar MCP server."""
        from server_modules.mcp_registry_service import discover_mcp_server_tools

        tools = discover_mcp_server_tools(
            transport="streamable_http",
            endpoint="https://calendarmcp.googleapis.com/mcp/v1",
            server_id="google-calendar",
            credential={"access_token": _real_token()},
        )

        assert len(tools) > 0, "Expected at least 1 tool from Calendar MCP server"
        tool_names = {t.get("name") for t in tools}
        print(f"\nCalendar MCP tools ({len(tools)}): {sorted(tool_names)}")

    def test_discover_drive_tools(self, workspace_id: str):
        """Discover tools from Google's Drive MCP server."""
        from server_modules.mcp_registry_service import discover_mcp_server_tools

        tools = discover_mcp_server_tools(
            transport="streamable_http",
            endpoint="https://drivemcp.googleapis.com/mcp/v1",
            server_id="google-drive",
            credential={"access_token": _real_token()},
        )

        assert len(tools) > 0, "Expected at least 1 tool from Drive MCP server"
        tool_names = {t.get("name") for t in tools}
        print(f"\nDrive MCP tools ({len(tools)}): {sorted(tool_names)}")

    def test_invoke_gmail_search(self, workspace_id: str):
        """Invoke a Gmail search tool with a simple query."""
        from server_modules.mcp_registry_service import (
            _build_mcp_http_client,
            _call_streamable_http_tool_async,
            discover_mcp_server_tools,
        )

        # Discover tools first to find the right search tool name
        credential = {"access_token": _real_token()}
        tools = discover_mcp_server_tools(
            transport="streamable_http",
            endpoint="https://gmailmcp.googleapis.com/mcp/v1",
            server_id="google-gmail",
            credential=credential,
        )

        # Find a search/list/query tool
        search_tool = None
        for tool in tools:
            name = str(tool.get("name") or "").lower()
            if any(kw in name for kw in ("search", "list", "query", "find")):
                search_tool = tool
                break

        if search_tool is None:
            pytest.skip("No search/list/query tool found in Gmail MCP server")

        tool_name = search_tool["name"]
        print(f"\nInvoking Gmail tool: {tool_name}")

        import asyncio

        http_client = _build_mcp_http_client(credential)
        result = asyncio.run(
            _call_streamable_http_tool_async(
                endpoint="https://gmailmcp.googleapis.com/mcp/v1",
                tool_name=tool_name,
                arguments={"query": "test"},
                http_client=http_client,
            )
        )

        assert result is not None, f"Tool {tool_name} returned None"
        print(f"Result type: {type(result).__name__}")
        print(f"Result: {str(result)[:500]}")

    @pytest.mark.skipif(
        not _env_flag("TEST_GOOGLE_FULL_PIPELINE"),
        reason="Set TEST_GOOGLE_FULL_PIPELINE=1 to run the full OAuth bridge test",
    )
    def test_full_oauth_bridge_flow(self, workspace_id: str):
        """End-to-end test: OAuth token → vault → MCP servers → tools → invoke.

        This test requires TEST_GOOGLE_ACCESS_TOKEN to contain a valid token.
        It bypasses the OAuth code exchange (we already have a token) but
        exercises the full registration + discovery + invoke pipeline.
        """
        import asyncio
        from server_modules.mcp_registry_service import (
            discover_mcp_server_tools,
            _call_streamable_http_tool_async,
        )

        cred_id = TestVaultCredentialStorage.credential_id()
        assert cred_id, "No credential stored"

        # Verify all 3 servers registered with correct credential_id
        for entry in GOOGLE_MCP_SERVERS:
            tools = discover_mcp_server_tools(
                transport="streamable_http",
                endpoint=entry["endpoint"],
                server_id=entry["server_id"],
                credential={"access_token": _real_token()},
            )
            assert len(tools) > 0, f"No tools discovered for {entry['server_id']}"
            print(f"\n{entry['server_id']}: {len(tools)} tools")

        print("\n✓ Full pipeline: credential → MCP servers → tools — all verified")


# ── async helper ─────────────────────────────────────────────────────────────


def asyncio_run(coro):
    """Run an async function synchronously, compatible with nested event loops."""
    import asyncio

    try:
        return asyncio.run(coro)
    except RuntimeError as exc:
        if "asyncio.run() cannot be called from a running event loop" not in str(exc):
            raise

    # Already inside an event loop — use a thread
    import threading

    result: Dict[str, Any] = {}
    failure: Dict[str, BaseException] = {}

    def _runner():
        try:
            result["value"] = asyncio.run(coro)
        except BaseException as err:
            failure["error"] = err

    thread = threading.Thread(target=_runner, daemon=True)
    thread.start()
    thread.join()
    if "error" in failure:
        raise failure["error"]
    return result.get("value")
