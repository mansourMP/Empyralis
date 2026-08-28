"""OpenClaw-transported channel outbound — the CLOUD half of
the OpenClaw channel adoption step 3.

WHAT THIS FILE IS ACTUALLY DEFENDING
------------------------------------
Step 3 deliberately added NO new cloud outbound stack. OpenClaw channels are
members of the local-bridge family (`_OpenClawPersonalChannelHandler` extends
`_LocalBridgePersonalChannelHandler`), so an agent's reply already travels
the same road Signal/iMessage/WeChat-personal use:

    _handle_local_bridge_gateway_channel_inbound
      -> _deliver_local_bridge_personal_reply
        -> gateway_protocol_service.dispatch_channel_outbound(channel.outbound)
          -> [gateway] OpenClawPersonalChannelRuntime.handleChannelOutbound
            -> OpenClaw `message.action` over the local WS

"The code exists" is not "the path is reachable" (CLAUDE.md). These tests run
the LIVE entry points and assert the frame that actually leaves the cloud —
its channel_key, provider, target and idempotency key — rather than asserting
that some function returns a dict.

They also pin the two failure properties step 3 is required to have:

  1. a delivery failure is NEVER recorded as a delivery (the outbound row
     stays undelivered and the inbound stays unprocessed, so nothing tells a
     customer work was done that never happened);
  2. every OpenClaw channel is reachable from the manual/test send route —
     that route's channel map was a third hardcoded copy that step 2 did not
     update, so it answered 404 for exactly these channels.
"""

from __future__ import annotations

import importlib
import tempfile
import unittest
from pathlib import Path
from typing import Any, Dict, Optional
from unittest.mock import AsyncMock, patch

from server_modules import (
    personal_channels_repository,
    personal_channels_service,
    routes_personal_channels,
)


OPENCLAW_CHANNEL_KEY = "openclaw_line"
OPENCLAW_PROVIDER = "openclaw"
GATEWAY_ID = "gw-openclaw-out-1"
CHAT_ID = "C-allowlisted-1"

_ALLOW_DISPATCH_DECISION = {
    "ok": True,
    "decision": "allow",
    "reason": "gateway_service_operation_allowed",
    "operation": "protocol_route",
    "next_action": "dispatch_gateway_operation",
}


class _FakeAgentInstallStore:
    """Minimal stand-in for agent_registry_repository's install bundle
    read/write — same per-file convention as test_openclaw_channel_inbound.py
    (duplicated rather than imported across test modules)."""

    def __init__(self) -> None:
        self.installs: Dict[str, Dict[str, Any]] = {}

    async def get_bundle(self, agent_id: str, *, tenant_id: str, workspace_id: str):
        return {"id": agent_id, "install_metadata": dict(self.installs.get(agent_id, {}))}

    async def update(self, agent_id: str, *, tenant_id: str, workspace_id: str, metadata=None, **_kwargs):
        merged = {**self.installs.get(agent_id, {}), **(metadata or {})}
        self.installs[agent_id] = merged
        return {"id": agent_id, "install_metadata": dict(merged)}


def _patch_agent_install_store(store: "_FakeAgentInstallStore"):
    return patch.multiple(
        "server_modules.agent_registry_repository",
        get_workspace_agent_install_bundle=AsyncMock(side_effect=store.get_bundle),
        update_workspace_agent_install=AsyncMock(side_effect=store.update),
    )


class OpenClawOutboundRouteSurfaceTests(unittest.TestCase):
    """The manual/test send route's channel map."""

    def test_every_openclaw_channel_is_reachable_from_the_manual_send_route(self) -> None:
        # REGRESSION GUARD. routes_personal_channels.LOCAL_BRIDGE_CHANNELS was
        # a third hardcoded copy of the local-bridge channel list; step 2
        # merged the OpenClaw channels into the service's map but not into
        # this one, so this route 404'd for a channel that was otherwise
        # fully live. It is now derived from the service map, and this test
        # fails if anyone re-hardcodes it.
        for channel_key in personal_channels_service.OPENCLAW_PERSONAL_CHANNELS:
            spec = routes_personal_channels.LOCAL_BRIDGE_CHANNELS.get(channel_key)
            self.assertIsNotNone(spec, f"{channel_key} is not reachable from the manual send route")
            self.assertEqual(spec["provider"], OPENCLAW_PROVIDER)
        # signal_personal/imessage_personal/wechat_personal (the first-party
        # local bridges) were DELETED 2026-08-14 (full OpenClaw channel
        # cutover) — their replacements (openclaw_signal/openclaw_imessage/
        # openclaw_openclaw-weixin) are already covered by the loop above,
        # since they are members of OPENCLAW_PERSONAL_CHANNELS.
        for channel_key in ("signal_personal", "imessage_personal", "wechat_personal"):
            self.assertNotIn(channel_key, routes_personal_channels.LOCAL_BRIDGE_CHANNELS)

    def test_the_route_map_is_derived_from_the_service_map_not_re_declared(self) -> None:
        self.assertEqual(
            set(routes_personal_channels.LOCAL_BRIDGE_CHANNELS),
            set(personal_channels_service.LOCAL_BRIDGE_PERSONAL_CHANNELS),
        )


class OpenClawOutboundDispatchTests(unittest.IsolatedAsyncioTestCase):
    """Through the LIVE handler chain, asserting the frame that leaves."""

    def setUp(self) -> None:
        global personal_channels_service, personal_channels_repository
        personal_channels_service = importlib.import_module("server_modules.personal_channels_service")
        personal_channels_repository = importlib.import_module(
            "server_modules.personal_channels_repository"
        )

        self.tmpdir = tempfile.TemporaryDirectory()
        db_path = Path(self.tmpdir.name) / "personal-channels.sqlite3"
        personal_channels_repository.init_personal_channels_db(db_path)
        self.db_patcher = patch.object(
            personal_channels_repository, "PERSONAL_CHANNELS_DB_FILE", db_path
        )
        self.db_patcher.start()

        self.store = _FakeAgentInstallStore()
        self.registration = {
            "gateway_id": GATEWAY_ID,
            "workspace_id": "default",
            "tenant_id": "tenant-1",
            "device_trust_state": "trusted",
            "active_session_id": "sess-1",
            "capabilities": ["channel.openclaw.line"],
        }
        # The only configuration that can open this path at all: the owner
        # allowlisted this chat and turned require_mention off. Anything less
        # and the message never reaches a reply, so there would be nothing
        # outbound to test. See test_openclaw_channel_inbound.py for the
        # closed cases.
        personal_channels_repository.upsert_local_bridge_state(
            gateway_id=GATEWAY_ID,
            tenant_id="tenant-1",
            workspace_id="default",
            user_id="",
            channel_key=OPENCLAW_CHANNEL_KEY,
            agent_id="agent-1",
            provider=OPENCLAW_PROVIDER,
            status="linked",
        )
        self.store.installs["agent-1"] = {
            "group_policy": {
                OPENCLAW_CHANNEL_KEY: {
                    "mode": personal_channels_service.GROUP_POLICY_ALLOWLIST,
                    "allowlist": [CHAT_ID],
                    "require_mention": False,
                }
            }
        }

    def tearDown(self) -> None:
        self.db_patcher.stop()
        self.tmpdir.cleanup()

    def _all_rows(self) -> Dict[str, list]:
        """Every inbound/outbound row for this gateway+channel, regardless of
        which agent scope it was written under.

        The local-bridge family writes its INBOUND rows agent-scoped
        (`record_inbound_message(agent_id=...)`) but its OUTBOUND rows under
        the legacy unscoped id, because `_deliver_local_bridge_personal_reply`
        never receives an agent_id. That split is pre-existing and shared by
        Signal/iMessage/WeChat — it is not an OpenClaw property and is not
        this change's to fix, so these tests deliberately read the union
        instead of encoding either scope as correct. They stay green whichever
        way that split is later resolved.
        """
        merged: Dict[str, list] = {"inbound": [], "outbound": []}
        seen: Dict[str, set] = {"inbound": set(), "outbound": set()}
        for agent_id in ("agent-1", personal_channels_repository.LEGACY_UNSCOPED_AGENT_ID):
            rows = personal_channels_repository.list_recent_gateway_messages(
                GATEWAY_ID, channel_key=OPENCLAW_CHANNEL_KEY, agent_id=agent_id, limit=25
            )
            for bucket in ("inbound", "outbound"):
                for row in rows[bucket]:
                    row_id = str(row.get("id"))
                    if row_id in seen[bucket]:
                        continue
                    seen[bucket].add(row_id)
                    merged[bucket].append(row)
        return merged

    def _payload(self, external_message_id: str = "oc-in-1") -> Dict[str, Any]:
        return {
            "channel_key": OPENCLAW_CHANNEL_KEY,
            "provider": OPENCLAW_PROVIDER,
            "message": {
                "external_message_id": external_message_id,
                "remote_jid": CHAT_ID,
                "sender_jid": "U-42",
                "push_name": "Ada",
                "text": "status please",
                "received_at": "2026-08-08T10:00:00.000Z",
                "from_me": False,
            },
        }

    async def _run_inbound(
        self,
        *,
        dispatch: AsyncMock,
        reply: Optional[Dict[str, Any]] = None,
        external_message_id: str = "oc-in-1",
    ):
        with (
            patch(
                "server_modules.personal_channels_service.rust_runtime_kernel_client.run_runtime_kernel_enforced",
                return_value=_ALLOW_DISPATCH_DECISION,
            ),
            _patch_agent_install_store(self.store),
            patch(
                "server_modules.personal_channels_service.personal_channel_sage_bridge_service.build_personal_channel_reply_async",
                new=AsyncMock(return_value=reply if reply is not None else {"text": "All green.", "source": "sage"}),
            ),
            patch(
                "server_modules.personal_channels_service.gateway_protocol_service.dispatch_channel_outbound",
                new=dispatch,
                create=True,
            ),
        ):
            return await personal_channels_service.handle_gateway_channel_inbound(
                gateway_id=GATEWAY_ID,
                registration=self.registration,
                payload=self._payload(external_message_id),
            )

    async def test_an_openclaw_reply_reaches_dispatch_channel_outbound_on_the_openclaw_lane(self) -> None:
        """The reachability proof. Nothing here is new machinery — the point
        is that the EXISTING machinery genuinely fires for an openclaw_* key
        and puts that key (not a first-party one) on the wire."""
        dispatch = AsyncMock(return_value={"external_message_id": "oc-out-1"})
        result = await self._run_inbound(dispatch=dispatch)

        dispatch.assert_awaited_once()
        kwargs = dispatch.await_args.kwargs
        self.assertEqual(kwargs["channel_key"], OPENCLAW_CHANNEL_KEY)
        self.assertEqual(kwargs["provider"], OPENCLAW_PROVIDER)
        self.assertEqual(kwargs["remote_jid"], CHAT_ID)
        self.assertEqual(kwargs["text"], "All green.")
        # The gateway REQUIRES an idempotency key: OpenClaw dedupes
        # `message.action` on it, which is the only thing that makes the
        # gateway-side retry safe.
        self.assertTrue(str(kwargs["idempotency_key"]).strip())
        # Automatic replies are not forced quote-replies.
        self.assertIsNone(kwargs["reply_to_external_message_id"])

        self.assertFalse(result.get("ignored"))
        outbound = result.get("outbound") or {}
        self.assertEqual(outbound.get("status"), "delivered")
        self.assertEqual(outbound.get("external_message_id"), "oc-out-1")

    async def test_a_delivery_failure_is_never_recorded_as_a_delivery(self) -> None:
        """The gateway throws when OpenClaw refuses (see
        outbound-runtime.ts), which dispatch_channel_outbound re-raises. That
        must leave BOTH books unwritten: the outbound row undelivered and the
        inbound unprocessed. Recording either would be the "silent
        misrouting" failure CLAUDE.md names — a customer told work was done
        that never happened."""
        dispatch = AsyncMock(
            side_effect=ValueError(
                "OpenClaw refused delivery for openclaw_line (rejected/INVALID_REQUEST): unsupported channel: line"
            )
        )
        with self.assertRaises(ValueError):
            await self._run_inbound(dispatch=dispatch)

        rows = self._all_rows()
        outbound_rows = rows["outbound"]
        self.assertEqual(len(outbound_rows), 1)
        self.assertNotEqual(outbound_rows[0].get("status"), "delivered")
        self.assertIsNone(outbound_rows[0].get("external_message_id"))

        inbound_rows = rows["inbound"]
        self.assertEqual(len(inbound_rows), 1)
        self.assertEqual(inbound_rows[0].get("external_message_id"), "oc-in-1")
        # Unprocessed: no reply idempotency key was stamped, so this message
        # is still visibly outstanding rather than silently closed out.
        self.assertFalse(str(inbound_rows[0].get("reply_idempotency_key") or "").strip())

    async def test_the_manual_send_path_dispatches_on_the_openclaw_lane_too(self) -> None:
        """send_local_bridge_personal_message is what the route above calls.
        It must carry the OpenClaw channel_key/provider through unchanged —
        this is the operator's only way to send a test message before any
        inbound traffic exists."""
        dispatch = AsyncMock(return_value={"external_message_id": "oc-out-2"})
        with (
            patch(
                "server_modules.personal_channels_service.rust_runtime_kernel_client.run_runtime_kernel_enforced",
                return_value=_ALLOW_DISPATCH_DECISION,
            ),
            patch(
                "server_modules.personal_channels_service.gateway_protocol_service.dispatch_channel_outbound",
                new=dispatch,
                create=True,
            ),
        ):
            result = await personal_channels_service.send_local_bridge_personal_message(
                gateway_id=GATEWAY_ID,
                registration=self.registration,
                channel_key=OPENCLAW_CHANNEL_KEY,
                provider=OPENCLAW_PROVIDER,
                remote_jid=CHAT_ID,
                text="ping",
                idempotency_key="manual-1",
            )
        dispatch.assert_awaited_once()
        kwargs = dispatch.await_args.kwargs
        self.assertEqual(kwargs["channel_key"], OPENCLAW_CHANNEL_KEY)
        self.assertEqual(kwargs["provider"], OPENCLAW_PROVIDER)
        self.assertEqual(kwargs["idempotency_key"], "manual-1")
        self.assertEqual(result.get("status"), "delivered")

    async def test_a_mismatched_provider_is_refused_before_anything_is_dispatched(self) -> None:
        """The lane contract is the gate, not a comment. A caller naming an
        OpenClaw channel with somebody else's provider must fail loudly
        rather than fall through to a default (CLAUDE.md)."""
        dispatch = AsyncMock()
        with (
            patch(
                "server_modules.personal_channels_service.rust_runtime_kernel_client.run_runtime_kernel_enforced",
                return_value=_ALLOW_DISPATCH_DECISION,
            ),
            patch(
                "server_modules.personal_channels_service.gateway_protocol_service.dispatch_channel_outbound",
                new=dispatch,
                create=True,
            ),
        ):
            with self.assertRaises(ValueError):
                await personal_channels_service.send_local_bridge_personal_message(
                    gateway_id=GATEWAY_ID,
                    registration=self.registration,
                    channel_key=OPENCLAW_CHANNEL_KEY,
                    provider="signal_local_bridge",
                    remote_jid=CHAT_ID,
                    text="ping",
                    idempotency_key="manual-2",
                )
        dispatch.assert_not_awaited()

    async def test_the_registry_handler_for_an_openclaw_channel_sends_on_its_own_lane(self) -> None:
        """The registry is what a generic caller reaches for. Its OpenClaw
        handler must not inherit some other channel's key/provider."""
        service = importlib.import_module("server_modules.personal_channels_service")
        handler = service._handler_registry.get(OPENCLAW_CHANNEL_KEY)
        dispatch = AsyncMock(return_value={"external_message_id": "oc-out-3"})
        with (
            patch(
                "server_modules.personal_channels_service.rust_runtime_kernel_client.run_runtime_kernel_enforced",
                return_value=_ALLOW_DISPATCH_DECISION,
            ),
            patch(
                "server_modules.personal_channels_service.gateway_protocol_service.dispatch_channel_outbound",
                new=dispatch,
                create=True,
            ),
        ):
            await handler.send_message(
                gateway_id=GATEWAY_ID,
                registration=self.registration,
                remote_jid=CHAT_ID,
                text="ping",
                idempotency_key="registry-1",
            )
        kwargs = dispatch.await_args.kwargs
        self.assertEqual(kwargs["channel_key"], OPENCLAW_CHANNEL_KEY)
        self.assertEqual(kwargs["provider"], OPENCLAW_PROVIDER)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
