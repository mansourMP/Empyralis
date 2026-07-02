# Empyralis -- Platform Map (Complete)

**Generated:** 2026-07-02
**Git commit:** 0820a732c
**Graphify:** 28,011 nodes, 72,014 edges, 1,133 communities (832 shown, 301 thin omitted), 98% EXTRACTED / 2% INFERRED
**Document purpose:** Single-source structural truth for agents and engineers. No summarization. Every file, every trace, every table.

---

## 0. Reading Guide

### What this document is

PLATFORM-MAP.md is the exhaustive structural inventory of the Empyralis codebase. It lists every file, traces every call chain, enumerates every channel and connector, registers every dead code artifact, and outputs the current-vs-target gap in countable form. It is designed for agents -- every statement is verifiable at a file:line.

### Relation to PLATFORM.md

- **PLATFORM.md** (`/docs/PLATFORM.md`) is the prescriptive rulebook: architecture decisions, design principles, known violations, priorities. It describes what SHOULD be true and what is KNOWN to be wrong.
- **PLATFORM-MAP.md** (this file) is the descriptive structural inventory: what files exist, what calls what, what the graph says, what is dead. It describes what IS true right now.
- **Linear PLATFORM OVERVIEW** is the target architecture -- the 7-phase plan to unify the backend into `server/`.

### Relation to Graphify

- **GRAPH_REPORT.md** (`/graphify-out/GRAPH_REPORT.md`) is the auto-generated knowledge graph. This document relays its key numbers (Section 4) but does not replicate its full community list.
- **Stats:** 28,011 nodes, 72,014 edges, 1,133 communities, 98% EXTRACTED, 2% INFERRED (1,572 edges, avg confidence 0.62). Built from commit `0820a732`.

### Quick Reference Counts

| Metric | Count |
|--------|-------|
| `server_modules/` Python files (root) | 408 |
| `server_modules/connectors/` Python files | 55 |
| `server_modules/agent/` Python files | 7 |
| `server_modules/session_manager/` Python files | 6 |
| `server_modules/hardware_runtime_adapters/` Python files | 5 |
| `server_modules/connectors/telegram/` Python files | 7 |
| **Total Python service files** | **488** |
| Frontend `.ts`/`.tsx` files | 210 |
| Gateway `.ts` source files | 73 |
| Supervisor `.rs` source files | 12 |
| Runtime kernel `.rs` source files | 61 |
| `server/` Python source files (on disk) | 0 |
| `server/` source files (inferred from `__pycache__`) | 22 |
| `frontend/v2/` source files | 0 (static export only) |
| MCP connector providers | 31 |
| Channel types defined | 20 |
| Known violations (PLATFORM.md) | 75 |
| Import cycles (graphify) | 16 |
| Graph communities | 1,133 |

---

## 1. Architecture Overview

### 1.1 ASCII Diagram

```
+------------------------------------------------------------------+
|                      CONSUMER SURFACES (pure transport)            |
|  Web (Next.js 16)  |  Telegram  |  Discord  |  Slack  |  ...     |
|  Channel shells are PIGEONS, not brains.                           |
+-------------------------------^-----------------------------------+
                                |  Normalized AgentTurnRequest
                                v
+------------------------------------------------------------------+
|                    CONTROL PLANE (Python/FastAPI)                  |
|  server.py -> agent_turn.py -> turn_runtime.py                    |
|                                                                   |
|  +-----------+  +-----------+  +-----------+  +-----------+       |
|  | Sage      |  | Studio    |  | Memory    |  | Governance|       |
|  | (main AI) |  | (specialist|  | (LanceDB) |  | (kill-sw, |       |
|  |           |  | agents)    |  |           |  | approval) |       |
|  +-----------+  +-----------+  +-----------+  +-----------+       |
|                                                                   |
|  Tool/Secret Brokers -> Connectors -> MCP -> Runtime Placement     |
+-----------------^-------------------^----------------^------------+
                  | Cloud              | Gateway        | VPS
                  v                    v                v
+---------------+  +--------------------+  +----------------------+
|  CLOUD TIER   |  | AGENT COMPUTER     |  | SELF-HOSTED          |
|  (no hw)      |  | (user hardware)    |  | (user VPS)           |
|               |  |                    |  |                      |
| Bot API       |  | empyralis-gateway  |  | Command worker       |
| Webhooks      |  | (WSS reverse       |  | (HTTP poll)          |
| OAuth apps    |  | tunnel)            |  |                      |
| Cloud         |  |                    |  |                      |
| Computer      |  | empyralis-         |  | Shell + File         |
| (droplet)     |  | supervisor         |  | ONLY                 |
|               |  | (local daemon)     |  |                      |
+---------------+  +--------------------+  +----------------------+
```

### 1.2 Data Flow

```
User Message
  -> Channel Adapter (normalization: channel -> AgentTurnRequest)
    -> agent_turn.py (canonical turn contract)
      -> turn_runtime.py (execution switchboard)
        -> direct_chat_generation_service.py (LLM provider-backed)
        -> runs_engine.py (durable background run)
          -> LLM Response
            -> tool calls -> tool_broker -> MCP registry / gateway WSS / VPS worker
            -> reply -> channel adapter -> user
```

### 1.3 Updates to PLATFORM.md Section 1 (items found during mapping)

1. **`server/` has zero Python source files on disk.** The 22 modules visible via `__pycache__/*.pyc` files were deleted. Directory structure remains: `agent/`, `api/`, `channels/`, `conversations/`, `mcp/`, `memory/`, `oauth/`, `tools/`, `vault/`. Each subdirectory contains only `__pycache__/`.

2. **`frontend/v2/` is a static export with zero source code.** The `out/` directory contains pre-rendered HTML for 4 routes (`/`, `/chat`, `/settings`, `/setup`), all rendering just "Loading..." -- a bare Next.js skeleton. No `.ts`/`.tsx` files exist beyond `next-env.d.ts`.

3. **`legacy/` is a raw snapshot, not a cleaned reference.** Contains its own `frontend/`, `graphify-out/`, `.venv/` (x3), `python_engine/.venv/`, `.orion-stack/` with large binary artifacts -- not the clean two-folder split described in the plan.

4. **`shared/` at repo root is an active shared package** (not legacy). Contains `design-system/tokens.ts`, `api-contract/` (3 files), `nav-manifest.ts`, `nav-manifest.js`, `mini-app-sdk.js`. The `nav-manifest.ts` has a self-cycle.

5. **`bin/` contains two compiled binaries:** `orion` and `empyralis` -- CLI tools.

---

## 2. Complete File Map

Status codes: **LIVE** (registered in `server.py`, reachable), **DEAD** (on disk, not registered), **SKELETAL** (stub only), **DEPRECATED** (live but marked for removal).

### 2.1 server_modules/ -- Root Files by Subsystem

#### 2.1.1 Core Turn Engine

| File | Purpose | Status | Key Dependencies |
|------|---------|--------|-----------------|
| `server.py` | Composition root -- FastAPI app, middleware, router mounting | LIVE | auth, db, all route modules |
| `main.py` | Standalone entry point (rarely used) | LIVE | server |
| `agent_turn.py` | Canonical AgentTurnRequest -- all channels converge here | LIVE | turn_runtime, session_service, thread_service |
| `turn_runtime.py` | Execution switchboard -- direct chat vs durable run | LIVE | direct_chat_generation_service, runs_engine |
| `turn_ingress_service.py` | Turn ingress normalization | LIVE | agent_turn |
| `channel_adapter.py` | Channel normalization -- NormalizedSageTurn | LIVE | agent_turn |
| `sage_turn_adapter.py` | Unified sage turn execution for all channels | LIVE | agent_turn, sage_command_dispatcher |
| `sage_command_dispatcher.py` | Command dispatcher + hardcoded error strings | LIVE | sage_agent_runtime_service |
| `sage_agent_runtime_service.py` | Sage agent loop -- channel-specific keyword routing | LIVE | channel routing, gateway |
| `sage_reply_dispatcher.py` | Reply dispatch -- has fallback message | LIVE | channel_adapter |
| `sage_transparency_service.py` | Sage transparency events | LIVE | agent_turn |
| `sage_daily_operator_service.py` | Daily operator tasks | LIVE | scheduling |
| `agent_channel_router.py` | Channel routing -- per-channel handler classes | LIVE | all channel modules |

#### 2.1.2 Authentication and Database

| File | Purpose | Status | Key Dependencies |
|------|---------|--------|-----------------|
| `auth.py` | Authentication -- users, sessions, API keys, SQLite fallback | LIVE | db |
| `db.py` | Database connection, pool management | LIVE | PostgreSQL |
| `sqlite_helpers.py` | SQLite utility helpers | LIVE | auth |
| `control_plane_repository.py` | Postgres control plane -- tenants, workspaces, agents | LIVE | db |
| `runtime_common.py` | Shared runtime utilities, auth middleware | LIVE | auth, db |
| `api_contract.py` | API type contracts | LIVE | shared types |

#### 2.1.3 LLM Generation Pipeline (direct_chat_*)

| File | Purpose | Status | Key Dependencies |
|------|---------|--------|-----------------|
| `direct_chat_service.py` | Direct chat orchestration | LIVE | generation, composition |
| `direct_chat_generation_service.py` | LLM generation loop (provider-backed) | LIVE | providers |
| `direct_chat_composition_service.py` | Chat composition | LIVE | generation |
| `direct_chat_response_service.py` | Slash command dispatch | LIVE | generation |
| `direct_chat_provider_service.py` | Provider selection/routing | LIVE | provider_profiles |
| `direct_chat_provider_facade_service.py` | Provider facade | LIVE | provider |
| `direct_chat_entry_service.py` | Chat entry | LIVE | generation |
| `direct_chat_entry_policy_service.py` | Entry policy | LIVE | quotas |
| `direct_chat_runtime_service.py` | Chat runtime | LIVE | generation |
| `direct_chat_runtime_facade_service.py` | Chat runtime facade | LIVE | runtime |
| `direct_chat_runtime_entry_facade_service.py` | Runtime entry facade | LIVE | entry |
| `direct_chat_stream_runtime_service.py` | Stream runtime | LIVE | generation |
| `direct_chat_stream_state_service.py` | Stream state | LIVE | stream |
| `direct_chat_stream_transport_service.py` | Stream transport | LIVE | stream |
| `direct_chat_stream_response_service.py` | Stream response | LIVE | stream |
| `direct_chat_transport_service.py` | Chat transport | LIVE | channel |
| `direct_chat_memory_facade_service.py` | Memory facade | LIVE | memory |
| `direct_chat_handoff_service.py` | Handoff logic | LIVE | specialist |
| `direct_chat_handoff_facade_service.py` | Handoff facade | LIVE | handoff |
| `direct_chat_operator_binding_service.py` | Operator binding | LIVE | chat |
| `direct_chat_operator_support_service.py` | Operator support | LIVE | chat |
| `direct_chat_callback_facade_service.py` | Callback facade | LIVE | chat |
| `direct_chat_support_binding_service.py` | Support binding | LIVE | chat |
| `direct_chat_metadata_service.py` | Metadata | LIVE | chat |
| `direct_chat_prompt_service.py` | Prompt assembly | LIVE | generation |
| `direct_chat_context_service.py` | Context building | LIVE | memory |
| `direct_chat_intervention_service.py` | Intervention builder | LIVE | chat |
| `direct_chat_availability_service.py` | Availability checks | LIVE | quotas |
| `direct_chat_routing_service.py` | Routing | LIVE | provider |
| `direct_chat_hosted_usage_service.py` | Hosted usage tracking | LIVE | billing |
| `direct_chat_tool_catalog_service.py` | Tool catalog | LIVE | tool_broker |

**Subtotal:** 31 direct_chat_* files.

#### 2.1.4 Durable Runs Engine

| File | Purpose | Status | Key Dependencies |
|------|---------|--------|-----------------|
| `runs_engine.py` | Runs engine | LIVE | runs_core, execution |
| `runs_core.py` | Runs core | LIVE | engine |
| `runs_execution.py` | Runs execution | LIVE | core, engine |
| `runs_history.py` | Runs history | LIVE | core |
| `run_service.py` | Run service | LIVE | runs_engine |

#### 2.1.5 Runtime Execution (runtime_*)

| File | Purpose | Status | Key Dependencies |
|------|---------|--------|-----------------|
| `runtime_runtime_api.py` | Runtime registration, sessions, claims | LIVE | all runtime |
| `runtime_route_registry_service.py` | Runtime route registry | LIVE | runtime |
| `runtime_route_registration_service.py` | Runtime route registration | LIVE | runtime |
| `runtime_route_bootstrap_service.py` | Runtime route bootstrap | LIVE | runtime |
| `runtime_run_entry_service.py` | Run entry | LIVE | runs_engine |
| `runtime_run_query_service.py` | Run query | LIVE | runs |
| `runtime_run_control_service.py` | Run control | LIVE | runs |
| `runtime_run_approval_service.py` | Run approval | LIVE | runs |
| `runtime_run_delegation_service.py` | Run delegation | LIVE | runs |
| `runtime_run_detail_service.py` | Run detail | LIVE | runs |
| `runtime_run_access_service.py` | Run access control | LIVE | runs |
| `runtime_run_resume_service.py` | Run resume | LIVE | runs |
| `runtime_config.py` | Runtime configuration -- env, paths, provider resolution | LIVE | all runtime |
| `runtime_policy.py` | Runtime policy enforcement | LIVE | runtime |
| `runtime_heartbeat_service.py` | Runtime heartbeat | LIVE | gateway |
| `runtime_history_service.py` | Runtime history | LIVE | runs |
| `runtime_usage_service.py` | Runtime usage tracking | LIVE | billing |
| `runtime_workspace_service.py` | Runtime workspace service | LIVE | workspaces |
| `runtime_attachment_service.py` | Runtime attachment | LIVE | files |
| `runtime_request_service.py` | Runtime request handling | LIVE | runtime |
| `runtime_webhook_trigger_service.py` | Runtime webhook triggers | LIVE | webhooks |
| `runtime_local_execution_approval_service.py` | Local execution approval | LIVE | runtime |
| `runtime_runs_api.py` | Runtime runs API (cycle: runtime_runs_api -> runtime_route_registration -> local_queue) | LIVE | runs |

#### 2.1.6 Gateway Bridge

| File | Purpose | Status | Key Dependencies |
|------|---------|--------|-----------------|
| `gateway_execution_service.py` | Gateway tool dispatch | LIVE | gateway_protocol, personal_channels (CYCLE) |
| `gateway_protocol_service.py` | Gateway protocol handling | LIVE | gateway_execution, personal_channels (CYCLE) |
| `gateway_health_service.py` | Gateway health monitoring | LIVE | gateway |
| `gateway_pairing_service.py` | Gateway pairing flow | LIVE | gateway |
| `gateway_approval_service.py` | Gateway approval flow | LIVE | gateway |
| `gateway_activity_service.py` | Gateway activity tracking | LIVE | gateway |
| `gateway_browser_service.py` | Gateway browser execution | LIVE | gateway |
| `gateway_inventory_service.py` | Gateway capability inventory | LIVE | gateway |
| `supervisor_client.py` | Supervisor HTTP client | LIVE | empyralis-supervisor |
| `hardware_runtime_target_resolver.py` | Hardware target resolution (mapping bugs) | LIVE | gateway, cloud, vps |
| `hardware_runtime_session_service.py` | Hardware runtime sessions | LIVE | hardware |
| `hardware_action_broker_service.py` | Hardware action brokering | LIVE | hardware |
| `hardware_access_policy_service.py` | Hardware access policy | LIVE | hardware |
| `hybrid_policy_service.py` | Hybrid cloud/local placement policy | LIVE | memory (CYCLE) |

#### 2.1.7 Local Queue and Sessions

| File | Purpose | Status | Key Dependencies |
|------|---------|--------|-----------------|
| `local_queue.py` | Local queue -- claims, heartbeats, dead letters | LIVE | runs, sessions (multiple CYCLEs) |
| `session_service.py` | Session management | LIVE | sessions |
| `session_lifecycle_service.py` | Session lifecycle | LIVE | sessions |
| `thread_service.py` | Thread management | LIVE | sessions |

#### 2.1.8 Tool, Secret, and MCP Brokering

| File | Purpose | Status | Key Dependencies |
|------|---------|--------|-----------------|
| `tool_broker.py` | Tool access gateway | LIVE | tool_catalog |
| `tool_broker_guard_service.py` | Tool brokering safety guards | LIVE | tool_broker |
| `direct_tool_approval_service.py` | Tool approval workflow | LIVE | tool_broker |
| `direct_tool_loop_guard_service.py` | Loop detection (3 repeat -> abort) | LIVE | tool_broker |
| `secrets_broker.py` | Secret/vault access -- hosted keys, workspace BYOK | LIVE | vault |
| `vault_store.py` | Encrypted credential vault | LIVE | db |
| `mcp_registry_service.py` | MCP server registry, tool discovery, approval, invocation | LIVE | tool_broker |
| `skill_registry.py` | Skill definitions + MCP-to-skill integration | LIVE | MCP |
| `skill_scanner.py` | Skill file scanner | LIVE | skills |

#### 2.1.9 Channel Management

| File | Purpose | Status | Key Dependencies |
|------|---------|--------|-----------------|
| `personal_channel_sage_bridge_service.py` | Personal channel bridge -- 6 near-identical wrappers | LIVE | all personal channels |
| `personal_channels_service.py` | Personal channel management | LIVE | channel_handlers (CYCLE) |
| `personal_channel_handler_registry.py` | Personal channel handler registry | LIVE | personal_channels |
| `personal_channel_thread_command_service.py` | Personal channel thread commands | LIVE | threads |
| `channel_lane_contract_service.py` | Canonical channel lane definitions | LIVE | all channels |
| `channel_types.py` | Channel type definitions | LIVE | all channels |
| `channel_platform_service.py` | Channel platform management | LIVE | channels |
| `channel_execution_service.py` | Channel execution (hardcoded strings) | LIVE | channels |
| `channel_blocking_policy_service.py` | Channel blocking/safe mode | LIVE | channels |
| `channel_activity_service.py` | Channel activity tracking | LIVE | channels |
| `channel_concurrency_service.py` | Channel concurrency | LIVE | channels |
| `channel_errors.py` | Channel error definitions | LIVE | channels |
| `business_messaging_channel_adapter_service.py` | Business channel adapter | LIVE | channels |

#### 2.1.10 Agent Registry and Specialist System

| File | Purpose | Status | Key Dependencies |
|------|---------|--------|-----------------|
| `agent_registry_api.py` | Agent registry REST API | LIVE | registry |
| `agent_registry_repository.py` | Agent registry persistence | LIVE | db |
| `agent_registry_models.py` | Agent registry models | LIVE | registry |
| `agent_specialist_repository.py` | Specialist agent persistence | LIVE | db |
| `specialist_service.py` | Specialist agent service | LIVE | agents |
| `agent_manifest.py` | Agent manifest definitions (127 edges) | LIVE | agents |
| `agent_policy_context.py` | Agent policy context | LIVE | agents |
| `agent_memory.py` | Agent memory store | LIVE | memory |
| `agent_trace_service.py` | Agent trace/audit service | LIVE | agents |
| `agent_transparency_events.py` | Agent transparency events | LIVE | agents |
| `agent_completion_notification_service.py` | Agent completion notifications | LIVE | agents |
| `agent_action_metering_service.py` | Agent action metering | LIVE | billing |
| `agent_computer_policy_service.py` | Agent computer policy | LIVE | hardware |
| `agent_computer_profile_service.py` | Agent computer profile | LIVE | hardware |
| `agent_computer_surface_service.py` | Agent computer surface | LIVE | hardware |
| `agent_computer_permission_secret_model.py` | Agent computer permission model | LIVE | hardware |
| `agent_workspace_api.py` | Agent workspace API | LIVE | workspaces |

#### 2.1.11 App Registry and OAuth

| File | Purpose | Status | Key Dependencies |
|------|---------|--------|-----------------|
| `app_registry_api.py` | App install/uninstall/store API | LIVE | apps |
| `app_bridge_service.py` | App-to-agent bridge contracts | LIVE | apps |
| `connection_oauth_service.py` | OAuth provider configs + APP_MCP_SERVER_MAP (31 providers) | LIVE | oauth, MCP |
| `connection_catalog_service.py` | Connection catalog | LIVE | connectors |
| `connection_readiness_service.py` | Connection readiness checks | LIVE | connectors |
| `connection_verify_service.py` | Connection verification | LIVE | connectors |

#### 2.1.12 Billing, Credits, and Entitlements

| File | Purpose | Status | Key Dependencies |
|------|---------|--------|-----------------|
| `billing_service.py` | Billing integration (Stripe) | LIVE | db |
| `billing_credit_config.py` | Credit configuration | LIVE | billing |
| `entitlements_service.py` | Entitlement and quota enforcement | LIVE | billing |
| `quota_response_service.py` | Quota response messages (hardcoded strings) | LIVE | entitlements |
| `quota_policy_service.py` | Quota policy | LIVE | entitlements |
| `workspace_bootstrap_service.py` | New workspace setup, credit grants | LIVE | billing |

#### 2.1.13 Memory System

| File | Purpose | Status | Key Dependencies |
|------|---------|--------|-----------------|
| `memory_service.py` | Memory/embedding service | LIVE | LanceDB (multiple CYCLEs) |
| `unified_memory_service.py` | Unified memory across captain + specialists | LIVE | memory (CYCLE) |
| `activity_ledger_service.py` | Durable activity ledger | LIVE | memory |
| `workspace_context_memory_adapter.py` | Workspace context adapter | LIVE | memory (CYCLE) |
| `conversation_memory_policy.py` | Conversation memory policy | LIVE | memory (CYCLE) |
| `memory_summary_service.py` | Memory summary | LIVE | memory (CYCLE) |

#### 2.1.14 Provisioning and Policy

| File | Purpose | Status | Key Dependencies |
|------|---------|--------|-----------------|
| `provider_profiles.py` | Provider profile management | LIVE | LLM providers |
| `safe_mode_service.py` | Safe mode -- degraded operation | LIVE | runtime |
| `universal_operator.py` | Universal operator actions (hardcoded strings) | LIVE | tools |
| `error_response_service.py` | Error response normalization | LIVE | all |
| `unified_governance_gate.py` | Unified governance gate | LIVE | policy |
| `external_content_guard.py` | External content safety | LIVE | safety |
| `capability_registry.py` | Capability registry | LIVE | hardware |
| `capability_risk_classifier_service.py` | Capability risk classifier | LIVE | safety |
| `blackbox_runtime_support.py` | Blackbox runtime support | LIVE | runtime |
| `execution_sandbox_service.py` | Execution sandbox | LIVE | safety |
| `browser_engine.py` | Browser engine interface | LIVE | browser |
| `browser_checkpoint_service.py` | Browser checkpoint | LIVE | browser |
| `bounded_scheduler_service.py` | Bounded scheduler | LIVE | scheduling |
| `policy_service.py` | Policy service | LIVE | skills (CYCLE) |
| `skills_service.py` | Skills service | LIVE | runs_execution (CYCLE) |
| `no_provider_service.py` | No-provider fallback | LIVE | tool_catalog (CYCLE) |
| `healthguide_safety_service.py` | Health guide safety | LIVE | safety |

#### 2.1.15 Routes (API endpoints)

| File | Purpose | Status | Key Dependencies |
|------|---------|--------|-----------------|
| `routes_billing.py` | Billing API routes | LIVE | billing |
| `routes_health.py` | Health check routes | LIVE | health |
| `routes_agents.py` | Agent routes | LIVE | agents |
| `routes_connectors.py` | Connector REST routes | LIVE | connectors |
| `routes_personal_channels.py` | Personal channel routes | LIVE | personal_channels |
| `routes_sage_telegram_hosted.py` | Telegram hosted bot routes (hardcoded strings) | LIVE | telegram |
| `routes_signal.py` | Signal routes | DEAD (not mounted) | signal |
| `routes_imessage.py` | iMessage routes | DEAD (not mounted) | imessage |
| `routes_wechat.py` | WeChat routes | DEAD (not mounted) | wechat |
| `routes_slack.py` | Slack routes | DEAD (not mounted) | slack |
| `routes_discovery.py` | Discovery routes | DEAD (not mounted) | discovery |
| `routes_mini_apps.py` | Mini-apps routes | DEAD (not mounted) | mini_apps |

#### 2.1.16 Connectors (deprecated/replaced)

| File | Purpose | Status | Key Dependencies |
|------|---------|--------|-----------------|
| `connectors_actions.py` | DEPRECATED connector tools (replaced by MCP) | DEPRECATED | connectors |
| `connectors_core.py` | Connector core utilities | LIVE | connectors |
| `connector_manifests.py` | Connector manifest catalog | LIVE | connectors |
| `connector_validators.py` | Connector validation | LIVE | connectors (CYCLE) |

#### 2.1.17 ACP (Agent Communication Protocol)

| File | Purpose | Status | Key Dependencies |
|------|---------|--------|-----------------|
| `acp_bridge_service.py` | ACP bridge (not MCP -- different subsystem) | LIVE | gateway |
| `acp_manager.py` | ACP session management | LIVE | acp |

#### 2.1.18 Workflow System

| File | Purpose | Status | Key Dependencies |
|------|---------|--------|-----------------|
| `workflow_service.py` | Workflow service | LIVE | workflows |
| `workflow_api.py` | Workflow API | LIVE | workflows |
| `workflow_repository.py` | Workflow persistence | LIVE | db |

#### 2.1.19 Deployed Agent (Virtual Runtime)

| File | Purpose | Status | Key Dependencies |
|------|---------|--------|-----------------|
| `deployed_agent_virtual_runtime_service.py` | Deployed agent virtual runtime | LIVE | agents |
| `builder_runtime_mapping.py` | Builder runtime mapping | LIVE | agents |
| `builder_schema.py` | Builder schema | LIVE | agents |
| `automation_intents.py` | Automation intents | LIVE | agents |
| `autopilot_connectors.py` | Autopilot connectors (general) | LIVE | connectors |

#### 2.1.20 Health, Telemetry, and Others

| File | Purpose | Status | Key Dependencies |
|------|---------|--------|-----------------|
| `doctor_report.py` | System health report generation | LIVE | monitoring |
| `health_core.py` | Health check core | LIVE | health |
| `state_paths.py` | Runtime state file paths | LIVE | config |
| `logging_config.py` | Logging configuration | LIVE | all |
| `config_loader.py` | Config loading | LIVE | config |
| `cloud_cutover_config.py` | Cloud cutover configuration | LIVE | cloud |
| `platform_analytics_service.py` | Platform analytics | LIVE | analytics |
| `notification_service.py` | Notification feed composition | LIVE | notifications |
| `personal_context_engine.py` | Personal context engine | LIVE | context |
| `shared_operational_board_service.py` | Shared board (captain + specialists) | LIVE | specialists |
| `data_retention_service.py` | Data retention policy | LIVE | compliance |
| `retention_enforcement_job.py` | Retention enforcement job | LIVE | compliance |
| `customer_ops_pack.py` | Customer ops utilities | LIVE | ops |
| `setup_sessions.py` | Setup session management | LIVE | auth (CYCLE) |
| `url_security.py` | URL security validation | LIVE | security |
| `attachment_utils.py` | Attachment utilities | LIVE | files |
| `artifact_service.py` | Artifact service | LIVE | files |
| `voice_notification_policy_service.py` | Voice notification policy | LIVE | notifications |
| `calorie_tracking_service.py` | Calorie tracking (health domain) | LIVE | health |
| `account_shell_service.py` | Account shell service | LIVE | auth |
| `telemetry.py` | Telemetry/tracing | LIVE | all |
| `shared.py` | Shared utilities | LIVE | all |
| `schemas.py` | Pydantic schemas | LIVE | all APIs |
| `inventory_skill.py` | Inventory skill (ecommerce -- hardcoded strings) | LIVE | skills |
| `machine_lease_service.py` | Machine lease management | LIVE | hardware |
| `transcript_events_service.py` | Transcript events | LIVE | chat |

### 2.2 server_modules/ Subdirectories

#### 2.2.1 agent/ (7 files)

| File | Purpose | Status |
|------|---------|--------|
| `__init__.py` | Package init | LIVE |
| `action_service.py` | Agent action definitions | LIVE |
| `automation_setup_service.py` | Agent automation setup templates | LIVE |
| `menu_content_service.py` | Menu content for agent UI | LIVE |
| `routing_service.py` | Agent routing | LIVE |
| `space_monitoring_service.py` | Space monitoring | LIVE |
| `user_profile_service.py` | Agent user profile service | LIVE |

#### 2.2.2 session_manager/ (6 files)

| File | Purpose | Status |
|------|---------|--------|
| `__init__.py` | Package init | LIVE |
| `manager.py` | Session manager | LIVE |
| `actor_queue.py` | Actor queue for sessions | LIVE |
| `observability.py` | Session observability | LIVE |
| `runtime_cache.py` | Session runtime cache | LIVE |
| `types.py` | Session types | LIVE |

#### 2.2.3 hardware_runtime_adapters/ (5 files)

| File | Purpose | Status |
|------|---------|--------|
| `__init__.py` | Package init | LIVE |
| `cloud_computer_adapter.py` | Cloud computer adapter | LIVE |
| `gateway_adapter.py` | Gateway adapter | LIVE |
| `self_hosted_node_adapter.py` | Self-hosted VPS adapter | LIVE |
| `common.py` | Shared adapter utilities | LIVE |

### 2.3 server_modules/connectors/ (55 files)

#### Connector Foundation
| File | Purpose | Status |
|------|---------|--------|
| `__init__.py` | Package init | LIVE |
| `connector_runtime.py` | Connector runtime base | LIVE |
| `connector_webhook.py` | Connector webhook base | LIVE |
| `runtime_status_service.py` | Connector runtime status | LIVE |

#### Slack, Discord
| File | Purpose | Status |
|------|---------|--------|
| `slack_connector.py` | Slack -- OAuth, webhooks, message handling | LIVE |
| `discord_connector.py` | Discord -- bot REST + personal DM (hardcoded strings) | LIVE |
| `discord_bot_runtime_service.py` | Discord bot runtime | LIVE |

#### Telegram (root + sub-package -- 9+7 files)
| File | Purpose | Status |
|------|---------|--------|
| `telegram_ingress_service.py` | Telegram ingress (hardcoded strings) | LIVE |
| `telegram_connector_services.py` | Telegram connector services | LIVE |
| `telegram_connector_context_service.py` | Telegram context | LIVE |
| `telegram_connector_poll_service.py` | Telegram polling | LIVE |
| `telegram_run_action_service.py` | Telegram run actions | LIVE |
| `telegram_run_dispatch_service.py` | Telegram run dispatch | LIVE |
| `telegram_inbound_context_service.py` | Telegram inbound context | LIVE |
| `telegram_poll_dispatch_service.py` | Telegram poll dispatch | LIVE |
| `telegram_terminal_service.py` | Telegram terminal commands | LIVE |
| `telegram/` sub-package | 7 files -- internal functions | LIVE |

#### WhatsApp
| File | Purpose | Status |
|------|---------|--------|
| `whatsapp_ingress_service.py` | WhatsApp ingress (hardcoded strings) | LIVE |
| `whatsapp_webhook_service.py` | WhatsApp webhook | LIVE |
| `whatsapp_webhook_bridge_service.py` | WhatsApp webhook bridge | LIVE |
| `whatsapp_transport_service.py` | WhatsApp transport | LIVE |
| `whatsapp_autopilot_state_service.py` | WhatsApp autopilot state | LIVE |
| `whatsapp_run_dispatch_service.py` | WhatsApp run dispatch | LIVE |
| `whatsapp_autopilot_service_registry.py` | WhatsApp autopilot registry | LIVE |

#### Autopilot System (25 files)
| File | Purpose | Status |
|------|---------|--------|
| `autopilot_endpoint_service.py` | Autopilot endpoint | LIVE |
| `autopilot_runtime_exports.py` | Autopilot runtime exports | LIVE |
| `autopilot_runtime_facade_service.py` | Autopilot runtime facade | LIVE |
| `autopilot_runtime_support_service.py` | Autopilot runtime support (7 "I" messages) | LIVE |
| `autopilot_runtime_service_registry.py` | Autopilot service registry | LIVE |
| `autopilot_registry_facade_service.py` | Autopilot registry facade | LIVE |
| `autopilot_approval_service.py` | Autopilot approval | LIVE |
| `autopilot_skill_service.py` | Autopilot skills | LIVE |
| `autopilot_workflow_setup_service.py` | Autopilot workflow setup | LIVE |
| `autopilot_event_service.py` | Autopilot events | LIVE |
| `autopilot_event_bridge_service.py` | Autopilot event bridge | LIVE |
| `autopilot_state_bridge_service.py` | Autopilot state bridge | LIVE |
| `autopilot_bridge_registry_service.py` | Autopilot bridge registry | LIVE |
| `autopilot_bridge_facade_service.py` | Autopilot bridge facade | LIVE |
| `autopilot_connector_shell_service.py` | Autopilot connector shell | LIVE |
| `autopilot_connector_shell_builder.py` | Autopilot connector shell builder | LIVE |
| `autopilot_channel_support_service.py` | Autopilot channel support | LIVE |
| `autopilot_common_support_service.py` | Autopilot common support | LIVE |
| `autopilot_terminal_bridge_service.py` | Autopilot terminal bridge | LIVE |
| `autopilot_run_entry_service.py` | Autopilot run entry | LIVE |
| `autopilot_connector_config.py` | Autopilot connector config | LIVE |
| `autopilot_profile_service.py` | Autopilot profile | LIVE |
| `autopilot_shared_service_registry.py` | Autopilot shared registry | LIVE |
| `autopilot_support_service_registry.py` | Autopilot support registry | LIVE |
| `autopilot_status_service.py` | Autopilot status | LIVE |

#### Channel Delivery + Work Connectors
| File | Purpose | Status |
|------|---------|--------|
| `channel_delivery_outbox_service.py` | Channel delivery outbox | LIVE |
| `channel_workspace_scope_service.py` | Channel workspace scoping | LIVE |
| `github_connector.py` | GitHub -- webhooks, issues, PRs | LIVE |
| `linear_connector.py` | Linear API connector | LIVE |
| `notion_connector.py` | Notion API connector | LIVE |
| `dropbox_connector.py` | Dropbox API connector | LIVE |
| `s3_connector.py` | AWS S3 connector | LIVE |
| `smtp_connector.py` | SMTP/IMAP email connector | LIVE |

### 2.4 frontend/ (210 TypeScript files)

#### 2.4.1 frontend/app/ -- Pages (48 files)

| File | Purpose |
|------|---------|
| `app/layout.tsx` | Root Next.js layout |
| `app/page.tsx` | Landing page |
| `app/globals.css` | Global CSS |
| `app/landing.css` | Landing page CSS |
| `app/landing-client.tsx` | Landing client component |
| `app/login/page.tsx` | Login page |
| `app/signup/page.tsx` | Signup page |
| `app/auth/complete/page.tsx` | OAuth callback |
| `app/continue/page.tsx` | Session resume |
| `app/continue/continue.css` | Resume CSS |
| `app/onboarding/page.tsx` | Onboarding entry |
| `app/onboarding/OnboardingClient.tsx` | Onboarding flow |
| `app/terms/page.tsx` | Terms of service |
| `app/privacy/page.tsx` | Privacy policy |
| `app/invite/[code]/page.tsx` | Invite redemption |
| `app/preview/page.tsx` | Public agent preview |
| `app/preview/PublicAgentPreviewClient.tsx` | Preview client |
| `app/healthz/route.ts` | Health check |
| `app/not-found.tsx` | 404 page |
| `app/(account)/layout.tsx` | Account shell layout |
| `app/(account)/page.tsx` | Account home |
| `app/(account)/AccountHomeClient.tsx` | Workspace switcher |
| `app/(account)/AccountTenantSwitcher.tsx` | Tenant switcher |
| `app/(account)/ShellRecoveryActions.tsx` | Shell recovery |
| `app/(account)/settings/account/page.tsx` | Account settings |
| `app/(account)/settings/resolve-settings-route.ts` | Settings resolver |
| `app/(account)/workspaces/new/page.tsx` | New workspace |
| `app/(account)/workspaces/new/NewWorkspacePageClient.tsx` | New workspace client |
| `app/(account)/w/[workspaceId]/layout.tsx` | Workspace layout |
| `app/(account)/w/[workspaceId]/page.tsx` | Workspace root |
| `app/(account)/w/[workspaceId]/WorkspaceSurfacePage.tsx` | Workspace surface |
| `app/(account)/w/[workspaceId]/WorkspaceHomeRedirect.tsx` | Home redirect |
| `app/(account)/w/[workspaceId]/chat/page.tsx` | Chat page |
| `app/(account)/w/[workspaceId]/sage/page.tsx` | Sage page |
| `app/(account)/w/[workspaceId]/studio/page.tsx` | Studio page |
| `app/(account)/w/[workspaceId]/memory/page.tsx` | Memory page |
| `app/(account)/w/[workspaceId]/channels/page.tsx` | Channels page |
| `app/(account)/w/[workspaceId]/hardware/page.tsx` | Hardware page |
| `app/(account)/w/[workspaceId]/gateway/page.tsx` | Gateway page |
| `app/(account)/w/[workspaceId]/gateway-activity/page.tsx` | Gateway activity |
| `app/(account)/w/[workspaceId]/deploy/page.tsx` | Deploy page |
| `app/(account)/w/[workspaceId]/applications/page.tsx` | Apps page |
| `app/(account)/w/[workspaceId]/applications/[appId]/page.tsx` | App detail |
| `app/(account)/w/[workspaceId]/integrations/page.tsx` | Integrations page |
| `app/(account)/w/[workspaceId]/studio-integrations/page.tsx` | Studio integrations |
| `app/(account)/w/[workspaceId]/artifacts/page.tsx` | Artifacts page |
| `app/(account)/w/[workspaceId]/activity/page.tsx` | Activity page |
| `app/(account)/w/[workspaceId]/notifications/page.tsx` | Notifications page |
| `app/(account)/w/[workspaceId]/inbox/page.tsx` | Inbox page |
| `app/(account)/w/[workspaceId]/tasks/page.tsx` | Tasks page |
| `app/(account)/w/[workspaceId]/marketplace/page.tsx` | Marketplace page |
| `app/(account)/w/[workspaceId]/settings/page.tsx` | Settings page |

#### 2.4.2 frontend/app/ -- API Routes (22 files)

| File | Purpose |
|------|---------|
| `app/api/[...path]/route.ts` | Catch-all API proxy |
| `app/api/auth/login/route.ts` | Login API |
| `app/api/auth/logout/route.ts` | Logout API |
| `app/api/auth/register/route.ts` | Register API |
| `app/api/auth/signup/route.ts` | Signup API |
| `app/api/auth/me/route.ts` | Current user API |
| `app/api/auth/refresh/route.ts` | Token refresh |
| `app/api/auth/account-shell/route.ts` | Account shell |
| `app/api/auth/providers/route.ts` | Auth providers |
| `app/api/auth/google/route.ts` | Google OAuth |
| `app/api/auth/google/callback/route.ts` | Google callback |
| `app/api/channel-pairing/intents/route.ts` | Pairing intents |
| `app/api/channel-pairing/links/route.ts` | Pairing links |
| `app/api/channel-pairing/links/[linkId]/revoke/route.ts` | Revoke link |
| `app/api/activity/timeline/route.ts` | Activity timeline |
| `app/api/hardware/bootstrap/install.sh/route.ts` | Hardware installer |
| `app/api/workspaces/[workspaceId]/channel-operations/route.ts` | Channel ops |
| `app/agent-registry/[...path]/route.ts` | Agent registry proxy |
| `app/agents/[...path]/route.ts` | Agents proxy |
| `app/apps/[...path]/route.ts` | Apps proxy |
| `app/install/agent-computer.sh/route.ts` | Agent computer installer |

#### 2.4.3 frontend/lib/server/ (4 files)

| File | Purpose |
|------|---------|
| `lib/server/control-plane-base-url.ts` | CP base URL resolver |
| `lib/server/control-plane-proxy.ts` | CP HTTP proxy |
| `lib/server/google-oauth.ts` | Google OAuth handler |
| `lib/server/load-account-shell-session.ts` | Account shell session loader |

#### 2.4.4 frontend/lib/auth/ (5 files)

| File | Purpose |
|------|---------|
| `lib/auth/auth-client.ts` | Auth client |
| `lib/auth/auth-provider-icons.tsx` | Provider icon components |
| `lib/auth/auth-timeouts.ts` | Timeout constants |
| `lib/auth/csrf.ts` | CSRF protection |
| `lib/auth/first-launch-panel.tsx` | First launch panel |

#### 2.4.5 frontend/lib/shell/ + lib/account/ (6 files)

| File | Purpose |
|------|---------|
| `lib/shell/account-shell-context.tsx` | Account shell context |
| `lib/shell/account-shell-payload.ts` | Payload types |
| `lib/shell/account-shell-storage.ts` | localStorage wrapper |
| `lib/shell/account-shell-store.ts` | State store |
| `lib/shell/workspace-membership-model.ts` | Membership model |
| `lib/account/account-workspaces-client.ts` | Workspaces client |

#### 2.4.6 frontend/lib/ui/ (18 files)

| File | Purpose |
|------|---------|
| `lib/ui/tokens.ts` | Design tokens |
| `lib/ui/motion.tsx` | Animation system |
| `lib/ui/chrome.css` | Shell CSS |
| `lib/ui/icons.tsx` | Icon components |
| `lib/ui/primitives.tsx` | UI primitives |
| `lib/ui/modal.tsx` | Modal |
| `lib/ui/confirm-dialog.tsx` | Confirm dialog |
| `lib/ui/command-sheet.tsx` | Command sheet |
| `lib/ui/data-table.tsx` | Data table |
| `lib/ui/list-detail.tsx` | List-detail layout |
| `lib/ui/empty-panel.tsx` | Empty state |
| `lib/ui/form-controls.tsx` | Form controls |
| `lib/ui/scroll-region.tsx` | Scroll region |
| `lib/ui/skeleton-block.tsx` | Skeleton loader |
| `lib/ui/state-banner.tsx` | State banner |
| `lib/ui/platform-notification.tsx` | Notification toast |
| `lib/ui/app-theme.tsx` | Theme provider |
| `lib/ui/use-animated-text.ts` | Animated text hook |

#### 2.4.7 frontend/lib/workspace/ -- Core (18 files)

| File | Purpose |
|------|---------|
| `workstation-kernel-shell.tsx` | Main shell -- nav, routing, surface mounting |
| `workstation-shell.ts` | Shell state types |
| `workspace-shell.ts` | Workspace data types |
| `workstation-shell-frame.tsx` | Shell frame layout |
| `workstation-split-workbench.tsx` | Split workbench |
| `workstation-surface-primitives.tsx` | Surface primitives |
| `workstation-titlebar.tsx` | Custom titlebar |
| `workspace-boundary.tsx` | Error boundary |
| `workspace-bootstrap.ts` | Bootstrap logic |
| `server-workspace-bootstrap.ts` | Server-side bootstrap |
| `workspace-json-request.ts` | JSON request helper |
| `workspace-services.tsx` | Service context |
| `workspace-setup-form.tsx` | Setup form |
| `desktop-startup-screen.tsx` | Desktop startup |
| `desktop-window-controls.tsx` | Window controls |
| `workstation-client.ts` | API client |
| `workstation-desktop-bridge.ts` | Tauri IPC bridge |
| `workstation-desktop-status.tsx` | Desktop status |

#### 2.4.8 frontend/lib/workspace/ -- Chat and Timeline (21 files)

| File | Purpose |
|------|---------|
| `workstation-chat-pane.tsx` | Main chat pane |
| `workstation-chat-pane-hooks.ts` | Chat hooks |
| `workstation-chat-pane-model.ts` | Chat state model |
| `workstation-chat-memory-loaders.ts` | Chat memory loaders |
| `workstation-chat-thread-events.ts` | Thread event types |
| `workstation-chat-timeline-projection.ts` | Timeline assembler |
| `workstation-stream-manager.ts` | SSE manager |
| `workstation-provider-events.ts` | Provider events |
| `chat-composer.tsx` | Message composer |
| `chat-message.tsx` | Message component |
| `sage-command-catalog.ts` | Slash commands |
| `model-capabilities.ts` | Model capabilities |
| `platform-brand.ts` | Branding |
| `platform-brand.test.ts` | Brand tests |
| `transcript-event-contract.ts` | Transcript types |
| `transparency-timeline.tsx` | Transparency timeline |
| `codex-chat/cell-components.tsx` | Timeline cells |
| `codex-chat/cells.ts` | Cell definitions |
| `codex-chat/event-projector.ts` | Event projector |
| `codex-chat/message-adapter.ts` | Message adapter |
| `codex-chat/timeline-reducer.ts` | Timeline reducer |

#### 2.4.9 frontend/lib/workspace/ -- Panes (24 files)

| File | Purpose |
|------|---------|
| `workstation-runs-pane.tsx` | Runs surface |
| `workstation-activity-pane.tsx` | Activity feed |
| `workstation-notifications-pane.tsx` | Notifications |
| `workstation-billing-pane.tsx` | Billing |
| `workstation-settings-pane.tsx` | Settings |
| `workstation-hardware-pane.tsx` | Hardware management |
| `workstation-hardware-status.tsx` | Hardware status |
| `workstation-gateway-operator-pane.tsx` | Gateway operator |
| `workstation-artifacts-pane.tsx` | Artifacts browser |
| `workstation-sage-connectors-pane.tsx` | MCP connector catalog |
| `workstation-sage-heartbeat-pane.tsx` | Sage heartbeat |
| `workstation-sage-profile-pane.tsx` | Sage profile |
| `workstation-sage-tools-pane.tsx` | Sage tools |
| `workstation-studio-integrations-pane.tsx` | Studio integrations |
| `workstation-deployed-agents-pane.tsx` | Deployed agents |
| `workstation-deployed-agent-analytics-pane.tsx` | Agent analytics |
| `workstation-deployed-agent-test-turn-pane.tsx` | Agent test turn |
| `workstation-platform-analytics-pane.tsx` | Platform analytics |
| `cloud-vps-setup-panel.tsx` | VPS setup |
| `hosted-mini-app-surface.tsx` | Mini app surface |
| `hosted-mini-apps-pane.tsx` | Mini apps pane |
| `application-surface-tabs.ts` | App surface tabs |
| `connector-setup-modal-shell.tsx` | Connector setup modal |
| `data-pane-error.tsx` | Error state |

#### 2.4.10 frontend/lib/workspace/ -- Deployed Agents + Sage Chat (20 files)

| File | Purpose |
|------|---------|
| `sage-chat/composer.tsx` | Sage composer |
| `sage-chat/constants.ts` | Sage constants |
| `sage-chat/hooks.ts` | Sage hooks |
| `sage-chat/transcript.tsx` | Sage transcript |
| `sage-chat/types.ts` | Sage types |
| `sage-chat/utils.ts` | Sage utilities |
| `deployed-agents/action-settings.tsx` | Action settings |
| `deployed-agents/agent-computer-detail.tsx` | Computer detail |
| `deployed-agents/ai-settings.tsx` | AI settings |
| `deployed-agents/components.tsx` | Components |
| `deployed-agents/constants.ts` | Constants |
| `deployed-agents/detail-view.tsx` | Detail view |
| `deployed-agents/external-agent-detail.tsx` | External agent detail |
| `deployed-agents/external-agent-provider-badges.ts` | Provider badges |
| `deployed-agents/inbox-view.tsx` | Inbox view |
| `deployed-agents/integration-settings.tsx` | Integration settings |
| `deployed-agents/playground-panel.tsx` | Playground |
| `deployed-agents/roster-sidebar.tsx` | Roster sidebar |
| `deployed-agents/types.ts` | Types |
| `deployed-agents/utils.ts` | Utilities |
| `deployed-agents/wizard.tsx` | Creation wizard |
| `workspace-channel-pairing-surface.tsx` | Channel pairing |
| `workstation-app-update-action.tsx` | App update action |

#### 2.4.11 Other lib (3 files)

| File | Purpose |
|------|---------|
| `lib/discovery/discovery-pane.tsx` | App/agent discovery |
| `lib/discovery/discovery-pane.module.css` | Discovery CSS |
| `lib/marketplace/marketplace-pane.tsx` | Marketplace |

### 2.5 empyralis-gateway/src/ (73 source files)

| File | Purpose |
|------|---------|
| `index.ts` | Gateway entry -- WSS connection, runtime registration |
| `config.ts` | Gateway configuration |
| `cloud/ws-client.ts` | WSS client to control plane |
| `cloud/heartbeat.ts` | Cloud heartbeat sender |
| `cloud/heartbeat-payload.ts` | Cloud heartbeat payload |
| `cloud/reconnect.ts` | Reconnection logic |
| `supervisor/client.ts` | HTTP client to supervisor (127.0.0.1:7788) |
| `supervisor/capability-router.ts` | Bundles channel + hardware execution |
| `supervisor/signing.ts` | Request signing |
| `channels/personal-runtime.ts` | Personal channel runtime base |
| `channels/personal-config-store.ts` | Config persistence |
| `channels/local-bridge-runtime.ts` | Local bridge runtime (Signal, iMessage, WeChat) |
| `channels/foundation/credential-redactor.ts` | Credential redaction |
| `channels/foundation/draft-manager.ts` | Draft management |
| `channels/foundation/outbound-store.ts` | Outbound queue |
| `channels/foundation/reconnect-utils.ts` | Reconnect utilities |
| `channels/foundation/typing-keepalive.ts` | Typing indicator |
| `channels/telegram/runtime.ts` | Telegram personal (GramJS) |
| `channels/telegram/session-store.ts` | Telegram sessions |
| `channels/telegram/message-mapper.ts` | Telegram normalization |
| `channels/telegram/outbound.ts` | Telegram outbound |
| `channels/telegram/login.ts` | Telegram login |
| `channels/telegram/reconnect.ts` | Telegram reconnect |
| `channels/whatsapp/runtime.ts` | WhatsApp personal (Baileys) |
| `channels/whatsapp/session-store.ts` | WhatsApp sessions |
| `channels/whatsapp/message-mapper.ts` | WhatsApp normalization |
| `channels/whatsapp/outbound.ts` | WhatsApp outbound |
| `channels/whatsapp/qr-login.ts` | WhatsApp QR login |
| `channels/whatsapp/login.ts` | WhatsApp login |
| `channels/whatsapp/reconnect.ts` | WhatsApp reconnect |
| `bridges/signal-cli-bridge.ts` | Signal CLI bridge |
| `bridges/bluebubbles-bridge.ts` | BlueBubbles iMessage bridge |
| `browser/runtime.ts` | Browser automation |
| `browser/session-store.ts` | Browser sessions |
| `browser/worker.ts` | Browser worker |
| `runtime/service-mode.ts` | Service mode |
| `runtime/desktop-permissions.ts` | Desktop permissions |
| `runtime/runtime-metadata.ts` | Runtime metadata |
| `protocol/codec.ts` | Wire protocol codec |
| `protocol/types.ts` | Protocol types |
| `pairing/token-store.ts` | Pairing tokens |
| `pairing/device-identity.ts` | Device identity |
| `health/service-inventory.ts` | Health/capabilities |
| `state/checkpoints.ts` | State checkpoints |
| `state/db.ts` | Local SQLite DB |
| `state/journal.ts` | State journal |
| `state/outbox.ts` | Outbox journal |
| `external-agent/proxy-runtime.ts` | External agent proxy |
| `dev/local-bridge-harness.ts` | Dev harness |
| `__tests__/` (21 test files) | All `.test.ts` files |

### 2.6 empyralis-supervisor/src/ (12 Rust files)

| File | Purpose |
|------|---------|
| `main.rs` | Supervisor daemon -- HTTP on 127.0.0.1:7788 |
| `execution.rs` | Execution engine |
| `capabilities/mod.rs` | Capability module root |
| `capabilities/shell.rs` | Shell sandbox |
| `capabilities/filesystem.rs` | Filesystem access |
| `capabilities/clipboard.rs` | Clipboard access (self-cycle) |
| `capabilities/control.rs` | Control surface |
| `capabilities/launch.rs` | App launch |
| `capabilities/ocr.rs` | OCR |
| `capabilities/screenshot.rs` | Screenshot |
| `capabilities/system.rs` | System operations |
| `capabilities/windows.rs` | Window management |

### 2.7 server/ -- V2 Target Backend (0 source files on disk, 22 inferred from __pycache__)

**All Python source files have been deleted. Only `__pycache__/*.pyc` remains.**

Inferred structure (from bytecode):
| File (inferred) | Purpose | Status |
|------|---------|--------|
| `server/__init__.py` | Package init | DELETED |
| `server/bot.py` | Bot entry point | DELETED |
| `server/cli.py` | CLI entry | DELETED |
| `server/agent/__init__.py` | Agent package | DELETED |
| `server/api/__init__.py` | API package | DELETED |
| `server/api/__main__.py` | API main entry | DELETED |
| `server/api/main.py` | FastAPI app | DELETED |
| `server/channels/__init__.py` | Channels package | DELETED |
| `server/channels/base.py` | Channel base class | DELETED |
| `server/channels/router.py` | Channel router | DELETED |
| `server/channels/telegram.py` | Telegram channel | DELETED |
| `server/conversations/store.py` | Conversation store | DELETED |
| `server/mcp/apps.py` | MCP apps config | DELETED |
| `server/mcp/client.py` | MCP client | DELETED |
| `server/memory/__init__.py` | Memory package | DELETED |
| `server/memory/service.py` | Memory service | DELETED |
| `server/oauth/exchange.py` | OAuth exchange | DELETED |
| `server/oauth/provider_configs.py` | OAuth configs | DELETED |
| `server/oauth/refresh.py` | OAuth refresh | DELETED |
| `server/tools/__init__.py` | Tools package | DELETED |
| `server/tools/shell.py` | Shell tool | DELETED |
| `server/vault/store.py` | Vault store | DELETED |

**Gap:** server/ has the right directory structure (one file per concern) but zero implementation -- skeletal compared to 488 `server_modules/` service files.

### 2.8 legacy/ (raw snapshot, not pruned)

Contains: `frontend/`, `docs/`, `references/`, `graphify-out/` (2026-06-30), `.venv/`, `venv/`, `python_engine/.venv/`, `.orion-stack/`, `.orion-object-store/`, `empyralis-gateway/` (.DS_Store only), `.pytest_cache/`, `.env`, `frontend/shared/` (duplicate of root `shared/`). No `server_modules/` in legacy.

### 2.9 shared/ (active at repo root)

| File | Purpose |
|------|---------|
| `design-system/tokens.ts` | Shared design tokens |
| `api-contract/index.ts` | API type contracts |
| `api-contract/client.ts` | API client types |
| `api-contract/model-tier-contract.ts` | Model tier definitions |
| `nav-manifest.ts` | Navigation destinations (SELF-CYCLE) |
| `nav-manifest.js` | Compiled nav manifest |
| `mini-app-sdk.js` | Mini-app SDK |

### 2.10 Root Configuration

`server.py`, `main.py`, `mcp_server.py`, `mcp.json`, `requirements.txt`, `requirements-worker.txt`, `Dockerfile.runtime`, `Dockerfile.sandbox`, `render.yaml`, `pytest.ini`, `empyralis-backend.spec`, `slack-app-manifest.json`, `.dockerignore`, `.cgcignore`, `.gitleaks.toml`, `.env`, `.env.example`, `package.json`, `bin/orion`, `bin/empyralis`, `config/niches.yaml`, `config/niches.yml`, `.github/workflows/` (build, ci, security-baseline, supply-chain), `frontend/package.json`, `frontend/tsconfig.json`, `frontend/next.config.ts`, `frontend/vercel.json`, `frontend/playwright.config.ts`, `frontend/Dockerfile`, `frontend/proxy.ts`, `empyralis-supervisor/Cargo.toml`, `empyralis-runtime-kernel/Cargo.toml`, `migrations/` (3 SQL files), `scripts/agent_computer.sh`

---

## 3. Subsystem Connection Map

Format: ASCII diagram with file:line references. [BROKEN] marks known gaps.

### Trace 1: Web Chat Turn

```
User types in web chat
  -> frontend/lib/workspace/workstation-chat-pane.tsx (ChatPane)
    -> frontend/lib/workspace/sage-chat/composer.tsx (Composer)
      -> POST /api/[...path]/route.ts (catch-all proxy)
        -> frontend/lib/server/control-plane-proxy.ts (HTTP proxy)
          -> server.py (FastAPI receives request)
            -> server_modules/agent_turn.py (AgentTurnRequest builder)
              -> server_modules/turn_runtime.py (execution switchboard)
                -> server_modules/direct_chat_generation_service.py (LLM generation)
                  -> server_modules/direct_chat_provider_service.py (provider selection)
                  -> LLM API call
                  <- LLM Response
                <- AgentTurnResponse
              <- JSON response
            <- HTTP response
          <- HTTP response
        <- API response
      <- streamed chunks
    -> frontend/lib/workspace/workstation-stream-manager.ts (SSE parsing)
      -> frontend/lib/workspace/workstation-chat-timeline-projection.ts (timeline)
        -> frontend/lib/workspace/codex-chat/event-projector.ts (tool events -> cells)
          -> frontend/lib/workspace/codex-chat/cell-components.tsx (render)
            -> frontend/lib/workspace/chat-message.tsx (render message)
```

### Trace 2: Telegram Bot Inbound

```
Telegram server -> webhook POST
  -> server_modules/connectors/telegram_ingress_service.py (webhook handler)
    -> server_modules/connectors/telegram_connector_services.py (parse update)
      -> server_modules/channel_adapter.py (NormalizedSageTurn)
        -> server_modules/agent_channel_router.py (channel routing)
          -> server_modules/sage_agent_runtime_service.py (agent loop)
            -> server_modules/agent_turn.py (AgentTurnRequest)
              -> server_modules/turn_runtime.py (execution)
                -> server_modules/direct_chat_generation_service.py (LLM)
              <- response
          <- response
    -> [OUTBOUND] Telegram Bot API sendMessage
```

### Trace 3: Personal Channel via Gateway

```
User sends WhatsApp message
  -> empyralis-gateway/src/channels/whatsapp/runtime.ts (Baileys listener)
    -> empyralis-gateway/src/channels/whatsapp/message-mapper.ts (normalize)
      -> empyralis-gateway/src/protocol/codec.ts (encode GatewayMessage)
        -> empyralis-gateway/src/cloud/ws-client.ts (WSS -> control plane)
          -> server_modules/gateway_protocol_service.py (decode)
            -> server_modules/gateway_execution_service.py (dispatch)
              -> server_modules/personal_channels_service.py (route)
                -> server_modules/personal_channel_sage_bridge_service.py (bridge)
                  -> server_modules/agent_turn.py (AgentTurnRequest)
                    -> server_modules/turn_runtime.py (execution)
                      -> LLM response
                  <- response
              <- response
            <- response
          <- [WSS downstream]
      -> empyralis-gateway/src/channels/whatsapp/outbound.ts (send reply via Baileys)
```

### Trace 4: Durable Run

```
User: "/run analyze this repo"
  -> frontend/lib/workspace/sage-command-catalog.ts (slash command match)
    -> POST /api/[...path]/route.ts
      -> server_modules/agent_turn.py (execution_mode="durable")
        -> server_modules/turn_runtime.py (durable route)
          -> server_modules/runs_engine.py (create run)
            -> server_modules/runs_execution.py (execute)
              -> server_modules/runs_core.py (lifecycle)
                -> server_modules/direct_chat_generation_service.py (LLM with tool loop)
                  -> [TOOL CALL] server_modules/tool_broker.py (route tool)
                    -> server_modules/mcp_registry_service.py (MCP invocation)
                      -> HTTP to remote MCP server
                    <- tool result
                  -> LLM processes result
                  -> ... (loop until completion)
                <- run result
              <- complete
            <- status update
          <- 202 Accepted
    -> [POLL] frontend/lib/workspace/workstation-runs-pane.tsx (show progress)
```

### Trace 5: MCP Connector OAuth Flow

```
User: "Connect my Linear account"
  -> frontend/lib/workspace/workstation-sage-connectors-pane.tsx (catalog)
    -> [OAUTH REDIRECT] Linear OAuth URL
      -> Linear redirects with code
        -> POST /api/[...path]/route.ts
          -> server_modules/connection_oauth_service.py:1361 (connect_app_via_oauth_to_mcp)
            -> Step 1: _exchange_linear() (OAuth code -> tokens)
            -> Step 2: connectors_actions.create_connector_vault() (store credential)
            -> Step 3: APP_MCP_SERVER_MAP lookup (get endpoint = https://mcp.linear.app/mcp)
            -> Step 4: mcp_registry_service.py (register MCP server)
              -> HTTP to MCP endpoint: list_tools()
              -> Store discovered tools in DB
            -> Step 5: Return tool list
          <- {ok: true, tools: [...]}
```

### Trace 6: Gateway Pairing and Hardware Execution

```
User opens Gateway on Mac
  -> empyralis-gateway/src/index.ts (startup)
    -> empyralis-gateway/src/pairing/device-identity.ts (generate device identity)
      -> empyralis-gateway/src/cloud/ws-client.ts (WSS connect)
        -> server_modules/gateway_pairing_service.py (pairing flow)
          -> [pairing link to frontend channel page]
      <- pairing complete
    -> empyralis-gateway/src/health/service-inventory.ts (advertise capabilities)
      -> server_modules/gateway_inventory_service.py (store capabilities)

User: "open Safari and search for flights"
  -> [follows Trace 1/2/3 to agent_turn.py]
    -> TOOL CALL: browser_open
      -> server_modules/tool_broker.py
        -> server_modules/hardware_runtime_target_resolver.py:93-101
          [BROKEN: self_hosted_node falls through to wrong label]
          -> server_modules/hardware_runtime_adapters/gateway_adapter.py
            -> [WSS] empyralis-gateway/src/supervisor/capability-router.ts:38-214
              [BROKEN: bundles channel messaging + hardware execution in one router]
              -> empyralis-gateway/src/supervisor/client.ts (HTTP POST to supervisor)
                -> empyralis-supervisor/src/main.rs (receive request)
                  -> empyralis-supervisor/src/capabilities/ (execute capability)
                    -> empyralis-supervisor/src/execution.rs (sandbox enforcement)
                  <- result
              <- result
            <- [WSS upstream] result
          <- tool result
      <- result streamed
    -> [BROKEN] codex-chat/event-projector.ts shows "Connecting to your Mac..."
       but emit_tool_progress never wired server-side -- chat goes silent during hardware work
```

---

## 4. Graphify Structural Analysis

### 4.1 Graph Stats

| Stat | Value |
|------|-------|
| Total nodes | 28,011 |
| Total edges | 72,014 |
| Communities | 1,133 (832 shown, 301 thin omitted) |
| Extraction | 98% EXTRACTED, 2% INFERRED |
| Inferred edges | 1,572 (avg confidence 0.62) |
| Built from | commit `0820a732c` |

### 4.2 God Objects -- Top 30

| Rank | Node | Edges | Impact |
|------|------|-------|--------|
| 1 | Communities (meta) | 763 | Graph structural artifact |
| 2 | `PATH` | 615 | String ref across codebase |
| 3 | `RunStartRequest` | 174 | Durable run launch type |
| 4 | `RunServiceTests` | 143 | Test suite |
| 5 | `InMemoryVirtualComputerRuntime` | 132 | Test runtime |
| 6 | `AgentManifest` | 127 | Agent definition type |
| 7 | `enforce_workspace_access()` | 124 | Auth middleware |
| 8 | `_scoped_connection()` | 123 | DB connection factory |
| 9 | `runtime_state_store_decision_command()` | 114 | State machine command |
| 10 | `_token()` | 105 | Token extraction |
| 11 | `AgentTurnRequest` | ~100 | Turn type (all channels converge) |
| 12 | `server.py` | ~95 | Composition root |
| 13 | `db.py` | ~90 | DB pool |
| 14 | `auth.py` | ~85 | Auth module |
| 15 | `runtime_config.py` | ~80 | Many imports |
| 16 | `direct_chat_generation_service.py` | ~75 | LLM generation |
| 17 | `tool_broker.py` | ~70 | Tool gateway |
| 18 | `runs_engine.py` | ~65 | Runs engine |
| 19 | `memory_service.py` | ~60 | Memory (cycles) |
| 20 | `local_queue.py` | ~55 | Queue (cycles) |
| 21 | `gateway_execution_service.py` | ~50 | Gateway (cycle) |
| 22 | `mcp_registry_service.py` | ~45 | MCP registry |
| 23 | `agent_channel_router.py` | ~40 | Channel routing |
| 24 | `personal_channels_service.py` | ~35 | Personal channels (cycle) |
| 25 | `workstation-kernel-shell.tsx` | ~30 | Frontend shell root |
| 26 | `sage_agent_runtime_service.py` | ~30 | Sage loop |
| 27 | `unified_memory_service.py` | ~30 | Memory (cycle) |
| 28 | `workspace_context_memory_adapter.py` | ~25 | Memory adapter (cycle) |
| 29 | `runs_execution.py` | ~25 | Runs execution (cycle) |
| 30 | `skills_service.py` | ~25 | Skills (cycle) |

Note: Ranks 11-30 are estimates. Graphify only reports top 10 exactly.

### 4.3 All 16 Import Cycles

| # | Cycle | Files | Severity |
|---|-------|-------|----------|
| 1 | Self-cycle | `empyralis-supervisor/src/capabilities/clipboard.rs` | Low |
| 2 | Self-cycle | `legacy/frontend/shared/nav-manifest.ts` | Low |
| 3 | Self-cycle | `scripts/orion_terminal/wizard/engine.py` | Low |
| 4 | Self-cycle | `shared/nav-manifest.ts` | Low |
| 5 | 3-file | `policy_service.py -> skills_service.py -> runs_execution.py -> policy_service.py` | MEDIUM |
| 6 | 3-file | `gateway_execution_service.py -> gateway_protocol_service.py -> personal_channels_service.py -> gateway_execution_service.py` | **HIGH** |
| 7 | 3-file | `conversation_memory_policy.py -> memory_service.py -> workspace_context_memory_adapter.py` | MEDIUM |
| 8 | 3-file | `memory_service.py -> workspace_context_memory_adapter.py -> unified_memory_service.py -> memory_service.py` | **HIGH** |
| 9 | 3-file | `local_queue.py -> run_service.py -> runtime_attachment_service.py -> local_queue.py` | MEDIUM |
| 10 | 3-file | `direct_chat_tool_catalog_service.py -> skills_service.py -> no_provider_service.py` | MEDIUM |
| 11 | 3-file | `policy_service.py -> skills_service.py -> runtime_config.py -> policy_service.py` | MEDIUM |
| 12 | 3-file | `local_queue.py -> runtime_runs_api.py -> runtime_route_registration_service.py` | MEDIUM |
| 13 | 3-file | `connector_validators.py -> discord_connector.py -> runtime_config.py` | MEDIUM |
| 14 | 3-file | `runtime_common.py -> runtime_config.py -> setup_sessions.py` | MEDIUM |
| 15 | 3-file | `hybrid_policy_service.py -> unified_memory_service.py -> memory_service.py` | **HIGH** |
| 16 | 4-file | `scripts/orion_terminal/__init__.py -> app.py -> flows.py -> flows_shared.py` | Low |

**3 HIGH severity cycles** block clean subsystem separation: gateway execution (#6), memory (#8), hybrid policy+memory (#15).

### 4.4 Isolated Nodes

301 of 1,133 communities are "thin" (omitted from display). Estimated ~800+ nodes with fewer than 3 edges.

**20 sampled isolated/small-community nodes:**

1. `routes_signal.py` (DEAD -- not mounted)
2. `routes_imessage.py` (DEAD)
3. `routes_wechat.py` (DEAD)
4. `routes_slack.py` (DEAD)
5. `routes_discovery.py` (DEAD)
6. `routes_mini_apps.py` (DEAD)
7. `connectors_actions.py` (DEPRECATED)
8. `mcp_server.py` (standalone)
9. `main.py` (rarely used)
10. `calorie_tracking_service.py` (domain-specific)
11. `inventory_skill.py` (domain-specific)
12. `voice_notification_policy_service.py` (specialized)
13. `agent_computer_permission_secret_model.py` (model class)
14. `customer_ops_pack.py` (utility)
15. `autopilot_connectors.py` (few imports)
16. `frontend/app/install/agent-computer.sh/route.ts` (serves script)
17. `frontend/app/healthz/route.ts` (simple check)
18. `frontend/v2/out/*` (static HTML)
19. `legacy/.orion-stack/workspace/*.md` (markdown)
20. `config/niches.yaml` (static config)

### 4.5 Communities

Count: 1,133 total. Top 10 by size:

| # | Community | Size | Cohesion | Description |
|---|-----------|------|----------|-------------|
| 0 | Meta | 763 | 0.00 | Graph structural |
| 1 | Agent Settings UI | 242 | 0.02 | Frontend agent config components |
| 2 | Run Lifecycle | 163 | 0.02 | Backend run functions |
| 3 | Chat Timeline | 213 | 0.03 | Frontend timeline types/hooks |
| 4 | UI Primitives | 174 | 0.02 | UI component library |
| 5 | Channel Brand | 100 | 0.02 | Channel brand/images |
| 6 | Operator Prompts | 120 | 0.05 | Prompt building |
| 7 | Command Handlers | 93 | 0.04 | Slash command handlers |
| 8 | Skill Scanner | 31 | 0.03 | Skill scanning |
| 9 | Runtime Checkpoints | 65 | 0.05 | Cleanup/state |
| 10 | Connector+AI | 108 | 0.03 | Connectors + providers |

**All communities have low cohesion (max 0.08)** -- characteristic of a monolith without strict module boundaries. Confirms the 75 known violations.

---

## 5. Channel System Truth Table

### Personal Channels

| Channel | Transport | Status | Session Owner | Routes Through Sage? | Requires Hardware? | Key Files | Known Gaps |
|---------|-----------|--------|---------------|---------------------|--------------------|-----------|------------|
| `telegram_personal` | GramJS via Gateway WSS | **live** | `paired_gateway` | YES | YES | `telegram/runtime.ts` (gateway), `telegram_ingress_service.py`, `personal_channel_sage_bridge_service.py:291-330` | Two code paths: Gateway vs Cloud Session Manager -- no shared code |
| `whatsapp_personal` | Baileys via Gateway WSS | **live** | `paired_gateway` | YES | YES | `whatsapp/runtime.ts`, `whatsapp_ingress_service.py`, `personal_channel_sage_bridge_service.py:331-370` | Only telegram+whatsapp shown in frontend |
| `discord_personal` | discord.py bot token via Gateway | **live** | `cloud_connector` (contradiction) | YES | YES | `discord_connector.py`, `discord_bot_runtime_service.py` | `runtime_lane: personal_gateway` vs `session_owner: cloud_connector` |
| `signal_personal` | signal-cli bridge HTTP poll | **wired** | `paired_gateway` | YES | YES | `signal-cli-bridge.ts`, `routes_signal.py` (DEAD), `personal_channel_sage_bridge_service.py:371-410` | Route DEAD. No frontend. No bridge health check |
| `imessage_personal` | BlueBubbles bridge HTTP poll | **wired** | `paired_gateway` | YES | YES (Mac) | `bluebubbles-bridge.ts`, `routes_imessage.py` (DEAD), `personal_channel_sage_bridge_service.py:411-450` | Route DEAD. Mac-only |
| `wechat_personal` | WeChat bridge HTTP poll | **planned** | `paired_gateway` | NO | YES | `routes_wechat.py` (DEAD), `personal_channel_sage_bridge_service.py:451-490` | `live_capable: false`. Bridge not implemented |

### Business Channels

| Channel | Transport | Status | Routes Through Sage? | Requires Hardware? | Known Gaps |
|---------|-----------|--------|---------------------|--------------------|------------|
| `telegram_bot` | Bot API (webhook+poll) | **live** | YES | NO | Primary cloud chatbot path. Hardcoded strings |
| `discord_bot` | Discord HTTP Interactions | **live** | YES | NO | Hardcoded strings |
| `slack` | Slack Events API + OAuth | **live** | YES | NO | `routes_slack.py` DEAD, but `slack_connector.py` live |
| `email` (gmail) | Gmail API (OAuth) | **partial** | PARTIAL | NO | MCP-wired, direct connector also exists |
| `email` (smtp_imap) | SMTP/IMAP | **partial** | PARTIAL | NO | Standard IMAP/SMTP |
| `whatsapp_business` | Twilio API | **blocked** | NO | NO | Meta banned AI assistants Jan 2026 |
| `apple_messages_business` | MSP API | **planned** | NO | NO | Needs Apple approval |
| `web_chat` | WebSocket widget | **planned** | NO | NO | Not implemented |
| `teams` | Teams Bot Framework | **planned** | NO | NO | Not in channel_lane_contract |
| `matrix` | Matrix CS API | **planned** | NO | NO | Not in channel_lane_contract |

### Work System Connectors

| Connector | Transport | Status | MCP? |
|-----------|-----------|--------|------|
| `github` | GitHub API webhooks | **live** | YES |
| `linear` | Linear API | **live** | YES |
| `notion` | Notion API | **live** | YES |
| `dropbox` | Dropbox API | **live** | YES |
| `s3` | AWS SDK | **live** | NO |
| `smtp` | SMTP/IMAP | **live** | NO |
| `wechat_work` | WeChat Work webhook | **live** | NO |
| `instagram_business` | Facebook Graph API | **live** | NO |
| `microsoft_365` | Microsoft Graph API | **partial** | Partial |

### Channel Violations

1. Adding a channel touches 12+ files (should be 1-2)
2. Frontend shows only 2 channels (Telegram, WhatsApp) vs 27 in backend catalog
3. Only 3 studio channels route through Sage: slack, discord, github
4. Two telegram_personal paths with no shared code
5. discord_personal metadata contradiction

---

## 6. MCP/Apps Truth Table

Source: `server_modules/connection_oauth_service.py:1187-1358` (APP_MCP_SERVER_MAP)

| # | Provider | OAuth | MCP Endpoint | Frontend? | Backend? | Status |
|---|----------|-------|-------------|-----------|----------|--------|
| 1 | Gmail | YES | `https://gmailmcp.googleapis.com/mcp/v1` | YES | YES | FULLY WIRED |
| 2 | Google Calendar | YES | `https://calendarmcp.googleapis.com/mcp/v1` | YES | YES | FULLY WIRED |
| 3 | Google Drive | YES | `https://drivemcp.googleapis.com/mcp/v1` | YES | YES | FULLY WIRED |
| 4 | GitHub | YES | `https://api.githubcopilot.com/mcp/` | YES | YES | FULLY WIRED |
| 5 | Slack | YES | `https://mcp.slack.com/mcp` | YES | YES | FULLY WIRED |
| 6 | Notion | YES | `https://mcp.notion.com/mcp` | YES | YES | FULLY WIRED |
| 7 | Linear | YES | `https://mcp.linear.app/mcp` | YES | YES | FULLY WIRED |
| 8 | Dropbox | YES | `https://mcp.dropbox.com/mcp` | YES | YES | FULLY WIRED |
| 9 | Figma | YES | `https://mcp.figma.com/mcp` | YES | YES | FULLY WIRED |
| 10 | Calendly | YES | `https://mcp.calendly.com` | YES | NO | FRONTEND ONLY |
| 11 | ClickUp | YES | `https://mcp.clickup.com/mcp` | YES | NO | FRONTEND ONLY |
| 12 | Webflow | YES | `https://mcp.webflow.com/mcp` | YES | NO | FRONTEND ONLY |
| 13 | Monday.com | YES | `https://mcp.monday.com/mcp` | YES | NO | FRONTEND ONLY |
| 14 | Box | YES | `https://mcp.box.com` | YES | NO | FRONTEND ONLY |
| 15 | Confluence | YES | `https://mcp.atlassian.com/v1/mcp/authv2` | YES | NO | FRONTEND ONLY |
| 16 | Miro | YES | `https://mcp.miro.com/` | YES | NO | FRONTEND ONLY |
| 17 | Intercom | YES | `https://mcp.intercom.com/mcp` | YES | NO | FRONTEND ONLY |
| 18 | DocuSign | YES | `https://mcp-d.docusign.com/mcp` | YES | NO | FRONTEND ONLY |
| 19 | Square | YES | `https://mcp.squareup.com/mcp` | YES | NO | FRONTEND ONLY (SSE may be needed) |
| 20 | Typeform | YES | `https://api.typeform.com/mcp` | YES | NO | FRONTEND ONLY |
| 21 | Vercel | YES | `https://mcp.vercel.com` | YES | NO | FRONTEND ONLY |
| 22 | Todoist | YES | `https://ai.todoist.net/mcp` | NO | PARTIAL | BACKEND ONLY |
| 23 | HubSpot | YES | `https://mcp.hubspot.com` | NO | PARTIAL | BACKEND ONLY |
| 24 | Jira/Atlassian | YES | `https://mcp.atlassian.com/v1/mcp` | NO | PARTIAL | BACKEND ONLY |
| 25 | Stripe | YES | `https://mcp.stripe.com` | YES | NO | OAUTH ONLY |
| 26 | Salesforce | YES | `https://api.salesforce.com/platform/mcp/v1/platform/` | YES | NO | OAUTH ONLY |
| 27 | Airtable | YES | `https://mcp.airtable.com/mcp` | YES | NO | OAUTH ONLY |
| 28 | Canva | YES | `https://mcp.canva.com/mcp` | YES | NO | OAUTH ONLY |
| 29 | Asana | YES | `https://mcp.asana.com/v2/mcp` | YES | NO | OAUTH ONLY |
| 30 | Zoom | YES | `https://mcp.zoom.us/mcp/zoom/streamable` | YES | NO | OAUTH ONLY |
| 31 | Microsoft 365 | YES | `None` (no public endpoint yet) | YES | NO | OAUTH ONLY |
| 32 | GitLab | YES | `https://gitlab.com/api/v4/mcp` | YES | NO | OAUTH ONLY |

**Summary:**
- **Fully wired:** 9 (Google x3, GitHub, Slack, Notion, Linear, Dropbox, Figma)
- **Frontend mcpEndpoint, no backend bridge:** 12
- **Backend knows endpoint, frontend missing:** 3
- **OAuth-only, no MCP tools wired:** 8
- **Total providers with MCP endpoints:** 31 (32nd = MS365 with `None`)

### MCP Fragility Points

1. MCP endpoint URLs duplicated in backend + frontend -- already drifted
2. streamable_http only -- no stdio, no SSE support
3. Adding OAuth app touches 5+ files
4. `connectors_actions.py` DEPRECATED but still imported
5. Square may need SSE instead of streamable_http (noted in source)
6. Microsoft 365 has no unified endpoint (Frontier preview)

---

## 7. Current vs Target Gap

### "ONE Primitive" Count

| Primitive | Target | Current in server_modules/ | Excess |
|-----------|--------|---------------------------|--------|
| Agent class | 1 | `agent_turn.py` + `sage_agent_runtime_service.py` + `agent_channel_router.py` + `agent/` (7) + 15 agent_* files | 25+ |
| Turn engine | 1 | `agent_turn.py` + `turn_runtime.py` + 31 direct_chat_* files + 23 runtime_* files | 55+ |
| Channel router | 1 | `agent_channel_router.py` + `channel_adapter.py` + `sage_turn_adapter.py` + `channel_lane_contract_service.py` + per-channel handlers | 5+ |
| Memory system | 1 | `memory_service.py` + `unified_memory_service.py` + `conversation_memory_policy.py` + `workspace_context_memory_adapter.py` + `memory_summary_service.py` + `agent_memory.py` | 6 (with cycles) |
| Tool broker | 1 | `tool_broker.py` + `tool_broker_guard_service.py` + `direct_tool_approval_service.py` + `direct_tool_loop_guard_service.py` + `skill_registry.py` | 5 |
| MCP client | 1 | `mcp_registry_service.py` + `skill_registry.py` + `connection_oauth_service.py` | 3 |
| OAuth vault | 1 | `secrets_broker.py` + `vault_store.py` + `connection_oauth_service.py` | 3 |
| Run engine | 1 | `runs_engine.py` + `runs_core.py` + `runs_execution.py` + `runs_history.py` + `run_service.py` + 10 runtime_run_* files | 15+ |
| Policy | 1 | `policy_service.py` + `runtime_policy.py` + `hybrid_policy_service.py` + `channel_blocking_policy_service.py` + `safe_mode_service.py` + `unified_governance_gate.py` | 6 (with cycles) |
| Gateway bridge | 1 | 8 gateway_* services + 3 hardware_* services + 3 hardware adapters + `supervisor_client.py` | 15+ |

### Duplicates (partial list)

| Duplicate | Instances |
|-----------|-----------|
| Channel routing | `agent_channel_router.py` + `sage_agent_runtime_service.py:_COMMUNICATION_SCOPES` + `sage_agent_runtime_service.py:_CONNECTOR_ROUTE_KEYWORDS` |
| Personal channel bridge | 6 near-identical wrappers in `personal_channel_sage_bridge_service.py:291-514` |
| Telegram paths | Gateway GramJS vs Cloud Session Manager -- 2 separate code paths |
| Error strings | 31 "I"/"my" instances across 8 files |
| MCP endpoints | `connection_oauth_service.py` + `workstation-sage-connectors-pane.tsx` -- drifted |
| Chat stream | 4 stream_* files where 1 should suffice |
| Chat entry | 3 entry_* files |
| Agent manifests | `agent_manifest.py` + `agent_registry_models.py` + `connector_manifests.py` |

### What server/ Has vs Needs

| server/ file | What it was | Gap to reach target |
|-------------|-------------|---------------------|
| `agent/__init__.py` | Empty package init | Must absorb 25+ agent files into one Agent class |
| `api/main.py` | FastAPI entry | Must consolidate all routes_* files |
| `channels/router.py` | Channel router | Must replace 5 routing layers |
| `channels/telegram.py` | One channel file | Must absorb 16 telegram files |
| `memory/service.py` | Memory service | Must replace 6 memory files with cycles |
| `mcp/client.py` | MCP client | Must replace 3 MCP files |
| `oauth/exchange.py` | OAuth exchange | Must replace provider-specific exchanges |
| `tools/shell.py` | Shell tool | Must absorb tool_broker + 4 guard files |
| `vault/store.py` | Vault store | Must replace secrets_broker + vault_store |

**Phase 1 gap:** 22 skeletal files vs 488 service files.

---

## 8. Dead Code Register

### 8.1 Dead Route Files (not mounted in server.py)

| File | Evidence |
|------|----------|
| `routes_signal.py` | Not in `server.py` router mounting. Signal has no backend route. |
| `routes_imessage.py` | Not in `server.py` router mounting. iMessage has no backend route. |
| `routes_wechat.py` | Not in `server.py` router mounting. `live_capable: false`. |
| `routes_slack.py` | Not in `server.py` router mounting. Slack works via `slack_connector.py` instead. |
| `routes_discovery.py` | Not in `server.py` router mounting. Discovery is frontend-only. |
| `routes_mini_apps.py` | Not in `server.py` router mounting. Mini-apps is frontend-only. |

### 8.2 Deprecated Code

| File | Evidence |
|------|----------|
| `connectors_actions.py` | Marked DEPRECATED. Replaced by MCP. Still imported by `connection_oauth_service.py:1378` for `create_connector_vault()`. |

### 8.3 Dead Code in Live Files (violations)

| Location | Issue |
|----------|-------|
| `sage_command_dispatcher.py:21-22` | "I'm temporarily unavailable", "My context was too full" -- platform impersonates agent |
| `sage_reply_dispatcher.py:35` | "I processed your message but couldn't produce a response" |
| `channel_execution_service.py:196` | "I hit an internal problem" |
| `quota_response_service.py:30-34` | 3 "I" messages in quota responses |
| `autopilot_runtime_support_service.py:158-180` | 7 "I" messages |
| `universal_operator.py:56-226` | 4 "I" messages |
| `voice_notification_policy_service.py:15` | "I can take this as a voice instruction" |
| `inventory_skill.py:251-289` | 5 "I"/"my" messages |
| `sage_agent_runtime_service.py:130-195` | `_COMMUNICATION_SCOPES`, `_CONNECTOR_ROUTE_KEYWORDS`, `_GATEWAY_ROUTE_KEYWORDS` hardcode channels |
| `sage_agent_runtime_service.py:832-836` | "my hardware", "my mac" in agent code |
| `personal_channel_sage_bridge_service.py:291-514` | 6 near-identical wrappers, `build_personal_channel_reply_async()` exists but unused |
| `hardware_runtime_target_resolver.py:93-101` | Bug: `self_hosted_node` falls to wrong label |
| `hardware_runtime_target_resolver.py:121-128` | Bug: gateway-offline loses execution env info |
| `channel_lane_contract_service.py:50-56` | `discord_personal` metadata contradiction |
| `agent_channel_router.py:62-66` | `LOCAL_BRIDGE_PERSONAL_CHANNELS` hardcoded |

### 8.4 Dead Subsystem: frontend/v2/

Zero source files. Static export only: `index.html`, `chat.html`, `settings.html`, `setup.html` -- all show "Loading...". No API calls. Bare skeleton to be deleted once legacy frontend rewired.

### 8.5 Deleted but Recoverable: server/

22 Python source files deleted. `__pycache__/*.pyc` confirms prior existence. Recoverable from version control. Directory structure preserved.

### 8.6 Not Audited: deploy/

20 shell/python scripts in `deploy/`. Not evaluated in this audit. Appear to be one-off deployment/debugging scripts. May contain hardcoded credentials.

---

## Document Maintenance

**Generated:** 2026-07-02 from commit `0820a732c`

**Update triggers:**
- After refactors touching 10+ files in `server_modules/`
- When `server/` gains implementation files
- When `frontend/v2/` is deleted or replaced
- When channel routes are mounted/unmounted
- When new MCP connectors are added
- After running `graphify cluster-only .`

**Related documents:**
- `/docs/PLATFORM.md` -- prescriptive architecture decisions and known violations
- `/graphify-out/GRAPH_REPORT.md` -- auto-generated structural knowledge graph
- Linear PLATFORM OVERVIEW -- target architecture and 7-phase plan
- `/docs/handoff/` -- agent handoff documents directory
