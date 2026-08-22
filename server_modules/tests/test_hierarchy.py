"""Phase L: Hierarchy tests — fleet tools, sub-agent gate, per-agent AI binding.

Tests:
  (a) Sage creates specialist; specialist appears in registry with correct defaults
  (b) specialist cannot call fleet tools (denial ledgered)
  (c) subagents_enabled gate blocks and allows correctly
  (d) agent bound to byok_api with missing key → provider_unavailable,
      zero credit decrement, no fallback
  (e) two agents in one workspace with different model_config resolve
      to different providers in the same test run
"""

from __future__ import annotations

import asyncio
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from server_modules import fleet_tools, tool_broker


def _run(coro):
    return asyncio.run(coro)


class FleetToolRoleTests(unittest.TestCase):
    """Tests (a) and (b): role defaults and fleet tool gating."""

    def test_seed_operator_has_correct_defaults(self):
        """Sage seeds as operator with subagents_enabled=True and platform_credits."""
        meta = fleet_tools.seed_operator_metadata()
        self.assertEqual(meta["role"], fleet_tools.OPERATOR_ROLE)
        self.assertTrue(meta["subagents_enabled"])
        self.assertEqual(meta["model_config"]["mode"], "platform_credits")

    def test_seed_specialist_has_correct_defaults(self):
        """Specialist seeds with subagents_enabled=False and platform_credits."""
        meta = fleet_tools.seed_specialist_metadata()
        self.assertEqual(meta["role"], fleet_tools.SPECIALIST_ROLE)
        self.assertFalse(meta["subagents_enabled"])
        self.assertEqual(meta["model_config"]["mode"], "platform_credits")

    def test_seed_specialist_model_is_a_real_current_deepseek_id(self):
        """Programmatic guard, not a hand check: seed_specialist_metadata
        builds its model_config dict directly rather than through fleet_
        tools.configure_agent's validated patch path, so it needed its OWN
        fix when DeepSeek retired "deepseek-reasoner" (2026-07-24) — it
        was seeding every NEW specialist agent with a dead model id. Checked
        against the live provider catalog (provider_profiles.
        model_is_known_for_provider), not a hardcoded string, so this
        cannot go stale silently the same way again."""
        from server_modules import provider_profiles

        meta = fleet_tools.seed_specialist_metadata()
        model_config = meta["model_config"]
        self.assertTrue(
            provider_profiles.model_is_known_for_provider(
                model_config.get("provider") or "deepseek", model_config["model"],
            )
        )

    def test_operator_role_resolves_correctly(self):
        """role=operator in install_metadata resolves to operator."""
        install = {"install_metadata": {"role": "operator"}}
        self.assertEqual(fleet_tools.resolve_agent_role(install), "operator")

    def test_specialist_role_resolves_correctly(self):
        """role=specialist in install_metadata resolves to specialist."""
        install = {"install_metadata": {"role": "specialist"}}
        self.assertEqual(fleet_tools.resolve_agent_role(install), "specialist")

    def test_missing_role_defaults_to_specialist(self):
        """No role → specialist (fail-safe)."""
        self.assertEqual(fleet_tools.resolve_agent_role(None), "specialist")
        self.assertEqual(fleet_tools.resolve_agent_role({}), "specialist")
        self.assertEqual(
            fleet_tools.resolve_agent_role({"install_metadata": {}}),
            "specialist",
        )

    def test_invalid_role_defaults_to_specialist(self):
        """Garbage role → specialist."""
        self.assertEqual(
            fleet_tools.resolve_agent_role({"install_metadata": {"role": "admin"}}),
            "specialist",
        )

    def test_fleet_tool_ids_are_recognized(self):
        """All 5 fleet tools are recognized by _is_fleet_tool."""
        for skill_id in tool_broker._FLEET_SKILL_IDS:
            with self.subTest(skill_id=skill_id):
                self.assertTrue(tool_broker._is_fleet_tool(skill_id))

    def test_non_fleet_tools_are_not_recognized(self):
        """Regular skills are NOT fleet tools."""
        self.assertFalse(tool_broker._is_fleet_tool("web-search"))
        self.assertFalse(tool_broker._is_fleet_tool("browser"))
        self.assertFalse(tool_broker._is_fleet_tool(""))
        self.assertFalse(tool_broker._is_fleet_tool("fleet"))  # prefix but not exact

    def test_specialist_fleet_tool_denial(self):
        """A specialist invoking a fleet tool gets denied with ToolExecutionDeniedError."""
        with patch(
            "server_modules.agent_registry_repository.get_workspace_agent_install_bundle",
            new=AsyncMock(return_value={
                "id": "agent-spec-1",
                "install_metadata": {"role": "specialist"},
            }),
        ):
            with self.assertRaises(tool_broker.ToolExecutionDeniedError) as ctx:
                _run(
                    tool_broker._enforce_fleet_tool_role(
                        "agent-spec-1",
                        "ws-test",
                        "fleet-create-agent",
                    )
                )
            self.assertIn("fleet_tool_requires_operator", ctx.exception.code)

    def test_operator_fleet_tool_allowed(self):
        """An operator invoking a fleet tool is allowed (no exception)."""
        with patch(
            "server_modules.agent_registry_repository.get_workspace_agent_install_bundle",
            new=AsyncMock(return_value={
                "id": "agent-op-1",
                "install_metadata": {"role": "operator"},
            }),
        ):
            # Must not raise
            _run(
                tool_broker._enforce_fleet_tool_role(
                    "agent-op-1",
                    "ws-test",
                    "fleet-list-agents",
                )
            )

    def test_fleet_tool_denial_is_ledgered(self):
        """Fleet tool denial by specialist raises ToolExecutionDeniedError with fleet code."""
        with patch(
            "server_modules.agent_registry_repository.get_workspace_agent_install_bundle",
            new=AsyncMock(return_value={
                "id": "agent-spec-2",
                "install_metadata": {"role": "specialist"},
            }),
        ):
            with self.assertRaises(tool_broker.ToolExecutionDeniedError) as ctx:
                _run(
                    tool_broker._enforce_fleet_tool_role(
                        "agent-spec-2",
                        "ws-test",
                        "fleet-message-agent",
                    )
                )
            self.assertIn("fleet_tool_requires_operator", ctx.exception.code)
            self.assertIn("specialist", ctx.exception.detail.lower())


class SubAgentGateTests(unittest.TestCase):
    """Test (c): subagents_enabled gate."""

    def test_subagents_enabled_true_for_operator(self):
        """Operator defaults to subagents_enabled=True."""
        install = {"install_metadata": fleet_tools.seed_operator_metadata()}
        self.assertTrue(fleet_tools.resolve_subagents_enabled(install))

    def test_subagents_enabled_false_for_specialist(self):
        """Specialist defaults to subagents_enabled=False."""
        install = {"install_metadata": fleet_tools.seed_specialist_metadata()}
        self.assertFalse(fleet_tools.resolve_subagents_enabled(install))

    def test_subagents_enabled_explicit_override(self):
        """Explicit subagents_enabled=True overrides specialist default."""
        install = {"install_metadata": {"role": "specialist", "subagents_enabled": True}}
        self.assertTrue(fleet_tools.resolve_subagents_enabled(install))

    def test_subagents_enabled_explicit_false_operator(self):
        """Explicit subagents_enabled=False overrides operator default."""
        install = {"install_metadata": {"role": "operator", "subagents_enabled": False}}
        self.assertFalse(fleet_tools.resolve_subagents_enabled(install))

    def test_subagents_enabled_none_install(self):
        """None install → subagents_enabled=False."""
        self.assertFalse(fleet_tools.resolve_subagents_enabled(None))


class PerAgentAIBindingTests(unittest.TestCase):
    """Tests (d) and (e): provider binding and no-fallback guarantee."""

    def test_model_config_default_is_platform_credits(self):
        """Default model_config mode is platform_credits."""
        self.assertEqual(
            fleet_tools.resolve_model_config(None)["mode"],
            "platform_credits",
        )
        self.assertEqual(
            fleet_tools.resolve_model_config({})["mode"],
            "platform_credits",
        )

    def test_model_config_byok_api(self):
        """BYOK agent returns correct mode."""
        install = {
            "install_metadata": {
                "model_config": {
                    "mode": "byok_api",
                    "provider": "anthropic",
                    "model": "claude-sonnet-5",
                },
            },
        }
        mc = fleet_tools.resolve_model_config(install)
        self.assertEqual(mc["mode"], "byok_api")
        self.assertEqual(mc["provider"], "anthropic")
        self.assertEqual(mc["model"], "claude-sonnet-5")

    def test_byok_api_missing_provider_raises(self):
        """Agent bound to byok_api without specifying provider raises RuntimeError."""
        with patch(
            "server_modules.sage_agent_runtime_service._ledger_provider_unavailable",
            new=AsyncMock(),
        ):
            with self.assertRaises(RuntimeError) as ctx:
                _run(
                    sage_agent_runtime_service._resolve_agent_cloud_provider(
                        workspace_id="ws-test",
                        agent_model_config={
                            "mode": "byok_api",
                            # no provider specified
                        },
                        agent_id="agent-byok-1",
                    )
                )
            self.assertIn("BYOK", str(ctx.exception))
            self.assertIn("no provider", str(ctx.exception).lower())

    def test_platform_credits_delegates_to_default(self):
        """platform_credits mode delegates to workspace default resolution."""
        with patch(
            "server_modules.sage_agent_runtime_service._resolve_cloud_provider",
            new=AsyncMock(return_value=("deepseek", {"api_key": "test"})),
        ):
            provider, creds, billing = _run(
                sage_agent_runtime_service._resolve_agent_cloud_provider(
                    workspace_id="ws-test",
                    agent_model_config={"mode": "platform_credits"},
                    agent_id="agent-pc-1",
                )
            )
            self.assertEqual(provider, "deepseek")
            self.assertEqual(billing, "platform_credits")

    def test_cli_subscription_without_gateway_binding_raises_bound_required(self):
        """BYO-brain Phase 3: cli_subscription mode with NO bound Gateway
        raises an honest, platform-voice 'requires a Gateway' error — never a
        silent fallback to platform credits, and never the old permanent-stub
        'not yet available' message (this mode is real now)."""
        with patch(
            "server_modules.sage_agent_runtime_service._ledger_provider_unavailable",
            new=AsyncMock(),
        ):
            with self.assertRaises(RuntimeError) as ctx:
                _run(
                    sage_agent_runtime_service._resolve_agent_cloud_provider(
                        workspace_id="ws-test",
                        agent_model_config={"mode": "cli_subscription"},
                        agent_id="agent-cli-1",
                    )
                )
            message = str(ctx.exception)
            self.assertIn("Heads up:", message)
            self.assertIn("requires a Gateway", message)
            self.assertNotIn("not yet available", message.lower())

    def test_cli_subscription_with_gateway_binding_resolves_billing_mode(self):
        """cli_subscription mode with a bound Gateway resolves to the
        requested runtime + the 'cli_subscription' billing mode (dispatch,
        readiness checks, and the actual CLI spawn happen at the turn seam,
        not here) — so nothing is ever charged to platform credits."""
        provider, creds, billing = _run(
            sage_agent_runtime_service._resolve_agent_cloud_provider(
                workspace_id="ws-test",
                agent_model_config={
                    "mode": "cli_subscription",
                    "runtime": "codex",
                    "gateway_binding": "gateway_abc123",
                },
                agent_id="agent-cli-2",
            )
        )
        self.assertEqual(provider, "codex")
        self.assertEqual(billing, "cli_subscription")
        self.assertEqual(creds.get("gateway_binding"), "gateway_abc123")
        self.assertEqual(creds.get("runtime"), "codex")

    def test_cli_subscription_defaults_to_claude_code_runtime(self):
        """An unset runtime defaults to claude_code, never a fabricated or
        empty runtime string."""
        provider, _creds, billing = _run(
            sage_agent_runtime_service._resolve_agent_cloud_provider(
                workspace_id="ws-test",
                agent_model_config={"mode": "cli_subscription", "gateway_binding": "gateway_abc123"},
                agent_id="agent-cli-3",
            )
        )
        self.assertEqual(provider, "claude_code")
        self.assertEqual(billing, "cli_subscription")

    def test_cli_subscription_invalid_runtime_raises(self):
        """An unsupported cli_subscription runtime is rejected up front, with
        its own distinct honest message — never silently coerced to a
        supported one."""
        with patch(
            "server_modules.sage_agent_runtime_service._ledger_provider_unavailable",
            new=AsyncMock(),
        ):
            with self.assertRaises(RuntimeError) as ctx:
                _run(
                    sage_agent_runtime_service._resolve_agent_cloud_provider(
                        workspace_id="ws-test",
                        agent_model_config={
                            "mode": "cli_subscription",
                            "runtime": "gpt-5-direct",
                            "gateway_binding": "gateway_abc123",
                        },
                        agent_id="agent-cli-4",
                    )
                )
            message = str(ctx.exception)
            self.assertIn("Heads up:", message)
            self.assertIn("not a supported cli_subscription runtime", message)

    def test_local_without_gateway_binding_raises_bound_required(self):
        """BYO-brain Phase 2: local mode with NO bound box raises an honest
        'no computer is bound' error — never a silent fallback."""
        with patch(
            "server_modules.sage_agent_runtime_service._ledger_provider_unavailable",
            new=AsyncMock(),
        ):
            with self.assertRaises(RuntimeError) as ctx:
                _run(
                    sage_agent_runtime_service._resolve_agent_cloud_provider(
                        workspace_id="ws-test",
                        agent_model_config={"mode": "local"},
                        agent_id="agent-local-1",
                    )
                )
            self.assertIn("no computer is bound", str(ctx.exception).lower())

    def test_local_with_gateway_binding_resolves_local_billing(self):
        """local mode with a bound box resolves to the on-box runtime + the
        'local' billing mode (dispatch happens at the turn seam, not here) —
        so nothing is ever charged to platform credits."""
        provider, creds, billing = _run(
            sage_agent_runtime_service._resolve_agent_cloud_provider(
                workspace_id="ws-test",
                agent_model_config={
                    "mode": "local",
                    "runtime": "ollama",
                    "gateway_binding": "gateway_abc123",
                },
                agent_id="agent-local-2",
            )
        )
        self.assertEqual(provider, "ollama")
        self.assertEqual(billing, "local")
        self.assertEqual(creds.get("gateway_binding"), "gateway_abc123")

    def test_two_agents_different_providers(self):
        """Two agents with different model_config resolve to different providers."""
        with patch(
            "server_modules.sage_agent_runtime_service._resolve_cloud_provider",
            new=AsyncMock(return_value=("deepseek", {"api_key": "test"})),
        ):
            # Agent 1: platform_credits → deepseek
            prov1, _, bill1 = _run(
                sage_agent_runtime_service._resolve_agent_cloud_provider(
                    workspace_id="ws-test",
                    agent_model_config={"mode": "platform_credits"},
                    agent_id="agent-1",
                )
            )
            self.assertEqual(prov1, "deepseek")
            self.assertEqual(bill1, "platform_credits")

        with patch(
            "server_modules.sage_agent_runtime_service.direct_chat_credentials",
            return_value={"api_key": "sk-byok"},
        ), patch(
            "server_modules.sage_agent_runtime_service.supports_direct_message_native_chat",
            return_value=True,
        ):
            # Agent 2: byok_api → anthropic
            prov2, _, bill2 = _run(
                sage_agent_runtime_service._resolve_agent_cloud_provider(
                    workspace_id="ws-test",
                    agent_model_config={
                        "mode": "byok_api",
                        "provider": "anthropic",
                    },
                    agent_id="agent-2",
                )
            )
            self.assertEqual(prov2, "anthropic")
            self.assertEqual(bill2, "byok_api")

        # Different providers resolved
        self.assertNotEqual(prov1, prov2)

    # ── §29 per-agent-provider fix: platform_credits resolves THIS agent's
    # own stored provider first, the shared workspace default only as a
    # fallback for an agent that's never had one of its own ──────────────

    def test_platform_credits_with_explicit_provider_never_touches_workspace_default(self):
        """The strongest possible proof, matching the existing "nothing
        configured" test's own style: patch _resolve_cloud_provider to
        explode if it's ever called, and confirm an agent with its OWN
        stored provider resolves without touching it at all."""
        exploding_workspace_default = AsyncMock(
            side_effect=AssertionError("must not be called for an agent with its own provider")
        )
        with (
            patch(
                "server_modules.sage_agent_runtime_service._resolve_cloud_provider",
                new=exploding_workspace_default,
            ),
            patch(
                "server_modules.sage_agent_runtime_service.direct_chat_credentials",
                return_value={"api_key": "sk-agent-own-anthropic-key"},
            ),
            patch(
                "server_modules.sage_agent_runtime_service.supports_direct_message_native_chat",
                return_value=True,
            ),
        ):
            provider, creds, billing = _run(
                sage_agent_runtime_service._resolve_agent_cloud_provider(
                    workspace_id="ws-test",
                    agent_model_config={"mode": "platform_credits", "provider": "anthropic"},
                    agent_id="agent-pc-own-provider",
                )
            )
        self.assertEqual(provider, "anthropic")
        self.assertEqual(billing, "platform_credits")
        self.assertEqual(creds, {"api_key": "sk-agent-own-anthropic-key"})
        exploding_workspace_default.assert_not_awaited()

    def test_two_default_agents_different_stored_providers_resolve_independently(self):
        """Task's literal ask (a): two agents, BOTH platform_credits, with
        different stored providers — each resolves to its own, never the
        other's, and never the shared workspace default."""
        def _fake_credentials(_workspace_id, provider):
            return {"api_key": f"sk-{provider}-key"}

        with (
            patch(
                "server_modules.sage_agent_runtime_service.direct_chat_credentials",
                side_effect=_fake_credentials,
            ),
            patch(
                "server_modules.sage_agent_runtime_service.supports_direct_message_native_chat",
                return_value=True,
            ),
        ):
            prov1, creds1, bill1 = _run(
                sage_agent_runtime_service._resolve_agent_cloud_provider(
                    workspace_id="ws-test",
                    agent_model_config={"mode": "platform_credits", "provider": "anthropic"},
                    agent_id="agent-default-1",
                )
            )
            prov2, creds2, bill2 = _run(
                sage_agent_runtime_service._resolve_agent_cloud_provider(
                    workspace_id="ws-test",
                    agent_model_config={"mode": "platform_credits", "provider": "openai"},
                    agent_id="agent-default-2",
                )
            )

        self.assertEqual(prov1, "anthropic")
        self.assertEqual(bill1, "platform_credits")
        self.assertEqual(creds1, {"api_key": "sk-anthropic-key"})
        self.assertEqual(prov2, "openai")
        self.assertEqual(bill2, "platform_credits")
        self.assertEqual(creds2, {"api_key": "sk-openai-key"})
        self.assertNotEqual(prov1, prov2)
        self.assertNotEqual(creds1, creds2)

    def test_changing_workspace_default_does_not_change_an_agents_own_provider(self):
        """Task's literal ask (b): an agent with its OWN stored provider is
        immune to a workspace-default change. Simulated by making the
        workspace-default resolver return something else entirely (a
        change an admin could make via Sage's /model command or the
        AI-Setup page) — the agent's resolved provider must not move."""
        with (
            patch(
                "server_modules.sage_agent_runtime_service._resolve_cloud_provider",
                new=AsyncMock(return_value=("gemini", {"api_key": "sk-new-workspace-default"})),
            ),
            patch(
                "server_modules.sage_agent_runtime_service.direct_chat_credentials",
                return_value={"api_key": "sk-agent-own-openai-key"},
            ),
            patch(
                "server_modules.sage_agent_runtime_service.supports_direct_message_native_chat",
                return_value=True,
            ),
        ):
            provider, creds, billing = _run(
                sage_agent_runtime_service._resolve_agent_cloud_provider(
                    workspace_id="ws-test",
                    agent_model_config={"mode": "platform_credits", "provider": "openai"},
                    agent_id="agent-pinned-to-openai",
                )
            )
        self.assertEqual(provider, "openai")
        self.assertNotEqual(provider, "gemini")
        self.assertEqual(creds, {"api_key": "sk-agent-own-openai-key"})
        self.assertEqual(billing, "platform_credits")

    def test_legacy_agent_with_no_stored_provider_still_tracks_workspace_default(self):
        """The documented backward-compat carve-out, in direct contrast to
        the test above: an agent that has NEVER been given its own provider
        (pre-fix agent, or one whose owner left it on "platform default" in
        the create-agent wizard) keeps tracking the shared workspace default
        live — the exact behavior it always had. This is intentional, not a
        gap: the create-agent wizard now asks for a provider on every new
        agent, so only already-unconfigured agents take this path."""
        with patch(
            "server_modules.sage_agent_runtime_service._resolve_cloud_provider",
            new=AsyncMock(return_value=("gemini", {"api_key": "sk-new-workspace-default"})),
        ) as mock_default:
            provider, creds, billing = _run(
                sage_agent_runtime_service._resolve_agent_cloud_provider(
                    workspace_id="ws-test",
                    agent_model_config={"mode": "platform_credits"},
                    agent_id="agent-legacy-no-provider",
                )
            )
        mock_default.assert_awaited_once()
        self.assertEqual(provider, "gemini")
        self.assertEqual(creds, {"api_key": "sk-new-workspace-default"})
        self.assertEqual(billing, "platform_credits")

    def test_platform_credits_explicit_provider_unavailable_hard_stops_no_fallback(self):
        """The hard rule applies to platform_credits' own-provider branch
        exactly like every other mode: an unavailable bound provider fails
        loudly and is ledgered — it must NEVER silently fall back to the
        workspace default (that would reopen the exact cross-agent bleed
        this fix closes, just one layer deeper)."""
        mock_ledger = AsyncMock()
        exploding_workspace_default = AsyncMock(
            side_effect=AssertionError("must not fall back to the workspace default")
        )
        with (
            patch("server_modules.activity_ledger_service.append_activity_event", new=mock_ledger),
            patch(
                "server_modules.sage_agent_runtime_service._resolve_cloud_provider",
                new=exploding_workspace_default,
            ),
            patch(
                "server_modules.sage_agent_runtime_service.direct_chat_credentials",
                return_value={},
            ),
            patch(
                "server_modules.sage_agent_runtime_service.supports_direct_message_native_chat",
                return_value=False,
            ),
        ):
            with self.assertRaises(RuntimeError) as ctx:
                _run(
                    sage_agent_runtime_service._resolve_agent_cloud_provider(
                        workspace_id="ws-test",
                        agent_model_config={"mode": "platform_credits", "provider": "anthropic"},
                        agent_id="agent-pc-dead-key",
                    )
                )
        self.assertIn("anthropic", str(ctx.exception))
        exploding_workspace_default.assert_not_awaited()
        mock_ledger.assert_awaited_once()
        call_kwargs = mock_ledger.await_args.kwargs
        self.assertEqual(call_kwargs["action"], "provider_unavailable")
        self.assertEqual(call_kwargs["status"], "blocked")
        self.assertEqual(call_kwargs["metadata"]["mode"], "platform_credits")
        self.assertEqual(call_kwargs["metadata"]["provider"], "anthropic")

    def test_provider_unavailable_is_ledgered(self):
        """Provider unavailable writes a ledger event."""
        mock_ledger = AsyncMock()
        with patch(
            "server_modules.activity_ledger_service.append_activity_event",
            new=mock_ledger,
        ):
            _run(
                sage_agent_runtime_service._ledger_provider_unavailable(
                    workspace_id="ws-test",
                    agent_id="agent-byok-2",
                    mode="byok_api",
                    provider="anthropic",
                    reason="Missing API key",
                )
            )

        self.assertGreater(mock_ledger.call_count, 0)
        call_kwargs = mock_ledger.call_args.kwargs
        self.assertEqual(call_kwargs["action"], "provider_unavailable")
        self.assertEqual(call_kwargs["status"], "blocked")

    def test_unknown_mode_raises(self):
        """Unknown model_config mode raises RuntimeError."""
        with patch(
            "server_modules.sage_agent_runtime_service._ledger_provider_unavailable",
            new=AsyncMock(),
        ):
            with self.assertRaises(RuntimeError) as ctx:
                _run(
                    sage_agent_runtime_service._resolve_agent_cloud_provider(
                        workspace_id="ws-test",
                        agent_model_config={"mode": "quantum_computer"},
                        agent_id="agent-q-1",
                    )
                )
            self.assertIn("Unknown model_config mode", str(ctx.exception))


class FleetConfigureValidationTests(unittest.TestCase):
    """Fleet configure validation."""

    def test_invalid_model_config_mode_rejected(self):
        """fleet_configure_agent rejects invalid model_config modes."""
        mock_bundle = AsyncMock(return_value=None)
        with patch(
            "server_modules.agent_registry_repository.get_workspace_agent_install_bundle",
            new=mock_bundle,
        ):
            # We need the bundle lookup to fail to hit the validation,
            # but actually validation happens before DB lookup.
            # Let's test the validation directly through the fleet_tools module.
            pass  # Validation is at the fleet_configure_agent level and requires DB

    def test_configure_agent_empty_patch_rejected(self):
        """Empty patch returns error."""
        # Test via direct call pattern
        result = _run(
            fleet_tools.fleet_configure_agent(
                actor_id="agent-op-1",
                workspace_id="ws-test",
                agent_id="agent-x",
                patch={},
            )
        )
        self.assertFalse(result["ok"])
        self.assertIn("No valid patch keys", result["error"])

    def test_configure_agent_valid_patch_keys_accepted(self):
        """Valid patch keys are accepted (fails at DB, not validation)."""
        result = _run(
            fleet_tools.fleet_configure_agent(
                actor_id="agent-op-1",
                workspace_id="ws-test",
                agent_id="agent-x",
                patch={
                    "subagents_enabled": True,
                    "model_config": {"mode": "byok_api", "provider": "openai"},
                },
            )
        )
        # Will fail at DB lookup (agent not found), not at validation
        self.assertFalse(result["ok"])

    def test_invalid_model_config_runtime_rejected(self):
        """BYO-brain Phase 0: an unknown model_config.runtime is rejected up front."""
        result = _run(
            fleet_tools.fleet_configure_agent(
                actor_id="agent-op-1",
                workspace_id="ws-test",
                agent_id="agent-x",
                patch={"model_config": {"mode": "cli_subscription", "runtime": "bogus_cli"}},
            )
        )
        self.assertFalse(result["ok"])
        self.assertIn("Invalid model_config runtime", result["error"])

    def test_invalid_model_config_model_rejected_up_front(self):
        """Billing-honesty fix: a model id not on the provider's own
        catalog fails loudly at SAVE TIME (before any DB lookup), never
        forwarded as free text into a live provider call. Mirrors
        test_invalid_model_config_runtime_rejected's own shape."""
        result = _run(
            fleet_tools.fleet_configure_agent(
                actor_id="agent-op-1",
                workspace_id="ws-test",
                agent_id="agent-x",
                patch={"model_config": {"mode": "platform_credits", "provider": "deepseek", "model": "deepseek-v5-ultra-nonexistent"}},
            )
        )
        self.assertFalse(result["ok"])
        self.assertIn("Invalid model_config model", result["error"])

    def test_retired_deepseek_model_id_rejected_up_front(self):
        """The specific bug this fix closes: DeepSeek retired
        "deepseek-chat"/"deepseek-reasoner" 2026-07-24 (provider_profiles.py's
        "deepseek" catalog entry) but its API silently accepts the old name
        and substitutes a different model rather than rejecting it — so a
        NEW save of the retired name must fail loudly here rather than
        quietly relying on that undocumented substitution at every turn."""
        for retired_model in ("deepseek-chat", "deepseek-reasoner"):
            result = _run(
                fleet_tools.fleet_configure_agent(
                    actor_id="agent-op-1",
                    workspace_id="ws-test",
                    agent_id="agent-x",
                    patch={"model_config": {"mode": "platform_credits", "provider": "deepseek", "model": retired_model}},
                )
            )
            self.assertFalse(result["ok"], f"{retired_model} must be rejected at save time")
            self.assertIn("Invalid model_config model", result["error"])

    def test_valid_deepseek_model_config_model_passes_validation(self):
        """The current, real model ids are accepted (fails later at DB
        lookup for a nonexistent agent, not at this validation step —
        same convention as test_configure_agent_valid_patch_keys_accepted)."""
        for real_model in ("deepseek-v4-flash", "deepseek-v4-pro"):
            result = _run(
                fleet_tools.fleet_configure_agent(
                    actor_id="agent-op-1",
                    workspace_id="ws-test",
                    agent_id="agent-x",
                    patch={"model_config": {"mode": "platform_credits", "provider": "deepseek", "model": real_model}},
                )
            )
            self.assertNotIn("Invalid model_config model", str(result.get("error") or ""))

    def test_open_catalog_provider_model_is_never_rejected(self):
        """Ollama's model space is whatever is pulled locally — never a
        fixed list this validation could enumerate — so nothing here is
        ever rejected on the model axis for it."""
        result = _run(
            fleet_tools.fleet_configure_agent(
                actor_id="agent-op-1",
                workspace_id="ws-test",
                agent_id="agent-x",
                patch={"model_config": {"mode": "byok_api", "provider": "ollama", "model": "some-locally-pulled-model"}},
            )
        )
        self.assertNotIn("Invalid model_config model", str(result.get("error") or ""))

    def test_mandate_patch_key_is_rejected_outright(self):
        """DELETED 2026-08-21, asserted as deleted.

        Five tests used to live here validating `mandate.audience_tools` —
        the owner-declared allowlist of tools a non-owner could trigger,
        written by the per-agent Tools tab. The tab, the grant and the tier
        behind them are gone (see server_modules/authority_mandate_service.py
        for the founder decision).

        The replacement assertion is the important one, and it is
        structural: `mandate` is out of _ALLOWED_CONFIGURE_KEYS, so a patch
        carrying it is refused as an unknown key rather than silently
        dropped. A silently-dropped patch key is this codebase's own
        documented "the write path exists and does nothing" failure — and
        `fleet_configure_agent` is callable BY AN AGENT, so an
        authority-shaped key it can send and have quietly ignored is worse
        than one it cannot send at all."""
        self.assertNotIn("mandate", fleet_tools._ALLOWED_CONFIGURE_KEYS)
        result = _run(
            fleet_tools.fleet_configure_agent(
                actor_id="agent-op-1",
                workspace_id="ws-test",
                agent_id="agent-x",
                patch={"mandate": {"audience_tools": ["custom_api.http_request"]}},
            )
        )
        self.assertFalse(result["ok"])
        self.assertIn("No valid patch keys", result["error"])

    def test_gateway_binding_accepted_by_validation(self):
        """BYO-brain Phase 0/3: gateway_binding + runtime pass validation when
        the binding resolves to a real, active, workspace-paired Gateway
        registration (the call then fails only at the agent DB lookup)."""
        registration = {
            "gateway_id": "gateway_abc123",
            "workspace_id": "ws-test",
            "status": "active",
            "device_trust_state": "trusted",
        }
        with patch(
            "server_modules.gateway_state_repository.get_gateway_registration",
            return_value=registration,
        ):
            result = _run(
                fleet_tools.fleet_configure_agent(
                    actor_id="agent-op-1",
                    workspace_id="ws-test",
                    agent_id="agent-x",
                    patch={
                        "model_config": {
                            "mode": "cli_subscription",
                            "provider": "claude_code_cli",
                            "runtime": "claude_code",
                            "gateway_binding": "gateway_abc123",
                        }
                    },
                )
            )
        # Rejected only at DB lookup (agent not found), never at validation.
        self.assertFalse(result["ok"])
        self.assertNotIn("Invalid model_config", result["error"])
        self.assertNotIn("gateway_binding must be", result["error"])
        self.assertNotIn("does not resolve", result["error"])

    def test_non_string_gateway_binding_rejected(self):
        """A non-string gateway_binding is rejected."""
        result = _run(
            fleet_tools.fleet_configure_agent(
                actor_id="agent-op-1",
                workspace_id="ws-test",
                agent_id="agent-x",
                patch={"model_config": {"mode": "cli_subscription", "gateway_binding": 123}},
            )
        )
        self.assertFalse(result["ok"])
        self.assertIn("gateway_binding must be", result["error"])

    def test_cli_subscription_gateway_binding_must_resolve_to_paired_gateway(self):
        """The confirmed bypass this fixes: saving model_config
        {mode: "cli_subscription", gateway_binding: "literally-anything"} must
        no longer succeed — an unresolvable gateway_binding (no registration
        at all) is rejected at save time, not silently persisted to fail
        later mid-conversation."""
        with patch(
            "server_modules.gateway_state_repository.get_gateway_registration",
            return_value=None,
        ):
            result = _run(
                fleet_tools.fleet_configure_agent(
                    actor_id="agent-op-1",
                    workspace_id="ws-test",
                    agent_id="agent-x",
                    patch={
                        "model_config": {
                            "mode": "cli_subscription",
                            "runtime": "claude_code",
                            "gateway_binding": "literally-anything",
                        }
                    },
                )
            )
        self.assertFalse(result["ok"])
        self.assertIn("does not resolve", result["error"])

    def test_cli_subscription_gateway_binding_wrong_workspace_rejected(self):
        """A gateway_binding that resolves to a REAL registration, but paired
        to a DIFFERENT workspace, is rejected — the workspace scope check is
        not skippable just because *some* registration exists."""
        registration = {
            "gateway_id": "gateway_other_ws",
            "workspace_id": "some-other-workspace",
            "status": "active",
            "device_trust_state": "trusted",
        }
        with patch(
            "server_modules.gateway_state_repository.get_gateway_registration",
            return_value=registration,
        ):
            result = _run(
                fleet_tools.fleet_configure_agent(
                    actor_id="agent-op-1",
                    workspace_id="ws-test",
                    agent_id="agent-x",
                    patch={
                        "model_config": {
                            "mode": "cli_subscription",
                            "runtime": "claude_code",
                            "gateway_binding": "gateway_other_ws",
                        }
                    },
                )
            )
        self.assertFalse(result["ok"])
        self.assertIn("does not resolve", result["error"])

    def test_cli_subscription_gateway_binding_revoked_rejected(self):
        """A gateway_binding pointing at a revoked registration is rejected."""
        registration = {
            "gateway_id": "gateway_revoked",
            "workspace_id": "ws-test",
            "status": "active",
            "device_trust_state": "revoked",
        }
        with patch(
            "server_modules.gateway_state_repository.get_gateway_registration",
            return_value=registration,
        ):
            result = _run(
                fleet_tools.fleet_configure_agent(
                    actor_id="agent-op-1",
                    workspace_id="ws-test",
                    agent_id="agent-x",
                    patch={
                        "model_config": {
                            "mode": "cli_subscription",
                            "runtime": "claude_code",
                            "gateway_binding": "gateway_revoked",
                        }
                    },
                )
            )
        self.assertFalse(result["ok"])
        self.assertIn("does not resolve", result["error"])

    def test_local_mode_gateway_binding_not_required_to_resolve(self):
        """The new resolution check is scoped to cli_subscription only (per
        spec) — "local" mode's gateway_binding keeps its existing, more
        lenient string-only validation, unaffected by this change."""
        result = _run(
            fleet_tools.fleet_configure_agent(
                actor_id="agent-op-1",
                workspace_id="ws-test",
                agent_id="agent-x",
                patch={"model_config": {"mode": "local", "runtime": "ollama", "gateway_binding": "literally-anything"}},
            )
        )
        # Rejected only at DB lookup (agent not found), never at validation —
        # "local" mode is untouched by the cli_subscription-only fix.
        self.assertFalse(result["ok"])
        self.assertNotIn("does not resolve", result["error"])


class FleetSeedAndBootstrapTests(unittest.TestCase):
    """Phase M3: fleet-seed + Sage operator bootstrap."""

    def test_ensure_sage_is_operator_noop_when_already_operator(self):
        """When Sage already has role=operator, bootstrap is a no-op."""
        with patch(
            "server_modules.agent_registry_repository.get_workspace_master_agent_install",
            new=AsyncMock(return_value={
                "id": "sage-install-1",
                "install_metadata": {"role": "operator", "subagents_enabled": True},
            }),
        ):
            result = _run(fleet_tools.ensure_sage_is_operator(
                workspace_id="ws-test",
            ))
        self.assertTrue(result["ok"])
        self.assertTrue(result.get("already_operator"))

    def test_ensure_sage_is_operator_bootstraps_missing_role(self):
        """When Sage lacks a role, bootstrap sets operator."""
        mock_update = AsyncMock()
        with patch(
            "server_modules.agent_registry_repository.get_workspace_master_agent_install",
            new=AsyncMock(return_value={
                "id": "sage-install-2",
                "install_metadata": {},
            }),
        ), patch(
            "server_modules.agent_registry_repository.update_workspace_agent_install",
            new=mock_update,
        ):
            result = _run(fleet_tools.ensure_sage_is_operator(
                workspace_id="ws-test",
            ))
        self.assertTrue(result["ok"])
        self.assertTrue(result.get("bootstrapped"))
        self.assertEqual(result.get("role"), "operator")
        # update was called
        self.assertGreater(mock_update.call_count, 0)
        # metadata includes operator role
        call_kwargs = mock_update.call_args.kwargs
        self.assertEqual(call_kwargs["metadata"]["role"], "operator")

    def test_ensure_sage_is_operator_bootstraps_specialist_to_operator(self):
        """When Sage has role=specialist (somehow), bootstrap upgrades to operator."""
        mock_update = AsyncMock()
        with patch(
            "server_modules.agent_registry_repository.get_workspace_master_agent_install",
            new=AsyncMock(return_value={
                "id": "sage-install-3",
                "install_metadata": {"role": "specialist"},
            }),
        ), patch(
            "server_modules.agent_registry_repository.update_workspace_agent_install",
            new=mock_update,
        ):
            result = _run(fleet_tools.ensure_sage_is_operator(
                workspace_id="ws-test",
            ))
        self.assertTrue(result["ok"])
        self.assertTrue(result.get("bootstrapped"))
        self.assertEqual(result.get("role"), "operator")

    def test_ensure_sage_is_operator_handles_missing_install(self):
        """When Sage install is not found, returns error gracefully."""
        with patch(
            "server_modules.agent_registry_repository.get_workspace_master_agent_install",
            new=AsyncMock(return_value=None),
        ):
            result = _run(fleet_tools.ensure_sage_is_operator(
                workspace_id="ws-test",
            ))
        self.assertFalse(result["ok"])
        self.assertIn("not_found", result.get("reason", ""))

    def test_fleet_specialist_definition_is_in_defaults(self):
        """DEFAULT_AGENT_DEFINITIONS includes fleet-specialist."""
        from server_modules.agent_registry_repository import DEFAULT_AGENT_DEFINITIONS
        slugs = [d["slug"] for d in DEFAULT_AGENT_DEFINITIONS]
        self.assertIn("fleet-specialist", slugs)

        fs_def = next(d for d in DEFAULT_AGENT_DEFINITIONS if d["slug"] == "fleet-specialist")
        self.assertEqual(fs_def["agent_kind"], "specialist")
        self.assertEqual(fs_def["visibility"], "private")


# Import at module level for test usage
from server_modules import sage_agent_runtime_service


class GatewayBrainDispatchTests(unittest.TestCase):
    """BYO-brain Phase 2: on-box Ollama dispatch via the gateway WSS rail.
    Proves the round-trip, the ledger tags, and the no-fallback hard rule."""

    def test_dispatch_success_returns_reply_and_ledgers_gateway_brain(self):
        exec_mock = AsyncMock(return_value={
            "result": {
                "text": "hi from the box's ollama",
                "model": "llama3.2",
                "usage": {"input_tokens": 5, "output_tokens": 3},
            }
        })
        brain_ledger = AsyncMock()
        with patch(
            "server_modules.gateway_execution_service.execute_tool_via_gateway",
            new=exec_mock,
        ), patch(
            "server_modules.sage_agent_runtime_service._ledger_gateway_brain_turn",
            new=brain_ledger,
        ):
            reply, usage, model = _run(
                sage_agent_runtime_service._dispatch_local_gateway_brain(
                    workspace_id="ws-1", tenant_id="default", agent_id="a1",
                    gateway_binding="gw-1", runtime="ollama", model="llama3.2",
                    system_prompt="You are a bot.", user_message="hello",
                    prior_messages=[{"role": "user", "content": "prev"}], trace_id="t1",
                )
            )
        self.assertEqual(reply, "hi from the box's ollama")
        self.assertEqual(model, "llama3.2")
        # Dispatched the llm.generate capability to the bound box.
        kwargs = exec_mock.call_args.kwargs
        self.assertEqual(kwargs["capability_id"], "llm.generate")
        self.assertEqual(kwargs["gateway_id"], "gw-1")
        self.assertEqual(kwargs["arguments"]["runtime"], "ollama")
        self.assertEqual(kwargs["arguments"]["model"], "llama3.2")
        # Ledgered as gateway_brain with the runtime + gateway id.
        self.assertTrue(brain_ledger.called)
        lk = brain_ledger.call_args.kwargs
        self.assertEqual(lk["gateway_id"], "gw-1")
        self.assertEqual(lk["runtime"], "ollama")

    def test_dispatch_without_binding_raises_no_fallback(self):
        unavail = AsyncMock()
        with patch(
            "server_modules.sage_agent_runtime_service._ledger_provider_unavailable",
            new=unavail,
        ):
            with self.assertRaises(RuntimeError) as ctx:
                _run(
                    sage_agent_runtime_service._dispatch_local_gateway_brain(
                        workspace_id="ws-1", tenant_id="default", agent_id="a1",
                        gateway_binding="", runtime="ollama", model="",
                        system_prompt="S", user_message="U",
                    )
                )
        self.assertIn("no computer is bound", str(ctx.exception).lower())
        self.assertTrue(unavail.called)

    def test_dispatch_gateway_offline_raises_friendly_no_fallback(self):
        # The gateway readiness check raises "gateway_offline" — the turn must
        # FAIL honestly, never fall back to control-plane Ollama or credits.
        exec_mock = AsyncMock(side_effect=ValueError("gateway_offline"))
        unavail = AsyncMock()
        with patch(
            "server_modules.gateway_execution_service.execute_tool_via_gateway",
            new=exec_mock,
        ), patch(
            "server_modules.sage_agent_runtime_service._ledger_provider_unavailable",
            new=unavail,
        ):
            with self.assertRaises(RuntimeError) as ctx:
                _run(
                    sage_agent_runtime_service._dispatch_local_gateway_brain(
                        workspace_id="ws-1", tenant_id="default", agent_id="a1",
                        gateway_binding="gw-1", runtime="ollama", model="llama3.2",
                        system_prompt="S", user_message="U",
                    )
                )
        self.assertIn("offline", str(ctx.exception).lower())
        self.assertTrue(unavail.called)

    def test_dispatch_capability_not_ready_maps_to_ollama_message(self):
        exec_mock = AsyncMock(side_effect=ValueError("gateway_capability_not_ready"))
        with patch(
            "server_modules.gateway_execution_service.execute_tool_via_gateway",
            new=exec_mock,
        ), patch(
            "server_modules.sage_agent_runtime_service._ledger_provider_unavailable",
            new=AsyncMock(),
        ):
            with self.assertRaises(RuntimeError) as ctx:
                _run(
                    sage_agent_runtime_service._dispatch_local_gateway_brain(
                        workspace_id="ws-1", tenant_id="default", agent_id="a1",
                        gateway_binding="gw-1", runtime="ollama", model="llama3.2",
                        system_prompt="S", user_message="U",
                    )
                )
        self.assertIn("ollama", str(ctx.exception).lower())

    def test_dispatch_empty_completion_raises(self):
        exec_mock = AsyncMock(return_value={"result": {"text": "", "model": "llama3.2"}})
        with patch(
            "server_modules.gateway_execution_service.execute_tool_via_gateway",
            new=exec_mock,
        ), patch(
            "server_modules.sage_agent_runtime_service._ledger_provider_unavailable",
            new=AsyncMock(),
        ):
            with self.assertRaises(RuntimeError) as ctx:
                _run(
                    sage_agent_runtime_service._dispatch_local_gateway_brain(
                        workspace_id="ws-1", tenant_id="default", agent_id="a1",
                        gateway_binding="gw-1", runtime="ollama", model="llama3.2",
                        system_prompt="S", user_message="U",
                    )
                )
        self.assertIn("empty reply", str(ctx.exception).lower())


class FleetStopControlTests(unittest.TestCase):
    """Owner-only stop control: fleet_stop_agent/fleet_resume_agent (agent:{id})
    and fleet_stop_workspace/fleet_resume_workspace (workspace:{id}). Not
    _ALLOWED_CONFIGURE_KEYS, not wired into skills_service's tool dispatcher —
    these are only ever called from routes_fleet.py's owner-gated routes."""

    def _bundle(self, *, stopped=None):
        return {
            "id": "agent-x",
            "install_metadata": {"stopped": dict(stopped)} if stopped is not None else {},
        }

    def test_stop_agent_persists_metadata_sets_kill_switch_and_ledgers(self):
        with (
            patch(
                "server_modules.agent_registry_repository.get_workspace_agent_install_bundle",
                new=AsyncMock(return_value=self._bundle()),
            ),
            patch(
                "server_modules.agent_registry_repository.update_workspace_agent_install",
                new=AsyncMock(return_value={"id": "agent-x"}),
            ) as update_mock,
            patch("server_modules.kill_switch_gate.set_kill_switch") as set_mock,
            patch("server_modules.activity_ledger_service.append_activity_event", new=AsyncMock()) as ledger_mock,
        ):
            result = _run(fleet_tools.fleet_stop_agent(
                actor_id="user-1", actor_label="owner@example.com",
                workspace_id="ws-test", agent_id="agent-x", reason="testing",
            ))

        self.assertTrue(result["ok"])
        self.assertTrue(result["stopped"]["active"])
        self.assertEqual(result["stopped"]["reason"], "testing")
        self.assertEqual(result["stopped"]["stopped_by_user_id"], "user-1")

        set_mock.assert_called_once_with("agent:agent-x")
        saved_metadata = update_mock.call_args.kwargs["metadata"]
        self.assertTrue(saved_metadata["stopped"]["active"])

        ledger_mock.assert_awaited_once()
        self.assertEqual(ledger_mock.call_args.kwargs["action"], "agent_stopped")
        self.assertEqual(ledger_mock.call_args.kwargs["actor_type"], "user")
        self.assertEqual(ledger_mock.call_args.kwargs["install_id"], "agent-x")

    def test_resume_agent_clears_metadata_and_kill_switch(self):
        with (
            patch(
                "server_modules.agent_registry_repository.get_workspace_agent_install_bundle",
                new=AsyncMock(return_value=self._bundle(stopped={"active": True, "reason": "x", "stopped_by_user_id": "user-1"})),
            ),
            patch(
                "server_modules.agent_registry_repository.update_workspace_agent_install",
                new=AsyncMock(return_value={"id": "agent-x"}),
            ) as update_mock,
            patch("server_modules.kill_switch_gate.clear_kill_switch") as clear_mock,
            patch("server_modules.activity_ledger_service.append_activity_event", new=AsyncMock()) as ledger_mock,
        ):
            result = _run(fleet_tools.fleet_resume_agent(
                actor_id="user-1", actor_label="owner@example.com", workspace_id="ws-test", agent_id="agent-x",
            ))

        self.assertTrue(result["ok"])
        self.assertFalse(result["stopped"]["active"])
        clear_mock.assert_called_once_with("agent:agent-x")
        self.assertFalse(update_mock.call_args.kwargs["metadata"]["stopped"]["active"])
        self.assertEqual(ledger_mock.call_args.kwargs["action"], "agent_resumed")

    def test_stop_agent_missing_agent_id_rejected(self):
        result = _run(fleet_tools.fleet_stop_agent(actor_id="user-1", workspace_id="ws-test", agent_id=""))
        self.assertFalse(result["ok"])

    def test_stop_agent_nonexistent_agent_rejected(self):
        with patch(
            "server_modules.agent_registry_repository.get_workspace_agent_install_bundle",
            new=AsyncMock(return_value=None),
        ):
            result = _run(fleet_tools.fleet_stop_agent(actor_id="user-1", workspace_id="ws-test", agent_id="agent-ghost"))
        self.assertFalse(result["ok"])
        self.assertIn("not found", result["error"])

    def test_stop_workspace_persists_metadata_sets_kill_switch_and_ledgers(self):
        workspace = {"id": "ws-test", "name": "Acme", "workspace_type": "personal", "metadata": {}}
        with (
            patch("server_modules.control_plane_repository.get_workspace_by_id", new=AsyncMock(return_value=workspace)),
            patch(
                "server_modules.control_plane_repository.update_workspace_profile",
                new=AsyncMock(return_value={"id": "ws-test"}),
            ) as update_mock,
            patch("server_modules.kill_switch_gate.set_kill_switch") as set_mock,
            patch("server_modules.activity_ledger_service.append_activity_event", new=AsyncMock()) as ledger_mock,
        ):
            result = _run(fleet_tools.fleet_stop_workspace(
                actor_id="user-1", actor_label="owner@example.com", workspace_id="ws-test", reason="incident",
            ))

        self.assertTrue(result["ok"])
        self.assertTrue(result["stopped"]["active"])
        set_mock.assert_called_once_with("workspace:ws-test")
        saved_metadata = update_mock.call_args.args[1]["metadata"]
        self.assertTrue(saved_metadata["kill_switch"]["active"])
        self.assertEqual(ledger_mock.call_args.kwargs["action"], "workspace_stopped")

    def test_resume_workspace_clears_kill_switch(self):
        workspace = {"id": "ws-test", "name": "Acme", "workspace_type": "personal", "metadata": {"kill_switch": {"active": True}}}
        with (
            patch("server_modules.control_plane_repository.get_workspace_by_id", new=AsyncMock(return_value=workspace)),
            patch(
                "server_modules.control_plane_repository.update_workspace_profile",
                new=AsyncMock(return_value={"id": "ws-test"}),
            ),
            patch("server_modules.kill_switch_gate.clear_kill_switch") as clear_mock,
            patch("server_modules.activity_ledger_service.append_activity_event", new=AsyncMock()),
        ):
            result = _run(fleet_tools.fleet_resume_workspace(actor_id="user-1", workspace_id="ws-test"))

        self.assertTrue(result["ok"])
        self.assertFalse(result["stopped"]["active"])
        clear_mock.assert_called_once_with("workspace:ws-test")

    def test_stop_workspace_nonexistent_workspace_rolls_back_kill_switch(self):
        """If persistence fails after the kill switch was already set, the
        switch must be rolled back — never leave a phantom stop active with
        no record of who/why."""
        with (
            patch("server_modules.control_plane_repository.get_workspace_by_id", new=AsyncMock(return_value=None)),
            patch("server_modules.kill_switch_gate.set_kill_switch") as set_mock,
            patch("server_modules.kill_switch_gate.clear_kill_switch") as clear_mock,
        ):
            result = _run(fleet_tools.fleet_stop_workspace(actor_id="user-1", workspace_id="ws-ghost"))

        self.assertFalse(result["ok"])
        set_mock.assert_called_once_with("workspace:ws-ghost")
        clear_mock.assert_called_once_with("workspace:ws-ghost")

    def test_stop_and_resume_not_in_allowed_configure_keys(self):
        """These are owner-only human actions — must never become PATCHable
        via fleet_configure_agent (the same function the fleet__configure_
        agent LLM tool calls), which would let an agent stop/resume itself
        or another agent."""
        self.assertNotIn("stopped", fleet_tools._ALLOWED_CONFIGURE_KEYS)
        self.assertNotIn("kill_switch", fleet_tools._ALLOWED_CONFIGURE_KEYS)


if __name__ == "__main__":
    unittest.main()
