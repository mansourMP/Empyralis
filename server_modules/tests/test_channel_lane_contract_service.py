import unittest

from server_modules import channel_lane_contract_service as service
from server_modules import routes_connectors, routes_personal_channels


class ChannelLaneContractServiceTests(unittest.TestCase):
    def test_personal_and_studio_routes_stay_in_separate_prefixes(self) -> None:
        personal_paths = {route.path for route in routes_personal_channels.router.routes}
        connector_paths = {route.path for route in routes_connectors.router.routes}

        self.assertTrue(personal_paths)
        self.assertTrue(connector_paths)
        self.assertTrue(all(service.is_personal_route_path(path) for path in personal_paths))
        self.assertFalse(any(service.is_personal_route_path(path) for path in connector_paths))

        webhook_paths = {
            "/channels/whatsapp/twilio/webhook",
            "/channels/sms/twilio/webhook",
            "/channels/telegram/webhook/{connector_id}",
            "/channels/slack/events",
            "/channels/github/webhook",
            "/connectors/discord/webhook",
        }
        self.assertTrue(webhook_paths.issubset(connector_paths))

    def test_build_personal_runtime_context_blocks_studio_session_fields(self) -> None:
        # whatsapp_personal (the first-party Baileys channel) was deleted
        # 2026-08-14 (full OpenClaw channel cutover); openclaw_whatsapp is its
        # live replacement and exercises the identical contract.
        runtime_context = service.build_personal_gateway_runtime_context(
            surface_channel="openclaw_whatsapp",
            workspace_id="workspace-1",
            gateway_id="gateway-1",
            remote_jid="user-1",
        )

        self.assertEqual(runtime_context["availability"]["runtime_lane"], service.PERSONAL_GATEWAY_RUNTIME_LANE)
        self.assertEqual(runtime_context["session_ctx"]["memory_surface"], service.DIRECT_CHAT_MEMORY_SURFACE)

        with self.assertRaisesRegex(ValueError, "Studio deployment state"):
            service.assert_personal_runtime_session_ctx(
                {
                    **runtime_context["session_ctx"],
                    "responder_install_id": "install-studio-1",
                }
            )

    def test_personal_provider_contract_rejects_connector_provider(self) -> None:
        with self.assertRaisesRegex(ValueError, "mismatched personal provider"):
            service.assert_personal_gateway_channel("openclaw_whatsapp", "twilio_whatsapp")

    def test_studio_webhook_contract_rejects_personal_route_path(self) -> None:
        with self.assertRaisesRegex(ValueError, "outside the Studio connector lane"):
            service.assert_public_studio_webhook_path("/personal-channels/whatsapp/gateways/gateway-1")

    def test_channel_catalog_keeps_personal_priority_and_studio_boundary(self) -> None:
        full_personal_catalog = service.personal_channel_catalog()
        studio_catalog = service.studio_channel_catalog()

        # 2026-08-14 full OpenClaw channel cutover: telegram_personal,
        # whatsapp_personal, signal_personal, imessage_personal and
        # wechat_personal (the entire first-party local-bridge/Baileys/
        # gramjs family) are DELETED — their live replacements are
        # openclaw_telegram/openclaw_whatsapp/openclaw_signal/
        # openclaw_imessage/openclaw_openclaw-weixin, all now ACTIVE (not
        # superseded) OpenClaw channels. discord_personal is the only
        # first-party personal channel left — see PERSONAL_CHANNEL_SPECS's
        # own comment on why (cloud_connector, no Agent Computer needed,
        # not a platform this hardware-bound transport would improve on).
        personal_catalog = [
            entry
            for entry in full_personal_catalog
            if entry["provider"] != service.OPENCLAW_TRANSPORT_PROVIDER
        ]
        openclaw_catalog = [
            entry
            for entry in full_personal_catalog
            if entry["provider"] == service.OPENCLAW_TRANSPORT_PROVIDER
        ]

        self.assertEqual(
            [entry["channel_key"] for entry in personal_catalog],
            ["discord_personal"],
        )
        self.assertEqual(
            [entry["channel_key"] for entry in full_personal_catalog[: len(personal_catalog)]],
            [entry["channel_key"] for entry in personal_catalog],
            "First-party channels must keep priority over the transported ones.",
        )

        # A derived set that silently emptied would make every assertion about
        # it pass vacuously, and would read to a customer as a product with no
        # channels rather than as a bug.
        self.assertTrue(openclaw_catalog)
        self.assertEqual(
            [entry["channel_key"] for entry in openclaw_catalog],
            [channel.channel_key for channel in service.OPENCLAW_ACTIVE_CHANNELS],
        )
        # whatsapp/telegram/signal/imessage/openclaw-weixin are now among the
        # active (cut-over) channels — proof the cutover actually moved them.
        active_ids = {channel.id for channel in service.OPENCLAW_ACTIVE_CHANNELS}
        self.assertTrue({"whatsapp", "telegram", "signal", "imessage", "openclaw-weixin"}.issubset(active_ids))
        # Every transported channel is "preview" — a property of the
        # transport, not a per-channel judgement, so there is no list to keep
        # honest. Nothing here has been driven with real credentials yet
        # (CHANNEL-ADOPTION-PLAN.md step 5).
        self.assertEqual({entry["stage"] for entry in openclaw_catalog}, {"preview"})
        # No transported channel may collide with a first-party one, and the
        # active/superseded split must be a partition — an id on both sides
        # would be two live implementations of one platform.
        self.assertFalse(
            {entry["channel_key"] for entry in personal_catalog}
            & {entry["channel_key"] for entry in openclaw_catalog}
        )
        self.assertFalse(
            {channel.id for channel in service.OPENCLAW_ACTIVE_CHANNELS}
            & {channel.id for channel in service.OPENCLAW_SUPERSEDED_CHANNELS}
        )

        self.assertEqual([entry["stage"] for entry in personal_catalog], ["live"])
        self.assertTrue(
            all(
                entry["runtime_lane"] == service.PERSONAL_GATEWAY_RUNTIME_LANE
                for entry in personal_catalog
                # discord_personal is bot-token-backed, not a paired-gateway
                # user-account session (Discord ToS prohibits self-bots) — see
                # its PERSONAL_CHANNEL_ROADMAP entry.
                if entry["channel_key"] != "discord_personal"
            )
        )
        self.assertEqual([entry["live_capable"] for entry in personal_catalog], ["true"])

        self.assertEqual(
            [entry["channel_key"] for entry in studio_catalog],
            ["web_chat", "email", "telegram_bot", "whatsapp_twilio", "sms_twilio", "apple_messages_business", "slack", "discord_bot"],
        )
        by_key = {entry["channel_key"]: entry for entry in studio_catalog}
        self.assertEqual(by_key["web_chat"]["status"], "roadmap")
        self.assertEqual(by_key["email"]["status"], "partial")
        self.assertEqual(by_key["telegram_bot"]["status"], "working_when_configured")
        self.assertEqual(by_key["whatsapp_twilio"]["status"], "out_of_scope")
        # SMS (Twilio) is a live, launchable cloud channel — each agent gets
        # its own phone number, reusing the WhatsApp-Twilio Messages plumbing.
        self.assertEqual(by_key["sms_twilio"]["status"], "working_when_configured")
        self.assertEqual(by_key["sms_twilio"]["provider"], "twilio_sms")
        self.assertEqual(by_key["sms_twilio"]["launch_allowed"], "true")
        self.assertEqual(by_key["apple_messages_business"]["status"], "roadmap")
        self.assertEqual(by_key["slack"]["status"], "working_when_configured")
        self.assertEqual(by_key["discord_bot"]["status"], "working_when_configured")
        self.assertEqual(by_key["telegram_bot"]["launch_allowed"], "true")
        self.assertTrue(
            all(
                entry["launch_allowed"] == "false"
                for entry in studio_catalog
                if entry["channel_key"] not in {"telegram_bot", "sms_twilio", "slack", "discord_bot"}
            )
        )
        self.assertTrue(
            all(entry["runtime_lane"] == service.STUDIO_CONNECTOR_RUNTIME_LANE for entry in studio_catalog)
        )

    def test_discord_is_not_accepted_as_personal_channel(self) -> None:
        self.assertFalse(service.is_personal_channel_key("discord_bot"))

        with self.assertRaisesRegex(ValueError, "non-personal channel"):
            service.assert_personal_gateway_channel("discord_bot", "discord_webhook")

    def test_platform_channel_catalog_keeps_business_and_personal_lanes_explicit(self) -> None:
        catalog = service.platform_channel_catalog()
        by_key = {entry["channel_key"]: entry for entry in catalog}

        # 21 first-party entries (26 minus the 5 deleted 2026-08-14: telegram_
        # personal, whatsapp_personal, signal_personal, imessage_personal,
        # wechat_personal), plus one per channel the pinned OpenClaw carries —
        # ALL of them, including the ones a first-party runtime still owns, so
        # the UI can show "carried by the transport, superseded here" rather
        # than leaving them invisible. Counted from the derived set rather
        # than re-typed, but the first-party 21 stays exact.
        openclaw_entries = [
            entry
            for entry in catalog
            if entry["provider"] == service.OPENCLAW_TRANSPORT_PROVIDER
        ]
        self.assertTrue(openclaw_entries)
        self.assertEqual(len(catalog) - len(openclaw_entries), 21)
        self.assertEqual(
            len(openclaw_entries), len(service.openclaw_channel_registry.CHANNELS)
        )
        # SMS via Twilio — a live business channel on the Studio connector lane
        # (the "each agent gets its own phone number" feature).
        self.assertEqual(by_key["sms_twilio"]["provider"], "twilio_sms")
        self.assertEqual(by_key["sms_twilio"]["binding_channel_key"], "sms")
        self.assertEqual(by_key["sms_twilio"]["runtime_lane"], service.STUDIO_CONNECTOR_RUNTIME_LANE)
        self.assertTrue(by_key["sms_twilio"]["live_capable"])
        self.assertTrue(by_key["sms_twilio"]["launch_allowed"])
        self.assertFalse(by_key["sms_twilio"]["requires_agent_computer"])
        self.assertEqual(by_key["sms_twilio"]["product_surface"], "business_channel")
        self.assertEqual(by_key["telegram_bot"]["binding_channel_key"], "telegram")
        self.assertEqual(by_key["telegram_bot"]["runtime_lane"], service.STUDIO_CONNECTOR_RUNTIME_LANE)
        # telegram_personal/whatsapp_personal/signal_personal/imessage_personal/
        # wechat_personal DELETED 2026-08-14 (full OpenClaw channel cutover);
        # their live replacements are openclaw_telegram/openclaw_whatsapp/
        # openclaw_signal/openclaw_imessage/openclaw_openclaw-weixin, which
        # share the exact same PERSONAL_GATEWAY_RUNTIME_LANE/requires_agent_
        # computer/taxonomy shape (they always required an Agent Computer,
        # cutover or not).
        self.assertEqual(by_key["openclaw_telegram"]["runtime_lane"], service.PERSONAL_GATEWAY_RUNTIME_LANE)
        self.assertTrue(by_key["openclaw_telegram"]["requires_agent_computer"])
        self.assertEqual(by_key["openclaw_telegram"]["surface_kind"], "messaging_channel")
        self.assertEqual(by_key["openclaw_telegram"]["product_surface"], "personal_messaging")
        self.assertEqual(by_key["openclaw_telegram"]["navigation_group"], "personal_messaging")
        self.assertEqual(by_key["openclaw_telegram"]["ownership_boundary"], "agent_computer")
        self.assertEqual(by_key["openclaw_whatsapp"]["surface_support"], ["sage"])
        self.assertTrue(by_key["openclaw_whatsapp"]["live_capable"])
        self.assertTrue(by_key["openclaw_signal"]["live_capable"])
        self.assertTrue(by_key["openclaw_imessage"]["live_capable"])
        self.assertTrue(by_key["openclaw_openclaw-weixin"]["live_capable"])
        # None of the five is launchable from this catalog directly — an
        # OpenClaw channel is credentialed inside OpenClaw on the owner's own
        # machine, so a "Connect" button here would be a dead control.
        for cut_over_key in (
            "openclaw_telegram", "openclaw_whatsapp", "openclaw_signal",
            "openclaw_imessage", "openclaw_openclaw-weixin",
        ):
            self.assertFalse(by_key[cut_over_key]["launch_allowed"])
            self.assertEqual(by_key[cut_over_key]["status"], "openclaw_transport")
        self.assertEqual(by_key["apple_messages_business"]["provider"], "apple_messages_business_msp")
        self.assertEqual(by_key["apple_messages_business"]["runtime_lane"], service.STUDIO_CONNECTOR_RUNTIME_LANE)
        self.assertEqual(by_key["apple_messages_business"]["product_surface"], "business_channel")
        self.assertFalse(by_key["apple_messages_business"]["requires_agent_computer"])
        self.assertFalse(by_key["apple_messages_business"]["live_capable"])
        self.assertFalse(by_key["apple_messages_business"]["launch_allowed"])
        self.assertIn("human_handoff_required", by_key["apple_messages_business"]["capabilities"])
        self.assertEqual(by_key["github"]["category"], "work_system")
        self.assertEqual(by_key["github"]["status"], "working_when_configured")
        self.assertTrue(by_key["github"]["live_capable"])
        self.assertTrue(by_key["github"]["launch_allowed"])
        self.assertEqual(by_key["github"]["surface_kind"], "connected_app")
        self.assertEqual(by_key["github"]["product_surface"], "connected_app")
        self.assertEqual(by_key["github"]["extension_kind"], "app_connector")
        self.assertEqual(by_key["notion"]["status"], "working_when_configured")
        self.assertTrue(by_key["notion"]["live_capable"])
        self.assertTrue(by_key["notion"]["launch_allowed"])
        self.assertEqual(by_key["notion"]["product_surface"], "connected_app")
        self.assertEqual(by_key["linear"]["status"], "working_when_configured")
        self.assertTrue(by_key["linear"]["live_capable"])
        self.assertTrue(by_key["linear"]["launch_allowed"])
        self.assertEqual(by_key["linear"]["product_surface"], "connected_app")
        for connected_app in ("dropbox", "s3", "smtp", "wechat_work", "instagram_business"):
            self.assertEqual(by_key[connected_app]["status"], "working_when_configured")
            self.assertTrue(by_key[connected_app]["live_capable"])
            self.assertTrue(by_key[connected_app]["launch_allowed"])
            self.assertEqual(by_key[connected_app]["product_surface"], "connected_app")
            self.assertEqual(by_key[connected_app]["ownership_boundary"], "workspace_account")
        self.assertEqual(by_key["telegram_bot"]["surface_kind"], "messaging_channel")
        self.assertEqual(by_key["telegram_bot"]["product_surface"], "business_channel")

        reserved = {entry["channel_key"] for entry in service.reserved_private_runtime_channel_catalog()}
        self.assertFalse({"imessage_personal", "signal_personal", "wechat_personal"} & reserved)
        self.assertTrue({"voice_wake", "mobile_nodes", "plugin_marketplace"}.issubset(reserved))

    def test_reserved_personal_specs_stay_on_personal_gateway_lane(self) -> None:
        # signal_personal/imessage_personal/wechat_personal (the first-party
        # local-bridge family) DELETED 2026-08-14 (full OpenClaw channel
        # cutover). Their live replacements — openclaw_signal/openclaw_
        # imessage/openclaw_openclaw-weixin — carry the identical contract:
        # PERSONAL_GATEWAY_RUNTIME_LANE, DIRECT_CHAT_MEMORY_SURFACE,
        # live_capable "true".
        for channel_key in ("openclaw_signal", "openclaw_imessage", "openclaw_openclaw-weixin"):
            spec = service.assert_personal_gateway_channel(channel_key, service.OPENCLAW_TRANSPORT_PROVIDER)

            self.assertEqual(spec["runtime_lane"], service.PERSONAL_GATEWAY_RUNTIME_LANE)
            self.assertEqual(spec["memory_surface"], service.DIRECT_CHAT_MEMORY_SURFACE)
            self.assertEqual(spec["live_capable"], "true")

    def test_agent_computer_bridge_channels_pass_personal_preflight(self) -> None:
        """The five platforms cut over 2026-08-14 (whatsapp/telegram/signal/
        imessage/openclaw-weixin) are all live_capable, so they pass
        preflight — same contract the first-party channels they replaced
        satisfied."""
        for channel_key in (
            "openclaw_whatsapp", "openclaw_telegram", "openclaw_signal",
            "openclaw_imessage", "openclaw_openclaw-weixin",
        ):
            preflight = service.personal_bridge_preflight(channel_key)

            self.assertEqual(preflight["status"], "pass")
            self.assertTrue(preflight["launch_allowed"])
            self.assertEqual(preflight["reason"], "live_personal_gateway_runtime")


if __name__ == "__main__":
    unittest.main()
