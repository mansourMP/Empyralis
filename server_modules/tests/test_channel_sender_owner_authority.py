"""The second half of the identity_links fix: sage_agent_runtime_service's
owner/audience tool-authority classification for a channel turn.

THE PROBLEM THIS CLOSES
------------------------
Fixing control_plane_repository.get_workspace_by_id's SELECT (so
workspace.identity_links is readable) does NOT restore owner tool-
authority on channels, because nothing in the shipped product has ever
WRITTEN to that column — routes_workspaces.py's identity-links settings
endpoints have zero frontend callers. Building a settings UI to feed it
would create a SECOND source of truth for a fact already held correctly
elsewhere: personal_channels_repository's linked_jid/linked_user_id/
linked_identity columns, populated by the real pairing/login path and
already trusted by personal_channels_service._is_owner_message for the
DM-policy gate that runs before a reply is even generated.

_resolve_channel_sender_class (sage_agent_runtime_service.py) now
consults personal_channels_repository — and ONLY personal_channels_
repository — as the sole authoritative source for this classification.
workspace.identity_links is never read here again.

WHAT IS ASSERTED
----------------
- A real, linked personal-channel identity (WhatsApp, Telegram, and the
  Signal/iMessage/WeChat/OpenClaw local-bridge family) resolves to
  "owner".
- An unlinked sender, and a workspace with nothing linked at all, resolve
  to "audience" — fail CLOSED, never fail-open.
- A lookup exception (the repository call itself blowing up) also fails
  CLOSED to "audience" — regression guard for a LATENT fail-open bug in
  the pre-fix code: the old inline logic's `except Exception: pass` left
  `_sender_class` at its pre-loop default of "owner" if anything inside
  the try block raised, for a real channel message. That is now `return
  "audience"` explicitly.
- personal_channels_repository.list_owner_linked_channel_identities_for_
  workspace itself: scoped correctly by workspace_id (a different
  workspace's linked identity must never leak in), covers all three
  channel families, and empty/missing input resolves to {} rather than
  raising.
"""

from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from server_modules import personal_channels_repository
from server_modules import sage_agent_runtime_service


def _run(coro):
    return asyncio.run(coro)


class WorkspaceScopedLinkedIdentityLookupTests(unittest.TestCase):
    """personal_channels_repository.list_owner_linked_channel_identities_
    for_workspace — direct, DB-backed tests against a throwaway temp
    SQLite file (never the developer's real ~/.empyralis/state personal-
    channels database)."""

    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmpdir.name) / "personal-channels-test.sqlite3"

    def tearDown(self) -> None:
        self._tmpdir.cleanup()

    def test_empty_workspace_id_returns_empty_dict(self) -> None:
        self.assertEqual(
            personal_channels_repository.list_owner_linked_channel_identities_for_workspace(
                "", db_path=self.db_path,
            ),
            {},
        )

    def test_workspace_with_nothing_linked_returns_empty_dict(self) -> None:
        self.assertEqual(
            personal_channels_repository.list_owner_linked_channel_identities_for_workspace(
                "ws-nothing-linked", db_path=self.db_path,
            ),
            {},
        )

    def test_whatsapp_telegram_and_local_bridge_all_resolve(self) -> None:
        personal_channels_repository.upsert_whatsapp_state(
            gateway_id="gw-1", tenant_id="t-1", workspace_id="ws-multi",
            user_id="u-1", channel_key="whatsapp_personal", provider="whatsapp",
            status="connected", linked_jid="1555550100@s.whatsapp.net",
            db_path=self.db_path,
        )
        personal_channels_repository.upsert_telegram_state(
            gateway_id="gw-1", tenant_id="t-1", workspace_id="ws-multi",
            user_id="u-1", channel_key="telegram_personal", provider="telegram",
            status="connected", linked_user_id="tg-owner-123",
            db_path=self.db_path,
        )
        personal_channels_repository.upsert_local_bridge_state(
            gateway_id="gw-1", tenant_id="t-1", workspace_id="ws-multi",
            user_id="u-1", channel_key="openclaw_signal", provider="signal",
            status="connected", linked_identity="+15555501234",
            db_path=self.db_path,
        )

        linked = personal_channels_repository.list_owner_linked_channel_identities_for_workspace(
            "ws-multi", db_path=self.db_path,
        )

        self.assertEqual(
            linked,
            {
                "whatsapp_personal": "1555550100@s.whatsapp.net",
                "telegram_personal": "tg-owner-123",
                "openclaw_signal": "+15555501234",
            },
        )

    def test_a_different_workspaces_linked_identity_never_leaks_in(self) -> None:
        personal_channels_repository.upsert_telegram_state(
            gateway_id="gw-1", tenant_id="t-1", workspace_id="ws-owner-of-this-data",
            user_id="u-1", channel_key="telegram_personal", provider="telegram",
            status="connected", linked_user_id="tg-owner-123",
            db_path=self.db_path,
        )

        linked = personal_channels_repository.list_owner_linked_channel_identities_for_workspace(
            "ws-a-completely-different-workspace", db_path=self.db_path,
        )

        self.assertEqual(linked, {})

    def test_unset_channels_are_simply_absent_not_empty_strings(self) -> None:
        """A row can exist (a paired-but-not-yet-linked gateway) with a
        NULL linked_jid — must not surface as a bogus channel_key: ''."""
        personal_channels_repository.upsert_whatsapp_state(
            gateway_id="gw-1", tenant_id="t-1", workspace_id="ws-unlinked",
            user_id="u-1", channel_key="whatsapp_personal", provider="whatsapp",
            status="connecting", linked_jid=None,
            db_path=self.db_path,
        )

        linked = personal_channels_repository.list_owner_linked_channel_identities_for_workspace(
            "ws-unlinked", db_path=self.db_path,
        )

        self.assertEqual(linked, {})


class ResolveChannelSenderClassTests(unittest.TestCase):
    """_resolve_channel_sender_class — the function
    _handle_sage_chat_unguarded now calls instead of the old inline
    workspace.identity_links lookup."""

    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmpdir.name) / "personal-channels-test.sqlite3"
        personal_channels_repository.upsert_telegram_state(
            gateway_id="gw-1", tenant_id="t-1", workspace_id="ws-real-owner",
            user_id="u-1", channel_key="telegram_personal", provider="telegram",
            status="connected", linked_user_id="tg-owner-123",
            db_path=self.db_path,
        )

    def tearDown(self) -> None:
        self._tmpdir.cleanup()

    def _patched_db_path(self):
        return patch.object(personal_channels_repository, "PERSONAL_CHANNELS_DB_FILE", self.db_path)

    def test_no_channel_context_is_owner_by_default(self) -> None:
        """Web/API sessions: no sender identity to doubt."""
        result = _run(sage_agent_runtime_service._resolve_channel_sender_class(
            channel_origin="", sender_id=None, workspace_id="ws-real-owner",
        ))
        self.assertEqual(result, "owner")

    def test_the_real_linked_owner_resolves_to_owner(self) -> None:
        with self._patched_db_path():
            result = _run(sage_agent_runtime_service._resolve_channel_sender_class(
                channel_origin="telegram_personal", sender_id="tg-owner-123", workspace_id="ws-real-owner",
            ))
        self.assertEqual(result, "owner")

    def test_an_unlinked_sender_on_the_same_channel_is_audience(self) -> None:
        with self._patched_db_path():
            result = _run(sage_agent_runtime_service._resolve_channel_sender_class(
                channel_origin="telegram_personal", sender_id="some-stranger-999", workspace_id="ws-real-owner",
            ))
        self.assertEqual(result, "audience")

    def test_a_workspace_with_nothing_linked_at_all_is_audience(self) -> None:
        """The core regression: a workspace whose owner has never (or
        cannot yet, on this backend) been linked must still deny — never
        silently grant owner authority just because there is no data to
        check against."""
        with self._patched_db_path():
            result = _run(sage_agent_runtime_service._resolve_channel_sender_class(
                channel_origin="telegram_personal", sender_id="tg-owner-123", workspace_id="ws-nothing-linked-here",
            ))
        self.assertEqual(result, "audience")

    def test_a_lookup_exception_fails_closed_to_audience(self) -> None:
        """Regression guard for the LATENT fail-open bug in the pre-fix
        code: the old inline `except Exception: pass` left _sender_class
        at its pre-loop "owner" default if the lookup itself raised, for
        a real channel message. Now explicit."""
        with patch.object(
            personal_channels_repository, "list_owner_linked_channel_identities_for_workspace",
            side_effect=RuntimeError("db unavailable"),
        ):
            result = _run(sage_agent_runtime_service._resolve_channel_sender_class(
                channel_origin="telegram_personal", sender_id="tg-owner-123", workspace_id="ws-real-owner",
            ))
        self.assertEqual(result, "audience")

    def test_workspace_identity_links_is_never_consulted(self) -> None:
        """Structural proof that the old source is truly gone, not just
        unused by coincidence in these fixtures: even a real, correctly-
        populated workspace.identity_links (get_workspace_by_id mocked to
        return it) must NOT make an unlinked-in-personal_channels_
        repository sender resolve as owner."""
        with (
            self._patched_db_path(),
            patch(
                "server_modules.control_plane_repository.get_workspace_by_id",
                return_value={
                    "identity_links": {
                        "telegram_personal": {"user_id": "tg-owner-123", "sender_hash": ""},
                    },
                },
            ),
        ):
            result = _run(sage_agent_runtime_service._resolve_channel_sender_class(
                channel_origin="telegram_personal", sender_id="tg-owner-123", workspace_id="ws-nothing-linked-here",
            ))
        # tg-owner-123 IS the linked owner per workspace.identity_links
        # (mocked above) but is NOT linked in personal_channels_
        # repository for THIS workspace — if the old source were still
        # consulted this would resolve "owner". It must not.
        self.assertEqual(result, "audience")


if __name__ == "__main__":
    unittest.main()
