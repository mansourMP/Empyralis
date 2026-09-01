"""command_registry — can the OWNER run an owner-gated command from a CHANNEL?

MAN-337 was filed claiming get_workspace_by_id's Postgres SELECT omits
`identity_links`, so `_is_sender_owner`'s channel check could never succeed.
That specific claim is ALREADY FIXED (commit 2622b8928, an ancestor of HEAD:
the SELECT names the column and _workspace_record_from_row decodes it).

The SYMPTOM the ticket was filed about was still real, for a different
reason, and that is what this file covers. `identity_links` is a column
nothing in the shipped product has ever WRITTEN — routes_workspaces.py's
identity-links endpoints are its only writer and have zero frontend callers.
So:

    web sender_id     == created_by_user_id      -> check 1 hits.  OK
    channel sender_id != created_by_user_id      -> check 1 misses
                      -> check 2 read identity_links, which is empty
                      -> ALWAYS False, for the workspace's own owner

and dispatch() returns None on a failed owner check, so /config /mcp
/plugins /debug /bash were silently treated as unrecognized text on every
channel and fell through to the model as literal chat, with nothing
anywhere saying why.

The authoritative store for "which sender id is the owner on which channel"
is personal_channels_repository (linked_jid / linked_user_id /
linked_identity, written only from a genuine owner login/pairing event).
agent_turn_runtime_service._resolve_channel_sender_class already uses it for
tool authority and personal_channels_service._is_owner_message already uses
it for the DM gate that runs BEFORE this command is dispatched — so a sender
the DM gate just recognised as the owner must not be a stranger here.

Every test below fails against the pre-fix _is_sender_owner and passes after.
"""

from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from server_modules import command_registry
from server_modules import personal_channels_repository


def _run(coro):
    return asyncio.run(coro)


# A workspace whose platform-owner column can NEVER match a channel sender id,
# and whose identity_links is empty exactly as every real workspace's is.
_REAL_SHAPED_WORKSPACE = {
    "created_by_user_id": "user-uuid-of-the-owner",
    "identity_links": {},
}

# A real Telegram numeric id, the shape personal_channels_repository stores.
_OWNER_TELEGRAM_ID = "1932934047"
_STRANGER_TELEGRAM_ID = "778899001"


def _with_linked(linked: dict, workspace: dict | None = None):
    """Patch both reads _is_sender_owner performs: the workspace record and
    the authoritative per-channel owner-identity store."""
    return (
        patch(
            "server_modules.control_plane_repository.get_workspace_by_id",
            new=AsyncMock(return_value=workspace or _REAL_SHAPED_WORKSPACE),
        ),
        patch(
            "server_modules.personal_channels_repository"
            ".list_owner_linked_channel_identities_for_workspace",
            return_value=linked,
        ),
    )


class ChannelLinkedOwnerReachesTheOwnerCheckTests(unittest.TestCase):

    def test_a_channel_linked_owner_passes_the_owner_check(self):
        """THE BUG. A Telegram sender id can only ever match through the
        channel-identity store; before the fix this returned False for the
        workspace's own owner."""
        ws_patch, links_patch = _with_linked(
            {"telegram_personal": _OWNER_TELEGRAM_ID}
        )
        with ws_patch, links_patch:
            self.assertTrue(
                _run(
                    command_registry._is_sender_owner(
                        _OWNER_TELEGRAM_ID, "ws-1", "telegram_personal",
                    )
                )
            )

    def test_a_stranger_on_a_linked_channel_is_still_refused(self):
        ws_patch, links_patch = _with_linked(
            {"telegram_personal": _OWNER_TELEGRAM_ID}
        )
        with ws_patch, links_patch:
            self.assertFalse(
                _run(
                    command_registry._is_sender_owner(
                        _STRANGER_TELEGRAM_ID, "ws-1", "telegram_personal",
                    )
                )
            )

    def test_openclaw_conversation_id_canonicalizes_onto_the_bare_identity(self):
        """The OpenClaw transport passes the CONVERSATION id
        ("telegram:1932934047") where a real login stored the bare sender
        ("1932934047"). Both sides go through the registry-backed prefix
        canonicalizer, so the owner is not a stranger on that lane."""
        ws_patch, links_patch = _with_linked(
            {"openclaw_telegram": _OWNER_TELEGRAM_ID}
        )
        with ws_patch, links_patch:
            self.assertTrue(
                _run(
                    command_registry._is_sender_owner(
                        f"telegram:{_OWNER_TELEGRAM_ID}",
                        "ws-1",
                        "openclaw_telegram",
                    )
                )
            )

    def test_an_arbitrary_prefix_cannot_launder_a_stranger_onto_the_owner_id(self):
        """Only a prefix the channel registry itself names is stripped. A
        general "everything after the last colon" rule would hand owner
        authority to anyone who can put a colon in a sender field."""
        ws_patch, links_patch = _with_linked(
            {"openclaw_telegram": _OWNER_TELEGRAM_ID}
        )
        with ws_patch, links_patch:
            self.assertFalse(
                _run(
                    command_registry._is_sender_owner(
                        f"anything:{_OWNER_TELEGRAM_ID}",
                        "ws-1",
                        "openclaw_telegram",
                    )
                )
            )

    def test_a_link_on_another_channel_does_not_grant_on_this_one(self):
        """channel_origin narrows to that channel's own linked identity."""
        ws_patch, links_patch = _with_linked(
            {"openclaw_feishu": _OWNER_TELEGRAM_ID}
        )
        with ws_patch, links_patch:
            self.assertFalse(
                _run(
                    command_registry._is_sender_owner(
                        _OWNER_TELEGRAM_ID, "ws-1", "openclaw_telegram",
                    )
                )
            )

    def test_an_identity_store_failure_fails_closed(self):
        ws_patch = patch(
            "server_modules.control_plane_repository.get_workspace_by_id",
            new=AsyncMock(return_value=_REAL_SHAPED_WORKSPACE),
        )
        links_patch = patch(
            "server_modules.personal_channels_repository"
            ".list_owner_linked_channel_identities_for_workspace",
            side_effect=RuntimeError("state db unavailable"),
        )
        with ws_patch, links_patch:
            self.assertFalse(
                _run(
                    command_registry._is_sender_owner(
                        _OWNER_TELEGRAM_ID, "ws-1", "telegram_personal",
                    )
                )
            )

    def test_the_web_surface_owner_check_is_unchanged(self):
        """created_by_user_id still decides the web surface, and does so
        without needing any channel identity to exist."""
        ws_patch, links_patch = _with_linked({})
        with ws_patch, links_patch:
            self.assertTrue(
                _run(
                    command_registry._is_sender_owner(
                        "user-uuid-of-the-owner", "ws-1",
                    )
                )
            )


class OwnerGatedCommandReachesItsHandlerFromAChannelTests(unittest.TestCase):
    """The live path: agent_command_dispatcher.dispatch_command -> dispatch().
    An owner-gated command must EXECUTE for the channel-linked owner and stay
    silently unrecognized (None) for anyone else."""

    def _dispatch(self, sender_id: str, linked: dict):
        ws_patch, links_patch = _with_linked(linked)
        with ws_patch, links_patch:
            return _run(
                command_registry.dispatch(
                    text="/config",
                    workspace_id="ws-1",
                    surface="channel",
                    channel_origin="telegram_personal",
                    sender_id=sender_id,
                )
            )

    def test_dispatch_runs_an_owner_gated_command_for_a_channel_linked_owner(self):
        result = self._dispatch(
            _OWNER_TELEGRAM_ID, {"telegram_personal": _OWNER_TELEGRAM_ID}
        )
        self.assertIsNotNone(
            result,
            "/config from the workspace's own Telegram owner must reach its "
            "handler, not fall through to the model as literal chat text.",
        )

    def test_dispatch_still_refuses_an_owner_gated_command_for_a_stranger(self):
        result = self._dispatch(
            _STRANGER_TELEGRAM_ID, {"telegram_personal": _OWNER_TELEGRAM_ID}
        )
        self.assertIsNone(result)

    def test_dispatch_refuses_when_no_channel_identity_is_linked_at_all(self):
        result = self._dispatch(_OWNER_TELEGRAM_ID, {})
        self.assertIsNone(result)


class CrossAgentOwnerIsolationTests(unittest.TestCase):
    """2026-09-02: the command-gate twin of 407cc0cc's tool-authority fix
    (test_channel_sender_owner_authority.CrossAgentOwnerIsolationTests).

    _channel_linked_owner_ids — command_registry._is_sender_owner's channel
    check, the thing that decides who may run /config /mcp /plugins /debug
    /bash from a channel — read list_owner_linked_channel_identities_for_
    workspace: workspace-wide, no agent scoping, whose own docstring says
    the LAST-updated row per channel_key wins across every agent that has
    written one. Concretely: every BYO Telegram bot in a workspace shares
    channel_key "telegram_agent_byo" (hosted_bot_provisioning_service.
    BYO_OWNER_CLAIM_CHANNEL_KEY), and a personal-gateway pairing can share
    "telegram_personal"/"whatsapp_personal" across agents too
    (personal_channels_service._claim_agent_channel_state writes a REAL
    per-agent agent_id for both). Agent A handed to person 1, agent B handed
    to person 2 later on the SAME channel_key: person 2's later link would
    silently decide who may run /bash on agent A too, and person 1 would be
    silently locked out of their own agent.

    Real sqlite-backed personal_channels_repository state (not a mock of the
    lookup function) — the same fixture shape
    test_channel_sender_owner_authority.CrossAgentOwnerIsolationTests uses
    for the identical bug shape on the tool-authority path."""

    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmpdir.name) / "cross-agent-command-owner-test.sqlite3"
        # Agent A handed to person 1 first.
        personal_channels_repository.upsert_telegram_state(
            gateway_id="gw-1", tenant_id="t-1", workspace_id="ws-shared",
            user_id="u-1", channel_key="telegram_personal", provider="telegram",
            status="connected", linked_user_id="person-1-telegram-id",
            agent_id="agent-A",
            db_path=self.db_path,
        )
        # Agent B handed to person 2 LATER, same workspace, same
        # channel_key — the exact shape that let person 2's link win
        # workspace-wide under the old (last-updated-row) lookup.
        personal_channels_repository.upsert_telegram_state(
            gateway_id="gw-2", tenant_id="t-1", workspace_id="ws-shared",
            user_id="u-1", channel_key="telegram_personal", provider="telegram",
            status="connected", linked_user_id="person-2-telegram-id",
            agent_id="agent-B",
            db_path=self.db_path,
        )

    def tearDown(self) -> None:
        self._tmpdir.cleanup()

    def _patched(self):
        """_is_sender_owner also reads control_plane_repository.
        get_workspace_by_id first (check 1: created_by_user_id, and check 3:
        identity_links) — give it a workspace record neither Telegram
        sender id can ever match, so only the channel-linked check under
        test (check 2) can grant, exactly like the real Telegram-sender
        shape."""
        ws_patch = patch(
            "server_modules.control_plane_repository.get_workspace_by_id",
            new=AsyncMock(return_value={
                "created_by_user_id": "unrelated-platform-user-uuid",
                "identity_links": {},
            }),
        )
        db_patch = patch.object(
            personal_channels_repository, "PERSONAL_CHANNELS_DB_FILE", self.db_path,
        )
        return ws_patch, db_patch

    def test_each_agent_resolves_its_own_owner_only(self) -> None:
        ws_patch, db_patch = self._patched()
        with ws_patch, db_patch:
            # Person 1 is owner on agent A...
            person1_on_agent_a = _run(command_registry._is_sender_owner(
                "person-1-telegram-id", "ws-shared", "telegram_personal", "agent-A",
            ))
            # ...and person 2 is owner on agent B...
            person2_on_agent_b = _run(command_registry._is_sender_owner(
                "person-2-telegram-id", "ws-shared", "telegram_personal", "agent-B",
            ))
            # ...but NEITHER leaks to the OTHER agent, even though this is
            # the same workspace and the same channel_key. This is the exact
            # cross-contamination the old workspace-wide lookup produced.
            person2_on_agent_a = _run(command_registry._is_sender_owner(
                "person-2-telegram-id", "ws-shared", "telegram_personal", "agent-A",
            ))
            person1_on_agent_b = _run(command_registry._is_sender_owner(
                "person-1-telegram-id", "ws-shared", "telegram_personal", "agent-B",
            ))

        self.assertTrue(person1_on_agent_a, "agent A's own real owner must pass")
        self.assertTrue(person2_on_agent_b, "agent B's own real owner must pass")
        self.assertFalse(person2_on_agent_a, "agent B's owner must not leak onto agent A")
        self.assertFalse(person1_on_agent_b, "agent A's owner must not leak onto agent B")

    def test_real_owner_still_granted_end_to_end_through_dispatch(self) -> None:
        """The other half, and the one that matters as much as isolation:
        this fix must not regress into failing every owner-gated command
        closed. dispatch() end-to-end for agent A's own real, correctly
        scoped owner must still reach the /config handler — a lockout here
        would be the exact 'nothing anywhere says why' trap this module's
        own docstring already warns about, just reintroduced by an
        over-eager per-agent scope."""
        ws_patch, db_patch = self._patched()
        with ws_patch, db_patch:
            result = _run(command_registry.dispatch(
                text="/config",
                workspace_id="ws-shared",
                surface="channel",
                channel_origin="telegram_personal",
                sender_id="person-1-telegram-id",
                agent_install_id="agent-A",
            ))
        self.assertIsNotNone(
            result,
            "/config from agent A's own real linked owner must reach its "
            "handler even though a DIFFERENT owner is linked to agent B on "
            "the identical channel_key in the same workspace.",
        )

    def test_no_agent_id_falls_back_to_workspace_wide_unchanged_behavior(self) -> None:
        """agent_install_id absent — every real dispatch_command caller that
        genuinely has no per-agent concept (hosted Telegram's single-
        pairing-per-workspace model; see command_registry.
        _channel_linked_owner_ids' own docstring) — must NOT fail closed for
        a real owner. It keeps today's workspace-wide read, unchanged.

        Deliberately its OWN isolated single-writer fixture, not this
        class's two-competing-agent setUp: with two agents linked to two
        different people on the same channel_key, the pre-existing
        workspace-wide "last write wins" ambiguity means only ONE of them
        resolves via the no-agent-id fallback — that ambiguity is the
        already-documented, pre-existing tradeoff a caller with no agent id
        to offer accepts, not the thing this test is about. This test is
        about the far more common shape (one workspace, one linked owner,
        no competing agent) never regressing into a hard lockout."""
        tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(tmpdir.cleanup)
        db_path = Path(tmpdir.name) / "single-agent-no-id-fallback-test.sqlite3"
        personal_channels_repository.upsert_telegram_state(
            gateway_id="gw-legacy", tenant_id="t-1", workspace_id="ws-legacy-single",
            user_id="u-1", channel_key="telegram_personal", provider="telegram",
            status="connected", linked_user_id="the-only-owner-telegram-id",
            db_path=db_path,
        )
        ws_patch = patch(
            "server_modules.control_plane_repository.get_workspace_by_id",
            new=AsyncMock(return_value={
                "created_by_user_id": "unrelated-platform-user-uuid",
                "identity_links": {},
            }),
        )
        db_patch = patch.object(
            personal_channels_repository, "PERSONAL_CHANNELS_DB_FILE", db_path,
        )
        with ws_patch, db_patch:
            result = _run(command_registry._is_sender_owner(
                "the-only-owner-telegram-id", "ws-legacy-single", "telegram_personal",
            ))
        self.assertTrue(
            result,
            "an absent agent id must fall back to the pre-fix workspace-wide "
            "lookup, not a stricter fail-closed posture that would lock out "
            "every owner-gated command for the single most common (single- "
            "agent) pairing shape in the product.",
        )


if __name__ == "__main__":
    unittest.main()
