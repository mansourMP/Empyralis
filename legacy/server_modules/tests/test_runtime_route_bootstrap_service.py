import threading
import unittest

from server_modules import runtime_heartbeat_service
from server_modules import runtime_route_bootstrap_service


class _DummyScheduler:
    def __init__(self) -> None:
        self.started = False

    def start(self) -> None:
        self.started = True


class RuntimeRouteBootstrapServiceTests(unittest.TestCase):
    def test_import_runtime_run_route_dependencies_loads_expected_members(self):
        modules = {
            "server_modules.connectors.autopilot_runtime_exports": type(
                "Connectors",
                (),
                {"handle_telegram_send_message": staticmethod(lambda *args, **kwargs: None)},
            )(),
            "server_modules.memory_service": type(
                "MemoryService",
                (),
                {
                    "delete_memory": staticmethod(lambda *args, **kwargs: None),
                    "workspace_memory_snapshot": staticmethod(lambda *args, **kwargs: {}),
                },
            )(),
            "server_modules.runtime_models": type(
                "RuntimeModels",
                (),
                {"RunStartRequest": type("RunStartRequest", (), {})},
            )(),
            "server_modules.agent_turn": type(
                "AgentTurnModule",
                (),
                {
                    "build_inbound_agent_turn_request": staticmethod(lambda **kwargs: kwargs),
                    "execute_system_agent_turn": staticmethod(lambda **kwargs: {}),
                },
            )(),
            "server_modules.turn_ingress_service": type(
                "TurnIngressModule",
                (),
                {"start_system_turn": staticmethod(lambda **kwargs: {})},
            )(),
            "server_modules.workspace_context": type(
                "WorkspaceContext",
                (),
                {
                    "read_workspace_context_files": staticmethod(lambda: []),
                    "write_workspace_context_file": staticmethod(lambda *args, **kwargs: None),
                },
            )(),
            "server_modules.runs_core": type(
                "RunsCore",
                (),
                {"trigger_pending_heartbeat_schedules": staticmethod(lambda: {"started": []})},
            )(),
            "server_modules.control_plane_repository": type(
                "ControlPlane",
                (),
                {"tenant_id_for_workspace": staticmethod(lambda workspace_id: f"tenant-for-{workspace_id}")},
            )(),
        }
        module_globals = {"existing": 1}
        server_module = type("ServerModule", (), {})()
        server_module.FOO = "bar"

        deps = runtime_route_bootstrap_service.import_runtime_run_route_dependencies(
            import_module=lambda name, fromlist=(): modules[name],
            module_globals=module_globals,
            server_module=server_module,
        )

        self.assertEqual(module_globals["FOO"], "bar")
        self.assertTrue(callable(deps.handle_telegram_send_message))
        self.assertTrue(callable(deps.trigger_pending_heartbeat_schedules))
        self.assertTrue(callable(deps.resolve_workspace_tenant_id))

    def test_build_runtime_run_route_bootstrap_callbacks_builds_both_callbacks(self):
        bootstrap_callbacks = runtime_route_bootstrap_service.build_runtime_run_route_bootstrap_callbacks(
            run_start_request_class=object(),
            build_inbound_agent_turn_request=lambda **kwargs: kwargs,
            trigger_pending_heartbeat_schedules=lambda: {"started": []},
            execute_system_run_start_request_via_turn_runtime=lambda *args, **kwargs: {},
            execute_system_agent_turn=lambda **kwargs: {},
            stamp_request_owner_fn=lambda *args, **kwargs: None,
            run_execution_services=lambda: "services",
            build_heartbeat_run_callback=lambda **kwargs: ("run", kwargs),
            handle_telegram_send_message=lambda *args, **kwargs: None,
            build_heartbeat_notify_callback=lambda **kwargs: ("notify", kwargs),
            resolve_workspace_tenant_id=lambda workspace_id: f"tenant-for-{workspace_id}",
            heartbeat_workspace_id="ws-1",
        )

        self.assertEqual(bootstrap_callbacks.heartbeat_run_callback[0], "run")
        self.assertEqual(bootstrap_callbacks.heartbeat_notify_callback[0], "notify")
        self.assertEqual(
            bootstrap_callbacks.heartbeat_run_callback[1]["resolve_workspace_tenant_id"]("ws-1"),
            "tenant-for-ws-1",
        )
        self.assertTrue(callable(bootstrap_callbacks.heartbeat_run_callback[1]["claim_due_scheduler_wake_requests"]))
        self.assertTrue(callable(bootstrap_callbacks.heartbeat_run_callback[1]["build_wakeup_execution_bundle"]))
        self.assertTrue(callable(bootstrap_callbacks.heartbeat_run_callback[1]["finalize_scheduler_wake_requests"]))
        self.assertEqual(bootstrap_callbacks.heartbeat_notify_callback[1]["workspace_id"], "ws-1")

    def test_ensure_runtime_run_route_bootstrap_starts_scheduler_and_loads_webhooks(self):
        created = []
        loaded = []

        scheduler = runtime_route_bootstrap_service.ensure_runtime_run_route_bootstrap(
            heartbeat_lock=threading.Lock(),
            heartbeat_scheduler=None,
            heartbeat_scheduler_factory=lambda: created.append(_DummyScheduler()) or created[-1],
            ensure_heartbeat_scheduler_started=runtime_heartbeat_service.ensure_heartbeat_scheduler_started,
            load_webhook_triggers=lambda: loaded.append(True),
        )

        self.assertIs(scheduler, created[0])
        self.assertTrue(created[0].started)
        self.assertEqual(loaded, [True])


if __name__ == "__main__":
    unittest.main()
