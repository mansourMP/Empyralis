"""A turn's ENGINE must not be chosen by guessing at the customer's wording.

THE BUG (measured live, 2026-08-15, same request both ways):

    "Run this on your computer and reply with the exact output: ..."
      not promoted -> direct chat  -> shell runs on the box, exit 0
      promoted     -> outcome pack -> "Action policy blocked requested
                                       actions: shell.execute."

`_promote_turn_request_to_primary_engine_path` read the message text, counted
"serious task markers", and silently swapped engines. The destination engine
CANNOT run a shell command at all: `classify_runtime_action("shell.execute")`
returns destructive_risk=True on every target, and
`decide_runtime_action_execution` denies destructive unconditionally. So the
product's headline capability — run a command on your own computer — worked or
failed purely on how the sentence was phrased. Measured before the fix: 2 of 4.
After: 5 of 5.

The same promotion also dropped the AI provider (see
run_service._ensure_durable_turn_provider). Two independent, invisible failures
out of one wording-based guess.

Promotion survives where it is ASKED for, or structurally required.
"""

import unittest

from server_modules import agent_turn
from server_modules.agent_turn import AgentTurnRequest, TurnActor


def _request(message: str, **overrides) -> AgentTurnRequest:
    base = dict(
        tenant_id="tenant_x",
        workspace_id="ws_x",
        thread_id="thread_x",
        session_id="session_x",
        channel="web",
        actor=TurnActor(type="user", id="user_x", display_name=""),
        message=message,
        attachments=[],
        context_hints={},
        execution_mode="sync",
        response_mode="stream",
        machine_target=None,
        policy_context={},
    )
    base.update(overrides)
    return AgentTurnRequest(**base)


def _promoted(request: AgentTurnRequest) -> bool:
    result = agent_turn._promote_turn_request_to_primary_engine_path(request)
    return result.execution_mode == "durable"


class EnginePromotionTests(unittest.TestCase):
    def test_the_message_that_broke_it_stays_on_direct_chat(self):
        """The exact live failure. This phrasing tripped the marker count."""
        self.assertFalse(
            _promoted(
                _request(
                    "Run this on your computer and reply with the exact output: "
                    "uname -a && whoami && date -u"
                )
            )
        )

    def test_no_phrasing_of_a_shell_request_changes_the_engine(self):
        """The defect was that WORDING decided capability. Vary it hard —
        length, imperatives, sequence and outcome words, multiple clauses —
        and the engine must not move."""
        for message in [
            "run a command",
            "Please run the following command on the agent computer and then "
            "report the exact stdout, step by step, and finally summarise it.",
            "First, open a shell. Next, execute `hostname`. Finally, deliver "
            "the output as a deliverable report.",
            "execute this and produce a complete result: echo hi",
            "hi",
        ]:
            with self.subTest(message=message):
                self.assertFalse(_promoted(_request(message)))

    def test_an_explicit_request_still_promotes(self):
        """Removing a guess is not removing the capability."""
        self.assertTrue(
            _promoted(_request("anything", context_hints={"force_durable_run": True}))
        )
        self.assertTrue(
            _promoted(
                _request("anything", context_hints={"metadata": {"force_durable_run": True}})
            )
        )

    def test_attachments_still_promote(self):
        """Structural, not a guess about wording."""
        self.assertTrue(_promoted(_request("here", attachments=[object()])))

    def test_force_direct_chat_still_wins_over_everything(self):
        self.assertFalse(
            _promoted(
                _request(
                    "anything",
                    context_hints={"force_direct_chat": True, "force_durable_run": True},
                )
            )
        )

    def test_a_non_stream_request_is_left_alone(self):
        """Unchanged precondition — promotion only ever applied to sync+stream."""
        request = _request("run a command", execution_mode="durable", response_mode="artifact")
        self.assertIs(agent_turn._promote_turn_request_to_primary_engine_path(request), request)


class EnginePromotionSourceTests(unittest.TestCase):
    def test_the_wording_heuristic_is_not_reintroduced(self):
        """A behavioural test cannot catch this coming back under a new name:
        whoever re-adds a phrasing rule will add new markers too, and the cases
        above would still pass while a NEW phrasing silently swaps engines
        again. Assert the promotion function consults no text-shape helper.
        """
        import ast
        import inspect

        source = inspect.getsource(agent_turn._promote_turn_request_to_primary_engine_path)
        tree = ast.parse(source.lstrip())
        called = {
            node.func.id
            for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        }
        self.assertNotIn("_serious_task_marker_count", called)
        self.assertNotIn("_looks_like_lightweight_direct_chat", called)

        names = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}
        self.assertNotIn("SERIOUS_SEQUENCE_MARKERS", names)
        self.assertNotIn("SERIOUS_OUTCOME_MARKERS", names)


if __name__ == "__main__":
    unittest.main()
