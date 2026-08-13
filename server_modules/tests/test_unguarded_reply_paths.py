"""Every Sage turn's reply passes the response leak guard — on the way to a
channel AND on the way to disk.

THE BUG THIS LOCKS DOWN
-----------------------
`sage_agent_runtime_service.handle_sage_chat` applied
`_guard_sage_visible_reply` once, near the end of a ~2,300-line function. Two
branches returned hundreds of lines before reaching it:

    handle_sage_chat
      ├─ specialist mode == "local"            return {"message": _local_reply}   <- unguarded
      ├─ specialist mode == "cli_subscription" return {"message": _cli_reply}     <- unguarded
      ├─ action loop                           guard -> return
      └─ cloud fallthrough                     guard -> return

`sage_turn_adapter.execute_sage_turn` relays `result["message"]` verbatim and
`personal_channels_service` hands that straight to WhatsApp, Telegram, Signal,
iMessage and the OpenClaw-transported channels, so both branches reached a real
recipient with secrets, RED / private-memory markers and internal tool markup
intact. Both also persisted the same raw text through
`thread_service.record_assistant_turn`, which makes it durable and re-injectable
into a later prompt.

These are the two worst branches to miss: they are the turns that run on the
owner's OWN box against their own local model or CLI subscription, so their
output is the most likely of any to contain file contents or credentials.

WHY THE FIX IS TWO SEAMS AND NOT TWO MORE CALLS
-----------------------------------------------
A third branch added later must not be able to reintroduce this, so the guard
moved to the places every branch must pass through:

  * `handle_sage_chat` is now a thin wrapper around
    `_handle_sage_chat_unguarded` (the old body) and guards the returned
    message once — a new `return` inside the body cannot reach around it.
  * `thread_service.record_assistant_turn` guards `reply` before the write,
    covering persistence for every caller present and future.

`StructuralGuardSeamTests` below fails if either seam is dismantled.

THE OTHER HALF
--------------
The guard was corrected on 2026-08-08 after its high-entropy sweep redacted 17
legitimate `connector__action` tool names as if they were credentials. Every
leak assertion here is therefore paired with a tool-name assertion on the same
reply: `project_task__update` and `fleet__schedule_recurring_task` must come out
UNCHANGED, so this fix can never be "guard harder" at the cost of that bug.

And `record_user_turn` is deliberately NOT guarded. Redaction belongs on what is
written OUT, never on what is read IN — a person quoting a tool name at their own
agent must have it survive. `UserTurnIsNotRedactedTests` pins that direction.
"""

from __future__ import annotations

import ast
import asyncio
import inspect
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from server_modules import sage_agent_runtime_service
from server_modules import sage_turn_adapter
from server_modules import thread_service
from server_modules.specialist_runtime_context import SpecialistRuntimeContext


SECRET = "sk-ABCDEF0123456789abcdef"
RED_MARKER_LINE = "RED: the owner's private note about the supplier price"
PRIVATE_MEMORY_MARKER = "PRIVATE_MEMORY"
SAFE_TOOL_NAMES = ("project_task__update", "fleet__schedule_recurring_task")

LEAKY_REPLY = (
    f"Here is the key from the box: {SECRET}\n"
    f"{RED_MARKER_LINE}\n"
    f"{PRIVATE_MEMORY_MARKER} block follows\n"
    "I can call project_task__update and fleet__schedule_recurring_task for you."
)

# A user message that mentions a tool name — the READ-IN direction, which must
# never be redacted (that was the 2026-08-08 bug).
USER_MESSAGE = "please use project_task__update on my behalf"


def _run(coro):
    return asyncio.run(coro)


class _CapturedTurns:
    def __init__(self) -> None:
        self.rows: list[dict] = []

    async def upsert(self, **kwargs):
        self.rows.append(dict(kwargs))
        return {"turn": {"id": "turn-1"}}

    def content_for(self, role: str) -> str:
        for row in self.rows:
            if str(row.get("role") or "") == role:
                return str(row.get("content") or "")
        raise AssertionError(f"no {role} turn was persisted; roles seen: {[r.get('role') for r in self.rows]}")


def _drive_gateway_brain_turn(*, mode: str, runtime: str, reply: str):
    """Drive a BYO-brain turn through the SAME call a channel relay makes.

    `sage_turn_adapter.execute_sage_turn` is the single ingress every channel
    uses, and `SageTurnResult.message` is the exact string
    `personal_channels_service` hands to WhatsApp/Telegram/Signal/iMessage —
    so asserting on it is asserting on what a recipient receives, not on an
    internal dict.

    Persistence is captured one layer BELOW `record_assistant_turn` (at
    `control_plane_repository.upsert_agent_turn`) on purpose: the storage guard
    lives inside `record_assistant_turn`, so a test that patched that function
    would mock away the very thing it is checking.
    """
    dispatch_attr = (
        "_dispatch_local_gateway_brain"
        if mode == "local"
        else "_dispatch_cli_subscription_gateway_brain"
    )
    spec = SpecialistRuntimeContext(
        agent_install_id="agent-1",
        agent_label="Box Agent",
        agent_kind="specialist",
        persona="You are a specialist.",
        mode=mode,
        runtime=runtime,
        gateway_binding="gw-1",
    )
    mock_dispatch = AsyncMock(return_value=(reply, {"input_tokens": 1, "output_tokens": 1}, "model-x"))
    captured = _CapturedTurns()

    # patch.object with call-time module resolution throughout: sibling modules
    # importlib.reload() parts of server_modules, so a dotted-path patch can
    # bind a different module object than the code under test is holding.
    with (
        patch.object(sage_agent_runtime_service.sage_profile_service, "list_sage_profile", return_value={"profile": {}}),
        patch.object(sage_agent_runtime_service.workspace_context, "read_workspace_context_files", return_value={}),
        patch.object(sage_agent_runtime_service.sage_heartbeat_service, "build_sage_heartbeat_snapshot", new=AsyncMock(return_value={})),
        patch.object(sage_agent_runtime_service, "list_skill_definitions", return_value=[]),
        patch.object(sage_agent_runtime_service, "_resolve_cloud_provider", new=AsyncMock(return_value=("deepseek", {"api_key": "sk-workspace-default"}))),
        patch.object(sage_agent_runtime_service, dispatch_attr, new=mock_dispatch),
        patch.object(sage_agent_runtime_service, "persist_interaction"),
        patch.object(sage_agent_runtime_service.activity_ledger_service, "append_activity_event", new=AsyncMock()),
        # The Rust kernel binary is not built in test environments; its
        # decision is not what this test is about.
        patch.object(
            thread_service,
            "_enforce_thread_record_decision",
            return_value={"next_action": "create_thread_turn", "request_id": ""},
        ),
        patch.object(thread_service.control_plane_repository, "upsert_agent_turn", new=captured.upsert),
    ):
        result = _run(sage_turn_adapter.execute_sage_turn(
            workspace_id="ws-1",
            message=USER_MESSAGE,
            specialist_context=spec,
            channel_origin="whatsapp_personal",
            channel_sender_id="15551234",
        ))

    mock_dispatch.assert_awaited()
    return result.message, captured


class GatewayBrainReplyReachesChannelGuardedTests(unittest.TestCase):
    """The BYO-brain branches — the ones that run on the owner's own box."""

    def _assert_guarded(self, text: str, where: str) -> None:
        self.assertNotIn(SECRET, text, f"{where}: raw credential survived")
        self.assertNotIn("RED:", text, f"{where}: RED sensitivity marker survived")
        self.assertNotIn(PRIVATE_MEMORY_MARKER, text, f"{where}: private-memory marker survived")

    def _assert_tool_names_intact(self, text: str, where: str) -> None:
        for name in SAFE_TOOL_NAMES:
            self.assertIn(name, text, f"{where}: legitimate tool name {name} was eaten by the guard")

    def test_local_branch_reply_reaches_the_channel_guarded(self):
        message, _captured = _drive_gateway_brain_turn(mode="local", runtime="ollama", reply=LEAKY_REPLY)
        self._assert_guarded(message, "local branch -> channel")
        self._assert_tool_names_intact(message, "local branch -> channel")

    def test_local_branch_reply_is_persisted_guarded(self):
        _message, captured = _drive_gateway_brain_turn(mode="local", runtime="ollama", reply=LEAKY_REPLY)
        stored = captured.content_for("assistant")
        self._assert_guarded(stored, "local branch -> durable turn store")
        self._assert_tool_names_intact(stored, "local branch -> durable turn store")

    def test_cli_subscription_branch_reply_reaches_the_channel_guarded(self):
        message, _captured = _drive_gateway_brain_turn(mode="cli_subscription", runtime="claude_code", reply=LEAKY_REPLY)
        self._assert_guarded(message, "cli_subscription branch -> channel")
        self._assert_tool_names_intact(message, "cli_subscription branch -> channel")

    def test_cli_subscription_branch_reply_is_persisted_guarded(self):
        _message, captured = _drive_gateway_brain_turn(mode="cli_subscription", runtime="claude_code", reply=LEAKY_REPLY)
        stored = captured.content_for("assistant")
        self._assert_guarded(stored, "cli_subscription branch -> durable turn store")
        self._assert_tool_names_intact(stored, "cli_subscription branch -> durable turn store")

    def test_a_clean_gateway_reply_is_delivered_byte_for_byte(self):
        """The guard is a filter, not a rewriter: a reply with nothing to
        redact — tool names included — must arrive exactly as the box produced
        it, on both the channel and the storage seam."""
        clean = "Done. I used project_task__update and then fleet__schedule_recurring_task, both fine."
        for mode, runtime in (("local", "ollama"), ("cli_subscription", "claude_code")):
            with self.subTest(mode=mode):
                message, captured = _drive_gateway_brain_turn(mode=mode, runtime=runtime, reply=clean)
                self.assertEqual(message, clean)
                self.assertEqual(captured.content_for("assistant"), clean)

    def test_silence_stays_silence(self):
        """Silence is a decision, never a failure. An empty gateway reply must
        not acquire filler text on its way through either seam."""
        for mode, runtime in (("local", "ollama"), ("cli_subscription", "claude_code")):
            with self.subTest(mode=mode):
                message, _captured = _drive_gateway_brain_turn(mode=mode, runtime=runtime, reply="")
                self.assertEqual(message, "")


class UserTurnIsNotRedactedTests(unittest.TestCase):
    """Redact what is written OUT, never what is read IN."""

    def test_inbound_user_message_mentioning_a_tool_name_is_stored_verbatim(self):
        for mode, runtime in (("local", "ollama"), ("cli_subscription", "claude_code")):
            with self.subTest(mode=mode):
                _message, captured = _drive_gateway_brain_turn(mode=mode, runtime=runtime, reply=LEAKY_REPLY)
                self.assertEqual(captured.content_for("user"), USER_MESSAGE)

    def test_record_user_turn_does_not_call_the_storage_guard(self):
        source = inspect.getsource(thread_service.record_user_turn)
        self.assertNotIn(
            "guard_assistant_reply_for_storage",
            source,
            "record_user_turn is a READ-IN path and must never be redacted",
        )


class StorageGuardTests(unittest.TestCase):
    def test_record_assistant_turn_guards_before_the_write(self):
        captured = _CapturedTurns()
        with (
            patch.object(
                thread_service,
                "_enforce_thread_record_decision",
                return_value={"next_action": "create_thread_turn", "request_id": ""},
            ),
            patch.object(thread_service.control_plane_repository, "upsert_agent_turn", new=captured.upsert),
        ):
            _run(thread_service.record_assistant_turn(
                thread_id="t-1", tenant_id="tn-1", workspace_id="ws-1", session_id=None,
                actor={"user_id": "agent-1", "name": "Agent"},
                reply=LEAKY_REPLY, status="completed",
            ))
        stored = captured.content_for("assistant")
        self.assertNotIn(SECRET, stored)
        self.assertNotIn("RED:", stored)
        self.assertNotIn(PRIVATE_MEMORY_MARKER, stored)
        for name in SAFE_TOOL_NAMES:
            self.assertIn(name, stored)

    def test_storage_guard_is_idempotent(self):
        once = thread_service.guard_assistant_reply_for_storage(LEAKY_REPLY)
        twice = thread_service.guard_assistant_reply_for_storage(once)
        self.assertEqual(once, twice)

    def test_storage_guard_leaves_empty_empty(self):
        self.assertEqual(thread_service.guard_assistant_reply_for_storage(""), "")
        self.assertEqual(thread_service.guard_assistant_reply_for_storage(None), "")


class VisibleGuardIdempotenceTests(unittest.TestCase):
    """The wrapper re-guards replies the action loop already guarded. That is
    only safe if guarding twice equals guarding once."""

    def test_guarding_twice_equals_guarding_once(self):
        for sample in (
            LEAKY_REPLY,
            "clean text with project_task__update and fleet__schedule_recurring_task",
            "",
        ):
            with self.subTest(sample=sample[:32]):
                once, _ = sage_agent_runtime_service._guard_sage_visible_reply(sample)
                twice, _ = sage_agent_runtime_service._guard_sage_visible_reply(once)
                self.assertEqual(once, twice)


class StructuralGuardSeamTests(unittest.TestCase):
    """A future branch must not be able to bypass the guard.

    Behavioural tests only cover the branches that exist today; these assert the
    SHAPE that makes a new branch safe by construction.
    """

    @staticmethod
    def _module_tree() -> ast.Module:
        path = Path(sage_agent_runtime_service.__file__)
        return ast.parse(path.read_text())

    @staticmethod
    def _find_func(tree: ast.Module, name: str):
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
                return node
        raise AssertionError(f"{name} not found")

    def test_handle_sage_chat_is_a_guarding_wrapper(self):
        tree = self._module_tree()
        wrapper = self._find_func(tree, "handle_sage_chat")
        called = {
            node.func.id
            for node in ast.walk(wrapper)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        }
        self.assertIn("_handle_sage_chat_unguarded", called)
        self.assertIn("_guard_sage_visible_reply", called)

    def test_the_real_body_has_exactly_one_caller_and_it_is_the_wrapper(self):
        """If a caller ever reaches `_handle_sage_chat_unguarded` directly, the
        seam is gone and every branch is unguarded again."""
        tree = self._module_tree()
        wrapper = self._find_func(tree, "handle_sage_chat")
        wrapper_lines = range(wrapper.lineno, (wrapper.end_lineno or wrapper.lineno) + 1)
        call_lines = [
            node.lineno
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "_handle_sage_chat_unguarded"
        ]
        self.assertEqual(len(call_lines), 1, f"expected one call site, found {call_lines}")
        self.assertIn(call_lines[0], wrapper_lines)

    # Test files that reference "_handle_sage_chat_unguarded" only as a
    # string/AST target for their OWN structural (source-inspection) tests —
    # never a live call or import that could route a real reply around the
    # guard. A naive substring scan cannot tell "calls it" from "parses the
    # source and looks for its name", so these are named exclusions rather
    # than a smarter (AST-on-every-candidate-file) scanner, matching this
    # test's own existing exclusion of itself for the identical reason.
    # test_default_engine_credit_debit.py's own AST checks predate this file
    # noticing the collision; test_tool_honesty_guard_wiring.py (MAN-263)
    # added the second.
    _STRUCTURAL_INSPECTION_ONLY_FILES = {
        "test_default_engine_credit_debit.py",
        "test_tool_honesty_guard_wiring.py",
    }

    def test_no_other_module_reaches_the_unguarded_body(self):
        root = Path(sage_agent_runtime_service.__file__).parent
        offenders = []
        for path in root.rglob("*.py"):
            if path.name in {"sage_agent_runtime_service.py", Path(__file__).name}:
                continue
            if path.name in self._STRUCTURAL_INSPECTION_ONLY_FILES:
                continue
            if "_handle_sage_chat_unguarded" in path.read_text():
                offenders.append(str(path))
        self.assertEqual(offenders, [], "the unguarded body must stay private to its own module")

    def test_record_assistant_turn_guards_reply_in_source(self):
        source = inspect.getsource(thread_service.record_assistant_turn)
        self.assertIn(
            "guard_assistant_reply_for_storage",
            source,
            "the persistence seam lost its guard; raw model output can become durable again",
        )


if __name__ == "__main__":
    unittest.main()
