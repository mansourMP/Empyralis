from __future__ import annotations

import os
import tempfile
import unittest
from unittest.mock import patch

from scripts.orion_terminal.tests.helpers import FakeUI, assert_snapshot
from scripts.orion_terminal import flows_configure, flows_orion_setup, flows_preflight, flows_run
from scripts.orion_terminal.state.workspace import load_workspace_state, save_workspace_state


class _FakeRuntime:
    def list_provider_catalog(self):
        return []


class FlowIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.prev_no_color = os.environ.get("NO_COLOR")
        self.prev_log = os.environ.get("ORION_WIZARD_SESSION_LOG")
        self.prev_state_dir = os.environ.get("ORION_CLI_STATE_DIR")
        self._tmp_state = tempfile.TemporaryDirectory()
        os.environ["NO_COLOR"] = "1"
        os.environ["ORION_WIZARD_SESSION_LOG"] = "0"
        os.environ["ORION_CLI_STATE_DIR"] = self._tmp_state.name

    def tearDown(self) -> None:
        if self.prev_no_color is None:
            os.environ.pop("NO_COLOR", None)
        else:
            os.environ["NO_COLOR"] = self.prev_no_color
        if self.prev_log is None:
            os.environ.pop("ORION_WIZARD_SESSION_LOG", None)
        else:
            os.environ["ORION_WIZARD_SESSION_LOG"] = self.prev_log
        if self.prev_state_dir is None:
            os.environ.pop("ORION_CLI_STATE_DIR", None)
        else:
            os.environ["ORION_CLI_STATE_DIR"] = self.prev_state_dir
        self._tmp_state.cleanup()

    def test_orion_setup_minimal_snapshot(self) -> None:
        ui = FakeUI(
            choose_keys=["local", "skip"],
            inputs=["phase4 setup goal"],
            confirms=[True],
        )
        captured: dict = {}

        def _fake_run_once(**kwargs):
            captured.update(kwargs)
            return 0

        with (
            patch("scripts.orion_terminal.flows_orion_setup._existing_config_summary", return_value=""),
            patch("scripts.orion_terminal.flows_orion_setup._setup_create_session", return_value={"id": "sess-1"}),
            patch("scripts.orion_terminal.flows_orion_setup._setup_action", return_value={}),
            patch("scripts.orion_terminal.flows_orion_setup._setup_complete", return_value={}),
            patch("scripts.orion_terminal.flows_orion_setup.persist_wizard_session", return_value=None),
            patch("scripts.orion_terminal.flows_orion_setup.run_once", side_effect=_fake_run_once),
        ):
            rc = flows_orion_setup.orion_setup_flow(
                ui=ui,
                api_url="http://runtime",
                api_key="key",
                workspace_id="default",
                engine="orion",
            )
        self.assertEqual(rc, 0)
        self.assertEqual(captured.get("goal"), "phase4 setup goal")
        self.assertEqual(captured.get("target").key, "local_companion")
        assert_snapshot(ui.render(), "flow_orion_setup_minimal")

    def test_orion_setup_uses_fixed_sections_in_lean_setup(self) -> None:
        ui = FakeUI(
            choose_keys=[
                "local",
                "skip",
            ],
            inputs=["goal ok"],
        )

        captured: dict = {}

        def _fake_run_once(**kwargs):
            captured.update(kwargs)
            return 0

        with (
            patch("scripts.orion_terminal.flows_orion_setup._existing_config_summary", return_value=""),
            patch("scripts.orion_terminal.flows_orion_setup._setup_create_session", return_value={"id": "sess-1"}),
            patch("scripts.orion_terminal.flows_orion_setup._setup_action", return_value={}),
            patch("scripts.orion_terminal.flows_orion_setup._setup_complete", return_value={}),
            patch("scripts.orion_terminal.flows_orion_setup.persist_wizard_session", return_value=None),
            patch("scripts.orion_terminal.flows_orion_setup.run_once", side_effect=_fake_run_once),
        ):
            rc = flows_orion_setup.orion_setup_flow(
                ui=ui,
                api_url="http://runtime",
                api_key="key",
                workspace_id="default",
                engine="orion",
            )

        self.assertEqual(rc, 0)
        metadata = captured.get("extra_metadata") or {}
        self.assertEqual(metadata.get("configured_sections"), ["model", "channels"])
        self.assertNotIn("Select at least 1 section", ui.render())

    def test_orion_setup_lean_path_skips_provider_picker_and_model_filter_prompt(self) -> None:
        ui = FakeUI(
            choose_keys=["local", "skip"],
        )
        captured: dict = {}

        def _fake_run_once(**kwargs):
            captured.update(kwargs)
            return 0

        with (
            patch("scripts.orion_terminal.flows_orion_setup._existing_config_summary", return_value=""),
            patch("scripts.orion_terminal.flows_orion_setup._setup_create_session", return_value={"id": "sess-1"}),
            patch("scripts.orion_terminal.flows_orion_setup._setup_action", return_value={}),
            patch("scripts.orion_terminal.flows_orion_setup._setup_complete", return_value={}),
            patch("scripts.orion_terminal.flows_orion_setup.persist_wizard_session", return_value=None),
            patch("scripts.orion_terminal.flows_orion_setup._detect_openai_oauth_env_token", return_value=("token", "env")),
            patch("scripts.orion_terminal.flows_orion_setup._prompt_provider_auth_choice_grouped") as provider_picker_mock,
            patch("scripts.orion_terminal.flows_orion_setup._model_filter_choices") as model_filter_mock,
            patch("scripts.orion_terminal.flows_orion_setup.run_once", side_effect=_fake_run_once),
        ):
            rc = flows_orion_setup.orion_setup_flow(
                ui=ui,
                api_url="http://runtime",
                api_key="key",
                workspace_id="default",
                engine="orion",
            )

        self.assertEqual(rc, 0)
        provider_picker_mock.assert_not_called()
        model_filter_mock.assert_not_called()
        metadata = captured.get("extra_metadata") or {}
        self.assertEqual(metadata.get("provider_group"), "openai")
        self.assertEqual(metadata.get("model_filter_provider"), "openai")
        rendered = ui.render()
        self.assertIn("Provider: OpenAI", rendered)
        self.assertIn("Model filter: openai", rendered)

    def test_orion_setup_codex_recovery_setup_later_path(self) -> None:
        ui = FakeUI(
            choose_keys=["local", "skip", "skip"],
        )

        with (
            patch("scripts.orion_terminal.flows_orion_setup._existing_config_summary", return_value=""),
            patch("scripts.orion_terminal.flows_orion_setup._setup_create_session", return_value={"id": "sess-1"}),
            patch("scripts.orion_terminal.flows_orion_setup._setup_action", return_value={}),
            patch("scripts.orion_terminal.flows_orion_setup._setup_complete", return_value={}),
            patch("scripts.orion_terminal.flows_orion_setup.persist_wizard_session", return_value=None),
            patch("scripts.orion_terminal.flows_orion_setup._detect_openai_oauth_env_token", return_value=("", "")),
            patch("scripts.orion_terminal.flows_orion_setup._model_filter_choices") as model_filter_mock,
            patch("scripts.orion_terminal.flows_orion_setup.run_once", return_value=0),
        ):
            rc = flows_orion_setup.orion_setup_flow(
                ui=ui,
                api_url="http://runtime",
                api_key="key",
                workspace_id="default",
                engine="orion",
            )

        self.assertEqual(rc, 0)
        model_filter_mock.assert_not_called()
        rendered = ui.render()
        self.assertIn("No existing Codex session found", rendered)
        self.assertIn("Auth recovery", rendered)
        self.assertIn("Credential source: later", rendered)

    def test_preflight_minimal_snapshot(self) -> None:
        # Step 1: Location (local companion)
        # Step 2: Trust (auto)
        # Step 3: Operator safety (default)
        # Step 4: Provider ("" -> runtime default)
        # (Step 4: Credential source is skipped because provider is empty)
        # Step 5: Connectors (later)
        # Step 6: File Scope (workspace)
        ui = FakeUI(
            choose_keys=["local_companion", "auto", "default", "", "runtime", "later", "workspace"],
            confirms=[True],
        )
        captured: dict = {}

        def _fake_run_once(**kwargs):
            captured.update(kwargs)
            return 0

        with (
            patch("scripts.orion_terminal.flows_preflight._runtime_client", return_value=_FakeRuntime()),
            patch("scripts.orion_terminal.flows_preflight.persist_wizard_session", return_value=None),
            patch("scripts.orion_terminal.flows_preflight.run_once", side_effect=_fake_run_once),
        ):
            rc = flows_preflight.preflight_flow(
                ui=ui,
                api_url="http://runtime",
                api_key="key",
                workspace_id="default",
                engine="orion",
                goal="phase4 preflight goal",
                auto_confirm=False,
            )
        self.assertEqual(rc, 0)
        self.assertEqual(captured.get("goal"), "phase4 preflight goal")
        metadata = captured.get("extra_metadata") or {}
        self.assertEqual(metadata.get("file_access_scope"), "workspace")
        self.assertEqual(metadata.get("operator_safety_profile"), "default")
        self.assertEqual(metadata.get("operator_approval_levels"), [])
        assert_snapshot(ui.render(), "flow_preflight_minimal")

    def test_configure_skip_snapshot(self) -> None:
        ui = FakeUI(choose_keys=["__continue"])
        with patch("scripts.orion_terminal.flows_configure.persist_wizard_session", return_value=None):
            rc = flows_configure.configure_flow(
                ui=ui,
                api_url="http://runtime",
                api_key="key",
                workspace_id="default",
                engine="orion",
            )
        self.assertEqual(rc, 0)
        assert_snapshot(ui.render(), "flow_configure_skip")

    def test_configure_profiles_skips_when_provider_not_verified(self) -> None:
        ui = FakeUI(choose_keys=["provider", "profiles", "__continue"])
        with (
            patch("scripts.orion_terminal.flows_configure.persist_wizard_session", return_value=None),
            patch("scripts.orion_terminal.flows_configure._setup_create_session", return_value={"id": "sess-1"}),
            patch(
                "scripts.orion_terminal.flows_configure.provider_and_credential_section",
                return_value=("openai", None, False),
            ),
            patch("scripts.orion_terminal.flows_configure._create_or_update_profile") as create_profile_mock,
            patch("scripts.orion_terminal.flows_configure._runtime_client") as runtime_client_mock,
        ):
            rc = flows_configure.configure_flow(
                ui=ui,
                api_url="http://runtime",
                api_key="key",
                workspace_id="default",
                engine="orion",
            )
        self.assertEqual(rc, 0)
        create_profile_mock.assert_not_called()
        runtime_client_mock.assert_not_called()
        rendered = ui.render()
        self.assertIn("Profile creation skipped: Provider/Auth is not fully verified yet.", rendered)

    def test_live_tui_exit_snapshot(self) -> None:
        ui = FakeUI(inputs=["/exit"])
        rc = flows_run.run_tui_shell(
            ui=ui,
            api_url="http://runtime",
            api_key="key",
            workspace_id="default",
            engine="orion",
        )
        self.assertEqual(rc, 0)
        assert_snapshot(ui.render(), "flow_live_tui_exit")

    def test_live_tui_chat_require_run_prefix_toggle(self) -> None:
        ui = FakeUI(inputs=["hello", "/run ship this", "/exit"])
        calls: list[str] = []

        def _fake_run_chat_goal(_ctx, goal: str):
            calls.append(str(goal or ""))
            return True, "abc12345", "ok"

        with (
            patch.dict(os.environ, {"ORION_CLI_CHAT_REQUIRE_RUN_PREFIX": "1"}),
            patch("scripts.orion_terminal.flows_run._inbox_events", return_value=[]),
            patch("scripts.orion_terminal.flows_run._run_chat_goal", side_effect=_fake_run_chat_goal),
        ):
            rc = flows_run.run_tui_shell(
                ui=ui,
                api_url="http://runtime",
                api_key="key",
                workspace_id="default",
                engine="orion",
            )
        self.assertEqual(rc, 0)
        self.assertEqual(calls, ["ship this"])
        rendered = ui.render()
        self.assertIn("Run prefix mode is enabled. Use /run <goal>.", rendered)
        self.assertNotIn("System [terminal]\n  Run prefix required in this mode.", rendered)

    def test_live_tui_strict_mode_requires_run_prefix(self) -> None:
        ui = FakeUI(inputs=["/trust", "hello", "/run ship this", "/exit"], choose_keys=["strict"])
        calls: list[str] = []

        def _fake_run_chat_goal(_ctx, goal: str):
            calls.append(str(goal or ""))
            return True, "abc12345", "ok"

        with (
            patch.dict(os.environ, {"ORION_CLI_CHAT_REQUIRE_RUN_PREFIX": "auto"}),
            patch("scripts.orion_terminal.flows_run._inbox_events", return_value=[]),
            patch("scripts.orion_terminal.flows_run._run_chat_goal", side_effect=_fake_run_chat_goal),
        ):
            rc = flows_run.run_tui_shell(
                ui=ui,
                api_url="http://runtime",
                api_key="key",
                workspace_id="default",
                engine="orion",
            )
        self.assertEqual(rc, 0)
        self.assertEqual(calls, ["ship this"])
        rendered = ui.render()
        self.assertIn("Run prefix mode is enabled. Use /run <goal>.", rendered)

    def test_live_tui_chat_does_not_rerender_identical_frame(self) -> None:
        ui = FakeUI(inputs=["", "", "/exit"])
        with patch("scripts.orion_terminal.flows_run._inbox_events", return_value=[]):
            rc = flows_run.run_tui_shell(
                ui=ui,
                api_url="http://runtime",
                api_key="key",
                workspace_id="default",
                engine="orion",
            )
        self.assertEqual(rc, 0)
        rendered = ui.render()
        self.assertEqual(rendered.count("orion tui - http://runtime"), 1)

    def test_live_tui_prefix_notice_only_once_per_scope(self) -> None:
        ui = FakeUI(inputs=["hello", "again", "/exit"])

        with (
            patch.dict(os.environ, {"ORION_CLI_CHAT_REQUIRE_RUN_PREFIX": "1"}),
            patch("scripts.orion_terminal.flows_run._inbox_events", return_value=[]),
            patch("scripts.orion_terminal.flows_run._run_chat_goal", return_value=(True, "abc12345", "ok")),
        ):
            rc = flows_run.run_tui_shell(
                ui=ui,
                api_url="http://runtime",
                api_key="key",
                workspace_id="default",
                engine="orion",
            )
        self.assertEqual(rc, 0)
        rendered = ui.render()
        self.assertEqual(rendered.count("Run prefix mode is enabled. Use /run <goal>."), 1)

    def test_live_tui_session_use_all_alias(self) -> None:
        ui = FakeUI(inputs=["/session use telegram", "/session use all", "/exit"])
        with patch("scripts.orion_terminal.flows_run._inbox_events", return_value=[]):
            rc = flows_run.run_tui_shell(
                ui=ui,
                api_url="http://runtime",
                api_key="key",
                workspace_id="default",
                engine="orion",
            )
        self.assertEqual(rc, 0)
        rendered = ui.render()
        self.assertIn("Session set to telegram channel.", rendered)
        self.assertIn("Session set to all channels.", rendered)

    def test_live_tui_session_overlay_picker(self) -> None:
        ui = FakeUI(inputs=["/session", "/exit"], choose_keys=["__telegram__"])
        with patch("scripts.orion_terminal.flows_run._inbox_events", return_value=[]):
            rc = flows_run.run_tui_shell(
                ui=ui,
                api_url="http://runtime",
                api_key="key",
                workspace_id="default",
                engine="orion",
            )
        self.assertEqual(rc, 0)
        rendered = ui.render()
        self.assertIn("Session set to telegram channel.", rendered)

    def test_live_tui_session_shorthand_alias(self) -> None:
        ui = FakeUI(inputs=["/session telegram", "/session all", "/exit"])
        with patch("scripts.orion_terminal.flows_run._inbox_events", return_value=[]):
            rc = flows_run.run_tui_shell(
                ui=ui,
                api_url="http://runtime",
                api_key="key",
                workspace_id="default",
                engine="orion",
            )
        self.assertEqual(rc, 0)
        rendered = ui.render()
        self.assertIn("Session set to telegram channel.", rendered)
        self.assertIn("Session set to all channels.", rendered)

    def test_live_tui_restores_persisted_session_and_engine(self) -> None:
        state = load_workspace_state("default")
        state.live_tui.session_filter = "telegram:restore-1"
        state.live_tui.channel_filter = ""
        state.live_tui.input_route = "local_run"
        state.live_tui.engine = "codex"
        save_workspace_state("default", state)

        ui = FakeUI(inputs=["/exit"])
        with patch("scripts.orion_terminal.flows_run._inbox_events", return_value=[]):
            rc = flows_run.run_tui_shell(
                ui=ui,
                api_url="http://runtime",
                api_key="key",
                workspace_id="default",
                engine="orion",
            )
        self.assertEqual(rc, 0)
        rendered = ui.render()
        self.assertIn("session telegram:restore-1", rendered)
        self.assertIn("engine codex", rendered)

    def test_live_tui_persists_session_and_engine_on_exit(self) -> None:
        ui = FakeUI(inputs=["/session telegram", "/engine codex", "/exit"])
        with patch("scripts.orion_terminal.flows_run._inbox_events", return_value=[]):
            rc = flows_run.run_tui_shell(
                ui=ui,
                api_url="http://runtime",
                api_key="key",
                workspace_id="default",
                engine="orion",
            )
        self.assertEqual(rc, 0)
        saved = load_workspace_state("default")
        self.assertEqual(saved.live_tui.channel_filter, "telegram")
        self.assertEqual(saved.live_tui.session_filter, "")
        self.assertEqual(saved.live_tui.engine, "codex")

    def test_live_tui_sessions_plural_alias(self) -> None:
        ui = FakeUI(inputs=["/session telegram", "/sessions all", "/exit"])
        with patch("scripts.orion_terminal.flows_run._inbox_events", return_value=[]):
            rc = flows_run.run_tui_shell(
                ui=ui,
                api_url="http://runtime",
                api_key="key",
                workspace_id="default",
                engine="orion",
            )
        self.assertEqual(rc, 0)
        rendered = ui.render()
        self.assertIn("Session set to telegram channel.", rendered)
        self.assertIn("Session set to all channels.", rendered)

    def test_live_tui_unknown_slash_command_does_not_run_goal(self) -> None:
        ui = FakeUI(inputs=["/foobar", "/exit"])
        calls: list[str] = []

        def _fake_run_chat_goal(_ctx, goal: str):
            calls.append(str(goal or ""))
            return True, "abc12345", "ok"

        with (
            patch("scripts.orion_terminal.flows_run._inbox_events", return_value=[]),
            patch("scripts.orion_terminal.flows_run._run_chat_goal", side_effect=_fake_run_chat_goal),
        ):
            rc = flows_run.run_tui_shell(
                ui=ui,
                api_url="http://runtime",
                api_key="key",
                workspace_id="default",
                engine="orion",
            )
        self.assertEqual(rc, 0)
        self.assertEqual(calls, [])
        rendered = ui.render()
        self.assertIn("Unknown command. Use /help.", rendered)

    def test_live_tui_route_telegram_sends_message_without_local_run(self) -> None:
        ui = FakeUI(inputs=["/route telegram_send", "hello", "/exit"])
        run_calls: list[str] = []
        send_calls: list[str] = []

        def _fake_run_chat_goal(_ctx, goal: str):
            run_calls.append(str(goal or ""))
            return True, "abc12345", "ok"

        def _fake_send_chat_telegram(_ctx, text: str, **kwargs):
            send_calls.append(str(text or ""))
            return True, "Sent."

        with (
            patch.dict(os.environ, {"ORION_CLI_ONE_APP_MODE": "0"}),
            patch("scripts.orion_terminal.flows_run._inbox_events", return_value=[]),
            patch("scripts.orion_terminal.flows_run._run_chat_goal", side_effect=_fake_run_chat_goal),
            patch("scripts.orion_terminal.flows_run._send_chat_telegram_message", side_effect=_fake_send_chat_telegram),
        ):
            rc = flows_run.run_tui_shell(
                ui=ui,
                api_url="http://runtime",
                api_key="key",
                workspace_id="default",
                engine="orion",
            )
        self.assertEqual(rc, 0)
        self.assertEqual(run_calls, [])
        self.assertEqual(send_calls, ["hello"])
        rendered = ui.render()
        self.assertIn("Input route set to telegram_send", rendered)

    def test_live_tui_route_telegram_allows_slash_message_forwarding(self) -> None:
        ui = FakeUI(inputs=["/route telegram_send", "/orion help", "/exit"])
        run_calls: list[str] = []
        send_calls: list[str] = []

        def _fake_run_chat_goal(_ctx, goal: str):
            run_calls.append(str(goal or ""))
            return True, "abc12345", "ok"

        def _fake_send_chat_telegram(_ctx, text: str, **kwargs):
            send_calls.append(str(text or ""))
            return True, "Sent."

        with (
            patch.dict(os.environ, {"ORION_CLI_ONE_APP_MODE": "0"}),
            patch("scripts.orion_terminal.flows_run._inbox_events", return_value=[]),
            patch("scripts.orion_terminal.flows_run._run_chat_goal", side_effect=_fake_run_chat_goal),
            patch("scripts.orion_terminal.flows_run._send_chat_telegram_message", side_effect=_fake_send_chat_telegram),
        ):
            rc = flows_run.run_tui_shell(
                ui=ui,
                api_url="http://runtime",
                api_key="key",
                workspace_id="default",
                engine="orion",
            )
        self.assertEqual(rc, 0)
        self.assertEqual(run_calls, [])
        self.assertEqual(send_calls, ["/orion help"])

    def test_live_tui_one_app_mode_pins_local_route(self) -> None:
        ui = FakeUI(inputs=["/route telegram_send", "hello", "/exit"])
        run_calls: list[str] = []
        send_calls: list[str] = []

        def _fake_run_chat_goal(_ctx, goal: str):
            run_calls.append(str(goal or ""))
            return True, "abc12345", "ok"

        def _fake_send_chat_telegram(_ctx, text: str, **kwargs):
            send_calls.append(str(text or ""))
            return True, "Sent."

        with (
            patch.dict(os.environ, {"ORION_CLI_ONE_APP_MODE": "1"}),
            patch("scripts.orion_terminal.flows_run._inbox_events", return_value=[]),
            patch("scripts.orion_terminal.flows_run._run_chat_goal", side_effect=_fake_run_chat_goal),
            patch("scripts.orion_terminal.flows_run._send_chat_telegram_message", side_effect=_fake_send_chat_telegram),
        ):
            rc = flows_run.run_tui_shell(
                ui=ui,
                api_url="http://runtime",
                api_key="key",
                workspace_id="default",
                engine="orion",
            )
        self.assertEqual(rc, 0)
        self.assertEqual(run_calls, ["hello"])
        self.assertEqual(send_calls, [])
        rendered = ui.render()
        self.assertIn("One-App Mode route: local_run.", rendered)

    def test_live_tui_one_app_mode_session_telegram_auto_routes_to_send(self) -> None:
        ui = FakeUI(inputs=["/session telegram", "hello", "/exit"])
        run_calls: list[str] = []
        send_calls: list[str] = []

        def _fake_run_chat_goal(_ctx, goal: str):
            run_calls.append(str(goal or ""))
            return True, "abc12345", "ok"

        def _fake_send_chat_telegram(_ctx, text: str, **kwargs):
            send_calls.append(str(text or ""))
            return True, "Sent."

        with (
            patch.dict(os.environ, {"ORION_CLI_ONE_APP_MODE": "1"}),
            patch("scripts.orion_terminal.flows_run._inbox_events", return_value=[]),
            patch("scripts.orion_terminal.flows_run._run_chat_goal", side_effect=_fake_run_chat_goal),
            patch("scripts.orion_terminal.flows_run._send_chat_telegram_message", side_effect=_fake_send_chat_telegram),
        ):
            rc = flows_run.run_tui_shell(
                ui=ui,
                api_url="http://runtime",
                api_key="key",
                workspace_id="default",
                engine="orion",
            )
        self.assertEqual(rc, 0)
        self.assertEqual(run_calls, ["hello"])
        self.assertEqual(send_calls, [])

    def test_live_tui_one_app_mode_session_telegram_send_when_enabled(self) -> None:
        ui = FakeUI(inputs=["/session telegram", "hello", "/exit"])
        run_calls: list[str] = []
        send_calls: list[str] = []

        def _fake_run_chat_goal(_ctx, goal: str):
            run_calls.append(str(goal or ""))
            return True, "abc12345", "ok"

        def _fake_send_chat_telegram(_ctx, text: str, **kwargs):
            send_calls.append(str(text or ""))
            return True, "Sent."

        with (
            patch.dict(os.environ, {"ORION_CLI_ONE_APP_MODE": "1", "ORION_CLI_ONE_APP_SESSION_SEND": "1"}),
            patch("scripts.orion_terminal.flows_run._inbox_events", return_value=[]),
            patch("scripts.orion_terminal.flows_run._run_chat_goal", side_effect=_fake_run_chat_goal),
            patch("scripts.orion_terminal.flows_run._send_chat_telegram_message", side_effect=_fake_send_chat_telegram),
        ):
            rc = flows_run.run_tui_shell(
                ui=ui,
                api_url="http://runtime",
                api_key="key",
                workspace_id="default",
                engine="orion",
            )
        self.assertEqual(rc, 0)
        self.assertEqual(run_calls, [])
        self.assertEqual(send_calls, ["hello"])

    def test_live_tui_model_command_sets_provider_and_model_override(self) -> None:
        ui = FakeUI(inputs=["/model openai/gpt-4.1", "/run hello", "/exit"])
        model_seen: list[tuple[str, str]] = []

        def _fake_run_chat_goal(ctx, goal: str):
            _ = goal
            model_seen.append((str(getattr(ctx, "provider_override", "")), str(getattr(ctx, "model_override", ""))))
            return True, "abc12345", "ok"

        with (
            patch("scripts.orion_terminal.flows_run._inbox_events", return_value=[]),
            patch("scripts.orion_terminal.flows_run._run_chat_goal", side_effect=_fake_run_chat_goal),
        ):
            rc = flows_run.run_tui_shell(
                ui=ui,
                api_url="http://runtime",
                api_key="key",
                workspace_id="default",
                engine="orion",
            )
        self.assertEqual(rc, 0)
        self.assertEqual(model_seen, [("openai", "gpt-4.1")])

    def test_live_tui_think_command_sets_thinking_level(self) -> None:
        ui = FakeUI(inputs=["/think low", "/run hello", "/exit"])
        levels_seen: list[str] = []

        def _fake_run_chat_goal(ctx, goal: str):
            _ = goal
            levels_seen.append(str(getattr(ctx, "thinking_level", "")))
            return True, "abc12345", "ok"

        with (
            patch("scripts.orion_terminal.flows_run._inbox_events", return_value=[]),
            patch("scripts.orion_terminal.flows_run._run_chat_goal", side_effect=_fake_run_chat_goal),
        ):
            rc = flows_run.run_tui_shell(
                ui=ui,
                api_url="http://runtime",
                api_key="key",
                workspace_id="default",
                engine="orion",
            )
        self.assertEqual(rc, 0)
        self.assertEqual(levels_seen, ["low"])

    def test_live_tui_settings_parity_commands(self) -> None:
        ui = FakeUI(
            inputs=[
                "/verbose on",
                "/reasoning on",
                "/usage tokens",
                "/elevated ask",
                "/activation always",
                "/settings",
                "/exit",
            ]
        )
        with patch("scripts.orion_terminal.flows_run._inbox_events", return_value=[]):
            rc = flows_run.run_tui_shell(
                ui=ui,
                api_url="http://runtime",
                api_key="key",
                workspace_id="default",
                engine="orion",
            )
        self.assertEqual(rc, 0)
        rendered = ui.render()
        self.assertIn("Verbose set to on.", rendered)
        self.assertIn("Reasoning set to on.", rendered)
        self.assertIn("Usage display set to tokens.", rendered)
        self.assertIn("Elevated execution set to ask.", rendered)
        self.assertIn("Activation mode set to always.", rendered)
        self.assertIn("verbose: on", rendered)
        self.assertIn("reasoning: on", rendered)
        self.assertIn("usage: tokens", rendered)
        self.assertIn("elevated: ask", rendered)
        self.assertIn("activation: always", rendered)

    def test_live_tui_agent_command_switches_mode(self) -> None:
        ui = FakeUI(inputs=["/agent weekly-content-studio", "/run hello", "/exit"])
        pack_seen: list[str] = []

        def _fake_run_chat_goal(ctx, goal: str):
            _ = goal
            pack_seen.append(str(getattr(ctx.pack, "key", "")))
            return True, "abc12345", "ok"

        with (
            patch("scripts.orion_terminal.flows_run._inbox_events", return_value=[]),
            patch("scripts.orion_terminal.flows_run._run_chat_goal", side_effect=_fake_run_chat_goal),
        ):
            rc = flows_run.run_tui_shell(
                ui=ui,
                api_url="http://runtime",
                api_key="key",
                workspace_id="default",
                engine="orion",
            )
        self.assertEqual(rc, 0)
        self.assertEqual(pack_seen, ["weekly-content-studio"])

    def test_live_tui_engine_command_switches_runtime_engine(self) -> None:
        ui = FakeUI(inputs=["/engine codex", "/run hello", "/exit"])
        engines_seen: list[str] = []

        def _fake_run_chat_goal(ctx, goal: str):
            _ = goal
            engines_seen.append(str(getattr(ctx, "engine", "")))
            return True, "abc12345", "ok"

        with (
            patch("scripts.orion_terminal.flows_run._inbox_events", return_value=[]),
            patch("scripts.orion_terminal.flows_run._run_chat_goal", side_effect=_fake_run_chat_goal),
        ):
            rc = flows_run.run_tui_shell(
                ui=ui,
                api_url="http://runtime",
                api_key="key",
                workspace_id="default",
                engine="orion",
            )
        self.assertEqual(rc, 0)
        self.assertEqual(engines_seen, ["codex"])

    def test_live_tui_approvals_resolve_uses_interactive_objective_picker(self) -> None:
        ui = FakeUI(inputs=["/approvals resolve", "", "/exit"])
        pending_events = [
            {
                "event_id": "evt-12345678",
                "risk_level": "medium",
                "objective_id": "obj-1",
                "objective_title": "SLA run",
                "summary": "Approval required for objective run.",
                "event_text": "/orion run status",
            }
        ]
        with (
            patch("scripts.orion_terminal.flows_run._inbox_events", return_value=[]),
            patch(
                "scripts.orion_terminal.tui.handlers.chat_actions._pending_cognitive_approvals_for_chat",
                return_value=pending_events,
            ),
            patch(
                "scripts.orion_terminal.tui.handlers.chat_actions._pending_approvals_for_chat",
                return_value=[],
            ),
            patch(
                "scripts.orion_terminal.clients.runtime.RuntimeClient.resolve_cognitive_approval",
                return_value={"status": "pending"},
            ) as resolve_mock,
        ):
            rc = flows_run.run_tui_shell(
                ui=ui,
                api_url="http://runtime",
                api_key="key",
                workspace_id="default",
                engine="orion",
            )
        self.assertEqual(rc, 0)
        resolve_mock.assert_called_once_with(
            event_id="evt-12345678",
            decision="approve",
            note=None,
        )
        rendered = ui.render()
        self.assertIn("Approved event evt-1234 (pending).", rendered)

    def test_live_tui_approval_menu_command_handles_no_pending(self) -> None:
        ui = FakeUI(inputs=["/approval-menu", "/exit"])
        with (
            patch("scripts.orion_terminal.flows_run._inbox_events", return_value=[]),
            patch(
                "scripts.orion_terminal.tui.handlers.chat_actions._pending_cognitive_approvals_for_chat",
                return_value=[],
            ),
            patch(
                "scripts.orion_terminal.tui.handlers.chat_actions._pending_approvals_for_chat",
                return_value=[],
            ),
        ):
            rc = flows_run.run_tui_shell(
                ui=ui,
                api_url="http://runtime",
                api_key="key",
                workspace_id="default",
                engine="orion",
            )
        self.assertEqual(rc, 0)
        rendered = ui.render()
        self.assertIn("No pending approvals.", rendered)

    def test_live_tui_inline_approval_rows_render_shortcuts(self) -> None:
        ui = FakeUI(inputs=["/exit"])
        pending_events = [
            {
                "event_id": "evt-12345678",
                "objective_id": "obj-1",
                "objective_title": "SLA run",
                "summary": "Approval required for objective run.",
                "event_text": "/orion run status",
                "updated_at": "2026-02-28T00:00:00Z",
            }
        ]
        with (
            patch("scripts.orion_terminal.flows_run._inbox_events", return_value=[]),
            patch(
                "scripts.orion_terminal.tui.handlers.chat_actions._pending_cognitive_approvals_for_chat",
                return_value=pending_events,
            ),
            patch(
                "scripts.orion_terminal.tui.handlers.chat_actions._pending_approvals_for_chat",
                return_value=[],
            ),
        ):
            rc = flows_run.run_tui_shell(
                ui=ui,
                api_url="http://runtime",
                api_key="key",
                workspace_id="default",
                engine="orion",
            )
        self.assertEqual(rc, 0)
        rendered = ui.render()
        self.assertIn("Use /ok evt-1234 or /hold evt-1234 <reason>.", rendered)

    def test_live_tui_ok_command_approves_objective_event(self) -> None:
        ui = FakeUI(inputs=["/ok evt-1234", "/exit"])
        pending_events = [
            {
                "event_id": "evt-12345678",
                "objective_id": "obj-1",
                "objective_title": "SLA run",
                "summary": "Approval required for objective run.",
                "event_text": "/orion run status",
            }
        ]
        with (
            patch("scripts.orion_terminal.flows_run._inbox_events", return_value=[]),
            patch(
                "scripts.orion_terminal.tui.handlers.chat_actions._pending_cognitive_approvals_for_chat",
                return_value=pending_events,
            ),
            patch(
                "scripts.orion_terminal.tui.handlers.chat_actions._pending_approvals_for_chat",
                return_value=[],
            ),
            patch(
                "scripts.orion_terminal.clients.runtime.RuntimeClient.resolve_cognitive_approval",
                return_value={"status": "pending"},
            ) as resolve_mock,
        ):
            rc = flows_run.run_tui_shell(
                ui=ui,
                api_url="http://runtime",
                api_key="key",
                workspace_id="default",
                engine="orion",
            )
        self.assertEqual(rc, 0)
        resolve_mock.assert_called_once_with(
            event_id="evt-12345678",
            decision="approve",
            note=None,
        )
        self.assertIn("Approved event evt-1234 (pending).", ui.render())

    def test_live_tui_hold_command_rejects_runtime_approval(self) -> None:
        ui = FakeUI(inputs=["/hold run-abc1 reason here", "/exit"])
        pending_runs = [
            {
                "run_id": "run-abc12345",
                "approval_id": "appr-12345678",
                "reason": "Approval required before action.",
            }
        ]
        with (
            patch("scripts.orion_terminal.flows_run._inbox_events", return_value=[]),
            patch(
                "scripts.orion_terminal.tui.handlers.chat_actions._pending_cognitive_approvals_for_chat",
                return_value=[],
            ),
            patch(
                "scripts.orion_terminal.tui.handlers.chat_actions._pending_approvals_for_chat",
                return_value=pending_runs,
            ),
            patch(
                "scripts.orion_terminal.clients.runtime.RuntimeClient.resolve_approval",
                return_value={"status": "ok"},
            ) as resolve_mock,
        ):
            rc = flows_run.run_tui_shell(
                ui=ui,
                api_url="http://runtime",
                api_key="key",
                workspace_id="default",
                engine="orion",
            )
        self.assertEqual(rc, 0)
        resolve_mock.assert_called_once_with(
            run_id="run-abc12345",
            approval_id="appr-12345678",
            decision="reject",
            note="reason here",
        )
        self.assertIn("Rejected run run-abc1 (ok).", ui.render())

    def test_live_tui_objective_list_command_renders_objectives(self) -> None:
        ui = FakeUI(inputs=["/objective list", "/exit"])
        with (
            patch("scripts.orion_terminal.flows_run._inbox_events", return_value=[]),
            patch(
                "scripts.orion_terminal.tui.handlers.chat_actions._run_cognitive_objective_command",
                return_value={
                    "ok": True,
                    "items": [
                        {
                            "id": "obj-12345678",
                            "title": "SLA run",
                            "status": "active",
                            "priority": 70,
                        }
                    ],
                },
            ),
        ):
            rc = flows_run.run_tui_shell(
                ui=ui,
                api_url="http://runtime",
                api_key="key",
                workspace_id="default",
                engine="orion",
            )
        self.assertEqual(rc, 0)
        rendered = ui.render()
        self.assertIn("Objectives:", rendered)
        self.assertIn("obj-1234", rendered)
        self.assertIn("SLA run", rendered)

    def test_live_tui_objective_add_command_creates_objective(self) -> None:
        ui = FakeUI(
            inputs=[
                '/objective add "SLA run v3" "status" --cadence 300 --priority 65 --sla 1200 --target local_companion --trust guarded --pause-on-escalation',
                "/exit",
            ]
        )
        with (
            patch("scripts.orion_terminal.flows_run._inbox_events", return_value=[]),
            patch(
                "scripts.orion_terminal.tui.handlers.chat_actions._run_cognitive_objective_command",
                return_value={
                    "ok": True,
                    "objective": {
                        "id": "obj-abcdef12",
                        "title": "SLA run v3",
                        "status": "active",
                    },
                },
            ) as cmd_mock,
        ):
            rc = flows_run.run_tui_shell(
                ui=ui,
                api_url="http://runtime",
                api_key="key",
                workspace_id="default",
                engine="orion",
            )
        self.assertEqual(rc, 0)
        cmd_mock.assert_called_once_with(
            [
                "objective-add",
                "--title",
                "SLA run v3",
                "--goal",
                "status",
                "--cadence-seconds",
                "300",
                "--priority",
                "65",
                "--sla-seconds",
                "1200",
                "--execution-target",
                "local_companion",
                "--trust-mode",
                "guarded",
                "--pause-on-escalation",
            ]
        )
        rendered = ui.render()
        self.assertIn("Objective created: SLA run v3", rendered)

    def test_live_tui_objective_doctor_renders_summary(self) -> None:
        ui = FakeUI(inputs=["/objective doctor obj-1 --limit 3", "/exit"])

        def _objective_side_effect(args):
            if args[:1] == ["objective-list"]:
                return {
                    "ok": True,
                    "items": [
                        {"id": "obj-12345678", "title": "SLA run", "status": "active", "priority": 70},
                    ],
                }
            if args[:1] == ["objective-get"]:
                return {
                    "ok": True,
                    "objective": {
                        "id": "obj-12345678",
                        "title": "SLA run",
                        "status": "active",
                        "priority": 70,
                        "next_run_at": 1000,
                        "last_run_at": 900,
                        "metadata": {
                            "policy": {
                                "verification_fail_streak": 1,
                                "escalation_state": "resolved",
                                "escalation_resolution": "auto_approved_low_risk",
                                "auto_approval_count": 2,
                            }
                        },
                    },
                }
            if args[:1] == ["objective-tail"]:
                return {
                    "ok": True,
                    "objective": {"id": "obj-12345678", "title": "SLA run", "status": "active", "priority": 70},
                    "events": [
                        {
                            "id": "evt-aaaabbbb",
                            "status": "done",
                            "digest": {
                                "one_line_summary": "Run complete.",
                                "risk_flags": ["auto_approved"],
                            },
                        }
                    ],
                    "reflections": [{"passed": True, "confidence": 0.9, "lesson": "Good execution."}],
                }
            if args[:1] == ["approvals"]:
                return {"ok": True, "items": []}
            return {"ok": False, "error": "unexpected_call"}

        with (
            patch("scripts.orion_terminal.flows_run._inbox_events", return_value=[]),
            patch(
                "scripts.orion_terminal.tui.handlers.chat_actions._run_cognitive_objective_command",
                side_effect=_objective_side_effect,
            ),
        ):
            rc = flows_run.run_tui_shell(
                ui=ui,
                api_url="http://runtime",
                api_key="key",
                workspace_id="default",
                engine="orion",
            )
        self.assertEqual(rc, 0)
        rendered = ui.render()
        self.assertIn("Objective doctor: SLA run", rendered)
        self.assertIn("latest_event: evt-aaaa", rendered)
        self.assertIn(
            "policy: fail_streak=1 escalation=resolved/auto_approved_low_risk remediation=-/- auto_approvals=2",
            rendered,
        )
        self.assertIn("latest_event_flags: auto_approved", rendered)
        self.assertIn("latest_reflection: passed=True", rendered)

    def test_live_tui_objective_tail_shows_skill_replay_signal(self) -> None:
        ui = FakeUI(inputs=["/objective tail obj-1 3", "/exit"])

        def _objective_side_effect(args):
            if args[:1] == ["objective-tail"]:
                return {
                    "ok": True,
                    "objective": {"id": "obj-12345678", "title": "SLA run", "status": "active", "priority": 70},
                    "events": [
                        {
                            "id": "evt-aaaabbbb",
                            "status": "done",
                            "result": {
                                "tick": {
                                    "observation": {
                                        "metadata": {
                                            "skill_replay": {
                                                "applied": False,
                                                "confidence": 0.44,
                                                "signature": "status.v1",
                                                "reason": "below_threshold",
                                            }
                                        }
                                    }
                                }
                            },
                        }
                    ],
                    "reflections": [],
                }
            return {"ok": False, "error": "unexpected_call"}

        with (
            patch("scripts.orion_terminal.flows_run._inbox_events", return_value=[]),
            patch(
                "scripts.orion_terminal.tui.handlers.chat_actions._run_cognitive_objective_command",
                side_effect=_objective_side_effect,
            ),
        ):
            rc = flows_run.run_tui_shell(
                ui=ui,
                api_url="http://runtime",
                api_key="key",
                workspace_id="default",
                engine="orion",
            )
        self.assertEqual(rc, 0)
        rendered = ui.render()
        self.assertIn("Objective tail: SLA run", rendered)
        self.assertIn("skill_replay: fallback conf=0.44 sig=status.v1 reason=below_threshold", rendered)

    def test_live_tui_objective_board_renders_compact_health_view(self) -> None:
        ui = FakeUI(inputs=["/objective board active 2", "/exit"])

        def _objective_side_effect(args):
            if args[:1] == ["objective-list"]:
                return {
                    "ok": True,
                    "items": [
                        {
                            "id": "obj-11111111",
                            "title": "SLA run A",
                            "status": "active",
                            "priority": 70,
                            "metadata": {
                                "policy": {
                                    "verification_fail_streak": 1,
                                    "escalation_state": "resolved",
                                    "escalation_resolution": "auto_approved_low_risk",
                                    "remediation_state": "active",
                                    "auto_approval_count": 2,
                                }
                            },
                        },
                        {
                            "id": "obj-22222222",
                            "title": "SLA run B",
                            "status": "active",
                            "priority": 60,
                            "metadata": {
                                "policy": {
                                    "verification_fail_streak": 0,
                                    "escalation_state": "-",
                                    "escalation_resolution": "-",
                                    "remediation_state": "-",
                                    "auto_approval_count": 0,
                                }
                            },
                        },
                    ],
                }
            if args[:1] == ["approvals"]:
                return {
                    "ok": True,
                    "items": [
                        {"event_id": "evt-1", "objective_id": "obj-11111111"},
                        {"event_id": "evt-2", "objective_id": "obj-11111111"},
                    ],
                }
            if args[:1] == ["objective-lessons"]:
                return {
                    "ok": True,
                    "items": [
                        {
                            "event_id": "evt-a1",
                            "objective_id": "obj-11111111",
                            "passed": True,
                            "confidence": 0.90,
                            "status": "ok",
                            "lesson": "Recent pass.",
                        },
                        {
                            "event_id": "evt-a2",
                            "objective_id": "obj-11111111",
                            "passed": False,
                            "confidence": 0.70,
                            "status": "failed",
                            "lesson": "Older fail.",
                        },
                    ],
                    "count": 2,
                }
            return {"ok": False, "error": "unexpected_call"}

        with (
            patch("scripts.orion_terminal.flows_run._inbox_events", return_value=[]),
            patch(
                "scripts.orion_terminal.tui.handlers.chat_actions._run_cognitive_objective_command",
                side_effect=_objective_side_effect,
            ),
        ):
            rc = flows_run.run_tui_shell(
                ui=ui,
                api_url="http://runtime",
                api_key="key",
                workspace_id="default",
                engine="orion",
            )
        self.assertEqual(rc, 0)
        rendered = ui.render()
        self.assertIn("Objective health board (active, limit=2)", rendered)
        self.assertIn("SLA run A", rendered)
        self.assertIn("esc=resolved/auto_approved_low_risk", rendered)
        self.assertIn("rem=active", rendered)
        self.assertIn("auto=2 approvals=2", rendered)
        self.assertIn("refl=2 pass=50.0% conf=0.80 trend=up", rendered)

    def test_live_tui_objective_lessons_renders_reflection_feed(self) -> None:
        ui = FakeUI(inputs=["/objective lessons obj-1 2", "/exit"])

        def _objective_side_effect(args):
            if args[:1] == ["objective-list"]:
                return {
                    "ok": True,
                    "items": [
                        {"id": "obj-11111111", "title": "SLA run A", "status": "active", "priority": 70},
                    ],
                }
            if args[:1] == ["objective-lessons"]:
                return {
                    "ok": True,
                    "items": [
                        {
                            "event_id": "evt-aaaa1111",
                            "objective_id": "obj-11111111",
                            "passed": True,
                            "status": "done",
                            "confidence": 0.92,
                            "lesson": "Use local_companion for status checks.",
                        },
                        {
                            "event_id": "evt-bbbb2222",
                            "objective_id": "obj-11111111",
                            "passed": False,
                            "status": "failed",
                            "confidence": 0.66,
                            "lesson": "Escalate after retry hold when approvals repeat.",
                        },
                    ],
                    "count": 2,
                }
            return {"ok": False, "error": "unexpected_call"}

        with (
            patch("scripts.orion_terminal.flows_run._inbox_events", return_value=[]),
            patch(
                "scripts.orion_terminal.tui.handlers.chat_actions._run_cognitive_objective_command",
                side_effect=_objective_side_effect,
            ),
        ):
            rc = flows_run.run_tui_shell(
                ui=ui,
                api_url="http://runtime",
                api_key="key",
                workspace_id="default",
                engine="orion",
            )
        self.assertEqual(rc, 0)
        rendered = ui.render()
        self.assertIn("Objective lessons (SLA run A, limit=2)", rendered)
        self.assertIn("evt-aaaa", rendered)
        self.assertIn("Use local_companion for status checks", rendered)

    def test_live_tui_objective_approve_uses_objective_filter(self) -> None:
        ui = FakeUI(inputs=["/objective approve obj-1", "/exit"])
        pending_events = [
            {
                "event_id": "evt-12345678",
                "risk_level": "medium",
                "objective_id": "obj-1",
                "objective_title": "SLA run",
                "summary": "Approval required.",
                "event_text": "/orion run status",
            }
        ]
        with (
            patch("scripts.orion_terminal.flows_run._inbox_events", return_value=[]),
            patch(
                "scripts.orion_terminal.tui.handlers.chat_actions._pending_cognitive_approvals_for_chat",
                return_value=pending_events,
            ),
            patch(
                "scripts.orion_terminal.clients.runtime.RuntimeClient.resolve_cognitive_approval",
                return_value={"status": "pending"},
            ) as resolve_mock,
        ):
            rc = flows_run.run_tui_shell(
                ui=ui,
                api_url="http://runtime",
                api_key="key",
                workspace_id="default",
                engine="orion",
            )
        self.assertEqual(rc, 0)
        resolve_mock.assert_called_once_with(
            event_id="evt-12345678",
            decision="approve",
            note=None,
        )
        rendered = ui.render()
        self.assertIn("Approved objective event evt-1234 (pending).", rendered)

    def test_live_tui_objective_approve_latest_picks_newest(self) -> None:
        ui = FakeUI(inputs=['/objective approve-latest obj-1 --note "ship it"', "/exit"])
        pending_events = [
            {
                "event_id": "evt-11111111",
                "objective_id": "obj-1",
                "objective_title": "SLA run",
                "updated_at": 100.0,
            },
            {
                "event_id": "evt-22222222",
                "objective_id": "obj-1",
                "objective_title": "SLA run",
                "updated_at": 200.0,
            },
        ]
        with (
            patch("scripts.orion_terminal.flows_run._inbox_events", return_value=[]),
            patch(
                "scripts.orion_terminal.tui.handlers.chat_actions._pending_cognitive_approvals_for_chat",
                return_value=pending_events,
            ),
            patch(
                "scripts.orion_terminal.clients.runtime.RuntimeClient.resolve_cognitive_approval",
                return_value={"status": "pending"},
            ) as resolve_mock,
        ):
            rc = flows_run.run_tui_shell(
                ui=ui,
                api_url="http://runtime",
                api_key="key",
                workspace_id="default",
                engine="orion",
            )
        self.assertEqual(rc, 0)
        resolve_mock.assert_called_once_with(
            event_id="evt-22222222",
            decision="approve",
            note="ship it",
        )
        rendered = ui.render()
        self.assertIn("Approved latest objective event evt-2222 (pending)", rendered)

    def test_live_tui_objective_run_now_dispatches_selected_objective(self) -> None:
        ui = FakeUI(inputs=["/objective run-now obj-1", "/exit"])

        def _objective_side_effect(args):
            if args[:1] == ["objective-list"]:
                return {
                    "ok": True,
                    "items": [
                        {"id": "obj-12345678", "title": "SLA run", "status": "active", "priority": 70},
                    ],
                }
            if args[:1] == ["objective-dispatch"]:
                return {
                    "ok": True,
                    "objective_id": "obj-12345678",
                    "event_id": "evt-11112222",
                    "event_text": "/orion status",
                }
            return {"ok": False, "error": "unexpected_call"}

        with (
            patch("scripts.orion_terminal.flows_run._inbox_events", return_value=[]),
            patch(
                "scripts.orion_terminal.tui.handlers.chat_actions._run_cognitive_objective_command",
                side_effect=_objective_side_effect,
            ) as cmd_mock,
        ):
            rc = flows_run.run_tui_shell(
                ui=ui,
                api_url="http://runtime",
                api_key="key",
                workspace_id="default",
                engine="orion",
            )
        self.assertEqual(rc, 0)
        rendered = ui.render()
        self.assertIn("Run-now dispatched SLA run", rendered)
        self.assertIn("event=evt-1111", rendered)
        dispatched_calls = [call.args[0] for call in cmd_mock.call_args_list if call.args]
        self.assertIn(["objective-dispatch", "--objective-id", "obj-12345678"], dispatched_calls)

    def test_live_tui_objective_resume_latest_resumes_paused_objective(self) -> None:
        ui = FakeUI(inputs=["/objective resume-latest", "/exit"])

        def _objective_side_effect(args):
            if args[:1] == ["objective-list"]:
                return {
                    "ok": True,
                    "items": [
                        {"id": "obj-a", "title": "Old paused", "status": "paused", "updated_at": 100.0},
                        {"id": "obj-b", "title": "Current paused", "status": "paused", "updated_at": 200.0},
                    ],
                }
            if args[:1] == ["objective-resume"]:
                return {"ok": True, "objective": {"id": "obj-b", "status": "active"}}
            return {"ok": False, "error": "unexpected_call"}

        with (
            patch("scripts.orion_terminal.flows_run._inbox_events", return_value=[]),
            patch(
                "scripts.orion_terminal.tui.handlers.chat_actions._run_cognitive_objective_command",
                side_effect=_objective_side_effect,
            ) as cmd_mock,
        ):
            rc = flows_run.run_tui_shell(
                ui=ui,
                api_url="http://runtime",
                api_key="key",
                workspace_id="default",
                engine="orion",
            )
        self.assertEqual(rc, 0)
        rendered = ui.render()
        self.assertIn("Resumed objective Current paused", rendered)
        resumed_calls = [call.args[0] for call in cmd_mock.call_args_list if call.args]
        self.assertIn(["objective-resume", "--objective-id", "obj-b"], resumed_calls)

    def test_live_tui_auto_focuses_telegram_once(self) -> None:
        ui = FakeUI(inputs=["/exit"])
        events = [
            {
                "ts": "2026-02-26T12:00:00Z",
                "direction": "inbound",
                "channel": "telegram",
                "text": "hello",
                "session_key": "telegram:1932934047",
            }
        ]
        with (
            patch.dict(os.environ, {"ORION_CLI_CHAT_AUTOFOCUS_TELEGRAM": "1"}),
            patch("scripts.orion_terminal.flows_run._inbox_events", return_value=events),
        ):
            rc = flows_run.run_tui_shell(
                ui=ui,
                api_url="http://runtime",
                api_key="key",
                workspace_id="default",
                engine="orion",
            )
        self.assertEqual(rc, 0)
        rendered = ui.render()
        self.assertIn("session telegram", rendered)

    def test_live_tui_auto_focuses_when_telegram_appears_later(self) -> None:
        ui = FakeUI(inputs=["", "/exit"])
        events_empty = []
        events_telegram = [
            {
                "ts": "2026-02-26T12:01:00Z",
                "direction": "inbound",
                "channel": "telegram",
                "text": "hello later",
                "session_key": "telegram:1932934047",
            }
        ]
        with (
            patch.dict(os.environ, {"ORION_CLI_CHAT_AUTOFOCUS_TELEGRAM": "1"}),
            patch("scripts.orion_terminal.flows_run._inbox_events", side_effect=[events_empty, events_telegram, events_telegram]),
        ):
            rc = flows_run.run_tui_shell(
                ui=ui,
                api_url="http://runtime",
                api_key="key",
                workspace_id="default",
                engine="orion",
            )
        self.assertEqual(rc, 0)
        rendered = ui.render()
        self.assertIn("session telegram", rendered)

    def test_live_tui_uses_terminal_label_for_local_chat(self) -> None:
        ui = FakeUI(inputs=["hello", "/exit"])
        events = [
            {
                "ts": "2026-02-26T12:00:00Z",
                "direction": "inbound",
                "channel": "telegram",
                "text": "from telegram",
                "session_key": "telegram:1932934047",
            }
        ]
        with (
            patch("scripts.orion_terminal.flows_run._inbox_events", return_value=events),
            patch("scripts.orion_terminal.flows_run._run_chat_goal", return_value=(True, "abc12345", "ok")),
        ):
            rc = flows_run.run_tui_shell(
                ui=ui,
                api_url="http://runtime",
                api_key="key",
                workspace_id="default",
                engine="orion",
            )
        self.assertEqual(rc, 0)
        rendered = ui.render()
        self.assertIn("hello", rendered)
        self.assertIn("ok", rendered)

    def test_live_tui_chat_accepts_stream_callback_updates(self) -> None:
        ui = FakeUI(inputs=["hello", "/exit"])

        def _fake_run_chat_goal(_ctx, goal: str, on_update=None):
            if on_update:
                on_update("abc12345", "Planning task from stream...", False)
                on_update("abc12345", "Final streamed answer.", True)
            return True, "abc12345", "Final streamed answer."

        with (
            patch("scripts.orion_terminal.flows_run._inbox_events", return_value=[]),
            patch("scripts.orion_terminal.flows_run._run_chat_goal", side_effect=_fake_run_chat_goal),
        ):
            rc = flows_run.run_tui_shell(
                ui=ui,
                api_url="http://runtime",
                api_key="key",
                workspace_id="default",
                engine="orion",
            )
        self.assertEqual(rc, 0)
        rendered = ui.render()
        self.assertIn("Final streamed answer.", rendered)

    def test_live_tui_hides_channel_control_commands_by_default(self) -> None:
        ui = FakeUI(inputs=["/exit"])
        events = [
            {
                "ts": "2026-02-26T12:00:00Z",
                "direction": "inbound",
                "channel": "telegram",
                "text": "/start",
                "session_key": "telegram:1932934047",
            },
            {
                "ts": "2026-02-26T12:00:01Z",
                "direction": "inbound",
                "channel": "telegram",
                "text": "Hello",
                "session_key": "telegram:1932934047",
            },
        ]
        with patch("scripts.orion_terminal.flows_run._inbox_events", return_value=events):
            rc = flows_run.run_tui_shell(
                ui=ui,
                api_url="http://runtime",
                api_key="key",
                workspace_id="default",
                engine="orion",
            )
        self.assertEqual(rc, 0)
        rendered = ui.render()
        self.assertIn("Hello", rendered)
        self.assertNotIn("/start", rendered)

    def test_live_tui_can_show_channel_control_commands_when_enabled(self) -> None:
        ui = FakeUI(inputs=["/exit"])
        events = [
            {
                "ts": "2026-02-26T12:00:00Z",
                "direction": "inbound",
                "channel": "telegram",
                "text": "/start",
                "session_key": "telegram:1932934047",
            },
        ]
        with (
            patch.dict(os.environ, {"ORION_CLI_CHAT_HIDE_CHANNEL_COMMANDS": "0"}),
            patch("scripts.orion_terminal.flows_run._inbox_events", return_value=events),
        ):
            rc = flows_run.run_tui_shell(
                ui=ui,
                api_url="http://runtime",
                api_key="key",
                workspace_id="default",
                engine="orion",
            )
        self.assertEqual(rc, 0)
        rendered = ui.render()
        self.assertIn("/start", rendered)


if __name__ == "__main__":
    unittest.main()
