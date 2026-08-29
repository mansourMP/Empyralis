"""agent_job_skills — the job-seeded procedure library, and its two contracts.

Two things can go silently wrong here and neither shows up at runtime:

  1. A body drifts past a cap fleet_tools enforces. `_clean_skill_record`
     TRUNCATES on read rather than raising, so an over-long body becomes a
     procedure whose last step is missing, in production, with nothing said.
     Every cap below is read FROM fleet_tools, never restated — a restated
     number is a second opinion that agrees until someone changes one of them.

  2. The seed stops reaching the agent. It is delivered by a chain nobody
     re-reads (fleet_create_agent -> install_metadata.skills ->
     resolve_agent_skills -> build_skills_plugin_dir -> a real SKILL.md), and
     every link already exists for owner-authored skills, so a break in the
     seeding link alone is invisible to every other test in this repo. The
     end-to-end test here drives the REAL bridge function and reads the real
     file off disk rather than asserting on the dict in the middle.

The installer half of the contract — that every command these bodies name is
actually installed on the box — is asserted in test_agent_computer_toolchain
.py, beside the installer it reads.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import unittest
from unittest.mock import AsyncMock, patch

from server_modules import agent_job_skills, claude_agent_sdk_bridge, fleet_tools


class JobSkillShapeTests(unittest.TestCase):
    """Every authored record is storable and deliverable exactly as written."""

    def test_every_body_survives_fleet_tools_caps_untruncated(self) -> None:
        for job_id, entries in agent_job_skills.JOB_SKILLS.items():
            for entry in entries:
                with self.subTest(job=job_id, skill=entry["name"]):
                    self.assertLessEqual(len(entry["name"]), fleet_tools._MAX_SKILL_NAME_CHARS)
                    self.assertLessEqual(
                        len(entry["description"]), fleet_tools._MAX_SKILL_DESCRIPTION_CHARS
                    )
                    self.assertLessEqual(len(entry["body"]), fleet_tools._MAX_SKILL_BODY_CHARS)

    def test_no_job_exceeds_the_per_agent_skill_ceiling(self) -> None:
        for job_id, entries in agent_job_skills.JOB_SKILLS.items():
            with self.subTest(job=job_id):
                self.assertLessEqual(len(entries), fleet_tools._MAX_SKILLS_PER_AGENT)

    def test_names_are_unique_within_a_job(self) -> None:
        # _normalize_skills_patch refuses a duplicate name outright, so a
        # seeded pair that collides would make the agent unsavable from the
        # Skills editor forever after — a create that quietly poisons a later
        # save is worse than one that fails now.
        for job_id, entries in agent_job_skills.JOB_SKILLS.items():
            names = [e["name"].strip().lower() for e in entries]
            with self.subTest(job=job_id):
                self.assertEqual(len(names), len(set(names)))

    def test_a_seeded_library_would_pass_the_save_time_validator(self) -> None:
        """The create path deliberately does NOT run _normalize_skills_patch
        (see fleet_create_agent's comment). This asserts the thing that
        exemption relies on: the authored records would have passed it."""
        for job_id in agent_job_skills.job_ids_with_skills():
            clean, error = fleet_tools._normalize_skills_patch(
                agent_job_skills.seed_skills_for_job(job_id)
            )
            with self.subTest(job=job_id):
                self.assertEqual(error, "")
                self.assertIsNotNone(clean)

    def test_seed_returns_the_storage_shape_resolve_reads_back(self) -> None:
        seeded = agent_job_skills.seed_skills_for_job("bookkeeping")
        self.assertTrue(seeded)
        read_back = fleet_tools.resolve_agent_skills(
            {"install_metadata": {"skills": seeded}}, enabled_only=True
        )
        # enabled_only=True is what a turn uses. A seeded skill that arrives
        # disabled is a library nobody can see and nothing reports.
        self.assertEqual(len(read_back), len(seeded))
        self.assertEqual([s["name"] for s in read_back], [s["name"] for s in seeded])

    def test_ids_are_unique_per_install(self) -> None:
        first = agent_job_skills.seed_skills_for_job("bookkeeping")
        second = agent_job_skills.seed_skills_for_job("bookkeeping")
        self.assertEqual(len(set(s["id"] for s in first)), len(first))
        self.assertFalse(set(s["id"] for s in first) & set(s["id"] for s in second))

    def test_an_unknown_job_seeds_nothing_and_never_raises(self) -> None:
        for value in ("", "   ", "general", "nonsense", None):
            with self.subTest(job=value):
                self.assertEqual(agent_job_skills.seed_skills_for_job(value), [])  # type: ignore[arg-type]

    def test_job_ids_are_lowercase_so_the_lookup_can_normalise_into_them(self) -> None:
        for job_id in agent_job_skills.JOB_SKILLS:
            self.assertEqual(job_id, job_id.strip().lower())
        self.assertEqual(
            [s["name"] for s in agent_job_skills.seed_skills_for_job("  BOOKKEEPING ")],
            [s["name"] for s in agent_job_skills.seed_skills_for_job("bookkeeping")],
        )


class BookkeepingProcedureContentTests(unittest.TestCase):
    """The two claims the whole feature rests on, asserted as content.

    Content assertions are usually a smell. These two are not: they are the
    product promise ("it reads the digits, it does not guess them") and the
    house honesty law applied to the one place a confident wrong number costs
    somebody money. A rewrite that loses either has lost the feature while
    leaving every structural test green.
    """

    def _bookkeeping(self) -> dict:
        return {e["name"]: e for e in agent_job_skills.JOB_SKILLS["bookkeeping"]}

    def test_the_invoice_procedure_extracts_text_before_it_looks_at_pixels(self) -> None:
        body = self._bookkeeping()["Read an invoice or receipt"]["body"]
        self.assertIn("pdftotext -layout", body)
        # -layout is the whole reason columns survive; without it a quantity
        # and an amount arrive as one string.
        self.assertIn("`-layout` is not optional", body)

    def test_the_invoice_procedure_refuses_to_present_guessed_digits_as_read(self) -> None:
        body = self._bookkeeping()["Read an invoice or receipt"]["body"]
        lowered = body.lower()
        self.assertIn("no text layer", lowered)
        self.assertIn("not verified", lowered)

    def test_the_invoice_procedure_checks_its_own_arithmetic(self) -> None:
        body = self._bookkeeping()["Read an invoice or receipt"]["body"]
        self.assertIn("stated subtotal", body)
        self.assertIn("stated total", body)
        # The finding is the disagreement, never a silently-adopted winner.
        self.assertIn("do not fix it", body.lower())

    def test_the_reconciliation_procedure_never_adjusts_the_ledger_to_balance(self) -> None:
        body = self._bookkeeping()["Reconcile a statement against the ledger"]["body"]
        self.assertIn("Never adjust a ledger figure", body)

    def test_the_reconciliation_procedure_names_the_memory_ceiling(self) -> None:
        # The honest pairing with the exit-137 work: pandas loads the whole
        # file, duckdb does not, and a killed command is what the difference
        # looks like from the agent's seat.
        body = self._bookkeeping()["Reconcile a statement against the ledger"]["body"]
        self.assertIn("duckdb", body)
        self.assertIn("killed", body.lower())

    def test_chasing_drafts_but_never_sends(self) -> None:
        body = self._bookkeeping()["Chase what is overdue"]["body"]
        self.assertIn("do not send them", body.lower())

    def test_descriptions_name_the_work_and_never_the_mechanism(self) -> None:
        """A description is the model's only basis for opening a body, and it
        is also customer-visible copy in the Skills editor. Naming a binary
        there is the 'labels, does not lecture' law broken in the one field
        that has to survive a tool change."""
        mechanism = ("pdftotext", "pandas", "duckdb", "python", "poppler", "openpyxl", "pypdf")
        for job_id, entries in agent_job_skills.JOB_SKILLS.items():
            for entry in entries:
                lowered = entry["description"].lower()
                for token in mechanism:
                    with self.subTest(job=job_id, skill=entry["name"], token=token):
                        self.assertNotIn(token, lowered)


class SeedReachesTheTurnTests(unittest.TestCase):
    """The create -> storage -> turn chain, driven end to end.

    Asserting that fleet_create_agent put a key in a dict would prove the
    first link and nothing else. This drives the REAL delivery function and
    reads the REAL SKILL.md off disk, because "built and never wired" is this
    codebase's most common defect and every link but one already existed.
    """

    def test_a_bookkeeping_create_writes_skills_into_install_metadata(self) -> None:
        captured: dict = {}

        async def _fake_create_install(**kwargs):
            captured.update(kwargs)
            return {"id": "ainstall_test"}

        with patch.object(
            fleet_tools, "_PURPOSE_PRESET_INSTRUCTIONS", fleet_tools._PURPOSE_PRESET_INSTRUCTIONS
        ):
            with patch("server_modules.agent_registry_repository.ensure_workspace_agent_registry_seeded", new=AsyncMock(return_value=None)), \
                 patch("server_modules.agent_registry_repository.list_agent_definitions", new=AsyncMock(return_value=[{"id": "def_1", "slug": "fleet-specialist"}])), \
                 patch("server_modules.agent_registry_repository.create_workspace_agent_install", new=AsyncMock(side_effect=_fake_create_install)), \
                 patch("server_modules.projects_repository.create_project", new=AsyncMock(return_value={"id": "proj_1"})), \
                 patch("server_modules.projects_repository.assign_install_to_project", new=AsyncMock(return_value=True)):
                asyncio.run(
                    fleet_tools.fleet_create_agent(
                        actor_id="owner",
                        workspace_id="ws_1",
                        tenant_id="t_1",
                        name="Books",
                        purpose_preset="internal_assistant",
                        capability_preset="standard",
                        job="bookkeeping",
                        project_id="proj_1",
                    )
                )

        skills = (captured.get("metadata") or {}).get("skills") or []
        self.assertEqual(len(skills), len(agent_job_skills.JOB_SKILLS["bookkeeping"]))
        self.assertTrue(all(s["enabled"] for s in skills))

    def test_a_general_create_writes_no_skills_key_at_all(self) -> None:
        """Absent, not empty. An empty list is a claim that this agent has a
        library and it is empty; absent is the pre-jobs agent unchanged."""
        captured: dict = {}

        async def _fake_create_install(**kwargs):
            captured.update(kwargs)
            return {"id": "ainstall_test"}

        with patch("server_modules.agent_registry_repository.ensure_workspace_agent_registry_seeded", new=AsyncMock(return_value=None)), \
             patch("server_modules.agent_registry_repository.list_agent_definitions", new=AsyncMock(return_value=[{"id": "def_1", "slug": "fleet-specialist"}])), \
             patch("server_modules.agent_registry_repository.create_workspace_agent_install", new=AsyncMock(side_effect=_fake_create_install)), \
             patch("server_modules.projects_repository.create_project", new=AsyncMock(return_value={"id": "proj_1"})), \
             patch("server_modules.projects_repository.assign_install_to_project", new=AsyncMock(return_value=True)):
            asyncio.run(
                fleet_tools.fleet_create_agent(
                    actor_id="owner",
                    workspace_id="ws_1",
                    tenant_id="t_1",
                    name="Anything",
                    capability_preset="standard",
                    job="general",
                    project_id="proj_1",
                )
            )

        self.assertNotIn("skills", captured.get("metadata") or {})

    def test_a_seeded_skill_becomes_a_real_skill_md_the_engine_can_deliver(self) -> None:
        install = {
            "install_metadata": {"skills": agent_job_skills.seed_skills_for_job("bookkeeping")}
        }
        skills = fleet_tools.resolve_agent_skills(install, enabled_only=True)
        plugin_dir = claude_agent_sdk_bridge.build_skills_plugin_dir(skills)
        self.assertTrue(plugin_dir, "a bookkeeping agent must deliver a real plugin dir")
        try:
            skill_root = os.path.join(plugin_dir, "skills")
            written = sorted(os.listdir(skill_root))
            self.assertEqual(len(written), len(skills))
            for slug in written:
                path = os.path.join(skill_root, slug, "SKILL.md")
                self.assertTrue(os.path.isfile(path))
                content = open(path, encoding="utf-8").read()
                self.assertTrue(content.startswith("---\n"))
                self.assertIn("description:", content)
            # The flagship claim, asserted at the far end of the chain rather
            # than at the source: the instruction that separates reading from
            # guessing is in a file the CLI will actually load.
            all_text = "\n".join(
                open(os.path.join(skill_root, slug, "SKILL.md"), encoding="utf-8").read()
                for slug in written
            )
            self.assertIn("pdftotext -layout", all_text)
        finally:
            shutil.rmtree(plugin_dir, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
