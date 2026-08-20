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

    def test_absent_reasoning_effort_fields_are_unknown_none_not_an_empty_list(self):
        # A gateway built BEFORE empyralis-gateway/src/llm/codex-app-server.ts
        # started forwarding these two fields sends no key at all. That is the
        # MAJORITY live fleet state, and it means "I could not tell you" — a
        # fact that must NOT collapse into the empty list that means "the model
        # reports no levels". The two demand opposite renderings downstream
        # (static fallback ladder vs. no control at all — see
        # frontend/lib/workspace/fleet/codex-reasoning-options.ts), so
        # flattening them would silently delete the reasoning picker for most
        # of the fleet or leave a dead control on a model that implements none
        # of its options.
        response = {
            "result": {
                "supported": True,
                "auth_method": "chatgpt",
                "models": [
                    {"id": "gpt-5.6-terra", "display_name": "GPT-5.6-Terra", "hidden": False, "is_default": True},
                ],
            }
        }
        with patch("server_modules.gateway_execution_service.execute_tool_via_gateway", new=AsyncMock(return_value=response)):
            result = _run(codex_model_catalog_service.fetch_codex_model_catalog(
                gateway_id="gw-1", workspace_id="ws-1", runtime="codex",
            ))
        self.assertIsNone(result["models"][0]["default_reasoning_effort"])
        self.assertIsNone(result["models"][0]["supported_reasoning_efforts"])

    def test_a_model_positively_reporting_no_levels_is_an_empty_list_not_none(self):
        # The other side of the same distinction: the gateway answered, and
        # the answer was "none". This must be DISTINGUISHABLE from the absent
        # case above, because it is the one that must render no control.
        response = {
            "result": {
                "supported": True,
                "auth_method": "chatgpt",
                "models": [
                    {"id": "some-future-model", "display_name": "Future", "supported_reasoning_efforts": []},
                ],
            }
        }
        with patch("server_modules.gateway_execution_service.execute_tool_via_gateway", new=AsyncMock(return_value=response)):
            result = _run(codex_model_catalog_service.fetch_codex_model_catalog(
                gateway_id="gw-1", workspace_id="ws-1", runtime="codex",
            ))
        self.assertEqual(result["models"][0]["supported_reasoning_efforts"], [])
        self.assertIsNotNone(result["models"][0]["supported_reasoning_efforts"])

    def test_reasoning_efforts_parse_from_the_REAL_gateway_wire_shape_camelCase_inner_keys(self):
        # THE SEAM WHERE THE TWO HALVES OF THIS CHAIN MEET, and the one place
        # a hand-invented fixture would have hidden a real bug.
        #
        # empyralis-gateway/src/llm/runtime.ts snake_cases only the OUTER
        # field name (supported_reasoning_efforts); the entries INSIDE are
        # relayed verbatim from codex app-server, which uses camelCase
        # (reasoningEffort). So the real wire payload is snake-outer /
        # camel-inner. This fixture is that exact mixed shape, transcribed
        # from a live `model/list` response captured on 2026-08-20 by driving
        # a real `codex app-server` (0.144.1) through its own
        # initialize -> getAuthStatus -> model/list JSON-RPC exchange against
        # a real authenticated ChatGPT/Codex login.
        #
        # "ultra" is a real level on the account's own DEFAULT model and
        # exists in no documentation and in no static table in this codebase.
        # If this ever has to be "fixed" by dropping it, the fix is wrong:
        # codex types ReasoningEffort as an open string precisely so a new
        # level needs no client change.
        response = {
            "result": {
                "supported": True,
                "auth_method": "chatgpt",
                "models": [
                    {
                        "id": "gpt-5.6-terra",
                        "display_name": "GPT-5.6-Terra",
                        "hidden": False,
                        "is_default": True,
                        "default_reasoning_effort": "medium",
                        "supported_reasoning_efforts": [
                            {"reasoningEffort": "low", "description": "Fast responses with lighter reasoning"},
                            {"reasoningEffort": "medium", "description": "Balances speed and reasoning depth for everyday tasks"},
                            {"reasoningEffort": "high", "description": "Greater reasoning depth for complex problems"},
                            {"reasoningEffort": "xhigh", "description": "Extra high reasoning depth for complex problems"},
                            {"reasoningEffort": "max", "description": "Maximum reasoning depth for the hardest problems"},
                            {"reasoningEffort": "ultra", "description": "Maximum reasoning with automatic task delegation"},
                        ],
                    },
                ],
            }
        }
        with patch("server_modules.gateway_execution_service.execute_tool_via_gateway", new=AsyncMock(return_value=response)):
            result = _run(codex_model_catalog_service.fetch_codex_model_catalog(
                gateway_id="gw-1", workspace_id="ws-1", runtime="codex",
            ))
        model = result["models"][0]
        self.assertEqual(model["default_reasoning_effort"], "medium")
        levels = [e["reasoning_effort"] for e in model["supported_reasoning_efforts"]]
        self.assertEqual(levels, ["low", "medium", "high", "xhigh", "max", "ultra"])
        # Normalized to snake_case on the way OUT, because that is what
        # frontend/lib/workspace/fleet/fleet-model-config.ts reads.
        self.assertEqual(
            model["supported_reasoning_efforts"][5]["description"],
            "Maximum reasoning with automatic task delegation",
        )

    def test_a_snake_case_inner_key_is_also_accepted(self):
        # Deliberate tolerance, not an accident: accepting both spellings is
        # what makes this seam robust to whichever side normalizes first.
        response = {
            "result": {
                "supported": True,
                "models": [
                    {"id": "m", "supported_reasoning_efforts": [{"reasoning_effort": "high", "description": "d"}]},
                ],
            }
        }
        with patch("server_modules.gateway_execution_service.execute_tool_via_gateway", new=AsyncMock(return_value=response)):
            result = _run(codex_model_catalog_service.fetch_codex_model_catalog(
                gateway_id="gw-1", workspace_id="ws-1", runtime="codex",
            ))
        self.assertEqual(result["models"][0]["supported_reasoning_efforts"], [{"reasoning_effort": "high", "description": "d"}])

    def test_an_unintelligible_list_is_unknown_not_no_levels(self):
        # Something was there and nothing survived parsing. That is "I could
        # not understand the answer", which is NOT the model saying no — so it
        # degrades to the fallback ladder, never to a hidden control.
        response = {
            "result": {
                "supported": True,
                "models": [{"id": "m", "supported_reasoning_efforts": ["garbage", None, {"description": "no level key"}]}],
            }
        }
        with patch("server_modules.gateway_execution_service.execute_tool_via_gateway", new=AsyncMock(return_value=response)):
            result = _run(codex_model_catalog_service.fetch_codex_model_catalog(
                gateway_id="gw-1", workspace_id="ws-1", runtime="codex",
            ))
        self.assertIsNone(result["models"][0]["supported_reasoning_efforts"])

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
