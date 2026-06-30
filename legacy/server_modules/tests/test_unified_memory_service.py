import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from server_modules import memory_service, session_transcript_store, shared_operational_board_service, unified_memory_service, workspace_context


class UnifiedMemoryServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory(prefix="unified-memory-service-")
        self.addCleanup(self._tmpdir.cleanup)
        tmp_root = Path(self._tmpdir.name)
        self._workspace_root = tmp_root / "workspace"
        self._workspace_root.mkdir(parents=True, exist_ok=True)
        self._memory_root = tmp_root / "runtime-memory"
        self._transcripts_root = tmp_root / "transcripts"
        self._workspace_patch = patch.object(workspace_context, "_WORKSPACE_DIR", self._workspace_root)
        self._memory_patch = patch.object(memory_service._workspace_memory_store, "_MEMORY_DIR", self._memory_root)
        self._semantic_model_patch = patch.object(memory_service._workspace_memory_store, "_SEMANTIC_MODEL", False)
        self._transcript_patch = patch.object(session_transcript_store, "_TRANSCRIPTS_ROOT", self._transcripts_root)
        self._workspace_patch.start()
        self._memory_patch.start()
        self._semantic_model_patch.start()
        self._transcript_patch.start()
        self.addCleanup(self._workspace_patch.stop)
        self.addCleanup(self._memory_patch.stop)
        self.addCleanup(self._semantic_model_patch.stop)
        self.addCleanup(self._transcript_patch.stop)

    def test_build_sage_memory_payload_exposes_layers_boundaries_and_retrieval(self) -> None:
        workspace_context.write_workspace_context_file(
            "USER.md",
            "# User Profile\n\n- Prefers concise daily summaries.\n- Works late nights.\n",
            workspace_id="default",
        )
        workspace_context.write_workspace_context_file(
            "MEMORY.md",
            "# Curated Memory\n\n- timezone: Asia/Shanghai\n",
            workspace_id="default",
        )
        knowledge_dir = workspace_context.workspace_knowledge_dir(workspace_id="default")
        (knowledge_dir / "notes.md").write_text(
            "# Planning\n\nThe customer wants the phone app as the default surface.\n",
            encoding="utf-8",
        )
        notes_dir = memory_service._workspace_memory_store._memory_notebook_dir("default")
        (notes_dir / "2026-04-10.md").write_text(
            "# 2026-04-10\n\nSage should stay mobile-first and privacy-safe.\n",
            encoding="utf-8",
        )
        memory_service.save_daily_log("default", "Discussed the unified memory rollout.")
        session_transcript_store.save_session_transcript(
            workspace_id="default",
            thread_id="thread-1",
            provider="openai",
            model="gpt-5.4",
            messages=[
                {"role": "user", "content": "Make mobile the main product."},
                {"role": "assistant", "content": "Kept mobile first and desktop as the power surface."},
            ],
            user_message="Make mobile the main product.",
            assistant_reply="Kept mobile first and desktop as the power surface.",
        )

        with patch(
            "server_modules.unified_memory_service.personal_context_engine.list_events",
            new=AsyncMock(
                return_value=[
                    {
                        "id": "evt-1",
                        "event_type": "message_awaiting_reply",
                        "source_app": "mobile",
                        "entity_id": "thread-123",
                        "summary": "Reply is still pending.",
                        "priority": 80,
                        "scope": {"audience": ["sage"]},
                        "created_at": "2026-04-10T00:00:00Z",
                    }
                ]
            ),
        ):
            payload = asyncio.run(
                unified_memory_service.build_sage_memory_payload(
                    tenant_id="tenant-1",
                    workspace_id="default",
                )
            )

        self.assertEqual(payload["audience"], "sage")
        self.assertEqual(payload["layer_order"], list(unified_memory_service.UNIFIED_MEMORY_LAYER_ORDER))
        self.assertIn("profile_memory", payload["layers"])
        self.assertIn("shared_operational_board", payload["layers"])
        self.assertIn("cloud_synced_memory", payload["layers"])
        self.assertIn("Prefers concise daily summaries.", payload["layers"]["profile_memory"]["items"][0]["text"])
        self.assertEqual(payload["layers"]["app_event_history"]["summary"]["count"], 1)
        self.assertGreaterEqual(payload["layers"]["notes_documents_retrieval"]["summary"]["indexed_document_count"], 2)
        snippets = payload["layers"]["notes_documents_retrieval"]["items"]
        self.assertTrue(any(item["path"] == "knowledge/notes.md" for item in snippets))
        self.assertIn("local_private_memory", payload["boundary_map"]["never_sync_by_default"])
        self.assertIn("app_event_history", payload["boundary_map"]["cloud_synced_by_default"])
        self.assertIn("shared_operational_board", payload["boundary_map"]["workspace_shared_by_default"])

    def test_build_sage_memory_payload_ingests_hybrid_summary_bridge_records_only_into_cloud_synced_layer(self) -> None:
        memory_service.publish_hybrid_summary_bridge_payload(
            "default",
            payload_class="artifact_summaries",
            summary="Research specialist finished a local summary draft.",
            items=[
                {
                    "kind": "artifact",
                    "label": "Draft",
                    "summary": "Draft summary ready for review.",
                    "artifact_id": "artifact-1",
                }
            ],
            agent_install_id="install-research",
            run_id="run-1",
            source_runtime_mode="local_secure",
            last_safe=True,
        )

        with patch(
            "server_modules.unified_memory_service.personal_context_engine.list_events",
            new=AsyncMock(return_value=[]),
        ):
            payload = asyncio.run(
                unified_memory_service.build_sage_memory_payload(
                    tenant_id="tenant-1",
                    workspace_id="default",
                )
            )

        cloud_items = payload["layers"]["cloud_synced_memory"]["items"]
        bridge_items = [item for item in cloud_items if item.get("source") == "hybrid_summary_bridge"]
        self.assertEqual(len(bridge_items), 1)
        self.assertEqual(bridge_items[0]["payload_class"], "artifact_summaries")
        self.assertEqual(bridge_items[0]["items"][0]["artifact_id"], "artifact-1")
        self.assertEqual(payload["layers"]["cloud_synced_memory"]["summary"]["hybrid_summary_bridge_count"], 1)
        self.assertTrue(payload["layers"]["cloud_synced_memory"]["summary"]["last_safe_summary_available"])
        self.assertEqual(payload["layers"]["local_private_memory"]["items"][0]["source"], "workspace_context_files")
        self.assertFalse(payload["state_layer_model"]["artifacts_history"]["raw_private_memory_embeds_allowed"])

    def test_build_specialist_memory_payload_exposes_published_shared_board_without_private_leakage(self) -> None:
        workspace_context.write_workspace_context_file(
            "USER.md",
            "# User Profile\n\n- Owner private profile.\n",
            workspace_id="default",
        )
        asyncio.run(
            shared_operational_board_service.write_shared_operational_board_entry(
                "default",
                tenant_id="tenant-1",
                actor_permission="publish_update",
                action="publish_update",
                actor={"type": "user", "id": "owner-1", "role": "owner"},
                entry_kind="sop_playbook",
                title="Refund playbook",
                body="Use the refund checklist and confirm account email before issuing a credit.",
            )
        )

        with patch(
            "server_modules.unified_memory_service.personal_context_engine.list_events",
            new=AsyncMock(return_value=[]),
        ):
            payload = asyncio.run(
                unified_memory_service.build_specialist_memory_payload(
                    tenant_id="tenant-1",
                    workspace_id="default",
                    agent_install_id="install-research",
                )
            )

        board_layer = payload["layers"]["shared_operational_board"]
        self.assertTrue(board_layer["accessible"])
        self.assertEqual(board_layer["items"][0]["title"], "Refund playbook")
        self.assertIn("refund checklist", board_layer["items"][0]["body"])
        self.assertNotIn("Owner private profile.", str(board_layer["items"]))
        self.assertTrue(payload["state_layer_model"]["shared_operational_board"]["accessible"])
        self.assertEqual(
            payload["state_layer_model"]["shared_operational_board"]["permission_tiers"],
            ["read", "propose_update", "publish_update"],
        )
        self.assertFalse(payload["state_layer_model"]["artifacts_history"]["raw_private_memory_embeds_allowed"])

    def test_build_specialist_memory_payload_keeps_owner_profile_hidden_by_default(self) -> None:
        workspace_context.write_workspace_context_file(
            "USER.md",
            "# User Profile\n\n- Owner private profile.\n",
            workspace_id="default",
        )
        workspace_context.write_workspace_context_file(
            "USER.md",
            "# Install Profile\n\n- Research specialist only.\n",
            workspace_id="default",
            agent_install_id="install-research",
        )
        memory_service.save_memory("default", "shared_fact", "shared value")
        memory_service.save_memory("default", "install_fact", "private research value", agent_install_id="install-research")
        memory_service.save_daily_log("default", "Workspace-wide log entry.")
        memory_service.save_daily_log("default", "Install-specific log entry.", agent_install_id="install-research")

        with patch(
            "server_modules.unified_memory_service.personal_context_engine.list_events",
            new=AsyncMock(
                return_value=[
                    {
                        "id": "evt-install",
                        "event_type": "study_session_completed",
                        "source_app": "flashcards",
                        "entity_id": "deck-1",
                        "summary": "Study session completed.",
                        "priority": 32,
                        "scope": {"audience": ["all_specialists"], "agent_install_ids": ["install-research"]},
                        "created_at": "2026-04-10T00:00:00Z",
                    }
                ]
            ),
        ):
            payload = asyncio.run(
                unified_memory_service.build_specialist_memory_payload(
                    tenant_id="tenant-1",
                    workspace_id="default",
                    agent_install_id="install-research",
                )
            )

        profile_layer = payload["layers"]["profile_memory"]
        self.assertTrue(profile_layer["accessible"])
        self.assertIn("Research specialist only.", profile_layer["items"][0]["text"])
        self.assertNotIn("Owner private profile.", profile_layer["items"][0]["text"])
        specialist_layer = payload["layers"]["specialist_scoped_memory"]
        snapshot = specialist_layer["items"][0]["snapshot"]
        self.assertEqual(snapshot["agent_install_id"], "install-research")
        self.assertEqual([entry["key"] for entry in snapshot["entries"]], ["install_fact"])
        self.assertEqual(payload["layers"]["local_private_memory"]["items"], [])
        self.assertEqual(payload["layers"]["app_event_history"]["summary"]["count"], 1)
        self.assertFalse(payload["state_layer_model"]["captain_private_memory"]["accessible"])
        self.assertTrue(payload["state_layer_model"]["specialist_private_memory"]["accessible"])
        self.assertFalse(payload["state_layer_model"]["specialist_private_memory"]["cross_install_allowed"])

    def test_build_sage_memory_payload_rejects_specialist_viewer_for_captain_private_memory(self) -> None:
        with self.assertRaises(PermissionError) as ctx:
            asyncio.run(
                unified_memory_service.build_sage_memory_payload(
                    tenant_id="tenant-1",
                    workspace_id="default",
                    viewer_role="specialist",
                    viewer_install_id="install-research",
                )
            )

        self.assertIn("Captain private memory", str(ctx.exception))

    def test_build_specialist_memory_payload_rejects_cross_specialist_private_access(self) -> None:
        with self.assertRaises(PermissionError) as ctx:
            asyncio.run(
                unified_memory_service.build_specialist_memory_payload(
                    tenant_id="tenant-1",
                    workspace_id="default",
                    agent_install_id="install-research",
                    viewer_role="specialist",
                    viewer_install_id="install-other",
                )
            )

        self.assertIn("another specialist's private memory", str(ctx.exception))

    def test_search_unified_memory_documents_finds_notebook_and_knowledge_sources(self) -> None:
        notes_dir = memory_service._workspace_memory_store._memory_notebook_dir("default")
        (notes_dir / "routing.md").write_text(
            "# Routing\n\nAgent routing for a secure hybrid runtime must stay install-scoped.\n",
            encoding="utf-8",
        )
        knowledge_dir = workspace_context.workspace_knowledge_dir(workspace_id="default")
        (knowledge_dir / "customer-brief.md").write_text(
            "# Brief\n\nThe customer needs a secure hybrid runtime model.\n",
            encoding="utf-8",
        )

        results = unified_memory_service.search_unified_memory_documents(
            workspace_id="default",
            query="secure hybrid runtime",
            limit=6,
        )

        paths = [item["path"] for item in results]
        self.assertIn("knowledge/customer-brief.md", paths)
        self.assertTrue(any(path == "memory/routing.md" for path in paths))

    def test_list_session_transcript_summaries_returns_recent_entries(self) -> None:
        session_transcript_store.save_session_transcript(
            workspace_id="default",
            thread_id="thread-a",
            provider="openai",
            model="gpt-5.4",
            messages=[
                {"role": "user", "content": "Track my reminders."},
                {"role": "assistant", "content": "I will watch for ignored reminders."},
            ],
            user_message="Track my reminders.",
            assistant_reply="I will watch for ignored reminders.",
        )

        items = session_transcript_store.list_session_transcript_summaries(
            workspace_id="default",
            limit=3,
        )

        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["thread_id"], "thread-a")
        self.assertIn("Track my reminders.", items[0]["summary"])
