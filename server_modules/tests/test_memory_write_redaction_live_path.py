"""MAN-53 follow-up: the native memory_write/memory_update/
memory_append_daily_note tools had ZERO secret redaction on their live
path, while a parallel per-agent skill path (agent_memory_tools.memory_write,
reached only via skill_invoke/skill-id "memory-write") already redacted
every write with secret_redaction_service.redact_text and passes 10/10 of
its own tests (test_agent_memory_write_secret_redaction.py).

VERIFIED GAP (re-confirmed before this fix):
  - The kernel prompt (sage_instruction_compiler_service.py's _kernel_prompt,
    the "## Memory -- why first" rule) instructs the model to persist
    durable facts by calling the tool literally named `memory_write` with
    path='MEMORY.md'.
  - direct_chat_operator_binding_service.parse_tool_name resolves that name
    to ("memory", "write").
  - skills_service.py's execute_single_direct_tool_call dispatches
    ("memory", "write") to memory_service.memory_write_file, ("memory",
    "update") to update_memory_context_file, and ("memory",
    "append_daily_note") to memory_append_daily_note.
  - memory_write_file and update_memory_context_file had NO redaction call
    at all. memory_append_daily_note had a narrow, THREE-pattern local
    redactor (sk-/api_key=/Bearer only) -- real, but far short of the
    ~20-pattern secret_redaction_service.redact_text (JWTs, AWS/GitHub/
    Slack/Stripe-style tokens, private keys, card numbers, phone numbers,
    unrecognized high-entropy secrets).

THE FIX: reuse (not reimplement) secret_redaction_service.redact_text --
the exact function agent_memory_tools.py's memory_write already uses --
at the memory_service.py chokepoint, applied to `content`/`note` before
anything else (provenance marker, cap check, index upsert, disk write).
The daily-note path's own narrow redactor is layered with (not replaced
by) the shared one.

A FOURTH seam, found during this follow-up's own re-verification pass
(not one of the three named above): memory_apply_edit (skills_service.py
dispatches ("memory","apply_edit") to memory_service.apply_memory_
consolidation_staging) writes model-composed `merged_files` content
DIRECTLY via write_workspace_context_file, never routing through
memory_write_file or update_memory_context_file, so it had the exact
same zero-redaction gap despite being a fully native, model-callable
tool (direct_chat_runtime_exports.py's tool list; schema in
skills_service.py). It is now redacted per merged file, same redactor,
same `redacted`/`redaction_note` visibility contract.

This file exercises:
  1. The REAL, unmocked native dispatch (tool name -> the real
     parse_tool_name -> skills_service.execute_single_direct_tool_call ->
     the real memory_service functions) for all four write seams.
  2. The same secret vectors test_agent_memory_write_secret_redaction.py
     already proved against the parallel path, applied directly to
     memory_service.memory_write_file/update_memory_context_file, for
     parity.
  3. Composition: redaction runs alongside (never instead of) the
     provenance/attribution write filter, the 200-line/25KB caps, and the
     auto-maintained MEMORY.md topic-file index -- none of that committed
     behavior (b06402bf1, cdef2d363) regresses.
  4. Visibility: the tool-result JSON the model sees carries an explicit
     `redacted` flag (and a `redaction_note` when true) -- never silent.
  5. A clean write (no secret-like content) is stored byte-for-byte
     unchanged, with `redacted: false` and no `redaction_note` key.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

from server_modules import (
    direct_chat_operator_binding_service,
    direct_tool_execution_service,
    memory_service,
    skills_service,
    workspace_context,
)


def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _callbacks() -> direct_tool_execution_service.DirectToolExecutionCallbacks:
    """Real router, real memory_service functions.

    `parse_tool_name` is the REAL direct_chat_operator_binding_service
    function (not a fixture-local approximation) -- this is what makes
    tool_call={"name": "memory_write", ...} actually reach the same
    ("memory", "write") branch the kernel prompt's live tool call reaches.
    memory_write_file / update_memory_context_file / memory_append_daily_note
    are deliberately left unset so they fall through to
    DirectToolExecutionCallbacks' own defaults, which import and call the
    REAL server_modules.memory_service functions -- the whole point of this
    file is proving the actual native path, not a stand-in for it."""
    return direct_tool_execution_service.DirectToolExecutionCallbacks(
        compact_step_detail=lambda value: None,
        titleize_direct_step_token=lambda value: str(value or ""),
        run_async_tool_call=lambda awaitable: awaitable,
        parse_tool_name=direct_chat_operator_binding_service.parse_tool_name,
        tool_arguments_payload=lambda payload: payload if isinstance(payload, dict) else {},
        parse_json_object_loose=lambda value: {},
        safe_positive_int=lambda value, default=0: int(value) if str(value or "").strip().isdigit() else default,
        normalize_reasoning_effort=lambda value: None,
        build_direct_local_tool_config=lambda connector_id, action_id, tool_input: ("", {}),
        format_direct_local_tool_result=lambda result: json.dumps(result, ensure_ascii=False),
        build_direct_tool_config=lambda connector_id, action_id, tool_input: {
            "connector": connector_id, "action": action_id, "input": tool_input,
        },
        format_direct_tool_result=lambda result: json.dumps(result, ensure_ascii=False),
        llm_task=lambda *args, **kwargs: {"ok": True},
        web_search=lambda query: [],
        web_fetch=lambda url: "",
        search_memory_notebook=lambda *args, **kwargs: {
            "results": [], "files_searched": 0, "errors": [], "status": "no_files", "message": "",
        },
        get_memory_notebook_excerpt=lambda *args, **kwargs: {},
    )


class _LiveMemoryTestBase(unittest.TestCase):
    """Same rk-mock + tmp-workspace scaffolding as
    test_memory_provenance.py/test_memory_file_caps.py's setUp -- every
    test runs against a fresh tmp dir, never the real .orion-stack/ tree."""

    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory(prefix="memory-write-redaction-live-path-")
        self.addCleanup(self._tmpdir.cleanup)
        tmp_root = Path(self._tmpdir.name)
        self._workspace_root = tmp_root / "workspace"
        self._workspace_root.mkdir(parents=True, exist_ok=True)
        self._memory_root = tmp_root / "runtime-memory"

        from server_modules import rust_runtime_kernel_client as rk

        mem_map = {
            "upsert_workspace_memory": "write_workspace_memory",
            "delete_workspace_memory": "delete_workspace_memory",
            "append_workspace_daily_log": "append_workspace_daily_log",
            "update_workspace_context_file": "write_workspace_context_file",
        }

        def _run_enforced(_name, payload):
            op = str((payload or {}).get("operation") or "")
            return {"decision": "allow", "next_action": mem_map.get(op, op)}

        def _state_decision(*, operation, **_kw):
            return {"decision": "allow", "next_action": operation}

        patchers = [
            patch.object(workspace_context, "_WORKSPACE_DIR", self._workspace_root),
            patch.object(memory_service._workspace_memory_store, "_MEMORY_DIR", self._memory_root),
            patch.object(rk, "run_runtime_kernel_enforced", _run_enforced),
            patch.object(rk, "runtime_state_store_decision", _state_decision),
            patch.object(rk, "enforce_kernel_decision", lambda *_a, **_k: None),
        ]
        for p in patchers:
            p.start()
            self.addCleanup(p.stop)

    def _memory_md(self, workspace_id: str = "ws-1") -> str:
        return workspace_context.read_workspace_context_file("MEMORY.md", workspace_id=workspace_id)


# ── 1. The REAL native dispatch, end to end ─────────────────────────────────


class NativeMemoryWriteToolLivePathTests(_LiveMemoryTestBase):
    """tool name "memory_write" -> real parse_tool_name -> real
    execute_single_direct_tool_call dispatch -> real memory_write_file."""

    def _call(self, *, content: str, mode: str = "append", path: str = "MEMORY.md") -> dict:
        raw = skills_service.execute_single_direct_tool_call(
            tool_call={"name": "memory_write", "arguments": {"path": path, "content": content, "mode": mode}},
            workspace_id="ws-1", thread_id="t-1",
            session_ctx={"authority_tier": "owner"},
            callbacks=_callbacks(),
        )
        return json.loads(raw)

    def test_secret_is_redacted_on_disk_and_flagged_in_the_tool_result(self) -> None:
        payload = self._call(
            content="My OpenAI key is sk-abcdefghijklmnopqrstuvwxyz1234567890ABCD, keep it safe.",
        )
        self.assertTrue(payload["ok"])
        self.assertTrue(payload["redacted"])
        self.assertIn("redaction_note", payload)
        # Model-facing wording, not hardcoded agent speech.
        self.assertIn("secret", payload["redaction_note"].lower())

        on_disk = self._memory_md()
        self.assertNotIn("sk-abcdefghijklmnopqrstuvwxyz1234567890ABCD", on_disk)
        self.assertIn("[redacted-secret]", on_disk)
        self.assertIn("My OpenAI key is", on_disk)
        self.assertIn("keep it safe", on_disk)

    def test_clean_content_is_stored_unchanged_and_not_flagged(self) -> None:
        payload = self._call(content="likes concise updates")
        self.assertTrue(payload["ok"])
        self.assertFalse(payload["redacted"])
        self.assertNotIn("redaction_note", payload)
        self.assertIn("likes concise updates", self._memory_md())


class NativeMemoryUpdateToolLivePathTests(_LiveMemoryTestBase):
    """tool name "memory_update" -> real dispatch -> real
    update_memory_context_file (the whole-file-replace seam)."""

    def _call(self, *, filename: str, content: str) -> dict:
        raw = skills_service.execute_single_direct_tool_call(
            tool_call={"name": "memory_update", "arguments": {"filename": filename, "content": content}},
            workspace_id="ws-1", thread_id="t-1",
            session_ctx={"authority_tier": "owner"},
            callbacks=_callbacks(),
        )
        return json.loads(raw)

    def test_secret_in_whole_file_replace_is_redacted_and_flagged(self) -> None:
        payload = self._call(
            filename="PROCEDURES.md",
            content="# Procedures\n\nStaging DB creds: user=admin password=Sup3rSecretPass!\n",
        )
        self.assertTrue(payload["ok"])
        self.assertTrue(payload["redacted"])
        self.assertIn("redaction_note", payload)

        on_disk = workspace_context.read_workspace_context_file("PROCEDURES.md", workspace_id="ws-1")
        self.assertNotIn("Sup3rSecretPass!", on_disk)
        self.assertIn("password=[redacted-secret]", on_disk)
        self.assertIn("user=admin", on_disk)

    def test_clean_whole_file_replace_is_unchanged_and_not_flagged(self) -> None:
        content = "# Procedures\n\n- Ship the memory wave.\n"
        payload = self._call(filename="PROCEDURES.md", content=content)
        self.assertTrue(payload["ok"])
        self.assertFalse(payload["redacted"])
        self.assertNotIn("redaction_note", payload)
        self.assertEqual(workspace_context.read_workspace_context_file("PROCEDURES.md", workspace_id="ws-1"), content)


class NativeMemoryAppendDailyNoteToolLivePathTests(_LiveMemoryTestBase):
    """tool name "memory_append_daily_note" -> real dispatch -> real
    memory_append_daily_note."""

    def _call(self, *, note: str) -> dict:
        with patch.object(memory_service, "_append_note_now", return_value=_today()):
            raw = skills_service.execute_single_direct_tool_call(
                tool_call={"name": "memory_append_daily_note", "arguments": {"note": note}},
                workspace_id="ws-1", thread_id="t-1",
                session_ctx={"authority_tier": "owner"},
                callbacks=_callbacks(),
            )
        return json.loads(raw)

    def test_secret_in_daily_note_is_redacted_and_flagged(self) -> None:
        payload = self._call(
            note="Decision: rotate the staging key; api_key=sk-liveSecretValue1234 must never be reused.",
        )
        self.assertTrue(payload["ok"])
        self.assertTrue(payload["saved"])
        self.assertTrue(payload["redacted"])
        self.assertIn("redaction_note", payload)

        content = workspace_context.read_workspace_context_file(f"memory/{_today()}.md", workspace_id="ws-1")
        self.assertNotIn("sk-liveSecretValue1234", content)

    def test_clean_daily_note_is_stored_unchanged_and_not_flagged(self) -> None:
        payload = self._call(note="Preference: keep memory notes concise and durable for future sessions.")
        self.assertTrue(payload["ok"])
        self.assertTrue(payload["saved"])
        self.assertFalse(payload["redacted"])
        self.assertNotIn("redaction_note", payload)


class NativeMemoryApplyEditToolLivePathTests(_LiveMemoryTestBase):
    """tool name "memory_stage_edit" -> "memory_apply_edit" -> real dispatch
    -> real memory_service.apply_memory_consolidation_staging -- the FOURTH
    seam (self-found, not one of the three named in the module docstring),
    which writes directly via write_workspace_context_file and never routes
    through memory_write_file/update_memory_context_file."""

    def _stage(self, *, content: str, target_files: list) -> dict:
        raw = skills_service.execute_single_direct_tool_call(
            tool_call={
                "name": "memory_stage_edit",
                "arguments": {"filename": target_files[0], "content": content, "reason": "consolidation test"},
            },
            workspace_id="ws-1", thread_id="t-1",
            session_ctx={"authority_tier": "owner"},
            callbacks=_callbacks(),
        )
        return json.loads(raw)

    def _apply(self, *, staging_filename: str, merged_files: dict) -> dict:
        raw = skills_service.execute_single_direct_tool_call(
            tool_call={
                "name": "memory_apply_edit",
                "arguments": {
                    "staging_filename": staging_filename,
                    "merged_files": merged_files,
                    "user_approved": True,
                },
            },
            workspace_id="ws-1", thread_id="t-1",
            session_ctx={"authority_tier": "owner"},
            callbacks=_callbacks(),
        )
        return json.loads(raw)

    def test_secret_in_merged_files_is_redacted_and_flagged(self) -> None:
        stage_payload = self._stage(
            content="Decision: rotate the shared portal password once a month.",
            target_files=["PROCEDURES.md"],
        )
        self.assertTrue(stage_payload["ok"])

        apply_payload = self._apply(
            staging_filename=stage_payload["filename"],
            merged_files={
                "PROCEDURES.md": "# Procedures\n\nPortal login: user=admin password=Sup3rSecretPass!\n",
            },
        )
        self.assertTrue(apply_payload["redacted"])
        self.assertIn("redaction_note", apply_payload)
        self.assertIn("secret", apply_payload["redaction_note"].lower())

        on_disk = workspace_context.read_workspace_context_file("PROCEDURES.md", workspace_id="ws-1")
        self.assertNotIn("Sup3rSecretPass!", on_disk)
        self.assertIn("password=[redacted-secret]", on_disk)
        self.assertIn("user=admin", on_disk)

    def test_clean_merged_files_are_unchanged_and_not_flagged(self) -> None:
        stage_payload = self._stage(
            content="Decision: ship the memory wave.",
            target_files=["PROCEDURES.md"],
        )
        content = "# Procedures\n\n- Ship the memory wave.\n"
        apply_payload = self._apply(
            staging_filename=stage_payload["filename"],
            merged_files={"PROCEDURES.md": content},
        )
        self.assertFalse(apply_payload["redacted"])
        self.assertNotIn("redaction_note", apply_payload)
        self.assertEqual(workspace_context.read_workspace_context_file("PROCEDURES.md", workspace_id="ws-1"), content)


class ApplyMemoryConsolidationStagingFunctionRedactionTests(_LiveMemoryTestBase):
    """Direct function-level parity/composition test for the fourth seam,
    matching MemoryWriteFileFunctionRedactionParityTests' style."""

    def test_secret_across_multiple_merged_root_files_is_redacted(self) -> None:
        staged = memory_service.create_memory_consolidation_staging_file(
            "ws-1",
            "Decision: consolidate credential handling notes into REFLECTION.md and PROCEDURES.md.",
            target_files=["REFLECTION.md", "PROCEDURES.md"],
        )
        result = memory_service.apply_memory_consolidation_staging(
            "ws-1",
            staged["filename"],
            {
                "REFLECTION.md": "# Reflection\n\nCustomer's card on file: 4111 1111 1111 1111.\n",
                "PROCEDURES.md": "# Procedures\n\n- Ship the memory wave.\n",
            },
            user_approved=True,
        )
        self.assertTrue(result["redacted"])
        reflection = workspace_context.read_workspace_context_file("REFLECTION.md", workspace_id="ws-1")
        procedures = workspace_context.read_workspace_context_file("PROCEDURES.md", workspace_id="ws-1")
        self.assertNotIn("4111 1111 1111 1111", reflection)
        self.assertEqual(procedures, "# Procedures\n\n- Ship the memory wave.\n")

    def test_clean_merge_is_byte_for_byte_unchanged(self) -> None:
        staged = memory_service.create_memory_consolidation_staging_file(
            "ws-1",
            "Decision: keep REFLECTION.md concise with stable notes.",
            target_files=["REFLECTION.md"],
        )
        content = "# Reflection\n\n- Prefers async updates.\n"
        result = memory_service.apply_memory_consolidation_staging(
            "ws-1", staged["filename"], {"REFLECTION.md": content}, user_approved=True,
        )
        self.assertFalse(result["redacted"])
        self.assertEqual(workspace_context.read_workspace_context_file("REFLECTION.md", workspace_id="ws-1"), content)


# ── 2. Parity with the parallel per-agent path's own proven vectors ─────────


class MemoryWriteFileFunctionRedactionParityTests(_LiveMemoryTestBase):
    """The exact secret shapes test_agent_memory_write_secret_redaction.py
    already proved against agent_memory_tools.memory_write, re-proved here
    directly against memory_service.memory_write_file -- same redactor,
    same guarantees, on the path that used to have none."""

    def test_api_key_is_redacted(self) -> None:
        memory_service.memory_write_file(
            "ws-1", "MEMORY.md",
            "the customer's OpenAI key is sk-abcdefghijklmnopqrstuvwxyz1234567890ABCD",
            mode="append", reason="memory_write",
        )
        content = self._memory_md()
        self.assertNotIn("sk-abcdefghijklmnopqrstuvwxyz1234567890ABCD", content)
        self.assertIn("[redacted-secret]", content)

    def test_jwt_bearer_token_is_redacted_on_a_topic_file_replace(self) -> None:
        jwt = (
            "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9."
            "eyJzdWIiOiIxMjM0NTY3ODkwIn0."
            "dozjgNryP4J3jVmNHl0w5N_XgL0n3I9PlFUP0THsR8U"
        )
        result = memory_service.memory_write_file(
            "ws-1", "memory/files/incident.md",
            f"Auth header used during the incident: Authorization: Bearer {jwt}",
            mode="replace", reason="memory_write", description="Incident notes.",
        )
        self.assertTrue(result["redacted"])
        content = workspace_context.read_workspace_context_file("memory/files/incident.md", workspace_id="ws-1")
        self.assertNotIn(jwt, content)

    def test_card_number_like_sequence_is_redacted(self) -> None:
        memory_service.memory_write_file(
            "ws-1", "memory/files/billing.md",
            "Customer's card on file: 4111 1111 1111 1111, exp 12/29.",
            mode="replace", reason="memory_write", description="Billing notes.",
        )
        content = workspace_context.read_workspace_context_file("memory/files/billing.md", workspace_id="ws-1")
        self.assertNotIn("4111 1111 1111 1111", content)

    def test_ordinary_prose_is_byte_for_byte_unchanged(self) -> None:
        content = (
            "The user's name is Alice Chen. She prefers async standups on "
            "Tuesdays and is based in Austin, Texas."
        )
        result = memory_service.memory_write_file(
            "ws-1", "memory/files/alice.md", content, mode="replace",
            reason="memory_write", description="Alice's preferences.",
        )
        self.assertFalse(result["redacted"])
        self.assertEqual(
            workspace_context.read_workspace_context_file("memory/files/alice.md", workspace_id="ws-1"),
            content,
        )


class UpdateMemoryContextFileFunctionRedactionTests(_LiveMemoryTestBase):
    def test_password_key_value_form_is_redacted(self) -> None:
        result = memory_service.update_memory_context_file(
            "ws-1", "PROCEDURES.md",
            "# Procedures\n\nStaging DB creds: user=admin password=Sup3rSecretPass!\n",
            reason="memory_update",
        )
        self.assertTrue(result["redacted"])
        content = workspace_context.read_workspace_context_file("PROCEDURES.md", workspace_id="ws-1")
        self.assertNotIn("Sup3rSecretPass!", content)

    def test_clean_replace_is_unchanged(self) -> None:
        content = "# Procedures\n\n- Ship the memory wave.\n"
        result = memory_service.update_memory_context_file(
            "ws-1", "PROCEDURES.md", content, reason="memory_update",
        )
        self.assertFalse(result["redacted"])
        self.assertEqual(workspace_context.read_workspace_context_file("PROCEDURES.md", workspace_id="ws-1"), content)


# ── 3. Composition: redaction + provenance + caps + index ──────────────────


class RedactionComposesWithProvenanceCapsAndIndexTests(_LiveMemoryTestBase):
    def _brother_source(self) -> dict:
        return {
            "platform": "whatsapp_personal", "surface": "dm",
            "sender_id": "brother-1", "sender_name": "Karim", "sender_is_owner": False,
        }

    def test_redaction_composes_with_the_provenance_marker(self) -> None:
        memory_service.memory_write_file(
            "ws-1", "MEMORY.md",
            "says the shared account key is sk-abcdefghijklmnopqrstuvwxyz1234567890ABCD",
            mode="append", reason="memory_write", source=self._brother_source(),
            attribution_reason="Karim shared what he believes is a shared account key.",
        )
        content = self._memory_md()
        self.assertIn("Karim", content)
        self.assertIn("not owner", content)
        self.assertNotIn("sk-abcdefghijklmnopqrstuvwxyz1234567890ABCD", content)
        self.assertIn("[redacted-secret]", content)

    def test_attribution_gate_still_blocks_even_when_content_has_a_secret(self) -> None:
        """Redaction must never become a backdoor around the write filter --
        a non-owner fact still requires attribution_reason, secret or not."""
        with self.assertRaises(ValueError):
            memory_service.memory_write_file(
                "ws-1", "MEMORY.md",
                "says the shared password is Sup3rSecretPass!",
                mode="append", reason="memory_write", source=self._brother_source(),
            )
        self.assertNotIn("Sup3rSecretPass", self._memory_md())
        self.assertNotIn("shared password", self._memory_md())

    def test_redaction_runs_before_the_index_cap_check(self) -> None:
        """Raw content well over the 25KB cap, but whose secret span
        collapses to a short placeholder under redaction, must be ACCEPTED
        -- proving redaction runs before the cap check (the cap must see
        what actually lands on disk, not the pre-redaction size)."""
        long_fake_key = "sk-" + ("A" * 30_000)
        raw = f"# Curated Memory\n\nAPI key on file: {long_fake_key}\n"
        self.assertGreater(len(raw.encode("utf-8")), memory_service.MEMORY_MD_INDEX_MAX_BYTES)

        result = memory_service.update_memory_context_file(
            "ws-1", "MEMORY.md", raw, reason="memory_update",
        )
        self.assertTrue(result["redacted"])
        content = self._memory_md()
        self.assertIn("[redacted-secret]", content)
        self.assertLess(len(content.encode("utf-8")), memory_service.MEMORY_MD_INDEX_MAX_BYTES)

    def test_oversized_content_that_is_not_a_secret_still_hits_the_cap(self) -> None:
        """Regression: redaction must not accidentally shrink or otherwise
        alter non-secret-like content -- a genuinely oversized, ordinary
        write is still refused exactly as before this fix."""
        oversized = "x" * (memory_service.MEMORY_MD_INDEX_MAX_BYTES + 500)
        with pytest.raises(ValueError, match="self-curation cap"):
            memory_service.memory_write_file(
                "ws-1", "MEMORY.md", oversized, mode="append", reason="memory_write",
            )
        self.assertNotIn("x" * 100, self._memory_md())

    def test_redaction_composes_with_the_topic_file_auto_index_upsert(self) -> None:
        result = memory_service.memory_write_file(
            "ws-1", "memory/files/widgetco.md",
            "Acme portal access uses password=Sup3rSecretPass! -- rotate quarterly.",
            mode="replace", reason="memory_write",
            description="Acme account: portal credentials note.",
        )
        self.assertTrue(result["redacted"])
        topic_content = workspace_context.read_workspace_context_file("memory/files/widgetco.md", workspace_id="ws-1")
        self.assertNotIn("Sup3rSecretPass!", topic_content)

        index = self._memory_md()
        self.assertIn("widgetco.md", index)
        self.assertIn("Acme account: portal credentials note.", index)


# ── 4. Daily-note redactor upgrade: broader than the old 3-pattern set ─────


class DailyNoteRedactionUpgradeTests(_LiveMemoryTestBase):
    def test_daily_note_now_redacts_a_card_number_the_old_patterns_missed(self) -> None:
        """The pre-fix _redact_daily_note_payload only matched sk-/
        api_key=|token=|secret=|password=/Authorization: Bearer -- a card
        number sailed straight through. It's now layered with the shared
        secret_redaction_service.redact_text, which catches it."""
        with patch.object(memory_service, "_append_note_now", return_value=_today()):
            memory_service.memory_append_daily_note(
                "ws-1",
                "Decision: customer's card on file is 4111 1111 1111 1111 for auto-billing.",
            )
        content = workspace_context.read_workspace_context_file(f"memory/{_today()}.md", workspace_id="ws-1")
        self.assertNotIn("4111 1111 1111 1111", content)

    def test_daily_note_still_redacts_the_original_three_patterns(self) -> None:
        """Regression: the pre-existing sk-/api_key=/Bearer protection this
        function already had must still work, unchanged, layered under the
        new shared redactor."""
        with patch.object(memory_service, "_append_note_now", return_value=_today()):
            memory_service.memory_append_daily_note(
                "ws-1",
                "Decision: keep runtime placement separate from computer automation; api_key=sk-secret-value.",
            )
        content = workspace_context.read_workspace_context_file(f"memory/{_today()}.md", workspace_id="ws-1")
        self.assertIn("[redacted-secret]", content)
        self.assertNotIn("sk-secret-value", content)


if __name__ == "__main__":
    unittest.main()
