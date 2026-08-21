"""The creation surface's own model pick — POST /fleet/agents' `model_choice`.

The founder, 2026-08-21: *"I should be able to pick what model I am going to
use."* Everything here guards the two ways that pick can go quietly wrong:

  1. It arrives, is unusable, and an agent is created anyway on a model the
     person did not choose ("created, but not the one you asked for" — the
     outcome-honesty shape CLAUDE.md bans).
  2. The picker offers a model the save then refuses — a control that submits
     and fails, which is worse than a control that isn't there.

Plus one CROSS-LANGUAGE drift test: the id the frontend pre-selects and the
model_config the server actually seeds are read from two different files, in
two different languages, and compared. Neither can be edited alone.
"""

import asyncio
import ast
import pathlib
import re
import unittest
from unittest import mock

from server_modules import fleet_tools


REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
AGENT_CREATE_MODEL_TS = REPO_ROOT / "frontend" / "lib" / "workspace" / "fleet" / "agent-create-model.ts"
FLEET_TOOLS_PY = REPO_ROOT / "server_modules" / "fleet_tools.py"


class CreateTimeModelChoiceShapeTests(unittest.TestCase):
    """The sync half: which keys, which modes. No network, no catalog."""

    def test_absent_and_empty_both_mean_use_the_server_seed(self):
        for raw in (None, {}):
            cleaned, error = fleet_tools.validate_create_time_model_choice(raw)
            self.assertIsNone(cleaned, raw)
            self.assertEqual(error, "", raw)

    def test_a_real_pick_is_normalized_not_merely_passed_through(self):
        cleaned, error = fleet_tools.validate_create_time_model_choice(
            {"mode": " Platform_Credits ", "provider": " DeepSeek ", "model": " deepseek-v4-pro "}
        )
        self.assertEqual(error, "")
        self.assertEqual(cleaned, {"mode": "platform_credits", "provider": "deepseek", "model": "deepseek-v4-pro"})

    def test_modes_needing_a_paired_computer_are_refused_by_name(self):
        for mode in ("cli_subscription", "local"):
            cleaned, error = fleet_tools.validate_create_time_model_choice(
                {"mode": mode, "provider": "claude_code_cli", "model": "sonnet"}
            )
            self.assertIsNone(cleaned)
            self.assertIn(mode, error)

    def test_extra_keys_are_refused_loudly_never_silently_dropped(self):
        # Silently dropping would let a caller believe a gateway binding /
        # runtime / reasoning effort took effect at creation when it never
        # arrived — CLAUDE.md: stale config must fail loudly.
        for extra in ("gateway_binding", "runtime", "engine", "reasoning_effort"):
            cleaned, error = fleet_tools.validate_create_time_model_choice(
                {"mode": "platform_credits", "provider": "deepseek", "model": "deepseek-v4-pro", extra: "x"}
            )
            self.assertIsNone(cleaned, extra)
            self.assertIn(extra, error, extra)

    def test_a_non_object_is_refused_rather_than_coerced(self):
        for raw in ("platform_credits", ["platform_credits"], 7):
            cleaned, error = fleet_tools.validate_create_time_model_choice(raw)  # type: ignore[arg-type]
            self.assertIsNone(cleaned)
            self.assertNotEqual(error, "")

    def test_blank_provider_or_model_is_refused(self):
        for patch in ({"provider": ""}, {"model": ""}):
            body = {"mode": "platform_credits", "provider": "deepseek", "model": "deepseek-v4-pro"}
            body.update(patch)
            cleaned, error = fleet_tools.validate_create_time_model_choice(body)
            self.assertIsNone(cleaned)
            self.assertNotEqual(error, "")


class CreateTimeModelIdentityTests(unittest.TestCase):
    """The async half: is this model real for this provider?

    Every live call is mocked — a test may never reach a real provider
    (conftest enforces this at the socket).
    """

    def _resolve(self, raw, live=None, raises=False):
        async def fake_get_provider_models(provider, workspace_id=None, **_kw):
            if raises:
                raise RuntimeError("upstream unreachable")
            return {"models": list(live or [])}

        with mock.patch(
            "server_modules.connectors_core.get_provider_models",
            side_effect=fake_get_provider_models,
        ) as spy:
            result = asyncio.run(fleet_tools.resolve_create_time_model_choice(raw, workspace_id="ws_t"))
        return result, spy

    def test_a_catalogued_model_is_accepted_with_no_network_call_at_all(self):
        # The common case — every platform tier — must never pay a round trip.
        (cleaned, error), spy = self._resolve(
            {"mode": "platform_credits", "provider": "deepseek", "model": "deepseek-v4-pro"}
        )
        self.assertEqual(error, "")
        self.assertEqual(cleaned["model"], "deepseek-v4-pro")
        self.assertEqual(spy.call_count, 0)

    def test_a_live_only_model_is_accepted_on_the_providers_own_word(self):
        # This is the whole reason the live branch exists: the picker offers
        # the customer's OWN live list, so a real model outside our static
        # mirror must not be refused (the exact "pick it, see it, then have
        # the save rejected" gap CLAUDE.md already flags for the Model tab).
        (cleaned, error), spy = self._resolve(
            {"mode": "byok_api", "provider": "openai", "model": "gpt-5.6-brand-new"},
            live=["gpt-5.6-brand-new", "gpt-4o"],
        )
        self.assertEqual(error, "")
        self.assertEqual(cleaned["model"], "gpt-5.6-brand-new")
        self.assertEqual(spy.call_count, 1)

    def test_a_model_the_account_cannot_use_is_refused_naming_what_it_can(self):
        (cleaned, error), _ = self._resolve(
            {"mode": "byok_api", "provider": "openai", "model": "not-yours"},
            live=["gpt-4o"],
        )
        self.assertIsNone(cleaned)
        self.assertIn("not-yours", error)
        self.assertIn("gpt-4o", error)

    def test_an_unconfirmable_model_fails_CLOSED(self):
        # Uncatalogued AND unconfirmable means nothing has ever vouched for
        # this id. Accepting it would surface as an opaque provider error
        # mid-conversation instead of here, where no agent exists yet.
        for kwargs in ({"live": []}, {"raises": True}):
            (cleaned, error), _ = self._resolve(
                {"mode": "byok_api", "provider": "openai", "model": "unverifiable"}, **kwargs
            )
            self.assertIsNone(cleaned, kwargs)
            self.assertIn("unverifiable", error, kwargs)

    def test_a_shape_failure_never_reaches_the_network(self):
        (cleaned, error), spy = self._resolve({"mode": "local", "provider": "ollama", "model": "llama3"})
        self.assertIsNone(cleaned)
        self.assertNotEqual(error, "")
        self.assertEqual(spy.call_count, 0)


class CreateAgentAppliesTheChoiceTests(unittest.TestCase):
    """fleet_create_agent must refuse BEFORE it writes, and apply WHOLE."""

    def test_an_invalid_choice_refuses_before_any_agent_is_created(self):
        with mock.patch("server_modules.agent_registry_repository.create_workspace_agent_install") as upsert:
            result = asyncio.run(
                fleet_tools.fleet_create_agent(
                    actor_id="owner",
                    workspace_id="ws_t",
                    name="Ruby",
                    model_choice={"mode": "cli_subscription", "provider": "claude_code_cli", "model": "sonnet"},
                )
            )
        self.assertFalse(result.get("ok"))
        self.assertIn("cli_subscription", result.get("error", ""))
        self.assertEqual(upsert.call_count, 0, "an unusable model pick must never produce an agent")

    def test_the_seeded_default_is_what_survives_when_nothing_is_picked(self):
        seeded = fleet_tools.seed_specialist_metadata()["model_config"]
        self.assertEqual(seeded.get("mode"), "platform_credits")
        self.assertTrue(str(seeded.get("model") or "").strip())


class CreateTimeModelChoiceStructureTests(unittest.TestCase):
    """Structural guards a behavioural test cannot give.

    Both of the shapes banned here type-check, run, and are silent.
    """

    def _fleet_tools_ast(self):
        return ast.parse(FLEET_TOOLS_PY.read_text(encoding="utf-8"))

    def test_the_create_path_accepts_a_STRICT_SUBSET_of_what_patch_accepts(self):
        # The narrowing is the whole safety argument: nothing needing a paired
        # computer can arrive through a surface that never asked about one.
        self.assertTrue(
            fleet_tools._CREATE_TIME_MODEL_CHOICE_KEYS
            <= {"mode", "provider", "model", "runtime", "engine", "reasoning_effort", "gateway_binding"}
        )
        self.assertEqual(fleet_tools._CREATE_TIME_MODEL_CHOICE_KEYS, frozenset({"mode", "provider", "model"}))
        self.assertNotIn("cli_subscription", fleet_tools._CREATE_TIME_MODEL_MODES)
        self.assertNotIn("local", fleet_tools._CREATE_TIME_MODEL_MODES)

    def test_fleet_create_agent_calls_the_ASYNC_resolver_not_the_shape_check_alone(self):
        # Calling validate_create_time_model_choice directly would compile,
        # run, and silently skip every model-identity check.
        tree = self._fleet_tools_ast()
        target = next(
            n for n in ast.walk(tree)
            if isinstance(n, ast.AsyncFunctionDef) and n.name == "fleet_create_agent"
        )
        called = {
            n.func.id for n in ast.walk(target)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
        }
        self.assertIn("resolve_create_time_model_choice", called)
        self.assertNotIn("validate_create_time_model_choice", called)

    def test_the_identity_check_reuses_the_shared_function_never_a_second_model_list(self):
        source = FLEET_TOOLS_PY.read_text(encoding="utf-8")
        start = source.index("async def resolve_create_time_model_choice")
        end = source.index("\ndef ", start) if "\ndef " in source[start:] else len(source)
        body = source[start:start + 6000]
        self.assertIn("model_is_known_for_provider", body)
        self.assertIn("get_provider_models", body)


class FrontendDefaultMatchesServerSeedTests(unittest.TestCase):
    """CROSS-LANGUAGE drift: the id the picker opens on vs. what the server
    actually stamps. Two files, two languages, one assertion — so neither can
    move alone and leave the picker lying about what happens if nobody
    touches it."""

    def test_the_frontend_default_names_the_model_the_server_seeds(self):
        ts = AGENT_CREATE_MODEL_TS.read_text(encoding="utf-8")
        match = re.search(
            r"AGENT_CREATE_DEFAULT_MODEL_ID\s*=\s*agentCreateModelChoiceId\(\s*"
            r'"(?P<mode>[a-z_]+)"\s*,\s*(?P<provider>[A-Za-z_.]+)\s*,\s*'
            r"(?P<model>PLATFORM_CREDITS_MODEL_BY_TIER\.[a-z]+)\s*,?\s*\)",
            ts,
        )
        self.assertIsNotNone(match, "CANARY: could not read the frontend default — the constant moved or was renamed")
        assert match is not None

        seeded = fleet_tools.seed_specialist_metadata()["model_config"]
        self.assertEqual(match.group("mode"), seeded.get("mode"))

        tier = match.group("model").rsplit(".", 1)[1]
        tier_map = re.search(
            r"PLATFORM_CREDITS_MODEL_BY_TIER[^=]*=\s*\{(?P<body>[^}]*)\}",
            (REPO_ROOT / "frontend" / "lib" / "workspace" / "fleet" / "fleet-model-config.ts").read_text(encoding="utf-8"),
        )
        self.assertIsNotNone(tier_map, "CANARY: could not read the frontend tier map")
        assert tier_map is not None
        tier_model = re.search(rf'{tier}\s*:\s*"([^"]+)"', tier_map.group("body"))
        self.assertIsNotNone(tier_model, f"CANARY: tier '{tier}' is not in the frontend tier map")
        assert tier_model is not None

        self.assertEqual(
            tier_model.group(1),
            seeded.get("model"),
            "the model the creation picker pre-selects is not the model the server seeds",
        )


if __name__ == "__main__":
    unittest.main()
