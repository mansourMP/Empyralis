from __future__ import annotations

"""Private implementation. No external caller may import from this module directly. Use memory_service.py."""

import re
import sqlite3
import time
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List

from server_modules.workspace_context import (
    agent_workspace_context_dir,
    read_workspace_context_file,
    write_workspace_context_file,
)


_REPO_ROOT = Path(__file__).resolve().parents[1]
_MEMORY_DIR = _REPO_ROOT / ".orion-stack" / "memory"
_SEMANTIC_MODEL: Any = None
_NOTEBOOK_DIRNAME = "memory"
_MEMORY_PROJECTION_SECTION_LIMIT = 8
_MEMORY_PROJECTION_TOTAL_LIMIT = 40
_FACT_KEY_RE = re.compile(r"^fact-[a-f0-9]+$", re.IGNORECASE)
_QUOTED_TERM_RE = re.compile(r"""['"]([^'"]{2,80})['"]""")
_NEGATIVE_PREFERENCE_RE = re.compile(
    r"\b(dislike|dislikes|do not want|does not want|remove|removed|avoid|never|stop using)\b",
    re.IGNORECASE,
)
_POSITIVE_PREFERENCE_RE = re.compile(
    r"\b(prefer|prefers|like|likes|want|wants|use|uses|comfortable with|communicate|communicates|communication style)\b",
    re.IGNORECASE,
)
_MEMORY_PROJECTION_SECTIONS = (
    ("preferences", "Preferences"),
    ("rules", "Operating Rules"),
    ("project", "Project Context"),
    ("environment", "Agent Computer & Environment"),
    ("decisions", "Decisions & Boundaries"),
    ("workflow", "Workflow"),
    ("other", "Other Facts"),
)


def _normalize_workspace_token(workspace_id: str) -> str:
    token = re.sub(r"[^A-Za-z0-9_.-]+", "-", str(workspace_id or "default").strip()).strip("-")
    return token or "default"


def _normalize_agent_token(agent_install_id: str) -> str:
    token = re.sub(r"[^A-Za-z0-9_.-]+", "-", str(agent_install_id or "").strip()).strip("-")
    return token or "install"


def _humanize_memory_key(key: str) -> str:
    normalized = str(key or "").strip()
    if not normalized:
        return ""
    return re.sub(r"[_-]+", " ", normalized).strip().title()


def _normalize_projection_text(text: str) -> str:
    normalized = re.sub(r"\s+", " ", str(text or "").strip()).lower()
    normalized = re.sub(r"\bfact-[a-f0-9]+\b", "", normalized)
    return normalized.strip(" -:;,.")


def _memory_projection_entry_text(entry: Dict[str, Any]) -> str:
    key = str(entry.get("key") or "").strip()
    content = re.sub(r"\s+", " ", str(entry.get("content") or "").strip())
    if not content:
        return ""
    if _FACT_KEY_RE.fullmatch(key):
        return content
    label = _humanize_memory_key(key)
    if label:
        return f"{label}: {content}"
    return content


def _memory_projection_preference_terms(text: str) -> set[str]:
    normalized = str(text or "")
    if not (_NEGATIVE_PREFERENCE_RE.search(normalized) or _POSITIVE_PREFERENCE_RE.search(normalized)):
        return set()
    return {
        re.sub(r"\s+", " ", term.strip().lower())
        for term in _QUOTED_TERM_RE.findall(normalized)
        if term and term.strip()
    }


def _memory_projection_section(key: str, text: str) -> str:
    combined = f"{key} {text}".lower()
    if any(term in combined for term in ("prefer", "dislike", "tone", "style", "language", "slang")):
        return "preferences"
    if any(term in combined for term in ("rule", "constraint", "avoid", "never", "approval", "boundary", "policy")):
        return "rules"
    if any(term in combined for term in ("project", "workspace", "repo", "platform", "launch", "empyralis", "sage", "openclaw")):
        return "project"
    if any(
        term in combined
        for term in (
            "agent computer",
            "computer",
            "gateway",
            "hardware",
            "mac",
            "macos",
            "apple",
            "homebrew",
            "zsh",
            "screen resolution",
        )
    ):
        return "environment"
    if any(term in combined for term in ("decision", "decided", "default", "architecture", "branch")):
        return "decisions"
    if any(term in combined for term in ("workflow", "procedure", "process", "test", "inspect", "check")):
        return "workflow"
    return "other"


def _is_importable_memory_key(key: str) -> bool:
    normalized = str(key or "").strip()
    if not normalized or " " in normalized:
        return False
    return bool(re.fullmatch(r"[a-z0-9][a-z0-9_.:-]{1,127}", normalized))


def _build_memory_md_projection(entries: List[Dict[str, Any]]) -> str:
    lines = [
        "# Curated Memory",
        "",
        "> Generated projection. Structured memory records are the source of truth; use memory search for raw facts and older details.",
        "",
    ]
    if not entries:
        lines.append("No structured memory facts saved yet.")
        return "\n".join(lines).strip() + "\n"

    buckets: Dict[str, List[str]] = {section_id: [] for section_id, _ in _MEMORY_PROJECTION_SECTIONS}
    seen_texts: set[str] = set()
    seen_preference_terms: set[str] = set()
    included = 0
    hidden = 0
    suppressed = 0

    for entry in entries:
        key = str(entry.get("key") or "").strip()
        text = _memory_projection_entry_text(entry)
        normalized = _normalize_projection_text(text)
        if not normalized:
            continue
        preference_terms = _memory_projection_preference_terms(text)
        if normalized in seen_texts or (preference_terms and preference_terms & seen_preference_terms):
            suppressed += 1
            continue
        section_id = _memory_projection_section(key, text)
        if included >= _MEMORY_PROJECTION_TOTAL_LIMIT or len(buckets[section_id]) >= _MEMORY_PROJECTION_SECTION_LIMIT:
            hidden += 1
            continue
        buckets[section_id].append(text)
        seen_texts.add(normalized)
        seen_preference_terms.update(preference_terms)
        included += 1

    for section_id, section_label in _MEMORY_PROJECTION_SECTIONS:
        items = buckets.get(section_id) or []
        if not items:
            continue
        lines.append(f"## {section_label}")
        lines.append("")
        lines.extend(f"- {item}" for item in items)
        lines.append("")

    if hidden or suppressed:
        lines.append("## More")
        lines.append("")
        if hidden:
            lines.append(f"- {hidden} additional structured facts are available through memory search.")
        if suppressed:
            lines.append(f"- {suppressed} duplicate or superseded facts are hidden from this projection.")
        lines.append("")

    return "\n".join(lines).strip() + "\n"


def _memory_db_path(workspace_id: str, agent_install_id: str | None = None) -> Path:
    _MEMORY_DIR.mkdir(parents=True, exist_ok=True)
    normalized_agent_install_id = str(agent_install_id or "").strip()
    if not normalized_agent_install_id:
        return _MEMORY_DIR / f"{_normalize_workspace_token(workspace_id)}.db"
    path = _MEMORY_DIR / _normalize_workspace_token(workspace_id) / "agents" / _normalize_agent_token(normalized_agent_install_id)
    path.mkdir(parents=True, exist_ok=True)
    return path / "memory.db"


def _memory_logs_dir(workspace_id: str, agent_install_id: str | None = None) -> Path:
    _MEMORY_DIR.mkdir(parents=True, exist_ok=True)
    normalized_agent_install_id = str(agent_install_id or "").strip()
    token = _normalize_workspace_token(workspace_id)
    if normalized_agent_install_id:
        path = _MEMORY_DIR / token / "agents" / _normalize_agent_token(normalized_agent_install_id) / "logs"
        path.mkdir(parents=True, exist_ok=True)
        return path
    if token == "default":
        return _MEMORY_DIR
    path = _MEMORY_DIR / token
    path.mkdir(parents=True, exist_ok=True)
    return path


def _memory_notebook_dir(workspace_id: str, agent_install_id: str | None = None) -> Path:
    path = agent_workspace_context_dir(workspace_id=workspace_id, agent_install_id=agent_install_id) / _NOTEBOOK_DIRNAME
    path.mkdir(parents=True, exist_ok=True)
    return path


def _memory_notebook_documents(workspace_id: str, agent_install_id: str | None = None) -> List[Dict[str, Any]]:
    root = agent_workspace_context_dir(workspace_id=workspace_id, agent_install_id=agent_install_id)
    docs: List[Dict[str, Any]] = []

    memory_md_path = root / "MEMORY.md"
    if memory_md_path.exists():
        docs.append({"path": "MEMORY.md", "abs_path": memory_md_path})

    notes_root = _memory_notebook_dir(workspace_id, agent_install_id=agent_install_id)
    for path in sorted(notes_root.rglob("*.md")):
        if not path.is_file():
            continue
        rel_path = path.relative_to(notes_root).as_posix()
        docs.append({"path": f"{_NOTEBOOK_DIRNAME}/{rel_path}", "abs_path": path})
    return docs


def _resolve_notebook_path(workspace_id: str, rel_path: str, agent_install_id: str | None = None) -> Path:
    normalized = str(rel_path or "").strip().replace("\\", "/").lstrip("/")
    root = agent_workspace_context_dir(workspace_id=workspace_id, agent_install_id=agent_install_id).resolve()
    if normalized == "MEMORY.md":
        path = (root / "MEMORY.md").resolve()
        if path.parent != root:
            raise ValueError("Invalid notebook path.")
        return path
    prefix = f"{_NOTEBOOK_DIRNAME}/"
    if not normalized.startswith(prefix):
        raise ValueError("Notebook path must be MEMORY.md or memory/*.md.")
    notes_root = _memory_notebook_dir(workspace_id, agent_install_id=agent_install_id).resolve()
    candidate = (notes_root / normalized[len(prefix):]).resolve()
    if candidate == notes_root or notes_root not in candidate.parents:
        raise ValueError("Notebook path escapes memory directory.")
    if candidate.suffix.lower() != ".md":
        raise ValueError("Notebook path must target a markdown file.")
    return candidate


@contextmanager
def _connect_memory_db(workspace_id: str, agent_install_id: str | None = None):
    db_path = _memory_db_path(workspace_id, agent_install_id=agent_install_id)
    connection = sqlite3.connect(str(db_path), timeout=10.0)
    connection.row_factory = sqlite3.Row
    try:
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS memory_entries (
                key TEXT PRIMARY KEY,
                content TEXT NOT NULL,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL
            )
            """
        )
        yield connection
    finally:
        connection.close()


def _cosine_similarity(left: List[float], right: List[float]) -> float:
    if not left or not right or len(left) != len(right):
        return 0.0
    return float(sum(float(a) * float(b) for a, b in zip(left, right)))


def _semantic_model():
    global _SEMANTIC_MODEL
    if _SEMANTIC_MODEL is False:
        return None
    if _SEMANTIC_MODEL is None:
        try:
            from sentence_transformers import SentenceTransformer

            _SEMANTIC_MODEL = SentenceTransformer("all-MiniLM-L6-v2")
        except Exception:
            _SEMANTIC_MODEL = False
    return _SEMANTIC_MODEL


def _embed_text(text: str) -> List[float]:
    normalized = re.sub(r"\s+", " ", str(text or "").strip())
    if not normalized:
        return []
    model = _semantic_model()
    if model is None or isinstance(model, bool) or not hasattr(model, "encode"):
        return []
    vector = model.encode(normalized, normalize_embeddings=True)
    if hasattr(vector, "tolist"):
        return [float(item) for item in vector.tolist()]
    return [float(item) for item in vector]


def _list_memory_entries(workspace_id: str, agent_install_id: str | None = None) -> List[Dict[str, Any]]:
    with _connect_memory_db(workspace_id, agent_install_id=agent_install_id) as connection:
        rows = connection.execute(
            """
            SELECT key, content, created_at, updated_at
            FROM memory_entries
            ORDER BY updated_at DESC, key ASC
            """
        ).fetchall()
    return [
        {
            "key": str(row["key"] or ""),
            "content": str(row["content"] or ""),
            "created_at": float(row["created_at"] or 0.0),
            "updated_at": float(row["updated_at"] or 0.0),
        }
        for row in rows
    ]


def _save_memory(
    workspace_id: str,
    key: str,
    content: str,
    *,
    sync_memory_md: bool = True,
    agent_install_id: str | None = None,
) -> None:
    normalized_key = str(key or "").strip()
    normalized_content = str(content or "").strip()
    if not normalized_key or not normalized_content:
        return
    now_ts = time.time()
    with _connect_memory_db(workspace_id, agent_install_id=agent_install_id) as connection:
        connection.execute(
            """
            INSERT INTO memory_entries (key, content, created_at, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET
                content = excluded.content,
                updated_at = excluded.updated_at
            """,
            (normalized_key, normalized_content, now_ts, now_ts),
        )
        connection.commit()
    if sync_memory_md:
        try:
            _export_memory_md(workspace_id, agent_install_id=agent_install_id)
        except Exception:
            pass


def _get_memory(workspace_id: str, agent_install_id: str | None = None) -> str:
    entries = _list_memory_entries(workspace_id, agent_install_id=agent_install_id)
    if not entries:
        return ""
    return "\n".join(
        f"- {entry['key']}: {entry['content']}"
        for entry in entries
        if str(entry.get("content") or "").strip()
    ).strip()


def _search_memory(workspace_id: str, query: str, agent_install_id: str | None = None) -> List[Dict[str, Any]]:
    normalized_query = str(query or "").strip()
    if not normalized_query:
        return []
    pattern = f"%{normalized_query}%"
    with _connect_memory_db(workspace_id, agent_install_id=agent_install_id) as connection:
        rows = connection.execute(
            """
            SELECT key, content, created_at, updated_at
            FROM memory_entries
            WHERE key LIKE ? OR content LIKE ?
            ORDER BY updated_at DESC, key ASC
            """,
            (pattern, pattern),
        ).fetchall()
    return [
        {
            "key": str(row["key"] or ""),
            "content": str(row["content"] or ""),
            "created_at": float(row["created_at"] or 0.0),
            "updated_at": float(row["updated_at"] or 0.0),
        }
        for row in rows
    ]


def _semantic_search(
    workspace_id: str,
    query: str,
    top_k: int = 5,
    agent_install_id: str | None = None,
) -> List[Dict[str, Any]]:
    normalized_query = str(query or "").strip()
    if not normalized_query:
        return []
    entries = _list_memory_entries(workspace_id, agent_install_id=agent_install_id)
    if not entries:
        return []
    query_vector = _embed_text(normalized_query)
    if not query_vector:
        return _search_memory(workspace_id, normalized_query, agent_install_id=agent_install_id)[: max(1, min(int(top_k or 5), 20))]
    scored: List[Dict[str, Any]] = []
    for entry in entries:
        combined_text = f"{entry.get('key')}: {entry.get('content')}"
        memory_vector = _embed_text(combined_text)
        if not memory_vector:
            continue
        scored.append(
            {
                **entry,
                "score": _cosine_similarity(query_vector, memory_vector),
            }
        )
    scored.sort(key=lambda item: float(item.get("score") or 0.0), reverse=True)
    return scored[: max(1, min(int(top_k or 5), 20))]


def _delete_memory(workspace_id: str, key: str, agent_install_id: str | None = None) -> bool:
    normalized_key = str(key or "").strip()
    if not normalized_key:
        return False
    with _connect_memory_db(workspace_id, agent_install_id=agent_install_id) as connection:
        cursor = connection.execute(
            "DELETE FROM memory_entries WHERE key = ?",
            (normalized_key,),
        )
        connection.commit()
        deleted = bool(cursor.rowcount)
    if deleted:
        try:
            _export_memory_md(workspace_id, agent_install_id=agent_install_id)
        except Exception:
            pass
    return deleted


class AgentMemoryRustGateError(RuntimeError):
    pass


_AGENT_MEMORY_STATE_ACTIONS = {
    "append_agent_memory_daily_log": "append_agent_memory_daily_log",
}


def _enforce_agent_memory_daily_log_append(
    *,
    workspace_id: str,
    agent_install_id: str | None,
    log_path,
    entry_bytes: int,
) -> None:
    from server_modules import rust_runtime_kernel_client

    payload = {
        "workspace_id": str(workspace_id or "").strip(),
        "agent_install_id": str(agent_install_id or "").strip(),
        "log_path": str(log_path),
        "entry_bytes": max(0, int(entry_bytes or 0)),
    }
    try:
        decision = rust_runtime_kernel_client.runtime_state_store_decision(
            operation="append_agent_memory_daily_log",
            state_class="agent_memory_daily_logs",
            workspace_id=str(payload["workspace_id"]),
            actor_id="system",
            status="active",
            payload=payload,
            payload_bytes=int(payload["entry_bytes"]),
            workspace_access=True,
            owner_access=True,
        )
        rust_runtime_kernel_client.enforce_kernel_decision(
            "runtime-state-store-decision",
            decision,
        )
        expected_action = _AGENT_MEMORY_STATE_ACTIONS["append_agent_memory_daily_log"]
        next_action = str((decision or {}).get("next_action") or "").strip()
        if next_action != expected_action:
            raise AgentMemoryRustGateError(f"unexpected_next_action:{next_action or 'missing'}")
    except rust_runtime_kernel_client.RustKernelDecisionError as exc:
        raise AgentMemoryRustGateError(exc.reason) from exc


def _save_daily_log(workspace_id: str, content: str, agent_install_id: str | None = None) -> None:
    normalized_content = str(content or "").strip()
    if not normalized_content:
        return
    log_dir = _memory_logs_dir(workspace_id, agent_install_id=agent_install_id)
    now = datetime.now(timezone.utc).astimezone()
    log_path = log_dir / f"{now.strftime('%Y-%m-%d')}.md"
    entry = f"## {now.strftime('%H:%M')}\n\n{normalized_content}\n"
    _enforce_agent_memory_daily_log_append(
        workspace_id=workspace_id,
        agent_install_id=agent_install_id,
        log_path=log_path,
        entry_bytes=len(entry.encode("utf-8")),
    )
    if not log_path.exists():
        log_path.write_text(f"# {now.strftime('%Y-%m-%d')}\n\n{entry}", encoding="utf-8")
        return
    with log_path.open("a", encoding="utf-8") as handle:
        handle.write("\n" + entry)


def _get_recent_logs(workspace_id: str, days: int = 7, agent_install_id: str | None = None) -> str:
    safe_days = max(1, min(int(days or 7), 30))
    log_dir = _memory_logs_dir(workspace_id, agent_install_id=agent_install_id)
    now = datetime.now(timezone.utc).astimezone()
    parts: List[str] = []
    for offset in range(safe_days - 1, -1, -1):
        day = now - timedelta(days=offset)
        path = log_dir / f"{day.strftime('%Y-%m-%d')}.md"
        if not path.exists():
            continue
        try:
            text = path.read_text(encoding="utf-8").strip()
        except Exception:
            continue
        if text:
            parts.append(text)
    return "\n\n".join(parts).strip()


def _update_memory_md(workspace_id: str, content: str, agent_install_id: str | None = None) -> Dict[str, Any]:
    _ = _normalize_workspace_token(workspace_id)
    saved = write_workspace_context_file(
        "MEMORY.md",
        str(content or ""),
        workspace_id=workspace_id,
        agent_install_id=agent_install_id,
    )
    return {
        "workspace_id": _normalize_workspace_token(workspace_id),
        "agent_install_id": str(agent_install_id or "").strip() or None,
        **saved,
    }


def _export_memory_md(workspace_id: str, agent_install_id: str | None = None) -> Dict[str, Any]:
    entries = _list_memory_entries(workspace_id, agent_install_id=agent_install_id)
    return _update_memory_md(
        workspace_id,
        _build_memory_md_projection(entries),
        agent_install_id=agent_install_id,
    )


def _import_memory_md(workspace_id: str, agent_install_id: str | None = None) -> Dict[str, Any]:
    raw = read_workspace_context_file("MEMORY.md", workspace_id=workspace_id, agent_install_id=agent_install_id)
    imported = 0
    for raw_line in str(raw or "").splitlines():
        line = raw_line.strip()
        if not line.startswith("- ") or ":" not in line:
            continue
        key, content = line[2:].split(":", 1)
        normalized_key = str(key or "").strip()
        normalized_content = str(content or "").strip()
        if not _is_importable_memory_key(normalized_key) or not normalized_content:
            continue
        _save_memory(
            workspace_id,
            normalized_key,
            normalized_content,
            sync_memory_md=False,
            agent_install_id=agent_install_id,
        )
        imported += 1
    _export_memory_md(workspace_id, agent_install_id=agent_install_id)
    return {
        "workspace_id": _normalize_workspace_token(workspace_id),
        "agent_install_id": str(agent_install_id or "").strip() or None,
        "imported": imported,
    }


def _list_memory_notebook_files(workspace_id: str, agent_install_id: str | None = None) -> List[Dict[str, Any]]:
    docs: List[Dict[str, Any]] = []
    for item in _memory_notebook_documents(workspace_id, agent_install_id=agent_install_id):
        path = item.get("abs_path")
        if not isinstance(path, Path):
            continue
        try:
            stat = path.stat()
        except Exception:
            continue
        docs.append(
            {
                "path": str(item.get("path") or "").strip(),
                "size": int(stat.st_size or 0),
                "updated_at": float(stat.st_mtime or 0.0),
            }
        )
    return docs


def _search_memory_notebook(
    workspace_id: str,
    query: str,
    *,
    max_results: int = 5,
    agent_install_id: str | None = None,
) -> List[Dict[str, Any]]:
    normalized_query = re.sub(r"\s+", " ", str(query or "").strip()).lower()
    if not normalized_query:
        return []
    query_tokens = [
        token
        for token in re.split(r"[^a-z0-9]+", normalized_query)
        if len(token) >= 2
    ]
    docs = _memory_notebook_documents(workspace_id, agent_install_id=agent_install_id)
    results: List[Dict[str, Any]] = []
    seen: set[tuple[str, int]] = set()

    for item in docs:
        rel_path = str(item.get("path") or "").strip()
        abs_path = item.get("abs_path")
        if not rel_path or not isinstance(abs_path, Path):
            continue
        try:
            lines = abs_path.read_text(encoding="utf-8").splitlines()
        except Exception:
            continue
        for index, line in enumerate(lines):
            compact_line = re.sub(r"\s+", " ", str(line or "").strip()).lower()
            if not compact_line:
                continue
            score = 0
            if normalized_query in compact_line:
                score += 10
            token_hits = sum(1 for token in query_tokens if token in compact_line)
            score += token_hits
            if score <= 0:
                continue
            start_line = max(1, index)
            end_line = min(len(lines), index + 2)
            dedupe_key = (rel_path, start_line)
            if dedupe_key in seen:
                continue
            seen.add(dedupe_key)
            snippet = "\n".join(lines[start_line - 1:end_line]).strip()
            results.append(
                {
                    "path": rel_path,
                    "start_line": start_line,
                    "end_line": end_line,
                    "score": score,
                    "snippet": snippet,
                }
            )

    results.sort(
        key=lambda item: (
            -int(item.get("score") or 0),
            str(item.get("path") or ""),
            int(item.get("start_line") or 0),
        )
    )
    return results[: max(1, min(int(max_results or 5), 20))]


def _get_memory_notebook_excerpt(
    workspace_id: str,
    rel_path: str,
    *,
    from_line: int | None = None,
    line_count: int | None = None,
    agent_install_id: str | None = None,
) -> Dict[str, Any]:
    path = _resolve_notebook_path(workspace_id, rel_path, agent_install_id=agent_install_id)
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()
    safe_from_line = max(1, int(from_line or 1))
    safe_line_count = max(1, min(int(line_count or max(len(lines), 1)), 200))
    if safe_from_line > len(lines):
        return {
            "path": str(rel_path or "").strip(),
            "from_line": safe_from_line,
            "to_line": safe_from_line - 1,
            "text": "",
            "total_lines": len(lines),
        }
    to_line = min(len(lines), safe_from_line + safe_line_count - 1)
    excerpt = "\n".join(lines[safe_from_line - 1:to_line])
    return {
        "path": str(rel_path or "").strip(),
        "from_line": safe_from_line,
        "to_line": to_line,
        "text": excerpt,
        "total_lines": len(lines),
    }
