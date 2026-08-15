"""A durable turn must resolve the workspace's provider for itself.

THE BUG THIS EXISTS FOR (observed live, 2026-08-15, on a real message):

    a chat message that "looks like a task"
      -> _promote_turn_request_to_primary_engine_path swaps the engine to
         durable_run underneath it
      -> nobody resolves the provider (the browser never sends one, and only
         the direct-chat branch was doing the resolution)
      -> runs_execution._honest_no_provider_error, 3 attempts, all fail
      -> an EMPTY assistant turn, and canned copy telling the customer to
         reconnect hardware that was never the problem

The same family as the engine-swap billing bug already recorded in CLAUDE.md:
a second engine behind one dispatch seam inherits the RETURN CONTRACT and
silently drops what the old branch's callee was supplying.

These are unit tests over the seam itself — no database, no provider call —
because the point is the wiring, not the resolver.
"""

import asyncio
import sys
import types
import unittest

from server_modules import run_service
from server_modules.agent_turn import AgentTurnRequest, TurnActor


def _request(**overrides) -> AgentTurnRequest:
    base = dict(
        tenant_id="tenant_x",
        workspace_id="ws_x",
        thread_id="thread_x",
        session_id="session_x",
        channel="web",
        actor=TurnActor(type="user", id="user_x", display_name=""),
        message="Run this on your computer and reply with the exact output: uname -a",
        attachments=[],
        context_hints={},
        execution_mode="durable",
        response_mode="artifact",
        machine_target=None,
        policy_context={},
    )
    base.update(overrides)
    return AgentTurnRequest(**base)


class _StubResolver:
    """Stands in for sage_agent_runtime_service._resolve_cloud_provider."""

    def __init__(self, provider: str):
        self.provider = provider
        self.calls: list[str] = []

    async def __call__(self, workspace_id: str, **kwargs):
        self.calls.append(workspace_id)
        return self.provider, {"credential": "stub"}


class DurableTurnProviderResolutionTests(unittest.TestCase):
    def _run_with_resolver(self, request, provider="deepseek"):
        """Patch the PACKAGE ATTRIBUTE, not sys.modules.

        `_ensure_durable_turn_provider` does `from server_modules import
        sage_agent_runtime_service`, and that reads the attribute off the
        already-imported `server_modules` package before it ever consults
        sys.modules. So a sys.modules stand-in works only when nothing else
        has imported the real module yet — i.e. it passes when this file runs
        alone and silently reaches the REAL resolver in a full suite run.
        Measured: 2 failures that appear only under the whole selection.

        Same family as CLAUDE.md's "a stand-in left in sys.modules becomes
        production's `server` forever" — the lesson there is that module
        stand-ins have to be installed where the importer actually looks.
        """
        import server_modules

        resolver = _StubResolver(provider)
        real = getattr(server_modules, "sage_agent_runtime_service", None)
        stub = types.ModuleType("server_modules.sage_agent_runtime_service")
        stub._resolve_cloud_provider = resolver  # type: ignore[attr-defined]

        real_in_sys = sys.modules.get("server_modules.sage_agent_runtime_service")
        setattr(server_modules, "sage_agent_runtime_service", stub)
        sys.modules["server_modules.sage_agent_runtime_service"] = stub
        try:
            result = asyncio.run(run_service._ensure_durable_turn_provider(request))
        finally:
            # Restore both, always — a leaked stand-in poisons every later test
            # that imports this module for real.
            if real is not None:
                setattr(server_modules, "sage_agent_runtime_service", real)
            else:
                try:
                    delattr(server_modules, "sage_agent_runtime_service")
                except AttributeError:
                    pass
            if real_in_sys is not None:
                sys.modules["server_modules.sage_agent_runtime_service"] = real_in_sys
            else:
                sys.modules.pop("server_modules.sage_agent_runtime_service", None)
        return result, resolver

    def test_a_turn_with_no_provider_gets_the_workspace_one(self):
        """The live failure: the browser sends no provider, because picking one
        is not the browser's job."""
        result, resolver = self._run_with_resolver(_request())

        self.assertEqual(result.context_hints.get("provider"), "deepseek")
        self.assertEqual(
            result.context_hints["metadata"].get("provider"),
            "deepseek",
            "runs_execution reads context.provider OR metadata.provider — both must carry it",
        )
        self.assertEqual(resolver.calls, ["ws_x"], "resolved once, for this workspace")

    def test_the_resolved_provider_is_marked_as_resolved(self):
        """A provider the platform chose and one the caller asked for are
        different facts; a run's own row has to be able to tell them apart."""
        result, _ = self._run_with_resolver(_request())
        self.assertEqual(
            result.context_hints["metadata"].get("provider_source"), "workspace_resolved"
        )

    def test_a_caller_supplied_provider_is_never_overridden(self):
        """An explicit choice outranks the workspace default, and must not even
        trigger a resolution."""
        result, resolver = self._run_with_resolver(
            _request(context_hints={"provider": "anthropic"})
        )
        self.assertEqual(result.context_hints.get("provider"), "anthropic")
        self.assertEqual(resolver.calls, [], "no resolution when one was supplied")

    def test_a_provider_hidden_in_metadata_also_counts(self):
        """runs_execution accepts metadata.provider, so this seam must treat it
        as present too — otherwise it would resolve over the top of it."""
        result, resolver = self._run_with_resolver(
            _request(context_hints={"metadata": {"provider": "openai"}})
        )
        self.assertEqual(result.context_hints["metadata"].get("provider"), "openai")
        self.assertEqual(resolver.calls, [])

    def test_an_empty_resolution_changes_nothing(self):
        """If the resolver genuinely has no answer, the request is handed on
        untouched so the engine's own honest error is what the customer gets —
        never a fabricated provider."""
        result, _ = self._run_with_resolver(_request(), provider="")
        self.assertEqual(result.context_hints.get("provider"), None)

    def test_the_original_request_is_not_mutated(self):
        """The promotion builder hands the same object to other readers."""
        request = _request()
        result, _ = self._run_with_resolver(request)
        self.assertEqual(request.context_hints, {})
        self.assertIsNot(result, request)


class DurableTurnProviderWiringTests(unittest.TestCase):
    def test_execute_durable_turn_request_calls_the_seam(self):
        """A behavioural test cannot catch the seam being unwired — the call
        would simply stop happening and every mocked test would still pass.
        Assert the call site exists, the same AST discipline this repo already
        uses for guards that must not lose their only caller.
        """
        import ast
        import inspect

        source = inspect.getsource(run_service.execute_durable_turn_request)
        tree = ast.parse(source.lstrip())
        called = {
            node.func.id
            for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        }
        self.assertIn(
            "_ensure_durable_turn_provider",
            called,
            "the durable entry point must resolve its provider before building the run",
        )


if __name__ == "__main__":
    unittest.main()
