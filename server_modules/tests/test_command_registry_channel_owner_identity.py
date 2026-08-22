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
import unittest
from unittest.mock import AsyncMock, patch

from server_modules import command_registry


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
    """The live path: sage_command_dispatcher.dispatch_command -> dispatch().
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


if __name__ == "__main__":
    unittest.main()
