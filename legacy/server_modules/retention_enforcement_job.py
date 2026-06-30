from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from server_modules import security_audit_service
from server_modules.data_retention_service import (
    RETENTION_CLASSES,
    DATA_STORE_CATALOG,
)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _coerce_text(value: Any) -> str:
    return str(value or "").strip()


@dataclass
class RetentionRunResult:
    dry_run: bool
    workspace_id: str
    started_at: str
    completed_at: str
    stores_checked: int
    records_eligible: int
    records_deleted: int
    errors: list[str] = field(default_factory=list)
    store_results: list[dict] = field(default_factory=list)


def _is_expired(created_at_str: Optional[str], retention_days: int) -> bool:
    if not created_at_str:
        return False
    try:
        created_at = datetime.fromisoformat(created_at_str.replace("Z", "+00:00"))
        cutoff = _utc_now() - timedelta(days=retention_days)
        return created_at < cutoff
    except (ValueError, TypeError):
        return False


def _evaluate_sage_memory_store(
    *,
    workspace_id: str,
    ttl_days: int,
    dry_run: bool,
) -> tuple[int, int]:
    from server_modules import sage_memory_service

    entries = sage_memory_service.list_sage_memory(workspace_id=workspace_id)
    sage_entries = (entries.get("items") or entries.get("entries") or []) if isinstance(entries, dict) else []
    eligible = 0
    deleted = 0
    for entry in sage_entries:
        if not isinstance(entry, dict):
            continue
        entry_created = entry.get("created_at") or entry.get("updated_at")
        if not _is_expired(entry_created, ttl_days):
            continue
        eligible += 1
        if dry_run:
            continue
        entry_id = entry.get("id")
        if not entry_id:
            continue
        try:
            sage_memory_service.delete_memory_entry(
                workspace_id=workspace_id,
                entry_id=str(entry_id),
                actor_user_id="retention_job",
            )
            deleted += 1
        except Exception:
            pass
    return eligible, deleted


async def evaluate_retention(
    *,
    tenant_id: str = "",
    workspace_id: str,
    dry_run: bool = True,
) -> RetentionRunResult:
    started_at = _utc_now().isoformat().replace("+00:00", "Z")
    result = RetentionRunResult(
        dry_run=dry_run,
        workspace_id=workspace_id,
        started_at=started_at,
        completed_at="",
        stores_checked=0,
        records_eligible=0,
        records_deleted=0,
    )

    try:
        from server_modules.data_retention_service import build_workspace_retention_inventory

        inventory = await build_workspace_retention_inventory(
            tenant_id=_coerce_text(tenant_id),
            workspace_id=workspace_id,
        )
    except Exception as exc:
        result.errors.append(f"inventory_failed: {exc}")
        result.completed_at = _utc_now().isoformat().replace("+00:00", "Z")
        return result

    stores = inventory.get("stores", [])
    if not isinstance(stores, list):
        stores = []

    for store in stores:
        if not isinstance(store, dict):
            continue
        result.stores_checked += 1
        store_id = store.get("store_id", "unknown")
        retention_policy = store.get("retention_policy", {})
        if not isinstance(retention_policy, dict):
            retention_policy = {}
        ttl_days = int(retention_policy.get("ttl_days", 365))
        row_count = int(store.get("row_count", 0))
        eligible_count = int(store.get("eligible_count") or store.get("expired_count") or 0)

        store_result = {
            "store_id": store_id,
            "ttl_days": ttl_days,
            "row_count": row_count,
            "eligible": eligible_count,
            "deleted": 0,
            "error": None,
        }
        result.records_eligible += eligible_count

        if store_id == "sage_memory" and store.get("supports_delete"):
            try:
                eligible, deleted = _evaluate_sage_memory_store(
                    workspace_id=workspace_id,
                    ttl_days=ttl_days,
                    dry_run=dry_run,
                )
                result.records_eligible += max(0, eligible - int(store_result["eligible"]))
                store_result["eligible"] = eligible
                store_result["deleted"] = deleted
                result.records_deleted += deleted
            except Exception as exc:
                store_result["error"] = str(exc)

        result.store_results.append(store_result)

    result.completed_at = _utc_now().isoformat().replace("+00:00", "Z")

    try:
        security_audit_service.emit_security_audit_event(
            action="retention.run_completed",
            status="success",
            workspace_id=workspace_id,
            detail=(
                f"Retention {'dry-run' if dry_run else 'apply'}: "
                f"{result.records_eligible} eligible, {result.records_deleted} deleted "
                f"across {result.stores_checked} stores"
            ),
            metadata={
                "dry_run": dry_run,
                "records_eligible": result.records_eligible,
                "records_deleted": result.records_deleted,
                "stores_checked": result.stores_checked,
                "errors": result.errors,
            },
        )
    except Exception:
        pass

    return result


async def run_retention_dry_run(*, tenant_id: str = "", workspace_id: str) -> RetentionRunResult:
    return await evaluate_retention(tenant_id=tenant_id, workspace_id=workspace_id, dry_run=True)


async def run_retention_apply(*, tenant_id: str = "", workspace_id: str) -> RetentionRunResult:
    return await evaluate_retention(tenant_id=tenant_id, workspace_id=workspace_id, dry_run=False)
