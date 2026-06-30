from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from server_modules import discovery_feed_service, mini_apps_service, workspace_context


class DiscoveryFeedServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory(prefix="discovery-feed-service-")
        self._workspace_root = Path(self._tmpdir.name) / "workspace"
        self._workspace_patch = patch.object(workspace_context, "_WORKSPACE_DIR", self._workspace_root)
        self._workspace_patch.start()
        self._rust_gate_patch = patch.object(
            mini_apps_service,
            "_enforce_mini_apps_state_decision",
            return_value={"decision": "allow"},
        )
        self._rust_gate_patch.start()

    def tearDown(self) -> None:
        self._rust_gate_patch.stop()
        self._workspace_patch.stop()
        self._tmpdir.cleanup()

    def test_feed_includes_public_apps_but_not_private_apps(self) -> None:
        mini_apps_service.publish_app(
            "source-ws",
            name="Public Budget",
            description="Track daily spending.",
            source_url="https://apps.example.com/budget",
            visibility="public",
            creator_label="@sarah",
        )
        mini_apps_service.approve_app_publication("source-ws", "public_budget")
        mini_apps_service.publish_app(
            "source-ws",
            name="Private Notes",
            description="Private notes.",
            source_url="https://apps.example.com/notes",
            visibility="private",
            creator_label="@sarah",
        )

        feed = discovery_feed_service.list_discovery_feed("target-ws", feed_filter="apps")

        self.assertEqual(feed["count"], 1)
        item = feed["items"][0]
        self.assertEqual(item["type"], "public_app")
        self.assertEqual(item["title"], "Public Budget")
        self.assertEqual(item["action"]["label"], "Clone Blueprint")
        self.assertNotIn("public_app_id", item)
        self.assertNotIn("package_id", item)
        self.assertNotIn("runtime_truth", item)

    def test_adopt_public_app_clones_private_copy(self) -> None:
        mini_apps_service.publish_app(
            "source-ws",
            name="Public Budget",
            description="Track daily spending.",
            source_url="https://apps.example.com/budget",
            visibility="public",
            creator_label="@sarah",
        )
        mini_apps_service.approve_app_publication("source-ws", "public_budget")
        feed_item = discovery_feed_service.list_discovery_feed("target-ws", feed_filter="apps")["items"][0]

        adopted = discovery_feed_service.adopt_discovery_item(
            "target-ws",
            feed_item["id"],
            actor_user_id="user-2",
            actor_label="@mike",
        )

        self.assertTrue(adopted["adopted"])
        self.assertEqual(adopted["target_kind"], "app")
        self.assertEqual(adopted["open_url"], "/w/target-ws/applications/public_budget")
        contract = mini_apps_service.get_mini_app_contract("target-ws", "public_budget")
        self.assertEqual(contract["visibility"], "workspace_private")
        self.assertEqual(contract["publisher_name"], "@mike")

    def test_agent_templates_copy_to_studio_without_raw_catalog_fields(self) -> None:
        feed = discovery_feed_service.list_discovery_feed("ws-1", feed_filter="agents")

        self.assertGreaterEqual(feed["count"], 3)
        item = next(feed_item for feed_item in feed["items"] if feed_item["title"] == "Shop Assistant")
        self.assertEqual(item["type"], "agent_template")
        self.assertEqual(item["action"]["label"], "Copy to Studio")
        self.assertNotIn("package_id", item)
        self.assertNotIn("review_state", item)
        self.assertNotIn("verification_status", item)

        adopted = discovery_feed_service.adopt_discovery_item("ws-1", item["id"], actor_user_id="user-1")

        self.assertTrue(adopted["adopted"])
        self.assertEqual(adopted["target_kind"], "studio")
        self.assertEqual(adopted["open_url"], "/w/ws-1/studio?proof_agent=shop-assistant")

    def test_tools_filter_excludes_agent_templates(self) -> None:
        feed = discovery_feed_service.list_discovery_feed("ws-1", feed_filter="tools")

        self.assertGreater(feed["count"], 0)
        self.assertNotIn("agent_template", {item["type"] for item in feed["items"]})
