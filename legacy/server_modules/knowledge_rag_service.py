from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
import math
import os
from pathlib import Path
import re
import time
from typing import Any, Dict, Iterable, List, Optional, Sequence

from server_modules import agent_action_metering_service, control_plane_repository
from server_modules import workspace_context


SUPPORTED_KNOWLEDGE_EXTENSIONS = {".md", ".markdown", ".txt", ".csv", ".json"}
HASH_EMBEDDING_MODEL = "empyralis-hash-embedding-v1"
SENTENCE_TRANSFORMERS_MODEL = os.getenv("EMPYRALIS_RAG_SENTENCE_TRANSFORMERS_MODEL", "all-MiniLM-L6-v2")
RAG_EMBEDDING_BACKEND = os.getenv("EMPYRALIS_RAG_EMBEDDING_BACKEND", "hash").strip().lower()
RRF_K = 60.0
DEFAULT_CHUNK_MAX_CHARS = 1800
DEFAULT_CHUNK_OVERLAP_CHARS = 180
DEFAULT_TOP_K = 5
DEFAULT_MAX_SOURCE_FILE_BYTES = 25 * 1024 * 1024

_SENTENCE_MODEL: Any = None


class KnowledgeSourceTooLargeError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class KnowledgeChunkInput:
    id: str
    chunk_index: int
    chunk_text: str
    token_estimate: int
    content_hash: str
    source_start: int
    source_end: int
    citation: Dict[str, Any] = field(default_factory=dict)
    metadata: Dict[str, Any] = field(default_factory=dict)
    embedding: List[float] = field(default_factory=list)
    embedding_model: str = HASH_EMBEDDING_MODEL

    def to_repository_payload(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "chunk_index": self.chunk_index,
            "chunk_text": self.chunk_text,
            "token_estimate": self.token_estimate,
            "content_hash": self.content_hash,
            "source_start": self.source_start,
            "source_end": self.source_end,
            "citation": dict(self.citation),
            "metadata": dict(self.metadata),
            "embedding": list(self.embedding),
            "embedding_model": self.embedding_model,
        }


def _normalize_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip())


def _sha256_text(value: Any) -> str:
    return hashlib.sha256(str(value or "").encode("utf-8")).hexdigest()


def _tokenize(value: Any) -> List[str]:
    return [token for token in re.split(r"[^a-z0-9]+", str(value or "").lower()) if len(token) >= 2]


def _stable_source_id(*, tenant_id: str, workspace_id: str, agent_id: Optional[str], source_uri: str) -> str:
    seed = "|".join(
        [
            str(tenant_id or "").strip(),
            str(workspace_id or "").strip(),
            str(agent_id or "").strip() or "workspace",
            str(source_uri or "").strip(),
        ]
    )
    return f"ksrc_{_sha256_text(seed)[:24]}"


def _normalize_ingestion_source_ref(ref: Any) -> str:
    normalized = str(ref or "").replace("\\", "/").strip()
    if normalized.startswith("knowledge://"):
        normalized = normalized[len("knowledge://") :]
    elif normalized.startswith("knowledge/"):
        normalized = normalized[len("knowledge/") :]
    return normalized.strip("/")


def _build_ingestion_source_filter(
    source_refs: Optional[Iterable[Any]],
) -> Optional[tuple[set[str], set[str]]]:
    exact_paths: set[str] = set()
    subtree_prefixes: set[str] = set()
    for ref in list(source_refs or []):
        normalized = _normalize_ingestion_source_ref(ref)
        if not normalized:
            continue
        suffix = Path(normalized).suffix.lower()
        if suffix in SUPPORTED_KNOWLEDGE_EXTENSIONS:
            exact_paths.add(normalized)
        else:
            subtree_prefixes.add(normalized.rstrip("/") + "/")
    if not exact_paths and not subtree_prefixes:
        return None
    return exact_paths, subtree_prefixes


def _matches_ingestion_source_filter(rel_path: str, source_filter: Optional[tuple[set[str], set[str]]]) -> bool:
    if source_filter is None:
        return True
    exact_paths, subtree_prefixes = source_filter
    if rel_path in exact_paths:
        return True
    return any(rel_path.startswith(prefix) for prefix in subtree_prefixes)


def _stable_chunk_id(source_id: str, chunk_index: int, chunk_text: str) -> str:
    return f"kchk_{_sha256_text(f'{source_id}:{chunk_index}:{chunk_text}')[:24]}"


def _json_normalize_if_needed(path: Path, raw_text: str) -> str:
    if path.suffix.lower() != ".json":
        return raw_text
    try:
        parsed = json.loads(raw_text)
    except Exception:
        return raw_text
    return json.dumps(parsed, ensure_ascii=False, indent=2, sort_keys=True)


def _max_source_file_bytes() -> int:
    raw_value = os.getenv("EMPYRALIS_RAG_MAX_SOURCE_FILE_BYTES", "").strip()
    if raw_value:
        try:
            parsed = int(raw_value)
            if parsed > 0:
                return parsed
        except ValueError:
            pass
    return DEFAULT_MAX_SOURCE_FILE_BYTES


def _read_supported_text_file(path: Path) -> str:
    if path.suffix.lower() not in SUPPORTED_KNOWLEDGE_EXTENSIONS:
        return ""
    max_bytes = _max_source_file_bytes()
    try:
        file_size = path.stat().st_size
    except OSError:
        return ""
    if file_size > max_bytes:
        raise KnowledgeSourceTooLargeError(
            f"knowledge source file exceeds max size ({file_size} bytes > {max_bytes} bytes)"
        )
    try:
        raw_text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        raw_text = path.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return ""
    return _json_normalize_if_needed(path, raw_text).strip()


def _estimate_tokens(text: str) -> int:
    compact = _normalize_text(text)
    return max(1, int(math.ceil(len(compact) / 4.0))) if compact else 0


def _chunk_text(text: str, *, max_chars: int = DEFAULT_CHUNK_MAX_CHARS, overlap_chars: int = DEFAULT_CHUNK_OVERLAP_CHARS) -> List[Dict[str, Any]]:
    value = str(text or "").strip()
    if not value:
        return []
    safe_max = max(400, int(max_chars or DEFAULT_CHUNK_MAX_CHARS))
    safe_overlap = max(0, min(int(overlap_chars or DEFAULT_CHUNK_OVERLAP_CHARS), safe_max // 3))
    chunks: List[Dict[str, Any]] = []
    start = 0
    while start < len(value):
        target_end = min(len(value), start + safe_max)
        end = target_end
        if target_end < len(value):
            whitespace = value.rfind(" ", start + int(safe_max * 0.6), target_end)
            newline = value.rfind("\n", start + int(safe_max * 0.5), target_end)
            end = max(whitespace, newline, start + safe_max)
            if end <= start or end > target_end:
                end = target_end
        raw_chunk = value[start:end]
        leading_trim = len(raw_chunk) - len(raw_chunk.lstrip())
        trailing_trim = len(raw_chunk.rstrip())
        chunk_text = raw_chunk.strip()
        if chunk_text:
            chunks.append(
                {
                    "chunk_index": len(chunks),
                    "chunk_text": chunk_text,
                    "source_start": start + leading_trim,
                    "source_end": start + trailing_trim,
                }
            )
        if end >= len(value):
            break
        next_start = max(end - safe_overlap, start + 1)
        if next_start <= start:
            next_start = end
        start = next_start
    return chunks


def _hash_embedding(text: str, *, dimensions: int = 96) -> List[float]:
    vector = [0.0] * dimensions
    tokens = _tokenize(text)
    if not tokens:
        return vector
    for token in tokens:
        digest = hashlib.sha256(token.encode("utf-8")).digest()
        bucket = int.from_bytes(digest[:4], "big") % dimensions
        sign = 1.0 if digest[4] % 2 == 0 else -1.0
        vector[bucket] += sign
    magnitude = math.sqrt(sum(item * item for item in vector))
    if magnitude <= 0:
        return vector
    return [round(item / magnitude, 8) for item in vector]


def _sentence_transformers_embedding(text: str) -> Optional[List[float]]:
    global _SENTENCE_MODEL
    if RAG_EMBEDDING_BACKEND not in {"sentence_transformers", "sentence-transformers"}:
        return None
    if _SENTENCE_MODEL is False:
        return None
    if _SENTENCE_MODEL is None:
        try:
            from sentence_transformers import SentenceTransformer

            _SENTENCE_MODEL = SentenceTransformer(SENTENCE_TRANSFORMERS_MODEL)
        except Exception:
            _SENTENCE_MODEL = False
            return None
    if _SENTENCE_MODEL is False:
        return None
    vector = _SENTENCE_MODEL.encode(_normalize_text(text), normalize_embeddings=True)
    if hasattr(vector, "tolist"):
        return [float(item) for item in vector.tolist()]
    return [float(item) for item in vector]


def embed_text(text: str) -> tuple[List[float], str]:
    normalized = _normalize_text(text)
    if not normalized:
        return [], HASH_EMBEDDING_MODEL
    sentence_vector = _sentence_transformers_embedding(normalized)
    if sentence_vector:
        return sentence_vector, f"sentence-transformers/{SENTENCE_TRANSFORMERS_MODEL}"
    return _hash_embedding(normalized), HASH_EMBEDDING_MODEL


def _cosine_similarity(left: Sequence[float], right: Sequence[float]) -> float:
    if not left or not right or len(left) != len(right):
        return 0.0
    dot = sum(float(a) * float(b) for a, b in zip(left, right))
    left_mag = math.sqrt(sum(float(a) * float(a) for a in left))
    right_mag = math.sqrt(sum(float(b) * float(b) for b in right))
    if left_mag <= 0.0 or right_mag <= 0.0:
        return 0.0
    return max(0.0, min(dot / (left_mag * right_mag), 1.0))


def _source_ref_values(source: Dict[str, Any]) -> set[str]:
    values: set[str] = set()
    for key in ("id", "source_id", "uri", "path", "document_id", "record_id", "external_id", "label"):
        token = str(source.get(key) or "").strip()
        if token:
            values.add(token)
            values.add(token.lower())
            if token.startswith("knowledge://"):
                suffix = token[len("knowledge://") :].lstrip("/")
                values.add(suffix)
                values.add(f"knowledge/{suffix}")
            if token.startswith("knowledge/"):
                suffix = token[len("knowledge/") :].lstrip("/")
                values.add(suffix)
                values.add(f"knowledge://{suffix}")
    return {item for item in values if item}


def source_reference_filter_values(sources: Iterable[Dict[str, Any]]) -> set[str]:
    values: set[str] = set()
    for source in sources:
        if isinstance(source, dict):
            values.update(_source_ref_values(source))
    return values


def _chunk_ref_values(chunk: Dict[str, Any]) -> set[str]:
    values: set[str] = set()
    for key in ("source_id", "source_uri", "source_label"):
        token = str(chunk.get(key) or "").strip()
        if token:
            values.add(token)
            values.add(token.lower())
    citation = chunk.get("citation") if isinstance(chunk.get("citation"), dict) else {}
    for key in ("path", "source_uri", "label"):
        token = str(citation.get(key) or "").strip()
        if token:
            values.add(token)
            values.add(token.lower())
            if token.startswith("knowledge/"):
                values.add(token[len("knowledge/") :])
    return values


def _matches_source_filter(chunk: Dict[str, Any], allowed_refs: set[str]) -> bool:
    if not allowed_refs:
        return True
    chunk_values = _chunk_ref_values(chunk)
    normalized_refs = set(allowed_refs) | {item.lower() for item in allowed_refs}
    return bool(chunk_values & normalized_refs)


def _keyword_score(query: str, text: str) -> float:
    query_tokens = set(_tokenize(query))
    if not query_tokens:
        return 0.0
    lowered = str(text or "").lower()
    exact = 1.0 if str(query or "").strip().lower() in lowered else 0.0
    text_tokens = set(_tokenize(text))
    overlap = len(query_tokens & text_tokens) / max(1, len(query_tokens))
    return max(0.0, min(1.0, (0.7 * overlap) + (0.3 * exact)))


def _rank_candidates(query: str, chunks: List[Dict[str, Any]], *, top_k: int) -> List[Dict[str, Any]]:
    query_embedding, query_embedding_model = embed_text(query)
    vector_ranked: List[Dict[str, Any]] = []
    keyword_ranked: List[Dict[str, Any]] = []
    for chunk in chunks:
        item = dict(chunk)
        chunk_text = str(item.get("chunk_text") or "")
        embedding = item.get("embedding") if isinstance(item.get("embedding"), list) else []
        vector_score = _cosine_similarity(query_embedding, [float(value) for value in embedding]) if embedding else 0.0
        keyword = _keyword_score(query, chunk_text)
        item["vector_score"] = round(vector_score, 6)
        item["keyword_score"] = round(keyword, 6)
        item["query_embedding_model"] = query_embedding_model
        if vector_score > 0:
            vector_ranked.append(item)
        if keyword > 0:
            keyword_ranked.append(item)
    vector_ranked.sort(key=lambda item: float(item.get("vector_score") or 0.0), reverse=True)
    keyword_ranked.sort(key=lambda item: float(item.get("keyword_score") or 0.0), reverse=True)
    fused: Dict[str, Dict[str, Any]] = {}
    for ranking_name, ranked_items in (("vector", vector_ranked), ("keyword", keyword_ranked)):
        for rank, item in enumerate(ranked_items, start=1):
            chunk_id = str(item.get("id") or "").strip()
            if not chunk_id:
                continue
            current = fused.setdefault(chunk_id, dict(item))
            current[f"{ranking_name}_rank"] = rank
            current["rrf_score"] = float(current.get("rrf_score") or 0.0) + (1.0 / (RRF_K + rank))
    for item in fused.values():
        final_score = (
            float(item.get("rrf_score") or 0.0)
            + (0.35 * float(item.get("keyword_score") or 0.0))
            + (0.15 * float(item.get("vector_score") or 0.0))
        )
        item["score"] = round(final_score, 6)
    ordered = sorted(
        fused.values(),
        key=lambda item: (
            -float(item.get("score") or 0.0),
            -float(item.get("keyword_score") or 0.0),
            str(item.get("source_uri") or ""),
            int(item.get("chunk_index") or 0),
        ),
    )
    return ordered[: max(1, min(int(top_k or DEFAULT_TOP_K), 20))]


def _knowledge_lancedb_uri() -> str:
    explicit = os.getenv("EMPYRALIS_RAG_LANCEDB_URI", "").strip()
    if explicit:
        return explicit
    state_home = Path(os.getenv("EMPYRALIS_STATE_HOME", str(Path.home() / ".empyralis" / "state"))).expanduser()
    return str(state_home / "knowledge_rag" / "lancedb")


def _upsert_lancedb_source_chunks(source_id: str, chunks: List[KnowledgeChunkInput]) -> Dict[str, Any]:
    if not chunks:
        return {"enabled": False, "stored": 0}
    try:
        import lancedb
        import pandas as pd

        uri = _knowledge_lancedb_uri()
        Path(uri).mkdir(parents=True, exist_ok=True)
        db = lancedb.connect(uri)
        table_name = "knowledge_chunks"
        records = [
            {
                "id": chunk.id,
                "source_id": source_id,
                "chunk_index": chunk.chunk_index,
                "text": chunk.chunk_text,
                "vector": chunk.embedding,
                "metadata": json.dumps({"citation": chunk.citation, **chunk.metadata}, ensure_ascii=False),
            }
            for chunk in chunks
            if chunk.embedding
        ]
        if not records:
            return {"enabled": False, "stored": 0}
        if table_name in db.table_names():
            table = db.open_table(table_name)
            try:
                escaped_source_id = source_id.replace("'", "''")
                table.delete(f"source_id = '{escaped_source_id}'")
            except Exception:
                pass
            table.add(pd.DataFrame(records))
        else:
            db.create_table(table_name, data=pd.DataFrame(records))
        return {"enabled": True, "stored": len(records), "uri": uri}
    except Exception as exc:
        return {"enabled": False, "stored": 0, "error": str(exc)}


async def ingest_workspace_knowledge_files(
    *,
    tenant_id: str,
    workspace_id: str,
    agent_id: Optional[str] = None,
    user_id: Optional[str] = None,
    source_refs: Optional[Iterable[Any]] = None,
) -> Dict[str, Any]:
    root = workspace_context.workspace_knowledge_dir(workspace_id=workspace_id, agent_install_id=None)
    source_filter = _build_ingestion_source_filter(source_refs)
    files = [
        path
        for path in sorted(root.rglob("*"))
        if path.is_file()
        and path.suffix.lower() in SUPPORTED_KNOWLEDGE_EXTENSIONS
        and _matches_ingestion_source_filter(path.relative_to(root).as_posix(), source_filter)
    ]
    ingested_sources: List[Dict[str, Any]] = []
    skipped: List[Dict[str, Any]] = []
    for path in files:
        rel_path = path.relative_to(root).as_posix()
        try:
            text = _read_supported_text_file(path)
        except KnowledgeSourceTooLargeError as exc:
            skipped.append(
                {
                    "path": f"knowledge/{rel_path}",
                    "reason": "file_too_large",
                    "error": str(exc),
                    "max_bytes": _max_source_file_bytes(),
                }
            )
            continue
        if not text:
            skipped.append({"path": f"knowledge/{rel_path}", "reason": "empty_or_unsupported"})
            continue
        source_uri = f"knowledge://{rel_path}"
        source_id = _stable_source_id(
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            agent_id=agent_id,
            source_uri=source_uri,
        )
        content_hash = _sha256_text(text)
        source = await control_plane_repository.upsert_knowledge_source(
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            source_id=source_id,
            source_uri=source_uri,
            source_kind="file",
            label=rel_path,
            content_hash=content_hash,
            agent_id=agent_id,
            status="indexed",
            metadata={
                "path": f"knowledge/{rel_path}",
                "abs_path": str(path),
                "ingested_by_user_id": str(user_id or "").strip() or None,
            },
        )
        chunk_inputs: List[KnowledgeChunkInput] = []
        for chunk in _chunk_text(text):
            chunk_text = str(chunk.get("chunk_text") or "")
            embedding, embedding_model = embed_text(chunk_text)
            chunk_index = int(chunk.get("chunk_index") or 0)
            chunk_inputs.append(
                KnowledgeChunkInput(
                    id=_stable_chunk_id(source_id, chunk_index, chunk_text),
                    chunk_index=chunk_index,
                    chunk_text=chunk_text,
                    token_estimate=_estimate_tokens(chunk_text),
                    content_hash=_sha256_text(chunk_text),
                    source_start=int(chunk.get("source_start") or 0),
                    source_end=int(chunk.get("source_end") or 0),
                    citation={
                        "source_id": source_id,
                        "source_uri": source_uri,
                        "label": rel_path,
                        "path": f"knowledge/{rel_path}",
                        "start": int(chunk.get("source_start") or 0),
                        "end": int(chunk.get("source_end") or 0),
                    },
                    metadata={"content_hash": content_hash},
                    embedding=embedding,
                    embedding_model=embedding_model,
                )
            )
        inserted = await control_plane_repository.replace_knowledge_source_chunks(
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            source_id=source_id,
            chunks=[chunk.to_repository_payload() for chunk in chunk_inputs],
            agent_id=agent_id,
        )
        lancedb_status = _upsert_lancedb_source_chunks(source_id, chunk_inputs)
        ingested_sources.append(
            {
                "source_id": source_id,
                "source_uri": source_uri,
                "label": rel_path,
                "chunk_count": len(inserted) if inserted else len(chunk_inputs),
                "content_hash": content_hash,
                "source": source,
                "lancedb": lancedb_status,
            }
        )
    return {
        "workspace_id": workspace_id,
        "agent_id": str(agent_id or "").strip() or None,
        "source_count": len(ingested_sources),
        "chunk_count": sum(int(item.get("chunk_count") or 0) for item in ingested_sources),
        "sources": ingested_sources,
        "skipped": skipped,
    }


async def retrieve_knowledge(
    *,
    tenant_id: str,
    workspace_id: str,
    query: str,
    surface: str,
    source_surface: Optional[str] = None,
    agent_id: Optional[str] = None,
    user_id: Optional[str] = None,
    thread_id: Optional[str] = None,
    run_id: Optional[str] = None,
    app_id: Optional[str] = None,
    allowed_source_refs: Optional[Iterable[str]] = None,
    top_k: int = DEFAULT_TOP_K,
    payer: str = "local",
) -> Dict[str, Any]:
    started = time.perf_counter()
    normalized_query = _normalize_text(query)
    if not normalized_query:
        raise ValueError("query is required.")
    bounded_top_k = max(1, min(int(top_k or DEFAULT_TOP_K), 20))
    chunks = await control_plane_repository.list_knowledge_chunks(
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        agent_id=agent_id,
        include_workspace_scope=True,
        limit=5000,
    )
    allowed_refs = {str(item or "").strip() for item in list(allowed_source_refs or []) if str(item or "").strip()}
    scoped_chunks = [chunk for chunk in chunks if _matches_source_filter(chunk, allowed_refs)]
    ranked = _rank_candidates(normalized_query, scoped_chunks, top_k=bounded_top_k) if scoped_chunks else []
    if not chunks or (allowed_refs and not scoped_chunks):
        status = "index_missing"
        message = "No indexed knowledge chunks are available for the requested source scope."
    elif ranked:
        status = "retrieval_available"
        message = "Retrieved source-grounded knowledge chunks."
    else:
        status = "no_hits"
        message = "Indexed knowledge exists, but no chunk matched this query."
    confidence = min(1.0, max((float(item.get("score") or 0.0) for item in ranked), default=0.0))
    latency_ms = int((time.perf_counter() - started) * 1000)
    source_ids = sorted({str(item.get("source_id") or "") for item in scoped_chunks if str(item.get("source_id") or "")})
    retrieved_chunk_ids = [str(item.get("id") or "") for item in ranked if str(item.get("id") or "")]
    query_hash = _sha256_text(normalized_query)
    retrieval_event = await control_plane_repository.record_knowledge_retrieval_event(
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        surface=surface,
        source_surface=source_surface or surface,
        query_hash=query_hash,
        query_text=normalized_query[:500],
        user_id=user_id,
        thread_id=thread_id,
        run_id=run_id,
        agent_id=agent_id,
        app_id=app_id,
        source_ids=source_ids,
        retrieved_chunk_ids=retrieved_chunk_ids,
        top_k=bounded_top_k,
        confidence_score=confidence,
        status=status,
        latency_ms=latency_ms,
        metadata={
            "allowed_source_ref_count": len(allowed_refs),
            "indexed_chunk_count": len(chunks),
            "scoped_chunk_count": len(scoped_chunks),
        },
    )
    retrieval_event_id = str((retrieval_event or {}).get("id") or "").strip() or None
    await control_plane_repository.record_credit_ledger_event(
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        source_table="knowledge_retrieval_events",
        source_event_id=retrieval_event_id,
        event={
            "surface": surface,
            "source_surface": source_surface or surface,
            "payer": payer,
            "credit_type": "knowledge_retrieval",
            "workspace_id": workspace_id,
            "user_id": user_id,
            "thread_id": thread_id,
            "run_id": run_id,
            "agent_id": agent_id,
            "app_id": app_id,
            "provider_usage": {
                "retrieved_chunk_count": len(ranked),
                "scoped_chunk_count": len(scoped_chunks),
                "indexed_chunk_count": len(chunks),
            },
            "platform_cost_usd": 0.0,
            "credits_debited": 0.0,
            "estimation_mode": "local_retrieval",
            "metadata": {
                "query_hash": query_hash,
                "status": status,
                "latency_ms": latency_ms,
                "source_ids": source_ids,
                "retrieved_chunk_ids": retrieved_chunk_ids,
            },
        },
    )
    await agent_action_metering_service.record_completed(
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        surface=surface,
        source_surface=source_surface or surface,
        action_domain="rag",
        action_type="retrieve",
        action_name="knowledge.retrieve",
        tool_kind="rag_retrieval",
        payer=payer,
        billing_mode="transparency",
        credit_type="knowledge_retrieval",
        credits_debited=0.0,
        platform_cost_usd=0.0,
        usage_ref={"credit_ledger_source_table": "knowledge_retrieval_events", "source_event_id": retrieval_event_id},
        user_id=user_id,
        thread_id=thread_id,
        run_id=run_id,
        agent_id=agent_id,
        app_id=app_id,
        input_summary=normalized_query,
        output_summary=status,
        source_table="knowledge_retrieval_events",
        source_event_id=retrieval_event_id,
        metadata={
            "query_hash": query_hash,
            "status": status,
            "latency_ms": latency_ms,
            "source_ids": source_ids,
            "retrieved_chunk_ids": retrieved_chunk_ids,
            "retrieved_chunk_count": len(ranked),
        },
    )
    return {
        "status": status,
        "message": message,
        "query": normalized_query,
        "query_hash": query_hash,
        "content_retrieval_available": bool(ranked),
        "chunks": ranked,
        "matched_chunks": ranked,
        "source_ids": source_ids,
        "retrieved_chunk_ids": retrieved_chunk_ids,
        "confidence_score": round(confidence, 6),
        "top_k": bounded_top_k,
        "latency_ms": latency_ms,
        "retrieval_event": retrieval_event,
    }


def render_retrieval_context_block(results: Iterable[Dict[str, Any]], *, title: str = "Retrieved Knowledge Sources") -> str:
    lines: List[str] = []
    for index, item in enumerate(list(results or [])[:10], start=1):
        citation = item.get("citation") if isinstance(item.get("citation"), dict) else {}
        label = str(citation.get("label") or item.get("source_label") or item.get("source_uri") or "Knowledge source").strip()
        snippet = _normalize_text(item.get("chunk_text"))[:900]
        if snippet:
            lines.append(f"[{index}] {label}\n{snippet}")
    if not lines:
        return ""
    return f"{title}\n" + "\n\n".join(lines)
