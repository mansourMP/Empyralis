# Empyralis Transparency & Runtime Layer Audit

## Part 1 — Current Layer Map

### A. Sage / Main Agent

| Dimension | Current State | File:Line |
|-----------|--------------|-----------|
| Entry points | `workstation-chat-pane.tsx` → `handle_sage_chat` → `sage_turn_adapter.py:44` | frontend, server |
| Runtime path | `agent_turn.py` → `turn_runtime.py` → direct chat or durable execution | `agent_turn.py:128-129` |
| Memory access | `unified_memory_service.py` 8-layer payload, `conversation_memory_facade_service.py` | `unified_memory_service.py:804` |
| Channel access | Personal Telegram (MTProto/hosted bot) plus Signal/iMessage/WeChat local bridges through Gateway. WhatsApp personal is not a supported channel. | `personal_channels_service.py`, `channel_lane_contract_service.py` |
| Gateway access | Yes — tool.invoke via `gateway_execution_service.py` | `gateway_execution_service.py:24` |
| Approval path | `gateway_approval_service.py`, interactive approvals in `agent_turn.py:476` | server |
| Audit path | `activity_ledger_service.append_activity_event`, `security_audit_service.emit_security_audit_event` | multiple |
| Trace generation | `sage_agent_runtime_service.py:332` — `trace_id = str(uuid.uuid4())` | server |
| Transparency shown | Thinking indicator, tool rows (name+status), activity steps (thinking/tool/search), approval prompts | `chat-message.tsx` |
| Missing transparency | No trace_id in chat, no per-turn "what happened" expandable, no tool input/output detail, no policy decision display in chat | frontend |

### B. Studio Agents

| Dimension | Current State | File:Line |
|-----------|--------------|-----------|
| Entry points | Studio wizard (8 steps), test playground, channel webhooks | `workstation-deployed-agents-pane.tsx`, `agent_channel_router.py:304` |
| Runtime modes | `text_agent`, `cloud_computer_agent`, `my_computer_agent`, `self_hosted_agent` | `deployed_agent_runtime_contract_service.py:65-68` |
| Memory access | Isolated per install, `deployed_agent_memory_service` overlay | `agent_channel_router.py:439` |
| Channel access | Business/official channels: Telegram Bot, Slack, Discord live when configured; Email/Microsoft 365 partial; Web Chat/Webhook/Teams/Matrix planned. WhatsApp Business is not supported (Meta bars third-party AI assistants). | `channel_lane_contract_service.py` |
| Gateway access | Only `my_computer_agent` mode | `deployed_agent_runtime_contract_service.py:119-133` |
| Approval path | Owner approval for computer actions, interactive approvals | `deployed_agent_runtime_contract_service.py:418-431` |
| Audit path | `activity_ledger_service`, `channel_activity_service.record_result` | multiple |
| Trace generation | `agent_turn.py:275-303` — `_bind_trace_id_to_turn_result` | server |
| Transparency shown | Test playground: reply, trace_id, approval_required, tools_considered, policy_decisions, memory_context, audit_events | `workstation-deployed-agent-test-turn-pane.tsx` |
| Missing transparency | Customer-facing agents show no transparency. No per-channel transparency controls. No "agent is working" status for end-users. | frontend |

### C. Runtime Modes

| Mode | Frontend Label | Backend Enum | Implementation | Runs On | Tools | Channels | Approval | Audit |
|------|---------------|-------------|----------------|---------|-------|----------|----------|-------|
| Text Agent | "Text Agent" | `text_agent` | Full | Cloud | chat, approved_tools, knowledge, memory, channels | All business channels | Interactive | Full |
| Cloud Computer Agent | "Cloud Computer Agent" | `cloud_computer_agent` | Full | Cloud VM | + browser, code_exec, file_artifacts | All business channels | Owner approval for computer actions | Full |
| My Computer Agent | "My Computer Agent" | `my_computer_agent` | Full | Customer local via Gateway | + local_companion, local_files, local_browser | Personal + business channels | Owner approval for local actions | Full |
| Self-Hosted Agent | "Self-Hosted Agent" | `self_hosted_agent` | Full | Customer hosted | + self_hosted_runtime, remote_files, remote_jobs | Business channels | Owner approval | Full |

**3 separate mode concepts:**
1. **Studio agent mode** (user-facing): `text_agent`, `cloud_computer_agent`, `my_computer_agent`, `self_hosted_agent`
2. **Runtime target** (deployment): `cloud`, `local`, `self_hosted`, `device`
3. **Specialist runtime_mode** (backing install): `hosted_secure`, `local_secure`, `privileged_device`

**Mapping:** `text_agent` → `cloud` → `hosted_secure`. `cloud_computer_agent` → `cloud` → `sage_cloud_computer`. `my_computer_agent` → `local` → `local_secure`. `self_hosted_agent` → `self_hosted` → varies.

---

## Part 2 — Current Transparency Gaps

### trace_id Flow

| Path | trace_id Present | Evidence |
|------|-----------------|----------|
| Sage turns | YES | `sage_agent_runtime_service.py:332` |
| Gateway tool execution | YES | `gateway_execution_service.py:30,76` |
| Gateway approvals | YES | `gateway_activity_service.py:74,76,112,114` |
| Activity ledger events | YES | `activity_ledger_service.py:391,416` |
| Security audit events | YES | `security_audit_service.py:30` |
| Channel automatic replies | **NO** | `personal_channels_service.py` `_emit_automatic_reply_audit` — no trace_id |
| Memory reads | **NO** | `conversation_memory_facade_service.py`, `unified_memory_service.py` — no trace_id |
| Memory writes | PARTIAL | Activity ledger records `memory_update` event class but no per-read audit |
| Frontend Sage chat | **NO** | trace_id tracked internally but never rendered in visible message area |
| Frontend Activity timeline | **FILTERED OUT** | `workstation-runs-pane.tsx:196` explicitly hides trace_id content |
| Frontend Studio test playground | YES | `DeployedAgentTestTurnPane` shows trace_id in result section |

### Memory Access Logging

- **No explicit read-access audit.** `unified_memory_service` builds 8-layer payloads without logging who accessed what.
- **Memory writes** are logged as `memory_update` event_class.
- **Memory visibility** is enforced via `_enforce_sage_memory_viewer` and `_enforce_specialist_memory_viewer` but access is not audited.

### Transparency Settings

- **No transparency visibility settings exist.** The `workstation-settings-pane.tsx` has a "Privacy & Safety" section that mentions approvals, memory, and computer trust — but no toggles for what users see during a turn.
- **No per-agent transparency overrides.**
- **No per-channel transparency overrides.**

---

## Part 3 — Proposed Transparency Event Model

### Transparency Levels

| Level | What's Shown | Audience |
|-------|-------------|----------|
| **Off** | Agent is typing, final answer, approval prompt if needed | Customer-facing Studio |
| **Minimal** | Checking memory, using tool, waiting for approval, action complete | Customer-facing Studio (verbose) |
| **Standard** | Tool names, sources/apps used, memory category used, safety decisions, approval state, trace_id | Sage chat (default) |
| **Full** | Step timeline, tool inputs/outputs summarized, blocked actions, policy decisions, memory included/excluded, channel events, runtime events | Owner in test playground |
| **Admin/Enterprise** | Full audit trail, actor identity, workspace, gateway/device, runtime session, exact policy gates, quotas, blocked actions, exportable logs | Admin Activity page |

### Canonical Event Model

```
AgentTransparencyEvent:
  event_id: str
  trace_id: str
  workspace_id: str
  agent_id: str
  actor_type: "sage" | "studio_agent" | "gateway" | "system"
  surface: "chat" | "channel" | "gateway" | "studio_test" | "admin"
  audience: "owner" | "customer" | "internal"
  visibility_level: "off" | "minimal" | "standard" | "full" | "enterprise"
  event_type: str       # see list below
  title: str            # human-readable one-liner
  summary: str          # 1-2 sentence detail
  status: "running" | "completed" | "failed" | "blocked" | "denied"
  timestamp: str
  tool_name: str | None
  channel: str | None
  runtime_mode: str | None
  memory_scope: str | None
  approval_id: str | None
  audit_event_id: str | None
  metadata: dict        # redacted — no secrets, no raw chain-of-thought
```

### Event Types

```
user_message_received     memory_loaded            memory_excluded
planning_started          tool_selected            tool_started
tool_completed            tool_failed              approval_required
approval_approved         approval_denied          gateway_action_started
gateway_action_completed  channel_message_sent     channel_message_received
policy_blocked            quota_blocked            unsafe_url_blocked
final_response_started    final_response_sent
```

---

## Part 4 — UI Placement

| Surface | Transparency Level | What's Shown |
|---------|-------------------|-------------|
| **Sage chat** | Standard (default) | Inline activity pills, expandable "What happened", approval card, trace ID copy |
| **Studio Agent test playground** | Full | Full timeline, policy decisions, memory used/excluded, tool calls, channel simulator events |
| **Customer-facing Studio agent** | Off or Minimal | "Agent is checking…" status only. Never show internal memory/policy details |
| **Owner/admin Activity page** | Admin/Enterprise | Full audit timeline, filters by trace_id/agent/channel/runtime/status |
| **Gateway screen** | Standard | Runtime/gateway action events, outbox uncertain actions, blocked actions, channel health |

---

## Part 5 — Settings Model

| Setting | Values | Default | Scope |
|---------|--------|---------|-------|
| `transparency_mode` | `off`, `minimal`, `standard`, `full` | `standard` (Sage), `off` (Studio customer-facing) | workspace, per-agent |
| `admin_transparency_mode` | `standard`, `full`, `enterprise` | `standard` | workspace |
| `customer_transparency_mode` | `off`, `minimal` | `off` | per-agent, per-channel |
| `show_trace_ids` | bool | `true` (owner), `false` (customer) | workspace |
| `show_tool_names` | bool | `true` | workspace, per-agent |
| `show_memory_usage` | bool | `false` | workspace |
| `show_policy_blocks` | bool | `true` | workspace |
| `show_sources` | bool | `true` | workspace, per-agent |

---

## Part 6 — Implementation Phases

| Phase | Description | Effort |
|-------|-------------|--------|
| **A** | Audit where trace_id already flows and document gaps | Done (this report) |
| **B** | Add `AgentTransparencyEvent` model to `server_modules/` | 1 day |
| **C** | Emit transparency events from Sage turn path (`sage_turn_adapter`, `agent_turn`) | 1-2 days |
| **D** | Emit transparency events from Gateway actions (`gateway_execution_service`) | 1 day |
| **E** | Emit transparency events from Studio Agent test playground | 1 day |
| **F** | Add chat UI activity pills that consume transparency events | 2-3 days |
| **G** | Add admin activity timeline filters (by trace_id, agent, channel, runtime, status) | 1-2 days |
| **H** | Add transparency settings to settings pane | 1-2 days |

---

## Part 7 — Risks

1. **trace_id missing from channel auto-replies** — automatic replies to personal messaging channels need trace correlation. Fix before Phase C.
2. **No memory read audit** — can't prove who accessed what memory. Defer to Phase G+.
3. **Frontend trace_id filtering** — activity timeline hides trace_id. Need admin toggle to reveal.
4. **No per-channel transparency controls** — customer-facing Studio agents currently show too much in test playground but too little in production. Phase H must address this.
5. **Do not expose raw chain-of-thought** — transparency events are action traces + summarized reasoning, not private model reasoning. The `ThinkingRow` toggle already handles this boundary.

---

## Part 8 — First Implementation Phase Recommendation

**Phase A is complete (this report). Start Phase B: `AgentTransparencyEvent` model.**

The event model is purely additive — a new dataclass in `server_modules/` with no behavior change. It establishes the canonical shape before any emission or UI work begins. Total scope: 1 file, ~60 lines of dataclass code, tests. Safe to commit to main.
