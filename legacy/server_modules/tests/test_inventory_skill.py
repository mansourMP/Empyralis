import importlib
import unittest
from unittest.mock import AsyncMock, patch


def _inventory_skill_module():
    return importlib.import_module("server_modules.inventory_skill")


class InventorySkillTests(unittest.IsolatedAsyncioTestCase):
    async def test_execute_inventory_skill_returns_live_artifact_for_matching_inventory(self):
        inventory_skill = _inventory_skill_module()
        with patch.object(
            inventory_skill,
            "search_workspace_inventory",
            new=AsyncMock(return_value=[
                {
                    "id": "inventory-1",
                    "tenant_id": "tenant-1",
                    "workspace_id": "workspace-1",
                    "sku": "TES-WIPER-M3-2022",
                    "product_name": "Tesla Model 3 Aero Wiper Kit",
                    "manufacturer": "Empyralis Shadow Catalog",
                    "make": "Tesla",
                    "model": "Model 3",
                    "category": "wipers",
                    "year_start": 2021,
                    "year_end": 2023,
                    "quantity_available": 3,
                    "unit_price": 24.99,
                    "currency": "USD",
                    "metadata": {"demo_seed": True},
                }
            ]),
        ):
            result = await inventory_skill.execute_inventory_skill(
                tenant_id="tenant-1",
                workspace_id="workspace-1",
                goal="Do you have 2022 Tesla Model 3 wipers?",
                agent_label="Parts Pro",
                hard_context="Use only inventory rows.",
                operational_policy="Never invent stock.",
                seed_demo_if_empty=True,
            )

        self.assertEqual(result["status"], "ok")
        self.assertIn("Tesla Model 3 Aero Wiper Kit", result["reply"])
        self.assertEqual(result["artifact"]["kind"], "inventory-live-data")
        self.assertEqual(result["items"][0]["sku"], "TES-WIPER-M3-2022")

    async def test_execute_inventory_skill_returns_graceful_unavailable_message_on_failure(self):
        inventory_skill = _inventory_skill_module()
        with patch.object(
            inventory_skill,
            "search_workspace_inventory",
            new=AsyncMock(side_effect=TimeoutError("db timeout")),
        ):
            result = await inventory_skill.execute_inventory_skill(
                tenant_id="tenant-1",
                workspace_id="workspace-1",
                goal="Do you have 2022 Tesla Model 3 wipers?",
                agent_label="Parts Pro",
                hard_context="Use only inventory rows.",
                operational_policy="Never invent stock.",
            )

        self.assertEqual(result["status"], "unavailable")
        self.assertEqual(result["reply"], "My inventory system is currently updating, one moment.")
        self.assertIsNone(result["artifact"])


if __name__ == "__main__":
    unittest.main()
