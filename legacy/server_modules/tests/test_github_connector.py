import importlib.util
import json
import sys
import unittest
from pathlib import Path

from server_modules.connectors import github_connector


ROOT_DIR = Path(__file__).resolve().parents[2]
MODULE_PATH = ROOT_DIR / "server_modules" / "direct_chat_runtime_exports.py"
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))
spec = importlib.util.spec_from_file_location("operator_chat_github_under_test", MODULE_PATH)
operator_chat = importlib.util.module_from_spec(spec)
sys.modules["operator_chat_github_under_test"] = operator_chat
assert spec and spec.loader
spec.loader.exec_module(operator_chat)


class GithubConnectorTests(unittest.TestCase):
    def test_list_repos_parsed(self):
        calls = []

        def fake_request(url, **kwargs):
            calls.append((url, kwargs))
            return {
                "status": 200,
                "json": [
                    {
                        "id": 1,
                        "name": "repo-one",
                        "full_name": "acme/repo-one",
                        "private": True,
                        "default_branch": "main",
                        "html_url": "https://github.com/acme/repo-one",
                        "owner": {"login": "acme"},
                    }
                ],
            }

        result = github_connector.list_repos(
            {"personal_access_token": "ghp_test"},
            org="acme",
            limit=20,
            http_json_request=fake_request,
        )

        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["full_name"], "acme/repo-one")
        self.assertEqual(result[0]["owner"], "acme")
        self.assertIn("/orgs/acme/repos", calls[0][0])

    def test_create_issue_requires_approval(self):
        payload = operator_chat._build_direct_tool_approval_response(
            tool_calls=[
                {
                    "name": "github__create_issue",
                    "arguments": json.dumps(
                        {
                            "input": json.dumps(
                                {
                                    "owner": "acme",
                                    "repo": "api",
                                    "title": "Bug report",
                                    "body": "Please inspect this issue.",
                                }
                            )
                        }
                    ),
                }
            ],
            tool_capabilities=[
                {
                    "id": "github",
                    "label": "GitHub",
                    "connected": True,
                    "authenticated": True,
                    "runtime_usable": True,
                    "read_actions": ["repositories.read", "issues.read"],
                    "write_actions": [
                        "list_repos",
                        "get_repo",
                        "list_issues",
                        "create_issue",
                        "comment_on_issue",
                        "list_pull_requests",
                        "create_pull_request",
                        "get_file_content",
                        "create_or_update_file",
                        "list_commits",
                    ],
                    "approval_required_actions": [
                        "create_issue",
                        "comment_on_issue",
                        "create_pull_request",
                        "create_or_update_file",
                    ],
                }
            ],
        )

        self.assertIsNotNone(payload)
        assert payload is not None
        self.assertEqual(payload["mode"], "answer_with_action")
        self.assertEqual(payload["actions"][0]["connector"], "github")
        self.assertEqual(payload["actions"][0]["action"], "create_issue")

    def test_webhook_push_event_parsed(self):
        parsed = github_connector.parse_inbound_event(
            {
                "ref": "refs/heads/main",
                "before": "abc",
                "after": "def",
                "repository": {
                    "name": "api",
                    "full_name": "acme/api",
                    "owner": {"login": "acme"},
                },
                "sender": {"login": "octocat"},
                "commits": [{"id": "1"}, {"id": "2"}],
                "head_commit": {"message": "Ship it"},
            },
            event_type="push",
            delivery_id="delivery-123",
        )

        self.assertEqual(parsed["event_type"], "push")
        self.assertEqual(parsed["repository"], "acme/api")
        self.assertEqual(parsed["owner"], "acme")
        self.assertEqual(parsed["sender"], "octocat")
        self.assertEqual(parsed["commit_count"], 2)
        self.assertEqual(parsed["head_commit"], "Ship it")
        self.assertEqual(parsed["delivery_id"], "delivery-123")


if __name__ == "__main__":
    unittest.main()
