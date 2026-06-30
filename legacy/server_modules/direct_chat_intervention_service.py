from __future__ import annotations

from typing import Any, Dict, Optional


def build_intervention(
    kind: str,
    title: str,
    *,
    detail: Optional[str] = None,
    severity: str = "info",
    status: Optional[str] = None,
    code: Optional[str] = None,
    run_id: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    return {
        "kind": str(kind or "").strip() or "system_notice",
        "title": str(title or "").strip() or "System notice",
        "detail": str(detail or "").strip() or None,
        "severity": str(severity or "").strip() or "info",
        "status": str(status or "").strip() or None,
        "code": str(code or "").strip() or None,
        "run_id": str(run_id or "").strip() or None,
        "metadata": dict(metadata or {}),
    }
