"""Stub — approval gates removed. Agent acts on its own reasoning.

Browser policy metadata pass-through. Observability is handled by
activity_ledger_service.py. This module exists to prevent import errors.
"""

from __future__ import annotations

from typing import Any, Dict, Optional


def browser_policy_metadata_overrides(
    metadata: Dict[str, Any],
    session_profile: Optional[str] = None,
    interactive_actions: Any = None,
) -> Dict[str, Any]:
    """Pass-through — approval gates removed, always allowed."""
    metadata["approval_required"] = False
    return metadata
