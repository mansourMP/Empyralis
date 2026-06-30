"""Empyralis v2 — One Agent class, one Runner.

No subclasses. No agent kinds. No approval gates."""

from dataclasses import dataclass, field
import os
from typing import Any, Callable

import anthropic

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

    def run(self, message: str) -> str:
        messages: list[dict] = [{"role": "user", "content": message}]

        while True:
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

            # Append assistant content with tool_use blocks
            messages.append({
                "role": "assistant",
                "content": [self._serialize_block(b) for b in resp.content],
            })

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
        """Convert an Anthropic content block to a dict for message history."""
        d: dict = {"type": block.type}
        if block.type == "text":
            d["text"] = block.text
        elif block.type == "tool_use":
            d["id"] = block.id
            d["name"] = block.name
            d["input"] = block.input
        return d
