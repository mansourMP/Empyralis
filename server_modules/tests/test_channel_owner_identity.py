"""Owner identity on the OpenClaw lane — the fact that makes owner authority
reachable from a channel at all.

THE BUG THIS COVERS, proven live on a real Telegram DM 2026-08-15:

    _channel_owner_linked_id branched on exactly two channel keys,
    WHATSAPP_PERSONAL_CHANNEL_KEY and TELEGRAM_PERSONAL_CHANNEL_KEY, and
    returned "" for everything else. The 2026-08-14 cutover moved every live
    channel onto `openclaw_*`, which LOCAL_BRIDGE_PERSONAL_CHANNELS carries,
    so it returned "" for every real message in the product:

      _channel_owner_linked_id -> ""       no linked identity can exist
        -> _is_owner_message False          (is_self_chat is hardcoded False
                                             on this transport BY DESIGN, so
                                             it is the only other route)
        -> "[Telegram · DM · from <name> — NOT your owner]"
        -> _resolve_channel_sender_class "audience" -> no shell/hardware

    _handle_local_bridge_gateway_channel_inbound was the other half: it
    passed existing_state=None unconditionally, so even a populated column
    would never have been read.

These tests hold BOTH halves plus the write path, and every one of them fails
against HEAD's version of the two modules (verified by swapping the pre-fix
files in with `git show HEAD:<path>`).

Two properties are load-bearing beyond "the owner is recognised", and each has
its own test, because getting either wrong turns a lockout into a stranger
holding shell and hardware tools:

  * The link is per (gateway, channel, AGENT) — a second agent on the same
    Agent Computer must not inherit the first agent's owner.
  * The link may only be established by a deliberate owner action, never
    derived from an inbound message's own sender fields, and it must survive
    every subsequent message (the per-message state sync used to clobber
    exactly this column's WhatsApp/Telegram equivalents — see
    _resolve_linked_identity_for_sync's docstring).
"""

from __future__ import annotations

import importlib
import tempfile
import unittest
from pathlib import Path
from typing import Any, Dict
from unittest.mock import patch

from server_modules import personal_channels_service, personal_channels_repository


CHANNEL_KEY = "openclaw_telegram"
OWNER_SENDER_ID = "1932934047"
STRANGER_SENDER_ID = "5550001111"


class _Base(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        global personal_channels_service, personal_channels_repository
        personal_channels_service = importlib.import_module("server_modules.personal_channels_service")
        personal_channels_repository = importlib.import_module("server_modules.personal_channels_repository")

        self.tmpdir = tempfile.TemporaryDirectory()
        db_path = Path(self.tmpdir.name) / "personal-channels.sqlite3"
        personal_channels_repository.init_personal_channels_db(db_path)
        self.db_patcher = patch.object(personal_channels_repository, "PERSONAL_CHANNELS_DB_FILE", db_path)
        self.db_patcher.start()

        self.gateway_id = "gw-owner-identity"
        self.agent_id = "ainstall_owner_a"
        self.other_agent_id = "ainstall_owner_b"
        self.registration: Dict[str, Any] = {
            "gateway_id": self.gateway_id,
            "workspace_id": "ws-owner",
            "tenant_id": "tenant-owner",
            "user_id": "user-owner",
        }

    def tearDown(self) -> None:
        self.db_patcher.stop()
        self.tmpdir.cleanup()

    def _link(self, *, agent_id: str | None = None, sender_id: str | None = OWNER_SENDER_ID):
        return personal_channels_service.set_personal_channel_owner_identity(
            registration=self.registration,
            gateway_id=self.gateway_id,
            channel_key=CHANNEL_KEY,
            agent_id=agent_id or self.agent_id,
            sender_id=sender_id,
        )

    def _state(self, *, agent_id: str | None = None):
        return personal_channels_repository.get_local_bridge_state(
            self.gateway_id, channel_key=CHANNEL_KEY, agent_id=agent_id or self.agent_id,
        )


class ChannelOwnerLinkedIdTests(_Base):
    """The read that returned "" for every live channel."""

    async def test_reads_the_local_bridge_linked_identity(self) -> None:
        """THE bug. Every OpenClaw-transported key is in
        LOCAL_BRIDGE_PERSONAL_CHANNELS, and the column has existed since that
        table was created — only the branch reading it was missing."""
        resolved = personal_channels_service._channel_owner_linked_id(
            channel_key=CHANNEL_KEY, state={"linked_identity": OWNER_SENDER_ID},
        )
        self.assertEqual(resolved, OWNER_SENDER_ID)

    async def test_every_live_channel_key_can_resolve_an_owner(self) -> None:
        """Not just Telegram. A per-channel fix would leave the identical
        hole open on the other twenty-three."""
        for channel_key in sorted(personal_channels_service.LOCAL_BRIDGE_PERSONAL_CHANNELS):
            with self.subTest(channel_key=channel_key):
                self.assertEqual(
                    personal_channels_service._channel_owner_linked_id(
                        channel_key=channel_key, state={"linked_identity": OWNER_SENDER_ID},
                    ),
                    OWNER_SENDER_ID,
                )

    async def test_no_link_means_no_owner(self) -> None:
        """Fails closed. An unset link must never read as "everyone"."""
        for state in ({}, {"linked_identity": ""}, {"linked_identity": None}, None):
            with self.subTest(state=state):
                self.assertEqual(
                    personal_channels_service._channel_owner_linked_id(
                        channel_key=CHANNEL_KEY, state=state,
                    ),
                    "",
                )

    async def test_the_dm_allowlist_is_not_read_as_owner_identity(self) -> None:
        """"May message this agent" and "IS the owner" are different facts.
        A state carrying an allowlist and no link resolves to no owner."""
        self.assertEqual(
            personal_channels_service._channel_owner_linked_id(
                channel_key=CHANNEL_KEY,
                state={"allowlist": [OWNER_SENDER_ID], "mode": "allowlist"},
            ),
            "",
        )


class IsOwnerMessageTests(_Base):
    """_is_owner_message, the gate-time consumer."""

    def _is_owner(self, *, sender_id: str, linked: str | None = OWNER_SENDER_ID) -> bool:
        return personal_channels_service._is_owner_message(
            channel_key=CHANNEL_KEY,
            message={"sender_jid": sender_id},
            sender_id=sender_id,
            existing_state={"linked_identity": linked} if linked is not None else {},
        )

    async def test_the_linked_owner_is_the_owner(self) -> None:
        self.assertTrue(self._is_owner(sender_id=OWNER_SENDER_ID))

    async def test_a_stranger_is_not(self) -> None:
        self.assertFalse(self._is_owner(sender_id=STRANGER_SENDER_ID))

    async def test_nobody_is_the_owner_when_nothing_is_linked(self) -> None:
        self.assertFalse(self._is_owner(sender_id=OWNER_SENDER_ID, linked=None))

    async def test_a_channel_prefixed_sender_still_matches(self) -> None:
        """The lane addresses one person two ways in one payload:
        remote_jid "telegram:1932934047", sender_jid "1932934047". A sender
        id that fell back to remote_jid must not read as a stranger."""
        self.assertTrue(self._is_owner(sender_id=f"telegram:{OWNER_SENDER_ID}"))

    async def test_a_forged_prefix_does_not_grant_owner_authority(self) -> None:
        """The reason the canonicalizer strips only the channel's OWN
        registry-derived prefix instead of "everything before the last
        separator": a general rule would hand owner authority to anyone who
        can put a colon in a sender field."""
        for forged in (
            f"evil:{OWNER_SENDER_ID}",
            f"signal:{OWNER_SENDER_ID}",
            f"x@{OWNER_SENDER_ID}",
            f"x/{OWNER_SENDER_ID}",
        ):
            with self.subTest(forged=forged):
                self.assertFalse(self._is_owner(sender_id=forged))

    async def test_a_non_transported_channel_key_does_not_raise(self) -> None:
        """The canonicalizer's registry lookup must tolerate a channel this
        transport does not carry. Its first implementation used
        openclaw_channel_id, which RAISES — which broke the first-party DM
        gate outright, and would have silently downgraded a hosted-channel
        owner to "audience" in _resolve_channel_sender_class, whose except
        branch fails closed."""
        for channel_key in ("telegram_personal", "whatsapp_personal", "telegram_hosted", "", "web"):
            with self.subTest(channel_key=channel_key):
                self.assertEqual(
                    personal_channels_service._channel_prefixed_identity_tail(
                        channel_key=channel_key, value=OWNER_SENDER_ID,
                    ),
                    OWNER_SENDER_ID,
                )

    async def test_the_owner_of_one_channel_is_not_the_owner_of_another(self) -> None:
        """resolve_sender_identity's binding is channel-scoped; prove it,
        since the same numeric id can exist on two platforms."""
        self.assertFalse(
            personal_channels_service._is_owner_message(
                channel_key="openclaw_signal",
                message={"sender_jid": OWNER_SENDER_ID},
                sender_id=OWNER_SENDER_ID,
                existing_state={},
            )
        )


class OwnerIdentityWritePathTests(_Base):
    """The deliberate owner action — the only writer of the column."""

    async def test_set_then_read_back(self) -> None:
        result = self._link()
        self.assertEqual(result["sender_id"], OWNER_SENDER_ID)
        self.assertEqual(
            personal_channels_service.get_personal_channel_owner_identity(
                gateway_id=self.gateway_id, channel_key=CHANNEL_KEY, agent_id=self.agent_id,
            )["sender_id"],
            OWNER_SENDER_ID,
        )

    async def test_the_stored_value_is_canonicalized_once_on_the_way_in(self) -> None:
        """An owner who copies "telegram:1932934047" out of a log gets the
        same link as one who types the bare id — canonicalized at the single
        write, not at each read."""
        self._link(sender_id=f"telegram:{OWNER_SENDER_ID}")
        self.assertEqual(self._state()["linked_identity"], OWNER_SENDER_ID)

    async def test_clearing_is_expressible(self) -> None:
        """A mistaken link that could never be undone would be worse than no
        link at all."""
        self._link()
        cleared = self._link(sender_id="")
        self.assertIsNone(cleared["sender_id"])
        self.assertIsNone(self._state()["linked_identity"])

    async def test_the_link_is_per_agent_not_per_box(self) -> None:
        """Two agents on one Agent Computer. Linking an owner on one must
        never grant owner authority on the other."""
        self._link(agent_id=self.agent_id)
        self.assertEqual(
            personal_channels_service.get_personal_channel_owner_identity(
                gateway_id=self.gateway_id, channel_key=CHANNEL_KEY, agent_id=self.other_agent_id,
            )["sender_id"],
            None,
        )
        self.assertFalse(
            personal_channels_service._is_owner_message(
                channel_key=CHANNEL_KEY,
                message={"sender_jid": OWNER_SENDER_ID},
                sender_id=OWNER_SENDER_ID,
                existing_state=self._state(agent_id=self.other_agent_id) or {},
            )
        )

    async def test_an_unscoped_write_is_refused(self) -> None:
        """Writing under LEGACY_UNSCOPED_AGENT_ID would attach an owner to a
        sentinel row any agent could be handed."""
        with self.assertRaises(ValueError):
            personal_channels_service.set_personal_channel_owner_identity(
                registration=self.registration,
                gateway_id=self.gateway_id,
                channel_key=CHANNEL_KEY,
                agent_id="",
                sender_id=OWNER_SENDER_ID,
            )

    async def test_a_channel_that_establishes_its_owner_elsewhere_is_refused(self) -> None:
        with self.assertRaises(personal_channels_service.UnsupportedOwnerIdentityChannelError):
            personal_channels_service.set_personal_channel_owner_identity(
                registration=self.registration,
                gateway_id=self.gateway_id,
                channel_key="telegram_personal",
                agent_id=self.agent_id,
                sender_id=OWNER_SENDER_ID,
            )

    async def test_the_link_survives_the_per_message_state_sync(self) -> None:
        """THE clobber. _resolve_local_bridge_agent_id re-upserts
        status="linked" on every inbound message that takes the slow path,
        and has no identity to offer. Before the repository preserved
        linked_identity on None, that erased the owner link on the very next
        message — an identity with a half-life of one message.

        Also proves an explicit "" still clears, i.e. preservation did not
        silently make the clear action a no-op.
        """
        self._link()
        for _ in range(3):
            personal_channels_repository.upsert_local_bridge_state(
                gateway_id=self.gateway_id,
                tenant_id="tenant-owner",
                workspace_id="ws-owner",
                user_id="user-owner",
                channel_key=CHANNEL_KEY,
                agent_id=self.agent_id,
                provider="openclaw",
                status="linked",
                metadata={"resolved_via": "preferred_gateway_id"},
            )
            self.assertEqual(self._state()["linked_identity"], OWNER_SENDER_ID)
        personal_channels_repository.upsert_local_bridge_state(
            gateway_id=self.gateway_id,
            tenant_id="tenant-owner",
            workspace_id="ws-owner",
            user_id="user-owner",
            channel_key=CHANNEL_KEY,
            agent_id=self.agent_id,
            provider="openclaw",
            status="linked",
            linked_identity="",
        )
        self.assertIsNone(self._state()["linked_identity"])


class OwnerIdentityReachesTheAuthorityChainTests(_Base):
    """The link is only worth anything if the turn's own tool-authority
    decision sees it. That decision reads a THIRD function
    (personal_channels_repository.list_owner_linked_channel_identities_for_workspace,
    via sage_agent_runtime_service._resolve_channel_sender_class), so prove
    the write lands where that function looks rather than assuming it."""

    async def test_the_workspace_owner_lookup_sees_the_link(self) -> None:
        self._link()
        linked = personal_channels_repository.list_owner_linked_channel_identities_for_workspace(
            "ws-owner"
        )
        self.assertEqual(linked.get(CHANNEL_KEY), OWNER_SENDER_ID)

    async def test_the_conversation_id_the_bridge_actually_passes_resolves_to_owner(self) -> None:
        """personal_channel_sage_bridge_service passes
        `channel_sender_id=remote_jid`, which on this transport is the
        CONVERSATION id ("telegram:1932934047"), not the bare sender. Before
        _resolve_channel_sender_class canonicalized, a sender the DM gate had
        just recognised as the owner arrived here as a stranger and fell
        through to "audience" — which strips EVERY tool, so the agent
        refused its own owner a shell command on a turn whose envelope
        header already said "your owner". Observed live 2026-08-15."""
        from server_modules import sage_agent_runtime_service

        self._link()
        self.assertEqual(
            await sage_agent_runtime_service._resolve_channel_sender_class(
                channel_origin=CHANNEL_KEY,
                sender_id=f"telegram:{OWNER_SENDER_ID}",
                workspace_id="ws-owner",
            ),
            "owner",
        )
        self.assertEqual(
            await sage_agent_runtime_service._resolve_channel_sender_class(
                channel_origin=CHANNEL_KEY,
                sender_id=f"telegram:{STRANGER_SENDER_ID}",
                workspace_id="ws-owner",
            ),
            "audience",
        )

    async def test_resolve_sender_identity_grants_owner_from_that_lookup(self) -> None:
        """The exact composition _resolve_channel_sender_class performs —
        run here rather than mocked, since a mock protects a seam, not a
        path."""
        from server_modules.triage_service import resolve_sender_identity

        self._link()
        linked = personal_channels_repository.list_owner_linked_channel_identities_for_workspace(
            "ws-owner"
        )
        bindings = [
            {"channel_type": key, "linked_user_id": value} for key, value in linked.items()
        ]
        self.assertEqual(
            resolve_sender_identity(
                sender_id=OWNER_SENDER_ID,
                channel_origin=CHANNEL_KEY,
                channel_bindings=bindings,
                audience_enabled=True,
            ),
            "owner",
        )
        self.assertEqual(
            resolve_sender_identity(
                sender_id=STRANGER_SENDER_ID,
                channel_origin=CHANNEL_KEY,
                channel_bindings=bindings,
                audience_enabled=True,
            ),
            "audience",
        )


class ChannelOriginSurvivesTheTurnAdapterTests(_Base):
    """The turn adapter used to hand handle_sage_chat a ChannelOrigin MEMBER,
    not a string, and the enum has no OpenClaw members. Two silent defects on
    one line, and together they meant nothing downstream could ever tell
    which channel a turn came from."""

    async def test_an_openclaw_channel_keeps_its_real_key(self) -> None:
        from server_modules.channel_adapter import normalize_sage_inbound
        from server_modules.sage_turn_adapter import _channel_origin_for_turn

        turn = normalize_sage_inbound(
            workspace_id="ws-owner", message="hi", channel_origin=CHANNEL_KEY,
        )
        self.assertEqual(_channel_origin_for_turn(turn, CHANNEL_KEY), CHANNEL_KEY)

    async def test_a_first_party_channel_yields_its_value_not_its_repr(self) -> None:
        """"channelorigin.telegram_hosted" is what the old line produced, and
        it matches no channel key anywhere — this half of the bug was never
        specific to the new channels."""
        from server_modules.channel_adapter import normalize_sage_inbound
        from server_modules.sage_turn_adapter import _channel_origin_for_turn

        turn = normalize_sage_inbound(
            workspace_id="ws-owner", message="hi", channel_origin="telegram_hosted",
        )
        resolved = _channel_origin_for_turn(turn, "telegram_hosted")
        self.assertEqual(resolved, "telegram_hosted")
        self.assertNotIn("channelorigin", resolved.lower())

    async def test_every_live_channel_key_survives(self) -> None:
        from server_modules.channel_adapter import normalize_sage_inbound
        from server_modules.sage_turn_adapter import _channel_origin_for_turn

        for channel_key in sorted(personal_channels_service.LOCAL_BRIDGE_PERSONAL_CHANNELS):
            with self.subTest(channel_key=channel_key):
                turn = normalize_sage_inbound(
                    workspace_id="ws-owner", message="hi", channel_origin=channel_key,
                )
                self.assertEqual(_channel_origin_for_turn(turn, channel_key), channel_key)


class InboundHandlerLoadsTheStateTests(_Base):
    """The second half of the bug: the handler hardcoded existing_state=None,
    so a populated column would still never have been read. Asserted at the
    gate the handler actually calls, with the state the handler now loads."""

    async def test_the_dm_gate_sees_the_owner(self) -> None:
        self._link()
        decision = await personal_channels_service._enforce_dm_policy(
            registration=self.registration,
            channel_key=CHANNEL_KEY,
            agent_id=self.agent_id,
            message={"sender_jid": OWNER_SENDER_ID, "is_self_chat": False},
            remote_jid=f"telegram:{OWNER_SENDER_ID}",
            existing_state=self._state(),
            label="Telegram",
        )
        self.assertTrue(decision["is_owner"])
        self.assertTrue(decision["allowed"])

    async def test_the_handler_passes_the_row_and_not_none(self) -> None:
        """Structural: the live handler must read the row for the SAME
        (gateway, channel, agent) tuple it recorded the message under. A
        behavioural test cannot distinguish "loaded the right row" from
        "loaded a gateway-wide one", and the wrong one would let a second
        agent inherit the first agent's owner."""
        import ast
        import inspect

        source = inspect.getsource(
            personal_channels_service._handle_local_bridge_gateway_channel_inbound
        )
        tree = ast.parse(source.lstrip())
        calls = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "_enforce_dm_policy"
        ]
        self.assertEqual(len(calls), 1, "one dm gate call site, or this assertion is blind")
        existing_state = next(
            kw.value for kw in calls[0].keywords if kw.arg == "existing_state"
        )
        self.assertNotIsInstance(
            existing_state,
            ast.Constant,
            "existing_state is a literal again — the owner can never be recognised",
        )
        rendered = ast.dump(existing_state)
        self.assertIn("get_local_bridge_state", rendered)
        self.assertIn("agent_id", rendered)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
