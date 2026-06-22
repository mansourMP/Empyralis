import unittest

from unittest.mock import patch

from server_modules import conversation_compaction
from server_modules import conversation_memory_policy
from server_modules import provider_profiles


class ConversationMemoryPolicyTests(unittest.TestCase):
    def test_direct_chat_profile_uses_canonical_budget(self) -> None:
        profile = conversation_memory_policy.get_memory_policy_profile(
            conversation_memory_policy.DIRECT_CHAT_PROFILE
        )

        self.assertEqual(profile.max_prompt_tokens, 8000)
        self.assertEqual(profile.preserve_last_messages, 10)
        self.assertEqual(profile.max_recent_log_days, 7)

    def test_external_channel_profile_uses_shared_channel_budget(self) -> None:
        profile = conversation_memory_policy.get_memory_policy_profile(
            conversation_memory_policy.EXTERNAL_CHANNEL_CUSTOMER_PROFILE
        )

        self.assertEqual(profile.max_prompt_tokens, 1100)
        self.assertEqual(profile.preserve_last_messages, 8)
        self.assertEqual(profile.summary_trigger_messages, 12)

    def test_external_channel_budget_presets_are_model_aware(self) -> None:
        compact = conversation_memory_policy.build_external_channel_memory_profile(
            context_budget_preset="compact",
            context_window_tokens=64_000,
        )
        standard = conversation_memory_policy.build_external_channel_memory_profile(
            context_budget_preset="standard",
            context_window_tokens=200_000,
        )
        max_profile = conversation_memory_policy.build_external_channel_memory_profile(
            context_budget_preset="max",
            context_window_tokens=1_000_000,
        )

        self.assertGreater(standard.max_prompt_tokens, compact.max_prompt_tokens)
        self.assertGreater(max_profile.max_prompt_tokens, standard.max_prompt_tokens)
        self.assertLess(max_profile.max_prompt_tokens, 1_000_000)
        self.assertGreaterEqual(max_profile.preserve_last_messages, standard.preserve_last_messages)

    def test_external_channel_budget_accepts_old_saved_preset_ids(self) -> None:
        self.assertEqual(
            conversation_memory_policy.normalize_context_budget_preset("balanced"),
            "standard",
        )
        self.assertEqual(
            conversation_memory_policy.normalize_context_budget_preset("deep"),
            "extended",
        )
        extended = conversation_memory_policy.build_external_channel_memory_profile(
            context_budget_preset="deep",
            context_window_tokens=200_000,
        )
        self.assertGreater(extended.max_prompt_tokens, 0)

    def test_model_aware_policy_preserves_reserves_for_small_context(self) -> None:
        base = conversation_memory_policy.get_memory_policy_profile(
            conversation_memory_policy.DIRECT_CHAT_PROFILE
        )
        policy = conversation_memory_policy.build_model_aware_memory_policy(
            base,
            context_window_tokens=8_000,
        )

        self.assertLess(policy.max_prompt_tokens, 8_000)
        self.assertGreaterEqual(policy.output_reserve_tokens, 2_048)
        self.assertGreaterEqual(policy.system_tool_reserve_tokens, 2_000)
        self.assertGreaterEqual(policy.retrieval_reserve_tokens, 2_000)
        self.assertLessEqual(policy.preserve_last_messages, 20)
        self.assertGreaterEqual(policy.max_summary_chars, 1_800)

    def test_model_aware_policy_scales_up_but_not_to_full_window(self) -> None:
        base = conversation_memory_policy.get_memory_policy_profile(
            conversation_memory_policy.DIRECT_CHAT_PROFILE
        )
        small = conversation_memory_policy.build_model_aware_memory_policy(
            base,
            context_window_tokens=8_000,
        )
        medium = conversation_memory_policy.build_model_aware_memory_policy(
            base,
            context_window_tokens=128_000,
        )
        huge = conversation_memory_policy.build_model_aware_memory_policy(
            base,
            context_window_tokens=1_000_000,
        )

        self.assertGreater(medium.max_prompt_tokens, small.max_prompt_tokens)
        self.assertGreater(huge.max_prompt_tokens, medium.max_prompt_tokens)
        self.assertLess(huge.max_prompt_tokens, 1_000_000)
        self.assertLessEqual(huge.max_summary_chars, 8_000)

    def test_context_window_for_model_falls_back_safely(self) -> None:
        self.assertEqual(
            provider_profiles.context_window_for_model("deepseek", "deepseek-chat"),
            1_000_000,
        )
        self.assertEqual(
            provider_profiles.context_window_for_model("openai", "gpt-4.1"),
            1_000_000,
        )
        self.assertIsNone(provider_profiles.context_window_for_model("unknown", "whatever"))
        self.assertIsNone(provider_profiles.context_window_for_model("deepseek", "non-existent"))

    def test_compaction_does_not_generate_summary_when_under_threshold(self) -> None:
        with patch.object(conversation_compaction, "summarize_messages") as summarize_mock:
            payload = conversation_compaction.compact_conversation_history(
                [{"role": "user", "content": "short"}],
                max_tokens=8_000,
                preserve_last_messages=4,
                recent_message_budget_tokens=1_200,
                summary_max_chars=2_000,
            )

        summarize_mock.assert_not_called()
        self.assertFalse(payload["compacted"])


if __name__ == "__main__":
    unittest.main()
