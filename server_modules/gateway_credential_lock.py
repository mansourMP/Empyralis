"""Machine-local per-credential lock for on-prem / gateway execution (Phase 3A).

Guarantees that a single external bot credential (e.g. one Telegram bot token,
one Slack app) is consumed by at most one gateway process on a given host.
This is the on-prem/single-host counterpart to the cloud-side guarantee we
already enforce in Postgres via the
`uq_agent_channel_bindings_active_inbound_owner` unique index — both layers
run at once: Postgres stops two *workers/tenants* from claiming the same
inbound owner; this file stops two *processes on one box* from opening the
same credential.

------------------------------------------------------------------------------
Portions adapted from hermes-agent (MIT, Copyright (c) 2025 Nous Research):
the scoped-lock mechanism — an atomic O_CREAT|O_EXCL lock file keyed by
scope + hash(identity), with PID + process-start-time staleness detection so a
recycled PID can't be mistaken for a live owner — is derived from hermes-agent
gateway/status.py `acquire_scoped_lock` / `release_scoped_lock`.
Full license text: THIRD_PARTY_LICENSES at the repo root.
------------------------------------------------------------------------------
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterator, Optional, Tuple

try:
    import psutil  # type: ignore
except Exception:  # pragma: no cover - psutil is a normal dependency
    psutil = None  # type: ignore

from server_modules.state_paths import empyralis_state_home

_LOCKS_DIRNAME = "gateway-credential-locks"


def _lock_dir() -> Path:
    override = os.getenv("EMPYRALIS_GATEWAY_LOCK_DIR")
    if override:
        return Path(override).expanduser()
    return (empyralis_state_home() / _LOCKS_DIRNAME).expanduser()


def _scope_hash(identity: str) -> str:
    return hashlib.sha256(str(identity or "").encode("utf-8")).hexdigest()[:16]


def _lock_path(scope: str, identity: str) -> Path:
    return _lock_dir() / f"{scope}-{_scope_hash(identity)}.lock"


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _pid_exists(pid: int) -> bool:
    """Is this PID alive, without signalling it? psutil is the canonical
    cross-platform answer; fall back to os.kill(pid, 0) on POSIX."""
    if pid <= 0:
        return False
    if psutil is not None:
        try:
            return psutil.pid_exists(int(pid))
        except Exception:
            pass
    if os.name == "posix":
        try:
            os.kill(int(pid), 0)
            return True
        except ProcessLookupError:
            return False
        except PermissionError:
            return True  # exists, owned by someone else
        except OSError:
            return False
    return True  # unknown platform without psutil — assume alive (don't steal)


def _process_start_time(pid: int) -> Optional[float]:
    """Kernel start time for a process, used to defeat PID reuse. Linux reads
    /proc field 22; elsewhere psutil.create_time(); None when unavailable."""
    stat_path = Path(f"/proc/{pid}/stat")
    try:
        if stat_path.exists():
            return float(stat_path.read_text(encoding="utf-8").split()[21])
    except (IndexError, ValueError, OSError):
        pass
    if psutil is not None:
        try:
            return float(psutil.Process(int(pid)).create_time())
        except Exception:
            return None
    return None


def _build_pid_record() -> Dict[str, Any]:
    pid = os.getpid()
    return {
        "pid": pid,
        "argv": list(sys.argv),
        "start_time": _process_start_time(pid),
    }


def _read_json_file(path: Path) -> Optional[Dict[str, Any]]:
    if not path.exists():
        return None
    try:
        raw = path.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    if not raw:
        return None
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


def _existing_is_live(existing: Dict[str, Any]) -> bool:
    """True when the lock's recorded owner is still a live process. Conservative:
    when we cannot compare start times (macOS/Windows without /proc), a live PID
    is treated as the owner rather than stolen."""
    try:
        existing_pid = int(existing["pid"])
    except (KeyError, TypeError, ValueError):
        return False  # malformed record -> stale
    if not _pid_exists(existing_pid):
        return False
    recorded_start = existing.get("start_time")
    current_start = _process_start_time(existing_pid)
    if recorded_start is not None and current_start is not None and recorded_start != current_start:
        return False  # PID was reused by a different process -> stale
    return True


def acquire_scoped_lock(
    scope: str, identity: str, *, metadata: Optional[Dict[str, Any]] = None
) -> Tuple[bool, Optional[Dict[str, Any]]]:
    """Acquire a machine-local lock keyed by ``scope`` + hash(``identity``).

    Returns ``(True, None)`` on success, or ``(False, existing_record)`` when a
    live process already holds it. Re-entrant for the same process (matching
    pid + start_time refreshes the record).
    """
    lock_path = _lock_path(scope, identity)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    record = {
        **_build_pid_record(),
        "scope": scope,
        "identity_hash": _scope_hash(identity),
        "metadata": dict(metadata or {}),
        "updated_at": _utc_now_iso(),
    }

    existing = _read_json_file(lock_path)
    if existing is None and lock_path.exists():
        # Empty/corrupt lock file (process killed mid-write) — treat as stale.
        try:
            lock_path.unlink(missing_ok=True)
        except OSError:
            pass
    elif existing is not None:
        same_process = (
            str(existing.get("pid")) == str(os.getpid())
            and existing.get("start_time") == record.get("start_time")
        )
        if same_process:
            _atomic_write(lock_path, record)
            return True, existing
        if _existing_is_live(existing):
            return False, existing
        try:
            lock_path.unlink(missing_ok=True)
        except OSError:
            pass

    try:
        fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        return False, _read_json_file(lock_path)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(record, handle, separators=(",", ":"))
    except Exception:
        try:
            lock_path.unlink(missing_ok=True)
        except OSError:
            pass
        raise
    return True, None


def _atomic_write(path: Path, payload: Dict[str, Any]) -> None:
    tmp = path.with_suffix(path.suffix + f".tmp-{os.getpid()}")
    try:
        with open(tmp, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, separators=(",", ":"))
        os.replace(tmp, path)
    finally:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass


def release_scoped_lock(scope: str, identity: str) -> None:
    """Release a lock previously acquired by this process. No-op if not owned."""
    lock_path = _lock_path(scope, identity)
    existing = _read_json_file(lock_path)
    if existing is None:
        return
    if str(existing.get("pid")) != str(os.getpid()):
        return  # not ours — leave it
    try:
        lock_path.unlink(missing_ok=True)
    except OSError:
        pass


class CredentialInUseError(RuntimeError):
    """Raised when another live process on this host already holds the
    credential lock for a scope/identity."""

    def __init__(self, scope: str, resource_desc: str, existing: Optional[Dict[str, Any]]):
        owner_pid = existing.get("pid") if isinstance(existing, dict) else None
        message = (
            f"{resource_desc} already in use"
            + (f" (PID {owner_pid})" if owner_pid else "")
            + " on this host. Stop the other gateway process first."
        )
        super().__init__(message)
        self.scope = scope
        self.existing = existing


@contextmanager
def scoped_credential_lock(
    scope: str, identity: str, *, resource_desc: str = "credential", metadata: Optional[Dict[str, Any]] = None
) -> Iterator[None]:
    """Context manager wrapping acquire/release. Raises CredentialInUseError if
    a live process on this host already holds the lock."""
    acquired, existing = acquire_scoped_lock(scope, identity, metadata=metadata)
    if not acquired:
        raise CredentialInUseError(scope, resource_desc, existing)
    try:
        yield
    finally:
        release_scoped_lock(scope, identity)
