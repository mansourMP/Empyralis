import importlib
import unittest
from unittest.mock import patch

from server_modules import model_router
from server_modules import usage_accounting_service


class ModelRouterTests(unittest.TestCase):
    def setUp(self):
        global model_router
        model_router = importlib.import_module("server_modules.model_router")

    def test_resolve_model_aliases(self):
        self.assertEqual(model_router.resolve_model("claude-sonnet"), "anthropic/claude-3-7-sonnet-20250219")
        self.assertEqual(model_router.resolve_model("gemini-flash"), "gemini/gemini-2.5-flash")
        self.assertEqual(model_router.resolve_model("gemini-pro"), "gemini/gemini-2.5-pro")
        self.assertEqual(model_router.resolve_model("gpt-4o-mini"), "gpt-4o-mini")
        self.assertEqual(model_router.resolve_model("deepseek-chat", provider="deepseek"), "deepseek-chat")
        self.assertEqual(model_router.resolve_model("qwen-plus", provider="qwen"), "qwen-plus")
        self.assertEqual(model_router.resolve_model("mistral-large-latest", provider="mistral"), "mistral-large-latest")
        self.assertEqual(model_router.resolve_model("llama3.2", provider="ollama"), "llama3.2")

    def test_infer_provider_supports_openai_compatible_and_local_catalogs(self):
        self.assertEqual(model_router.infer_provider("deepseek-chat"), "deepseek")
        self.assertEqual(model_router.infer_provider("deepseek-reasoner"), "deepseek")
        self.assertEqual(model_router.infer_provider("qwen-plus"), "qwen")
        self.assertEqual(model_router.infer_provider("mistral-large-latest"), "mistral")
        self.assertEqual(model_router.infer_provider("llama3.2"), "ollama")

    def test_infer_provider_flags_leftover_vertex_model_strings_instead_of_openai_default(self):
        # Vertex AI was removed as a provider entirely. A leftover stored
        # "vertex_ai/..." (or bare "vertex...") model string must still
        # resolve to the "vertex" provider id here rather than silently
        # falling through to the "openai" default below it -- that default
        # is what provider_catalog_service.resolve_provider_model_selection
        # relies on to raise "Unsupported provider" instead of misrouting a
        # leftover Gemini-on-Vertex model string to the OpenAI adapter.
        self.assertEqual(model_router.infer_provider("vertex_ai/gemini-1.5-pro"), "vertex")
        self.assertEqual(model_router.infer_provider("vertex-gemini-pro"), "vertex")

    def test_normalize_messages_filters_invalid_shapes(self):
        messages = model_router.normalize_messages(
            [
                {"role": "system", "content": "sys"},
                {"role": "invalid-role", "content": 12},
                "skip-me",
                {"content": None},
            ]
        )
        self.assertEqual(
            messages,
            [
                {"role": "system", "content": "sys"},
                {"role": "user", "content": "12"},
                {"role": "user", "content": ""},
            ],
        )

    def test_list_model_aliases_exposes_defaults_and_providers(self):
        models = model_router.list_model_aliases()
        by_alias = {item["alias"]: item for item in models}

        self.assertIn("gpt-4o-mini", by_alias)
        self.assertIn("claude-sonnet", by_alias)
        self.assertIn("gemini-flash", by_alias)
        self.assertNotIn("vertex-gemini-flash", by_alias)
        self.assertNotIn("vertex-gemini-pro", by_alias)

        self.assertEqual(by_alias["gpt-4o-mini"]["provider"], "openai")
        self.assertEqual(by_alias["claude-sonnet"]["provider"], "anthropic")
        self.assertEqual(by_alias["gemini-flash"]["provider"], "gemini")

        self.assertTrue(by_alias["gpt-4o"]["is_global_default"])
        self.assertFalse(by_alias["gpt-4o-mini"]["is_global_default"])
        self.assertFalse(by_alias["claude-haiku"]["is_provider_default"])
        self.assertFalse(by_alias["claude-sonnet"]["is_provider_default"])
        self.assertTrue(by_alias["gemini-flash"]["is_provider_default"])
        self.assertFalse(by_alias["gemini-flash"]["is_global_default"])

    def test_call_model_sync_returns_normalized_shape(self):
        http_json_request_patcher = patch.object(model_router, "http_json_request")
        http_json_request_mock = http_json_request_patcher.start()
        self.addCleanup(http_json_request_patcher.stop)
        http_json_request_mock.return_value = {
            "status": 200,
            "json": {
                "choices": [{"message": {"content": "hello"}}],
                "usage": {"prompt_tokens": 3, "completion_tokens": 5, "total_tokens": 8},
            },
            "text": "",
            "headers": {},
        }

        result = model_router.call_model_sync(
            messages=[{"role": "user", "content": "Say hello"}],
            model="gpt-4o-mini",
            provider="openai",
            credentials={"api_key": "test-key"},
            max_tokens=100,
            temperature=0.2,
        )

        self.assertEqual(result["content"], "hello")
        self.assertEqual(result["provider"], "openai")
        self.assertEqual(result["model"], "gpt-4o-mini")
        usage = result["usage"]
        self.assertEqual(usage["prompt_tokens"], 3)
        self.assertEqual(usage["completion_tokens"], 5)
        self.assertEqual(usage["total_tokens"], 8)
        self.assertEqual(usage["estimation_mode"], "provider_usage_exact")
        self.assertEqual(usage["source_surface"], "direct_model_router")
        self.assertIn("usage_accounting", usage)
        _, kwargs = http_json_request_mock.call_args
        self.assertEqual(kwargs["payload"]["model"], "gpt-4o-mini")
        self.assertEqual(kwargs["payload"]["messages"], [{"role": "user", "content": "Say hello"}])
        self.assertEqual(kwargs["headers"]["Authorization"], "Bearer test-key")

    def test_call_model_sync_preserves_openai_cached_and_reasoning_tokens(self):
        http_json_request_patcher = patch.object(model_router, "http_json_request")
        http_json_request_mock = http_json_request_patcher.start()
        self.addCleanup(http_json_request_patcher.stop)
        http_json_request_mock.return_value = {
            "status": 200,
            "json": {
                "choices": [{"message": {"content": "hello"}}],
                "usage": {
                    "prompt_tokens": 100,
                    "completion_tokens": 40,
                    "total_tokens": 140,
                    "prompt_tokens_details": {"cached_tokens": 30},
                    "completion_tokens_details": {"reasoning_tokens": 12},
                },
            },
            "text": "",
            "headers": {},
        }

        result = model_router.call_model_sync(
            messages=[{"role": "user", "content": "Say hello"}],
            model="gpt-4o-mini",
            provider="openai",
            credentials={"api_key": "test-key"},
            max_tokens=100,
            temperature=0.2,
        )

        usage = result["usage"]
        self.assertEqual(usage["prompt_tokens"], 70)
        self.assertEqual(usage["cached_input_tokens"], 30)
        self.assertEqual(usage["cache_read_tokens"], 30)
        self.assertEqual(usage["visible_output_tokens"], 28)
        self.assertEqual(usage["reasoning_tokens"], 12)
        self.assertEqual(usage["completion_tokens"], 40)

    def test_call_model_sync_preserves_openai_compatible_usage_for_deepseek(self):
        http_json_request_patcher = patch.object(model_router, "http_json_request")
        http_json_request_mock = http_json_request_patcher.start()
        self.addCleanup(http_json_request_patcher.stop)
        http_json_request_mock.return_value = {
            "status": 200,
            "json": {
                "choices": [{"message": {"content": "deepseek ok"}}],
                "usage": {
                    "prompt_tokens": 100,
                    "prompt_cache_hit_tokens": 40,
                    "prompt_cache_miss_tokens": 60,
                    "completion_tokens": 25,
                    "total_tokens": 125,
                },
            },
            "text": "",
            "headers": {},
        }

        result = model_router.call_model_sync(
            messages=[{"role": "user", "content": "Say hello"}],
            model="deepseek-v4-flash",
            provider="deepseek",
            credentials={"api_key": "deepseek-key"},
            max_tokens=100,
            temperature=0.2,
            source_surface="durable_run",
        )

        self.assertEqual(result["content"], "deepseek ok")
        self.assertEqual(result["provider"], "deepseek")
        self.assertEqual(result["model"], "deepseek-v4-flash")
        usage = result["usage"]
        self.assertEqual(usage["prompt_tokens"], 60)
        self.assertEqual(usage["cached_input_tokens"], 40)
        self.assertEqual(usage["cache_read_tokens"], 40)
        self.assertEqual(usage["completion_tokens"], 25)
        self.assertEqual(usage["total_tokens"], 125)
        self.assertNotEqual(usage["total_tokens"], 0)
        self.assertEqual(usage["source_surface"], "durable_run")
        _, kwargs = http_json_request_mock.call_args
        self.assertEqual(kwargs["payload"]["model"], "deepseek-v4-flash")

    def test_vertex_is_no_longer_a_supported_provider(self):
        # Vertex AI was removed as a provider entirely -- provider_profiles.
        # resolve_provider_adapter (called for real, not mocked, since this
        # test exists specifically to prove there is no adapter left to
        # mock) must reject it loudly rather than routing through the old
        # legacy-adapter compatibility fallback this test used to cover.
        with self.assertRaises(RuntimeError):
            model_router.call_model_sync(
                messages=[{"role": "user", "content": "hi"}],
                model="gemini-1.5-pro",
                provider="vertex",
                credentials={"access_token": "token", "project_id": "proj", "location": "us-central1"},
            )


if __name__ == "__main__":
    unittest.main()
