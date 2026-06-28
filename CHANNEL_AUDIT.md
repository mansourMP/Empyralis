# Empyralis Channel Layer — Full Architectural Audit
# 2026-06-28 | Read-only — no modifications performed

---

# PART 1 — OpenClaw Organization (reference model)

## Top-level structure

```
openclaw/
  packages/      21 shared packages (the "utils" layer)
  src/           62 core modules (agent, channels, gateway, sessions, tools, ...)
  extensions/    139 extensions (channels, AI providers, tools, media, speech, ...)
```

## packages/ — 21 shared packages (reusable, publishable)

| Package | Role |
|---------|------|
| `plugin-sdk` | THE extension contract — 533 files. Every channel imports from here |
| `agent-core` | Agent harness, session storage, run management |
| `llm-core` | LLM provider abstraction |
| `llm-runtime` | LLM runtime helpers |
| `gateway-client` | Gateway WebSocket client |
| `gateway-protocol` | Gateway message protocol |
| `markdown-core` | Markdown → channel format conversion |
| `media-core` | Media type handling |
| `media-generation-core` | Image/video/music generation |
| `media-understanding-common` | Media analysis |
| `memory-host-sdk` | Memory/vector-store interface |
| `model-catalog-core` | AI model catalog |
| `net-policy` | Network SSRF policy |
| `normalization-core` | String, number, timestamp coercion |
| `plugin-package-contract` | Plugin packaging spec |
| `sdk` | Public SDK entry point |
| `speech-core` | TTS/STT abstraction |
| `terminal-core` | Terminal/TUI rendering |
| `tool-call-repair` | Tool call fixup |
| `web-content-core` | Web fetch/parse |

## src/ — 62 core modules

Every module is domain-scoped: `channels/`, `sessions/`, `tools/`, `commands/`, `agents/`, `gateway/`, `security/`, `memory/`, `routing/`, `skills/`, `mcp/`, `provider-runtime/`, `config/`, `utils/`, `shared/`, etc.

## extensions/ — 139 extensions

Channels: `telegram/` (328 files), `discord/` (505), `whatsapp/` (236), `slack/` (256), `signal/` (71), `imessage/` (132)

AI providers: `anthropic/`, `openai/`, `deepseek/`, `ollama/`, `groq/`, `google/`, `xai/`, `mistral/`, etc.

Other: `browser/`, `codex/`, `mcp/`, `memory/`, `speech/`, etc.

---

## Deep dive — Telegram extension

**328 files** in `extensions/telegram/src/`. Key patterns:

### What it imports FROM packages/src (shared):
```
openclaw/plugin-sdk/channel-inbound        — normalize inbound events
openclaw/plugin-sdk/channel-feedback       — status reactions
openclaw/plugin-sdk/conversation-runtime   — session tracking
openclaw/plugin-sdk/media-runtime          — media loading
openclaw/plugin-sdk/fetch-runtime          — HTTP proxy/fetch
openclaw/plugin-sdk/runtime-config-snapshot — config
openclaw/plugin-sdk/session-store-runtime  — session persistence
openclaw/plugin-sdk/channel-activity-runtime — activity logging
openclaw/plugin-sdk/reply-dispatch-runtime — outbound reply dispatch
openclaw/plugin-sdk/agent-runtime          — agent resolution
openclaw/plugin-sdk/routing                — session routing
openclaw/plugin-sdk/markdown-table-runtime — formatting
openclaw/plugin-sdk/security-runtime       — allowlist, DM policy
openclaw/plugin-sdk/config-contracts       — type contracts
```

### What it imports FROM other extensions: ZERO

### How it calls the core agent:
The Telegram extension does NOT call the agent loop directly. It calls `runChannelInboundEvent()` from the core, which delegates to the reply pipeline, which calls `runReplyAgent()`.

## Deep dive — Discord extension

**505 files.** Same pattern: all shared imports come from `openclaw/plugin-sdk/*`. Zero cross-extension imports. Calls the core via `runChannelInboundEvent()`.

## Deep dive — Slack extension

**256 files.** Same pattern. Shared imports from `openclaw/plugin-sdk/*`. Zero cross-extension imports.

---

## The OpenClaw channel contract — what every channel implements

Every channel extension implements the same interface (from `plugin-sdk/channel-entry-contract.ts`):

```typescript
defineBundledChannelEntry({
  id: "telegram",           // unique channel ID
  name: "Telegram",         // display name
  description: "...",       // description
  plugin: { specifier: "./channel-plugin-api.js" },
  secrets: { specifier: "./secret-contract-api.js" },
  runtime: { specifier: "./runtime-setter-api.js" },
  accountInspect: { specifier: "./account-inspect-api.js" }
})
```

Every channel MUST provide these API surfaces. The core dispatches to them. Shared behavior (routing, sessions, fetch, policy, formatting) comes from `plugin-sdk`. Channel-specific behavior (transport, media parsing, keyboard rendering) lives in the extension.

---

# PART 2 — Where OpenClaw puts each utility type

| Utility type | OpenClaw location(s) | Copies? |
|-------------|---------------------|---------|
| **HTTP request helpers** | `plugin-sdk/fetch-runtime.ts` + `plugin-sdk/infra-runtime.ts` + `infra/fetch.ts` | ❌ SINGLE — one canonical version, imported by all extensions |
| **Message delivery** | `channels/message/inbound-reply-dispatch.ts` + `channels/direct-dm.ts` | ❌ SINGLE — one dispatch pipeline |
| **Inbound parsers** | `plugin-sdk/channel-inbound.ts` — shared normalize function | ❌ SINGLE — `buildChannelInboundEventContext()` used by all |
| **Auth/signature verification** | Per-channel (telegram, discord, slack each have their own) BUT each is in the extension, not copied across extensions | ⚠️ PER-CHANNEL — but isolated, not duplicated |
| **Timestamp utilities** | `packages/normalization-core/src/number-coercion.ts` | ❌ SINGLE |
| **Token/credential** | `plugin-sdk/runtime-secret-resolution.ts` + per-channel secret contract | ❌ SINGLE shared + per-channel contract |
| **Config schema** | `plugin-sdk/channel-config-schema.ts` — shared schema builder | ❌ SINGLE — `buildChannelConfigSchema()` used by all |
| **Allowlist/DM policy** | `plugin-sdk/security-runtime.ts` + `plugin-sdk/channel-policy.ts` | ❌ SINGLE |
| **Session routing** | `plugin-sdk/routing.ts` + `plugin-sdk/session-store-runtime.ts` | ❌ SINGLE |
| **Markdown rendering** | `plugin-sdk/markdown-table-runtime.ts` | ❌ SINGLE |
| **Media handling** | `plugin-sdk/media-runtime.ts` | ❌ SINGLE |

**The pattern:** OpenClaw has ONE copy of every shared utility. Channels import it. Channels NEVER copy it into their own code. Auth/signature is the ONE exception — each channel has its own verification logic because Discord OAuth ≠ Telegram webhook secret ≠ Slack HMAC-SHA256. But even there, the verification is in the extension, NOT copied across channels.

---

# PART 3 — Proposed mapping to Empyralis

## 3a — Where each Empyralis duplicate group should live

| Duplicate group | Copies | OpenClaw pattern | Proposed Empyralis home | Notes |
|-----------------|--------|-----------------|------------------------|-------|
| `_http_json_request` | 10 | `plugin-sdk/fetch-runtime.ts` | `server_modules/channel_transport.py` OR new `server_modules/http_utils.py` | Trivial to consolidate — same function, different files |
| `_env_first` | 6 | `plugin-sdk/runtime-env.ts` | `server_modules/env_utils.py` | 2-line helper, pure consolidation |
| `_coerce_dict` | 7 | `packages/normalization-core` | `server_modules/data_utils.py` | Already likely has a home in the codebase |
| `_utc_now_iso` | 5 | `packages/normalization-core` | `server_modules/time_utils.py` | Trivial |
| `_token` / `_bot_token` | 10 | `plugin-sdk/runtime-secret-resolution.ts` | `server_modules/credential_resolver.py` | Should read from env + config, not scattered |
| `upload_file` / `download_file` | 10 | `plugin-sdk/media-runtime.ts` | `server_modules/media_utils.py` | Consolidate |
| `parse_inbound_event` | 6 | `plugin-sdk/channel-inbound.ts` | `server_modules/channel_adapter.py` (already exists!) | Need to check — does each channel's version differ? |
| `build_run_goal_from_event` | 6 | Per-channel but standardized | `server_modules/channel_turn_request_service.py` (already exists) | Can consolidate |
| `should_trigger_agent_run` | 6 | `plugin-sdk/channel-ingress.ts` | `server_modules/channel_ingress_guard.py` | Consolidate |
| `event_matches_connector` | 6 | Per-channel, legitimately different | Keep per-channel | Each connector has different event matching |
| `send_message` | 5 | `channels/message/inbound-reply-dispatch.ts` | `server_modules/outbound_dispatch.py` | ONE delivery dispatcher |
| `verify_request_signature` | 4 | Per-channel, legitimately different | Keep per-channel but share HMAC helper | Telegram ≠ Slack ≠ Discord verification |
| `send_dm` | 4 | `channels/direct-dm.ts` | `server_modules/direct_message_service.py` | Consolidate |
| `search` / `list_issues` / `create_issue` | 4 each | Not applicable (connector-specific) | Keep per-connector | These are connector actions, not transport |
| `list_channels` | 4 | `plugin-sdk/directory-runtime.ts` | `server_modules/channel_registry.py` | ONE channel registry |
| `send_whatsapp_personal_message` (3) / `send_telegram_personal_message` (3) / `send_local_bridge_personal_message` (3) | 9 | Thin per-channel wrappers | Keep per-channel but share base | Legitimate thin adapters |
| `_route_inbound_channel_message` (stub) | 4 | Already unified via `execute_sage_turn()` | Already clean | This is the honest stub that's now bypassed |

## 3b — Duplicates that should NOT be consolidated

| Group | Reason |
|-------|--------|
| `verify_request_signature` | Each service has a fundamentally different auth mechanism: Telegram secret token, Slack HMAC-SHA256, Discord Ed25519, WhatsApp webhook verification. The HMAC helper can be shared; the verification function cannot. |
| `telegram_webhook` | Only Telegram has webhooks. Duplicates across telegram files (3 copies) should be consolidated within the Telegram connector, not across channels. |
| `event_matches_connector` | Each connector matches different event shapes (GitHub webhook ≠ Linear webhook ≠ Discord interaction). The matching logic is inherently connector-specific. |
| `search` / `list_issues` / `create_issue` | These are connector actions (GitHub, Linear, Notion), not channel transport. Each connector has its own API. |
| `send_telegram_personal_message` / `send_whatsapp_personal_message` / `send_local_bridge_personal_message` | These are legitimate thin transport adapters — each calls a different gateway/API. They share a signature but call different backends. |

## 3c — Implementation plan (ordered by safety)

### Phase A — TRIVIAL (zero risk, pure code movement)
1. Create `server_modules/http_utils.py` — move all `_http_json_request` (10 copies), `_request_json` (4 copies)
2. Create `server_modules/time_utils.py` — move all `_utc_now_iso` (5 copies), `_coerce_epoch` (4 copies)
3. Create `server_modules/env_utils.py` — move all `_env_first` (6 copies)
4. Create `server_modules/data_utils.py` — move all `_coerce_dict` (7 copies), `_text` (3 copies)
5. Create `server_modules/credential_utils.py` — move all `_token`, `_bot_token` (10 copies combined), `_headers` (4 copies)

### Phase B — LOW RISK (consolidate behavior, keep interfaces)
6. Consolidate `send_message` (5 copies) into `server_modules/outbound_delivery.py` — one dispatcher that routes to channel-specific send functions
7. Consolidate `send_dm` (4 copies) into `server_modules/direct_message_service.py`
8. Consolidate `upload_file` / `download_file` (10 copies) into `server_modules/media_transfer.py`
9. Consolidate `parse_inbound_event` (6 copies) — verify each channel's version is truly different or can share a base
10. Consolidate `build_run_goal_from_event` (6 copies) — standardize as `channel_turn_request_service.build_run_goal()`

### Phase C — MEDIUM RISK (may change behavior, test each)
11. Consolidate `should_trigger_agent_run` (6 copies) into `server_modules/channel_ingress_guard.py`
12. Delete `scripts/telegram_mcp_server.py` — channel-scoped MCP, wrong architecture
13. Consolidate `list_channels` (4 copies) into `server_modules/channel_registry.py`
14. Verify `personal_channel_thread_command_service.py` — is it still handling commands after the registry unification?

### Phase D — VERIFY (no changes needed, already correct)
15. `agent_channel_router.py` (2205 lines) — pure router, clean, zero LLM hits
16. `connectors_core.py` (812 lines) — connector dispatch, thin delegates
17. All route handlers (`routes_slack.py`, `routes_wechat.py`, `routes_imessage.py`) — already thin, already converging on `execute_sage_turn()`

---

# APPENDIX A — Prior fixes (2026-06-27/28)

- ✅ Single entry point — web and channels converge on `execute_sage_turn()`
- ✅ Single command registry — `command_registry.py`
- ✅ Single provider resolver — `_resolve_cloud_provider()`
- ✅ Deleted regex tool shortcut — `explicit_provider_parity_tools`
- ✅ `/model` persists to workspace metadata
- ✅ Durable runs gated on explicit mode
- ✅ Streaming + thinking trace + word animation — web UI

# APPENDIX B — Channel convergence (verified)

Every channel converges on `execute_sage_turn()`. Zero exceptions. The 2026-06-27 unification already closed the web gap. No channel implements its own agent loop. No channel calls the LLM directly.

# APPENDIX C — What OpenClaw does that Empyralis should copy

1. **One shared SDK for all extensions** — OpenClaw has `plugin-sdk` (533 files). Empyralis scatters utilities across connector files. Solution: `server_modules/channel_sdk/` with the consolidated utilities from Phase A+B.

2. **Zero cross-extension imports** — OpenClaw extensions never import from each other. Empyralis connectors share utilities through a central location, not by importing `telegram_ingress_service` into `discord_bot_runtime_service`.

3. **Channel contract** — OpenClaw's `defineBundledChannelEntry()` enforces what every channel must provide. Empyralis channels all call `execute_sage_turn()` but the interface is implicit. Making it explicit (`execute_sage_turn()` IS the contract) is already done.

4. **Auth per channel, shared helpers** — OpenClaw keeps signature verification in each extension (because Telegram secret token ≠ Slack HMAC) but shares the HTTP/routing/session layer. Empyralis should do the same: keep `verify_request_signature` per-channel, share `_http_json_request`.
