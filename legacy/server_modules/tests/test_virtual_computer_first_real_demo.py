import asyncio
import unittest

from server_modules import runtime_policy
from server_modules.virtual_computer_runtime import InMemoryVirtualComputerRuntime


class VirtualComputerFirstRealDemoTests(unittest.TestCase):
    def setUp(self) -> None:
        runtime_policy._init()

    def test_shop_assistant_supplier_portal_end_to_end_demo(self):
        route = runtime_policy.decide_execution_target(
            {
                "template_slug": "shop-assistant",
                "supplier_portal": True,
                "order_status_workflow": True,
            }
        )
        self.assertEqual(route.get("runtime_choice_selected"), runtime_policy.RUNTIME_CHOICE_VIRTUAL_BROWSER)
        self.assertEqual(route.get("selected"), runtime_policy.EXECUTION_TARGET_CLOUD)

        runtime = InMemoryVirtualComputerRuntime()
        created = asyncio.run(
            runtime.create_session(
                {
                    "runtime_choice": str(route.get("runtime_choice_selected") or "virtual_browser"),
                    "enterprise_controls": {
                        "per_team_approval_roles": ["owner"],
                    },
                    "isolation_profile": {
                        "file_transfer_enabled": True,
                    },
                }
            )
        )
        session_id = created.get("session_id")

        asyncio.run(
            runtime.execute_action(
                {
                    "session_id": session_id,
                    "action": "open_url",
                    "action_args": {"url": "https://supplier.example.com/portal"},
                }
            )
        )

        asyncio.run(
            runtime.execute_action(
                {
                    "session_id": session_id,
                    "action": "type",
                    "action_args": {"text": "SKU-12345", "target_description": "supplier portal search field"},
                }
            )
        )
        asyncio.run(
            runtime.execute_action(
                {
                    "session_id": session_id,
                    "action": "click",
                    "action_args": {"x": 420, "y": 180, "target_description": "search supplier SKU"},
                }
            )
        )

        screenshot_result = asyncio.run(runtime.stream_screenshot({"session_id": session_id}))
        artifact_id = (screenshot_result.get("artifact") or {}).get("artifact_id")
        self.assertTrue(artifact_id)
        self.assertEqual((screenshot_result.get("artifact") or {}).get("type"), "screenshot")

        asyncio.run(
            runtime.execute_action(
                {
                    "session_id": session_id,
                    "action": "type",
                    "action_args": {
                        "text": "Draft: SKU-12345 is in stock (42) at $19.99 each.",
                        "target_description": "draft customer answer field",
                    },
                }
            )
        )

        with self.assertRaises(RuntimeError):
            asyncio.run(
                runtime.execute_action(
                    {
                        "session_id": session_id,
                        "approval_id": "appr_send_1",
                        "approval_role": "agent",
                        "action": "type",
                        "action_args": {
                            "text": "Send customer quote with 15% discount.",
                            "target_description": "send message with discount quote",
                        },
                    }
                )
            )

        approved_send = asyncio.run(
            runtime.execute_action(
                {
                    "session_id": session_id,
                    "approval_id": "appr_send_2",
                    "approval_role": "owner",
                    "action": "type",
                    "action_args": {
                        "text": "Send customer quote with 15% discount.",
                        "target_description": "send message with discount quote",
                    },
                }
            )
        )
        policy = (approved_send.get("action_result") or {}).get("policy") or {}
        self.assertEqual(policy.get("risk_class"), "ORANGE")
        self.assertTrue(policy.get("approval_granted"))

        collected = asyncio.run(
            runtime.collect_artifact(
                {
                    "session_id": session_id,
                    "artifact_id": artifact_id,
                    "export_destination": "workspace",
                    "approval_id": "appr_artifact_workspace_1",
                }
            )
        )
        self.assertEqual((collected.get("artifact") or {}).get("artifact_id"), artifact_id)

        exported = asyncio.run(runtime.export_audit_report({"session_id": session_id}))
        report = exported.get("audit_report") or {}
        self.assertTrue(report.get("chain_valid"))
        event_types = [str((item or {}).get("event_type") or "") for item in (report.get("events") or [])]
        self.assertIn("session_created", event_types)
        self.assertIn("model_action_request", event_types)
        self.assertIn("approval_decision", event_types)
        self.assertIn("executed_action", event_types)
        self.assertIn("screenshot_hash", event_types)
        self.assertIn("artifact_hash", event_types)

        session_payload = approved_send.get("session") or {}
        timeline = session_payload.get("ui_action_timeline") or []
        self.assertTrue(any((item or {}).get("event_type") == "action" for item in timeline))
        self.assertTrue(any((item or {}).get("event_type") == "stream" for item in timeline))


if __name__ == "__main__":
    unittest.main()
