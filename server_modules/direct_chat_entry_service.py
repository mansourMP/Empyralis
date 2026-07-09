from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional

from server_modules import (
    empyralis_model_tier_contract,
    empyralis_model_tier_routing_service,
    entitlements_service,
    provider_profiles,
)
from server_modules import session_transcript_store
from server_modules import tool_registry_service
from server_modules.conversation_memory_policy import (
    DIRECT_CHAT_PROFILE,
    build_model_aware_memory_policy,
    get_memory_policy_profile,
)


_REAL_MACHINE_LOCAL_TOOL_PREFIXES = ("screenshot__", "computer__")
_HARDWARE_ACTION_TOOL_NAME = "hardware__action"


def _direct_tool_name(tool: Any) -> str:
    if not isinstance(tool, dict):
        return ""
    function = tool.get("function")
    if isinstance(function, dict):
        return str(function.get("name") or "").strip()
    return str(tool.get("name") or "").strip()


def _with_tool_description(tool: Dict[str, Any], description: str) -> Dict[str, Any]:
    next_tool = dict(tool)
    function = next_tool.get("function")
    if isinstance(function, dict):
        next_function = dict(function)
        next_function["description"] = description
        next_tool["function"] = next_function
    elif "description" in next_tool:
        next_tool["description"] = description
    return next_tool


def _verified_user_device_gateway_context(workspace_id: str) -> Optional[Dict[str, Any]]:
    clean_workspace_id = str(workspace_id or "").strip()
    if not clean_workspace_id:
        return None
    try:
        from server_modules import gateway_registry_service, gateway_state_repository
    except Exception:
        return None
    try:
        registrations = gateway_state_repository.list_workspace_gateway_registrations(
            clean_workspace_id,
            include_revoked=False,
        )
    except Exception:
        return None
    for registration in registrations:
        if not isinstance(registration, dict):
            continue
        try:
            public_payload = gateway_registry_service.gateway_registration_public_payload(registration)
        except Exception:
            public_payload = dict(registration)
        status = str(public_payload.get("status") or registration.get("status") or "").strip().lower()
        trust_state = str(
            public_payload.get("device_trust_state") or registration.get("device_trust_state") or ""
        ).strip().lower()
        connection_status = str(public_payload.get("connection_status") or "").strip().lower()
        latest_session_status = str(public_payload.get("latest_session_status") or "").strip().lower()
        if status != "active":
            continue
        if trust_state != "verified":
            continue
        if connection_status != "online":
            continue
        if latest_session_status and latest_session_status not in {"active", "connected"}:
            continue
        if public_payload.get("heartbeat_fresh") is not True:
            continue
        gateway_id = str(public_payload.get("gateway_id") or registration.get("gateway_id") or "").strip()
        if not gateway_id:
            continue
        return {
            "gateway_id": gateway_id,
            "device_name": str(public_payload.get("display_name") or registration.get("display_name") or "").strip()
            or "Agent Computer",
            "runtime_target": "user_device_gateway",
            "session_verified": True,
        }
    return None


def _tools_for_verified_user_device(
    tools: List[Dict[str, Any]],
    gateway_context: Optional[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    if not gateway_context:
        return tools
    device_name = str(gateway_context.get("device_name") or "the user's Agent Computer").strip()
    prepared: List[Dict[str, Any]] = []
    for tool in tools:
        name = _direct_tool_name(tool)
        if any(name.startswith(prefix) for prefix in _REAL_MACHINE_LOCAL_TOOL_PREFIXES):
            continue
        if name == _HARDWARE_ACTION_TOOL_NAME and isinstance(tool, dict):
            prepared.append(
                _with_tool_description(
                    tool,
                    (
                        "Run a hardware action on the user's verified Agent Computer. "
                        f"The connected device is {device_name}. Use this for screenshots, "
                        "screen inspection, keyboard, mouse, app/window control, and other "
                        "real-machine computer-control tasks. Use runtime_target "
                        "user_device_gateway."
                    ),
                )
            )
            continue
        prepared.append(tool)
    return prepared


@dataclass(slots=True)
class PreparedDirectChatRequest:
    normalized_message: str
    normalized_workspace_id: str
    normalized_thread_id: str
    session_key: str
    normalized_requested_provider: str
    normalized_requested_model: str
    normalized_reasoning_effort: Optional[str]
    compaction: Dict[str, Any]
    compacted_prior_messages: List[Dict[str, Any]]
    proactive_suggestions: List[str]
    tool_loop_session_key: str
    availability_payload: Dict[str, Any]
    connected_systems: List[str]
    tool_capabilities: List[Dict[str, Any]]
    tools: List[Dict[str, Any]]
    tool_registry: List[Any]  # List[tool_registry_service.RegistryEntry]
    approved_action_payload: Optional[Dict[str, str]]
    base_context_used: Dict[str, Any]
    slash_command_name: str
    slash_remainder: str
    resolved_chat_max_iterations: int


def _highest_allowed_empyralis_tier(
    tier_policy: Dict[str, Any],
) -> Optional[str]:
    tiers = tier_policy.get("tiers") if isinstance(tier_policy.get("tiers"), dict) else {}
    for candidate in ("max", "pro", "light"):
        record = tiers.get(candidate) if isinstance(tiers, dict) else None
        if isinstance(record, dict) and bool(record.get("enabled")):
            return candidate
    return None


def prepare_direct_chat_request(
    *,
    resolved_turn_request: Optional[Any],
    session_ctx: Optional[Dict[str, Any]],
    message: str,
    workspace_id: str,
    thread_id: str,
    requested_model: str,
    requested_provider: str,
    prior_messages: Optional[List[Dict[str, Any]]],
    reasoning_effort: str,
    availability: Optional[Dict[str, Any]],
    approved_action: Optional[Dict[str, Any]],
    max_iterations: Optional[int],
    direct_chat_session_key_fn: Callable[[str, str], str],
    resolved_chat_iteration_limit_fn: Callable[[Any], int],
    session_model_preference_fn: Callable[[str], Dict[str, Optional[str]]],
    normalize_reasoning_effort_fn: Callable[[str], Optional[str]],
    parse_slash_command_fn: Callable[[str], Dict[str, str]],
    set_session_model_preference_fn: Callable[..., None],
    mark_thread_cleared_fn: Callable[[str], None],
    normalize_prior_messages_fn: Callable[[Optional[List[Dict[str, Any]]]], List[Dict[str, Any]]],
    consume_thread_cleared_fn: Callable[[str], bool],
    compact_conversation_history_fn: Callable[..., Dict[str, Any]],
    build_proactive_suggestions_fn: Callable[[str], List[str]],
    direct_tool_session_key_fn: Callable[[str, str], str],
    resolve_direct_chat_availability_fn: Callable[[str, str, Optional[Dict[str, Any]]], Dict[str, Any]],
    connected_system_labels_fn: Callable[[Dict[str, Any]], List[str]],
    context_tool_capabilities_fn: Callable[[Dict[str, Any]], List[Dict[str, Any]]],
    build_direct_chat_tools_fn: Callable[[List[Dict[str, Any]]], List[Dict[str, Any]]],
    build_local_direct_chat_tools_fn: Callable[[Dict[str, Any]], List[Dict[str, Any]]],
    build_builtin_direct_chat_tools_fn: Callable[[], List[Dict[str, Any]]],
    build_always_on_direct_chat_tools_fn: Callable[[], List[Dict[str, Any]]],
    build_registry_entries_fn: Callable[..., List[Any]],
    normalize_direct_approved_action_fn: Callable[[Any], Optional[Dict[str, str]]],
    build_context_used_fn: Callable[..., Dict[str, Any]],
    direct_chat_compaction_token_limit: int,
) -> PreparedDirectChatRequest:
    resolved_turn_metadata = (
        resolved_turn_request.context_hints.get("metadata")
        if resolved_turn_request is not None and isinstance(getattr(resolved_turn_request, "context_hints", None), dict)
        and isinstance(resolved_turn_request.context_hints.get("metadata"), dict)
        else {}
    )
    normalized_message = (
        str(resolved_turn_request.message or "").strip()
        if resolved_turn_request is not None
        else str(message or "").strip()
    )
    normalized_workspace_id = (
        str(resolved_turn_request.workspace_id or "default").strip() or "default"
        if resolved_turn_request is not None
        else str(workspace_id or "default").strip() or "default"
    )
    normalized_thread_id = (
        str(resolved_turn_request.session_id or "").strip()
        if resolved_turn_request is not None
        else str(thread_id or "").strip()
    )
    session_key = direct_chat_session_key_fn(normalized_workspace_id, normalized_thread_id)
    normalized_requested_provider = str(requested_provider or "").strip().lower()
    normalized_requested_model = str(requested_model or "").strip()
    resolved_chat_max_iterations = resolved_chat_iteration_limit_fn(max_iterations)

    session_model_preference = session_model_preference_fn(session_key)
    if session_model_preference.get("provider"):
        normalized_requested_provider = str(session_model_preference.get("provider") or "").strip().lower()
    if session_model_preference.get("model"):
        normalized_requested_model = str(session_model_preference.get("model") or "").strip()

    normalized_reasoning_effort = normalize_reasoning_effort_fn(reasoning_effort)
    slash_command = parse_slash_command_fn(normalized_message)
    slash_command_name = str(slash_command.get("command") or "").strip().lower()
    slash_remainder = str(slash_command.get("remainder") or "").strip()
    if slash_command_name == "model":
        model_parts = slash_remainder.split(None, 1) if slash_remainder else []
        selected_model_token = str(model_parts[0] or "").strip() if model_parts else ""
        trailing_content = str(model_parts[1] or "").strip() if len(model_parts) > 1 else ""
        selected_provider = normalized_requested_provider or None
        selected_model = selected_model_token
        if ":" in selected_model_token:
            provider_token, model_token = selected_model_token.split(":", 1)
            selected_provider = str(provider_token or "").strip().lower() or selected_provider
            selected_model = str(model_token or "").strip()
        if selected_provider:
            normalized_requested_provider = selected_provider
        if selected_model:
            normalized_requested_model = selected_model
        if selected_provider or selected_model:
            set_session_model_preference_fn(
                session_key,
                provider=normalized_requested_provider or None,
                model=normalized_requested_model or None,
            )
        if trailing_content:
            normalized_message = trailing_content
            slash_command_name = ""
            slash_remainder = ""
    elif slash_command_name == "clear" and slash_remainder:
        mark_thread_cleared_fn(session_key)
        normalized_message = slash_remainder
        slash_command_name = ""
        slash_remainder = ""

    migrated_public_tier = empyralis_model_tier_routing_service.infer_migrated_public_tier_from_legacy_selection(
        requested_provider=normalized_requested_provider,
        requested_model=normalized_requested_model,
        metadata=resolved_turn_metadata,
    )
    empyralis_tier_route = empyralis_model_tier_routing_service.resolve_requested_empyralis_tier(
        requested_provider=normalized_requested_provider,
        requested_model=normalized_requested_model,
        metadata={
            **resolved_turn_metadata,
            **(
                {"public_tier": migrated_public_tier}
                if migrated_public_tier
                else {}
            ),
        },
    )
    hosted_empyralis_tier = ""
    if empyralis_tier_route:
        requested_public_tier = empyralis_model_tier_contract.normalize_model_tier(
            empyralis_tier_route.get("public_tier") or normalized_requested_model or "pro",
            fallback="pro",
        )
        requested_tier_contract = empyralis_model_tier_contract.model_tier_contract(requested_public_tier)
        if requested_tier_contract.user_owned:
            if requested_public_tier == "local_ai":
                normalized_requested_provider = "ollama"
                normalized_requested_model = (
                    normalized_requested_model
                    or str(provider_profiles.provider_catalog_entry("ollama").get("default_model") or "").strip()
                )
            if requested_public_tier == "my_ai_account" and not normalized_requested_provider:
                normalized_requested_provider = "openai-codex"
            if not normalized_requested_provider and requested_public_tier == "my_api_key":
                normalized_requested_provider = "openai"
            if not normalized_requested_model and normalized_requested_provider:
                normalized_requested_model = str(
                    provider_profiles.provider_catalog_entry(normalized_requested_provider).get("default_model") or ""
                ).strip()
            if not normalized_reasoning_effort:
                normalized_reasoning_effort = normalize_reasoning_effort_fn(
                    str(requested_tier_contract.reasoning_effort or "")
                )
        else:
            hosted_empyralis_tier = requested_public_tier
            normalized_requested_provider = str(empyralis_tier_route.get("provider") or "").strip().lower()
            normalized_requested_model = str(empyralis_tier_route.get("model") or "").strip()
            if not normalized_reasoning_effort:
                normalized_reasoning_effort = normalize_reasoning_effort_fn(
                    str(empyralis_tier_route.get("reasoning_effort") or "")
                )
        try:
            tier_policy = entitlements_service.chat_model_tier_policy_for_workspace_id(
                workspace_id=normalized_workspace_id,
            )
        except Exception:
            tier_policy = {
                "tiers": {
                    "light": {"enabled": True},
                    "pro": {"enabled": True},
                    "max": {"enabled": True},
                }
            }

        requested_tier_record = (
            tier_policy.get("tiers", {}).get(requested_public_tier)
            if isinstance(tier_policy.get("tiers"), dict)
            else None
        )
        requested_tier_enabled = bool(
            isinstance(requested_tier_record, dict)
            and requested_tier_record.get("enabled")
        )
        if (
            requested_public_tier in empyralis_model_tier_contract.EMPYRALIS_HOSTED_TIERS
            and not requested_tier_enabled
        ):
            fallback_tier = _highest_allowed_empyralis_tier(tier_policy)
            if fallback_tier and fallback_tier != requested_public_tier:
                empyralis_tier_route = empyralis_model_tier_routing_service.resolve_backend_provider_model_for_tier(
                    fallback_tier
                )
                normalized_requested_provider = str(empyralis_tier_route.get("provider") or "").strip().lower()
                normalized_requested_model = str(empyralis_tier_route.get("model") or "").strip()
                normalized_reasoning_effort = normalize_reasoning_effort_fn(
                    str(empyralis_tier_route.get("reasoning_effort") or "")
                )

    base_direct_chat_policy = get_memory_policy_profile(DIRECT_CHAT_PROFILE)
    context_window_tokens = provider_profiles.context_window_for_model(
        normalized_requested_provider,
        normalized_requested_model,
    )
    if context_window_tokens is None:
        model_aware_direct_chat_policy = base_direct_chat_policy
    else:
        model_aware_direct_chat_policy = build_model_aware_memory_policy(
            base_direct_chat_policy,
            context_window_tokens=context_window_tokens,
            runtime_lane="direct_chat",
            trust_zone=str(
                (
                    session_ctx.get("meta", {}).get("trust_zone")
                    if isinstance(session_ctx, dict) and isinstance(session_ctx.get("meta"), dict)
                    else ""
                )
                or resolved_turn_metadata.get("trust_zone")
                or "shared_cloud"
            ),
        )

    normalized_prior_messages = normalize_prior_messages_fn(prior_messages)
    if not normalized_prior_messages and normalized_thread_id:
        transcript_prior_messages = session_transcript_store.load_latest_session_transcript_messages(
            workspace_id=normalized_workspace_id,
            thread_id=normalized_thread_id,
            limit=model_aware_direct_chat_policy.max_transcript_items,
        )
        normalized_prior_messages = normalize_prior_messages_fn(transcript_prior_messages)
    if consume_thread_cleared_fn(session_key):
        normalized_prior_messages = []
    compaction = compact_conversation_history_fn(
        normalized_prior_messages,
        max_tokens=model_aware_direct_chat_policy.max_prompt_tokens or direct_chat_compaction_token_limit,
        preserve_last_messages=model_aware_direct_chat_policy.preserve_last_messages,
        recent_message_budget_tokens=model_aware_direct_chat_policy.recent_message_budget_tokens,
        summary_max_chars=model_aware_direct_chat_policy.max_summary_chars,
    )
    compacted_prior_messages = [
        item
        for item in (compaction.get("messages") if isinstance(compaction, dict) else [])
        if isinstance(item, dict)
    ]
    proactive_suggestions = build_proactive_suggestions_fn(normalized_workspace_id) if not normalized_prior_messages else []
    tool_loop_session_key = direct_tool_session_key_fn(normalized_workspace_id, normalized_thread_id)
    availability_override = dict(availability) if isinstance(availability, dict) else {}
    if hosted_empyralis_tier:
        availability_override.setdefault("ai_tier", hosted_empyralis_tier)
        availability_override.setdefault("billing_source", "empyralis_credits")
        availability_override.setdefault("credential_plane", "platform_runtime")
        availability_override.setdefault("force_platform_runtime", True)
    if resolved_turn_request is not None:
        source_token = str(
            resolved_turn_metadata.get("source")
            or resolved_turn_request.context_hints.get("source")
            or ""
        ).strip().lower()
        availability_override.setdefault(
            "surface_channel",
            str(resolved_turn_request.channel or "").strip().lower() or None,
        )
        availability_override.setdefault("source", source_token or None)
        if str(resolved_turn_request.channel or "").strip().lower() == "mobile" or source_token == "mobile_chat":
            availability_override.setdefault("mobile_server_first", True)
    availability_payload = resolve_direct_chat_availability_fn(
        normalized_workspace_id,
        normalized_requested_provider,
        availability_override or None,
    )
    gateway_context = _verified_user_device_gateway_context(normalized_workspace_id)
    if gateway_context and isinstance(availability_payload, dict):
        availability_payload = {
            **availability_payload,
            "verified_user_device_gateway": gateway_context,
        }
    connected_systems = connected_system_labels_fn(availability_payload)
    if gateway_context:
        connected_systems = list(connected_systems)
        connected_systems.append(
            (
                "Agent Computer connected: "
                f"{gateway_context.get('device_name')}. Use hardware__action with "
                "runtime_target user_device_gateway for screenshots and computer-control "
                "tasks on the user's real machine."
            )
        )
    tool_capabilities = context_tool_capabilities_fn(availability_payload)
    # ── Two-tier tool assembly ──
    # Tier 1: always-on tools (8 core tools, ~800 tokens) injected every turn.
    # Tier 2: registry — everything else, loaded on demand via query_tool_registry.
    tools = build_always_on_direct_chat_tools_fn()
    tool_registry = build_registry_entries_fn(
        tool_capabilities,
        availability_payload,
    )
    tools = _tools_for_verified_user_device(tools, gateway_context)
    approved_action_payload = normalize_direct_approved_action_fn(approved_action)
    base_context_used = build_context_used_fn(
        workspace_id=normalized_workspace_id,
        requested_provider=normalized_requested_provider,
        effective_provider=None,
        requested_model=normalized_requested_model,
        effective_model=None,
        reasoning_effort=normalized_reasoning_effort,
        connected_systems=connected_systems,
        tool_capabilities=tool_capabilities,
        prior_messages_used=False,
        history_mode="none",
        run_created=False,
    )
    return PreparedDirectChatRequest(
        normalized_message=normalized_message,
        normalized_workspace_id=normalized_workspace_id,
        normalized_thread_id=normalized_thread_id,
        session_key=session_key,
        normalized_requested_provider=normalized_requested_provider,
        normalized_requested_model=normalized_requested_model,
        normalized_reasoning_effort=normalized_reasoning_effort,
        compaction=compaction if isinstance(compaction, dict) else {},
        compacted_prior_messages=compacted_prior_messages,
        proactive_suggestions=proactive_suggestions,
        tool_loop_session_key=tool_loop_session_key,
        availability_payload=availability_payload,
        connected_systems=connected_systems,
        tool_capabilities=tool_capabilities,
        tools=tools,
        tool_registry=tool_registry,
        approved_action_payload=approved_action_payload,
        base_context_used=base_context_used,
        slash_command_name=slash_command_name,
        slash_remainder=slash_remainder,
        resolved_chat_max_iterations=resolved_chat_max_iterations,
    )
