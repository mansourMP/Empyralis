"""Test harness. Run: python -m server.cli "your message"

Week 2 adds: python -m server.cli connect <app>

Wires Agent + shell + memory + MCP tools, runs the loop, prints response."""

import asyncio
import sys
import urllib.parse
from functools import partial

from server.agent import Agent, Runner
from server.tools.shell import run as shell_run, TOOL_DEF as SHELL_TOOL
from server.memory.service import (
    memory_list, memory_read, memory_write, ALL_MEMORY_TOOLS,
)
from server.vault.store import load_vault, save_vault, set_credential
from server.oauth.provider_configs import get_provider, PROVIDERS
from server.oauth.exchange import exchange_code
from server.mcp.client import discover_mcp_tools
from server.mcp.apps import APPS

SAGE_INSTRUCTIONS = """You are Sage, the Empyralis assistant.

You act on your own reasoning — there are no approval gates. You have access to
shell, memory, and MCP tools (Gmail, Calendar, Drive, Slack, Notion — if connected).
Use them when they help answer the user's request.

When running shell commands:
- Prefer listing and reading over destructive actions
- Explain what you're doing before running commands that modify state
- The working directory is the user's home directory

When using memory:
- Check memory_list() before answering preference questions
- Write important facts the user shares to memory_write()
- Use memory_read() to recall specific stored facts

When using MCP tools:
- Use them freely — no approval needed
- If a tool call fails, tell the user what went wrong
- Prefer the most specific tool for the job

Be concise. Don't ask permission — just act."""

SAGE_MANIFEST = Agent(
    name="Sage",
    instructions=SAGE_INSTRUCTIONS,
    tools=[SHELL_TOOL] + ALL_MEMORY_TOOLS,
    model="claude-sonnet-4-6",
    tool_allowlist=["*"],
)

REDIRECT_URI = "http://localhost:0/oauth/callback"


def _register_builtins(runner: Runner) -> None:
    runner.register("shell", shell_run)
    runner.register("memory_list", partial(memory_list, workspace="default", agent="sage"))
    runner.register("memory_read", partial(memory_read, workspace="default", agent="sage"))
    runner.register("memory_write", partial(memory_write, workspace="default", agent="sage"))


async def _register_mcp_tools(runner: Runner) -> None:
    """Discover and register tools from all MCP apps with credentials in vault."""
    vault = load_vault()
    runner.set_vault(vault)
    for provider_key, apps in APPS.items():
        cred_id = f"mcp:{provider_key}"
        from server.oauth.refresh import resolve_credential
        credential = resolve_credential(vault, cred_id)
        for app in apps:
            try:
                tools = await discover_mcp_tools(app.endpoint, credential=credential)
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
                    print(f"  [mcp] {app.server_id}/{name}")
            except Exception as exc:
                print(f"  [mcp] {app.server_id}: skipped ({exc})")


def cmd_connect(app_name: str) -> None:
    """OAuth flow: print URL, accept pasted redirect, exchange tokens."""
    provider_key = app_name.strip().lower()
    if provider_key not in PROVIDERS and provider_key not in APPS:
        print(f"Unknown app: {app_name}")
        print(f"Available: {', '.join(sorted(PROVIDERS.keys()))}")
        sys.exit(1)

    config = get_provider(provider_key)
    client_id = __import__("os").environ.get(config.client_id_env, "").strip()
    if not client_id:
        print(f"Missing {config.client_id_env}. Set it in .env and retry.")
        sys.exit(1)

    # Build auth URL
    scopes = " ".join(config.scopes) if config.scopes else ""
    params = {
        "client_id": client_id,
        "redirect_uri": REDIRECT_URI,
        "response_type": "code",
        "scope": scopes,
        **config.auth_params,
    }
    if provider_key == "slack":
        params["user_scope"] = scopes
        params["scope"] = ""
    url = f"{config.auth_url}?{urllib.parse.urlencode(params)}"
    print(f"\nOpen this URL in your browser:\n{url}\n")
    print("After authorizing, you'll be redirected to a URL starting with:")
    print(f"  {REDIRECT_URI}?code=...")
    print("Paste the full redirect URL here:")

    redirect = input("> ").strip()
    parsed = urllib.parse.urlparse(redirect)
    qs = urllib.parse.parse_qs(parsed.query)
    code = qs.get("code", [""])[0]
    if not code:
        print("No authorization code found in URL. Make sure you pasted the full redirect URL.")
        sys.exit(1)

    print("Exchanging code for tokens...")
    creds = exchange_code(provider_key, code, REDIRECT_URI)
    vault = load_vault()
    set_credential(vault, f"mcp:{provider_key}", creds)
    save_vault(vault)
    print(f"✓ Connected {config.label}! Credential saved as mcp:{provider_key}")


async def cmd_run(message: str) -> None:
    """Run a single message through Sage with all tools wired."""
    agent = SAGE_MANIFEST
    runner = Runner(agent)
    _register_builtins(runner)
    await _register_mcp_tools(runner)
    response = await runner.run(message)
    print(response)


def main():
    if len(sys.argv) < 2:
        print("Usage: python -m server.cli \"your message\"")
        print("       python -m server.cli connect <app>")
        sys.exit(1)

    if sys.argv[1] == "connect":
        if len(sys.argv) < 3:
            print("Usage: python -m server.cli connect <app>")
            print(f"Apps: {', '.join(sorted(PROVIDERS.keys()))}")
            sys.exit(1)
        cmd_connect(sys.argv[2])
    else:
        asyncio.run(cmd_run(sys.argv[1]))


if __name__ == "__main__":
    main()
