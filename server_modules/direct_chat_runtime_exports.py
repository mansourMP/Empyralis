from __future__ import annotations

import json
import logging
import os
import re
import sentry_sdk
import sys
import time
import importlib.util
from functools import partial
from pathlib import Path
from typing import Any, Dict, List, Optional

from scripts.orion_local_worker_llm import (
    SUPPORTED_PROVIDERS,
    generate_chat_reply_stream_with_provider_fallback,
    generate_chat_reply_with_provider_fallback,
    get_claude_code_session_token,
    parse_json_object_loose,
    provider_has_key,
)
from scripts.orion_local_worker_utils import build_operator_system_prompt
from server_modules import direct_chat_availability_service
from server_modules import direct_chat_handoff_facade_service
from server_modules import direct_chat_entry_policy_service
from server_modules import direct_chat_operator_binding_service
from server_modules import direct_chat_operator_support_service
from server_modules import direct_chat_support_binding_service
from server_modules import direct_chat_provider_facade_service
from server_modules import direct_chat_routing_service
from server_modules import direct_chat_runtime_facade_service
from server_modules import direct_chat_tool_catalog_service

from server_modules import direct_tool_config_service
from server_modules import direct_tool_execution_service
from server_modules import memory_service
from server_modules import no_provider_service
from server_modules import runtime_config as runtime_config
from server_modules.provider_profiles import _build_provider_credential_candidates, normalize_auth_mode
from server_modules.local_queue import _is_worker_online
from server_modules.shared import LOCAL_WORKER_REGISTRY
try:
    from server_modules.tool_availability_truth import resolve_workspace_tool_capabilities
except Exception:
    _tool_availability_path = Path(__file__).resolve().parent / "tool_availability_truth.py"
    _tool_availability_spec = importlib.util.spec_from_file_location("operator_chat_tool_availability_truth", _tool_availability_path)
    _tool_availability_module = importlib.util.module_from_spec(_tool_availability_spec)
    sys.modules["operator_chat_tool_availability_truth"] = _tool_availability_module
    assert _tool_availability_spec and _tool_availability_spec.loader
    _tool_availability_spec.loader.exec_module(_tool_availability_module)
    resolve_workspace_tool_capabilities = _tool_availability_module.resolve_workspace_tool_capabilities

from server_modules.memory_service import (
    delete_memory,
    get_memory_notebook_excerpt,
    get_memory,
    list_memory_entries,
    search_memory_notebook,
    update_memory_context_file,
)
from server_modules.agent_turn import resolve_agent_turn_request
from server_modules.conversation_memory_policy import DIRECT_CHAT_PROFILE, get_memory_policy_profile
from server_modules.conversation_compaction import compact_conversation_history
from server_modules.llm_task import llm_task
from server_modules.session_transcript_store import save_session_transcript
from server_modules.web_tools import web_fetch, web_search
from server_modules.workspace_context import workspace_context_dir

WORKFLOW_REQUEST_MARKERS = (
    "turn this into a workflow",
    "turn this into workflow",
    "make this a workflow",
    "make this workflow",
    "create a workflow",
    "create workflow",
    "build a workflow",
    "build workflow",
    "set up a workflow",
    "set this up as a workflow",
    "turn this into an automation",
    "turn this into automation",
    "set this up as automation",
    "automate this",
    "repeat this every day",
    "repeat this every week",
)
EXECUTION_MARKERS = (
    "run",
    "execute",
    "send",
    "draft",
    "check",
    "triage",
    "summarize",
    "review",
    "search",
    "find",
    "inspect",
    "pull",
    "organize",
    "schedule",
    "prepare",
    "update",
    "create",
)
QUESTION_OPENERS = ("what", "why", "how", "should", "can", "could", "would", "is", "are", "do", "does")
GOOGLE_WORKSPACE_KEYWORDS = ("email", "emails", "gmail", "inbox", "calendar", "drive", "meeting", "meetings")
SMTP_KEYWORDS = ("smtp", "generic email", "mail server", "imap")
TELEGRAM_KEYWORDS = ("telegram", "bot", "chat reply", "message on telegram")
SLACK_KEYWORDS = ("slack", "slack dm", "slack message", "post to slack", "send to slack")
DISCORD_KEYWORDS = ("discord", "discord dm", "discord message", "post to discord", "send to discord")
DROPBOX_KEYWORDS = ("dropbox", "dropbox folder", "shared link", "dropbox file")
S3_KEYWORDS = ("s3", "amazon s3", "bucket", "buckets", "presigned url", "object storage")
BROWSER_KEYWORDS = (
    "browser",
    "go to",
    "open page",
    "open website",
    "open site",
    "visit",
    "page title",
    "main heading",
    "click",
    "fill form",
    "screenshot",
    "screen shot",
)
CHAT_MAX_ITERATIONS_DEFAULT = 30
CHAT_MAX_ITERATIONS_CEILING = 100
LOCAL_FILE_KEYWORDS = (
    "read file",
    "open file",
    "write file",
    "save file",
    "save to",
    "append to",
    "delete file",
    "file at",
    "path",
)
LOCAL_SHELL_KEYWORDS = (
    "shell command",
    "terminal command",
    "run command",
    "execute command",
    "in the shell",
    "in terminal",
    "bash",
    "zsh",
)
LOCAL_SCREENSHOT_KEYWORDS = (
    "screenshot",
    "screen shot",
    "capture screen",
    "capture the screen",
    "take a screenshot",
)
LOCAL_COMPUTER_CONTROL_KEYWORDS = (
    "ocr",
    "screen text",
    "read the screen",
    "click at",
    "click the screen",
    "type text",
    "paste this",
    "copy to clipboard",
    "read clipboard",
    "write clipboard",
    "launch app",
    "open finder",
    "open mail",
    "applescript",
    "notification",
    "notify me",
    "list running apps",
    "speak this",
    "read this aloud",
    "say this",
)
LOGGER = logging.getLogger(__name__)


_safe_positive_int = direct_chat_entry_policy_service.safe_positive_int
_resolved_chat_iteration_limit = partial(
    direct_chat_entry_policy_service.resolved_chat_iteration_limit,
    default_limit=CHAT_MAX_ITERATIONS_DEFAULT,
    ceiling=CHAT_MAX_ITERATIONS_CEILING,
    env_var_name="ORION_MAX_CHAT_ITERATIONS",
    safe_positive_int_fn=_safe_positive_int,
)
_chat_iteration_limit_reply = direct_chat_entry_policy_service.chat_iteration_limit_reply
WEB_LOOKUP_KEYWORDS = (
    "latest",
    "today",
    "current",
    "look up",
    "lookup",
    "research",
    "web search",
    "search the web",
    "search web",
    "search online",
    "internet search",
    "online",
    "website",
    "web",
    "news",
)
HTTP_REQUEST_KEYWORDS = (
    "http request",
    "api request",
    "call the api",
    "call this api",
    "call this endpoint",
    "endpoint",
    "webhook",
    "rest api",
    "post to",
    "put to",
    "patch",
    "delete",
    "curl",
)
IMAGE_GENERATION_KEYWORDS = (
    "generate image",
    "create image",
    "make an image",
    "image prompt",
    "illustration",
    "render an image",
    "poster",
    "concept art",
)
LLM_TASK_KEYWORDS = (
    "analyze",
    "classify",
    "extract",
    "compare",
    "summarize",
    "rewrite",
    "convert",
)
COMPLEX_TASK_SEQUENCE_MARKERS = (
    " and then ",
    " then ",
    " after that ",
    " afterwards ",
    " next ",
    " finally ",
    " step by step",
    " end to end",
)
COMPLEX_TASK_OUTCOME_MARKERS = (
    "next steps",
    "plan",
    "report",
    "investigate",
    "debug",
    "fix",
    "compare",
    "audit",
)
DIRECT_RUN_OPENERS = (
    "summarize",
    "check",
    "search",
    "find",
    "review",
    "triage",
    "draft",
    "send",
    "schedule",
    "prepare",
    "update",
    "create",
    "organize",
    "pull",
    "inspect",
    "please",
    "can you",
    "could you",
    "would you",
)
MAX_DIRECT_CHAT_PRIOR_MESSAGES = 80
MAX_DIRECT_CHAT_PRIOR_MESSAGE_CHARS = 4000
MAX_CONTEXT_TOOL_CAPABILITIES = 6
MAX_CONTEXT_TOOL_ACTIONS = 6
DIRECT_CHAT_RUN_HANDOFF_LIVE_WINDOW_SECONDS = 12.0
DIRECT_CHAT_RUN_HANDOFF_POLL_SECONDS = 0.25
DIRECT_CHAT_COMPACTION_TOKEN_LIMIT = get_memory_policy_profile(DIRECT_CHAT_PROFILE).max_prompt_tokens
DIRECT_CHAT_LOOP_REPEAT_LIMIT = 3
_DIRECT_CHAT_MEMORY_SYSTEM_PREFIX = (
    "Persistent workspace memory. Use this only as background context when it is relevant, "
    "and do not repeat it unless it helps answer the user.\n"
)
_DIRECT_CHAT_MEMORY_EXTRACTION_SYSTEM_PROMPT = (
    "You extract durable memory from a chat. Return only a JSON array of strings. "
    "Include stable user preferences, facts, and lasting project context that will be useful in future conversations. "
    "Do not include temporary task details, one-off requests, raw tool outputs, or assistant opinions."
)
_DIRECT_CHAT_MEMORY_EXTRACTION_PROMPT = (
    "What important facts about the user or their preferences were revealed in this conversation? "
    "Reply with a JSON list or empty list."
)
_DIRECT_CHAT_LOOP_REPLY = "A tool loop was detected. Please rephrase the request or break it into smaller steps."
_DIRECT_TOOL_LOOP_STATE: Dict[str, Dict[str, Any]] = {}
_DIRECT_CHAT_MODEL_PREFERENCES: Dict[str, Dict[str, Optional[str]]] = {}
_DIRECT_CHAT_CLEAR_MARKERS: set[str] = set()
_MEMORY_NOTEBOOK_TOOL_NAMES = {
    "memory_search",
    "memory_get",
    "memory_update",
    "memory_stage_edit",
    "memory_apply_edit",
    "memory_append_daily_note",
    "memory_stage_consolidation",
    "memory_consolidate_daily_notes",
    "memory_list_versions",
    "memory_rollback_version",
}


_DIRECT_TOOL_RESULT_SUMMARY_SYSTEM_MESSAGE = (
    "Do not repeat or quote file contents, shell output, or tool results in your response. "
    "Use the information to answer the user's question directly and concisely. "
    "Never paste raw content."
)


globals().update(
    direct_chat_operator_binding_service.build_direct_chat_module_export_map_from_namespace(
        namespace=globals(),
        agent_machine_full_trust_enabled_fn=runtime_config.agent_machine_full_trust_enabled,
        chat_max_iterations_default=CHAT_MAX_ITERATIONS_DEFAULT,
        chat_max_iterations_ceiling=CHAT_MAX_ITERATIONS_CEILING,
        compact_text_fn=lambda value: re.sub(r"\s+", " ", str(value or "").strip()).lower(),
        direct_chat_compaction_token_limit=DIRECT_CHAT_COMPACTION_TOKEN_LIMIT,
        execution_markers=EXECUTION_MARKERS,
        question_openers=QUESTION_OPENERS,
        direct_run_openers=DIRECT_RUN_OPENERS,
        workflow_request_markers=WORKFLOW_REQUEST_MARKERS,
        google_workspace_keywords=GOOGLE_WORKSPACE_KEYWORDS,
        smtp_keywords=SMTP_KEYWORDS,
        telegram_keywords=TELEGRAM_KEYWORDS,
        slack_keywords=SLACK_KEYWORDS,
        dropbox_keywords=DROPBOX_KEYWORDS,
        s3_keywords=S3_KEYWORDS,
        max_context_tool_actions=MAX_CONTEXT_TOOL_ACTIONS,
        max_context_tool_capabilities=MAX_CONTEXT_TOOL_CAPABILITIES,
        max_direct_chat_prior_message_chars=MAX_DIRECT_CHAT_PRIOR_MESSAGE_CHARS,
        max_direct_chat_prior_messages=MAX_DIRECT_CHAT_PRIOR_MESSAGES,
        direct_chat_model_preferences=_DIRECT_CHAT_MODEL_PREFERENCES,
        direct_chat_clear_markers=_DIRECT_CHAT_CLEAR_MARKERS,
        direct_tool_loop_state=_DIRECT_TOOL_LOOP_STATE,
        direct_chat_loop_repeat_limit=DIRECT_CHAT_LOOP_REPEAT_LIMIT,
        parse_json_object_loose_fn=parse_json_object_loose,
        generate_reply_fn=generate_chat_reply_with_provider_fallback,
        extraction_prompt=_DIRECT_CHAT_MEMORY_EXTRACTION_PROMPT,
        extraction_system_prompt=_DIRECT_CHAT_MEMORY_EXTRACTION_SYSTEM_PROMPT,
        save_session_transcript_fn=save_session_transcript,
        system_prefix=_DIRECT_CHAT_MEMORY_SYSTEM_PREFIX,
        workspace_context_dir_fn=workspace_context_dir,
        memory_suggestion_prompts_fn=lambda workspace_id: memory_service.memory_suggestion_prompts(
            workspace_id,
            limit=2,
        ),
        direct_chat_run_snapshot_fn=lambda run_id: _direct_chat_run_snapshot(run_id),
        direct_chat_run_event_to_step_fn=lambda run_id, event: _direct_chat_run_event_to_step(run_id, event),
        direct_chat_run_snapshot_to_step_fn=lambda run_id, snapshot: _direct_chat_run_snapshot_to_step(run_id, snapshot),
        direct_chat_run_final_payload_fn=lambda **kwargs: _direct_chat_run_final_payload(**kwargs),
        live_window_seconds=DIRECT_CHAT_RUN_HANDOFF_LIVE_WINDOW_SECONDS,
        poll_seconds=DIRECT_CHAT_RUN_HANDOFF_POLL_SECONDS,
        monotonic_fn=time.monotonic,
        sleep_fn=time.sleep,
        parse_json_object_loose_support_fn=parse_json_object_loose,
        direct_chat_tool_policy_callbacks_fn=lambda: _direct_chat_tool_policy_callbacks(),
        direct_chat_routing_policy_callbacks_fn=lambda: _direct_chat_routing_policy_callbacks(),
        capture_exception_fn=sentry_sdk.capture_exception,
        generate_chat_reply_stream_with_provider_fallback_fn=generate_chat_reply_stream_with_provider_fallback,
        compact_conversation_history_fn=compact_conversation_history,
        parse_memory_write_fn=memory_service.parse_no_provider_memory_write,
        parse_memory_read_fn=memory_service.parse_no_provider_memory_read,
        handle_memory_request_fn=memory_service.handle_no_provider_memory_request,
        list_memory_entries_fn=list_memory_entries,
        get_memory_fn=get_memory,
        delete_memory_fn=delete_memory,
        no_provider_reasoning_required_response_fn=no_provider_service.no_provider_reasoning_required_response,
        supported_providers=list(SUPPORTED_PROVIDERS),
        complex_task_sequence_markers=COMPLEX_TASK_SEQUENCE_MARKERS,
        complex_task_outcome_markers=COMPLEX_TASK_OUTCOME_MARKERS,
        discord_keywords=DISCORD_KEYWORDS,
        browser_keywords=BROWSER_KEYWORDS,
        local_file_keywords=LOCAL_FILE_KEYWORDS,
        local_shell_keywords=LOCAL_SHELL_KEYWORDS,
        local_screenshot_keywords=LOCAL_SCREENSHOT_KEYWORDS,
        local_computer_control_keywords=LOCAL_COMPUTER_CONTROL_KEYWORDS,
        web_lookup_keywords=WEB_LOOKUP_KEYWORDS,
        http_request_keywords=HTTP_REQUEST_KEYWORDS,
        image_generation_keywords=IMAGE_GENERATION_KEYWORDS,
        llm_task_keywords=LLM_TASK_KEYWORDS,
        llm_task_fn=llm_task,
        web_search_fn=web_search,
        web_fetch_fn=web_fetch,
        search_memory_notebook_fn=search_memory_notebook,
        get_memory_notebook_excerpt_fn=get_memory_notebook_excerpt,
        build_operator_system_prompt_fn=build_operator_system_prompt,
        memory_tool_names=_MEMORY_NOTEBOOK_TOOL_NAMES,
        local_worker_registry=LOCAL_WORKER_REGISTRY,
        is_worker_online_fn=_is_worker_online,
        resolve_workspace_tool_capabilities_fn=resolve_workspace_tool_capabilities,
        build_provider_credential_candidates_fn=_build_provider_credential_candidates,
        normalize_auth_mode_fn=normalize_auth_mode,
        get_claude_code_session_token_fn=get_claude_code_session_token,
        provider_has_key_fn=provider_has_key,
        chat_iteration_limit_reply_fn=_chat_iteration_limit_reply,
        safe_positive_int_fn=_safe_positive_int,
    )
)
