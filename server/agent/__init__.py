"""Empyralis v2 — One Agent class, one Runner.

No subclasses. No agent kinds. No approval gates."""

from dataclasses import dataclass, field
import os
from pathlib import Path
from typing import Any, Callable

import anthropic


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


class Runner:
    """Stateless: takes (agent, message), runs the loop, returns response."""

    def __init__(self, agent: Agent):
        self.agent = agent
        self._client = anthropic.Anthropic(
            api_key=os.environ["ANTHROPIC_API_KEY"]
        )
        self._registry: ToolRegistry = {}

    def register(self, name: str, handler: ToolHandler) -> None:
        self._registry[name] = handler

    def run(self, message: str, max_turns: int = 25) -> str:
        messages: list[dict] = [
            {"role": "user", "content": [{"type": "text", "text": message}]}
        ]
        turns = 0

        while True:
            turns += 1
            if turns > max_turns:
                return (
                    f"Run halted: exceeded {max_turns} turns. "
                    "Sage may be stuck in a tool loop."
                )
            resp = self._client.messages.create(
                model=self.agent.model,
                max_tokens=4096,
                system=self.agent.instructions,
                tools=self.agent.tools if self.agent.tools else None,
                messages=messages,
            )

            tool_uses = [b for b in resp.content if b.type == "tool_use"]
            if not tool_uses:
                texts = [b for b in resp.content if b.type == "text"]
                return "\n".join(b.text for b in texts)

            # Append assistant content (skip thinking blocks)
            serialized = [s for b in resp.content
                          if (s := self._serialize_block(b)) is not None]
            messages.append({"role": "assistant", "content": serialized})

            # Execute each tool and append results
            tool_results = []
            for tu in tool_uses:
                handler = self._registry.get(tu.name)
                if handler:
                    try:
                        result = handler(**tu.input)
                    except Exception as exc:
                        result = f"error: {exc}"
                else:
                    result = f"unknown tool: {tu.name}"
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": tu.id,
                    "content": str(result),
                })

            messages.append({"role": "user", "content": tool_results})

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
