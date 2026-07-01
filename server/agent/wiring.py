"""Shared runner wiring — register built-in tools + MCP discovery.

Every channel (web, Telegram, CLI) calls wire_runner() exactly once per
Runner instance.  This replaces the triple copy-paste that lived in
api/main.py, channels/telegram.py, and cli.py before the MAN-29
consolidation pass.
"""

from functools import partial

from server.mcp.apps import APPS
from server.mcp.client import discover_mcp_tools
from server.memory.service import memory_list, memory_read, memory_write
from server.oauth.refresh import resolve_credential
from server.tools.shell import run as shell_run
from server.vault.store import credential_id, load_vault


async def wire_runner(runner, *, scope: str) -> None:
    """Register built-in tools and discover MCP tools for connected apps.

    Parameters
    ----------
    runner : Runner
        The Runner instance to wire up.
    scope : str
        Passed to credential_id() for MCP credential lookups.
        Examples:
          - ``"global"``                    (CLI dev harness)
          - ``"session:<session_id>"``      (web API)
          - ``"workspace:<workspace_id>"``  (Telegram)
    """
    # -- built-in tools ----------------------------------------------------
    runner.register("shell", shell_run)
    runner.register("memory_list",
                    partial(memory_list, workspace="default", agent="sage"))
    runner.register("memory_read",
                    partial(memory_read, workspace="default", agent="sage"))
    runner.register("memory_write",
                    partial(memory_write, workspace="default", agent="sage"))

    # -- MCP tools ----------------------------------------------------------
    vault = load_vault()
    runner.set_vault(vault)

    for provider_key, apps in APPS.items():
        cred_id = credential_id(scope=scope, provider=provider_key)
        credential = resolve_credential(vault, cred_id)
        for app in apps:
            try:
                tools = await discover_mcp_tools(app.endpoint,
                                                  credential=credential)
                for tool in tools:
                    name = tool.get("name", "")
                    if not name:
                        continue
                    runner.register_mcp_tool(
                        server_id=app.server_id,
                        tool_name=name,
                        endpoint=app.endpoint,
                        input_schema=tool.get("input_schema"),
                        credential_id=cred_id,
                    )
            except Exception:
                pass  # App not connected — skip
