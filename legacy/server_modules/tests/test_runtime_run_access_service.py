import unittest

from fastapi import HTTPException

from server_modules import runtime_run_access_service


class _Request:
    def __init__(self) -> None:
        self.metadata = {}


class RuntimeRunAccessServiceTests(unittest.TestCase):
    def test_extract_run_owner_user_id_reads_direct_and_context_metadata(self):
        self.assertEqual(
            runtime_run_access_service.extract_run_owner_user_id({"owner_user_id": "user-1"}),
            "user-1",
        )
        self.assertEqual(
            runtime_run_access_service.extract_run_owner_user_id(
                {"context": {"metadata": {"owner_user_id": "user-2"}}}
            ),
            "user-2",
        )

    def test_current_user_is_privileged_accepts_api_key_and_admin_lists(self):
        self.assertTrue(
            runtime_run_access_service.current_user_is_privileged(
                {"auth_type": "api_key"},
                admin_user_ids=set(),
                admin_emails=set(),
            )
        )
        self.assertTrue(
            runtime_run_access_service.current_user_is_privileged(
                {"user_id": "user-1"},
                admin_user_ids={"user-1"},
                admin_emails=set(),
            )
        )

    def test_enforce_run_owner_access_rejects_other_user(self):
        with self.assertRaises(HTTPException):
            runtime_run_access_service.enforce_run_owner_access(
                {"user_id": "user-1"},
                {"owner_user_id": "user-2"},
                current_user_is_privileged_fn=lambda current_user: False,
                extract_run_owner_user_id_fn=lambda payload: str(payload.get("owner_user_id") or ""),
            )

    def test_stamp_request_owner_adds_owner_metadata(self):
        request = _Request()

        stamped = runtime_run_access_service.stamp_request_owner(
            request,
            {"user_id": "user-1", "email": "user@example.com", "role": "owner", "is_admin": True, "auth_type": "bearer"},
        )

        self.assertIs(stamped, request)
        self.assertEqual(stamped.metadata["owner_user_id"], "user-1")
        self.assertEqual(stamped.metadata["owner_email"], "user@example.com")
        self.assertEqual(stamped.metadata["owner_role"], "owner")
        self.assertTrue(stamped.metadata["owner_is_admin"])
        self.assertEqual(stamped.metadata["auth_type"], "bearer")


if __name__ == "__main__":
    unittest.main()
