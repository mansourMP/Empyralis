from server_modules import runtime_config as config
from server_modules import run_service
from server_modules import shared as shared
from server_modules import runtime_common as common
from server_modules import credential_rotation_service
from server_modules import config_defaults_service
# Phase 3 stubs: approval system removed — these were from runs_history
def _approval_correlation_id(approval_id: str, run_id: str | None = None, event_id: str | None = None) -> str:
    return f"corr-{approval_id or run_id or event_id or 'unknown'}"

def _append_approval_audit(**kwargs) -> None:
    pass
from server_modules.runs_output import _compact_event_text, _json_safe

globals().update({key: value for key, value in vars(config).items() if not key.startswith("__")})
globals().update({key: value for key, value in vars(shared).items() if not key.startswith("__")})
globals().update({key: value for key, value in vars(common).items() if not key.startswith("__")})

configure_runtime_model_context(
    memory_max_text_chars=ORION_MEMORY_MAX_TEXT_CHARS,
    normalize_memory_bucket=_normalize_memory_bucket,
    normalize_action_id=normalize_action_id,
    provider_catalog=PROVIDER_CATALOG,
    connector_catalog=CONNECTOR_CATALOG,
    channel_catalog=CHANNEL_REGISTRY,
)

RUN_TOOL_LOOP_REPEAT_LIMIT = 3
RUN_TOOL_LOOP_REPLY = "A tool loop was detected. Please rephrase the request or break it into smaller steps."
_RUN_TOOL_LOOP_STATE: Dict[str, Dict[str, Any]] = {}

# MAN-144: per-run cost ledger, keyed by run_id -- NOT the `state` dict
# threaded through DAG-node execution, because that dict is recreated fresh
# per node (and per loop iteration) for the workflow_graph_execute path (see
# _resolve_agent_generation_state / the loop-node's iteration_state in
# runs_execution.py), so it cannot carry a running total across an entire
# run's model calls. run_id is the one identifier stable across every node,
# loop iteration, and retry within a single run. Mirrors the existing
# _RUN_TOOL_LOOP_STATE pattern immediately above -- same lifecycle, same
# cleanup hook (clear_run_tool_signature_state, called at every run-terminal
# point in run_orion_mission).
_RUN_COST_LEDGERS: Dict[str, Dict[str, float]] = {}


def build_tool_call_signature(tool_name: str, arguments: Any) -> str:
    normalized_name = str(tool_name or "").strip()
    try:
        normalized_arguments = json.dumps(arguments, sort_keys=True, ensure_ascii=False)
    except Exception:
        normalized_arguments = str(arguments)
    return f"{normalized_name}:{normalized_arguments}"


def record_run_tool_signature(session_key: str, tool_name: str, arguments: Any) -> bool:
    signature = build_tool_call_signature(tool_name, arguments)
    state = _RUN_TOOL_LOOP_STATE.get(session_key) or {"signature": "", "count": 0}
    if state.get("signature") == signature:
        state["count"] = int(state.get("count") or 0) + 1
    else:
        state = {"signature": signature, "count": 1}
    _RUN_TOOL_LOOP_STATE[session_key] = state
    return int(state.get("count") or 0) >= RUN_TOOL_LOOP_REPEAT_LIMIT


def clear_run_tool_signature_state(session_key: str) -> None:
    _RUN_TOOL_LOOP_STATE.pop(session_key, None)
    # MAN-144: run_orion_mission calls this at every terminal point of a run
    # (success and every failure/cancellation branch) with session_key ==
    # run_id, so it doubles as the cost ledger's cleanup hook rather than
    # adding a second call at each of those call sites.
    _RUN_COST_LEDGERS.pop(session_key, None)


def format_agent_summary(agents: Any) -> str:
    if not isinstance(agents, list) or not agents:
        return "- none"

    lines: List[str] = []
    for agent in agents:
        if not isinstance(agent, dict):
            continue
        role = str(agent.get("role") or "Worker")
        model = str(agent.get("modelId") or "unknown-model")
        provider = str(agent.get("provider") or "unknown-provider")
        duty = str(agent.get("duty") or agent.get("description") or "").strip()
        if duty:
            lines.append(f"- {role} ({provider}:{model}) duty={duty[:120]}")
        else:
            lines.append(f"- {role} ({provider}:{model})")
    return "\n".join(lines) if lines else "- none"


def resolve_run_execution_context(context: Dict[str, Any]):
    metadata = context.get("metadata") if isinstance(context.get("metadata"), dict) else {}
    provider = normalize_provider_id(context.get("provider") or metadata.get("provider") or "openai")
    if provider not in PROVIDER_CATALOG:
        raise RuntimeError(f"Unsupported provider '{provider}'")

    workspace_id = context.get("workspace_id") or metadata.get("workspace_id")
    credential_id = context.get("credential_id") or metadata.get("credential_id")
    selected_model = (
        context.get("model")
        or metadata.get("model")
        or PROVIDER_CATALOG.get(provider, {}).get("default_model")
        or CODEX_MODEL
    )
    candidate_context = dict(context)
    candidate_context["credential_id"] = credential_id
    candidate_context["workspace_id"] = workspace_id
    candidates = _build_provider_credential_candidates(candidate_context, metadata, provider)
    if not candidates:
        raise RuntimeError(f"No credentials available for provider '{provider}'.")

    return provider, str(selected_model), candidates, metadata


# MAN-144: substring every "stopped deliberately, not crashed" run halt in
# this file is matched on (see is_non_retryable_runtime_error below and the
# dedicated branch in runs_execution.run_orion_mission that turns this into
# a "cancelled" run status, exactly like the pre-existing "stopped by human
# decision" halt). Keep this string and that branch's substring check in
# sync if either changes.
RUN_COST_CEILING_ERROR_MARKER = "run cost ceiling reached"


def _run_cost_ceiling_usd(context: Dict[str, Any]) -> float:
    """Resolve the enforced per-run spend ceiling for the run this `context`
    belongs to. Reuses deployed_agent_cost_cap_service's config precedence
    (per-agent commerce_policy override -> platform default) when a caller
    has already resolved and stashed a value onto
    context["metadata"]["deployed_agent_run_cost_ceiling_usd"] (a plain
    float -- deliberately NOT a DB lookup here: this function runs deep
    inside a background run thread, not an async request context, so it
    cannot safely await a repository call). Absent that, every run still
    gets the platform default -- never unbounded.
    """
    metadata = context.get("metadata") if isinstance(context.get("metadata"), dict) else {}
    override = metadata.get("deployed_agent_run_cost_ceiling_usd")
    if override is not None:
        try:
            parsed = float(override)
        except (TypeError, ValueError):
            parsed = 0.0
        if parsed > 0:
            return parsed
    return config_defaults_service.default_run_cost_ceiling_usd()


def generate_with_candidate_failover(
    state: Dict[str, Any],
    context: Dict[str, Any],
    log_queue: queue.Queue,
    system_prompt: str,
    user_input: str,
    *,
    run_id: Optional[str] = None,
) -> str:
    from server_modules.runs_core import emit_log

    # MAN-144: enforced mid-run ceiling, checked before every single call
    # this function makes on behalf of a run -- not just the first. A run
    # that makes N model calls (one plan_generate + one result_generate
    # node, or one call per workflow "agent" node, possibly repeated across
    # loop iterations) gets checked before each one.
    #
    # Keyed by run_id (see _RUN_COST_LEDGERS above) rather than the `state`
    # dict passed in here: for the workflow_graph_execute path, callers pass
    # a fresh per-node/per-loop-iteration state object on every call (see
    # _resolve_agent_generation_state and the loop node's iteration_state in
    # runs_execution.py), so accumulating on `state` would silently reset to
    # zero on every node -- a ceiling that resets itself is not a ceiling.
    # run_id is stable for the whole run; when it's not supplied (a caller
    # this change didn't update, or a direct unit-test call) this falls back
    # to accumulating on `state` itself so nothing breaks, but that fallback
    # only actually accumulates correctly for callers that reuse the same
    # `state` object across calls (true for the plan_generate/result_generate
    # DAG path today).
    cost_ledger = _RUN_COST_LEDGERS.setdefault(run_id, {}) if run_id else state
    if "_run_cost_ceiling_usd" not in cost_ledger:
        cost_ledger["_run_cost_ceiling_usd"] = _run_cost_ceiling_usd(context)
        cost_ledger["_run_cost_accumulated_usd"] = 0.0
    ceiling_usd = float(cost_ledger.get("_run_cost_ceiling_usd") or 0.0)
    accumulated_usd = float(cost_ledger.get("_run_cost_accumulated_usd") or 0.0)
    if ceiling_usd > 0 and accumulated_usd >= ceiling_usd:
        raise RuntimeError(
            f"Run stopped: {RUN_COST_CEILING_ERROR_MARKER} "
            f"(spent ${accumulated_usd:.4f} of a ${ceiling_usd:.4f} per-run ceiling "
            f"before this call would have been made)."
        )

    provider = str(state.get("provider") or "openai").strip().lower()
    default_model = str(state.get("selected_model") or CODEX_MODEL).strip() or CODEX_MODEL
    candidates = state.get("credential_candidates") if isinstance(state.get("credential_candidates"), list) else []
    if not candidates:
        raise RuntimeError(f"No credentials available for provider '{provider}'.")

    last_error: Optional[Exception] = None
    for idx, candidate in enumerate(candidates):
        if not isinstance(candidate, dict):
            continue
        credentials = candidate.get("credentials") if isinstance(candidate.get("credentials"), dict) else {}
        if not credentials:
            continue
        initial_model = str(candidate.get("model") or default_model).strip() or default_model
        profile_id = str(candidate.get("profile_id") or "").strip() or None
        credential_id = str(candidate.get("credential_id") or "").strip() or None
        source = str(candidate.get("source") or "unknown").strip()
        fallback_models = candidate.get("fallback_models")
        if not isinstance(fallback_models, list):
            fallback_models = context.get("fallback_models") if isinstance(context.get("fallback_models"), list) else []
        allow_model_fallback = str(
            candidate.get("allow_model_fallback")
            or context.get("allow_model_fallback")
            or context.get("explicit_model_fallback")
            or ""
        ).strip().lower() in {"1", "true", "yes", "on"}
        model_attempts = _candidate_model_attempts(
            provider,
            initial_model,
            allow_fallback=allow_model_fallback,
            fallback_models=fallback_models,
        )
        candidate_error: Optional[Exception] = None
        for model_attempt_index, model in enumerate(model_attempts):
            try:
                resolved_provider, adapter_key, adapter = resolve_provider_adapter(provider, credentials)
                text = adapter.generate(system_prompt, user_input, model, credentials)
                # MAN-144: ProviderAdapter.generate() returns text only, no
                # usage -- so, exactly like this run's own existing telemetry
                # a few lines below (and pack_finalize/usage_finalize's
                # build_masked_usage calls elsewhere in this run system),
                # reuse usage_accounting_service's masked/estimated-token
                # costing rather than inventing a second estimator. Counted
                # against the ceiling even if this call's answer is later
                # discarded by a failover/retry higher up -- the money was
                # still spent.
                call_cost_usd = float(
                    build_masked_usage(resolved_provider, model, f"{system_prompt}\n{user_input}", text).get(
                        "estimated_cost_usd"
                    )
                    or 0.0
                )
                cost_ledger["_run_cost_accumulated_usd"] = (
                    float(cost_ledger.get("_run_cost_accumulated_usd") or 0.0) + call_cost_usd
                )
                if profile_id:
                    _mark_profile_success(profile_id)
                if credential_id:
                    credential_rotation_service.mark_success(credential_id)
                state["active_candidate_index"] = idx
                state["active_profile_id"] = profile_id
                state["active_model"] = model
                state["active_provider"] = resolved_provider
                state["active_adapter"] = adapter_key
                state["credentials"] = credentials
                if idx > 0:
                    emit_log(
                        log_queue,
                        "warn",
                        f"Provider failover succeeded using candidate {idx + 1} ({source}).",
                        event="profile_failover",
                        data={"provider": provider, "profile_id": profile_id, "candidate_index": idx, "source": source},
                    )
                if model_attempt_index > 0:
                    emit_log(
                        log_queue,
                        "warn",
                        f"Recovered with fallback model {model}.",
                        event="profile_model_fallback",
                        data={
                            "provider": provider,
                            "profile_id": profile_id,
                            "candidate_index": idx,
                            "source": source,
                            "requested_model": initial_model,
                            "model": model,
                        },
                    )
                return text
            except Exception as exc:
                last_error = exc
                candidate_error = exc
                is_rate_limited = _is_rate_limit_runtime_error(exc)
                if is_rate_limited and credential_id:
                    # Gap 1.5 -- rotate away from this key: it won't be
                    # offered again as a candidate (this run or future ones)
                    # until its cooldown elapses.
                    credential_rotation_service.mark_rate_limited(credential_id)
                if (
                    is_rate_limited
                    and model_attempt_index + 1 < len(model_attempts)
                ):
                    next_model = model_attempts[model_attempt_index + 1]
                    emit_log(
                        log_queue,
                        "warn",
                        f"Model {model} is rate-limited; retrying with {next_model}.",
                        event="profile_model_retry",
                        data={
                            "provider": provider,
                            "profile_id": profile_id,
                            "candidate_index": idx,
                            "source": source,
                            "model": model,
                            "next_model": next_model,
                            "error": str(exc)[:800],
                        },
                    )
                    continue
                break
        if candidate_error is not None:
            raw_error = str(candidate_error)
            if profile_id:
                _mark_profile_failure(profile_id, raw_error)
            emit_log(
                log_queue,
                "warn",
                f"Provider candidate failed ({source}): {friendly_runtime_error_message(candidate_error)}",
                event="profile_candidate_failed",
                data={
                    "provider": provider,
                    "profile_id": profile_id,
                    "candidate_index": idx,
                    "source": source,
                    "error": raw_error[:800],
                },
            )
            continue
    if last_error is not None:
        raise last_error
    raise RuntimeError(f"No usable credentials remained for provider '{provider}'.")


def _is_rate_limit_runtime_error(exc: Exception) -> bool:
    lowered = str(exc or "").strip().lower()
    return "429" in lowered or "rate limit" in lowered


def _candidate_model_attempts(
    provider: str,
    preferred_model: str,
    *,
    allow_fallback: bool = False,
    fallback_models: Optional[List[Any]] = None,
) -> List[str]:
    model = str(preferred_model or "").strip()
    attempts: List[str] = [model] if model else []
    if allow_fallback:
        for fallback in list(fallback_models or []):
            token = str(fallback).strip()
            if token and token not in attempts:
                attempts.append(token)
    return attempts or [str(CODEX_MODEL or "gpt-4o-mini").strip() or "gpt-4o-mini"]


def wait_for_human_response(
    run_id: str,
    prompt: str,
    *,
    source: str = "runtime_wait",
    metadata: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    from server_modules.runs_core import (
        _begin_run_pending_confirmation,
        _clear_pending_confirmation,
        _get_pending_confirmation,
        _set_pending_confirmation,
        emit_log,
        set_run_status,
    )
    return run_service.wait_for_human_response(
        run_id,
        prompt,
        runs_by_id=runs,
        begin_run_pending_confirmation_fn=_begin_run_pending_confirmation,
        clear_pending_confirmation_fn=_clear_pending_confirmation,
        get_pending_confirmation_fn=_get_pending_confirmation,
        set_pending_confirmation_fn=_set_pending_confirmation,
        approval_correlation_id_fn=_approval_correlation_id,
        append_approval_audit_fn=_append_approval_audit,
        emit_log_fn=emit_log,
        set_run_status_fn=set_run_status,
        utc_now_iso_fn=_utc_now_iso,
        approval_ttl_seconds=ORION_APPROVAL_TTL_SECONDS,
        monotonic_fn=time.monotonic,
        queue_empty_exception=queue.Empty,
        source=source,
        metadata=metadata,
    )


def wait_for_human_decision(
    run_id: str,
    prompt: str,
    *,
    source: str = "runtime_wait",
    metadata: Optional[Dict[str, Any]] = None,
) -> bool:
    from server_modules.runs_core import emit_log

    run = runs.get(run_id) if isinstance(runs, dict) else None
    log_queue = run.get("logs") if isinstance(run, dict) else None
    if log_queue is not None:
        emit_log(
            log_queue,
            "info",
            "Approval gate bypassed; agent continued execution.",
            event="approval_bypassed",
            data={"source": source, "prompt": prompt, "metadata": metadata or {}},
        )
    return True

def validate_orion_runtime() -> List[str]:
    errors: List[str] = []
    if ORION_RUN_TIMEOUT_SECONDS <= 0:
        errors.append("ORION_RUN_TIMEOUT_SECONDS must be > 0.")
    if ORION_MAX_RETRIES < 0:
        errors.append("ORION_MAX_RETRIES cannot be negative.")
    if ORION_RETRY_BACKOFF_SECONDS < 0:
        errors.append("ORION_RETRY_BACKOFF_SECONDS cannot be negative.")
    if ORION_MAX_EVENT_BUFFER <= 0:
        errors.append("ORION_MAX_EVENT_BUFFER must be > 0.")
    if ORION_HISTORY_LIMIT <= 0:
        errors.append("ORION_HISTORY_LIMIT must be > 0.")
    if ORION_CHANNEL_EVENTS_LIMIT <= 0:
        errors.append("ORION_CHANNEL_EVENTS_LIMIT must be > 0.")
    if ORION_SCHEDULER_POLL_SECONDS < 5:
        errors.append("ORION_SCHEDULER_POLL_SECONDS must be >= 5.")
    if ORION_APPROVAL_TTL_SECONDS < 30:
        errors.append("ORION_APPROVAL_TTL_SECONDS must be >= 30.")
    if ORION_SETUP_SESSION_TTL_SECONDS < 300:
        errors.append("ORION_SETUP_SESSION_TTL_SECONDS must be >= 300.")
    if ORION_IDEMPOTENCY_TTL_SECONDS < 60:
        errors.append("ORION_IDEMPOTENCY_TTL_SECONDS must be >= 60.")
    if CONTROL_PLANE_RATE_LIMIT_PER_MINUTE <= 0:
        errors.append("CONTROL_PLANE_RATE_LIMIT_PER_MINUTE must be > 0.")
    if CONTROL_PLANE_RATE_LIMIT_BURST < 0:
        errors.append("CONTROL_PLANE_RATE_LIMIT_BURST cannot be negative.")
    if ORION_TELEGRAM_AUTOPILOT_POLL_SECONDS < 1:
        errors.append("ORION_TELEGRAM_AUTOPILOT_POLL_SECONDS must be >= 1.")
    if ORION_TELEGRAM_AUTOPILOT_MAX_UPDATES <= 0:
        errors.append("ORION_TELEGRAM_AUTOPILOT_MAX_UPDATES must be > 0.")
    if ORION_TELEGRAM_AUTOPILOT_MAX_UPDATES > 100:
        errors.append("ORION_TELEGRAM_AUTOPILOT_MAX_UPDATES must be <= 100.")
    if ORION_TELEGRAM_AUTOPILOT_RUN_TIMEOUT_SECONDS < 30:
        errors.append("ORION_TELEGRAM_AUTOPILOT_RUN_TIMEOUT_SECONDS must be >= 30.")
    if ORION_TELEGRAM_AUTOPILOT_MAX_REPLY_CHARS < 120:
        errors.append("ORION_TELEGRAM_AUTOPILOT_MAX_REPLY_CHARS must be >= 120.")
    if ORION_TELEGRAM_AUTOPILOT_MAX_REPLY_CHARS > 6000:
        errors.append("ORION_TELEGRAM_AUTOPILOT_MAX_REPLY_CHARS must be <= 6000.")
    target_raw = str(ORION_TELEGRAM_AUTOPILOT_EXECUTION_TARGET or "").strip().lower()
    valid_target_inputs = VALID_EXECUTION_TARGETS.union(
        {"local", "local-worker", "local_worker", "companion", "localcompanion", "server", "managed", "hybrid"}
    )
    if target_raw and target_raw not in valid_target_inputs:
        errors.append("ORION_TELEGRAM_AUTOPILOT_EXECUTION_TARGET is invalid.")
    trust_raw = str(ORION_TELEGRAM_AUTOPILOT_TRUST_MODE or "").strip().lower()
    valid_trust_inputs = set(TRUST_MODE_ALIASES.keys()).union(VALID_TRUST_MODES)
    if trust_raw and trust_raw not in valid_trust_inputs:
        errors.append("ORION_TELEGRAM_AUTOPILOT_TRUST_MODE is invalid.")
    if ORION_TELEGRAM_AUTOPILOT_PROFILE and ORION_TELEGRAM_AUTOPILOT_PROFILE not in TELEGRAM_AUTOPILOT_PROFILE_CATALOG:
        errors.append("ORION_TELEGRAM_AUTOPILOT_PROFILE is invalid.")
    if ORION_TELEGRAM_AUTOPILOT_ENGINE and ORION_TELEGRAM_AUTOPILOT_ENGINE not in ENGINE_REGISTRY:
        errors.append("ORION_TELEGRAM_AUTOPILOT_ENGINE is invalid.")
    if ORION_WHATSAPP_AUTOPILOT_RUN_TIMEOUT_SECONDS < 30:
        errors.append("ORION_WHATSAPP_AUTOPILOT_RUN_TIMEOUT_SECONDS must be >= 30.")
    if ORION_WHATSAPP_AUTOPILOT_MAX_REPLY_CHARS < 120:
        errors.append("ORION_WHATSAPP_AUTOPILOT_MAX_REPLY_CHARS must be >= 120.")
    if ORION_WHATSAPP_AUTOPILOT_MAX_REPLY_CHARS > 6000:
        errors.append("ORION_WHATSAPP_AUTOPILOT_MAX_REPLY_CHARS must be <= 6000.")
    whatsapp_target_raw = str(ORION_WHATSAPP_AUTOPILOT_EXECUTION_TARGET or "").strip().lower()
    valid_target_inputs = VALID_EXECUTION_TARGETS.union(
        {"local", "local-worker", "local_worker", "companion", "localcompanion", "server", "managed", "hybrid"}
    )
    if whatsapp_target_raw and whatsapp_target_raw not in valid_target_inputs:
        errors.append("ORION_WHATSAPP_AUTOPILOT_EXECUTION_TARGET is invalid.")
    whatsapp_trust_raw = str(ORION_WHATSAPP_AUTOPILOT_TRUST_MODE or "").strip().lower()
    valid_trust_inputs = set(TRUST_MODE_ALIASES.keys()).union(VALID_TRUST_MODES)
    if whatsapp_trust_raw and whatsapp_trust_raw not in valid_trust_inputs:
        errors.append("ORION_WHATSAPP_AUTOPILOT_TRUST_MODE is invalid.")
    if ORION_WHATSAPP_AUTOPILOT_PROFILE and ORION_WHATSAPP_AUTOPILOT_PROFILE not in WHATSAPP_AUTOPILOT_PROFILE_CATALOG:
        errors.append("ORION_WHATSAPP_AUTOPILOT_PROFILE is invalid.")
    if ORION_WHATSAPP_AUTOPILOT_ENGINE and ORION_WHATSAPP_AUTOPILOT_ENGINE not in ENGINE_REGISTRY:
        errors.append("ORION_WHATSAPP_AUTOPILOT_ENGINE is invalid.")
    if not PROVIDER_ADAPTERS:
        errors.append("No provider adapters are registered.")
    return errors


class OrionEngineAdapter:
    name = "orion"

    def execute(self, run_id: str):
        from server_modules.runs_execution import run_orion_mission

        run_orion_mission(run_id)


def extract_openai_text(payload: Dict[str, Any]) -> str:
    if isinstance(payload.get("output_text"), str) and payload.get("output_text"):
        return payload["output_text"]

    output = payload.get("output") or []
    text_parts: List[str] = []
    if isinstance(output, list):
        for item in output:
            if not isinstance(item, dict):
                continue
            content = item.get("content") or []
            if isinstance(content, list):
                for block in content:
                    if not isinstance(block, dict):
                        continue
                    block_text = block.get("text")
                    if isinstance(block_text, str) and block_text.strip():
                        text_parts.append(block_text.strip())
            item_text = item.get("text")
            if isinstance(item_text, str) and item_text.strip():
                text_parts.append(item_text.strip())

    if text_parts:
        return "\n".join(text_parts)

    choices = payload.get("choices") or []
    if isinstance(choices, list) and choices:
        first = choices[0]
        if isinstance(first, dict):
            message = first.get("message") or {}
            if isinstance(message, dict):
                content = message.get("content")
                if isinstance(content, str) and content.strip():
                    return content
    return json.dumps(payload)


def call_openai_responses(system_prompt: str, user_input: str) -> Dict[str, Any]:
    openai_key, _ = _openai_env_bearer_with_source()
    if not openai_key:
        raise RuntimeError(
            "OpenAI credential is missing "
            "(CODEX_OAUTH_TOKEN / OPENAI_OAUTH_TOKEN / OPENAI_ACCESS_TOKEN"
            " / OPENAI_API_KEY)."
        )

    try:
        from server_modules.model_router import call_model_sync

        result = call_model_sync(
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_input},
            ],
            model=CODEX_MODEL,
            provider="openai",
            credentials={
                "access_token": openai_key,
                "org_id": OPENAI_ORG_ID,
                "project_id": OPENAI_PROJECT_ID,
            },
        )
        return {
            "output_text": result.get("content") or "",
            "model": result.get("model") or CODEX_MODEL,
            "usage": result.get("usage") or {},
        }
    except Exception as exc:
        raise RuntimeError(f"OpenAI responses call failed: {exc}") from exc


class CodexEngineAdapter:
    name = "codex"

    def execute(self, run_id: str):
        run = runs[run_id]
        run["thread_id"] = threading.get_ident()
        log_queue = run["logs"]
        context = run.get("context", {})

        try:
            set_run_status(run_id, "running")
            emit_log(log_queue, "info", f"Codex run started.", event="run_start", data={"run_id": run_id})

            workflow_id = context.get("workflow_id")
            metadata = context.get("metadata") or {}
            workspace_id = context.get("workspace_id") or metadata.get("workspace_id")
            tenant_id = context.get("tenant_id") or metadata.get("tenant_id")
            user_goal = context.get("user_goal") or "Execute the requested business plan."
            business_plan = context.get("business_plan") or ""
            agents = context.get("agents") or []

            provider = normalize_provider_id(context.get("provider") or metadata.get("provider") or "openai")
            if provider not in PROVIDER_CATALOG:
                raise RuntimeError(f"Unsupported provider '{provider}'")

            credential_id = context.get("credential_id") or metadata.get("credential_id")
            selected_model = context.get("model") or metadata.get("model") or PROVIDER_CATALOG.get(provider, {}).get("default_model") or CODEX_MODEL

            credentials: Dict[str, Any] = {}
            if credential_id:
                credentials = resolve_vault_credential(
                    str(credential_id),
                    str(workspace_id) if workspace_id else None,
                    tenant_id=str(tenant_id) if tenant_id else None,
                    tool_name="provider_inference",
                    run_id=run_id,
                    provider=provider,
                    runtime_mode=str(metadata.get("runtime_mode") or "").strip().lower() or None,
                    purpose="provider_execution",
                    actor_type="runtime",
                )
            elif isinstance(metadata.get("credentials"), dict):
                credentials = metadata.get("credentials") or {}
            elif provider == "openai":
                try:
                    credentials = resolve_default_vault_credential(
                        "openai",
                        str(workspace_id) if workspace_id else None,
                        tenant_id=str(tenant_id) if tenant_id else None,
                        tool_name="provider_inference",
                        run_id=run_id,
                        runtime_mode=str(metadata.get("runtime_mode") or "").strip().lower() or None,
                        purpose="provider_default_execution",
                        actor_type="runtime",
                    )
                except Exception:
                    openai_key, _ = _openai_env_bearer_with_source()
                    if openai_key:
                        credentials = {
                            "access_token": openai_key,
                            "org_id": OPENAI_ORG_ID,
                            "project_id": OPENAI_PROJECT_ID,
                        }

            if not credentials:
                raise RuntimeError(f"No credentials available for provider '{provider}'.")

            agent_summary = (
                [
                    f"- {agent.get('role', 'Worker')} ({agent.get('provider', 'unknown-provider')}:{agent.get('modelId', 'unknown-model')})"
                    for agent in agents
                    if isinstance(agent, dict)
                ]
                if isinstance(agents, list)
                else []
            )
            wants_structured_plan = bool((business_plan or "").strip()) or bool(
                re.search(r"\b(plan|roadmap|strategy|steps|phase|milestone|architecture|execution plan)\b", (user_goal or "").strip().lower())
            )
            available_agents = chr(10).join(agent_summary) if agent_summary else "- none"
            memory_context_block = _memory_prompt_context_block(context)
            response_contract = (
                "Provide: 1) ordered steps, 2) immediate external actions, 3) key risks/unknowns."
                if wants_structured_plan
                else "Respond as a normal assistant (not a forced plan template). For greeting/chat reply naturally and briefly; for work requests give direct actionable help; ask clarifying questions only when required."
            )
            user_message = (
                f"Workflow ID: {workflow_id or 'n/a'}\nUser Request: {user_goal}\n\n"
                f"Business Plan Context:\n{business_plan or 'No business plan provided.'}\n\n"
                f"{memory_context_block}\n\n"
                f"Selected Provider: {provider}\nSelected Model: {selected_model}\n\nAvailable Agents:\n{available_agents}\n\n{response_contract}"
            )

            system_prompt = ORION_CODEX_SYSTEM_PROMPT
            _, _, adapter = resolve_provider_adapter(provider, credentials)
            text = adapter.generate(system_prompt, user_message, str(selected_model), credentials)
            usage = build_masked_usage(provider, str(selected_model), user_message, text)
            usage_message = (
                f"[Telemetry] provider={usage['provider']} model={usage['model']} "
                f"tokens~{usage['total_tokens_est']} cost={usage['cost_band']}"
            )

            emit_log(log_queue, "info", text, event="codex_output")
            emit_log(log_queue, "info", usage_message, event="usage_masked", data=usage)
            emit_log(log_queue, "info", "Codex run completed.", event="run_complete")
            run["result"] = text
            run["usage_masked"] = usage
            set_run_status(run_id, "completed")
        except Exception as exc:
            emit_log(log_queue, "error", str(exc), event="run_error")
            set_run_status(run_id, "failed")
        finally:
            log_queue.put(None)


ENGINE_REGISTRY = {
    "orion": OrionEngineAdapter(),
    "codex": CodexEngineAdapter(),
}

ORION_ENGINE_VALIDATION_ERRORS = validate_orion_runtime()
if ORION_ENGINE_VALIDATION_ERRORS:
    print("⚠️ Empyralis runtime validation failed:")
    for err in ORION_ENGINE_VALIDATION_ERRORS:
        print(f" - {err}")
