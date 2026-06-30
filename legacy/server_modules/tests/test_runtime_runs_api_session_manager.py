import sys
import types
import unittest
from unittest.mock import AsyncMock, Mock, patch

from fastapi import HTTPException

from server_modules import runtime_runs_api
from server_modules.agent_turn import build_direct_chat_turn_request
from server_modules.direct_chat_service import DirectChatExecutionServices, build_direct_chat_execution_services
from server_modules.run_service import RunExecutionServices
from server_modules.runtime_models import RunStartRequest
from server_modules import turn_runtime
from server_modules import session_service
from server_modules.turn_runtime import (
    TurnExecutionServices,
    build_turn_execution_services,
    build_execute_unowned_system_run_start_request_via_turn_runtime,
    execute_built_legacy_unowned_system_run_start_request_via_turn_runtime,
    execute_built_unowned_system_run_start_request_via_turn_runtime,
    execute_agent_turn_request,
    execute_run_start_request_via_turn_runtime,
    execute_system_run_start_request_via_turn_runtime,
    execute_unowned_system_run_start_request_via_turn_runtime,
)


class _DummyManager:
    def __init__(self) -> None:
        self.calls = []
        self.eviction_calls = 0

    def evict_idle_handles(self, **kwargs):
        self.eviction_calls += 1
        return 0

    def iter_turn_events(self, **kwargs):
        self.calls.append(kwargs)
        yield {
            "type": "final",
            "payload": {
                "reply": "session-manager",
                "actions": [],
                "mode": "answer",
                "error": "",
            },
        }


class _FakeApp:
    def __init__(self) -> None:
        self.routes = {}

    def _register(self, method, path, **kwargs):
        def _decorator(fn):
            self.routes[(method, path)] = fn
            return fn

        return _decorator

    def get(self, path, **kwargs):
        return self._register("GET", path, **kwargs)

    def post(self, path, **kwargs):
        return self._register("POST", path, **kwargs)

    def delete(self, path, **kwargs):
        return self._register("DELETE", path, **kwargs)


class _FakeRequest:
    def __init__(self, payload=None, *, headers=None) -> None:
        self._payload = payload or {}
        self.headers = headers or {}

    async def json(self):
        return self._payload


class RuntimeRunsApiSessionManagerTests(unittest.TestCase):
    def _current_user(self):
        return {
            "user_id": "user-1",
            "email": "user@example.com",
            "auth_type": "bearer",
            "role": "owner",
            "is_admin": True,
            "workspace_ids": ["default"],
            "workspace_access": {
                "default": {
                    "workspace_id": "default",
                    "tenant_id": "default",
                    "role": "owner",
                    "tenant_role": "owner",
                    "capabilities": {"allow": [], "deny": []},
                    "tenant_capabilities": {"allow": [], "deny": []},
                    "workspace_capabilities": {"allow": [], "deny": []},
                    "dangerous_action_classes": {"allow": [], "deny": []},
                    "tenant_dangerous_action_classes": {"allow": [], "deny": []},
                    "workspace_dangerous_action_classes": {"allow": [], "deny": []},
                    "connectors": {"allow": [], "deny": []},
                    "tenant_connectors": {"allow": [], "deny": []},
                    "workspace_connectors": {"allow": [], "deny": []},
                    "machine_enrollment_scope": "workspace",
                    "trusted_owner_machine_ids": [],
                }
            },
        }

    def test_builder_helpers_preserve_turn_runtime_callbacks(self):
        run_execution = RunExecutionServices(
            stamp_request_owner=lambda req, current_user: req,
            prepare_run_start_request=lambda req: {"metadata": dict(req.metadata or {})},
            create_run_from_request=lambda req: {"run_id": "unused"},
        )
        direct_chat = build_direct_chat_execution_services(
            chat_stream_key=runtime_runs_api._chat_stream_key,
            session_manager_enabled=runtime_runs_api._direct_chat_session_manager_enabled,
            session_manager_factory=runtime_runs_api._direct_chat_session_manager,
            build_direct_operator_reply=lambda **kwargs: {"reply": "unused"},
            build_chat_turn_event_stream=lambda **kwargs: iter(()),
        )

        services = build_turn_execution_services(
            run_execution=run_execution,
            direct_chat=direct_chat,
        )

        self.assertIs(services.run_execution, run_execution)
        self.assertIs(services.direct_chat, direct_chat)

    def test_runtime_runs_api_uses_session_manager_when_flag_enabled(self):
        manager = _DummyManager()
        body = {
            "message": "hello",
            "thread_id": "thread-1",
            "provider": "openai",
            "model": "gpt-test",
            "availability": {},
        }
        current_user = {"user_id": "user-1", "email": "user@example.com"}

        with patch.object(runtime_runs_api, "_direct_chat_session_manager_enabled", return_value=True), patch.object(
            runtime_runs_api,
            "_direct_chat_session_manager",
            return_value=manager,
        ):
            producer = runtime_runs_api._build_direct_chat_event_producer(
                current_user=current_user,
                body=body,
                message="hello",
                workspace_id="default",
                session_key="user-1:thread-1:req-1",
                thread_id="thread-1",
                client_request_id="req-1",
            )
            events = list(producer)

        self.assertEqual(events[-1]["payload"]["reply"], "session-manager")
        self.assertEqual(len(manager.calls), 1)
        call = manager.calls[0]
        self.assertEqual(call["session_id"], "user-1:default:thread-1")
        self.assertEqual(call["actor_key"], "user-1:default:thread-1")
        self.assertEqual(call["workspace_id"], "default")
        self.assertEqual(call["user_id"], "user-1")
        self.assertEqual(call["request_meta"]["request_id"], "req-1")
        self.assertEqual(call["request_meta"]["agent_turn_request"]["workspace_id"], "default")
        self.assertEqual(call["request_meta"]["agent_turn_request"]["session_id"], "thread-1")
        self.assertEqual(call["request_meta"]["agent_turn_request"]["message"], "hello")
        self.assertEqual(manager.eviction_calls, 1)

    def test_execute_agent_turn_request_returns_direct_chat_stream_plan(self):
        turn_request = build_direct_chat_turn_request(
            current_user={"user_id": "user-1"},
            body={"thread_id": "thread-1"},
            workspace_id="default",
            thread_id="thread-1",
            client_request_id="req-1",
            message="hello",
        )
        services = build_turn_execution_services(
            run_execution=RunExecutionServices(
                stamp_request_owner=lambda req, current_user: req,
                prepare_run_start_request=lambda req: {"metadata": dict(req.metadata or {})},
                create_run_from_request=lambda req: {"run_id": "unused"},
            ),
            direct_chat=build_direct_chat_execution_services(
                chat_stream_key=runtime_runs_api._chat_stream_key,
                session_manager_enabled=runtime_runs_api._direct_chat_session_manager_enabled,
                session_manager_factory=runtime_runs_api._direct_chat_session_manager,
                build_direct_operator_reply=lambda **kwargs: {"reply": "unused"},
                build_chat_turn_event_stream=lambda **kwargs: iter(()),
            ),
        )

        execution = __import__("asyncio").run(
            execute_agent_turn_request(
                turn_request=turn_request,
                current_user={"user_id": "user-1"},
                services=services,
                chat_body={"thread_id": "thread-1", "client_request_id": "req-1"},
            )
        )

        self.assertEqual(execution["kind"], "direct_chat_stream")
        self.assertEqual(execution["workspace_id"], "default")
        self.assertEqual(execution["thread_id"], "thread-1")
        self.assertEqual(execution["client_request_id"], "req-1")
        self.assertTrue(callable(execution["producer"]))

    def test_execute_agent_turn_request_delegates_durable_branch_through_run_service(self):
        turn_request = type("TurnRequest", (), {"execution_mode": "durable"})()
        services = build_turn_execution_services(
            run_execution=RunExecutionServices(
                stamp_request_owner=lambda req, current_user: req,
                prepare_run_start_request=lambda req: {"metadata": dict(req.metadata or {})},
                create_run_from_request=lambda req: {"run_id": "unused"},
            ),
            direct_chat=build_direct_chat_execution_services(
                chat_stream_key=runtime_runs_api._chat_stream_key,
                session_manager_enabled=runtime_runs_api._direct_chat_session_manager_enabled,
                session_manager_factory=runtime_runs_api._direct_chat_session_manager,
                build_direct_operator_reply=lambda **kwargs: {"reply": "unused"},
                build_chat_turn_event_stream=lambda **kwargs: iter(()),
            ),
        )

        with patch.object(
            turn_runtime.run_service,
            "execute_durable_agent_turn_dispatch",
            new=AsyncMock(return_value={"result": {"run_id": "run-durable"}}),
        ) as execute_mock:
            execution = __import__("asyncio").run(
                execute_agent_turn_request(
                    turn_request=turn_request,
                    current_user={"user_id": "user-1"},
                    services=services,
                    run_request={"run": True},
                )
            )

        self.assertEqual(execution["result"]["run_id"], "run-durable")
        self.assertIs(execute_mock.await_args.kwargs["turn_request"], turn_request)
        self.assertEqual(execute_mock.await_args.kwargs["base_request"], {"run": True})

    def test_execute_run_start_request_via_turn_runtime_returns_result_payload(self):
        request = RunStartRequest(engine="orion", workspace_id="default", user_goal="hello")
        services = RunExecutionServices(
            stamp_request_owner=lambda req, current_user: req,
            prepare_run_start_request=lambda req: {"metadata": dict(req.metadata or {})},
            create_run_from_request=lambda req: {"run_id": "unused"},
        )

        with patch(
            "server_modules.turn_ingress_service.start_run_start",
            new=AsyncMock(return_value={"run_id": "run-1", "status": "starting"}),
        ):
            result = __import__("asyncio").run(
                execute_run_start_request_via_turn_runtime(
                    request,
                    current_user={"user_id": "user-1"},
                    stamp_request_owner_fn=lambda req, current_user: req,
                    services=services,
                )
            )

        self.assertEqual(result["run_id"], "run-1")
        self.assertEqual(result["status"], "starting")

    def test_execute_system_run_start_request_via_turn_runtime_uses_system_user(self):
        request = RunStartRequest(engine="orion", workspace_id="default", user_goal="hello")
        captured = {}

        async def _fake_execute(request_payload, *, current_user, **kwargs):
            captured["current_user"] = dict(current_user or {})
            return {"run_id": "run-2", "status": "starting"}

        with patch(
            "server_modules.turn_ingress_service.start_run_start",
            side_effect=_fake_execute,
        ):
            result = execute_system_run_start_request_via_turn_runtime(
                request,
                stamp_request_owner_fn=lambda req, current_user: req,
                services=RunExecutionServices(
                    stamp_request_owner=lambda req, current_user: req,
                    prepare_run_start_request=lambda req: {"metadata": dict(req.metadata or {})},
                    create_run_from_request=lambda req: {"run_id": "unused"},
                ),
            )

        self.assertEqual(result["run_id"], "run-2")
        self.assertEqual(captured["current_user"]["auth_type"], "api_key")

    def test_execute_unowned_system_run_start_request_via_turn_runtime_uses_noop_owner_stamp(self):
        request = RunStartRequest(engine="orion", workspace_id="default", user_goal="hello")
        captured = {}
        original = object()

        def _fake_system_execute(request_payload, *, stamp_request_owner_fn, current_user=None, **kwargs):
            captured["stamped"] = stamp_request_owner_fn(original, {"user_id": "ignored"})
            captured["current_user"] = dict(current_user or {})
            return {"run_id": "run-3", "status": "starting"}

        with patch.object(
            turn_runtime,
            "execute_system_run_start_request_via_turn_runtime",
            side_effect=_fake_system_execute,
        ):
            result = execute_unowned_system_run_start_request_via_turn_runtime(
                request,
                services=RunExecutionServices(
                    stamp_request_owner=lambda req, current_user: req,
                    prepare_run_start_request=lambda req: {"metadata": dict(req.metadata or {})},
                    create_run_from_request=lambda req: {"run_id": "unused"},
                ),
            )

        self.assertEqual(result["run_id"], "run-3")
        self.assertIs(captured["stamped"], original)

    def test_turn_route_normalizes_session_scope_violation_to_http_conflict(self):
        fake_server = types.ModuleType("server")
        fake_server.require_api_key = object()
        fake_server.require_admin_api_key = object()
        fake_server.ORION_SINGLE_AGENT_MODE = False
        fake_server.runs = {}
        fake_server.iter_logs_for_run = lambda run_id: []
        fake_server._get_replay_payload = lambda run_id: {}

        previous_server = sys.modules.get("server")
        sys.modules["server"] = fake_server
        original_register = runtime_runs_api.runtime_route_registration_service.register_runtime_run_routes_from_api
        original_refresh = runtime_runs_api._refresh_server_exports
        original_stamp = runtime_runs_api._stamp_workspace_authorization_on_turn_payload
        original_turn = runtime_runs_api.execute_canonical_agent_turn
        original_run_services = runtime_runs_api._run_execution_services
        original_direct_chat_services = runtime_runs_api._direct_chat_execution_services
        original_start_turn = runtime_runs_api.turn_ingress_service.start_turn
        try:
            runtime_runs_api.runtime_route_registration_service.register_runtime_run_routes_from_api = lambda *args, **kwargs: None
            runtime_runs_api._refresh_server_exports = lambda: fake_server
            runtime_runs_api._stamp_workspace_authorization_on_turn_payload = lambda payload, **kwargs: payload
            runtime_runs_api._run_execution_services = lambda: "run-services"
            runtime_runs_api._direct_chat_execution_services = lambda: "chat-services"
            runtime_runs_api.execute_canonical_agent_turn = AsyncMock(
                side_effect=session_service.SessionScopeViolationError("scope mismatch")
            )
            runtime_runs_api.turn_ingress_service.start_turn = AsyncMock(
                side_effect=session_service.SessionScopeViolationError("scope mismatch")
            )

            app = _FakeApp()
            runtime_runs_api.register_run_routes(app)

            with self.assertRaises(HTTPException) as ctx:
                __import__("asyncio").run(
                    app.routes[("POST", "/turn")](
                        _FakeRequest(
                            {
                                "thread_id": "thread-1",
                                "workspace_id": "default",
                                "session_id": "thread-1",
                                "channel": "web",
                                "actor": {"type": "user", "id": "user-1"},
                                "message": "hello",
                                "execution_mode": "durable",
                                "response_mode": "artifact",
                            }
                        ),
                        {
                            "thread_id": "thread-1",
                            "workspace_id": "default",
                            "session_id": "thread-1",
                            "channel": "web",
                            "actor": {"type": "user", "id": "user-1"},
                            "message": "hello",
                            "execution_mode": "durable",
                            "response_mode": "artifact",
                        },
                        current_user=self._current_user(),
                    )
                )

            self.assertEqual(ctx.exception.status_code, 409)
            self.assertEqual(ctx.exception.detail["code"], "session_scope_mismatch")
        finally:
            runtime_runs_api.runtime_route_registration_service.register_runtime_run_routes_from_api = original_register
            runtime_runs_api._refresh_server_exports = original_refresh
            runtime_runs_api._stamp_workspace_authorization_on_turn_payload = original_stamp
            runtime_runs_api.execute_canonical_agent_turn = original_turn
            runtime_runs_api._run_execution_services = original_run_services
            runtime_runs_api._direct_chat_execution_services = original_direct_chat_services
            runtime_runs_api.turn_ingress_service.start_turn = original_start_turn
            if previous_server is not None:
                sys.modules["server"] = previous_server
            else:
                sys.modules.pop("server", None)

    def test_execute_built_unowned_system_run_start_request_via_turn_runtime_builds_services(self):
        request = RunStartRequest(engine="orion", workspace_id="default", user_goal="hello")
        built_services = RunExecutionServices(
            stamp_request_owner=lambda req, current_user: req,
            prepare_run_start_request=lambda req: {"metadata": {}},
            create_run_from_request=lambda req: {"run_id": "unused"},
        )
        captured = {}

        def _fake_execute(request_payload, *, stamp_request_owner_fn, services, **kwargs):
            captured["stamped"] = stamp_request_owner_fn("req", {"user_id": "ignored"})
            captured["services"] = services
            return {"run_id": "run-4", "status": "starting"}

        result = execute_built_unowned_system_run_start_request_via_turn_runtime(
            request,
            execute_system_run_start_request_via_turn_runtime_fn=_fake_execute,
            build_run_execution_services_fn=lambda: built_services,
        )

        self.assertEqual(result["run_id"], "run-4")
        self.assertEqual(captured["stamped"], "req")
        self.assertIs(captured["services"], built_services)

    def test_execute_built_legacy_unowned_system_run_start_request_via_turn_runtime_uses_runtime_even_with_mock(self):
        request = RunStartRequest(engine="orion", workspace_id="default", user_goal="hello")
        create_run = Mock(return_value={"run_id": "run-5", "status": "starting"})
        built_services = RunExecutionServices(
            stamp_request_owner=lambda req, current_user: req,
            prepare_run_start_request=lambda req: {"metadata": {}},
            create_run_from_request=lambda req: {"run_id": "unused"},
        )

        with patch(
            "server_modules.turn_runtime.execute_built_unowned_system_run_start_request_via_turn_runtime"
        ) as execute_built:
            execute_built.return_value = {"run_id": "run-5", "status": "starting"}
            result = execute_built_legacy_unowned_system_run_start_request_via_turn_runtime(
                request,
                execute_system_run_start_request_via_turn_runtime_fn=lambda *args, **kwargs: {"run_id": "unexpected"},
                build_run_execution_services_fn=lambda: built_services,
                create_run_from_request_fn=create_run,
            )

        self.assertEqual(result["run_id"], "run-5")
        create_run.assert_not_called()
        execute_built.assert_called_once()

    def test_execute_built_legacy_unowned_system_run_start_request_via_turn_runtime_uses_runtime_when_not_mocked(self):
        request = RunStartRequest(engine="orion", workspace_id="default", user_goal="hello")
        built_services = RunExecutionServices(
            stamp_request_owner=lambda req, current_user: req,
            prepare_run_start_request=lambda req: {"metadata": {}},
            create_run_from_request=lambda req: {"run_id": "unused"},
        )

        with patch(
            "server_modules.turn_runtime.execute_built_unowned_system_run_start_request_via_turn_runtime",
            return_value={"run_id": "run-6", "status": "starting"},
        ) as execute_built:
            result = execute_built_legacy_unowned_system_run_start_request_via_turn_runtime(
                request,
                execute_system_run_start_request_via_turn_runtime_fn=lambda *args, **kwargs: {"run_id": "unexpected"},
                build_run_execution_services_fn=lambda: built_services,
                create_run_from_request_fn=lambda req: {"run_id": "unused"},
            )

        self.assertEqual(result["run_id"], "run-6")
        execute_built.assert_called_once()

    def test_build_execute_unowned_system_run_start_request_via_turn_runtime_delegates_to_run_service(self):
        with patch.object(
            turn_runtime.run_service,
            "build_execute_unowned_system_run_start_request_via_turn_runtime",
            return_value="executor",
        ) as build_mock:
            executor = build_execute_unowned_system_run_start_request_via_turn_runtime(
                execute_unowned_system_run_start_request_via_turn_runtime_fn=lambda *args, **kwargs: {}
            )

        self.assertEqual(executor, "executor")
        build_mock.assert_called_once()


if __name__ == "__main__":
    unittest.main()
