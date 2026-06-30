import asyncio
import unittest
from unittest.mock import patch

from server_modules import health_core
from server_modules import runtime_models
from server_modules.runtime_models import MemorySearchRequest, MemoryUpsertRequest


class HealthCoreMemoryTests(unittest.TestCase):
    def test_memory_search_delegates_to_memory_service(self) -> None:
        body = MemorySearchRequest(
            query="customer migration",
            bucket="session",
            workspace_id="default",
            profile_id="profile-1",
            project_id="project-1",
            session_key="session-1",
            k=4,
        )
        expected = {
            "ok": True,
            "query": "customer migration",
            "bucket": "session",
            "workspace_id": "default",
            "count": 1,
            "items": [{"id": "mem-1", "text": "customer migration"}],
        }

        with patch.object(runtime_models, "_NORMALIZE_MEMORY_BUCKET", side_effect=lambda value, required=True: value), patch.object(
            health_core,
            "runtime_memory_search",
            return_value=expected,
        ) as search_mock:
            result = asyncio.run(health_core.memory_search(body))

        search_mock.assert_called_once_with(
            query="customer migration",
            bucket="session",
            workspace_id="default",
            profile_id="profile-1",
            project_id="project-1",
            session_key="session-1",
            k=4,
        )
        self.assertEqual(result, expected)

    def test_memory_upsert_delegates_to_memory_service(self) -> None:
        body = MemoryUpsertRequest(
            text="Remember the rollout checklist.",
            bucket="session",
            workspace_id="default",
            profile_id="profile-1",
            project_id="project-1",
            session_key="session-1",
            source="api",
            retention_days=14,
            metadata={"source_kind": "test"},
            id="mem-2",
        )
        expected = {
            "ok": True,
            "id": "mem-2",
            "bucket": "session",
            "workspace_id": "default",
            "retention_days": 14,
            "expires_at": "2026-04-18T00:00:00Z",
        }

        with patch.object(runtime_models, "_NORMALIZE_MEMORY_BUCKET", side_effect=lambda value, required=True: value), patch.object(
            health_core,
            "runtime_memory_upsert",
            return_value=expected,
        ) as upsert_mock:
            result = asyncio.run(health_core.memory_upsert(body))

        upsert_mock.assert_called_once_with(
            text="Remember the rollout checklist.",
            bucket="session",
            workspace_id="default",
            profile_id="profile-1",
            project_id="project-1",
            session_key="session-1",
            source="api",
            retention_days=14,
            metadata={"source_kind": "test"},
            memory_id="mem-2",
        )
        self.assertEqual(result, expected)


class HealthCoreRuntimeTests(unittest.TestCase):
    def test_health_uses_codex_profile_readiness_when_direct_openai_probe_is_unhealthy(self) -> None:
        direct_chat_snapshot = {
            "enabled": False,
            "runtime_cache": {"active_sessions": 0, "idle_ttl_ms": 0, "evicted_total": 0},
            "turns": {"active": 0, "queue_depth": 0},
            "interrupted_stale_sessions": 0,
            "errors_by_code": {},
        }
        provider_runtime_truth = {
            "providers_by_id": {
                "openai-codex": {
                    "id": "openai-codex",
                    "state": "active",
                    "usable": True,
                    "active": True,
                },
                "openai": {
                    "id": "openai",
                    "state": "setup_required",
                    "usable": False,
                    "active": False,
                },
                "anthropic": {
                    "id": "anthropic",
                    "state": "configured",
                    "usable": False,
                    "active": False,
                },
                "gemini": {
                    "id": "gemini",
                    "state": "configured",
                    "usable": False,
                    "active": False,
                },
            },
            "providers": [
                {
                    "id": "openai-codex",
                    "state": "active",
                    "usable": True,
                    "active": True,
                },
                {
                    "id": "openai",
                    "state": "setup_required",
                    "usable": False,
                    "active": False,
                },
                {
                    "id": "anthropic",
                    "state": "configured",
                    "usable": False,
                    "active": False,
                },
                {
                    "id": "gemini",
                    "state": "configured",
                    "usable": False,
                    "active": False,
                },
            ],
            "codex_profile_ready": True,
            "openai_profile_ready": False,
        }

        with patch.object(
            health_core.provider_profiles_service,
            "build_provider_runtime_truth",
            return_value=provider_runtime_truth,
        ), patch.object(
            health_core,
            "_runtime_contract_payload",
            return_value={"contract_schema_version": "2026.2.0"},
        ), patch.object(
            health_core,
            "_openai_env_bearer_with_source",
            return_value=("token", "env_codex_oauth_token"),
        ), patch.object(
            health_core,
            "probe_openai_credential",
            return_value={
                "openai_key_present": True,
                "openai_vault_present": False,
                "openai_key_valid": False,
                "openai_credential_source": "none",
                "openai_status": 403,
                "openai_error": "HTTP Error 403: Forbidden",
            },
        ), patch.object(
            health_core,
            "collect_runtime_counts",
            return_value={
                "weekly_schedules": 0,
                "setup_sessions_total": 0,
                "setup_sessions_active": 0,
                "provider_profiles_total": 1,
                "idempotency_records": 0,
            },
        ), patch.object(
            health_core,
            "_telegram_autopilot_snapshot",
            return_value={},
        ), patch.object(
            health_core,
            "_whatsapp_autopilot_snapshot",
            return_value={},
        ), patch.object(
            health_core,
            "tool_policy_snapshot",
            return_value={
                "blocked_actions": [],
                "approval_actions": [],
                "allow_actions": [],
                "sensitive_tools": [],
                "critical_tools": [],
                "block_cloud_critical": True,
                "connector_cloud_readonly": True,
                "policy_mode": "local_default",
            },
        ), patch.object(
            health_core,
            "_memory_health_snapshot",
            return_value={
                "enabled": True,
                "manager_ready": True,
                "manager_error": None,
                "db_path": "db",
                "db_exists": True,
                "db_size_bytes": 1,
                "sqlite_rows": 0,
                "sqlite_error": None,
                "lancedb_uri": "uri",
                "lancedb_initialized": True,
            },
        ), patch.object(
            health_core,
            "_runtime_skills_snapshot",
            return_value={"version": 1},
        ), patch.object(
            health_core,
            "_direct_chat_session_manager_snapshot",
            return_value=direct_chat_snapshot,
        ), patch.object(
            health_core,
            "_process_runtime_snapshot",
            return_value={
                "pid": 123,
                "uptime_seconds": 12.5,
                "thread_count": 4,
                "open_fd_count": 20,
                "max_rss_bytes": 8192,
                "measurement_scope": "live_since_process_start",
            },
        ), patch.object(
            health_core,
            "_database_runtime_snapshot",
            return_value={
                "dsn_configured": True,
                "postgres_pool_max_size": 50,
                "active_pool_count": 1,
                "sqlite_status": "active",
                "measurement_scope": "live_since_process_start",
            },
        ), patch.object(
            health_core,
            "ORION_ENGINE_VALIDATION_ERRORS",
            [],
        ), patch.object(
            health_core,
            "ORION_AUTH_MODE",
            "codex",
        ):
            payload = asyncio.run(health_core.health())

        self.assertTrue(payload["ok"])
        self.assertFalse(payload["openai_key_valid"])
        self.assertTrue(payload["cloud_provider_ready"])
        self.assertEqual(payload["cloud_provider_source"], "provider_profile:openai-codex")
        self.assertTrue(payload["provider_profile_health"]["codex_profile_ready"])
        self.assertEqual(payload["provider_profile_health"]["providers_by_id"]["openai-codex"]["state"], "active")
        self.assertEqual(payload["process_runtime"]["pid"], 123)
        self.assertEqual(payload["database_runtime"]["sqlite_status"], "active")
        self.assertEqual(payload["healthguide_safety_policy"]["policy_version"], 1)
        self.assertGreaterEqual(payload["healthguide_safety_policy"]["red_flag_rule_count"], 1)

    def test_health_accepts_active_anthropic_runtime_provider_when_openai_probe_is_unhealthy(self) -> None:
        direct_chat_snapshot = {
            "enabled": False,
            "runtime_cache": {"active_sessions": 0, "idle_ttl_ms": 0, "evicted_total": 0},
            "turns": {"active": 0, "queue_depth": 0},
            "interrupted_stale_sessions": 0,
            "errors_by_code": {},
        }

        with patch.object(
            health_core,
            "_runtime_contract_payload",
            return_value={"contract_schema_version": "2026.2.0"},
        ), patch.object(
            health_core,
            "_openai_env_bearer_with_source",
            return_value=("", "none"),
        ), patch.object(
            health_core,
            "probe_openai_credential",
            return_value={
                "openai_key_present": False,
                "openai_vault_present": False,
                "openai_key_valid": False,
                "openai_credential_source": "none",
                "openai_status": None,
                "openai_error": None,
            },
        ), patch.object(
            health_core.provider_profiles_service,
            "build_provider_runtime_truth",
            return_value={
                "providers_by_id": {
                    "anthropic": {
                        "state": "active",
                        "usable": True,
                        "active": True,
                    },
                },
                "providers": [
                    {
                        "id": "anthropic",
                        "state": "active",
                        "usable": True,
                        "active": True,
                    },
                ],
            },
        ), patch.object(
            health_core,
            "collect_runtime_counts",
            return_value={
                "weekly_schedules": 0,
                "setup_sessions_total": 0,
                "setup_sessions_active": 0,
                "provider_profiles_total": 0,
                "idempotency_records": 0,
            },
        ), patch.object(
            health_core,
            "_telegram_autopilot_snapshot",
            return_value={},
        ), patch.object(
            health_core,
            "_whatsapp_autopilot_snapshot",
            return_value={},
        ), patch.object(
            health_core,
            "tool_policy_snapshot",
            return_value={
                "blocked_actions": [],
                "approval_actions": [],
                "allow_actions": [],
                "sensitive_tools": [],
                "critical_tools": [],
                "block_cloud_critical": True,
                "connector_cloud_readonly": True,
                "policy_mode": "local_default",
            },
        ), patch.object(
            health_core,
            "_memory_health_snapshot",
            return_value={
                "enabled": True,
                "manager_ready": True,
                "manager_error": None,
                "db_path": "db",
                "db_exists": True,
                "db_size_bytes": 1,
                "sqlite_rows": 0,
                "sqlite_error": None,
                "lancedb_uri": "uri",
                "lancedb_initialized": True,
            },
        ), patch.object(
            health_core,
            "_runtime_skills_snapshot",
            return_value={"version": 1},
        ), patch.object(
            health_core,
            "_direct_chat_session_manager_snapshot",
            return_value=direct_chat_snapshot,
        ), patch.object(
            health_core,
            "_process_runtime_snapshot",
            return_value={
                "pid": 123,
                "uptime_seconds": 12.5,
                "thread_count": 4,
                "open_fd_count": 20,
                "max_rss_bytes": 8192,
                "measurement_scope": "live_since_process_start",
            },
        ), patch.object(
            health_core,
            "_database_runtime_snapshot",
            return_value={
                "dsn_configured": True,
                "postgres_pool_max_size": 50,
                "active_pool_count": 1,
                "sqlite_status": "active",
                "measurement_scope": "live_since_process_start",
            },
        ), patch.object(
            health_core,
            "ORION_ENGINE_VALIDATION_ERRORS",
            [],
        ):
            payload = asyncio.run(health_core.health())

        self.assertTrue(payload["ok"])
        self.assertFalse(payload["openai_key_valid"])
        self.assertTrue(payload["cloud_provider_ready"])
        self.assertEqual(payload["cloud_provider_source"], "provider_profile:anthropic")

    def test_health_exposes_scale_safety_baseline_with_provider_backpressure_and_queueing(self) -> None:
        rate_limited_profile = {
            "id": "profile-openai",
            "provider": "openai",
            "label": "OpenAI Direct",
            "auth_mode": "api_key",
            "workspace_id": "default",
            "enabled": True,
            "cooldown_until": "2026-04-10T18:00:30Z",
            "last_success_at": None,
            "last_failure_at": "2026-04-10T18:00:00Z",
            "last_error": "429 Rate limit exceeded.",
        }
        direct_chat_snapshot = {
            "enabled": True,
            "runtime_cache": {"active_sessions": 1, "idle_ttl_ms": 600000, "evicted_total": 0},
            "turns": {"active": 2, "queue_depth": 6},
            "interrupted_stale_sessions": 0,
            "errors_by_code": {},
        }
        provider_runtime_truth = {
            "providers_by_id": {
                "openai": {
                    "id": "openai",
                    "state": "degraded",
                    "usable": False,
                    "active": False,
                    "configured": True,
                    "active_source": "profile",
                    "backpressure": True,
                    "failure_class": "rate_limited",
                    "retry_after_seconds": 30,
                    "profile_count": 1,
                    "enabled_profile_count": 1,
                    "cooldown_profile_count": 1,
                    "healthy_profile_count": 0,
                    "issue_code": "rate_limited",
                    "issue": "429 Rate limit exceeded.",
                    "state_detail": "429 Rate limit exceeded.",
                }
            },
            "providers": [
                {
                    "id": "openai",
                    "state": "degraded",
                    "usable": False,
                    "active": False,
                    "configured": True,
                    "active_source": "profile",
                    "backpressure": True,
                    "failure_class": "rate_limited",
                    "retry_after_seconds": 30,
                }
            ],
            "state_summary": {"active": 0, "configured": 0, "setup_required": 0, "unavailable": 0, "degraded": 1},
        }

        with patch.object(
            health_core.provider_profiles_service,
            "build_provider_runtime_truth",
            return_value=provider_runtime_truth,
        ), patch.object(
            health_core,
            "_runtime_contract_payload",
            return_value={"contract_schema_version": "2026.2.0"},
        ), patch.object(
            health_core,
            "_openai_env_bearer_with_source",
            return_value=("token", "env_openai_api_key"),
        ), patch.object(
            health_core,
            "probe_openai_credential",
            return_value={
                "openai_key_present": True,
                "openai_vault_present": False,
                "openai_key_valid": False,
                "openai_credential_source": "none",
                "openai_status": 429,
                "openai_error": "Rate limit exceeded.",
            },
        ), patch.object(
            health_core,
            "collect_runtime_counts",
            return_value={
                "weekly_schedules": 0,
                "setup_sessions_total": 0,
                "setup_sessions_active": 0,
                "provider_profiles_total": 1,
                "idempotency_records": 0,
            },
        ), patch.object(
            health_core,
            "_telegram_autopilot_snapshot",
            return_value={},
        ), patch.object(
            health_core,
            "_whatsapp_autopilot_snapshot",
            return_value={},
        ), patch.object(
            health_core,
            "tool_policy_snapshot",
            return_value={
                "blocked_actions": [],
                "approval_actions": [],
                "allow_actions": [],
                "sensitive_tools": [],
                "critical_tools": [],
                "block_cloud_critical": True,
                "connector_cloud_readonly": True,
                "policy_mode": "local_default",
            },
        ), patch.object(
            health_core,
            "_memory_health_snapshot",
            return_value={
                "enabled": True,
                "manager_ready": True,
                "manager_error": None,
                "db_path": "db",
                "db_exists": True,
                "db_size_bytes": 1,
                "sqlite_rows": 0,
                "sqlite_error": None,
                "lancedb_uri": "uri",
                "lancedb_initialized": True,
            },
        ), patch.object(
            health_core,
            "_runtime_skills_snapshot",
            return_value={"version": 1},
        ), patch.object(
            health_core,
            "_direct_chat_session_manager_snapshot",
            return_value=direct_chat_snapshot,
        ), patch.object(
            health_core,
            "_process_runtime_snapshot",
            return_value={
                "pid": 456,
                "uptime_seconds": 30.0,
                "thread_count": 6,
                "open_fd_count": 24,
                "max_rss_bytes": 16384,
                "measurement_scope": "live_since_process_start",
            },
        ), patch.object(
            health_core,
            "_database_runtime_snapshot",
            return_value={
                "dsn_configured": True,
                "postgres_pool_max_size": 50,
                "active_pool_count": 1,
                "sqlite_status": "active",
                "measurement_scope": "live_since_process_start",
            },
        ), patch.object(
            health_core,
            "ORION_ENGINE_VALIDATION_ERRORS",
            [],
        ), patch.object(
            health_core,
            "ORION_AUTH_MODE",
            "openai",
        ), patch(
            "server_modules.local_queue.build_local_scale_safety_baseline",
            return_value={
                "state": "saturated",
                "summary": "Local worker demand is saturated and backpressure should be expected.",
                "queued_count": 12,
                "claimed_count": 2,
                "online_workers": 1,
                "busy_workers": 1,
                "idle_workers": 0,
                "backlog_per_online_worker": 12.0,
                "retry_after_seconds": 30,
                "workspace_hotspots": [{"workspace_id": "default", "count": 12}],
                "specialist_hotspots": [],
                "dead_letters": {"dead_letter_count": 1},
                "durable_intake": True,
                "queue_strategy": "workspace_specialist_weighted_fifo",
                "queued_state_instead_of_collapse": True,
                "safe_retry_enabled": True,
            },
        ):
            payload = asyncio.run(health_core.health())

        baseline = payload["scale_safety_baseline"]
        self.assertEqual(baseline["durable_intake"]["queueing"], "enabled")
        self.assertEqual(baseline["local_queue"]["state"], "saturated")
        self.assertEqual(baseline["local_queue"]["retry_after_seconds"], 30)
        self.assertEqual(baseline["provider_backpressure"]["state"], "backpressure")
        self.assertEqual(baseline["provider_backpressure"]["rate_limited_profiles"][0]["provider"], "openai")
        self.assertEqual(payload["provider_profile_health"]["providers_by_id"]["openai"]["state"], "degraded")
        self.assertEqual(baseline["direct_chat_runtime"]["queue_depth"], 6)
        self.assertTrue(baseline["failure_mode_contract"]["dead_letter_tracking"])
        self.assertEqual(baseline["process_runtime"]["pid"], 456)
        self.assertEqual(baseline["database_runtime"]["active_pool_count"], 1)
        self.assertEqual(payload["process_runtime"]["open_fd_count"], 24)
        self.assertEqual(payload["database_runtime"]["postgres_pool_max_size"], 50)


if __name__ == "__main__":
    unittest.main()
