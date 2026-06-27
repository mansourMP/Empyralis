🔬 Empyralis Architecture — Full Forensic Audit

Source: /Users/mansur/empyralis/ (working directory, main branch)
Version: Pre-release (private repo)
Stack: Python (FastAPI) + TypeScript (Next.js/Node.js) + Rust (policy kernel + desktop supervisor)
License: Proprietary

---
STEP 1 — FINDING THE SOURCE

Empyralis is a multi-language monorepo at /Users/mansur/empyralis/:

┌───────────────────┬────────────────────────┬───────────────────────────────────────┬────────────────────────────────────────────────────────────┐
│     Component     │        Language        │                                     Purpose                           │
├───────────────────┼────────────────────────┼───────────────────────────────────────┼────────────────────────────────────────────────────────────┤
│ Server            │ Python (FastAPI)       │ server.py, sein API, message routing, LLM orchestration               │
│                   │                        │ files)                                │                                                            │
├───────────────────┼────────────────────────┼───────────────────────────────────────────────────────────────────────┤
│ Python Engine     │ Python                 │ python_engine/                        │ Cognitive loop (OODA), LanceDB memory, daemon              │
├───────────────────┼────────────────────────┼───────────────────────────────────────────────────────────────────────┤
│ Frontend          │ TypeScript (Next.js    │ frontend/                             │ Web UI — chat, studio, deployments, settings               │
│                   │ 16)                    │                                                                       │
├───────────────────┼────────────────────────┼───────────────────────────────────────┼────────────────────────────────────────────────────────────┤
│ Gateway           │ TypeScript (Node.js)   │ empyralis-gatcal daemon — WhatsApp, Telegram, browser, supervisor     │
│                   │                        │                                       │ bridge                                                     │
├───────────────────┼────────────────────────┼───────────────────────────────────────────────────────────────────────┤
│ Runtime Kernel    │ Rust                   │ empyralis-runtime-kernel/             │ 53-command JSON-in/JSON-out policy decision engine         │
├───────────────────┼────────────────────────┼───────────────────────────────────────────────────────────────────────┤
│ Supervisor        │ Rust (axum)            │ empyralis-supervisor/                 │ Desktop automation daemon (127.0.0.1:7788)                 │
├───────────────────┼────────────────────────┼───────────────────────────────────────────────────────────────────────┤
│ Mobile            │ TypeScript (Expo RN)   │ mobile/                               │ iOS/Android app                                            │
├───────────────────┼────────────────────────┼───────────────────────────────────────────────────────────────────────┤
│ Tray App          │ Rust/React (Tauri)     │ apps/empyralis-tray/                  │ Desktop system tray companion                              │
├───────────────────┼────────────────────────┼───────────────────────────────────────────────────────────────────────┤
│ Cloud Session Mgr │ TypeScript (Express)   │ cloud-session-manager/                │ Redis-backed Telegram session pool for cloud-hosted bots   │
├───────────────────┼────────────────────────┼───────────────────────────────────────────────────────────────────────┤
│ Platform          │ Next.js 16             │ platform/                             │ Separate marketing website — NOT part of Empyralis         │
│ (Marshal)         │                        │                                                                       │
├───────────────────┼────────────────────────┼───────────────────────────────────────┼────────────────────────────────────────────────────────────┤
│ Shared            │ TypeScript             │ shared/      I contracts, design tokens                               │
├───────────────────┼────────────────────────┼───────────────────────────────────────┼────────────────────────────────────────────────────────────┤
│ Skills            │ Markdown               │ skills/ (9 skstruction templates for the LLM                          │
├───────────────────┼────────────────────────┼───────────────────────────────────────┼────────────────────────────────────────────────────────────┤
│ MCP               │ Python/JSON            │ mcp_server.pyP server for vision-monitor                              │
└───────────────────┴────────────────────────┴───────────────────────────────────────┴────────────────────────────────────────────────────────────┘

---
STEP 2 — MESSAGE ENTRY: How a message enters

Empyralis has SEVEN distinct entry paths, all converging to

Path A: Web Chat (POST /api/turn)

File: server_modules/runtime_runs_api.py:974-1029

@app.post("/turn", dependencies=[Depends(member_dependency)]
async def canonical_turn(request, body, current_user):
    # 1. Reads JSON body
    # 2. Stamps workspace auth
    # 3. Enforces Rust run-api decision
    # 4. Calls turn_ingress_service.start_turn(payload=payload, ...)

The frontend sends:
{
  "channel": "web",
  "actor": {"type": "user", "id": "...", "display_name": "..
  "thread_id": "...", "session_id": "...",
  "message": "user text",
  "execution_mode": "sync",
  "response_mode": "stream",
  "context_hints": {"force_direct_chat": true, "source": "workstation_client"}
}

Frontend proxy: frontend/app/api/[...path]/route.ts → fronteroxy.ts:202 → fetch(upstreamUrl) to Python backend at127.0.0.1:8001.

Path B: Telegram Webhook (POST /channels/telegram/webhook)

File: server_modules/routes_connectors.py:193-198

async def telegram_webhook(request: Request, connector_id: str):
    # Rate-limited via _dispatch_public_studio_webhook()
    # Delegates to connectors_actions.telegram_webhook_canonical(request, connector_id)

This triggers TelegramIngressService.ingest_update() (server_modules/connectors/telegram_ingress_service.py:52) which parses the update, resolves
workspace scope, extracts message, and calls agent_channel_ressage().

Path C: Telegram Hosted Sage Bot (POST /api/sage/telegram-ho

File: server_modules/routes_sage_telegram_hosted.py:88-100

Verifies X-Telegram-Bot-Api-Secret-Token, routes through sagage_turn_adapter.execute_sage_turn() — the unified Sage entrypoint.

Path D: Personal Gateway Channels (WhatsApp, Telegram Personal, Discord Personal)

File: server_modules/agent_channel_router.py:2043 — handle_cloud_channel_inbound()

The local gateway daemon (empyralis-gateway) receives messages via Baileys (WhatsApp Web) or GramJS (Telegram MTProto), then forwards to the cloud API
over WebSocket as channel.inbound events.

Gateway WebSocket protocol: empyralis-gateway/src/protocol/t
interface GatewayChannelInboundPayload {
  channel_key: string;      // "telegram_personal" | "whatsa
  session_id: string;
  remote_jid: string;
  message: {...};           // text, attachments, reply context
  idempotency_key: string;
}

Path E: Discord Bot Gateway Listener

File: server_modules/connectors/discord_bot_runtime_service.py

Started at server boot (server.py:291):
_launch_discord_bot_runtime()
Background threads listen for Discord gateway events. DMs to the bot are routed to Sage.

Path F: Slack / GitHub / Studio Connectors

Webhooks arrive at /api/connectors/.... Parsed by connector-specific modules, then routed through agent_channel_router.route_inbound_channel_message()
— currently an honest stub returning channel_unavailable (li

async def route_inbound_channel_message(...) -> Dict[str, An
    """HONEST STUB — Studio connector channels are NOT yet implemented end-to-end."""
    return {"ok": False, "status": "channel_unavailable", ..

Path G: Scheduled Runs / Cron

Managed via /schedules routes in routes_runs.py. Cron and weeation via run_service.

Message Normalization — ALL paths converge here

File: server_modules/agent_turn.py:119

@dataclass(slots=True)
class AgentTurnRequest:
    tenant_id: str
    workspace_id: str
    thread_id: str
    session_id: str
    channel: str           # "web" | "telegram_personal" | "
    actor: TurnActor       # {type, id, display_name}
    message: str
    attachments: List[TurnAttachment]
    context_hints: Dict[str, Any]
    execution_mode: "sync" | "durable"
    response_mode: "stream" | "artifact" | "channel_reply"
    machine_target: Optional[str]
    policy_context: Dict[str, Any]

Normalization functions:
- agent_turn.py:resolve_run_start_turn_request() — for run start bodies
- agent_turn.py:resolve_direct_chat_turn_request() — for dir
- turn_ingress_service.py:192:_resolve_turn_ingress() — detects payload shape, normalizes
- sage_turn_adapter.py:normalize_sage_inbound() — for Sage cageTurn

Is it the SAME handoff for every channel? YES — with caveats

All paths call ONE of two unified entry points:
1. turn_ingress_service.start_turn() (server_modules/turn_ingress_service.py:269) — for web/API-based entry
2. sage_turn_adapter.execute_sage_turn() (server_modules/saghannel-based Sage entry

Both converge at turn_runtime.execute_agent_turn_request().

---
STEP 3 — THE CORE ENGINE

Dispatch: execute_agent_turn_request()

File: server_modules/turn_runtime.py:39-67

async def execute_agent_turn_request(*, turn_request, current_user, services, ...):
    # Try durable (long-running run) path first
    durable_execution = await run_service.execute_durable_agent_turn_dispatch(
        turn_request=turn_request, current_user=current_user
        services=services.run_execution, ...
    )
    if durable_execution is not None:
        return durable_execution  # Run was dispatched

    # Fall through to direct chat (immediate) path
    return await execute_direct_chat_turn_request(
        turn_request=turn_request, current_user=current_user
        services=services.direct_chat, ...
    )

Two execution paths:
- Durable Run: Long-running task with state machine (queued → planning → executing → ...). LLM call in runs_engine.py:92 via
generate_with_candidate_failover()
- Direct Chat: Immediate conversational turn. LLM call in direct_chat_generation_service.py:547 via stream_provider_backed_direct_chat()

Model/Provider Selection

File: server_modules/provider_profiles.py:460-579 — PROVIDER_CATALOG

Supported providers: openai, openai-codex, anthropic, claude_code_cli, gemini, vertex, groq, openrouter, deepseek, qwen, mistral, ollama, ollama_cloud,
azure_openai, bedrock, xai, custom_openai_compatible

Resolution chain:
1. Session model preference stored in memory dict (direct_chat_context_service.py:239)
2. Per-workspace default from PROVIDER_CATALOG
3. Global default: openai / gpt-5.4

resolve_provider_adapter() (provider_profiles.py:1894):
def resolve_provider_adapter(provider, credentials=None):
    provider_id = normalize_provider_id(provider)
    auth_mode = normalize_auth_mode(provider, credentials=cr
    adapter_key = provider_id
    if provider_id == "anthropic" and auth_mode == "local_cl
        adapter_key = "claude_code_cli"
    adapter = PROVIDER_ADAPTERS.get(adapter_key)
    return adapter

Credential Failover

File: server_modules/runs_engine.py:92-214 — generate_with_candidate_failover()

def generate_with_candidate_failover(state, context, log_queue, system_prompt, user_input):
    for candidate in candidates:  # credential profiles
        adapter = resolve_provider_adapter(candidate.provider, candidate.credentials)
        for model_attempt in _candidate_model_attempts(candi
            try:
                reply = adapter.generate(system_prompt, user
                return reply  # Success
            except RateLimitError:
                continue  # Try next fallback model
        _mark_profile_failure(candidate)  # Move to next cre

Default: NO FALLBACK — disable_fallback defaults to true in 7-3501:
# "ONE AI ROAD. NO FALLBACK. EVER."

System Prompt Building

Sage path (sage_agent_runtime_service.py:2469-2618):
instruction_bundle = sage_instruction_compiler_service.build
system_prompt = f"{instruction_bundle.system_prompt}{sage_surface_guardrails}{attachment_context}{mcp_tool_inventory}"

Direct chat path (direct_chat_runtime_service.py:1465-1486):
raw_system_prompt = services.build_direct_chat_system_promptty=..., tools=...)
system_prompt = direct_chat_prompt_service.combine_workspace_context(
    system_prompt=raw_system_prompt,
    workspace_context_text=workspace_context_text,
    identity_guardrail=identity_guardrail,
)

Components: identity profile (USER.md), memory context (semantic search), skills catalog, MCP inventory, conversation history (last 10 turns),
heartbeat snapshot.

Tool Selection

Tools are assembled dynamically — there is NO single tool reuntime_service.py:1120-1154:

def _direct_tool_bundle(*, workspace_id, provider):
    # 1. Connector tools (from workspace capabilities)
    tools.extend(_build_direct_chat_tools(tool_capabilities)
    # 2. Hardware tools (if local worker online)
    tools.extend(_build_local_direct_chat_tools(availability
    # 3. Built-in tools (web, memory, browser, etc.)
    tools.extend(_build_builtin_direct_chat_tools())
    return tools

34+ tools across categories: file__read/write, shell__exec, screenshot__capture, computer__* (14 tools), web__search/fetch, memory_search/get/update,
browser__* (12 tools), generate_image, http_request, llm__ta__action, send_image.

The LLM decides which tool to call. There is NO regex/keyworssed via tools=list(tools) to the generation service, whichsends them as function definitions in the LLM API call.

The Agentic Loop

File: server_modules/direct_chat_generation_service.py:796-1649

for iteration in range(max_iterations):
    # 1. Strip tools on synthesis (DeepSeek fix)
    if executed_any_tools:
        metadata["tools"] = []; context["tools"] = []

    # 2. Call LLM with streaming
    for event in services.generate_chat_reply_stream_with_provider_fallback(
        context=context, metadata=metadata,
        user_goal=current_prompt, system_prompt=system_prompt,
        prior_messages=messages,
    ):
        # accumulate text, detect tool_calls

    # 3. Extract tool calls from LLM response
    iteration_tool_calls = event.get("tool_calls", [])

    # 4. Loop detection (3 consecutive identical calls → break)
    loop_detected = any(
        services.record_direct_tool_signature(session_key, tc)
        for tc in iteration_tool_calls
    )
    if loop_detected:
        yield tool_loop_detected intervention
        return

    # 5. Approval check
    if approval_required:
        yield approval card and return

    # 6. Execute tools (ThreadPoolExecutor, per-category tim
    for tool_call in iteration_tool_calls:
        tool_result = thread_pool.submit(services.execute_si
        # Sanitize, trace, append to conversation_messages

    # 7. Feed tool results back → loop to step 1
    conversation_messages.append({"role": "tool", "tool_call)
    current_prompt = "Based on the tool results above, provide a clear answer..."

---
STEP 4 — TOOLS AND SKILLS

Tool Definition Format

File: server_modules/skills_service.py:36

@dataclass(slots=True)
class ToolDescriptor:
    tool_name: str           # e.g. "web__search"
    label: str               # human label
    connector_id: str        # source connector
    action_id: str           # action identifier
    description: str         # LLM-visible description
    capability_id: str       # gateway capability
    risk_level: str          # "low"|"medium"|"high"|"critic
    requires_approval: bool
    parameters: Dict[str, Any]  # JSON Schema for function c
    requires_runtime: bool
    metadata: Dict[str, Any]

Converted to LLM function schema via _tool_payload_from_desc05):
{
    "name": "web__search",
    "description": "Search the web, return top 5 results",                                                                                          "parameters": {"type": "object", "properties": {"query":ed": ["query"]},
    "risk_level": "low",                                                                                                                            "action_class": "read",
    ...                                                                                                                                         }
                                                                                                                                                Complete Tool Inventory
                                                                                                                                                Local Tools (14) — require gateway/worker online:
file__read, file__write, shell__exec, screenshot__capture, computer__ocr, computer__click, computer__type, computer__applescript,               computer__clipboard_read, computer__clipboard_write, computes, computer__launch_app, computer__speak
                                                                                                                                                Built-in Tools (25+) — always available:
hardware__action, memory_search, memory_get, memory_update, memory_stage_edit, memory_apply_edit, memory_append_daily_note, memory_stage_consolimemory_consolidate_daily_notes, memory_list_versions, memoryh, web__fetch, llm__task, http_request, generate_image,sage_service__list_state, sage_service__update_profile, sage_service__create_entry, browser__navigate, browser__screenshot, browser__observe,   browser__click, browser__fill, browser__extract_text, browsexecute_js, browser__new_tab, browser__switch_tab,browser__download_file, browser__start_intercept, browser__stop_intercept, browser__pdf, send_image                                             
Skills vs Tools                                                                                                                                 
Skills are Markdown instruction files in skills/<name>/SKILL.md. They're injected into the system prompt as context teaching the LLM how to use specific capabilities. They are NOT executable — they're pro

9 skills: browser, business-skill-template, code-runner, filmory-manager, telegram-bot, vision-monitor, web-search

Tools are compiled Python dataclasses that the LLM calls viate real code and return results.

Skill loading: server_modules/installed_skills.py:49 — loads
return [
    ("workspace", workspace_skills_root()),   # ./.orion-sta
    ("global", global_skills_root()),          # ~/.orion-stack/skills/
    ("bundled", bundled_skills_root()),        # ./skills/
]

Security scanning: skill_scanner.py scans Python AST for eval, exec, os.system, subprocess(shell=True), getattr abuse, dynamic imports. JS/TS scanned
for child_process, eval, Function constructor.

Tool Execution (Sandboxing)

There is NO Docker/container sandboxing in the Python server
1. Gateway protocol — gateway_execution_service.execute_tool_via_gateway() sends over WebSocket to the local gateway daemon
2. Gateway → Supervisor — the gateway routes to empyralis-su POST for desktop control
3. Direct subprocess — some tools run subprocess.run() directly on the server

Supervisor sandboxing (empyralis-supervisor/src/capabilities/shell.rs:19-257):
- Hard-blocked commands: rm -rf /, mkfs.*, dd, fork bombs, s
- Protected paths: ~/.empyralis/state/vault, ~/.ssh, ~/.gnupg
- Symlink bypass detection
- Untrusted mode: only safe read-only commands allowed

Rust kernel sandbox (empyralis-runtime-kernel/src/sandbox.rs:1-112):
- Docker containers: --read-only, --network none, --cap-droprivileges:true
- Memory limit 512MB, timeout 25s, non-root user
- But this is policy output only — actual Docker execution i

---
STEP 5 — /COMMANDS

Web Direct Chat Commands

File: server_modules/direct_chat_response_service.py:52 — slash_command_payload()

┌────────────────────┬─────────────────────────────────────────────────────────────────────┐
│      Command       │                               Action   │
├────────────────────┼─────────────────────────────────────────────────────────────────────┤
│ /status            │ Report AI readiness, connected provids │
├────────────────────┼─────────────────────────────────────────────────────────────────────┤
│ /memory            │ Return memory facts for workspace      │
├────────────────────┼─────────────────────────────────────────────────────────────────────┤
│ /forget <key>      │ Delete a memory entry                  │
├────────────────────┼─────────────────────────────────────────────────────────────────────┤
│ /model <name>      │ Set chat model for the session         │
├────────────────────┼─────────────────────────────────────────────────────────────────────┤
│ /clear             │ Clear conversation history for the th  │
├────────────────────┼─────────────────────────────────────────────────────────────────────┤
│ /help              │ Show slash command help text           │
├────────────────────┼─────────────────────────────────────────────────────────────────────┤
│ /export-trajectory │ Export trajectory via diagnostics API  │
├────────────────────┼─────────────────────────────────────────────────────────────────────┤
│ /thinking <level>  │ Set thinking effort (off, minimal, lo  │
└────────────────────┴─────────────────────────────────────────────────────────────────────┘

Parsing: server_modules/direct_chat_context_service.py:227:
def parse_slash_command(message: str) -> Dict[str, str]:
    normalized = message.strip()
    if not normalized.startswith("/"):
        return {}
    tokens = normalized.split()
    command = tokens[0][1:].strip().lower()
    remainder = normalized[len(tokens[0]):].strip()
    return {"command": command, "remainder": remainder}

Sage Channel Commands (Telegram/Discord/WhatsApp)

File: server_modules/sage_command_dispatcher.py:18-182

SUPPORTED_COMMANDS = ["/compact", "/new", "/main", "/approve", "/deny", "/help", "/memory"]

async def dispatch_command(*, command, workspace_id, thread_id, channel_origin, sender_id):
    cmd = text.split()[0].strip().lower()
    if cmd == "/compact":    return await _handle_compact(workspace_id, thread_id)
    if cmd == "/new":        return await _handle_new(worksp
    if cmd == "/main":       return await _handle_main(workspace_id, channel_origin)
    if cmd == "/approve":    return await _handle_approve(wo
    if cmd == "/deny":       return await _handle_approve(workspace_id, approved=False)
    if cmd == "/help":       return SAGE_HELP_TEXT
    if cmd == "/memory":     return await _handle_memory(workspace_id)
    return None  # Not a command — treat as normal message

Command Dispatch Flow

There is no single command registry. Commands are dispatchedchains:
1. Web: direct_chat_entry_policy_service.py → slash_command_payload()
2. Channels: sage_command_dispatcher.dispatch_command() befo
3. Gateway: agent_channel_router.py → personal_channel_thread_command_service.py

Model Switching Propagation

/model gpt-5 writes to an in-memory dict (direct_chat_context_service.py:239):
store[session_key] = {"provider": "openai", "model": "gpt-5"
Session key: "{workspace_id}:{chat_id}". On next message, session_model_preference() reads this dict. Propagation across channels depends on whether
channels share the same session key. Typically they DON'T — ion key, so model preference is per-channel, not global.

---
STEP 6 — TERMINAL / CLI MODE

Empyralis does NOT have a built-in terminal TUI like OpenClaw's openclaw terminal. The closest equivalent:

1. main.py — standalone CLI script that calls OpenAI directly via urllib. Not connected to the Empyralis server. A prototype/demo.
2. python_engine/cognitive_loop.py — has a CLI mode that runoop. This is the "Agency OS" — it connects to the EmpyralisAPI as a client.
3. Gateway daemon — empyralis-gateway runs as a persistent pe cloud API over WebSocket. Not interactive.
4. Supervisor — empyralis-supervisor is an HTTP daemon, not a CLI.

Verdict: No terminal chat mode equivalent to OpenClaw's openclaw tui. Messages enter through channels (Telegram, Discord, web UI, mobile app) or the
HTTP API.

---
STEP 7 — WEB UI

Frontend (frontend/)

Framework: Next.js 16.2.6 + React 19.2.3

Key routes: /login, /signup, /onboarding, /(account)/w/[workspaceId]/chat (main chat), /sage, /memory, /studio, /deploy, /hardware, /integrations,
/settings, /inbox, /gateway

Communication: All API calls go through Next.js proxy → Pyth
- frontend/app/api/[...path]/route.ts → control-plane-proxy.ts → fetch(upstreamUrl) to 127.0.0.1:8001
- Chat uses SSE streaming via submitTurnStream() (frontend/lnt.ts:2610)
- Notifications use EventSource at /api/notifications?stream=true

Same pipeline as channels? YES. The web chat goes through POST /api/turn — the EXACT same endpoint as Telegram webhook messages after normalization.
The difference is channel: "web" and response_mode: "stream"

Control UI

There is no separate admin control UI like OpenClaw's controory is a completely separate Next.js app for a lubricantscompany's marketing website — NOT part of Empyralis.

---
STEP 8 — SESSION / THREAD MANAGEMENT

Session Storage

File: server_modules/session_manager/manager.py:191 — Empyra

Sessions are stored in a SQLite database via runtime_state_session_id, actor_key, workspace_id, user_id, status,runtime_options, meta.

Session ID Format

Plain strings, typically "{workspace_id}:{chat_id}" or user-provided. Per-actor locking via SessionActorQueue (session_manager/actor_queue.py:21):

@contextmanager
def claim(self, actor_key: str) -> Iterator[None]:
    token = self._normalize_actor_key(actor_key)  # lowers and strips
    with self._global_lock:
        lock = threading.Lock()  # one lock per actor
    lock.acquire()
    try:
        yield  # Only one turn per actor at a time
    finally:
        lock.release()

Cross-Channel Sessions

NOT cross-channel by default. Each channel derives its own snnels (Telegram hosted, Discord bot) all use the samethread_id: "sage-main" and workspace, so they share conversation history via the thread store.

Conversation History

Stored as turns in the thread store (thread_service). Last SAGE_THREAD_MAX_TURNS (10) turns are loaded for context. History is compacted when
approaching context window limits.

---
STEP 9 — PROVIDER / MODEL MANAGEMENT

Provider Catalog

File: server_modules/provider_profiles.py:460-579

18 providers with auth modes (api_key, oauth_token, local_cli, access_token). Default: openai / gpt-5.4.

Where the Active Model is Stored

In-memory dict store: Dict[str, Dict[str, Optional[str]]] keyed by session_key (direct_chat_context_service.py:239). NOT persisted across server
restarts.

Model Switch Propagation

When user runs /model gpt-5, writes store["workspace:chat"][age reads via session_model_preference(). Does NOT propagateto other channels unless they share the same session key.

Provider Adapter Pattern

File: server_modules/provider_profiles.py:1943

class ProviderAdapter:
    provider_id = ""
    def validate(self, credentials): ...
    def list_models(self, credentials): ...
    def generate(self, system_prompt, user_input, model, credentials): ...

Concrete adapters: OpenAIAdapter (line 2086), AnthropicAdapter (line 2533), ClaudeCodeCLIAdapter (line 2599), OpenAICompatibleAdapter (line 2166),
GeminiAdapter, VertexAdapter, OllamaCloudAdapter (line 2333)

Fallback

Default is NO FALLBACK (disable_fallback=true). Optional falgent. Failover across credential profiles viagenerate_with_candidate_failover() in runs_engine.py:92.

---
STEP 10 — GATEWAY ARCHITECTURE

What is the Gateway?

A persistent local daemon (empyralis-gateway/, Node.js) thatIt connects to the Empyralis cloud API via WebSocket and actsas the local control-plane bridge.

Three responsibilities:
1. Personal chat — WhatsApp (Baileys) + Telegram (GramJS) cl
2. Desktop control — routes tool.invoke requests to empyralis-supervisor via HMAC-signed HTTP
3. Browser automation — spawns a Python child process (Playw

Gateway Protocol

File: empyralis-gateway/src/protocol/types.ts

Protocol version v1alpha2. JSON frames over WebSocket. Max f

Request types: gateway.connect, gateway.heartbeat, gateway.pateway.disconnect, tool.invoke, tool.interrupt,channel.outbound

Event types: gateway.hello, gateway.presence, channel.inbound

Message Routing

The GatewayCapabilityRouter (empyralis-gateway/src/supervisor/capability-router.ts:73-141) dispatches tool.invoke frames:

// Priority order:
1. Browser runtime        → Python Playwright worker
2. External agent proxy   → third-party agents
3. Personal channel runtimes → WhatsApp/Telegram send
4. Supervisor             → shell, filesystem, screenshot, computer_control

Agent-Channel Binding

Configured per workspace. Each gateway pairs with a workspace. Channels are bound to the Sage agent. Personal channels (WhatsApp, Telegram DM) each use
their own runtime within the gateway.

Outbound Reply Routing

Replies go back through the originating channel via:
1. dispatch_approved_personal_channel_outbound() (agent_channel_router.py:1812)
2. gateway_protocol_service.dispatch_channel_outbound() — se gateway
3. Gateway delivers via Baileys (WhatsApp) or GramJS (Telegram)

For Sage Hosted Telegram: replies go via HTTP to the cloud session manager, which relays through GramJS.

---
FULL PIPELINE (ASCII)

┌───────────────────────────────────────────────────────────
│ CHANNEL ADAPTERS                                                    │
│ ┌──────────────┐ ┌──────────────┐ ┌──────────────┐ ┌──────
│ │Telegram      │ │WhatsApp      │ │Discord       │ │Web Chat     │ │
│ │Webhook       │ │Gateway WS    │ │Gateway Bot   │ │POST /
│ │routes_connec-│ │empyralis-    │ │discord_bot_  │ │turn         │ │
│ │tors.py:193   │ │gateway WS    │ │runtime:bg    │ │runtim
│ │              │ │client:858    │ │thread        │ │_api.py:974  │ │
│ └──────┬───────┘ └──────┬───────┘ └──────┬───────┘ └──────
│        │                │                │                │        │
│        │    ┌───────────┴───────────┐    │
│        │    │agent_channel_router   │    │                │        │
│        │    │route_inbound_channel  │    │
│        │    │_message() STUB        │    │                │        │
│        │    └───────────┬───────────┘    │
│        │                │                │                │        │
│        ▼                ▼                ▼
│  ┌─────────────────────────────────────────────────────────────┐   │
│  │              NORMALIZATION LAYER
│  │  AgentTurnRequest dataclass (agent_turn.py:119)              │   │
│  │  ┌────────────────┐  ┌─────────────────────────────────
│  │  │turn_ingress    │  │sage_turn_adapter                 │   │   │
│  │  │_service        │  │.execute_sage_turn()
│  │  │.start_turn()   │  │(sage_turn_adapter.py:41)         │   │   │
│  │  │(turn_ingress   │  │
│  │  │_service:269)   │  │                                  │   │   │
│  │  └───────┬────────┘  └──────────────┬──────────────────
│  └──────────┼──────────────────────────┼───────────────────────┘   │
│             │                          │
│             └──────────┬───────────────┘                           │
│                        ▼
│  ┌─────────────────────────────────────────────────────────────┐   │
│  │              GOVERNANCE GATE
│  │  turn_ingress_service → Rust kernel run-api-decision         │   │
│  │  unified_governance_gate.evaluate_action_policy()
│  │  kill_switch_gate.evaluate_kill_switch()                     │   │
│  │  capability_risk_classifier.classify_capability_risk()
│  └──────────────────────┬──────────────────────────────────────┘   │
│                         ▼
│  ┌─────────────────────────────────────────────────────────────┐   │
│  │              EXECUTION SWITCHBOARD
│  │  turn_runtime.execute_agent_turn_request()                   │   │
│  │  (turn_runtime.py:39)
│  │                                                              │   │
│  │  ┌──────────────────┐     ┌────────────────────────────
│  │  │ DURABLE RUN      │     │ DIRECT CHAT                  │  │   │
│  │  │ run_service.py   │     │ direct_chat_service.py
│  │  │ State machine:   │     │ execute_direct_chat_turn_    │  │   │
│  │  │ queued→planning  │     │ request()
│  │  │→executing→done   │     │                              │  │   │
│  │  └────────┬─────────┘     └──────────────┬─────────────
│  └───────────┼──────────────────────────────┼──────────────────┘   │
│              │                              │
│              └──────────────┬───────────────┘                      │
│                             ▼
│  ┌─────────────────────────────────────────────────────────────┐   │
│  │              LLM GENERATION LAYER
│  │                                                              │   │
│  │  RUN PATH:                     DIRECT CHAT PATH:
│  │  runs_engine.py:92             direct_chat_generation_       │   │
│  │  generate_with_candidate_      service.py:547
│  │  failover()                    stream_provider_backed_       │   │
│  │  → adapter.generate()          direct_chat()
│  │                                → generate_chat_reply_        │   │
│  │  SAGE PATH:                    stream_with_provider_
│  │  sage_agent_runtime_           fallback()                    │   │
│  │  service.py:2288              (orion_local_worker_llm)
│  │  handle_sage_chat()            → provider API call           │   │
│  │  → _run_sage_action_loop_v3    → streaming SSE/JSON
│  │  → stream_provider_backed_                                   │   │
│  │    direct_chat()
│  └──────────────────────┬──────────────────────────────────────┘   │
│                         ▼
│  ┌─────────────────────────────────────────────────────────────┐   │
│  │              TOOL EXECUTION LOOP
│  │  direct_chat_generation_service.py:796-1649                  │   │
│  │
│  │  for iteration in range(max_iterations):                     │   │
│  │    LLM call → extract tool_calls
│  │    → loop detection (3 repeat → break)                       │   │
│  │    → approval check →
│  │    → ThreadPoolExecutor → execute tool                       │   │
│  │    → append tool result → loop back
│  └──────────────────────┬──────────────────────────────────────┘   │
│                         ▼
│  ┌─────────────────────────────────────────────────────────────┐   │
│  │              RESPONSE DELIVERY
│  │                                                              │   │
│  │  Web: SSE streaming → frontend renders markdown
│  │  Telegram: gateway_execution_service → channel.outbound      │   │
│  │  WhatsApp: gateway_execution_service → Baileys send
│  │  Discord: discord_bot_runtime_service → gateway API          │   │
│  │  Slack/Studio: STUB (channel_unavailable)
│  └──────────────────────────────────────────────────────────────┘   │
└───────────────────────────────────────────────────────────

---
KEY ARCHITECTURAL DIFFERENCES: Empyralis vs OpenClaw

┌──────────────────────┬─────────────────────────────────────────────────────────────┬─────────────────────────────────────────────────────────────┐
│      Dimension       │                          OpenClaw                         Empyralis                          │
├──────────────────────┼─────────────────────────────────────────────────────────────┼─────────────────────────────────────────────────────────────┤
│ Language             │ TypeScript (mono)                  thon + TS + Rust (polyglot)                               │
├──────────────────────┼─────────────────────────────────────────────────────────────┼─────────────────────────────────────────────────────────────┤
│ Plugin System        │ definePluginEntry() — everything is unified plugin system; tools/skills/providers are        │
│                      │                                                             │ modules                                                     │
├──────────────────────┼──────────────────────────────────────────────────────────────────────────────────────────────┤
│ Agent Runner         │ @earendil-works/pi-ai (embedded)                            │ Custom orion_local_worker_llm.py (4516 lines)               │
├──────────────────────┼──────────────────────────────────────────────────────────────────────────────────────────────┤
│ Tool Loop            │ Pi/Codex managed                                            │ Custom for-loop in stream_provider_backed_direct_chat       │
├──────────────────────┼──────────────────────────────────────────────────────────────────────────────────────────────┤
│ Gateway              │ Central HTTP+WS server (gateway process)                    │ Local daemon + cloud API (split architecture)               │
├──────────────────────┼──────────────────────────────────────────────────────────────────────────────────────────────┤
│ Desktop Control      │ Via MCP/plugins                                             │ Dedicated Rust supervisor with HMAC signing                 │
├──────────────────────┼──────────────────────────────────────────────────────────────────────────────────────────────┤
│ Policy Engine        │ In-process TypeScript                                       │ Separate Rust CLI (53 commands, JSON-in/JSON-out)           │
├──────────────────────┼──────────────────────────────────────────────────────────────────────────────────────────────┤
│ Sandboxing           │ Pi sandbox + Codex App Server                               │ Rust kernel policy → Docker (policy only, execution in      │
│                      │                                    thon)                                                     │
├──────────────────────┼─────────────────────────────────────────────────────────────┼─────────────────────────────────────────────────────────────┤
│ Session Store        │ JSON file per agent                Lite + in-memory dict                                     │
├──────────────────────┼─────────────────────────────────────────────────────────────┼─────────────────────────────────────────────────────────────┤
│ Cross-Channel        │ Same session store, different sessir-channel session keys, shared thread store               │
│ Sessions             │                                                             │                                                             │
├──────────────────────┼──────────────────────────────────────────────────────────────────────────────────────────────┤
│ Command System       │ defineChatCommand() registry                                │ If/elif chains in 3 places                                  │
├──────────────────────┼──────────────────────────────────────────────────────────────────────────────────────────────┤
│ Web UI               │ SPA in dist (control panel)                                 │ Next.js 16 SPA (workspace + chat)                           │
├──────────────────────┼──────────────────────────────────────────────────────────────────────────────────────────────┤
│ Mobile               │ None                                                        │ Expo React Native app                                       │
├──────────────────────┼──────────────────────────────────────────────────────────────────────────────────────────────┤
│ Channel Coverage     │ Telegram, Discord, Signal, iMessage, Slack, Mattermost,     │ Telegram, WhatsApp, Discord (live), Slack/Studio (stubbed)  │
│                      │ IRC, Tlon                                                                                    │
├──────────────────────┼─────────────────────────────────────────────────────────────┼─────────────────────────────────────────────────────────────┤
│ Model Fallback       │ Built-in runWithModelFallback()    nerate_with_candidate_failover() — disabled by default    │
├──────────────────────┼─────────────────────────────────────────────────────────────┼─────────────────────────────────────────────────────────────┤
│ Context Compaction   │ Preflight + reactive via runPrefligoactive + reactive via _compact_now_proactive() with      │
│                      │                                                             │ retry loop                                                  │
└──────────────────────┴──────────────────────────────────────────────────────────────────────────────────────────────┘

