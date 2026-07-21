"""BYO-provider API key rotation on rate-limit (reliability gap 1.5).

Context
-------
`vault_credentials` (see `server_modules/control_plane_repository.py`,
`CREATE TABLE vault_credentials`) has never had a uniqueness constraint on
(workspace_id, provider) -- only `id` is a primary key. A workspace could
already register a second, third, etc. credential row for the same provider
by POSTing to `/api/connectors/vault` (`connectors_actions.create_vault_credential`)
again with a different label -- nothing rejects it. What was missing was (a)
a way to discover that pool of sibling credentials as failover candidates at
the LLM call site, and (b) a per-credential cooldown so a key that just got
rate-limited isn't immediately retried on the very next run.

This module supplies (b). `provider_profiles._build_provider_credential_candidates`
supplies (a) -- see the "rotation" candidates appended there next to the
primary `credential_id` candidate.

Scope / honesty
----------------
Cooldown state here is an in-memory, per-process dict guarded by a lock --
it is NOT persisted to Postgres or a file. That means:
  * It resets on process restart.
  * It is NOT shared across multiple server replicas/processes.
This mirrors the bounded-retry MECHANISM the task asked for, but is a known
limitation relative to `PROVIDER_PROFILES`' `cooldown_until` (file-persisted
via `_persist_provider_profiles`) -- a durable version would move this into
the vault credential's own `metadata` JSONB column (already there, no schema
change needed) via `vault_store.update_credential_metadata`. Left as-is for
now to keep the change small and side-effect-free on the hot path; flagged
here rather than silently shipped as if it were durable.
"""
from __future__ import annotations

import threading
import time
from typing import Dict, Optional

# Matches ORION_PROFILE_COOLDOWN_RATE_LIMIT_SECONDS' default
# (server_modules/runtime_config.py) so a rate-limited BYOK credential and a
# rate-limited provider profile cool down for the same default window.
DEFAULT_RATE_LIMIT_COOLDOWN_SECONDS = 120

_LOCK = threading.Lock()
_COOLDOWN_UNTIL: Dict[str, float] = {}  # credential_id -> time.time() epoch seconds


def mark_rate_limited(credential_id: Optional[str], *, cooldown_seconds: int = DEFAULT_RATE_LIMIT_COOLDOWN_SECONDS) -> None:
    """Record that `credential_id` just hit a provider 429. It will be
    excluded from the rotation pool (see provider_profiles.py) until the
    cooldown window elapses. Bounded: this only ever prevents this ONE key
    from being picked again for a while -- it never removes the key."""
    cid = str(credential_id or "").strip()
    if not cid:
        return
    with _LOCK:
        _COOLDOWN_UNTIL[cid] = time.time() + max(1, int(cooldown_seconds))


def mark_success(credential_id: Optional[str]) -> None:
    """Clear any cooldown on `credential_id` after a successful call."""
    cid = str(credential_id or "").strip()
    if not cid:
        return
    with _LOCK:
        _COOLDOWN_UNTIL.pop(cid, None)


def is_cooling_down(credential_id: Optional[str]) -> bool:
    cid = str(credential_id or "").strip()
    if not cid:
        return False
    with _LOCK:
        until = _COOLDOWN_UNTIL.get(cid)
        if until is None:
            return False
        if until <= time.time():
            _COOLDOWN_UNTIL.pop(cid, None)
            return False
        return True


def cooldown_remaining_seconds(credential_id: Optional[str]) -> float:
    cid = str(credential_id or "").strip()
    if not cid:
        return 0.0
    with _LOCK:
        until = _COOLDOWN_UNTIL.get(cid)
    if until is None:
        return 0.0
    return max(0.0, until - time.time())


def reset_all() -> None:
    """Test-only helper: clears all in-memory cooldown state."""
    with _LOCK:
        _COOLDOWN_UNTIL.clear()
