import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

from server_modules.connectors import s3_connector


ROOT_DIR = Path(__file__).resolve().parents[2]
MODULE_PATH = ROOT_DIR / "server_modules" / "direct_chat_runtime_exports.py"
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))
spec = importlib.util.spec_from_file_location("operator_chat_s3_under_test", MODULE_PATH)
operator_chat = importlib.util.module_from_spec(spec)
sys.modules["operator_chat_s3_under_test"] = operator_chat
assert spec and spec.loader
spec.loader.exec_module(operator_chat)


class FakeS3Client:
    def __init__(self):
        self.uploads = []

    def list_buckets(self):
        return {"Buckets": [{"Name": "alpha"}, {"Name": "beta"}]}

    def upload_file(self, local_path, bucket, key):
        self.uploads.append((local_path, bucket, key))

    def download_file(self, bucket, key, local_path):
        Path(local_path).write_text(f"{bucket}:{key}", encoding="utf-8")


class S3ConnectorTests(unittest.TestCase):
    def test_list_buckets_parsed(self):
        result = s3_connector.list_buckets(
            {
                "aws_access_key_id": "AKIATEST1234",
                "aws_secret_access_key": "secret",
                "region": "us-east-1",
            },
            client=FakeS3Client(),
        )

        self.assertEqual(len(result), 2)
        self.assertEqual(result[0]["name"], "alpha")
        self.assertEqual(result[1]["name"], "beta")

    def test_upload_file_requires_approval(self):
        payload = operator_chat._build_direct_tool_approval_response(
            tool_calls=[
                {
                    "name": "s3__upload_file",
                    "arguments": json.dumps(
                        {
                            "input": json.dumps(
                                {
                                    "local_path": "/tmp/report.txt",
                                    "bucket": "alpha",
                                    "key": "reports/report.txt",
                                }
                            )
                        }
                    ),
                }
            ],
            tool_capabilities=[
                {
                    "id": "s3",
                    "label": "Amazon S3",
                    "connected": True,
                    "authenticated": True,
                    "runtime_usable": True,
                    "read_actions": ["buckets.read", "objects.read"],
                    "write_actions": [
                        "list_buckets",
                        "list_objects",
                        "upload_file",
                        "download_file",
                        "delete_object",
                        "get_presigned_url",
                        "create_bucket",
                    ],
                    "approval_required_actions": ["upload_file", "delete_object", "create_bucket"],
                }
            ],
        )

        self.assertIsNotNone(payload)
        assert payload is not None
        self.assertEqual(payload["mode"], "answer_with_action")
        self.assertEqual(payload["actions"][0]["connector"], "s3")
        self.assertEqual(payload["actions"][0]["action"], "upload_file")

    def test_upload_file_uses_mock_client(self):
        client = FakeS3Client()
        with tempfile.NamedTemporaryFile("w", delete=False) as handle:
            handle.write("s3-body")
            local_path = handle.name
        try:
            result = s3_connector.upload_file(
                {
                    "aws_access_key_id": "AKIATEST1234",
                    "aws_secret_access_key": "secret",
                    "region": "us-east-1",
                },
                local_path,
                "alpha",
                "reports/report.txt",
                client=client,
            )
        finally:
            Path(local_path).unlink(missing_ok=True)

        self.assertTrue(result["ok"])
        self.assertEqual(result["bucket"], "alpha")
        self.assertEqual(result["key"], "reports/report.txt")
        self.assertEqual(len(client.uploads), 1)


if __name__ == "__main__":
    unittest.main()
