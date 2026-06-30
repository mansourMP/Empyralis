import unittest

from server_modules.agent_turn import build_inbound_agent_turn_request
from server_modules.api_contract import (
    ApiAgentTurnRequest,
    CANONICAL_API_ENDPOINTS,
    build_turn_chat_body,
    normalize_session_record,
    normalize_agent_turn_result,
    request_body_to_turn_request,
)


class ApiContractTests(unittest.TestCase):
    def test_request_body_to_turn_request_round_trips_contract(self):
        body = ApiAgentTurnRequest(
            tenant_id="tenant-1",
            workspace_id="workspace-1",
            session_id="session-1",
            channel="web",
            actor={"type": "user", "id": "user-1", "display_name": "Alice"},
            message="hello",
            context_hints={"provider": "openai", "model": "gpt-test"},
        )

        request = request_body_to_turn_request(body)

        self.assertEqual(request.workspace_id, "workspace-1")
        self.assertEqual(request.actor.id, "user-1")
        self.assertEqual(request.context_hints["provider"], "openai")

    def test_build_turn_chat_body_preserves_runtime_hints(self):
        turn_request = build_inbound_agent_turn_request(
            workspace_id="workspace-1",
            session_id="thread-1",
            channel="web",
            actor_type="user",
            actor_id="user-1",
            message="hello",
            context_hints={
                "provider": "openai",
                "model": "gpt-test",
                "reasoning_effort": "high",
                "prior_messages": [{"role": "user", "content": "earlier"}],
            },
        )

        payload = build_turn_chat_body(turn_request)

        self.assertEqual(payload["workspace_id"], "workspace-1")
        self.assertEqual(payload["thread_id"], "thread-1")
        self.assertEqual(payload["provider"], "openai")
        self.assertEqual(payload["model"], "gpt-test")
        self.assertEqual(payload["reasoning_effort"], "high")
        self.assertEqual(len(payload["prior_messages"]), 1)

    def test_normalize_agent_turn_result_shapes_durable_run(self):
        turn_request = build_inbound_agent_turn_request(
            workspace_id="workspace-1",
            session_id="session-1",
            channel="web",
            actor_type="user",
            actor_id="user-1",
            message="hello",
            execution_mode="durable",
            response_mode="artifact",
        )

        payload = normalize_agent_turn_result(
            {
                "kind": "durable_run",
                "result": {
                    "run_id": "run-1",
                    "status": "starting",
                    "engine": "orion",
                },
            },
            turn_request=turn_request,
        )

        self.assertTrue(payload.ok)
        self.assertEqual(payload.run_id, "run-1")
        self.assertEqual(payload.status, "starting")
        self.assertEqual(payload.metadata["kind"], "durable_run")

    def test_normalize_session_record_shapes_canonical_response(self):
        payload = normalize_session_record(
            {
                "session_id": "session-1",
                "workspace_id": "workspace-1",
                "tenant_id": "tenant-1",
                "channel": "web",
                "actor": {"type": "user", "id": "user-1"},
                "created_at": "2026-04-06T00:00:00Z",
                "expires_at": "2026-04-07T00:00:00Z",
                "metadata": {"source": "test"},
                "status": "active",
            }
        )

        self.assertEqual(payload.session_id, "session-1")
        self.assertEqual(payload.actor["id"], "user-1")
        self.assertEqual(payload.metadata["source"], "test")

    def test_canonical_api_contract_documents_standalone_approvals_endpoint(self):
        self.assertEqual(
            CANONICAL_API_ENDPOINTS["approvals_resolve"]["path"],
            "/approvals/{approval_id}/resolve",
        )


if __name__ == "__main__":
    unittest.main()
