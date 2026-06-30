import sys
import unittest
import json
from pathlib import Path
from unittest.mock import patch


ROOT_DIR = Path(__file__).resolve().parents[2]
SCRIPTS_DIR = ROOT_DIR / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import orion_local_worker_llm as worker_llm


class OrionLocalWorkerLlmDirectToolTests(unittest.TestCase):
    def test_extract_dsml_tool_call_from_codex_text(self):
        text = (
            "Let me check that.\n\n"
            "<||DSML||tool_calls>"
            "<||DSML||invoke name=\"bash\">"
            "<||DSML||parameter name=\"description\" string=\"true\">Check current directory</||DSML||parameter>"
            "<||DSML||parameter name=\"command\" string=\"true\">pwd && uname -a</||DSML||parameter>"
            "</||DSML||invoke>"
        )

        clean_text, tool_calls = worker_llm.extract_dsml_tool_calls_from_text(text)

        self.assertEqual(clean_text, "Let me check that.")
        self.assertEqual(tool_calls, [
            {
                "name": "shell__exec",
                "arguments": {
                    "command": "pwd && uname -a",
                    "description": "Check current directory",
                },
            }
        ])

    def test_extract_spaced_dsml_tool_call_from_codex_text(self):
        text = (
            "One moment.\n"
            "< | | DSML | | tool_calls>"
            "< | | DSML | | invoke name=\"bash\">"
            "< | | DSML | | parameter name=\"command\" string=\"true\">ls -la</ | | DSML | | parameter>"
            "</ | | DSML | | invoke>"
        )

        clean_text, tool_calls = worker_llm.extract_dsml_tool_calls_from_text(text)

        self.assertEqual(clean_text, "One moment.")
        self.assertEqual(tool_calls[0]["name"], "shell__exec")
        self.assertEqual(tool_calls[0]["arguments"]["command"], "ls -la")

    def test_extract_fullwidth_dsml_tool_call_from_trusted_worker_text(self):
        text = (
            "Let me check.\n"
            "<｜｜DSML｜｜tool_calls>"
            "<｜｜DSML｜｜invoke name=\"bash\">"
            "<｜｜DSML｜｜parameter name=\"description\" string=\"true\">Check hardware</｜｜DSML｜｜parameter>"
            "<｜｜DSML｜｜parameter name=\"command\" string=\"true\">system_profiler SPHardwareDataType</｜｜DSML｜｜parameter>"
            "</｜｜DSML｜｜invoke>"
        )

        clean_text, tool_calls = worker_llm.extract_dsml_tool_calls_from_text(text)

        self.assertEqual(clean_text, "Let me check.")
        self.assertEqual(tool_calls, [
            {
                "name": "shell__exec",
                "arguments": {
                    "command": "system_profiler SPHardwareDataType",
                    "description": "Check hardware",
                },
            }
        ])

    def test_extract_mixed_delimiter_dsml_tool_call_from_trusted_worker_text(self):
        text = (
            "Let me check.\n"
            "<|｜DSML｜|tool_calls>"
            "<｜|DSML|｜invoke name=\"bash\">"
            "<|｜DSML｜|parameter name=\"command\" string=\"true\">system_profiler SPHardwareDataType</｜|DSML|｜parameter>"
            "</｜|DSML|｜invoke>"
        )

        clean_text, tool_calls = worker_llm.extract_dsml_tool_calls_from_text(text)

        self.assertEqual(clean_text, "Let me check.")
        self.assertEqual(tool_calls[0]["name"], "shell__exec")
        self.assertEqual(tool_calls[0]["arguments"]["command"], "system_profiler SPHardwareDataType")

    def test_codex_backend_buffers_dsml_text_instead_of_streaming_it(self):
        class FakeSseResponse:
            def __init__(self, chunks):
                self._chunks = list(chunks)

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self, _size):
                if not self._chunks:
                    return b""
                return self._chunks.pop(0)

        dsml_text = (
            "Let me check.\n"
            "< | | DSML | | tool_calls>"
            "< | | DSML | | invoke name=\"bash\">"
            "< | | DSML | | parameter name=\"command\" string=\"true\">pwd</ | | DSML | | parameter>"
            "</ | | DSML | | invoke>"
        )
        events = [
            {"type": "response.output_text.delta", "delta": dsml_text},
            {"type": "response.completed", "response": {"usage": {"input_tokens": 1}}},
        ]
        payload = "".join(f"data: {json.dumps(event)}\n\n" for event in events).encode("utf-8")

        with patch("urllib.request.urlopen", return_value=FakeSseResponse([payload])):
            parsed_events = list(worker_llm.iter_openai_codex_backend_events(
                "system",
                "check my laptop",
                credential_override={"oauth_token": "token", "account_id": "acct"},
                tools=[],
            ))

        self.assertEqual(len(parsed_events), 1)
        self.assertEqual(parsed_events[0]["type"], "done")
        self.assertEqual(parsed_events[0]["text"], "Let me check.")
        self.assertEqual(parsed_events[0]["tool_calls"][0]["name"], "shell__exec")
        self.assertEqual(parsed_events[0]["tool_calls"][0]["arguments"]["command"], "pwd")

    def test_resolve_requested_tools_prefers_metadata(self):
        tools = worker_llm.resolve_requested_tools(
            {"tools": [{"name": "context_only", "parameters": {"type": "object"}}]},
            {
                "tools": [
                    {
                        "name": "telegram_bot__send_message",
                        "description": "Execute send_message on Telegram Bot LIVE",
                        "parameters": {
                            "type": "object",
                            "properties": {"input": {"type": "string"}},
                            "required": ["input"],
                        },
                    }
                ]
            },
        )

        self.assertEqual(len(tools), 1)
        self.assertEqual(tools[0]["name"], "telegram_bot__send_message")

    def test_generate_chat_stream_propagates_tool_calls_from_codex_backend(self):
        with patch.object(worker_llm, "provider_order_for_run", return_value=["codex_cli"]):
            with patch.object(
                worker_llm,
                "iter_openai_codex_backend_events",
                return_value=iter([
                    {
                        "type": "done",
                        "text": "",
                        "usage": None,
                        "model": "gpt-5.4",
                        "tool_calls": [
                            {
                                "name": "telegram_bot__send_message",
                                "arguments": "{\"input\":\"hello world\"}",
                            }
                        ],
                    }
                ]),
            ) as events_mock:
                events = list(
                    worker_llm.generate_chat_reply_stream_with_provider_fallback(
                        context={},
                        metadata={
                            "tools": [
                                {
                                    "name": "telegram_bot__send_message",
                                    "description": "Execute send_message on Telegram Bot LIVE",
                                    "parameters": {
                                        "type": "object",
                                        "properties": {"input": {"type": "string"}},
                                        "required": ["input"],
                                    },
                                }
                            ]
                        },
                        user_goal="send a telegram message",
                        system_prompt="You are concise.",
                    )
                )

        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["type"], "result")
        self.assertEqual(events[0]["tool_calls"][0]["name"], "telegram_bot__send_message")
        events_mock.assert_called_once()

    def test_generate_chat_stream_propagates_tool_calls_from_deepseek_branch_when_tools_present(self):
        with patch.object(worker_llm, "provider_order_for_run", return_value=["deepseek"]):
            with patch.object(
                worker_llm,
                "iter_openai_compatible_chat_events",
                return_value=iter([
                    {
                        "type": "done",
                        "text": "",
                        "usage": None,
                        "model": "deepseek-chat",
                        "tool_calls": [
                            {
                                "id": "call_1",
                                "name": "file__read",
                                "arguments": {"path": "~/Desktop"},
                            }
                        ],
                    }
                ]),
            ) as events_mock:
                events = list(
                    worker_llm.generate_chat_reply_stream_with_provider_fallback(
                        context={},
                        metadata={
                            "tools": [
                                {
                                    "name": "file__read",
                                    "description": "Read a local file",
                                    "parameters": {
                                        "type": "object",
                                        "properties": {"path": {"type": "string"}},
                                        "required": ["path"],
                                    },
                                }
                            ]
                        },
                        user_goal="List the files on my desktop.",
                        system_prompt="You are concise.",
                    )
                )

        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["type"], "result")
        self.assertEqual(events[0]["provider"], "deepseek")
        self.assertEqual(events[0]["tool_calls"][0]["name"], "file__read")
        events_mock.assert_called_once()

    def test_generate_chat_stream_retries_tool_mode_with_compact_prompt_after_transport_error(self):
        seen_prompts = []

        def _iter_events(prompt_variant, *_args, **_kwargs):
            seen_prompts.append(prompt_variant)
            if len(seen_prompts) == 1:
                return iter([
                    {
                        "type": "error",
                        "error": "IncompleteRead(0 bytes read)",
                        "model": "deepseek-chat",
                    }
                ])
            return iter([
                {
                    "type": "done",
                    "text": "",
                    "usage": None,
                    "model": "deepseek-chat",
                    "tool_calls": [
                        {
                            "id": "call_1",
                            "name": "file__read",
                            "arguments": {"path": "~/Desktop"},
                        }
                    ],
                }
            ])

        with patch.object(worker_llm, "provider_order_for_run", return_value=["deepseek"]):
            with patch.object(
                worker_llm,
                "iter_openai_compatible_chat_events",
                side_effect=_iter_events,
            ) as events_mock:
                events = list(
                    worker_llm.generate_chat_reply_stream_with_provider_fallback(
                        context={},
                        metadata={
                            "tools": [
                                {
                                    "name": "file__read",
                                    "description": "Read a local file",
                                    "parameters": {
                                        "type": "object",
                                        "properties": {"path": {"type": "string"}},
                                        "required": ["path"],
                                    },
                                }
                            ]
                        },
                        user_goal="List the files on my desktop.",
                        system_prompt=(
                            "Base instructions\n\n"
                            "## Workspace Context\nVery large workspace context here.\n\n"
                            "## Runtime Identity\nprovider deepseek, model deepseek-chat."
                        ),
                    )
                )

        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["type"], "result")
        self.assertEqual(events[0]["tool_calls"][0]["name"], "file__read")
        self.assertEqual(events_mock.call_count, 2)
        self.assertIn("## Workspace Context", str(seen_prompts[0]))
        self.assertNotIn("## Workspace Context", str(seen_prompts[1]))
        self.assertIn("## Runtime Identity", str(seen_prompts[1]))

    def test_build_openai_compatible_messages_preserves_tool_turns(self):
        messages = worker_llm._build_openai_compatible_messages(
            "",
            prior_messages=[
                {"role": "user", "content": "List the files on my desktop."},
                {
                    "role": "assistant",
                    "tool_calls": [
                        {
                            "id": "call_1",
                            "name": "file__read",
                            "arguments": {"path": "~/Desktop"},
                        }
                    ],
                },
                {
                    "role": "tool",
                    "tool_call_id": "call_1",
                    "name": "file__read",
                    "content": "Desktop contents here",
                },
            ],
        )

        self.assertEqual(messages[1]["role"], "assistant")
        self.assertEqual(messages[1]["tool_calls"][0]["function"]["name"], "file__read")
        self.assertEqual(messages[2]["role"], "tool")
        self.assertEqual(messages[2]["tool_call_id"], "call_1")


if __name__ == "__main__":
    unittest.main()
