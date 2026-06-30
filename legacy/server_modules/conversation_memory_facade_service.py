from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from server_modules import deployed_channel_memory_adapter
from server_modules import semantic_runtime_memory_adapter
from server_modules import workspace_context_memory_adapter
from server_modules.conversation_memory_policy import (
    DIRECT_CHAT_PROFILE,
    DURABLE_RUN_PROFILE,
    EXTERNAL_CHANNEL_CUSTOMER_PROFILE,
    MemoryPolicyProfile,
    get_memory_policy_profile,
)


DIRECT_CHAT_SURFACE = "direct_chat"
DEPLOYED_AGENT_CHANNEL_SURFACE = "deployed_agent_channel"
DURABLE_RUN_SURFACE = "durable_run"


@dataclass(frozen=True, slots=True)
class ConversationMemorySubject:
    tenant_id: str = ""
    workspace_id: str = "default"
    surface_kind: str = DIRECT_CHAT_SURFACE
    responder_install_id: str = ""
    thread_id: str = ""
    session_key: str = ""
    deployed_agent_id: str = ""
    channel_key: str = ""
    external_user_id: str = ""
    profile_id: str = ""
    project_id: str = ""


@dataclass(frozen=True, slots=True)
class ConversationMemoryLoadRequest:
    subject: ConversationMemorySubject
    policy_profile: str | MemoryPolicyProfile
    query_text: str = ""
    exclude_event_id: str | None = None
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ConversationMemoryPersistRequest:
    subject: ConversationMemorySubject
    policy_profile: str | MemoryPolicyProfile
    user_message: str = ""
    assistant_reply: str = ""
    run_summary: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class ConversationMemoryContext:
    prior_messages: List[Dict[str, str]] = field(default_factory=list)
    contextual_blocks: List[str] = field(default_factory=list)
    stable_contextual_blocks: List[str] = field(default_factory=list)
    retrieved_contextual_blocks: List[str] = field(default_factory=list)
    semantic_hits: List[Dict[str, Any]] = field(default_factory=list)
    rolled_summary: Optional[str] = None
    business_plan: Optional[str] = None
    compacted: bool = False
    diagnostics: Dict[str, Any] = field(default_factory=dict)


def load_context(
    subject: ConversationMemorySubject,
    policy_profile: str | MemoryPolicyProfile,
    *,
    query_text: str = "",
    exclude_event_id: str | None = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> ConversationMemoryContext:
    request = ConversationMemoryLoadRequest(
        subject=subject,
        policy_profile=policy_profile,
        query_text=query_text,
        exclude_event_id=exclude_event_id,
        metadata=dict(metadata or {}),
    )
    surface_kind = str(request.subject.surface_kind or "").strip().lower()
    resolved_profile = get_memory_policy_profile(request.policy_profile)
    if surface_kind == DIRECT_CHAT_SURFACE:
        payload = workspace_context_memory_adapter.load_workspace_context_payload(
            workspace_id=request.subject.workspace_id,
            memory_query=request.query_text,
            agent_install_id=request.subject.responder_install_id or None,
            policy_profile=resolved_profile,
            include_memory_context=request.metadata.get("include_memory_context")
            if isinstance(request.metadata.get("include_memory_context"), bool)
            else None,
        )
        return ConversationMemoryContext(
            contextual_blocks=list(payload.get("contextual_blocks") or []),
            stable_contextual_blocks=list(payload.get("stable_contextual_blocks") or []),
            retrieved_contextual_blocks=list(payload.get("retrieved_contextual_blocks") or []),
            semantic_hits=list(payload.get("semantic_hits") or []),
            diagnostics=dict(payload.get("diagnostics") or {}),
        )
    if surface_kind == DURABLE_RUN_SURFACE:
        return ConversationMemoryContext(
            diagnostics={
                "profile": resolved_profile.name,
                "mode": "durable_run_adapter",
            }
        )
    raise ValueError(f"Use aload_context() for async memory surface: {surface_kind}")


async def aload_context(
    subject: ConversationMemorySubject,
    policy_profile: str | MemoryPolicyProfile,
    *,
    query_text: str = "",
    exclude_event_id: str | None = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> ConversationMemoryContext:
    request = ConversationMemoryLoadRequest(
        subject=subject,
        policy_profile=policy_profile,
        query_text=query_text,
        exclude_event_id=exclude_event_id,
        metadata=dict(metadata or {}),
    )
    surface_kind = str(request.subject.surface_kind or "").strip().lower()
    if surface_kind != DEPLOYED_AGENT_CHANNEL_SURFACE:
        return load_context(
            request.subject,
            request.policy_profile,
            query_text=request.query_text,
            exclude_event_id=request.exclude_event_id,
            metadata=request.metadata,
        )
    payload = await deployed_channel_memory_adapter.load_deployed_channel_context(
        tenant_id=request.subject.tenant_id,
        workspace_id=request.subject.workspace_id,
        deployed_agent=request.metadata.get("deployed_agent") if isinstance(request.metadata.get("deployed_agent"), dict) else None,
        channel_key=request.subject.channel_key,
        external_user_id=request.subject.external_user_id,
        session_key=request.subject.session_key,
        responder_install_id=request.subject.responder_install_id or None,
        exclude_event_id=request.exclude_event_id,
    )
    return ConversationMemoryContext(
        prior_messages=list(payload.get("prior_messages") or []),
        rolled_summary=str(payload.get("summary_text") or "").strip() or None,
        business_plan=str(payload.get("business_plan") or "").strip() or None,
        compacted=bool(payload.get("compacted")),
        diagnostics={
            "enabled": bool(payload.get("enabled")),
            "applied": bool(payload.get("applied")),
            "summary_present": bool(payload.get("summary_present")),
            "message_count": int(payload.get("message_count") or 0),
        },
    )


def persist_interaction(
    subject: ConversationMemorySubject,
    policy_profile: str | MemoryPolicyProfile,
    *,
    user_message: str,
    assistant_reply: str,
    metadata: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
    request = ConversationMemoryPersistRequest(
        subject=subject,
        policy_profile=policy_profile,
        user_message=user_message,
        assistant_reply=assistant_reply,
        metadata=dict(metadata or {}),
    )
    surface_kind = str(request.subject.surface_kind or "").strip().lower()
    if surface_kind == DIRECT_CHAT_SURFACE:
        _persist_direct_chat_interaction(request)
        return {"surface_kind": DIRECT_CHAT_SURFACE, "persisted": True}
    if surface_kind == DURABLE_RUN_SURFACE:
        return {"surface_kind": DURABLE_RUN_SURFACE, "persisted": False}
    raise ValueError(f"Use apersist_interaction() for async memory surface: {surface_kind}")


async def apersist_interaction(
    subject: ConversationMemorySubject,
    policy_profile: str | MemoryPolicyProfile,
    *,
    user_message: str,
    assistant_reply: str,
    metadata: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
    request = ConversationMemoryPersistRequest(
        subject=subject,
        policy_profile=policy_profile,
        user_message=user_message,
        assistant_reply=assistant_reply,
        metadata=dict(metadata or {}),
    )
    surface_kind = str(request.subject.surface_kind or "").strip().lower()
    if surface_kind != DEPLOYED_AGENT_CHANNEL_SURFACE:
        return persist_interaction(
            request.subject,
            request.policy_profile,
            user_message=request.user_message,
            assistant_reply=request.assistant_reply,
            metadata=request.metadata,
        )
    persisted = await deployed_channel_memory_adapter.persist_deployed_channel_interaction(
        tenant_id=request.subject.tenant_id,
        workspace_id=request.subject.workspace_id,
        deployed_agent=request.metadata.get("deployed_agent") if isinstance(request.metadata.get("deployed_agent"), dict) else None,
        channel_key=request.subject.channel_key,
        external_user_id=request.subject.external_user_id,
        session_key=request.subject.session_key,
        thread_id=request.subject.thread_id,
        responder_install_id=request.subject.responder_install_id or None,
        user_message=request.user_message,
        assistant_reply=request.assistant_reply,
    )
    return persisted or {"surface_kind": DEPLOYED_AGENT_CHANNEL_SURFACE, "persisted": False}


def persist_run_completion(
    subject: ConversationMemorySubject,
    policy_profile: str | MemoryPolicyProfile,
    *,
    run_summary: str,
    metadata: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
    payload = dict(metadata or {})
    run_id = str(payload.get("run_id") or "").strip()
    run = payload.get("run")
    if not run_id or not isinstance(run, dict):
        return None
    semantic_runtime_memory_adapter.persist_run_memory(run_id, run)
    return {"surface_kind": DURABLE_RUN_SURFACE, "persisted": True}


def hydrate_run_memory_context(run_id: str, run: Dict[str, Any]) -> None:
    semantic_runtime_memory_adapter.hydrate_run_memory_context(run_id, run)


def persist_run_memory(run_id: str, run: Dict[str, Any]) -> None:
    semantic_runtime_memory_adapter.persist_run_memory(run_id, run)


def build_workspace_context_text(
    subject: ConversationMemorySubject,
    policy_profile: str | MemoryPolicyProfile,
    *,
    query_text: str = "",
    include_memory_context: bool | None = None,
) -> str:
    context = load_context(
        subject,
        policy_profile,
        query_text=query_text,
        metadata={"include_memory_context": include_memory_context}
        if include_memory_context is not None
        else None,
    )
    if context.stable_contextual_blocks or context.retrieved_contextual_blocks:
        return workspace_context_memory_adapter.render_workspace_context_text(
            context.contextual_blocks,
            stable_blocks=context.stable_contextual_blocks,
            retrieved_blocks=context.retrieved_contextual_blocks,
        )
    return workspace_context_memory_adapter.render_workspace_context_text(context.contextual_blocks)


def delete_subject_memory(subject: ConversationMemorySubject) -> Dict[str, Any]:
    from server_modules import memory_service

    surface_kind = str(subject.surface_kind or "").strip().lower()
    workspace_id = str(subject.workspace_id or "").strip()
    if not workspace_id:
        return {"deleted": False, "surface_kind": surface_kind, "reason": "missing_workspace_id"}

    if surface_kind == DIRECT_CHAT_SURFACE:
        agent_install_id = str(subject.responder_install_id or "").strip() or None
        entries = memory_service.list_memory_entries(workspace_id, agent_install_id=agent_install_id)
        deleted_count = 0
        for entry in entries:
            key = str(entry.get("key") or entry.get("id") or "").strip()
            if key and memory_service.delete_memory(workspace_id, key, agent_install_id=agent_install_id):
                deleted_count += 1
        return {"deleted": deleted_count > 0, "surface_kind": surface_kind, "deleted_count": deleted_count}

    if surface_kind == DURABLE_RUN_SURFACE:
        return {"deleted": False, "surface_kind": surface_kind, "reason": "durable_run_deletion_not_yet_implemented"}

    return {
        "deleted": False,
        "surface_kind": surface_kind,
        "reason": "deletion_requires_surface_specific_workflow",
    }


def _persist_direct_chat_interaction(request: ConversationMemoryPersistRequest) -> None:
    from server_modules import memory_service

    metadata = request.metadata
    persist_memory = bool(metadata.get("persist_memory"))
    persist_transcript = bool(metadata.get("persist_transcript"))
    if persist_memory:
        memory_service.persist_direct_chat_memory_best_effort(
            workspace_id=request.subject.workspace_id,
            provider=metadata.get("provider"),
            model=metadata.get("model"),
            credentials=metadata.get("credentials") if isinstance(metadata.get("credentials"), dict) else None,
            reasoning_effort=str(metadata.get("reasoning_effort") or "medium").strip() or "medium",
            prior_messages=list(metadata.get("prior_messages") or []),
            user_message=request.user_message,
            assistant_reply=request.assistant_reply,
            generate_reply=metadata.get("generate_reply"),
            extraction_prompt=str(metadata.get("extraction_prompt") or "").strip(),
            extraction_system_prompt=str(metadata.get("extraction_system_prompt") or "").strip(),
        )
    if persist_transcript:
        save_session_transcript_fn = metadata.get("save_session_transcript_fn")
        if callable(save_session_transcript_fn):
            save_session_transcript_fn(
                workspace_id=request.subject.workspace_id,
                thread_id=request.subject.thread_id,
                provider=metadata.get("provider"),
                model=metadata.get("model"),
                messages=list(metadata.get("messages") or []),
                user_message=request.user_message,
                assistant_reply=request.assistant_reply,
            )
