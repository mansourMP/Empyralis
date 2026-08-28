# OpenClaw Architecture — Business Model Reference

> **Empyralis has zero shared code with OpenClaw.** This document studies OpenClaw's architecture
> solely as a business model reference — it validated market demand for AI agents in messaging channels.
> Empyralis is a completely different stack: Python FastAPI + Next.js 16 + Rust supervisor vs OpenClaw's TypeScript/Node.js.

Source: /opt/homebrew/lib/node_modules/openclaw/ (npm install v2026.5.27)
GitHub: https://github.com/openclaw/openclaw
License: MIT
Runtime: Node.js ≥22.19

> **Terminology note (2026-07-23):** "Sage" is legacy Empyralis product terminology — the founder ruled the concept removed from the product; the platform has only agents (owner-facing, customer-facing serving the owner, and AskAI). Everywhere below this doc says "Sage" (in Empyralis-side comparisons — OpenClaw's own naming is unaffected), read: the owner-facing agent. `SageLauncher.tsx` and other `sage_*`/`Sage*` code identifiers are legacy code artifacts only, not a live product concept. This is a lighter-touch terminology note, not a full rewrite — the body below is unchanged and may still use "Sage" throughout.

---
STEP 8 — SESSION / THREAD MANAGEMENT

Session Key format: agent:<agentId>:<sessionKey> or unscoped sessionKey

Session resolution: session-D_iBBJQp.js:173 resolveSession(opts) calls resolveSessionKeyForRequest(opts) which:
1. Loads session store from disk (JSON file)
2. Resolves sessionKey from channel context (channel:accountId:chatId:threadId)
3. Checks reset policy (via resolveSessionResetPolicy)
4. Returns { sessionKey, sessionStore, sessionEntry }

Session store is a flat JSON file at ~/.openclaw/sessions/<agentId>/store.json.

Sessions are NOT cross-channel — each channel has its own session key derivation. But sessions CAN be spawned across agents via sessions_spawn tool.

---
FULL PIPELINE (ASCII)

┌──────────────────────┐
│  CHANNEL ADAPTER     │  bot-Cg3cHBMq.js:6854 (Telegram processMessage)
│  - Telegram Bot API  │  provider-CiI2mFp8.js (Discord, etc.)
│  - Discord Gateway   │  Each channel has own extension in
│  - Signal, iMessage, │  extensions/<channel>/ directory
│  - Slack, Mattermost │
│  - IRC, Webchat,     │
│    Tlon/Urbit        │
└──────┬───────────────┘
       │ Normalized ctxPayload with:
       │   From, To, RawBody, SessionKey,
       │   ChatType, InboundEventKind, MediaType
       │
       ▼
┌──────────────────────┐
│  INGRESS AUTH        │  bot-Cg3cHBMq.js:310-368
│  createChannelIngress│  (extensions/telegram/src/ingress.ts)
│  Resolver.event()    │  Checks allowlist, DM policy, group policy
│  Resolver.command()  │  Distinguishes "event" from "command" ingress
└──────┬───────────────┘
       │ Authorized ingress result with sessionKey
       │
       ▼
┌──────────────────────┐
│  MESSAGE CONTEXT     │  bot-Cg3cHBMq.js:6860
│  buildTelegramMessage│  Resolves chat context, thread binding,
│  Context()           │  history, allowlists, group config
└──────┬───────────────┘
       │ ctxPayload with resolved SessionKey
       │
       ▼
┌──────────────────────┐
│  DISPATCH            │  bot-Cg3cHBMq.js:5714
│  dispatchTelegram    │  Streaming draft lanes (answer, reasoning)
│  Message()           │  Tool progress, reply fencing, quote handling
└──────┬───────────────┘
       │ Calls runChannelInboundEvent()
       │
       ▼
┌──────────────────────┐
│  INBOUND EVENT       │  inbound-reply-dispatch-Cyw7nhJQ.js
│  runChannelInbound   │  Wraps channel event into followup run
│  Event()             │  Enqueues into reply pipeline
└──────┬───────────────┘
       │
       ▼
┌──────────────────────┐
│  REPLY PIPELINE      │  reply-pipeline-C3CPjsQr.js
│  createChannelReply  │  Queue/debounce → followup-runner
│  Pipeline()          │  Resolves session, model, tools, skills
└──────┬───────────────┘
       │ followupRun with: prompt, sessionKey, defaultModel
       │
       ▼
┌──────────────────────┐
│  AGENT RUNNER        │  agent-runner.runtime-CQGHXG44.js:2861
│  runReplyAgent()     │  Heartbeat check → queue mode → steering
│                      │  → preflight compaction → memory flush
│                      │  → tool selection → LLM call
└──────┬───────────────┘
       │ Resolves model via resolveDefaultModelForAgent()
       │ model-selection-DF4MbMUd.js:91
       │
       ▼
┌──────────────────────┐
│  PROVIDER CLIENT     │  provider-CiI2mFp8.js / extensions/<ai>/
│  (Anthropic, OpenAI, │  Transport policy, streaming, tool-use
│  DeepSeek, Ollama,   │  Each provider is an extension plugin
│  Groq, OpenRouter)   │  API call via provider contract
└──────┬───────────────┘
       │ LLM response (tool calls or text)
       │
       ▼
┌──────────────────────┐
│  TOOL EXECUTION      │  run-attempt-CnazfKzC.js
│  Embedded Pi / Codex │  Tool call → sandboxed execution
│  App Server          │  Code execution, file ops, web, etc.
└──────┬───────────────┘
       │ Tool results fed back to LLM (agentic loop)
       │ Loop: tool calls → execute → feed back → more tools
       │
       ▼
┌──────────────────────┐
│  REPLY DELIVERY      │  delivery.runtime.js / reply-dispatch
│  dispatchReplyWith   │  Text rendering (Markdown → Telegram HTML)
│  Dispatcher()        │  Chunking, streaming, reply threading
└──────┬───────────────┘
       │ Outbound message through channel adapter
       │ bot.api.sendMessage() / discord message / etc.
       │
       ▼
┌──────────────────────┐
│  CHANNEL RESPONSE    │  Each channel plugin's send methods
│  (Telegram, Discord, │  Telegram: sendMessageTelegram()
│   etc.)              │  Discord: message.send() / interaction.reply()
└──────────────────────┘

---
STEP 2 — MESSAGE ENTRY

Telegram

File: bot-Cg3cHBMq.js:6854 (in dist/)
Source: extensions/telegram/src/bot-message.ts

The function createTelegramMessageProcessor() creates the main message handler. Every Telegram update flows through:

// bot-Cg3cHBMq.js:6845 (simplified)
const createTelegramMessageProcessor = (deps) => {
  return async (primaryCtx, allMedia, storeAllowFrom, options,
                  replyMedia, replyChain, promptContext, lifecycle) => {
    // STEP 1: Build normalized message context
    const context = await buildTelegramMessageContext({
      primaryCtx, allMedia, replyMedia, replyChain,
      promptContext, storeAllowFrom, options,
      bot, cfg, account, historyLimit, groupHistories,
      dmPolicy, allowFrom, groupAllowFrom, ...
    });
    if (!context) return false; // dropped

    // STEP 2: Log and send typing indicator
    telegramInboundLog.info(formatTelegramInboundLogLine({...}));

    // STEP 3: Dispatch
    await dispatchTelegramMessage({
      context, bot, cfg, runtime, replyToMode,
      streamMode, textLimit, telegramCfg, telegramDeps, opts
    });
  };
};

Normalization: buildTelegramMessageContext() (called from processMessage) normalizes into a ctxPayload object:
- From — sender ID/name
- To — recipient
- RawBody — message text
- SessionKey — derived session identifier
- ChatType — "direct" | "group" | "channel" (src/channels/chat-type.ts:11)
- InboundEventKind — "user_request" | "room_event" (CORRECTED 2026-07-19 — was misrecorded as
  "message"|"room_event" here; actual type at src/channels/inbound-event/kind.ts:4. Full envelope
  + memory-attribution deep dive below.)
- MediaType — optional media content type

Ingress Authorization

File: bot-Cg3cHBMq.js:310-368 (source: extensions/telegram/src/ingress.ts)

Before processing, every message goes through ingress authorization:

// bot-Cg3cHBMq.js:310
const telegramIngressIdentity = defineStableChannelIngressIdentity({
    key: "telegram-user-id",
    normalize: (value) => {
        const normalized = normalizeAllowFrom([value]);
        return normalized.entries[0] ??
               (normalized.hasWildcard ? "*" : null);
    },
    sensitivity: "pii"
});

// For events (regular messages):
async function resolveTelegramEventIngressAuthorization(params) {
    return (await createTelegramIngressResolver({
        accountId: params.accountId
    }).event({
        subject: createTelegramIngressSubject(params.senderId),
        conversation: telegramConversation(params),
        event: { kind: params.eventKind, authMode: "inbound" },
        dmPolicy: params.dmPolicy,
        groupPolicy: params.enforceGroupAuthorization ? "allowlist" : "open",
        allowFrom: telegramAllowEntries(params.effectiveDmAllow),
        groupAllowFrom: params.enforceGroupAuthorization ?
            telegramAllowEntries(params.effectiveGroupAllow) : []
    })).ingress;
}

YES, it uses the SAME handoff function for every channel

Every channel plugin implements the same contract via channel-entry-contract-DfWF0KEA.js:

// channel-entry-contract (simplified from types)
defineBundledChannelEntry({
    id: "telegram",      // unique channel ID
    name: "Telegram",
    description: "Telegram channel plugin",
    plugin: { specifier: "./channel-plugin-api.js", ... },
    secrets: { specifier: "./secret-contract-api.js", ... },
    runtime: { specifier: "./runtime-setter-api.js", ... },
    accountInspect: { specifier: "./account-inspect-api.js", ... }
})

Each channel's runtime-api.js exports the same interface. The dispatch path for each channel converges at runChannelInboundEvent() → reply-pipeline → runReplyAgent().

---
STEP 3 — THE CORE ENGINE

Model Selection

File: model-selection-DF4MbMUd.js:91

function resolveDefaultModelForAgent(params) {
    // 1. Check agent-level model override
    const agentModelOverride = params.agentId ?
        resolveAgentEffectiveModelPrimary(params.cfg, params.agentId) : void 0;

    // 2. Fall back to global defaults
    // 3. Resolve from configured model catalog
    const resolved = resolveModelRefFromString({
        raw: agentModelOverride ?? globalDefault,
        defaultProvider: DEFAULT_PROVIDER,
        catalog: buildConfiguredModelCatalog(cfg)
    });

    return {
        provider: resolved.provider,
        model: resolved.model,
        thinkingLevel: resolved.thinkingLevel
    };
}

Provider catalog is built in provider-CiI2mFp8.js — it scans the config for models.providers entries and plugin-registered providers. Each provider is a plugin extension in extensions/<name>/.

Tool Selection

File: openclaw-tools-BUQsixTe.js

Tools are selected in the agent runner before LLM call:

1. Core tools defined in tool-catalog-Bxn5jw8h.js — CORE_TOOL_DEFINITIONS array
2. Each tool has: id, label, description, sectionId, profiles[]
3. At runtime, normalizeAgentRuntimeTools() filters based on:
  - Agent config (agents.defaults.tools)
  - Session capabilities
  - Channel capabilities
  - Tool policy (allow/deny lists)
  - Inherited tool restrictions (for subagents)
4. Tools are sent as function/tool definitions in the LLM API call
5. The LLM decides which tool to call — there is NO regex/keyword matching

System Prompt Building

The system prompt is built from multiple sources:
- Agent identity (IDENTITY.md, USER.md templates)
- Skills (loaded from skills/ directory)
- Bootstrap files (from agent workspace)
- Memory context (from memory search)
- Channel context (from channel event)
- Session history (compacted transcript)

Agentic Loop

File: agent-runner.runtime-CQGHXG44.js:2861 (runReplyAgent)

runReplyAgent:
  1. Check heartbeat/isActive
  2. Steering (inject into running stream if active)
  3. Queue management (enqueue vs drop vs followup)
  4. Preflight compaction (if context too large)
  5. Memory flush (if memory needs saving)
  6. LLM call via provider
  7. Tool execution (if LLM returns tool calls)
  8. Feed tool results back to LLM
  9. Loop until LLM returns final text
  10. Deliver reply

The actual LLM call + tool loop is handled by run-attempt-CnazfKzC.js which wraps @earendil-works/pi-ai (the embedded agent runtime).

---
STEP 10 — GATEWAY ARCHITECTURE

Gateway is a PROCESS — spawned by openclaw gateway run.

The gateway:
- Listens on an HTTP + WebSocket port
- Provides the control UI (control-ui/ — a SPA served from dist)
- Routes messages between channels and the agent engine
- Manages session state, cron jobs, node connections
- Broadcasts events to connected WebSocket clients

Entry point: gateway-entrypoint-CA3xEreb.js

// gateway-entrypoint-CA3xEreb.js:38
export {
    resolveGatewayInstallEntrypoint,
    findFirstAccessibleGatewayEntrypoint,
    isGatewayDistEntrypointPath,
    buildGatewayDistEntrypointCandidates
};

Agent-Channel Binding: Configured via channels config. Each channel account is bound to one or more agents. The resolveAgentRoute() function determines which agent handles a given message.

Outbound Reply Routing: The reply goes back through the ORIGINATING channel's send API. Reply context is stored in the session, including OriginatingChannel, accountId, chatId, threadId. The delivery pipeline unwraps this to route the reply.

---
STEP 6 — TERMINAL / CLI MODE

Entry: entry.js — invoked via openclaw terminal or openclaw tui or openclaw chat.

From entry.js:183-184:
const INTERACTIVE_TTY_COMMANDS = new Set([
    "tui",
    "terminal",
    "chat"
]);

The CLI mode goes through the SAME agentCommandFromIngress() pipeline:

// agent-command-l6N_480Y.js:1406
async function agentCommandFromIngress(opts, runtime, deps) {
    // Same code path as channel messages
    // ingressOpts with channel="terminal"
}

Or via the gateway:
// agent-via-gateway-BDchs9k_.js:283
async function agentViaGatewayCommand(opts, runtime, signalBridge) {
    // Talks to running gateway over HTTP/WS
}

---
STEP 4 — TOOLS AND SKILLS

Tool Definitions

Defined in tool-catalog-Bxn5jw8h.js as CORE_TOOL_DEFINITIONS array:

const CORE_TOOL_DEFINITIONS = [
    { id: "read",        label: "read",        description: "Read file contents",     sectionId: "fs", profiles: ["coding"] },
    { id: "write",       label: "write",       description: "Create or overwrite files", sectionId: "fs", profiles: ["coding"] },
    { id: "edit",        label: "edit",        description: "Make precise edits",     sectionId: "fs", profiles: ["coding"] },
    { id: "exec",        label: "exec",        description: "Run shell now.",         sectionId: "runtime", profiles: ["coding"] },
    { id: "process",     label: "process",     description: "Inspect/control exec sessions.", sectionId: "runtime", profiles: ["coding"] },
    { id: "code_execution", label: "code_execution", description: "Run sandboxed remote analysis", sectionId: "runtime", profiles: ["coding"] },
    { id: "web_search",  label: "web_search",  description: "Search the web",        sectionId: "web", profiles: ["coding"] },
    { id: "web_fetch",   label: "web_fetch",   description: "Fetch web content",     sectionId: "web", profiles: ["coding"] },
    { id: "x_search",    label: "x_search",    description: "Search X posts",        sectionId: "web", profiles: ["coding"] },
    { id: "memory_search", label: "memory_search", description: "Semantic search",   sectionId: "memory", profiles: ["coding"] },
    { id: "memory_get",  label: "memory_get",  description: "Read memory files",     sectionId: "memory", profiles: ["coding"] },
    { id: "sessions_list",   label: "sessions_list",   description: "List visible sessions...",   sectionId: "sessions" },
    { id: "sessions_history", label: "sessions_history", description: "Read sanitized session history...", sectionId: "sessions" },
    { id: "sessions_send",   label: "sessions_send",   description: "Message session...",       sectionId: "sessions" },
    { id: "sessions_spawn",  label: "sessions_spawn",  description: "Spawn subagent...",         sectionId: "sessions" },
    { id: "session_status",  label: "session_status",  description: "Show session status...",    sectionId: "sessions" },
    { id: "update_plan",     label: "update_plan",     description: "Track short work plan.",    sectionId: "sessions" },
    { id: "cron",            label: "cron",            description: "Schedule reminders...",     sectionId: "automation" },
    { id: "message_action",  label: "message_action",  description: "Cross-channel message actions", sectionId: "messaging" },
    // ... more tools
];

Tool Registration

Tools are registered through:
1. Core tools — built into CORE_TOOL_DEFINITIONS
2. Plugin tools — via resolvePluginTools() from each plugin's register() hook
3. MCP tools — from MCP server connections
4. Channel-specific tools — like message_action for cross-channel messaging

Skills vs Tools

Skills are Markdown-based instruction files in skills/<name>/SKILL.md. They are loaded at runtime and injected into the system prompt. Skills are NOT tools — they're instructions/context that teach the LLM how to use specific CLI tools or services.

Tools are compiled JS functions that the LLM calls via function-calling. They're registered with schemas, execute real code, and return results.

Tool Execution (Sandboxing)

File: run-attempt-CnazfKzC.js

Tool execution uses:
- @earendil-works/pi-ai (complete() function) for LLM calls
- @earendil-works/pi-coding-agent (createReadTool, createWriteTool, createEditTool) for file operations
- Sandbox paths enforced via assertSandboxPath()/sandbox-paths-U414eGG1.js
- For code execution: Codex App Server (optional remote sandbox) or local embedded Pi runner
- Timeouts enforced via buildTimeoutAbortSignal()

---
STEP 5 — /COMMANDS

All Known Commands

Found in commands-registry.data-BTTJ3_K_.js via defineChatCommand():

┌──────────┬─────────────┬──────────────┬──────────────────────────────┐
│ Command  │ Native Name │  Text Alias  │         Description          │
├──────────┼─────────────┼──────────────┼──────────────────────────────┤
│ help     │ help        │ /help        │ Show available commands      │
├──────────┼─────────────┼──────────────┼──────────────────────────────┤
│ commands │ commands    │ /commands    │ List all slash commands      │
├──────────┼─────────────┼──────────────┼──────────────────────────────┤
│ tools    │ tools       │ /tools       │ List available runtime tools │
├──────────┼─────────────┼──────────────┼──────────────────────────────┤
│ skill    │ skill       │ /skill       │ Run a skill by name          │
├──────────┼─────────────┼──────────────┼──────────────────────────────┤
│ new      │ new         │ /new, /reset │ Start fresh session          │
├──────────┼─────────────┼──────────────┼──────────────────────────────┤
│ model    │ model       │ /model       │ Show/change AI model         │
├──────────┼─────────────┼──────────────┼──────────────────────────────┤
│ think    │ think       │ /think       │ Set thinking level           │
├──────────┼─────────────┼──────────────┼──────────────────────────────┤
│ status   │ status      │ /status      │ Show session/model status    │
├──────────┼─────────────┼──────────────┼──────────────────────────────┤
│ compact  │ compact     │ /compact     │ Compact conversation         │
├──────────┼─────────────┼──────────────┼──────────────────────────────┤
│ stop     │ stop        │ /stop        │ Stop current generation      │
├──────────┼─────────────┼──────────────┼──────────────────────────────┤
│ continue │ continue    │ /continue    │ Continue last response       │
├──────────┼─────────────┼──────────────┼──────────────────────────────┤
│ system   │ system      │ /system      │ System message context       │
├──────────┼─────────────┼──────────────┼──────────────────────────────┤
│ config   │ config      │ /config      │ View/change config           │
├──────────┼─────────────┼──────────────┼──────────────────────────────┤
│ agents   │ agents      │ /agents      │ Manage agents                │
├──────────┼─────────────┼──────────────┼──────────────────────────────┤
│ bind     │ bind        │ /bind        │ Bind session to thread       │
├──────────┼─────────────┼──────────────┼──────────────────────────────┤
│ unbind   │ unbind      │ /unbind      │ Unbind from thread           │
├──────────┼─────────────┼──────────────┼──────────────────────────────┤
│ mcp      │ mcp         │ /mcp         │ MCP tools status             │
├──────────┼─────────────┼──────────────┼──────────────────────────────┤
│ plugins  │ plugins     │ /plugins     │ Manage plugins               │
├──────────┼─────────────┼──────────────┼──────────────────────────────┤
│ queue    │ queue       │ /queue       │ Queue configuration          │
├──────────┼─────────────┼──────────────┼──────────────────────────────┤
│ exec     │ exec        │ /exec        │ Shell execution config       │
├──────────┼─────────────┼──────────────┼──────────────────────────────┤
│ debug    │ debug       │ /debug       │ Debug info                   │
├──────────┼─────────────┼──────────────┼──────────────────────────────┤
│ usage    │ usage       │ /usage       │ Token usage info             │
├──────────┼─────────────┼──────────────┼──────────────────────────────┤
│ speech   │ speech      │ /speech      │ TTS/voice settings           │
├──────────┼─────────────┼──────────────┼──────────────────────────────┤
│ context  │ context     │ /context     │ Context window info          │
└──────────┴─────────────┴──────────────┴──────────────────────────────┘

Command Dispatch

File: commands-registry-Brl2piP4.js

Commands can come in two forms:
1. Native — Telegram bot commands (/help as bot command)
2. Text — Text message starting with /

Dispatch flow:
1. Message received → text checked for /command prefix
2. findCommandByNativeName() or text alias matching
3. parseCommandArgs() extracts arguments
4. Command handler executed (model switch, config change, etc.)

Model switch via /model propagates to ALL channels because it writes to the SESSION ENTRY in the session store. When any session (regardless of channel) loads, it reads from the same session store entry and picks up the model override.

---
STEP 7 — WEB UI

Yes, OpenClaw has a web UI — called "OpenClaw Control."

Frontend: SPA in dist/control-ui/ serving:
- index.html — main shell (supports themes: claw, knot, dash)
- assets/channels-DLznpXsV.js — channel management
- assets/agents-DRRMzlPn.js — agent management
- assets/cron-BVzi_5UI.js — cron job management
- assets/debug-CTBS8fG0.js — debug panel
- assets/config-runtime-CCw2hptH.js — config editor

Connection: The gateway serves this UI. The gateway runs an HTTP server with WebSocket for real-time events. The web UI connects via WebSocket to the same gateway that channel plugins connect to.

Same Pipeline: NO — the web UI is a CONTROL interface, not a chat interface. For chat, the gateway exposes a webchat channel which DOES go through the same pipeline. But the control UI manages configuration, not messages.

---
STEP 9 — PROVIDER / MODEL MANAGEMENT

Provider extensions: Each AI provider is a plugin in extensions/<name>/:
- extensions/anthropic/ — Claude (Anthropic)
- extensions/openai/ — OpenAI (GPT, Codex)
- extensions/deepseek/ — DeepSeek
- extensions/ollama/ — Ollama (local)
- extensions/groq/ — Groq
- extensions/openrouter/ — OpenRouter
- extensions/google/ — Google (Gemini)
- extensions/xai/ — xAI (Grok)
- extensions/mistral/ — Mistral
- extensions/perplexity/ — Perplexity
- extensions/github-copilot/ — GitHub Copilot
- extensions/lmstudio/ — LM Studio
- extensions/vllm/ — vLLM
- ... and 25+ more

ONE place where active model is stored: The session entry's modelOverride and providerOverride fields in the session store JSON file.

Model switch propagation: When a user runs /model gpt-5, it writes modelOverride: "gpt-5" to the session entry. On next message, resolveDefaultModelForAgent() checks resolveStoredModelOverride() before falling back to defaults. This propagates across channels because the SAME session entry is read regardless of which channel the next message comes from.

Fallback: model-fallback--w47Y_pS.js provides runWithModelFallback() which retries with fallback models on failure. Fallbacks are configured per-agent via agents.defaults.model.fallbacks.

---
STEP 11 — SIGNAL CHANNEL (researched 2026-07-19 for Empyralis Signal-parity work)

Sources: /opt/homebrew/lib/node_modules/openclaw/docs/channels/signal.md,
/opt/homebrew/lib/node_modules/openclaw/docs/plugins/reference/signal.md, and the
compiled extensions/signal/src/monitor/event-handler.ts + send-*.js bundles
(function names below are recovered from those bundles' own source-map comments,
e.g. "//#region extensions/signal/src/monitor/event-handler.ts").

Transport: gateway talks to `signal-cli` over HTTP, in one of two modes:
- Native: JSON-RPC at `/api/v1/rpc`, SSE receive stream at `/api/v1/events`
  (`apiMode: "native"`). This is the mode Empyralis's own
  signal-cli-bridge.ts implements.
- Container: bbernhard/signal-cli-rest-api Docker wrapper — REST `/v2/send`,
  WebSocket `/v1/receive/{account}` (`apiMode: "container"`, requires
  `MODE=json-rpc` on the container). `apiMode: "auto"` probes both and caches
  the result 30s. Empyralis does not implement container mode — native only.

Number model: the gateway is one Signal "device" (either linked via QR to an
existing account, or a dedicated registered bot number). Running the bot on
your OWN personal number makes it ignore your own messages (loop
protection) — OpenClaw's docs explicitly recommend a SEPARATE bot number for
"I text the bot and it replies." Empyralis's signal-cli-bridge.ts supports
either mode identically (it just needs a JSON-RPC endpoint + account).

DM / group access control:
- `dmPolicy`: pairing (default) | allowlist | open | disabled. Pairing
  issues a one-time challenge code (`openclaw pairing approve signal
  <CODE>`), expires after 1h. UUID-only senders are stored as `uuid:<id>`.
  Empyralis's own dmPolicy (personal_channels_service._enforce_dm_policy)
  is architecturally the same shape (owner_only/allowlist/pairing/open) and
  is SHARED across all personal channels including Signal — not something
  built per this research pass, already correct going in.
- `groupPolicy`: open | allowlist | disabled (default allowlist), with
  per-group `requireMention` override and `groupAllowFrom`. Empyralis's
  group gate (local-bridge-runtime.ts's pollInboundEvents + the backend
  safety-net in personal_channels_service._handle_local_bridge_gateway_channel_inbound)
  is simpler (always "must be mentioned or replying to Sage in a group,
  no per-group config") but was ALREADY implemented before this research
  pass — signal-cli-bridge.ts's mapSignalCliReceiveNotification already
  computed is_group/is_mentioned/is_reply_to_sage correctly.
- Session/routing model: DMs share the agent's main session; groups are
  isolated per-group (`agent:<agentId>:signal:group:<groupId>`).

Media + attachments (GAP — not built in this pass, see below): OpenClaw
downloads attachment bytes via `deps.fetchAttachment({baseUrl, account,
attachment, sender, groupId, maxBytes})`, where `attachment` is signal-cli's
own JsonAttachment (`{id, contentType, ...}` — confirmed against the
compiled bundle, not guessed). Caps: `mediaMaxMb` (default 8),
`ignoreAttachments` to skip downloads entirely. When text is empty but
attachments are present, OpenClaw's own fallback text is
`<media:${kindFromMime(contentType)}>` (e.g. `<media:image>`) for a single
attachment, or a summarized `[N images + M files ...]`-style string
(`formatAttachmentSummaryPlaceholder`) for multiple. Voice notes use the
signal-cli filename as a MIME fallback when contentType is missing (so
transcription can still classify AAC voice memos).
  Empyralis parity built in this pass: signal-cli-bridge.ts now emits the
  SAME kind of fallback text for an attachment-only message
  (`<media:attachment> (N)`, mirroring our own bluebubbles-bridge.ts's
  identical convention rather than OpenClaw's exact string) so the message
  no longer vanishes silently — but this is a TEXT PLACEHOLDER ONLY. Real
  binary attachment download/forwarding (fetching bytes from signal-cli and
  writing them to the gateway's shared media state dir in the
  GatewayChannelInboundMediaItem contract every OTHER local-bridge channel
  already leaves unbuilt too — see protocol/types.ts) remains a real,
  documented gap, shared by Signal/iMessage/WeChat alike, not attempted here
  since it needs a live signal-cli instance to build against safely.

Typing indicators (BUILT in this pass): OpenClaw calls signal-cli's
`sendTyping` JSON-RPC method via `sendTypingSignal(to, opts)` — params are
`{recipient: [...] | groupId, account?, stop?: true}` (recovered directly
from the compiled bundle, not guessed), refreshed while a reply is in
flight, explicit `stop` when it lands. Empyralis's signal-cli-bridge.ts now
exposes a `POST /typing {channel_key, remote_jid, action: start|stop}`
endpoint calling the IDENTICAL `sendTyping` RPC shape, and
local-bridge-runtime.ts (the shared machinery ALL THREE local-bridge
channels use) now starts a typing keepalive the instant an inbound message
is admitted and claims+stops it when the reply is dispatched — reusing the
existing, already-shared channels/foundation/typing-keepalive.ts
TypingKeepalive class WhatsApp/Telegram already use, same 3s
refresh/60s-ceiling constants. Best-effort: a bridge that doesn't implement
`/typing` (BlueBubbles, WeChat today) just never gets a successful call.

Read receipts (NOT built — documented gap): OpenClaw calls signal-cli's
`sendReceipt` JSON-RPC method via `sendReadReceiptSignal(to, targetTimestamp,
opts)` — params are `{recipient: [...], targetTimestamp, type: "read",
account?}` (also recovered directly from the compiled bundle). Gated by
`channels.signal.sendReadReceipts` (off by default), DMs only (signal-cli
has no group read receipts). NOT built for Empyralis Signal in this pass:
neither Telegram nor WhatsApp (the "mature channels" parity bar this task
was scoped against) have read receipts either, so building it only for
Signal would create asymmetric, not-actually-matching parity. The exact RPC
contract is recorded here for whoever picks this up — it's a small,
low-risk addition once it's wanted, same shape as the typing endpoint above.

Reactions (NOT built — documented gap, same reasoning as read receipts):
OpenClaw's `message action=react` supports Signal reactions
(`sendReactionSignal`/`removeReactionSignal`), including using 👍/👎
reactions as exec/plugin approval responses (`approvals.exec`/
`approvals.plugin`). Empyralis has NO reaction support on ANY personal
channel yet — WhatsApp's and Telegram's own gateway manifests both
explicitly declare `media: {..., reactions: false, ...}`
(whatsapp/runtime.ts, telegram/runtime.ts) — so this is a platform-wide
absent feature, not a Signal-specific gap, and out of scope for a
Signal-vs-our-own-mature-channels parity pass.

Self-chat / "Note to Self" as an owner command channel: OPENCLAW DOES NOT
HAVE THIS for Signal. Its own inbound handler explicitly drops every
`syncMessage` envelope unconditionally (`if ("syncMessage" in envelope)
return;`) as a blanket loop-prevention measure — it has no concept of
"the owner's self-conversation is a command channel," unlike Empyralis's
own established WhatsApp/Telegram is_self_chat pattern (see
whatsapp/message-mapper.ts's `is_self_chat: ownedJid ? remoteJid ===
ownedJid : false`). This is an Empyralis-specific product decision being
EXTENDED to Signal in this pass, not something ported from OpenClaw:
signal-cli-bridge.ts now computes is_self_chat the same way (fromMe &&
remoteJid === the bridge's own configured account && !isGroup), and
personal_channels_service.py's shared local-bridge inbound handler now
lets a self-chat message through the from_me gate exactly like the
WhatsApp/Telegram handlers already do (`_is_owner_message`'s
`is_self_chat` shortcut is channel-agnostic, so no separate backend
change was needed once the Signal bridge/gateway-runtime layers
supplied the field). A LOOP GUARD was required precisely because
OpenClaw's blanket-drop approach doesn't need one: signal-cli syncs
EVERY send the bridge itself makes back through the same receive path
(the same mechanism the existing from_me/is_reply_to_sage logic already
depends on), so without suppressing an echo of Sage's own self-chat
reply (matched via the same sentMessageIds set already used for
is_reply_to_sage), it would re-admit as a fresh command and loop forever
— the identical bug class WhatsApp's/Telegram's own self-chat echo
guards exist for (see empyralis-gateway/src/__tests__/whatsapp-self-chat.test.ts).

Approval reactions: Signal exec/plugin approvals route through the
top-level `approvals.exec`/`approvals.plugin` blocks (no
`channels.signal.execApprovals`) — 👍 approves once, 👎 denies,
`/approve <id> allow-always` for persistent approval. Not applicable to
Empyralis today (no reaction support at all yet, see above).

Text chunking: `textChunkLimit` (default 4000), optional
`chunkMode: "newline"` to prefer splitting on blank lines before length
chunking. Empyralis's signal-cli-bridge.ts does not chunk outbound text at
all yet (sends the full string in one signal-cli `send` call) — worth
noting if very long Sage replies into Signal turn out to hit signal-cli's
own message-size limits, but not exercised or fixed in this pass (no
evidence it's actually broken; flagged for awareness only).

Multi-account: `channels.signal.accounts.<id>` with per-account
config/groups overrides. Empyralis's signal-cli-bridge.ts is single-account
only (one EMPYRALIS_SIGNAL_CLI_ACCOUNT per bridge process) — matches the
existing single-account shape of every other local-bridge channel, not a
gap specific to Signal.

Summary table — Empyralis Signal vs OpenClaw Signal vs Empyralis's own
mature Telegram/WhatsApp, as of this research pass:

| Feature                        | OpenClaw Signal        | Empyralis Signal (after this pass) | Empyralis Telegram/WhatsApp |
|---------------------------------|-------------------------|-------------------------------------|-------------------------------|
| Catalog-listed as connectable    | n/a (not a product)     | YES (was the core bug — fixed)      | YES |
| Send + receive text              | yes                     | yes                                 | yes |
| Group support (mention/reply gate)| yes (configurable)     | yes (fixed gate, not configurable)  | yes |
| Self-chat as owner command channel| NO (blanket-dropped)   | YES (built this pass, + loop guard) | yes |
| Attachment text fallback (no drop)| yes (real download)    | yes (placeholder text only)         | yes (real download) |
| Real attachment/media forwarding | yes                     | NO (documented gap)                 | yes |
| Typing indicator                 | yes                     | YES (built this pass)               | yes |
| Read receipts                    | yes (DM only)           | NO (documented gap, matches TG/WA)  | NO |
| Reactions                        | yes                     | NO (documented gap, matches TG/WA)  | NO |
| Error-silence (no raw errors to chat)| n/a                 | yes (shared _deliver_local_bridge_personal_reply "ABSOLUTE RULE")| yes |

---
STEP 1 — SOURCE LOCATIONS

OpenClaw source was found at:
- /opt/homebrew/lib/node_modules/openclaw/ — npm install (v2026.5.27) — PRIMARY SOURCE
- /Users/mansur/empyralis/_archive/reference/openclaw/openclaw-src/ — archive reference (partial, incomplete)
- /Users/mansur/.openclaw/ — runtime config/cache (not source)

The main source is compiled TypeScript → minified JS in dist/. The original .ts source files are not on this machine (the archive reference only has vendor files).

---
KEY ARCHITECTURAL INSIGHTS

1. Plugin Architecture: Everything is a plugin — channels, providers, tools, commands, skills. Each is registered via definePluginEntry()/defineBundledChannelEntry().
2. Pi/Codex Runtime: The actual LLM conversation loop runs inside @earendil-works/pi-ai (the "Pi" embedded agent runner), with optional Codex App Server for sandboxed code execution.
3. Session-Centric: Everything revolves around sessions. A session binds a conversation to a channel, stores model preference, history, usage stats, and task state.
4. Gateway as Central Hub: The gateway process runs an HTTP+WS server that serves the control UI, proxies agent commands, manages cron, broadcasts events, and routes node connections.
5. Agent-Channel Model: Agents are independent identities bound to channels. One agent can serve multiple channels. Channels can have multiple accounts.

---
GROUP/MENTION GATING — researched 2026-07-19 while root-causing Empyralis's
"family group" bug (the agent answering every message in a 20-person
Telegram group as though the owner had asked). Source:
/opt/homebrew/lib/node_modules/openclaw/ (package.json version 2026.6.10 —
verified via package.json, not assumed; newer install than the
STEP-numbered sections above, whose file inventory was captured at
v2026.5.27 — chunk hashes differ between installs, e.g. the Telegram
message-context file is now bot-message-context.runtime-CU08Ix_N.js, not
bot-Cg3cHBMq.js:6854).

The core mechanism lives in dist/mention-gating-3P8aSD7o.js
(src/channels/mention-gating.ts). `resolveInboundMentionDecision({facts,
policy})` decides `shouldSkip` from:
  facts:  canDetectMention, wasMentioned, hasAnyMention, implicitMentionKinds
  policy: isGroup, requireMention, allowedImplicitMentionKinds,
          allowTextCommands, hasControlCommand, commandAuthorized
  shouldSkip = policy.requireMention && facts.canDetectMention && !effectiveWasMentioned
  effectiveWasMentioned = facts.wasMentioned || implicitMention || shouldBypassMention

Two things this buys OpenClaw that Empyralis's Telegram gateway (pre-fix)
didn't have:

1. `wasMentioned` is a FACT the channel plugin computes, not read from a
   platform-wide "you were addressed" flag. For Telegram specifically,
   OpenClaw's channel is bot-API-based — CONFIRMED via
   package.json's own dependencies, not inferred from naming: `"grammy":
   "1.43.0"` plus `@grammyjs/runner`/`@grammyjs/transformer-throttler`/
   `@grammyjs/types` (grammY, the standard Telegram Bot API framework —
   bot-token auth, not a user-session library like GramJS/Telethon). There
   is no full-account/user-session Telegram mode: the one
   telegram-account-flavored export
   (dist/plugin-sdk/telegram-account.d.ts, itself marked `@deprecated`)
   turns out to just be bot-TOKEN resolution across multiple configured
   bot accounts (`ResolvedTelegramAccount.token`/`tokenSource: "env" |
   "tokenFile" | "config" | "none"`), not a GramJS-style login. The bot
   has its OWN identity (@BotUsername), distinct from any human account.
   Telegram's own mention/reply semantics for a bot's messages are
   inherently about THAT bot, never conflated with a human owner being
   mentioned or replied to. Empyralis's Telegram
   channel, by contrast, is a full-account GramJS session (the owner's own
   personal Telegram, not a bot) — there is no separate "agent identity"
   for Telegram to flag as mentioned, so reading the raw MTProto
   `message.mentioned` bit (true for an explicit @mention OR a reply to a
   message the account sent) meant "someone replied to the OWNER, a real
   human participant in the group" registered as "the agent was addressed."
   That conflation was the actual root cause (see
   empyralis-gateway/src/channels/telegram/runtime.ts's
   hasExplicitTelegramMention, fixed 2026-07-19: explicit @mention/text-
   mention ENTITIES only, never the raw platform flag).

2. `policy.isGroup`/`requireMention`/`allowedImplicitMentionKinds` are
   explicit, per-provider-configurable policy inputs, not a single
   hardcoded OR of two signals. dist/runtime-group-policy-BEjP88cf.js
   additionally resolves a channel-level `groupPolicy: "open" | "allowlist"
   | "disabled"` with distinct configured-vs-missing-provider fallbacks
   (`resolveOpenProviderRuntimeGroupPolicy` defaults configured-but-silent
   providers to "open", missing-config providers to "allowlist" — fail
   CLOSED when nothing is configured at all). This is a SEPARATE axis from
   mention detection: even in an "open" group, a sender still needs to
   satisfy the mention/implicit-mention facts above to avoid `shouldSkip`.
   Empyralis's personal-channel group gate has no equivalent
   allowlist/open/disabled axis — every group is implicitly "open," gated
   purely by mention/reply-to-Sage.

Also relevant: this doc's own STEP 2 pipeline notes (above)
already show `ChatType — "direct" or "group"` as a normalized field on
every inbound ctxPayload, and the ingress resolver
(createTelegramIngressResolver(...).event(...)) is handed `groupPolicy`/
`groupAllowFrom` explicitly per call — group-vs-direct and
sender-authorization are first-class, named inputs to ingress, not
derived ad hoc inside a channel-specific handler the way Empyralis's
Telegram/WhatsApp gateway runtimes each independently reimplement their
own group gate today (real code duplication — see runtime.ts in both
channels/telegram and channels/whatsapp).
6. No Regex/KW Matching: Tool selection is 100% LLM-driven via function calling. There's no fallback regex or keyword-based tool dispatch.

---
IMESSAGE / BLUEBUBBLES ONBOARDING — researched 2026-07-21 for Empyralis
iMessage setup-quality work.

Source: /Users/mansur/openclaw (checked out git source tree — confirmed via
`package.json`: `"name": "openclaw"`, `"version": "2026.6.11"`; has
`src/`, `extensions/`, `dist/`, `docs/`, distinct from the npm-installed
`/opt/homebrew/lib/node_modules/openclaw` used by earlier STEP sections
above). `/Users/mansur/OpenClaw` (capitalized) is the SAME directory —
macOS's filesystem is case-insensitive, confirmed by identical `ls -la`
output (same inode-level listing, same file sizes/timestamps) for both
paths. `/Users/mansur/.openclaw` and `/Users/mansur/.openclaw-dev` are
runtime/config dirs (`openclaw.json`, `agents/`, `cron/`, `logs/` — no
`package.json` or `src/`), not source.

**CRITICAL FRAMING CORRECTION: OpenClaw does not use BlueBubbles anymore.**
BlueBubbles support was REMOVED from OpenClaw. The docs are explicit and
un-hedged about this (docs/channels/imessage.md:15-17):

> BlueBubbles support was removed. Migrate `channels.bluebubbles` configs
> to `channels.imessage`; OpenClaw supports iMessage through `imsg` only.

And the dedicated migration announcement (docs/announcements/bluebubbles-imessage.md:18):

> There is no BlueBubbles HTTP server, webhook route, REST password, or
> BlueBubbles plugin runtime in the supported OpenClaw iMessage path.

So "make Empyralis's iMessage work exactly the way OpenClaw does it" cannot
mean "make BlueBubbles smoother" — OpenClaw's current, only, supported
iMessage path is a completely different architecture: a native CLI tool
called `imsg` (https://github.com/steipete/imsg, third-party, MIT, by
Peter Steinberger — not an OpenClaw-authored project) that OpenClaw's
gateway spawns as a **child process** and talks to over **JSON-RPC on
stdio** — no HTTP server, no webhook, no port, no polling loop at all.
What follows below is what actually makes OpenClaw's iMessage setup smooth
(so it can be selectively adopted), plus an explicit call-out of the one
piece (private-API/SIP) Empyralis should NOT blindly copy.

## What OpenClaw actually does (imsg architecture, not BlueBubbles)

1. **The "bridge" is an in-process child, not a separate service the user
   launches.** `extensions/imessage/src/client.ts:1,63-75` — `IMessageRpcClient`
   uses Node's `child_process.spawn` directly inside the gateway process;
   there is no separate daemon, no port to bind, nothing to `npm run`.
   The channel plugin owns the subprocess's lifecycle (start on gateway
   start / channel enable, stop on gateway stop) automatically.

2. **Transport is a persistent JSON-RPC stream over stdio — no
   webhook, no polling.** Confirmed by grep: zero occurrences of "webhook"
   anywhere in `extensions/imessage/`. Inbound delivery is a live
   `watch.subscribe` RPC call over the same long-lived stdio connection
   (docs/channels/imessage.md:736 describes `imsg watch.subscribe` with a
   `since_rowid` cursor for replay). Every small message is a
   newline-framed JSON-RPC frame (docs/channels/imessage.md:122-130
   explicitly documents the anti-buffering contract any transport wrapper,
   e.g. an SSH pipe, must honor: forward each line as soon as bytes are
   available, never block on EOF).

3. **A real preflight/health command exists and is invoked by name in
   every setup step:** `openclaw channels status --probe`
   (docs/channels/imessage.md:47, 240, 762, 805). Backing implementation
   is `extensions/imessage/src/probe.ts`'s `probeIMessage()`
   (probe.ts:290-337) which chains: (a) `detectBinary(cliPath)` — is the
   CLI even installed (probe.ts:306-309); (b) `imsg rpc --help` — does
   this build support RPC at all, 5-min TTL cache (probe.ts:113-143); (c)
   `imsg status --json` — parses `advanced_features`, `v2_ready`,
   per-method `selectors`, and a human `statusMessage` explaining WHY the
   private-API bridge is down (SIP/library-validation/AMFI) when it is
   (probe.ts:222-283); (d) a live `chats.list` RPC call as the actual
   liveness check (probe.ts:330). This single command tells the operator
   exactly which of 4 distinct failure layers they're in, not just
   up/down.

4. **Auto-detection during setup, not manual typing.** The setup wizard's
   `cliPath` text input (`extensions/imessage/src/setup-core.ts:170-181`,
   `createIMessageCliPathTextInput`) resolves/validates against
   `detectBinary` from the shared plugin-sdk (same detector probe.ts
   calls), so `openclaw setup`/`openclaw onboard` (docs/cli/setup.md:12,
   docs/cli/index.md:16-17 — the guided first-run CLI wizard) finds an
   already-installed `imsg` on PATH instead of asking the user to hand-type
   a path blind.

5. **Remote/non-Mac hosting is a first-class documented pattern, not a
   workaround.** `channels.imessage.cliPath` can point at an SSH wrapper
   script that runs `imsg` on a remote Mac (docs/channels/imessage.md:89-134);
   `resolveIMessageNonMacHostError()` (probe.ts:103-111) detects "you're on
   Linux/Windows with the default local `imsg` path" and returns a
   specific, actionable error instead of a generic connection failure.

6. **Setup completion note is concrete and ordered, not generic.**
   `extensions/imessage/src/setup-core.ts:183-194` (`imessageCompletionNote`)
   is the literal text the CLI wizard prints after configuring iMessage:
   run OpenClaw on the Messages Mac (or set an SSH `cliPath`), run
   `imsg launch`, run `openclaw channels status --probe` to verify, confirm
   Full Disk Access + Automation, list chats with `imsg chats --limit 20`,
   link to docs. Every step is copy-pasteable.

7. **Inbound recovery after restart is automatic and explicitly
   documented, with a named suppression mechanism.**
   docs/channels/imessage.md:731-747 — on startup the monitor persists the
   last dispatched `chat.db` rowid per account, replays via
   `since_rowid`, dedupes by Apple GUID (`imessage.inbound-dedupe`
   persistent plugin state), and fences out Apple's post-Push-recovery
   "backlog bomb" by send-date age (~15 min). This is a real reliability
   feature with zero required config — "there is no config to enable"
   (docs/channels/imessage.md:12).

8. **The SIP/Private-API tradeoff is disclosed as a deliberate, opt-in
   decision with a stated default — not silently assumed.**
   docs/channels/imessage.md:179-194: `imsg` ships in two modes.
   **Basic mode is the default** — "no SIP changes needed... This is
   what you get out of the box from a fresh `brew install`" — text/media
   send-receive only. **Private API mode** (reactions, edit, unsend,
   threaded replies, effects, polls, group management, typing, read
   receipts) requires disabling System Integrity Protection AND macOS
   Library Validation, injecting a helper dylib into `Messages.app`, and
   is presented with an explicit `<Warning>` block: "Disabling SIP is a
   real security tradeoff... disabling SIP on Apple Silicon Macs also
   disables the ability to install and run iOS apps." OpenClaw explicitly
   tells operators who can't accept that tradeoff to stay in basic mode
   (docs/channels/imessage.md:247-253) or run a **separate, dedicated bot
   Mac** with SIP off rather than weakening a primary device.

## Empyralis's current implementation (what exists today)

- `empyralis-gateway/src/bridges/bluebubbles-bridge.ts` — a standalone HTTP
  server (`http.createServer`, bluebubbles-bridge.ts:373-449) exposing
  `/health`, `/messages` (POST, outbound send via BlueBubbles REST API),
  `/events` (GET, drains an in-memory queue), and `/webhook` (POST,
  BlueBubbles pushes inbound events here). It has a `main()` entrypoint
  (bluebubbles-bridge.ts:466-486) reading `EMPYRALIS_IMESSAGE_BRIDGE_PORT`,
  `EMPYRALIS_BLUEBUBBLES_SERVER_URL`, `EMPYRALIS_BLUEBUBBLES_PASSWORD`,
  `EMPYRALIS_IMESSAGE_BRIDGE_TOKEN` from env — i.e. it is designed to be
  run as its own process.
- **Nothing auto-starts it.** `empyralis-gateway/package.json:11-12` has a
  `signal:bridge` npm script (`node dist/bridges/signal-cli-bridge.js`)
  but **no `imessage:bridge` script at all** — the only way to run
  `bluebubbles-bridge.ts`'s `main()` today is to invoke
  `node dist/bridges/bluebubbles-bridge.js` by hand. `scripts/install-agent-computer.sh`
  (520 lines, greped in full) has zero mentions of "imessage" or
  "bluebubbles" anywhere — it writes exactly one systemd unit, for the
  gateway itself (`write_systemd_units`, install-agent-computer.sh:363-378),
  with no equivalent unit for any local bridge (Signal's `signal:bridge`
  isn't auto-started by the installer either — this is a shared gap, not
  iMessage-specific, but iMessage is the one with a UI door promising
  "Full account").
- **Gateway-side transport is polling, not a live subscribe.**
  `empyralis-gateway/src/channels/local-bridge-runtime.ts:251-258`
  (`LocalBridgePersonalChannelRuntime.start()`) sets a `setInterval` polling
  `${baseUrl}/events` every `EMPYRALIS_IMESSAGE_BRIDGE_POLL_MS` (default
  5000ms, local-bridge-runtime.ts:349). So the real path is: BlueBubbles
  Server webhook-pushes → `bluebubbles-bridge.ts`'s `/webhook` handler
  enqueues in memory (bluebubbles-bridge.ts:436-444) → gateway polls that
  queue every 5s. Two hops, one of which (gateway↔bridge) is poll-based
  where OpenClaw's imsg path is a single live stdio stream with zero hops.
- **No preflight/health command equivalent to `openclaw channels status
  --probe`.** The only health surface is `/health`
  (bluebubbles-bridge.ts:380-394), which does one thing: pings BlueBubbles'
  own `/api/v1/ping`. It cannot distinguish "BlueBubbles not installed" vs
  "wrong password" vs "signed out of iMessage" vs "our own bridge process
  isn't even running" — that last one is invisible to the gateway entirely
  until a poll cycle times out, because there is no process supervision
  linking bridge liveness to gateway state.
- **No setup wizard / auto-detection.** The only "setup" UX is
  `frontend/lib/workspace/fleet/SageLauncher.tsx:52`
  (`NOT_YET_SUPPORTED_CHANNELS.imessage`): *"iMessage requires a Mac
  running BlueBubbles Server — there's no in-app setup for this yet. Point
  your Gateway at it with the `EMPYRALIS_BLUEBUBBLES_SERVER_URL` and
  `EMPYRALIS_BLUEBUBBLES_PASSWORD` environment variables."* — literally
  tells the user to go set env vars by hand. `FleetAgentDetail.tsx:1273-1302`
  (`LocalBridgeChannelStatus`) does render a live status pill once
  something is connected/polling, but there's no path INTO configured
  state from the UI — no cliPath/URL/password form, no "detected imsg" /
  "detected BlueBubbles" auto-check.
- **No SIP/Private-API disclosure at all** — Empyralis's BlueBubbles path
  is REST-only (BlueBubbles' own Private API is a separate concern
  BlueBubbles Server itself manages, not surfaced anywhere in Empyralis's
  UI copy or bridge code) — reactions/typing/edit/unsend are simply absent
  (`local-bridge-runtime.ts:167-168,486-487` comments confirm typing is a
  no-op no-op for BlueBubbles today: *"a bridge that doesn't implement
  /typing (BlueBubbles, WeChat today) simply never gets a successful
  call"*), with no user-facing explanation of what's missing or why.

## Adoption plan, ordered by leverage

1. **Auto-start the bridge process from the gateway itself, in-process,
   like OpenClaw's `client.ts` spawn model** — highest leverage, closes the
   "rough, manual, multi-process" complaint directly. Fold
   `bluebubbles-bridge.ts`'s HTTP server startup into
   `LocalBridgePersonalChannelRuntime.start()`
   (`local-bridge-runtime.ts:242-258`) so enabling the `imessage_personal`
   channel on a gateway starts the bridge automatically, the same way
   OpenClaw's channel enable spawns `imsg rpc`. This alone eliminates the
   missing `imessage:bridge` npm script gap
   (`empyralis-gateway/package.json:11-12`) and the missing systemd unit
   gap (`scripts/install-agent-computer.sh:363-378`) without needing a new
   installer path — it becomes "just enable the channel."
2. **Add a real preflight/health command, modeled on `probeIMessage()`
   (probe.ts:290-337).** Extend `bluebubbles-bridge.ts`'s `/health`
   handler (currently just `/api/v1/ping`, bluebubbles-bridge.ts:380-394)
   to report the SAME layered breakdown OpenClaw's probe does: is the
   bridge process running at all / is BlueBubbles reachable / is the
   password valid / is Messages actually signed in — and surface that
   breakdown in `LocalBridgeChannelStatus`
   (`FleetAgentDetail.tsx:1273-1302`) instead of the current binary
   connected/not-connected pill.
3. **Replace gateway↔bridge polling with a push/stream model**, closing
   the gap with OpenClaw's live stdio subscribe. Lowest-effort version:
   have the bridge push straight to the gateway's existing inbound
   publish path instead of `local-bridge-runtime.ts`'s `setInterval`
   poll (`local-bridge-runtime.ts:251-258, 353-410`) — e.g. an SSE or
   WebSocket connection from bridge to gateway, replacing the 5s poll
   loop. This does not require adopting `imsg`; it only removes the
   extra poll hop already identified above.
4. **Build a real in-app connect/setup panel for `imessage_personal`**,
   replacing the "paste env vars yourself" message in
   `SageLauncher.tsx:52`. Modeled on OpenClaw's wizard text-input +
   auto-detect + completion-note pattern (`setup-core.ts:170-194`): a form
   for BlueBubbles server URL + password (or, if Empyralis ever adopts
   `imsg`-style local install, a cliPath field with `detectBinary`-style
   auto-detection), plus an ordered completion checklist mirroring
   `imessageCompletionNote` (setup-core.ts:183-194) — "install X, grant Y
   permission, click verify."
5. **Document/expose Full Disk Access + Automation as explicit
   requirements** the way docs/channels/imessage.md:136-152 does, since
   Empyralis's bridge also ultimately depends on a signed-in Messages.app
   Mac even though it's mediated through BlueBubbles Server rather than
   `imsg` directly — today this is entirely undocumented in-app.

## What NOT to blindly copy

- **Do not port `imsg`'s SIP-disable / Library-Validation-disable /
  dylib-injection private-API path wholesale.** OpenClaw itself treats
  this as an opt-in, disclosed tradeoff, not a default
  (docs/channels/imessage.md:190-194, 247-253) — "if your threat model
  can't tolerate SIP being off, bundled iMessage is limited to basic
  mode." For Empyralis, shipping guidance that tells a customer to
  disable a core macOS security boundary (with the explicit Apple
  Silicon iOS-app-support side effect OpenClaw's own docs flag) is a
  bigger ask than anything currently promised in the product surface —
  if ever pursued, it should be presented with the same explicit
  Warning-block treatment OpenClaw uses, opt-in, and reserved for a
  dedicated bot Mac, never the default path.
- **Do not adopt `imsg` itself without a legal/security review.** It is a
  third-party MIT tool (github.com/steipete/imsg) that injects into
  Apple-signed `Messages.app` via an adhoc-signed helper dylib once SIP is
  off — a meaningfully different trust boundary than shelling out to
  BlueBubbles' own REST API, and worth evaluating independently of the
  onboarding-UX lessons above (which are transport/process-supervision
  lessons, not an endorsement of the specific binary).
- **OpenClaw's CLI-wizard-first UX (`openclaw setup`) doesn't map
  directly** — Empyralis is a hosted product with a web UI as the primary
  surface, not a CLI tool users run locally, so "auto-detect binary on
  PATH during an interactive terminal wizard" becomes "detect
  bridge/BlueBubbles reachability from the web UI's health poll" instead
  of a literal port of the CLI flow.


UPDATE 2026-07-16 — full .ts source now available; prefer it for all future OpenClaw research

Better source than the minified dist above: `/Users/mansur/openclaw` — the actual TypeScript
(`extensions/*/src`, `src/`, `docs/`). This doc is our knowledge cache — every OpenClaw sub-agent
finding gets folded back here so we stop re-researching. (This worktree's copy of the doc predates
several rounds of research already folded into the main checkout's `docs/OpenClaw.md` — TOPOLOGY,
MULTI-AGENT ROUTING, MCP client/server, and MEDIA-GENERATION sections exist there but not here yet.
Reconcile on next merge; not duplicated below to avoid drift.)

========================================================================
INBOUND CONTEXT ENVELOPE — what OpenClaw hands the model for one message
========================================================================
Source: /Users/mansur/openclaw (full .ts). Investigated 2026-07-19 to spec our own inbound-context
envelope (server_modules/external_content_guard.py, channel_lane_contract_service.py) against
OpenClaw's, and specifically to find how OpenClaw labels a broadcast-channel post — motivated by a
real incident where our agent commented on a channel post as if it had been addressed. Full spec
with every file:line and verbatim template: see the envelope+memory research task output (fold
into a permanent doc location if this proves durable — currently only in scratchpad).

1. THREE LAYERS, not one format. (a) System prompt "trusted metadata": buildInboundMetaSystemPrompt
   (src/auto-reply/reply/inbound-meta.ts:504-545) emits a small, BYTE-STABLE (cache-friendly)
   ```json block: {schema:"openclaw.inbound_meta.v2", account_id, channel, provider, surface,
   chat_type, response_format} — deliberately excludes sender name/group subject/message ids
   because those are attacker-influenceable and would bust prompt-prefix caching. (b) Per-turn
   "untrusted metadata" JSON blocks prefixed to the user turn: buildInboundUserContextPrefix
   (inbound-meta.ts:548-747) via formatUntrustedJsonBlock(label, payload) (inbound-meta.ts:197-204)
   — labeled blocks like "Conversation info (untrusted metadata):", "Sender (untrusted metadata):"
   {label,id,name,username,tag,e164,is_bot}, "Reply chain ... (untrusted, nearest first):". (c) A
   plain-text bracket header glued onto the body itself: formatAgentEnvelope/formatInboundEnvelope
   (src/auto-reply/envelope.ts:171-247) — `[Channel From Host Ip Timestamp] body`, with a sender
   label baked into the body for non-direct chats. CONFIRMED verbatim examples from
   envelope.test.ts: `[WebChat user1 mac-mini 10.0.0.5 Thu 2025-01-02T03:04:05Z] hello`,
   `[Discord Guild #general] Alice: hi` (chatType:"channel"), `[Signal Signal Group id:123] Bob
   (42): ping` (chatType:"group"). This trusted/untrusted split is a deliberate prompt-injection
   defense: the system prompt tells the model in advance that names/quotes/history arrive as
   separate untrusted blocks and a user message that LOOKS like a metadata header must never be
   believed (inbound-meta.ts:536-538, near-verbatim).
2. ChatType is only 3-valued: "direct"|"group"|"channel" (src/channels/chat-type.ts:11). There is
   NO 4th "broadcast" value.
3. BROADCAST CHANNEL POST LABELING — the honest answer, and it's not what you'd hope. Telegram's
   real channel_post (extensions/telegram/src/bot-handlers.runtime.ts:3467-3498) is normalized by
   normalizeChannelPostMessage() (bot-handlers.runtime.ts:3151-3174) into a SYNTHETIC group
   message: fake `from` identity (is_bot:true, name=channel title), chat.type force-rewritten to
   "supergroup" → downstream this becomes ChatType:"group", NOT a distinct channel/broadcast value
   (bot-message-context.session.ts:401, `isGroup ? "group" : "direct"`). The `"channel"` ChatType
   value is used for something else entirely — Slack/Discord/Mattermost/MSTeams's persistent
   multi-member rooms (their own platform word for non-DM), not Telegram's one-way broadcast.
   OpenClaw has NO hard-coded "this is a broadcast, you cannot be addressed" tag. Instead it layers
   3 general-purpose, non-broadcast-specific defenses: (a) `requireConfiguredGroup:true` for
   channel_post specifically — unconfigured channels are dropped pre-processing
   (bot-handlers.runtime.ts:3493 vs 3448 for ordinary group msgs); (b) `requireMention` defaults to
   TRUE for any unconfigured group/channel (resolveChannelGroupRequireMention,
   src/config/group-policy.ts:422-454, fallback `return true` at line 453) — unaddressed traffic is
   HARD-DROPPED (`return null`) before the model ever sees it
   (extensions/telegram/src/bot-message-context.body.ts:418-475); (c) an OPT-IN (no default —
   z.enum(...).optional(), zod-schema.core.ts:586,614) "room_event" framing for operators who want
   unaddressed group/channel traffic to still reach the model but clearly marked passive: literal
   `"[OpenClaw room event]"` header (src/auto-reply/reply/prompt-prelude.ts:14) +
   `"Treat this as observed room activity. Decide whether to act."` (prompt-prelude.ts:143-160) —
   this is the closest thing to an explicit non-addressed label, but it's generic ambient-group
   framing, not broadcast-specific, and it's off by default. On top, every group/channel turn's
   system prompt carries buildGroupChatContext's generic "mostly lurk ... reply only when directly
   addressed ... if addressed to someone else, stay silent" instruction (src/auto-reply/reply/groups.ts:235-301,
   249,264-271) — same text for a 2-person Slack channel and a 50k-subscriber Telegram broadcast.
   BOTTOM LINE: if an operator misconfigures requireMention:false or groupActivation:always for a
   channel-post-fed group, OpenClaw has the SAME "replies to every channel post" failure mode our
   owner hit. This is a config footgun OpenClaw also has, not a problem it solved.
4. Operator-facing "shows JSON with username+message" — NOT CONFIRMED as a distinct feature after a
   real search (src/tui/**, src/interactive/payload.ts, raw-update-log.ts, /debug /context /system
   commands all checked, none match; dist/control-ui is compiled, source unavailable). Strong
   candidate (INFERRED): this is just layers (a)/(b) above AS SEEN in a verbose/debug prompt log —
   they ARE literally ```json fenced blocks containing sender/sender_id/sender_username next to the
   body, because that's the real prompt payload, not a bespoke display feature.
5. >> vs EMPYRALIS: our current envelope (server_modules/external_content_guard.py:149-192,
   wired via channel_lane_contract_service.guard_personal_gateway_inbound_message,
   channel_lane_contract_service.py:864-878) wraps EVERY personal-channel inbound message —
   including the owner's own DMs — in the identical "SECURITY NOTICE: external, untrusted source"
   framing, with no chat_type/chat_kind field threaded through at all (confirmed zero grep hits for
   chat_type|broadcast|channel_post|is_owner in channel_lane_contract_service.py). There IS one
   good existing gate — WhatsApp group inbound is skipped unless mentioned or replying to Sage
   (personal_channels_service.py:1410-1419), same idea as OpenClaw's requireMention hard-drop — but
   it's WhatsApp-specific in what was found; Telegram's build_telegram_personal_reply_async
   (personal_channel_sage_bridge_service.py:354) needs a separate audit for an equivalent gate,
   since Telegram is the platform with the actual broadcast-channel concept. We're full-account
   (not bot identity) on these channels, so OpenClaw's "disguise the channel post as a group
   message from a synthetic bot sender" hack doesn't transfer cleanly (no bot-vs-human fiction to
   lean on) and shouldn't be copied — the fix should be an explicit chat_kind field
   (direct|group|broadcast_channel|status) sourced from the gateway's native client, checked BEFORE
   any reply/comment tool call is allowed, not just mentioned in prompt text.

========================================================================
MEMORY ATTRIBUTION — does OpenClaw stop a stranger's words becoming "the owner's" fact?
========================================================================
Source: /Users/mansur/openclaw (full .ts). Investigated 2026-07-19 alongside the envelope research
above, direct motivation: a stranger's statement in a group got treated as the owner's own
preference by our agent. Two structurally different OpenClaw memory-write paths, both checked.

1. PATH A — file-based, default, always on. MEMORY.md + memory/YYYY-MM-DD.md, plain Markdown
   (docs/concepts/memory.md:9-11, "there is no hidden state"). WHAT gets written is decided by THE
   MODEL ITSELF via write/edit tool calls, nudged by a pre-compaction "memory flush" turn
   (src/auto-reply/reply/memory-flush.ts — gates WHEN, not WHAT; shouldRunMemoryFlush,
   memory-flush.ts:136-161) and by an optional "dreaming" consolidation pass gated on score/recall-
   frequency/query-diversity thresholds (src/memory-host-sdk/dreaming.ts) — none of dreaming's
   gates are source-trust related, confirmed by reading its MemoryLightDreamingSource /
   MemoryDeepDreamingSource enums (dreaming.ts:66-67): "daily"|"sessions"|"recall"|"memory"|"logs" —
   data-source TYPES, not sender identity. Whatever lands in MEMORY.md is trusted unconditionally
   next turn: `"MEMORY.md: durable user preferences and behavior guidance. Keep following it
   throughout the session unless higher-priority instructions override."` (src/agents/system-prompt.ts:229,
   verbatim). The ONLY attribution-awareness anywhere in OpenClaw is PROSE GUIDANCE in
   docs/concepts/memory.md's "Action-sensitive memories" section (read in full, lines 58-96),
   telling the model to optionally note "who is the source or owner, if that affects trust or
   authority" as part of a memory note's text — advisory, not a schema field, not code-enforced,
   entirely dependent on model compliance.
2. PATH B — extensions/memory-lancedb, bundled but OPT-IN (autoCapture defaults false,
   config.ts:249, `cfg.autoCapture === true`). CONFIRMED the default backend (memory-core) does NOT
   auto-capture from raw text at all (zero agent_end/autoCapture hits in src/plugin-sdk/memory-core*.ts)
   — this vulnerability only exists if an operator opts in. When it IS on: shouldCapture()
   (extensions/memory-lancedb/index.ts:1350-1393) is a PURELY CONTENT-BASED filter — envelope-
   sludge/length/emoji/prompt-injection checks plus a MEMORY_TRIGGERS regex match — takes NO
   sender/chatType/owner parameter, full function signature confirmed by direct read. Worse:
   sanitizeForMemoryCapture() (index.ts:1183-1322), which runs first, EXPLICITLY STRIPS the
   sender-attribution prefix the envelope layer built (`"Bob: ..."` / `"(self): ..."`) before
   storage — own code comment at index.ts:1299-1307 confirms this is deliberate cleanup, not an
   accident. The storage schema has nowhere to put attribution even if it survived:
   `MemoryEntry = {id, text, vector, importance, category, createdAt}` (index.ts:50-57) — no
   sender/owner/source-channel field at all. Wired to `api.on("agent_end", ...)`
   (index.ts:1943-2015), fires after EVERY successful turn in EVERY chatType (direct/group/channel)
   — no chatType scoping on the write side (config.ts:7-21 confirms: autoCapture, captureMaxChars,
   customTriggers only). SHARPEST finding: the attribution data is NOT architecturally unavailable
   — the agent_end hook's own `ctx` param (PluginHookAgentContext, src/plugins/hook-types.ts:243-275)
   carries `senderId`, `channel`, `chatId`, `channelContext:{sender,chat}` right there, and the
   plugin's capture code simply never reads any of them. `senderIsOwner` is a real, pervasively-used
   concept elsewhere in OpenClaw core (agent-tools.ts, agent-command.ts, message-tool.ts, dozens of
   files) for AUTHORIZATION — just never threaded into this memory-write path. Contrast: the
   SIBLING recall-side plugin (active-memory) DOES scope by chat type, and its default is
   restrictive — `allowedChatTypes: [...] ?? ["direct"]` (extensions/active-memory/index.ts:867) —
   recall defaults to direct-chats-only, capture has no equivalent default-safe scoping at all. That
   asymmetry (safe-by-default reads, unscoped writes) is backwards from what attribution-safety
   would want.
3. CONCLUSION: OpenClaw does not solve this. Path B has a confirmed, reproducible bug: any group/
   channel message matching a preference/fact/decision-shaped regex gets embedded and later
   recalled into ANY session (including 1:1 DMs) with zero record of who said it, and the plugin
   actively discards attribution data sitting in its own hook context. Path A is "attribution-
   aware" only to the extent a compliant model follows advisory docs. DO NOT COPY PATH B'S PATTERN.
4. Memory schema, both paths (CONFIRMED): LanceDB `MemoryEntry` has 6 fields, none provenance
   (index.ts:50-57); category enum `preference|fact|decision|entity|other`
   (extensions/memory-lancedb/config.ts:26). MEMORY.md/daily notes: unstructured prose, no schema
   at all. Neither has a first-class who-said-it / source-channel / owner-vs-third-party field.
5. UI/config surface for memory rules (CONFIRMED, mostly absent): no dedicated memory-attribution
   settings screen found anywhere in the TS source (dist/control-ui is compiled, out of scope).
   Capture config: autoCapture/captureMaxChars/customTriggers, no chat scoping. Recall config:
   allowedChatTypes/allowedChatIds/deniedChatIds, chat-scoped but never sender-scoped. The ONE place
   owner-identity is actually checked in the whole memory stack is `/active-memory ... --global`'s
   admin-permission gate on CHANGING CONFIG (lacksAdminToMutateActiveMemoryGlobal, index.ts:830-838)
   — not a gate on what gets written to memory.
6. >> vs EMPYRALIS: server_modules/agent_memory.py's `memory_entries` SQLite schema
   (agent_memory.py:267-274, `{key, content, created_at, updated_at}`) has the EXACT SAME gap as
   OpenClaw's MemoryEntry — no sender/chat_kind/owner field anywhere. `_save_memory()`
   (agent_memory.py:333-363) and `_save_daily_log()` (agent_memory.py:500-518) take no sender/
   chat_kind/owner parameter. MEMORY.md is confirmed by this file's own docstring
   (agent_memory.py:556-578) to be the real "source of truth," injected "verbatim every turn" —
   same unconditional-trust posture as OpenClaw's system-prompt.ts:229 line. We independently
   converged on the identical MEMORY.md + memory/YYYY-MM-DD.md file-naming convention, worth noting.
   UNLIKE OpenClaw's Path B, no automatic regex-trigger capture from raw conversation text was
   found in this file — `_save_memory`/`_save_daily_log` take caller-supplied key/content directly,
   suggesting an explicit-tool-call path closer to OpenClaw's Path A. NOT YET TRACED: which caller
   (memory_write tool? sage_agent_runtime_service.py?) actually supplies key/content, and whether
   THAT call site has any sender/chat_kind awareness — flagged as a fast follow-up, not confirmed
   clean. RECOMMENDATION: this is the highest-leverage place to do strictly better than upstream,
   not just match it — add a required provenance field to memory_entries (source_sender_id,
   source_is_owner or attributed_to enum, source_chat_kind), thread the sender/chat_kind that
   channel_lane_contract_service's guard call already resolves through to whatever calls
   _save_memory for channel-originated turns, and gate the "durable, verbatim-injected" trust class
   on it. Since OpenClaw's own plugin-hook contract proves this data is normally already available
   at the write decision point, our fix is plumbing existing data through, not inventing new
   detection — precisely the step OpenClaw's bundled plugin skips.
6. No Regex/KW Matching: Tool selection is 100% LLM-driven via function calling. There's no fallback regex or keyword-based tool dispatch.


6. No Regex/KW Matching: Tool selection is 100% LLM-driven via function calling. There's no fallback regex or keyword-based tool dispatch.

---
ADDENDUM (2026-07-20) — WECHAT CHANNEL / TENCENT iLINK PROTOCOL

CORRECTION to prior internal claims: `frontend/lib/workspace/fleet/SageLauncher.tsx:53`
says "Personal WeChat has no official API to build a bridge against, so this isn't
supported yet", and the since-deleted platform map said the same: WeChat is
unbuilt because "Personal WeChat has no official API to build a bridge
against." Both were true when written but
are now STALE. Tencent released an official personal-WeChat bot API on 2026-03-21/22
(confirmed via npm registry `time.created` for `@tencent-weixin/openclaw-weixin`, not
just a blog claim). The old grey-market/reverse-engineered path (WeChatPadPro, itchat,
iPad-protocol hooking — genuinely high ban risk, ToS-violating) is still banned/unsafe;
that's a separate thing from iLink, which is Tencent's own sanctioned product. Full
scoping doc with every claim source-tagged CONFIRMED/INFERRED:
`/private/tmp/claude-501/-Users-mansur-empyralis/4c1272c1-6a8e-4f4c-9592-15f4633ec1b7/scratchpad/wechat-ilink-scoping.md`
(session scratchpad — copy anything load-bearing into a permanent doc before it expires).

What iLink is: a plain HTTP/JSON REST API at `https://ilinkai.weixin.qq.com` — not a
websocket protocol, not SDK-only. Confirmed live myself with a bare unauthenticated curl
(`GET /ilink/bot/get_bot_qrcode?bot_type=3` → real QR token, HTTP 200, zero credentials
needed for that step). Auth is QR-scan-to-token: `get_bot_qrcode` → user scans in WeChat →
poll `get_qrcode_status` (long-poll) → `bot_token` + `baseurl`, used thereafter as
`Authorization: Bearer <bot_token>` + fixed `AuthorizationType: ilink_bot_token` +
per-request `X-WECHAT-UIN` (random, anti-replay). Inbound messages are long-polled via
`POST getupdates` (~35s hold, cursor-based — same shape as Telegram's getUpdates).
Outbound replies via `POST sendmessage` MUST echo the inbound message's `context_token`
or the reply doesn't attach to the right conversation. Media (image/voice/file/video)
goes through a separate CDN with client-side AES-128-ECB encryption. No group chat today
(schema has a placeholder `group_id` field but OpenClaw's own docs say the plugin
"declares direct chats only"). No proactive/cold outbound — bot can only reply inside an
existing context_token thread the human started. No published rate limits anywhere.

Is this OpenClaw-locked or can we call it directly? DIRECTLY CALLABLE, confirmed multiple
ways:
1. Tencent's own `Tencent/openclaw-weixin` README has a "Backend API Protocol" section
   explicitly for "developers integrating with their own backend" — the full endpoint
   table, request/response JSON. Tencent documents this for third parties on purpose.
2. Three independent non-OpenClaw reimplementations already exist in the wild:
   `github.com/x1ah/wechat-ilink-demo` (raw JS, README literally says "iLink 协议本身是
   独立的 HTTP/JSON API，完全可以脱离 OpenClaw 直接调用" — standalone, no OpenClaw needed),
   `github.com/the-yex/wechat-ilink-sdk` (community Go SDK, Apache-2.0, no OpenClaw dep),
   and a standalone Python client (per a third writeup) built by stripping 3 OpenClaw
   coupling points out of Tencent's own npm package source.
3. There's a `bot_agent` field (UA-style, "used for log attribution... not used for
   authentication or routing") that Tencent's README explicitly invites third-party apps
   to set to their own name instead of the "OpenClaw" default — built for non-OpenClaw
   clients on purpose.

What IS OpenClaw-locked: the published npm package `@tencent-weixin/openclaw-weixin`
itself (41 TS source files: auth/, api/, cdn/, messaging/, monitor/, config/, storage/)
declares `peerDependencies: { "openclaw": ">=2026.5.12" }` (confirmed via direct
`registry.npmjs.org` query) and "checks the host version at startup and will refuse to
load if the running OpenClaw version is outside the supported range" per its own README.
So: the PACKAGE is OpenClaw-only; the PROTOCOL it implements is not. No separate official
Tencent SDK exists outside this OpenClaw plugin — but the protocol is small enough (7
endpoints, one auth flow, one AES-128-ECB media codec) that writing an Empyralis-native
client from Tencent's own documented endpoint table is less work than extracting OpenClaw
glue from their package.

Empyralis integration shape: almost all our own plumbing for `wechat_personal` already
exists and is dev-harness-tested (`LOCAL_BRIDGE_PERSONAL_CHANNEL_CONFIGS` in
`empyralis-gateway/src/channels/local-bridge-runtime.ts:128-136`, the generic
`LocalBridgePersonalChannelRuntime` polling/dedup/publish class in the same file, the
`wechat_personal` catalog entry in `server_modules/connection_catalog_service.py:265-286`
already marked `LAUNCH_LIVE_WHEN_CONFIGURED`, and the generic
`_handle_local_bridge_gateway_channel_inbound` dispatch in
`server_modules/agent_channel_router.py:1625`). The only missing piece is a new
`empyralis-gateway/src/bridges/wechat-bridge.ts`, same shape as the existing
`signal-cli-bridge.ts` (expose `/health`, `POST /messages`, `GET /events`; translate to/
from iLink underneath). The one genuinely new design problem, not present in any existing
bridge: our generic inbound/outbound event shape has no slot for `context_token`, which
iLink requires on every reply — needs to ride in the existing free-form `metadata` field
plus a small `remote_jid → last context_token` map the bridge keeps itself.

Biggest open question, unresolved by any source including Tencent's own docs: whether
non-Mainland-China-registered WeChat accounts are eligible at all. Worth one real test
login before relying on this for the stated China-market strategic angle.
