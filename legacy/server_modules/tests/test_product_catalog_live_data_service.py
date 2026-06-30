from __future__ import annotations

import time
import unittest
from unittest.mock import patch

from server_modules import product_catalog_live_data_service
from server_modules import studio_proof_agent_seed_service


def _deployed_agent_with_catalog(source: dict[str, object]) -> dict[str, object]:
    return {
        "id": "dagent_shop",
        "tenant_id": "tenant-1",
        "owner_workspace_id": "ws-1",
        "metadata": {
            "live_data_connectors": {
                "product_catalog": {
                    "source_id": "product_catalog",
                    "deployed_agent_id": "dagent_shop",
                    "workspace_id": "ws-1",
                    **source,
                }
            }
        },
    }


class ProductCatalogLiveDataServiceTests(unittest.TestCase):
    def test_csv_catalog_lookup_answers_availability_from_live_rows(self) -> None:
        agent = _deployed_agent_with_catalog(
            {
                "connector_type": "csv",
                "csv_text": "\n".join(
                    [
                        "sku,product_name,category,price,currency,quantity_available,variant,brand,shipping_eta_days,aliases",
                        "SHOE-AIRMAX-42-BLK,Nike Air Max 90,shoes,129,USD,8,black / size 42,Nike,2,airmax black 42",
                    ]
                ),
            }
        )

        result = product_catalog_live_data_service.lookup_product_catalog(
            agent,
            workspace_id="ws-1",
            query="Do you have Nike Air Max black size 42?",
        )

        self.assertTrue(result["read_only"])
        self.assertEqual(result["matches"][0]["sku"], "SHOE-AIRMAX-42-BLK")
        self.assertIn("8 in stock", result["answer"])
        self.assertEqual(result["source"]["connector_type"], "csv")

    def test_google_sheets_shaped_rows_lookup_without_inventory_hallucination(self) -> None:
        agent = _deployed_agent_with_catalog(
            {
                "connector_type": "google_sheets",
                "connector_id": "gsheet-products-1",
                "rows": [
                    {
                        "sku": "SHOE-ULTRABOOST-41-WHT",
                        "product_name": "Adidas Ultraboost 23",
                        "category": "shoes",
                        "price": 149.0,
                        "currency": "USD",
                        "quantity_available": 5,
                        "variant": "white / size 41",
                        "brand": "Adidas",
                    }
                ],
            }
        )

        result = product_catalog_live_data_service.lookup_product_catalog(
            agent,
            workspace_id="ws-1",
            connector_id="gsheet-products-1",
            query="Do you have a red hiking backpack?",
        )

        self.assertEqual(result["matches"], [])
        self.assertIn("No matching catalog row", result["answer"])
        self.assertEqual(result["source"]["connector_type"], "google_sheets")

    def test_catalog_source_is_scoped_to_agent_and_workspace(self) -> None:
        wrong_agent_source = {
            "connector_type": "google_sheets",
            "deployed_agent_id": "dagent_other",
            "workspace_id": "ws-1",
            "rows": [
                {
                    "sku": "SKU-1",
                    "product_name": "Private Product",
                    "category": "private",
                    "price": 1,
                    "currency": "USD",
                    "quantity_available": 1,
                }
            ],
        }
        agent = _deployed_agent_with_catalog(wrong_agent_source)

        with self.assertRaises(PermissionError):
            product_catalog_live_data_service.lookup_product_catalog(
                agent,
                workspace_id="ws-1",
                query="Private Product",
            )

        wrong_workspace_agent = _deployed_agent_with_catalog(
            {
                **wrong_agent_source,
                "deployed_agent_id": "dagent_shop",
                "workspace_id": "ws-2",
            }
        )
        with self.assertRaises(PermissionError):
            product_catalog_live_data_service.lookup_product_catalog(
                wrong_workspace_agent,
                workspace_id="ws-1",
                query="Private Product",
            )

    def test_read_only_action_blocks_writes(self) -> None:
        agent = _deployed_agent_with_catalog(
            {
                "connector_type": "google_sheets",
                "rows": [
                    {
                        "sku": "SKU-1",
                        "product_name": "Private Product",
                        "category": "private",
                        "price": 1,
                        "currency": "USD",
                        "quantity_available": 1,
                    }
                ],
            }
        )

        with self.assertRaises(PermissionError):
            product_catalog_live_data_service.execute_read_only_catalog_action(
                agent,
                workspace_id="ws-1",
                action_id="catalog.update",
                arguments={"query": "Private Product"},
            )

    def test_golden_shop_assistant_catalog_question_uses_demo_catalog(self) -> None:
        contract = studio_proof_agent_seed_service.get_studio_proof_agent_seed_contract("shop-assistant")
        self.assertIsNotNone(contract)
        revenue_proof = contract["revenue_proof"]
        agent = _deployed_agent_with_catalog(
            {
                "connector_type": "google_sheets",
                "rows": revenue_proof["demo_catalog_rows"],
            }
        )

        result = product_catalog_live_data_service.lookup_product_catalog(
            agent,
            workspace_id="ws-1",
            query="Do you have Nike Air Max 90 in black size 42?",
        )

        self.assertEqual(result["matches"][0]["sku"], "SHOE-AIRMAX-42-BLK")
        self.assertIn("8 in stock", result["answer"])
        self.assertNotIn("red hiking backpack", result["answer"].lower())

    def test_spreadsheet_source_fetches_rows_and_caches(self) -> None:
        agent = _deployed_agent_with_catalog(
            {
                "connector_type": "google_sheets",
                "connector_id": "cred-google-1",
                "spreadsheet_id": "sheet-123",
                "range": "Products!A1:G10",
                "workspace_id": "ws-1",
                "deployed_agent_id": "dagent_shop",
            }
        )

        def fake_read_spreadsheet(credentials, spreadsheet_id, range="Sheet1"):
            self.assertEqual(spreadsheet_id, "sheet-123")
            self.assertEqual(range, "Products!A1:G10")
            return [
                {"sku": "SKU-1", "product_name": "Widget", "category": "gadgets", "price": "19.99", "currency": "USD", "quantity_available": "10"},
            ]

        def fake_resolve_credentials(credential_id, workspace_id):
            self.assertEqual(credential_id, "cred-google-1")
            self.assertEqual(workspace_id, "ws-1")
            return {"access_token": "token-1"}

        with patch.object(
            product_catalog_live_data_service.google_drive_api,
            "google_workspace_read_spreadsheet",
            fake_read_spreadsheet,
        ), patch.object(
            product_catalog_live_data_service,
            "_resolve_connector_credentials",
            fake_resolve_credentials,
        ):
            result = product_catalog_live_data_service.resolve_deployed_agent_product_catalog(
                agent,
                workspace_id="ws-1",
            )

        self.assertEqual(len(result["rows"]), 1)
        self.assertEqual(result["rows"][0]["sku"], "SKU-1")
        self.assertEqual(result["source"]["connector_type"], "google_sheets")
        self.assertIn("last_synced_at", result["source"])
        self.assertTrue(result["source"]["read_only"])

    def test_spreadsheet_credentials_are_resolved_with_workspace_scope(self) -> None:
        observed: dict[str, object] = {}

        def fake_resolve(load_vault_fn, decrypt_fn, credential_id, workspace_id=None):
            observed["credential_id"] = credential_id
            observed["workspace_id"] = workspace_id
            return {"access_token": "token-scoped"}

        with patch.object(
            product_catalog_live_data_service.vault_helpers,
            "resolve_vault_credential",
            fake_resolve,
        ):
            credentials = product_catalog_live_data_service._resolve_connector_credentials(
                "cred-google-1",
                "ws-1",
            )

        self.assertEqual(credentials["access_token"], "token-scoped")
        self.assertEqual(observed["credential_id"], "cred-google-1")
        self.assertEqual(observed["workspace_id"], "ws-1")

    def test_spreadsheet_cache_hit_avoids_second_fetch(self) -> None:
        agent = _deployed_agent_with_catalog(
            {
                "connector_type": "google_sheets",
                "connector_id": "cred-google-1",
                "spreadsheet_id": "sheet-cache-test",
                "workspace_id": "ws-1",
                "deployed_agent_id": "dagent_shop",
            }
        )

        call_count = 0

        def fake_read_spreadsheet(credentials, spreadsheet_id, range="Sheet1"):
            nonlocal call_count
            call_count += 1
            return [
                {"sku": "SKU-CACHE", "product_name": "Cached Item", "category": "test", "price": "5", "currency": "USD", "quantity_available": "1"},
            ]

        def fake_resolve_credentials(credential_id, workspace_id):
            self.assertEqual(workspace_id, "ws-1")
            return {"access_token": "token-1"}

        with patch.object(
            product_catalog_live_data_service.google_drive_api,
            "google_workspace_read_spreadsheet",
            fake_read_spreadsheet,
        ), patch.object(
            product_catalog_live_data_service,
            "_resolve_connector_credentials",
            fake_resolve_credentials,
        ):
            result1 = product_catalog_live_data_service.resolve_deployed_agent_product_catalog(
                agent,
                workspace_id="ws-1",
            )
            result2 = product_catalog_live_data_service.resolve_deployed_agent_product_catalog(
                agent,
                workspace_id="ws-1",
            )

        self.assertEqual(call_count, 1)
        self.assertEqual(result1["rows"][0]["sku"], "SKU-CACHE")
        self.assertEqual(result2["rows"][0]["sku"], "SKU-CACHE")
        self.assertIn("last_synced_at", result2["source"])

    def test_spreadsheet_cache_expires_and_refetches(self) -> None:
        agent = _deployed_agent_with_catalog(
            {
                "connector_type": "google_sheets",
                "connector_id": "cred-google-1",
                "spreadsheet_id": "sheet-expire-test",
                "workspace_id": "ws-1",
                "deployed_agent_id": "dagent_shop",
            }
        )

        call_count = 0

        def fake_read_spreadsheet(credentials, spreadsheet_id, range="Sheet1"):
            nonlocal call_count
            call_count += 1
            return [
                {"sku": f"SKU-{call_count}", "product_name": "Item", "category": "test", "price": "5", "currency": "USD", "quantity_available": "1"},
            ]

        def fake_resolve_credentials(credential_id, workspace_id):
            self.assertEqual(workspace_id, "ws-1")
            return {"access_token": "token-1"}

        with patch.object(
            product_catalog_live_data_service.google_drive_api,
            "google_workspace_read_spreadsheet",
            fake_read_spreadsheet,
        ), patch.object(
            product_catalog_live_data_service,
            "_resolve_connector_credentials",
            fake_resolve_credentials,
        ):
            result1 = product_catalog_live_data_service.resolve_deployed_agent_product_catalog(
                agent,
                workspace_id="ws-1",
            )
            # Force cache expiration
            cache_key = product_catalog_live_data_service._catalog_cache_key("ws-1", "dagent_shop", "cred-google-1", "sheet-expire-test")
            product_catalog_live_data_service._CATALOG_CACHE[cache_key]["expires_at"] = time.time() - 1
            result2 = product_catalog_live_data_service.resolve_deployed_agent_product_catalog(
                agent,
                workspace_id="ws-1",
            )

        self.assertEqual(call_count, 2)
        self.assertEqual(result1["rows"][0]["sku"], "SKU-1")
        self.assertEqual(result2["rows"][0]["sku"], "SKU-2")

    def test_spreadsheet_missing_credentials_raises_value_error(self) -> None:
        agent = _deployed_agent_with_catalog(
            {
                "connector_type": "google_sheets",
                "connector_id": "cred-missing",
                "spreadsheet_id": "sheet-xyz",
                "workspace_id": "ws-1",
                "deployed_agent_id": "dagent_shop",
            }
        )

        with patch.object(
            product_catalog_live_data_service,
            "_resolve_connector_credentials",
            return_value=None,
        ):
            with self.assertRaises(ValueError) as context:
                product_catalog_live_data_service.resolve_deployed_agent_product_catalog(
                    agent,
                    workspace_id="ws-1",
                )
            self.assertIn("credentials are not available", str(context.exception))


if __name__ == "__main__":
    unittest.main()
