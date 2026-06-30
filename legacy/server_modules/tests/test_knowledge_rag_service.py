from __future__ import annotations

import pytest

from server_modules import control_plane_repository
from server_modules import knowledge_rag_service


def test_control_plane_schema_declares_first_party_rag_tables() -> None:
    schema = control_plane_repository.CONTROL_PLANE_SCHEMA_SQL

    assert "CREATE TABLE IF NOT EXISTS knowledge_sources" in schema
    assert "CREATE TABLE IF NOT EXISTS knowledge_chunks" in schema
    assert "CREATE TABLE IF NOT EXISTS knowledge_embeddings" in schema
    assert "CREATE TABLE IF NOT EXISTS knowledge_retrieval_events" in schema
    assert "idx_knowledge_retrieval_events_scope_created" in schema


def test_chunk_text_preserves_offsets_and_token_estimates() -> None:
    text = "Refund policy applies within 30 days. " * 120

    chunks = knowledge_rag_service._chunk_text(text, max_chars=420, overlap_chars=40)

    assert len(chunks) > 1
    assert chunks[0]["chunk_index"] == 0
    assert chunks[0]["source_start"] == 0
    assert chunks[0]["source_end"] <= len(text)
    assert "Refund policy" in chunks[0]["chunk_text"]


def test_read_supported_text_file_rejects_oversized_files_before_loading(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    source = tmp_path / "huge.json"
    source.write_text('{"body":"' + ("x" * 64) + '"}', encoding="utf-8")
    monkeypatch.setenv("EMPYRALIS_RAG_MAX_SOURCE_FILE_BYTES", "32")

    with pytest.raises(knowledge_rag_service.KnowledgeSourceTooLargeError):
        knowledge_rag_service._read_supported_text_file(source)


@pytest.mark.asyncio
async def test_ingest_workspace_knowledge_files_chunks_and_records(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    knowledge_dir = tmp_path / "knowledge"
    knowledge_dir.mkdir()
    (knowledge_dir / "refunds.md").write_text(
        "# Refund Policy\n\nRefunds are available within 30 days with receipt.\n",
        encoding="utf-8",
    )
    calls: dict[str, object] = {}

    async def fake_upsert_source(**kwargs):
        calls["source"] = kwargs
        return {"id": kwargs["source_id"], "source_uri": kwargs["source_uri"]}

    async def fake_replace_chunks(**kwargs):
        calls["chunks"] = kwargs["chunks"]
        return kwargs["chunks"]

    monkeypatch.setattr(knowledge_rag_service.workspace_context, "workspace_knowledge_dir", lambda **_: knowledge_dir)
    monkeypatch.setattr(knowledge_rag_service.control_plane_repository, "upsert_knowledge_source", fake_upsert_source)
    monkeypatch.setattr(knowledge_rag_service.control_plane_repository, "replace_knowledge_source_chunks", fake_replace_chunks)
    monkeypatch.setattr(knowledge_rag_service, "_upsert_lancedb_source_chunks", lambda source_id, chunks: {"enabled": False, "stored": len(chunks)})

    result = await knowledge_rag_service.ingest_workspace_knowledge_files(
        tenant_id="tenant-1",
        workspace_id="ws-1",
        agent_id="agent-1",
        user_id="user-1",
    )

    assert result["source_count"] == 1
    assert result["chunk_count"] >= 1
    assert calls["source"]["source_uri"] == "knowledge://refunds.md"
    first_chunk = calls["chunks"][0]
    assert first_chunk["embedding"]
    assert first_chunk["embedding_model"] == knowledge_rag_service.HASH_EMBEDDING_MODEL
    assert first_chunk["citation"]["path"] == "knowledge/refunds.md"


@pytest.mark.asyncio
async def test_ingest_workspace_knowledge_files_filters_to_matching_sources(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    knowledge_dir = tmp_path / "knowledge"
    (knowledge_dir / "business-agents" / "agent-a").mkdir(parents=True)
    (knowledge_dir / "business-agents" / "agent-b").mkdir(parents=True)
    (knowledge_dir / "business-agents" / "agent-a" / "a.md").write_text("Agent A private policy", encoding="utf-8")
    (knowledge_dir / "business-agents" / "agent-b" / "b.md").write_text("Agent B public policy", encoding="utf-8")
    (knowledge_dir / "business-agents" / "agent-b" / "extra.txt").write_text("Agent B hours", encoding="utf-8")
    (knowledge_dir / "shared.md").write_text("Shared workspace note", encoding="utf-8")
    indexed_sources: list[str] = []

    async def fake_upsert_source(**kwargs):
        indexed_sources.append(kwargs["source_uri"])
        return {"id": kwargs["source_id"], "source_uri": kwargs["source_uri"]}

    async def fake_replace_chunks(**kwargs):
        return kwargs["chunks"]

    monkeypatch.setattr(knowledge_rag_service.workspace_context, "workspace_knowledge_dir", lambda **_: knowledge_dir)
    monkeypatch.setattr(knowledge_rag_service.control_plane_repository, "upsert_knowledge_source", fake_upsert_source)
    monkeypatch.setattr(knowledge_rag_service.control_plane_repository, "replace_knowledge_source_chunks", fake_replace_chunks)
    monkeypatch.setattr(knowledge_rag_service, "_upsert_lancedb_source_chunks", lambda source_id, chunks: {"enabled": False, "stored": len(chunks)})

    result = await knowledge_rag_service.ingest_workspace_knowledge_files(
        tenant_id="tenant-1",
        workspace_id="ws-1",
        agent_id="agent-b",
        user_id="user-1",
        source_refs=["knowledge://business-agents/agent-b"],
    )

    assert result["source_count"] == 2
    assert indexed_sources == [
        "knowledge://business-agents/agent-b/b.md",
        "knowledge://business-agents/agent-b/extra.txt",
    ]


@pytest.mark.asyncio
async def test_ingest_workspace_knowledge_files_skips_oversized_sources(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    knowledge_dir = tmp_path / "knowledge"
    knowledge_dir.mkdir()
    (knowledge_dir / "huge.csv").write_text("name,notes\nalice," + ("x" * 128), encoding="utf-8")
    monkeypatch.setenv("EMPYRALIS_RAG_MAX_SOURCE_FILE_BYTES", "32")
    monkeypatch.setattr(knowledge_rag_service.workspace_context, "workspace_knowledge_dir", lambda **_: knowledge_dir)

    result = await knowledge_rag_service.ingest_workspace_knowledge_files(
        tenant_id="tenant-1",
        workspace_id="ws-1",
        agent_id="agent-1",
        user_id="user-1",
    )

    assert result["source_count"] == 0
    assert result["skipped"] == [
        {
            "path": "knowledge/huge.csv",
            "reason": "file_too_large",
            "error": "knowledge source file exceeds max size (145 bytes > 32 bytes)",
            "max_bytes": 32,
        }
    ]


@pytest.mark.asyncio
async def test_hybrid_retrieval_ranks_exact_keyword_hit_and_writes_ledger(monkeypatch: pytest.MonkeyPatch) -> None:
    refund_embedding, model = knowledge_rag_service.embed_text("refund policy receipt")
    weather_embedding, _ = knowledge_rag_service.embed_text("weather forecast")
    events: dict[str, object] = {}

    async def fake_chunks(**kwargs):
        return [
            {
                "id": "chunk-weather",
                "source_id": "source-weather",
                "source_uri": "knowledge://weather.md",
                "source_label": "weather.md",
                "chunk_index": 0,
                "chunk_text": "The weather forecast is sunny.",
                "embedding": weather_embedding,
                "embedding_model": model,
                "citation": {"path": "knowledge/weather.md", "label": "weather.md"},
            },
            {
                "id": "chunk-refund",
                "source_id": "source-refund",
                "source_uri": "knowledge://refunds.md",
                "source_label": "refunds.md",
                "chunk_index": 0,
                "chunk_text": "Refund policy: customers can return items within 30 days with a receipt.",
                "embedding": refund_embedding,
                "embedding_model": model,
                "citation": {"path": "knowledge/refunds.md", "label": "refunds.md"},
            },
        ]

    async def fake_retrieval_event(**kwargs):
        events["retrieval"] = kwargs
        return {"id": "kret-1", **kwargs}

    async def fake_credit_event(**kwargs):
        events["credit"] = kwargs
        return {"id": "cled-1"}

    async def fake_action_event(**kwargs):
        events["action"] = kwargs
        return {"id": "aact-1"}

    monkeypatch.setattr(knowledge_rag_service.control_plane_repository, "list_knowledge_chunks", fake_chunks)
    monkeypatch.setattr(knowledge_rag_service.control_plane_repository, "record_knowledge_retrieval_event", fake_retrieval_event)
    monkeypatch.setattr(knowledge_rag_service.control_plane_repository, "record_credit_ledger_event", fake_credit_event)
    monkeypatch.setattr(knowledge_rag_service.agent_action_metering_service, "record_completed", fake_action_event)

    result = await knowledge_rag_service.retrieve_knowledge(
        tenant_id="tenant-1",
        workspace_id="ws-1",
        query="refund policy receipt",
        surface="studio",
        source_surface="studio_knowledge_verify",
        agent_id="agent-1",
        allowed_source_refs=["knowledge://refunds.md"],
        top_k=2,
    )

    assert result["status"] == "retrieval_available"
    assert result["matched_chunks"][0]["id"] == "chunk-refund"
    assert result["content_retrieval_available"] is True
    assert events["retrieval"]["retrieved_chunk_ids"] == ["chunk-refund"]
    assert events["credit"]["event"]["credit_type"] == "knowledge_retrieval"
    assert events["credit"]["event"]["credits_debited"] == 0.0
    assert events["action"]["action_domain"] == "rag"
    assert events["action"]["usage_ref"]["source_event_id"] == "kret-1"


@pytest.mark.asyncio
async def test_retrieval_reports_index_missing_for_unindexed_source_scope(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_chunks(**kwargs):
        return []

    async def fake_retrieval_event(**kwargs):
        return {"id": "kret-1", **kwargs}

    async def fake_credit_event(**kwargs):
        return {"id": "cled-1"}

    async def fake_action_event(**kwargs):
        return {"id": "aact-1"}

    monkeypatch.setattr(knowledge_rag_service.control_plane_repository, "list_knowledge_chunks", fake_chunks)
    monkeypatch.setattr(knowledge_rag_service.control_plane_repository, "record_knowledge_retrieval_event", fake_retrieval_event)
    monkeypatch.setattr(knowledge_rag_service.control_plane_repository, "record_credit_ledger_event", fake_credit_event)
    monkeypatch.setattr(knowledge_rag_service.agent_action_metering_service, "record_completed", fake_action_event)

    result = await knowledge_rag_service.retrieve_knowledge(
        tenant_id="tenant-1",
        workspace_id="ws-1",
        query="refund policy",
        surface="studio",
        agent_id="agent-1",
        allowed_source_refs=["knowledge://refunds.md"],
    )

    assert result["status"] == "index_missing"
    assert result["content_retrieval_available"] is False
    assert result["matched_chunks"] == []
