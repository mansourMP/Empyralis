import unittest
import importlib
from unittest.mock import patch

from fastapi import HTTPException

from server_modules import app_bridge_service


class AppBridgeServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        global app_bridge_service
        app_bridge_service = importlib.import_module("server_modules.app_bridge_service")

    def test_normalize_bridge_contract_rejects_unsupported_bridge_type(self) -> None:
        with patch(
            "server_modules.app_bridge_service._resolve_registry_app_item",
            return_value={"id": "study", "status": "installed", "permissions": ["files.read"]},
        ):
            with self.assertRaises(HTTPException) as ctx:
                app_bridge_service.normalize_bridge_contract(
                    app_id="study",
                    bridge_kind="app_to_sage",
                    bridge_type="bad_type",
                )

        self.assertEqual(ctx.exception.status_code, 400)

    def test_normalize_bridge_contract_requires_specialist_target(self) -> None:
        with patch(
            "server_modules.app_bridge_service._resolve_registry_app_item",
            return_value={"id": "study", "status": "installed", "permissions": ["files.read"]},
        ):
            with self.assertRaises(HTTPException) as ctx:
                app_bridge_service.normalize_bridge_contract(
                    app_id="study",
                    bridge_kind="app_to_specialist",
                    bridge_type="status_request",
                )

        self.assertEqual(ctx.exception.status_code, 400)

    def test_normalize_bridge_contract_rejects_nested_implicit_captain_context_metadata(self) -> None:
        with patch(
            "server_modules.app_bridge_service._resolve_registry_app_item",
            return_value={"id": "study", "status": "installed", "permissions": ["files.read"]},
        ):
            with self.assertRaises(HTTPException) as ctx:
                app_bridge_service.normalize_bridge_contract(
                    app_id="study",
                    bridge_kind="app_to_sage",
                    bridge_type="summary_request",
                    metadata={
                        "captain_context": {"raw": True},
                    },
                )

        self.assertEqual(ctx.exception.status_code, 400)
        self.assertIn("captain_context", str(ctx.exception.detail))

    def test_normalize_bridge_contract_rejects_nested_forbidden_target_key(self) -> None:
        with patch(
            "server_modules.app_bridge_service._resolve_registry_app_item",
            return_value={"id": "study", "status": "installed", "permissions": ["files.read"]},
        ):
            with self.assertRaises(HTTPException) as ctx:
                app_bridge_service.normalize_bridge_contract(
                    app_id="study",
                    bridge_kind="app_to_sage",
                    bridge_type="summary_request",
                    target={
                        "safe": {"steps": [1, 2, 3]},
                        "payload": [
                            {"kind": "safe"},
                            {"nested": {"sage_memory": {"raw": "blocked"}}},
                        ],
                    },
                )

        self.assertEqual(ctx.exception.status_code, 400)
        self.assertIn("sage_memory", str(ctx.exception.detail))
        self.assertIn("bridge.target", str(ctx.exception.detail))

    def test_normalize_bridge_contract_rejects_nested_forbidden_bridge_metadata_key(self) -> None:
        with patch(
            "server_modules.app_bridge_service._resolve_registry_app_item",
            return_value={"id": "study", "status": "installed", "permissions": ["files.read"]},
        ):
            with self.assertRaises(HTTPException) as ctx:
                app_bridge_service.normalize_bridge_contract(
                    app_id="study",
                    bridge_kind="app_to_sage",
                    bridge_type="summary_request",
                    metadata={
                        "events": [
                            {"kind": "ok"},
                            {"nested": [{"private_context": "blocked"}]},
                        ]
                    },
                )

        self.assertEqual(ctx.exception.status_code, 400)
        self.assertIn("private_context", str(ctx.exception.detail))
        self.assertIn("bridge.metadata", str(ctx.exception.detail))

    def test_normalize_app_context_envelope_rejects_nested_forbidden_key(self) -> None:
        with self.assertRaises(HTTPException) as ctx:
            app_bridge_service.normalize_app_context_envelope(
                {
                    "user_selected_inputs": [
                        {"kind": "document", "id": "doc-1"},
                        {"meta": {"raw_memory": {"snippet": "blocked"}}},
                    ]
                }
            )

        self.assertEqual(ctx.exception.status_code, 400)
        self.assertIn("raw_memory", str(ctx.exception.detail))
        self.assertIn("context_envelope.user_selected_inputs", str(ctx.exception.detail))

    def test_normalize_app_context_envelope_rejects_connected_computer_bypass(self) -> None:
        with self.assertRaises(HTTPException) as ctx:
            app_bridge_service.normalize_app_context_envelope(
                {
                    "user_selected_inputs": [
                        {
                            "kind": "request",
                            "connectedComputer": {
                                "gateway_id": "gateway-local-1",
                                "runtime_node_id": "node-1",
                            },
                        },
                    ]
                }
            )

        self.assertEqual(ctx.exception.status_code, 400)
        self.assertIn("connected_computer", str(ctx.exception.detail))

    def test_normalize_bridge_contract_rejects_nested_gateway_identity_bypass(self) -> None:
        with patch(
            "server_modules.app_bridge_service._resolve_registry_app_item",
            return_value={"id": "study", "status": "installed", "permissions": ["files.read"]},
        ):
            with self.assertRaises(HTTPException) as ctx:
                app_bridge_service.normalize_bridge_contract(
                    app_id="study",
                    bridge_kind="app_to_sage",
                    bridge_type="summary_request",
                    metadata={
                        "safe": {
                            "route": "ok",
                            "local_gateway_attachment": {"gateway_id": "gateway-local-1"},
                        }
                    },
                )

        self.assertEqual(ctx.exception.status_code, 400)
        self.assertIn("local_gateway_attachment", str(ctx.exception.detail))

    def test_enforce_app_metadata_contract_rejects_implicit_captain_context(self) -> None:
        with patch(
            "server_modules.app_bridge_service._resolve_registry_app_item",
            return_value={"id": "study", "status": "installed", "permissions": ["files.read"]},
        ):
            with self.assertRaises(HTTPException) as ctx:
                app_bridge_service.enforce_app_metadata_contract(
                    metadata={
                        "app_id": "study",
                        "captain_context": {"raw": True},
                    }
                )

        self.assertEqual(ctx.exception.status_code, 400)
        self.assertIn("captain_context", str(ctx.exception.detail))

    def test_enforce_app_metadata_contract_rejects_captain_profile_metadata(self) -> None:
        with patch(
            "server_modules.app_bridge_service._resolve_registry_app_item",
            return_value={"id": "study", "status": "installed", "permissions": ["files.read"]},
        ):
            with self.assertRaises(HTTPException) as ctx:
                app_bridge_service.enforce_app_metadata_contract(
                    metadata={
                        "app_id": "study",
                        "captain_profile": {"display_name": "Nope"},
                    }
                )

        self.assertEqual(ctx.exception.status_code, 400)
        self.assertIn("captain_profile", str(ctx.exception.detail))

    def test_enforce_app_metadata_contract_rejects_nested_forbidden_bridge_payload_key(self) -> None:
        with patch(
            "server_modules.app_bridge_service._resolve_registry_app_item",
            return_value={"id": "study", "status": "installed", "permissions": ["files.read"]},
        ):
            with self.assertRaises(HTTPException) as ctx:
                app_bridge_service.enforce_app_metadata_contract(
                    metadata={
                        "app_id": "study",
                        "app_bridge": {
                            "kind": "app_to_sage",
                            "type": "summary_request",
                            "target": {
                                "nested": [
                                    {"route": "safe"},
                                    {"payload": {"specialist_memory": "blocked"}},
                                ]
                            },
                        },
                    }
                )

        self.assertEqual(ctx.exception.status_code, 400)
        self.assertIn("specialist_memory", str(ctx.exception.detail))
        self.assertIn("metadata.app_bridge", str(ctx.exception.detail))

    def test_enforce_app_metadata_contract_normalizes_context_and_bridge(self) -> None:
        with patch(
            "server_modules.app_bridge_service._resolve_registry_app_item",
            return_value={"id": "study", "status": "installed", "permissions": ["files.read"]},
        ):
            metadata = app_bridge_service.enforce_app_metadata_contract(
                metadata={
                    "app_id": "study",
                    "app_context": {
                        "user_selected_inputs": {"topic": "biology"},
                        "app_history": [{"id": "h-1"}],
                        "explicit_imports": [{"kind": "summary"}],
                    },
                    "app_bridge": {
                        "kind": "app_to_sage",
                        "type": "summary_request",
                    },
                }
            )

        self.assertEqual(metadata["app_id"], "study")
        self.assertEqual(metadata["app_context_envelope"]["classes"], [
            "app_owned_history",
            "explicit_imports_from_sage",
            "user_selected_inputs",
        ])
        self.assertEqual(metadata["app_bridge"]["bridge_kind"], "app_to_sage")
        self.assertEqual(metadata["app_bridge"]["bridge_type"], "summary_request")
        self.assertTrue(metadata["app_runtime_contract"]["has_explicit_bridge"])


if __name__ == "__main__":
    unittest.main()
