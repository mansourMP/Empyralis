import unittest
from unittest.mock import patch

from server_modules import runs_core


class RunsCoreScheduleRuntimeTests(unittest.TestCase):
    def test_schedule_run_execution_services_binds_schedule_id(self):
        with patch.object(runs_core, "_create_run_from_request", return_value={"run_id": "run-1"}) as create_run:
            services = runs_core._schedule_run_execution_services("sched-1")
            result = services.create_run_from_request(object())

        self.assertEqual(result["run_id"], "run-1")
        self.assertEqual(create_run.call_args.kwargs["schedule_id"], "sched-1")

    def test_execute_scheduled_run_request_routes_through_turn_runtime(self):
        with patch.object(
            runs_core,
            "execute_system_run_start_request_via_turn_runtime",
            return_value={"run_id": "run-2", "status": "starting"},
        ) as execute_run:
            result = runs_core._execute_scheduled_run_request(object(), schedule_id="sched-2")

        self.assertEqual(result["run_id"], "run-2")
        self.assertTrue(callable(execute_run.call_args.kwargs["stamp_request_owner_fn"]))


if __name__ == "__main__":
    unittest.main()
