import asyncio
import unittest

from server_modules.virtual_computer_runtime import InMemoryVirtualComputerRuntime


class VirtualComputerAuditChainTests(unittest.TestCase):
    def test_audit_chain_is_append_only_hash_chained_and_exportable(self):
        runtime = InMemoryVirtualComputerRuntime()
        created = asyncio.run(runtime.create_session({"runtime_choice": "virtual_browser"}))
        session_id = created.get("session_id")

        asyncio.run(
            runtime.execute_action(
                {
                    "session_id": session_id,
                    "action": "screenshot",
                    "action_args": {"page_text": "api_key=sk-testsecret123456"},
                }
            )
        )
        streamed = asyncio.run(runtime.stream_screenshot({"session_id": session_id}))
        artifact_id = (streamed.get("artifact") or {}).get("artifact_id")
        asyncio.run(runtime.collect_artifact({"session_id": session_id, "artifact_id": artifact_id}))
        asyncio.run(runtime.terminate_session({"session_id": session_id}))

        exported = asyncio.run(runtime.export_audit_report({"session_id": session_id}))
        report = exported.get("audit_report") or {}
        self.assertTrue(report.get("chain_valid"))
        self.assertGreaterEqual(int(report.get("event_count") or 0), 8)
        event_types = [str((item or {}).get("event_type") or "") for item in (report.get("events") or [])]
        self.assertIn("session_created", event_types)
        self.assertIn("policy_bundle", event_types)
        self.assertIn("model_action_request", event_types)
        self.assertIn("approval_decision", event_types)
        self.assertIn("executed_action", event_types)
        self.assertIn("screenshot_hash", event_types)
        self.assertIn("artifact_hash", event_types)
        self.assertIn("session_terminated", event_types)

        model_request = next((item for item in (report.get("events") or []) if item.get("event_type") == "model_action_request"), {})
        request_blob = str((model_request.get("metadata") or {}).get("action_args") or "")
        self.assertNotIn("sk-testsecret123456", request_blob)
        self.assertIn("[redacted-secret]", request_blob)

    def test_denied_action_is_audited(self):
        runtime = InMemoryVirtualComputerRuntime()
        created = asyncio.run(runtime.create_session({"runtime_choice": "virtual_browser"}))
        session_id = created.get("session_id")

        with self.assertRaises(RuntimeError):
            asyncio.run(
                runtime.execute_action(
                    {
                        "session_id": session_id,
                        "action": "type",
                        "action_args": {"text": "hello", "target_description": "login form submit field"},
                    }
                )
            )

        exported = asyncio.run(runtime.export_audit_report({"session_id": session_id}))
        report = exported.get("audit_report") or {}
        denied = [
            item
            for item in (report.get("events") or [])
            if item.get("event_type") == "approval_decision" and item.get("decision") == "denied"
        ]
        self.assertTrue(denied)

    def test_tampering_breaks_audit_chain_validation(self):
        runtime = InMemoryVirtualComputerRuntime()
        created = asyncio.run(runtime.create_session({"runtime_choice": "virtual_browser"}))
        session_id = created.get("session_id")
        asyncio.run(
            runtime.execute_action(
                {
                    "session_id": session_id,
                    "action": "open_url",
                    "action_args": {"url": "https://example.com"},
                }
            )
        )

        session = runtime._sessions[session_id]
        audit_events = session.get("audit_events") or []
        audit_events[0]["metadata"] = {"tampered": True}

        exported = asyncio.run(runtime.export_audit_report({"session_id": session_id}))
        report = exported.get("audit_report") or {}
        self.assertFalse(report.get("chain_valid"))


if __name__ == "__main__":
    unittest.main()
