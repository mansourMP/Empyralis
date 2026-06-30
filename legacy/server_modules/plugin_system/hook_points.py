from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional


class HookPoint(Enum):
    LLM_INPUT = "llm_input"
    LLM_OUTPUT = "llm_output"
    TOOL_CALL = "tool_call"
    TOOL_RESULT = "tool_result"
    AGENT_START = "agent_start"
    AGENT_END = "agent_end"


HOOK_LLM_INPUT = HookPoint.LLM_INPUT
HOOK_LLM_OUTPUT = HookPoint.LLM_OUTPUT
HOOK_TOOL_CALL = HookPoint.TOOL_CALL
HOOK_TOOL_RESULT = HookPoint.TOOL_RESULT
HOOK_AGENT_START = HookPoint.AGENT_START
HOOK_AGENT_END = HookPoint.AGENT_END

ALL_HOOK_POINTS = frozenset(HookPoint)


@dataclass
class HookContext:
    hook_point: HookPoint
    workspace_id: str = ""
    session_id: str = ""
    channel: str = ""

    messages: List[Dict[str, Any]] = field(default_factory=list)
    system_prompt: str = ""
    tools: List[Dict[str, Any]] = field(default_factory=list)

    reply: str = ""
    tool_calls: List[Dict[str, Any]] = field(default_factory=list)
    tool_name: str = ""
    tool_arguments: Dict[str, Any] = field(default_factory=dict)
    tool_result: Any = None

    usage: Dict[str, Any] = field(default_factory=dict)
    metadata: Dict[str, Any] = field(default_factory=dict)

    aborted: bool = False
    abort_reason: str = ""


@dataclass
class HookResult:
    modified: bool = False

    messages: Optional[List[Dict[str, Any]]] = None
    system_prompt: Optional[str] = None
    tools: Optional[List[Dict[str, Any]]] = None
    reply: Optional[str] = None
    tool_arguments: Optional[Dict[str, Any]] = None
    tool_result: Optional[Any] = None

    abort: bool = False
    abort_reason: str = ""

    def apply(self, ctx: HookContext) -> HookContext:
        if self.messages is not None:
            ctx.messages = self.messages
            self.modified = True
        if self.system_prompt is not None:
            ctx.system_prompt = self.system_prompt
            self.modified = True
        if self.tools is not None:
            ctx.tools = self.tools
            self.modified = True
        if self.reply is not None:
            ctx.reply = self.reply
            self.modified = True
        if self.tool_arguments is not None:
            ctx.tool_arguments = self.tool_arguments
            self.modified = True
        if self.tool_result is not None:
            ctx.tool_result = self.tool_result
            self.modified = True
        if self.abort:
            ctx.aborted = True
            ctx.abort_reason = self.abort_reason
        return ctx
