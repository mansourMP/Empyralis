import unittest

from server_modules.connectors.autopilot_connector_shell_service import AutopilotConnectorShellService


class _FakeFacade:
    instances = []

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.init_calls = 0
        type(self).instances.append(self)

    def init_runtime(self):
        self.init_calls += 1

    def telegram_service_registry(self):
        return {"telegram": True}

    def whatsapp_service_registry(self):
        return {"whatsapp": True}

    def telegram_helper_registry(self):
        return {"helper": True}

    def support_service_registry(self):
        class _Support:
            def profile_service(self_inner):
                return {"profile": True}

            def runtime_status_service(self_inner):
                return {"status": True}

            def workflow_setup_service(self_inner):
                return {"workflow": True}

            def connector_context_service(self_inner):
                return {"context": True}

            def approval_service(self_inner):
                return {"approval": True}

            def common_support_service(self_inner):
                return {"common": True}

            def skill_service(self_inner):
                return {"skill": True}

            def channel_support_service(self_inner):
                return {"channel": True}

        return _Support()

    def runtime_service_registry(self):
        class _Runtime:
            def connector_support_service(self_inner):
                return {"connector": True}

            def transport_service(self_inner):
                return {"transport": True}

            def terminal_service(self_inner):
                return {"terminal": True}

            def run_entry_service(self_inner):
                return {"run_entry": True}

            def runtime_support_service(self_inner):
                return {"runtime_support": True}

            def menu_service(self_inner):
                return {"menu": True}

        return _Runtime()

    def shared_service_registry(self):
        return {"shared": True}

    def autopilot_status_service(self):
        return {"status": True}

    def autopilot_endpoint_service(self):
        return {"endpoint": True}

    def autopilot_event_service(self):
        return {"event": True}

    def event_bridge_service(self):
        return {"event_bridge": True}

    def terminal_bridge_service(self):
        return {"terminal_bridge": True}

    def state_bridge_service(self):
        return {"state_bridge": True}

    def compatibility_bridge_service(self):
        return {"compatibility_bridge": True}

    def webhook_bridge_service(self):
        return {"webhook_bridge": True}


class AutopilotConnectorShellServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        _FakeFacade.instances.clear()

    def test_shell_caches_facades_and_keeps_late_bound_global_getters(self) -> None:
        namespace = {
            "ORION_TELEGRAM_AUTOPILOT_PROFILE": "ops",
            "ORION_TELEGRAM_AUTOPILOT_ENABLED": True,
            "ORION_TELEGRAM_AUTOPILOT_WORKSPACE_ID": "default",
            "ORION_WHATSAPP_AUTOPILOT_PROFILE": "support",
            "ORION_WHATSAPP_AUTOPILOT_ENABLED": True,
            "ORION_WHATSAPP_AUTOPILOT_WORKSPACE_ID": "default",
            "ENGINE_REGISTRY": {"orion": {}},
            "Response": lambda **kwargs: kwargs,
            "query_active_installed_skills": lambda **kwargs: [],
            "list_vault_connectors": lambda workspace_id: [],
            "list_recent_connector_messages": lambda credentials, limit=0: [],
            "runs": {},
            "_append_channel_event": lambda **kwargs: kwargs,
            "_json_safe": lambda value: value,
            "ORION_LOCAL_LEASE_SECONDS": 30,
            "ORION_TELEGRAM_AUTOPILOT_STATE_FILE": "/tmp/tg-state.json",
            "ORION_WHATSAPP_AUTOPILOT_STATE_FILE": "/tmp/wa-state.json",
        }
        shell = AutopilotConnectorShellService(
            global_namespace=namespace,
            sync_server_globals=("TELEGRAM_AUTOPILOT_THREAD",),
            project_root="/tmp/project",
            default_chat_prefix="/empyralis",
            telegram_onboarding_enabled=True,
            telegram_space_status_enabled=True,
            telegram_media_max_items=4,
            telegram_max_updates=5,
            telegram_poll_seconds=2.0,
            telegram_guided_automation_setup_enabled=True,
            telegram_media_enabled=True,
            telegram_media_dir="/tmp/media",
            telegram_media_max_bytes=1024,
            telegram_media_include_in_goal=True,
            telegram_camera_setup_state_file="/tmp/camera.json",
            telegram_profile_state_file="/tmp/profiles.json",
            telegram_onboarding_state_file="/tmp/onboarding.json",
            quick_goal_templates={"project update": "summary"},
            menu_goal_templates={"project update": "summary"},
            workflow_api_url="http://workflow",
            runtime_url="http://runtime",
            web_url="http://web",
            installed_skills_enabled=True,
            error_category_hints=[("timeout", "timeout")],
            non_retryable_run_error_hints=["fatal"],
            dead_letter_lock=object(),
            dead_letter_file="/tmp/dead.json",
            dead_letter_limit=100,
            local_lease_seconds=30,
            telegram_show_buttons=True,
            telegram_profile_fields=["about"],
            run_history=[],
            run_history_lock=object(),
            runtime_metrics={"runs": 1},
            metrics_lock=object(),
            local_queue_lock=object(),
            local_pending_run_ids=[],
            local_claimed_runs=[],
            local_worker_registry={},
            server_getter=lambda: None,
            server_setter=lambda value: value,
            import_server=lambda: object(),
            read_json=lambda path, default: default,
            write_json=lambda path, payload: payload,
            utc_now_iso=lambda: "2026-04-05T00:00:00Z",
            normalize_workspace_id=lambda value: str(value or "default"),
            load_vault=lambda: {"vault": True},
            workspace_visible=lambda workspace_id, entry: True,
            get_updates_process_lock=lambda bot_token: bot_token,
            resolve_vault_credential=lambda credential_id, workspace_id: {"credential_id": credential_id, "workspace_id": workspace_id},
            http_json_request=lambda *args, **kwargs: {"args": args, "kwargs": kwargs},
            safe_path_token_impl=lambda value: f"safe:{value}",
            telegram_space_question_via_mcp=lambda **kwargs: kwargs,
            utc_now=lambda: "now",
            parse_utc_ts=lambda value: value,
            create_run=lambda **kwargs: kwargs,
            decide_execution_target=lambda metadata: {"selected": "cloud", "metadata": metadata},
            apply_execution_route_metadata=lambda metadata, route: {**metadata, "route": route},
            normalize_trust_mode=lambda value: str(value),
            normalize_execution_target=lambda value: str(value),
            bridge_facade_class=_FakeFacade,
            registry_facade_class=_FakeFacade,
            runtime_facade_class=_FakeFacade,
        )

        registry_first = shell.registry_facade_service()
        bridge_first = shell.bridge_facade_service()
        runtime_first = shell.runtime_facade_service()

        self.assertIs(registry_first, shell.registry_facade_service())
        self.assertIs(bridge_first, shell.bridge_facade_service())
        self.assertIs(runtime_first, shell.runtime_facade_service())
        self.assertEqual(len(_FakeFacade.instances), 3)
        self.assertEqual(registry_first.kwargs["telegram_default_profile_getter"](), "ops")
        self.assertEqual(bridge_first.kwargs["telegram_default_profile_getter"](), "ops")
        shell._init_runtime()
        self.assertEqual(runtime_first.init_calls, 1)

    def test_shell_defaults_telegram_workspace_to_default(self) -> None:
        shell = AutopilotConnectorShellService(
            global_namespace={},
            sync_server_globals=(),
            project_root="/tmp/project",
            default_chat_prefix="/empyralis",
            telegram_onboarding_enabled=True,
            telegram_space_status_enabled=True,
            telegram_media_max_items=4,
            telegram_max_updates=5,
            telegram_poll_seconds=2.0,
            telegram_guided_automation_setup_enabled=True,
            telegram_media_enabled=True,
            telegram_media_dir="/tmp/media",
            telegram_media_max_bytes=1024,
            telegram_media_include_in_goal=True,
            telegram_camera_setup_state_file="/tmp/camera.json",
            telegram_profile_state_file="/tmp/profiles.json",
            telegram_onboarding_state_file="/tmp/onboarding.json",
            quick_goal_templates={},
            menu_goal_templates={},
            workflow_api_url="http://workflow",
            runtime_url="http://runtime",
            web_url="http://web",
            installed_skills_enabled=True,
            error_category_hints=[],
            non_retryable_run_error_hints=[],
            dead_letter_lock=object(),
            dead_letter_file="/tmp/dead.json",
            dead_letter_limit=100,
            local_lease_seconds=30,
            telegram_show_buttons=True,
            telegram_profile_fields=[],
            run_history=[],
            run_history_lock=object(),
            runtime_metrics={},
            metrics_lock=object(),
            local_queue_lock=object(),
            local_pending_run_ids=[],
            local_claimed_runs=[],
            local_worker_registry={},
            server_getter=lambda: None,
            server_setter=lambda value: value,
            import_server=lambda: object(),
            read_json=lambda path, default: default,
            write_json=lambda path, payload: payload,
            utc_now_iso=lambda: "2026-04-05T00:00:00Z",
            normalize_workspace_id=lambda value: str(value or "default"),
            load_vault=lambda: {"credentials": []},
            workspace_visible=lambda workspace_id, entry: True,
            get_updates_process_lock=lambda bot_token: bot_token,
            resolve_vault_credential=lambda credential_id, workspace_id: {},
            http_json_request=lambda *args, **kwargs: {"args": args, "kwargs": kwargs},
            safe_path_token_impl=lambda value: str(value),
            telegram_space_question_via_mcp=lambda **kwargs: kwargs,
            utc_now=lambda: "now",
            parse_utc_ts=lambda value: value,
            create_run=lambda **kwargs: kwargs,
            decide_execution_target=lambda metadata: metadata,
            apply_execution_route_metadata=lambda metadata, route: metadata,
            normalize_trust_mode=lambda value: str(value),
            normalize_execution_target=lambda value: str(value),
            bridge_facade_class=_FakeFacade,
            registry_facade_class=_FakeFacade,
            runtime_facade_class=_FakeFacade,
        )
        self.assertEqual(shell._telegram_workspace_id(), "default")


if __name__ == "__main__":
    unittest.main()
