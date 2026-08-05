"""MAN-310 Phase 1: specialist_runtime_context.resolve_specialist_runtime_
context must resolve model_config.engine onto SpecialistRuntimeContext.engine
the same way it already resolves reasoning_effort/mode/runtime — a per-agent
choice read straight off the install bundle's install_metadata.model_config,
lowercased, defaulting to "" (unset) when absent."""

from __future__ import annotations

import asyncio
import unittest
from unittest.mock import AsyncMock, patch

from server_modules import specialist_runtime_context as src


def _run(coro):
    return asyncio.run(coro)


def _bundle(*, agent_id="agent-1", label="Research Agent", model_config=None, agent_kind="specialist"):
    return {
        "id": agent_id,
        "label": label,
        "project_id": "proj-1",
        "agent_definition": {"agent_kind": agent_kind},
        "metadata": {
            "instructions": "Help with research.",
            "model_config": dict(model_config or {}),
        },
    }


class ResolveSpecialistRuntimeContextEngineFieldTests(unittest.TestCase):
    @staticmethod
    def _resolve(model_config):
        with (
            patch(
                "server_modules.agent_registry_repository.get_workspace_master_agent_install",
                new=AsyncMock(return_value={"id": "master-1"}),
            ),
            patch(
                "server_modules.agent_registry_repository.get_workspace_agent_install_bundle",
                new=AsyncMock(return_value=_bundle(model_config=model_config)),
            ),
        ):
            return _run(src.resolve_specialist_runtime_context(
                workspace_id="ws-1", tenant_id="default", active_agent_install_id="agent-1",
            ))

    def test_engine_is_resolved_from_model_config(self):
        ctx = self._resolve({"mode": "byok_api", "engine": "claude_agent_sdk"})
        self.assertEqual(ctx.engine, "claude_agent_sdk")

    def test_engine_defaults_to_empty_string_when_unset(self):
        ctx = self._resolve({"mode": "byok_api"})
        self.assertEqual(ctx.engine, "")

    def test_engine_defaults_to_empty_string_when_model_config_absent(self):
        ctx = self._resolve({})
        self.assertEqual(ctx.engine, "")

    def test_engine_value_is_lowercased(self):
        ctx = self._resolve({"mode": "byok_api", "engine": "Claude_Agent_SDK"})
        self.assertEqual(ctx.engine, "claude_agent_sdk")

    def test_engine_does_not_affect_other_resolved_fields(self):
        """Sanity check: adding engine resolution must not disturb the
        existing mode/provider/reasoning_effort resolution it sits next to."""
        ctx = self._resolve({
            "mode": "byok_api", "provider": "anthropic", "model": "claude-sonnet-4-6",
            "reasoning_effort": "high", "engine": "claude_agent_sdk",
        })
        self.assertEqual(ctx.mode, "byok_api")
        self.assertEqual(ctx.provider, "anthropic")
        self.assertEqual(ctx.model, "claude-sonnet-4-6")
        self.assertEqual(ctx.reasoning_effort, "high")
        self.assertEqual(ctx.engine, "claude_agent_sdk")


if __name__ == "__main__":
    unittest.main()
