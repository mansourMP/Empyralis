"""Agent private memory -- sync-facing service wrapper over
agent_private_memory_repository.py, the PER-PERSON half of the
shared-vs-private memory split. See that module's docstring and
migrations/add_agent_private_memory.sql for the full rationale; this module
only adds what a repository shouldn't own: tenant resolution from
workspace_id (never from a stale users.tenant_id -- CLAUDE.md's own
standing warning), secret redaction (the same secret_redaction_service.
redact_text already proven at memory_write_file), and a size cap so a
private note stays a compact preference summary rather than growing
unboundedly (the same discipline MEMORY_MD_INDEX_MAX_BYTES enforces for the
shared index, at a smaller budget appropriate to "how I like to be worked
with" rather than "everything the agent knows").

WHO MAY CALL THIS: every function below takes `user_id` as a required
keyword with no default. There is deliberately no code path anywhere in
this module -- or in its caller, skills_service.execute_single_direct_tool_
call's memory/write_private and memory/get_private dispatch branches -- that
accepts a model-supplied user_id. The tool schema exposed to the model
(skills_service._builtin_tool_descriptors) has no user_id parameter at all;
the only source is session_metadata["user_id"], resolved server-side from
the actual authenticated caller's session before the tool body ever runs.
This is the same honesty posture CLAUDE.md documents for tool_honesty_guard
and agent_goals' attempt_count: the scoping decision is made by the FIRING
CODE, never narrated or supplied by the model.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from server_modules import agent_private_memory_repository
from server_modules import secret_redaction_service
from server_modules import sync_asyncio_bridge

# A private preference note is meant to stay a compact summary of "how this
# person likes to be worked with" -- not a second MEMORY.md. Smaller budget
# than MEMORY_MD_INDEX_MAX_BYTES (25_000) on purpose: this is one person's
# preferences, not a whole agent's shared index.
PRIVATE_MEMORY_NOTE_MAX_CHARS = 4_000

# The SHARED pool (agent_memory.agent_workspace_context_dir) treats an empty
# agent_install_id as "the workspace's own root agent" -- one pool per
# workspace when no specialist install is in play, never a blocked state.
# This table's agent_install_id is NOT NULL (a private note needs SOME
# agent to attach to), so callers resolving "no specific specialist install"
# use this fixed sentinel instead of leaving it blank -- the identical
# fallback semantics as the shared pool, expressed as a real value instead
# of NULL/empty so the NOT NULL constraint and the required-scope guard in
# agent_private_memory_repository stay meaningful for every OTHER caller
# that forgets to resolve one.
ROOT_AGENT_PRIVATE_MEMORY_SCOPE = "_workspace_root_"


def resolve_agent_install_scope(agent_install_id: Any) -> str:
    """Normalize an agent_install_id for private-memory scoping: the real
    id if one was resolved, else the root-agent sentinel above -- never a
    blank string reaching the repository layer."""
    token = str(agent_install_id or "").strip()
    return token or ROOT_AGENT_PRIVATE_MEMORY_SCOPE


def _require_user_id(user_id: Any) -> str:
    token = str(user_id or "").strip()
    if not token:
        raise ValueError(
            "Private memory requires a resolved user identity. This is never "
            "supplied by the model -- it must come from the caller's own "
            "authenticated session (session_metadata['user_id']), and is "
            "absent on surfaces (e.g. an anonymous external channel) that "
            "have no internal user to scope to."
        )
    return token


def _require_agent_install_id(agent_install_id: Any) -> str:
    token = str(agent_install_id or "").strip()
    if not token:
        raise ValueError(
            "Private memory requires a resolved agent_install_id -- a "
            "private note with no agent to attach to is not expressible."
        )
    return token


async def _resolve_tenant_id(workspace_id: str) -> str:
    from server_modules import control_plane_repository

    # Never read tenant_id off a user record (CLAUDE.md's standing warning:
    # users.tenant_id is written once at signup and goes stale the moment a
    # user is invited into a second workspace). Always resolve per-workspace.
    return await control_plane_repository.resolve_tenant_id_for_workspace(workspace_id)


def get_private_memory_note(
    workspace_id: str,
    *,
    agent_install_id: str,
    user_id: str,
) -> Optional[Dict[str, Any]]:
    """Read THIS user's private note for THIS agent. Returns None if no
    Postgres pool is available or nothing has been saved yet -- never
    another user's row (agent_private_memory_repository.get_private_note's
    own WHERE clause makes that structurally impossible, not merely
    policy)."""
    return sync_asyncio_bridge.run_coro_sync(
        aget_private_memory_note(
            workspace_id,
            agent_install_id=agent_install_id,
            user_id=user_id,
        )
    )


async def aget_private_memory_note(
    workspace_id: str,
    *,
    agent_install_id: str,
    user_id: str,
) -> Optional[Dict[str, Any]]:
    """Async twin of get_private_memory_note, holding the IDENTICAL guards.

    It exists because `run_coro_sync` blocks the calling thread on a
    separate bridge loop -- correct for a sync tool-dispatch body, and a
    stalled event loop if an async FastAPI handler calls it. The owner-facing
    memory route in routes_fleet.py is such a handler, so it awaits this
    instead. The sync entrypoint above now delegates HERE rather than
    carrying a second copy of the scope guards: `user_id` stays a required
    keyword with no default on both, and there is still exactly one place
    that decides which row is read."""
    resolved_user_id = _require_user_id(user_id)
    resolved_agent_install_id = _require_agent_install_id(agent_install_id)
    normalized_workspace_id = str(workspace_id or "").strip() or "default"
    tenant_id = await _resolve_tenant_id(normalized_workspace_id)
    return await agent_private_memory_repository.get_private_note(
        tenant_id=tenant_id,
        workspace_id=normalized_workspace_id,
        agent_install_id=resolved_agent_install_id,
        user_id=resolved_user_id,
    )


def get_private_memory_block(
    workspace_id: str,
    *,
    agent_install_id: str,
    user_id: str,
) -> str:
    """Render THIS user's private note as a context-block-ready string, or
    "" if there is none. Same isolation guarantee as get_private_memory_note
    -- this is the function a turn-context assembler would call to append
    the private layer alongside the shared one for the person actually
    talking, never for anyone else."""
    note = get_private_memory_note(
        workspace_id,
        agent_install_id=agent_install_id,
        user_id=user_id,
    )
    content = str((note or {}).get("content") or "").strip()
    return content


def write_private_memory_note(
    workspace_id: str,
    *,
    agent_install_id: str,
    user_id: str,
    content: str,
    reason: str = "memory_write_private",
) -> Dict[str, Any]:
    """Write (create or replace) THIS user's private note. `user_id` is
    required with no default -- see this module's own docstring for why
    that alone is the isolation guarantee: the ON CONFLICT target in
    agent_private_memory_repository.upsert_private_note is the four-column
    tuple including user_id, so two different people's writes can never
    collide into the same row, and a write always lands under the identity
    that was actually resolved for THIS call, never one a caller merely
    claims."""
    return sync_asyncio_bridge.run_coro_sync(
        awrite_private_memory_note(
            workspace_id,
            agent_install_id=agent_install_id,
            user_id=user_id,
            content=content,
            reason=reason,
        )
    )


async def awrite_private_memory_note(
    workspace_id: str,
    *,
    agent_install_id: str,
    user_id: str,
    content: str,
    reason: str = "memory_write_private",
) -> Dict[str, Any]:
    """Async twin of write_private_memory_note, holding the IDENTICAL guards
    (required user_id, required agent_install_id, empty-content refusal,
    secret redaction BEFORE the write, size cap). Same reason the read has
    one -- see aget_private_memory_note's docstring. The sync entrypoint
    delegates here, so the redaction and the cap cannot be enforced on one
    path and skipped on the other."""
    resolved_user_id = _require_user_id(user_id)
    resolved_agent_install_id = _require_agent_install_id(agent_install_id)
    normalized_workspace_id = str(workspace_id or "").strip() or "default"
    raw_content = str(content or "")
    if not raw_content.strip():
        raise ValueError("Private memory content cannot be empty.")
    # Same secret-redaction discipline as memory_write_file -- content is
    # scrubbed BEFORE anything touches disk/Postgres, not after.
    redacted_content = secret_redaction_service.redact_text(raw_content)
    content_was_redacted = redacted_content != raw_content
    if len(redacted_content) > PRIVATE_MEMORY_NOTE_MAX_CHARS:
        raise ValueError(
            f"Private memory note exceeds its {PRIVATE_MEMORY_NOTE_MAX_CHARS}-char "
            "cap. Keep it a compact summary of preferences, not a transcript."
        )
    tenant_id = await _resolve_tenant_id(normalized_workspace_id)
    saved = await agent_private_memory_repository.upsert_private_note(
        tenant_id=tenant_id,
        workspace_id=normalized_workspace_id,
        agent_install_id=resolved_agent_install_id,
        user_id=resolved_user_id,
        content=redacted_content,
        reason=reason,
    )
    saved["redacted"] = content_was_redacted
    return saved
