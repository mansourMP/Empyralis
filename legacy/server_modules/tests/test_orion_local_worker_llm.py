import http.client
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from server_modules.agent_turn import build_inbound_agent_turn_request

ROOT_DIR = Path(__file__).resolve().parents[2]
SCRIPTS_DIR = ROOT_DIR / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import orion_local_worker_llm as worker_llm


class OrionLocalWorkerLlmTests(unittest.TestCase):
    def test_resolve_requested_provider_preserves_explicit_anthropic_in_local_cli_mode(self):
        with patch.dict(os.environ, {"ORION_AUTH_MODE": "local_cli"}, clear=False):
            with patch.object(worker_llm, "get_claude_code_session_token", return_value="session-token"):
                provider = worker_llm.resolve_requested_provider({"provider": "anthropic"}, {})

        self.assertEqual(provider, "anthropic")

    def test_generate_chat_for_turn_request_binds_agent_turn_metadata(self):
        turn_request = build_inbound_agent_turn_request(
            workspace_id="workspace-1",
            session_id="thread-1",
            channel="local_worker",
            actor_type="worker",
            actor_id="worker-1",
            message="Summarize this",
            response_mode="artifact",
        )

        with patch.object(
            worker_llm,
            "generate_chat_reply_with_provider_fallback",
            return_value=("ok", {"provider": "openai"}, "openai", ""),
        ) as generate_mock:
            text, usage, attempted, error = worker_llm.generate_chat_reply_for_turn_request(
                turn_request=turn_request,
                context={"workspace_id": "workspace-1"},
                metadata={},
                system_prompt="You are concise.",
            )

        self.assertEqual(text, "ok")
        self.assertEqual(usage["provider"], "openai")
        self.assertEqual(attempted, "openai")
        self.assertEqual(error, "")
        passed_metadata = generate_mock.call_args.kwargs["metadata"]
        self.assertEqual(passed_metadata["agent_turn_request"]["workspace_id"], "workspace-1")
        self.assertEqual(passed_metadata["agent_turn_request"]["channel"], "local_worker")

    def test_codex_account_id_from_token_extracts_chatgpt_account(self):
        token = (
            "header."
            "eyJodHRwczovL2FwaS5vcGVuYWkuY29tL2F1dGgiOnsiY2hhdGdwdF9hY2NvdW50X2lkIjoiYWNjXzEyMyJ9fQ."
            "sig"
        )

        account_id = worker_llm.codex_account_id_from_token(token)

        self.assertEqual(account_id, "acc_123")

    def test_build_responses_input_uses_output_text_for_assistant_history(self):
        items = worker_llm._build_responses_input(
            "whats up ?",
            prior_messages=[
                {"role": "user", "content": "hello"},
                {"role": "assistant", "content": "Hello! How can I help?"},
            ],
        )

        self.assertEqual(items[0]["role"], "user")
        self.assertEqual(items[0]["content"][0]["type"], "input_text")
        self.assertEqual(items[1]["role"], "assistant")
        self.assertEqual(items[1]["content"][0]["type"], "output_text")
        self.assertEqual(items[2]["role"], "user")
        self.assertEqual(items[2]["content"][0]["type"], "input_text")

    def test_codex_instructions_returns_empty_string_when_prompt_is_empty(self):
        self.assertEqual(worker_llm.codex_instructions(None), "")
        self.assertEqual(worker_llm.codex_instructions(""), "")
        self.assertEqual(worker_llm.codex_instructions("You are concise."), "You are concise.")

    def test_provider_order_prefers_codex_cli_before_openai_in_codex_mode(self):
        with patch.dict(
            os.environ,
            {
                "ORION_AUTH_MODE": "codex",
                "ORION_LOCAL_WORKER_PREFER_DIRECT_OPENAI": "1",
                "ORION_LOCAL_WORKER_USE_CODEX_CLI": "1",
            },
            clear=False,
        ):
            with patch.object(
                worker_llm,
                "provider_has_key",
                side_effect=lambda provider: provider in {"openai", "codex_cli"},
            ):
                order = worker_llm.provider_order_for_run({}, {})
        self.assertGreaterEqual(len(order), 2)
        self.assertEqual(order[0], "codex_cli")
        self.assertEqual(order[1], "openai")

    def test_generate_chat_uses_plain_chat_transport_for_codex_auth_mode(self):
        with patch.dict(os.environ, {"ORION_AUTH_MODE": "codex"}, clear=False):
            with patch.object(worker_llm, "provider_order_for_run", return_value=["openai", "codex_cli"]):
                with patch.object(
                    worker_llm,
                    "openai_responses_text",
                    side_effect=AssertionError("responses api should be skipped for codex auth"),
                ):
                    with patch.object(
                        worker_llm,
                        "openai_chat_text",
                        return_value=("Short reply", {"total_tokens": 5}, "gpt-4.1", ""),
                    ) as openai_chat_text_mock:
                        with patch.object(
                            worker_llm,
                            "openai_chat_json",
                            side_effect=AssertionError("json helper should not be used for direct chat"),
                        ):
                            text, usage, attempted, error = worker_llm.generate_chat_reply_with_provider_fallback(
                                context={},
                                metadata={},
                                user_goal="Write a short summary",
                                system_prompt="You are concise.",
                            )

        self.assertEqual(text, "Short reply")
        self.assertIsNotNone(usage)
        self.assertEqual(attempted, "openai")
        self.assertEqual(error, "")
        openai_chat_text_mock.assert_called_once()

    def test_generate_chat_rejects_explicit_oauth_token_mode_for_openai_direct(self):
        with patch.dict(os.environ, {"ORION_AUTH_MODE": "api_key"}, clear=False):
            with patch.object(worker_llm, "provider_order_for_run", return_value=["openai"]):
                with patch.object(
                    worker_llm,
                    "openai_responses_text",
                    side_effect=AssertionError("responses api should not be used for rejected oauth_token auth mode"),
                ):
                    with patch.object(
                        worker_llm,
                        "openai_chat_text",
                        side_effect=AssertionError("chat api should not be used for rejected oauth_token auth mode"),
                    ) as openai_chat_text_mock:
                        with patch.object(
                            worker_llm,
                            "openai_chat_json",
                            side_effect=AssertionError("json helper should not be used for direct chat"),
                        ):
                            text, usage, attempted, error = worker_llm.generate_chat_reply_with_provider_fallback(
                                context={"auth_mode": "oauth_token"},
                                metadata={},
                                user_goal="Answer briefly",
                                system_prompt="You are concise.",
                            )

        self.assertEqual(text, "")
        self.assertIsNone(usage)
        self.assertEqual(attempted, "openai")
        self.assertIn(worker_llm.OPENAI_CODEX_DIRECT_AUTH_ERROR, error)
        openai_chat_text_mock.assert_not_called()

    def test_codex_exec_prefers_direct_backend_before_cli(self):
        with patch.object(
            worker_llm,
            "openai_codex_backend_text",
            return_value=("Direct Codex answer", {"total_tokens": 12}, "gpt-5.4", ""),
        ) as direct_mock:
            with patch.object(worker_llm, "codex_cli_available", return_value=True):
                text, usage, model, error = worker_llm.codex_exec_text(
                    "You are concise.",
                    "Give me the top 3 priorities for shipping this platform in 2 days.",
                )

        self.assertEqual(text, "Direct Codex answer")
        self.assertEqual(model, "gpt-5.4")
        self.assertEqual(error, "")
        self.assertEqual(usage, {"total_tokens": 12})
        direct_mock.assert_called_once()

    def test_generate_chat_passes_requested_model_to_selected_provider(self):
        with patch.object(worker_llm, "provider_order_for_run", return_value=["codex_cli"]):
            with patch.object(
                worker_llm,
                "openai_codex_backend_text",
                return_value=("Direct answer", None, "gpt-5.3-codex", ""),
            ) as codex_backend_mock:
                text, usage, attempted, error = worker_llm.generate_chat_reply_with_provider_fallback(
                    context={"model": "gpt-5.3-codex"},
                    metadata={"provider": "codex_cli", "disable_provider_fallback": True},
                    user_goal="hello",
                    system_prompt="You are concise.",
                )

        self.assertEqual(text, "Direct answer")
        self.assertEqual(attempted, "codex_cli")
        self.assertEqual(error, "")
        codex_backend_mock.assert_called_once_with(
            "You are concise.",
            "hello",
            model_override="gpt-5.3-codex",
            reasoning_effort_override=None,
            prior_messages=None,
            credential_override=None,
        )

    def test_resolve_requested_model_defaults_anthropic_to_current_default(self):
        with patch.object(worker_llm, "anthropic_default_model", return_value="claude-3-7-sonnet-20250219"):
            model = worker_llm.resolve_requested_model({"provider": "anthropic"}, {})

        self.assertEqual(model, "claude-3-7-sonnet-20250219")

    def test_generate_chat_remaps_retired_anthropic_model(self):
        with patch.object(worker_llm, "anthropic_default_model", return_value="claude-3-5-haiku-20241022"):
            with patch.object(worker_llm, "provider_order_for_run", return_value=["anthropic"]):
                with patch.object(
                    worker_llm,
                    "anthropic_chat_text",
                    return_value=("Anthropic reply", {"input_tokens": 5, "output_tokens": 7}, "claude-3-5-haiku-20241022", ""),
                ) as anthropic_mock:
                    text, usage, attempted, error = worker_llm.generate_chat_reply_with_provider_fallback(
                        context={"provider": "anthropic", "model": "claude-3-5-sonnet-20241022"},
                        metadata={"provider": "anthropic", "disable_provider_fallback": True},
                        user_goal="hello",
                        system_prompt="You are concise.",
                    )

        self.assertEqual(text, "Anthropic reply")
        self.assertEqual(attempted, "anthropic")
        self.assertEqual(error, "")
        self.assertIsNotNone(usage)
        anthropic_mock.assert_called_once_with(
            "You are concise.",
            "hello",
            model_override="claude-3-5-haiku-20241022",
            prior_messages=None,
            credential_override=None,
        )

    def test_generate_chat_coerces_unsupported_openai_model_for_codex_cli(self):
        with patch.object(worker_llm, "provider_order_for_run", return_value=["codex_cli"]):
            with patch.object(
                worker_llm,
                "openai_codex_backend_text",
                return_value=("Direct answer", None, "gpt-5.4", ""),
            ) as codex_backend_mock:
                text, usage, attempted, error = worker_llm.generate_chat_reply_with_provider_fallback(
                    context={"provider": "openai", "model": "gpt-4o-mini"},
                    metadata={},
                    user_goal="hello",
                    system_prompt="You are concise.",
                )

        self.assertEqual(text, "Direct answer")
        self.assertIsNotNone(usage)
        self.assertEqual(attempted, "codex_cli")
        self.assertEqual(error, "")
        codex_backend_mock.assert_called_once_with(
            "You are concise.",
            "hello",
            model_override="gpt-5.4",
            reasoning_effort_override=None,
            prior_messages=None,
            credential_override=None,
        )

    def test_generate_chat_fails_closed_when_codex_cli_has_only_prompt_transport(self):
        with patch.object(worker_llm, "provider_order_for_run", return_value=["codex_cli"]):
            with patch.object(
                worker_llm,
                "openai_codex_backend_text",
                return_value=("", None, "gpt-5.4", "backend_unavailable"),
            ):
                with patch.object(
                    worker_llm,
                    "codex_exec_text",
                    side_effect=AssertionError("cli prompt transport should not be used for direct chat"),
                ):
                    text, usage, attempted, error = worker_llm.generate_chat_reply_with_provider_fallback(
                        context={},
                        metadata={},
                        user_goal="hello",
                        system_prompt="You are concise.",
                    )

        self.assertEqual(text, "")
        self.assertIsNone(usage)
        self.assertEqual(attempted, "codex_cli")
        self.assertIn(worker_llm.DIRECT_CHAT_TRANSPORT_UNAVAILABLE, error)

    def test_generate_chat_routes_claude_code_cli_through_anthropic_transport(self):
        with patch.object(worker_llm, "provider_order_for_run", return_value=["claude_code_cli"]):
            with patch.object(
                worker_llm,
                "claude_code_exec_text",
                side_effect=AssertionError("claude cli prompt transport should not be used for direct chat"),
            ):
                with patch.object(
                    worker_llm,
                    "anthropic_chat_text",
                    return_value=("Claude reply", {"input_tokens": 5}, "claude-3-5-sonnet-20241022", ""),
                ) as anthropic_chat_text_mock:
                    text, usage, attempted, error = worker_llm.generate_chat_reply_with_provider_fallback(
                        context={},
                        metadata={},
                        user_goal="hello",
                        system_prompt="You are concise.",
                    )

        self.assertEqual(text, "Claude reply")
        self.assertIsNotNone(usage)
        self.assertEqual(attempted, "claude_code_cli")
        self.assertEqual(error, "")
        anthropic_chat_text_mock.assert_called_once()

    def test_generate_pack_keeps_json_transport(self):
        with patch.object(worker_llm, "provider_order_for_run", return_value=["openai"]):
            with patch.object(
                worker_llm,
                "openai_chat_json",
                return_value=({"summary": "Done", "content_plan": [], "next_steps": []}, {"total_tokens": 5}, "gpt-4.1", ""),
            ) as openai_chat_json_mock:
                result, usage, attempted, error = worker_llm.generate_pack_with_provider_fallback(
                    context={},
                    metadata={},
                    system_prompt="Structured output only.",
                    user_prompt="Build a plan.",
                )

        self.assertEqual(result["summary"], "Done")
        self.assertIsNotNone(usage)
        self.assertEqual(attempted, "openai")
        self.assertEqual(error, "")
        openai_chat_json_mock.assert_called_once()

    def test_disable_provider_fallback_locks_requested_provider(self):
        with patch.object(
            worker_llm,
            "provider_has_key",
            side_effect=lambda provider: provider in {"openai", "codex_cli"},
        ):
            order = worker_llm.provider_order_for_run(
                {"provider": "openai"},
                {"disable_provider_fallback": True},
            )

        self.assertEqual(order, ["openai"])

    def test_provider_order_honors_configured_fallback_provider(self):
        with patch.dict(
            os.environ,
            {
                "ORION_LOCAL_WORKER_PROVIDER_FALLBACK": "1",
                "ORION_LOCAL_WORKER_FALLBACK_PROVIDER": "anthropic",
            },
            clear=False,
        ):
            with patch.object(
                worker_llm,
                "provider_has_key",
                side_effect=lambda provider: provider in {"openai", "anthropic", "codex_cli"},
            ):
                order = worker_llm.provider_order_for_run({"provider": "openai"}, {})

        self.assertGreaterEqual(len(order), 2)
        self.assertEqual(order[0], "openai")
        self.assertEqual(order[1], "anthropic")

    def test_openai_chat_text_applies_provider_output_cap_to_payload(self):
        captured_payload: dict[str, object] = {}

        def _mock_request(*, base_url, api_key, payload, timeout_seconds, query_params=None):
            captured_payload.update(payload)
            return {
                "choices": [{"message": {"content": "ok"}}],
                "usage": {"prompt_tokens": 5, "completion_tokens": 3, "total_tokens": 8},
            }

        with patch.object(worker_llm, "resolve_openai_compatible_api_key", return_value="k"), patch.object(
            worker_llm,
            "resolve_openai_compatible_base_url",
            return_value="https://api.example.com/v1",
        ), patch.object(
            worker_llm,
            "_provider_limit_policy",
            return_value={"max_output_tokens": 777, "max_retry_attempts": 1, "backoff_base_seconds": 1.0, "backoff_max_seconds": 4.0, "supports_retry_after": True},
        ), patch.object(worker_llm, "_openai_compatible_chat_completion_request", side_effect=_mock_request):
            text, usage, model, error = worker_llm.openai_chat_text(
                "You are concise.",
                "Say hello",
                model_override="gpt-4.1",
                provider="openai",
            )

        self.assertEqual(text, "ok")
        self.assertEqual(error, "")
        self.assertEqual(model, "gpt-4.1")
        self.assertEqual(captured_payload.get("max_tokens"), 777)
        self.assertEqual(usage, {"prompt_tokens": 5, "completion_tokens": 3, "total_tokens": 8})

    def test_openai_compatible_supports_byok_first_provider_defaults(self):
        self.assertIn("openrouter", worker_llm.SUPPORTED_PROVIDERS)
        self.assertIn("groq", worker_llm.SUPPORTED_PROVIDERS)
        self.assertEqual(
            worker_llm.resolve_openai_compatible_base_url("openrouter"),
            "https://openrouter.ai/api/v1",
        )
        self.assertEqual(
            worker_llm.resolve_openai_compatible_base_url("groq"),
            "https://api.groq.com/openai/v1",
        )
        self.assertEqual(worker_llm.default_openai_compatible_model("groq"), "llama-3.3-70b-versatile")

    def test_byok_first_openai_compatible_providers_do_not_use_local_env_credentials(self):
        with patch.dict(
            os.environ,
            {
                "GROQ_API_KEY": "env-groq",
                "OPENROUTER_API_KEY": "env-openrouter",
                "AZURE_OPENAI_API_KEY": "env-azure",
                "AZURE_OPENAI_ENDPOINT": "https://env.openai.azure.com",
                "AZURE_OPENAI_API_VERSION": "2024-10-21",
                "CUSTOM_OPENAI_COMPATIBLE_API_KEY": "env-custom",
                "CUSTOM_OPENAI_COMPATIBLE_BASE_URL": "https://custom.example/v1",
                "CUSTOM_OPENAI_COMPATIBLE_MODEL": "custom-model",
            },
            clear=False,
        ):
            for provider in ("groq", "openrouter", "azure_openai", "custom_openai_compatible"):
                with self.subTest(provider=provider):
                    self.assertEqual(worker_llm.resolve_openai_compatible_api_key(provider), "")
            self.assertEqual(worker_llm.resolve_openai_compatible_base_url("azure_openai", model="deployment-a"), "")
            self.assertEqual(worker_llm.resolve_openai_compatible_base_url("custom_openai_compatible"), "")
            self.assertEqual(worker_llm.default_openai_compatible_model("custom_openai_compatible"), "")

    def test_azure_openai_requires_endpoint_deployment_and_api_version(self):
        base_url = worker_llm.resolve_openai_compatible_base_url(
            "azure_openai",
            credential_override={"endpoint": "https://example.openai.azure.com"},
            model="deployment-a",
        )
        self.assertEqual(
            base_url,
            "https://example.openai.azure.com/openai/deployments/deployment-a",
        )

        _text, _usage, model, error = worker_llm.openai_chat_text(
            "system",
            "hello",
            provider="azure_openai",
            credential_override={
                "api_key": "k",
                "endpoint": "https://example.openai.azure.com",
                "deployment": "deployment-a",
            },
        )
        self.assertEqual(model, "deployment-a")
        self.assertIn("API version", error)

    def test_custom_openai_compatible_requires_base_url_and_model(self):
        _text, _usage, model, error = worker_llm.openai_chat_text(
            "system",
            "hello",
            provider="custom_openai_compatible",
            credential_override={"api_key": "k"},
        )
        self.assertEqual(model, "")
        self.assertIn("model id", error)

        _text, _usage, model, error = worker_llm.openai_chat_text(
            "system",
            "hello",
            provider="custom_openai_compatible",
            model_override="my-model",
            credential_override={"api_key": "k"},
        )
        self.assertEqual(model, "my-model")
        self.assertIn("base URL", error)

    def test_generate_chat_retries_rate_limited_provider_before_fallback(self):
        with patch.object(worker_llm, "provider_order_for_run", return_value=["openai", "anthropic"]), patch.object(
            worker_llm,
            "_provider_limit_policy",
            side_effect=lambda provider, model=None: {
                "max_output_tokens": 1200,
                "max_retry_attempts": 2 if str(provider) == "openai" else 1,
                "backoff_base_seconds": 0.01,
                "backoff_max_seconds": 0.02,
                "supports_retry_after": True,
            },
        ), patch.object(worker_llm, "should_use_openai_chat_completions", return_value=True), patch.object(
            worker_llm,
            "openai_chat_text",
            return_value=("", None, "gpt-4.1", "http_429: retry-after 1"),
        ) as openai_mock, patch.object(
            worker_llm,
            "anthropic_chat_text",
            return_value=("fallback reply", {"input_tokens": 3, "output_tokens": 4}, "claude-3-7-sonnet-20250219", ""),
        ) as anthropic_mock, patch.object(worker_llm.time, "sleep") as sleep_mock:
            text, usage, attempted, error = worker_llm.generate_chat_reply_with_provider_fallback(
                context={"provider": "openai"},
                metadata={},
                user_goal="hello",
                system_prompt="You are concise.",
            )

        self.assertEqual(text, "fallback reply")
        self.assertEqual(error, "")
        self.assertEqual(attempted, "openai,anthropic")
        self.assertEqual(openai_mock.call_count, 2)
        anthropic_mock.assert_called_once()
        sleep_mock.assert_called_once()
        self.assertIsNotNone(usage)

    def test_generate_chat_logs_when_requested_provider_is_overridden(self):
        with patch.object(worker_llm, "provider_order_for_run", return_value=["codex_cli"]):
            with patch.object(
                worker_llm,
                "openai_codex_backend_text",
                return_value=("Direct answer", None, "gpt-5.4", ""),
            ):
                with patch.object(worker_llm.LOGGER, "warning") as logger_warning:
                    text, usage, attempted, error = worker_llm.generate_chat_reply_with_provider_fallback(
                        context={"provider": "anthropic"},
                        metadata={},
                        user_goal="hello",
                        system_prompt="You are concise.",
                    )

        self.assertEqual(text, "Direct answer")
        self.assertIsNotNone(usage)
        self.assertEqual(attempted, "codex_cli")
        self.assertEqual(error, "")
        logger_warning.assert_called_once()

    def test_openai_path_does_not_use_codex_auth_vault_token(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            auth_file = Path(tmpdir) / "auth.json"
            auth_file.write_text('{"tokens":{"access_token":"codex-vault-token"}}', encoding="utf-8")
            with patch.dict(
                os.environ,
                {
                    "CODEX_AUTH_FILE": str(auth_file),
                    "ORION_LOCAL_WORKER_OPENAI_API_KEY": "",
                    "OPENAI_API_KEY": "",
                    "ORION_LOCAL_WORKER_OPENAI_TOKEN": "",
                    "CODEX_OAUTH_TOKEN": "",
                    "OPENAI_OAUTH_TOKEN": "",
                    "OPENAI_ACCESS_TOKEN": "",
                },
                clear=False,
            ):
                self.assertEqual(worker_llm.get_openai_api_key(), "")
                self.assertEqual(worker_llm.get_openai_bearer_token(), "")
                self.assertEqual(worker_llm.get_codex_oauth_token(), "codex-vault-token")
                self.assertFalse(worker_llm.provider_has_key("openai"))

    def test_openai_responses_text_requires_real_api_key(self):
        with patch.dict(
            os.environ,
            {
                "ORION_LOCAL_WORKER_OPENAI_API_KEY": "",
                "OPENAI_API_KEY": "",
                "ORION_LOCAL_WORKER_OPENAI_TOKEN": "",
                "CODEX_OAUTH_TOKEN": "codex-oauth-token",
                "OPENAI_OAUTH_TOKEN": "",
                "OPENAI_ACCESS_TOKEN": "",
            },
            clear=False,
        ):
            text, usage, model, error = worker_llm.openai_responses_text(
                "You are concise.",
                "Say hello.",
            )

        self.assertEqual(text, "")
        self.assertIsNone(usage)
        self.assertEqual(model, "")
        self.assertEqual(error, worker_llm.OPENAI_API_KEY_MISSING_ERROR)

    def test_anthropic_chat_text_falls_back_to_curl_on_incomplete_read(self):
        curl_body = (
            b'{"content":[{"type":"text","text":"Desktop files listed."}],"usage":{"input_tokens":3,"output_tokens":5}}'
            b"\n__CODEX_STATUS__:200"
        )
        with patch.object(worker_llm, "resolve_anthropic_api_key", return_value="test-key"):
            with patch.object(worker_llm.urllib.request, "urlopen", side_effect=http.client.IncompleteRead(b"", 0)):
                with patch.object(worker_llm.shutil, "which", return_value="/usr/bin/curl"):
                    with patch.object(
                        worker_llm.subprocess,
                        "run",
                        return_value=subprocess.CompletedProcess(
                            args=["curl"],
                            returncode=0,
                            stdout=curl_body,
                            stderr=b"",
                        ),
                    ):
                        text, usage, model, error = worker_llm.anthropic_chat_text(
                            "You are concise.",
                            "List the files on my desktop.",
                            model_override="claude-3-7-sonnet-20250219",
                        )

        self.assertEqual(text, "Desktop files listed.")
        self.assertEqual(model, "claude-3-7-sonnet-20250219")
        self.assertEqual(error, "")
        self.assertEqual(usage, {"input_tokens": 3, "output_tokens": 5})

    def test_iter_anthropic_chat_events_falls_back_to_curl_on_incomplete_read(self):
        curl_body = (
            b'{"content":[{"type":"text","text":"Desktop files listed."}],"usage":{"input_tokens":3,"output_tokens":5}}'
            b"\n__CODEX_STATUS__:200"
        )
        with patch.object(worker_llm, "resolve_anthropic_api_key", return_value="test-key"):
            with patch.object(worker_llm.urllib.request, "urlopen", side_effect=http.client.IncompleteRead(b"", 0)):
                with patch.object(worker_llm.shutil, "which", return_value="/usr/bin/curl"):
                    with patch.object(
                        worker_llm.subprocess,
                        "run",
                        return_value=subprocess.CompletedProcess(
                            args=["curl"],
                            returncode=0,
                            stdout=curl_body,
                            stderr=b"",
                        ),
                    ):
                        events = list(
                            worker_llm.iter_anthropic_chat_events(
                                "You are concise.",
                                "List the files on my desktop.",
                                model_override="claude-3-7-sonnet-20250219",
                            )
                        )

        self.assertEqual(events[0]["type"], "delta")
        self.assertEqual(events[0]["delta"], "Desktop files listed.")
        self.assertEqual(events[1]["type"], "done")
        self.assertEqual(events[1]["text"], "Desktop files listed.")
        self.assertEqual(events[1]["usage"], {"input_tokens": 3, "output_tokens": 5})

    def test_ollama_enabled_when_local_service_has_models(self):
        with patch.dict(os.environ, {"ORION_LOCAL_WORKER_OLLAMA_ENABLED": "0"}, clear=False):
            with patch.object(
                worker_llm,
                "ollama_service_status",
                return_value={"reachable": True, "has_models": True, "models": ["llama3.2"]},
            ):
                self.assertTrue(worker_llm.ollama_enabled())

    def test_ollama_disabled_when_local_service_has_no_models(self):
        with patch.dict(os.environ, {"ORION_LOCAL_WORKER_OLLAMA_ENABLED": "0"}, clear=False):
            with patch.object(
                worker_llm,
                "ollama_service_status",
                return_value={"reachable": True, "has_models": False, "models": []},
            ):
                self.assertFalse(worker_llm.ollama_enabled())


if __name__ == "__main__":
    unittest.main()
