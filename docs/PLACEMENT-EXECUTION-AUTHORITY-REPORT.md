# Placement, Execution, Authority, Channels, Transparency — Research Report

**Date:** 2026-07-09
**Branch:** `verify`
**Purpose:** End-to-end mental model of how agent placement, execution, authority,
channels, and transparency connect — to inform (a) redesign of the create-agent
flow and (b) the execution-authority model.

Every claim is marked: **wired** / **partial** / **spec-only** / **stub-in-local-dev**,
with `file:line` references.

---

## 1. PLACEMENT + EXECUTION

### 1.1 Placement Modes

Four placement modes defined in
`server_modules/deployed_agent_runtime_contract_service.py:75-78`:

| Mode | Constant | Execution Target |
|------|----------|-----------------|
| Text Agent (no hardware) | `text_agent` | `cloud_default` |
| Cloud Computer Agent | `cloud_computer_agent` | `sage_cloud_computer` |
| My Computer Agent (local Gateway) | `my_computer_agent` | `local_companion` |
| Self-Hosted Agent (VPS/SSH) | `self_hosted_agent` | custom (via runtime binding contract) |

### 1.2 Execution Dispatch Flow — wired

The full path from agent turn → tool call → execution:

1. **`agent_turn.py:119-131`** — `AgentTurnRequest` carries `machine_target`, which controls where execution lands.
2. **`agent_turn.py:181-191`** — `_bind_cloud_runtime_session_if_needed()` calls
   `deployed_agent_virtual_runtime_service.ensure_runtime_session_binding()`.
3. **`deployed_agent_virtual_runtime_service.py:1890-1944`** — Branches by `studio_agent_mode`:
   - `text_agent` → returns `None` (no runtime binding, pure LLM)
   - `self_hosted_agent` → `ensure_self_hosted_runtime_session_binding()`
   - Otherwise → `ensure_cloud_runtime_session_binding()` (handles both `cloud_computer_agent` and `my_computer_agent`)
4. **`direct_tool_execution_service.py:1077-1133`** — Tool execution dispatch:
   - If cloud binding → `execute_bound_cloud_runtime_tool_call()`
   - If self-hosted binding → `execute_bound_self_hosted_runtime_tool_call()`
   - Fallback → `skills_service.execute_single_direct_tool_call()` (dispatches to Gateway or local)

### 1.3 Per-Placement Capability Matrix

| Placement | Shell | Files | Browser (render) | Browser (interact) | Screenshot | Mouse/Keyboard |
|-----------|-------|-------|-------------------|---------------------|------------|----------------|
| **Cloud (text_agent)** | LLM tools only | LLM tools only | `web_fetch` only | No | No | No |
| **Cloud (cloud_computer)** | Provider-dependent | Provider-dependent | Provider-dependent | Provider-dependent | Provider-dependent | Provider-dependent |
| **Local Gateway** | Yes (Gateway TS) | Yes (Gateway TS) | Yes (headless-fetch) | **No (supervisor archived)** | **No (supervisor archived)** | **No (supervisor archived)** |
| **Self-hosted (VPS/SSH)** | Yes (Docker, jailed) | Yes (Docker) | No | No | No (artifacts only) | No |

### 1.4 Cloud Runtime Providers

**`virtual_computer_runtime.py:110-116`** — four providers, three wired:

| Provider | Status | Capabilities |
|----------|--------|-------------|
| Browserbase | **wired** | browser: true |
| E2B | **wired** | browser: true, shell: true, filesystem: true |
| Daytona | **wired** | browser: true, shell: true, filesystem: true |
| DigitalOcean SSH | **wired** | shell: true, routed through SSH |
| AWS Workspaces | **spec-only** | no factory |
| Azure Virtual Desktop | **spec-only** | no factory |
| Docker/Kubernetes | **partial** | factory exists (`SelfHostedNodeVirtualComputerRuntime`) |
| InMemory (local dev) | **wired** | blocked in staging/production (`virtual_computer_runtime.py:71-83`) |

### 1.5 VPS / Self-Hosted Node (SSH) — wired, contract-heavy

- **VPS Provisioning:** `vps_provisioning_service.py` — real DigitalOcean OAuth flow, droplet provisioning.
  API routes at `routes_gateway.py:1544-1830` (OAuth, listing, provision, status, deprovision).
- **SSH Setup:** `routes_gateway.py:711` — real paramiko SSH connection with fingerprint validation.
- **Runtime:** `virtual_computer_runtime.py:3986` (`SelfHostedNodeVirtualComputerRuntime`) and
  `virtual_computer_runtime.py:4299` (`DigitalOceanSSHVirtualComputerRuntime`) — SSH-based with Docker
  containers (`--network none`, jailed to `/workspace`).
- **Contract requirements:** `deployed_agent_virtual_runtime_service.py:1235-1253` — requires
  `runtime_node_id`, `runtime_profile_id`, `runtime_attachment_id`, `workspace_id`.

### 1.6 Rust Supervisor — ARCHIVED

**`_archive/supervisor/README.md:1-7`** confirms:
> "The Rust empyralis-supervisor daemon (mouse/keyboard/screen/local-fs control) is DISABLED, not deleted."
> "Owner decision: Empyralis agents do NOT control user desktops."

The archived supervisor had full, tested implementations of: shell (with hard-blocks for `rm -rf /`,
`mkfs`, `dd`), filesystem, clipboard, screenshot, OCR, app launch, mouse/keyboard control, system ops,
window management. The revival checklist documents 18 steps to bring it back.

**Impact:** On local Gateway, all desktop control tools (`computer__click`, `computer__type`,
`computer__ocr`, `computer__clipboard_read`, `computer__clipboard_write`, `computer__launch_app`,
`computer__focus_window`, `computer__speak`, `computer__applescript`) have **no executor**.
`gateway_execution_service.py:57-60` confirms this explicitly.

### 1.7 Rust Runtime Kernel — ACTIVE (distinct from the archived supervisor)

**`empyralis-runtime-kernel/`** — a separate decision-enforcement engine. Lean, stateless
(dependencies: `serde`, `serde_json` only). 59 Rust source files.

- Called as a subprocess: Python sends JSON via stdin, reads decision from stdout
  (`rust_runtime_kernel_client.py:435`).
- **FAILS CLOSED** (`rust_runtime_kernel_client.py:304-306`): if binary unavailable, returns
  `{"ok": false, "decision": "block", "reason": "runtime_kernel_unavailable"}`.
- 80 allowed commands in `ACTIVE_ENFORCEMENT_COMMANDS` (`rust_runtime_kernel_client.py:81-140`).
- Imported by 30+ Python modules for hard enforcement of kill switch, policy state, tool execution
  authorization, path containment, policy validation.

### 1.8 Browser Engine — wired, Python-owned

**`browser_engine.py`** — Playwright-based headless browser for page rendering. Singleton pattern,
dedicated event loop thread. Only `navigate`, `extract_text`, `extract_dom`, `close` are supported.
All access must go through `execution_router.py`'s `BrowserExecutionAdapter` — direct imports
outside `execution_router` are forbidden.

### 1.9 Is Execution "Free / Claude-Code-Like"?

**No.** Every execution path passes through multiple enforcement gates:
1. Rust kernel (fail-closed binary) validates the operation
2. Kill switches (global, workspace, agent, gateway, channel scopes)
3. Cost/budget gates (daily, monthly, per-turn)
4. Runtime binding contract enforcement (node_id, profile_id, workspace scope)
5. Tool catalog filtering (which tools the LLM can see)
6. Forbidden policy override keys (14 keys rejected at `deployed_virtual_runtime.rs:14-29`)

However, the **approval gate** between tool selection and tool execution is deregistered:
`policy_service.py:1528-1539` — `approval_required_for_direct_tool()` unconditionally returns `False`.

---

## 2. EXECUTION AUTHORITY — THE CRUX

### 2.1 Does the System Distinguish Owner from End-Customer at Execution Time?

**NO — there is no code-level distinction in the execution path for authority.**

Identity IS tracked (`ChannelRoutingContext` carries `external_user_id`, `sender_name`,
`sender_platform_id`, `end_user_profile`), and sender class IS classified
(`triage_service.py:83` → `"owner"` | `"audience"` | `"unknown"`). But at the execution boundary,
all messages flow through the same code path with the same tool-gating logic.

**The system's security model:** anyone who can reach the agent can cause it to run tools,
subject to the same tool-level allowlist/denylist gates.

### 2.2 What Distinctions DO Exist?

**Tool visibility** is scoped by sender class (wired):

- **`sage_agent_runtime_service.py:1564`** — when `_sender_class != "owner"`, calls
  `audience_tool_filter.filter_tools_for_audience()` which strips all tools NOT marked
  `audience_safe: True` in their `ToolDescriptor` manifest.
- **`audience_tool_filter.py:133`** — behavioral instructions injected into system prompt:
  "Treat every request as a SERVICE REQUEST, never as a command."
- **`sage_agent_runtime_service.py:3214`** — different system prompt instructions for audience sessions.

**BUT this is a VISIBILITY gate, not an enforcement gate.** The filter removes tools from the
tool array sent to the LLM, but there is no execution-time guard that would block a tool call
if one somehow bypassed the filter.

**`command_registry.py:139-145`** — `_is_sender_owner()` is explicitly **stub-in-local-dev**:
```python
def _is_sender_owner(sender_id: str, workspace_id: str) -> bool:
    """Stub — returns False until workspace ownership is wired."""
    return False
```

### 2.3 Governance/Policy/Autonomy/Kernel Controls Inventory

#### `policy_service.py` — wired, but approval gate deregistered

- **`resolve_tool_policy():239-286`** — orchestrator: system blocks → kill switch → cost/budget → kernel decision → autonomy gate.
- **`approval_required_for_direct_tool():1528-1539`** — **deregistered**: unconditionally returns `False`.
- **`evaluate_tool_policy_decision():968`** — full pipeline including Rust kernel `authorize-request`/`authorize-execution`, but result is advisory in the main Sage path.
- **`DEFAULT_ACTION_POLICY:759`** — default blocks: `shell.execute`, `delete_files`, `delete_records`, `transfer_funds`; approval-requires most write actions.

#### `runtime_policy.py` — wired (core definitions active)

3886 lines. Defines: `AUTONOMY_MODES` hierarchy (`read_only` → `yolo`), `VALID_POLICY_MODES`,
`EXECUTION_TARGET_*`, `RUNTIME_TRUST_ZONE_*`.
- **`decide_runtime_action_execution():1866`** — decision engine considering elevated access, action type, trust zone.
- **`set_tool_enabled()` / `is_tool_enabled()`** — per-tool enable/disable with kernel enforcement.

#### `agent_policy_context.py` — wired (system prompt injection)

Implements "internalized governance": policy is INJECTED into the agent's system prompt so the
agent decides what to do, and the platform audits silently.
- **`build_agent_policy_context():108`** — assembles the policy block.
- `PLATFORM_RULES:55`, `HARDWARE_ACCESS_NOTICE:65`, `KILL_SWITCH_NOTICE:73` — injected rules.
- Three tiers: T0 = cloud_only, T1 = cloud_compute, T2 = hardware.

#### `unified_governance_gate.py` — partial

**`evaluate_action_policy():95`** claims a 3-step pipeline:
1. **Kill switch** — calls `kill_switch_gate.evaluate_kill_switch()` → **wired**.
2. **Safe mode / capability disable** — calls `safe_mode_service.resolve_capability_disable_state()` → **wired**.
3. **Risk classification + approval decision** — attempts to import
   `agent_computer_approval_decision_service.decide_agent_computer_action()` →
   **THE IMPORT ALWAYS FAILS** — the file `agent_computer_approval_decision_service.py`
   **DOES NOT EXIST** anywhere in the codebase. Falls back to registry contract check.

Callers treat the result as **AUDIT-ONLY**:
- `routes_gateway.py:2198` — comment: "AUDIT ONLY"
- `sage_agent_runtime_service.py:939` — comment: "AUDIT-ONLY"

#### `kill_switch_gate.py` — wired

Five scopes: `global_pilot`, `workspace:{id}`, `agent:{id}`, `gateway:{id}`, `channel:{id}`.
- **`evaluate_kill_switch():181`** — checks all scopes + delegates to `safe_mode_service`.
- **`assert_not_killed():248`** — raises `KillSwitchBlockedError` if blocked.
- Rust kernel enforcement on all mutations (`_enforce_kill_switch_state_decision():53`).

#### `gateway_approval_service.py` — fully deregistered

All functions are stubs returning "always approved":
- `request_gateway_tool_approval()` → `{"approved": True}`
- `capability_requires_owner_approval()` → `False`
- Module docstring: "Stub — approval gates removed. Agent acts on its own reasoning."

#### `computer_action_safety.py` — wired (last hard enforcement)

**`evaluate_dangerous_computer_action_policy():318`** — hard enforcement for credential entry,
purchase checkout, system changes.
**`virtual_computer_runtime.py:787`** — `_evaluate_action_policy_and_approval()` calls
`owner_or_admin_mode()` at line 882 — this gate remains ACTIVE for computer-use actions.

#### `agent_computer_policy_service.py` — partial

Full policy specification: `autonomy_mode`, `allowed_capabilities`, `blocked_capabilities`,
`terminal_policy`, `network_policy`, `browser_access_policy`.
Six autonomy modes defined (lines 18-31): `read_only` → `yolo`.
Rust kernel enforcement exists (`evaluate_agent_computer_request():568` calls `validate-policy`
and `check-path-containment`).
**BUT** the decision service that would orchestrate risk classification + approval memory
(`agent_computer_approval_decision_service.py`) **DOES NOT EXIST**.

#### `agent_approval_memory_service.py` — **DOES NOT EXIST**

Also referenced by `unified_governance_gate.py` but the file is absent.

#### Kernel Autonomy Modes (in execution path)

**`policy_service.py:152-166`** — `resolve_autonomy_gate()`:
- `fully_autonomous` → kernel decision (no approval needed)
- `autonomous` → kernel decision, but blocks if `requires_approval`
- `supervised` → always requires approval, blocks if kernel blocks
- This is the ONLY autonomy enforcement in the execution path, and it only controls
  **approval requirements**, not **action scope**.

Agent scheduling models define autonomy tiers (`agent_scheduling_models.py:18-20`):
`supervised`, `autonomous`, `fully_autonomous`. Used for queue routing, not execution gating.

### 2.4 Per-Agent Action Scoping — What Exists

| Mechanism | Status | What It Does |
|-----------|--------|-------------|
| `tools_catalog` field | **wired** | Which tools the LLM can see (`full`, `browser_only`, `shell_only`, `connectors_only`) — `fleet_tools.py:29`, `skills_service.py:124` |
| Capability presets | **wired** | `standard` (full tools, hardware unlocked) vs `knowledge` (connectors only, hardware locked to `none`) — `capability_presets.py:40-146` |
| Specialist toolset filtering | **wired** | Per-specialist manifest declares `enabled_skills` — `sage_agent_runtime_service.py:1547` |
| `AgentComputerPolicy` | **partial** | Full policy objects with autonomy mode, capabilities, network, browser policy — stored but not enforced (decision service missing) |
| Per-agent command whitelist | **not wired** | Shell command blocks are global (`rm -rf /`, `mkfs`, `dd`), not configurable per-agent |
| Per-agent file path containment | **not wired** | Path protection is global, not per-agent |
| Per-agent channel restrictions | **not wired** | Channel binding validates lane compatibility, not which messages can trigger which actions |

### 2.5 What's Missing for "Authority Comes From the Owner, Not the Channel"

| Missing Control | What It Would Do |
|----------------|-----------------|
| **Message-source authority tier** | Distinguish owner-initiated, end-customer-initiated, and scheduled/automated turns at the policy level |
| **Hard enforcement for audience tool calls** | Beyond tool visibility filtering, actually block non-`audience_safe` tool calls at execution time |
| **Channel-based action gating** | "End-customer messages on Telegram cannot trigger `shell.exec`, owner messages can" |
| **Hardware-scoped authority** | "End-customer messages cannot access files outside `/tmp`" |
| **Action budget by message source** | Budget is per-agent, not per-customer or per-channel |
| **Mandate declaration/enforcement** | No "mandate" concept anywhere in the codebase (0 search results) |

### 2.6 Execution Authority Summary Table

| Component | Wired into Execution? | What It Covers | Owner-vs-Customer Distinction? |
|-----------|----------------------|----------------|-------------------------------|
| `policy_service.resolve_tool_policy()` | **Yes** | System blocks, kill switch, cost gate, kernel decision, autonomy gate | **No** |
| `runtime_policy_integration.RuntimePolicyBridge` | **Yes** | Computer, shell, file, browser, screenshot action evaluation | **No** |
| `agent_policy_context.AgentPolicyContext` | **Yes** (carried in context) | Runtime cluster info, budget, capability labels | **No authority tier field** |
| `policy_service.resolve_autonomy_gate()` | **Yes** | Controls approval requirements based on autonomy mode | **No** |
| `unified_governance_gate.evaluate_action_policy()` | **Partial** (audit-only) | Kill switch + safe mode blocking; risk classifier dormant | **No** |
| `tools_catalog` | **Yes** (via `skills_service`) | Which tools are offered to the LLM | **No per-sender scoping** |
| `audience_tool_filter` | **Yes** (visibility only) | Strips non-`audience_safe` tools for non-owner sessions | **Yes, but visibility only** |
| Kill switch | **Yes** | Emergency stop for the entire agent | **No** (workspace-wide) |
| Cost/budget gates | **Yes** | Daily/monthly/per-turn budgets | **No** (per-agent only) |
| Rust runtime kernel | **Yes** | Final yes/no on individual tool calls | **No** |
| `computer_action_safety` | **Yes** | Hard enforcement for dangerous computer actions | **Checks `owner_or_admin_mode()`** |
| `approval_required_for_direct_tool()` | **Deregistered** | Always returns `False` | **N/A** |
| `gateway_approval_service` | **Deregistered** | All functions are stubs | **N/A** |
| `agent_computer_approval_decision_service` | **Spec-only** | **FILE DOES NOT EXIST** | **N/A** |

### 2.7 Architecture Verdict

The current execution authority model is **"declare, don't enforce"**:
1. The agent's system prompt declares what it can/cannot do (`agent_policy_context`).
2. Tool visibility is scoped per sender-class (owner vs audience) and per-specialist manifest.
3. Kill switch and computer action safety provide the ONLY hard enforcement layers.
4. The approval gate/risk classifier pipeline is entirely dormant — the Python glue module doesn't exist.
5. The Rust kernel IS wired for specific capability classes, but Python-side code treats kernel decisions as advisory in the main Sage path.

**An agent with hardware access and a Telegram channel will execute tool calls from an end-customer
with the same execution authority as tool calls from the workspace owner.** The only distinctions
are tool visibility (the LLM sees fewer tools for audience members) and computer action safety
(which checks `owner_or_admin_mode` for specific dangerous actions).

---

## 3. PLACEMENT CUSTOMIZABILITY

### 3.1 Can Placement Be Changed After Creation?

**Yes — for both Fleet agents and Deployed/Studio agents. No one-way doors.**

#### Fleet agents (`hardware_access` column)

- **wired** — `fleet_tools.py:735-771`: PATCH accepts `none|gateway|vps|all`, checks policy lock, persists via `repo.update_workspace_agent_install(..., hardware_access=...)`.
- **wired** — `agent_registry_repository.py:2548` (PG path) and `2226` (SQLite fallback): direct column UPDATE.
- **wired** — Frontend `FleetAgentDetail.tsx:979-1015` (`HardwareBindingSection`): toggle + save button sends `PATCH` with `hardware_access` and `preferred_gateway_id`.

#### Deployed/Studio agents (`runtime_target`)

- **wired** — `routes_deployed_agents.py:597-622`: PATCH accepts `runtime_target`.
- **wired** — `deployed_agent_service.py:4524-4525`: normalized and stored on every PATCH.
- **wired** — `deployed_agent_runtime_contract_service.py:414-447` (`normalize_runtime_placement`): accepts `managed_cloud|hosted_hardware_pool|customer_local|customer_hosted` plus aliases. Falls back to `managed_cloud` if unrecognized.

### 3.2 Mechanisms

| Mechanism | Status | Evidence |
|-----------|--------|----------|
| Fleet `hardware_access` PATCH | **wired** | `fleet_tools.py:735-771` |
| Deployed agent `runtime_target` PATCH | **wired** | `routes_deployed_agents.py:597`, `deployed_agent_service.py:4524-4525` |
| Gateway pairing intent creation | **wired** | `routes_gateway.py:1420-1450` |
| SSH auto-pairing | **wired** | `routes_gateway.py:1453-1531` |
| Gateway registration | **wired** | `routes_gateway.py:1817-1835` |
| Gateway revocation (full unpair) | **wired** | `routes_gateway.py:2006-2048` |
| VPS provision | **wired** | `routes_gateway.py:1641-1737` |
| VPS deprovision | **wired** | `routes_gateway.py:1787-1814` |
| Workspace-level default placement | **wired** | `workspace_config_schema.py:83`, `workspace_admin_service.py:725-729` |

### 3.3 Lock-in / One-Way Doors

**No lock-in or irreversible placement transitions exist.**

- `hardware_access` column: freely writable, no "has_been_set" guard. `none → gateway → vps → none` works freely.
- `runtime_target` on deployed agents: normalized and stored on every PATCH, no transition rules.
- `runtime_placement` normalization: purely a mapping function, no state-machine logic.
- **Knowledge preset hardware lock** (`capability_presets.py:112-114`): the only "gate." But it is
  **not a one-way door** — it's a policy check that can be lifted by changing the capability preset
  to Standard or Operator. The `hardware_locked` flag lives in mutable metadata.
- Gateway revocation exists (`routes_gateway.py:2006-2048`). No restrictions on re-pairing.
- VPS deletion exists (`routes_gateway.py:1787-1814`). No restriction on provisioning a new one.
- No `immutable_placement` or `placement_frozen` field found anywhere in schema, repository, or routes.

---

## 4. CHANNELS ↔ PLACEMENT

### 4.1 Are Channels and Placement Independent?

**Yes, at the data model level.** `runtime_placement` and `channels` are peer fields on
`DeployedAgentConfig` (`deployed_agent_config_schema.py:259,272`). `ChannelRoutingContext`
(`channel_routing_models.py:7-38`) carries `channel_key` and `runtime_mode`/`runtime_profile_id`
as peers.

**However**, a lane-based guard couples certain channel families to specific runtime infrastructure:

- **`channel_platform_service.py:462-466`** — forbids binding `personal_gateway`-lane channels
  (telegram_personal, whatsapp_personal, signal_personal, imessage_personal, wechat_personal)
  to non-Sage/non-Agent-Computer agents.
- This is a **lane-based check** (checking `runtime_lane == "personal_gateway"`), not a
  placement-based check (it does not examine `runtime_placement`).

### 4.2 Can a Cloud Agent Have Channels?

**Yes.** A `studio_agent_mode=text_agent` (runtime_placement=`managed_cloud`) agent can bind any
studio/business channel (telegram_bot, discord_bot, slack, web_chat, email, etc.) via
`DeployedAgentConfig.channels`. It **cannot** bind personal channels (telegram_personal,
whatsapp_personal, etc.) — those require Agent Computer.

### 4.3 Can a VPS Agent Have Channels?

**Yes.** A `studio_agent_mode=my_computer_agent` agent can bind both studio AND personal channels,
because the Agent Computer gateway satisfies the `requires_agent_computer` requirement.
Same `DeployedAgentConfig.channels` field.

### 4.4 Channel Types Inventory

**Personal channels** (lane `personal_gateway`, require Agent Computer):
- `telegram_personal` — **wired** (GramJS, `connection_catalog_service.py:177-198`)
- `whatsapp_personal` — **wired** (Baileys, `connection_catalog_service.py:199-219`)
- `discord_personal` — **wired** (bot-token DM, `channel_lane_contract_service.py:50-56`)
- `signal_personal` — **stub-in-local-dev** (`connection_catalog_service.py:221-241`)
- `imessage_personal` — **stub-in-local-dev** (`connection_catalog_service.py:243-263`)
- `wechat_personal` — **stub-in-local-dev** (`connection_catalog_service.py:265-285`)

**Studio/business channels** (lane `studio_business_connector`, no Agent Computer required):
- `telegram_bot` — **wired** (BYO BotFather, `connection_catalog_service.py:287-307`)
- `sage_telegram_hosted` — **wired** (Empyralis first-party bot, `connection_catalog_service.py:308-328`)
- `discord_bot` — **wired** (`channel_lane_contract_service.py:196-206`)
- `slack` — **wired** (`channel_lane_contract_service.py:184-194`)
- `web_chat` — **spec/roadmap** (`connection_catalog_service.py:329-343`)
- `email` — **partial/wired** (`connection_catalog_service.py:329-343`)
- `whatsapp_twilio` — **stub-in-local-dev** (`channel_lane_contract_service.py:160-170`)
- Plus: `github`, `linear`, `notion`, `dropbox`, `s3`, `smtp`, `teams`, `matrix` (mix of partial/roadmap/wired)

**Direct Chat** — API channel (`direct_chat_entry_service.py:127-128`), connects via install ID. **wired**.

### 4.5 Channel Routing Flow — wired

1. Webhook hits public endpoint (e.g., `routes_connectors.py:192-198`, `routes_sage_telegram_hosted.py:131-233`)
2. `channel_preflight_service.py:11-40` validates channel key, endpoint key, kill-switch state
3. `channel_turn_request_service.py:288-410` builds `ChannelRoutingContext` and turn request
4. For deployed agents, `bind_deployed_agent_source_to_context()` overlays provider/model/placement
5. Turn dispatched to agent runtime

---

## 5. CURRENT CREATE FLOW

### 5.1 Wizard Steps (Exact Order)

Defined in `FleetCreateAgentWizard.tsx:29`:
```
STEP_LABELS = ["Name", "Project", "Capability preset", "Hardware", "AI brain", "Model", "Channel"]
```

| Step | UI Action | Persistence |
|------|-----------|-------------|
| 1 Name | Enter name + description | UI state only |
| 2 Project | Pick a project | UI state only |
| 3 Capability preset | Pick "Standard" or "Knowledge" | **Agent created here** via `POST /fleet/agents` (`FleetCreateAgentWizard.tsx:125-145`). Name, instructions, capability_preset, project_id sent. `agent_id` returned. |
| 4 Hardware | Pick "No dedicated hardware" or "A paired computer" | UI state only (seeds gatewayBinding for step 5) |
| 5 AI brain | Pick platform/byok/subscription/local | BYOK: saves vault key + provider profile (`FleetCreateAgentWizard.tsx:181-227`). No agent patch yet. |
| 6 Model | Pick specific model | Patches agent via `PATCH /fleet/agents/{agent_id}` with `model_config` (`FleetCreateAgentWizard.tsx:242-265`) |
| 7 Channel | "Not yet" or "BYO bot" | No server call — signpost only. Wizard closes. |

**Key fact:** The agent row is created at step 3, NOT step 1. The "Next" button at step 3 calls
the creation endpoint. Step 7's "Create" button actually closes the wizard (the agent already exists).

### 5.2 Where Each Attribute Is Set

| Attribute | Creation-time? | Post-creation editing? | Status |
|-----------|---------------|----------------------|--------|
| **Name** | Required (`routes_fleet.py:194`) | `display_name` in PATCH allowlist (`fleet_tools.py:29`), but **no frontend UI to rename** — Overview tab has no label editor | **partial** |
| **Provider** | Optional (`FleetCreateAgentRequest.provider`) | Full editing in ModelTab (`FleetAgentDetail.tsx:1130`) | **wired** |
| **Model** | Optional | Full editing in ModelTab | **wired** |
| **Hardware/Placement** | Optional (`runtime_target`) | HardwareTab + HardwareBindingSection | **wired** |
| **Channels** | NOT in creation (wizard step 7 is signpost only) | ChannelsTab (`FleetAgentDetail.tsx:484`) + dedicated endpoints for Telegram/Discord (`routes_fleet.py:642-707`) | **wired post-creation** |
| **MCP** | NOT in wizard at all | Connectors tab (`routes_fleet.py:458-613`) | **wired post-creation** |

### 5.3 Difficulty Assessment for Reorder: Placement → Provider+Model → Channels → MCP, NAME optional

| Concern | Difficulty | Notes |
|---------|-----------|-------|
| Making name optional | **Low** | Change one Pydantic field (`routes_fleet.py:194`), add auto-generation. DB allows null labels (`agent_registry_models.py:111`). Repository has 3-tier fallback: label → definition name → "Installed Agent" (`agent_registry_repository.py:2422`). Backend already tolerates empty name — uses "Fleet Specialist" fallback (`fleet_tools.py:925`). |
| Making name editable later | **Low-Medium** | Backend already supports `display_name` PATCH. Frontend needs a small in-place edit control in Overview tab (the wizard comment at line 308 says "Rename it from the agent's Overview tab" but this UI does NOT exist — **spec-only**). |
| Moving Placement before Provider+Model | **Low** | Placement step 4 is UI-only (no server call); moving earlier has no dependency on agent creation. |
| Moving Provider+Model earlier | **Low** | Already at steps 5-6, after creation at step 3. PATCH endpoint works anytime. |
| Adding MCP to wizard | **Medium** | No current wizard step. Requires wiring `connector_bindings` into creation or post-creation patch. |
| "Agent created at step N" logic | **Medium** | Agent is created at step 3 today. In reorder, simplest approach: create the agent immediately with auto-generated name + defaults, then patch each field as user advances. |

### 5.4 Existing Default-Name / Auto-Generation Mechanisms

- `agent_id` auto-generation: **wired** — `routes_fleet.py:291-293` calls `fleet_manager.generate_agent_id()`.
- Backend name fallbacks: **wired** — "Fleet Specialist" (`fleet_tools.py:925`), "Installed Agent" (`agent_registry_repository.py:2422`).
- Frontend fallback: **wired** (display only) — `deployed-agents/utils.ts:131` uses `'Untitled agent'` when label looks like a placeholder.
- **No random-name generator, no sequential naming, no name pool exists.** Would need to be built.

---

## 6. TRANSPARENCY / TASKS

### 6.1 What the Agent Is Doing NOW

| Surface | Status | Detail |
|---------|--------|--------|
| Agent presence dot (online/offline/typing) | **wired** | `agent_presence_service.py:18-28`, Redis-backed. Frontend: `PrimaryRail.tsx:200-224`, `FleetCard.tsx:215` |
| Gateway connection status | **wired** | `gateway_connection_ledger.py:244`, WebSocket-hb-based |
| Live SSE activity stream during chat turn | **wired** (chat surface only) | SSE `trace`/`step` events → `LiveActivityStepState[]`, `LiveTraceState` (`workstation-chat-pane-hooks.ts:249-299`). Event projector: `codex-chat/event-projector.ts` |
| Hardware status (online/offline/unknown) | **wired** | `fleet_tools.py:228-291` checks `last_heartbeat_at` from `fleet_worker_registrations`. 120s cutoff. |
| Fleet header: "{n} agents · {n} online" | **wired** | `FleetHome.tsx:65-66` |
| "What is agent X doing right NOW" dashboard view | **spec-only** | No dashboard panel showing currently-executing task per agent. `current_run_id` exists in worker registrations but is not surfaced in UI. |

### 6.2 Plan / Steps (What It Intends to Do)

| Surface | Status | Detail |
|---------|--------|--------|
| Sage transparency events (per-stage) | **backend-wired, frontend partial** | `sage_transparency_service.py:57-346` emits typed events for every stage. But frontend `WorkstationTurnResponse` type (`workstation-client.ts:54-65`) does NOT include `transparency_events` — silently dropped. |
| Gateway transparency events | **wired** | `gateway_transparency_service.py:34-157` — action start/complete, approvals, safety blocks, channel messages. |
| Deployed agent test turn transparency | **wired** (studio test surface) | `deployed_agent_transparency_service.py:29-237`. |
| Transcript event contract (SSE trace/step) | **wired** | `transcript-event-contract.ts:1-80` — defines safe-to-display event types. |
| Plan/Steps dedicated UI | **partial** | Plan objects emitted (`deployed_agent_transparency_service.py:220-240`) but no dedicated frontend plan view exists. |
| Transparency timeline component | **REMOVED** | `frontend/lib/workspace/transparency-timeline.tsx` deleted from source (shows in `git status`). |

**Note:** Two parallel event systems exist and are NOT unified:
1. `AgentTransparencyEvent` (Python dataclass) — typed stages emitted by backend, persisted to ledger.
2. `AgentActivityEvent` (TypeScript) — derived from raw SSE `trace`/`step` events during chat stream.

The frontend chat never renders `AgentTransparencyEvent` records. The backend transparency event
store never feeds the SSE stream directly.

### 6.3 What It DID (History)

| Surface | Status | Detail |
|---------|--------|--------|
| Activity ledger (`activity_ledger_events`) | **wired, central** | `control_plane_repository.py:1355-1381` — columns: `actor_type`, `actor_id`, `install_id`, `run_id`, `thread_id`, `channel`, `event_class`, `action`, `trace_id`, `title`, `summary`, `status`, `payload`, `metadata`. 15 event classes (`activity_ledger_service.py:14-30`). |
| FleetAgentDetail "Recent activity" | **wired** | `FleetAgentDetail.tsx:319-358` — last 50 ledger events. Fetch: `useFleetAgentActivity()` → `fleet_tools.py:444-501`. |
| Workspace activity timeline | **wired** | `activity_ledger_service.py:658-709`, frontend polls every 30s (`fleet-data.ts:324-353`). |
| Channel activity ledger writes | **wired** | `channel_activity_service.py:34-179` — every channel turn outcome recorded. |
| Hardware activity events | **wired** | `hardware_activity_event_service.py:50-80` — gateway hardware action events. |
| Agent traces (`agent_traces`) | **wired** (backend, no UI) | `control_plane_repository.py:1159-1184` — tables exist, no dedicated frontend surface. |
| ActionItemCard logs | **partial** | `FleetAgentDetail.tsx:1356` — empty until turns have executed. |
| Total actions counter | **partial** | `FleetDashboard.tsx:173` — depends on ledger, empty in local dev. |
| Cost/token dashboard | **spec-only** | Data exists in ledger, no visualization surface built. |

### 6.4 Runtime / Uptime

| Surface | Status | Detail |
|---------|--------|--------|
| Hardware status (online/offline) | **wired** | `fleet_tools.py:228-291` — heartbeat within 120s = "online" |
| `last_heartbeat` ISO timestamp | **wired** | `fleet-data.ts:14` |
| Gateway/pairing box liveness | **wired** | `gateway-box-picker.tsx` |
| Lifecycle state machine | **wired** | `agent_lifecycle_state_machine.py:1-221` — tracks creating→preparing→ready→suspended/error |
| Live runs (`live_runs` table) | **wired** (backend only) | `run_state_schema.sql:1-12` |
| Cumulative uptime history / percentage | **spec-only** | No uptime calculator exists |

### 6.5 WHO It's Running For (End-Customer Identity)

| Surface | Status | Detail |
|---------|--------|--------|
| `install_id` on activity ledger | **wired** | Every event links to the specific agent install |
| Channel routing context carries identity | **wired** | `external_user_id`, `sender_name`, `sender_platform_id`, `end_user_profile` |
| Customer attribution on turns | **wired** | `deployed_agent_transparency_service.py:270-278` — `customer_id`, `customer_label`, `channel` |
| Customer identity in transparency events | **wired** | `agent_transparency_events.py:50,66,75` |
| Frontend customer label display | **wired** | `FleetAgentDetail.tsx:309` |
| Agent transparency events lack `install_id` | **partial** | `AgentTransparencyEvent` has `agent_id` but NOT `install_id` — trace-level scope loses per-agent identity when persisted to ledger |

### 6.6 Work / Conversations Tab

**`frontend/app/(account)/w/[workspaceId]/projects/[projectId]/agents/[agentId]/[tab]/page.tsx:578-641`**

| Field | Status | Source |
|-------|--------|--------|
| Conversation list (past conversations) | **wired** | `deployed_agent_transparency_service.py:388-436` |
| Start time | **wired** | `conversation["started_at"]` |
| Last activity timestamp | **wired** | `conversation["last_activity"]` |
| Message count | **wired** | `conversation["message_count"]` |
| End-customer identity | **wired** | `conversation["customer_label"]` |
| Channel that carried conversation | **wired** | `conversation["channel"]` |
| Turn session ID | **wired** | `conversation["turn_session_id"]` |
| Cost estimate | **wired** | `conversation["est_cost_usd"]` |
| Click-through to full conversation | **wired** | Navigation to `/conversations/{turn_session_id}` |
| Deduplication | **wired** | COALESCE grouping by `transparency_override.customer_id` |
| Current active conversation | **wired** | Active only when agent is engaged in a turn |

### 6.7 Real in Local Dev vs Empty-Until-It-Runs

**Real in local dev:**
- Agent presence/dot (via Redis)
- Gateway connection status (if gateway connected locally)
- Lifecycle state machine transitions
- Activity ledger table exists (SQLite), writes work
- Customer attribution on every event
- Conversation list (populated from past runs)

**Empty until it runs:**
- Activity summary (no turns = no summary)
- Recent work stats (zero counts)
- Action item logs (empty list)
- Total actions counter (zero)
- Cost estimates ($0.00)
- Message counts (zero)
- Customer labels (populated from channel identity during actual runs)
- Task metadata in transparency snapshot (only when tasks assigned)

---

## 7. SYNTHESIS — THE BIG PICTURE

### 7.1 How the Pieces Connect

```
┌─────────────────────────────────────────────────────────────────────┐
│                        OWNER (workspace)                             │
│  Creates agent, sets: name, capability preset, provider, model,     │
│  hardware_access, channels, tools_catalog, autonomy mode            │
└──────────┬──────────────────────────────────┬───────────────────────┘
           │                                  │
     ┌─────▼──────┐                   ┌──────▼──────────┐
     │  CHANNEL   │                   │  DIRECT CHAT    │
     │ (Telegram, │                   │  (API/Web)      │
     │  Discord,  │                   │  sender_class   │
     │  Slack…)   │                   │  = "owner"      │
     │ sender_class│                  │  default        │
     │ = "audience"│                  │                  │
     └─────┬──────┘                   └──────┬──────────┘
           │                                  │
     ┌─────▼──────────────────────────────────▼──────────────────────┐
     │                    CHANNEL ROUTING                             │
     │  ChannelRoutingContext: external_user_id, channel_key,        │
     │  sender_name, end_user_profile, runtime_mode                   │
     │  IDENTITY TRACKED but AUTHORITY NOT ENFORCED                   │
     └─────┬─────────────────────────────────────────────────────────┘
           │
     ┌─────▼──────────────────────────────────────────────────────────┐
     │                    TOOL VISIBILITY FILTER                       │
     │  audience_tool_filter: strips non-audience_safe tools          │
     │  VISIBILITY GATE ONLY — no hard enforcement at execution       │
     └─────┬──────────────────────────────────────────────────────────┘
           │
     ┌─────▼──────────────────────────────────────────────────────────┐
     │                    AGENT RUNTIME (LLM turn)                     │
     │  System prompt: agent_policy_context (PLATFORM_RULES,          │
     │  HARDWARE_ACCESS_NOTICE, KILL_SWITCH_NOTICE)                   │
     │  Agent chooses tool from filtered tool list                    │
     └─────┬──────────────────────────────────────────────────────────┘
           │
     ┌─────▼──────────────────────────────────────────────────────────┐
     │                    EXECUTION GATES                              │
     │  ┌──────────────────────────────────────────────────────────┐  │
     │  │ policy_service.resolve_tool_policy()                     │  │
     │  │  ✓ System blocks (3 hardcoded Apple commands)            │  │
     │  │  ✓ Kill switch (5 scopes, wired)                         │  │
     │  │  ✓ Cost/budget gate (daily, monthly, per-turn)           │  │
     │  │  ✓ Rust kernel authorize-execution/authorize-request     │  │
     │  │  ✓ Autonomy gate (supervised→requires approval)          │  │
     │  │  ✗ APPROVAL GATE DEREGISTERED (always returns False)     │  │
     │  │  ✗ RISK CLASSIFIER DORMANT (glue module missing)         │  │
     │  └──────────────────────────────────────────────────────────┘  │
     │                                                                │
     │  ┌──────────────────────────────────────────────────────────┐  │
     │  │ computer_action_safety (LAST HARD GATE)                  │  │
     │  │  ✓ Checks owner_or_admin_mode() for dangerous actions    │  │
     │  │  ✓ Credential entry, purchase checkout, system changes   │  │
     │  └──────────────────────────────────────────────────────────┘  │
     └─────┬──────────────────────────────────────────────────────────┘
           │
     ┌─────▼──────────────────────────────────────────────────────────┐
     │                    EXECUTION DISPATCH                           │
     │  direct_tool_execution_service / gateway_execution_service     │
     │  → Cloud runtime (Browserbase/E2B/Daytona)                     │
     │  → Gateway (local TS process over WebSocket)                   │
     │  → Self-hosted (SSH → Docker, jailed)                         │
     └─────┬──────────────────────────────────────────────────────────┘
           │
     ┌─────▼──────────────────────────────────────────────────────────┐
     │                    TRANSPARENCY EVENTS                          │
     │  agent_transparency_events → Redis pub/sub                     │
     │  → transparency_event_store_service → activity_ledger_events   │
     │  → deployed_agent_transparency_service (turn start/complete)   │
     │  → channel_activity_service (per-message records)              │
     └────────────────────────────────────────────────────────────────┘
```

### 7.2 The Critical Gap

**An agent with hardware access AND a customer-facing channel currently operates with no
execution-time distinction between owner and end-customer authority.** The tool visibility
filter removes non-`audience_safe` tools from the LLM's view, but if a tool call is made
(through whatever means), the execution gates treat it identically regardless of who sent
the triggering message.

The approval gate is deregistered. The risk classifier is dormant. The "mandate" concept
doesn't exist. Budget is per-agent, not per-customer.

### 7.3 What IS Wired and Working

1. **Placement is fully customizable** — live changes, no one-way doors, all four modes wired, VPS/SSH path real and contract-enforced.
2. **Channels are independent of placement** at the data model, with one lane-based guard (personal channels require Agent Computer).
3. **The Rust runtime kernel** is active, fail-closed, and provides hard enforcement for kill switches, policy mutations, path containment, and tool authorization decisions.
4. **The transparency ledger** is comprehensive — every turn, action, channel message, and customer identity is recorded with full timestamps.
5. **The Work tab** shows per-agent conversation history with customer attribution, channel tags, and cost estimates.
6. **Tool catalog and capability presets** provide per-agent tool scoping at the visibility level.
7. **Sender identity** is tracked end-to-end from channel webhook through to transparency events.
8. **The create flow** is architecturally ready for reordering — single-step creation with post-creation PATCH for every field, auto-generatable IDs, and a DB schema that tolerates null/empty values.

### 7.4 What's Needed for the Target Model

For **"authority comes from the owner, not the channel"**:
1. Wire `sender_class` into the execution gate (not just tool visibility).
2. Implement or revive the `agent_computer_approval_decision_service` (the missing glue module).
3. Add per-agent mandate/scope declarations that gate execution, not just tool visibility.
4. Add channel-based action gating ("end-customer on Telegram cannot trigger `shell.exec`").
5. Add action budget by message source (per-customer or per-channel, not just per-agent).

For **the reordered create flow** (Placement → Provider+Model → Channels → MCP, NAME optional):
1. Make `name` optional in `FleetCreateAgentRequest` (one Pydantic field change).
2. Add auto-generated naming (e.g., "Agent {n}").
3. Add a rename UI in the Overview tab (backend already supports `display_name` PATCH).
4. Build the multi-step wizard that creates the agent record immediately (auto-name + defaults), then patches each field as the user advances.
5. Add MCP step to the wizard (requires wiring `connector_bindings` into the wizard flow).
