from __future__ import annotations

import asyncio
import unittest
from unittest.mock import AsyncMock, patch

from server_modules import codex_model_catalog_service


def _run(coro):
    return asyncio.run(coro)


class FetchCodexModelCatalogTests(unittest.TestCase):
    """URGENT fix (2026-08-14) — the live catalog fetch that replaces the
    hand-typed MODELS_BY_PROVIDER mirror. Mirrors
    CliSubscriptionGatewayBrainTests's mocking shape in
    test_sage_agent_runtime_service.py (same execute_tool_via_gateway seam)."""

    def test_supported_runtime_returns_relayed_catalog(self):
        response = {
            "result": {
                "supported": True,
                "auth_method": "chatgpt",
                "models": [
                    {"id": "gpt-5.6-terra", "display_name": "GPT-5.6-Terra", "description": "Balanced.", "hidden": False, "is_default": True},
                    {"id": "codex-auto-review", "display_name": "Codex Auto Review", "description": "Internal.", "hidden": True, "is_default": False},
                ],
            }
        }
        with patch("server_modules.gateway_execution_service.execute_tool_via_gateway", new=AsyncMock(return_value=response)) as mock_dispatch:
            result = _run(codex_model_catalog_service.fetch_codex_model_catalog(
                gateway_id="gw-1", workspace_id="ws-1", runtime="codex", actor_id="user-1",
            ))
        self.assertTrue(result["supported"])
        self.assertEqual(result["auth_method"], "chatgpt")
        self.assertEqual(len(result["models"]), 2)
        self.assertEqual(result["models"][0]["id"], "gpt-5.6-terra")
        self.assertTrue(result["models"][0]["is_default"])
        self.assertTrue(result["models"][1]["hidden"])
        # Capability id matches the Gateway's own advertised capability
        # (empyralis-gateway/src/llm/runtime.ts's LLM_MODELS_LIST_CAPABILITY)
        # — a drift here would silently talk past every real Gateway.
        self.assertEqual(mock_dispatch.await_args.kwargs["capability_id"], "llm.models.list")
        self.assertEqual(mock_dispatch.await_args.kwargs["arguments"], {"runtime": "codex"})

    def test_unverified_runtime_short_circuits_without_a_dispatch(self):
        with patch("server_modules.gateway_execution_service.execute_tool_via_gateway", new=AsyncMock()) as mock_dispatch:
            result = _run(codex_model_catalog_service.fetch_codex_model_catalog(
                gateway_id="gw-1", workspace_id="ws-1", runtime="claude_code",
            ))
        self.assertFalse(result["supported"])
        self.assertEqual(result["models"], [])
        mock_dispatch.assert_not_awaited()

    def test_dispatch_failure_raises_a_classified_error_never_a_silent_empty_result(self):
        with patch(
            "server_modules.gateway_execution_service.execute_tool_via_gateway",
            new=AsyncMock(side_effect=ValueError("gateway offline")),
        ):
            with self.assertRaises(codex_model_catalog_service.CodexModelCatalogError) as ctx:
                _run(codex_model_catalog_service.fetch_codex_model_catalog(
                    gateway_id="gw-1", workspace_id="ws-1", runtime="codex",
                ))
        self.assertEqual(ctx.exception.status_code, 409)

    def test_model_ids_in_catalog_includes_hidden_models(self):
        catalog = {
            "models": [
                {"id": "visible-1", "hidden": False},
                {"id": "hidden-1", "hidden": True},
            ]
        }
        self.assertEqual(
            set(codex_model_catalog_service.model_ids_in_catalog(catalog)),
            {"visible-1", "hidden-1"},
        )


if __name__ == "__main__":
    unittest.main()
