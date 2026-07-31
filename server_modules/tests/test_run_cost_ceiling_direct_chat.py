"""MAN-144: enforced per-run (per-turn) cost ceiling for the direct-chat
tool-calling loop (Path B).

New file -- does not modify any existing test file or conftest.py.

Covers server_modules/direct_chat_generation_service.stream_provider_backed_
direct_chat's tool-loop, which is the real, single choke point every
conversational direct-chat turn's model calls pass through
(services.generate_chat_reply_stream_with_provider_fallback, called once per
loop iteration). This loop has no run_id / run_service.py-tracked run object
at all -- direct chat is not a "run" in that system's sense -- so a halt here
is surfaced the same way this loop already surfaces its other non-crash
deliberate stop (tool_loop_detected): an intervention in the final payload
plus a distinct error code, never a silent failure.
"""

from __future__ import annotations

import unittest

from server_modules import config_defaults_service
from server_modules import direct_chat_generation_service


class RunCostCeilingDirectChatTests(unittest.TestCase):
    def _services(self, *, stream_fn) -> direct_chat_generation_service.DirectChatGenerationServices:
        return direct_chat_generation_service.DirectChatGenerationServices(
            thinking_step_payload=lambda iteration, status, detail=None: {
                "type": "step",
                "iteration": iteration,
                "status": status,
                "detail": detail,
            },
            build_context_used=lambda **kwargs: kwargs,
            build_direct_tool_approval_response=lambda **kwargs: None,
            parse_tool_name=lambda name: tuple(str(name).split("__", 1)) if "__" in str(name) else ("", ""),
            tool_arguments_payload=lambda value: value if isinstance(value, dict) else {},
            parse_page_state=lambda value: {},
            direct_tool_step_payload=lambda connector_id, action_id, arguments, **kwargs: {
                "type": "step",
                "connector": connector_id,
                "action": action_id,
                "arguments": arguments,
                **kwargs,
            },
            execute_single_direct_tool_call=lambda **kwargs: "tool result",
            direct_tool_followup_message=lambda tool_name, result_text: f"{tool_name}: {result_text}",
            suggest_actions=lambda _message, _availability: [],
            clear_direct_tool_loop_state=lambda _session_key: None,
            persist_direct_chat_memory_best_effort=lambda **kwargs: None,
            persist_direct_chat_transcript_best_effort=lambda **kwargs: None,
            persist_direct_chat_hosted_usage_best_effort=lambda **kwargs: None,
            record_direct_tool_signature=lambda _session_key, _tool_call: False,
            direct_chat_error_reply=lambda error: f"Chat failed: {error}",
            capture_exception=lambda exc: None,
            generate_chat_reply_stream_with_provider_fallback=stream_fn,
        )

    def _run(self, *, services, metadata, max_iterations=10):
        return list(
            direct_chat_generation_service.stream_provider_backed_direct_chat(
                services=services,
                context={"provider": "anthropic"},
                metadata=metadata,
                system_prompt="System prompt",
                normalized_workspace_id="ws-1",
                normalized_requested_provider="anthropic",
                normalized_requested_model="claude-test",
                normalized_reasoning_effort="medium",
                normalized_thread_id="thread-1",
                normalized_message="Do the thing.",
                compacted_prior_messages=[],
                prior_messages_used=False,
                history_mode="none",
                connected_systems=[],
                tool_capabilities=[],
                availability_payload={"ai_ready": True},
                tools=[],
                direct_chat_credentials={},
                proactive_suggestions=[],
                tool_loop_session_key="session-cost-ceiling",
                fallback_reason=None,
                session_ctx=None,
                trace_context=None,
                resolved_chat_max_iterations=max_iterations,
                direct_tool_result_summary_system_message="Summarize tool results.",
            )
        )

    @staticmethod
    def _tool_call_result_event(*, cost_usd: float, call_id: str) -> dict:
        return {
            "type": "result",
            "reply": "",
            "usage_masked": {"provider": "anthropic", "model": "claude-test", "estimated_cost_usd": cost_usd},
            "provider": "anthropic",
            "model": "claude-test",
            "attempted_providers": "anthropic",
            "error": "",
            "tool_calls": [
                {"id": call_id, "name": "test__tool", "arguments": {}},
            ],
        }

    @staticmethod
    def _final_reply_result_event(*, cost_usd: float, reply: str = "All done.") -> dict:
        return {
            "type": "result",
            "reply": reply,
            "usage_masked": {"provider": "anthropic", "model": "claude-test", "estimated_cost_usd": cost_usd},
            "provider": "anthropic",
            "model": "claude-test",
            "attempted_providers": "anthropic",
            "error": "",
            "tool_calls": [],
        }

    def test_turn_under_ceiling_completes_normally(self) -> None:
        call_count = {"value": 0}

        def _stream(**kwargs):
            call_count["value"] += 1
            if call_count["value"] == 1:
                return iter([self._tool_call_result_event(cost_usd=0.01, call_id="call-1")])
            return iter([self._final_reply_result_event(cost_usd=0.01)])

        services = self._services(stream_fn=_stream)
        events = self._run(services=services, metadata={"provider": "anthropic", "run_cost_ceiling_usd": 5.0})

        self.assertEqual(call_count["value"], 2)
        final_payload = events[-1]["payload"]
        self.assertEqual(final_payload["reply"], "All done.")
        self.assertEqual(final_payload["error"], "")
        self.assertEqual(final_payload.get("interventions", []), [])

    def test_turn_exceeding_ceiling_halts_before_the_call_that_would_cross_it(self) -> None:
        call_count = {"value": 0}

        def _stream(**kwargs):
            call_count["value"] += 1
            # If this ever gets called a THIRD time, the ceiling failed to
            # stop the loop -- return an event that would make that failure
            # visible as a normal-looking completion rather than a crash, so
            # a regression here shows up as a wrong reply, not a KeyError.
            if call_count["value"] == 1:
                return iter([self._tool_call_result_event(cost_usd=0.6, call_id="call-1")])
            return iter([self._final_reply_result_event(cost_usd=0.6, reply="Should not have run.")])

        services = self._services(stream_fn=_stream)
        # Ceiling of $0.50: the first call alone (estimated $0.60) already
        # meets/exceeds it, so the SECOND call must never happen.
        events = self._run(services=services, metadata={"provider": "anthropic", "run_cost_ceiling_usd": 0.50})

        self.assertEqual(
            call_count["value"], 1, "the model was called again after the ceiling was already reached"
        )
        final_payload = events[-1]["payload"]
        self.assertNotEqual(final_payload["reply"], "Should not have run.")
        self.assertEqual(final_payload["error"], "run_cost_ceiling_reached")

    def test_halt_is_distinguishable_from_a_crash(self) -> None:
        """A ceiling halt must carry its own named intervention/error code --
        never collapse into the same generic failure vocabulary a real
        provider crash uses (provider_generation_failed / unknown_error)."""
        call_count = {"value": 0}

        def _stream(**kwargs):
            call_count["value"] += 1
            return iter([self._tool_call_result_event(cost_usd=1.0, call_id=f"call-{call_count['value']}")])

        services = self._services(stream_fn=_stream)
        events = self._run(services=services, metadata={"provider": "anthropic", "run_cost_ceiling_usd": 0.5})

        final_payload = events[-1]["payload"]
        self.assertEqual(final_payload["error"], "run_cost_ceiling_reached")
        self.assertNotIn(final_payload["error"], {"provider_generation_failed", "unknown_error", "max_tool_iterations_reached"})
        interventions = final_payload["interventions"]
        self.assertEqual(len(interventions), 1)
        self.assertEqual(interventions[0]["code"], "run_cost_ceiling_reached")
        self.assertEqual(interventions[0]["severity"], "warning")
        self.assertIn("ceiling", final_payload["reply"].lower())
        # Distinguishable from the loop's OTHER non-crash deliberate stop too.
        self.assertNotEqual(interventions[0]["code"], "tool_loop_detected")

    def test_default_applies_when_no_per_agent_value_is_set(self) -> None:
        resolved = direct_chat_generation_service._resolve_run_cost_ceiling_usd({})
        self.assertEqual(resolved, config_defaults_service.default_run_cost_ceiling_usd())
        self.assertGreater(resolved, 0.0)

        # And the loop actually uses it: with no override in metadata, a
        # single call costing more than the platform default must still
        # block the next one.
        call_count = {"value": 0}
        big_cost = config_defaults_service.default_run_cost_ceiling_usd() + 1.0

        def _stream(**kwargs):
            call_count["value"] += 1
            return iter([self._tool_call_result_event(cost_usd=big_cost, call_id=f"call-{call_count['value']}")])

        services = self._services(stream_fn=_stream)
        events = self._run(services=services, metadata={"provider": "anthropic"})  # no run_cost_ceiling_usd key

        self.assertEqual(call_count["value"], 1)
        self.assertEqual(events[-1]["payload"]["error"], "run_cost_ceiling_reached")


if __name__ == "__main__":
    unittest.main()
