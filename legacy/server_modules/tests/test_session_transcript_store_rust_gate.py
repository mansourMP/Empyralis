import tempfile
import unittest
from pathlib import Path
from unittest import mock

from server_modules import session_transcript_store
from server_modules.rust_runtime_kernel_client import RustKernelDecisionError


class SessionTranscriptStoreRustGateTests(unittest.TestCase):
    def test_save_session_transcript_calls_rust_before_append(self):
        with tempfile.TemporaryDirectory() as tmpdir, mock.patch.object(
            session_transcript_store,
            "_transcript_dir",
            return_value=Path(tmpdir),
        ), mock.patch.object(
            session_transcript_store.rust_runtime_kernel_client,
            "run_runtime_kernel_enforced",
            return_value={
                "ok": True,
                "decision": "allow",
                "reason": "runtime_state_store_policy_satisfied",
                "operation": "append_session_transcript",
                "next_action": "append_session_transcript",
            },
        ) as rust_gate, mock.patch.object(
            session_transcript_store,
            "save_daily_log",
        ):
            result = session_transcript_store.save_session_transcript(
                workspace_id="ws-1",
                agent_install_id=None,
                thread_id="thread-1",
                provider="openai",
                model="gpt-4o",
                messages=[{"role": "user", "content": "hello"}],
                user_message="hello",
                assistant_reply="hi",
            )

        rust_gate.assert_called_once()
        command, payload = rust_gate.call_args.args
        self.assertEqual(command, "runtime-state-store-decision")
        self.assertEqual(payload["operation"], "append_session_transcript")
        self.assertEqual(payload["state_class"], "session_transcripts")
        self.assertEqual(payload["workspace_id"], "ws-1")
        self.assertIn("summary", result)
        self.assertIn("path", result)

    def test_save_session_transcript_rust_denial_blocks_append(self):
        denied = RustKernelDecisionError(
            {
                "ok": False,
                "decision": "block",
                "reason": "workspace_access_denied",
                "operation": "append_session_transcript",
            },
            command="runtime-state-store-decision",
        )
        with tempfile.TemporaryDirectory() as tmpdir, mock.patch.object(
            session_transcript_store,
            "_transcript_dir",
            return_value=Path(tmpdir),
        ), mock.patch.object(
            session_transcript_store.rust_runtime_kernel_client,
            "run_runtime_kernel_enforced",
            side_effect=denied,
        ):
            with self.assertRaises(RuntimeError):
                session_transcript_store.save_session_transcript(
                    workspace_id="ws-1",
                    agent_install_id=None,
                    thread_id="thread-1",
                    provider="openai",
                    model="gpt-4o",
                    messages=[{"role": "user", "content": "hello"}],
                    user_message="hello",
                    assistant_reply="hi",
                )

        self.assertEqual(list(Path(tmpdir).glob("*.jsonl")), [])

    def test_save_session_transcript_unexpected_next_action_blocks_append(self):
        with tempfile.TemporaryDirectory() as tmpdir, mock.patch.object(
            session_transcript_store,
            "_transcript_dir",
            return_value=Path(tmpdir),
        ), mock.patch.object(
            session_transcript_store.rust_runtime_kernel_client,
            "run_runtime_kernel_enforced",
            return_value={
                "ok": True,
                "decision": "allow",
                "reason": "runtime_state_store_policy_satisfied",
                "operation": "append_session_transcript",
                "next_action": "write_state",
            },
        ):
            with self.assertRaises(RuntimeError) as raised:
                session_transcript_store.save_session_transcript(
                    workspace_id="ws-1",
                    agent_install_id=None,
                    thread_id="thread-1",
                    provider="openai",
                    model="gpt-4o",
                    messages=[{"role": "user", "content": "hello"}],
                    user_message="hello",
                    assistant_reply="hi",
                )

        self.assertIn("unexpected next_action", str(raised.exception))
        self.assertEqual(list(Path(tmpdir).glob("*.jsonl")), [])
