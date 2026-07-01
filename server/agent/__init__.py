"""Empyralis v2 — One Agent class, one Runner.

No subclasses. No agent kinds. No approval gates."""

from dataclasses import dataclass, field
import json
import os
from pathlib import Path
from typing import Any, Callable

import anthropic

from server.agent.providers import resolve_provider


def _load_dotenv() -> None:
    """Minimal .env parser — no python-dotenv dependency."""
    env_path = Path(__file__).resolve().parent.parent.parent / ".env"
    if not env_path.is_file():
        return
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key, val = key.strip(), val.strip().strip("\"'")
        if key and key not in os.environ:
            os.environ[key] = val


_load_dotenv()

ToolDef = dict[str, Any]
ToolHandler = Callable[..., str]
ToolRegistry = dict[str, ToolHandler]


@dataclass(frozen=True)
class Agent:
    """Immutable agent configuration. NOT inherited by anything.

    Three configs (Sage, Native, External) — one class."""
    name: str
    instructions: str
    tools: list[ToolDef] = field(default_factory=list)
    model: str = "claude-sonnet-4-6"
    tool_allowlist: list[str] | None = None  # None = allow all; ["*"] = allow all


class Runner:
    """Stateless: takes (agent, message), runs the loop, returns response."""

    def __init__(self, agent: Agent, api_key: str | None = None):
        self.agent = agent
        if api_key:
            key = api_key
        else:
            # Non-HTTP callers (CLI dev harness, Telegram bot) may omit api_key
            # and rely on ANTHROPIC_API_KEY from the environment. The web API
            # (server/api/main.py) MUST always pass an explicit key from the vault.
            key = os.environ.get("ANTHROPIC_API_KEY", "")
            if not key:
                raise RuntimeError(
                    "No API key provided and ANTHROPIC_API_KEY is not set in environment."
                )
        # Route to the correct API: Anthropic keys start with sk-ant,
        # DeepSeek keys start with sk- (without ant).
        self._base_url, self._model = resolve_provider(key, self.agent.model)
        self._client = anthropic.AsyncAnthropic(
            api_key=key, base_url=self._base_url
        )
        self._registry: ToolRegistry = {}
        # MCP tool metadata: tool_name → {server_id, endpoint, credential_id, input_schema}
        self._mcp_tools: dict[str, dict[str, Any]] = {}
        self._mcp_tool_schemas: list[dict[str, Any]] = []
        self._vault: dict[str, Any] | None = None

    def set_vault(self, vault: dict[str, Any]) -> None:
        self._vault = vault

    def register(self, name: str, handler: ToolHandler) -> None:
        self._registry[name] = handler

    def register_mcp_tool(self, server_id: str, tool_name: str, endpoint: str,
                          input_schema: dict[str, Any] | None = None,
                          credential_id: str | None = None) -> None:
        """Register an MCP tool for dispatch. The runner handles auth + call."""
        self._mcp_tools[tool_name] = {
            "server_id": server_id, "endpoint": endpoint,
            "credential_id": credential_id,
            "input_schema": input_schema or {},
        }
        self._mcp_tool_schemas.append({
            "name": tool_name,
            "description": f"MCP tool '{tool_name}' on server '{server_id}'",
            "input_schema": input_schema or {"type": "object", "properties": {}},
        })

    def _allowed_tools(self) -> list[dict[str, Any]] | None:
        """Build the tool list sent to the LLM, filtered by allowlist."""
        allowlist = self.agent.tool_allowlist
        all_tools = list(self.agent.tools) + self._mcp_tool_schemas
        if allowlist is None or "*" in allowlist:
            return all_tools if all_tools else None
        return [t for t in all_tools if t["name"] in allowlist] or None

    async def run(self, message: str, max_turns: int = 25,
                  message_history: list[dict] | None = None,
                  on_token: Any | None = None,
                  on_usage: Callable[[int, int], Any] | None = None) -> str:
        """Run the agent loop. Optionally prepend prior conversation messages.

        If on_token is provided, streams text chunks via callback while
        collecting the full response. Non-streaming otherwise (CLI, Telegram).

        If on_usage is provided, fired after each completed API call with
        (input_tokens, output_tokens). Fires once per turn — a multi-turn
        tool-use run fires it multiple times; the caller accumulates."""
        messages: list[dict] = []
        if message_history:
            for msg in message_history:
                role = msg.get("role", "user")
                content = msg.get("content", "")
                messages.append(
                    {"role": role,
                     "content": [{"type": "text", "text": str(content)}]})
        messages.append(
            {"role": "user", "content": [{"type": "text", "text": message}]})
        turns = 0

        while True:
            turns += 1
            if turns > max_turns:
                return (
                    f"Run halted: exceeded {max_turns} turns. "
                    "Sage may be stuck in a tool loop."
                )

            if on_token:
                full_text = ""
                async with self._client.messages.stream(
                    model=self._model,
                    max_tokens=4096,
                    system=self.agent.instructions,
                    tools=self._allowed_tools(),
                    messages=messages,
                ) as stream:
                    async for chunk in stream.text_stream:
                        full_text += chunk
                        await on_token(chunk)
                    resp = await stream.get_final_message()
                    if on_usage and resp.usage:
                        await on_usage(resp.usage.input_tokens, resp.usage.output_tokens)
            else:
                resp = await self._client.messages.create(
                    model=self._model,
                    max_tokens=4096,
                    system=self.agent.instructions,
                    tools=self._allowed_tools(),
                    messages=messages,
                )
                if on_usage and resp.usage:
                    await on_usage(resp.usage.input_tokens, resp.usage.output_tokens)

            tool_uses = [b for b in resp.content if b.type == "tool_use"]
            if not tool_uses:
                if on_token:
                    return full_text
                texts = [b for b in resp.content if b.type == "text"]
                return "\n".join(b.text for b in texts)

            # Append assistant content (skip thinking blocks)
            serialized = [s for b in resp.content
                          if (s := self._serialize_block(b)) is not None]
            messages.append({"role": "assistant", "content": serialized})

            # Execute each tool and append results
            tool_results = []
            for tu in tool_uses:
                result = await self._execute_tool(tu.name, tu.input)
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": tu.id,
                    "content": str(result),
                })

            messages.append({"role": "user", "content": tool_results})

    async def _execute_tool(self, name: str, args: dict[str, Any]) -> str:
        """Execute a tool: check registry first, then MCP tools."""
        # Built-in handler (sync — fast enough to call directly)
        handler = self._registry.get(name)
        if handler:
            try:
                return handler(**args)
            except Exception as exc:
                return f"error: {exc}"

        # MCP tool (async — await directly, no thread bridge)
        mcp = self._mcp_tools.get(name)
        if mcp:
            try:
                from server.mcp.client import call_mcp_tool, _validate_arguments
                from server.oauth.refresh import resolve_credential

                cleaned = _validate_arguments(args, mcp["input_schema"], tool_name=name)
                credential = None
                if self._vault and mcp["credential_id"]:
                    credential = resolve_credential(self._vault, mcp["credential_id"])

                result = await call_mcp_tool(
                    endpoint=mcp["endpoint"],
                    tool_name=name,
                    arguments=cleaned,
                    credential=credential,
                )
                return json.dumps(result, ensure_ascii=False, default=str)
            except Exception as exc:
                return f"MCP error: {exc}"

        return f"unknown tool: {name}"

    @staticmethod
    def _serialize_block(block: Any) -> dict:
        """Convert an Anthropic content block to a dict for message history.

        Skips thinking/redacted_thinking blocks — those are Claude-internal
        and must not be sent back to the API."""
        d: dict = {"type": block.type}
        if block.type in ("thinking", "redacted_thinking"):
            return None  # type: ignore — filtered by caller
        if block.type == "text":
            d["text"] = block.text
        elif block.type == "tool_use":
            d["id"] = block.id
            d["name"] = block.name
            d["input"] = block.input
        return d
