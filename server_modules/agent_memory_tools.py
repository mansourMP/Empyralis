"""Phase N: Agent-scoped memory tools.

Three tools for agent self-managed memory:
  - memory_read(path)      — read a file from the agent's memory directory
  - memory_write(path, content, mode="append"|"overwrite") — write a file
  - memory_list()          — list all files in the agent's memory directory

Path traversal defense: any path that escapes the agent's memory directory
is rejected. Every write is ledgered with {event_class:"memory", action:"write"}.

The agent's memory directory is: <workspace_context>/agents/<install_id>/memory/
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional


# ── Path resolution ─────────────────────────────────────────────────────────


def _agent_memory_dir(
    *,
    workspace_id: str,
    agent_install_id: str,
) -> Path:
    """Resolve the agent's memory directory."""
    from server_modules import workspace_context

    agent_dir = workspace_context.agent_workspace_context_dir(
        workspace_id=workspace_id,
        agent_install_id=agent_install_id,
    )
    memory_dir = agent_dir / "memory"
    memory_dir.mkdir(parents=True, exist_ok=True)
    return memory_dir


def _resolve_safe_path(
    memory_dir: Path,
    requested_path: str,
) -> Path:
    """Resolve and validate a path within the agent's memory directory.

    Raises ValueError if the path escapes the memory directory.
    """
    clean = str(requested_path or "").strip().lstrip("/\\")
    if not clean:
        raise ValueError("path is required")

    # Reject path traversal patterns
    if ".." in clean:
        raise ValueError("path traversal denied: '..' not allowed")
    if clean.startswith("~") or clean.startswith("/"):
        raise ValueError("absolute paths not allowed")

    resolved = (memory_dir / clean).resolve()
    if not str(resolved).startswith(str(memory_dir.resolve())):
        raise ValueError("path traversal denied: escapes memory directory")

    return resolved


# ── Ledger ───────────────────────────────────────────────────────────────────


async def _ledger_memory_write(
    *,
    workspace_id: str,
    actor_id: str,
    path: str,
    byte_count: int,
    mode: str = "append",
) -> None:
    """Best-effort ledger for memory writes — NEVER stores contents."""
    try:
        from server_modules import activity_ledger_service

        await activity_ledger_service.append_activity_event(
            tenant_id="system",
            workspace_id=workspace_id,
            actor_type="agent",
            actor_id=str(actor_id or "").strip() or "unknown",
            event_class="memory",
            detail_level="audit_reference",
            action="write",
            title=f"Memory write: {path} ({byte_count}B, {mode})",
            summary=(
                f"Agent {actor_id} wrote {byte_count} bytes to "
                f"memory file '{path}' (mode={mode}). "
                f"Contents redacted per audit policy."
            ),
            status="written",
            metadata={
                "path": path,
                "byte_count": byte_count,
                "mode": mode,
            },
        )
    except Exception:
        pass


# ── Tool implementations ─────────────────────────────────────────────────────


async def memory_read(
    *,
    workspace_id: str,
    agent_install_id: str,
    agent_id: str = "",
    path: str = "",
) -> Dict[str, Any]:
    """Read a file from the agent's memory directory.

    Args:
        path: Relative path within the memory directory (e.g., "SOUL.md")
    """
    try:
        memory_dir = _agent_memory_dir(
            workspace_id=workspace_id,
            agent_install_id=agent_install_id or agent_id,
        )
        safe_path = _resolve_safe_path(memory_dir, path)

        if not safe_path.exists():
            return {
                "ok": False,
                "error": f"File not found: {path}",
                "path": path,
            }

        content = safe_path.read_text(encoding="utf-8")
        return {
            "ok": True,
            "path": path,
            "content": content,
            "byte_count": len(content.encode("utf-8")),
        }
    except ValueError as exc:
        return {"ok": False, "error": str(exc), "path": path}
    except Exception as exc:
        return {"ok": False, "error": f"Read failed: {exc}", "path": path}


async def memory_write(
    *,
    workspace_id: str,
    agent_install_id: str,
    agent_id: str = "",
    path: str = "",
    content: str = "",
    mode: str = "append",
) -> Dict[str, Any]:
    """Write to a file in the agent's memory directory.

    Args:
        path: Relative path within the memory directory
        content: Text content to write
        mode: "append" (default) or "overwrite"
    """
    if not str(content or "").strip():
        return {"ok": False, "error": "content is required", "path": path}

    write_mode = str(mode or "append").strip().lower()
    if write_mode not in ("append", "overwrite"):
        return {"ok": False, "error": "mode must be 'append' or 'overwrite'", "path": path}

    try:
        memory_dir = _agent_memory_dir(
            workspace_id=workspace_id,
            agent_install_id=agent_install_id or agent_id,
        )
        safe_path = _resolve_safe_path(memory_dir, path)

        if write_mode == "append" and safe_path.exists():
            existing = safe_path.read_text(encoding="utf-8")
            new_content = existing.rstrip("\n") + "\n" + str(content).strip() + "\n"
        else:
            new_content = str(content).strip() + "\n"

        safe_path.parent.mkdir(parents=True, exist_ok=True)
        safe_path.write_text(new_content, encoding="utf-8")

        byte_count = len(new_content.encode("utf-8"))
        actor = str(agent_install_id or agent_id or "").strip() or "unknown"

        # Ledger (best-effort, never blocks)
        await _ledger_memory_write(
            workspace_id=workspace_id,
            actor_id=actor,
            path=path,
            byte_count=byte_count,
            mode=write_mode,
        )

        return {
            "ok": True,
            "path": path,
            "byte_count": byte_count,
            "mode": write_mode,
        }
    except ValueError as exc:
        return {"ok": False, "error": str(exc), "path": path}
    except Exception as exc:
        return {"ok": False, "error": f"Write failed: {exc}", "path": path}


async def memory_list(
    *,
    workspace_id: str,
    agent_install_id: str,
    agent_id: str = "",
) -> Dict[str, Any]:
    """List all files in the agent's memory directory.

    Returns a flat list of relative paths with sizes.
    """
    try:
        memory_dir = _agent_memory_dir(
            workspace_id=workspace_id,
            agent_install_id=agent_install_id or agent_id,
        )

        if not memory_dir.exists():
            return {"ok": True, "files": [], "count": 0}

        files: List[Dict[str, Any]] = []
        for root, dirs, filenames in os.walk(memory_dir):
            # Skip hidden/dream dirs
            dirs[:] = [d for d in dirs if not d.startswith(".")]
            for fname in filenames:
                if fname.startswith("."):
                    continue
                fpath = Path(root) / fname
                try:
                    rel = str(fpath.relative_to(memory_dir))
                except ValueError:
                    rel = fname
                files.append({
                    "path": rel,
                    "size": fpath.stat().st_size,
                    "modified": datetime.fromtimestamp(
                        fpath.stat().st_mtime, tz=timezone.utc
                    ).isoformat(),
                })

        files.sort(key=lambda f: f["path"])
        return {"ok": True, "files": files, "count": len(files)}
    except Exception as exc:
        return {"ok": False, "error": str(exc), "files": [], "count": 0}


# ── Migration helper ─────────────────────────────────────────────────────────


async def migrate_existing_memory_to_index(
    *,
    workspace_id: str,
    agent_install_id: str,
) -> Dict[str, Any]:
    """Generate a MEMORY.md index from existing root memory files.

    For every existing Sage install: keep the 12 files as-is.
    Generate a MEMORY.md that lists them as index entries.
    Does NOT touch file contents.
    """
    from server_modules import workspace_context

    try:
        agent_dir = workspace_context.agent_workspace_context_dir(
            workspace_id=workspace_id,
            agent_install_id=agent_install_id,
        )
        memory_dir = agent_dir / "memory"
        memory_dir.mkdir(parents=True, exist_ok=True)

        # Discover existing root .md files
        root_files: List[Path] = []
        if agent_dir.exists():
            for f in sorted(agent_dir.iterdir()):
                if f.is_file() and f.suffix == ".md" and not f.name.startswith("."):
                    if f.name != "MEMORY.md":
                        root_files.append(f)

        # Discover files in memory/ subdir
        memory_files: List[Path] = []
        if memory_dir.exists():
            for root, dirs, filenames in os.walk(memory_dir):
                dirs[:] = [d for d in dirs if not d.startswith(".")]
                for fname in sorted(filenames):
                    if fname.endswith(".md") and not fname.startswith("."):
                        memory_files.append(Path(root) / fname)

        # Build MEMORY.md index
        lines = [
            "# Agent Memory Index",
            "",
            f"Generated: {datetime.now(timezone.utc).isoformat()}",
            "",
            "This is the agent's memory index. Files listed below are available",
            "for reading via `memory_read`. The agent maintains this index — add,",
            "rename, or remove entries as memory evolves.",
            "",
            "## Root Files",
            "",
        ]

        # Known descriptions for common files
        _DESCRIPTIONS: Dict[str, str] = {
            "SOUL.md": "personality, tone, and voice guidelines",
            "IDENTITY.md": "name, channel presence, public identity",
            "USER.md": "who the user is, preferences, context",
            "AGENTS.md": "operating rules and fleet configuration",
            "TOOLS.md": "available tools and usage notes",
            "GOALS.md": "short and long-term goals",
            "HEARTBEAT.md": "heartbeat run log and scheduled actions",
            "PROCEDURES.md": "standard operating procedures",
            "REFLECTION.md": "periodic self-reflection entries",
            "SELF_MODEL.md": "agent's model of its own capabilities",
            "LIFE_STORY.md": "long-form narrative memory",
        }

        for f in root_files:
            desc = _DESCRIPTIONS.get(f.name, "memory file")
            size = f.stat().st_size
            lines.append(f"- [{f.name}]({f.name}) — {desc} ({size}B)")

        if memory_files:
            lines.append("")
            lines.append("## Memory Directory")
            lines.append("")
            for f in memory_files:
                try:
                    rel = str(f.relative_to(memory_dir))
                except ValueError:
                    rel = f.name
                size = f.stat().st_size
                lines.append(f"- [{rel}](memory/{rel}) — memory file ({size}B)")

        lines.append("")
        lines.append("## How I Use This")
        lines.append("")
        lines.append("I read files from this index when I need durable context.")
        lines.append("When the user corrects me, I write the correction to memory")
        lines.append("so it survives future sessions. I update this index when I")
        lines.append("create, rename, or retire files.")

        # Write MEMORY.md to agent root
        index_path = agent_dir / "MEMORY.md"
        content = "\n".join(lines) + "\n"
        index_path.write_text(content, encoding="utf-8")

        return {
            "ok": True,
            "root_files_indexed": len(root_files),
            "memory_files_indexed": len(memory_files),
            "index_path": str(index_path),
        }
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def build_memory_starter_template() -> str:
    """Return the starter MEMORY.md template for new agents."""
    return (
        "# Agent Memory Index\n"
        "\n"
        "This is the agent's memory index. It is the ONLY file guaranteed\n"
        "to be injected into every turn's context. All other memory files\n"
        "are read on-demand via `memory_read`.\n"
        "\n"
        "The agent maintains this index — add, rename, or remove entries\n"
        "as memory evolves. When the user corrects the agent, write the\n"
        "correction to memory so it survives future sessions.\n"
        "\n"
        "## Files\n"
        "\n"
        "(No memory files yet. Create one with memory_write.)\n"
        "\n"
        "## How I Use This\n"
        "\n"
        "I read files from this index when I need durable context.\n"
        "I update this index when I create, rename, or retire files.\n"
    )
