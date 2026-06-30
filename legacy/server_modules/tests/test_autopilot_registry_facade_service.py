import unittest

from server_modules.connectors.autopilot_registry_facade_service import AutopilotRegistryFacadeService


class _FakeHelperBridge:
    instances = []

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        type(self).instances.append(self)

    def telegram_helper_registry(self):
        class _Registry:
            def profile_service(self_inner):
                class _Profile:
                    def normalize_profile_field(self, value):
                        return value

                    def get_profile(self, workspace_id, chat_id):
                        return {"workspace_id": workspace_id, "chat_id": chat_id}

                    def build_goal_with_profile(self, goal, profile):
                        return {"goal": goal, "profile": profile}

                    def onboarding_prompt(self, step_index, retry=False):
                        return {"step_index": step_index, "retry": retry}

                    def start_onboarding(self, workspace_id, chat_id):
                        return {"workspace_id": workspace_id, "chat_id": chat_id}

                    def profile_text(self, profile, chat_profile):
                        return {"profile": profile, "chat_profile": chat_profile}

                    def profile_help_text(self, profile):
                        return profile

                    def set_profile_field(self, workspace_id, chat_id, field_name, value):
                        return {"workspace_id": workspace_id, "chat_id": chat_id, "field_name": field_name, "value": value}

                    def clear_profile(self, workspace_id, chat_id, field_name):
                        return {"workspace_id": workspace_id, "chat_id": chat_id, "field_name": field_name}

                    def get_onboarding_state(self, workspace_id, chat_id):
                        return {"workspace_id": workspace_id, "chat_id": chat_id}

                    def onboarding_consume_answer(self, workspace_id, chat_id, text):
                        return {"workspace_id": workspace_id, "chat_id": chat_id, "text": text}

                    def profile_has_context(self, profile):
                        return bool(profile)

                return _Profile()

            def routing_service(self_inner):
                class _Routing:
                    def help_text(self, profile):
                        return profile

                    def route_message(self, text, profile):
                        return {"text": text, "profile": profile}

                    def is_explicit_run_command(self, raw_text):
                        return raw_text.startswith("/run")

                return _Routing()

            def media_service(self_inner):
                class _Media:
                    def extract_message(self, update):
                        return update.get("message")

                    def store_attachments(self, **kwargs):
                        return kwargs

                    def build_goal_with_attachments(self, goal, attachments):
                        return {"goal": goal, "attachments": attachments}

                return _Media()

            def camera_setup_service(self_inner):
                return {"camera": True}

        return _Registry()


class _FakeSupportRegistry:
    instances = []

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        type(self).instances.append(self)

    def profile_service(self):
        return {"profile": True}

    def runtime_status_service(self):
        return {"status": True}

    def workflow_setup_service(self):
        class _Workflow:
            def handle_telegram_guided_automation_setup(self, **kwargs):
                return kwargs

        return _Workflow()

    def connector_context_service(self):
        class _Context:
            def workspace_connector_context(self, **kwargs):
                return kwargs

            def build_goal_with_connector_context(self, goal, prompt):
                return {"goal": goal, "prompt": prompt}

            def installed_skill_query(self, **kwargs):
                return kwargs

        return _Context()

    def approval_service(self):
        return {"approval": True}

    def common_support_service(self):
        class _Common:
            def cognitive_module(self):
                return "module"

            def cognitive_defaults(self):
                return {"ok": True}

            def normalize_string_list(self, value):
                return value

            def chat_id_from_session_key(self, key):
                return key

        return _Common()

    def skill_service(self):
        class _Skill:
            def select_skill_from_text(self, text):
                return {"text": text}

            def telegram_skill_goal(self, skill):
                return f"goal:{skill}"

            def telegram_skills_menu_text(self, profile):
                return f"skills:{profile}"

        return _Skill()

    def channel_support_service(self):
        class _Channel:
            def truncate_one_line(self, text, limit):
                return str(text)[:limit]

            def classify_error(self, detail):
                return detail

            def iso_from_epoch(self, ts):
                return ts

            def telegram_autopilot_log(self, message):
                return message

            def telegram_session_key(self, chat_id):
                return f"tg:{chat_id}"

            def whatsapp_session_key(self, inbound_from, inbound_to):
                return f"wa:{inbound_from}:{inbound_to}"

            def telegram_trace_id(self, chat_id, update_id, message_id):
                return f"{chat_id}:{update_id}:{message_id}"

            def include_run_meta(self):
                return True

        return _Channel()


class _FakeRuntimeRegistry:
    instances = []

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        type(self).instances.append(self)

    def connector_support_service(self):
        class _Connector:
            def resolve_allow_from(self, entry, allow_from):
                return allow_from

            def get_secret(self, entry):
                return entry

            def connector_paused(self, item):
                return False

            def chat_matches(self, configured_chat_id, chat):
                return configured_chat_id == chat

            def sender_allowed(self, sender, allow_from):
                return True

            def connector_assigned_agent_role(self, entry):
                return "assistant"

            def bool_from_any(self, value, default=False):
                return bool(value) if value is not None else default

        return _Connector()

    def transport_service(self):
        class _Transport:
            def api_request(self, *args, **kwargs):
                return {"args": args, "kwargs": kwargs}

            def send_message(self, **kwargs):
                return kwargs

            def send_chat_action(self, *args, **kwargs):
                return {"args": args, "kwargs": kwargs}

            def edit_message(self, *args, **kwargs):
                return {"args": args, "kwargs": kwargs}

        return _Transport()

    def terminal_service(self):
        return {"terminal": True}

    def run_entry_service(self):
        class _RunEntry:
            def create_telegram_run(self, **kwargs):
                return kwargs

            def create_whatsapp_run(self, **kwargs):
                return kwargs

            def can_auto_approve_wait(self, run):
                return False

            def pending_confirmation_payload(self, run):
                return run

        return _RunEntry()

    def runtime_support_service(self):
        class _RuntimeSupport:
            def local_companion_snapshot(self):
                return {"local": True}

            def current_runtime_metrics(self):
                return {"metrics": True}

            def latest_runtime_run_summary(self):
                return "summary"

            def humanize_telegram_run_summary(self, text):
                return text

            def latest_run_error_message(self, run):
                return run

            def is_non_retryable_run_error(self, error):
                return False

            def friendly_run_error(self, error):
                return error

            def summarize_run_terminal_result(self, run, limit):
                return {"run": run, "limit": limit}

        return _RuntimeSupport()

    def menu_service(self):
        class _Menu:
            def menu_keyboard(self, profile, menu_id):
                return {"profile": profile, "menu_id": menu_id}

            def reply_keyboard(self, profile):
                return {"profile": profile}

        return _Menu()


class _FakeDirectTelegramRegistry:
    instances = []

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        type(self).instances.append(self)


class _FakeDirectWhatsAppRegistry:
    instances = []

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        type(self).instances.append(self)


class _FakeBridgeFacade:
    def event_bridge_service(self):
        class _EventBridge:
            def append_channel_dead_letter(self, **kwargs):
                return kwargs

            def record_channel_event(self, **kwargs):
                return kwargs

            def record_channel_event_throttled(self, **kwargs):
                return kwargs

        return _EventBridge()


class AutopilotRegistryFacadeServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        _FakeHelperBridge.instances.clear()
        _FakeSupportRegistry.instances.clear()
        _FakeRuntimeRegistry.instances.clear()
        _FakeDirectTelegramRegistry.instances.clear()
        _FakeDirectWhatsAppRegistry.instances.clear()

    def _service(self, patched):
        return AutopilotRegistryFacadeService(
            project_root="/tmp/project",
            default_chat_prefix="/empyralis",
            telegram_default_workspace_id_getter=lambda: patched["telegram_workspace_id"],
            telegram_onboarding_enabled=True,
            telegram_require_prefix_getter=lambda: patched["telegram_require_prefix"],
            telegram_prefix_getter=lambda: patched["telegram_prefix"],
            telegram_space_status_enabled=True,
            telegram_media_max_items=4,
            telegram_max_updates=5,
            telegram_poll_seconds=2.0,
            telegram_delivery_mode_getter=lambda: patched["telegram_delivery_mode"],
            telegram_run_timeout_seconds_getter=lambda: patched["telegram_timeout"],
            telegram_max_reply_chars_getter=lambda: patched["telegram_max_reply_chars"],
            telegram_send_ack_getter=lambda: patched["telegram_send_ack"],
            telegram_enabled_getter=lambda: patched["telegram_enabled"],
            telegram_default_profile_getter=lambda: patched["telegram_profile"],
            telegram_guided_automation_setup_enabled=True,
            telegram_trust_mode_value_getter=lambda: patched["telegram_trust_mode"],
            telegram_execution_target_value_getter=lambda: patched["telegram_execution_target"],
            whatsapp_enabled_getter=lambda: patched["whatsapp_enabled"],
            whatsapp_default_profile_getter=lambda: patched["whatsapp_profile"],
            whatsapp_require_prefix_getter=lambda: patched["whatsapp_require_prefix"],
            whatsapp_prefix_getter=lambda: patched["whatsapp_prefix"],
            whatsapp_run_timeout_seconds_getter=lambda: patched["whatsapp_timeout"],
            whatsapp_max_reply_chars_getter=lambda: patched["whatsapp_max_reply_chars"],
            whatsapp_send_ack_getter=lambda: patched["whatsapp_send_ack"],
            whatsapp_trust_mode_value_getter=lambda: patched["whatsapp_trust_mode"],
            whatsapp_execution_target_value_getter=lambda: patched["whatsapp_execution_target"],
            telegram_state_getter=lambda: {"active": True},
            telegram_lock_getter=lambda: object(),
            telegram_state_file="/tmp/tg-state.json",
            whatsapp_state_getter=lambda: {"active": True},
            whatsapp_lock_getter=lambda: object(),
            whatsapp_state_file="/tmp/wa-state.json",
            read_json=lambda path, default: default,
            write_json=lambda path, payload: payload,
            utc_now_iso=lambda: "2026-04-05T00:00:00Z",
            normalize_workspace_id=lambda value: str(value or "default"),
            load_vault=lambda: {"vault": True},
            workspace_visible=lambda workspace_id, entry: True,
            telegram_thread_alive=lambda: True,
            telegram_allow_from_value=lambda: "",
            get_updates_process_lock=lambda bot_token: bot_token,
            mark_telegram_started=lambda started_at: started_at,
            resolve_vault_credential=lambda credential_id, workspace_id: {"credential_id": credential_id, "workspace_id": workspace_id},
            safe_path_token=lambda value: f"safe:{value}",
            runs_get=lambda run_id: {"run_id": run_id},
            telegram_space_question_via_mcp=lambda **kwargs: kwargs,
            helper_profile_state_file="/tmp/profiles.json",
            helper_onboarding_state_file="/tmp/onboarding.json",
            helper_camera_setup_state_file="/tmp/camera.json",
            helper_media_dir="/tmp/media",
            helper_media_enabled=True,
            helper_media_max_items=4,
            helper_media_max_bytes=1024,
            helper_media_include_in_goal=True,
            helper_quick_goal_templates={"project update": "summary"},
            helper_menu_goal_templates={"project update": "summary"},
            support_workflow_api_url="http://workflow",
            support_runtime_url="http://runtime",
            support_web_url="http://web",
            telegram_profile_catalog_getter=lambda: {"ops": {}},
            whatsapp_profile_catalog_getter=lambda: {"support": {}},
            support_installed_skills_enabled=True,
            support_error_category_hints=[("timeout", "timeout")],
            support_engine_validation_errors_getter=lambda: [],
            env_get=lambda key, default="": default,
            init_runtime=lambda: None,
            runtime_telegram_engine_getter=lambda: "orion",
            runtime_whatsapp_engine_getter=lambda: "orion",
            runtime_telegram_show_buttons=True,
            runtime_local_lease_seconds=30,
            runtime_non_retryable_run_error_hints=["fatal"],
            runtime_builtin_skills_limit_builder=lambda scope_key, limit: [{"scope_key": scope_key, "limit": limit}],
            normalize_agent_role=lambda value: str(value or "").lower(),
            allow_any_chat_getter=lambda: True,
            http_json_request=lambda *args, **kwargs: {"args": args, "kwargs": kwargs},
            telegram_profile_fields=["about"],
            normalize_trust_mode=lambda value: str(value),
            normalize_execution_target=lambda value: str(value),
            decide_execution_target=lambda metadata: {"selected": "cloud", "metadata": metadata},
            apply_execution_route_metadata=lambda metadata, route: {**metadata, "route": route},
            create_run=lambda **kwargs: kwargs,
            inherit_owner_user_id=lambda owner_user_id=None: owner_user_id,
            agent_machine_full_trust_enabled=lambda owner_user_id: True,
            run_history=[],
            run_history_lock=object(),
            runtime_metrics={"runs": 1},
            metrics_lock=object(),
            utc_now=lambda: "now",
            parse_utc_ts=lambda value: value,
            worker_online_helper=lambda record, now=None: True,
            local_queue_lock=object(),
            local_pending_run_ids=[],
            local_claimed_runs=[],
            local_worker_registry={},
            truncate_one_line=lambda text, limit: str(text)[:limit],
            bool_from_any=lambda value, default=False: bool(value) if value is not None else default,
            local_companion_snapshot=lambda: {"local": True},
            current_runtime_metrics=lambda: {"metrics": True},
            latest_runtime_run_summary=lambda: "summary",
            list_vault_connectors=lambda workspace_id: [],
            list_recent_connector_messages=lambda credentials, limit: [],
            query_active_installed_skills=lambda **kwargs: [],
            runtime_builtin_skills_getter=lambda: [],
            runtime_skills_snapshot_getter=lambda: [],
            bridge_facade_getter=lambda: _FakeBridgeFacade(),
            helper_registry_bridge_class=_FakeHelperBridge,
            telegram_service_registry_class=_FakeDirectTelegramRegistry,
            whatsapp_service_registry_class=_FakeDirectWhatsAppRegistry,
            support_registry_class=_FakeSupportRegistry,
            runtime_registry_class=_FakeRuntimeRegistry,
        )

    def test_facade_builds_direct_registries_without_channel_support_runtime_bridges(self) -> None:
        patched = {
            "telegram_workspace_id": "workspace-123",
            "telegram_require_prefix": False,
            "telegram_prefix": "/empyralis",
            "telegram_timeout": 180,
            "telegram_max_reply_chars": 1200,
            "telegram_send_ack": False,
            "telegram_delivery_mode": "webhook",
            "telegram_enabled": True,
            "telegram_profile": "ops",
            "telegram_trust_mode": "workspace_write",
            "telegram_execution_target": "local",
            "whatsapp_enabled": True,
            "whatsapp_profile": "support",
            "whatsapp_require_prefix": False,
            "whatsapp_prefix": "/empyralis",
            "whatsapp_timeout": 180,
            "whatsapp_max_reply_chars": 1200,
            "whatsapp_send_ack": False,
            "whatsapp_trust_mode": "workspace_write",
            "whatsapp_execution_target": "local",
        }
        service = self._service(patched)

        telegram_registry = service.telegram_service_registry()
        whatsapp_registry = service.whatsapp_service_registry()

        self.assertIs(telegram_registry, service.telegram_service_registry())
        self.assertIs(whatsapp_registry, service.whatsapp_service_registry())
        self.assertEqual(len(_FakeHelperBridge.instances), 1)
        self.assertEqual(len(_FakeSupportRegistry.instances), 1)
        self.assertEqual(len(_FakeRuntimeRegistry.instances), 1)
        self.assertEqual(len(_FakeDirectTelegramRegistry.instances), 1)
        self.assertEqual(len(_FakeDirectWhatsAppRegistry.instances), 1)
        self.assertEqual(telegram_registry.kwargs["default_workspace_id"], "workspace-123")
        self.assertEqual(telegram_registry.kwargs["delivery_mode"], "webhook")
        self.assertEqual(whatsapp_registry.kwargs["default_profile"], "support")
        self.assertTrue(whatsapp_registry.kwargs["enabled"])
        self.assertEqual(service.profile_service(), {"profile": True})
        self.assertEqual(service.runtime_status_service(), {"status": True})
        self.assertEqual(service.terminal_service(), {"terminal": True})


if __name__ == "__main__":
    unittest.main()
