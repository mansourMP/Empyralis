"""Tests for task_mention_service.py (MAN-66, "@agent mention parser").

Three layers, tested separately per the module's own split:
  1. Parsing/resolution -- pure, no I/O (find_mention_candidates /
     resolve_mention_candidates). Covers the required cases from the brief:
     unknown names, ambiguous names, names with spaces/punctuation, and an
     `@` that isn't a mention (email addresses, code snippets).
  2. Roster loading -- workspace-scoping (the hard security requirement: a
     mention must never resolve across workspace boundaries).
  3. Dispatch -- bounding, self-mention safety, and "a human is notified,
     never woken."

Also exercises the real, shared `project_tasks_service.add_task_comment`
path end to end, proving mentions ride on the SAME comment write for every
author kind (human, agent, external_agent, system) with no second write.
"""

from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, Mock, patch

from server_modules import project_tasks_service
from server_modules import task_mention_service


# ── 1. Parsing (pure) ────────────────────────────────────────────────────


class FindMentionCandidatesTests(unittest.TestCase):
    def test_simple_mention_at_start(self):
        candidates = task_mention_service.find_mention_candidates("@Atlas can you check this?")
        self.assertEqual(len(candidates), 1)
        start, end, run = candidates[0]
        self.assertEqual(start, 0)
        self.assertTrue(run.startswith("Atlas"))

    def test_mention_after_punctuation(self):
        candidates = task_mention_service.find_mention_candidates("(@Atlas) thanks")
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0][0], 1)

    def test_email_address_is_not_a_mention(self):
        candidates = task_mention_service.find_mention_candidates("reach me at jane@example.com please")
        self.assertEqual(candidates, [])

    def test_at_inside_inline_code_span_is_skipped(self):
        candidates = task_mention_service.find_mention_candidates(
            "printf uses `@decorator` syntax, not a mention"
        )
        self.assertEqual(candidates, [])

    def test_bare_at_with_no_name_characters_is_not_a_candidate(self):
        candidates = task_mention_service.find_mention_candidates("cost is $5 @ checkout")
        self.assertEqual(candidates, [])

    def test_multiple_mentions_in_one_comment(self):
        candidates = task_mention_service.find_mention_candidates("@Bob Smith and @Atlas, please sync up")
        self.assertEqual(len(candidates), 2)


class ResolveMentionCandidatesTests(unittest.TestCase):
    ROSTER = [
        {"kind": "agent", "id": "agent-1", "display_name": "Atlas"},
        {"kind": "agent", "id": "agent-2", "display_name": "Jane Doe"},
        {"kind": "user", "id": "user-1", "display_name": "Jane"},
        {"kind": "user", "id": "user-2", "display_name": "Bob Smith"},
    ]

    def _resolve(self, text: str):
        candidates = task_mention_service.find_mention_candidates(text)
        return task_mention_service.resolve_mention_candidates(candidates, roster=self.ROSTER)

    def test_resolves_single_word_agent_name(self):
        resolved = self._resolve("@Atlas can you check this?")
        self.assertEqual(len(resolved), 1)
        self.assertEqual(resolved[0]["kind"], "agent")
        self.assertEqual(resolved[0]["id"], "agent-1")
        self.assertEqual(resolved[0]["raw"], "@Atlas")

    def test_resolves_multi_word_name_greedily_not_the_shorter_prefix(self):
        """'Jane Doe' (agent, 2 words) must win over the distinct 'Jane'
        (human, 1 word) roster entry -- longest-prefix-match, not
        first-word-match."""
        resolved = self._resolve("@Jane Doe, please take a look")
        self.assertEqual(len(resolved), 1)
        self.assertEqual(resolved[0]["kind"], "agent")
        self.assertEqual(resolved[0]["id"], "agent-2")
        self.assertEqual(resolved[0]["raw"], "@Jane Doe")

    def test_falls_back_to_shorter_name_when_longer_run_matches_nothing(self):
        resolved = self._resolve("@Jane are you free later")
        self.assertEqual(len(resolved), 1)
        self.assertEqual(resolved[0]["kind"], "user")
        self.assertEqual(resolved[0]["id"], "user-1")

    def test_unknown_name_left_unresolved(self):
        """Never fabricate a target: an unrecognized name resolves to
        nothing at all (the caller leaves the raw @text untouched)."""
        resolved = self._resolve("@nonexistent-agent please help")
        self.assertEqual(resolved, [])

    def test_ambiguous_name_across_kinds_is_left_unresolved(self):
        """Two roster entries (an agent and a human) sharing a display name
        must NOT silently resolve to either one -- documented ruling: a
        wrong-but-confident resolution is worse than plain text."""
        roster = [
            {"kind": "agent", "id": "agent-9", "display_name": "Casey"},
            {"kind": "user", "id": "user-9", "display_name": "Casey"},
        ]
        candidates = task_mention_service.find_mention_candidates("@Casey can you check this?")
        resolved = task_mention_service.resolve_mention_candidates(candidates, roster=roster)
        self.assertEqual(resolved, [])

    def test_ambiguous_at_longer_length_does_not_fall_through_to_shorter(self):
        """If 'Casey Jones' itself were ambiguous, this must NOT silently
        drop to a distinct, unrelated 'Casey' -- ambiguity is terminal for
        that occurrence, no fallback."""
        roster = [
            {"kind": "agent", "id": "agent-a", "display_name": "Casey Jones"},
            {"kind": "user", "id": "user-a", "display_name": "Casey Jones"},
            {"kind": "user", "id": "user-b", "display_name": "Casey"},
        ]
        candidates = task_mention_service.find_mention_candidates("@Casey Jones, thoughts?")
        resolved = task_mention_service.resolve_mention_candidates(candidates, roster=roster)
        self.assertEqual(resolved, [])

    def test_offsets_are_correct_for_rendering(self):
        resolved = self._resolve("hello @Atlas, ok?")
        self.assertEqual(len(resolved), 1)
        m = resolved[0]
        self.assertEqual(m["start"], 6)
        self.assertEqual(m["end"], 12)

    def test_punctuation_wrapped_mention_still_resolves(self):
        resolved = self._resolve("(@Atlas) thanks")
        self.assertEqual(len(resolved), 1)
        self.assertEqual(resolved[0]["id"], "agent-1")


# ── 2. Roster loading / workspace scoping ────────────────────────────────


class _ScopedFakePool:
    """A fake pool whose .fetch() results depend on the (tenant_id,
    workspace_id) bound params actually passed in -- a much stronger proof
    of workspace isolation than a pool that always returns the same rows
    regardless of scope. `agents_by_scope`/`members_by_scope` are keyed by
    (tenant_id, workspace_id)."""

    def __init__(self, *, agents_by_scope=None, members_by_scope=None):
        self.agents_by_scope = agents_by_scope or {}
        self.members_by_scope = members_by_scope or {}
        self.fetch_calls: list[tuple] = []

    async def fetch(self, query, *args):
        self.fetch_calls.append((query, args))
        tenant_id, workspace_id = args[0], args[1]
        if "workspace_agent_installs" in query:
            return self.agents_by_scope.get((tenant_id, workspace_id), [])
        if "workspace_memberships" in query:
            return self.members_by_scope.get((tenant_id, workspace_id), [])
        return []


class LoadMentionRosterScopingTests(unittest.IsolatedAsyncioTestCase):
    async def test_roster_is_scoped_to_the_callers_own_workspace(self):
        pool = _ScopedFakePool(
            agents_by_scope={
                ("tenant-1", "ws-1"): [{"id": "agent-1", "label": "Atlas"}],
                ("tenant-1", "ws-OTHER"): [{"id": "agent-evil", "label": "Atlas"}],
            },
        )
        roster = await task_mention_service.load_mention_roster(
            tenant_id="tenant-1", workspace_id="ws-1", pool=pool,
        )
        ids = {entry["id"] for entry in roster}
        self.assertIn("agent-1", ids)
        self.assertNotIn("agent-evil", ids)

    async def test_resolve_task_mentions_never_crosses_workspace_boundary(self):
        """SECURITY: the same display name ('Atlas') exists as a DIFFERENT
        agent in a different workspace. A mention in ws-1 must resolve to
        ws-1's agent, never ws-OTHER's -- proving resolution end-to-end,
        not just the roster query in isolation."""
        pool = _ScopedFakePool(
            agents_by_scope={
                ("tenant-1", "ws-1"): [{"id": "agent-1", "label": "Atlas"}],
                ("tenant-1", "ws-OTHER"): [{"id": "agent-evil", "label": "Atlas"}],
            },
        )
        resolved = await task_mention_service.resolve_task_mentions(
            tenant_id="tenant-1", workspace_id="ws-1", body="@Atlas please look", pool=pool,
        )
        self.assertEqual(len(resolved), 1)
        self.assertEqual(resolved[0]["id"], "agent-1")

        resolved_other = await task_mention_service.resolve_task_mentions(
            tenant_id="tenant-1", workspace_id="ws-OTHER", body="@Atlas please look", pool=pool,
        )
        self.assertEqual(len(resolved_other), 1)
        self.assertEqual(resolved_other[0]["id"], "agent-evil")

    async def test_empty_body_or_no_at_sign_never_touches_the_pool(self):
        pool = _ScopedFakePool()
        resolved = await task_mention_service.resolve_task_mentions(
            tenant_id="tenant-1", workspace_id="ws-1", body="no mentions here", pool=pool,
        )
        self.assertEqual(resolved, [])
        self.assertEqual(pool.fetch_calls, [])

    async def test_roster_load_failure_degrades_to_no_mentions_not_an_exception(self):
        class _BrokenPool:
            async def fetch(self, *_args, **_kwargs):
                raise RuntimeError("db unavailable")

        resolved = await task_mention_service.resolve_task_mentions(
            tenant_id="tenant-1", workspace_id="ws-1", body="@Atlas hi", pool=_BrokenPool(),
        )
        self.assertEqual(resolved, [])


# ── 3. Dispatch: bounding, self-mention safety, human-never-wakes ───────


def _mention(kind: str, entity_id: str, name: str) -> dict:
    return {"raw": f"@{name}", "start": 0, "end": len(name) + 1, "kind": kind, "id": entity_id, "display_name": name}


class DispatchResolvedMentionsTests(unittest.IsolatedAsyncioTestCase):
    async def test_mentioning_one_agent_wakes_it_exactly_once(self):
        wake_mock = AsyncMock(return_value={"id": "wake-1", "status": "pending"})
        with patch("server_modules.bounded_scheduler_service.schedule_task_commented_wakeup", new=wake_mock):
            result = await task_mention_service.dispatch_resolved_mentions(
                tenant_id="tenant-1", workspace_id="ws-1", task_id="task-1", task_title="Ship it",
                resolved_mentions=[_mention("agent", "agent-1", "Atlas")],
                author_type="human", author_id="user-1",
            )
        wake_mock.assert_awaited_once()
        self.assertEqual(wake_mock.await_args.kwargs["agent_id"], "agent-1")
        self.assertEqual(result["agent_wakes"], ["agent-1"])

    async def test_mentioning_three_agents_wakes_all_three(self):
        wake_mock = AsyncMock(return_value={"id": "wake-x", "status": "pending"})
        mentions = [_mention("agent", f"agent-{i}", f"Agent{i}") for i in range(3)]
        with patch("server_modules.bounded_scheduler_service.schedule_task_commented_wakeup", new=wake_mock):
            result = await task_mention_service.dispatch_resolved_mentions(
                tenant_id="tenant-1", workspace_id="ws-1", task_id="task-1", task_title="Ship it",
                resolved_mentions=mentions, author_type="human", author_id="user-1",
            )
        self.assertEqual(wake_mock.await_count, 3)
        self.assertEqual(len(result["agent_wakes"]), 3)

    async def test_mentioning_more_than_the_bound_wakes_only_up_to_the_bound(self):
        """The bounding decision under test: mentioning MORE agents than
        task_mention_service.max_mentioned_agent_wakes_per_comment() (3 by
        default) wakes only the first N -- one comment can never wake an
        unbounded number of agents."""
        wake_mock = AsyncMock(return_value={"id": "wake-x", "status": "pending"})
        cap = task_mention_service.max_mentioned_agent_wakes_per_comment()
        mentions = [_mention("agent", f"agent-{i}", f"Agent{i}") for i in range(cap + 2)]
        with patch("server_modules.bounded_scheduler_service.schedule_task_commented_wakeup", new=wake_mock):
            result = await task_mention_service.dispatch_resolved_mentions(
                tenant_id="tenant-1", workspace_id="ws-1", task_id="task-1", task_title="Ship it",
                resolved_mentions=mentions, author_type="human", author_id="user-1",
            )
        self.assertEqual(wake_mock.await_count, cap)
        self.assertEqual(len(result["agent_wakes"]), cap)

    async def test_mentioning_a_human_notifies_and_never_calls_the_scheduler(self):
        wake_mock = AsyncMock(side_effect=AssertionError("must never wake a human"))
        notify_mock = Mock()
        with (
            patch("server_modules.bounded_scheduler_service.schedule_task_commented_wakeup", new=wake_mock),
            patch("server_modules.outbox_service.emit_notification_event", new=notify_mock),
        ):
            result = await task_mention_service.dispatch_resolved_mentions(
                tenant_id="tenant-1", workspace_id="ws-1", task_id="task-1", task_title="Ship it",
                resolved_mentions=[_mention("user", "user-1", "Jane")],
                author_type="agent", author_id="agent-1",
            )
        wake_mock.assert_not_awaited()
        notify_mock.assert_called_once()
        self.assertEqual(notify_mock.call_args.kwargs["action"], "task_mention")
        self.assertEqual(notify_mock.call_args.kwargs["metadata"]["mentioned_user_id"], "user-1")
        self.assertEqual(result["notified_users"], ["user-1"])
        self.assertEqual(result["agent_wakes"], [])

    async def test_agent_mentioning_itself_schedules_zero_wakes(self):
        """The infinite-loop-risk case the brief calls out explicitly: an
        agent commenting on its own task and mentioning itself must not
        wake itself."""
        wake_mock = AsyncMock(side_effect=AssertionError("must never self-wake"))
        with patch("server_modules.bounded_scheduler_service.schedule_task_commented_wakeup", new=wake_mock):
            result = await task_mention_service.dispatch_resolved_mentions(
                tenant_id="tenant-1", workspace_id="ws-1", task_id="task-1", task_title="Ship it",
                resolved_mentions=[_mention("agent", "agent-1", "Atlas")],
                author_type="agent", author_id="agent-1",
            )
        wake_mock.assert_not_awaited()
        self.assertEqual(result["agent_wakes"], [])

    async def test_agent_self_mention_among_others_still_wakes_the_others(self):
        """Self-exclusion drops only the author's own identity -- mentioning
        itself AND a teammate in the same comment still wakes the teammate."""
        wake_mock = AsyncMock(return_value={"id": "wake-1", "status": "pending"})
        with patch("server_modules.bounded_scheduler_service.schedule_task_commented_wakeup", new=wake_mock):
            result = await task_mention_service.dispatch_resolved_mentions(
                tenant_id="tenant-1", workspace_id="ws-1", task_id="task-1", task_title="Ship it",
                resolved_mentions=[
                    _mention("agent", "agent-1", "Atlas"),
                    _mention("agent", "agent-2", "Otto"),
                ],
                author_type="agent", author_id="agent-1",
            )
        wake_mock.assert_awaited_once()
        self.assertEqual(wake_mock.await_args.kwargs["agent_id"], "agent-2")
        self.assertEqual(result["agent_wakes"], ["agent-2"])

    async def test_human_mentioning_self_sends_no_notification(self):
        notify_mock = Mock()
        with patch("server_modules.outbox_service.emit_notification_event", new=notify_mock):
            result = await task_mention_service.dispatch_resolved_mentions(
                tenant_id="tenant-1", workspace_id="ws-1", task_id="task-1", task_title="Ship it",
                resolved_mentions=[_mention("user", "user-1", "Jane")],
                author_type="human", author_id="user-1",
            )
        notify_mock.assert_not_called()
        self.assertEqual(result["notified_users"], [])

    async def test_duplicate_mentions_of_the_same_agent_only_wake_once(self):
        wake_mock = AsyncMock(return_value={"id": "wake-1", "status": "pending"})
        with patch("server_modules.bounded_scheduler_service.schedule_task_commented_wakeup", new=wake_mock):
            result = await task_mention_service.dispatch_resolved_mentions(
                tenant_id="tenant-1", workspace_id="ws-1", task_id="task-1", task_title="Ship it",
                resolved_mentions=[
                    _mention("agent", "agent-1", "Atlas"),
                    _mention("agent", "agent-1", "Atlas"),
                ],
                author_type="human", author_id="user-1",
            )
        wake_mock.assert_awaited_once()
        self.assertEqual(result["agent_wakes"], ["agent-1"])

    async def test_wake_failure_for_one_agent_does_not_block_the_others(self):
        async def flaky(*, agent_id, **_kwargs):
            if agent_id == "agent-1":
                raise RuntimeError("scheduler unavailable")
            return {"id": "wake-2", "status": "pending"}

        with patch("server_modules.bounded_scheduler_service.schedule_task_commented_wakeup", side_effect=flaky):
            result = await task_mention_service.dispatch_resolved_mentions(
                tenant_id="tenant-1", workspace_id="ws-1", task_id="task-1", task_title="Ship it",
                resolved_mentions=[
                    _mention("agent", "agent-1", "Atlas"),
                    _mention("agent", "agent-2", "Otto"),
                ],
                author_type="human", author_id="user-1",
            )
        self.assertEqual(result["agent_wakes"], ["agent-2"])
        self.assertEqual(len(result["wake_errors"]), 1)
        self.assertEqual(result["wake_errors"][0]["agent_id"], "agent-1")


# ── 4. End-to-end through project_tasks_service.add_task_comment ────────


class _QueuedFakePool:
    """Mirrors test_project_tasks.py's own _QueuedFakePool exactly."""

    def __init__(self, *, fetchrow_results=None, fetch_results=None):
        self._fetchrow_results = list(fetchrow_results or [])
        self._fetch_results = list(fetch_results or [])
        self.fetchrow_calls: list[tuple] = []
        self.fetch_calls: list[tuple] = []

    async def fetchrow(self, query, *args):
        self.fetchrow_calls.append((query, args))
        if not self._fetchrow_results:
            return None
        return self._fetchrow_results.pop(0)

    async def fetch(self, query, *args):
        self.fetch_calls.append((query, args))
        if not self._fetch_results:
            return []
        return self._fetch_results.pop(0)

    async def execute(self, query, *args):
        return "UPDATE 1"


def _task_row(**overrides) -> dict:
    row = {
        "id": "task-1",
        "tenant_id": "tenant-1",
        "workspace_id": "ws-1",
        "project_id": "proj-1",
        "title": "Ship the widget",
        "description": "Build and ship it.",
        "status": "todo",
        "priority": 0,
        "assignee_agent_id": None,
        "created_by": "user-1",
        "due_at": None,
        "plan": [],
        "metadata": {},
        "created_at": "2026-07-23T00:00:00Z",
        "updated_at": "2026-07-23T00:00:00Z",
    }
    row.update(overrides)
    return row


class AddTaskCommentMentionIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_mention_in_comment_is_stored_and_dispatched(self):
        """One write, no forking: add_task_comment (the function EVERY
        comment author -- human wrapper, agent tool, system -- ultimately
        calls) resolves + stores + dispatches mentions in the same call
        that persists the comment."""
        stored_comment_body = {
            "comments": [
                {
                    "author_type": "human",
                    "author_id": "user-1",
                    "body": "@Atlas can you take a look?",
                    "mentions": [
                        {"raw": "@Atlas", "start": 0, "end": 6, "kind": "agent", "id": "agent-1", "display_name": "Atlas"}
                    ],
                }
            ]
        }
        pool = _QueuedFakePool(
            fetch_results=[
                [{"id": "agent-1", "label": "Atlas"}],  # agent roster
                [],  # member roster
            ],
            fetchrow_results=[_task_row(metadata=stored_comment_body, title="Ship the widget")],
        )
        wake_mock = AsyncMock(return_value={"id": "wake-1", "status": "pending"})
        with (
            patch(
                "server_modules.project_tasks_service.control_plane_repository.ensure_control_plane_schema",
                new=AsyncMock(return_value=pool),
            ),
            patch("server_modules.bounded_scheduler_service.schedule_task_commented_wakeup", new=wake_mock),
        ):
            task = await project_tasks_service.add_task_comment(
                tenant_id="tenant-1", workspace_id="ws-1", task_id="task-1",
                author_type="human", author_id="user-1", body="@Atlas can you take a look?",
            )
        self.assertIsNotNone(task)
        # The comment persisted (the jsonb blob sent to Postgres) carries a
        # resolved mentions array riding along with the body.
        _query, args = pool.fetchrow_calls[0]
        self.assertIn('"mentions"', args[3])
        self.assertIn('"agent-1"', args[3])
        # And the agent got woken as a result, in the same call.
        wake_mock.assert_awaited_once()
        self.assertEqual(wake_mock.await_args.kwargs["agent_id"], "agent-1")

    async def test_comment_with_no_mention_does_not_touch_the_scheduler(self):
        pool = _QueuedFakePool(
            fetch_results=[[], []],
            fetchrow_results=[_task_row(metadata={"comments": [{"body": "no mentions here"}]})],
        )
        wake_mock = AsyncMock(side_effect=AssertionError("must not be called"))
        with (
            patch(
                "server_modules.project_tasks_service.control_plane_repository.ensure_control_plane_schema",
                new=AsyncMock(return_value=pool),
            ),
            patch("server_modules.bounded_scheduler_service.schedule_task_commented_wakeup", new=wake_mock),
        ):
            task = await project_tasks_service.add_task_comment(
                tenant_id="tenant-1", workspace_id="ws-1", task_id="task-1",
                author_type="human", author_id="user-1", body="no mentions here",
            )
        self.assertIsNotNone(task)
        wake_mock.assert_not_called()

    async def test_agent_commenting_and_mentioning_itself_never_wakes_itself(self):
        """Full end-to-end proof of the self-mention rule, exercised through
        the exact call shape project_task__comment (skills_service.py) uses
        for a platform agent's own in-turn comment."""
        pool = _QueuedFakePool(
            fetch_results=[
                [{"id": "agent-1", "label": "Atlas"}],  # agent roster
                [],  # member roster
            ],
            fetchrow_results=[_task_row(
                assignee_agent_id="agent-1",
                metadata={"comments": [{"author_type": "agent", "author_id": "agent-1", "body": "@Atlas: still working on this"}]},
            )],
        )
        wake_mock = AsyncMock(side_effect=AssertionError("must never self-wake"))
        with (
            patch(
                "server_modules.project_tasks_service.control_plane_repository.ensure_control_plane_schema",
                new=AsyncMock(return_value=pool),
            ),
            patch("server_modules.bounded_scheduler_service.schedule_task_commented_wakeup", new=wake_mock),
        ):
            task = await project_tasks_service.add_task_comment(
                tenant_id="tenant-1", workspace_id="ws-1", task_id="task-1",
                author_type="agent", author_id="agent-1", body="@Atlas: still working on this",
            )
        self.assertIsNotNone(task)
        wake_mock.assert_not_called()


if __name__ == "__main__":
    unittest.main()
