# Phase 0 — Ground Truth Report (Main Agent)

> Read-only. No code was changed. Everything below comes from the actual code,
> not from docs or PLATFORM.md.

---

## 1. The Main Agent's Live Path Today (End-to-End Trace)

Here is one real message flowing through the system, traced through the actual
files and functions:

### Step-by-step: a Telegram message to Sage

```
Telegram user sends "What's on my screen?"
        │
        ▼
[1] INBOUND — routes_sage_telegram_hosted.py, line 88
    telegram_webhook() receives the HTTP POST from Telegram
    → verifies HMAC signature (line 95)
    → parse_telegram_update() extracts text + sender info (line 103)
    → handle_inbound_message() maps chat_id → workspace (line 107)
        │
        ▼
[2] COMMAND CHECK — sage_command_dispatcher.py, line 164
    dispatch_command() checks if the message is a /command
    (/help, /compact, /new, /approve, /deny, /memory)
    If it is: returns reply immediately, skips the LLM
    If not: returns None, continues to the LLM path
        │
        ▼
[3] NORMALIZE — channel_adapter.py, line 68
    normalize_sage_inbound() packs everything into a NormalizedSageTurn
    (workspace_id, message, channel_origin="telegram_hosted", sender info)
    Channel origin is stored as routing metadata ONLY — never reaches the prompt
        │
        ▼
[4] MAIN AGENT RUNTIME — sage_agent_runtime_service.py, line 2192
    handle_sage_chat() is the main entry point
    → loads profile context (custom instructions, preferences)
    → loads memory context (past conversations, facts)
    → loads attachment context (photos, documents from the message)
    → loads heartbeat snapshot (connected tools, agent computer status)
    → resolves identity links (cross-channel sender matching)
    → builds the system prompt via sage_instruction_compiler_service
    → resolves the model provider (DeepSeek by default, or BYOK)
        │
        ▼
[5] ACTION LOOP — sage_agent_runtime_service.py, line 1447
    _run_sage_action_loop_v3() decides: text-only reply, or tool-using loop?
    → checks if Agent Computer is connected (line 1477)
    → if tool use is needed, enters the action loop:
        │
        ▼
[6] LLM CALL — direct_chat_generation_service.py
    stream_provider_backed_direct_chat() calls the model (DeepSeek / Anthropic / etc.)
    The model can return:
      - a text reply (delivered directly)
      - a tool_call (e.g., hardware__action, browser__navigate, shell__exec)
        │
        ▼
[7] TOOL EXECUTION — hardware_action_broker_service.py, line 623
    execute_hardware_action() routes the tool call to the right target:
      ├── user_device_gateway → WebSocket → empyralis-gateway (on user's Mac)
      │       → capability router → empyralis-supervisor (Rust, localhost:7788)
      │       → enigo crate for mouse/keyboard, /bin/zsh for shell
      ├── empyralis_cloud_computer → virtual sandbox (BrowserBase/E2B/Daytona/Docker)
      └── self_hosted_node → enqueue command → VPS worker picks it up
        │
        ▼
[8] REPLY — routes_sage_telegram_hosted.py, line 181-187
    send_sage_reply() delivers the response back to Telegram
    → filter_outbound_reply() checks for [SILENT] marker
    → Telegram Bot API sends the message
```

### Key files involved (in order of importance to the Main Agent)

| File | Lines | What it does |
|---|---|---|
| `sage_agent_runtime_service.py` | 3,055 | Sage chat handler — system prompt, LLM call, action loop |
| `hardware_action_broker_service.py` | 909 | Central dispatcher — routes tool calls to gateway/cloud/VPS |
| `agent_channel_router.py` | 2,111 | Channel routing for deployed agents (not used for owner Sage) |
| `gateway_protocol_service.py` | 2,207 | WebSocket protocol between cloud and user's local machine |
| `agent_turn.py` | 1,801 | Turn contract, request normalization |
| `policy_service.py` | 1,629 | Policy evaluation for tool calls |
| `sage_command_dispatcher.py` | ~350 | /command handling shared across channels |
| `channel_adapter.py` | 132 | Normalize inbound → canonical form |

---

## 2. Does the Main Agent Already Run on the VPS End to End Right Now?

**No — not in the way the build brief describes.**

Here's what actually happens today:

- **The LLM reasoning (the "brain") runs on the cloud server** for ALL profiles.
  The model call happens in `direct_chat_generation_service.stream_provider_backed_direct_chat()`
  on the cloud control plane. There is NO code path where the reasoning loop runs
  on the VPS or local machine.

- **Tool actions can execute on three different targets**, but they are shipped
  one at a time from the cloud:

  | Target | How it works | Governance on node? |
  |---|---|---|
  | **User's Mac** (gateway) | WebSocket from cloud → empyralis-gateway → supervisor | Partial — kill switch enforced, approval checked before dispatch |
  | **Cloud Computer** (sandbox) | Virtual runtime (BrowserBase, E2B, Docker, etc.) | Yes — cloud-side checks only |
  | **Self-Hosted VPS** | Cloud enqueues command → VPS worker picks it up from queue | Cloud-side checks before enqueue; node-side unclear |

- **The owner's belief that "the agent runs on the VPS" is partially correct for
  tool execution but wrong for reasoning.** The reasoning never leaves the cloud
  today. The VPS path uses a queue model (cloud enqueues, VPS worker polls)
  rather than direct SSH for the main adapter path. There ARE direct SSH
  functions (`vps_ssh_execute`, `vps_ssh_file_read`, `vps_ssh_file_write`)
  defined in `hardware_runtime_target_resolver.py` but the main adapter
  (`self_hosted_node_adapter.py`) does NOT use them — it enqueues commands
  instead.

- **Bottom line:** The Main Agent today is a cloud-resident brain that can reach
  out to hardware for tool execution. It is NOT "a node-resident agent that
  reasons and acts locally." The target architecture in the build brief (agent
  runtime fully on the node, cloud dispatches tasks not clicks) is NOT the
  current reality.

---

## 3. Where Reasoning Currently Lives (Per Runtime Profile)

| Runtime Profile | Reasoning (LLM call) | Tool Execution | Hardware Action Broker on path? |
|---|---|---|---|
| **My Computer** (user_device_gateway) | ☁️ Cloud server | 💻 User's Mac via WebSocket | **YES** — per-action shipping via `tool.invoke` frames |
| **Self-Hosted Agent** (VPS) | ☁️ Cloud server | 🖥️ VPS via queue (worker polls) | **YES** — but via enqueue, not direct SSH |
| **Cloud Computer Agent** (sandbox) | ☁️ Cloud server | ☁️ Cloud sandbox (BrowserBase/E2B/etc.) | **YES** — virtual computer runtime adapter |
| **Cloud default** (no hardware) | ☁️ Cloud server | ❌ Degraded — text-only | N/A |

**The Hardware Action Broker is on the local path for ALL profiles that use
hardware.** It is the central dispatcher at `hardware_action_broker_service.py`
line 623. For the My Computer profile, individual low-level actions (mouse move,
click, keystroke, screenshot, shell command) ARE shipped from cloud to node one
at a time — exactly the "chatty, slow, and complex" model the build brief wants
to replace.

The full chain for a single click on the user's Mac:
```
Cloud LLM decides to click
  → hardware_action_broker_service.execute_hardware_action()
    → gateway_adapter.execute_gateway_action()
      → gateway_execution_service.execute_tool_via_gateway()
        → gateway_protocol_service.dispatch_tool_invoke()  [WebSocket frame]
          → empyralis-gateway (TypeScript, on Mac) receives frame
            → capabilityRouter.handleToolInvoke()
              → supervisorClient.execute()  [HTTP POST to localhost:7788]
                → empyralis-supervisor (Rust, on Mac) receives request
                  → enigo crate physically moves mouse and clicks
```

---

## 4. Channel Inventory

### Working end-to-end today:

| Channel | Path | How it connects | Status |
|---|---|---|---|
| **Telegram Hosted Bot** | Path D (standalone) | Webhook or polling, deep-link pairing | ✅ Fully working |
| **Telegram Personal** | Path B (gateway) | Via Agent Computer gateway on user's Mac | ✅ Working |
| **WhatsApp Personal** | Path B (gateway) | Via Agent Computer gateway, QR pairing | ✅ Working |
| **WhatsApp Twilio** | Path C (connector webhook) | Twilio webhook for deployed agents | ✅ Working |
| **Slack** | Path C (connector) + Path A (simple) | OAuth v2, Events API, chat.postMessage | ✅ Working |
| **Discord Bot** | Path C (connector webhook) | Interaction webhook with signature verify | ✅ Working |
| **GitHub** | Path C (connector webhook) | Webhook for push/PR/issue events | ✅ Working |
| **iMessage** | Path A + Path B (bridge) | BlueBubbles Mac bridge required | ✅ Working |
| **WeChat** | Path A + Path B (bridge) | Bridge + simple route | ✅ Working |
| **Signal** | Path B (bridge only) | Local bridge via Agent Computer | ✅ Working |

### Not yet implemented:

| Channel | Status |
|---|---|
| **Discord Personal** | Enum value exists, no handler code |
| **Apple Messages Business** | Stubbed — stage is "planned", `launch_allowed: False` |

### Web endpoint:

The web UI (`frontend/`) is the **owner's control room** (Next.js app) — it is
NOT a consumer-facing channel endpoint. No public web endpoint for consumers to
message the agent exists today.

### Three different message-flow architectures (this matters):

The project has **three completely separate inbound pipelines** plus a fourth
standalone one for the hosted Telegram bot. They share almost no code:

1. **Path A — Simple Sage Chat** (WeChat, iMessage, Slack simple route, Telegram Hosted):
   HTTP POST → command check → `normalize_sage_inbound()` → `handle_sage_chat()` → reply
   
2. **Path B — Personal Gateway Channels** (WhatsApp Personal, Telegram Personal, Signal/iMessage/WeChat local bridges):
   WebSocket from gateway → `handle_gateway_channel_inbound()` → handler registry → Sage bridge → approval check → dispatch
   
3. **Path C — Connector Webhooks** (Telegram Bot connector, WhatsApp Twilio, Slack connector, Discord, GitHub):
   HTTP POST → connector-specific ingress → `route_inbound_channel_message()` → full routing pipeline (concurrency, quotas, policy overlays) → specialist agent turn
   
4. **Path D — Telegram Hosted Bot** (standalone, in-memory state):
   Same as Path A but with its own pairing, persistence, and delivery

**This is a tangled area.** Four paths where one should suffice for the Main
Agent. Path C (connector webhooks) is designed for deployed specialist agents,
not the owner's Main Agent — but some code paths blur this line.

---

## 5. Governance Reality

### What is actually enforced today:

| Mechanism | Defined? | Wired into execution? | How? |
|---|---|---|---|
| **Capability risk classifier** | ✅ Yes — 57+ capabilities classified (low/medium/high/critical) | ✅ Enforced at 3 call sites | `classify_capability_risk()` called explicitly in gateway path, Sage path, approval service |
| **Approval gates** | ✅ Yes — pre-execution decision envelope | ✅ Enforced but spot-check | `decide_agent_computer_action()` called in Sage path (line 527); separate inlined checks in gateway path |
| **Kill switch** | ✅ Yes — TWO layers | ✅ Fully enforced | `kill_switch_gate.py` (emergency stop) + `safe_mode_service.py` (scoped control). Checked at all gateway entries, tool broker, and Sage path |
| **Audit trail / transparency** | ✅ Yes — 4 layers | ✅ Recording real events | `activity_ledger_service` (30+ call sites), `agent_trace_service` (full replay), `agent_action_metering_service` (per-action), `agent_transparency_events` (visibility controls) |
| **Policy rules** (filesystem scope, domain allowlist, runtime caps, budget caps, terminal policy) | ✅ Yes — comprehensive | ✅ Enforced via Rust kernel | `agent_computer_policy_service.py` defines rules; Rust runtime kernel enforces as final authorization |
| **Secret redaction** | ✅ Yes | ✅ Applied at all write points | Audit events, trace events, transparency events all redacted before persistence |

### The important weakness:

**All enforcement is via inline checks at specific call sites, not via
middleware or decorators.** There is no architectural guarantee that every
execution path is covered. If a developer adds a new path and forgets to call
the governance functions, that path has zero enforcement.

Specifically:
- `decide_agent_computer_action()` is called from **only one production path**
  (Sage chat, line 527 of `sage_agent_runtime_service.py`)
- Gateway path has its **own separate, inlined** approval logic in
  `routes_gateway.py` — duplicated, not shared
- The self-hosted/VPS adapter has its own approval check in
  `enforce_self_hosted_runtime_action_decision()` — different from both above
- `evaluate_tool_policy_decision()` is only called from a couple of
  builder/runtime paths

**This is not broken — it works. But it is fragile.** The build brief's goal of
"governance enforced on the node" would require making these checks mandatory
and uniform, not scattered and optional.

---

## 6. Top 5 Tangled or Half-Finished Things on the Main Agent Path

### 1. Four separate message pipelines with overlapping responsibilities

The Main Agent (owner's Sage) has no single, clean inbound path. A Telegram
message can arrive through Path A (simple), Path B (gateway), or Path D
(hosted) depending on configuration. A WhatsApp message uses Path B. Each
pipeline duplicates command handling, normalization, and delivery. When the
build brief says "channels should never reach into hardware execution directly —
they hand a task to the control plane," we are far from that clean separation.

**Severity:** High. **Effort to fix:** Large (M/L).

### 2. LLM reasoning lives on cloud for ALL profiles — the "node-resident agent" does not exist

The target architecture says "put the whole agent runtime on the node." Today,
the LLM call is always on the cloud server. Only individual tool actions are
dispatched to hardware. For the My Computer profile, every click and keystroke
is shipped as a separate WebSocket frame from cloud to node — exactly the chatty
model the build brief wants to eliminate.

**Severity:** High (this is the core architectural shift). **Effort to fix:**
Large (L).

### 3. Self-hosted/VPS path is queue-based, not direct, and may not work end-to-end

The `self_hosted_node_adapter.py` enqueues commands via
`agent_registry_repository.enqueue_self_hosted_runtime_command()` and returns
"running" immediately. There is a Rust `local_worker.rs` in the runtime kernel
that presumably picks up commands, but the adapter never calls
`vps_ssh_execute()` directly. The direct SSH functions exist separately in
`hardware_runtime_target_resolver.py` but are unused by the main adapter. This
suggests two parallel VPS implementations that may or may not both work.

**Severity:** High. **Effort to fix:** Large (L).

### 4. Governance is enforced but not architecturally guaranteed

Each execution path has its own governance checks, implemented differently.
There is no single choke point where ALL actions must pass through approval,
kill switch, and policy evaluation. The Rust runtime kernel acts as a final
authorization layer for some decisions, but not all paths route through it. This
means the build brief's non-negotiable guardrail ("governance must be enforced
on the node") requires building that unified enforcement point.

**Severity:** Medium. **Effort to fix:** Medium (M).

### 5. Channel plane and execution plane are entangled

`routes_sage_telegram_hosted.py` directly imports and calls
`handle_sage_chat()` from the Sage agent runtime. The gateway channel handlers
in `agent_channel_router.py` directly import and call gateway protocol dispatch
functions. The channel adapter normalizes messages but the routing decisions
happen in channel-specific code. There is no clean "channel hands task to
control plane, control plane dispatches to node" separation.

**Severity:** Medium. **Effort to fix:** Medium (M).

---

## Summary for the Owner

**What you have today is impressive — far more than scaffolding.** The Main
Agent (Sage) works end-to-end on Telegram. You can message it, it can reason via
an LLM, it can take actions on your Mac (mouse, keyboard, shell, browser,
files), and it can reply. Approvals fire on risky actions. The kill switch
works. The audit trail records everything. Multiple channels (Telegram,
WhatsApp, Slack, Discord) are operational.

**But the architecture is not what the build brief describes.** The agent's
"brain" lives on the cloud, not on your hardware. Tool actions are shipped
one-at-a-time from cloud to your Mac, like remote control — not like an
autonomous agent running locally. The channel system has four different
pipelines that overlap. Governance works but is held together by developers
remembering to call the right checks, not by architectural guarantees.

**The target** — a node-resident agent that receives tasks from the cloud,
reasons locally, uses tools locally, enforces governance locally, and reports
back — **requires a significant architectural shift, not just cleanup.** The
pieces exist (gateway protocol, hardware adapters, policy engine, Rust
supervisor) but they are wired in the "cloud controls everything" model rather
than the "node is autonomous, cloud orchestrates" model.

**Next step:** Phase 1 — the gap map from current state to target state.
