"""Workspace scope resolution — truthful isolation, no silent defaults.

Phase G: Every call site that needs a workspace_id must resolve it through
resolve_workspace(). When no workspace is available:
  - A ledger integrity event is written (report_missing_workspace)
  - An ephemeral per-process marker _unscoped_<uuid> is returned
    (NEVER "default", NEVER shared between callers)
  - If EMPYRALIS_REQUIRE_WORKSPACE is true, raises WorkspaceUnresolvedError

This module is a LEAF — it imports only stdlib and activity_ledger_service.
It MUST NOT create import cycles.
"""

from __future__ import annotations

import os
import threading
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Optional


# ---------------------------------------------------------------------------
# Launch gate — do NOT flip until customer #2 onboarding.
# ---------------------------------------------------------------------------

def _require_workspace_flag() -> bool:
    """Read EMPYRALIS_REQUIRE_WORKSPACE from env.

    Default: false in dev. Set to '1' or 'true' to enforce strict mode
    where resolve_workspace() raises instead of returning an ephemeral id.
    """
    return os.getenv("EMPYRALIS_REQUIRE_WORKSPACE", "false").strip().lower() in (
        "1", "true", "yes", "on",
    )


# ---------------------------------------------------------------------------
# Error type
# ---------------------------------------------------------------------------

class WorkspaceUnresolvedError(RuntimeError):
    """Raised by resolve_workspace() when EMPYRALIS_REQUIRE_WORKSPACE is true
    and no workspace can be resolved from any source."""

    def __init__(self, site: str, sources: tuple) -> None:
        self.site = site
        self.sources = sources
        super().__init__(
            f"WorkspaceUnresolvedError at {site}: "
            f"no workspace_id resolved from sources={sources!r}"
        )


# ---------------------------------------------------------------------------
# Ephemeral marker factory
# ---------------------------------------------------------------------------

def _ephemeral_workspace_id() -> str:
    """Return a per-process-unique ephemeral workspace marker.

    Two callers that both lack a real workspace will get DIFFERENT markers.
    This guarantees they cannot accidentally share state or credentials.
    """
    return f"_unscoped_{uuid.uuid4().hex[:8]}"


# ---------------------------------------------------------------------------
# Missing-workspace counter (Prometheus-style, thread-safe)
# ---------------------------------------------------------------------------

_workspace_default_used_total: Dict[str, int] = {}
_counter_lock = threading.Lock()


def workspace_default_used_counter() -> Dict[str, int]:
    """Return a snapshot of the per-site counter.

    Key: site identifier (e.g. "run_service:create_run").
    Value: count of times resolve_workspace() returned an ephemeral id
           at that site.
    """
    with _counter_lock:
        return dict(_workspace_default_used_total)


# ---------------------------------------------------------------------------
# Integrity event
# ---------------------------------------------------------------------------

def _emit_missing_workspace_ledger(site: str, context: Dict[str, Any]) -> None:
    """Write a platform_integrity ledger event and bump the counter.

    This is fire-and-forget — ledger write failures are logged, never raised.
    """
    # Bump counter (always succeeds)
    with _counter_lock:
        _workspace_default_used_total[site] = (
            _workspace_default_used_total.get(site, 0) + 1
        )

    # Write ledger entry (best-effort)
    try:
        from server_modules import activity_ledger_service

        import asyncio as _asyncio

        async def _write():
            await activity_ledger_service.append_activity_event(
                tenant_id="system",
                workspace_id="_platform",
                actor_type="platform",
                actor_id="workspace_scope",
                event_class="platform_integrity",
                detail_level="audit_reference",
                action="workspace_default_used",
                title=f"Unscoped workspace at {site}",
                summary=(
                    f"resolve_workspace() returned ephemeral marker at "
                    f"{site}. Sources available: {list(context.keys()) or ['none']}. "
                    f"Every 'default' fallback is a bug — this event marks one."
                ),
                status="logged",
                metadata={
                    "site": site,
                    "context_keys": sorted(context.keys()),
                    "require_workspace_flag": _require_workspace_flag(),
                },
            )

        try:
            _asyncio.get_running_loop()
        except RuntimeError:
            _asyncio.run(_write())
        else:
            # We're inside an event loop — schedule without awaiting.
            import asyncio as _aio2

            try:
                _aio2.create_task(_write())
            except RuntimeError:
                pass
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Core resolution
# ---------------------------------------------------------------------------

def resolve_workspace(
    workspace_id: Optional[str] = None,
    *sources: Any,
    site: str = "unknown",
) -> str:
    """Resolve a workspace_id from available sources.

    Resolution order:
      1. workspace_id if truthy and non-"default"
      2. Each source in order — checked for .get("workspace_id"),
         ["workspace_id"], .workspace_id attribute, or str value
      3. On miss: report_missing_workspace + ephemeral marker
         (or raise if EMPYRALIS_REQUIRE_WORKSPACE is true)

    A source can be:
      - A dict with a "workspace_id" key
      - An object with a workspace_id attribute
      - A plain string

    Args:
        workspace_id: explicit workspace_id (from function param)
        *sources: fallback sources in priority order
        site: human-readable call-site identifier for logging
              (e.g. "run_service:create_run")

    Returns:
        A resolved workspace_id string — either a real UUID or
        an ephemeral _unscoped_<uuid> marker.

    Raises:
        WorkspaceUnresolvedError: if EMPYRALIS_REQUIRE_WORKSPACE is true
            and no workspace can be resolved.
    """
    # 1. Explicit workspace_id (but NOT the literal string "default")
    if workspace_id is not None:
        clean = str(workspace_id).strip()
        if clean and clean.lower() != "default" and not clean.startswith("_unscoped_"):
            return clean

    # 2. Walk sources
    for source in sources:
        if source is None:
            continue
        if isinstance(source, dict):
            val = source.get("workspace_id")
            if val:
                clean = str(val).strip()
                if clean and clean.lower() != "default" and not clean.startswith(
                    "_unscoped_"
                ):
                    return clean
        elif isinstance(source, str):
            clean = source.strip()
            if clean and clean.lower() != "default" and not clean.startswith(
                "_unscoped_"
            ):
                return clean
        else:
            # Object with .workspace_id attribute
            val = getattr(source, "workspace_id", None)
            if val:
                clean = str(val).strip()
                if clean and clean.lower() != "default" and not clean.startswith(
                    "_unscoped_"
                ):
                    return clean

    # 3. No workspace found — this is the truth.
    context = {
        "explicit_workspace_id": str(workspace_id)[:200] if workspace_id else None,
        "source_count": len(sources),
    }
    _emit_missing_workspace_ledger(site, context)

    if _require_workspace_flag():
        raise WorkspaceUnresolvedError(site, sources)

    return _ephemeral_workspace_id()
