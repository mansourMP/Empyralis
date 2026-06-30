"""Direct local tool executor — bypasses gateway, WebSocket, and Rust supervisor.

Used when the backend runs on the same machine as the user (ENV=development).
No approval gates. No session checks. Just execute and return.
"""

from __future__ import annotations

import os
import subprocess
import logging
from pathlib import Path
from typing import Any, Dict, Optional

LOGGER = logging.getLogger(__name__)

_SHELL_TIMEOUT_SECONDS = int(os.getenv("LOCAL_SHELL_TIMEOUT", "30"))
_MAX_FILE_READ_BYTES = int(os.getenv("LOCAL_FILE_READ_MAX_BYTES", str(1024 * 1024)))  # 1 MB


def is_local_dev() -> bool:
    """True when backend is running locally (no DATABASE_URL, or ENV=development)."""
    env = str(os.getenv("ENV") or os.getenv("ENVIRONMENT") or "").strip().lower()
    if env in {"development", "dev", "local"}:
        return True
    db_url = str(os.getenv("DATABASE_URL") or "").strip()
    return not db_url


def shell_execute(command: str, *, timeout: int = _SHELL_TIMEOUT_SECONDS) -> Dict[str, Any]:
    """Run a shell command directly on the local machine.

    Returns dict with stdout, stderr, exit_code.
    """
    print(f"[LOCAL_EXECUTOR] shell_execute called: {command}", flush=True)
    if not command or not command.strip():
        return {"stdout": "", "stderr": "no command provided", "exit_code": 1}

    try:
        result = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=str(Path.home()),
            env={**os.environ},
        )
        return {
            "stdout": result.stdout or "",
            "stderr": result.stderr or "",
            "exit_code": result.returncode,
            "command": command,
            "status": "completed",
        }
    except subprocess.TimeoutExpired:
        return {
            "stdout": "",
            "stderr": f"Command timed out after {timeout}s",
            "exit_code": -1,
            "command": command,
            "status": "timeout",
        }
    except Exception as exc:
        return {
            "stdout": "",
            "stderr": str(exc),
            "exit_code": -1,
            "command": command,
            "status": "error",
        }


def filesystem_read(path: str) -> Dict[str, Any]:
    """Read a file from the local filesystem.

    Returns dict with content, path, size_bytes, status.
    """
    target = _resolve_path(path)
    if target is None:
        return {"content": "", "path": path, "error": "invalid path", "status": "error"}

    try:
        resolved = target.resolve()
        if not resolved.exists():
            return {"content": "", "path": str(resolved), "error": "file not found", "status": "not_found"}
        if resolved.is_dir():
            return _filesystem_list_impl(resolved)

        size = resolved.stat().st_size
        if size > _MAX_FILE_READ_BYTES:
            return {
                "content": f"[file too large: {size} bytes, max {_MAX_FILE_READ_BYTES}]",
                "path": str(resolved),
                "size_bytes": size,
                "status": "truncated",
            }

        content = resolved.read_text(encoding="utf-8", errors="replace")
        return {
            "content": content,
            "path": str(resolved),
            "size_bytes": size,
            "status": "completed",
        }
    except PermissionError:
        return {"content": "", "path": str(target), "error": "permission denied", "status": "error"}
    except Exception as exc:
        return {"content": "", "path": str(target), "error": str(exc), "status": "error"}


def filesystem_write(path: str, content: str) -> Dict[str, Any]:
    """Write content to a file on the local filesystem.

    Returns dict with path, size_bytes, status.
    """
    target = _resolve_path(path)
    if target is None:
        return {"path": path, "error": "invalid path", "status": "error"}

    try:
        resolved = target.resolve()
        resolved.parent.mkdir(parents=True, exist_ok=True)
        resolved.write_text(str(content or ""), encoding="utf-8")
        size = resolved.stat().st_size
        return {
            "path": str(resolved),
            "size_bytes": size,
            "status": "completed",
        }
    except PermissionError:
        return {"path": str(target), "error": "permission denied", "status": "error"}
    except Exception as exc:
        return {"path": str(target), "error": str(exc), "status": "error"}


def filesystem_list(path: str) -> Dict[str, Any]:
    """List directory contents on the local filesystem.

    Returns dict with entries, path, status.
    """
    target = _resolve_path(path)
    if target is None:
        return {"entries": [], "path": path, "error": "invalid path", "status": "error"}

    try:
        resolved = target.resolve()
        if not resolved.exists():
            return {"entries": [], "path": str(resolved), "error": "not found", "status": "not_found"}
        return _filesystem_list_impl(resolved)
    except PermissionError:
        return {"entries": [], "path": str(target), "error": "permission denied", "status": "error"}
    except Exception as exc:
        return {"entries": [], "path": str(target), "error": str(exc), "status": "error"}


def _filesystem_list_impl(resolved: Path) -> Dict[str, Any]:
    entries = []
    try:
        for item in sorted(resolved.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower())):
            try:
                st = item.stat()
                entries.append({
                    "name": item.name,
                    "path": str(item),
                    "is_directory": item.is_dir(),
                    "size_bytes": st.st_size if not item.is_dir() else None,
                    "modified_at": st.st_mtime,
                })
            except OSError:
                entries.append({"name": item.name, "path": str(item), "is_directory": item.is_dir()})
    except Exception:
        pass
    return {
        "path": str(resolved),
        "is_directory": True,
        "entries": entries,
        "count": len(entries),
        "status": "completed",
    }


def _resolve_path(raw: str) -> Optional[Path]:
    """Resolve a user-provided path string to an absolute Path, with basic safety."""
    token = str(raw or "").strip()
    if not token:
        return None
    try:
        p = Path(token).expanduser()
        if not p.is_absolute():
            p = Path.home() / p
        return p.resolve() if p.exists() else p
    except Exception:
        return None
