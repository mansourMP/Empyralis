"""`attach_label` has always written `project_task_labels.added_by`, and
nothing anywhere read it back.

Every caller that bothers to pass an actor was writing into a column no reader
could reach: `routes_fleet.py`'s authenticated human (`current_user.user_id`),
`skills_service.py`'s calling agent (`_caller_agent_id`), and the MCP tool's
external-agent roster id. Written-and-never-read is not a smaller bug than
not-written -- it looks exactly like working attribution right up to the moment
somebody asks who put this chip on this card, which is the only moment it is
ever needed.

The fix is to READ it, not to stop writing it: the column is already populated
in production, so surfacing it recovers real history, while deleting the write
would throw that history away to make an audit note go quiet.

Two shapes are asserted, from different sources:
  * the SQL genuinely projects the LINK row's columns (a source-level check --
    the naming trap below is invisible to a behavioural test), and
  * the returned dict carries them (driven through the real function against a
    fake pool, so the mapping is exercised rather than described).
"""

from __future__ import annotations

import asyncio
import inspect
import unittest
from unittest.mock import patch

from server_modules import workspace_labels_service as labels


class LabelAttributionSqlShapeTests(unittest.TestCase):

    def test_list_task_labels_projects_the_link_rows_added_by(self):
        source = inspect.getsource(labels.list_task_labels)
        self.assertIn("tl.added_by", source)

    def test_the_link_timestamp_is_aliased_apart_from_the_labels_own(self):
        """`workspace_labels.created_at` (when the label was invented) and
        `project_task_labels.created_at` (when it was put on THIS task) are
        different facts. Projecting the link's column without an alias would
        silently overwrite the label's own in the row mapping -- the same
        column name answering a different question, with nothing to notice
        it."""
        source = inspect.getsource(labels.list_task_labels)
        self.assertIn("tl.created_at AS added_at", source)
        self.assertNotIn("tl.created_at,", source)


class LabelAttributionIsReturnedTests(unittest.TestCase):
    """Drives the real `list_task_labels` against a fake pool so the row->dict
    mapping is exercised, not restated."""

    def _run_with_rows(self, rows):
        class _FakePool:
            pass

        async def _fake_fetch(pool, sql, *args, **kwargs):
            return rows

        async def _fake_pool(*args, **kwargs):
            return _FakePool()

        with patch.object(
            labels.control_plane_repository, "ensure_control_plane_schema",
            side_effect=_fake_pool,
        ), patch.object(
            labels.control_plane_repository, "rls_fetch", side_effect=_fake_fetch,
        ):
            return asyncio.run(labels.list_task_labels(
                tenant_id="t1", workspace_id="w1", task_id="task_1",
            ))

    def test_added_by_and_added_at_reach_the_caller(self):
        result = self._run_with_rows([{
            "id": "lbl_1", "tenant_id": "t1", "workspace_id": "w1",
            "name": "bug", "color": "#ff0000",
            "created_by": "user_who_invented_the_label",
            "created_at": "2026-01-01T00:00:00+00:00",
            "updated_at": "2026-01-01T00:00:00+00:00",
            "added_by": "ext_agent_abc123",
            "added_at": "2026-08-18T12:00:00+00:00",
        }])
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["added_by"], "ext_agent_abc123")
        self.assertEqual(result[0]["added_at"], "2026-08-18T12:00:00+00:00")

    def test_the_labels_own_author_and_birthday_are_not_clobbered(self):
        """The whole point of the separate names: a caller must still be able
        to ask who INVENTED the label, not only who attached it."""
        result = self._run_with_rows([{
            "id": "lbl_1", "tenant_id": "t1", "workspace_id": "w1",
            "name": "bug", "color": "#ff0000",
            "created_by": "user_who_invented_the_label",
            "created_at": "2026-01-01T00:00:00+00:00",
            "updated_at": "2026-01-01T00:00:00+00:00",
            "added_by": "ext_agent_abc123",
            "added_at": "2026-08-18T12:00:00+00:00",
        }])
        self.assertEqual(result[0]["created_by"], "user_who_invented_the_label")
        self.assertEqual(result[0]["created_at"], "2026-01-01T00:00:00+00:00")

    def test_an_unattributed_link_reports_none_rather_than_an_empty_string(self):
        """Rows written before any caller passed an actor carry NULL. "Nobody
        recorded who did this" and "somebody with a blank name" are different
        facts; only None can say the first."""
        result = self._run_with_rows([{
            "id": "lbl_1", "tenant_id": "t1", "workspace_id": "w1",
            "name": "bug", "color": "#ff0000",
            "created_by": None, "created_at": None, "updated_at": None,
            "added_by": None, "added_at": None,
        }])
        self.assertIsNone(result[0]["added_by"])
        self.assertIsNone(result[0]["added_at"])


if __name__ == "__main__":
    unittest.main()
