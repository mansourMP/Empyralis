from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import mimetypes
import re
from typing import Any, Dict, Iterable, List, Optional

from server_modules import memory_service
from server_modules import personal_context_engine
from server_modules import session_transcript_store
from server_modules import workspace_context


UNIFIED_MEMORY_LAYER_ORDER: tuple[str, ...] = (
    "profile_memory",
    "episodic_memory",
    "app_event_history",
    "shared_operational_board",
    "notes_documents_retrieval",
    "specialist_scoped_memory",
    "local_private_memory",
    "cloud_synced_memory",
)

STATE_LAYER_MODEL_ORDER: tuple[str, ...] = (
    "captain_private_memory",
    "specialist_private_memory",
    "shared_operational_board",
    "artifacts_history",
)


MEMORY_LAYER_SPECS: dict[str, dict[str, Any]] = {
    "profile_memory": {
        "ownership": "Workspace owner profile and durable user preferences.",
        "storage_location": "Workspace USER.md context file and future profile-specific overlays.",
        "retention_rules": "Durable until the owner edits or deletes the stored profile notes.",
        "sync_rules": {
            "default": "local_only",
            "may_sync": True,
            "requires_explicit_opt_in": True,
            "notes": "Personal profile facts stay local by default and may sync only through explicit future profile-sync policy.",
        },
        "indexing_retrieval": "Context markdown is available for direct read and summary synthesis; it is not broadly searchable by specialists.",
        "privacy_level": "personal_profile",
        "readers": "Sage by default. Specialists only via explicit shared excerpts or install-specific profile notes.",
    },
    "episodic_memory": {
        "ownership": "Recent conversations, transcript summaries, and short-horizon behavioral history.",
        "storage_location": "Local transcript jsonl files plus daily logs in the workspace/install memory namespace.",
        "retention_rules": "Rolling recent context retained locally until pruned or archived by policy.",
        "sync_rules": {
            "default": "local_only",
            "may_sync": True,
            "requires_explicit_opt_in": True,
            "notes": "Only compressed summaries should ever sync, never raw transcripts by default.",
        },
        "indexing_retrieval": "Recent-first transcript and daily-log summaries with bounded excerpts.",
        "privacy_level": "behavioral_history",
        "readers": "Sage by default. Specialists can read only their own install-scoped episodic memory.",
    },
    "app_event_history": {
        "ownership": "Structured mobile/app/module event publishers.",
        "storage_location": "Durable control-plane personal_context_events feed.",
        "retention_rules": "Durable event feed until archived or pruned by control-plane retention policy.",
        "sync_rules": {
            "default": "cloud_synced",
            "may_sync": True,
            "requires_explicit_opt_in": False,
            "notes": "Structured events are the default cloud-safe context layer.",
        },
        "indexing_retrieval": "Structured filters by type, source app, priority, install scope, and seen state.",
        "privacy_level": "structured_event_feed",
        "readers": "Sage by default. Specialists only when the event scope explicitly includes them.",
    },
    "shared_operational_board": {
        "ownership": "Workspace-visible operational instructions, SOPs, handoffs, and approved artifact references.",
        "storage_location": "Append-only shared operational board records managed by shared_operational_board_service.",
        "retention_rules": "Durable and version-aware until superseded or explicitly removed by future board policy.",
        "sync_rules": {
            "default": "workspace_shared",
            "may_sync": True,
            "requires_explicit_opt_in": False,
            "notes": "Only explicitly published operational content belongs here; this is not a raw private-memory sync path.",
        },
        "indexing_retrieval": "Board entries are listed by revision history and published operational state.",
        "privacy_level": "workspace_shared_operational",
        "readers": "Sage and permitted specialists read published entries. Proposals require elevated board permissions to inspect.",
    },
    "notes_documents_retrieval": {
        "ownership": "Workspace notes, curated memory, uploaded knowledge, and retrieval snippets.",
        "storage_location": "Local memory notebook files plus the workspace knowledge directory.",
        "retention_rules": "Durable until files are edited or removed.",
        "sync_rules": {
            "default": "local_only",
            "may_sync": True,
            "requires_explicit_opt_in": True,
            "notes": "Documents and uploads remain local unless a future sync policy explicitly publishes summaries or selected files.",
        },
        "indexing_retrieval": "File listing plus bounded text snippet retrieval for notebook and knowledge sources.",
        "privacy_level": "document_knowledge",
        "readers": "Sage by default. Specialists get only their install namespace or explicitly shared document context.",
    },
    "specialist_scoped_memory": {
        "ownership": "Per-install specialist namespace.",
        "storage_location": "Install-specific context files, memory facts, notebook docs, daily logs, and transcript history.",
        "retention_rules": "Durable within the install namespace until deleted or pruned.",
        "sync_rules": {
            "default": "local_only",
            "may_sync": True,
            "requires_explicit_opt_in": True,
            "notes": "Install-private state never becomes shared memory without an explicit brokered share path.",
        },
        "indexing_retrieval": "Install-scoped snapshot, recent log summaries, and document search restricted to the install namespace.",
        "privacy_level": "specialist_private",
        "readers": "The owning specialist install and Sage when explicitly brokered. Other specialists are denied by default.",
    },
    "local_private_memory": {
        "ownership": "Device-local state and private workspace memory that should not leave the host by default.",
        "storage_location": "Local workspace context files, notebook docs, knowledge files, logs, and transcripts on the attached runtime.",
        "retention_rules": "Local until the user deletes it or a local retention policy prunes it.",
        "sync_rules": {
            "default": "never",
            "may_sync": False,
            "requires_explicit_opt_in": True,
            "notes": "This layer is intentionally excluded from cloud sync by default.",
        },
        "indexing_retrieval": "Local-only retrieval. Accessible through the local runtime, not through broad cloud sync.",
        "privacy_level": "local_private",
        "readers": "Sage through an attached local runtime only. Specialists do not inherit this layer by default.",
    },
    "cloud_synced_memory": {
        "ownership": "Cloud-safe structured summaries and explicitly synced feeds.",
        "storage_location": "Control-plane records and future cloud-safe summary stores.",
        "retention_rules": "Durable under control-plane retention rules.",
        "sync_rules": {
            "default": "cloud_synced",
            "may_sync": True,
            "requires_explicit_opt_in": False,
            "notes": "Only cloud-safe, policy-approved memory classes belong here.",
        },
        "indexing_retrieval": "Structured metadata and summary retrieval on synced payloads only.",
        "privacy_level": "cloud_safe_structured",
        "readers": "Sage by default. Specialists only when the synced memory item is explicitly scoped to them.",
    },
}


TEXT_DOCUMENT_EXTENSIONS = {
    ".json",
    ".log",
    ".md",
    ".markdown",
    ".txt",
    ".yaml",
    ".yml",
    ".csv",
}


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _compact_text(value: Any, limit: int = 400) -> str:
    normalized = re.sub(r"\s+", " ", str(value or "").strip())
    if len(normalized) <= limit:
        return normalized
    return normalized[: limit - 1].rstrip() + "…"


def _meaningful_context_content(filename: str, value: Any) -> str:
    text = str(value or "").strip()
    default = str(workspace_context.DEFAULT_CONTEXT_FILE_CONTENTS.get(filename) or "").strip()
    if not text or text == default:
        return ""
    return text


def _base_layer(layer_id: str, *, accessible: bool, scope: str) -> Dict[str, Any]:
    spec = MEMORY_LAYER_SPECS[layer_id]
    return {
        "layer_id": layer_id,
        "scope": scope,
        "accessible": bool(accessible),
        "ownership": spec["ownership"],
        "storage_location": spec["storage_location"],
        "retention_rules": spec["retention_rules"],
        "sync_rules": dict(spec["sync_rules"]),
        "indexing_retrieval": spec["indexing_retrieval"],
        "privacy_level": spec["privacy_level"],
        "readers": spec["readers"],
        "items": [],
        "summary": {},
    }


def _split_recent_log_excerpt(raw_text: str, *, limit: int = 5) -> List[str]:
    excerpt = str(raw_text or "").strip()
    if not excerpt:
        return []
    blocks = [block.strip() for block in re.split(r"\n\s*\n", excerpt) if block.strip()]
    return blocks[: max(1, min(int(limit or 5), 10))]


def _knowledge_documents(*, workspace_id: str, agent_install_id: str | None = None) -> List[Dict[str, Any]]:
    root = workspace_context.workspace_knowledge_dir(
        workspace_id=workspace_id,
        agent_install_id=agent_install_id,
    )
    docs: List[Dict[str, Any]] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        try:
            stat = path.stat()
        except Exception:
            continue
        docs.append(
            {
                "path": f"knowledge/{path.relative_to(root).as_posix()}",
                "abs_path": path,
                "size": int(stat.st_size or 0),
                "updated_at": float(stat.st_mtime or 0.0),
                "mime_type": mimetypes.guess_type(str(path))[0] or "application/octet-stream",
            }
        )
    docs.sort(key=lambda item: (-float(item.get("updated_at") or 0.0), str(item.get("path") or "")))
    return docs


def _read_text_excerpt(path: Path, *, max_lines: int = 12, max_chars: int = 800) -> str:
    if path.suffix.lower() not in TEXT_DOCUMENT_EXTENSIONS:
        return ""
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except Exception:
        return ""
    excerpt = "\n".join(lines[: max(1, min(int(max_lines or 12), 40))]).strip()
    if len(excerpt) <= max_chars:
        return excerpt
    return excerpt[: max_chars - 1].rstrip() + "…"


def _search_knowledge_documents(
    *,
    workspace_id: str,
    query: str,
    agent_install_id: str | None = None,
    max_results: int = 5,
) -> List[Dict[str, Any]]:
    normalized_query = str(query or "").strip().lower()
    if not normalized_query:
        return []
    query_tokens = [token for token in re.split(r"[^a-z0-9]+", normalized_query) if len(token) >= 2]
    results: List[Dict[str, Any]] = []
    for item in _knowledge_documents(workspace_id=workspace_id, agent_install_id=agent_install_id):
        abs_path = item.get("abs_path")
        if not isinstance(abs_path, Path):
            continue
        excerpt = _read_text_excerpt(abs_path, max_lines=40, max_chars=4000)
        if not excerpt:
            continue
        compact = excerpt.lower()
        score = 0
        if normalized_query in compact:
            score += 10
        score += sum(1 for token in query_tokens if token in compact)
        if score <= 0:
            continue
        results.append(
            {
                "path": str(item.get("path") or "").strip(),
                "source": "knowledge",
                "score": score,
                "snippet": _compact_text(excerpt, limit=420),
            }
        )
    results.sort(key=lambda entry: (-int(entry.get("score") or 0), str(entry.get("path") or "")))
    return results[: max(1, min(int(max_results or 5), 20))]


def search_unified_memory_documents(
    *,
    workspace_id: str,
    query: str,
    agent_install_id: str | None = None,
    limit: int = 6,
) -> List[Dict[str, Any]]:
    safe_limit = max(1, min(int(limit or 6), 20))
    notebook_hits = memory_service.search_memory_notebook(
        workspace_id,
        query,
        max_results=safe_limit,
        agent_install_id=agent_install_id,
    )
    knowledge_hits = _search_knowledge_documents(
        workspace_id=workspace_id,
        query=query,
        agent_install_id=agent_install_id,
        max_results=safe_limit,
    )
    merged: List[Dict[str, Any]] = []
    for item in notebook_hits:
        merged.append(
            {
                "path": str(item.get("path") or "").strip(),
                "source": "memory_notebook",
                "score": int(item.get("score") or 0),
                "snippet": str(item.get("snippet") or "").strip(),
            }
        )
    merged.extend(knowledge_hits)
    merged.sort(key=lambda entry: (-int(entry.get("score") or 0), str(entry.get("path") or "")))
    return merged[:safe_limit]


def _recent_document_snippets(
    *,
    workspace_id: str,
    agent_install_id: str | None = None,
    limit: int = 3,
) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    safe_limit = max(1, min(int(limit or 3), 10))

    notebook_files = memory_service.list_memory_notebook_files(
        workspace_id,
        agent_install_id=agent_install_id,
    )
    notebook_files = sorted(
        notebook_files,
        key=lambda item: (-float(item.get("updated_at") or 0.0), str(item.get("path") or "")),
    )
    for item in notebook_files:
        if len(out) >= safe_limit:
            break
        path = str(item.get("path") or "").strip()
        if not path:
            continue
        excerpt = memory_service.get_memory_notebook_excerpt(
            workspace_id,
            path,
            from_line=1,
            line_count=8,
            agent_install_id=agent_install_id,
        )
        text = _compact_text(excerpt.get("text"), limit=420)
        if not text:
            continue
        out.append(
            {
                "path": path,
                "source": "memory_notebook",
                "snippet": text,
            }
        )

    for item in _knowledge_documents(workspace_id=workspace_id, agent_install_id=agent_install_id):
        if len(out) >= safe_limit:
            break
        abs_path = item.get("abs_path")
        if not isinstance(abs_path, Path):
            continue
        excerpt = _read_text_excerpt(abs_path, max_lines=8, max_chars=420)
        if not excerpt:
            continue
        out.append(
            {
                "path": str(item.get("path") or "").strip(),
                "source": "knowledge",
                "snippet": _compact_text(excerpt, limit=420),
            }
        )
    return out[:safe_limit]


def _boundary_map() -> Dict[str, List[str]]:
    return {
        "local_only_by_default": [
            "profile_memory",
            "episodic_memory",
            "notes_documents_retrieval",
            "specialist_scoped_memory",
            "local_private_memory",
        ],
        "workspace_shared_by_default": [
            "shared_operational_board",
        ],
        "cloud_synced_by_default": [
            "app_event_history",
            "cloud_synced_memory",
        ],
        "requires_explicit_opt_in_to_sync": [
            "profile_memory",
            "episodic_memory",
            "notes_documents_retrieval",
            "specialist_scoped_memory",
        ],
        "never_sync_by_default": [
            "local_private_memory",
        ],
    }


def _enforce_sage_memory_viewer(*, viewer_role: str, viewer_install_id: str | None = None) -> None:
    normalized_viewer_role = str(viewer_role or "sage").strip().lower() or "sage"
    if normalized_viewer_role != "sage":
        viewer_token = str(viewer_install_id or "").strip() or normalized_viewer_role
        raise PermissionError(
            f"Captain private memory is only available through the Sage payload boundary; '{viewer_token}' cannot request it directly."
        )


def _enforce_specialist_memory_viewer(
    *,
    requested_install_id: str,
    viewer_role: str,
    viewer_install_id: str | None = None,
) -> None:
    normalized_viewer_role = str(viewer_role or "specialist").strip().lower() or "specialist"
    if normalized_viewer_role != "specialist":
        raise PermissionError(
            "Raw specialist private memory is only available to the owning specialist install."
        )
    normalized_viewer_install_id = str(viewer_install_id or requested_install_id).strip()
    if normalized_viewer_install_id != requested_install_id:
        raise PermissionError(
            "Specialists cannot access another specialist's private memory."
        )


def _state_layer_model(*, audience: str, agent_install_id: str | None = None) -> Dict[str, Dict[str, Any]]:
    board_contract = memory_service.shared_operational_board_access_contract()
    artifact_contract = memory_service.artifacts_history_access_contract()
    return {
        "captain_private_memory": {
            "accessible": audience == "sage",
            "default_access": "captain_only",
            "specialists_allowed": False,
            "explicit_broker_required_for_specialists": True,
        },
        "specialist_private_memory": {
            "accessible": audience == "specialist",
            "default_access": "owning_install_only",
            "agent_install_id": str(agent_install_id or "").strip() or None,
            "cross_install_allowed": False,
            "captain_raw_access_by_default": False,
        },
        "shared_operational_board": {
            "accessible": True,
            **board_contract,
        },
        "artifacts_history": {
            "accessible": True,
            **artifact_contract,
        },
    }


def _service_boundaries() -> Dict[str, Dict[str, Any]]:
    return {
        "profile_memory": {"repository": "workspace_context", "writers": ["write_workspace_context_file(USER.md)"]},
        "episodic_memory": {"repository": "session_transcript_store + memory_service", "writers": ["save_session_transcript", "save_daily_log"]},
        "app_event_history": {"repository": "personal_context_engine", "writers": ["publish_event"]},
        "shared_operational_board": {
            "repository": "shared_operational_board_service",
            "writers": ["propose_update", "publish_update"],
        },
        "notes_documents_retrieval": {"repository": "memory_service + workspace_context.workspace_knowledge_dir", "writers": ["memory_notebook files", "knowledge uploads"]},
        "specialist_scoped_memory": {"repository": "install-scoped workspace_context + memory_service", "writers": ["install context files", "install memory/log writes"]},
        "local_private_memory": {"repository": "local filesystem", "writers": ["local runtime attachments only"]},
        "cloud_synced_memory": {"repository": "control plane", "writers": ["policy-approved sync publishers"]},
    }


def _ingestion_contract() -> List[Dict[str, Any]]:
    return [
        {"source_kind": "profile_context", "layer": "profile_memory", "ingestion_path": "workspace_context.USER.md"},
        {"source_kind": "transcript_summary", "layer": "episodic_memory", "ingestion_path": "session_transcript_store.save_session_transcript"},
        {"source_kind": "daily_log", "layer": "episodic_memory", "ingestion_path": "memory_service.save_daily_log"},
        {"source_kind": "personal_context_event", "layer": "app_event_history", "ingestion_path": "personal_context_engine.publish_event"},
        {"source_kind": "shared_operational_board_entry", "layer": "shared_operational_board", "ingestion_path": "shared_operational_board_service.write_shared_operational_board_entry"},
        {"source_kind": "memory_notebook_document", "layer": "notes_documents_retrieval", "ingestion_path": "memory notebook namespace"},
        {"source_kind": "uploaded_knowledge", "layer": "notes_documents_retrieval", "ingestion_path": "workspace_knowledge_dir"},
        {"source_kind": "install_private_memory", "layer": "specialist_scoped_memory", "ingestion_path": "install-scoped memory and context files"},
        {"source_kind": "hybrid_summary_bridge_payload", "layer": "cloud_synced_memory", "ingestion_path": "memory_service.publish_hybrid_summary_bridge_payload"},
    ]


def _profile_layer(
    *,
    workspace_id: str,
    audience: str,
    agent_install_id: str | None = None,
) -> Dict[str, Any]:
    accessible = audience == "sage"
    layer = _base_layer("profile_memory", accessible=accessible, scope=audience)
    if accessible:
        user_text = _meaningful_context_content(
            "USER.md",
            workspace_context.read_workspace_context_file("USER.md", workspace_id=workspace_id),
        )
        if user_text:
            layer["items"].append(
                {
                    "source": "USER.md",
                    "scope": "workspace",
                    "text": user_text,
                }
            )
    else:
        install_text = _meaningful_context_content(
            "USER.md",
            workspace_context.read_workspace_context_file(
                "USER.md",
                workspace_id=workspace_id,
                agent_install_id=agent_install_id,
            ),
        )
        if install_text:
            layer["accessible"] = True
            layer["items"].append(
                {
                    "source": "USER.md",
                    "scope": "install",
                    "text": install_text,
                }
            )
    layer["summary"] = {
        "count": len(layer["items"]),
        "policy": "Shared owner profile is visible to Sage by default and withheld from specialists unless install-specific or explicitly shared.",
    }
    return layer


def _episodic_layer(*, workspace_id: str, audience: str, agent_install_id: str | None = None) -> Dict[str, Any]:
    layer = _base_layer("episodic_memory", accessible=True, scope=audience)
    transcript_items = session_transcript_store.list_session_transcript_summaries(
        workspace_id=workspace_id,
        agent_install_id=agent_install_id if audience == "specialist" else None,
        limit=4,
    )
    for item in transcript_items:
        layer["items"].append(
            {
                "source": "session_transcript",
                "timestamp": item.get("timestamp"),
                "thread_id": item.get("thread_id"),
                "provider": item.get("provider"),
                "model": item.get("model"),
                "summary": item.get("summary"),
            }
        )
    recent_logs = memory_service.get_recent_logs(
        workspace_id,
        days=7,
        agent_install_id=agent_install_id if audience == "specialist" else None,
    )
    for block in _split_recent_log_excerpt(recent_logs, limit=4):
        layer["items"].append({"source": "daily_log", "text": _compact_text(block, limit=420)})
    layer["summary"] = {
        "count": len(layer["items"]),
        "transcript_count": len(transcript_items),
        "log_block_count": max(0, len(layer["items"]) - len(transcript_items)),
    }
    return layer


async def _app_event_layer(
    *,
    tenant_id: str,
    workspace_id: str,
    audience: str,
    agent_install_id: str | None = None,
) -> Dict[str, Any]:
    layer = _base_layer("app_event_history", accessible=True, scope=audience)
    events = await personal_context_engine.list_events(
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        audience="sage" if audience == "sage" else "specialist",
        agent_install_id=agent_install_id if audience == "specialist" else None,
        limit=12,
        unseen_only=False,
    )
    layer["items"] = list(events)
    layer["summary"] = personal_context_engine.summarize_context_feed(events)
    return layer


def _shared_operational_board_layer(*, workspace_id: str, audience: str) -> Dict[str, Any]:
    layer = _base_layer("shared_operational_board", accessible=True, scope=audience)
    entries = memory_service.list_shared_operational_board_entries(
        workspace_id,
        actor_permission="read",
        limit=12,
    )
    layer["items"] = [
        {
            "source": "shared_operational_board",
            "entry_id": str(item.get("entry_id") or "").strip() or None,
            "entry_kind": str(item.get("entry_kind") or "").strip() or None,
            "title": str(item.get("title") or "").strip() or None,
            "body": str(item.get("body") or "").strip() or None,
            "links": list(item.get("links") or []),
            "artifacts": list(item.get("artifacts") or []),
            "revision_number": int(item.get("revision_number") or 0),
            "created_at": str(item.get("created_at") or "").strip() or None,
            "published_at": str(item.get("published_at") or "").strip() or None,
            "status": str(item.get("status") or "").strip() or None,
        }
        for item in entries
    ]
    layer["summary"] = {
        "count": len(layer["items"]),
        "entry_kinds": sorted(
            {
                str(item.get("entry_kind") or "").strip()
                for item in layer["items"]
                if str(item.get("entry_kind") or "").strip()
            }
        ),
        "policy": "Only explicit shared board content is exposed here. Captain and specialist private memory are never auto-imported into this layer.",
    }
    return layer


def _notes_and_documents_layer(
    *,
    workspace_id: str,
    audience: str,
    agent_install_id: str | None = None,
    retrieval_query: str = "",
) -> Dict[str, Any]:
    layer = _base_layer("notes_documents_retrieval", accessible=True, scope=audience)
    notebook_files = memory_service.list_memory_notebook_files(
        workspace_id,
        agent_install_id=agent_install_id if audience == "specialist" else None,
    )
    knowledge_files = _knowledge_documents(
        workspace_id=workspace_id,
        agent_install_id=agent_install_id if audience == "specialist" else None,
    )
    retrieval_snippets = (
        search_unified_memory_documents(
            workspace_id=workspace_id,
            query=retrieval_query,
            agent_install_id=agent_install_id if audience == "specialist" else None,
            limit=4,
        )
        if str(retrieval_query or "").strip()
        else _recent_document_snippets(
            workspace_id=workspace_id,
            agent_install_id=agent_install_id if audience == "specialist" else None,
            limit=4,
        )
    )
    layer["items"] = list(retrieval_snippets)
    layer["summary"] = {
        "indexed_document_count": len(notebook_files) + len(knowledge_files),
        "memory_notebook_count": len(notebook_files),
        "knowledge_document_count": len(knowledge_files),
        "retrieval_snippet_count": len(retrieval_snippets),
    }
    layer["indexed_documents"] = [
        {
            "path": str(item.get("path") or "").strip(),
            "source": "memory_notebook",
            "size": int(item.get("size") or 0),
            "updated_at": float(item.get("updated_at") or 0.0),
        }
        for item in notebook_files
    ] + [
        {
            "path": str(item.get("path") or "").strip(),
            "source": "knowledge",
            "size": int(item.get("size") or 0),
            "updated_at": float(item.get("updated_at") or 0.0),
            "mime_type": str(item.get("mime_type") or "").strip(),
        }
        for item in knowledge_files
    ]
    return layer


def _specialist_layer_for_sage() -> Dict[str, Any]:
    layer = _base_layer("specialist_scoped_memory", accessible=False, scope="sage")
    layer["summary"] = {
        "count": 0,
        "policy": "Specialist private memory stays isolated. Sage reaches it only through an explicit brokered path, not by default aggregation.",
    }
    return layer


def _specialist_layer_for_install(*, workspace_id: str, agent_install_id: str) -> Dict[str, Any]:
    layer = _base_layer("specialist_scoped_memory", accessible=True, scope="specialist")
    snapshot = memory_service.workspace_memory_snapshot(workspace_id, agent_install_id=agent_install_id).as_payload()
    recent_logs = memory_service.get_recent_logs(workspace_id, days=7, agent_install_id=agent_install_id)
    layer["items"] = [
        {"source": "workspace_memory_snapshot", "snapshot": snapshot},
    ]
    for block in _split_recent_log_excerpt(recent_logs, limit=3):
        layer["items"].append({"source": "install_daily_log", "text": _compact_text(block, limit=420)})
    layer["summary"] = {
        "count": len(layer["items"]),
        "agent_install_id": agent_install_id,
        "entry_count": len(list(snapshot.get("entries") or [])),
        "shared_context_sources": [],
        "policy": "Specialists receive install-scoped memory by default. Shared memory must be explicitly brokered.",
    }
    return layer


def _local_private_layer(*, workspace_id: str, audience: str, agent_install_id: str | None = None) -> Dict[str, Any]:
    accessible = audience == "sage"
    layer = _base_layer("local_private_memory", accessible=accessible, scope=audience)
    scope_install_id = agent_install_id if audience == "specialist" else None
    context_files = workspace_context.read_workspace_context_files(
        workspace_id=workspace_id,
        agent_install_id=scope_install_id,
    )
    meaningful_files = [
        filename
        for filename, content in context_files.items()
        if _meaningful_context_content(filename, content)
    ]
    notebook_count = len(memory_service.list_memory_notebook_files(workspace_id, agent_install_id=scope_install_id))
    knowledge_count = len(_knowledge_documents(workspace_id=workspace_id, agent_install_id=scope_install_id))
    layer["items"] = [
        {"source": "workspace_context_files", "paths": meaningful_files},
        {"source": "memory_notebook", "count": notebook_count},
        {"source": "knowledge_dir", "count": knowledge_count},
    ]
    if not accessible:
        layer["items"] = []
    layer["summary"] = {
        "local_sources": len(meaningful_files) + notebook_count + knowledge_count,
        "policy": "This layer stays on the local runtime and is never cloud-synced by default.",
    }
    return layer


def _hybrid_summary_bridge_items(*, workspace_id: str, audience: str) -> List[Dict[str, Any]]:
    if audience != "sage":
        return []
    records = memory_service.list_hybrid_summary_bridge_payloads(
        workspace_id,
        limit=8,
    )
    items: List[Dict[str, Any]] = []
    for record in records:
        items.append(
            {
                "source": "hybrid_summary_bridge",
                "payload_class": str(record.get("payload_class") or "").strip(),
                "summary": str(record.get("summary") or "").strip(),
                "items": list(record.get("items") or []),
                "memory_layer": str(record.get("memory_layer") or "").strip() or None,
                "agent_install_id": str(record.get("agent_install_id") or "").strip() or None,
                "run_id": str(record.get("run_id") or "").strip() or None,
                "last_safe": bool(record.get("last_safe")),
                "created_at": str(record.get("created_at") or "").strip() or None,
            }
        )
    return items


def _cloud_synced_layer(*, workspace_id: str, event_layer: Dict[str, Any], audience: str) -> Dict[str, Any]:
    layer = _base_layer("cloud_synced_memory", accessible=True, scope=audience)
    layer["items"] = [
        {
            "source": "personal_context_events",
            "count": len(list(event_layer.get("items") or [])),
            "summary": dict(event_layer.get("summary") or {}),
        }
    ]
    bridge_items = _hybrid_summary_bridge_items(workspace_id=workspace_id, audience=audience)
    layer["items"].extend(bridge_items)
    layer["summary"] = {
        "count": len(layer["items"]),
        "synced_sources": ["personal_context_events", "hybrid_summary_bridge"] if bridge_items else ["personal_context_events"],
        "hybrid_summary_bridge_count": len(bridge_items),
        "last_safe_summary_available": any(bool(item.get("last_safe")) for item in bridge_items),
        "policy": "Only cloud-safe structured memory classes are present in this layer by default.",
    }
    return layer


def _payload_summary(layers: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    return {
        "layer_count": len(layers),
        "accessible_layers": [
            layer_id for layer_id, layer in layers.items()
            if bool(layer.get("accessible"))
        ],
        "item_counts": {
            layer_id: len(list(layer.get("items") or []))
            for layer_id, layer in layers.items()
        },
    }


async def build_sage_memory_payload(
    *,
    tenant_id: str,
    workspace_id: str,
    retrieval_query: str = "",
    viewer_role: str = "sage",
    viewer_install_id: str | None = None,
) -> Dict[str, Any]:
    _enforce_sage_memory_viewer(viewer_role=viewer_role, viewer_install_id=viewer_install_id)
    profile_layer = _profile_layer(workspace_id=workspace_id, audience="sage")
    episodic_layer = _episodic_layer(workspace_id=workspace_id, audience="sage")
    event_layer = await _app_event_layer(
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        audience="sage",
    )
    shared_board_layer = _shared_operational_board_layer(
        workspace_id=workspace_id,
        audience="sage",
    )
    notes_layer = _notes_and_documents_layer(
        workspace_id=workspace_id,
        audience="sage",
        retrieval_query=retrieval_query,
    )
    specialist_layer = _specialist_layer_for_sage()
    local_private_layer = _local_private_layer(workspace_id=workspace_id, audience="sage")
    cloud_synced_layer = _cloud_synced_layer(workspace_id=workspace_id, event_layer=event_layer, audience="sage")
    layers = {
        "profile_memory": profile_layer,
        "episodic_memory": episodic_layer,
        "app_event_history": event_layer,
        "shared_operational_board": shared_board_layer,
        "notes_documents_retrieval": notes_layer,
        "specialist_scoped_memory": specialist_layer,
        "local_private_memory": local_private_layer,
        "cloud_synced_memory": cloud_synced_layer,
    }
    return {
        "generated_at": _utc_now_iso(),
        "audience": "sage",
        "workspace_id": workspace_id,
        "tenant_id": tenant_id,
        "layer_order": list(UNIFIED_MEMORY_LAYER_ORDER),
        "state_layer_model_order": list(STATE_LAYER_MODEL_ORDER),
        "state_layer_model": _state_layer_model(audience="sage"),
        "layers": layers,
        "boundary_map": _boundary_map(),
        "ingestion_contract": _ingestion_contract(),
        "service_boundaries": _service_boundaries(),
        "summary": _payload_summary(layers),
    }


async def build_specialist_memory_payload(
    *,
    tenant_id: str,
    workspace_id: str,
    agent_install_id: str,
    retrieval_query: str = "",
    viewer_role: str = "specialist",
    viewer_install_id: str | None = None,
) -> Dict[str, Any]:
    normalized_agent_install_id = str(agent_install_id or "").strip()
    if not normalized_agent_install_id:
        raise ValueError("agent_install_id is required.")
    _enforce_specialist_memory_viewer(
        requested_install_id=normalized_agent_install_id,
        viewer_role=viewer_role,
        viewer_install_id=viewer_install_id,
    )
    profile_layer = _profile_layer(
        workspace_id=workspace_id,
        audience="specialist",
        agent_install_id=normalized_agent_install_id,
    )
    episodic_layer = _episodic_layer(
        workspace_id=workspace_id,
        audience="specialist",
        agent_install_id=normalized_agent_install_id,
    )
    event_layer = await _app_event_layer(
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        audience="specialist",
        agent_install_id=normalized_agent_install_id,
    )
    shared_board_layer = _shared_operational_board_layer(
        workspace_id=workspace_id,
        audience="specialist",
    )
    notes_layer = _notes_and_documents_layer(
        workspace_id=workspace_id,
        audience="specialist",
        agent_install_id=normalized_agent_install_id,
        retrieval_query=retrieval_query,
    )
    specialist_layer = _specialist_layer_for_install(
        workspace_id=workspace_id,
        agent_install_id=normalized_agent_install_id,
    )
    local_private_layer = _local_private_layer(
        workspace_id=workspace_id,
        audience="specialist",
        agent_install_id=normalized_agent_install_id,
    )
    cloud_synced_layer = _cloud_synced_layer(workspace_id=workspace_id, event_layer=event_layer, audience="specialist")
    layers = {
        "profile_memory": profile_layer,
        "episodic_memory": episodic_layer,
        "app_event_history": event_layer,
        "shared_operational_board": shared_board_layer,
        "notes_documents_retrieval": notes_layer,
        "specialist_scoped_memory": specialist_layer,
        "local_private_memory": local_private_layer,
        "cloud_synced_memory": cloud_synced_layer,
    }
    return {
        "generated_at": _utc_now_iso(),
        "audience": "specialist",
        "workspace_id": workspace_id,
        "tenant_id": tenant_id,
        "agent_install_id": normalized_agent_install_id,
        "layer_order": list(UNIFIED_MEMORY_LAYER_ORDER),
        "state_layer_model_order": list(STATE_LAYER_MODEL_ORDER),
        "state_layer_model": _state_layer_model(audience="specialist", agent_install_id=normalized_agent_install_id),
        "layers": layers,
        "boundary_map": _boundary_map(),
        "ingestion_contract": _ingestion_contract(),
        "service_boundaries": _service_boundaries(),
        "summary": _payload_summary(layers),
    }
