from unittest import TestCase

from server_modules import telemetry


class TelemetryReliabilityTests(TestCase):
    def setUp(self) -> None:
        telemetry.reset_reliability_metrics()

    def tearDown(self) -> None:
        telemetry.reset_reliability_metrics()

    def test_reliability_snapshot_reports_control_plane_and_enqueue_latency(self) -> None:
        telemetry.record_control_plane_request(
            method="POST",
            path="/runs",
            status_code=202,
            duration_ms=120.0,
            recorded_at="2026-04-08T00:00:00Z",
        )
        telemetry.record_reliability_latency_sample(
            "run_enqueue_ack_latency_ms",
            250.0,
            recorded_at="2026-04-08T00:00:01Z",
            metadata={"run_id": "run-1"},
        )

        snapshot = telemetry.get_reliability_snapshot()

        self.assertEqual(snapshot["control_plane_api"]["request_count"], 1)
        self.assertEqual(snapshot["control_plane_api"]["available_count"], 1)
        self.assertEqual(snapshot["control_plane_api"]["latency_ms"]["p95_ms"], 120.0)
        self.assertEqual(snapshot["run_enqueue_ack"]["sample_count"], 1)
        self.assertEqual(snapshot["run_enqueue_ack"]["p95_ms"], 250.0)
        self.assertEqual(snapshot["run_enqueue_ack"]["last_metadata"]["run_id"], "run-1")

    def test_machine_revocation_propagation_records_first_observation(self) -> None:
        telemetry.mark_machine_revocation_requested(
            "machine-1",
            started_monotonic=10.0,
            recorded_at="2026-04-08T00:00:00Z",
        )

        duration_ms = telemetry.observe_machine_revocation_propagated(
            "machine-1",
            observed_monotonic=12.5,
            observed_at="2026-04-08T00:00:02Z",
        )

        snapshot = telemetry.get_reliability_snapshot()
        self.assertEqual(duration_ms, 2500.0)
        self.assertEqual(snapshot["machine_revocation_propagation"]["sample_count"], 1)
        self.assertEqual(snapshot["machine_revocation_propagation"]["p95_ms"], 2500.0)
        self.assertEqual(snapshot["machine_revocation_propagation"]["pending_revocation_count"], 0)

    def test_failed_run_replay_completeness_marks_incomplete_runs(self) -> None:
        snapshot = telemetry.get_reliability_snapshot(
            failed_run_snapshots=[
                {
                    "run_id": "run-complete",
                    "status": "failed",
                    "replay_request": {"run_id": "run-complete"},
                    "run_detail_contract": {"status": "failed"},
                    "events": [{"event": "failed"}],
                },
                {
                    "run_id": "run-incomplete",
                    "status": "timeout",
                    "replay_request": {"run_id": "run-incomplete"},
                    "run_detail_contract": {},
                    "events": [],
                },
            ]
        )

        replay = snapshot["failed_run_replay_completeness"]
        self.assertEqual(replay["evaluated_run_count"], 2)
        self.assertEqual(replay["complete_run_count"], 1)
        self.assertEqual(replay["incomplete_run_count"], 1)
        self.assertEqual(replay["incomplete_run_ids"], ["run-incomplete"])
        self.assertFalse(replay["meets_target"])
