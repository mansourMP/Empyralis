import importlib.util
import json
import sys
import unittest
from pathlib import Path

from server_modules.connectors import linear_connector


ROOT_DIR = Path(__file__).resolve().parents[2]
MODULE_PATH = ROOT_DIR / "server_modules" / "direct_chat_runtime_exports.py"
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))
spec = importlib.util.spec_from_file_location("operator_chat_linear_under_test", MODULE_PATH)
operator_chat = importlib.util.module_from_spec(spec)
sys.modules["operator_chat_linear_under_test"] = operator_chat
assert spec and spec.loader
spec.loader.exec_module(operator_chat)


class LinearConnectorTests(unittest.TestCase):
    def test_list_issues_parsed(self):
        calls = []

        def fake_request(url, **kwargs):
            calls.append((url, kwargs))
            return {
                "status": 200,
                "json": {
                    "data": {
                        "issues": {
                            "nodes": [
                                {
                                    "id": "issue-1",
                                    "identifier": "ENG-123",
                                    "title": "Polish approvals",
                                    "priority": 2,
                                    "url": "https://linear.app/acme/issue/ENG-123",
                                }
                            ]
                        }
                    }
                },
            }

        result = linear_connector.list_issues(
            {"api_key": "lin_api_test"},
            "team-1",
            state="In Progress",
            assignee="user-1",
            limit=10,
            http_json_request=fake_request,
        )

        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["identifier"], "ENG-123")
        self.assertEqual(calls[0][0], linear_connector.LINEAR_API_URL)
        variables = calls[0][1]["payload"]["variables"]
        self.assertEqual(variables["first"], 10)
        self.assertEqual(variables["filter"]["team"]["id"]["eq"], "team-1")
        self.assertEqual(variables["filter"]["state"]["name"]["eq"], "In Progress")
        self.assertEqual(variables["filter"]["assignee"]["id"]["eq"], "user-1")

    def test_create_issue_requires_approval(self):
        payload = operator_chat._build_direct_tool_approval_response(
            tool_calls=[
                {
                    "name": "linear__create_issue",
                    "arguments": json.dumps(
                        {
                            "input": json.dumps(
                                {
                                    "team_id": "team-1",
                                    "title": "Ship connector",
                                    "description": "Finalize the Linear connector rollout.",
                                }
                            )
                        }
                    ),
                }
            ],
            tool_capabilities=[
                {
                    "id": "linear",
                    "label": "Linear",
                    "connected": True,
                    "authenticated": True,
                    "runtime_usable": True,
                    "read_actions": ["teams.read", "issues.read", "projects.read"],
                    "write_actions": [
                        "list_teams",
                        "list_issues",
                        "get_issue",
                        "create_issue",
                        "update_issue",
                        "list_projects",
                        "add_comment",
                    ],
                    "approval_required_actions": [
                        "create_issue",
                        "update_issue",
                        "add_comment",
                    ],
                }
            ],
        )

        self.assertIsNotNone(payload)
        assert payload is not None
        self.assertEqual(payload["mode"], "answer_with_action")
        self.assertEqual(payload["actions"][0]["connector"], "linear")
        self.assertEqual(payload["actions"][0]["action"], "create_issue")


if __name__ == "__main__":
    unittest.main()
