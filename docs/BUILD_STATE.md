# Empyralis Build State

> **OUTDATED (2026-07-23):** this is a point-in-time build-state snapshot from
> 2026-07-03 (Phase T, boot reliability) — three weeks of subsequent work have
> moved the platform far past this phase; current state lives in
> `docs/PLATFORM-MAP.md`. It also predates the 2026-07-23 founder ruling that
> "Sage" is dead product terminology (the platform has only agents —
> owner-facing, customer-facing serving the owner, and AskAI); this document
> uses "Sage" throughout as a live concept. Kept for history; do not build
> from this.

**Last updated:** 2026-07-03
**Current phase:** T (boot reliability — in progress)
**Branch:** main
**Known-good commit:** `c499c6d60` (Phase S: real turn completes)

## Boot Requirements (Phase T)

The server runs a startup preflight at `server_modules/preflight.py` before serving.
Any missing dependency raises `PreflightError` — the server refuses to boot
half-alive.

### Required

| Dependency | Env Var | Check |
|-----------|---------|-------|
| Rust runtime kernel | `EMPYRALIS_RUNTIME_KERNEL_BIN` | Binary must exist at path |
| PostgreSQL | `DATABASE_URL` | asyncpg connection + `workspace_agent_installs` has stage_4b columns |
| Redis | `REDIS_URL` (default `redis://localhost:6379`) | PING — skip with `EMPYRALIS_SKIP_REDIS_CHECK=true` |

### Exact boot steps

```sh
# 1. Kernel (one-time build or after Rust changes)
cd /path/to/empyralis
cargo build --manifest-path empyralis-runtime-kernel/Cargo.toml
export EMPYRALIS_RUNTIME_KERNEL_BIN="$(pwd)/empyralis-runtime-kernel/target/debug/empyralis-runtime-kernel"

# 2. Database
export DATABASE_URL="postgresql://localhost:5432/empyralis_dev"
# Ensure migrations are applied (preflight checks stage_4b columns):
#   psql $DATABASE_URL -f migrations/stage_4b_agent_isolation.sql

# 3. Redis (optional — skip if not installed)
export REDIS_URL="redis://localhost:6379"
# or: export EMPYRALIS_SKIP_REDIS_CHECK=true

# 4. AI provider keys (at least one required for chat)
export DEEPSEEK_API_KEY="sk-..."

# 5. Start
python server.py
# → preflight runs → /health ok → real turn works
```

### Verify boot

```sh
curl http://127.0.0.1:8001/health
# {"ok":true}
```

## Phase T — Boot Reliability Results

### Startup preflight ✅
- Module: `server_modules/preflight.py` (106 lines)
- Checks: kernel binary, PostgreSQL + stage_4b columns, Redis
- Wired into `server.py` lifespan — runs before first request
- Fails loudly with `PreflightError` naming each gap

### Test fixes ✅
- 3 pre-existing failures in `test_direct_chat_operator_binding_service.py` resolved
- Root cause: `_operator_namespace()` helper missing `_build_always_on_direct_chat_tools` and `_build_registry_entries`; `DirectChatOperatorToolRoutingBindings` no longer has `approval_required_for_direct_tool` (approval-era vestige)
- 20/20 binding tests pass; 10/10 preflight tests pass

### Test baseline updated
| Suite | Tests | Status |
|-------|-------|--------|
| `test_direct_chat_operator_binding_service.py` | 20 | ✅ All pass |
| `test_preflight.py` | 10 | ✅ All pass |
| `test_ledger_audit.py` | 13 | ✅ All pass |
| `test_hierarchy.py` | 33 | ✅ All pass |
| `test_triage.py` | 15 | ✅ All pass |
| **Total** | **91** | **0 failures** |

## Phase Q — Live Verification Results

### Step 1: BUILD_STATE.md bootstrap ✅
- Created this file from git log, ROLLBACK.md, and prior commit messages.

### Step 2: Frontend build ✅
```
▲ Next.js 16.2.6 (Turbopack)
✓ Compiled successfully in 2.9s
✓ Generating static pages (16/16) in 153ms
55 routes built, 0 errors
```

### Step 3: Server boot + health ✅
```
$ curl http://127.0.0.1:8001/health
{"ok":true}
```
- PostgreSQL: running (localhost:/tmp, 63 tables, 882 ledger events)
- Redis: running (PONG)
- Rust runtime kernel: **built during Phase Q** (previously unbuilt — all governance decisions returned `runtime_kernel_unavailable`)
  - Binary: `empyralis-runtime-kernel/target/debug/empyralis-runtime-kernel`
  - Set `EMPYRALIS_RUNTIME_KERNEL_BIN` to the absolute path before boot
- Real chat turn: blocked by Rust kernel before Phase Q build. After build, server restart needed. Owner manual step below.

### Step 4: Migrations check ✅ (2 gaps found and fixed)

| Migration | Before Phase Q | After Phase Q |
|-----------|---------------|---------------|
| `stage_4b_agent_isolation.sql` | ❌ NOT applied | ✅ Applied — 5 columns added |
| `enable_rls.sql` | ❌ NOT applied | ❌ Still deferred (needs session variable wiring for local dev) |
| `vault_migration_stage4b.py` | N/A (Python module) | N/A — operates on in-memory credential dicts |

**Columns applied by stage_4b migration:**
```
enabled_tools       | text[]    | (Phase K tool gating)
enabled_connectors  | text[]    | (Phase K connector gating)
channel_bindings    | jsonb     | (Phase F channel routing)
subagents_enabled   | boolean   | (Phase L sub-agent gate)
hardware_access     | text      | (Phase F hardware scoping)
```

31 existing Sage agents backfilled with `hardware_access='all', subagents_enabled=TRUE`.

### Step 5: Telegram single-path ✅
- `EMPYRALIS_TELEGRAM_PATH`: **not set** in `.env` → defaults to `gateway`
- Gateway GramJS: active (path != "csm" → proceeds normally)
- CSM GramJS: disabled (path == "gateway" → throws `telegram_path_gateway` error)
- **No dual-path conflict.** Single listener confirmed.

### Step 6: Smoke tests ✅
```
test_ledger_audit.py .............  13 passed
test_hierarchy.py .................  33 passed
test_triage.py ....................  15 passed
                              ---
                        61 passed, 10 subtests passed in 2.71s
```
**Broader subsystem tests:** 26 passed, 29 pre-existing failures (all Rust kernel `McpRegistryRustGateError` / `RustKernelDecisionError`). Zero NEW failures.

### Step 7: Ledger sanity ✅
- 882 events in activity_ledger_events (3 event classes: `system_activity` 813, `sage_activity` 68, `blocked_action` 1)
- New event classes (`gateway_channel`, `gateway_hardware`, `fleet_control`, `triage`, `memory`): 0 rows (not yet exercised against live dev DB — tested via unit suite)
- No raw secrets, tokens, or message content found in ledger summaries — redaction verified via test suite

**Sample rows (last 5):**
```
event_class      | action                | actor_id                          | title
system_activity  | gateway_disconnected  | gateway:gateway_69ff24a9...        | Gateway disconnected
system_activity  | gateway_state_updated | gateway:gateway_69ff24a9...        | Gateway state updated
system_activity  | gateway_connected     | gateway:gateway_69ff24a9...        | Gateway connected
sage_activity    | final_response_sent   | transparency:d19c553f-...          | Response sent
sage_activity    | user_message_received | transparency:d19c553f-...          | Message received
```

## Phases landed (from git log)

| Phase | Commit | Description |
|-------|--------|-------------|
| A | `0820a732c` | Remove approval system — agent now acts on reasoning, not approval gates |
| A | `528730b5d` | Fix startup import errors from approval system removal |
| A | `11ad1628c` | Verify: platform alive post stage 0-1 (+fixes) |
| B | `7f594ec1f` | Stage 2: platform voice — pigeon theory enforced |
| C | `207cb351c` | Stage 3: HIGH import cycles broken via contract leaf modules |
| D | `1d5fa116c` | Harness: honest green board (skips + fakes, no prod gate changes) |
| E | `da3ccf041` | Stage 4a: workspace isolation — classification + dead-code fix |
| F | `25302bb5c` | Stage 4b: per-agent credentials + channel bindings + tool gating |
| F | `06d666bb7` | Stage 4b runtime: agent_id threaded, tool gating live, router multi-agent, 9 isolation tests green |
| F | `c5d7ae034` | Stage 4a real: instrument default-workspace, fix top-9 live sites |
| G | `cc308e21e` | Stage 4 live: router wired, agent_id threaded+instrumented, workers scoped, unscoped writes blocked |
| K | `38a531f7d` | Ledger: channel sends, file writes, shell — full audit trail with hard redaction |
| L+M+N | `876ec6e4c` | Phase M+N: merge readiness, telegram single-path (gateway default), fleet seed + sage operator bootstrap, stage 5 memory as agent-controlled tree |
| L+M+N | `2dd9b62bb` | Merge refactor-fable → main: Phase K–N |
| P | `6b66aac28` | Phase P: triage layers — scope + identity gates before reasoning, opt-in per agent, fully ledgered |

## Environment flags introduced

| Flag | Default | Purpose | Introduced |
|------|---------|---------|------------|
| `EMPYRALIS_TELEGRAM_PATH` | `gateway` | Telegram path: `gateway` \| `csm` \| `both` | Phase M |
| `EMPYRALIS_REQUIRE_AGENT_ID` | `false` | Block tool dispatch without agent_id when `true` | Phase K |
| `EMPYRALIS_REQUIRE_WORKSPACE` | `false` | Block unscoped tool dispatch when `true` | Phase F |
| `EMPYRALIS_RUNTIME_KERNEL_BIN` | (none) | Path to Rust kernel binary (required for governance decisions) | Phase F |

## Agent features (per-agent, opt-in)

| Feature | Config path | Default | Introduced |
|---------|-------------|---------|------------|
| Triage (scope + identity gates) | `install_metadata.triage.enabled` | `false` | Phase P |
| Sub-agents | `install_metadata.subagents_enabled` | `false` | Phase L |
| Role (operator/specialist) | `install_metadata.role` | `"specialist"` (Sage: `"operator"`) | Phase L |
| AI provider binding | `install_metadata.model_config.mode` | `platform_credits` | Phase L |

## AI provider binding modes

| Mode | Description | Status |
|------|-------------|--------|
| `platform_credits` | Uses workspace credit pool | Live |
| `byok_api` | Agent's own API key, no credit decrement | Live |
| `cli_subscription` | User's local CLI subscription | Not yet available |
| `local` | Local model, no cloud call | Not yet available |

## Memory system (Stage 5)

- MEMORY.md is the sole file guaranteed in turn context
- 3 agent-scoped tools: `memory_read`, `memory_write`, `memory_list`
- Path traversal defense: blocks `..`, absolute paths, `~` expansion
- All writes are ledgered (event_class: `memory`, action: `write`)

## Fleet tools (operator-only)

Live end-to-end from Sage's chat via direct tool calling. Each tool has a
`fleet__{action}` ToolDescriptor in the builtin catalog, dispatch in
`skills_service.execute_single_direct_tool_call`, and a SkillDefinition executor
in `skill_registry`. Role-gated: only the operator (Sage) can invoke them;
specialist calls are denied and escalated.

1. `fleet__create_agent` — Create a new specialist agent from fleet-specialist definition
2. `fleet__list_agents` — List all agents in workspace
3. `fleet__get_agent_activity` — Query agent's ledger events
4. `fleet__get_project_activity` — Query project's ledger events
5. `fleet__configure_agent` — Patch agent metadata (role, model_config, etc.)
6. `fleet__message_agent` — Enqueue message to agent's fleet_inbox

## Gateway ledger (Phase K)

- `gateway_channel` events: channel outbound dispatch (redacted: recipient hash, byte count)
- `gateway_hardware` events: tool invocations (redacted: capability_id, arg keys only)
- Legacy token instrumentation: counts + ledgers tokens with `agent_install_id` but no `agent_id`

## Test baseline

| Suite | Tests | Status |
|-------|-------|--------|
| `test_ledger_audit.py` | 13 | ✅ All pass |
| `test_hierarchy.py` | 33 | ✅ All pass |
| `test_triage.py` | 15 | ✅ All pass |
| **Total new** | **61** | **0 failures** |

### Pre-existing test failures
- 29 failures across `test_agent_channel_router.py` (20) and `test_tool_broker.py` (9) — all Rust kernel `McpRegistryRustGateError` / `RustKernelDecisionError`, not caused by fable merge

## Migration status (post Phase Q)

| Migration file | Applied to dev DB? | Notes |
|---------------|-------------------|-------|
| `add_agent_specialist_persistence.sql` | ✅ Yes | All tables exist |
| `add_workspace_inventory_items.sql` | ✅ Yes | Table exists |
| `stage_4b_agent_isolation.sql` | ✅ Yes (applied Phase Q) | 5 columns added, 31 Sage agents backfilled |
| `enable_rls.sql` | ❌ No | RLS functions not created — deferred (needs `app.current_tenant_id`/`app.current_workspace_id` session variable wiring in server.py middleware) |
| `vault_migration_stage4b.py` | N/A | Python module, not a DB migration |

## Rollback

- **Tag:** `fable-merge-1`
- **Rollback commit:** `e6f360a1f4e6d770a8ae7b20c8fbacf38c49245b`
- **Instructions:** `docs/ROLLBACK.md`

## Deferred

- Gateway cycle #6 (outbox hardening)
- `connectors_actions.py` connector action audit
- ~250 workspace sites risk-first audit
- Memory Stage 5 full rollout (prompt-tuning, frontend Memory pane)
- Fleet UI Stage 9 (wizard integration for role/model_config)
- `memory_summary_service` / `conversation_memory_policy` reshape
- Specialist agent runtime paths for triage
- Contact store for identity resolution
- RLS enforcement on all tables (`enable_rls.sql` not yet applied — needs session variable wiring in server.py middleware)
- Stage 4B: separate DB columns exist but code reads from `metadata` JSONB — schema-code inconsistency to resolve

## Open questions

1. Triage wiring to specialist agents — currently only runs on Sage turns
2. Scope-check LLM cost for byok_api agents — uses agent's own provider
3. Contact store — no contact store exists yet for richer identity resolution
4. Schema-code inconsistency: stage_4b_agent_isolation.sql columns exist but code reads same fields from `metadata` JSONB — which is source of truth?
