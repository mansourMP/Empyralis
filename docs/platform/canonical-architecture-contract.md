# Canonical Architecture Contract

Last verified: 2026-04-13
Phase: A
Prompt: 01
Status: Defined now, not yet fully enforced

This document freezes the launch-path architecture rules for the active runtime.
It is the source of truth for how turn ingress, deployed-agent ingress, memory,
quota, and cost accounting are supposed to fit together before deeper
enforcement work in later prompts.

## Architecture Truths

1. A deployed agent is not a separate engine.
   A deployed agent is deployment configuration layered on top of the canonical
   turn engine.

2. Telegram (hosted bot), Discord, Slack, and future channels are shells.
   Channels own ingress normalization and egress formatting only.
   Channels do not own a separate product brain, policy engine, memory engine,
   or run engine.
   Note: WhatsApp is not a supported channel (see channel ground truth decisions).

3. `server_modules/agent_turn.py` is the canonical turn contract.
   All user-facing and system-facing work must become an
   `AgentTurnRequest` before execution.

4. `server_modules/turn_runtime.py` is the execution switchboard.
   It decides whether a canonical turn resolves through direct chat or durable
   execution. It is not an alternate engine.

5. `server_modules/agent_channel_router.py` is the external channel ingress
   adapter.
   It resolves channel owner, identity, preflight policy, memory/quota
   overlays, then hands one canonical turn into `agent_turn.py`.

6. `server.py` is the composition root only.
   It mounts routers, lifecycle, middleware, and shared service wiring.
   It must not contain product logic.

## Canonical Paths

### A. Web Turn Ingress

```txt
server.py
-> runtime_runs_api.py /turn
-> agent_turn.resolve_direct_chat_turn_request() or request_body_to_turn_request()
-> agent_turn.normalize_server_owned_turn_request()
-> agent_turn.agent_turn()
-> turn_runtime.execute_agent_turn_request()
-> direct chat execution OR durable execution
-> normalized result
```

Boundary rule:
Public web chat enters through `/turn`.
No other web route is allowed to invent a parallel turn ingress contract.

### B. Durable Run Start

```txt
caller
-> turn_runtime.execute_run_start_request_via_turn_runtime()
-> run_service.execute_run_start_request_via_turn_runtime()
-> agent_turn.resolve_run_start_turn_request()
-> execute_durable_turn_request()
```

Boundary rule:
A run start is a specialized canonical turn ingress.
It is not a separate product execution path.

### C. Deployed-Agent Channel Ingress

```txt
connector or internal channel route
-> agent_channel_router.route_inbound_channel_message()
-> agent_channel_router.prepare_canonical_channel_turn()
-> agent_channel_router._build_channel_turn_request()
-> agent_channel_router.execute_prepared_channel_turn()
-> agent_channel_router.execute_canonical_channel_turn()
-> agent_turn.execute_system_agent_turn()
-> agent_turn.agent_turn()
-> turn_runtime.execute_agent_turn_request()
```

Boundary rule:
All public deployed-agent channel traffic must converge on
`route_inbound_channel_message()`.
No connector may create a separate deployed-agent execution contract.

### D. Memory

Current public deployed-agent path:

```txt
agent_channel_router.route_inbound_channel_message()
-> deployed_agent_memory_service.load_deployed_agent_memory_context()
-> _build_channel_turn_request(prior_messages, business_plan)
-> agent_turn.agent_turn()
-> deployed_agent_memory_service.persist_deployed_agent_memory_snapshot()
```

Boundary rule:
Memory is assembled before canonical turn execution.
`agent_turn` owns thread/session persistence, not channel-history lookup.
Connectors must never load or persist conversation memory directly.

### E. Quota And Throttling

Current public-channel path:

```txt
agent_channel_router.route_inbound_channel_message()
-> deployed_agent_rate_limit_service.enforce_deployed_agent_daily_message_limit()
-> agent_channel_router.execute_prepared_channel_turn()
-> channel_concurrency_service.channel_execution_slot()
```

Boundary rule:
Product quota and transport concurrency are separate controls, but both must be
applied inside the canonical channel ingress path.
Connectors must not invent their own user-visible limit behavior.

### F. Cost Accounting

Canonical path:

```txt
agent_turn.agent_turn()
-> turn_runtime / run_service durable execution
-> usage snapshot generation
-> usage_reporting.usage_row_from_snapshot()
-> deployed_agent_cost_cap_service.settle_deployed_agent_monthly_cost_cap()
-> control_plane_repository monthly deployment ledger
```

Boundary rule:
Pricing, burn, caps, and analytics must derive from one usage-accounting chain.
No connector or UI surface may compute deployment burn independently.

## Subsystem Ownership Map

### 1. `server.py`

Owns:
- FastAPI app creation
- middleware registration
- router mounting
- process lifecycle

Must not own:
- turn policy
- connector business logic
- quota logic
- memory logic
- deployment behavior

### 2. `server_modules/agent_turn.py`

Owns:
- `AgentTurnRequest` contract
- turn request builders
- turn policy normalization
- trace/session/thread lifecycle
- canonical execution handoff into `turn_runtime`

Must not own:
- connector-specific parsing
- webhook semantics
- channel transport formatting
- direct database quota mutations
- provider pricing tables

### 3. `server_modules/turn_runtime.py`

Owns:
- canonical execution switch between sync direct chat and durable run execution
- run-start bridging into the durable path

Must not own:
- route/auth logic
- connector logic
- channel identity resolution
- quota logic
- memory assembly

### 4. `server_modules/agent_channel_router.py`

Owns:
- channel ingress normalization
- owner resolution
- channel preflight controls
- assembly of one canonical `AgentTurnRequest` for external channel traffic
- outbound event persistence for channel traffic

Must not own long-term:
- all quota logic
- all memory logic
- all health safety logic
- all activity ledger logic
- god-object orchestration of every public policy

### 5. Memory Policy Layer

Owns:
- conversation memory keying
- summary generation
- bounded history assembly
- memory persistence

Must not own:
- connector routing
- execution dispatch
- UI behavior

### 6. Quota Policy Layer

Owns:
- daily user quota
- concurrency gates
- product-visible limit reasons

Must not own:
- transport formatting
- pricing
- memory

### 7. Usage Accounting Layer

Owns:
- pricing registry
- usage normalization
- cost rollups
- cap settlement

Must not own:
- connector routing
- auth logic
- UI logic

## Forbidden Bypass Paths

These bypasses are architecture debt. They should be removed or wrapped behind
the canonical boundaries in later prompts.

1. Direct construction of `AgentTurnRequest` outside `agent_turn.py` request
   builders.
   Current repo status: no known production-code violations.

2. Direct execution of canonical turns from route/controller code outside the
   canonical ingress surfaces.
   Current repo status: no known production-code violations.

3. Direct `create_run()` from connector flows for user-facing work.
   Compatibility callback names are allowed only when they resolve into
   `turn_runtime` run-start bridging or canonical channel routing.
   Current violations:
   - `server_modules/connectors/whatsapp_webhook_service.py` (legacy dead code — WhatsApp channel is not supported)
   - `server_modules/connectors/discord_connector.py`
   - `server_modules/runs_execution.py`

4. Connector bootstrap code calling system turn/run execution directly.
   Compatibility bootstrap wrappers are acceptable only when they delegate
   immediately into `turn_ingress_service` or `turn_runtime`.
   Current violations:
   - `server_modules/runtime_run_delegation_service.py`
   - `server_modules/runtime_run_replay_service.py`
   - `server_modules/runtime_webhook_trigger_service.py`
   - `server_modules/demo_workflows.py`

5. Channel-specific memory reads/writes outside the memory policy owner.

6. Channel-specific quota messaging outside the canonical channel ingress path.

7. Independent cost or burn calculations outside the usage-accounting chain.

## Allowed Entry Surfaces

1. `server_modules/runtime_runs_api.py` `/turn`
   The canonical public web turn ingress.

2. `server_modules/turn_runtime.py` run-start functions
   The canonical run-start ingress functions.

3. `server_modules/agent_channel_router.py`
   `route_inbound_channel_message()`
   The canonical public deployed-agent channel ingress.

4. `server_modules/agent_channel_router.py`
   `route_transport_channel_message()`
   Transitional transport-channel ingress that must converge toward the same
   policy model as `route_inbound_channel_message()`.

## Decision Rules

1. If a new surface needs execution, it must produce an `AgentTurnRequest` and
   enter through the canonical turn path.

2. If a new surface needs a durable run, it must enter through turn-runtime
   run-start bridging, not `create_run()` directly.

3. If a new channel is added, it is a shell only.

4. If a deployed agent needs behavior, it is config plus policy overlays on the
   same engine.

5. If a feature cannot be explained as config over the canonical turn engine,
   it is probably architectural drift.

## Prompt 01 Success Condition

Prompt 01 is complete when the team accepts these as non-optional rules:

- one canonical path for turn ingress
- one canonical path for run start
- one canonical path for deployed-agent channel ingress
- one canonical policy owner for memory
- one canonical policy owner for quota
- one canonical usage-accounting chain for cost
- an explicit removal plan for every listed bypass path
