import unittest

from server_modules import direct_chat_prompt_service


class DirectChatPromptServiceTests(unittest.TestCase):
    def test_tool_prompt_lines_include_name_and_description(self) -> None:
        self.assertEqual(
            direct_chat_prompt_service.tool_prompt_lines(
                [{"name": "file__read", "description": "Read a local file"}]
            ),
            ["file__read: Read a local file"],
        )

    def test_memory_recall_section_only_appears_when_memory_tools_are_available(self) -> None:
        section = direct_chat_prompt_service.memory_recall_section(
            [{"name": "memory_search"}, {"name": "memory_get"}],
            memory_tool_names={"memory_search", "memory_get"},
        )
        self.assertIn("## Memory Recall", section)
        self.assertIn("run memory_search", section)
        self.assertIn("untrusted context", section)
        self.assertNotIn("memory_update", section)
        self.assertNotIn("memory_append_daily_note", section)
        self.assertNotIn("memory_stage_consolidation", section)
        self.assertNotIn("memory_consolidate_daily_notes", section)
        self.assertNotIn("memory_list_versions", section)
        self.assertNotIn("memory_rollback_version", section)

        full_section = direct_chat_prompt_service.memory_recall_section(
            [
                {"name": "memory_search"},
                {"name": "memory_get"},
                {"name": "memory_append_daily_note"},
                {"name": "memory_stage_edit"},
                {"name": "memory_apply_edit"},
                {"name": "memory_stage_consolidation"},
                {"name": "memory_consolidate_daily_notes"},
                {"name": "memory_list_versions"},
                {"name": "memory_rollback_version"},
            ],
            memory_tool_names={
                "memory_search",
                "memory_get",
                "memory_append_daily_note",
                "memory_stage_edit",
                "memory_apply_edit",
                "memory_stage_consolidation",
                "memory_consolidate_daily_notes",
                "memory_list_versions",
                "memory_rollback_version",
            },
        )
        self.assertIn("memory_append_daily_note", full_section)
        self.assertIn("memory_stage_edit", full_section)
        self.assertIn("memory_apply_edit", full_section)
        self.assertIn("memory_stage_consolidation", full_section)
        self.assertIn("memory_consolidate_daily_notes", full_section)
        self.assertIn("memory_list_versions", full_section)
        self.assertIn("memory_rollback_version", full_section)

        empty = direct_chat_prompt_service.memory_recall_section(
            [{"name": "web__search"}],
            memory_tool_names={"memory_search", "memory_get"},
        )
        self.assertEqual(empty, "")

    def test_build_system_prompt_includes_base_prompt_and_memory_section(self) -> None:
        prompt = direct_chat_prompt_service.build_system_prompt(
            workspace_id="default",
            availability={"ai_ready": True},
            tools=[
                {"name": "memory_search", "description": "Search memory"},
                {"name": "memory_get", "description": "Read memory excerpt"},
            ],
            availability_lines=lambda workspace_id, availability: [f"Workspace: {workspace_id}", "AI account: ready"],
            build_operator_system_prompt=lambda lines, tool_lines=None: "\n".join([*lines, *(tool_lines or [])]),
            memory_tool_names={"memory_search", "memory_get"},
        )

        self.assertIn("Workspace: default", str(prompt))
        self.assertIn("memory_search: Search memory", str(prompt))
        self.assertIn("## Tool Use Rules", str(prompt))
        self.assertIn("Use matching tools when available", str(prompt))
        self.assertIn("Do not end a turn by saying you will check", str(prompt))
        self.assertIn("## Memory Recall", str(prompt))

    def test_combine_workspace_context_prefers_prompt_then_context(self) -> None:
        combined = direct_chat_prompt_service.combine_workspace_context(
            system_prompt="Base prompt",
            workspace_context_text="Workspace context",
        )
        self.assertEqual(combined, "Base prompt\n\n## Workspace Context\nWorkspace context")

        context_only = direct_chat_prompt_service.combine_workspace_context(
            system_prompt=None,
            workspace_context_text="Workspace context",
        )
        self.assertEqual(context_only, "Workspace context")

    def test_build_system_prompt_includes_follow_through_rule_without_tools(self) -> None:
        prompt = direct_chat_prompt_service.build_system_prompt(
            workspace_id="default",
            availability={"ai_ready": True},
            tools=[],
            availability_lines=lambda workspace_id, availability: [f"Workspace: {workspace_id}"],
            build_operator_system_prompt=lambda lines, tool_lines=None: "\n".join([*lines, *(tool_lines or [])]),
            memory_tool_names={"memory_search", "memory_get"},
        )

        self.assertIn("## Follow-through Rule", str(prompt))
        self.assertIn("Do not end a turn by saying you will check", str(prompt))

    def test_combine_workspace_context_places_identity_before_workspace_context(self) -> None:
        combined = direct_chat_prompt_service.combine_workspace_context(
            system_prompt="Base prompt",
            workspace_context_text="Workspace context",
            identity_guardrail="Identity guardrail",
        )

        self.assertEqual(
            combined,
            "Base prompt\n\nIdentity guardrail\n\n## Workspace Context\nWorkspace context",
        )

    def test_platform_paid_runtime_environment_uses_empyralis_ai_only(self) -> None:
        guardrail = direct_chat_prompt_service.build_runtime_identity_guardrail(
            provider="deepseek",
            model="deepseek-v4-pro",
            billing_source="empyralis_credits",
            ai_tier="pro",
        )

        self.assertIn("## Runtime Environment", guardrail)
        self.assertIn("Empyralis AI", guardrail)
        self.assertNotIn("Empyralis Pro AI", guardrail)
        self.assertNotIn("deepseek", guardrail.lower())
        self.assertNotIn("deepseek-v4-pro", guardrail)
        self.assertNotIn("If asked", guardrail)
        self.assertNotIn("Do not", guardrail)

    def test_user_owned_runtime_environment_does_not_override_model_identity(self) -> None:
        guardrail = direct_chat_prompt_service.build_runtime_identity_guardrail(
            provider="openai",
            model="gpt-5.4",
            user_owned_ai_label="connected AI account",
        )

        self.assertIn("## Runtime Environment", guardrail)
        self.assertIn("this AI model", guardrail)
        self.assertNotIn("connected AI account", guardrail)
        self.assertNotIn("openai", guardrail.lower())
        self.assertNotIn("gpt-5.4", guardrail)
        self.assertNotIn("If asked", guardrail)
        self.assertNotIn("Do not", guardrail)

    def test_build_proactive_suggestions_dedupes_limits_and_has_no_generic_fallback(self) -> None:
        suggestions = direct_chat_prompt_service.build_proactive_suggestions(
            "default",
            heartbeat_tasks=lambda: ["follow up with onboarding", "follow up with onboarding"],
            recent_run_prompts=lambda workspace_id: ["review the current runtime plan", "review the current runtime plan"],
            memory_suggestion_prompts=lambda workspace_id: ["Use my saved context: Asia/Shanghai"],
        )

        self.assertEqual(len(suggestions), 3)
        self.assertEqual(suggestions[0], "Handle heartbeat task: follow up with onboarding")
        self.assertEqual(suggestions[1], "Continue: review the current runtime plan")
        self.assertEqual(suggestions[2], "Use my saved context: Asia/Shanghai")

        empty = direct_chat_prompt_service.build_proactive_suggestions(
            "default",
            heartbeat_tasks=lambda: [],
            recent_run_prompts=lambda workspace_id: [],
            memory_suggestion_prompts=lambda workspace_id: [],
        )
        self.assertEqual(empty, [])


if __name__ == "__main__":
    unittest.main()
