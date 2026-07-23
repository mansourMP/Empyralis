"""Phase 6: per-agent memory tree.

A thin, hardened facade over the per-namespace context files that already exist
(MEMORY.md index + memory/files/**.md topic files). It does NOT introduce a
parallel store — every call is scoped by ``agent_install_id`` and reuses the
Phase 4/4B isolation: a worker passes its install id, Sage passes ``None`` (the
workspace-scoped namespace). Path hardening (traversal, absolute, size caps,
markdown-only, one-subdir-deep) is enforced by ``workspace_context`` plus the
caps here.

Tree layout, per namespace::

    MEMORY.md                      # index: Summary / Learned rules / Topic files
    memory/files/<name>.md         # flat topic file
    memory/files/<category>/<name>.md   # one category deep (customers/acme.md)

Callers use the *friendly* topic path (``customers/acme.md``); the file layer
maps it into ``memory/files/`` transparently.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

INDEX_FILE = "MEMORY.md"
_TOPIC_PREFIX = "memory/files/"
MAX_TOPIC_FILE_BYTES = 32_000            # per topic file
MAX_TREE_FILES = 200                     # per namespace
_DEFAULT_RETRIEVAL_CHAR_BUDGET = 4_000   # total chars of topic content injected


def _friendly(context_path: str) -> str:
    """memory/files/customers/acme.md -> customers/acme.md ; MEMORY.md -> MEMORY.md"""
    cp = str(context_path or "").strip()
    return cp[len(_TOPIC_PREFIX):] if cp.startswith(_TOPIC_PREFIX) else cp


def _topic_files_dir(workspace_id: str, agent_install_id: Optional[str]):
    from server_modules.workspace_context import agent_workspace_context_dir
    return (
        agent_workspace_context_dir(workspace_id=workspace_id, agent_install_id=agent_install_id)
        / "memory"
        / "files"
    )


# ── Tree read ───────────────────────────────────────────────────────────────


def list_tree(workspace_id: str, *, agent_install_id: Optional[str] = None) -> Dict[str, Any]:
    """Return the tree: the MEMORY.md index plus every topic file (path/size/mtime)."""
    from server_modules import memory_service

    index = memory_service.memory_read_file(workspace_id, INDEX_FILE, agent_install_id=agent_install_id)
    topics: List[Dict[str, Any]] = []
    files_dir = _topic_files_dir(workspace_id, agent_install_id)
    if files_dir.exists():
        for path in sorted(files_dir.rglob("*.md")):
            try:
                rel = path.relative_to(files_dir)
                stat = path.stat()
            except Exception:
                continue
            topics.append(
                {"path": str(rel), "size": int(stat.st_size or 0), "updated_at": float(stat.st_mtime or 0.0)}
            )
            if len(topics) >= MAX_TREE_FILES:
                break
    return {
        "index": {"path": INDEX_FILE, "chars": int(index.get("chars") or 0)},
        "topics": topics,
        "topic_count": len(topics),
    }


def read_file(workspace_id: str, path: str, *, agent_install_id: Optional[str] = None) -> Dict[str, Any]:
    from server_modules import memory_service
    result = memory_service.memory_read_file(workspace_id, path, agent_install_id=agent_install_id)
    return {
        "path": _friendly(result.get("file") or path),
        "content": result.get("content") or "",
        "chars": int(result.get("chars") or 0),
        "is_default": bool(result.get("is_default") or False),
    }


# ── Tree write ──────────────────────────────────────────────────────────────


def _enforce_size(content: str) -> None:
    if len(str(content or "").encode("utf-8")) > MAX_TOPIC_FILE_BYTES:
        raise ValueError(f"Memory file exceeds the {MAX_TOPIC_FILE_BYTES}-byte cap.")


def write_file(
    workspace_id: str,
    path: str,
    content: str,
    *,
    mode: str = "replace",
    agent_install_id: Optional[str] = None,
    actor: str = "owner",
) -> Dict[str, Any]:
    _enforce_size(content)
    from server_modules import memory_service
    saved = memory_service.memory_write_file(
        workspace_id, path, content, mode=str(mode or "replace").strip().lower(),
        agent_install_id=agent_install_id, actor=actor, reason="memory_tree_write",
    )
    return {"ok": True, "path": _friendly(saved.get("filename") or saved.get("file") or path)}


def append_file(
    workspace_id: str, path: str, content: str, *, agent_install_id: Optional[str] = None, actor: str = "owner",
) -> Dict[str, Any]:
    return write_file(workspace_id, path, content, mode="append", agent_install_id=agent_install_id, actor=actor)


def delete_file(workspace_id: str, path: str, *, agent_install_id: Optional[str] = None) -> bool:
    """Delete a topic file. Routed through memory_service.memory_delete_topic_file
    (rather than calling workspace_context directly) so MEMORY.md's
    auto-maintained topic-file index line for this file is removed in the
    same call -- the index must never claim a file exists that's actually
    been deleted, whether the deletion came from the agent or (as here) the
    owner's own manual Memory-tab editor."""
    from server_modules import memory_service
    return memory_service.memory_delete_topic_file(workspace_id, path, agent_install_id=agent_install_id)


# ── Selective retrieval (for per-turn injection) ─────────────────────────────


def _tokens(text: str) -> List[str]:
    return [t for t in re.split(r"[^a-z0-9]+", str(text or "").lower()) if len(t) >= 2]


def retrieve_relevant_topics(
    workspace_id: str,
    query: str,
    *,
    agent_install_id: Optional[str] = None,
    max_files: int = 3,
    char_budget: int = _DEFAULT_RETRIEVAL_CHAR_BUDGET,
) -> List[Dict[str, Any]]:
    """Return the topic files most relevant to ``query`` — keyword overlap on the
    file's path and content — clipped to fit ``char_budget`` total. Empty when the
    query is empty (so an empty turn injects no topic files: absent-when-irrelevant).
    """
    query_tokens = set(_tokens(query))
    if not query_tokens:
        return []
    files_dir = _topic_files_dir(workspace_id, agent_install_id)
    if not files_dir.exists():
        return []

    scored: List[tuple[int, str, str]] = []
    for path in files_dir.rglob("*.md"):
        try:
            content = path.read_text(encoding="utf-8")
            rel = str(path.relative_to(files_dir))
        except Exception:
            continue
        haystack = set(_tokens(rel)) | set(_tokens(content))
        overlap = len(query_tokens & haystack)
        # Phrase bonus: the raw query appears verbatim in the content.
        phrase = str(query or "").strip().lower()
        bonus = 5 if phrase and phrase in content.lower() else 0
        score = overlap + bonus
        if score > 0:
            scored.append((score, rel, content))

    scored.sort(key=lambda item: item[0], reverse=True)
    out: List[Dict[str, Any]] = []
    remaining = max(0, int(char_budget))
    per_file = max(400, remaining // max(1, max_files)) if max_files else remaining
    for score, rel, content in scored[: max(0, int(max_files))]:
        if remaining <= 0:
            break
        clip = min(per_file, remaining)
        excerpt = content if len(content) <= clip else content[:clip].rstrip() + "\n…(truncated)"
        remaining -= len(excerpt)
        out.append({"path": rel, "score": int(score), "content": excerpt})
    return out
