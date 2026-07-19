"""FIX 2 regression cover: agent_specialist_repository._assert_unique_
inbound_channel_owners is the soft pre-check behind the generic specialist
channel-bind endpoints (PUT /agent-registry/specialists/{id}/channels, PATCH
/agent-registry/specialists/{id}, POST /agent-registry/specialists), the
literal source of CHANNEL_OWNERSHIP_CONFLICT_MESSAGE ("This channel already
has an inbound owner.") that agent_registry_api._raise_specialist_write_error
turns into a real HTTP 409.

Before this fix the message never named which agent already owned the
channel. Now it does when a label is cheaply resolvable on the same
connection/transaction (see _channel_ownership_conflict_message), falling
back to the original generic message on any lookup miss or failure -- a
label lookup must never be able to block the conflict error itself.
"""

from __future__ import annotations

import asyncio
import unittest

from server_modules import agent_specialist_repository as specialist


def _run(coro):
    return asyncio.run(coro)


class _FakeConnection:
    """fetchrow dispatches on which query it is asked: the conflict lookup
    (agent_channel_bindings) vs the owner-label lookup
    (workspace_agent_installs)."""

    def __init__(self, *, conflict_row=None, label_row=None, label_lookup_raises=False):
        self.conflict_row = conflict_row
        self.label_row = label_row
        self.label_lookup_raises = label_lookup_raises
        self.queries: list[str] = []

    async def fetchrow(self, query, *args):
        self.queries.append(query)
        if "agent_channel_bindings" in query:
            return self.conflict_row
        if "workspace_agent_installs" in query:
            if self.label_lookup_raises:
                raise RuntimeError("db down")
            return self.label_row
        raise AssertionError(f"unexpected query: {query}")


def _slack_binding(endpoint_key: str = "C1") -> dict:
    return {"enabled": True, "is_inbound_owner": True, "endpoint_key": endpoint_key}


class AssertUniqueInboundChannelOwnersTests(unittest.TestCase):
    def test_no_conflict_passes_silently(self) -> None:
        connection = _FakeConnection(conflict_row=None)
        _run(specialist._assert_unique_inbound_channel_owners(
            connection, install_id="agent-a", tenant_id="t1", workspace_id="w1",
            channel_bindings={"slack": _slack_binding()},
        ))  # no exception raised

    def test_ignores_channel_keys_outside_the_inbound_owner_set(self) -> None:
        """A channel_key not in _INBOUND_OWNER_CHANNEL_KEYS must never even
        query for a conflict -- e.g. a connector-only key."""
        connection = _FakeConnection(conflict_row={"agent_install_id": "agent-b"})
        _run(specialist._assert_unique_inbound_channel_owners(
            connection, install_id="agent-a", tenant_id="t1", workspace_id="w1",
            channel_bindings={"not_a_real_channel_key": _slack_binding()},
        ))
        self.assertEqual(connection.queries, [])

    def test_raises_generic_message_when_conflict_found_but_label_unresolvable(self) -> None:
        connection = _FakeConnection(conflict_row={"agent_install_id": "agent-b"}, label_row=None)
        with self.assertRaises(specialist.ChannelOwnershipConflictError) as ctx:
            _run(specialist._assert_unique_inbound_channel_owners(
                connection, install_id="agent-a", tenant_id="t1", workspace_id="w1",
                channel_bindings={"slack": _slack_binding()},
            ))
        self.assertEqual(str(ctx.exception), specialist.CHANNEL_OWNERSHIP_CONFLICT_MESSAGE)

    def test_raises_enriched_message_naming_the_owning_agent_when_resolvable(self) -> None:
        connection = _FakeConnection(
            conflict_row={"agent_install_id": "agent-b"}, label_row={"label": "Support Bot"},
        )
        with self.assertRaises(specialist.ChannelOwnershipConflictError) as ctx:
            _run(specialist._assert_unique_inbound_channel_owners(
                connection, install_id="agent-a", tenant_id="t1", workspace_id="w1",
                channel_bindings={"slack": _slack_binding()},
            ))
        message = str(ctx.exception)
        self.assertIn("Support Bot", message)
        self.assertIn("one agent at a time", message.lower())

    def test_label_lookup_failure_falls_back_to_the_generic_message(self) -> None:
        """A label-lookup hiccup must never block the conflict error itself
        -- best-effort enrichment only."""
        connection = _FakeConnection(conflict_row={"agent_install_id": "agent-b"}, label_lookup_raises=True)
        with self.assertRaises(specialist.ChannelOwnershipConflictError) as ctx:
            _run(specialist._assert_unique_inbound_channel_owners(
                connection, install_id="agent-a", tenant_id="t1", workspace_id="w1",
                channel_bindings={"slack": _slack_binding()},
            ))
        self.assertEqual(str(ctx.exception), specialist.CHANNEL_OWNERSHIP_CONFLICT_MESSAGE)

    def test_disabled_or_non_owner_bindings_never_trigger_a_conflict_check(self) -> None:
        connection = _FakeConnection(conflict_row={"agent_install_id": "agent-b"})
        _run(specialist._assert_unique_inbound_channel_owners(
            connection, install_id="agent-a", tenant_id="t1", workspace_id="w1",
            channel_bindings={"slack": {"enabled": False, "is_inbound_owner": True, "endpoint_key": "C1"}},
        ))
        _run(specialist._assert_unique_inbound_channel_owners(
            connection, install_id="agent-a", tenant_id="t1", workspace_id="w1",
            channel_bindings={"slack": {"enabled": True, "is_inbound_owner": False, "endpoint_key": "C1"}},
        ))
        self.assertEqual(connection.queries, [])


if __name__ == "__main__":
    unittest.main()
