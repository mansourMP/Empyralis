import unittest
from unittest.mock import AsyncMock, patch

from server_modules import scale_harness


class ScaleHarnessTests(unittest.IsolatedAsyncioTestCase):
    def test_percentile_handles_edges_and_interpolation(self) -> None:
        self.assertEqual(scale_harness._percentile([], 95), None)
        self.assertEqual(scale_harness._percentile([10, 20, 30], 0), 10)
        self.assertEqual(scale_harness._percentile([10, 20, 30], 100), 30)
        self.assertEqual(scale_harness._percentile([10, 20, 30, 40], 95), 38.5)

    def test_run_fleet_runtime_simulation_is_reproducible(self) -> None:
        config = scale_harness.FleetScaleConfig(seed=17, total_runs=120, active_threads=110, worker_count=24, workspace_count=6)
        first = scale_harness.run_fleet_runtime_simulation(config=config)
        second = scale_harness.run_fleet_runtime_simulation(config=config)

        self.assertEqual(first["completed_runs"], second["completed_runs"])
        self.assertEqual(first["failed_runs"], second["failed_runs"])
        self.assertEqual(first["retry_count"], second["retry_count"])
        self.assertEqual(first["queue_delay_p95_seconds"], second["queue_delay_p95_seconds"])
        self.assertEqual(first["completion_latency_p95_seconds"], second["completion_latency_p95_seconds"])
        self.assertEqual(first["peak_concurrency"], second["peak_concurrency"])

    async def test_run_scale_harness_evaluates_slos_and_findings(self) -> None:
        with (
            patch.object(
                scale_harness,
                "run_control_plane_registry_benchmark",
                new=AsyncMock(
                    return_value={
                        "insert_seconds": 25.0,
                        "list_latency_p95_ms": 1700.0,
                        "supports_required_volume": True,
                    }
                ),
            ),
            patch.object(
                scale_harness,
                "run_fleet_runtime_simulation",
                return_value={
                    "peak_concurrency": 120,
                    "queue_delay_p95_seconds": 42.0,
                    "completion_latency_p95_seconds": 210.0,
                    "saturation_time_ratio": 0.4,
                    "retry_rate": 0.03,
                    "dead_letter_rate": 0.01,
                    "error_rate": 0.01,
                    "throughput_runs_per_second": 2.0,
                    "max_backlog_per_online_worker": 5.2,
                },
            ),
        ):
            payload = await scale_harness.run_scale_harness()

        failed_names = {item["name"] for item in payload["slo_results"] if not item["passed"]}
        self.assertIn("control_plane_bulk_insert_10k", failed_names)
        self.assertIn("control_plane_list_p95", failed_names)
        self.assertIn("fleet_completion_latency_p95", failed_names)
        self.assertIn("fleet_saturation_ratio", failed_names)
        self.assertGreaterEqual(len(payload["bottlenecks"]), 1)
        self.assertGreaterEqual(len(payload["correction_recommendations"]), 1)
