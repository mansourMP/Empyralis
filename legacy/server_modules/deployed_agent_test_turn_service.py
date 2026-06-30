from __future__ import annotations

import uuid
from typing import Any, Dict, Optional

from server_modules import (
    billing_service,
    control_plane_repository,
    credit_ledger_contract,
    deployed_agent_config_schema,
    deployed_agent_runtime_contract_service,
    deployed_agent_service,
    deployed_agent_transparency_service,
    empyralis_model_tier_routing_service,
    model_router,
    rust_runtime_kernel_client,
    studio_app_boundary_service,
    transparency_event_store_service,
    usage_accounting_service,
    security_audit_service,
)
from server_modules.schemas import DeployedAgentTestTurnRequest, DeployedAgentTestTurnResponse

ALLOWED_TEST_CHANNELS = {"telegram", "whatsapp", "web_widget", "test"}
ALLOWED_RUNTIME_MODES = {
    "text_agent",
    "cloud_computer_agent",
    "my_computer_agent",
    "self_hosted_agent",
}
TESTABLE_STATES = {"draft", "private_test", "ready_for_review"}
DEFAULT_STUDIO_TEST_PROVIDER = "deepseek"
DEFAULT_STUDIO_TEST_MODEL = "deepseek-v4-pro"
MAX_STUDIO_TEST_HISTORY_MESSAGES = 16
MAX_STUDIO_TEST_HISTORY_CHARS = 12000
MAX_STUDIO_TEST_HISTORY_ITEM_CHARS = 2500
MAX_STUDIO_TEST_CONTEXT_TOKENS = 8000
STUDIO_TEST_OUTPUT_TOKEN_RESERVE = 800


def _coerce_text(value: Any) -> str:
    return str(value or "").strip()


def _estimate_token_count(value: Any) -> int:
    text = _coerce_text(value)
    if not text:
        return 0
    return max(1, (len(text) + 3) // 4)


def _authoritative_runtime_mode(config: Any) -> str:
    configured_mode = _coerce_text(getattr(config, "studio_agent_mode", "")).lower()
    if configured_mode:
        return deployed_agent_runtime_contract_service.resolve_studio_agent_mode(
            configured_mode,
            runtime_placement=getattr(config, "runtime_placement", None),
            runtime_target=getattr(config, "runtime_target", None),
            runtime_supplier=getattr(config, "runtime_supplier", None),
        )
    return deployed_agent_runtime_contract_service.resolve_studio_agent_mode(
        "",
        runtime_placement=getattr(config, "runtime_placement", None),
        runtime_target=getattr(config, "runtime_target", None),
        runtime_supplier=getattr(config, "runtime_supplier", None),
    )


def _enforce_deployed_test_turn_readiness(
    *,
    deployed_agent_id: str,
    workspace_id: str,
    tenant_id: str,
    config: Any,
    deployment_state: str,
) -> Dict[str, Any]:
    try:
        decision = rust_runtime_kernel_client.run_runtime_kernel_enforced(
            "deployed-readiness-decision",
            {
                "stage": "test_turn",
                "workspace_id": workspace_id,
                "tenant_id": tenant_id,
                "deployed_agent_id": deployed_agent_id,
                "studio_agent_mode": _authoritative_runtime_mode(config),
                "current_state": deployment_state,
                "next_state": deployment_state,
                "backing_install_required": False,
            },
        )
        next_action = _coerce_text(decision.get("next_action"))
        if next_action != "execute_studio_test_turn":
            raise ValueError(f"unexpected_next_action:{next_action or 'missing'}")
        return decision
    except rust_runtime_kernel_client.RustKernelDecisionError as exc:
        reason = _coerce_text(getattr(exc, "reason", "")) or "deployed_readiness_denied"
        raise ValueError(f"Rust deployed-readiness blocked test turn: {reason}") from exc


def _build_policy_decisions(
    *,
    config: Any,
    requested_mode: str,
    requested_channel: str,
) -> list[dict]:
    decisions: list[dict] = []

    actual_mode = _authoritative_runtime_mode(config)
    contract = deployed_agent_runtime_contract_service.studio_agent_mode_contract(
        actual_mode,
    )
    runtime_allowed = actual_mode == requested_mode
    decisions.append({
        "policy": "runtime_mode",
        "requested": requested_mode,
        "resolved": actual_mode,
        "configured": actual_mode,
        "allowed": runtime_allowed,
        "reason": "configured_runtime_mode" if runtime_allowed else "runtime_mode_mismatch",
        "placement": contract.get("placement"),
        "supplier": contract.get("supplier"),
        "computer_allowed": contract.get("computer_allowed"),
    })

    channel_config = (getattr(config, "channels", None) or {}).get(requested_channel)
    channel_configured = channel_config is not None and bool(
        getattr(channel_config, "enabled", True)
    )
    decisions.append({
        "policy": "channel",
        "channel": requested_channel,
        "configured": channel_configured,
        "simulated": True,
    })

    tool_policy = getattr(config, "tool_policy", None)
    if tool_policy is not None:
        decisions.append({
            "policy": "tools",
            "allowed_tools": list(getattr(tool_policy, "allowed_tool_ids", []) or []),
            "blocked_tools": list(getattr(tool_policy, "blocked_tool_ids", []) or []),
        })

    safety_policy = getattr(config, "safety_policy", None)
    if safety_policy is not None:
        decisions.append({
            "policy": "safety",
            "approval_mode": getattr(safety_policy, "approval_mode", "balanced"),
            "handoff_mode": getattr(safety_policy, "handoff_mode", "always"),
        })

    memory_policy = getattr(config, "memory_policy", None)
    if memory_policy is not None:
        decisions.append({
            "policy": "memory",
            "enabled": getattr(memory_policy, "memory_enabled", False),
            "context_budget_preset": getattr(memory_policy, "context_budget_preset", "compact"),
            "retention_preset": getattr(memory_policy, "retention_preset", "standard"),
        })

    return decisions


def _evaluate_tool_policy(
    *,
    config: Any,
    message: str,
) -> tuple[list[dict], list[str], bool]:
    tool_policy = getattr(config, "tool_policy", None)
    if tool_policy is None:
        return [], [], False

    allowed_ids = set(getattr(tool_policy, "allowed_tool_ids", []) or [])
    blocked_ids = set(getattr(tool_policy, "blocked_tool_ids", []) or [])

    considered: list[dict] = []
    used: list[str] = []
    approval_required = False

    from server_modules.skill_registry import list_skill_definitions

    all_skills = list_skill_definitions(include_disabled=False)
    lower_message = message.lower()

    for skill in all_skills:
        triggered = any(term and term in lower_message for term in skill.trigger_terms)
        if not triggered:
            continue

        entry = {
            "skill_id": skill.id,
            "label": skill.label,
            "action_class": skill.action_class,
        }

        if skill.id in blocked_ids:
            entry["allowed"] = False
            entry["reason"] = "blocked_by_policy"
        elif allowed_ids and skill.id not in allowed_ids:
            entry["allowed"] = False
            entry["reason"] = "not_in_allowlist"
        elif skill.action_class in ("write", "execute"):
            entry["allowed"] = False
            entry["reason"] = "requires_approval"
            entry["approval_required"] = True
            approval_required = True
        else:
            entry["allowed"] = True
            entry["reason"] = "read_only_safe"
            used.append(skill.id)

        considered.append(entry)

    return considered, used, approval_required


def _load_memory_context_for_test(
    *,
    workspace_id: str,
    tenant_id: str,
    config: Any,
) -> dict:
    memory_policy = getattr(config, "memory_policy", None)
    if memory_policy is None or not getattr(memory_policy, "memory_enabled", False):
        return {"applied": False, "enabled": False, "prior_messages": [], "business_plan": ""}

    try:
        from server_modules.conversation_memory_policy import (
            build_external_channel_memory_profile,
        )

        profile = build_external_channel_memory_profile(
            context_budget_preset=getattr(memory_policy, "context_budget_preset", "compact"),
            retention_preset=getattr(memory_policy, "retention_preset", "standard"),
        )
        return {
            "applied": True,
            "enabled": True,
            "prior_messages": [],
            "business_plan": "",
            "context_budget_preset": profile.name if hasattr(profile, "name") else str(profile),
            "summary_present": False,
            "message_count": 0,
        }
    except Exception:
        return {"applied": False, "enabled": False, "error": "memory_load_failed"}


def _validate_test_customer_profile_boundary(customer_profile: Any) -> None:
    if customer_profile in (None, {}, []):
        return
    try:
        studio_app_boundary_service.assert_no_forbidden_owner_resource_keys(
            customer_profile,
            root_label="customer_profile",
        )
    except studio_app_boundary_service.StudioAppBoundaryError as exc:
        raise ValueError(str(exc)) from exc


def _studio_test_ai_source(config: Any) -> Dict[str, Any]:
    runtime_supply = getattr(config, "runtime_supply", None)
    payload = runtime_supply.get("ai_source") if isinstance(runtime_supply, dict) else None
    return deployed_agent_runtime_contract_service.normalize_studio_ai_source(payload)


def _studio_test_public_tier(config: Any) -> str:
    runtime_supply = getattr(config, "runtime_supply", None)
    payload = runtime_supply if isinstance(runtime_supply, dict) else {}
    model_tier = payload.get("model_tier") if isinstance(payload.get("model_tier"), dict) else {}
    return _coerce_text(model_tier.get("public_tier")) or "pro"


def _resolve_studio_test_model_route(config: Any) -> tuple[str, str, Dict[str, Any]]:
    ai_source = _studio_test_ai_source(config)
    source_kind = _coerce_text(ai_source.get("kind"))
    configured_provider = _coerce_text(getattr(config, "provider", "")).lower()
    configured_model = _coerce_text(getattr(config, "model", ""))

    if source_kind == deployed_agent_runtime_contract_service.STUDIO_AI_SOURCE_EMPYRALIS_CREDITS:
        public_tier = _studio_test_public_tier(config)
        tier_route = empyralis_model_tier_routing_service.resolve_backend_provider_model_for_tier(
            public_tier,
        )
        provider = _coerce_text(tier_route.get("provider")) or DEFAULT_STUDIO_TEST_PROVIDER
        model = _coerce_text(tier_route.get("model")) or DEFAULT_STUDIO_TEST_MODEL
        return provider, model, {
            **ai_source,
            "public_tier": tier_route.get("public_tier") or public_tier,
            "model_tier_route": tier_route,
        }

    if source_kind == deployed_agent_runtime_contract_service.STUDIO_AI_SOURCE_WORKSPACE_API_KEY:
        if not configured_provider or not configured_model:
            raise RuntimeError("Workspace-key Studio test turns require a stored provider and model.")
        return configured_provider, configured_model, ai_source

    raise RuntimeError(
        "Studio private test chat currently requires Empyralis credits or a workspace API key. "
        f"Configured source: {source_kind or 'unknown'}."
    )


def _candidate_recent_test_messages(value: Any) -> list[dict]:
    if not isinstance(value, list):
        return []
    candidates: list[dict] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        role = _coerce_text(item.get("role")).lower()
        if role == "agent":
            role = "assistant"
        if role not in {"user", "assistant"}:
            continue
        content = _coerce_text(item.get("content"))
        if not content:
            continue
        candidates.append({"role": role, "content": content})
    return candidates


def _normalize_recent_test_messages(
    value: Any,
    *,
    token_budget: Optional[int] = None,
) -> tuple[list[dict], dict]:
    candidates = _candidate_recent_test_messages(value)
    normalized: list[dict] = []
    recent_window = candidates[-MAX_STUDIO_TEST_HISTORY_MESSAGES:]
    remaining_chars = MAX_STUDIO_TEST_HISTORY_CHARS
    remaining_tokens = max(0, int(token_budget)) if token_budget is not None else None
    context_truncated = len(candidates) > len(recent_window)
    received_chars = sum(len(item["content"]) for item in candidates)

    for item in reversed(recent_window):
        if remaining_tokens is not None and remaining_tokens <= 0:
            context_truncated = True
            break
        role = item["role"]
        content = item["content"]
        char_limit = min(len(content), MAX_STUDIO_TEST_HISTORY_ITEM_CHARS, remaining_chars)
        if remaining_tokens is not None:
            char_limit = min(char_limit, remaining_tokens * 4)
        clipped = content[:char_limit]
        if not clipped:
            context_truncated = True
            break
        if len(clipped) < len(content):
            context_truncated = True
        estimated_tokens = _estimate_token_count(clipped)
        if remaining_tokens is not None and estimated_tokens > remaining_tokens:
            clipped = clipped[: max(0, remaining_tokens * 4)]
            estimated_tokens = _estimate_token_count(clipped)
            context_truncated = True
        if not clipped:
            break
        normalized.insert(0, {"role": role, "content": clipped})
        remaining_chars -= len(clipped)
        if remaining_tokens is not None:
            remaining_tokens -= estimated_tokens
        if remaining_chars <= 0:
            context_truncated = True
            break

    included_chars = sum(len(item["content"]) for item in normalized)
    return normalized, {
        "recent_messages_received": len(candidates),
        "recent_messages_included": len(normalized),
        "max_history_messages": MAX_STUDIO_TEST_HISTORY_MESSAGES,
        "history_chars_received": received_chars,
        "history_chars_included": included_chars,
        "history_token_budget": token_budget,
        "context_truncated": context_truncated,
    }


def _studio_test_system_prompt(config: Any, agent_record: Dict[str, Any]) -> tuple[str, dict]:
    persona = (
        _coerce_text(getattr(config, "persona", ""))
        or _coerce_text(agent_record.get("persona"))
    )
    instructions = (
        _coerce_text(getattr(config, "instructions", ""))
        or _coerce_text(agent_record.get("instructions"))
    )
    system_prompt = (
        _coerce_text(getattr(config, "system_prompt", ""))
        or _coerce_text(agent_record.get("system_prompt"))
    )
    system_parts = [
        "You are the configured Studio assistant being checked in a private test chat.",
        "Answer the user directly and concisely.",
        "Do not identify yourself by an internal Studio record name unless that name is explicitly present in the stored public-facing instructions.",
        "Do not claim that external tools, channel sends, purchases, file changes, or computer actions were executed.",
        "If the user asks for an action that requires tools or approval, explain that the private test can only preview the response.",
    ]
    if persona:
        system_parts.append(f"Configured persona:\n{persona}")
    if instructions:
        system_parts.append(f"Stored instructions:\n{instructions}")
    if system_prompt:
        system_parts.append(f"Stored system prompt:\n{system_prompt}")

    prompt_source = "stored_instructions" if persona or instructions or system_prompt else "test_guardrails_only"
    return "\n\n".join(system_parts), {
        "prompt_source": prompt_source,
        "persona_applied": bool(persona),
        "stored_instructions_applied": bool(instructions or system_prompt),
        "system_prompt_applied": bool(system_prompt),
        "instructions_applied": bool(instructions),
        "internal_record_name_applied": False,
    }


def _build_live_test_messages(
    *,
    config: Any,
    agent_record: Dict[str, Any],
    message: str,
    recent_messages: Optional[list[dict]] = None,
) -> tuple[list[dict], dict]:
    system_prompt, prompt_context = _studio_test_system_prompt(config, agent_record)
    reserved_tokens = _estimate_token_count(system_prompt) + _estimate_token_count(message) + STUDIO_TEST_OUTPUT_TOKEN_RESERVE
    history_token_budget = max(0, MAX_STUDIO_TEST_CONTEXT_TOKENS - reserved_tokens)
    normalized_recent_messages, history_context = _normalize_recent_test_messages(
        recent_messages,
        token_budget=history_token_budget,
    )
    estimated_input_tokens = (
        _estimate_token_count(system_prompt)
        + _estimate_token_count(message)
        + sum(_estimate_token_count(item.get("content")) for item in normalized_recent_messages)
    )
    prompt_context = {
        **prompt_context,
        **history_context,
        "max_context_tokens": MAX_STUDIO_TEST_CONTEXT_TOKENS,
        "output_token_reserve": STUDIO_TEST_OUTPUT_TOKEN_RESERVE,
        "estimated_input_tokens": estimated_input_tokens,
    }
    return [
        {"role": "system", "content": system_prompt},
        *normalized_recent_messages,
        {"role": "user", "content": message},
    ], prompt_context


def _safe_int(value: Any) -> int:
    try:
        return max(0, int(value or 0))
    except Exception:
        return 0


async def _persist_studio_test_turn_usage(
    *,
    tenant_id: str,
    workspace_id: str,
    deployed_agent_id: str,
    actor_user_id: str,
    trace_id: str,
    provider: str,
    model: str,
    usage: Dict[str, Any],
    ai_source: Dict[str, Any],
    prompt_context: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    if not usage:
        raise RuntimeError("Studio test AI usage is missing for platform credit accounting.")

    payer = _coerce_text(ai_source.get("payer")) or "platform_credits"
    usage_accounting = usage.get("usage_accounting") if isinstance(usage.get("usage_accounting"), dict) else {}
    usage_source = usage_accounting if usage_accounting else usage
    row = usage_accounting_service.build_usage_accounting_record(
        run_id=trace_id,
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        deployed_agent_id=deployed_agent_id,
        surface="studio",
        source_surface="studio_test_turn",
        requested_provider=provider,
        effective_provider=usage_source.get("effective_provider") or usage.get("provider") or provider,
        requested_model=model,
        effective_model=usage_source.get("effective_model") or usage.get("model") or model,
        input_tokens=_safe_int(usage_source.get("input_tokens") or usage.get("prompt_tokens")),
        output_tokens=_safe_int(usage_source.get("output_tokens") or usage.get("completion_tokens")),
        total_tokens=_safe_int(usage_source.get("total_tokens") or usage.get("total_tokens")),
        cached_input_tokens=_safe_int(usage_source.get("cached_input_tokens") or usage.get("cached_input_tokens")),
        cache_write_tokens=_safe_int(usage_source.get("cache_write_tokens") or usage.get("cache_write_tokens")),
        cache_read_tokens=_safe_int(usage_source.get("cache_read_tokens") or usage.get("cache_read_tokens")),
        visible_output_tokens=_safe_int(
            usage_source.get("visible_output_tokens")
            or usage_source.get("output_tokens")
            or usage.get("visible_output_tokens")
            or usage.get("completion_tokens")
        ),
        reasoning_tokens=_safe_int(usage_source.get("reasoning_tokens") or usage.get("reasoning_tokens")),
        thinking_tokens=_safe_int(usage_source.get("thinking_tokens") or usage.get("thinking_tokens")),
        provider_cost_usd=usage_source.get("provider_cost_usd") or usage.get("provider_cost_usd"),
        retail_credits_charged=usage_source.get("retail_credits_charged") or usage.get("retail_credits_charged"),
        payer=payer,
        user_id=actor_user_id,
        agent_id=deployed_agent_id,
        thread_id=f"studio-test:{deployed_agent_id}",
        estimation_mode=_coerce_text(usage_source.get("estimation_mode") or usage.get("estimation_mode")) or "provider_reported",
        metadata={
            **(usage_source.get("metadata") if isinstance(usage_source.get("metadata"), dict) else {}),
            "credential_plane": "platform_runtime" if usage_accounting_service.is_platform_paid_usage_value(payer) else "workspace_api_key",
            "request_id": trace_id,
            "deployed_agent_id": deployed_agent_id,
            "ai_source_kind": ai_source.get("kind"),
            "prompt_context": prompt_context or {},
        },
        require_known_pricing=usage_accounting_service.is_platform_paid_usage_value(payer),
    )
    if usage_accounting_service.is_platform_paid_usage_value(payer):
        validation_error = usage_accounting_service.platform_paid_usage_validation_error(row)
        if validation_error:
            raise RuntimeError(
                "Studio test AI usage is not exact enough for platform credit accounting "
                f"({validation_error})."
            )

    line_item_metadata = credit_ledger_contract.build_credit_ledger_line_item(
        metadata={
            **(row.get("metadata") if isinstance(row.get("metadata"), dict) else {}),
            "surface": "studio",
            "source_surface": "studio_test_turn",
            "billing_source": "empyralis_credits" if usage_accounting_service.is_platform_paid_usage_value(payer) else payer,
        },
        total_tokens=row.get("total_tokens"),
    )
    unified_ledger_event = credit_ledger_contract.build_unified_credit_ledger_event(
        surface="studio",
        source_surface="studio_test_turn",
        payer="platform_credits" if usage_accounting_service.is_platform_paid_usage_value(payer) else payer,
        credit_type=line_item_metadata.get("credit_type") or "ai_tokens",
        provider=row.get("effective_provider") or row.get("provider"),
        model=row.get("effective_model") or row.get("model"),
        runtime_target="cloud_default",
        workspace_id=workspace_id,
        user_id=actor_user_id,
        thread_id=f"studio-test:{deployed_agent_id}",
        run_id=trace_id,
        agent_id=deployed_agent_id,
        provider_usage=row,
        platform_cost_usd=row.get("estimated_cost_usd") if usage_accounting_service.is_platform_paid_usage_value(payer) else 0,
        provider_reported_cost=row.get("provider_cost_usd") or row.get("estimated_cost_usd"),
        provider_reported_currency="USD" if row.get("estimated_cost_usd") is not None else None,
        credits_debited=row.get("retail_credits_charged") if usage_accounting_service.is_platform_paid_usage_value(payer) else 0,
        estimation_mode=row.get("estimation_mode"),
        created_at=row.get("completed_at") or row.get("created_at"),
    )

    if not usage_accounting_service.is_platform_paid_usage_value(payer):
        await control_plane_repository.record_credit_ledger_event(
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            event=unified_ledger_event,
            source_table="studio_test_turn",
            source_event_id=trace_id,
        )
        return row

    ledger_entry = await control_plane_repository.record_workspace_hosted_ai_monthly_cost_ledger_entry(
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        request_id=trace_id,
        thread_id=f"studio-test:{deployed_agent_id}",
        source_surface="studio_test_turn",
        provider=row.get("effective_provider") or row.get("provider"),
        model=row.get("effective_model") or row.get("model"),
        prompt_tokens=int(row.get("input_tokens") or 0),
        completion_tokens=int(row.get("output_tokens") or 0),
        total_tokens=int(row.get("total_tokens") or 0),
        estimated_cost_usd=float(row.get("estimated_cost_usd") or 0.0),
        completed_at=row.get("completed_at") or row.get("created_at"),
        metadata={
            **line_item_metadata,
            "request_id": trace_id,
            "surface": "studio",
            "source_surface": "studio_test_turn",
            "deployed_agent_id": deployed_agent_id,
            "prompt_context": prompt_context or {},
            "usage_accounting": row,
            "unified_credit_ledger_event": unified_ledger_event,
        },
    )
    if not isinstance(ledger_entry, dict):
        raise RuntimeError("Studio test AI cost ledger persistence failed.")
    durable_event = await control_plane_repository.record_credit_ledger_event(
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        event=unified_ledger_event,
        source_table="workspace_hosted_ai_monthly_cost_ledger",
        source_event_id=str(ledger_entry.get("id") or trace_id),
    )
    if not isinstance(durable_event, dict):
        raise RuntimeError("Studio test AI unified credit ledger persistence failed.")
    debit_result = billing_service.debit_workspace_credit_balance_for_hosted_usage(
        workspace_id=workspace_id,
        tenant_id=tenant_id,
        request_id=trace_id,
    )
    if not isinstance(debit_result, dict) or not debit_result.get("ok", False):
        raise RuntimeError("Studio test AI credit debit failed.")
    return row


async def _generate_live_test_reply(
    *,
    tenant_id: str,
    workspace_id: str,
    deployed_agent_id: str,
    actor_user_id: str,
    trace_id: str,
    agent_record: Dict[str, Any],
    config: Any,
    message: str,
    recent_messages: Optional[list[dict]] = None,
) -> tuple[str, Dict[str, Any]]:
    provider, model, ai_source = _resolve_studio_test_model_route(config)
    credentials_payload = model_router.resolve_call_credentials(
        provider=provider,
        model=model,
        workspace_id=workspace_id,
    )
    resolved_provider = _coerce_text(credentials_payload.get("provider")) or provider
    resolved_model = _coerce_text(credentials_payload.get("model")) or model
    credentials = credentials_payload.get("credentials")
    if not isinstance(credentials, dict):
        raise RuntimeError(
            "DeepSeek is not configured for Studio test turns. "
            "Set DEEPSEEK_API_KEY or ORION_HOSTED_DEEPSEEK_API_KEY and restart the backend."
        )

    model_messages, prompt_context = _build_live_test_messages(
        config=config,
        agent_record=agent_record,
        message=message,
        recent_messages=recent_messages,
    )
    result = await model_router.call_model(
        model_messages,
        provider=resolved_provider,
        model=resolved_model,
        credentials=credentials,
        max_tokens=600,
        temperature=0.35,
        stream=False,
        source_surface="studio",
    )
    if not isinstance(result, dict):
        raise RuntimeError("Studio test AI returned an unsupported response.")
    reply = _coerce_text(result.get("content"))
    if not reply:
        raise RuntimeError("Studio test AI returned an empty reply.")
    usage = result.get("usage") if isinstance(result.get("usage"), dict) else {}
    usage_row = await _persist_studio_test_turn_usage(
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        deployed_agent_id=deployed_agent_id,
        actor_user_id=actor_user_id,
        trace_id=trace_id,
        provider=resolved_provider,
        model=resolved_model,
        usage=usage,
        ai_source=ai_source,
        prompt_context=prompt_context,
    )
    return reply, {
        "provider": resolved_provider,
        "model": resolved_model,
        "usage": usage_row,
        "ai_source": ai_source,
        "prompt_context": prompt_context,
    }


async def execute_test_turn(
    *,
    deployed_agent_id: str,
    workspace_id: str,
    tenant_id: str,
    request: DeployedAgentTestTurnRequest,
    current_user: dict,
) -> DeployedAgentTestTurnResponse:
    trace_id = str(uuid.uuid4())
    actor_user_id = _coerce_text((current_user or {}).get("user_id"))
    actor_email = _coerce_text((current_user or {}).get("email"))

    normalized_message = _coerce_text(request.message)
    normalized_channel = _coerce_text(request.channel).lower() or "test"
    normalized_mode = _coerce_text(request.runtime_mode).lower() or "text_agent"

    if not normalized_message:
        raise ValueError("message must not be empty")
    if normalized_channel not in ALLOWED_TEST_CHANNELS:
        raise ValueError(f"Unsupported channel: {request.channel}. Allowed: {sorted(ALLOWED_TEST_CHANNELS)}")
    if normalized_mode not in ALLOWED_RUNTIME_MODES:
        raise ValueError(f"Unsupported runtime_mode: {request.runtime_mode}. Allowed: {sorted(ALLOWED_RUNTIME_MODES)}")
    _validate_test_customer_profile_boundary(request.customer_profile)

    agent_detail = await deployed_agent_service.get_deployed_agent_detail(
        deployed_agent_id=deployed_agent_id,
        current_user=current_user,
        owner_workspace_id=workspace_id,
    )
    if not isinstance(agent_detail, dict):
        raise ValueError("Deployed agent not found.")

    agent_record = agent_detail.get("deployed_agent") or agent_detail
    config = deployed_agent_config_schema.deployed_agent_config_from_record(agent_record)

    deployment_state = _coerce_text(agent_record.get("deployment_state") or config.deployment_state)
    deployed_agent_service._enforce_deployed_agent_service_decision(
        "test_turn",
        deployed_agent=agent_record,
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        current_user=current_user,
    )
    _enforce_deployed_test_turn_readiness(
        deployed_agent_id=deployed_agent_id,
        workspace_id=workspace_id,
        tenant_id=tenant_id,
        config=config,
        deployment_state=deployment_state,
    )
    if deployment_state not in TESTABLE_STATES:
        raise ValueError(
            f"Agent state '{deployment_state}' does not allow test turns. "
            f"Test turns require: {', '.join(sorted(TESTABLE_STATES))}."
        )

    policy_decisions = _build_policy_decisions(
        config=config,
        requested_mode=normalized_mode,
        requested_channel=normalized_channel,
    )
    runtime_decision = next(
        (item for item in policy_decisions if item.get("policy") == "runtime_mode"),
        {},
    )
    effective_runtime_mode = _coerce_text(runtime_decision.get("resolved")) or normalized_mode
    runtime_mode_allowed = bool(runtime_decision.get("allowed", False))

    tools_considered, tools_used, approval_required = _evaluate_tool_policy(
        config=config,
        message=normalized_message,
    )

    memory_context = _load_memory_context_for_test(
        workspace_id=workspace_id,
        tenant_id=tenant_id,
        config=config,
    )

    model_metadata: Dict[str, Any] = {}
    if runtime_mode_allowed and not approval_required:
        try:
            reply, model_metadata = await _generate_live_test_reply(
                tenant_id=tenant_id,
                workspace_id=workspace_id,
                deployed_agent_id=deployed_agent_id,
                actor_user_id=actor_user_id,
                trace_id=trace_id,
                agent_record=agent_record,
                config=config,
                message=normalized_message,
                recent_messages=request.recent_messages,
            )
            policy_decisions.append({
                "policy": "ai_model",
                "provider": model_metadata.get("provider"),
                "model": model_metadata.get("model"),
                "source": (model_metadata.get("ai_source") or {}).get("kind"),
                "allowed": True,
                "reason": "live_studio_test",
            })
        except Exception as exc:
            raise ValueError(f"Studio live test failed: {exc}") from exc
    else:
        reply = _simulate_reply(
            message=normalized_message,
            channel=normalized_channel,
            mode=effective_runtime_mode,
            approval_required=approval_required,
            runtime_mode_allowed=runtime_mode_allowed,
            requested_mode=normalized_mode,
        )

    audit_events = _emit_test_turn_audit(
        trace_id=trace_id,
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        deployed_agent_id=deployed_agent_id,
        actor_user_id=actor_user_id,
        actor_email=actor_email,
        channel=normalized_channel,
        mode=effective_runtime_mode,
        approval_required=approval_required,
        tools_used=tools_used,
    )

    test_result = {
        "reply": reply,
        "policy_decisions": policy_decisions,
        "tools_considered": tools_considered,
        "tools_used": tools_used,
        "memory_context": memory_context,
        "prompt_context": model_metadata.get("prompt_context") if isinstance(model_metadata.get("prompt_context"), dict) else {},
        "approval_required": approval_required,
        "audit_events": audit_events,
        "trace_id": trace_id,
        "model_provider": model_metadata.get("provider"),
        "model": model_metadata.get("model"),
        "usage": model_metadata.get("usage"),
    }
    try:
        events = deployed_agent_transparency_service.emit_deployed_agent_test_turn_events(
            trace_id=trace_id,
            workspace_id=workspace_id,
            deployed_agent_id=deployed_agent_id,
            user_message=normalized_message,
            test_result=test_result,
        )
        transparency_payloads = [e.to_user_payload() for e in events]
    except Exception:
        transparency_payloads = []

    if transparency_payloads:
        try:
            await transparency_event_store_service.persist_transparency_events(
                trace_id=trace_id,
                tenant_id=tenant_id,
                workspace_id=workspace_id,
                events=transparency_payloads,
                surface="studio_test",
            )
        except Exception:
            pass

    try:
        from server_modules import agent_completion_notification_service

        await agent_completion_notification_service.notify_completion(
            workspace_id=workspace_id,
            agent_name=_coerce_text(agent_record.get("name") or agent_record.get("label") or deployed_agent_id) or "Agent",
            result_summary=reply,
            artifact_url=None,
        )
    except Exception:
        pass

    return DeployedAgentTestTurnResponse(
        reply=reply,
        policy_decisions=policy_decisions,
        tools_considered=tools_considered,
        tools_used=tools_used,
        memory_context=memory_context,
        prompt_context=model_metadata.get("prompt_context") if isinstance(model_metadata.get("prompt_context"), dict) else {},
        approval_required=approval_required,
        audit_events=audit_events,
        trace_id=trace_id,
        transparency_events=transparency_payloads,
        model_provider=_coerce_text(model_metadata.get("provider")) or None,
        model=_coerce_text(model_metadata.get("model")) or None,
        usage=model_metadata.get("usage") if isinstance(model_metadata.get("usage"), dict) else None,
    )


def _simulate_reply(
    *,
    message: str,
    channel: str,
    mode: str,
    approval_required: bool,
    runtime_mode_allowed: bool,
    requested_mode: str,
) -> str:
    if not runtime_mode_allowed:
        return (
            f"[TEST MODE] The requested runtime mode is not allowed for this agent:\n"
            f"'{message[:200]}'\n\n"
            f"Channel: {channel} | Configured mode: {mode} | Requested mode: {requested_mode}\n"
            f"Status: runtime_mode_mismatch\n"
            f"This is a test turn — no real actions were executed."
        )
    if approval_required:
        return (
            f"[TEST MODE] Your request triggers actions that require approval:\n"
            f"'{message[:200]}'\n\n"
            f"Channel: {channel} | Mode: {mode}\n"
            f"Status: approval_required=true\n"
            f"This is a test turn — no real actions were executed."
        )
    return (
        f"[TEST MODE] Simulated reply for:\n"
        f"'{message[:200]}'\n\n"
        f"Channel: {channel} | Mode: {mode}\n"
        f"Status: safe — all triggered tools are read-only.\n"
        f"This is a test turn — no real actions were executed."
    )


def _emit_test_turn_audit(
    *,
    trace_id: str,
    tenant_id: str,
    workspace_id: str,
    deployed_agent_id: str,
    actor_user_id: str,
    actor_email: str,
    channel: str,
    mode: str,
    approval_required: bool,
    tools_used: list[str],
) -> list[dict]:
    events: list[dict] = []

    status = "approval_required" if approval_required else "success"
    detail = (
        f"Test turn for agent {deployed_agent_id} via {channel}/{mode}"
    )

    try:
        security_audit_service.emit_security_audit_event(
            action="deployed_agent.test_turn",
            status=status,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            actor_user_id=actor_user_id or None,
            actor_email=actor_email or None,
            trace_id=trace_id,
            detail=detail,
            metadata={
                "deployed_agent_id": deployed_agent_id,
                "channel": channel,
                "runtime_mode": mode,
                "approval_required": approval_required,
                "tools_used": tools_used,
            },
            idempotency_key=f"test_turn:{trace_id}",
        )
        events.append({
            "action": "deployed_agent.test_turn",
            "status": status,
            "trace_id": trace_id,
        })
    except Exception:
        events.append({
            "action": "deployed_agent.test_turn",
            "status": "audit_emission_failed",
            "trace_id": trace_id,
        })

    return events
