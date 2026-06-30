import importlib.util
import sys
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch


def _load_module():
    scripts_dir = Path(__file__).resolve().parents[2] / "scripts"
    module_path = scripts_dir / "orion_local_worker_llm.py"
    sys.path.insert(0, str(scripts_dir))
    try:
        spec = importlib.util.spec_from_file_location("test_orion_local_worker_llm_module", module_path)
        module = importlib.util.module_from_spec(spec)
        assert spec and spec.loader
        spec.loader.exec_module(module)
        return module
    finally:
        if sys.path and sys.path[0] == str(scripts_dir):
            sys.path.pop(0)


worker_llm = _load_module()


class LocalWorkerProviderOrderTests(TestCase):
    @patch.dict(
        worker_llm.os.environ,
        {
            "ORION_AUTH_MODE": "codex",
            "ORION_LOCAL_WORKER_PROVIDER_FALLBACK": "1",
            "ORION_LOCAL_WORKER_USE_CODEX_CLI": "1",
        },
        clear=False,
    )
    @patch.object(worker_llm, "provider_has_key")
    def test_codex_mode_prefers_codex_cli_before_openai(self, mock_provider_has_key):
        mock_provider_has_key.side_effect = lambda provider: provider in {"codex_cli", "openai"}

        order = worker_llm.provider_order_for_run({}, {})

        self.assertGreaterEqual(len(order), 2)
        self.assertEqual(order[0], "codex_cli")
        self.assertEqual(order[1], "openai")

    @patch.dict(
        worker_llm.os.environ,
        {
            "ORION_AUTH_MODE": "codex",
            "ORION_LOCAL_WORKER_PROVIDER_FALLBACK": "1",
            "ORION_LOCAL_WORKER_USE_CODEX_CLI": "1",
        },
        clear=False,
    )
    @patch.object(worker_llm, "provider_has_key")
    def test_telegram_autopilot_codex_mode_uses_codex_cli_only(self, mock_provider_has_key):
        mock_provider_has_key.side_effect = lambda provider: provider in {"codex_cli", "openai", "claude_code_cli"}

        order = worker_llm.provider_order_for_run({}, {"source": "telegram_autopilot"})

        self.assertEqual(order, ["codex_cli"])

    @patch.dict(
        worker_llm.os.environ,
        {
            "ORION_AUTH_MODE": "codex",
            "ORION_LOCAL_WORKER_PROVIDER_FALLBACK": "1",
            "ORION_LOCAL_WORKER_USE_CODEX_CLI": "1",
            "ORION_LOCAL_WORKER_PROVIDER": "codex_cli",
        },
        clear=False,
    )
    @patch.object(worker_llm, "provider_has_key")
    def test_explicit_requested_provider_stays_first_even_when_codex_mode_is_active(self, mock_provider_has_key):
        mock_provider_has_key.side_effect = lambda provider: provider in {"codex_cli", "anthropic"}

        order = worker_llm.provider_order_for_run({"provider": "anthropic"}, {})

        self.assertGreaterEqual(len(order), 2)
        self.assertEqual(order[0], "anthropic")
        self.assertEqual(order[1], "codex_cli")
