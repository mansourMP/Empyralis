import importlib
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from server_modules import memory_service, workspace_context


class MemoryServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        global memory_service, workspace_context
        memory_service = importlib.import_module("server_modules.memory_service")
        workspace_context = importlib.import_module("server_modules.workspace_context")
        self._tmpdir = tempfile.TemporaryDirectory(prefix="memory-service-")
        self.addCleanup(self._tmpdir.cleanup)
        tmp_root = Path(self._tmpdir.name)
        self._workspace_root = tmp_root / "workspace"
        self._workspace_root.mkdir(parents=True, exist_ok=True)
        self._memory_root = tmp_root / "runtime-memory"
        self._workspace_patch = patch.object(workspace_context, "_WORKSPACE_DIR", self._workspace_root)
        self._memory_patch = patch.object(memory_service._workspace_memory_store, "_MEMORY_DIR", self._memory_root)
        self._semantic_model_patch = patch.object(memory_service._workspace_memory_store, "_SEMANTIC_MODEL", False)
        self._workspace_patch.start()
        self._memory_patch.start()
        self._semantic_model_patch.start()
        self.addCleanup(self._workspace_patch.stop)
        self.addCleanup(self._memory_patch.stop)
        self.addCleanup(self._semantic_model_patch.stop)

    def test_workspace_memory_snapshot_contains_entries_and_text(self) -> None:
        memory_service.save_memory("default", "timezone", "Asia/Shanghai")
        memory_service.save_memory("default", "preferred_editor", "Neovim")

        snapshot = memory_service.workspace_memory_snapshot("default")

        self.assertEqual(snapshot.workspace_id, "default")
        self.assertEqual(snapshot.entries[0]["key"], "preferred_editor")
        self.assertIn("- preferred_editor: Neovim", snapshot.text)
        self.assertEqual(snapshot.as_payload()["workspace_id"], "default")

    def test_memory_md_projection_is_categorized_and_hides_raw_fact_ids(self) -> None:
        memory_service.save_memory(
            "default",
            "fact-111aaa",
            "User prefers casual friendly communication style (e.g. 'my dude', 'my nigga').",
        )
        memory_service.save_memory(
            "default",
            "fact-111bbb",
            "User communicates in casual, friendly style ('my dude', 'my nigga').",
        )
        memory_service.save_memory(
            "default",
            "fact-222bbb",
            "User dislikes the slang terms 'my dude' and 'my nigga' and wants them removed from memory.",
        )
        memory_service.save_memory("default", "timezone", "Asia/Shanghai")
        memory_service.save_memory("default", "project_context", "User has a project workspace named Empyralis.")

        content = workspace_context.read_workspace_context_file("MEMORY.md", workspace_id="default")

        self.assertIn("# Curated Memory", content)
        self.assertIn("## Preferences", content)
        self.assertIn("## Project Context", content)
        self.assertIn("User dislikes the slang terms", content)
        self.assertIn("Timezone: Asia/Shanghai", content)
        self.assertNotIn("fact-111aaa", content)
        self.assertNotIn("fact-222bbb", content)
        self.assertNotIn("prefers casual friendly", content)
        self.assertNotIn("communicates in casual, friendly style", content)

    def test_memory_md_projection_caps_large_structured_fact_sets(self) -> None:
        for index in range(50):
            memory_service.save_memory("default", f"fact-{index:06x}", f"User project fact {index}.")

        content = workspace_context.read_workspace_context_file("MEMORY.md", workspace_id="default")

        self.assertIn("additional structured facts are available through memory search", content)
        self.assertLess(content.count("- User project fact"), 50)

    def test_publish_hybrid_summary_bridge_payload_persists_and_reports_last_safe_status(self) -> None:
        record = memory_service.publish_hybrid_summary_bridge_payload(
            "default",
            payload_class="bounded_local_private_summaries",
            summary="Local notes summarized into a cloud-safe snapshot.",
            items=[
                {
                    "kind": "local_context",
                    "label": "Notebook",
                    "summary": "Two private notes were reduced into one safe summary.",
                    "source_count": 2,
                }
            ],
            memory_layer="local_private_memory",
            source_runtime_mode="local_secure",
            last_safe=True,
        )

        records = memory_service.list_hybrid_summary_bridge_payloads("default")
        status = memory_service.hybrid_summary_bridge_status("default")

        self.assertEqual(record["payload_class"], "bounded_local_private_summaries")
        self.assertEqual(len(records), 1)
        self.assertTrue(status["last_safe_summary_available"])
        self.assertEqual(status["available_payload_classes"], ["bounded_local_private_summaries"])

    def test_publish_hybrid_summary_bridge_payload_rejects_forbidden_shape(self) -> None:
        with self.assertRaises(Exception) as error:
            memory_service.publish_hybrid_summary_bridge_payload(
                "default",
                payload_class="artifact_summaries",
                summary="Attempted unsafe publish.",
                items=[
                    {
                        "label": "Artifact",
                        "summary": "Unsafe raw artifact body.",
                        "raw_content": "secret body",
                    }
                ],
                source_runtime_mode="local_secure",
            )

        self.assertIn("raw-content fields", str(error.exception))

    def test_publish_hybrid_summary_bridge_payload_rejects_disallowed_payload_class(self) -> None:
        with self.assertRaises(Exception) as error:
            memory_service.publish_hybrid_summary_bridge_payload(
                "default",
                payload_class="full_local_specialist_internals",
                summary="Unsafe payload class.",
                source_runtime_mode="local_secure",
            )

        self.assertIn("not allowed", str(error.exception))

    def test_query_memory_returns_matching_items_and_context_blocks(self) -> None:
        memory_service.save_memory("default", "timezone", "Asia/Shanghai")
        memory_service.save_memory("default", "favorite_drink", "tea")
        memory_service.save_daily_log("default", "Reviewed the canonical runtime convergence work.")

        result = memory_service.query_memory(
            memory_service.MemoryQuery(
                workspace_id="default",
                session_id="thread-1",
                text="timezone",
            )
        )

        self.assertTrue(any(item.metadata.get("key") == "timezone" for item in result.items))
        self.assertTrue(any(block.startswith("Runtime Memory Facts") for block in result.context_blocks))
        self.assertTrue(any(block.startswith("Recent Daily Logs") for block in result.context_blocks))

    def test_memory_append_daily_note_appends_to_today_and_redacts_secrets(self) -> None:
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        with patch.object(memory_service, "_append_note_now", return_value=today):
            first = memory_service.memory_append_daily_note(
                "default",
                "Decision: keep runtime placement separate from computer automation; api_key=sk-secret-value.",
            )
            second = memory_service.memory_append_daily_note(
                "default",
                "Preference: keep memory notes concise and durable so future sessions have reliable context.",
            )

        expected_filename = f"memory/{today}.md"
        self.assertEqual(first["filename"], expected_filename)
        self.assertEqual(second["filename"], expected_filename)
        content = workspace_context.read_workspace_context_file(expected_filename, workspace_id="default")
        self.assertIn("[redacted-secret]", content)
        self.assertNotIn("sk-secret-value", content)
        self.assertGreaterEqual(content.count("- ["), 2)

    def test_memory_append_daily_note_rejects_transcript_payload(self) -> None:
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        with patch.object(memory_service, "_append_note_now", return_value=today), self.assertRaises(ValueError):
            memory_service.memory_append_daily_note(
                "default",
                "user: please remember this raw transcript\nassistant: ok I will keep this",
            )

    def test_memory_append_daily_note_rejects_low_usefulness_payload(self) -> None:
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        with patch.object(memory_service, "_append_note_now", return_value=today), self.assertRaises(ValueError):
            memory_service.memory_append_daily_note(
                "default",
                "This is just a quick test draft for now and maybe later we can ignore this note.",
            )

    def test_memory_append_daily_note_deduplicates_similar_note(self) -> None:
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        with patch.object(memory_service, "_append_note_now", return_value=today):
            first = memory_service.memory_append_daily_note(
                "default",
                "Decision: route all cloud-costly browser automation through virtual computer with approval and budget caps enabled.",
            )
            duplicate = memory_service.memory_append_daily_note(
                "default",
                "Decision: route cloud-costly browser automation via virtual computer with approval and budget caps.",
            )

        self.assertTrue(first["saved"])
        self.assertFalse(duplicate["saved"])
        self.assertIn("duplicate_of", duplicate)
        content = workspace_context.read_workspace_context_file(f"memory/{today}.md", workspace_id="default")
        self.assertEqual(content.count("- ["), 1)

    def test_memory_append_daily_note_blocks_wrong_date_write(self) -> None:
        with patch.object(memory_service, "_append_note_now", return_value="2000-01-01"), self.assertRaises(ValueError):
            memory_service.memory_append_daily_note(
                "default",
                "Decision: keep durable preferences and project context in daily memory notes.",
            )

    def test_create_memory_consolidation_staging_file_writes_under_dreams(self) -> None:
        saved = memory_service.create_memory_consolidation_staging_file(
            "default",
            "Decision: consolidate durable user preferences from daily notes into USER.md and PROCEDURES.md.",
            target_files=["USER.md", "PROCEDURES.md"],
            source_refs=["memory/2026-05-10.md#L12", "memory/2026-05-11.md#L3"],
        )

        self.assertTrue(str(saved["filename"]).startswith("memory/.dreams/"))
        content = workspace_context.read_workspace_context_file(saved["filename"], workspace_id="default")
        self.assertIn("## Proposed Consolidation", content)
        self.assertIn("## Proposed Target Files", content)
        self.assertIn("USER.md", content)

    def test_apply_memory_consolidation_staging_requires_approval_or_policy(self) -> None:
        saved = memory_service.create_memory_consolidation_staging_file(
            "default",
            "Decision: merge stable project context from daily logs into MEMORY.md.",
            target_files=["MEMORY.md"],
        )
        with self.assertRaises(ValueError):
            memory_service.apply_memory_consolidation_staging(
                "default",
                saved["filename"],
                {"MEMORY.md": "# Curated Memory\n\n- Stable context.\n"},
                user_approved=False,
                policy_allows=False,
            )

    def test_apply_memory_consolidation_staging_updates_root_when_approved(self) -> None:
        saved = memory_service.create_memory_consolidation_staging_file(
            "default",
            "Decision: keep USER.md concise with stable preferences.",
            target_files=["USER.md"],
        )
        result = memory_service.apply_memory_consolidation_staging(
            "default",
            saved["filename"],
            {"USER.md": "# User Profile\n\n- Prefers async updates.\n"},
            user_approved=True,
        )
        self.assertIn("USER.md", result["applied_files"])
        updated = workspace_context.read_workspace_context_file("USER.md", workspace_id="default")
        self.assertIn("Prefers async updates", updated)

    def test_consolidate_daily_memory_notes_returns_proposal_without_merge(self) -> None:
        workspace_context.write_workspace_context_file(
            "memory/2026-05-08.md",
            "- [08:01:00 UTC] Decision: keep vendor-neutral runtime placement policy for cloud tasks.\n",
            workspace_id="default",
        )
        workspace_context.write_workspace_context_file(
            "memory/2026-05-09.md",
            "- [10:00:00 UTC] Goal: close investor demo blockers this week and keep a clear rollout sequence.\n",
            workspace_id="default",
        )

        result = memory_service.consolidate_daily_memory_notes("default", apply_merge=False)
        self.assertFalse(result["merged"])
        self.assertIn("MEMORY.md", result["proposed_updates"])
        self.assertIn("GOALS.md", result["proposed_updates"])
        self.assertIsNone(result["audit_id"])

    def test_consolidate_daily_memory_notes_requires_approval_for_merge(self) -> None:
        workspace_context.write_workspace_context_file(
            "memory/2026-05-08.md",
            "- [08:01:00 UTC] Decision: keep vendor-neutral runtime placement policy for cloud tasks.\n",
            workspace_id="default",
        )
        with self.assertRaises(ValueError):
            memory_service.consolidate_daily_memory_notes(
                "default",
                apply_merge=True,
                user_approved=False,
                policy_allows=False,
            )

    def test_consolidate_daily_memory_notes_merges_and_archives_with_audit(self) -> None:
        workspace_context.write_workspace_context_file(
            "memory/2026-05-08.md",
            (
                "- [08:01:00 UTC] Decision: keep vendor-neutral runtime placement policy for cloud tasks.\n"
                "- [08:10:00 UTC] Procedure: always require owner approval for payment links and mass messages.\n"
            ),
            workspace_id="default",
        )

        result = memory_service.consolidate_daily_memory_notes(
            "default",
            apply_merge=True,
            compact_mode="archive",
            user_approved=True,
            run_id="run-123",
        )

        self.assertTrue(result["merged"])
        self.assertTrue(result["audit_id"])
        self.assertIn("memory/2026-05-08.md", result["compacted_daily_notes"])
        memory_content = workspace_context.read_workspace_context_file("MEMORY.md", workspace_id="default")
        procedures_content = workspace_context.read_workspace_context_file("PROCEDURES.md", workspace_id="default")
        self.assertIn("vendor-neutral runtime placement", memory_content)
        self.assertIn("owner approval for payment links", procedures_content)
        compacted_content = workspace_context.read_workspace_context_file("memory/2026-05-08.md", workspace_id="default")
        self.assertIn("Daily Note Archived", compacted_content)
        audit_path = self._memory_root / "default" / "memory_consolidation_audit" / "merges.jsonl"
        self.assertTrue(audit_path.exists())
        self.assertIn("run-123", audit_path.read_text(encoding="utf-8"))

    def test_consolidate_daily_memory_notes_rejects_delete_compaction_mode(self) -> None:
        workspace_context.write_workspace_context_file(
            "memory/2026-05-08.md",
            "- [08:01:00 UTC] Decision: keep vendor-neutral runtime placement policy for cloud tasks.\n",
            workspace_id="default",
        )
        with self.assertRaises(ValueError):
            memory_service.consolidate_daily_memory_notes(
                "default",
                apply_merge=True,
                compact_mode="delete",
                user_approved=True,
            )

    def test_memory_write_audit_records_required_fields_and_versions(self) -> None:
        updated = memory_service.update_memory_context_file(
            "default",
            "MEMORY.md",
            "# Curated Memory\n\n- Keep durable platform decisions.\n",
            actor="sage-agent",
            reason="manual_curation",
            run_id="run-abc",
            audit_metadata={"source": "unit_test"},
        )
        self.assertTrue(updated["old_hash"])
        self.assertTrue(updated["new_hash"])
        self.assertTrue(updated["version_id"])

        versions = memory_service.list_memory_file_versions("default", "MEMORY.md")
        self.assertGreaterEqual(len(versions), 1)
        first = versions[0]
        self.assertEqual(first["actor"], "sage-agent")
        self.assertEqual(first["workspace_id"], "default")
        self.assertEqual(first["filename"], "MEMORY.md")
        self.assertEqual(first["reason"], "manual_curation")
        self.assertEqual(first["run_id"], "run-abc")
        self.assertTrue(first["timestamp"])
        self.assertTrue(first["old_hash"])
        self.assertTrue(first["new_hash"])

    def test_memory_file_version_rollback_restores_previous_snapshot(self) -> None:
        first = memory_service.update_memory_context_file(
            "default",
            "REFLECTION.md",
            "# Reflection\n\n- first\n",
            actor="sage-agent",
            reason="seed",
        )
        second = memory_service.update_memory_context_file(
            "default",
            "REFLECTION.md",
            "# Reflection\n\n- second\n",
            actor="sage-agent",
            reason="rewrite",
        )
        self.assertNotEqual(first["new_hash"], second["new_hash"])
        rollback = memory_service.rollback_memory_file_version(
            "default",
            "REFLECTION.md",
            version_id=first["version_id"],
            actor="owner-user",
            reason="rollback_requested",
            run_id="run-rb",
        )
        self.assertEqual(rollback["rolled_back_to_version_id"], first["version_id"])
        restored = workspace_context.read_workspace_context_file("REFLECTION.md", workspace_id="default")
        self.assertIn("- first", restored)
        self.assertNotIn("- second", restored)

    def test_runtime_memory_search_wraps_runtime_subsystem_results(self) -> None:
        with patch.object(memory_service, "_memory_manager_or_503", return_value=object()) as manager_mock, patch.object(
            memory_service,
            "_normalize_memory_bucket",
            return_value="session",
        ) as bucket_mock, patch.object(
            memory_service,
            "_runtime_workspace_id",
            return_value="default",
        ) as workspace_mock, patch.object(
            memory_service,
            "_memory_search_scoped",
            return_value=[{"id": "mem-1", "text": "remember this", "metadata": {"bucket": "session"}}],
        ) as scoped_mock:
            result = memory_service.runtime_memory_search(
                query="remember",
                bucket="session",
                workspace_id="default",
                profile_id="profile-1",
                project_id="project-1",
                session_key="session-1",
                k=4,
            )

        manager_mock.assert_called_once_with()
        bucket_mock.assert_called_once_with("session", required=False)
        workspace_mock.assert_called_once_with("default")
        scoped_mock.assert_called_once_with(
            query="remember",
            bucket="session",
            workspace_id="default",
            agent_install_id=None,
            profile_id="profile-1",
            project_id="project-1",
            session_key="session-1",
            k=4,
        )
        self.assertEqual(result["count"], 1)
        self.assertEqual(result["items"][0]["id"], "mem-1")

    def test_runtime_memory_upsert_wraps_runtime_subsystem_manager(self) -> None:
        manager = type("Manager", (), {"upsert_memory": lambda self, text, metadata: metadata.get("id") or "mem-2"})()
        with patch.object(memory_service, "_memory_manager_or_503", return_value=manager) as manager_mock, patch.object(
            memory_service,
            "_normalize_memory_bucket",
            return_value="session",
        ) as bucket_mock, patch.object(
            memory_service,
            "_runtime_workspace_id",
            return_value="default",
        ) as workspace_mock, patch.object(
            memory_service,
            "_runtime_utc_now",
            return_value=datetime(2026, 4, 4, tzinfo=timezone.utc),
        ), patch.object(
            memory_service.runtime_memory,
            "ORION_MEMORY_RETENTION_DAYS_DEFAULT",
            30,
        ):
            result = memory_service.runtime_memory_upsert(
                text="Remember the current customer migration plan.",
                bucket="session",
                workspace_id="default",
                profile_id="profile-1",
                project_id="project-1",
                session_key="session-1",
                source="api",
                retention_days=14,
                metadata={"source_kind": "test"},
                memory_id="mem-2",
            )

        manager_mock.assert_called_once_with()
        bucket_mock.assert_called_once_with("session", required=True)
        workspace_mock.assert_called_once_with("default")
        self.assertEqual(result["id"], "mem-2")
        self.assertEqual(result["bucket"], "session")
        self.assertEqual(result["workspace_id"], "default")
        self.assertEqual(result["retention_days"], 14)
        self.assertTrue(str(result["expires_at"]).endswith("Z"))

    def test_memory_search_scoped_hides_other_install_namespaces_by_default(self) -> None:
        manager = type(
            "Manager",
            (),
            {
                "search_memory": lambda self, query, limit: [
                    {"id": "shared", "text": "shared context", "metadata": {"bucket": "session", "workspace_id": "default"}},
                    {
                        "id": "install-a",
                        "text": "specialist A context",
                        "metadata": {"bucket": "session", "workspace_id": "default", "agent_install_id": "install-a"},
                    },
                    {
                        "id": "install-b",
                        "text": "specialist B context",
                        "metadata": {"bucket": "session", "workspace_id": "default", "agent_install_id": "install-b"},
                    },
                ]
            },
        )()

        with patch.object(memory_service.runtime_memory, "_memory_manager", return_value=manager), patch.object(
            memory_service,
            "_runtime_utc_now",
            return_value=datetime(2026, 4, 4, tzinfo=timezone.utc),
        ):
            items = memory_service._memory_search_scoped(
                "context",
                bucket="session",
                workspace_id="default",
                agent_install_id="install-a",
                k=5,
            )

        self.assertEqual([item["id"] for item in items], ["shared", "install-a"])

    def test_agent_memory_namespace_denies_cross_agent_reads(self) -> None:
        workspace_context.write_workspace_context_file(
            "USER.md",
            "Agent A private profile.",
            workspace_id="default",
            agent_install_id="install-a",
        )
        workspace_context.write_workspace_context_file(
            "USER.md",
            "Agent B private profile.",
            workspace_id="default",
            agent_install_id="install-b",
        )
        memory_service.save_memory("default", "agent_a_secret", "Agent A private memory.", agent_install_id="install-a")
        memory_service.save_memory("default", "agent_b_fact", "Agent B private memory.", agent_install_id="install-b")
        memory_service.save_daily_log("default", "Agent A private log.", agent_install_id="install-a")
        memory_service.save_daily_log("default", "Agent B private log.", agent_install_id="install-b")

        agent_b_entries = memory_service.list_memory_entries("default", agent_install_id="install-b")
        agent_b_memory = memory_service.get_memory("default", agent_install_id="install-b")
        agent_b_search = memory_service.semantic_search(
            "default",
            "private",
            agent_install_id="install-b",
        )
        agent_b_logs = memory_service.get_recent_logs("default", days=7, agent_install_id="install-b")
        agent_b_context = memory_service.direct_chat_workspace_context_text(
            "default",
            memory_query="remember private",
            agent_install_id="install-b",
        )

        self.assertEqual([entry["key"] for entry in agent_b_entries], ["agent_b_fact"])
        self.assertIn("Agent B private memory.", agent_b_memory)
        self.assertNotIn("Agent A private memory.", agent_b_memory)
        self.assertTrue(all(item["key"] == "agent_b_fact" for item in agent_b_search))
        self.assertIn("Agent B private log.", agent_b_logs)
        self.assertNotIn("Agent A private log.", agent_b_logs)
        self.assertIn("Agent B private profile.", agent_b_context)
        self.assertIn("Agent B private memory.", agent_b_context)
        self.assertIn("Agent B private log.", agent_b_context)
        self.assertNotIn("Agent A private profile.", agent_b_context)
        self.assertNotIn("Agent A private memory.", agent_b_context)
        self.assertNotIn("Agent A private log.", agent_b_context)

    def test_hydrate_run_memory_context_uses_canonical_memory_service_search(self) -> None:
        run = {
            "logs": object(),
            "context": {
                "workspace_id": "default",
                "user_goal": "Find customer migration status",
                "metadata": {"profile_id": "profile-1", "active_agent_install_id": "install-specialist"},
            },
            "memory_trace": {"enabled": True, "reads": [], "writes": [], "last_error": None, "updated_at": None},
        }

        with patch.object(memory_service.runtime_memory, "ORION_MEMORY_ENABLED", True), patch.object(
            memory_service.runtime_memory,
            "_memory_manager",
            return_value=object(),
        ), patch.object(
            memory_service,
            "_memory_search_scoped",
            return_value=[{"id": "mem-1", "text": "customer migration active", "metadata": {"bucket": "profile"}}],
        ) as scoped_mock, patch.object(
            memory_service.runtime_memory,
            "_emit_log",
            return_value=None,
        ) as emit_log_mock, patch.object(
            memory_service.runtime_memory,
            "_compact_event_text",
            side_effect=lambda value, limit=800: str(value or "")[:limit],
        ), patch.object(
            memory_service,
            "_runtime_workspace_id",
            return_value="default",
        ), patch.object(
            memory_service,
            "_runtime_utc_now_iso",
            return_value="2026-04-04T00:00:00Z",
        ):
            memory_service._hydrate_run_memory_context("run-1", run)

        scoped_mock.assert_called()
        self.assertEqual(scoped_mock.call_args.kwargs["agent_install_id"], "install-specialist")
        self.assertEqual(run["context"]["metadata"]["memory_context"]["count"], 1)
        self.assertEqual(run["memory_trace"]["reads"][0]["bucket"], "profile")
        emit_log_mock.assert_called_once()

    def test_memory_scope_from_context_uses_active_install_only(self) -> None:
        with patch.object(memory_service, "_runtime_workspace_id", return_value="default"):
            scope_without_active = memory_service._memory_scope_from_context(
                {
                    "workspace_id": "default",
                    "metadata": {
                        "workspace_agent_install_id": "install-parent",
                    },
                }
            )
            self.assertEqual(scope_without_active["agent_install_id"], "")

            scope_with_active = memory_service._memory_scope_from_context(
                {
                    "workspace_id": "default",
                    "metadata": {
                        "active_agent_install_id": "install-child",
                        "workspace_agent_install_id": "install-parent",
                    },
                }
            )
        self.assertEqual(scope_with_active["agent_install_id"], "install-child")

    def test_persist_run_memory_uses_canonical_memory_service_write_path(self) -> None:
        writes = []
        manager = type(
            "Manager",
            (),
            {"upsert_memory": lambda self, text, metadata: writes.append((text, metadata)) or f"mem-{len(writes)}"},
        )()
        run = {
            "status": "completed",
            "engine": "orion",
            "logs": object(),
            "result": "Customer migration completed.",
            "context": {
                "workspace_id": "default",
                "user_goal": "Complete customer migration",
                "metadata": {
                    "profile_id": "profile-1",
                    "project_id": "project-1",
                    "active_agent_install_id": "install-specialist",
                },
            },
            "memory_trace": {"enabled": True, "reads": [], "writes": [], "last_error": None, "updated_at": None},
        }

        with patch.object(memory_service.runtime_memory, "ORION_MEMORY_ENABLED", True), patch.object(
            memory_service.runtime_memory,
            "_memory_manager",
            return_value=manager,
        ), patch.object(
            memory_service.runtime_memory,
            "MEMORY_BUCKETS",
            {"session", "project", "profile"},
        ), patch.object(
            memory_service.runtime_memory,
            "_emit_log",
            return_value=None,
        ) as emit_log_mock, patch.object(
            memory_service.runtime_memory,
            "_refresh_archived_run_snapshot",
            return_value=None,
        ), patch.object(
            memory_service.runtime_memory,
            "_json_safe",
            side_effect=lambda value: value,
        ), patch.object(
            memory_service.runtime_memory,
            "_compact_event_text",
            side_effect=lambda value, limit=800: str(value or "")[:limit],
        ), patch.object(
            memory_service,
            "_runtime_utc_now",
            return_value=datetime(2026, 4, 4, tzinfo=timezone.utc),
        ), patch.object(
            memory_service,
            "_runtime_workspace_id",
            return_value="default",
        ), patch.object(
            memory_service,
            "_runtime_utc_now_iso",
            return_value="2026-04-04T00:00:00Z",
        ):
            memory_service._persist_run_memory("run-1", run)

        self.assertEqual(len(writes), 3)
        self.assertTrue(all(metadata.get("agent_install_id") == "install-specialist" for _text, metadata in writes))
        self.assertEqual(run["memory_trace"]["writes"][-1]["bucket"], "profile")
        emit_log_mock.assert_called_once()

    def test_direct_chat_workspace_context_text_reads_install_namespace_when_requested(self) -> None:
        workspace_context.write_workspace_context_file("USER.md", "Shared user profile.\n", workspace_id="default")
        workspace_context.write_workspace_context_file(
            "USER.md",
            "Specialist user profile.\n",
            workspace_id="default",
            agent_install_id="install-specialist",
        )
        memory_service.save_memory("default", "shared_key", "shared value")
        memory_service.save_memory("default", "private_key", "private value", agent_install_id="install-specialist")
        memory_service.save_daily_log("default", "Shared log entry.")
        memory_service.save_daily_log("default", "Private log entry.", agent_install_id="install-specialist")

        text = memory_service.direct_chat_workspace_context_text(
            "default",
            memory_query="remember private",
            agent_install_id="install-specialist",
        )

        self.assertIn("Specialist user profile.", text)
        self.assertNotIn("Shared user profile.", text)
        self.assertIn("private value", text)
        self.assertNotIn("shared value", text)
        self.assertIn("Private log entry.", text)
        self.assertNotIn("Shared log entry.", text)

    def test_direct_chat_workspace_context_text_collects_context_logs_and_memory(self) -> None:
        workspace_context.write_workspace_context_file("USER.md", "Owner prefers async updates.\n", workspace_id="default")
        memory_service.save_memory("default", "timezone", "Asia/Shanghai")
        memory_service.save_daily_log("default", "Reviewed the canonical architecture document.")

        text = memory_service.direct_chat_workspace_context_text("default", memory_query="timezone")

        self.assertIn("USER.md", text)
        self.assertIn("MEMORY.md", text)
        self.assertIn("Recent Daily Logs", text)
        self.assertIn("Retrieved Relevant Memory", text)
        self.assertIn("timezone", text)

    def test_direct_chat_memory_context_message_returns_system_payload(self) -> None:
        memory_service.save_memory("default", "timezone", "Asia/Shanghai")

        message = memory_service.direct_chat_memory_context_message(
            "default",
            system_prefix="Remember this:\n",
        )

        self.assertEqual(message["role"], "system")
        self.assertIn("Remember this:", message["content"])
        self.assertIn("Asia/Shanghai", message["content"])

    def test_store_direct_chat_memory_fact_hashes_and_normalizes_fact(self) -> None:
        memory_service.store_direct_chat_memory_fact("default", "  Mansur   prefers  concise updates.  ")

        entries = memory_service.list_memory_entries("default")
        self.assertEqual(len(entries), 1)
        self.assertTrue(str(entries[0]["key"]).startswith("fact-"))
        self.assertEqual(entries[0]["content"], "Mansur prefers concise updates.")

    def test_save_direct_chat_daily_log_summary_writes_recent_logs(self) -> None:
        summary = memory_service.save_direct_chat_daily_log_summary(
            workspace_id="default",
            user_message="Continue the architecture refactor.",
            assistant_reply="Moved chat memory helpers behind the service boundary.",
        )

        recent_logs = memory_service.get_recent_logs("default", days=7)
        self.assertIn("Continue the architecture refactor.", summary)
        self.assertIn("Moved chat memory helpers behind the service boundary.", recent_logs)

    def test_parse_direct_chat_memory_facts_dedupes_and_accepts_object_wrapper(self) -> None:
        facts = memory_service.parse_direct_chat_memory_facts(
            '{"facts":["Mansur prefers concise updates.","Mansur prefers concise updates.","Timezone is Asia/Shanghai."]}'
        )

        self.assertEqual(
            facts,
            [
                "Mansur prefers concise updates.",
                "Timezone is Asia/Shanghai.",
            ],
        )

    def test_persist_direct_chat_memory_best_effort_stores_log_and_extracted_facts(self) -> None:
        def fake_generate_reply(**kwargs):
            self.assertEqual(kwargs["context"]["source"], "chat_direct_memory_extract")
            self.assertEqual(kwargs["metadata"]["source"], "chat_direct_memory_extract")
            self.assertEqual(kwargs["user_goal"], "Extract durable facts.")
            self.assertEqual(kwargs["system_prompt"], "Return a JSON array.")
            self.assertEqual(kwargs["prior_messages"][-2]["role"], "user")
            self.assertEqual(kwargs["prior_messages"][-1]["role"], "assistant")
            return ('["Timezone is Asia/Shanghai.","Timezone is Asia/Shanghai."]', {}, "openai", "")

        memory_service.persist_direct_chat_memory_best_effort(
            workspace_id="default",
            provider="openai",
            model="gpt-test",
            credentials={"api_key": "sk-test"},
            reasoning_effort="medium",
            prior_messages=[{"role": "user", "content": "Earlier context."}],
            user_message="Remember the timezone.",
            assistant_reply="I will keep that in mind.",
            generate_reply=fake_generate_reply,
            extraction_prompt="Extract durable facts.",
            extraction_system_prompt="Return a JSON array.",
        )

        recent_logs = memory_service.get_recent_logs("default", days=7)
        entries = memory_service.list_memory_entries("default")

        self.assertIn("Remember the timezone.", recent_logs)
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["content"], "Timezone is Asia/Shanghai.")

    def test_persist_direct_chat_memory_best_effort_strips_red_facts_before_extraction_prompt(self) -> None:
        captured_messages = []

        def fake_generate_reply(**kwargs):
            captured_messages.extend(kwargs["prior_messages"])
            return ('["Safe preference retained."]', {}, "openai", "")

        memory_service.persist_direct_chat_memory_best_effort(
            workspace_id="default",
            provider="openai",
            model="gpt-test",
            credentials={"api_key": "sk-test"},
            reasoning_effort="medium",
            prior_messages=[
                {
                    "role": "user",
                    "content": "Safe prior note.\nRED: customer token abcdefghijklmnopqrstuvwxyz123456",
                }
            ],
            user_message="Remember safe preference.\n- [RED] API key: sk-agent-secret-123456789",
            assistant_reply="Saved the safe preference.",
            generate_reply=fake_generate_reply,
            extraction_prompt="Extract durable facts.",
            extraction_system_prompt="Return a JSON array.",
        )

        rendered = "\n".join(item["content"] for item in captured_messages)
        self.assertIn("Safe prior note.", rendered)
        self.assertIn("Remember safe preference.", rendered)
        self.assertIn("RED memory fact(s) stripped", rendered)
        self.assertNotIn("abcdefghijklmnopqrstuvwxyz123456", rendered)
        self.assertNotIn("sk-agent-secret-123456789", rendered)

    def test_find_workspace_memory_entry_matches_key_or_content(self) -> None:
        memory_service.save_memory("default", "favorite_editor", "Neovim")
        memory_service.save_memory("default", "timezone", "Asia/Shanghai")

        by_key = memory_service.find_workspace_memory_entry("default", "favorite editor")
        by_content = memory_service.find_workspace_memory_entry("default", "shanghai")

        self.assertEqual(by_key["key"], "favorite_editor")
        self.assertEqual(by_content["key"], "timezone")

    def test_memory_suggestion_prompts_uses_saved_context_entries(self) -> None:
        memory_service.save_memory("default", "timezone", "Asia/Shanghai")
        memory_service.save_memory("default", "work_style", "async check-ins")

        prompts = memory_service.memory_suggestion_prompts("default", limit=2)

        self.assertEqual(len(prompts), 2)
        self.assertTrue(all(prompt.startswith("Use my saved context:") for prompt in prompts))

    def test_parse_no_provider_memory_write_normalizes_common_patterns(self) -> None:
        self.assertEqual(
            memory_service.parse_no_provider_memory_write("Remember my name is TestUser."),
            {"key": "name", "value": "TestUser", "display_key": "name"},
        )
        self.assertEqual(
            memory_service.parse_no_provider_memory_write("remember: Favorite Editor = Neovim."),
            {"key": "favorite_editor", "value": "Neovim", "display_key": "Favorite Editor"},
        )

    def test_parse_no_provider_memory_read_normalizes_name_and_lookup_patterns(self) -> None:
        self.assertEqual(memory_service.parse_no_provider_memory_read("What is my name"), "name")
        self.assertEqual(memory_service.parse_no_provider_memory_read("What's my timezone?"), "my timezone")
        self.assertEqual(memory_service.parse_no_provider_memory_read("What is my timezone?"), "my timezone")
        self.assertEqual(memory_service.parse_no_provider_memory_read("Recall favorite editor"), "favorite editor")
        self.assertEqual(
            memory_service.parse_no_provider_memory_read("What did I say about timezone"),
            "timezone",
        )

    def test_handle_no_provider_memory_request_handles_write_and_read_paths(self) -> None:
        write_reply = memory_service.handle_no_provider_memory_request("default", "Remember that timezone is Asia/Shanghai.")
        read_reply = memory_service.handle_no_provider_memory_request("default", "What is timezone")
        missing_reply = memory_service.handle_no_provider_memory_request("default", "What is favorite color")

        self.assertIsNone(write_reply)
        self.assertEqual(read_reply, "timezone = Asia/Shanghai")
        self.assertIsNone(missing_reply)

    def test_handle_no_provider_memory_request_reads_common_question_with_punctuation(self) -> None:
        write_reply = memory_service.handle_no_provider_memory_request("default", "Remember that my timezone is Asia/Shanghai.")
        read_reply = memory_service.handle_no_provider_memory_request("default", "What is my timezone?")

        self.assertIsNone(write_reply)
        self.assertEqual(read_reply, "my_timezone = Asia/Shanghai")


if __name__ == "__main__":
    unittest.main()
