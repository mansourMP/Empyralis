from __future__ import annotations

import os
from typing import Any, Callable, Dict, List, Optional, Set

from server_modules import direct_chat_context_service
from server_modules import direct_chat_provider_service
from server_modules import direct_chat_routing_service


def safe_positive_int(value: Any, default: int) -> int:
    try:
        parsed = int(value)
    except Exception:
        return int(default)
    return parsed if parsed > 0 else int(default)


def resolved_chat_iteration_limit(
    explicit: Any = None,
    *,
    default_limit: int,
    ceiling: int,
    env_var_name: str,
    safe_positive_int_fn: Callable[[Any, int], int] = safe_positive_int,
    env_get_fn: Callable[[str, str], str] = os.getenv,
) -> int:
    configured = safe_positive_int_fn(
        explicit,
        safe_positive_int_fn(env_get_fn(env_var_name, str(default_limit)), default_limit),
    )
    return max(1, min(configured, ceiling))


def chat_iteration_limit_reply(limit: int) -> str:
    return (
        f"Reached maximum steps ({limit}). "
        "To continue, start a new run or increase ORION_MAX_CHAT_ITERATIONS."
    )


def direct_chat_runtime_available(
    local_worker_registry: Dict[str, Any],
    *,
    is_worker_online_fn: Callable[[Any, Any], bool],
) -> bool:
    return direct_chat_provider_service.direct_chat_runtime_available(
        local_worker_registry,
        is_worker_online_fn=is_worker_online_fn,
    )


def resolve_direct_chat_availability(
    workspace_id: str,
    requested_provider: str = "",
    *,
    direct_chat_runtime_available_fn: Callable[[], bool],
    preferred_provider_fn: Callable[[str, str], tuple[str, Dict[str, Any]]],
    supports_direct_message_native_chat_fn: Callable[[str, Optional[Dict[str, Any]]], bool],
    resolve_workspace_tool_capabilities_fn: Callable[[str], List[Dict[str, Any]]],
    availability_override: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    return direct_chat_provider_service.resolve_direct_chat_availability(
        workspace_id,
        requested_provider,
        direct_chat_runtime_available_fn=direct_chat_runtime_available_fn,
        preferred_provider_fn=preferred_provider_fn,
        supports_direct_message_native_chat_fn=supports_direct_message_native_chat_fn,
        resolve_workspace_tool_capabilities_fn=resolve_workspace_tool_capabilities_fn,
        availability_override=availability_override,
    )


def availability_lines(
    workspace_id: str,
    availability: Dict[str, Any],
    *,
    normalize_tool_capabilities: Any,
) -> List[str]:
    return direct_chat_context_service.availability_lines(
        workspace_id,
        availability,
        normalize_tool_capabilities=normalize_tool_capabilities,
    )


def connected_system_labels(
    availability: Dict[str, Any],
    *,
    normalize_tool_capabilities: Any,
) -> List[str]:
    return direct_chat_context_service.connected_system_labels(
        availability,
        normalize_tool_capabilities=normalize_tool_capabilities,
    )


def context_tool_capabilities(
    availability: Dict[str, Any],
    *,
    normalize_tool_capabilities: Any,
    max_context_tool_actions: int,
    max_context_tool_capabilities: int,
) -> List[Dict[str, Any]]:
    return direct_chat_context_service.context_tool_capabilities(
        availability,
        normalize_tool_capabilities=normalize_tool_capabilities,
        max_context_tool_actions=max_context_tool_actions,
        max_context_tool_capabilities=max_context_tool_capabilities,
    )


def normalize_prior_messages(
    prior_messages: Any,
    *,
    max_direct_chat_prior_message_chars: int,
    max_direct_chat_prior_messages: int,
) -> List[Dict[str, str]]:
    return direct_chat_context_service.normalize_prior_messages(
        prior_messages,
        max_direct_chat_prior_message_chars=max_direct_chat_prior_message_chars,
        max_direct_chat_prior_messages=max_direct_chat_prior_messages,
    )


def direct_tool_session_key(workspace_id: str, thread_id: str) -> str:
    return direct_chat_context_service.direct_tool_session_key(workspace_id, thread_id)


def direct_chat_session_key(workspace_id: str, thread_id: str) -> str:
    return direct_chat_context_service.direct_chat_session_key(workspace_id, thread_id)


def parse_slash_command(message: str) -> Dict[str, str]:
    return direct_chat_context_service.parse_slash_command(message)


def session_model_preference(
    session_key: str,
    *,
    store: Dict[str, Dict[str, Optional[str]]],
) -> Dict[str, Optional[str]]:
    return direct_chat_context_service.session_model_preference(
        session_key,
        store=store,
    )


def set_session_model_preference(
    session_key: str,
    *,
    provider: Optional[str],
    model: Optional[str],
    store: Dict[str, Dict[str, Optional[str]]],
) -> None:
    direct_chat_context_service.set_session_model_preference(
        session_key,
        provider=provider,
        model=model,
        store=store,
    )


def mark_thread_cleared(session_key: str, *, clear_markers: Set[str]) -> None:
    direct_chat_context_service.mark_thread_cleared(
        session_key,
        clear_markers=clear_markers,
    )


def consume_thread_cleared(session_key: str, *, clear_markers: Set[str]) -> bool:
    return direct_chat_context_service.consume_thread_cleared(
        session_key,
        clear_markers=clear_markers,
    )


def connected_provider_tokens(
    workspace_id: str,
    *,
    supported_providers: List[str] | tuple[str, ...],
    direct_chat_credentials_fn: Callable[[str, str], Dict[str, Any]],
) -> List[str]:
    return direct_chat_provider_service.connected_provider_tokens(
        workspace_id,
        supported_providers=supported_providers,
        direct_chat_credentials_fn=direct_chat_credentials_fn,
    )


def resolve_provider_for_direct_chat_message(
    workspace_id: str,
    requested_provider: str,
    message: str,
    *,
    tools_present: bool,
    preferred_provider_fn: Callable[[str, str], tuple[str, Dict[str, Any]]],
    direct_chat_credentials_fn: Callable[[str, str], Dict[str, Any]],
    supports_direct_message_native_chat_fn: Callable[[str, Optional[Dict[str, Any]]], bool],
    compact_text_fn: Callable[[Any], str],
    mentions_any_fn: Callable[[str, tuple[str, ...] | list[str]], bool],
    message_requests_local_file_tool_fn: Callable[[str], bool],
    message_requests_local_shell_tool_fn: Callable[[str], bool],
    message_requests_local_screenshot_tool_fn: Callable[[str], bool],
    message_requests_local_computer_tool_fn: Callable[[str], bool],
    google_workspace_keywords: tuple[str, ...] | list[str],
    telegram_keywords: tuple[str, ...] | list[str],
    slack_keywords: tuple[str, ...] | list[str],
    dropbox_keywords: tuple[str, ...] | list[str],
    s3_keywords: tuple[str, ...] | list[str],
) -> tuple[str, Dict[str, Any]]:
    return direct_chat_provider_service.resolve_provider_for_direct_chat_message(
        workspace_id,
        requested_provider,
        message,
        tools_present=tools_present,
        preferred_provider_fn=preferred_provider_fn,
        direct_chat_credentials_fn=direct_chat_credentials_fn,
        supports_direct_message_native_chat_fn=supports_direct_message_native_chat_fn,
        compact_text_fn=compact_text_fn,
        mentions_any_fn=mentions_any_fn,
        message_requests_local_file_tool_fn=message_requests_local_file_tool_fn,
        message_requests_local_shell_tool_fn=message_requests_local_shell_tool_fn,
        message_requests_local_screenshot_tool_fn=message_requests_local_screenshot_tool_fn,
        message_requests_local_computer_tool_fn=message_requests_local_computer_tool_fn,
        google_workspace_keywords=google_workspace_keywords,
        telegram_keywords=telegram_keywords,
        slack_keywords=slack_keywords,
        dropbox_keywords=dropbox_keywords,
        s3_keywords=s3_keywords,
    )


def plan_direct_chat_route(
    *,
    message: str,
    availability: Dict[str, Any],
    provider: str,
    tools: List[Dict[str, Any]],
    compact_text_fn: Callable[[Any], str],
    mentions_any_fn: Callable[[str, tuple[str, ...] | list[str]], bool],
    is_obvious_smtp_write_request_fn: Callable[[str], bool],
    preview_run_response_fn: Callable[[str, Dict[str, Any]], Optional[Dict[str, Any]]],
    prefer_durable_run_handoff_fn: Callable[[str, Dict[str, Any]], bool],
    durable_run_preferred_response_fn: Callable[[str], Dict[str, Any]],
    message_can_use_direct_connector_tools_fn: Callable[[str], bool],
    message_can_use_direct_local_tools_fn: Callable[[str], bool],
    message_can_use_builtin_direct_tools_fn: Callable[[str], bool],
    can_auto_start_run_handoff_fn: Callable[[Dict[str, Any]], bool],
    google_workspace_keywords: tuple[str, ...] | list[str],
    telegram_keywords: tuple[str, ...] | list[str],
    slack_keywords: tuple[str, ...] | list[str],
    discord_keywords: tuple[str, ...] | list[str],
    dropbox_keywords: tuple[str, ...] | list[str],
    s3_keywords: tuple[str, ...] | list[str],
) -> direct_chat_routing_service.DirectChatRouteDecision:
    return direct_chat_routing_service.plan_direct_chat_route(
        message=message,
        availability=availability,
        provider=provider,
        tools=tools,
        compact_text_fn=compact_text_fn,
        mentions_any_fn=mentions_any_fn,
        is_obvious_smtp_write_request_fn=is_obvious_smtp_write_request_fn,
        preview_run_response_fn=preview_run_response_fn,
        prefer_durable_run_handoff_fn=prefer_durable_run_handoff_fn,
        durable_run_preferred_response_fn=durable_run_preferred_response_fn,
        message_can_use_direct_connector_tools_fn=message_can_use_direct_connector_tools_fn,
        message_can_use_direct_local_tools_fn=message_can_use_direct_local_tools_fn,
        message_can_use_builtin_direct_tools_fn=message_can_use_builtin_direct_tools_fn,
        can_auto_start_run_handoff_fn=can_auto_start_run_handoff_fn,
        google_workspace_keywords=google_workspace_keywords,
        telegram_keywords=telegram_keywords,
        slack_keywords=slack_keywords,
        discord_keywords=discord_keywords,
        dropbox_keywords=dropbox_keywords,
        s3_keywords=s3_keywords,
    )


def active_run_count(workspace_id: str, *, live_runs: Any) -> int:
    return direct_chat_context_service.active_run_count(workspace_id, live_runs=live_runs)


def slash_command_help_text() -> str:
    return direct_chat_context_service.slash_command_help_text()
