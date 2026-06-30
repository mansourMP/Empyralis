import asyncio
import unittest

from server_modules.virtual_computer_runtime import InMemoryVirtualComputerRuntime


class VirtualComputerCertificationSuiteTests(unittest.TestCase):
    def _create_runtime(self) -> InMemoryVirtualComputerRuntime:
        return InMemoryVirtualComputerRuntime()

    def _create_session(self, runtime: InMemoryVirtualComputerRuntime, payload: dict | None = None) -> str:
        created = asyncio.run(runtime.create_session(payload or {"runtime_choice": "virtual_browser"}))
        return str(created.get("session_id") or "")

    def test_unapproved_payment_blocked(self):
        runtime = self._create_runtime()
        session_id = self._create_session(runtime)
        with self.assertRaises(RuntimeError):
            asyncio.run(
                runtime.execute_action(
                    {
                        "session_id": session_id,
                        "action": "click",
                        "action_args": {"x": 10, "y": 20, "target_description": "confirm payment now"},
                    }
                )
            )

    def test_unapproved_message_send_blocked(self):
        runtime = self._create_runtime()
        session_id = self._create_session(runtime)
        with self.assertRaises(RuntimeError):
            asyncio.run(
                runtime.execute_action(
                    {
                        "session_id": session_id,
                        "action": "type",
                        "action_args": {
                            "text": "Please send this now",
                            "target_description": "send message to customer",
                        },
                    }
                )
            )

    def test_private_ip_blocked(self):
        runtime = self._create_runtime()
        session_id = self._create_session(runtime)
        with self.assertRaises(RuntimeError):
            asyncio.run(
                runtime.execute_action(
                    {
                        "session_id": session_id,
                        "action": "open_url",
                        "action_args": {"url": "http://10.0.0.2/internal"},
                    }
                )
            )

    def test_metadata_endpoint_blocked(self):
        runtime = self._create_runtime()
        session_id = self._create_session(runtime)
        with self.assertRaises(RuntimeError):
            asyncio.run(
                runtime.execute_action(
                    {
                        "session_id": session_id,
                        "action": "open_url",
                        "action_args": {"url": "http://169.254.169.254/latest/meta-data"},
                    }
                )
            )

    def test_oversized_artifact_blocked(self):
        runtime = self._create_runtime()
        session_id = self._create_session(
            runtime,
            {
                "runtime_choice": "virtual_browser",
                "artifact_policy": {"max_artifact_size_bytes": 1024},
            },
        )
        with self.assertRaises(RuntimeError):
            asyncio.run(runtime.stream_screenshot({"session_id": session_id, "size_bytes": 4096}))

    def test_secret_redaction_in_logs(self):
        runtime = self._create_runtime()
        session_id = self._create_session(runtime)
        asyncio.run(
            runtime.execute_action(
                {
                    "session_id": session_id,
                    "action": "screenshot",
                    "action_args": {"page_text": "api_key=sk-testsecret123456"},
                }
            )
        )
        exported = asyncio.run(runtime.export_audit_report({"session_id": session_id}))
        report = exported.get("audit_report") or {}
        model_request = next((item for item in (report.get("events") or []) if item.get("event_type") == "model_action_request"), {})
        payload_blob = str((model_request.get("metadata") or {}).get("action_args") or "")
        self.assertNotIn("sk-testsecret123456", payload_blob)
        self.assertIn("[redacted-secret]", payload_blob)

    def test_session_ttl_kill(self):
        runtime = self._create_runtime()
        session_id = self._create_session(
            runtime,
            {
                "runtime_choice": "virtual_browser",
                "isolation_profile": {"runtime_ttl_seconds": 60},
            },
        )
        runtime._isolation_by_session[session_id].expires_at_epoch = 0.0
        with self.assertRaises(RuntimeError):
            asyncio.run(
                runtime.execute_action(
                    {
                        "session_id": session_id,
                        "action": "open_url",
                        "action_args": {"url": "https://example.com"},
                    }
                )
            )

    def test_revoked_session_cannot_resume(self):
        runtime = self._create_runtime()
        session_id = self._create_session(runtime)
        runtime._sessions[session_id]["session_revoked"] = True
        with self.assertRaises(RuntimeError):
            asyncio.run(runtime.resume_session({"session_id": session_id}))

    def test_stale_token_cannot_execute_action(self):
        runtime = self._create_runtime()
        session_id = self._create_session(
            runtime,
            {
                "runtime_choice": "virtual_browser",
                "require_session_token": True,
                "session_token": "tok_vc20",
                "session_token_expires_at_epoch": 0.0,
            },
        )
        with self.assertRaises(RuntimeError):
            asyncio.run(
                runtime.execute_action(
                    {
                        "session_id": session_id,
                        "session_token": "tok_vc20",
                        "action": "screenshot",
                        "action_args": {},
                    }
                )
            )

    def test_snapshot_does_not_include_injected_secrets(self):
        runtime = self._create_runtime()
        session_id = self._create_session(
            runtime,
            {
                "runtime_choice": "virtual_browser",
                "isolation_profile": {"allow_private_lan": True},
            },
        )
        asyncio.run(
            runtime.execute_action(
                {
                    "session_id": session_id,
                    "action": "screenshot",
                    "action_args": {"page_text": "my secret is sk-snapshotsecret123"},
                }
            )
        )
        snap = asyncio.run(runtime.snapshot_session({"session_id": session_id}))
        snapshot = snap.get("snapshot") or {}
        actions_blob = str(snapshot.get("actions") or "")
        self.assertNotIn("sk-snapshotsecret123", actions_blob)
        self.assertIn("[redacted-secret]", actions_blob)

    def test_cross_tenant_session_access_denied(self):
        runtime = self._create_runtime()
        session_id = self._create_session(
            runtime,
            {
                "runtime_choice": "virtual_browser",
                "workspace_id": "workspace_alpha",
                "tenant_id": "tenant_alpha",
            },
        )
        with self.assertRaises(RuntimeError):
            asyncio.run(
                runtime.execute_action(
                    {
                        "session_id": session_id,
                        "actor_workspace_id": "workspace_beta",
                        "actor_tenant_id": "tenant_beta",
                        "action": "screenshot",
                        "action_args": {},
                    }
                )
            )

    def test_audit_hash_chain_detects_tampering(self):
        runtime = self._create_runtime()
        session_id = self._create_session(runtime)
        asyncio.run(
            runtime.execute_action(
                {
                    "session_id": session_id,
                    "action": "screenshot",
                    "action_args": {},
                }
            )
        )
        audit_events = runtime._sessions[session_id].get("audit_events") or []
        audit_events[0]["metadata"] = {"tampered": True}
        report = (asyncio.run(runtime.export_audit_report({"session_id": session_id})).get("audit_report") or {})
        self.assertFalse(report.get("chain_valid"))


if __name__ == "__main__":
    unittest.main()
