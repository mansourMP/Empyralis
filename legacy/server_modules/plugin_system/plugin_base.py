from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from server_modules.plugin_system.hook_points import HookContext, HookResult


class PluginInstallError(ValueError):
    pass


@dataclass
class PluginManifest:
    plugin_id: str
    name: str
    version: str
    description: str = ""
    author: str = ""
    hooks: List[str] = None

    def __post_init__(self):
        if self.hooks is None:
            self.hooks = []

    def as_dict(self) -> Dict[str, Any]:
        return {
            "plugin_id": self.plugin_id,
            "name": self.name,
            "version": self.version,
            "description": self.description,
            "author": self.author,
            "hooks": self.hooks,
        }


@dataclass
class PluginHealth:
    plugin_id: str
    healthy: bool = True
    last_error: str = ""
    hook_count: int = 0


class Plugin:
    manifest: PluginManifest

    def __init__(self) -> None:
        self._enabled = True

    @property
    def enabled(self) -> bool:
        return self._enabled

    def enable(self) -> None:
        self._enabled = True

    def disable(self) -> None:
        self._enabled = False

    def on_llm_input(self, ctx: HookContext) -> HookResult:
        return HookResult()

    def on_llm_output(self, ctx: HookContext) -> HookResult:
        return HookResult()

    def on_tool_call(self, ctx: HookContext) -> HookResult:
        return HookResult()

    def on_tool_result(self, ctx: HookContext) -> HookResult:
        return HookResult()

    def on_agent_start(self, ctx: HookContext) -> HookResult:
        return HookResult()

    def on_agent_end(self, ctx: HookContext) -> HookResult:
        return HookResult()

    def health(self) -> PluginHealth:
        return PluginHealth(
            plugin_id=self.manifest.plugin_id if self.manifest else "unknown",
            healthy=self._enabled,
        )
