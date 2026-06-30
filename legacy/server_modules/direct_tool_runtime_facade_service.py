from __future__ import annotations

from typing import Any, Dict, List, Optional

from server_modules import direct_chat_runtime_facade_service
from server_modules import direct_tool_execution_service


_EXECUTION_CALLBACK_NAMES = (
    "compact_step_detail",
    "titleize_direct_step_token",
    "run_async_tool_call",
    "parse_tool_name",
    "tool_arguments_payload",
    "safe_positive_int",
    "normalize_reasoning_effort",
    "build_direct_local_tool_config",
    "format_direct_local_tool_result",
    "build_direct_tool_config",
    "format_direct_tool_result",
)


def _lookup(namespace: Dict[str, Any], name: str) -> Any:
    value = namespace.get(name)
    if value is None:
        raise KeyError(f"Missing execution dependency '{name}'")
    return value


def build_direct_tool_execution_callbacks(
    *,
    namespace: Dict[str, Any],
    parse_json_object_loose: Any,
    llm_task: Any,
    web_search: Any,
    web_fetch: Any,
    search_memory_notebook: Any,
    get_memory_notebook_excerpt: Any,
) -> direct_tool_execution_service.DirectToolExecutionCallbacks:
    base = {name: _lookup(namespace, name) for name in _EXECUTION_CALLBACK_NAMES}
    return direct_tool_execution_service.DirectToolExecutionCallbacks(
        compact_step_detail=base["compact_step_detail"],
        titleize_direct_step_token=base["titleize_direct_step_token"],
        run_async_tool_call=base["run_async_tool_call"],
        parse_tool_name=base["parse_tool_name"],
        tool_arguments_payload=base["tool_arguments_payload"],
        parse_json_object_loose=parse_json_object_loose,
        safe_positive_int=base["safe_positive_int"],
        normalize_reasoning_effort=base["normalize_reasoning_effort"],
        build_direct_local_tool_config=base["build_direct_local_tool_config"],
        format_direct_local_tool_result=base["format_direct_local_tool_result"],
        build_direct_tool_config=base["build_direct_tool_config"],
        format_direct_tool_result=base["format_direct_tool_result"],
        llm_task=llm_task,
        web_search=web_search,
        web_fetch=web_fetch,
        search_memory_notebook=search_memory_notebook,
        get_memory_notebook_excerpt=get_memory_notebook_excerpt,
    )


def build_no_provider_execution_services(*, callbacks):
    return direct_chat_runtime_facade_service.build_no_provider_execution_services(callbacks)


def build_direct_tool_approval_response(
    *,
    tool_calls: List[Dict[str, Any]],
    tool_capabilities: List[Dict[str, Any]],
    session_ctx: Optional[Dict[str, Any]],
    callbacks,
) -> Optional[Dict[str, Any]]:
    return direct_chat_runtime_facade_service.build_direct_tool_approval_response(
        tool_calls=tool_calls,
        tool_capabilities=tool_capabilities,
        session_ctx=session_ctx,
        callbacks=callbacks,
    )


def message_has_obvious_direct_tool_intent(
    message: str,
    tools: List[Dict[str, Any]],
    callbacks,
) -> bool:
    return direct_chat_runtime_facade_service.message_has_obvious_direct_tool_intent(
        message,
        tools,
        callbacks,
    )
