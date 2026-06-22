# Project Map

Last verified: 2026-06-23
Latest verified green commit: see git log

## Current Platform Shape

The active platform is centered in:
- `/Users/mansur/empyralis/server_modules`
- `/Users/mansur/empyralis/frontend`
- `/Users/mansur/empyralis/mobile`
- `/Users/mansur/empyralis/src-tauri`
- `/Users/mansur/empyralis/empyralis-supervisor`

`/Users/mansur/empyralis/backend` is a frozen legacy Nest control-plane sidecar. It is not part of the active launch path, and the default local stack now skips it until it is repaired.

The system is one runtime platform with multiple shells, not multiple product brains.

Shell classes are frozen as:
- `full_shell`
  - mobile
  - web
  - desktop
- `channel_shell`
  - Live Telegram path: hosted bot (recommended)
  - Advanced/fragile personal Agent Computer channel: Telegram personal (MTProto)
  - **WhatsApp: NOT SUPPORTED.** Baileys is banned; Business API bars third-party AI as of Jan 15 2026.
  - Planned personal Agent Computer bridge channels: Signal, iMessage, WeChat
  - Live when configured cloud/business channels: Telegram Bot, Slack, Discord
  - Email setup: Gmail through Google Workspace, Microsoft 365, or custom SMTP / IMAP
  - Planned/partial channels: generic Email channel, Web Chat, Webhook, Teams, Matrix, phone

Channel shells may do:
- conversation
- summaries
- notifications
- lightweight approvals where supported

Channel shells may not become:
- deep admin surfaces
- separate product brains
- separate policy engines

## Backend Service Map

In this document, "backend" means the active runtime/backend under `server_modules`, not the legacy Nest sidecar in `/backend`.

### Identity And Workspace Control

Primary modules:
- `server_modules/auth.py`
- `server_modules/control_plane_repository.py`
- `server_modules/db.py`

Responsibilities:
- user and workspace auth
- tenant and workspace resolution
- workspace access enforcement
- local fallback auth storage
- Postgres-backed control-plane schema and RLS session scope

### Captain / Specialist / App Runtime Layer

Primary modules:
- `server_modules/agent_registry_api.py`
- `server_modules/agent_registry_repository.py`
- `server_modules/agent_specialist_repository.py`
- `server_modules/specialist_service.py`
- `server_modules/app_bridge_service.py`
- `server_modules/shared_operational_board_service.py`
- `server_modules/runtime_attachment_service.py`
- `server_modules/hybrid_policy_service.py`
- `server_modules/entitlements_service.py`

Responsibilities:
- Sage and specialist install inventory
- captain identity metadata and stable-install projection
- specialist mode contracts and authoring-mode gating
- specialist service contracts
- shared operational board storage, permissions, and version history
- app-agent bridge enforcement
- runtime selection and hybrid placement
- entitlement and quota policy

### Run And Turn Orchestration

Primary modules:
- `server_modules/agent_turn.py`
- `server_modules/run_service.py`
- `server_modules/runs_core.py`
- `server_modules/runs_engine.py`
- `server_modules/runs_execution.py`
- `server_modules/runtime_run_entry_service.py`
- `server_modules/runtime_run_query_service.py`
- `server_modules/runtime_run_control_service.py`
- `server_modules/runtime_run_approval_service.py`
- `server_modules/runtime_run_delegation_service.py`

Responsibilities:
- canonical run lifecycle
- approval flow
- delegation and child-run orchestration
- live and archived run query paths
- surface-safe run detail and history responses

### Memory, Context, And Activity

Primary modules:
- `server_modules/memory_service.py`
- `server_modules/unified_memory_service.py`
- `server_modules/personal_context_engine.py`
- `server_modules/activity_ledger_service.py`
- `server_modules/notification_service.py`

Responsibilities:
- memory shaping
- shared operational board projection into unified memory
- explicit state-layer model (`captain private`, `specialist private`, `shared operational board`, `artifacts/history`)
- Sage memory payload assembly
- personal context event feed
- durable activity ledger
- notification feed composition

### Tool, Secret, And Connector Boundary

Primary modules:
- `server_modules/tool_broker.py`
- `server_modules/secrets_broker.py`
- `server_modules/vault_store.py`
- `server_modules/connectors_actions.py`
- `server_modules/agent_channel_router.py`
- `server_modules/file_bridge_service.py`

Responsibilities:
- tool brokering
- secret and vault access
- connector class contracts (`api_connector`, `browser_connector`, `media_generation_connector`)
- sandbox/runtime state-layer policy propagation
- brokered connector execution and approval gating
- API-first connector routing with browser fallback only where needed
- inbound channel routing and channel-shell separation
- artifact export and managed file-bridge rules for connector outputs

### Local Cluster And Supervisor

Primary modules:
- `server_modules/local_queue.py`
- `server_modules/runtime_runtime_api.py`
- `server_modules/supervisor_client.py`
- `server_modules/machine_lease_service.py`

Responsibilities:
- local cluster lifecycle APIs
- worker registration and heartbeat
- revoke and recover flow
- machine lease and local runtime coordination
- explicit local queue pressure, retry, and dead-letter baseline for operator surfaces

## Persistence Map

There are three important persistence layers today.

### 1. Control-Plane Postgres Schema

Defined in `server_modules/control_plane_repository.py`.

Core identity and workspace tables:
- `tenants`
- `users`
- `auth_identities`
- `workspaces`
- `workspace_memberships`

Thread and conversation tables:
- `agent_threads`
- `agent_sessions`
- `agent_turns`

Workflow and agent definition tables:
- `workflow_definitions`
- `workflow_versions`
- `runtime_profiles`
- `agent_definitions`
- `agent_definition_versions`
- `workspace_agent_installs`
- `workspace_inventory_items`

Install contract truth in `workspace_agent_installs` now includes:
- captain display metadata projected against a stable captain install id
- explicit specialist operating mode:
  - `owner_edit`
  - `owner_test`
  - `customer_live`

Specialist and control-plane extension tables:
- `agent_manifests`
- `agent_bible_versions`
- `agent_skill_bindings`
- `agent_connector_bindings`
- `agent_channel_bindings`
- `agent_runtime_profiles`

Context, scheduler, channel, security, and audit tables:
- `personal_context_events`
- `agent_scheduler_wake_requests`
- `agent_channel_events`
- `agent_secret_access_events`
- `agent_egress_events`
- `agent_channel_execution_leases`
- `security_control_states`
- `security_control_events`
- `activity_ledger_events`

### 2. Durable Runtime-State Schema

Defined in `server_modules/run_state_schema.sql`.

Runtime-state tables:
- `live_runs`
- `run_transitions`
- `run_approvals`
- `local_queue_claims`
- `local_queue_dead_letters`
- `fleet_worker_registrations`
- `fleet_queue_partitions`
- `run_archive`
- `runtime_sessions`

This is the active operational substrate for live runs, approvals, worker claims, local queue durability, and archived run state.

### 3. Local Auth Fallback SQLite

Defined in `server_modules/auth.py`.

Fallback local tables include:
- `users`
- `workspace_memberships`
- `workspace_registry`
- `workspace_policies`
- `tenant_policies`
- `tenant_enterprise_settings`
- `user_enterprise_security`
- `user_auth_methods`
- `user_provider_connections`
- `user_identity_versions`
- `auth_sessions`
- `user_devices`

This fallback exists for local and bootstrap behavior. It must preserve the same tenant and workspace semantics as the control plane.

## Migration State

Repo migrations currently present:
- `migrations/enable_rls.sql`
- `migrations/add_agent_specialist_persistence.sql`
- `migrations/add_workspace_inventory_items.sql`

What they establish:
- tenant and workspace RLS enablement
- specialist persistence and security/control tables
- inventory table and policy

## Security And Isolation Truth

The codebase assumes:
- tenant and workspace scoping on all control-plane reads and writes
- Postgres RLS enforcement for control-plane tables
- strict API gateway entrypoints
- brokered tool and secret access
- fail-closed runtime durability and hybrid policy enforcement

### Current Auth And Public Ingress Boundary

Protected auth path is centered in:
- `server_modules/auth.py`
- `server_modules/runtime_common.py`
- `server_modules/routes_connectors.py`

Current auth rules:
- protected routes resolve through `get_current_user`, `require_api_key`, `require_member_api_key`, or `require_admin_api_key`
- auth-disabled mode is explicit local-dev only
- local-dev defaults to `member` access on workspace `default`
- local-dev elevation requires explicit env-backed role and workspace scope
- production rejects auth-disabled mode instead of silently running without auth

Intentional public ingress in this router is limited to verified webhook routes.
These routes are ingress enforcement points; they are not by themselves proof
that every matching product card is launch-ready:
- `/channels/slack/events`
  - enforced by `server_modules/connectors_actions.py` and `server_modules/connectors/slack_connector.py`
- `/channels/github/webhook`
  - enforced by `server_modules/connectors_actions.py` and `server_modules/connectors/github_connector.py`
- `/connectors/discord/webhook`
  - enforced by `server_modules/connectors_actions.py` and `server_modules/connectors/discord_connector.py`
- `/channels/whatsapp/twilio/webhook`
  - enforced by `server_modules/connectors/autopilot_runtime_exports.py`
  - `server_modules/connectors/autopilot_endpoint_service.py`
  - `server_modules/connectors/whatsapp_webhook_bridge_service.py`

Ingress rules:
- Slack requires Slack signing-secret verification before parse
- GitHub requires a configured connector webhook secret plus valid HMAC before parse and activity append
- Discord requires signature headers, timestamp, and configured public key before parse, dispatch, or run creation
- Twilio WhatsApp requires a configured shared secret before form parse and inbound handling
- operational status and autopilot profile routes remain protected by `require_api_key`

## CI Truth

GitHub workflows currently in `.github/workflows`:
- `ci.yml`
- `security-baseline.yml`
- `supply-chain.yml`
- `build.yml`

As of `b3eca81`, CI, security baseline, and supply chain baseline were green.

## What Is Real Versus Still Pending

Real and implemented:
- canonical backend golden path smoke proof in `server_modules/tests/test_golden_path.py`
- rendered web auth/session proof through the current control-plane BFF
- rendered web cloud-backed assistant answer through the current shell
- serious first-send task requests now promote into the durable run path
- canonical parent and delegated install-backed runtime attachment enforcement
- live install-backed local completion through the canonical agent-run path
- automatic local summary publish into the hybrid summary-bridge store for allowed payload classes
- validated hybrid summary-bridge publish/ingest contract with fail-closed offline fallback
- hybrid policy enforcement in runtime selection
- durable activity ledger and timeline API
- contract-aligned `/runs`, `/activity/timeline`, `/approvals`, and notifications backend surfaces
- install-backed `app_to_sage` and `app_to_specialist` bridge execution
- local cluster lifecycle APIs
- specialist service contracts
- mobile and desktop parity contract reflected in code paths

Still incomplete:
- rendered local proof
- rendered hybrid proof
- live hosted-captain degraded summary-bridge consumption proof
- generic non-install local routing without explicit runtime binding
- fully distributed summary-bridge replication beyond the local persisted bridge store
- consolidated operator UI for hybrid/local control
- `sage_to_app` handoff and `app_to_connector_runtime` product flows
- full frontend rebuild around the new contracts

## Frozen Frontend And BFF Contract Boundary

The next UI rebuild must consume these backend and BFF truths as fixed contracts:
- `/api/control-plane/session`
- `/api/control-plane/auth/me`
- `/api/control-plane/providers/runtime-availability`
- `/api/chat/master-context`
- `/api/turn`
- `/api/runs`
- `/api/activity/timeline`
- `/api/approvals`
- `/api/approvals/resolve`
- the runtime workspace aliasing performed inside the current BFF layer

The rebuild may change:
- layout, navigation, and route structure
- visual system, motion, spacing, and component composition
- how timeline and approval state are grouped, filtered, and presented
- mobile versus desktop information density

The rebuild may not change:
- auth/session semantics
- workspace access semantics
- `/turn` versus `/runs` contract meaning
- runtime placement or hybrid sync policy in the client
- approval, artifact, memory, or notification truth in the client
