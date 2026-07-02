"""Vault key migration: add agent_id to credential entries.

Phase F (Stage 4B): Credentials are keyed by
    (workspace_id, agent_id, provider, account_label)
instead of just (workspace_id, provider).

Migration strategy:
  - Existing rows without agent_id → migrate to
    (workspace, sage_agent_id_for_that_workspace, provider, "default")
  - Reversible: agent_id can be stripped to restore previous behavior
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional


def migrate_vault_credentials(
    credentials: List[Dict[str, Any]],
    *,
    sage_agent_id: str = "agent-sage-001",
) -> List[Dict[str, Any]]:
    """Add agent_id and account_label to every credential entry.

    Existing entries without agent_id are assigned to the Sage agent
    with account_label "default".
    """
    migrated: List[Dict[str, Any]] = []
    for entry in credentials:
        entry = dict(entry)
        if not entry.get("agent_id"):
            entry["agent_id"] = str(
                entry.get("agent_id") or sage_agent_id
            ).strip()
        if not entry.get("account_label"):
            entry["account_label"] = str(
                entry.get("account_label") or "default"
            ).strip()
        migrated.append(entry)
    return migrated


def reverse_migration(
    credentials: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Strip agent_id and account_label from credential entries.

    Reversible fallback — restores pre-Stage-4B behavior.
    """
    reverted: List[Dict[str, Any]] = []
    for entry in credentials:
        entry = dict(entry)
        entry.pop("agent_id", None)
        entry.pop("account_label", None)
        reverted.append(entry)
    return reverted


def resolve_agent_credential(
    credentials: List[Dict[str, Any]],
    *,
    provider: str,
    workspace_id: str,
    agent_id: str,
    account_label: str = "default",
) -> Optional[Dict[str, Any]]:
    """Find a credential scoped to a specific agent.

    Resolution order:
      1. Exact match: (workspace_id, agent_id, provider, account_label)
      2. Agent match with default label: (workspace_id, agent_id, provider, "default")
      3. Workspace default: (workspace_id, None/empty agent_id, provider, "default")
      4. Global: (None, None, provider, "default")
    """
    provider_lower = str(provider).strip().lower()
    ws = str(workspace_id).strip()
    agent = str(agent_id).strip()
    label = str(account_label or "default").strip()

    candidates: List[Dict[str, Any]] = []

    for entry in credentials:
        if str(entry.get("provider") or "").strip().lower() != provider_lower:
            continue
        entry_ws = str(entry.get("workspace_id") or "").strip()
        entry_agent = str(entry.get("agent_id") or "").strip()
        entry_label = str(entry.get("account_label") or "default").strip()

        # Score: higher = better match
        score = 0
        if entry_ws == ws:
            score += 100
        elif not entry_ws:
            score += 0  # global
        else:
            continue  # wrong workspace

        if entry_agent == agent:
            score += 10
        elif not entry_agent:
            score += 1  # workspace default
        else:
            continue  # wrong agent

        if entry_label == label:
            score += 1
        elif entry_label == "default" and label != "default":
            score += 0  # default label is a weaker match
        else:
            continue

        candidates.append((score, entry))

    if not candidates:
        return None

    # Sort by score descending, then by updated_at descending
    candidates.sort(key=lambda item: (
        -item[0],
        str(item[1].get("updated_at") or item[1].get("created_at") or ""),
    ), reverse=False)

    return candidates[0][1]
