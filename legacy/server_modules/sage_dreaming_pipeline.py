from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

from server_modules import rust_runtime_kernel_client, sage_memory_service, workspace_context


DREAMING_STAGING_DIR = ".dreams"
DEFAULT_SIMILARITY_THRESHOLD = 0.75
MAX_STAGING_FILES = 10
STAGING_TTL_DAYS = 7


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _staging_dir(workspace_id: str) -> Path:
    memory_dir = workspace_context.workspace_memory_dir(workspace_id)
    return memory_dir / DREAMING_STAGING_DIR


class SageDreamingRustGateError(RuntimeError):
    pass


def _enforce_sage_dreaming_state_decision(
    *,
    operation: str,
    workspace_id: str,
    path: Path,
    payload: Dict[str, Any],
) -> Dict[str, Any]:
    normalized_payload = payload if isinstance(payload, dict) else {}
    decision_payload = {
        "path": str(path),
        "stage": str(normalized_payload.get("stage") or "").strip(),
        "payload": normalized_payload,
    }
    try:
        decision = rust_runtime_kernel_client.runtime_state_store_decision(
            operation=operation,
            state_class="sage_dreaming",
            workspace_id=str(workspace_id or "").strip(),
            actor_id="system",
            status=str(normalized_payload.get("stage") or "active").strip() or "active",
            payload=decision_payload,
            payload_bytes=len(json.dumps(decision_payload, sort_keys=True, default=str).encode("utf-8")),
            workspace_access=True,
            owner_access=True,
        )
        decision = rust_runtime_kernel_client.enforce_kernel_decision(
            "runtime-state-store-decision",
            decision,
        )
        if str(decision.get("next_action") or "").strip() != str(operation or "").strip():
            raise SageDreamingRustGateError("unexpected_next_action")
        return decision
    except rust_runtime_kernel_client.RustKernelDecisionError as exc:
        raise SageDreamingRustGateError(exc.reason) from exc


def _write_dreaming_json(
    *,
    operation: str,
    workspace_id: str,
    path: Path,
    payload: Dict[str, Any],
) -> None:
    _enforce_sage_dreaming_state_decision(
        operation=operation,
        workspace_id=workspace_id,
        path=path,
        payload=payload,
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def _jaccard_similarity(a: str, b: str) -> float:
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    words_a = set(a.lower().split())
    words_b = set(b.lower().split())
    intersection = len(words_a & words_b)
    union = len(words_a | words_b)
    return intersection / union if union > 0 else 0.0


def _prune_staging(workspace_id: str) -> None:
    staging = _staging_dir(workspace_id)
    if not staging.exists():
        return
    files = sorted(staging.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    for stale in files[MAX_STAGING_FILES:]:
        stale.unlink(missing_ok=True)
    cutoff = datetime.now(timezone.utc).timestamp() - (STAGING_TTL_DAYS * 86400)
    for f in files:
        try:
            if f.stat().st_mtime < cutoff:
                f.unlink(missing_ok=True)
        except OSError:
            pass


def light_sleep(workspace_id: str) -> Dict[str, Any]:
    state = sage_memory_service._safe_read_json(
        sage_memory_service._state_file(workspace_id)
    )
    entries: List[Dict[str, Any]] = state.get("entries", [])

    merged = 0
    seen: Set[int] = set()
    deduped: List[Dict[str, Any]] = []

    for i, entry in enumerate(entries):
        if i in seen:
            continue
        content_i = str(entry.get("content", ""))
        for j in range(i + 1, len(entries)):
            if j in seen:
                continue
            content_j = str(entries[j].get("content", ""))
            similarity = _jaccard_similarity(content_i, content_j)
            if similarity >= DEFAULT_SIMILARITY_THRESHOLD and entries[i].get("category") == entries[j].get("category"):
                seen.add(j)
                merged += 1
        deduped.append(entry)

    result = {
        "stage": "light_sleep",
        "workspace_id": workspace_id,
        "timestamp": _utc_now_iso(),
        "entries_before": len(entries),
        "entries_after": len(deduped),
        "merged": merged,
        "deduped": merged > 0,
    }

    if merged > 0:
        state["entries"] = deduped
        state["updated_at"] = _utc_now_iso()
        target = sage_memory_service._state_file(workspace_id)
        _write_dreaming_json(
            operation="write_sage_dreaming_memory_state",
            workspace_id=workspace_id,
            path=target,
            payload=state,
        )

    _prune_staging(workspace_id)
    staging_file = _staging_dir(workspace_id) / f"light_sleep_{_utc_now_iso().replace(':', '-')}.json"
    _write_dreaming_json(
        operation="write_sage_dreaming_staging_file",
        workspace_id=workspace_id,
        path=staging_file,
        payload=result,
    )

    return result


def rem_sleep(workspace_id: str) -> Dict[str, Any]:
    state = sage_memory_service._safe_read_json(
        sage_memory_service._state_file(workspace_id)
    )
    entries: List[Dict[str, Any]] = state.get("entries", [])
    if len(entries) < 2:
        return {
            "stage": "rem_sleep",
            "workspace_id": workspace_id,
            "timestamp": _utc_now_iso(),
            "entries": len(entries),
            "cross_references": 0,
            "skipped": True,
            "reason": "too_few_entries",
        }

    cross_refs = 0
    for i, entry_a in enumerate(entries):
        cat_a = str(entry_a.get("category", ""))
        title_a = str(entry_a.get("title", ""))
        for j in range(i + 1, len(entries)):
            entry_b = entries[j]
            cat_b = str(entry_b.get("category", ""))
            if cat_a == cat_b:
                continue
            content_a = str(entry_a.get("content", ""))
            content_b = str(entry_b.get("content", ""))
            similarity = _jaccard_similarity(content_a, content_b)
            if similarity < 0.35:
                continue

            if not isinstance(entry_a.get("cross_refs"), list):
                entry_a["cross_refs"] = []
            ref_b = f"{entry_b.get('id', '')}:{title_b}"
            if ref_b not in entry_a["cross_refs"]:
                entry_a["cross_refs"].append(ref_b)
                cross_refs += 1

    result = {
        "stage": "rem_sleep",
        "workspace_id": workspace_id,
        "timestamp": _utc_now_iso(),
        "entries": len(entries),
        "cross_references": cross_refs,
    }

    if cross_refs > 0:
        state["entries"] = entries
        state["updated_at"] = _utc_now_iso()
        target = sage_memory_service._state_file(workspace_id)
        _write_dreaming_json(
            operation="write_sage_dreaming_memory_state",
            workspace_id=workspace_id,
            path=target,
            payload=state,
        )

    staging_file = _staging_dir(workspace_id) / f"rem_sleep_{_utc_now_iso().replace(':', '-')}.json"
    _write_dreaming_json(
        operation="write_sage_dreaming_staging_file",
        workspace_id=workspace_id,
        path=staging_file,
        payload=result,
    )

    return result


def deep_sleep(
    workspace_id: str,
    *,
    max_age_days: int = 30,
    max_entries: int = 50,
) -> Dict[str, Any]:
    state = sage_memory_service._safe_read_json(
        sage_memory_service._state_file(workspace_id)
    )
    entries: List[Dict[str, Any]] = state.get("entries", [])

    pruned = 0
    promoted = 0
    demoted = 0
    cutoff = datetime.now(timezone.utc).timestamp() - (max_age_days * 86400)
    kept: List[Dict[str, Any]] = []

    promotion_map = {
        "safe_general": "sensitive",
        "sensitive": "private",
    }
    demotion_map = {v: k for k, v in promotion_map.items()}

    for entry in entries:
        pinned = bool(entry.get("pinned", False))
        if pinned:
            kept.append(entry)
            continue

        updated_raw = str(entry.get("updated_at", ""))
        age_days = max_age_days + 1
        try:
            updated_ts = datetime.fromisoformat(updated_raw.replace("Z", "+00:00")).timestamp()
            age_days = (datetime.now(timezone.utc).timestamp() - updated_ts) / 86400
        except (ValueError, TypeError):
            pass

        if age_days > max_age_days:
            pruned += 1
            continue

        kept.append(entry)

    if len(kept) > max_entries:
        unpinned = [e for e in kept if not bool(e.get("pinned", False))]
        pinned_entries = [e for e in kept if bool(e.get("pinned", False))]
        overflow = len(unpinned) - (max_entries - len(pinned_entries))
        if overflow > 0:
            unpinned.sort(key=lambda e: str(e.get("updated_at", "")), reverse=True)
            kept = pinned_entries + unpinned[: max_entries - len(pinned_entries)]
            pruned += overflow

    result = {
        "stage": "deep_sleep",
        "workspace_id": workspace_id,
        "timestamp": _utc_now_iso(),
        "entries_before": len(entries),
        "entries_after": len(kept),
        "pruned": pruned,
        "promoted": promoted,
        "demoted": demoted,
    }

    if pruned > 0 or promoted > 0 or demoted > 0:
        state["entries"] = kept
        state["updated_at"] = _utc_now_iso()
        target = sage_memory_service._state_file(workspace_id)
        _write_dreaming_json(
            operation="write_sage_dreaming_memory_state",
            workspace_id=workspace_id,
            path=target,
            payload=state,
        )

    staging_file = _staging_dir(workspace_id) / f"deep_sleep_{_utc_now_iso().replace(':', '-')}.json"
    _write_dreaming_json(
        operation="write_sage_dreaming_staging_file",
        workspace_id=workspace_id,
        path=staging_file,
        payload=result,
    )

    return result


def run_dreaming_cycle(
    workspace_id: str,
    *,
    stages: Optional[List[str]] = None,
    max_age_days: int = 30,
    max_entries: int = 50,
) -> Dict[str, Any]:
    if stages is None:
        stages = ["light_sleep"]

    results: Dict[str, Any] = {
        "workspace_id": workspace_id,
        "started_at": _utc_now_iso(),
        "stages_run": stages,
    }

    for stage in stages:
        try:
            if stage == "light_sleep":
                results["light_sleep"] = light_sleep(workspace_id)
            elif stage == "rem_sleep":
                results["rem_sleep"] = rem_sleep(workspace_id)
            elif stage == "deep_sleep":
                results["deep_sleep"] = deep_sleep(
                    workspace_id, max_age_days=max_age_days, max_entries=max_entries
                )
        except Exception as exc:
            results[stage] = {"error": str(exc)}

    results["completed_at"] = _utc_now_iso()
    return results
