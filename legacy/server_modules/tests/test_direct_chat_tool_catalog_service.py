import unittest

from server_modules import direct_chat_tool_catalog_service as service


def _callbacks() -> service.DirectChatToolPolicyCallbacks:
    return service.DirectChatToolPolicyCallbacks(
        compact_text=lambda value: " ".join(str(value or "").strip().lower().split()),
        question_like=lambda compact: compact.startswith(("what ", "why ", "how ", "can ")),
        mentions_any=lambda compact, keywords: any(token in compact for token in keywords),
        extract_first_path_reference=lambda message: "/tmp/demo.txt" if "/tmp/demo.txt" in str(message or "") else "",
        extract_first_url=lambda message: "https://example.com" if "example.com" in str(message or "") else "",
        provider_supports_direct_tool_calls=service.provider_supports_direct_tool_calls,
        is_obvious_smtp_write_request=lambda compact: "email" in compact and "send" in compact,
        google_workspace_keywords=("gmail", "calendar", "drive"),
        smtp_keywords=("smtp",),
        telegram_keywords=("telegram",),
        slack_keywords=("slack",),
        discord_keywords=("discord",),
        dropbox_keywords=("dropbox",),
        s3_keywords=("s3",),
        browser_keywords=("browser", "go to", "page title"),
        local_file_keywords=("read file", "open file"),
        local_shell_keywords=("run command", "shell"),
        local_screenshot_keywords=("screenshot",),
        local_computer_control_keywords=("click screen", "ocr screen"),
        web_lookup_keywords=("search web", "look up"),
        http_request_keywords=("http", "api request"),
        image_generation_keywords=("generate image",),
        llm_task_keywords=("think deeply",),
    )


class DirectChatToolCatalogServiceTests(unittest.TestCase):
    def test_provider_supports_direct_tool_calls_is_provider_agnostic(self) -> None:
        self.assertTrue(service.provider_supports_direct_tool_calls("anthropic"))
        self.assertTrue(service.provider_supports_direct_tool_calls("ollama"))
        self.assertFalse(service.provider_supports_direct_tool_calls(""))

    def test_build_local_direct_chat_tools_requires_local_worker(self) -> None:
        self.assertEqual(
            service.build_local_direct_chat_tools({"runtime_ok": False}, local_worker_available=lambda availability: False),
            [],
        )
        tools = service.build_local_direct_chat_tools({"runtime_ok": True}, local_worker_available=lambda availability: True)
        self.assertTrue(any(item["name"] == "file__read" for item in tools))
        self.assertTrue(any(item["name"] == "computer__click" for item in tools))

    def test_builtin_tools_include_canonical_hardware_action(self) -> None:
        tools = service.build_builtin_direct_chat_tools()

        hardware_tool = next(item for item in tools if item["name"] == "hardware__action")
        self.assertEqual(hardware_tool["connector_id"], "hardware")
        self.assertIn("runtime_target", hardware_tool["parameters"]["properties"])
        self.assertIn("action", hardware_tool["parameters"]["required"])

    def test_build_direct_chat_tools_uses_normalized_usable_write_actions(self) -> None:
        tools = service.build_direct_chat_tools(
            [
                {"id": " Slack ", "label": "Slack", "runtime_usable": True, "write_actions": [" post_message ", "post_message"]},
                {"id": "dropbox", "label": "Dropbox", "runtime_usable": False, "write_actions": ["upload_file"]},
            ]
        )

        self.assertEqual([item["name"] for item in tools], ["slack__post_message"])

    def test_message_can_use_direct_local_tools_detects_file_request(self) -> None:
        allowed = service.message_can_use_direct_local_tools(
            "Open /tmp/demo.txt",
            provider="codex_cli",
            tools=[{"name": "file__read"}],
            callbacks=_callbacks(),
        )

        self.assertTrue(allowed)

    def test_message_can_use_direct_local_tools_allows_hardware_action_fallback(self) -> None:
        allowed = service.message_can_use_direct_local_tools(
            "Take a screenshot of my screen",
            provider="deepseek",
            tools=[{"name": "hardware__action"}],
            callbacks=_callbacks(),
        )

        self.assertTrue(allowed)

    def test_message_can_use_direct_local_tools_detects_desktop_listing_request(self) -> None:
        allowed = service.message_can_use_direct_local_tools(
            "List the files on my desktop.",
            provider="deepseek",
            tools=[{"name": "file__read"}],
            callbacks=_callbacks(),
        )

        self.assertTrue(allowed)

    def test_message_can_use_direct_local_tools_detects_local_hardware_info_request(self) -> None:
        callbacks = _callbacks()

        allowed = service.message_can_use_direct_local_tools(
            "check what i have in this hardware !",
            provider="deepseek",
            tools=[{"name": "shell__exec"}],
            callbacks=callbacks,
        )

        self.assertTrue(allowed)
        self.assertTrue(
            service.message_can_use_builtin_direct_tools(
                "What hardware specs does this machine have?",
                tools=[{"name": "hardware__action"}],
                callbacks=callbacks,
            )
        )

    def test_message_can_use_direct_local_tools_ignores_generic_hardware_question(self) -> None:
        allowed = service.message_can_use_direct_local_tools(
            "What is hardware acceleration?",
            provider="deepseek",
            tools=[{"name": "shell__exec"}],
            callbacks=_callbacks(),
        )

        self.assertFalse(allowed)

    def test_message_can_use_direct_local_tools_detects_working_directory_request(self) -> None:
        callbacks = _callbacks()

        allowed = service.message_can_use_direct_local_tools(
            "what folder are you in ?",
            provider="deepseek",
            tools=[{"name": "shell__exec"}],
            callbacks=callbacks,
        )

        self.assertTrue(allowed)
        self.assertTrue(service.looks_like_local_working_directory_request("what is your current working directory?"))
        self.assertFalse(service.looks_like_local_working_directory_request("what folder should I use for invoices?"))

    def test_message_can_use_direct_local_tools_ignores_pasted_terminal_context(self) -> None:
        pasted_context = """
        Shift+Tab to accept edits
        1 GEMINI.md file · 1 skill
        > Type your message or @path/to/file
        workspace (/directory)      branch   sandbox    /model
        quota   memory   tokens
        ~/Multi_Agent_Orchestrator_Project main untrusted gemini-3.1-pro-preview
        """

        allowed = service.message_can_use_direct_local_tools(
            pasted_context,
            provider="deepseek",
            tools=[
                {"name": "computer__click"},
                {"name": "file__read"},
                {"name": "shell__exec"},
            ],
            callbacks=_callbacks(),
        )

        self.assertFalse(allowed)

    def test_message_requests_local_computer_tool_requires_actual_control_intent(self) -> None:
        callbacks = _callbacks()

        self.assertFalse(
            service.message_requests_local_computer_tool(
                "Type your message or @path/to/file",
                callbacks,
            )
        )
        self.assertTrue(
            service.message_requests_local_computer_tool(
                "Click the screen button in the active window",
                callbacks,
            )
        )

    def test_message_can_use_builtin_direct_tools_detects_browser_and_http_cases(self) -> None:
        callbacks = _callbacks()
        self.assertTrue(
            service.message_can_use_builtin_direct_tools(
                "Go to https://example.com and tell me the page title",
                tools=[{"name": "browser__navigate"}],
                callbacks=callbacks,
            )
        )
        self.assertTrue(
            service.message_can_use_builtin_direct_tools(
                "Make an HTTP request to https://example.com/api",
                tools=[{"name": "http_request"}],
                callbacks=callbacks,
            )
        )

    def test_message_can_use_builtin_direct_tools_detects_web_lookup_cases(self) -> None:
        callbacks = _callbacks()
        for message in (
            "do some web search!",
            "search the web for Netanyahu news",
            "research current Netanyahu news",
            "search the web for OpenClaw browser docs",
        ):
            with self.subTest(message=message):
                self.assertTrue(
                    service.message_can_use_builtin_direct_tools(
                        message,
                        tools=[{"name": "web__search"}, {"name": "web__fetch"}],
                        callbacks=callbacks,
                    )
                )
        self.assertFalse(
            service.message_can_use_builtin_direct_tools(
                "Open https://example.com and tell me the page title",
                tools=[{"name": "web__search"}, {"name": "web__fetch"}],
                callbacks=callbacks,
            )
        )
        self.assertFalse(
            service.message_can_use_builtin_direct_tools(
                "what is your current working directory?",
                tools=[{"name": "web__search"}, {"name": "web__fetch"}],
                callbacks=callbacks,
            )
        )

    def test_browser_automation_intent_requires_control_intent(self) -> None:
        self.assertFalse(service.message_has_browser_automation_intent("search the web for OpenClaw browser docs"))
        self.assertFalse(service.message_has_browser_automation_intent("fetch https://browser.example.com/docs"))
        self.assertTrue(service.message_has_browser_automation_intent("open https://example.com in the browser"))
        self.assertTrue(service.message_has_browser_automation_intent("go to example.com and tell me the page title"))
        self.assertTrue(service.message_has_browser_automation_intent("click the login button"))
        self.assertTrue(service.message_has_browser_automation_intent("use the browser to check this page"))

    def test_message_can_use_direct_connector_tools_detects_workspace_connector(self) -> None:
        callbacks = _callbacks()
        allowed = service.message_can_use_direct_connector_tools(
            "Send a telegram message",
            provider="codex_cli",
            tools=[{"name": "telegram_bot__send_message"}],
            callbacks=callbacks,
        )

        self.assertTrue(allowed)

    def test_tool_inventory_questions_are_detected(self) -> None:
        self.assertTrue(service.message_requests_tool_inventory("/tools"))
        self.assertTrue(service.message_requests_tool_inventory("show tool inventory"))
        self.assertTrue(service.message_requests_tool_inventory("List tool inventory"))
        self.assertTrue(service.message_requests_tool_inventory("what capabilities do you have?"))
        self.assertTrue(service.message_requests_tool_inventory("what tools do you have?"))
        self.assertFalse(service.message_requests_tool_inventory("what can you do?"))
        self.assertFalse(service.message_requests_tool_inventory("What can you help me with here right now?"))
        self.assertFalse(service.message_requests_tool_inventory("What can Sage do?"))
        self.assertFalse(service.message_requests_tool_inventory("List your tools"))
        self.assertFalse(service.message_requests_tool_inventory("use the web search tool"))

    def test_tool_inventory_reply_uses_actual_tool_list(self) -> None:
        reply = service.direct_chat_tool_inventory_reply(
            [
                {"name": "web__search", "description": "Search the web"},
                {"name": "file__read", "description": "Read a file"},
            ],
            {"runtime_ok": False},
        )

        self.assertIn("Web:", reply)
        self.assertIn("Search the web", reply)
        self.assertIn("Agent Computer:", reply)
        self.assertIn("Read a file", reply)
        self.assertIn("This Device capabilities require Agent Computer to be online.", reply)

    def test_tool_inventory_reply_distinguishes_cloud_computer_from_personal_gateway(self) -> None:
        reply = service.direct_chat_tool_inventory_reply(
            [
                {"name": "file__read", "description": "Read a file"},
                {"name": "web__search", "description": "Search the web"},
            ],
            {"cloud_computer_online": True, "local_gateway_online": False},
        )

        self.assertIn("Computer capabilities are available through Sage Cloud Computer.", reply)
        self.assertIn("This Device still requires a paired Agent Computer.", reply)
        self.assertIn("Capability availability is based on Agent Computer status, connector state, and workspace policy", reply)

    def test_tool_inventory_reply_includes_setup_actions_from_capability_truth(self) -> None:
        reply = service.direct_chat_tool_inventory_reply(
            [{"name": "web__search", "description": "Search the web"}],
            {
                "capability_truth": {
                    "provider": {"label": "DeepSeek", "ai_ready": False},
                    "credits": {"hosted_enabled": False, "reason": "policy_disabled"},
                    "my_computer": {"state": "offline"},
                    "connected_apps": [
                        {"label": "Google Workspace", "connected": True, "runtime_usable": True},
                        {"label": "GitHub", "connected": False, "runtime_usable": None},
                    ],
                    "channels": [
                        {"label": "Telegram", "connected": True, "runtime_usable": True},
                        {"label": "WhatsApp", "connected": False, "runtime_usable": False},
                    ],
                    "byok_providers": [
                        {"label": "DeepSeek"},
                        {"label": "Gemini"},
                        {"label": "OpenAI"},
                    ],
                    "required_setup_actions": [
                        {"label": "Connect My Computer", "href": "/integrations"},
                        {"label": "Add API key", "href": "/integrations"},
                    ]
                }
            },
        )

        self.assertIn("Sage AI: setup required", reply)
        self.assertIn("Workspace AI: blocked (policy disabled)", reply)
        self.assertIn("Agent Computer: offline", reply)
        self.assertIn("Connected apps: Google Workspace, GitHub (not connected)", reply)
        self.assertIn("Messaging channels: Telegram, WhatsApp (not ready)", reply)
        self.assertIn("Additional AI connections: 3 configured", reply)
        self.assertIn("Setup available now: Connect Agent Computer, Add API key.", reply)


if __name__ == "__main__":
    unittest.main()
