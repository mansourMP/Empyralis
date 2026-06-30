from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
import json
from typing import Any, AsyncIterator, Dict, Optional

from server_modules import control_plane_repository
from server_modules.db import asyncpg
from server_modules import quota_policy_service, quota_response_service
from server_modules import rust_runtime_kernel_client


_WORKSPACE_DEFAULT_MAX_ACTIVE_THREADS = 24
_AGENT_DEFAULT_MAX_ACTIVE_THREADS = 8
_WORKSPACE_DEFAULT_MAX_TURNS_PER_MINUTE = 180
_DEFAULT_MAX_RUNTIME_SECONDS = 45
_MIN_RUNTIME_SECONDS = 5
_MAX_RUNTIME_SECONDS = 300
_MIN_CONCURRENCY = 1
_MAX_CONCURRENCY = 1000
_MAX_TURNS_PER_MINUTE = 10_000
_THREAD_LEASE_CONSTRAINT = "uq_agent_channel_execution_leases_active_thread"


class ChannelExecutionLimitError(Exception):
    def __init__(
        self,
        *,
        reason: str,
        message: str,
        retry_after_seconds: int,
        quota_snapshot: "ChannelQuotaSnapshot",
    ) -> None:
        super().__init__(message)
        self.reason = str(reason or "workspace_busy").strip() or "workspace_busy"
        self.message = str(message or "The system is busy.").strip() or "The system is busy."
        self.retry_after_seconds = max(int(retry_after_seconds or 1), 1)
        self.quota_snapshot = quota_snapshot


class ChannelConcurrencyRustGateError(RuntimeError):
    pass


@dataclass(frozen=True)
class ChannelQuotaSnapshot:
    max_workspace_active_threads: int
    max_agent_active_threads: int
    max_workspace_turns_per_minute: int
    max_runtime_seconds: int

    def as_dict(self) -> Dict[str, int]:
        return asdict(self)


def _coerce_dict(value: Any) -> Dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _payload_size_bytes(value: Any) -> int:
    try:
        return len(json.dumps(value, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8"))
    except Exception:
        return len(str(value or "").encode("utf-8"))


def _enforce_channel_execution_lease_transition(
    *,
    operation: str,
    tenant_id: str,
    workspace_id: str,
    lease_id: str = "",
    responder_install_id: Optional[str] = None,
    thread_id: str = "",
    session_key: str = "",
    channel_key: str = "",
    endpoint_key: str = "",
    quota_snapshot: Optional["ChannelQuotaSnapshot"] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    payload = {
        "tenant_id": str(tenant_id or "").strip(),
        "workspace_id": str(workspace_id or "").strip(),
        "lease_id": str(lease_id or "").strip(),
        "responder_install_id": str(responder_install_id or "").strip(),
        "thread_id": str(thread_id or "").strip(),
        "session_key": str(session_key or "").strip(),
        "channel_key": str(channel_key or "").strip().lower(),
        "endpoint_key": str(endpoint_key or "").strip().lower(),
        "quota_snapshot": quota_snapshot.as_dict() if isinstance(quota_snapshot, ChannelQuotaSnapshot) else {},
        "metadata": dict(metadata or {}),
    }
    try:
        decision = rust_runtime_kernel_client.runtime_state_store_decision(
            operation=operation,
            state_class="channel_execution_leases",
            tenant_id=str(payload["tenant_id"] or "default").strip() or "default",
            workspace_id=str(payload["workspace_id"]),
            actor_id="system",
            status="active",
            payload=payload,
            payload_bytes=_payload_size_bytes(payload),
            workspace_access=True,
            owner_access=True,
        )
        rust_runtime_kernel_client.enforce_kernel_decision(
            "runtime-state-store-decision",
            decision,
        )
        next_action = str(decision.get("next_action") or "").strip()
        if next_action != str(operation or "").strip():
            raise ChannelConcurrencyRustGateError("unexpected_next_action")
        return decision
    except rust_runtime_kernel_client.RustKernelDecisionError as exc:
        raise ChannelConcurrencyRustGateError(exc.reason) from exc


def _coerce_int(
    value: Any,
    default: int,
    *,
    minimum: int,
    maximum: int,
) -> int:
    try:
        resolved = int(value)
    except (TypeError, ValueError):
        resolved = int(default)
    return max(minimum, min(maximum, resolved))


def _workspace_quota_metadata(workspace: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    metadata = _coerce_dict(_coerce_dict(workspace).get("metadata"))
    return {
        **_coerce_dict(metadata.get("concurrency")),
        **_coerce_dict(metadata.get("plan_limits")),
    }


def _agent_quota_metadata(install: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    metadata = _coerce_dict(_coerce_dict(install).get("metadata"))
    return {
        **_coerce_dict(metadata.get("concurrency")),
        **_coerce_dict(metadata.get("plan_limits")),
    }


def resolve_channel_quota_snapshot(
    *,
    workspace: Optional[Dict[str, Any]],
    install: Optional[Dict[str, Any]],
) -> ChannelQuotaSnapshot:
    workspace_meta = _workspace_quota_metadata(workspace)
    agent_meta = _agent_quota_metadata(install)

    max_workspace_active_threads = _coerce_int(
        workspace_meta.get("max_concurrent_customer_threads")
        or workspace_meta.get("max_workspace_active_threads"),
        _WORKSPACE_DEFAULT_MAX_ACTIVE_THREADS,
        minimum=_MIN_CONCURRENCY,
        maximum=_MAX_CONCURRENCY,
    )
    max_agent_active_threads = _coerce_int(
        agent_meta.get("max_active_threads")
        or workspace_meta.get("max_concurrent_threads_per_agent")
        or workspace_meta.get("max_agent_active_threads"),
        _AGENT_DEFAULT_MAX_ACTIVE_THREADS,
        minimum=_MIN_CONCURRENCY,
        maximum=_MAX_CONCURRENCY,
    )
    max_workspace_turns_per_minute = _coerce_int(
        workspace_meta.get("max_customer_turns_per_minute")
        or workspace_meta.get("max_workspace_turns_per_minute"),
        _WORKSPACE_DEFAULT_MAX_TURNS_PER_MINUTE,
        minimum=1,
        maximum=_MAX_TURNS_PER_MINUTE,
    )
    max_runtime_seconds = _coerce_int(
        agent_meta.get("max_runtime_seconds")
        or workspace_meta.get("max_customer_runtime_seconds")
        or workspace_meta.get("max_runtime_seconds"),
        _DEFAULT_MAX_RUNTIME_SECONDS,
        minimum=_MIN_RUNTIME_SECONDS,
        maximum=_MAX_RUNTIME_SECONDS,
    )
    return ChannelQuotaSnapshot(
        max_workspace_active_threads=max_workspace_active_threads,
        max_agent_active_threads=max_agent_active_threads,
        max_workspace_turns_per_minute=max_workspace_turns_per_minute,
        max_runtime_seconds=max_runtime_seconds,
    )


def _thread_busy_error(quota_snapshot: ChannelQuotaSnapshot) -> ChannelExecutionLimitError:
    return ChannelExecutionLimitError(
        reason="thread_busy",
        message=quota_response_service.channel_reply_for_reason("thread_busy"),
        retry_after_seconds=2,
        quota_snapshot=quota_snapshot,
    )


def _agent_limit_error(quota_snapshot: ChannelQuotaSnapshot) -> ChannelExecutionLimitError:
    return ChannelExecutionLimitError(
        reason="agent_limit_exceeded",
        message=quota_response_service.channel_reply_for_reason("agent_limit_exceeded"),
        retry_after_seconds=5,
        quota_snapshot=quota_snapshot,
    )


def _workspace_limit_error(quota_snapshot: ChannelQuotaSnapshot) -> ChannelExecutionLimitError:
    return ChannelExecutionLimitError(
        reason="workspace_limit_exceeded",
        message=quota_response_service.channel_reply_for_reason("workspace_limit_exceeded"),
        retry_after_seconds=5,
        quota_snapshot=quota_snapshot,
    )


def _workspace_rate_limit_error(quota_snapshot: ChannelQuotaSnapshot) -> ChannelExecutionLimitError:
    return ChannelExecutionLimitError(
        reason="workspace_rate_limited",
        message=quota_response_service.channel_reply_for_reason("workspace_rate_limited"),
        retry_after_seconds=10,
        quota_snapshot=quota_snapshot,
    )


def build_limit_result(*, error: ChannelExecutionLimitError) -> Dict[str, Any]:
    decision = quota_policy_service.channel_limit_error_decision(
        subject=quota_policy_service.QuotaSubject(surface_kind="deployed_agent_channel"),
        error=error,
    )
    return quota_response_service.channel_payload_from_quota_decision(decision=decision)


def build_runtime_capped_result(*, quota_snapshot: ChannelQuotaSnapshot) -> Dict[str, Any]:
    decision = quota_policy_service.runtime_cap_quota_decision(
        subject=quota_policy_service.QuotaSubject(surface_kind="deployed_agent_channel"),
        quota_snapshot=quota_snapshot,
    )
    return quota_response_service.channel_payload_from_quota_decision(decision=decision)


def _is_thread_lease_conflict(error: Exception) -> bool:
    constraint_name = str(getattr(error, "constraint_name", "") or "").strip()
    if constraint_name == _THREAD_LEASE_CONSTRAINT:
        return True
    unique_error = getattr(asyncpg, "UniqueViolationError", None)
    if unique_error and isinstance(error, unique_error):
        return constraint_name == _THREAD_LEASE_CONSTRAINT
    return _THREAD_LEASE_CONSTRAINT in str(error).lower()


async def acquire_channel_execution_lease(
    *,
    tenant_id: str,
    workspace_id: str,
    responder_install_id: Optional[str],
    thread_id: str,
    session_key: str,
    channel_key: str,
    endpoint_key: str,
    workspace: Optional[Dict[str, Any]] = None,
    install: Optional[Dict[str, Any]] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    resolved_workspace = workspace or await control_plane_repository.get_workspace_by_id(workspace_id)
    quota_snapshot = resolve_channel_quota_snapshot(workspace=resolved_workspace, install=install)
    lease_id = f"lease_{uuid.uuid4().hex[:16]}"
    now_ts = datetime.now(timezone.utc)
    ttl_seconds = max(quota_snapshot.max_runtime_seconds + 15, 30)
    expires_at = now_ts + timedelta(seconds=ttl_seconds)
    _enforce_channel_execution_lease_transition(
        operation="acquire_channel_execution_lease",
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        lease_id=lease_id,
        responder_install_id=responder_install_id,
        thread_id=thread_id,
        session_key=session_key,
        channel_key=channel_key,
        endpoint_key=endpoint_key,
        quota_snapshot=quota_snapshot,
        metadata=metadata,
    )
    async with control_plane_repository._scoped_connection(tenant_id=tenant_id, workspace_id=workspace_id) as connection:
        if connection is None:
            raise _workspace_limit_error(quota_snapshot)
        async with connection.transaction():
            await connection.fetchval(
                "SELECT pg_advisory_xact_lock(hashtext($1), hashtext($2))",
                str(tenant_id or "").strip(),
                str(workspace_id or "").strip(),
            )
            await connection.execute(
                """
                DELETE FROM agent_channel_execution_leases
                WHERE tenant_id = $1
                  AND workspace_id = $2
                  AND expires_at <= NOW()
                """,
                str(tenant_id or "").strip(),
                str(workspace_id or "").strip(),
            )
            active_thread = await connection.fetchval(
                """
                SELECT 1
                FROM agent_channel_execution_leases
                WHERE tenant_id = $1
                  AND workspace_id = $2
                  AND thread_id = $3
                  AND expires_at > NOW()
                LIMIT 1
                """,
                str(tenant_id or "").strip(),
                str(workspace_id or "").strip(),
                str(thread_id or "").strip(),
            )
            if active_thread:
                raise _thread_busy_error(quota_snapshot)
            if str(responder_install_id or "").strip():
                active_agent_count = int(
                    await connection.fetchval(
                        """
                        SELECT COUNT(*)
                        FROM agent_channel_execution_leases
                        WHERE tenant_id = $1
                          AND workspace_id = $2
                          AND responder_install_id = $3
                          AND expires_at > NOW()
                        """,
                        str(tenant_id or "").strip(),
                        str(workspace_id or "").strip(),
                        str(responder_install_id or "").strip(),
                    )
                    or 0
                )
                if active_agent_count >= quota_snapshot.max_agent_active_threads:
                    raise _agent_limit_error(quota_snapshot)
            active_workspace_count = int(
                await connection.fetchval(
                    """
                    SELECT COUNT(*)
                    FROM agent_channel_execution_leases
                    WHERE tenant_id = $1
                      AND workspace_id = $2
                      AND expires_at > NOW()
                    """,
                    str(tenant_id or "").strip(),
                    str(workspace_id or "").strip(),
                )
                or 0
            )
            if active_workspace_count >= quota_snapshot.max_workspace_active_threads:
                raise _workspace_limit_error(quota_snapshot)
            recent_turn_count = int(
                await connection.fetchval(
                    """
                    SELECT COUNT(*)
                    FROM agent_channel_events
                    WHERE tenant_id = $1
                      AND workspace_id = $2
                      AND direction = 'inbound'
                      AND event_type = 'message'
                      AND created_at >= NOW() - INTERVAL '60 seconds'
                    """,
                    str(tenant_id or "").strip(),
                    str(workspace_id or "").strip(),
                )
                or 0
            )
            if recent_turn_count >= quota_snapshot.max_workspace_turns_per_minute:
                raise _workspace_rate_limit_error(quota_snapshot)
            try:
                await connection.execute(
                    """
                    INSERT INTO agent_channel_execution_leases (
                        id, tenant_id, workspace_id, responder_install_id, thread_id, session_key,
                        channel_key, endpoint_key, metadata, acquired_at, updated_at, expires_at
                    ) VALUES (
                        $1, $2, $3, $4, $5, $6,
                        $7, $8, $9::jsonb, $10::timestamptz, $10::timestamptz, $11::timestamptz
                    )
                    """,
                    lease_id,
                    str(tenant_id or "").strip(),
                    str(workspace_id or "").strip(),
                    str(responder_install_id or "").strip() or None,
                    str(thread_id or "").strip(),
                    str(session_key or "").strip(),
                    str(channel_key or "").strip().lower(),
                    str(endpoint_key or "").strip().lower(),
                    control_plane_repository._to_json(metadata, default={}),
                    now_ts,
                    expires_at,
                )
            except Exception as error:
                if _is_thread_lease_conflict(error):
                    raise _thread_busy_error(quota_snapshot) from error
                raise
    return {
        "lease_id": lease_id,
        "quota_snapshot": quota_snapshot,
        "expires_at": expires_at.isoformat(),
    }


async def release_channel_execution_lease(
    *,
    tenant_id: str,
    workspace_id: str,
    lease_id: str,
) -> None:
    if not str(lease_id or "").strip():
        return
    _enforce_channel_execution_lease_transition(
        operation="release_channel_execution_lease",
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        lease_id=lease_id,
    )
    async with control_plane_repository._scoped_connection(tenant_id=tenant_id, workspace_id=workspace_id) as connection:
        if connection is None:
            return
        await connection.execute(
            """
            DELETE FROM agent_channel_execution_leases
            WHERE id = $1
              AND tenant_id = $2
              AND workspace_id = $3
            """,
            str(lease_id or "").strip(),
            str(tenant_id or "").strip(),
            str(workspace_id or "").strip(),
        )


@asynccontextmanager
async def channel_execution_slot(
    *,
    tenant_id: str,
    workspace_id: str,
    responder_install_id: Optional[str],
    thread_id: str,
    session_key: str,
    channel_key: str,
    endpoint_key: str,
    workspace: Optional[Dict[str, Any]] = None,
    install: Optional[Dict[str, Any]] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> AsyncIterator[Dict[str, Any]]:
    lease = await acquire_channel_execution_lease(
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        responder_install_id=responder_install_id,
        thread_id=thread_id,
        session_key=session_key,
        channel_key=channel_key,
        endpoint_key=endpoint_key,
        workspace=workspace,
        install=install,
        metadata=metadata,
    )
    try:
        yield lease
    finally:
        await release_channel_execution_lease(
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            lease_id=str(lease.get("lease_id") or "").strip(),
        )
