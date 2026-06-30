from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional

from server_modules import direct_chat_hosted_usage_service
from server_modules import direct_chat_memory_facade_service
from server_modules import direct_chat_metadata_service
from server_modules import direct_chat_prompt_service


def persist_direct_chat_memory_best_effort(
    *,
    workspace_id: str,
    provider: Optional[str],
    model: Optional[str],
    credentials: Optional[Dict[str, Any]],
    reasoning_effort: str,
    prior_messages: List[Dict[str, str]],
    user_message: str,
    assistant_reply: str,
    generate_reply: Callable[..., Any],
    extraction_prompt: str,
    extraction_system_prompt: str,
) -> None:
    direct_chat_memory_facade_service.persist_direct_chat_memory_best_effort(
        workspace_id=workspace_id,
        provider=provider,
        model=model,
        credentials=credentials,
        reasoning_effort=reasoning_effort,
        prior_messages=prior_messages,
        user_message=user_message,
        assistant_reply=assistant_reply,
        generate_reply=generate_reply,
        extraction_prompt=extraction_prompt,
        extraction_system_prompt=extraction_system_prompt,
    )


def persist_direct_chat_transcript_best_effort(
    *,
    workspace_id: str,
    thread_id: str,
    provider: Optional[str],
    model: Optional[str],
    messages: List[Dict[str, str]],
    user_message: str,
    assistant_reply: str,
    save_session_transcript_fn: Callable[..., Any],
) -> None:
    direct_chat_memory_facade_service.persist_direct_chat_transcript_best_effort(
        workspace_id=workspace_id,
        thread_id=thread_id,
        provider=provider,
        model=model,
        messages=messages,
        user_message=user_message,
        assistant_reply=assistant_reply,
        save_session_transcript_fn=save_session_transcript_fn,
    )


def persist_direct_chat_hosted_usage_best_effort(**kwargs: Any) -> None:
    direct_chat_hosted_usage_service.persist_direct_chat_hosted_usage_best_effort(**kwargs)


def build_context_used(**kwargs: Any) -> Dict[str, Any]:
    return direct_chat_metadata_service.build_context_used(**kwargs)


def heartbeat_pending_tasks_for_suggestions(
    *,
    workspace_context_dir_fn: Callable[..., Any],
) -> List[str]:
    return direct_chat_metadata_service.heartbeat_pending_tasks_for_suggestions(
        workspace_context_dir_fn=workspace_context_dir_fn,
    )


def recent_run_prompts_for_suggestions(
    workspace_id: str,
    *,
    run_history: Any,
) -> List[str]:
    return direct_chat_metadata_service.recent_run_prompts_for_suggestions(
        workspace_id,
        run_history=run_history,
    )


def build_proactive_suggestions(
    workspace_id: str,
    *,
    heartbeat_tasks: Callable[[], List[str]],
    recent_run_prompts: Callable[[str], List[str]],
    memory_suggestion_prompts: Callable[[str], List[str]],
) -> List[str]:
    return direct_chat_prompt_service.build_proactive_suggestions(
        workspace_id,
        heartbeat_tasks=heartbeat_tasks,
        recent_run_prompts=recent_run_prompts,
        memory_suggestion_prompts=memory_suggestion_prompts,
    )
