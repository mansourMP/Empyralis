"""MAN-358 — the one URL builder, and what it must refuse to build.

The headline assertion in this file is a NEGATIVE one: a deployment that has
not declared its public origin gets NO link. That is the whole difference
between an agent saying "I created GEN-12" with a tappable address and one
handing a person `None/w/.../tasks/...` or a localhost URL they cannot open.

The route templates are checked against the REAL Next.js route tree rather
than against a copy of themselves — expected set and actual set from
different places, per this codebase's own record of a conformance check that
parsed the file it was verifying and could therefore only confirm itself.
"""

from __future__ import annotations

import os
import unittest
from pathlib import Path

from server_modules import deep_link_service as deep_links
from server_modules import secret_redaction_service


PROD_ENV = {
    "EMPYRALIS_DEPLOY_ENV": "production",
    "EMPYRALIS_PUBLIC_FRONTEND_ORIGIN": "https://empyralis.ai",
}
SELF_HOSTED_ENV = {
    # Production today reports itself as `self-hosted`, which is NOT one of
    # cloud_cutover_config's CLOUD_ENVIRONMENTS — so this is the env shape the
    # real deployment actually has, and the one most likely to regress.
    "EMPYRALIS_DEPLOY_ENV": "self-hosted",
    "EMPYRALIS_PUBLIC_FRONTEND_ORIGIN": "https://empyralis.ai/",
}
NO_ORIGIN_ENV = {"EMPYRALIS_DEPLOY_ENV": "self-hosted"}
DEV_NO_ORIGIN_ENV: dict[str, str] = {}


class DeepLinkOriginTests(unittest.TestCase):
    def test_configured_origin_builds_an_absolute_url(self) -> None:
        self.assertEqual(
            deep_links.build_task_url(
                workspace_id="ws_1", project_id="proj_1", task_id="task_1", env=PROD_ENV,
            ),
            "https://empyralis.ai/w/ws_1/projects/proj_1/tasks/task_1",
        )

    def test_trailing_slash_on_the_origin_does_not_double(self) -> None:
        self.assertEqual(
            deep_links.build_document_url(
                workspace_id="ws_1", project_id="proj_1", document_id="doc_1", env=SELF_HOSTED_ENV,
            ),
            "https://empyralis.ai/w/ws_1/projects/proj_1/documents/doc_1",
        )

    def test_no_configured_origin_emits_no_link_at_all(self) -> None:
        """THE case this module exists for. Not a placeholder, not a relative
        path, not a localhost URL — nothing."""
        for env in (NO_ORIGIN_ENV, DEV_NO_ORIGIN_ENV):
            with self.subTest(env=env):
                self.assertEqual(deep_links.resolve_app_origin(env), "")
                self.assertEqual(
                    deep_links.build_task_url(
                        workspace_id="ws_1", project_id="proj_1", task_id="task_1", env=env,
                    ),
                    "",
                )
                self.assertEqual(
                    deep_links.build_document_url(
                        workspace_id="ws_1", project_id="proj_1", document_id="doc_1", env=env,
                    ),
                    "",
                )

    def test_the_shared_resolvers_dev_loopback_fallback_is_never_used(self) -> None:
        """cloud_cutover_config.resolve_public_frontend_origin defaults to
        http://127.0.0.1:3000 for a developer's browser. That value in a
        Telegram message is a link nobody who receives it can open, so this
        builder passes allow_dev_fallback=False. Asserted directly, because
        flipping it back is a one-word change that breaks nothing locally."""
        from server_modules.cloud_cutover_config import resolve_public_frontend_origin

        self.assertEqual(resolve_public_frontend_origin({}), "http://127.0.0.1:3000")
        self.assertEqual(deep_links.resolve_app_origin({}), "")

    def test_a_missing_id_emits_no_link_rather_than_a_list_page(self) -> None:
        for kwargs in (
            {"workspace_id": "", "project_id": "proj_1", "task_id": "task_1"},
            {"workspace_id": "ws_1", "project_id": "", "task_id": "task_1"},
            {"workspace_id": "ws_1", "project_id": "proj_1", "task_id": ""},
            {"workspace_id": "ws_1", "project_id": None, "task_id": "task_1"},
        ):
            with self.subTest(**kwargs):
                self.assertEqual(deep_links.build_task_url(env=PROD_ENV, **kwargs), "")

    def test_ids_are_percent_encoded(self) -> None:
        url = deep_links.build_task_url(
            workspace_id="ws /1", project_id="proj_1", task_id="task_1", env=PROD_ENV,
        )
        self.assertEqual(url, "https://empyralis.ai/w/ws%20%2F1/projects/proj_1/tasks/task_1")

    def test_reads_the_process_environment_when_none_is_passed(self) -> None:
        previous = dict(os.environ)
        try:
            os.environ.pop("EMPYRALIS_PUBLIC_FRONTEND_ORIGIN", None)
            os.environ.pop("FRONTEND_PUBLIC_ORIGIN", None)
            os.environ.pop("FRONTEND_ORIGINS", None)
            os.environ["EMPYRALIS_DEPLOY_ENV"] = "self-hosted"
            self.assertEqual(deep_links.resolve_app_origin(), "")
            os.environ["EMPYRALIS_PUBLIC_FRONTEND_ORIGIN"] = "https://empyralis.ai"
            self.assertEqual(deep_links.resolve_app_origin(), "https://empyralis.ai")
        finally:
            os.environ.clear()
            os.environ.update(previous)


class TaskDisplayIdTests(unittest.TestCase):
    def test_real_identifier(self) -> None:
        self.assertEqual(
            deep_links.task_display_id({"number": 12, "project_task_key": "GEN"}), "GEN-12",
        )

    def test_no_identifier_is_empty_not_a_uuid_fragment(self) -> None:
        """The frontend's taskDisplayId falls back to a hex slice of the uuid
        so a table cell is never blank. Here the value goes into a sentence an
        agent writes to a person, and "task 69D656" is a uuid fragment dressed
        up as an identifier — say nothing instead."""
        for task in (
            {"number": None, "project_task_key": "GEN", "id": "task_69d6567ebfd3485a"},
            {"number": 12, "project_task_key": "", "id": "task_69d6567ebfd3485a"},
            {"id": "task_69d6567ebfd3485a"},
        ):
            with self.subTest(task=task):
                self.assertEqual(deep_links.task_display_id(task), "")


class AnnotateTests(unittest.TestCase):
    def test_annotate_task_adds_url_and_display_id_without_mutating(self) -> None:
        task = {"id": "task_1", "project_id": "proj_1", "number": 12, "project_task_key": "GEN"}
        enriched = deep_links.annotate_task(task, workspace_id="ws_1", env=PROD_ENV)
        self.assertEqual(enriched["url"], "https://empyralis.ai/w/ws_1/projects/proj_1/tasks/task_1")
        self.assertEqual(enriched["display_id"], "GEN-12")
        self.assertNotIn("url", task)
        self.assertNotIn("display_id", task)

    def test_annotate_omits_the_key_entirely_rather_than_emitting_an_empty_one(self) -> None:
        """An empty-string `url` is worse than an absent one: a model reading
        the result can interpolate it into a sentence and produce a broken
        link, which is exactly what the no-origin case must never do."""
        enriched = deep_links.annotate_task(
            {"id": "task_1", "project_id": "proj_1", "number": 12, "project_task_key": "GEN"},
            workspace_id="ws_1",
            env=NO_ORIGIN_ENV,
        )
        self.assertNotIn("url", enriched)
        self.assertEqual(enriched["display_id"], "GEN-12")

    def test_annotate_document(self) -> None:
        enriched = deep_links.annotate_document(
            {"id": "doc_1", "project_id": "proj_1", "title": "Spec"},
            workspace_id="ws_1",
            env=PROD_ENV,
        )
        self.assertEqual(enriched["url"], "https://empyralis.ai/w/ws_1/projects/proj_1/documents/doc_1")

    def test_annotate_passes_non_dicts_through(self) -> None:
        self.assertIsNone(deep_links.annotate_task(None, workspace_id="ws_1", env=PROD_ENV))
        self.assertIsNone(deep_links.annotate_document(None, workspace_id="ws_1", env=PROD_ENV))


class RedactionSurvivalTests(unittest.TestCase):
    """secret_redaction_service.redact_text runs on every visible reply and on
    every persisted assistant turn. A link the redactor eats is a link the
    person never receives, and the failure is silent — the same shape that
    once removed 17 of 72 tool names from the system prompt."""

    def test_a_real_deep_link_survives_redaction_intact(self) -> None:
        for url in (
            deep_links.build_task_url(
                workspace_id="ws_9f2c1a4b7d8e",
                project_id="proj_4c8a1f2e9b3d",
                task_id="task_69d6567ebfd3485a",
                env=PROD_ENV,
            ),
            deep_links.build_document_url(
                workspace_id="ws_9f2c1a4b7d8e",
                project_id="proj_4c8a1f2e9b3d",
                document_id="doc_1a2b3c4d5e6f7081",
                env=PROD_ENV,
            ),
        ):
            with self.subTest(url=url):
                self.assertTrue(url)
                self.assertEqual(secret_redaction_service.redact_text(url), url)
                sentence = f"I created GEN-12 for you: {url}"
                self.assertEqual(secret_redaction_service.redact_text(sentence), sentence)

    def test_a_dotless_host_is_eaten_which_is_a_second_reason_for_no_loopback(self) -> None:
        """Measured, not assumed. The redactor's URL-ish rescue requires a dot
        in the host, so `http://localhost:3000/w/...` is swallowed whole by
        the high-entropy sweep. Pinned here so nobody "helpfully" restores the
        loopback fallback and ships links that arrive as
        `http://localhost:[redacted-secret]`."""
        loopback = "http://localhost:3000/w/ws_9f2c1a4b7d8e/projects/proj_4c8a1f2e9b3d/tasks/task_69d6567ebfd3485a"
        self.assertIn("[redacted-secret]", secret_redaction_service.redact_text(loopback))


class RouteShapeDriftTests(unittest.TestCase):
    """The templates name a route in a codebase that cannot import them.

    Expected set: the real Next.js App Router directory tree. Actual set: the
    constants in deep_link_service. Two independent sources — a template that
    stops naming a real page produces a 404 in a channel message with nothing
    on the Python side to notice.
    """

    ROUTES_ROOT = Path(__file__).resolve().parents[2] / "frontend" / "app" / "(account)"

    def _route_dir_for(self, template: str) -> Path:
        # /w/{workspace_id}/projects/{project_id}/tasks/{task_id}
        #   -> w/[workspaceId]/projects/[projectId]/tasks/[taskId]
        segment_names = {
            "workspace_id": "[workspaceId]",
            "project_id": "[projectId]",
            "task_id": "[taskId]",
            "document_id": "[documentId]",
        }
        parts = []
        for segment in template.strip("/").split("/"):
            if segment.startswith("{") and segment.endswith("}"):
                key = segment[1:-1]
                self.assertIn(key, segment_names, f"unmapped template placeholder {segment}")
                parts.append(segment_names[key])
            else:
                parts.append(segment)
        return self.ROUTES_ROOT.joinpath(*parts)

    def test_the_route_tree_is_actually_where_this_test_thinks(self) -> None:
        """Canary. Without it, a moved frontend directory makes every
        assertion below vacuous and the suite reports green."""
        self.assertTrue(
            self.ROUTES_ROOT.is_dir(),
            f"account route root not found at {self.ROUTES_ROOT} — this scan is not looking where it thinks",
        )
        self.assertTrue(
            (self.ROUTES_ROOT / "w" / "[workspaceId]" / "page.tsx").is_file(),
            "the workspace root page is missing — the route-naming convention this test assumes has changed",
        )

    def test_every_template_names_a_real_page(self) -> None:
        for template in (
            deep_links.WORKSPACE_PATH_TEMPLATE,
            deep_links.PROJECT_PATH_TEMPLATE,
            deep_links.TASK_PATH_TEMPLATE,
            deep_links.DOCUMENT_PATH_TEMPLATE,
        ):
            with self.subTest(template=template):
                page = self._route_dir_for(template) / "page.tsx"
                self.assertTrue(page.is_file(), f"{template} does not name a real Next.js page ({page})")

    def test_no_template_is_a_redirect_source_in_next_config(self) -> None:
        """A next.config redirect resolves AHEAD of the router, so a rule
        pointing at one of these paths would make every deep link land
        somewhere else with no React code able to say so — the LEGACY_REDIRECTS
        trap this codebase already paid for once."""
        config = (Path(__file__).resolve().parents[2] / "frontend" / "next.config.ts").read_text(encoding="utf-8")
        self.assertGreater(len(config), 200, "next.config.ts scan found no content — canary")
        for template in (deep_links.TASK_PATH_TEMPLATE, deep_links.DOCUMENT_PATH_TEMPLATE):
            colon_form = (
                template.replace("{workspace_id}", ":workspaceId")
                .replace("{project_id}", ":projectId")
                .replace("{task_id}", ":taskId")
                .replace("{document_id}", ":documentId")
            )
            self.assertNotIn(f"source: '{colon_form}'", config)
            self.assertNotIn(f'source: "{colon_form}"', config)


if __name__ == "__main__":
    unittest.main()
