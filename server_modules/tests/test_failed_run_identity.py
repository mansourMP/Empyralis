"""A failed run carries WHICH agent and WHY, all the way to the row.

The chain, and what was missing at each link before this:

    run_service._emit_run_transition_outbox_event   had `run` in hand,
                                                    forwarded nothing
      → outbox_service.emit_run_transition_event    payload was 5 keys:
                                                    no agent, no reason
        → notification_service                      text = "Run <uuid> failed."
          → activity_ledger_service                 install_id NULL — it reads
                                                    metadata["agent_install_id"],
                                                    which nothing set
            → the Inbox row                         "Run failed", detail null,
                                                    href null

So a person saw a failure, could not tell which agent it was, and had no
route to the cause — which existed the whole time, in the trace.

These tests drive the REAL producers rather than asserting on a fixture
shaped like what the consumer wants, which is how the frontend's own
fixture came to hand-set an `install_id` the producer never wrote.
"""
from __future__ import annotations

import unittest
from unittest import mock

from server_modules import agent_trace_service, notification_service, outbox_service, run_service


class LinkableTraceIdTests(unittest.TestCase):
    """"The run has a trace id" and "there is a trace to open" differ.

    run_service._run_trace_id falls back to an OpenTelemetry span id when a
    run has no agent trace of its own — right for correlation, useless as a
    link, because it has no row in agent_traces.
    """

    def test_a_real_agent_trace_id_is_linkable(self):
        # The expected value comes from the GENERATOR, not from a literal
        # copied into this test: create_agent_trace mints
        # f"trace_{uuid.uuid4().hex[:24]}" and is the only thing that may.
        import uuid

        minted = f"trace_{uuid.uuid4().hex[:24]}"
        self.assertTrue(agent_trace_service.is_linkable_trace_id(minted))

    def test_an_otel_span_id_is_not_linkable(self):
        # 32 bare hex chars, exactly what
        # f"{span.get_span_context().trace_id:032x}" produces.
        self.assertFalse(agent_trace_service.is_linkable_trace_id("a" * 32))

    def test_empty_and_prefix_only_are_not_linkable(self):
        for value in ("", None, "   ", "trace_"):
            self.assertFalse(agent_trace_service.is_linkable_trace_id(value), repr(value))

    def test_the_generator_and_the_check_have_not_drifted(self):
        """A canary: if create_agent_trace stopped using this prefix, every
        assertion above would still pass while the predicate answered False
        for every real trace in production."""
        import inspect

        from server_modules import control_plane_repository

        source = inspect.getsource(control_plane_repository.create_agent_trace)
        self.assertIn(
            'f"trace_',
            source,
            "create_agent_trace no longer mints a trace_-prefixed id — "
            "is_linkable_trace_id is now wrong for every real trace",
        )


class RunMetadataResolutionTests(unittest.TestCase):
    def _run(self, metadata):
        return {"context": {"metadata": metadata}}

    def test_install_id_uses_the_same_order_as_the_trace_root(self):
        """One resolution, two callers. Two independent answers to "which
        agent is this run" is how a trace and a notification end up
        disagreeing about the same run."""
        for key in ("active_agent_install_id", "master_agent_install_id", "workspace_agent_install_id"):
            run = self._run({key: "ainstall_abc"})
            self.assertEqual(run_service._run_agent_install_id(run), "ainstall_abc")
            self.assertEqual(
                run_service._trace_root_agent_id_for_metadata(run["context"]["metadata"]),
                "specialist:ainstall_abc",
            )

    def test_precedence_is_preserved(self):
        run = self._run({
            "active_agent_install_id": "ainstall_active",
            "master_agent_install_id": "ainstall_master",
        })
        self.assertEqual(run_service._run_agent_install_id(run), "ainstall_active")

    def test_the_operator_has_no_install_id(self):
        self.assertEqual(run_service._run_agent_install_id(self._run({})), "")
        self.assertEqual(run_service._trace_root_agent_id_for_metadata({}), "sage")

    def test_failure_summary_prefers_the_real_outcome(self):
        run = {"execution_outcome": {"summary": "provider_auth_failed: DeepSeek rejected the API key."}}
        self.assertEqual(
            run_service._run_failure_summary(run, "failed"),
            "provider_auth_failed: DeepSeek rejected the API key.",
        )

    def test_failure_summary_falls_back_to_the_run_error(self):
        run = {"error": "provider_rate_limited: DeepSeek is rate limiting this workspace right now."}
        self.assertIn("rate limiting", run_service._run_failure_summary(run, "failed"))

    def test_failure_summary_is_never_empty(self):
        """A row that says nothing is the bug. There is always a sentence."""
        self.assertTrue(run_service._run_failure_summary({}, "failed").strip())


class OutboxPayloadTests(unittest.TestCase):
    """The real emit function, with the kernel decision stubbed.

    Only the decision gate and the persist sink are replaced; the payload
    under test is built by production code.
    """

    def _emit(self, **kwargs):
        captured = {}

        def _persist(event, **_):
            captured["event"] = event

        with mock.patch.object(
            outbox_service,
            "_enforce_run_record_outbox_decision",
            return_value={"next_action": "emit_run_transition_event"},
        ), mock.patch.object(outbox_service, "_persist_outbox_event", _persist):
            outbox_service.emit_run_transition_event(
                run_id="run-1",
                tenant_id="t1",
                workspace_id="ws1",
                from_state="running",
                to_state="failed",
                actor="runtime",
                trace_id="trace_abc",
                **kwargs,
            )
        return captured["event"].payload

    def test_the_agent_and_the_reason_ride_with_the_transition(self):
        payload = self._emit(
            agent_install_id="ainstall_abc",
            failure_reason="provider_auth_failed: DeepSeek rejected the API key.",
        )
        metadata = payload["metadata"]
        # activity_ledger_service reads THIS key to fill the row's
        # install_id. The name is not ours to choose.
        self.assertEqual(metadata["agent_install_id"], "ainstall_abc")
        self.assertIn("DeepSeek", metadata["failure_reason"])

    def test_an_absent_fact_is_an_absent_key_not_an_empty_one(self):
        """An empty install id in the row is indistinguishable from an agent
        that could not be resolved, and a consumer would render a blank name
        rather than fall back."""
        metadata = self._emit()["metadata"]
        self.assertNotIn("agent_install_id", metadata)
        self.assertNotIn("failure_reason", metadata)

    def test_the_original_five_keys_are_untouched(self):
        payload = self._emit(agent_install_id="ainstall_abc")
        for key in ("run_id", "from_state", "to_state", "actor", "emitted_at"):
            self.assertIn(key, payload)

    def test_the_reason_is_bounded(self):
        metadata = self._emit(failure_reason="x" * 5000)["metadata"]
        self.assertLessEqual(len(metadata["failure_reason"]), 1000)


class NotificationTextTests(unittest.TestCase):
    """The sentence the ledger row's `summary` column ends up holding."""

    def _notification(self, payload):
        event = mock.Mock()
        event.event_type = "run_transition"
        event.tenant_id = "t1"
        event.workspace_id = "ws1"
        event.run_id = "c4ab05f3-bcc9-4503-b48b-bd8c0f8e7aec"
        event.machine_id = None
        event.trace_id = "trace_abc"
        event.payload = payload
        event.event_id = "evt-1"
        event.created_at = "2026-08-29T00:00:00Z"
        return notification_service.build_notification_from_outbox_event(event)

    def _failed_payload(self, **metadata):
        return {
            "run_id": "c4ab05f3-bcc9-4503-b48b-bd8c0f8e7aec",
            "from_state": "running",
            "to_state": "failed",
            "actor": "runtime",
            "metadata": metadata,
        }

    def test_the_row_says_why_not_just_the_id_again(self):
        note = self._notification(
            self._failed_payload(failure_reason="provider_auth_failed: DeepSeek rejected the API key.")
        )
        self.assertEqual(note["text"], "provider_auth_failed: DeepSeek rejected the API key.")
        # The id is still there for a machine — it just stopped being the
        # entire message shown to a person.
        self.assertEqual(note["run_id"], "c4ab05f3-bcc9-4503-b48b-bd8c0f8e7aec")

    def test_the_agent_reaches_the_ledger_writer(self):
        note = self._notification(self._failed_payload(agent_install_id="ainstall_abc"))
        self.assertEqual(note["metadata"]["agent_install_id"], "ainstall_abc")

    def test_without_a_reason_the_old_sentence_is_kept(self):
        """No invented certainty: a transition that carries no reason still
        reports honestly rather than claiming one."""
        note = self._notification(self._failed_payload())
        self.assertIn("failed", note["text"])
        self.assertIn("c4ab05f3", note["text"])

    def test_a_completed_run_is_unchanged(self):
        payload = self._failed_payload(failure_reason="should be ignored")
        payload["to_state"] = "completed"
        note = self._notification(payload)
        self.assertIn("completed", note["text"])
        self.assertNotIn("should be ignored", note["text"])


class TaskCarriesItsCauseTests(unittest.TestCase):
    """A task moved to `blocked` records the run that blocked it.

    Before this, the run id survived only as prose inside a system comment,
    which nothing parses — so the board said "Blocked" and the cause was
    reachable from nowhere.
    """

    def _capture(self, **kwargs):
        from server_modules import project_tasks_service

        captured = {}

        async def _rls_execute(pool, sql, *params, **kw):
            captured["sql"] = sql
            captured["params"] = params

        async def _schema():
            return object()

        with mock.patch.object(project_tasks_service.control_plane_repository, "rls_execute", _rls_execute), \
                mock.patch.object(project_tasks_service.control_plane_repository, "ensure_control_plane_schema", _schema):
            import asyncio

            asyncio.run(
                project_tasks_service.record_task_run_failure(
                    tenant_id="t1", workspace_id="ws1", task_id="task_1", **kwargs
                )
            )
        return captured

    def test_the_run_trace_and_reason_are_stamped_on_the_task(self):
        import json

        captured = self._capture(
            run_id="run-1",
            trace_id="trace_abc",
            reason="provider_auth_failed: DeepSeek rejected the API key.",
        )
        record = json.loads(captured["params"][3])
        self.assertEqual(record["run_id"], "run-1")
        self.assertEqual(record["trace_id"], "trace_abc")
        self.assertIn("DeepSeek", record["reason"])
        self.assertIn("at", record)

    def test_it_writes_to_the_metadata_seam_the_siblings_use(self):
        captured = self._capture(run_id="run-1")
        self.assertIn("last_failed_run", captured["sql"])
        self.assertIn("jsonb_set", captured["sql"])
        # Scoped, not a bare id: project_tasks is FORCE RLS and the write
        # must name both scope columns.
        self.assertIn("tenant_id = $1", captured["sql"])
        self.assertIn("workspace_id = $2", captured["sql"])

    def test_an_absent_trace_or_reason_is_omitted_not_blanked(self):
        import json

        record = json.loads(self._capture(run_id="run-1")["params"][3])
        self.assertNotIn("trace_id", record)
        self.assertNotIn("reason", record)

    def test_a_missing_scope_writes_nothing(self):
        """A scoped write with a blank scope is the fail-open shape this
        codebase bans; it must do nothing rather than write unscoped."""
        from server_modules import project_tasks_service

        calls = []

        async def _rls_execute(*a, **k):
            calls.append(a)

        with mock.patch.object(project_tasks_service.control_plane_repository, "rls_execute", _rls_execute):
            import asyncio

            asyncio.run(
                project_tasks_service.record_task_run_failure(
                    tenant_id="", workspace_id="ws1", task_id="task_1", run_id="run-1"
                )
            )
        self.assertEqual(calls, [])


if __name__ == "__main__":
    unittest.main()
