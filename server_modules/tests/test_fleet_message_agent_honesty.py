"""Regression cover for C1 (docs/design/audit-silent-failures.md):
fleet_message_agent used to write into the target agent's
install_metadata.fleet_inbox (which nothing in the turn-building pipeline
ever reads back) and unconditionally return {"ok": True, "status":
"enqueued"} -- so any caller, including an external agent connected via
the empyralis_message_agent MCP tool, was told its message was delivered
when it never would be.

The fix is honesty, not delivery: fleet_message_agent must now ALWAYS
return ok: False with an explicit, model-facing explanation, for every
caller -- the raw function, the tool-broker's fleet__message_agent action
(skills_service.py), and the empyralis_message_agent MCP tool
(mcp_server.py) -- since all three route through this one function.
"""

from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, patch

from server_modules import fleet_tools


class FleetMessageAgentHonestyTests(unittest.IsolatedAsyncioTestCase):
    async def test_always_returns_ok_false_even_for_a_real_agent(self) -> None:
        """Even if the target agent genuinely exists and the write would
        have succeeded under the old code, the call must still fail
        honestly -- the point is that delivery never happens regardless."""
        with patch(
            "server_modules.agent_registry_repository.get_workspace_agent_install_bundle",
            new=AsyncMock(return_value={"id": "ainstall_real1", "metadata": {}}),
        ) as bundle_mock, patch(
            "server_modules.agent_registry_repository.update_workspace_agent_install",
            new=AsyncMock(),
        ) as update_mock:
            result = await fleet_tools.fleet_message_agent(
                actor_id="actor-1",
                workspace_id="workspace-1",
                tenant_id="tenant-1",
                agent_id="ainstall_real1",
                message="please do the thing",
            )

        self.assertFalse(result.get("ok"))
        self.assertIn("not implemented", result.get("error", "").lower())
        # No delivery attempt is even made anymore -- no lookup, no write,
        # no fleet_inbox mutation. A refusal that still silently touched
        # the DB would be its own smaller dishonesty.
        bundle_mock.assert_not_called()
        update_mock.assert_not_called()

    async def test_error_message_tells_the_model_what_to_do_instead(self) -> None:
        result = await fleet_tools.fleet_message_agent(
            actor_id="actor-1",
            workspace_id="workspace-1",
            agent_id="ainstall_x",
            message="hello",
        )
        error = result.get("error", "").lower()
        self.assertIn("not implemented", error)
        self.assertIn("task", error)
        self.assertNotIn("enqueued", result.get("status", ""))

    async def test_missing_agent_id_still_fails_with_the_original_validation_error(self) -> None:
        result = await fleet_tools.fleet_message_agent(
            actor_id="actor-1", workspace_id="workspace-1", agent_id="", message="hi",
        )
        self.assertFalse(result.get("ok"))
        self.assertEqual(result.get("error"), "agent_id is required")

    async def test_missing_message_still_fails_with_the_original_validation_error(self) -> None:
        result = await fleet_tools.fleet_message_agent(
            actor_id="actor-1", workspace_id="workspace-1", agent_id="ainstall_x", message="",
        )
        self.assertFalse(result.get("ok"))
        self.assertEqual(result.get("error"), "message is required")

    async def test_never_returns_ok_true_status_enqueued_shape_again(self) -> None:
        """Direct regression guard against the exact old success shape --
        {"ok": True, "status": "enqueued"} must never come back out of
        this function again, for any input."""
        for agent_id, message in [("a1", "m1"), ("ainstall_deadbeef", "urgent!!"), ("x", "y")]:
            result = await fleet_tools.fleet_message_agent(
                actor_id="actor-1", workspace_id="workspace-1", agent_id=agent_id, message=message,
            )
            self.assertNotEqual(
                result, {"ok": True, "agent_id": agent_id, "status": "enqueued"},
            )
            self.assertFalse(result.get("ok"))


class FleetMessageAgentToolBrokerSurfaceTests(unittest.TestCase):
    """The tool-calling schema shown to the model on every turn
    (skills_service.py's fleet__message_agent ToolDescriptor) must itself
    be honest -- a model choosing tools by description should not be told
    this delivers a message."""

    def test_tool_descriptor_description_says_not_implemented(self) -> None:
        import inspect

        from server_modules import skills_service

        source = inspect.getsource(skills_service)
        assert 'tool_name="fleet__message_agent"' in source
        idx = source.index('tool_name="fleet__message_agent"')
        snippet = source[idx: idx + 700]
        self.assertIn("Not implemented", snippet)
        self.assertIn("always returns ok: false", snippet.lower())

    def test_mcp_tool_docstring_says_not_implemented(self) -> None:
        """The external-facing empyralis_message_agent MCP tool -- what a
        Claude Code/Claude Desktop/ChatGPT client sees when it lists this
        platform's tools -- must not claim delivery either."""
        import inspect

        import mcp_server

        source = inspect.getsource(mcp_server)
        assert "async def empyralis_message_agent" in source
        idx = source.index("async def empyralis_message_agent")
        snippet = source[idx: idx + 400]
        self.assertIn("Not implemented", snippet)
        self.assertIn("always returns ok: false", snippet.lower())


if __name__ == "__main__":
    unittest.main()
