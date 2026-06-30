import unittest
from unittest.mock import patch

from server_modules import runs_execution


class RunsExecutionPolicyServiceTests(unittest.TestCase):
    def test_compute_tool_policy_precheck_delegates_to_policy_service(self) -> None:
        with patch(
            "server_modules.runs_execution.policy_service.compute_tool_policy_precheck",
            return_value={"blocked_count": 0},
        ) as mock_compute:
            result = runs_execution._compute_tool_policy_precheck({"metadata": {}})

        self.assertEqual(result, {"blocked_count": 0})
        mock_compute.assert_called_once()


if __name__ == "__main__":
    unittest.main()
