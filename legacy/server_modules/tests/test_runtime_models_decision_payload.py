import unittest

from fastapi import HTTPException

from server_modules.runtime_models import DecisionPayload


class DecisionPayloadTests(unittest.TestCase):
    def test_accepts_once_scope_and_note(self):
        payload = DecisionPayload(decision="Proceed", scope="once", note="Allow this pending step")
        payload.validate_fields()
        self.assertEqual(payload.scope, "once")
        self.assertEqual(payload.note, "Allow this pending step")

    def test_normalizes_scope_case(self):
        payload = DecisionPayload(decision="Proceed", scope="Once")
        payload.validate_fields()
        self.assertEqual(payload.scope, "once")

    def test_rejects_non_enforced_scope(self):
        payload = DecisionPayload(decision="Proceed", scope="workflow")
        with self.assertRaises(HTTPException) as ctx:
            payload.validate_fields()
        self.assertEqual(ctx.exception.status_code, 400)


if __name__ == "__main__":
    unittest.main()
