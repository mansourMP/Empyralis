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
        runtime_context = service.build_personal_gateway_runtime_context(
            surface_channel="whatsapp_personal",
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
            service.assert_personal_gateway_channel("whatsapp_personal", "twilio_whatsapp")

    def test_studio_webhook_contract_rejects_personal_route_path(self) -> None:
        with self.assertRaisesRegex(ValueError, "outside the Studio connector lane"):
            service.assert_public_studio_webhook_path("/personal-channels/whatsapp/gateways/gateway-1")

    def test_channel_catalog_keeps_personal_priority_and_studio_boundary(self) -> None:
        full_personal_catalog = service.personal_channel_catalog()
        studio_catalog = service.studio_channel_catalog()

        # The catalog is now the first-party channels followed by the DERIVED
        # OpenClaw-transported ones. The first-party half is still asserted
        # exactly, order included — that order is the product's priority
        # order. The derived half is asserted by its contract instead of by a
        # list that would have to be re-typed every time OpenClaw ships a
        # channel, which is the defect this split exists to remove.
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
            ["telegram_personal", "whatsapp_personal", "signal_personal", "imessage_personal", "wechat_personal", "discord_personal"],
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

        self.assertEqual(
            [entry["stage"] for entry in personal_catalog],
            # Signal, iMessage, and WeChat are all first-class,
            # owner-connectable local-bridge gateway channels like
            # Telegram/WhatsApp — none are "coming soon". Signal's catalog
            # entry previously said "planned"/live_capable=false here while
            # its handler, gateway runtime, and signal-cli bridge were
            # already fully wired — that mismatch (not any real capability
            # gap) is what made Signal read as disabled everywhere this
            # roadmap feeds; see get_gateway_personal_channel_surfaces.
            ["live", "live", "live", "live", "live", "live"],
        )
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
        self.assertEqual(
            [entry["live_capable"] for entry in personal_catalog],
            ["true", "true", "true", "true", "true", "true"],
        )

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

        # 26 first-party entries, plus one per channel the pinned OpenClaw
        # carries — ALL of them, including the ones a first-party runtime
        # still owns, so the UI can show "carried by the transport, superseded
        # here" rather than leaving them invisible. Counted from the derived
        # set rather than re-typed, but the first-party 26 stays exact.
        openclaw_entries = [
            entry
            for entry in catalog
            if entry["provider"] == service.OPENCLAW_TRANSPORT_PROVIDER
        ]
        self.assertTrue(openclaw_entries)
        self.assertEqual(len(catalog) - len(openclaw_entries), 26)
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
        self.assertEqual(by_key["telegram_personal"]["runtime_lane"], service.PERSONAL_GATEWAY_RUNTIME_LANE)
        self.assertTrue(by_key["telegram_personal"]["requires_agent_computer"])
        self.assertEqual(by_key["telegram_personal"]["surface_kind"], "messaging_channel")
        self.assertEqual(by_key["telegram_personal"]["product_surface"], "personal_messaging")
        self.assertEqual(by_key["telegram_personal"]["navigation_group"], "personal_messaging")
        self.assertEqual(by_key["telegram_personal"]["ownership_boundary"], "agent_computer")
        self.assertEqual(by_key["whatsapp_personal"]["surface_support"], ["sage"])
        self.assertEqual(by_key["signal_personal"]["status"], "agent_computer_bridge")
        self.assertTrue(by_key["signal_personal"]["live_capable"])
        self.assertTrue(by_key["signal_personal"]["launch_allowed"])
        # Signal, iMessage, and WeChat are all shipped, owner-connectable
        # gateway channels (like Telegram/WhatsApp) — live_capable/
        # launch_allowed are true for all three. The "agent_computer_bridge"
        # status (not "agent_computer_only") is the honest bit that
        # survives: each requires a real bridge process on the agent's own
        # gateway (signal-cli for Signal; BlueBubbles on a Mac for iMessage;
        # a best-effort local WeChat session bridge for WeChat) rather than
        # a cloud path — a deployment that hasn't configured that bridge's
        # env vars still reads "not configured" via the bridge's own health
        # check, not this catalog flag.
        self.assertEqual(by_key["imessage_personal"]["provider"], "bluebubbles_local_bridge")
        self.assertEqual(by_key["imessage_personal"]["status"], "agent_computer_bridge")
        self.assertTrue(by_key["imessage_personal"]["live_capable"])
        self.assertTrue(by_key["imessage_personal"]["launch_allowed"])
        self.assertEqual(by_key["wechat_personal"]["provider"], "wechat_local_bridge")
        self.assertEqual(by_key["wechat_personal"]["status"], "agent_computer_bridge")
        self.assertTrue(by_key["wechat_personal"]["live_capable"])
        self.assertTrue(by_key["wechat_personal"]["launch_allowed"])
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
        # signal_personal is not part of this ruling and stays pre-existing
        # here; imessage_personal and wechat_personal are the two channels
        # this contract promotes to first-class/live_capable.
        for channel_key, provider in (
            ("signal_personal", "signal_local_bridge"),
            ("imessage_personal", "bluebubbles_local_bridge"),
            ("wechat_personal", "wechat_local_bridge"),
        ):
            spec = service.assert_personal_gateway_channel(channel_key, provider)

            self.assertEqual(spec["runtime_lane"], service.PERSONAL_GATEWAY_RUNTIME_LANE)
            self.assertEqual(spec["memory_surface"], service.DIRECT_CHAT_MEMORY_SURFACE)
            self.assertEqual(spec["live_capable"], "true")

    def test_agent_computer_bridge_channels_pass_personal_preflight(self) -> None:
        """iMessage and WeChat are shipped, full-account gateway channels —
        like Telegram/WhatsApp, they pass preflight with launch_allowed=True.
        (Signal also currently passes here; that predates and is independent
        of this iMessage/WeChat ruling.)"""
        for channel_key in ("signal_personal", "imessage_personal", "wechat_personal"):
            preflight = service.personal_bridge_preflight(channel_key)

            self.assertEqual(preflight["status"], "pass")
            self.assertTrue(preflight["launch_allowed"])
            self.assertEqual(preflight["reason"], "live_personal_gateway_runtime")

        telegram_preflight = service.personal_bridge_preflight("telegram_personal")
        self.assertEqual(telegram_preflight["status"], "pass")
        self.assertTrue(telegram_preflight["launch_allowed"])


if __name__ == "__main__":
    unittest.main()
