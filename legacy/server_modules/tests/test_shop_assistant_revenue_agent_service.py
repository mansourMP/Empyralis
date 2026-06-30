from __future__ import annotations

import unittest

from server_modules import shop_assistant_revenue_agent_service
from server_modules import studio_proof_agent_seed_service


def _shop_agent() -> dict[str, object]:
    contract = studio_proof_agent_seed_service.get_studio_proof_agent_seed_contract("shop-assistant")
    return {
        "id": "dagent_shop",
        "tenant_id": "tenant-1",
        "owner_workspace_id": "ws-1",
        "metadata": {
            "live_data_connectors": {
                "product_catalog": {
                    "connector_type": "google_sheets",
                    "connector_id": "gsheet-products-1",
                    "workspace_id": "ws-1",
                    "deployed_agent_id": "dagent_shop",
                    "rows": contract["revenue_proof"]["demo_catalog_rows"],
                }
            }
        },
    }


class ShopAssistantRevenueAgentServiceTests(unittest.TestCase):
    def test_backend_package_contains_business_workflow_assets(self) -> None:
        package = shop_assistant_revenue_agent_service.build_shop_assistant_backend_package()

        self.assertEqual(package["vertical"], "shop_assistant")
        self.assertGreaterEqual(len(package["demo_catalog_rows"]), 3)
        self.assertGreaterEqual(len(package["faq_seed"]), 3)
        self.assertEqual(package["approval_gates"]["discount"], "owner_required")
        self.assertEqual(package["approval_gates"]["refund"], "owner_required")
        self.assertEqual(package["approval_gates"]["payment_link"], "owner_required")
        self.assertEqual(package["approval_gates"]["mass_message"], "owner_required")
        self.assertIn("questions_handled", package["roi_metric_contract"])

    def test_catalog_question_answers_from_live_catalog_and_records_roi(self) -> None:
        result = shop_assistant_revenue_agent_service.evaluate_shop_assistant_customer_question(
            _shop_agent(),
            workspace_id="ws-1",
            connector_id="gsheet-products-1",
            customer_message="Do you have Nike Air Max 90 in black size 42?",
        )

        self.assertEqual(result["status"], "answered")
        self.assertEqual(result["intent"], "catalog_lookup")
        self.assertIn("8 in stock", result["answer"])
        self.assertEqual(result["catalog"]["matches"][0]["sku"], "SHOE-AIRMAX-42-BLK")
        event_names = {item["event"] for item in result["activity_events"]}
        self.assertIn("studio.proof.shop_assistant.inventory_hit", event_names)
        self.assertIn("studio.proof.shop_assistant.conversation_handled", event_names)
        self.assertEqual(result["roi_metrics"]["questions_handled"], 1)
        self.assertEqual(result["roi_metrics"]["inventory_hits"], 1)

    def test_activity_billing_evidence_separates_visible_and_admin_fields(self) -> None:
        result = shop_assistant_revenue_agent_service.evaluate_shop_assistant_customer_question(
            _shop_agent(),
            workspace_id="ws-1",
            connector_id="gsheet-products-1",
            customer_message="Do you have Nike Air Max 90 in black size 42?",
        )

        evidence = shop_assistant_revenue_agent_service.build_shop_assistant_activity_billing_evidence(
            result,
            public_tier="pro",
            total_tokens=1200,
            provider_metadata={
                "effective_provider": "deepseek",
                "effective_model": "deepseek-v4-pro",
                "fallback_provider": "deepseek",
                "fallback_model": "deepseek-v4-flash",
            },
        )

        visible = evidence["visible_activity"]
        admin = evidence["admin_audit"]
        self.assertEqual(visible["title"], "Shop Assistant answered product question.")
        self.assertEqual(visible["tier_label"], "Pro")
        self.assertNotIn("raw_provider", visible)
        self.assertNotIn("raw_model", visible)
        self.assertEqual(admin["raw_provider"], "deepseek")
        self.assertEqual(admin["raw_model"], "deepseek-v4-pro")
        self.assertTrue(any(item["credit_item_type"] == "ai_pro_tokens" for item in evidence["credit_line_items"]))
        self.assertTrue(any(item["credit_item_type"] == "connector_read" for item in evidence["credit_line_items"]))

    def test_faq_question_uses_seed_policy_without_approval(self) -> None:
        result = shop_assistant_revenue_agent_service.evaluate_shop_assistant_customer_question(
            _shop_agent(),
            workspace_id="ws-1",
            customer_message="Can I return worn shoes?",
        )

        self.assertEqual(result["status"], "answered")
        self.assertEqual(result["intent"], "faq")
        self.assertIn("unworn items within 14 days", result["answer"])
        self.assertFalse(result["approval"]["required"])
        self.assertEqual(result["roi_metrics"]["questions_handled"], 1)

    def test_discount_refund_payment_and_mass_message_require_owner_approval(self) -> None:
        cases = [
            ("Can you give me 25% discount if I buy two pairs now?", "discount"),
            ("I want a refund for this order.", "refund"),
            ("Send me a payment link and I'll pay now.", "payment_link"),
            ("Broadcast this promo to all customers.", "mass_message"),
        ]

        for customer_message, expected_gate in cases:
            with self.subTest(expected_gate=expected_gate):
                result = shop_assistant_revenue_agent_service.evaluate_shop_assistant_customer_question(
                    _shop_agent(),
                    workspace_id="ws-1",
                    customer_message=customer_message,
                )

                self.assertEqual(result["status"], "approval_required")
                self.assertEqual(result["approval"]["gate"], expected_gate)
                self.assertEqual(result["approval"]["risk_class"], "ORANGE")
                self.assertTrue(result["approval"]["required"])
                self.assertTrue(any(item["approval_required"] for item in result["activity_events"]))
                self.assertEqual(result["roi_metrics"]["escalations"], 1)

    def test_order_status_request_records_connector_needed_event(self) -> None:
        result = shop_assistant_revenue_agent_service.evaluate_shop_assistant_customer_question(
            _shop_agent(),
            workspace_id="ws-1",
            customer_message="Order #A-2041 still shows pending, can you check status?",
        )

        self.assertEqual(result["status"], "needs_connector")
        self.assertEqual(result["intent"], "order_status")
        self.assertIn("order source is connected", result["answer"])
        self.assertEqual(
            result["activity_events"][0]["event"],
            "studio.proof.shop_assistant.order_status_requested",
        )

    def test_cross_workspace_catalog_source_is_blocked(self) -> None:
        agent = _shop_agent()
        agent["metadata"]["live_data_connectors"]["product_catalog"]["workspace_id"] = "ws-2"

        with self.assertRaises(PermissionError):
            shop_assistant_revenue_agent_service.evaluate_shop_assistant_customer_question(
                agent,
                workspace_id="ws-1",
                customer_message="Do you have Nike Air Max 90?",
            )


if __name__ == "__main__":
    unittest.main()
