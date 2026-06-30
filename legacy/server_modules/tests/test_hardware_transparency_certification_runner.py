from __future__ import annotations

import argparse
import importlib.util
from pathlib import Path
import sys
import tempfile
import unittest


SCRIPT_PATH = Path(__file__).resolve().parents[2] / "scripts" / "empyralis_hardware_transparency_certification.py"
SPEC = importlib.util.spec_from_file_location("hardware_certification_runner", SCRIPT_PATH)
cert = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
sys.modules[SPEC.name] = cert
SPEC.loader.exec_module(cert)


class HardwareTransparencyCertificationRunnerTests(unittest.TestCase):
    def test_automated_backend_gate_includes_adapter_contract_tests(self) -> None:
        self.assertIn("server_modules/tests/test_hardware_cloud_computer_adapter.py", cert.BACKEND_TESTS)
        self.assertIn("server_modules/tests/test_hardware_gateway_adapter.py", cert.BACKEND_TESTS)
        self.assertIn("server_modules/tests/test_hardware_self_hosted_node_adapter.py", cert.BACKEND_TESTS)

    def test_agent_computer_suite_is_available_with_phase_tests(self) -> None:
        self.assertIn("agent-computer", cert.VALID_SUITES)
        self.assertIn("server_modules/tests/test_agent_computer_service_script.py", cert.AGENT_COMPUTER_BACKEND_TESTS)
        self.assertIn("server_modules/tests/test_gateway_browser_attach_policy.py", cert.AGENT_COMPUTER_BACKEND_TESTS)
        self.assertIn("dist/__tests__/system-service-mode.test.js", cert.AGENT_COMPUTER_GATEWAY_TESTS)

    def test_gateway_missing_inputs_fail_with_setup_details(self) -> None:
        args = argparse.Namespace(gateway_id="")
        with tempfile.TemporaryDirectory() as tmp:
            steps = cert.gateway_steps(args, Path(tmp))

        self.assertEqual(len(steps), 1)
        self.assertEqual(steps[0].status, "FAIL")
        self.assertIn("--gateway-id", steps[0].details["missing_flags"])
        self.assertEqual(steps[0].details["env_alternatives"]["gateway-id"], "EMPYRALIS_CERT_GATEWAY_ID")

    def test_self_hosted_missing_inputs_fail_with_setup_details(self) -> None:
        args = argparse.Namespace(self_hosted_profile_id="", self_hosted_node_token="", self_hosted_node_id="")
        with tempfile.TemporaryDirectory() as tmp:
            steps = cert.self_hosted_steps(args, Path(tmp))

        self.assertEqual(len(steps), 1)
        self.assertEqual(steps[0].status, "FAIL")
        self.assertIn("--self-hosted-profile-id", steps[0].details["missing_flags"])
        self.assertIn("--self-hosted-node-token", steps[0].details["missing_flags"])

    def test_runtime_session_assertion_enforces_request_correlation(self) -> None:
        with self.assertRaises(cert.CertificationError) as raised:
            cert._assert_runtime_session(
                {
                    "status": "completed",
                    "runtime_session": {
                        "canonical_runtime_target": "user_device_gateway",
                        "request_id": "wrong",
                        "runtime_access_mode": "full_access",
                        "state": "ready",
                    },
                },
                runtime_target="user_device_gateway",
                request_id="expected",
                access_mode="full_access",
                states={"ready"},
            )

        self.assertIn("request_id", str(raised.exception))

    def test_cert_ids_collects_runtime_artifact_and_session_ids(self) -> None:
        ids = cert._cert_ids(
            {
                "trace_id": "trace-1",
                "runtime_session": {
                    "session_id": "hrs-1",
                    "request_id": "req-1",
                    "runtime_node_id": "node-1",
                    "artifacts": ["artifact-1"],
                },
            }
        )

        self.assertEqual(ids["trace_id"], "trace-1")
        self.assertEqual(ids["runtime_session_id"], "hrs-1")
        self.assertEqual(ids["request_id"], "req-1")
        self.assertEqual(ids["runtime_node_id"], "node-1")
        self.assertEqual(ids["artifact_ids"], ["artifact-1"])

    def test_transcript_guard_rejects_mixed_delimiter_dsml(self) -> None:
        with self.assertRaises(cert.CertificationError) as raised:
            cert._assert_safe_transcript_events(
                [
                    {
                        "event": "step",
                        "schema_version": 1,
                        "payload": {
                            "event_type": "tool.progress",
                            "data": {"label": "<|｜DSML｜|tool_calls>"},
                        },
                    }
                ]
            )

        self.assertIn("internal tool markup", str(raised.exception))

    def test_launch_gate_final_steps_fail_on_skips(self) -> None:
        args = argparse.Namespace(launch_gate=True, suite="full")
        results = [cert.StepResult("frontend_typecheck", "SKIP", [], 0.0, "not run")]
        with tempfile.TemporaryDirectory() as tmp:
            steps = cert.launch_gate_final_steps(args, Path(tmp), results)

        self.assertEqual(steps[0].name, "launch_gate_no_skipped_steps")
        self.assertEqual(steps[0].status, "FAIL")
        self.assertIn("skipped", steps[0].summary)

    def test_markdown_report_lists_failed_blockers(self) -> None:
        markdown = cert._render_markdown_report(
            {
                "generated_at": "2026-06-11T00:00:00Z",
                "suite": "full",
                "launch_gate": True,
                "result": "FAIL",
                "report_path": "reports/cert.json",
                "logs_dir": ".tmp/hardware_transparency_cert",
                "git": {"branch": "main", "sha": "abc123", "dirty": False},
                "environment": {"backend_url": "http://127.0.0.1:8001"},
                "steps": [
                    {
                        "name": "gateway_sage_chat_local_gateway_smoke",
                        "status": "FAIL",
                        "duration_seconds": 1.2,
                        "summary": "missing local_gateway proof",
                        "log_path": ".tmp/hardware_transparency_cert/gateway.log",
                    }
                ],
                "real_hardware_note": "real hardware required",
            }
        )

        self.assertIn("**FAIL. Do not ship", markdown)
        self.assertIn("gateway_sage_chat_local_gateway_smoke", markdown)
        self.assertIn("missing local_gateway proof", markdown)
