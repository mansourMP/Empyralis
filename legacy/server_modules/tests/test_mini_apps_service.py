from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from server_modules import mini_apps_service, workspace_context


class MiniAppsServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory(prefix="mini-apps-service-")
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

    def test_share_token_secret_requires_exact_share_secret(self) -> None:
        with patch.dict("os.environ", {}, clear=True):
            with self.assertRaises(mini_apps_service.ConfigurationError):
                mini_apps_service.issue_unlisted_share_token(workspace_id="ws-1", app_id="budget_tracker")

    def test_share_token_secret_rejects_legacy_launch_secret_alias(self) -> None:
        with patch.dict("os.environ", {"EMPYRALIS_MINI_APP_LAUNCH_SECRET": "legacy-secret"}, clear=True):
            with self.assertRaises(mini_apps_service.ConfigurationError):
                mini_apps_service.issue_unlisted_share_token(workspace_id="ws-1", app_id="budget_tracker")

    def test_share_token_secret_accepts_required_share_secret(self) -> None:
        with patch.dict("os.environ", {"EMPYRALIS_MINI_APP_SHARE_SECRET": "s" * 40}, clear=True):
            issued = mini_apps_service.issue_unlisted_share_token(workspace_id="ws-1", app_id="budget_tracker")
        self.assertIn("token", issued)

    def test_upsert_and_list_contracts_preserve_shared_contract_shape(self) -> None:
        mini_apps_service.upsert_mini_app_contract(
            "ws-1",
            "calorie_tracking",
            label="Calorie Tracking",
            current_state={"today_calories": 1840, "goal": 2200},
            recent_events=[
                {"id": "meal-1", "kind": "meal", "summary": "Chicken bowl", "created_at": "2026-04-17T08:00:00Z"},
            ],
            daily_summary={"status": "under goal"},
            weekly_summary={"trend": "stable"},
            long_term_facts=["Cutting phase", {"text": "Protein target is 180g", "kind": "goal"}],
            records=[
                {"id": "meal-1", "kind": "meal", "summary": "Chicken bowl", "tags": ["lunch"], "created_at": "2026-04-17T08:00:00Z"},
                {"id": "meal-2", "kind": "meal", "summary": "Protein yogurt", "tags": ["breakfast"], "created_at": "2026-04-16T07:00:00Z"},
            ],
        )

        listing = mini_apps_service.list_mini_app_contracts("ws-1")

        self.assertEqual(listing["count"], 1)
        contract = listing["items"][0]
        self.assertEqual(contract["app_id"], "calorie_tracking")
        self.assertEqual(contract["label"], "Calorie Tracking")
        self.assertEqual(contract["current_state"]["today_calories"], 1840)
        self.assertEqual(contract["retrieve_records"]["default_limit"], mini_apps_service.DEFAULT_RETRIEVE_LIMIT)
        self.assertIn("since", contract["retrieve_records"]["supported_filters"])
        self.assertEqual(contract["records_count"], 2)
        self.assertEqual(contract["trust_tier"], "first_party")
        self.assertFalse(contract["background_ai_allowed"])
        self.assertEqual(contract["runtime_access"], "none")

    def test_workspace_private_user_apps_default_to_user_private_trust(self) -> None:
        contract = mini_apps_service.upsert_mini_app_contract(
            "ws-1",
            "travel_partner",
            label="Travel Partner",
            delivery_mode="hosted",
            hosted_url="https://miniapps.example.com/travel",
            allowed_origins=["https://miniapps.example.com"],
        )

        self.assertEqual(contract["trust_tier"], "user_private")
        self.assertFalse(contract["background_ai_allowed"])
        self.assertEqual(contract["runtime_access"], "none")

    def test_publish_app_returns_clean_app_card_with_safe_defaults(self) -> None:
        card = mini_apps_service.publish_app(
            "ws-1",
            name="Budget Tracker",
            description="Track expenses.",
            source_url="https://apps.example.com/budget",
            visibility="private",
            creator_label="@sarah",
            creator_id="user-1",
        )

        self.assertEqual(card["app_id"], "budget_tracker")
        self.assertEqual(card["name"], "Budget Tracker")
        self.assertEqual(card["creator"]["byline"], "by @sarah")
        self.assertEqual(card["visibility"], "private")
        self.assertEqual(card["source"]["kind"], "website")
        self.assertNotIn("runtime_type", card)
        self.assertNotIn("trust_tier", card)
        self.assertNotIn("bridge_contracts", card)

        contract = mini_apps_service.get_mini_app_contract("ws-1", "budget_tracker")
        self.assertEqual(contract["runtime_type"], "private")
        self.assertEqual(contract["delivery_mode"], "hosted")
        self.assertEqual(contract["visibility"], "workspace_private")
        self.assertEqual(contract["hosted_app"]["allowed_origins"], ["https://apps.example.com"])
        self.assertIn("app.summary.read", contract["permissions"])
        self.assertIn("app.records.write", contract["permissions"])
        self.assertNotIn("app.ai.invoke", contract["permissions"])
        self.assertEqual(contract["bridge_contracts"], {})
        self.assertEqual(contract["ai_invoke_policy"]["monthly_credit_cap"], mini_apps_service.DEFAULT_AI_MONTHLY_CREDIT_CAP)
        self.assertEqual(contract["ai_invoke_policy"]["per_invocation_credit_cap"], mini_apps_service.DEFAULT_AI_INVOCATION_CREDIT_CAP)

    def test_update_app_settings_maps_plain_toggles_to_contract(self) -> None:
        mini_apps_service.publish_app(
            "ws-1",
            name="Budget Tracker",
            description="Track expenses.",
            source_url="https://apps.example.com/budget",
        )

        card = mini_apps_service.update_app_settings(
            "ws-1",
            "budget_tracker",
            ai_enabled=True,
            records_enabled=False,
            sage_enabled=True,
            monthly_credit_limit=250,
            per_invocation_credit_limit=25,
            visibility="public",
        )

        self.assertTrue(card["settings"]["ai_enabled"])
        self.assertFalse(card["settings"]["records_enabled"])
        self.assertTrue(card["settings"]["sage_enabled"])
        self.assertEqual(card["settings"]["monthly_credit_limit"], 250)
        self.assertEqual(card["visibility"], "private")
        self.assertEqual(card["blueprint"]["status"], "publish_requested")

        contract = mini_apps_service.get_mini_app_contract("ws-1", "budget_tracker")
        self.assertEqual(contract["visibility"], "workspace_private")
        self.assertEqual(contract["blueprint_status"], "publish_requested")
        self.assertIn("app.ai.invoke", contract["permissions"])
        self.assertNotIn("app.records.write", contract["permissions"])
        self.assertIn("app.bridge.sage.request", contract["permissions"])
        self.assertEqual(contract["bridge_contracts"]["app_to_sage"], ["summary_request"])
        self.assertEqual(contract["ai_invoke_policy"]["consent_status"], "granted")

    def test_public_app_index_follows_visibility(self) -> None:
        private_card = mini_apps_service.publish_app(
            "ws-1",
            name="Budget Tracker",
            description="Track expenses.",
            source_url="https://apps.example.com/budget",
            visibility="private",
            creator_label="@sarah",
        )

        self.assertIsNone(private_card["public_app_id"])
        self.assertEqual(mini_apps_service._safe_read_public_apps_index()["items"], {})

        requested_card = mini_apps_service.update_app_settings("ws-1", "budget_tracker", visibility="public")
        self.assertIsNone(requested_card["public_app_id"])
        self.assertEqual(requested_card["blueprint"]["status"], "publish_requested")
        self.assertEqual(mini_apps_service._safe_read_public_apps_index()["items"], {})

        public_card = mini_apps_service.approve_app_publication("ws-1", "budget_tracker")
        public_app_id = public_card["public_app_id"]
        self.assertTrue(public_app_id)
        self.assertIn(public_app_id, mini_apps_service._safe_read_public_apps_index()["items"])

        mini_apps_service.update_app_settings("ws-1", "budget_tracker", visibility="private")
        self.assertNotIn(public_app_id, mini_apps_service._safe_read_public_apps_index()["items"])

    def test_clone_public_app_creates_private_copy_with_attribution(self) -> None:
        mini_apps_service.publish_app(
            "source-ws",
            name="Budget Tracker",
            description="Track expenses.",
            source_url="https://apps.example.com/budget",
            visibility="public",
            creator_label="@sarah",
        )
        source_card = mini_apps_service.approve_app_publication("source-ws", "budget_tracker")

        cloned = mini_apps_service.clone_public_app(
            "target-ws",
            public_app_id=source_card["public_app_id"],
            creator_label="@mike",
            creator_id="user-2",
        )

        self.assertEqual(cloned["visibility"], "private")
        self.assertEqual(cloned["creator"]["byline"], "by @mike")
        self.assertFalse(cloned["clone_available"])
        contract = mini_apps_service.get_mini_app_contract("target-ws", cloned["app_id"])
        self.assertEqual(contract["visibility"], "workspace_private")
        self.assertEqual(contract["hosted_url"], "https://apps.example.com/budget")
        raw_state = mini_apps_service._safe_read_state("target-ws")
        raw_entry = raw_state["apps"][cloned["app_id"]]
        self.assertEqual(raw_entry["cloned_from_public_app_id"], source_card["public_app_id"])
        self.assertEqual(raw_entry["cloned_from_workspace_id"], "source-ws")

        target_feed = mini_apps_service.list_apps("target-ws")
        self.assertEqual([item["name"] for item in target_feed["items"]], ["Budget Tracker"])

    def test_clone_private_or_missing_public_app_is_rejected(self) -> None:
        mini_apps_service.publish_app(
            "source-ws",
            name="Private Tracker",
            description="Track expenses.",
            source_url="https://apps.example.com/private",
            visibility="private",
        )

        with self.assertRaises(KeyError):
            mini_apps_service.clone_public_app("target-ws", public_app_id="missing")

        mini_apps_service.publish_app(
            "source-ws",
            name="Public Tracker",
            description="Track expenses.",
            source_url="https://apps.example.com/public",
            visibility="public",
        )
        public_card = mini_apps_service.approve_app_publication("source-ws", "public_tracker")
        mini_apps_service.update_app_settings("source-ws", "public_tracker", visibility="private")
        with self.assertRaises(KeyError):
            mini_apps_service.clone_public_app("target-ws", public_app_id=public_card["public_app_id"])

    def test_update_app_settings_preserves_explicit_zero_ai_caps(self) -> None:
        mini_apps_service.publish_app(
            "ws-1",
            name="Budget Tracker",
            description="Track expenses.",
            source_url="https://apps.example.com/budget",
        )

        card = mini_apps_service.update_app_settings(
            "ws-1",
            "budget_tracker",
            ai_enabled=False,
            monthly_credit_limit=0,
            per_invocation_credit_limit=0,
        )

        self.assertFalse(card["settings"]["ai_enabled"])
        self.assertEqual(card["settings"]["monthly_credit_limit"], 0)
        self.assertEqual(card["settings"]["per_invocation_credit_limit"], 0)
        contract = mini_apps_service.get_mini_app_contract("ws-1", "budget_tracker")
        self.assertEqual(contract["ai_invoke_policy"]["monthly_credit_cap"], 0)
        self.assertEqual(contract["ai_invoke_policy"]["per_invocation_credit_cap"], 0)

    def test_state_persistence_uses_atomic_replace(self) -> None:
        original_replace = mini_apps_service.os.replace
        replacements: list[tuple[Path, Path]] = []

        def wrapped_replace(source, target):
            source_path = Path(source)
            target_path = Path(target)
            self.assertTrue(source_path.exists())
            replacements.append((source_path, target_path))
            return original_replace(source_path, target_path)

        with patch.object(mini_apps_service.os, "replace", side_effect=wrapped_replace):
            contract = mini_apps_service.upsert_mini_app_contract(
                "ws-1",
                "private_app",
                label="Private App",
                delivery_mode="hosted",
                hosted_url="https://apps.example.com/private",
                allowed_origins=["https://apps.example.com"],
            )

        self.assertEqual(contract["app_id"], "private_app")
        self.assertEqual(len(replacements), 1)
        state_file = mini_apps_service._mini_apps_state_path("ws-1")
        self.assertEqual(replacements[0][1], state_file)
        self.assertTrue(state_file.exists())
        self.assertFalse(list(state_file.parent.glob("*.tmp")))
        listing = mini_apps_service.list_mini_app_contracts("ws-1")
        self.assertEqual(listing["items"][0]["app_id"], "private_app")

    def test_retrieve_records_filters_narrow_history(self) -> None:
        mini_apps_service.upsert_mini_app_contract(
            "ws-1",
            "flashcards",
            records=[
                {"id": "card-1", "kind": "review", "tags": ["spanish", "verbs"], "created_at": "2026-04-17T09:00:00Z", "summary": "Reviewed estar"},
                {"id": "card-2", "kind": "review", "tags": ["spanish", "food"], "created_at": "2026-04-16T09:00:00Z", "summary": "Reviewed desayuno"},
                {"id": "card-3", "kind": "create", "tags": ["math"], "created_at": "2026-04-15T09:00:00Z", "summary": "Added calculus card"},
            ],
        )

        payload = mini_apps_service.retrieve_mini_app_records(
            "ws-1",
            "flashcards",
            filters={"tag": "spanish", "kind": "review", "since": "2026-04-16T12:00:00Z"},
        )

        self.assertEqual(payload["total_matches"], 1)
        self.assertEqual(payload["items"][0]["id"], "card-1")

    def test_retrieve_records_without_narrow_filters_is_rejected(self) -> None:
        mini_apps_service.upsert_mini_app_contract(
            "ws-1",
            "flashcards",
            records=[
                {"id": "card-1", "kind": "review", "summary": "Reviewed estar", "created_at": "2026-04-17T09:00:00Z"},
            ],
        )
        with self.assertRaises(ValueError) as ctx:
            mini_apps_service.retrieve_mini_app_records(
                "ws-1",
                "flashcards",
                filters={},
            )
        self.assertIn("narrow user query", str(ctx.exception))

    def test_context_block_uses_compact_summaries_not_full_raw_history(self) -> None:
        mini_apps_service.upsert_mini_app_contract(
            "ws-1",
            "speaking",
            label="Speaking Practice",
            current_state={"language": "Spanish", "streak_days": 5},
            recent_events=[
                {"id": "session-1", "summary": "Practiced introductions for 20 minutes", "created_at": "2026-04-17T08:00:00Z"},
            ],
            weekly_summary={"trend": "confidence improving"},
            long_term_facts=["Listening is weaker than speaking"],
            records=[
                {"id": "raw-1", "transcript": "very long raw transcript should not appear in compact block", "created_at": "2026-04-17T08:00:00Z"},
            ],
        )

        block = mini_apps_service.build_mini_apps_context_block("ws-1")

        self.assertIn("Mini App Summaries", block)
        self.assertIn("Speaking Practice", block)
        self.assertIn("streak days: 5", block.lower())
        self.assertIn("Retrieval hook", block)
        self.assertNotIn("very long raw transcript", block)

    def test_hosted_contract_exposes_embed_and_bridge_manifest_without_sage_memory(self) -> None:
        contract = mini_apps_service.upsert_mini_app_contract(
            "ws-1",
            "travel_partner",
            label="Travel Partner",
            description="Hosted itinerary planner",
            delivery_mode="hosted",
            hosted_url="https://miniapps.example.com/travel",
            allowed_origins=["https://miniapps.example.com"],
            bridge_contracts={"app_to_sage": ["summary_request"]},
            context_envelope={"default_classes": ["user_selected_inputs"]},
            permissions=["app.bridge.sage.request"],
        )

        self.assertEqual(contract["delivery_mode"], "hosted")
        self.assertEqual(contract["memory_scope"], "none_by_default")
        self.assertEqual(contract["hosted_url"], "https://miniapps.example.com/travel")
        self.assertEqual(contract["hosted_app"]["embed"]["kind"], "iframe")
        self.assertEqual(contract["hosted_app"]["allowed_origins"], ["https://miniapps.example.com"])
        self.assertEqual(contract["hosted_app"]["bridge"]["allowed_contracts"]["app_to_sage"], ["summary_request"])
        self.assertFalse(contract["context_envelope"]["inherits_sage_memory_by_default"])

        manifest = mini_apps_service.get_hosted_mini_app_manifest("ws-1", "travel_partner")
        self.assertEqual(manifest["delivery_mode"], "hosted")
        self.assertEqual(manifest["hosted_app"]["bridge"]["request_type"], "empyralis.hosted_app.bridge.request")
        self.assertIn("read_sage_memory", manifest["hosted_app"]["bridge"]["denied_by_default"])
        self.assertEqual(
            manifest["hosted_app"]["embed"]["sandbox"],
            ["allow-forms", "allow-modals", "allow-scripts"],
        )
        self.assertEqual(manifest["hosted_app"]["embed"]["allow"], [])
        self.assertNotIn("allow-popups", manifest["hosted_app"]["embed"]["sandbox"])
        self.assertNotIn("allow-popups-to-escape-sandbox", manifest["hosted_app"]["embed"]["sandbox"])
        self.assertNotIn("allow-same-origin", manifest["hosted_app"]["embed"]["sandbox"])

    def test_structured_apps_default_to_summary_read_permission(self) -> None:
        contract = mini_apps_service.upsert_mini_app_contract(
            "ws-1",
            "calorie_tracking",
            label="Calorie Tracking",
            delivery_mode="structured",
        )
        self.assertIn("app.summary.read", contract["permissions"])
        self.assertNotIn("app.records.read.raw", contract["permissions"])

    def test_export_and_wipe_state_support_workspace_data_retention(self) -> None:
        mini_apps_service.upsert_mini_app_contract(
            "ws-1",
            "calorie_tracking",
            label="Calorie Tracking",
            records=[
                {"id": "meal-1", "kind": "meal", "summary": "Chicken bowl", "created_at": "2026-04-17T08:00:00Z"},
                {"id": "meal-2", "kind": "meal", "summary": "Protein yogurt", "created_at": "2026-04-16T08:00:00Z"},
            ],
        )
        mini_apps_service.upsert_mini_app_contract(
            "ws-1",
            "flashcards",
            label="Flashcards",
            records=[
                {"id": "card-1", "kind": "review", "summary": "Reviewed estar", "created_at": "2026-04-15T08:00:00Z"},
            ],
        )

        exported = mini_apps_service.export_mini_apps_state("ws-1")
        self.assertEqual(exported["count"], 2)
        self.assertEqual(exported["total_records"], 3)

        wiped = mini_apps_service.wipe_mini_apps_state("ws-1")
        self.assertEqual(wiped["deleted_apps_count"], 2)
        self.assertEqual(wiped["deleted_records_count"], 3)

        listing = mini_apps_service.list_mini_app_contracts("ws-1")
        self.assertEqual(listing["count"], 0)


if __name__ == "__main__":
    unittest.main()
