from __future__ import annotations

from dataclasses import replace
from typing import Any, Dict, Optional

from server_modules.agent_turn import build_inbound_agent_turn_request
from server_modules import deployed_agent_config_schema
from server_modules import external_content_guard
from server_modules.channel_identity_service import (
    build_inbound_identity,
    normalize_channel_key,
    normalize_endpoint_key,
)
from server_modules.channel_preflight_service import assert_agent_allowed, assert_inbound_allowed
from server_modules.channel_routing_models import ChannelExecutionResult, ChannelRoutingContext


def coerce_dict(value: Any) -> Dict[str, Any]:
    return dict(value or {}) if isinstance(value, dict) else {}


def runtime_profile_id(install: Dict[str, Any]) -> Optional[str]:
    runtime_profile = coerce_dict(install.get("runtime_profile"))
    token = str(runtime_profile.get("id") or install.get("runtime_profile_id") or "").strip()
    return token or None


def agent_role_token(*, install: Dict[str, Any], manifest: Any) -> Optional[str]:
    from server_modules.channel_identity_service import safe_slug

    install_metadata = coerce_dict(install.get("metadata"))
    install_agent = coerce_dict(install.get("agent_definition"))
    candidates = (
        install_metadata.get("agent_role"),
        install_agent.get("slug"),
        getattr(getattr(manifest, "identity", None), "role", None),
        install.get("label"),
    )
    for candidate in candidates:
        token = safe_slug(candidate, fallback="").replace("-", "_").strip("_")
        if token:
            return token
    return None


def install_declares_deployed_agent_source(install: Dict[str, Any]) -> bool:
    metadata = coerce_dict(install.get("metadata"))
    return (
        str(metadata.get("source") or "").strip().lower() == "deployed_agent"
        or isinstance(metadata.get("deployed_agent"), dict)
    )


def channel_turn_owner_user(*, install: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "auth_type": "api_key",
        "role": "owner",
        "is_admin": True,
        "user_id": str(install.get("owner_user_id") or "").strip(),
        "email": str(install.get("owner_email") or "").strip().lower(),
    }


def channel_connector_id(*, install: Dict[str, Any], metadata: Dict[str, Any]) -> Optional[str]:
    install_metadata = coerce_dict(install.get("metadata"))
    install_config = coerce_dict(install.get("config"))
    deployed_agent = coerce_dict(install_metadata.get("deployed_agent"))
    candidates = (
        metadata.get("connector_id"),
        metadata.get("source_connector_id"),
        metadata.get("telegram_connector_id"),
        install.get("connector_id"),
        install.get("source_connector_id"),
        install.get("telegram_connector_id"),
        install_metadata.get("connector_id"),
        install_metadata.get("source_connector_id"),
        install_metadata.get("telegram_connector_id"),
        install_config.get("connector_id"),
        install_config.get("telegram_connector_id"),
        deployed_agent.get("connector_id"),
        deployed_agent.get("telegram_connector_id"),
    )
    for candidate in candidates:
        token = str(candidate or "").strip()
        if token:
            return token
    return None


def _normalize_tool_ids(value: Any) -> list[str]:
    items = value if isinstance(value, list) else []
    return [
        str(item or "").strip().lower()
        for item in items
        if str(item or "").strip()
    ]


def _merge_tool_descriptors(existing: Any, additions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in list(existing or []) + additions:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        parameters = item.get("parameters") if isinstance(item.get("parameters"), dict) else None
        if not name or not parameters or name in seen:
            continue
        seen.add(name)
        merged.append(
            {
                "name": name,
                "description": str(item.get("description") or "").strip(),
                "parameters": parameters,
            }
        )
    return merged


def _specialist_tool_descriptors(selected_tool_ids: list[str]) -> list[dict[str, Any]]:
    descriptors: list[dict[str, Any]] = []
    if "web_search" in selected_tool_ids:
        descriptors.append(
            {
                "name": "web__search",
                "description": "Search the web and return the top results with titles, URLs, and snippets.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": "The search query to run.",
                        }
                    },
                    "required": ["query"],
                },
            }
        )
    return descriptors


def _guard_external_channel_message(
    *,
    message: str,
    channel_key: str,
    actor_id: str,
    actor_display_name: str,
    request_id: Optional[str],
    metadata: Dict[str, Any],
) -> tuple[str, Dict[str, Any]]:
    guarded = external_content_guard.wrap_external_content(
        message,
        source="external_channel_ingress",
        sender=actor_display_name or actor_id or None,
        channel=channel_key,
        source_event_id=request_id,
        metadata={
            "actor_id": actor_id,
            **{
                key: value
                for key, value in dict(metadata or {}).items()
                if key in {"endpoint_key", "message_id", "connector_id", "source_connector_id"}
            },
        },
    )
    return guarded.text, {
        "external_content_guard": {
            "wrapped": True,
            "wrapper_id": guarded.wrapper_id,
            "source": guarded.metadata.source,
            "channel": guarded.metadata.channel,
            "source_event_id": guarded.metadata.source_event_id,
            "injection_score": guarded.injection_score,
            "injection_severity": guarded.injection_severity,
            "suspicious_patterns": list(guarded.suspicious_patterns),
        }
    }


def build_channel_turn_request(
    *,
    tenant_id: str,
    workspace_id: str,
    channel_key: str,
    actor_id: str,
    actor_display_name: str,
    message: str,
    thread_id: str,
    session_id: str,
    install: Dict[str, Any],
    manifest: Any,
    shared_metadata: Dict[str, Any],
    master_install_id: Optional[str],
    runtime_mode: str,
    runtime_profile_id: Optional[str],
    request_id: Optional[str],
    privileged_runtime_approved: bool,
    prior_messages: Optional[list[dict[str, Any]]] = None,
    business_plan: Optional[str] = None,
) -> Any:
    runtime_profile = coerce_dict(install.get("runtime_profile"))
    install_metadata = coerce_dict(install.get("metadata"))
    deployed_agent_metadata = coerce_dict(install_metadata.get("deployed_agent"))
    source_authority = str(shared_metadata.get("ai_source_authority") or "").strip().lower()
    if source_authority == "deployed_agent_record":
        selected_provider = str(shared_metadata.get("provider") or "").strip() or None
        selected_model = str(shared_metadata.get("model") or "").strip() or None
    else:
        selected_provider = (
            str(shared_metadata.get("provider") or "").strip()
            or str(deployed_agent_metadata.get("provider") or "").strip()
            or str(install_metadata.get("provider") or "").strip()
            or None
        )
        selected_model = (
            str(shared_metadata.get("model") or "").strip()
            or str(deployed_agent_metadata.get("model") or "").strip()
            or str(install_metadata.get("model") or "").strip()
            or None
        )
    selected_tool_ids = _normalize_tool_ids(
        shared_metadata.get("selected_tool_ids")
        or deployed_agent_metadata.get("selected_tool_ids")
        or install_metadata.get("selected_tool_ids")
    )
    requested_tools = _merge_tool_descriptors(
        shared_metadata.get("tools"),
        _specialist_tool_descriptors(selected_tool_ids),
    )
    metadata = {
        **shared_metadata,
        "workspace_agent_install_id": str(install.get("id") or "").strip() or None,
        "active_agent_install_id": str(install.get("id") or "").strip() or None,
        "master_agent_install_id": str(master_install_id or install.get("id") or "").strip() or None,
        "runtime_mode": str(runtime_mode or "").strip().lower() or None,
        "runtime_profile_id": runtime_profile_id,
        "runtime_profile_label": str(runtime_profile.get("label") or "").strip() or None,
        "runtime_id": str(runtime_profile.get("runtime_id") or "").strip() or None,
        "machine_id": str(runtime_profile.get("machine_id") or "").strip() or None,
        "agent_role": agent_role_token(install=install, manifest=manifest),
        "agent_role_source": "channel_owner_binding",
        "selected_tool_ids": selected_tool_ids or None,
        "tools": requested_tools or None,
    }
    guarded_message, guard_metadata = _guard_external_channel_message(
        message=message,
        channel_key=channel_key,
        actor_id=actor_id,
        actor_display_name=actor_display_name,
        request_id=request_id,
        metadata=metadata,
    )
    metadata.update(guard_metadata)
    return build_inbound_agent_turn_request(
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        session_id=session_id,
        thread_id=thread_id,
        channel=channel_key,
        actor_type="user",
        actor_id=actor_id,
        actor_display_name=actor_display_name,
        message=guarded_message,
        prior_messages=prior_messages,
        context_hints={
            "source": "external_channel_ingress",
            "request_id": request_id,
            "provider": selected_provider,
            "model": selected_model,
            "metadata": {
                key: value
                for key, value in metadata.items()
                if value not in (None, "", [], {})
            },
        },
        business_plan=business_plan,
        execution_mode="durable",
        response_mode="channel_reply",
        machine_target=str(shared_metadata.get("machine_target") or "").strip() or None,
        policy_context={
            "execution_target": str(shared_metadata.get("execution_target") or "").strip() or None,
            "trust_mode": str(shared_metadata.get("trust_mode") or "").strip() or None,
            "privileged_runtime_approved": bool(privileged_runtime_approved),
        },
    )


def build_routing_context(
    *,
    tenant_id: str,
    workspace_id: str,
    channel_key: str,
    endpoint_key: Any,
    customer_message: str,
    install: Dict[str, Any],
    manifest: Any = None,
    owner_type: str = "specialist",
    session_key: Optional[str] = None,
    message_id: Optional[str] = None,
    actor_id: Optional[str] = None,
    actor_display_name: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
    master_install_id: Optional[str] = None,
    runtime_mode: Optional[str] = None,
    runtime_profile_id: Optional[str] = None,
    request_id: Optional[str] = None,
    privileged_runtime_approved: bool = False,
    allow_master_fallback: bool = False,
    validate_preflight: bool = True,
) -> ChannelRoutingContext:
    if validate_preflight:
        resolved_channel_key, resolved_endpoint_key, resolved_message = assert_inbound_allowed(
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            channel_key=channel_key,
            endpoint_key=endpoint_key,
            customer_message=customer_message,
        )
    else:
        resolved_channel_key = normalize_channel_key(channel_key)
        resolved_endpoint_key = str(normalize_endpoint_key(resolved_channel_key, endpoint_key) or "").strip().lower()
        resolved_message = str(customer_message or "").strip()

    install_payload = coerce_dict(install)
    identity = build_inbound_identity(
        workspace_id=workspace_id,
        channel_key=resolved_channel_key,
        endpoint_key=resolved_endpoint_key,
        session_key=session_key,
        message_id=message_id,
        actor_id=actor_id,
        actor_display_name=actor_display_name,
    )
    responder_install_id = str(install_payload.get("id") or "").strip() or None
    assert_agent_allowed(
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        responder_install_id=responder_install_id,
    )
    resolved_runtime_mode = (
        str(
            runtime_mode
            or install_payload.get("runtime_mode")
            or getattr(getattr(manifest, "runtime", None), "mode", "hosted_secure")
            or "hosted_secure"
        )
        .strip()
        .lower()
        or "hosted_secure"
    )
    resolved_runtime_profile_id = str(runtime_profile_id or runtime_profile_id_from_install(install_payload) or "").strip() or None
    resolved_owner_type = str(owner_type or "specialist").strip() or "specialist"
    responder_label = (
        str(install_payload.get("label") or "").strip()
        or getattr(getattr(manifest, "identity", None), "name", "Agent")
    )
    shared_metadata = {
        "source": "external_channel_ingress",
        "channel_key": resolved_channel_key,
        "endpoint_key": resolved_endpoint_key,
        "owner_type": resolved_owner_type,
        "responder_install_id": responder_install_id,
        "message_id": str(message_id or "").strip() or None,
        **coerce_dict(metadata),
    }
    turn_request = build_channel_turn_request(
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        channel_key=resolved_channel_key,
        actor_id=identity["actor_id"],
        actor_display_name=identity["actor_display_name"],
        message=resolved_message,
        thread_id=identity["thread_id"],
        session_id=identity["session_key"],
        install=install_payload,
        manifest=manifest,
        shared_metadata=shared_metadata,
        master_install_id=master_install_id,
        runtime_mode=resolved_runtime_mode,
        runtime_profile_id=resolved_runtime_profile_id,
        request_id=request_id,
        privileged_runtime_approved=privileged_runtime_approved,
        prior_messages=None,
        business_plan=None,
    )
    return ChannelRoutingContext(
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        channel_key=resolved_channel_key,
        endpoint_key=resolved_endpoint_key,
        actor_id=identity["actor_id"],
        actor_display_name=identity["actor_display_name"],
        session_key=identity["session_key"],
        thread_id=identity["thread_id"],
        message=resolved_message,
        owner_type=resolved_owner_type,
        install=install_payload,
        manifest=manifest,
        responder_install_id=responder_install_id,
        responder_label=responder_label,
        runtime_mode=resolved_runtime_mode,
        runtime_profile_id=resolved_runtime_profile_id,
        master_install_id=str(master_install_id or install_payload.get("id") or "").strip() or None,
        shared_metadata=shared_metadata,
        turn_request=turn_request,
        execution_owner=channel_turn_owner_user(install=install_payload),
        connector_id=channel_connector_id(install=install_payload, metadata=shared_metadata),
        allow_master_fallback=bool(allow_master_fallback),
        privileged_runtime_approved=bool(privileged_runtime_approved),
    )


def _deployed_agent_source_policy(deployed_agent: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    config = deployed_agent_config_schema.deployed_agent_config_from_record(deployed_agent)
    runtime_supply = coerce_dict(config.runtime_supply)
    ai_source = coerce_dict(runtime_supply.get("ai_source"))
    return {
        "provider": str(config.provider or "").strip() or None,
        "model": str(config.model or "").strip() or None,
        "runtime_target": str(config.runtime_target or "").strip() or None,
        "runtime_placement": str(config.runtime_placement or "").strip() or None,
        "runtime_supply": runtime_supply or None,
        "ai_source": ai_source or None,
        "payer": str(ai_source.get("payer") or "").strip() or None,
    }


def bind_deployed_agent_source_to_context(
    context: ChannelRoutingContext,
    *,
    deployed_agent: Dict[str, Any],
    request_id: Optional[str] = None,
) -> ChannelRoutingContext:
    policy = _deployed_agent_source_policy(deployed_agent)
    next_shared_metadata = dict(context.shared_metadata or {})
    for spoofable_key in ("provider", "model", "payer", "ai_source"):
        next_shared_metadata.pop(spoofable_key, None)
    next_shared_metadata.update(
        {
            "deployed_agent_id": str(deployed_agent.get("id") or context.deployed_agent_id or "").strip() or None,
            "deployment_state": str(deployed_agent.get("deployment_state") or context.deployed_agent_state or "").strip().lower() or None,
            "provider": policy.get("provider"),
            "model": policy.get("model"),
            "runtime_target": policy.get("runtime_target"),
            "runtime_placement": policy.get("runtime_placement"),
            "runtime_supply": policy.get("runtime_supply"),
            "ai_source": policy.get("ai_source"),
            "payer": policy.get("payer"),
            "ai_source_authority": "deployed_agent_record",
        }
    )
    next_shared_metadata = {
        key: value
        for key, value in next_shared_metadata.items()
        if value not in (None, "", [], {})
    }
    return rebuild_turn_request(
        context,
        request_id=request_id,
        shared_metadata=next_shared_metadata,
    )


def runtime_profile_id_from_install(install: Dict[str, Any]) -> Optional[str]:
    return runtime_profile_id(install)


def rebuild_turn_request(
    context: ChannelRoutingContext,
    *,
    request_id: Optional[str] = None,
    prior_messages: Optional[list[dict[str, Any]]] = None,
    business_plan: Optional[str] = None,
    shared_metadata: Optional[Dict[str, Any]] = None,
) -> ChannelRoutingContext:
    next_shared_metadata = coerce_dict(shared_metadata) if shared_metadata is not None else dict(context.shared_metadata)
    next_prior_messages = prior_messages if prior_messages is not None else context.prior_messages
    next_business_plan = business_plan if business_plan is not None else context.business_plan
    turn_request = build_channel_turn_request(
        tenant_id=context.tenant_id,
        workspace_id=context.workspace_id,
        channel_key=context.channel_key,
        actor_id=context.actor_id,
        actor_display_name=context.actor_display_name,
        message=context.message,
        thread_id=context.thread_id,
        session_id=context.session_key,
        install=context.install,
        manifest=context.manifest,
        shared_metadata=next_shared_metadata,
        master_install_id=context.master_install_id,
        runtime_mode=context.runtime_mode,
        runtime_profile_id=context.runtime_profile_id,
        request_id=request_id,
        privileged_runtime_approved=context.privileged_runtime_approved,
        prior_messages=next_prior_messages if isinstance(next_prior_messages, list) else None,
        business_plan=str(next_business_plan or "").strip() or None,
    )
    return replace(
        context,
        shared_metadata=next_shared_metadata,
        prior_messages=next_prior_messages if isinstance(next_prior_messages, list) else None,
        business_plan=str(next_business_plan or "").strip() or None,
        turn_request=turn_request,
    )


def canonical_run_reply(*, status: str, run_id: Optional[str]) -> str:
    normalized = str(status or "").strip().lower()
    base = {
        "accepted": "Run accepted.",
        "queued": "Run queued.",
        "queued_local": "Run queued for Gateway.",
        "running": "Run started.",
        "running_local": "Run started on Gateway.",
        "waiting_for_input": "Run is waiting for required input.",
    }.get(normalized, "")
    if not base and normalized:
        base = f"Run {normalized}."
    if not base:
        base = "Run accepted."
    if str(run_id or "").strip():
        return f"{base} run_id: {run_id}"
    return base


def execution_result_from_dict(*, payload: Dict[str, Any]) -> ChannelExecutionResult:
    return ChannelExecutionResult(
        status=str(payload.get("status") or "completed").strip().lower() or "completed",
        reply=str(payload.get("reply") or "").strip(),
        run_id=str(payload.get("run_id") or "").strip() or None,
        artifact=payload.get("artifact"),
        steps=list(payload.get("steps") or []),
        critic=payload.get("critic"),
        limit_reason=payload.get("limit_reason"),
        retry_after_seconds=payload.get("retry_after_seconds"),
        quota_snapshot=payload.get("quota_snapshot"),
        metadata=coerce_dict(payload.get("metadata")),
        event_type=str(payload.get("event_type") or "response").strip() or "response",
        incident_scope=str(payload.get("incident_scope") or "").strip() or None,
        incident_mode=str(payload.get("incident_mode") or "").strip() or None,
        payload=coerce_dict(payload.get("payload")),
        event_metadata=coerce_dict(payload.get("event_metadata")),
        health_safety=coerce_dict(payload.get("health_safety")) or None,
        error=coerce_dict(payload.get("error")) or None,
        degraded_operations=[
            coerce_dict(item)
            for item in list(payload.get("degraded_operations") or [])
            if isinstance(item, dict)
        ],
    )


def normalize_canonical_channel_result(
    *,
    execution_result: Dict[str, Any],
) -> ChannelExecutionResult:
    if str(execution_result.get("kind") or "").strip() == "durable_run":
        durable = coerce_dict(execution_result.get("result"))
        run_id = str(durable.get("run_id") or "").strip() or None
        raw_status = str(durable.get("status") or "accepted").strip().lower() or "accepted"
        status = {
            "starting": "accepted",
        }.get(raw_status, raw_status)
        return ChannelExecutionResult(
            status=status,
            reply=canonical_run_reply(status=status, run_id=run_id),
            run_id=run_id,
            metadata={
                "kind": "durable_run",
                "engine": durable.get("engine"),
                "route": durable.get("route"),
                "doctor_preflight": durable.get("doctor_preflight"),
                "created_run": durable.get("created_run"),
            },
            event_type="run_started",
        )
    return execution_result_from_dict(payload=execution_result)


def context_to_compat_dict(context: ChannelRoutingContext) -> Dict[str, Any]:
    return {
        "tenant_id": context.tenant_id,
        "workspace_id": context.workspace_id,
        "channel_key": context.channel_key,
        "endpoint_key": context.endpoint_key,
        "session_key": context.session_key,
        "thread_id": context.thread_id,
        "message": context.message,
        "actor_id": context.actor_id,
        "actor_display_name": context.actor_display_name,
        "install": context.install,
        "manifest": context.manifest,
        "owner_type": context.owner_type,
        "responder_install_id": context.responder_install_id,
        "responder_label": context.responder_label,
        "runtime_mode": context.runtime_mode,
        "runtime_profile_id": context.runtime_profile_id,
        "master_install_id": context.master_install_id,
        "shared_metadata": context.shared_metadata,
        "turn_request": context.turn_request,
        "execution_owner": context.execution_owner,
        "connector_id": context.connector_id,
        "deployed_agent": context.deployed_agent,
        "deployed_agent_id": context.deployed_agent_id,
        "deployed_agent_state": context.deployed_agent_state,
        "inbound_event": context.inbound_event,
        "prior_messages": context.prior_messages,
        "business_plan": context.business_plan,
        "health_safety_context": context.health_safety_context,
    }


def build_route_response(
    *,
    context: ChannelRoutingContext,
    result: ChannelExecutionResult,
    inbound_event_id: Optional[str],
    outbound_event_id: Optional[str],
    notices: Optional[list[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    payload: Dict[str, Any] = {
        "workspace_id": context.workspace_id,
        "tenant_id": context.tenant_id,
        "channel_key": context.channel_key,
        "endpoint_key": context.endpoint_key,
        "session_key": context.session_key,
        "thread_id": context.thread_id,
        "owner": {
            "install_id": context.responder_install_id,
            "label": context.responder_label,
            "type": context.owner_type,
            "runtime_mode": context.runtime_mode,
        },
        "status": result.status,
        "reply": result.reply,
        "artifact": result.artifact,
        "steps": result.steps,
        "critic": result.critic,
        "limit_reason": result.limit_reason,
        "retry_after_seconds": result.retry_after_seconds,
        "quota_snapshot": result.quota_snapshot,
        "audit": {
            "inbound_event_id": inbound_event_id,
            "outbound_event_id": outbound_event_id,
        },
    }
    if result.run_id is not None:
        payload["run_id"] = result.run_id
    if result.error:
        payload["error"] = result.error
    if result.metadata:
        payload["metadata"] = result.metadata
    reply_markup = payload.get("reply_markup")
    if not isinstance(reply_markup, dict) and isinstance(result.payload, dict):
        candidate_markup = result.payload.get("reply_markup")
        if isinstance(candidate_markup, dict):
            payload["reply_markup"] = candidate_markup
    if result.degraded_operations:
        payload["degraded_operations"] = list(result.degraded_operations)
    if result.incident_scope is not None:
        payload["incident_scope"] = result.incident_scope
    if result.incident_mode is not None:
        payload["incident_mode"] = result.incident_mode
    if notices:
        payload["notices"] = [dict(item) for item in notices if isinstance(item, dict)]
    return payload
