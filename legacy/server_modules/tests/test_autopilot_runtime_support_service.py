import threading
import unittest
from datetime import datetime, timedelta, timezone

from server_modules.connectors.autopilot_runtime_support_service import AutopilotRuntimeSupportService


class AutopilotRuntimeSupportServiceTests(unittest.TestCase):
    def _make_service(self, **overrides) -> AutopilotRuntimeSupportService:
        now = overrides.pop("now", datetime(2026, 4, 5, 0, 0, tzinfo=timezone.utc))
        return AutopilotRuntimeSupportService(
            run_history=overrides.pop("run_history", []),
            run_history_lock=threading.Lock(),
            runtime_metrics=overrides.pop("runtime_metrics", {}),
            metrics_lock=threading.Lock(),
            utc_now=overrides.pop("utc_now", lambda: now),
            parse_utc_ts=overrides.pop(
                "parse_utc_ts",
                lambda value: datetime.fromisoformat(str(value).replace("Z", "+00:00")) if value else None,
            ),
            worker_online_helper=overrides.pop("worker_online_helper", lambda record, current=None: (_ for _ in ()).throw(RuntimeError("fallback"))),
            local_lease_seconds=overrides.pop("local_lease_seconds", 30),
            local_queue_lock=threading.Lock(),
            local_pending_run_ids=overrides.pop("local_pending_run_ids", []),
            local_claimed_runs=overrides.pop("local_claimed_runs", {}),
            local_worker_registry=overrides.pop("local_worker_registry", {}),
            truncate_one_line=overrides.pop("truncate_one_line", lambda text, limit: str(text or "")[:limit]),
            non_retryable_run_error_hints=overrides.pop("non_retryable_run_error_hints", ["fatal", "missing scopes"]),
        )

    def test_runtime_metrics_and_recent_summary(self) -> None:
        service = self._make_service(
            run_history=[{"run_id": "abcdef123456", "status": "completed"}],
            runtime_metrics={"runs_started": 5, "runs_completed": 4, "runs_failed": 1, "runs_timeout": 0},
        )
        self.assertEqual(service.latest_runtime_run_summary(), "abcdef12 completed")
        self.assertEqual(service.current_runtime_metrics()["runs_started"], 5)

    def test_worker_online_fallback_and_local_snapshot(self) -> None:
        now = datetime(2026, 4, 5, 0, 0, tzinfo=timezone.utc)
        service = self._make_service(
            now=now,
            local_pending_run_ids=["r1", "r2"],
            local_claimed_runs={"r3": {}},
            local_worker_registry={
                "w1": {"last_seen_at": (now - timedelta(seconds=10)).isoformat().replace("+00:00", "Z")},
                "w2": {"last_seen_at": (now - timedelta(seconds=1000)).isoformat().replace("+00:00", "Z")},
            },
        )
        snapshot = service.local_companion_snapshot()
        self.assertEqual(snapshot["online_workers"], 1)
        self.assertEqual(snapshot["pending_runs"], 2)
        self.assertEqual(snapshot["claimed_runs"], 1)

    def test_run_error_helpers_and_summary(self) -> None:
        service = self._make_service(truncate_one_line=lambda text, limit: str(text or "").strip()[:limit])
        run = {
            "events": [{"event": "run_error", "message": "missing scopes for api.responses.write"}],
            "last_error": "missing scopes for api.responses.write",
        }
        self.assertEqual(service.latest_run_error_message(run), "missing scopes for api.responses.write")
        self.assertTrue(service.is_non_retryable_run_error("missing scopes for api.responses.write"))
        self.assertIn("authorization", service.friendly_run_error("missing scopes for api.responses.write").lower())
        summary = service.summarize_run_terminal_result(run, 120)
        self.assertIn("Missing required scope", summary)

    def test_summarize_run_terminal_result_uses_durable_result_summary(self) -> None:
        service = self._make_service(truncate_one_line=lambda text, limit: str(text or "").strip()[:limit])

        summary = service.summarize_run_terminal_result(
            {"status": "completed", "result_summary": "Blackbox durable reply: customer-facing answer"},
            120,
        )

        self.assertIn("customer-facing answer", summary)
        self.assertNotEqual(summary, "Run finished.")

    def test_humanize_telegram_run_summary(self) -> None:
        service = self._make_service()
        self.assertIn("offline", service.humanize_telegram_run_summary("Gateway is offline right now."))
        self.assertIn("Please retry", service.humanize_telegram_run_summary("missing required scope: api.responses.write"))

    def test_summarize_run_terminal_result_applies_health_safety_overlay(self) -> None:
        service = self._make_service(truncate_one_line=lambda text, limit: str(text or "")[:limit])
        run = {
            "result": "Blackbox durable reply: I have chest pain and trouble breathing",
            "context": {
                "user_goal": "I have chest pain and trouble breathing",
                "metadata": {
                    "health_safety_enabled": True,
                    "health_safety_assistant_name": "HealthGuide",
                },
            },
        }

        summary = service.summarize_run_terminal_result(run, 400)

        self.assertIn("urgent medical attention", summary.lower())
        self.assertNotIn("Blackbox durable reply:", summary)


if __name__ == "__main__":
    unittest.main()
