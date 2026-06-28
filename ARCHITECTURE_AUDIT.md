# Empyralis Architecture Audit — 2026-06-28

## 1. DIRECTORY TREE

```
empyralis/
├── server.py                          # FastAPI entry point (all routes mounted here)
├── mcp_server.py / mcp.json           # MCP server
├── docker-compose.yml / Dockerfile.*  # Container infra
├── pytest.ini / requirements*.txt
├── CHANNEL_AUDIT.md / CONCURRENCY_AUDIT.md / TOOL_AUDIT.md
├── test_suite_1_turn_pipeline.py ... test_suite_5_connectors.py
│
├── server_modules/                    # Python backend (~400+ .py files)
│   ├── auth.py                        # JWT, OAuth, CSRF, rate limits
│   ├── runtime_common.py              # API key auth, origin checks
│   ├── channel_adapter.py             # ChannelOrigin enum, filter_outbound_reply
│   ├── sage_turn_adapter.py           # execute_sage_turn() — unified ingress
│   ├── sage_reply_dispatcher.py       # dispatch_sage_reply() — typing/chunk/fallback
│   ├── sage_agent_runtime_service.py  # handle_sage_chat() — core agent loop
│   ├── command_registry.py            # 20 slash commands, directives, shortcuts
│   ├── sage_command_dispatcher.py     # Thin delegate → command_registry
│   │
│   ├── routes_sage_telegram_hosted.py # Telegram hosted: webhook + dev-poll
│   ├── routes_slack.py                # Slack inbound
│   ├── routes_wechat.py               # WeChat personal (Gateway bridge)
│   ├── routes_imessage.py             # iMessage personal (BlueBubbles bridge)
│   ├── routes_personal_channels.py    # WhatsApp/Telegram personal API
│   ├── routes_connectors.py           # Connector webhooks (Twilio,Telegram,Slack,Discord,GitHub)
│   ├── routes_gateway.py              # 151K — ACP protocol + Gateway WS
│   ├── routes_connections.py / routes_agents.py / routes_workspaces.py / ...
│   │
│   ├── connectors/
│   │   ├── discord_bot_runtime_service.py  # Discord DM listener + slash commands
│   │   ├── discord_connector.py            # Discord guild handler
│   │   └── autopilot_registry_facade_service.py
│   ├── connectors_actions.py          # Slack/Discord/GitHub/Telegram/WhatsApp webhooks
│   ├── agent_channel_router.py        # 2205L — gateway channel routing + HONEST STUB
│   ├── personal_channel_sage_bridge_service.py  # Personal channel → Sage bridge
│   ├── personal_channels_service.py   # Cloud + Gateway inbound handling
│   │
│   ├── skills_service.py              # ToolDescriptor registry (48 tools)
│   ├── skill_registry.py              # SkillDefinition + execution framework
│   ├── installed_skills.py            # Skill discovery, loading, security scan
│   ├── skill_scanner.py               # Python AST + JS/TS regex security scanner
│   │
│   ├── memory_service.py              # 2655L — all memory operations
│   ├── agent_memory.py                # SQLite key-value store + semantic search
│   ├── sage_memory_service.py         # 4-category structured memory (safe/sensitive/private/critical)
│   ├── workspace_context_memory_adapter.py  # RED stripping, context injection
│   ├── sage_instruction_compiler_service.py # System prompt builder
│   │
│   ├── gateway_protocol_service.py    # WebSocket protocol handler
│   ├── gateway_pairing_service.py     # Device registration/pairing
│   ├── gateway_browser_runtime.py     # Browser automation via Playwright
│   ├── gateway_execution_service.py   # Tool execution dispatch
│   ├── rust_runtime_kernel_client.py  # Rust governance kernel calls
│   │
│   ├── control_plane_repository.py    # DB access layer
│   ├── provider_profiles.py           # PROVIDER_MODEL_CATALOG (~3000 lines)
│   ├── direct_chat_service.py         # SSE streaming (contextvar event sink)
│   ├── direct_chat_generation_service.py  # LLM generation loop
│   │
│   ├── channel_sdk.py                 # Shared HTTP helper (consolidated from 5 copies)
│   ├── kill_switch_gate.py            # Emergency shutoff
│   └── tests/                         # ~50+ test files
│
├── frontend/                          # Next.js App Router
│   ├── app/
│   │   ├── layout.tsx / page.tsx      # Landing → redirect if authenticated
│   │   ├── login/ / signup/ / onboarding/ / preview/ / continue/
│   │   └── (account)/
│   │       └── w/[workspaceId]/       # 22 surface routes (sage,chat,memory,gateway,hardware,...)
│   ├── lib/
│   │   ├── auth/                      # Auth client, CSRF, session management
│   │   ├── workspace/                 # ~50 files — chat, panes, streams, deployed agents
│   │   │   ├── workstation-chat-pane.tsx / chat-composer.tsx / chat-message.tsx
│   │   │   ├── workstation-client.ts  # 3.5K+ lines core client
│   │   │   ├── workstation-gateway-operator-pane.tsx
│   │   │   ├── workstation-hardware-pane.tsx
│   │   │   ├── workstation-settings-pane.tsx
│   │   │   ├── codex-chat/            # Cell-based chat renderer
│   │   │   └── sage-chat/             # Sage chat hooks + composer
│   │   ├── ui/                        # Shared components, use-animated-text.ts
│   │   └── shell/                      # Account shell + membership
│   ├── proxy.ts                        # API proxy → backend
│   └── tests/e2e/                      # ~20 Playwright spec files
│
├── scripts/                           # ~100 build/ops/smoke-test scripts
│   ├── orion_local_worker*.py         # Local worker (7 modules)
│   ├── orion_terminal/                # TUI wizard (~20 .py files)
│   ├── run_discord_bot_runtime.py
│   ├── empyralis_self_hosted_command_worker.py
│   └── phase*.py / phase*.sh          # ~20 phase-gate test scripts
│
├── empyralis-gateway/                 # TypeScript Gateway (Node.js process on user machine)
│   └── src/                           # WebSocket client, capability router, channel runtimes
│
├── empyralis-runtime-kernel/          # Rust governance kernel
│   └── src/                           # gateway.rs, gateway_action.rs, gateway_frame.rs, ...
│
├── skills/                            # 9 bundled skills (browser, code-runner, file-manager, ...)
├── platform/codex-skills/             # 16 Codex platform skills (separate system)
└── docs/                              # Architecture docs
```

## 2. CHANNEL IMPLEMENTATIONS

### Fully Wired (13 paths → execute_sage_turn / handle_sage_chat)

| # | Channel | channel_origin | Entry Point | Connection | Call Site |
|---|---------|---------------|-------------|------------|-----------|
| 1 | Telegram Hosted | `telegram_hosted` | HTTP webhook + polling | Telegram Bot API | `routes_sage_telegram_hosted.py:168` → `dispatch_sage_reply_safe` |
| 2 | Slack | `slack` | HTTP webhook | Slack Events API (HMAC-SHA256 verified) | `routes_slack.py:111` → `execute_sage_turn` |
| 3 | WeChat Personal | `wechat_personal` | HTTP POST | Gateway local bridge | `routes_wechat.py:50` → `execute_sage_turn` |
| 4 | iMessage Personal | `imessage_personal` | HTTP POST | Gateway BlueBubbles bridge | `routes_imessage.py:50` → `execute_sage_turn` |
| 5 | Discord DM (Path A) | `discord_personal` | Discord Gateway WS | discord.py bot client | `discord_bot_runtime_service.py:412` → `execute_sage_turn` |
| 6 | Discord DM (Path B) | `discord_personal` | HTTP webhook | Discord Interactions (Ed25519 verified) | `connectors_actions.py:1356` → `execute_sage_turn` |
| 7 | Discord DM (Path C) | `discord_personal` | Discord Gateway WS | discord.py guild listener | `discord_connector.py:977` → `execute_sage_turn` |
| 8 | WhatsApp Personal | `whatsapp_personal` | Gateway WebSocket | Agent Computer (Baileys) | `personal_channel_sage_bridge_service.py:179` → `execute_sage_turn_for_channel` |
| 9 | Telegram Personal | `telegram_personal` | Gateway WS + Cloud | Agent Computer (GramJS) or Cloud Session Manager | `personal_channel_sage_bridge_service.py:179` → `execute_sage_turn_for_channel` |
| 10 | Web SSE | `web` | HTTP POST | Browser → SSE streaming | `direct_chat_service.py:347` → `execute_sage_turn` |
| 11 | Web REST | `web` | HTTP POST | Browser → REST | `sage_chat_api.py:225` → `handle_sage_chat` |
| 12 | Gateway ACP | `acp` | HTTP POST | Agent Computer Protocol | `routes_gateway.py:3484` → `handle_sage_chat` |
| 13 | WeChat/iMessage/Signal Personal | varies | Gateway WS | Local bridge on Agent Computer | `agent_channel_router.py` → handler registry → `execute_sage_turn_for_channel` |

### Stubbed (3 paths → agent_channel_router.route_inbound_channel_message)

| # | Channel | Webhook | Stub Location | Status |
|---|---------|---------|---------------|--------|
| 14 | Slack Guild | `/channels/slack/events` | `connectors_actions.py:1168` → `route_inbound_channel_message` | Returns `channel_unavailable` |
| 15 | Discord Guild | Discord Interactions webhook | `connectors_actions.py:1379` → `route_inbound_channel_message` | Returns `channel_unavailable` |
| 16 | GitHub | `/channels/github/webhook` | `connectors_actions.py:1508` → `route_inbound_channel_message` | Returns `channel_unavailable` |

### Deployed Agent Path (separate pipeline, not Sage)

| # | Channel | Pipeline |
|---|---------|----------|
| 17 | Telegram Connector | `connectors_actions.py:685` → autopilot `_dispatch_public_deployed_agent_envelope` |
| 18 | WhatsApp Business (Twilio) | `connectors_actions.py:627` → `handle_whatsapp_twilio_webhook` (autopilot) |

### No Handler

| # | Channel | Status |
|---|---------|--------|
| 19 | Signal Personal | Defined in ChannelOrigin enum, catalogued in gateway, but NO inbound HTTP route exists |

### Key Architectural Points

- **All wired channels converge on `execute_sage_turn()`** — confirmed 2026-06-27 unification.
- **The 3 stubbed channels share one bottleneck:** `agent_channel_router.route_inbound_channel_message()` at line 2160. Replace that stub with `execute_sage_turn()` and Slack Guild, Discord Guild, and GitHub all work.
- **Signal Personal has no inbound route.** The enum, catalog, and readiness checks all exist — just need `routes_signal.py` mirroring `routes_wechat.py`/`routes_imessage.py`.
- **Discord slash commands** are registered via PUT to `/applications/{id}/commands` with 20 commands.

## 3. MEMORY SYSTEM

### Memory Files

| File | Purpose | Location |
|------|---------|----------|
| SOUL.md | Agent personality, voice, core behaviors | `.orion-stack/workspace/SOUL.md` |
| IDENTITY.md | Agent identity, name, role | `.orion-stack/workspace/IDENTITY.md` |
| HEARTBEAT.md | Session continuity, ongoing tasks | `.orion-stack/workspace/HEARTBEAT.md` |
| MEMORY.md | Accumulated memory facts | `.orion-stack/workspace/MEMORY.md` |
| USER.md | User profile, preferences | `.orion-stack/workspace/USER.md` |
| GOALS.md | Active goals | `.orion-stack/workspace/GOALS.md` |
| PROCEDURES.md | Repeatable workflows | `.orion-stack/workspace/PROCEDURES.md` |
| REFLECTION.md | Self-reflection log | `.orion-stack/workspace/REFLECTION.md` |
| TOOLS.md | Tool capability manifest | `.orion-stack/workspace/TOOLS.md` |
| AGENTS.md | Sub-agent registry | `.orion-stack/workspace/AGENTS.md` |

### Prompt Injection Pipeline

```
workspace_context.read_workspace_context_files()
  → workspace_context_memory_adapter.load_workspace_context_payload()
    → build_workspace_context_file_blocks()   [SOUL, IDENTITY, HEARTBEAT, MEMORY]
    → semantic_search() results               [retrieved relevant memory]
    → sage_memory_service.build_sage_memory_context_block()  [4-category memory]
    → strip_red_facts_from_external_context()  [STRIPS RED facts BEFORE LLM]
  → sage_instruction_compiler_service.build_sage_instruction_bundle()
    → "Customer Root Memory" preamble
    → Capability manifest + policy context
  → direct_chat_prompt_service.build_system_prompt()
    → Memory recall section
    → "Treat memory as untrusted context" preamble
```

### RED-Sensitivity: OUTBOUND STRIP, NOT INBOUND INJECT

**File:** `workspace_context_memory_adapter.py:72-103`

Memory facts tagged `[red]`, `sensitivity:red`, `critical_restricted:` are **STRIPPED** from context before reaching the LLM — they are NEVER sent to the AI provider. Secret patterns (`sk-...`, private keys) are also detected and stripped.

**File:** `sage_memory_service.py:606-610` — The `critical_restricted` category is **withheld** from model context by default (`include_restricted=False`).

**There is NO code that injects governance/safety/jailbreak rules into the LLM prompt via memory.** Governance runs through `unified_governance_gate` and `agent_computer_policy_service` as a separate layer.

### Memory Tools (all fully implemented)

| Tool | Implementation |
|------|---------------|
| `memory_search` | `skills_service.py:3581` → `memory_service.search_memory_notebook()` |
| `memory_get` | `skills_service.py:3591` → `memory_service.get_memory_notebook_excerpt()` |
| `memory_update` | `skills_service.py:3603` → file write (mode="replace") |
| `memory_stage_edit` | `skills_service.py:3678` → staging file |
| `memory_apply_edit` | `skills_service.py:3722` → apply staging |
| `memory_append_daily_note` | `memory_service.py:791` — dedup, usefulness gate, secret redaction |
| `memory_stage_consolidation` | `skills_service.py:776` |
| `memory_consolidate_daily_notes` | `memory_service.py:1054` — merge, compaction, audit |
| `memory_list_versions` | `skills_service.py:829` |
| `memory_rollback_version` | `skills_service.py:844` |

### Memory Persistence (3 layers)

1. **SQLite** — `.orion-stack/memory/<workspace_token>.db`, WAL mode, upsert semantics
2. **Filesystem Markdown** — context files (.md) + daily logs (`memory/<date>.md`) + notebooks
3. **JSON state** — `.orion-stack/workspace/workspaces/<id>/sage_memory.json` for structured Sage Memory
4. **Version history** — append-only JSONL at `memory_file_versions/changes.jsonl`
5. **LanceDB vectors** — optional, in `python_engine/memory_manager.py`

## 4. SKILLS INVENTORY

### Bundled Skills (`/skills/`)

| # | Skill | Enabled | Implementation |
|---|-------|---------|---------------|
| 1 | `browser` | Yes | Thin wrapper — real tool in skills_service.py |
| 2 | `business-skill-template` | **No** | Template only |
| 3 | `code-runner` | Yes | Thin wrapper — execution tools |
| 4 | `file-manager` | Yes | Thin wrapper — file tools |
| 5 | `inventory-tool` | Yes | Backed by `server_modules/inventory_skill.py` |
| 6 | `memory-manager` | Yes | Thin wrapper — memory tools |
| 7 | `telegram-bot` | Yes | Thin wrapper — Telegram connector |
| 8 | `vision-monitor` | Yes | **Fully implemented** — 8 files (analyze.py, worker.py, query_handler.py, model_router.py, etc.) |
| 9 | `web-search` | Yes | Thin wrapper — web search tools |

**Note:** Most bundled skills are SKILL.md prompt wrappers around built-in tools in `skills_service.py`. Only `vision-monitor` and `inventory-tool` have custom Python handlers.

### Codex Platform Skills (`/platform/codex-skills/`)

16 additional skills in a separate system (codex-skill-* directories). Not part of the core Empyralis skill system.

### Skill Registration

1. `installed_skills.skill_roots()` walks 3 tiers: workspace (`.orion-stack/skills/`), global (`~/.orion-stack/skills/`), bundled (`skills/`)
2. Each directory: parse `SKILL.md` YAML frontmatter or `skill.json`
3. **Security scan** via `skill_scanner.scan_skill_dir()` — Python AST + JS/TS regex
4. Fails insecure skills
5. Checks OS/binary/Python availability
6. Applies per-workspace overrides
7. Builds prompt append block via `build_active_skill_prompt_append()`

### `/skills` Command

Calls `skills_service.list_installed_skills()` → shows sorted skill names. Skill execution (with args) returns "skill execution is not yet wired" — the registry works, the execution handler is a stub.

## 5. SECURITY LAYERS

### Implemented

| Layer | Location | Mechanism |
|-------|----------|-----------|
| JWT Bearer Auth | `auth.py:5581` | HMAC-SHA256 JWT, file-backed secret, auto-generated on first start |
| API Key (X-API-Key) | `runtime_common.py:345` | Constant-time compare vs `ORION_API_KEY` env |
| OAuth2 (Google/Apple) | `auth.py:464` | RS256 JWT verification against JWKS endpoints |
| CSRF Protection | `auth.py:314` | Double-submit cookie + header comparison |
| Rate Limiting | `auth.py:43-78` | Token-bucket: login 5/min, API 600/min, models 180/min |
| Body Size Limits | `server.py:80-101` | Default 2MB, webhooks 1MB, audio 25MB |
| Origin Checking | `runtime_common.py:171` | Mutations checked against `CONTROL_PLANE_ORIGINS` |
| Gateway Pairing Token | `gateway_pairing_service.py:65` | Single-use, TTL 15min, Rust kernel gate |
| Gateway Session Auth | `gateway_protocol_service.py` | Registration → token → session → WS |
| Cloud HMAC | `routes_personal_channels.py:726` | HMAC-SHA256 of canonicalized JSON |
| Rust Kernel Gates | `rust_runtime_kernel_client.py` | Every state mutation + gateway decision passes kernel |
| Channel Webhook Verification | Slack HMAC, Discord Ed25519, GitHub HMAC, Telegram secret token | All 4 verified |
| Workspace Access Control | `auth.py` + per-route `enforce_workspace_access` | Minimum role checks |

### Gaps

| # | Issue | Risk |
|---|-------|------|
| 1 | `CLOUD_SESSION_HMAC_SECRET` defaults to `"dev-secret-change-me"` | MEDIUM — forgeable in staging |
| 2 | Auth hot-cache TTL = 15s — revoked users retain access briefly | LOW |
| 3 | No Next.js `middleware.ts` — auth gating done in layout.tsx + proxy.ts | LOW-MEDIUM |
| 4 | Gateway fallback registration path has no rate limiting | MEDIUM |
| 5 | `_orion_auth_required()` can be disabled via env (blocked in production) | MEDIUM |

## 6. FRONTEND PANES

### Public Pages (no auth)
`/`, `/login`, `/signup`, `/auth/complete`, `/invite/[code]`, `/onboarding`, `/privacy`, `/terms`, `/preview`, `/continue`

### Workspace Surfaces (all under `/w/[workspaceId]/...`)

| Route | Component | Status |
|-------|-----------|--------|
| `/sage` | WorkstationChatPane | ✓ Built — full chat with streaming, timeline, composer |
| `/chat` | WorkstationChatPane (variant) | ✓ Built |
| `/memory` | WorkstationActivityPane | ✓ Built |
| `/activity` | WorkstationRunsPane | ✓ Built |
| `/approvals` | WorkstationApprovalsPane | ✓ Built |
| `/artifacts` | WorkstationArtifactsPane | ✓ Built |
| `/notifications` | WorkstationNotificationsPane | ✓ Built |
| `/tasks` | WorkstationSageWorkCenterPane | ✓ Built |
| `/integrations` | WorkstationSageConnectorsPane | ✓ Built |
| `/channels` | WorkstationSageConnectorsPane (variant) | ✓ Built |
| `/studio` | WorkstationDeployedAgentsPane | ✓ Built |
| `/inbox` | WorkstationDeployedAgentsPane (inbox) | ✓ Built |
| `/deploy` | WorkstationDeployedAgentsPane (deploy) | ✓ Built |
| `/studio-integrations` | WorkstationStudioIntegrationsPane | ✓ Built |
| `/gateway` | WorkstationGatewayOperatorPane | ✓ Built |
| `/gateway-activity` | WorkstationGatewayOperatorPane (activity) | ✓ Built |
| `/gateway-approvals` | WorkstationGatewayOperatorPane (approvals) | ✓ Built |
| `/hardware` | WorkstationHardwarePane | ✓ Built |
| `/marketplace` | DiscoveryPane | ✓ Built |
| `/applications` | HostedMiniAppsPane | ✓ Built |
| `/settings` | WorkstationSettingsPane | ✓ Built |
| `/settings/account` | Account settings | ✓ Built |

**All 22 workspace surfaces are built.** No placeholders. The framework (`WorkspaceSurfacePage` → `WORKSPACE_SURFACE_RENDERERS`) is mature.

### Missing
- **`/billing`** — `workstation-billing-pane.tsx` component EXISTS but is NOT wired to any page route. Credits/usage API exists on backend.

## 7. GATEWAY STATUS

### What It Is

The Empyralis Gateway (`empyralis-gateway/`) is a **TypeScript Node.js process** running on the user's machine. It connects to the backend via WebSocket and executes local capabilities.

### Connection Flow

```
User machine                          Empyralis Cloud
    │                                      │
    ├─ POST /gateway/registrations ───────→ (pairing token, single-use, 15min TTL)
    │← gateway_token ──────────────────────┤
    │                                      │
    ├─ POST /gateway/sessions ────────────→ (gateway_token + device info)
    │← ws_url + session_token ────────────┤
    │                                      │
    ├─ WS connect /api/gateway/ws?token= ─→ (session_token)
    │← JSON frames {kind, id, type, ...} ─→┤
    │                                      │
    ├─ gateway.heartbeat ────────────→ (every 20s)
    │← stale eviction if 4x interval ──────┤
```

### Implemented Capabilities

| Capability | Implementation |
|-----------|---------------|
| `shell.execute` | Via GatewaySupervisorClient (separate supervisor process, `http://127.0.0.1:7788`) |
| `file.*` (read/write/list) | Via supervisor |
| `browser.*` (navigate/click/type/screenshot) | Via GatewayBrowserRuntime + Playwright in Docker |
| `screenshot.capture` | Via supervisor |
| `computer.*` (click/type/clipboard/ocr/notify) | Via supervisor |
| `telegram_personal.*` | GramJS client — login, session store, message mapper |
| `whatsapp_personal.*` | Baileys client — QR login, session store, message mapper |
| `signal_personal.*` | Local bridge |
| `imessage_personal.*` | BlueBubbles local bridge |
| `wechat_personal.*` | Local bridge |
| `sage.chat` | Declared capability, routed to backend |

### Complete
- Core WS client with reconnection (exponential backoff 1s-30s)
- Registration/session/pairing flow
- Outbox for reliable delivery, journal/logging, checkpoints
- Heartbeat loop, stale detection
- Device identity management, token persistence
- All 5 personal channel runtimes
- Browser automation (Docker + Playwright, proven on real droplet)
- External agent proxy runtime
- Comprehensive test suite (~20 test files)
- Service inventory for health monitoring

### Partial
- **Windows support:** `platform_execution.py:497` — `raise RuntimeError("Screenshot capture is not implemented on Windows yet.")`
- Supervisor is a **separate process** at `EMPYRALIS_SUPERVISOR_URL` — if not running, shell/file/computer capabilities fail

## 8. KNOWN ISSUES

### Critical (blocking functionality)

| # | File | Issue |
|---|------|-------|
| 1 | `agent_channel_router.py:2157` | **Studio connector routing is a stub** — Slack Guild, Discord Guild, GitHub all return `channel_unavailable` |
| 2 | `command_registry.py` | **10 slash commands return "not yet wired"**: tasks, agents, skills (execution), config, mcp, plugins, debug, tts, bash, usage |
| 3 | `provider_profiles.py:1947-1967` | 3x `raise NotImplementedError` in provider profile methods |

### Medium (reduced functionality)

| # | File | Issue |
|---|------|-------|
| 4 | `connection_oauth_service.py:763` | Todoist OAuth "not yet wired" — hard 409 |
| 5 | `sage_agent_runtime_service.py:2160` | PDF text extraction "not yet supported" |
| 6 | `skill_registry.py` | Skill execution handler is a stub for most skills |
| 7 | `connection_verify_service.py:134,146` | Two connection types return 409 "not implemented" |
| 8 | `google_drive_api.py:186` | Local CLI mode "not yet implemented" for reads |
| 9 | `provider_profiles.py:2901` | Amazon Bedrock "not wired for Studio agents yet" |

### Low

| # | File | Issue |
|---|------|-------|
| 10 | `routes_gateway.py:3548` | ACP message types beyond health/turn/session return 501 |
| 11 | `virtual_computer_runtime.py:1437` | Virus scanner uses `"builtin_stub"` — always returns clean |
| 12 | `scripts/platform_execution.py:497` | Screenshot not implemented on Windows |
| 13 | `signal_personal` | Enum + catalog + readiness checks exist, but no inbound HTTP route |
| 14 | `workstation-billing-pane.tsx` | Component exists but not wired to any page route |

### Summary Stats
- `NotImplementedError`: 3 (all in `provider_profiles.py`)
- "not yet wired" commands: 10
- "not yet implemented" features: 8
- Stubs (non-test): 5
- TODOs/FIXMEs/HACKs: 0 explicit markers (codebase uses prose "not yet" instead)
