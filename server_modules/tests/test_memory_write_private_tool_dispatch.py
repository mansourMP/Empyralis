"""Tests for the memory_write_private / memory_get_private tool dispatch --
skills_service.execute_single_direct_tool_call's ("memory", "write_private")
and ("memory", "get_private") branches, the model-facing surface for the
PER-PERSON private memory layer (see agent_private_memory_service.py).

THE CORE GUARANTEE THIS FILE PROVES: the tool's identity scoping comes ONLY
from session_metadata["user_id"] -- resolved server-side from the caller's
actual authenticated session before the tool body ever runs -- and NEVER
from argument_payload (what the model's tool call itself supplies). The
ToolDescriptor's own JSON schema (skills_service._builtin_tool_descriptors)
has no user_id property at all, so there is no field for a model to set;
this file goes further and proves that even if a hostile/confused model
call stuffs a "user_id" key into its arguments anyway, the dispatch code
never reads it -- the call that reaches the service layer is always scoped
by session_metadata's real identity. This is the same honesty posture
CLAUDE.md documents for tool_honesty_guard and agent_goals' attempt_count:
the partitioning decision is made by the FIRING CODE, never narrated or
supplied by the model.

Uses the REAL parse_tool_name (direct_chat_operator_binding_service) and
the REAL execute_single_direct_tool_call (skills_service) -- only
agent_private_memory_service's write/read functions are mocked, so this
exercises the actual dispatch chain a live tool call takes.
"""

from __future__ import annotations

import json
import unittest
from unittest.mock import patch

from server_modules import direct_chat_operator_binding_service, direct_tool_execution_service, skills_service


def _callbacks() -> direct_tool_execution_service.DirectToolExecutionCallbacks:
    return direct_tool_execution_service.DirectToolExecutionCallbacks(
        compact_step_detail=lambda value: None,
        titleize_direct_step_token=lambda value: str(value or ""),
        run_async_tool_call=lambda awaitable: awaitable,
        parse_tool_name=direct_chat_operator_binding_service.parse_tool_name,
        tool_arguments_payload=lambda payload: payload if isinstance(payload, dict) else {},
        parse_json_object_loose=lambda value: {},
        safe_positive_int=lambda value, default=0: int(value) if str(value or "").strip().isdigit() else default,
        normalize_reasoning_effort=lambda value: None,
        build_direct_local_tool_config=lambda connector_id, action_id, tool_input: ("", {}),
        format_direct_local_tool_result=lambda result: json.dumps(result, ensure_ascii=False),
        build_direct_tool_config=lambda connector_id, action_id, tool_input: {
            "connector": connector_id, "action": action_id, "input": tool_input,
        },
        format_direct_tool_result=lambda result: json.dumps(result, ensure_ascii=False),
        llm_task=lambda *args, **kwargs: {"ok": True},
        web_search=lambda query: [],
        web_fetch=lambda url: "",
        search_memory_notebook=lambda *args, **kwargs: {
            "results": [], "files_searched": 0, "errors": [], "status": "no_files", "message": "",
        },
        get_memory_notebook_excerpt=lambda *args, **kwargs: {},
    )


class ParseToolNameMappingTests(unittest.TestCase):
    def test_memory_write_private_maps_to_memory_write_private_action(self) -> None:
        self.assertEqual(
            direct_chat_operator_binding_service.parse_tool_name("memory_write_private"),
            ("memory", "write_private"),
        )

    def test_memory_get_private_maps_to_memory_get_private_action(self) -> None:
        self.assertEqual(
            direct_chat_operator_binding_service.parse_tool_name("memory_get_private"),
            ("memory", "get_private"),
        )


class WritePrivateDispatchTests(unittest.TestCase):
    def _call(self, *, arguments: dict, session_ctx: dict) -> dict:
        raw = skills_service.execute_single_direct_tool_call(
            tool_call={"name": "memory_write_private", "arguments": arguments},
            workspace_id="ws-1",
            thread_id="t-1",
            session_ctx=session_ctx,
            callbacks=_callbacks(),
        )
        return json.loads(raw)

    def test_no_resolved_user_identity_refuses_and_never_calls_the_service(self) -> None:
        with patch(
            "server_modules.agent_private_memory_service.write_private_memory_note",
        ) as mocked_write:
            with self.assertRaises(RuntimeError):
                self._call(
                    arguments={"content": "I like short replies."},
                    session_ctx={"authority_tier": "owner"},  # no user_id at all
                )
        self.assertEqual(mocked_write.call_count, 0)

    def test_user_id_is_taken_from_session_metadata_only_exactly_once(self) -> None:
        with patch(
            "server_modules.agent_private_memory_service.write_private_memory_note",
            return_value={"content": "I like short replies.", "redacted": False, "revision_recorded": True},
        ) as mocked_write:
            payload = self._call(
                arguments={"content": "I like short replies."},
                session_ctx={"authority_tier": "owner", "user_id": "user-a"},
            )
        self.assertTrue(payload["ok"])
        self.assertEqual(mocked_write.call_count, 1)
        _, kwargs = mocked_write.call_args
        self.assertEqual(kwargs["user_id"], "user-a")

    def test_a_user_id_smuggled_into_arguments_is_ignored_real_identity_wins(self) -> None:
        """The ToolDescriptor's JSON schema has no user_id property, so a
        well-behaved model can never send one -- but the dispatch code must
        not accidentally read argument_payload.get('user_id') either, in
        case a future edit or a malformed/hostile call includes it anyway.
        The identity that lands in the service call must always be the
        one resolved from session_metadata, never the one in the payload."""
        with patch(
            "server_modules.agent_private_memory_service.write_private_memory_note",
            return_value={"content": "x", "redacted": False, "revision_recorded": True},
        ) as mocked_write:
            self._call(
                arguments={"content": "I like short replies.", "user_id": "user-b-impersonation-attempt"},
                session_ctx={"authority_tier": "owner", "user_id": "user-a"},
            )
        self.assertEqual(mocked_write.call_count, 1)
        _, kwargs = mocked_write.call_args
        self.assertEqual(kwargs["user_id"], "user-a")
        self.assertNotEqual(kwargs["user_id"], "user-b-impersonation-attempt")

    def test_empty_content_refuses_and_never_calls_the_service(self) -> None:
        with patch(
            "server_modules.agent_private_memory_service.write_private_memory_note",
        ) as mocked_write:
            with self.assertRaises(RuntimeError):
                self._call(
                    arguments={"content": "   "},
                    session_ctx={"authority_tier": "owner", "user_id": "user-a"},
                )
        self.assertEqual(mocked_write.call_count, 0)


class GetPrivateDispatchTests(unittest.TestCase):
    def _call(self, *, session_ctx: dict) -> dict:
        raw = skills_service.execute_single_direct_tool_call(
            tool_call={"name": "memory_get_private", "arguments": {}},
            workspace_id="ws-1",
            thread_id="t-1",
            session_ctx=session_ctx,
            callbacks=_callbacks(),
        )
        return json.loads(raw)

    def test_no_resolved_user_identity_returns_empty_without_calling_the_service(self) -> None:
        with patch(
            "server_modules.agent_private_memory_service.get_private_memory_note",
        ) as mocked_get:
            payload = self._call(session_ctx={"authority_tier": "owner"})
        self.assertEqual(payload, {"content": "", "exists": False, "reason": "no_resolved_user_identity"})
        self.assertEqual(mocked_get.call_count, 0)

    def test_reads_use_the_session_users_identity_exactly_once(self) -> None:
        with patch(
            "server_modules.agent_private_memory_service.get_private_memory_note",
            return_value={"content": "user-a's saved preference."},
        ) as mocked_get:
            payload = self._call(session_ctx={"authority_tier": "owner", "user_id": "user-a"})
        self.assertEqual(payload, {"content": "user-a's saved preference.", "exists": True})
        self.assertEqual(mocked_get.call_count, 1)
        _, kwargs = mocked_get.call_args
        self.assertEqual(kwargs["user_id"], "user-a")

    def test_two_different_sessions_never_share_a_call_to_the_same_note(self) -> None:
        """Simulates user-a and user-b each reading their own private note
        in the same process -- proves the dispatch always threads the
        CURRENT session's identity through, never a stale/cached one from
        a previous call."""
        calls = []

        def _fake_get(_workspace_id, *, agent_install_id, user_id):
            calls.append(user_id)
            return {"content": f"note for {user_id}"}

        with patch(
            "server_modules.agent_private_memory_service.get_private_memory_note",
            side_effect=_fake_get,
        ):
            payload_a = self._call(session_ctx={"authority_tier": "owner", "user_id": "user-a"})
            payload_b = self._call(session_ctx={"authority_tier": "owner", "user_id": "user-b"})

        self.assertEqual(payload_a["content"], "note for user-a")
        self.assertEqual(payload_b["content"], "note for user-b")
        self.assertEqual(calls, ["user-a", "user-b"])


class ToolDescriptorAudienceSafetyTests(unittest.TestCase):
    """Both private-memory tools must be blocked from the external/audience
    tier -- an external caller has no internal user_id to scope to at all,
    so exposing either tool to that tier would be a dead control."""

    def test_both_private_memory_tools_are_registered_and_not_audience_safe(self) -> None:
        descriptors = {d.tool_name: d for d in skills_service._builtin_tool_descriptors()}
        self.assertIn("memory_write_private", descriptors)
        self.assertIn("memory_get_private", descriptors)
        self.assertFalse(descriptors["memory_write_private"].audience_safe)
        self.assertFalse(descriptors["memory_get_private"].audience_safe)

    def test_neither_tool_declares_a_user_id_parameter(self) -> None:
        """The schema itself must never offer the model a way to set who
        this write/read is for."""
        descriptors = {d.tool_name: d for d in skills_service._builtin_tool_descriptors()}
        for name in ("memory_write_private", "memory_get_private"):
            properties = descriptors[name].parameters.get("properties", {})
            self.assertNotIn("user_id", properties)


if __name__ == "__main__":
    unittest.main()


class PrivateMemoryUsesTheRealProductionSessionShapeTests(unittest.TestCase):
    """The shape a LIVE turn actually passes — not the one a fixture invents.

    Every test above builds `session_ctx={"user_id": ...}` by hand, flat. A
    real turn does not look like that. `sage_agent_runtime_service`'s turn
    builder produces:

        session_ctx = {
            "metadata": {"user_id": actor_user_id or None, ...},
            "sender_id": actor_user_id or "",
            ...
        }

    and `skills_service` sets `session_metadata = session_ctx` — the WHOLE
    dict. So the original `session_metadata.get("user_id")` was None on
    every real turn, and both private-memory tools raised "requires a
    resolved user identity" from the day they shipped (2026-08-12) while
    their own dispatch tests stayed green.

    That is this codebase's documented "a mock protects a seam, not a path"
    failure in its purest form: a fixture that invents its own input cannot
    notice that the real input is shaped differently. These tests build the
    context the way production does, so they fail if the resolver regresses.
    """

    def _write(self, *, session_ctx: dict):
        return skills_service.execute_single_direct_tool_call(
            tool_call={
                "name": "memory_write_private",
                "arguments": {"content": "I like short replies."},
            },
            workspace_id="ws-1",
            thread_id="t-1",
            session_ctx=session_ctx,
            callbacks=_callbacks(),
        )

    def _production_session_ctx(self, user_id):
        """Mirrors sage_agent_runtime_service's own turn builder."""
        return {
            "authority_tier": "owner",
            "metadata": {
                "source": "sage_chat",
                "surface": "sage",
                "agent_scope": "sage",
                "user_id": user_id,
                "envelope": None,
            },
            "sender_id": user_id or "",
        }

    def test_a_real_turns_nested_identity_is_resolved(self) -> None:
        with patch(
            "server_modules.agent_private_memory_service.write_private_memory_note",
            return_value={"content": "x", "redacted": False, "revision_recorded": True},
        ) as mocked_write:
            raw = self._write(session_ctx=self._production_session_ctx("user-real"))
        self.assertTrue(json.loads(raw)["ok"])
        self.assertEqual(mocked_write.call_count, 1)
        _, kwargs = mocked_write.call_args
        self.assertEqual(kwargs["user_id"], "user-real")

    def test_the_nested_identity_wins_over_a_stale_flat_one(self) -> None:
        """metadata.user_id is what the turn builder sets deliberately.

        If the two ever disagree, the deliberate one is the answer — a flat
        key left over from an older context must not silently redirect a
        private note into somebody else's partition.
        """
        ctx = self._production_session_ctx("user-real")
        ctx["user_id"] = "user-stale"
        with patch(
            "server_modules.agent_private_memory_service.write_private_memory_note",
            return_value={"content": "x", "redacted": False, "revision_recorded": True},
        ) as mocked_write:
            self._write(session_ctx=ctx)
        self.assertEqual(mocked_write.call_count, 1)
        _, kwargs = mocked_write.call_args
        self.assertEqual(kwargs["user_id"], "user-real")

    def test_an_anonymous_real_turn_still_refuses(self) -> None:
        """A channel turn with no internal user (an external customer) has
        no private partition to write to, and must still say so rather than
        inventing one. The production shape carries the key with a None
        value, which is not the same as the key being absent."""
        with patch(
            "server_modules.agent_private_memory_service.write_private_memory_note",
        ) as mocked_write:
            with self.assertRaises(RuntimeError):
                self._write(session_ctx=self._production_session_ctx(None))
        self.assertEqual(mocked_write.call_count, 0)
