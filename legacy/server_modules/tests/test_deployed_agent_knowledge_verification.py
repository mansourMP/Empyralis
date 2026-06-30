from __future__ import annotations

import asyncio
import unittest
from unittest.mock import AsyncMock, patch

from server_modules import deployed_agent_service


class DeployedAgentKnowledgeVerificationTests(unittest.TestCase):
    def test_verification_returns_real_retrieved_chunks_when_indexed(self):
        with patch(
            "server_modules.deployed_agent_service.auth_module.enforce_workspace_access",
            return_value="ws-1",
        ), patch(
            "server_modules.deployed_agent_service.control_plane_repository.get_workspace_by_id",
            new=AsyncMock(return_value={"id": "ws-1", "tenant_id": "tenant-1"}),
        ), patch(
            "server_modules.deployed_agent_service.control_plane_repository.get_deployed_agent_by_id",
            new=AsyncMock(return_value={
                "id": "agent-1",
                "owner_workspace_id": "ws-1",
                "deployment_state": "draft",
                "knowledge_sources": [
                    {
                        "id": "policy-doc",
                        "label": "Refund Policy",
                        "kind": "document",
                        "uri": "drive://refund-policy",
                    },
                    {
                        "id": "menu-sheet",
                        "label": "Daily Menu",
                        "kind": "google_sheet",
                        "uri": "sheet://menu",
                    },
                ],
            }),
        ), patch(
            "server_modules.deployed_agent_service.knowledge_rag_service.ingest_workspace_knowledge_files",
            new=AsyncMock(return_value={"source_count": 1, "chunk_count": 1}),
        ), patch(
            "server_modules.deployed_agent_service.knowledge_rag_service.retrieve_knowledge",
            new=AsyncMock(return_value={
                "status": "retrieval_available",
                "message": "Retrieved source-grounded knowledge chunks.",
                "content_retrieval_available": True,
                "matched_chunks": [
                    {
                        "id": "chunk-1",
                        "source_id": "policy-doc",
                        "chunk_text": "Refund Policy: Refunds are available within 30 days.",
                        "citation": {"label": "Refund Policy", "path": "knowledge/refunds.md"},
                        "score": 0.91,
                    }
                ],
                "retrieved_chunk_ids": ["chunk-1"],
                "confidence_score": 0.91,
            }),
        ):
            result = asyncio.run(
                deployed_agent_service.verify_deployed_agent_knowledge_retrieval(
                    deployed_agent_id="agent-1",
                    current_user={"user_id": "user-1"},
                    owner_workspace_id="ws-1",
                    query="refund policy",
                )
            )

        self.assertEqual(result["status"], "retrieval_available")
        self.assertEqual(result["verification_kind"], "content_retrieval")
        self.assertTrue(result["content_retrieval_available"])
        self.assertEqual(result["source_count"], 2)
        self.assertEqual(result["matched_sources"][0]["id"], "policy-doc")
        self.assertGreaterEqual(result["matched_sources"][0]["score"], 1)
        self.assertEqual(result["matched_chunks"][0]["id"], "chunk-1")
        self.assertEqual(result["confidence_score"], 0.91)

    def test_verification_reports_index_missing_without_pretending_retrieval(self):
        with patch(
            "server_modules.deployed_agent_service.auth_module.enforce_workspace_access",
            return_value="ws-1",
        ), patch(
            "server_modules.deployed_agent_service.control_plane_repository.get_workspace_by_id",
            new=AsyncMock(return_value={"id": "ws-1", "tenant_id": "tenant-1"}),
        ), patch(
            "server_modules.deployed_agent_service.control_plane_repository.get_deployed_agent_by_id",
            new=AsyncMock(return_value={
                "id": "agent-1",
                "owner_workspace_id": "ws-1",
                "deployment_state": "draft",
                "knowledge_sources": [
                    {
                        "id": "menu-sheet",
                        "label": "Daily Menu",
                        "kind": "google_sheet",
                        "uri": "sheet://menu",
                    },
                ],
            }),
        ), patch(
            "server_modules.deployed_agent_service.knowledge_rag_service.ingest_workspace_knowledge_files",
            new=AsyncMock(return_value={"source_count": 0, "chunk_count": 0}),
        ), patch(
            "server_modules.deployed_agent_service.knowledge_rag_service.retrieve_knowledge",
            new=AsyncMock(return_value={
                "status": "index_missing",
                "message": "No indexed knowledge chunks are available.",
                "content_retrieval_available": False,
                "matched_chunks": [],
                "retrieved_chunk_ids": [],
                "confidence_score": 0.0,
            }),
        ):
            result = asyncio.run(
                deployed_agent_service.verify_deployed_agent_knowledge_retrieval(
                    deployed_agent_id="agent-1",
                    current_user={"user_id": "user-1"},
                    owner_workspace_id="ws-1",
                    query="refund policy",
                )
            )

        self.assertEqual(result["status"], "index_missing")
        self.assertFalse(result["content_retrieval_available"])
        self.assertEqual(result["matched_sources"], [])
        self.assertNotIn("not wired yet", result["message"])
        self.assertEqual(result["matched_chunks"], [])

    def test_reference_verification_rejects_empty_query(self):
        with patch(
            "server_modules.deployed_agent_service.auth_module.enforce_workspace_access",
            return_value="ws-1",
        ):
            with self.assertRaises(ValueError):
                asyncio.run(
                    deployed_agent_service.verify_deployed_agent_knowledge_retrieval(
                        deployed_agent_id="agent-1",
                        current_user={"user_id": "user-1"},
                        owner_workspace_id="ws-1",
                        query=" ",
                    )
                )


if __name__ == "__main__":
    unittest.main()
