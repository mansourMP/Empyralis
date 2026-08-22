from __future__ import annotations

import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from server_modules import (
    billing_credit_config,
    control_plane_repository,
    credit_ledger_contract,
    empyralis_model_tier_routing_service,
    usage_accounting_service,
)
from server_modules.direct_tool_config_service import run_async_tool_call
from server_modules.platform_event import AI_LIMIT_REACHED_WEB


_STATE_HOME = Path(
    os.getenv("EMPYRALIS_STATE_HOME", str(Path.home() / ".empyralis" / "state"))
).expanduser()
_RESERVATION_DB_PATH = Path(
    os.getenv(
        "EMPYRALIS_HOSTED_AI_RESERVATION_DB",
        str(_STATE_HOME / "quota" / "hosted-ai-reservations.sqlite3"),
    )
).expanduser()
_RESERVATION_SCHEMA_READY = False
_HOSTED_AI_PREFLIGHT_RESERVATION_USD = float(os.getenv("EMPYRALIS_HOSTED_AI_PREFLIGHT_RESERVATION_USD", "0.01"))
_HOSTED_AI_MAX_ACTIVE_RESERVATION_USD = float(os.getenv("EMPYRALIS_HOSTED_AI_MAX_ACTIVE_RESERVATION_USD", "2.00"))


def _text(value: Any) -> str:
    return str(value or "").strip()


def _coerce_dict(value: Any) -> Dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _reservation_connection() -> sqlite3.Connection:
    _RESERVATION_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(str(_RESERVATION_DB_PATH), timeout=10.0, isolation_level=None)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA busy_timeout=10000")
    return connection


def _ensure_reservation_schema(connection: sqlite3.Connection) -> None:
    global _RESERVATION_SCHEMA_READY
    if _RESERVATION_SCHEMA_READY:
        return
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS hosted_ai_reservations (
            reservation_key TEXT PRIMARY KEY,
            tenant_id TEXT NOT NULL,
            workspace_id TEXT NOT NULL,
            request_id TEXT NOT NULL,
            source_surface TEXT NOT NULL,
            amount_usd REAL NOT NULL,
            status TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_hosted_ai_reservations_scope ON hosted_ai_reservations(workspace_id, status)"
    )
    _RESERVATION_SCHEMA_READY = True


def _reservation_key(*, tenant_id: str, workspace_id: str, request_id: str, source_surface: str) -> str:
    return ":".join(
        [
            _text(tenant_id) or "default",
            _text(workspace_id) or "default",
            _text(request_id) or "request",
            _text(source_surface) or "sage_direct_chat",
        ]
    )


def _session_request_id(session_ctx: Optional[Dict[str, Any]], thread_id: str) -> str:
    payload = _coerce_dict(session_ctx)
    turn_request = _coerce_dict(payload.get("agent_turn_request"))
    context_hints = _coerce_dict(turn_request.get("context_hints"))
    return (
        _text(payload.get("request_id"))
        or _text(payload.get("client_request_id"))
        or _text(context_hints.get("request_id"))
        or _text(thread_id)
    )


def _session_tenant_id(session_ctx: Optional[Dict[str, Any]], workspace_id: str) -> Optional[str]:
    payload = _coerce_dict(session_ctx)
    turn_request = _coerce_dict(payload.get("agent_turn_request"))
    tenant_id = (
        _text(payload.get("tenant_id"))
        or _text(turn_request.get("tenant_id"))
    )
    if tenant_id:
        return tenant_id
    try:
        workspace = run_async_tool_call(control_plane_repository.get_workspace_by_id(workspace_id)) or {}
    except Exception:
        workspace = {}
    resolved = _text(_coerce_dict(workspace).get("tenant_id"))
    return resolved or None


def reserve_direct_chat_hosted_usage_best_effort(
    *,
    workspace_id: str,
    thread_id: str,
    session_ctx: Optional[Dict[str, Any]],
    availability_payload: Optional[Dict[str, Any]],
    requested_provider: Optional[str],
    requested_model: Optional[str],
    credit_available_usd: float = -1.0,
) -> Optional[Dict[str, Any]]:
    availability = _coerce_dict(availability_payload)
    if _text(availability.get("credential_plane")).lower() != "platform_runtime":
        return None
    if not bool(availability.get("platform_runtime_allowed")):
        raise RuntimeError("Hosted AI platform runtime is not allowed for this request.")
    workspace_token = _text(workspace_id)
    request_id = _session_request_id(session_ctx, _text(thread_id))
    tenant_id = _session_tenant_id(session_ctx, workspace_token) if workspace_token else None
    if not workspace_token or not request_id or not tenant_id:
        raise RuntimeError("Hosted AI reservation is missing tenant/workspace/request scope.")
    amount_usd = max(float(_HOSTED_AI_PREFLIGHT_RESERVATION_USD or 0), 0.0)
    max_active_usd = max(float(_HOSTED_AI_MAX_ACTIVE_RESERVATION_USD or 0), amount_usd)
    source_surface = "sage_direct_chat"
    key = _reservation_key(
        tenant_id=tenant_id,
        workspace_id=workspace_token,
        request_id=request_id,
        source_surface=source_surface,
    )
    # ── Read credit state BEFORE the transaction (close enough to atomic) ──
    # credit_available_usd < 0 means "not provided" (sentinel); resolve it.
    # 0.0 means "explicitly zero" → hard stop below.
    if credit_available_usd < 0:
        try:
            from server_modules.entitlements_service import (
                hosted_sage_ai_access_state_for_workspace_id as _resolve_credit,
            )
            _access = _resolve_credit(workspace_id=workspace_token)
            credit_available_usd = float(
                _coerce_dict(_access).get("total_available_usd", 0) or 0
            )
        except Exception:
            credit_available_usd = 0.0
    available = max(0.0, round(float(credit_available_usd), 6))
    now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    with _reservation_connection() as connection:
        _ensure_reservation_schema(connection)
        connection.execute("BEGIN IMMEDIATE")
        try:
            existing = connection.execute(
                "SELECT reservation_key, status, amount_usd FROM hosted_ai_reservations WHERE reservation_key = ?",
                (key,),
            ).fetchone()
            if existing is not None:
                status = _text(existing["status"]).lower()
                connection.commit()
                if status == "active":
                    return {
                        "reservation_key": key,
                        "amount_usd": float(existing["amount_usd"] or 0.0),
                        "status": status,
                    }
                if status in {"settled", "released"}:
                    return None
                raise RuntimeError("Hosted AI reservation has an uncertain previous outcome.")
            active_total = float(
                connection.execute(
                    """
                    SELECT COALESCE(SUM(amount_usd), 0) AS total
                    FROM hosted_ai_reservations
                    WHERE workspace_id = ? AND status = 'active'
                    """,
                    (workspace_token,),
                ).fetchone()["total"]
                or 0.0
            )
            # ── Credit balance gate — atomic with hold creation ──
            # active_total includes holds from concurrent calls.  If
            # available + active_total + amount_usd would exceed the
            # balance, block the call BEFORE the LLM is invoked.
            if available <= 0 or available - active_total - amount_usd < 0:
                connection.commit()
                # The canonical constant, not a hand-retyped copy — this was
                # the THIRD independent hardcoded copy of the same sentence
                # found 2026-08-19 (alongside entitlements_service.py and
                # sage_command_dispatcher.py's own SAGE_AI_LIMIT_MESSAGE),
                # which is exactly what test_credit_exhaustion_message_is_
                # present's own comment warns a hand-typed copy causes: this
                # one kept the pre-rewording "Open AI & Setup →" text (a
                # dead click affordance on this plain-text surface) after
                # the canonical message dropped it.
                raise RuntimeError(AI_LIMIT_REACHED_WEB.detail)
            if active_total + amount_usd > max_active_usd:
                connection.commit()
                raise RuntimeError("Hosted AI preflight reservation cap exceeded.")
            connection.execute(
                """
                INSERT INTO hosted_ai_reservations (
                    reservation_key, tenant_id, workspace_id, request_id, source_surface,
                    amount_usd, status, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, 'active', ?, ?)
                """,
                (key, tenant_id, workspace_token, request_id, source_surface, amount_usd, now, now),
            )
            connection.commit()
        except Exception:
            connection.rollback()
            raise
    return {
        "reservation_key": key,
        "amount_usd": amount_usd,
        "status": "active",
        "requested_provider": _text(requested_provider) or None,
        "requested_model": _text(requested_model) or None,
    }


def release_direct_chat_hosted_usage_reservation_best_effort(
    *,
    workspace_id: str,
    thread_id: str,
    session_ctx: Optional[Dict[str, Any]],
    status: str = "released",
) -> None:
    workspace_token = _text(workspace_id)
    request_id = _session_request_id(session_ctx, _text(thread_id))
    tenant_id = _session_tenant_id(session_ctx, workspace_token) if workspace_token else None
    if not workspace_token or not request_id or not tenant_id:
        return
    key = _reservation_key(
        tenant_id=tenant_id,
        workspace_id=workspace_token,
        request_id=request_id,
        source_surface="sage_direct_chat",
    )
    normalized_status = _text(status).lower()
    if normalized_status not in {"released", "settled", "uncertain"}:
        normalized_status = "released"
    now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    with _reservation_connection() as connection:
        _ensure_reservation_schema(connection)
        connection.execute(
            """
            UPDATE hosted_ai_reservations
            SET status = ?, updated_at = ?
            WHERE reservation_key = ? AND status = 'active'
            """,
            (normalized_status, now, key),
        )


def _session_turn_metadata(session_ctx: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    payload = _coerce_dict(session_ctx)
    turn_request = _coerce_dict(payload.get("agent_turn_request"))
    context_hints = _coerce_dict(turn_request.get("context_hints"))
    metadata = _coerce_dict(context_hints.get("metadata"))
    if metadata:
        return metadata
    return _coerce_dict(payload.get("metadata"))


def _non_platform_direct_chat_payer(availability: Dict[str, Any], provider: Optional[str]) -> str:
    if not isinstance(availability, dict):
        raise RuntimeError("Direct chat non-platform usage requires an AI source payload.")
    plane = _text(availability.get("credential_plane")).lower()
    source = _text(availability.get("billing_source") or availability.get("ai_source_kind")).lower()
    provider_token = _text(provider).lower()
    if not plane and not source:
        raise RuntimeError("Direct chat non-platform usage requires a known AI source.")
    if plane == "platform_runtime":
        raise RuntimeError("Direct chat platform runtime usage cannot be recorded as non-platform usage.")
    if provider_token in {"codex_cli", "claude_code_cli"} or "subscription" in plane or "subscription" in source:
        return "subscription_passthrough"
    if provider_token in {"ollama", "local", "local_model"} or "local" in plane or "local" in source:
        return "local"
    if "workspace" not in plane and "byok" not in plane and "api_key" not in source and "workspace_api_key" not in source:
        raise RuntimeError("Direct chat non-platform usage requires BYOK, local, or subscription source.")
    return "BYOK"


def _record_usage_event_best_effort(
    *,
    provider: Optional[str],
    model: Optional[str],
    tokens_in: int,
    tokens_out: int,
    run_id: Optional[str],
    mode: Optional[str],
    usd_cost: Optional[float] = None,
) -> None:
    """Fire-and-forget a normalized usage_events row. Never raises, never blocks
    the caller — metering must not be able to break a chat turn."""
    try:
        from server_modules import usage_events_repository as _uev
        import asyncio as _aio_uev

        _coro = _uev.record_usage_from_context(
            provider=provider, model=model, tokens_in=tokens_in, tokens_out=tokens_out,
            usd_cost=usd_cost, run_id=run_id, mode=mode,
        )
        try:
            _aio_uev.get_running_loop().create_task(_coro)
        except RuntimeError:
            _aio_uev.run(_coro)
    except Exception:
        pass


def _record_direct_chat_transparency_usage(
    *,
    workspace_id: str,
    thread_id: str,
    session_ctx: Optional[Dict[str, Any]],
    availability: Dict[str, Any],
    usage: Dict[str, Any],
    requested_provider: Optional[str],
    effective_provider: Optional[str],
    requested_model: Optional[str],
    effective_model: Optional[str],
) -> None:
    workspace_token = _text(workspace_id)
    if not workspace_token:
        return
    raw_thread_token = _text(thread_id)
    request_id = _session_request_id(session_ctx, raw_thread_token)
    if not request_id:
        return
    tenant_id = _session_tenant_id(session_ctx, workspace_token)
    if not tenant_id:
        return
    timestamp = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    provider = _text(effective_provider) or _text(requested_provider) or _text(usage.get("provider"))
    model = _text(effective_model) or _text(requested_model) or _text(usage.get("model"))
    snapshot = {
        "usage_masked": {
            **usage,
            "provider": provider,
            "model": model,
            "requested_provider": _text(requested_provider) or None,
            "requested_model": _text(requested_model) or None,
            "surface": "sage",
            "source_surface": "sage_direct_chat",
            "payer": _non_platform_direct_chat_payer(availability, provider),
            "timestamp": timestamp,
            "metadata": {
                **_coerce_dict(usage.get("metadata")),
                "thread_id": raw_thread_token or "direct-chat",
                "request_id": request_id,
                "credential_plane": _text(availability.get("credential_plane")) or None,
                "billing_source": _text(availability.get("billing_source")) or None,
                "transparency_only": True,
            },
        }
    }
    row = usage_accounting_service.usage_row_from_snapshot(snapshot) if usage else None
    payer = _non_platform_direct_chat_payer(availability, provider)

    # Phase 5A: normalized per-call usage_events row (all payers). Best-effort;
    # attribution (agent_install_id/project_id) comes from the turn contextvar.
    _mode_map = {"platform": "platform_credits", "platform_credits": "platform_credits",
                 "byok": "byok", "local": "local", "subscription_passthrough": "no_provider"}
    _record_usage_event_best_effort(
        provider=provider, model=model,
        tokens_in=int(usage.get("input_tokens") or usage.get("prompt_tokens") or 0),
        tokens_out=int(usage.get("output_tokens") or usage.get("completion_tokens") or 0),
        run_id=request_id, mode=_mode_map.get(payer, payer),
    )
    unified_ledger_event = credit_ledger_contract.build_unified_credit_ledger_event(
        surface="sage",
        source_surface="sage_direct_chat",
        payer=payer,
        credit_type="ai_tokens",
        provider=provider,
        model=model,
        runtime_target="local_companion" if payer in {"local", "subscription_passthrough"} else "cloud_default",
        workspace_id=workspace_token,
        user_id=_text(_session_turn_metadata(session_ctx).get("user_id")),
        thread_id=raw_thread_token or "direct-chat",
        run_id=request_id,
        provider_usage=(row.get("usage_accounting") if isinstance(row, dict) and isinstance(row.get("usage_accounting"), dict) else row) or {},
        platform_cost_usd=0,
        provider_reported_cost=(row or {}).get("provider_cost_usd") if isinstance(row, dict) else None,
        provider_reported_currency="USD" if isinstance(row, dict) and (row.get("provider_cost_usd") is not None) else None,
        credits_debited=billing_credit_config.credits_for_byo_usage_cost_usd(
            (row or {}).get("provider_cost_usd") if isinstance(row, dict) else None
        ),
        estimation_mode=(row or {}).get("estimation_mode") if isinstance(row, dict) else "provider_usage_missing",
        created_at=(row or {}).get("completed_at") if isinstance(row, dict) else timestamp,
        metadata={
            "requested_provider": _text(requested_provider) or None,
            "requested_model": _text(requested_model) or None,
            "credential_plane": _text(availability.get("credential_plane")) or None,
            "billing_source": _text(availability.get("billing_source")) or None,
            "transparency_only": True,
            "label": "Used your AI source" if payer == "BYOK" else "Used non-Empyralis AI source",
        },
    )
    durable_event = run_async_tool_call(
        control_plane_repository.record_credit_ledger_event(
            tenant_id=tenant_id,
            workspace_id=workspace_token,
            event=unified_ledger_event,
            source_table="direct_chat_transparency",
            source_event_id=request_id,
        )
    )
    if not isinstance(durable_event, dict):
        raise RuntimeError("Direct chat transparency credit ledger persistence failed.")


def persist_direct_chat_hosted_usage_best_effort(
    *,
    workspace_id: str,
    thread_id: str,
    session_ctx: Optional[Dict[str, Any]],
    availability_payload: Optional[Dict[str, Any]],
    usage_masked: Optional[Dict[str, Any]],
    requested_provider: Optional[str],
    effective_provider: Optional[str],
    requested_model: Optional[str],
    effective_model: Optional[str],
) -> None:
    availability = _coerce_dict(availability_payload)
    usage = _coerce_dict(usage_masked)
    # ── Skip debit for empty / failed replies ──
    # If the LLM returned no meaningful tokens, the user should not be
    # charged.  Release the hold but don't run the debit.
    _total_tokens = (
        usage.get("total_tokens")
        or _coerce_dict(usage.get("usage_accounting")).get("total_tokens")
        or 0
    )
    if not _total_tokens or int(_total_tokens) <= 0:
        release_direct_chat_hosted_usage_reservation_best_effort(
            workspace_id=workspace_id,
            thread_id=thread_id,
            session_ctx=session_ctx,
            status="released",
        )
        return
    if _text(availability.get("credential_plane")).lower() != "platform_runtime":
        if not availability:
            raise RuntimeError("Direct chat usage is missing a known AI source for credit accounting.")
        try:
            _record_direct_chat_transparency_usage(
                workspace_id=workspace_id,
                thread_id=thread_id,
                session_ctx=session_ctx,
                availability=availability,
                usage=usage,
                requested_provider=requested_provider,
                effective_provider=effective_provider,
                requested_model=requested_model,
                effective_model=effective_model,
            )
        except Exception:
            pass
        return
    if not bool(availability.get("platform_runtime_allowed")):
        raise RuntimeError("Hosted AI platform runtime usage was not allowed for credit accounting.")
    if not usage:
        raise RuntimeError("Hosted AI usage is missing for platform credit accounting.")

    workspace_token = _text(workspace_id)
    if not workspace_token:
        raise RuntimeError("Hosted AI usage is missing workspace scope for credit accounting.")
    raw_thread_token = _text(thread_id)
    request_id = _session_request_id(session_ctx, raw_thread_token)
    if not request_id:
        raise RuntimeError("Hosted AI usage is missing request id for credit accounting.")
    thread_token = raw_thread_token or "direct-chat"
    tenant_id = _session_tenant_id(session_ctx, workspace_token)
    if not tenant_id:
        raise RuntimeError("Hosted AI usage is missing tenant scope for credit accounting.")

    timestamp = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    turn_metadata = _session_turn_metadata(session_ctx)
    tier_route = empyralis_model_tier_routing_service.resolve_requested_empyralis_tier(
        requested_provider=requested_provider,
        requested_model=requested_model,
        metadata=turn_metadata,
    )
    line_item_metadata = credit_ledger_contract.build_credit_ledger_line_item(
        metadata={
            "public_tier": _text(turn_metadata.get("public_tier") or turn_metadata.get("model_tier") or turn_metadata.get("empyralis_model_tier")),
            "billing_source": _text(
                turn_metadata.get("billing_source")
                or (tier_route.get("billing_source") if isinstance(tier_route, dict) else "")
            ),
        },
        public_tier=(tier_route or {}).get("public_tier") if isinstance(tier_route, dict) else None,
        billing_source=(tier_route or {}).get("billing_source") if isinstance(tier_route, dict) else None,
        credit_multiplier=(tier_route or {}).get("credit_multiplier") if isinstance(tier_route, dict) else None,
        total_tokens=usage.get("total_tokens")
        or _coerce_dict(usage.get("usage_accounting")).get("total_tokens"),
    )
    snapshot = {
        "run_id": request_id,
        "workspace_id": workspace_token,
        "tenant_id": tenant_id,
        "completed_at": timestamp,
        "context": {
            "workspace_id": workspace_token,
            "tenant_id": tenant_id,
            "metadata": {
                "thread_id": thread_token,
                "request_id": request_id,
                "credential_plane": "platform_runtime",
                "surface": "sage",
                "source_surface": "sage_direct_chat",
            },
        },
    }
    if isinstance(usage.get("usage_accounting"), dict):
        snapshot["usage_accounting"] = {
            **_coerce_dict(usage.get("usage_accounting")),
            "run_id": request_id,
            "tenant_id": tenant_id,
            "workspace_id": workspace_token,
            "surface": "sage",
            "source_surface": "sage_direct_chat",
            "requested_provider": _text(requested_provider) or _coerce_dict(usage.get("usage_accounting")).get("requested_provider"),
            "effective_provider": _text(effective_provider) or _coerce_dict(usage.get("usage_accounting")).get("effective_provider"),
            "requested_model": _text(requested_model) or _coerce_dict(usage.get("usage_accounting")).get("requested_model"),
            "effective_model": _text(effective_model) or _coerce_dict(usage.get("usage_accounting")).get("effective_model"),
            "completed_at": timestamp,
            "metadata": {
                **_coerce_dict(_coerce_dict(usage.get("usage_accounting")).get("metadata")),
                "thread_id": thread_token,
                "request_id": request_id,
                "credential_plane": "platform_runtime",
                "billing_source": line_item_metadata.get("billing_source"),
                "public_tier": line_item_metadata.get("public_tier"),
                "credit_item_type": line_item_metadata.get("credit_item_type"),
                "credit_quantity": line_item_metadata.get("quantity"),
                "credit_quantity_unit": line_item_metadata.get("quantity_unit"),
                "credit_multiplier": line_item_metadata.get("credit_multiplier"),
            },
        }
    else:
        snapshot["usage_masked"] = {
            **usage,
            "provider": _text(effective_provider) or usage.get("provider"),
            "model": _text(effective_model) or usage.get("model"),
            "requested_provider": _text(requested_provider) or usage.get("requested_provider"),
            "requested_model": _text(requested_model) or usage.get("requested_model"),
            "surface": "sage",
            "source_surface": "sage_direct_chat",
            "timestamp": timestamp,
            "metadata": {
                **_coerce_dict(usage.get("metadata")),
                "thread_id": thread_token,
                "request_id": request_id,
                "credential_plane": "platform_runtime",
                "billing_source": line_item_metadata.get("billing_source"),
                "public_tier": line_item_metadata.get("public_tier"),
                "credit_item_type": line_item_metadata.get("credit_item_type"),
                "credit_quantity": line_item_metadata.get("quantity"),
                "credit_quantity_unit": line_item_metadata.get("quantity_unit"),
                "credit_multiplier": line_item_metadata.get("credit_multiplier"),
            },
        }

    row = usage_accounting_service.usage_row_from_snapshot(snapshot)
    if not isinstance(row, dict):
        raise RuntimeError("Hosted AI usage could not be normalized for credit accounting.")
    # Record ground-truth usage before any pricing/ledger step that can raise below,
    # so metering survives even if credit accounting itself fails.
    _record_usage_event_best_effort(
        provider=row.get("provider"), model=row.get("model"),
        tokens_in=int(row.get("prompt_tokens") or 0), tokens_out=int(row.get("completion_tokens") or 0),
        usd_cost=row.get("estimated_cost_usd"), run_id=request_id, mode="platform_credits",
    )
    validation_error = usage_accounting_service.platform_paid_usage_validation_error(row)
    if validation_error:
        reason = "unknown pricing" if validation_error == "unknown_pricing" else validation_error
        raise RuntimeError(
            "Hosted AI usage is not exact enough for platform credit accounting "
            f"({reason}) for {row.get('provider') or 'unknown'}:{row.get('model') or 'unknown'}."
        )
    unified_ledger_event = credit_ledger_contract.build_unified_credit_ledger_event(
        surface="sage",
        source_surface="sage_direct_chat",
        payer="platform_credits",
        credit_type=line_item_metadata.get("credit_type") or "ai_tokens",
        provider=row.get("provider"),
        model=row.get("model"),
        runtime_target="cloud_default",
        workspace_id=workspace_token,
        user_id=_text(_session_turn_metadata(session_ctx).get("user_id")),
        thread_id=thread_token,
        run_id=request_id,
        provider_usage=row.get("usage_accounting") if isinstance(row.get("usage_accounting"), dict) else row,
        platform_cost_usd=row.get("estimated_cost_usd"),
        provider_reported_cost=row.get("provider_cost_usd") or row.get("estimated_cost_usd"),
        provider_reported_currency="USD",
        credits_debited=row.get("retail_credits_charged"),
        estimation_mode=row.get("estimation_mode"),
        created_at=row.get("completed_at") or timestamp,
    )

    try:
        ledger_entry = run_async_tool_call(
            control_plane_repository.record_workspace_hosted_ai_monthly_cost_ledger_entry(
                tenant_id=tenant_id,
                workspace_id=workspace_token,
                request_id=request_id,
                thread_id=thread_token,
                source_surface="sage_direct_chat",
                provider=row.get("provider"),
                model=row.get("model"),
                prompt_tokens=int(row.get("prompt_tokens") or 0),
                completion_tokens=int(row.get("completion_tokens") or 0),
                total_tokens=int(row.get("total_tokens") or 0),
                estimated_cost_usd=float(row.get("estimated_cost_usd") or 0.0),
                completed_at=row.get("completed_at") or timestamp,
                metadata={
                    **_coerce_dict(row.get("metadata")),
                    "credential_plane": "platform_runtime",
                    "requested_provider": _text(requested_provider) or None,
                    "requested_model": _text(requested_model) or None,
                    "billing_source": line_item_metadata.get("billing_source"),
                    "credit_type": line_item_metadata.get("credit_type"),
                    "public_tier": line_item_metadata.get("public_tier"),
                    "credit_item_type": line_item_metadata.get("credit_item_type"),
                    "credit_quantity": line_item_metadata.get("quantity"),
                    "credit_quantity_unit": line_item_metadata.get("quantity_unit"),
                    "credit_multiplier": line_item_metadata.get("credit_multiplier"),
                    "unified_credit_ledger_event": unified_ledger_event,
                },
            )
        )
    except Exception as exc:
        raise RuntimeError("Hosted AI usage cost ledger persistence failed.") from exc
    if not isinstance(ledger_entry, dict):
        raise RuntimeError("Hosted AI usage cost ledger persistence failed.")
    try:
        durable_event = run_async_tool_call(
            control_plane_repository.record_credit_ledger_event(
                tenant_id=tenant_id,
                workspace_id=workspace_token,
                event=unified_ledger_event,
                source_table="workspace_hosted_ai_monthly_cost_ledger",
                source_event_id=_text(ledger_entry.get("id")) or request_id,
            )
        )
    except Exception as exc:
        raise RuntimeError("Hosted AI unified credit ledger persistence failed.") from exc
    if not isinstance(durable_event, dict):
        raise RuntimeError("Hosted AI unified credit ledger persistence failed.")
    try:
        from server_modules import billing_service

        # MAN-108 Bug 3 fix (2026-07-28): this call site is the PRIMARY debit
        # path -- it fires on every normal live chat turn that goes through
        # _run_sage_action_loop_v3 -> stream_provider_backed_direct_chat's
        # success/error/loop-detected completion handlers (see
        # agent_turn_runtime_service.py's `_turn_credit_idempotency_key`
        # docstring for the two-path map). It used to call
        # billing_service.debit_workspace_credit_balance_for_hosted_usage,
        # which only draws down credit_balance_usd once a workspace's
        # CUMULATIVE MONTHLY cost exceeds hosted_sage_ai_monthly_cap_usd (see
        # control_plane_repository.py's "Direct per-turn credit debit"
        # section) -- a legacy monthly-cap-overage mechanic that stays inert
        # for any workspace under the (default $5.00) monthly cap, i.e. for
        # ~all normal usage. That made this call site a de facto no-op:
        # ground-truth cost was faithfully metered (the ledger writes above)
        # but credit_balance_usd never moved, so the Billing page showed $0
        # usage despite real accruing LLM cost. The OTHER debit path (the
        # "cloud fallthrough" block in agent_turn_runtime_service.handle_
        # sage_chat) already calls the correct, MAN-74-reconnected per-turn
        # primitive -- but that block is only reached when the action loop
        # returns None, which real turns essentially never do (handle_sage_
        # chat returns immediately after a non-None action_result). Switching
        # this call site to the SAME real primitive, with the SAME shared
        # request_id (computed above via _session_request_id, which reads
        # exactly the turn_credit_idempotency_key agent_turn_runtime_service
        # threads into session_ctx), makes the primary path actually debit
        # while preserving idempotency/no-double-charge against the fallback
        # path via the shared credit_transactions ledger dedup.
        _credits_owed = billing_credit_config.credits_for_turn_cost_usd(row.get("estimated_cost_usd"))
        if _credits_owed > 0:
            debit_result = billing_service.debit_workspace_credits_for_turn(
                workspace_id=workspace_token,
                tenant_id=tenant_id,
                request_id=request_id,
                credits_to_charge=_credits_owed,
                floor_usd=billing_credit_config.NEW_ACCOUNT_SIGNUP_CREDIT_USD,
            )
        else:
            debit_result = {"ok": True, "credits_debited": 0, "debited_usd": 0.0, "reason": "no_debit_needed"}
    except Exception as exc:
        raise RuntimeError("Hosted AI credit debit failed.") from exc
    debit_payload = _coerce_dict(debit_result)
    if debit_payload.get("ok") is not True:
        reason = (
            _text(debit_payload.get("error"))
            or _text(debit_payload.get("reason"))
            or _text(debit_payload.get("code"))
            or "unknown debit failure"
        )
        raise RuntimeError(f"Hosted AI credit debit failed: {reason}.")
    if debit_payload.get("insufficient"):
        import logging

        logging.getLogger(__name__).warning(
            "credit_debit: workspace=%s ran short covering %s credits (only %s debited) "
            "for request_id=%s -- turn was NOT blocked.",
            workspace_token, _credits_owed, debit_payload.get("credits_debited"), request_id,
        )
    release_direct_chat_hosted_usage_reservation_best_effort(
        workspace_id=workspace_token,
        thread_id=thread_token,
        session_ctx=session_ctx,
        status="settled",
    )
