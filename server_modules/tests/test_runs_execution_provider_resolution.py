"""
Durable-run provider-resolution honesty tests.

Live-verified root cause (2026-07-14): a real scheduled wake-up on a
cli_subscription agent (run_id c6ffc089-3e16-438c-99f1-fe6906733cd6)
reached provider resolution with context.provider and metadata.provider
both unset. resolve_run_execution_context (runs_engine.py) silently
defaulted to "openai" and failed one layer down with "No credentials
available for provider 'openai'" -- a misleading message that looks like
a missing OpenAI key when in fact no provider was ever configured for the
run at all.

These tests cover the two real entry points inside runs_execution.py that
feed resolve_run_execution_context: _resolve_agent_generation_state (the
generic "workflow" agent-node engine) and the "runtime_resolve" orion DAG
node (the one proven live above). Both must now fail honestly instead of
silently assuming "openai", while any run that DOES set an explicit
provider must resolve exactly as before (no regression).
"""

import queue
import unittest
from unittest.mock import patch

from server_modules import runs_execution


class ResolveAgentGenerationStateHonestFailureTests(unittest.TestCase):
    def test_no_provider_anywhere_raises_instead_of_defaulting_to_openai(self):
        with self.assertRaises(RuntimeError) as ctx:
            runs_execution._resolve_agent_generation_state({}, {})
        msg = str(ctx.exception)
        self.assertIn("No AI provider is configured", msg)
        self.assertNotIn("No credentials available for provider 'openai'", msg)

    def test_heartbeat_context_gets_the_scheduler_specific_message(self):
        base_context = {
            "metadata": {
                "source": "heartbeat",
                "wake_request_ids": ["wake_bfe2f20fd5774b44"],
                "heartbeat_trigger": "schedule",
            }
        }
        with self.assertRaises(RuntimeError) as ctx:
            runs_execution._resolve_agent_generation_state(base_context, {})
        msg = str(ctx.exception)
        self.assertIn("heartbeat", msg.lower())
        self.assertIn("orchestrator", msg.lower())
        self.assertIn("cli_subscription", msg)

    def test_explicit_provider_in_runtime_config_still_resolves_unchanged(self):
        config = {"runtime": {"provider": "anthropic", "model": "claude-sonnet-4-6"}}
        with patch.object(
            runs_execution,
            "resolve_run_execution_context",
            return_value=("anthropic", "claude-sonnet-4-6", [{"credentials": {"api_key": "x"}}], {}),
        ) as mock_resolve:
            execution_context, state = runs_execution._resolve_agent_generation_state({}, config)

        self.assertEqual(state["provider"], "anthropic")
        self.assertEqual(execution_context["provider"], "anthropic")
        mock_resolve.assert_called_once()

    def test_explicit_provider_in_metadata_still_resolves_unchanged(self):
        base_context = {"metadata": {"provider": "openai"}}
        with patch.object(
            runs_execution,
            "resolve_run_execution_context",
            return_value=("openai", "gpt-5.4", [{"credentials": {"api_key": "x"}}], {}),
        ):
            execution_context, state = runs_execution._resolve_agent_generation_state(base_context, {})

        # Explicitly requested "openai" is legitimate and must pass through --
        # only the SILENT default is being removed, not the ability to ask
        # for openai on purpose.
        self.assertEqual(state["provider"], "openai")


class RuntimeResolveDagNodeHonestFailureTests(unittest.TestCase):
    """The 'runtime_resolve' kind is the orion DAG node proven live to be
    the actual failing node for scheduled/heartbeat runs -- confirmed via
    a real run (run_id c6ffc089-3e16-438c-99f1-fe6906733cd6, DAG
    orion-standard-v1) whose persisted context had provider=None,
    metadata.provider=None, metadata.source="heartbeat"."""

    def _run_node(self, context):
        return runs_execution._execute_orion_dag_node(
            "test-run-id", context, queue.Queue(), {"kind": "runtime_resolve"}, {}
        )

    def test_no_provider_anywhere_raises_instead_of_defaulting_to_openai(self):
        context = {"workflow_id": "wf-test", "user_goal": "test goal", "metadata": {}}
        with self.assertRaises(RuntimeError) as ctx:
            self._run_node(context)
        msg = str(ctx.exception)
        self.assertIn("No AI provider is configured", msg)
        self.assertNotIn("No credentials available for provider 'openai'", msg)

    def test_live_verified_heartbeat_shape_raises_the_scheduler_specific_message(self):
        """Mirrors the exact metadata shape read back from the real
        production run_archive row for run_id
        c6ffc089-3e16-438c-99f1-fe6906733cd6 (queried 2026-07-14)."""
        context = {
            "workflow_id": None,
            "user_goal": "Wake reasons:\n- [self_proposed] LIVE VERIFY: scheduler wake-up test",
            "provider": None,
            "model": None,
            "agent_role": "orchestrator",
            "metadata": {
                "source": "heartbeat",
                "wake_request_ids": ["wake_bfe2f20fd5774b44"],
                "heartbeat_trigger": "schedule",
                "trigger_source": "schedule",
                "agent_role": "orchestrator",
                "owner_user_id": "telegram-bot",
            },
        }
        with self.assertRaises(RuntimeError) as ctx:
            self._run_node(context)
        msg = str(ctx.exception)
        self.assertIn("heartbeat", msg.lower())
        self.assertIn("scheduler", msg.lower())

    def test_explicit_provider_on_context_still_resolves_unchanged(self):
        context = {
            "workflow_id": "wf-test",
            "user_goal": "test goal",
            "provider": "anthropic",
            "agents": [],
            "metadata": {},
        }
        with patch.object(
            runs_execution,
            "resolve_run_execution_context",
            return_value=("anthropic", "claude-sonnet-4-6", [{"credentials": {"api_key": "x"}}], {}),
        ) as mock_resolve:
            state = {}
            result = runs_execution._execute_orion_dag_node(
                "test-run-id", context, queue.Queue(), {"kind": "runtime_resolve"}, state
            )

        self.assertEqual(state["provider"], "anthropic")
        self.assertEqual(result["provider"], "anthropic")
        mock_resolve.assert_called_once()

    def test_explicit_provider_in_metadata_still_resolves_unchanged(self):
        context = {
            "workflow_id": "wf-test",
            "user_goal": "test goal",
            "agents": [],
            "metadata": {"provider": "openai"},
        }
        with patch.object(
            runs_execution,
            "resolve_run_execution_context",
            return_value=("openai", "gpt-5.4", [{"credentials": {"api_key": "x"}}], {}),
        ):
            state = {}
            runs_execution._execute_orion_dag_node(
                "test-run-id", context, queue.Queue(), {"kind": "runtime_resolve"}, state
            )

        self.assertEqual(state["provider"], "openai")


class HonestNoProviderErrorMessageTests(unittest.TestCase):
    """Direct unit tests of the shared message-builder, isolated from the
    two call sites above."""

    def test_generic_case_names_the_workflow_when_present(self):
        err = runs_execution._honest_no_provider_error({"workflow_id": "wf-42"}, {})
        self.assertIn("wf-42", str(err))

    def test_generic_case_falls_back_to_this_run_when_no_workflow_id(self):
        err = runs_execution._honest_no_provider_error({}, {})
        self.assertIn("this run", str(err))

    def test_heartbeat_detected_via_source_field(self):
        err = runs_execution._honest_no_provider_error({}, {"source": "heartbeat"})
        self.assertIn("heartbeat", str(err).lower())

    def test_heartbeat_detected_via_wake_request_ids(self):
        err = runs_execution._honest_no_provider_error({}, {"wake_request_ids": ["wake_1"]})
        self.assertIn("heartbeat", str(err).lower())

    def test_heartbeat_detected_via_heartbeat_trigger(self):
        err = runs_execution._honest_no_provider_error({}, {"heartbeat_trigger": "schedule"})
        self.assertIn("heartbeat", str(err).lower())

    def test_non_heartbeat_metadata_does_not_trigger_heartbeat_message(self):
        err = runs_execution._honest_no_provider_error({}, {"source": "studio_manual_run"})
        self.assertNotIn("heartbeat", str(err).lower())


if __name__ == "__main__":
    unittest.main()
