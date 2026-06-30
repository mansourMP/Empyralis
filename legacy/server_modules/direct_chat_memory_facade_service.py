from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional

from server_modules import conversation_memory_facade_service
from server_modules import memory_summary_service
from server_modules import workspace_context_memory_adapter
from server_modules.conversation_memory_policy import DIRECT_CHAT_PROFILE


def direct_chat_memory_context_message(
    workspace_id: str,
    *,
    system_prefix: str,
) -> Optional[Dict[str, str]]:
    context = conversation_memory_facade_service.load_context(
        conversation_memory_facade_service.ConversationMemorySubject(
            workspace_id=workspace_id,
            surface_kind=conversation_memory_facade_service.DIRECT_CHAT_SURFACE,
        ),
        DIRECT_CHAT_PROFILE,
        metadata={"include_memory_context": True},
    )
    return workspace_context_memory_adapter.build_workspace_memory_context_message(
        system_prefix=system_prefix,
        contextual_blocks=context.contextual_blocks,
    )


def direct_chat_workspace_context_text(
    workspace_id: str,
    *,
    memory_query: str = "",
) -> str:
    return conversation_memory_facade_service.build_workspace_context_text(
        conversation_memory_facade_service.ConversationMemorySubject(
            workspace_id=workspace_id,
            surface_kind=conversation_memory_facade_service.DIRECT_CHAT_SURFACE,
        ),
        DIRECT_CHAT_PROFILE,
        query_text=memory_query,
    )


def build_direct_chat_daily_log_summary(
    *,
    user_message: str,
    assistant_reply: str,
) -> str:
    return memory_summary_service.build_direct_chat_daily_log_summary(
        user_message=user_message,
        assistant_reply=assistant_reply,
    )


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
    conversation_memory_facade_service.persist_interaction(
        conversation_memory_facade_service.ConversationMemorySubject(
            workspace_id=workspace_id,
            surface_kind=conversation_memory_facade_service.DIRECT_CHAT_SURFACE,
        ),
        DIRECT_CHAT_PROFILE,
        user_message=user_message,
        assistant_reply=assistant_reply,
        metadata={
            "persist_memory": True,
            "provider": provider,
            "model": model,
            "credentials": credentials,
            "reasoning_effort": reasoning_effort,
            "prior_messages": prior_messages,
            "generate_reply": generate_reply,
            "extraction_prompt": extraction_prompt,
            "extraction_system_prompt": extraction_system_prompt,
        },
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
    try:
        conversation_memory_facade_service.persist_interaction(
            conversation_memory_facade_service.ConversationMemorySubject(
                workspace_id=workspace_id,
                thread_id=thread_id,
                surface_kind=conversation_memory_facade_service.DIRECT_CHAT_SURFACE,
            ),
            DIRECT_CHAT_PROFILE,
            user_message=user_message,
            assistant_reply=assistant_reply,
            metadata={
                "persist_transcript": True,
                "provider": provider,
                "model": model,
                "messages": messages,
                "save_session_transcript_fn": save_session_transcript_fn,
            },
        )
    except Exception:
        return
