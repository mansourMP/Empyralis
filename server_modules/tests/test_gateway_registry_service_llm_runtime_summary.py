import unittest

from server_modules import gateway_registry_service


class LlmRuntimeSummaryGrokBuildCursorCliTests(unittest.TestCase):
    """xAI Grok Build / Cursor CLI addition (2026-07-24) — direct unit
    coverage for _llm_runtime_summary's id-mapping: the top-level key a
    caller reads (e.g. llm_runtimes["grok_build"]) must correctly source
    from the RIGHT service_inventory row id ("grok_cli"/"cursor_cli" —
    see empyralis-gateway/src/health/service-inventory.ts's probeGrokBuildCli/
    probeCursorCli). A typo'd id mapping here would silently return an
    always-"unknown"/never-ready entry with no error at all — exactly the
    silent-failure shape this test guards against."""

    @staticmethod
    def _metadata(service_inventory):
        return {"service_inventory": service_inventory}

    def test_grok_build_sources_from_the_grok_cli_service_inventory_row(self) -> None:
        metadata = self._metadata([
            {"id": "grok_cli", "detected": True, "status": "ready", "metadata": {"installed": True, "authenticated": True}},
        ])
        summary = gateway_registry_service._llm_runtime_summary(metadata)
        self.assertEqual(
            summary["grok_build"],
            {"detected": True, "status": "ready", "installed": True, "authenticated": True},
        )

    def test_cursor_cli_sources_from_the_cursor_cli_service_inventory_row(self) -> None:
        metadata = self._metadata([
            {"id": "cursor_cli", "detected": True, "status": "degraded", "metadata": {"installed": True, "authenticated": False}},
        ])
        summary = gateway_registry_service._llm_runtime_summary(metadata)
        self.assertEqual(
            summary["cursor_cli"],
            {"detected": True, "status": "degraded", "installed": True, "authenticated": False},
        )

    def test_absent_rows_default_to_a_safe_not_ready_shape_never_an_error(self) -> None:
        summary = gateway_registry_service._llm_runtime_summary(self._metadata([]))
        self.assertEqual(
            summary["grok_build"],
            {"detected": False, "status": "unknown", "installed": False, "authenticated": False},
        )
        self.assertEqual(
            summary["cursor_cli"],
            {"detected": False, "status": "unknown", "installed": False, "authenticated": False},
        )

    def test_all_four_cli_subscription_runtimes_plus_ollama_are_present_and_independent(self) -> None:
        metadata = self._metadata([
            {"id": "claude_cli", "detected": True, "status": "ready", "metadata": {"installed": True, "authenticated": True}},
            {"id": "codex_cli", "detected": True, "status": "missing", "metadata": {"installed": False, "authenticated": False}},
            {"id": "grok_cli", "detected": True, "status": "degraded", "metadata": {"installed": True, "authenticated": False}},
            {"id": "cursor_cli", "detected": True, "status": "ready", "metadata": {"installed": True, "authenticated": True}},
        ])
        summary = gateway_registry_service._llm_runtime_summary(metadata)
        self.assertEqual(summary["claude_code"]["status"], "ready")
        self.assertEqual(summary["codex"]["status"], "missing")
        self.assertEqual(summary["grok_build"]["status"], "degraded")
        self.assertEqual(summary["cursor_cli"]["status"], "ready")
        self.assertIn("ollama", summary)
        self.assertIn("local_model_ready", summary)


if __name__ == "__main__":
    unittest.main()
