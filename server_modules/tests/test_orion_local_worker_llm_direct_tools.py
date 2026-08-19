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

    def test_iter_openai_compatible_chat_events_parses_dsml_tool_call_when_structured_tool_calls_missing(self):
        # MAN-308: live production incident. deepseek-reasoner (the
        # platform-credit default model) was sent a real OpenAI-style
        # tools/tool_choice:"auto" payload (see the payload built in
        # iter_openai_compatible_chat_events) but returned a chat-completions
        # response with an EMPTY message.tool_calls while message.content
        # held a literal DSML tool-call envelope instead. Before this fix,
        # _normalize_openai_function_call only ever looked at
        # message["tool_calls"]/message["function_call"], so this response
        # shape produced tool_calls == [] and the DSML text became the
        # "final answer" text -- nothing reached the executor, and
        # response_leak_guard_service silently stripped the DSML on the way
        # to the user, making the dropped tool call look like a normal,
        # complete answer.
        fake_response = {
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": (
                            "Checking now.\n"
                            "<｜｜DSML｜｜tool_calls>"
                            "<｜｜DSML｜｜invoke name=\"hardware__action\">"
                            "<｜｜DSML｜｜parameter name=\"action\" string=\"true\">shell.execute</｜｜DSML｜｜parameter>"
                            "<｜｜DSML｜｜parameter name=\"command\" string=\"true\">whoami</｜｜DSML｜｜parameter>"
                            "</｜｜DSML｜｜invoke>"
                        ),
                    },
                    "finish_reason": "stop",
                }
            ],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5},
        }

        with patch.object(
            worker_llm,
            "_openai_compatible_chat_completion_request",
            return_value=fake_response,
        ):
            events = list(
                worker_llm.iter_openai_compatible_chat_events(
                    "System prompt",
                    "run whoami on my paired hardware",
                    model_override="deepseek-reasoner",
                    provider="deepseek",
                    credential_override={"api_key": "test-key"},
                    tools=[
                        {
                            "name": "hardware__action",
                            "description": "Run an action on paired hardware.",
                            "parameters": {
                                "type": "object",
                                "properties": {"action": {"type": "string"}},
                            },
                        }
                    ],
                )
            )

        done_events = [event for event in events if event.get("type") == "done"]
        self.assertEqual(len(done_events), 1)
        done = done_events[0]
        self.assertEqual(done["text"], "Checking now.")
        self.assertEqual(len(done["tool_calls"]), 1)
        self.assertEqual(done["tool_calls"][0]["name"], "hardware__action")
        self.assertEqual(done["tool_calls"][0]["arguments"]["command"], "whoami")

        # The raw DSML markup must never appear in a delta chunk either --
        # not just be absent from the final text.
        streamed_deltas = "".join(str(event.get("delta") or "") for event in events if event.get("type") == "delta")
        self.assertNotIn("DSML", streamed_deltas)
        self.assertNotIn("tool_calls", streamed_deltas)

    def test_iter_openai_compatible_chat_events_prefers_structured_tool_calls_over_dsml_text(self):
        # When the provider DOES populate the structured tool_calls field
        # (the common/expected case), that must win outright -- the DSML
        # fallback added for MAN-308 must never run or interfere.
        fake_response = {
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": "",
                        "tool_calls": [
                            {
                                "id": "call_1",
                                "type": "function",
                                "function": {
                                    "name": "hardware__action",
                                    "arguments": "{\"action\": \"shell.execute\", \"command\": \"whoami\"}",
                                },
                            }
                        ],
                    },
                    "finish_reason": "tool_calls",
                }
            ],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5},
        }

        with patch.object(
            worker_llm,
            "_openai_compatible_chat_completion_request",
            return_value=fake_response,
        ):
            events = list(
                worker_llm.iter_openai_compatible_chat_events(
                    "System prompt",
                    "run whoami on my paired hardware",
                    model_override="deepseek-reasoner",
                    provider="deepseek",
                    credential_override={"api_key": "test-key"},
                    tools=[
                        {
                            "name": "hardware__action",
                            "description": "Run an action on paired hardware.",
                            "parameters": {"type": "object", "properties": {"action": {"type": "string"}}},
                        }
                    ],
                )
            )

        done_events = [event for event in events if event.get("type") == "done"]
        self.assertEqual(len(done_events), 1)
        self.assertEqual(done_events[0]["tool_calls"][0]["name"], "hardware__action")

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
