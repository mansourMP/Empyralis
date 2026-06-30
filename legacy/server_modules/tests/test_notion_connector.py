import importlib.util
import json
import sys
import unittest
from pathlib import Path

from server_modules.connectors import notion_connector


ROOT_DIR = Path(__file__).resolve().parents[2]
MODULE_PATH = ROOT_DIR / "server_modules" / "direct_chat_runtime_exports.py"
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))
spec = importlib.util.spec_from_file_location("operator_chat_notion_under_test", MODULE_PATH)
operator_chat = importlib.util.module_from_spec(spec)
sys.modules["operator_chat_notion_under_test"] = operator_chat
assert spec and spec.loader
spec.loader.exec_module(operator_chat)


class NotionConnectorTests(unittest.TestCase):
    def test_search_parsed(self):
        calls = []

        def fake_request(url, **kwargs):
            calls.append((url, kwargs))
            return {
                "status": 200,
                "json": {
                    "results": [
                        {"id": "page-1", "object": "page"},
                        {"id": "db-1", "object": "database"},
                    ]
                },
            }

        result = notion_connector.search(
            {"integration_token": "secret_test"},
            "Roadmap",
            filter_type="page",
            http_json_request=fake_request,
        )

        self.assertEqual(len(result), 2)
        self.assertEqual(result[0]["id"], "page-1")
        self.assertIn("/search", calls[0][0])
        self.assertEqual(calls[0][1]["payload"]["query"], "Roadmap")
        self.assertEqual(calls[0][1]["payload"]["filter"]["value"], "page")

    def test_create_page_requires_approval(self):
        payload = operator_chat._build_direct_tool_approval_response(
            tool_calls=[
                {
                    "name": "notion__create_page",
                    "arguments": json.dumps(
                        {
                            "input": json.dumps(
                                {
                                    "parent_id": "page-parent",
                                    "title": "Launch checklist",
                                    "content_blocks": [{"object": "block"}],
                                }
                            )
                        }
                    ),
                }
            ],
            tool_capabilities=[
                {
                    "id": "notion",
                    "label": "Notion",
                    "connected": True,
                    "authenticated": True,
                    "runtime_usable": True,
                    "read_actions": ["pages.read", "databases.read"],
                    "write_actions": [
                        "search",
                        "get_page",
                        "create_page",
                        "update_page",
                        "append_blocks",
                        "query_database",
                        "create_database_item",
                    ],
                    "approval_required_actions": [
                        "create_page",
                        "update_page",
                        "create_database_item",
                    ],
                }
            ],
        )

        self.assertIsNotNone(payload)
        assert payload is not None
        self.assertEqual(payload["mode"], "answer_with_action")
        self.assertEqual(payload["actions"][0]["connector"], "notion")
        self.assertEqual(payload["actions"][0]["action"], "create_page")


if __name__ == "__main__":
    unittest.main()
