import queue
import unittest
from unittest.mock import patch

from server_modules import provider_profiles, runs_engine


class RunsEngineFailoverTests(unittest.TestCase):
    def setUp(self) -> None:
        provider_profiles._init()

    @patch("server_modules.runs_core.emit_log")
    @patch("server_modules.runs_engine._mark_profile_failure")
    @patch("server_modules.runs_engine._mark_profile_success")
    @patch("server_modules.runs_engine.resolve_provider_adapter")
    def test_openai_rate_limit_does_not_fallback_without_explicit_policy(
        self,
        resolve_provider_adapter_mock,
        mark_profile_success_mock,
        mark_profile_failure_mock,
        emit_log_mock,
    ):
        class _FakeAdapter:
            def __init__(self):
                self.calls = []

            def generate(self, system_prompt, user_input, model, credentials):
                self.calls.append(model)
                if model == "gpt-4.1":
                    raise RuntimeError("Rate limit reached for 'gpt-4.1'. Try again shortly or switch models.")
                return "READY"

        adapter = _FakeAdapter()
        resolve_provider_adapter_mock.return_value = ("openai", "openai", adapter)

        state = {
            "provider": "openai",
            "selected_model": "gpt-4.1",
            "credential_candidates": [
                {
                    "credentials": {"access_token": "token"},
                    "profile_id": "profile-1",
                    "source": "env",
                }
            ],
        }

        with self.assertRaisesRegex(RuntimeError, "Rate limit reached"):
            runs_engine.generate_with_candidate_failover(
                state,
                {},
                queue.Queue(),
                "system",
                "user",
            )

        self.assertEqual(adapter.calls, ["gpt-4.1"])
        self.assertNotIn("active_model", state)
        mark_profile_success_mock.assert_not_called()
        mark_profile_failure_mock.assert_called_once()
        self.assertFalse(any(call.kwargs.get("event") == "profile_model_retry" for call in emit_log_mock.mock_calls))
        self.assertFalse(any(call.kwargs.get("event") == "profile_model_fallback" for call in emit_log_mock.mock_calls))

    @patch("server_modules.runs_core.emit_log")
    @patch("server_modules.runs_engine._mark_profile_failure")
    @patch("server_modules.runs_engine._mark_profile_success")
    @patch("server_modules.runs_engine.resolve_provider_adapter")
    def test_openai_rate_limit_uses_explicit_model_fallback_policy(
        self,
        resolve_provider_adapter_mock,
        mark_profile_success_mock,
        mark_profile_failure_mock,
        emit_log_mock,
    ):
        class _FakeAdapter:
            def __init__(self):
                self.calls = []

            def generate(self, system_prompt, user_input, model, credentials):
                self.calls.append(model)
                if model == "gpt-4.1":
                    raise RuntimeError("Rate limit reached for 'gpt-4.1'. Try again shortly or switch models.")
                return "READY"

        adapter = _FakeAdapter()
        resolve_provider_adapter_mock.return_value = ("openai", "openai", adapter)

        state = {
            "provider": "openai",
            "selected_model": "gpt-4.1",
            "credential_candidates": [
                {
                    "credentials": {"access_token": "token"},
                    "profile_id": "profile-1",
                    "source": "env",
                }
            ],
        }

        result = runs_engine.generate_with_candidate_failover(
            state,
            {"allow_model_fallback": True, "fallback_models": ["gpt-4o-mini"]},
            queue.Queue(),
            "system",
            "user",
        )

        self.assertEqual(result, "READY")
        self.assertEqual(adapter.calls, ["gpt-4.1", "gpt-4o-mini"])
        self.assertEqual(state["active_model"], "gpt-4o-mini")
        mark_profile_success_mock.assert_called_once_with("profile-1")
        mark_profile_failure_mock.assert_not_called()
        self.assertTrue(any(call.kwargs.get("event") == "profile_model_retry" for call in emit_log_mock.mock_calls))
        self.assertTrue(any(call.kwargs.get("event") == "profile_model_fallback" for call in emit_log_mock.mock_calls))

    @patch("server_modules.runs_core.emit_log")
    @patch("server_modules.runs_engine._mark_profile_failure")
    @patch("server_modules.runs_engine._mark_profile_success")
    @patch("server_modules.runs_engine.resolve_provider_adapter")
    def test_non_rate_limit_error_still_fails_candidate(
        self,
        resolve_provider_adapter_mock,
        mark_profile_success_mock,
        mark_profile_failure_mock,
        emit_log_mock,
    ):
        class _FakeAdapter:
            def generate(self, system_prompt, user_input, model, credentials):
                raise RuntimeError("Invalid API key.")

        resolve_provider_adapter_mock.return_value = ("openai", "openai", _FakeAdapter())

        state = {
            "provider": "openai",
            "selected_model": "gpt-4.1",
            "credential_candidates": [
                {
                    "credentials": {"access_token": "token"},
                    "profile_id": "profile-1",
                    "source": "env",
                }
            ],
        }

        with self.assertRaises(RuntimeError):
            runs_engine.generate_with_candidate_failover(
                state,
                {},
                queue.Queue(),
                "system",
                "user",
            )

        mark_profile_success_mock.assert_not_called()
        mark_profile_failure_mock.assert_called_once()
        self.assertFalse(any(call.kwargs.get("event") == "profile_model_retry" for call in emit_log_mock.mock_calls))

    @patch("server_modules.provider_profiles.OpenAIAdapter.generate", return_value="READY")
    def test_openai_direct_candidate_uses_openai_adapter(self, generate_mock):
        state = {
            "provider": "openai",
            "selected_model": "gpt-4.1",
            "credential_candidates": [
                {
                    "credentials": {"api_key": "sk-test"},
                    "profile_id": "profile-1",
                    "source": "env",
                }
            ],
        }

        result = runs_engine.generate_with_candidate_failover(
            state,
            {},
            queue.Queue(),
            "system",
            "user",
        )

        self.assertEqual(result, "READY")
        self.assertEqual(state["active_provider"], "openai")
        self.assertEqual(state["active_adapter"], "openai")
        generate_mock.assert_called_once()

    def test_openai_codex_token_candidate_fails_with_clear_direct_auth_error(self):
        state = {
            "provider": "openai",
            "selected_model": "gpt-4.1",
            "credential_candidates": [
                {
                    "credentials": {"oauth_token": "token-123", "account_id": "acct-123", "credential_type": "codex_token"},
                    "profile_id": "profile-1",
                    "source": "env",
                }
            ],
        }

        with self.assertRaisesRegex(RuntimeError, "Codex OAuth token"):
            runs_engine.generate_with_candidate_failover(
                state,
                {},
                queue.Queue(),
                "system",
                "user",
            )

    @patch("server_modules.provider_profiles._load_openai_codex_transport")
    def test_explicit_openai_codex_candidate_uses_codex_transport(self, load_transport_mock):
        worker_llm = unittest.mock.MagicMock()
        worker_llm.openai_codex_backend_text.return_value = ("READY", {"prompt_tokens": 1}, "gpt-5.4", "")
        load_transport_mock.return_value = worker_llm

        state = {
            "provider": "openai-codex",
            "selected_model": "gpt-5.4",
            "credential_candidates": [
                {
                    "credentials": {"oauth_token": "token-123", "account_id": "acct-123", "credential_type": "codex_token"},
                    "profile_id": "profile-1",
                    "source": "env",
                }
            ],
        }

        result = runs_engine.generate_with_candidate_failover(
            state,
            {},
            queue.Queue(),
            "system",
            "user",
        )

        self.assertEqual(result, "READY")
        self.assertEqual(state["active_provider"], "openai-codex")
        self.assertEqual(state["active_adapter"], "openai-codex")
        worker_llm.openai_codex_backend_text.assert_called_once()


if __name__ == "__main__":
    unittest.main()
