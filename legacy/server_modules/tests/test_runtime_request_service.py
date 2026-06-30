import unittest

from fastapi import HTTPException

from server_modules import runtime_request_service


class _Request:
    def __init__(self, payload=None, error: Exception | None = None) -> None:
        self._payload = payload
        self._error = error

    async def json(self):
        if self._error is not None:
            raise self._error
        return self._payload


class RuntimeRequestServiceTests(unittest.TestCase):
    def test_require_authenticated_user_accepts_mapping(self):
        current_user = {"user_id": "user-1"}

        self.assertIs(runtime_request_service.require_authenticated_user(current_user), current_user)

    def test_require_authenticated_user_rejects_non_mapping(self):
        with self.assertRaises(HTTPException):
            runtime_request_service.require_authenticated_user(None)

    def test_read_json_payload_wraps_parse_errors(self):
        with self.assertRaises(HTTPException):
            self._run_async(
                runtime_request_service.read_json_payload(
                    _Request(error=ValueError("bad json")),
                    invalid_detail="Invalid payload",
                )
            )

    def test_read_json_object_payload_requires_dict(self):
        with self.assertRaises(HTTPException):
            self._run_async(
                runtime_request_service.read_json_object_payload(
                    _Request(payload=["not", "a", "dict"]),
                    invalid_detail="Invalid payload",
                )
            )

    def test_read_json_object_payload_returns_dict(self):
        payload = self._run_async(
            runtime_request_service.read_json_object_payload(
                _Request(payload={"ok": True}),
                invalid_detail="Invalid payload",
            )
        )

        self.assertEqual(payload, {"ok": True})

    def _run_async(self, coroutine):
        import asyncio

        return asyncio.run(coroutine)


if __name__ == "__main__":
    unittest.main()
