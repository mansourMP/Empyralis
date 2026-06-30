import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

from server_modules.connectors import dropbox_connector


ROOT_DIR = Path(__file__).resolve().parents[2]
MODULE_PATH = ROOT_DIR / "server_modules" / "direct_chat_runtime_exports.py"
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))
spec = importlib.util.spec_from_file_location("operator_chat_dropbox_under_test", MODULE_PATH)
operator_chat = importlib.util.module_from_spec(spec)
sys.modules["operator_chat_dropbox_under_test"] = operator_chat
assert spec and spec.loader
spec.loader.exec_module(operator_chat)


class _DropboxListResult:
    def __init__(self, entries):
        self.entries = entries
        self.has_more = False
        self.cursor = None


class FakeDropboxClient:
    def __init__(self):
        self.upload_calls = []

    def files_list_folder(self, path):
        return _DropboxListResult(
            [
                {"id": "id:file1", "name": "report.txt", "path_display": "/report.txt", ".tag": "file", "size": 42},
                {"id": "id:folder1", "name": "docs", "path_display": "/docs", ".tag": "folder"},
            ]
        )

    def files_upload(self, content, path, mode=None, mute=True):
        self.upload_calls.append((content, path, mode, mute))
        return {"id": "id:file2", "name": "draft.txt", "path_display": path}


class DropboxConnectorTests(unittest.TestCase):
    def test_list_folder_parsed(self):
        result = dropbox_connector.list_folder(
            {"access_token": "dropbox-test"},
            path="/",
            client=FakeDropboxClient(),
        )

        self.assertEqual(len(result), 2)
        self.assertEqual(result[0]["name"], "report.txt")
        self.assertEqual(result[0]["kind"], "file")
        self.assertEqual(result[1]["kind"], "folder")

    def test_upload_file_requires_approval(self):
        payload = operator_chat._build_direct_tool_approval_response(
            tool_calls=[
                {
                    "name": "dropbox__upload_file",
                    "arguments": json.dumps(
                        {
                            "input": json.dumps(
                                {
                                    "local_path": "/tmp/report.txt",
                                    "dropbox_path": "/reports/report.txt",
                                }
                            )
                        }
                    ),
                }
            ],
            tool_capabilities=[
                {
                    "id": "dropbox",
                    "label": "Dropbox",
                    "connected": True,
                    "authenticated": True,
                    "runtime_usable": True,
                    "read_actions": ["files.read", "folders.read", "shared_links.read"],
                    "write_actions": [
                        "list_folder",
                        "upload_file",
                        "download_file",
                        "delete",
                        "move",
                        "get_shared_link",
                        "search",
                    ],
                    "approval_required_actions": ["upload_file", "delete", "move"],
                }
            ],
        )

        self.assertIsNotNone(payload)
        assert payload is not None
        self.assertEqual(payload["mode"], "answer_with_action")
        self.assertEqual(payload["actions"][0]["connector"], "dropbox")
        self.assertEqual(payload["actions"][0]["action"], "upload_file")

    def test_upload_file_uses_mock_client(self):
        client = FakeDropboxClient()
        with tempfile.NamedTemporaryFile("w", delete=False) as handle:
            handle.write("dropbox-body")
            local_path = handle.name
        try:
            result = dropbox_connector.upload_file(
                {"access_token": "dropbox-test"},
                local_path,
                "/reports/report.txt",
                client=client,
            )
        finally:
            Path(local_path).unlink(missing_ok=True)

        self.assertTrue(result["ok"])
        self.assertEqual(result["path_display"], "/reports/report.txt")
        self.assertEqual(len(client.upload_calls), 1)


if __name__ == "__main__":
    unittest.main()
