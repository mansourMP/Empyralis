import unittest
from unittest.mock import patch
import importlib

from server_modules.connectors import autopilot_connector_shell_builder


class _FakeShellService:
    def __init__(self, **kwargs):
        self.kwargs = kwargs


class AutopilotConnectorShellBuilderTests(unittest.TestCase):
    def setUp(self) -> None:
        global autopilot_connector_shell_builder

        autopilot_connector_shell_builder = importlib.import_module(
            "server_modules.connectors.autopilot_connector_shell_builder"
        )

    def test_builder_keeps_late_bound_runtime_callbacks(self) -> None:
        namespace: dict[str, object] = {
            "_normalize_workspace_id": lambda value: f"ws:{value}",
        }

        built = autopilot_connector_shell_builder.build_autopilot_connector_shell_service(
            global_namespace=namespace,
            sync_server_globals=("X",),
            server_getter=lambda: None,
            server_setter=lambda value: None,
            import_server=lambda: object(),
            shell_service_class=_FakeShellService,
        )

        self.assertEqual(built.kwargs["normalize_workspace_id"]("alpha"), "ws:alpha")
        self.assertEqual(built.kwargs["run_history"], {})
        self.assertEqual(built.kwargs["local_worker_registry"], {})

        create_calls: list[dict[str, object]] = []
        namespace["create_run"] = lambda **kwargs: create_calls.append(kwargs) or "run-1"
        result = built.kwargs["create_run"](engine="orion", context={"metadata": {}})

        self.assertEqual(result, "run-1")
        self.assertEqual(create_calls[0]["engine"], "orion")

    def test_builder_resolve_vault_credential_falls_back_to_runtime_common_shape(self) -> None:
        namespace: dict[str, object] = {}

        def fake_import_attr(module_name: str, attr_name: str):
            if module_name == "server_modules.runtime_common" and attr_name == "resolve_vault_credential":
                return lambda credential_id, workspace_id=None: {
                    "credential_id": credential_id,
                    "workspace_id": workspace_id,
                }
            if module_name == "server_modules.runtime_common" and attr_name == "http_json_request":
                return lambda *args, **kwargs: {"args": args, "kwargs": kwargs}
            raise AssertionError(f"unexpected import {module_name}.{attr_name}")

        with patch("server_modules.connectors.autopilot_connector_shell_builder._import_attr", side_effect=fake_import_attr):
            built = autopilot_connector_shell_builder.build_autopilot_connector_shell_service(
                global_namespace=namespace,
                sync_server_globals=("X",),
                server_getter=lambda: None,
                server_setter=lambda value: None,
                import_server=lambda: object(),
                shell_service_class=_FakeShellService,
            )
            resolved = built.kwargs["resolve_vault_credential"]("cred-1", "default")

            self.assertEqual(
                resolved,
                {
                    "credential_id": "cred-1",
                    "workspace_id": "default",
                },
            )


if __name__ == "__main__":
    unittest.main()
