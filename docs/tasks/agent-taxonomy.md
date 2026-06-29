# Agent Taxonomy — Native & External Agent Architecture

**Created:** 2026-06-30
**Source:** Code audit + graphify knowledge graph (27,358 nodes, 72,124 edges)
**Status:** reference doc

---

## 1. The Agent Manifest (the definition format)

Every agent in Empyralis is defined by an `AgentManifest` — a structured JSON/YAML document at `agent_manifest.py`. This is the "agent birth certificate."

```
AgentManifest
├── manifest_id          — unique slug
├── scope                — "global_master" | "specialist"
├── engine               — always "universal_operator" (the only runtime)
├── identity
│   ├── name, role, archetype  — "support_specialist" | "task_automator" | "intelligence_researcher" | "master_os"
│   ├── summary
│   ├── owner_mode_enabled     — can the workspace owner talk to this agent?
│   └── customer_mode_enabled  — can external customers talk to it?
├── role                 — job_to_be_done, success_definition, escalation_owner
├── voice                — tone, response_style, service_boundaries
├── bible                — mission, hard_context, operational_policy, core_responsibilities, guardrails, escalation_triggers
├── skills[]             — bound skill IDs (email-access, web-search, browser, etc.)
├── connectors           — requested vs bound connectors
├── channels             — web_chat, email, phone, whatsapp, telegram (bool flags)
├── runtime              — mode: "hosted_secure" | "local_secure" | "privileged_device"
├── policy               — reflection_enabled, approval_mode: "system" | "guarded" | "strict"
└── blueprint            — source: "system" | "forge" | "imported_blueprint"
```

**`agent_manifest.py`** — the single source of truth for this schema.

---

## 2. Agent Taxonomy — 5 kinds

### Kind 1: Sage (Global Master)

| Attribute | Value |
|-----------|-------|
| **Scope** | `global_master` |
| **Archetype** | `master_os` |
| **Engine** | `universal_operator` |
| **Manifest** | `SAGE_GLOBAL_MANIFEST` (hardcoded in `agent_manifest.py:126`) |
| **What it does** | The reach-in assistant. Users talk to Sage via channels (Telegram, Discord, web chat). Sage plans, delegates, approves, supervises. It is the "omniscient operator surface." |
| **Where it runs** | Cloud (hosted_secure) by default. Can route work to specialists. |
| **Key files** | `sage_agent_runtime_service.py` (2287+ lines), `sage_command_dispatcher.py`, `sage_reply_dispatcher.py` |
| **How invoked** | Any inbound message that's not `/studio` or a specialist-bound channel → Sage handles it |

### Kind 2: Studio / Specialist Agents

| Attribute | Value |
|-----------|-------|
| **Scope** | `specialist` |
| **Archetype** | `support_specialist`, `task_automator`, or `intelligence_researcher` |
| **Engine** | `universal_operator` |
| **What they do** | Autonomous, long-running agents with a specific job. Unlike Sage (reactive, reach-in), specialists are proactive — they wake up on schedules, monitor systems, run workflows. |
| **Where they run** | Depends on `runtime.mode`: hosted_secure (cloud), local_secure (Gateway), or privileged_device (user's Mac) |
| **Key files** | `agent_registry_api.py` (100KB), `agent_registry_repository.py` (104KB), `agent_specialist_repository.py` (59KB) |
| **Frontend** | `workstation-deployed-agents-pane.tsx`, `deployed-agents/wizard.tsx`, `deployed-agents/detail-view.tsx` |

**Specialist lifecycle:**
1. Created via Forge (the agent builder) or imported from a blueprint
2. Installed into a workspace → gets a `WorkspaceAgentInstallModel` row
3. Bound to a runtime profile (cloud sandbox, Gateway, or desktop companion)
4. Runs on schedule or on trigger, reports to Sage

### Kind 3: Deployed Agents (installed specialists)

A "deployed agent" = a specialist that has been installed into a specific workspace with a specific runtime binding.

| Attribute | Value |
|-----------|-------|
| **Storage** | `WorkspaceAgentInstallModel` (`agent_registry_models.py:93`) |
| **Key fields** | `agent_kind` (default "specialist"), `agent_definition_id`, `runtime_profile_id`, `master_agent_install_id` |
| **Runtime profiles** | `RuntimeProfileModel` (`agent_registry_models.py:20`) — defines where the agent executes |
| **Master binding** | Each specialist has a `master_agent_install_id` linking it to Sage |

**Files involved in deployed agent execution:**
- `deployed_agent_virtual_runtime_service.py` — session binding
- `deployed_agent_test_turn_service.py` — test turns
- `deployed_agent_transparency_service.py` — observability events
- `deployed_agent_memory_service.py` — per-agent memory

### Kind 4: External Agents (connected, not native)

| Attribute | Value |
|-----------|-------|
| **What they are** | Third-party agents running OUTSIDE Empyralis (OpenClaw, Hermes, NemoClaw, or custom HTTP). They expose a manifest at a URL and Empyralis proxies chat/tools/events through that endpoint. |
| **Protocol** | `custom_http`, `a2a`, `mcp`, `openclaw`, `hermes`, or `nemoclaw` |
| **Key file** | `connected_external_agent_service.py` |
| **Surface kinds** | `connected_external_agent`, `native_studio_agent`, `agent_computer`, `agent_group_reserved` |

**How external agents connect:**
1. External agent exposes a manifest at a URL (e.g. `https://my-agent.example.com/manifest.json`)
2. Workspace owner registers it via the Deployed Agents UI → `POST /api/agents/external`
3. Empyralis validates the manifest, stores connection config
4. Chat messages route through `connected_external_agent_service.py` → `POST {chat_url}` with normalized message format
5. The external agent returns replies, tool calls, events — Empyralis renders them in the Studio pane

**External agent manifest schema:** `studio.external_agent.v1`
**Allowed provider kinds:** `openclaw`, `hermes`, `nemoclaw`, `custom`
**Allowed capabilities (20):** chat, actions, activity, artifacts, channels, devices, events, health, knowledge, logs, mcp, memory, nodes, sub_agents, skills, tools, voice_channels, workflows

**Key external agent files:**
| File | Role |
|------|------|
| `connected_external_agent_service.py` | Registration, validation, chat proxy, section fetching |
| `routes_connections.py` | REST endpoints for external agent CRUD |
| `workstation-deployed-agents-pane.tsx` | UI for managing external agents |
| `deployed-agents/external-agent-detail.tsx` | External agent detail view |
| `deployed-agents/external-agent-provider-badges.ts` | Provider badge rendering (OpenClaw, Hermes, NemoClaw logos) |
| `deployed-agents/wizard.tsx` | Agent creation wizard with external agent flow |

### Kind 5: Agent Computer (hardware bridge)

| Attribute | Value |
|-----------|-------|
| **Surface kind** | `agent_computer` |
| **What it is** | A Gateway-connected machine (Mac, Windows, Linux) that exposes local capabilities to Sage or specialists. Not an agent itself — it's the "hands" an agent uses. |
| **Key file** | `agent_computer_policy_service.py` (33KB), `agent_computer_surface_service.py` |

---

## 3. The Turn Engine — how any agent processes a message

```
Channel inbound
    │
    ▼
agent_turn.py:agent_turn()
    │  Normalizes channel → AgentTurnRequest
    │  Resolves session, thread, policy context
    ▼
turn_runtime.py:execute_agent_turn_request()
    │  Routes by surface kind:
    │
    ├── surface = "web" / "telegram_hosted" / "discord"
    │   └── Sage handles it (sage_agent_runtime_service.handle_sage_chat)
    │
    ├── surface = "native_studio_agent"
    │   └── Deployed specialist agent handles it
    │       └── agent_registry_api → resolve specialist install → execute
    │
    ├── surface = "connected_external_agent"
    │   └── Proxy to external agent's chat_url
    │       └── connected_external_agent_service.send_chat_message()
    │
    └── surface = "agent_computer"
        └── Gateway-executed action (shell, browser, file, etc.)
```

**Key types:**
- `AgentTurnRequest` (`agent_turn.py:119`) — tenant_id, workspace_id, thread_id, channel, actor, message, execution_mode, response_mode, policy_context
- `AgentTurnResponse` (`agent_turn.py:136`) — status, reply, run_id, artifacts, approvals, interventions, metadata
- `ExecutionMode` — "sync" | "async"
- `ResponseMode` — "stream" | "full"

---

## 4. Routing: how messages find their agent

**`agent_channel_router.py`** (95KB) — the central routing table.

```
Inbound message
    │
    ▼
agent_channel_router
    │
    ├── Sage (direct) — Telegram hosted, Discord DM, web chat
    │   └── Personal channels: iMessage, Signal, WeChat (via Gateway bridge)
    │
    ├── Studio specialist — scheduled/invoked via `/studio` or direct specialist channel
    │
    ├── External agent — proxied to third-party endpoint
    │
    └── Agent Computer — Gateway-local execution
```

---

## 5. All relevant files (graphify-verified)

### Agent definitions & models
| File | Size | Role |
|------|------|------|
| `agent_manifest.py` | 6.4KB | AgentManifest schema + `SAGE_GLOBAL_MANIFEST` constant |
| `agent_registry_models.py` | 32KB | SQLAlchemy models for agents, installs, events |
| `agent_registry_repository.py` | 104KB | Database operations for agent CRUD |
| `agent_registry_api.py` | 100KB | REST API for agent management |
| `agent_specialist_repository.py` | 59KB | Specialist-specific storage and channel binding |

### Sage (master agent)
| File | Size | Role |
|------|------|------|
| `sage_agent_runtime_service.py` | ~80KB+ | Main Sage execution loop |
| `sage_command_dispatcher.py` | ~15KB | Slash command handling for Sage |
| `sage_reply_dispatcher.py` | ~10KB | Reply formatting and fallback |
| `sage_transparency_service.py` | ~15KB | Sage observability events |
| `universal_operator.py` | ~30KB | The "engine" that runs all native agents |

### Specialist / deployed agents
| File | Size | Role |
|------|------|------|
| `deployed_agent_virtual_runtime_service.py` | ~10KB | Session-to-runtime binding |
| `deployed_agent_test_turn_service.py` | ~5KB | Test turns for deployed agents |
| `deployed_agent_transparency_service.py` | ~3KB | Deployed agent events |
| `deployed_agent_memory_service.py` | ~6KB | Per-agent memory |
| `specialist_service.py` | ~10KB | Specialist resolution and invocation |

### External agents
| File | Size | Role |
|------|------|------|
| `connected_external_agent_service.py` | ~20KB | External agent registration, validation, chat proxy |
| `routes_connections.py` | ~10KB | REST endpoints for external agent CRUD |
| `external_agent_provider_badges.ts` | ~2KB | Provider badge rendering |

### Routing & turn engine
| File | Size | Role |
|------|------|------|
| `agent_turn.py` | 74KB | Turn request/response types + main entry point |
| `agent_channel_router.py` | 95KB | Channel-to-agent routing |
| `turn_runtime.py` | ~20KB | Turn execution orchestration |
| `agent_policy_context.py` | 8KB | Policy normalization for turns |

### Agent computer (hardware)
| File | Size | Role |
|------|------|------|
| `agent_computer_policy_service.py` | 33KB | Hardware policy enforcement |
| `agent_computer_surface_service.py` | 3KB | Agent computer surface management |
| `agent_computer_approval_decision_service.py` | 13KB | Hardware action approvals |
| `agent_computer_profile_service.py` | 16KB | Hardware profile management |

### Frontend
| File | Role |
|------|------|
| `workstation-deployed-agents-pane.tsx` | Main deployed agents management UI |
| `deployed-agents/wizard.tsx` | Agent creation wizard (native + external) |
| `deployed-agents/detail-view.tsx` | Agent detail view |
| `deployed-agents/external-agent-detail.tsx` | External agent detail (manifest, health, chat) |
| `deployed-agents/external-agent-provider-badges.ts` | Provider badge lookup |
| `deployed-agents/ai-settings.tsx` | AI provider settings per agent |
| `deployed-agents/agent-computer-detail.tsx` | Hardware agent detail |
| `deployed-agents/roster-sidebar.tsx` | Agent roster sidebar |
| `deployed-agents/inbox-view.tsx` | Agent activity inbox |
| `deployed-agents/components.tsx` | Shared agent UI components |
| `deployed-agents/constants.ts` | Studio templates, runtime options, context presets |
| `deployed-agents/types.ts` | TypeScript types for agents |
| `deployed-agents/utils.ts` | Agent utility functions |

---

## 6. What works vs what's wired

### Proven (F1 audit verified)
- **Sage** via Telegram hosted bot — works
- **Sage** via Discord DM — works
- **Sage** via web chat — works (two dispatch paths, needs collapse)
- **Sage** with VPS shell/file execution — works

### Wired but unverified
- **Studio specialists** — code exists (100KB+ of API + repository), frontend exists, test turns work. Not verified in production.
- **External agents** — full registration/validation/chat-proxy pipeline wired. Provider badges exist for OpenClaw/Hermes/NemoClaw. Not verified with real external agents.
- **Agent Computer** — policy service, approval service, surface service all wired. Gateway integration exists. Not end-to-end verified.

### Not wired
- **Agent-to-agent handoff** — Sage can theoretically route to specialists, but the handoff protocol isn't built
- **Cross-agent memory** — each agent has its own memory; no shared memory bus
- **Multi-agent workflows** — `routes_workflows.py` exists but workflows are limited to single-agent runs

---

## 7. Architectural observations

1. **"Universal Operator" is the only engine.** All native agents (Sage + specialists) run on the same `universal_operator.py` engine. The difference is the manifest, not the runtime.

2. **Sage is hardcoded.** `SAGE_GLOBAL_MANIFEST` is a Python constant at `agent_manifest.py:126`, not loaded from a file or DB. Specialists are dynamic (DB-backed). This means Sage can't be customized per-workspace without code changes.

3. **External agents are proxied, not hosted.** Empyralis doesn't run external agents — it proxies messages to their HTTP endpoint. The external agent runs on its own infrastructure.

4. **Specialists require a runtime profile.** Every specialist must be bound to a `RuntimeProfileModel` — cloud sandbox, Gateway worker, or desktop companion. Without a profile, it can't execute.

5. **Agent transparency events** — a parallel event system (`agent_transparency_events.py`, `sage_transparency_service.py`, `deployed_agent_transparency_service.py`, `gateway_transparency_service.py`) emits structured events for every agent action. These are separate from PlatformEvents — transparency events are for audit/observability, PlatformEvents are for user-facing notifications.

6. **Three surface kinds, four dispatch paths.**
   - `native_studio_agent` → specialist execution
   - `connected_external_agent` → HTTP proxy
   - `agent_computer` → Gateway execution
   - Sage (no surface kind — it's the default fallback for all unmapped channels)
