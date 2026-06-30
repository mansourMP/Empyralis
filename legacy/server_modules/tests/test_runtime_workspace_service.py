import unittest

from fastapi import HTTPException

from server_modules import runtime_workspace_service


class _Snapshot:
    def __init__(self, payload):
        self._payload = payload

    def as_payload(self):
        return self._payload


class RuntimeWorkspaceServiceTests(unittest.TestCase):
    def test_list_workspace_memory_payload_normalizes_workspace_id(self):
        payload = runtime_workspace_service.list_workspace_memory_payload(
            "",
            workspace_memory_snapshot=lambda workspace_id: _Snapshot({"workspace_id": workspace_id}),
        )

        self.assertEqual(payload, {"workspace_id": "default"})

    def test_delete_workspace_memory_payload_rejects_blank_key(self):
        with self.assertRaises(HTTPException):
            runtime_workspace_service.delete_workspace_memory_payload(
                "default",
                "",
                delete_memory=lambda workspace_id, key: True,
            )

    def test_delete_workspace_memory_payload_returns_deleted_key(self):
        payload = runtime_workspace_service.delete_workspace_memory_payload(
            "default",
            " memory-key ",
            delete_memory=lambda workspace_id, key: True,
        )

        self.assertEqual(
            payload,
            {"ok": True, "workspace_id": "default", "key": "memory-key"},
        )

    def test_workspace_context_files_payload_serializes_content(self):
        payload = runtime_workspace_service.workspace_context_files_payload(
            read_workspace_context_files=lambda: {"SOUL.md": "Stay concise.\n"},
        )

        self.assertEqual(
            payload,
            {"ok": True, "files": [{"filename": "SOUL.md", "content": "Stay concise.\n"}]},
        )

    def test_update_workspace_context_file_payload_requires_content(self):
        with self.assertRaises(HTTPException):
            runtime_workspace_service.update_workspace_context_file_payload(
                "SOUL.md",
                {},
                write_workspace_context_file=lambda filename, content: {},
            )

    def test_update_workspace_context_file_payload_returns_saved_payload(self):
        payload = runtime_workspace_service.update_workspace_context_file_payload(
            "SOUL.md",
            {"content": "Stay concise.\n"},
            write_workspace_context_file=lambda filename, content: {"filename": filename, "content": content},
        )

        self.assertEqual(
            payload,
            {"ok": True, "filename": "SOUL.md", "content": "Stay concise.\n"},
        )

    def test_update_workspace_context_file_payload_supports_file_like_memory_documents(self):
        payload = runtime_workspace_service.update_workspace_context_file_payload(
            "REFLECTION.md",
            {"content": "# Reflection\n\nSaved exactly.\n"},
            write_workspace_context_file=lambda filename, content: {"filename": filename, "content": content},
        )

        self.assertEqual(
            payload,
            {"ok": True, "filename": "REFLECTION.md", "content": "# Reflection\n\nSaved exactly.\n"},
        )


if __name__ == "__main__":
    unittest.main()
