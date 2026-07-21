"""Gap 1.5 -- BYO API key rotation on provider rate-limit (429).

Covers:
  * A workspace with exactly one vault credential for a provider is
    unaffected -- it keeps getting exactly the same single candidate it got
    before this feature existed.
  * A workspace with a second vault credential registered for the same
    (workspace, provider) gets it appended as a rotation candidate.
  * A credential currently cooling down from a recent 429 is excluded from
    the candidate pool (but never leaves the pool empty for a single-key
    workspace).
  * `generate_with_candidate_failover` (the real call site, server_modules/
    runs_engine.py) actually rotates to the next candidate when the primary
    key's call raises a 429-shaped error, and marks that key's cooldown so
    it isn't immediately reused; a single-key workspace still raises (no
    fabricated recovery) exactly like before this feature existed.
"""
import queue
import unittest
from unittest.mock import MagicMock, patch

from server_modules import credential_rotation_service, provider_profiles, runs_engine


class RotationPoolCandidateTests(unittest.TestCase):
    def setUp(self):
        provider_profiles._init()
        credential_rotation_service.reset_all()

    def tearDown(self):
        credential_rotation_service.reset_all()

    @patch("server_modules.provider_profiles._sorted_profiles", return_value=[])
    @patch("server_modules.provider_profiles.claude_code_cli_available", return_value=False)
    @patch("server_modules.provider_profiles._resolve_hosted_provider_api_key", return_value=("", ""))
    @patch("server_modules.provider_profiles.list_vault_credentials")
    @patch("server_modules.provider_profiles.resolve_vault_credential")
    def test_single_key_workspace_gets_exactly_one_unchanged_candidate(
        self,
        resolve_vault_credential_mock,
        list_vault_credentials_mock,
        _hosted_api_key_mock,
        _claude_cli_mock,
        _sorted_profiles_mock,
    ):
        resolve_vault_credential_mock.return_value = {"api_key": "sk-primary"}
        # Only one row for this (workspace, provider) exists in the vault.
        list_vault_credentials_mock.return_value = [
            {"id": "cred-primary", "provider": "anthropic", "workspace_id": "ws-1", "created_at": "2026-01-01T00:00:00Z"},
        ]

        candidates = provider_profiles._build_provider_credential_candidates(
            {"workspace_id": "ws-1", "credential_id": "cred-primary"},
            {},
            "anthropic",
        )

        self.assertEqual([c["label"] for c in candidates], ["credential:cred-primary"])
        self.assertEqual(candidates[0]["credentials"]["api_key"], "sk-primary")

    @patch("server_modules.provider_profiles._sorted_profiles", return_value=[])
    @patch("server_modules.provider_profiles.claude_code_cli_available", return_value=False)
    @patch("server_modules.provider_profiles._resolve_hosted_provider_api_key", return_value=("", ""))
    @patch("server_modules.provider_profiles.list_vault_credentials")
    @patch("server_modules.provider_profiles.resolve_vault_credential")
    def test_second_registered_key_for_same_provider_becomes_a_rotation_candidate(
        self,
        resolve_vault_credential_mock,
        list_vault_credentials_mock,
        _hosted_api_key_mock,
        _claude_cli_mock,
        _sorted_profiles_mock,
    ):
        resolve_vault_credential_mock.side_effect = (
            lambda cid, ws=None, **_kwargs: {"api_key": f"key-for-{cid}"}
        )
        # A second row for the SAME (workspace, provider) -- e.g. registered
        # via a second POST to /api/connectors/vault with a new label.
        list_vault_credentials_mock.return_value = [
            {"id": "cred-primary", "provider": "anthropic", "workspace_id": "ws-1", "created_at": "2026-01-01T00:00:00Z"},
            {"id": "cred-secondary", "provider": "anthropic", "workspace_id": "ws-1", "created_at": "2026-01-02T00:00:00Z"},
            # A different provider's credential must never leak into the pool.
            {"id": "cred-other-provider", "provider": "openai", "workspace_id": "ws-1", "created_at": "2026-01-01T00:00:00Z"},
        ]

        candidates = provider_profiles._build_provider_credential_candidates(
            {"workspace_id": "ws-1", "credential_id": "cred-primary"},
            {},
            "anthropic",
        )

        self.assertEqual(
            [c["label"] for c in candidates],
            ["credential:cred-primary", "rotation:cred-secondary"],
        )
        self.assertEqual(candidates[1]["credential_id"], "cred-secondary")
        self.assertEqual(candidates[1]["credentials"]["api_key"], "key-for-cred-secondary")

    @patch("server_modules.provider_profiles._sorted_profiles", return_value=[])
    @patch("server_modules.provider_profiles.claude_code_cli_available", return_value=False)
    @patch("server_modules.provider_profiles._resolve_hosted_provider_api_key", return_value=("", ""))
    @patch("server_modules.provider_profiles.list_vault_credentials")
    @patch("server_modules.provider_profiles.resolve_vault_credential")
    def test_cooling_down_primary_key_is_dropped_in_favor_of_the_live_sibling(
        self,
        resolve_vault_credential_mock,
        list_vault_credentials_mock,
        _hosted_api_key_mock,
        _claude_cli_mock,
        _sorted_profiles_mock,
    ):
        resolve_vault_credential_mock.side_effect = (
            lambda cid, ws=None, **_kwargs: {"api_key": f"key-for-{cid}"}
        )
        list_vault_credentials_mock.return_value = [
            {"id": "cred-primary", "provider": "anthropic", "workspace_id": "ws-1", "created_at": "2026-01-01T00:00:00Z"},
            {"id": "cred-secondary", "provider": "anthropic", "workspace_id": "ws-1", "created_at": "2026-01-02T00:00:00Z"},
        ]
        credential_rotation_service.mark_rate_limited("cred-primary")

        candidates = provider_profiles._build_provider_credential_candidates(
            {"workspace_id": "ws-1", "credential_id": "cred-primary"},
            {},
            "anthropic",
        )

        self.assertEqual([c["label"] for c in candidates], ["rotation:cred-secondary"])

    @patch("server_modules.provider_profiles._sorted_profiles", return_value=[])
    @patch("server_modules.provider_profiles.claude_code_cli_available", return_value=False)
    @patch("server_modules.provider_profiles._resolve_hosted_provider_api_key", return_value=("", ""))
    @patch("server_modules.provider_profiles.list_vault_credentials")
    @patch("server_modules.provider_profiles.resolve_vault_credential")
    def test_cooling_down_single_key_is_still_offered_because_it_has_no_alternative(
        self,
        resolve_vault_credential_mock,
        list_vault_credentials_mock,
        _hosted_api_key_mock,
        _claude_cli_mock,
        _sorted_profiles_mock,
    ):
        resolve_vault_credential_mock.return_value = {"api_key": "sk-primary"}
        list_vault_credentials_mock.return_value = [
            {"id": "cred-primary", "provider": "anthropic", "workspace_id": "ws-1", "created_at": "2026-01-01T00:00:00Z"},
        ]
        credential_rotation_service.mark_rate_limited("cred-primary")

        candidates = provider_profiles._build_provider_credential_candidates(
            {"workspace_id": "ws-1", "credential_id": "cred-primary"},
            {},
            "anthropic",
        )

        # No sibling exists -- filtering out the cooling-down primary would
        # leave zero candidates, which must never happen: the single-key
        # workspace keeps behaving exactly like it did before this feature.
        self.assertEqual([c["label"] for c in candidates], ["credential:cred-primary"])


class GenerateWithCandidateFailoverRotationTests(unittest.TestCase):
    def setUp(self):
        credential_rotation_service.reset_all()

    def tearDown(self):
        credential_rotation_service.reset_all()

    def test_rotates_to_next_key_on_429_and_cools_down_the_rate_limited_one(self):
        state = {
            "provider": "anthropic",
            "selected_model": "claude-3-7-sonnet-20250219",
            "credential_candidates": [
                {
                    "source": "credential_id",
                    "credentials": {"api_key": "sk-primary"},
                    "credential_id": "cred-primary",
                    "profile_id": None,
                    "label": "credential:cred-primary",
                },
                {
                    "source": "rotation",
                    "credentials": {"api_key": "sk-secondary"},
                    "credential_id": "cred-secondary",
                    "profile_id": None,
                    "label": "rotation:cred-secondary",
                },
            ],
        }

        adapter = MagicMock()

        def _generate(system_prompt, user_input, model, credentials):
            if credentials.get("api_key") == "sk-primary":
                raise RuntimeError("429 Too Many Requests")
            return "reply from secondary key"

        adapter.generate.side_effect = _generate

        with patch("server_modules.runs_engine.resolve_provider_adapter", return_value=("anthropic", "anthropic", adapter)):
            result = runs_engine.generate_with_candidate_failover(
                state, {}, queue.Queue(), "system prompt", "hello",
            )

        self.assertEqual(result, "reply from secondary key")
        self.assertEqual(adapter.generate.call_count, 2)
        self.assertTrue(credential_rotation_service.is_cooling_down("cred-primary"))
        self.assertFalse(credential_rotation_service.is_cooling_down("cred-secondary"))
        self.assertEqual(state["active_candidate_index"], 1)

    def test_single_key_workspace_still_raises_on_429_no_fabricated_recovery(self):
        state = {
            "provider": "anthropic",
            "selected_model": "claude-3-7-sonnet-20250219",
            "credential_candidates": [
                {
                    "source": "credential_id",
                    "credentials": {"api_key": "sk-only"},
                    "credential_id": "cred-only",
                    "profile_id": None,
                    "label": "credential:cred-only",
                },
            ],
        }

        adapter = MagicMock()
        adapter.generate.side_effect = RuntimeError("429 Too Many Requests")

        with patch("server_modules.runs_engine.resolve_provider_adapter", return_value=("anthropic", "anthropic", adapter)):
            with self.assertRaises(RuntimeError):
                runs_engine.generate_with_candidate_failover(
                    state, {}, queue.Queue(), "system prompt", "hello",
                )

        # The mechanism still records the cooldown (useful for the next run's
        # candidate pool if a sibling ever gets registered), but does NOT
        # invent a second credential or otherwise change the outcome: the
        # single-key workspace fails exactly like it did before this feature.
        self.assertTrue(credential_rotation_service.is_cooling_down("cred-only"))
        self.assertEqual(adapter.generate.call_count, 1)


class CredentialRotationServiceTests(unittest.TestCase):
    def setUp(self):
        credential_rotation_service.reset_all()

    def tearDown(self):
        credential_rotation_service.reset_all()

    def test_mark_and_query_cooldown(self):
        self.assertFalse(credential_rotation_service.is_cooling_down("cred-x"))
        credential_rotation_service.mark_rate_limited("cred-x", cooldown_seconds=60)
        self.assertTrue(credential_rotation_service.is_cooling_down("cred-x"))
        self.assertGreater(credential_rotation_service.cooldown_remaining_seconds("cred-x"), 0)

    def test_success_clears_cooldown(self):
        credential_rotation_service.mark_rate_limited("cred-x", cooldown_seconds=60)
        self.assertTrue(credential_rotation_service.is_cooling_down("cred-x"))
        credential_rotation_service.mark_success("cred-x")
        self.assertFalse(credential_rotation_service.is_cooling_down("cred-x"))

    def test_empty_credential_id_is_a_no_op(self):
        credential_rotation_service.mark_rate_limited(None)
        credential_rotation_service.mark_rate_limited("")
        self.assertFalse(credential_rotation_service.is_cooling_down(None))
        self.assertFalse(credential_rotation_service.is_cooling_down(""))


if __name__ == "__main__":
    unittest.main()
