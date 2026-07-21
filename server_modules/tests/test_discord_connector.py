import importlib.util
import json
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from server_modules.connectors import discord_connector


ROOT_DIR = Path(__file__).resolve().parents[2]
MODULE_PATH = ROOT_DIR / "server_modules" / "direct_chat_runtime_exports.py"
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))
spec = importlib.util.spec_from_file_location("operator_chat_discord_under_test", MODULE_PATH)
operator_chat = importlib.util.module_from_spec(spec)
sys.modules["operator_chat_discord_under_test"] = operator_chat
assert spec and spec.loader
spec.loader.exec_module(operator_chat)


class DiscordConnectorTests(unittest.TestCase):
    def test_send_message_requires_approval(self):
        payload = operator_chat._build_direct_tool_approval_response(
            tool_calls=[
                {
                    "name": "discord_bot__send_message",
                    "arguments": json.dumps({"input": "{\"channel_id\":\"123\",\"message\":\"hello from discord\"}"}),
                }
            ],
            tool_capabilities=[
                {
                    "id": "discord_bot",
                    "label": "Discord Bot",
                    "connected": True,
                    "authenticated": True,
                    "runtime_usable": True,
                    "read_actions": ["guilds.read", "guild_channels.read"],
                    "write_actions": [
                        "send_message",
                        "send_dm",
                        "edit_message",
                        "delete_message",
                        "list_guilds",
                        "list_channels",
                        "list_members",
                        "get_message_history",
                        "create_thread",
                        "add_reaction",
                    ],
                    "approval_required_actions": ["send_message", "send_dm", "delete_message"],
                }
            ],
        )

        self.assertIsNotNone(payload)
        assert payload is not None
        self.assertEqual(payload["mode"], "answer_with_action")
        self.assertEqual(payload["actions"][0]["connector"], "discord_bot")
        self.assertEqual(payload["actions"][0]["action"], "send_message")

    def test_inbound_message_triggers_handler(self):
        parsed = discord_connector.parse_inbound_event(
            {
                "t": "MESSAGE_CREATE",
                "d": {
                    "id": "msg-1",
                    "channel_id": "123",
                    "guild_id": "456",
                    "content": "<@999> please investigate this",
                    "author": {"id": "321", "username": "alice"},
                    "mentions": [{"id": "999"}],
                },
            }
        )

        appended = []
        executed = []

        def fake_append_event(**kwargs):
            appended.append(kwargs)
            return kwargs

        def fake_execute_agent_turn_request(*, turn_request):
            executed.append(turn_request)
            return {"run_id": "run-discord-1"}

        result = discord_connector.dispatch_inbound_event(
            parsed,
            # metadata.bot_id="999" matches the mention in the payload above
            # (<@999> / mentions:[{"id":"999"}]) — this is the bot's own id
            # being mentioned, the addressed-to-us case that must trigger.
            connector_entry={"id": "cred-discord", "workspace_id": "default", "metadata": {"bot_id": "999"}},
            credentials={"bot_token": "discord-token", "channel_id": "123", "guild_id": "456"},
            append_event_fn=fake_append_event,
            execute_agent_turn_request=fake_execute_agent_turn_request,
        )

        self.assertTrue(result["triggered"])
        self.assertEqual(result["run_id"], "run-discord-1")
        self.assertEqual(executed[0].message, "please investigate this")
        self.assertEqual(appended[0]["channel"], "discord")
        self.assertEqual(appended[0]["direction"], "inbound")

    def test_inbound_message_prefers_canonical_run_start_request_when_available(self):
        parsed = discord_connector.parse_inbound_event(
            {
                "t": "MESSAGE_CREATE",
                "d": {
                    "id": "msg-1",
                    "channel_id": "123",
                    "guild_id": "456",
                    "content": "<@999> please investigate this",
                    "author": {"id": "321", "username": "alice"},
                    "mentions": [{"id": "999"}],
                },
            }
        )

        captured = {}
        def fake_start_run(request):
            captured["request"] = request
            return {"run_id": "run-discord-2", "status": "starting"}

        result = discord_connector.dispatch_inbound_event(
            parsed,
            # metadata.bot_id="999" matches the mention in the payload above
            # — the bot's own id being mentioned, which must trigger.
            connector_entry={"id": "cred-discord", "workspace_id": "default", "metadata": {"bot_id": "999"}},
            credentials={"bot_token": "discord-token", "channel_id": "123", "guild_id": "456"},
            append_event_fn=None,
            run_start_request_class=lambda **kwargs: SimpleNamespace(**kwargs),
            start_run_request=fake_start_run,
        )

        self.assertTrue(result["triggered"])
        self.assertEqual(result["run_id"], "run-discord-2")
        self.assertEqual(captured["request"].workspace_id, "default")
        self.assertEqual(captured["request"].user_goal, "please investigate this")
        self.assertEqual(captured["request"].metadata["channel"], "discord")
        self.assertEqual(captured["request"].metadata["agent_turn_request"]["channel"], "discord")
        self.assertEqual(captured["request"].metadata["agent_turn_request"]["message"], "please investigate this")

    def test_inbound_message_requires_canonical_ingress_callbacks(self):
        parsed = discord_connector.parse_inbound_event(
            {
                "t": "MESSAGE_CREATE",
                "d": {
                    "id": "msg-1",
                    "channel_id": "123",
                    "guild_id": "456",
                    "content": "<@999> please investigate this",
                    "author": {"id": "321", "username": "alice"},
                    "mentions": [{"id": "999"}],
                },
            }
        )

        with self.assertRaises(RuntimeError):
            discord_connector.dispatch_inbound_event(
                parsed,
                # metadata.bot_id="999" matches the mention in the payload
                # above, so this event actually triggers and reaches the
                # "no canonical ingress callback configured" RuntimeError
                # this test exists to prove.
                connector_entry={"id": "cred-discord", "workspace_id": "default", "metadata": {"bot_id": "999"}},
                credentials={"bot_token": "discord-token", "channel_id": "123", "guild_id": "456"},
                append_event_fn=None,
            )

    def test_guild_list_parsed(self):
        calls = []

        def fake_request(url, **kwargs):
            calls.append((url, kwargs))
            return {
                "status": 200,
                "json": [
                    {
                        "id": "1",
                        "name": "Acme Guild",
                        "owner": True,
                    }
                ],
            }

        result = discord_connector.list_guilds(
            {"bot_token": "discord-token"},
            limit=20,
            http_json_request=fake_request,
        )

        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["name"], "Acme Guild")
        self.assertIn("/users/@me/guilds?limit=20", calls[0][0])

    # ── Outbound 429 rate-limit retry ───────────────────────────────────

    def test_send_message_retries_after_429_then_succeeds(self):
        """A single 429 (Discord's documented shape: JSON `retry_after` +
        `Retry-After` header) must be retried in place, not raised."""
        calls = []

        def fake_request(url, **kwargs):
            calls.append((url, kwargs))
            if len(calls) == 1:
                return {
                    "status": 429,
                    "json": {"message": "You are being rate limited.", "retry_after": 0.25, "global": False},
                    "headers": {"Retry-After": "1"},
                }
            return {"status": 200, "json": {"id": "msg-1", "content": "hi"}}

        with patch("server_modules.connectors.discord_connector.time.sleep") as mock_sleep:
            result = discord_connector.send_message(
                {"bot_token": "discord-token"},
                "123",
                "hi",
                http_json_request=fake_request,
            )

        self.assertEqual(len(calls), 2, "should retry exactly once after the 429")
        self.assertEqual(result, {"id": "msg-1", "content": "hi"})
        # The JSON body's sub-second retry_after (0.25s) is authoritative
        # over the coarser 1s Retry-After header.
        mock_sleep.assert_called_once_with(0.25)

    def test_send_message_gives_up_after_exhausting_429_retries(self):
        """Persistent 429s must still raise — never retry unboundedly."""
        calls = []

        def fake_request(url, **kwargs):
            calls.append((url, kwargs))
            return {
                "status": 429,
                "json": {"message": "You are being rate limited.", "retry_after": 0.1, "global": False},
                "headers": {},
            }

        with patch("server_modules.connectors.discord_connector.time.sleep") as mock_sleep:
            with self.assertRaises(RuntimeError):
                discord_connector.send_message(
                    {"bot_token": "discord-token"},
                    "123",
                    "hi",
                    http_json_request=fake_request,
                )

        self.assertEqual(
            len(calls),
            discord_connector._DISCORD_RATE_LIMIT_MAX_ATTEMPTS,
            "must stop at the bounded attempt cap, never retry forever",
        )
        # One sleep between each pair of attempts, never after the last.
        self.assertEqual(mock_sleep.call_count, discord_connector._DISCORD_RATE_LIMIT_MAX_ATTEMPTS - 1)

    def test_send_message_retry_wait_is_capped_per_attempt(self):
        """A pathological Retry-After from Discord must be clamped, never
        turned into an effectively unbounded single wait."""
        calls = []

        def fake_request(url, **kwargs):
            calls.append((url, kwargs))
            if len(calls) == 1:
                return {
                    "status": 429,
                    "json": {"message": "rate limited", "retry_after": 9999.0, "global": True},
                    "headers": {},
                }
            return {"status": 200, "json": {"id": "msg-2"}}

        with patch("server_modules.connectors.discord_connector.time.sleep") as mock_sleep:
            result = discord_connector.send_message(
                {"bot_token": "discord-token"},
                "123",
                "hi",
                http_json_request=fake_request,
            )

        self.assertEqual(result, {"id": "msg-2"})
        mock_sleep.assert_called_once_with(discord_connector._DISCORD_RATE_LIMIT_MAX_SLEEP_SECONDS)

    def test_non_429_error_status_does_not_retry(self):
        """A plain 4xx/5xx (not a rate limit) must fail immediately — the
        retry path is 429-specific, not a generic error retry."""
        calls = []

        def fake_request(url, **kwargs):
            calls.append((url, kwargs))
            return {"status": 500, "json": {"message": "internal error"}}

        with patch("server_modules.connectors.discord_connector.time.sleep") as mock_sleep:
            with self.assertRaises(RuntimeError):
                discord_connector.send_message(
                    {"bot_token": "discord-token"},
                    "123",
                    "hi",
                    http_json_request=fake_request,
                )

        self.assertEqual(len(calls), 1)
        mock_sleep.assert_not_called()

    # ── Deduplication guard tests ──────────────────────────────────────

    def test_dedup_first_call_not_duplicate(self):
        """First call for a message_id should return False (not a duplicate)."""
        discord_connector._clear_discord_dedup_cache()
        result = discord_connector._is_duplicate_discord_message("msg-001", "discord_personal")
        self.assertFalse(result, "First call should not be a duplicate")

    def test_dedup_second_call_is_duplicate(self):
        """Second call for the same (message_id, channel_origin) should return True."""
        discord_connector._clear_discord_dedup_cache()
        self.assertFalse(discord_connector._is_duplicate_discord_message("msg-002", "discord_personal"))
        self.assertTrue(discord_connector._is_duplicate_discord_message("msg-002", "discord_personal"))

    def test_dedup_different_channel_origin_not_duplicate(self):
        """Same message_id on different channel_origins are not duplicates."""
        discord_connector._clear_discord_dedup_cache()
        self.assertFalse(discord_connector._is_duplicate_discord_message("msg-003", "discord_personal"))
        self.assertFalse(discord_connector._is_duplicate_discord_message("msg-003", "discord_guild"))

    def test_dedup_empty_message_id_not_duplicate(self):
        """Empty or None message_id always returns False."""
        discord_connector._clear_discord_dedup_cache()
        self.assertFalse(discord_connector._is_duplicate_discord_message("", "discord_personal"))
        self.assertFalse(discord_connector._is_duplicate_discord_message("", "discord_personal"))

    def test_dedup_expired_entry_reprocessed(self):
        """An entry whose TTL has expired should be reprocessed."""
        discord_connector._clear_discord_dedup_cache()
        self.assertFalse(discord_connector._is_duplicate_discord_message("msg-004", "discord_personal"))
        # Artificially age the entry beyond TTL
        import time as _t
        key = "msg-004:discord_personal"
        discord_connector._DEDUP_CACHE[key] = _t.time() - discord_connector._DEDUP_TTL_SECONDS - 10
        self.assertFalse(discord_connector._is_duplicate_discord_message("msg-004", "discord_personal"))

    def test_dedup_max_size_eviction(self):
        """When the cache exceeds max size, stale entries are evicted."""
        discord_connector._clear_discord_dedup_cache()
        import time as _t
        # Fill the cache with stale entries + one fresh one
        discord_connector._DEDUP_MAX_SIZE = 5  # temporarily lower for test
        for i in range(10):
            key = f"stale-{i}:discord_personal"
            discord_connector._DEDUP_CACHE[key] = _t.time() - discord_connector._DEDUP_TTL_SECONDS - 60
        # A new unique message should trigger eviction
        result = discord_connector._is_duplicate_discord_message("fresh-msg", "discord_personal")
        self.assertFalse(result)
        # The cache shouldn't have grown; stale entries should be gone
        self.assertLessEqual(len(discord_connector._DEDUP_CACHE), 12)
        # Reset for other tests
        discord_connector._clear_discord_dedup_cache()
        discord_connector._DEDUP_MAX_SIZE = 2000

    # ── FIX 1: guild @mention/reply must be addressed to THIS bot ──────
    # Before this fix, _mention_ids_from_payload set message_type="mention"
    # for ANY non-empty mentions list, and should_trigger_agent_run treated
    # every "mention" as triggering — so a guild message mentioning some
    # unrelated member made the agent reply anyway. The bot's own id was
    # never compared against the mention list anywhere.

    def _guild_mention_payload(self, *, mention_ids, text="hello", referenced_message=None):
        data = {
            "id": "msg-fix1",
            "channel_id": "123",
            "guild_id": "456",
            "content": text,
            "author": {"id": "user-1", "username": "alice"},
            "mentions": [{"id": mid} for mid in mention_ids],
        }
        if referenced_message is not None:
            data["referenced_message"] = referenced_message
        return discord_connector.parse_inbound_event({"t": "MESSAGE_CREATE", "d": data})

    def test_guild_mention_of_non_bot_user_does_not_trigger(self):
        """Mentioning some OTHER guild member (not the bot) must stay silent."""
        parsed = self._guild_mention_payload(mention_ids=["555"], text="<@555> can you take this")
        self.assertEqual(parsed["message_type"], "mention")
        self.assertFalse(
            discord_connector.should_trigger_agent_run(
                parsed,
                {"bot_token": "t", "channel_id": "123", "guild_id": "456"},
                metadata={"bot_id": "999"},
            )
        )

    def test_guild_mention_of_bot_triggers(self):
        """Mentioning the bot's own id must trigger."""
        parsed = self._guild_mention_payload(mention_ids=["999"], text="<@999> please investigate")
        self.assertEqual(parsed["message_type"], "mention")
        self.assertTrue(
            discord_connector.should_trigger_agent_run(
                parsed,
                {"bot_token": "t", "channel_id": "123", "guild_id": "456"},
                metadata={"bot_id": "999"},
            )
        )

    def test_guild_mention_of_bot_among_others_still_triggers(self):
        """The bot being ONE of several mentions is still addressed to it."""
        parsed = self._guild_mention_payload(mention_ids=["555", "999"], text="<@555> <@999> thoughts?")
        self.assertTrue(
            discord_connector.should_trigger_agent_run(
                parsed,
                {"bot_token": "t", "channel_id": "123", "guild_id": "456"},
                metadata={"bot_id": "999"},
            )
        )

    def test_guild_reply_to_bot_message_triggers_without_explicit_mention(self):
        """A reply to one of the bot's own prior messages triggers even with
        no @mention text at all (Discord replies don't always carry one)."""
        parsed = self._guild_mention_payload(
            mention_ids=[],
            text="sounds good, do it",
            referenced_message={"author": {"id": "999"}},
        )
        self.assertEqual(parsed["message_type"], "mention")
        self.assertTrue(
            discord_connector.should_trigger_agent_run(
                parsed,
                {"bot_token": "t", "channel_id": "123", "guild_id": "456"},
                metadata={"bot_id": "999"},
            )
        )

    def test_guild_reply_to_someone_elses_message_does_not_trigger(self):
        """A reply to a DIFFERENT member's message (not the bot's) stays silent."""
        parsed = self._guild_mention_payload(
            mention_ids=[],
            text="sounds good",
            referenced_message={"author": {"id": "555"}},
        )
        self.assertFalse(
            discord_connector.should_trigger_agent_run(
                parsed,
                {"bot_token": "t", "channel_id": "123", "guild_id": "456"},
                metadata={"bot_id": "999"},
            )
        )

    def test_guild_mention_fails_closed_when_bot_id_is_unresolvable(self):
        """With no bot_id configured anywhere (metadata, credentials), a
        mention must NOT trigger — unknown identity fails closed, not open."""
        parsed = self._guild_mention_payload(mention_ids=["999"], text="<@999> hello")
        self.assertFalse(
            discord_connector.should_trigger_agent_run(
                parsed,
                {"bot_token": "t", "channel_id": "123", "guild_id": "456"},
                metadata={},
            )
        )

    def test_resolve_discord_bot_id_prefers_metadata_then_credentials_then_application_id(self):
        self.assertEqual(
            discord_connector._resolve_discord_bot_id({"bot_id": "from-creds"}, {"bot_id": "from-metadata"}),
            "from-metadata",
        )
        self.assertEqual(discord_connector._resolve_discord_bot_id({"bot_id": "from-creds"}, {}), "from-creds")
        self.assertEqual(
            discord_connector._resolve_discord_bot_id({"application_id": "app-1"}, {}),
            "app-1",
        )
        self.assertEqual(discord_connector._resolve_discord_bot_id({}, {}), "")

    # ── FIX 2: Group DM must be mention/reply-gated, true 1:1 DM stays open ──
    # Before this fix, DiscordGatewayListener.on_message's `guild is None`
    # check treated a multi-party Group DM exactly like a true 1:1 DM and
    # replied to every message, unconditionally, from any participant.

    def test_is_group_dm_message_true_via_recipients_list(self):
        message = SimpleNamespace(
            guild=None,
            channel=SimpleNamespace(recipients=[SimpleNamespace(id="1"), SimpleNamespace(id="2")]),
        )
        self.assertTrue(discord_connector._is_group_dm_message(message))

    def test_is_group_dm_message_true_via_channel_type_group(self):
        message = SimpleNamespace(guild=None, channel=SimpleNamespace(type=SimpleNamespace(name="group")))
        self.assertTrue(discord_connector._is_group_dm_message(message))

    def test_is_group_dm_message_false_for_true_one_on_one_dm(self):
        message = SimpleNamespace(
            guild=None,
            channel=SimpleNamespace(recipients=[SimpleNamespace(id="1")], type=SimpleNamespace(name="private")),
        )
        self.assertFalse(discord_connector._is_group_dm_message(message))

    def test_is_group_dm_message_false_for_guild_channel(self):
        message = SimpleNamespace(guild=SimpleNamespace(id="456"), channel=SimpleNamespace(recipients=[1, 2, 3]))
        self.assertFalse(discord_connector._is_group_dm_message(message))

    def _group_dm_payload(self, *, mention_ids=(), text="hello", referenced_message=None):
        data = {
            "id": "msg-groupdm",
            "channel_id": "gdm-1",
            "guild_id": "",
            "content": text,
            "author": {"id": "user-1", "username": "alice"},
            "mentions": [{"id": mid} for mid in mention_ids],
            "is_group_dm": True,
        }
        if referenced_message is not None:
            data["referenced_message"] = referenced_message
        return discord_connector.parse_inbound_event({"t": "MESSAGE_CREATE", "d": data})

    def test_group_dm_message_not_addressing_bot_stays_silent(self):
        """A Group DM message that neither mentions nor replies to the bot
        must NOT trigger — this is the exact bug: zero addressing check
        meant every Group DM message triggered a reply."""
        parsed = self._group_dm_payload(text="what time works for everyone?")
        self.assertIsNone(parsed["guild_id"])
        self.assertNotEqual(parsed["message_type"], "direct_message", "a Group DM must not collapse into the unconditional 1:1 DM path")
        self.assertFalse(
            discord_connector.should_trigger_agent_run(
                parsed,
                {"bot_token": "t"},
                metadata={"bot_id": "999"},
            )
        )

    def test_group_dm_message_mentioning_bot_triggers(self):
        """A Group DM message that DOES @mention the bot still triggers —
        proves the gate isn't over-broad, only unaddressed messages are silent."""
        parsed = self._group_dm_payload(mention_ids=["999"], text="<@999> what do you think?")
        self.assertTrue(
            discord_connector.should_trigger_agent_run(
                parsed,
                {"bot_token": "t"},
                metadata={"bot_id": "999"},
            )
        )

    def test_true_one_on_one_dm_still_triggers_unconditionally(self):
        """A real 1:1 DM (is_group_dm not set) keeps triggering regardless
        of mention — Group DM gating must not regress this."""
        parsed = discord_connector.parse_inbound_event(
            {
                "t": "MESSAGE_CREATE",
                "d": {
                    "id": "msg-dm-1",
                    "channel_id": "dm-1",
                    "guild_id": "",
                    "content": "hey, are you there?",
                    "author": {"id": "user-1", "username": "alice"},
                    "mentions": [],
                },
            }
        )
        self.assertEqual(parsed["message_type"], "direct_message")
        self.assertTrue(
            discord_connector.should_trigger_agent_run(
                parsed,
                {"bot_token": "t"},
                metadata={},
            )
        )


class DiscordDmGatewayHandlerTests(unittest.IsolatedAsyncioTestCase):
    """Live coverage for _handle_dm_via_gateway — the CANONICAL, currently
    active Discord 1:1 DM handler (Path C, see its docstring). Touched by
    FIX 2 (on_message now routes only a true 1:1 DM here, a Group DM takes
    the mention-gated path instead) — added because no test previously
    exercised this handler at all.

    NOTE: test_discord_sage_ingress.py has 5 pre-existing failures at this
    baseline commit — it tests an OLDER DM handler location
    (DiscordBotRuntimeService.handle_parsed_event) that a prior refactor
    moved away from; those failures are unrelated to this fix and are left
    alone (out of scope — see the task's verification notes).
    """

    def setUp(self):
        discord_connector._clear_discord_dedup_cache()

    def _make_message(self, *, text, author_id="user-1", author_name="alice", message_id="dm-msg-1"):
        sent: list[str] = []

        async def _send(content):
            sent.append(content)

        message = SimpleNamespace(
            content=text,
            id=message_id,
            author=SimpleNamespace(id=author_id, name=author_name),
            channel=SimpleNamespace(send=_send),
        )
        return message, sent

    async def test_dm_without_paired_workspace_prompts_for_pair_code(self):
        message, sent = self._make_message(text="hello there")
        with patch(
            "server_modules.discord_pairing_service.get_workspace_for_discord_user",
            return_value="",
        ):
            await discord_connector._handle_dm_via_gateway(message)

        self.assertEqual(len(sent), 1)
        self.assertIn("/pair", sent[0])

    async def test_dm_with_paired_workspace_routes_through_sage_and_replies(self):
        sage_result = SimpleNamespace(message="Nothing urgent right now.")
        message, sent = self._make_message(text="what's on my plate today")
        with (
            patch(
                "server_modules.discord_pairing_service.get_workspace_for_discord_user",
                return_value="ws-1",
            ),
            patch(
                "server_modules.sage_command_dispatcher.dispatch_command",
                new=AsyncMock(return_value=None),
            ),
            patch(
                "server_modules.sage_turn_adapter.execute_sage_turn",
                new=AsyncMock(return_value=sage_result),
            ) as mock_turn,
            patch(
                "server_modules.channel_adapter.filter_channel_outbound_reply",
                side_effect=lambda text: text,
            ),
        ):
            await discord_connector._handle_dm_via_gateway(message)

        mock_turn.assert_awaited_once()
        call_kwargs = mock_turn.await_args.kwargs
        self.assertEqual(call_kwargs["workspace_id"], "ws-1")
        self.assertEqual(call_kwargs["channel_origin"], "discord_personal")
        self.assertEqual(call_kwargs["channel_sender_id"], "user-1")
        self.assertEqual(sent, ["Nothing urgent right now."])

    async def test_pair_command_links_workspace_and_confirms(self):
        message, sent = self._make_message(text="/pair ABC123")
        with (
            patch(
                "server_modules.sage_telegram_hosted_service.consume_pairing_code",
                return_value="ws-9",
            ),
            patch("server_modules.discord_pairing_service.pair_discord_workspace") as mock_pair,
        ):
            await discord_connector._handle_dm_via_gateway(message)

        mock_pair.assert_called_once_with("user-1", "ws-9")
        self.assertEqual(len(sent), 1)
        self.assertIn("Connected", sent[0])

    async def test_duplicate_message_id_is_not_double_processed(self):
        """A retried/duplicate gateway delivery of the same DM message id
        must not run a second Sage turn or send a second reply."""
        sage_result = SimpleNamespace(message="Reply once.")
        message, sent = self._make_message(text="hi", message_id="dm-dedup-1")
        with (
            patch(
                "server_modules.discord_pairing_service.get_workspace_for_discord_user",
                return_value="ws-1",
            ),
            patch(
                "server_modules.sage_command_dispatcher.dispatch_command",
                new=AsyncMock(return_value=None),
            ),
            patch(
                "server_modules.sage_turn_adapter.execute_sage_turn",
                new=AsyncMock(return_value=sage_result),
            ) as mock_turn,
            patch(
                "server_modules.channel_adapter.filter_channel_outbound_reply",
                side_effect=lambda text: text,
            ),
        ):
            await discord_connector._handle_dm_via_gateway(message)
            await discord_connector._handle_dm_via_gateway(message)

        mock_turn.assert_awaited_once()
        self.assertEqual(sent, ["Reply once."])


if __name__ == "__main__":
    unittest.main()
