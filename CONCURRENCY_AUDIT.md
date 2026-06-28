# Empyralis Concurrency Audit — Agent Entry Points — 2026-06-28

## 1 — Full Call Map

| Channel | Entry route | → | Dispatch function | → | Agent call | File:Line | Awaited? |
|---------|-----------|----|------------------|----|-----------|-----------|----------|
| Telegram Hosted | `routes_sage_telegram_hosted.py:168` | → | `dispatch_sage_reply_safe` | → | `execute_sage_turn` | `sage_reply_dispatcher.py:212` | ✅ `await` — blocks |
| Telegram Personal | gateway WS → `agent_channel_router.py` | → | `connectors_actions.py:1356` | → | `execute_sage_turn` | `connectors_actions.py:1356` | ✅ `await` — blocks |
| Discord | `discord_bot_runtime_service.py:319` | → | `dispatch_command` → inline | → | `execute_sage_turn` | `discord_bot_runtime_service.py:392` | ✅ `await` — blocks |
| Slack | `routes_slack.py:111` | → | direct inline | → | `execute_sage_turn` | `routes_slack.py:111` | ✅ `await` — blocks |
| WeChat | `routes_wechat.py:50` | → | direct inline | → | `execute_sage_turn` | `routes_wechat.py:50` | ✅ `await` — blocks |
| iMessage | `routes_imessage.py:50` | → | direct inline | → | `execute_sage_turn` | `routes_imessage.py:50` | ✅ `await` — blocks |
| Web | `runtime_runs_api.py:974` | → | `turn_ingress_service.start_turn` → `execute_direct_chat_turn_request` | → | `execute_sage_turn` | `direct_chat_service.py:347` | ✅ `run_until_complete` — blocks producer thread |
| Gateway ACP | `routes_gateway.py:3414` | → | inline | → | `handle_sage_chat` | `routes_gateway.py:3484` | ✅ `await` — blocks |
| /api/sage/chat | `sage_chat_api.py:161` | → | inline | → | `handle_sage_chat` | `sage_chat_api.py:225` | ✅ `await` — blocks |
| Voice | `voice_notification_policy_service.py:235` | → | direct inline | → | `execute_sage_turn` | `voice_notification_policy_service.py:235` | ✅ `await` — blocks |

**Every call is `await`-ed inline.** Nothing is fire-and-forget. Every transport handler blocks until the turn completes.

---

## 2 — Concurrency Protection

### Web path: PROTECTED (per actor)

The session manager (`session_manager/manager.py:270`) gates web turn execution with `self.actor_queue.claim(actor_token)`. The `actor_token` is `{user_id}:{workspace_id}:{thread_id}`. This creates a per-actor `threading.Lock` — only ONE web turn per (user, workspace, thread) executes at a time. Other requests queue.

**File:** `session_manager/actor_queue.py:22` — `SessionActorQueue.claim(actor_key)`  
**Scope:** Web only (entered through `start_turn` → `stream_response_builder` → `build_agent_turn_stream_response`)

### Channel paths (Telegram, Discord, Slack, WeChat, iMessage): UNPROTECTED

No lock. No queue. No semaphore. `dispatch_sage_reply()` at `sage_reply_dispatcher.py:142` calls `execute_sage_turn()` with a bare `await`. If two Telegram messages arrive 200ms apart for the same workspace, they execute **concurrently** — both in the same event loop, both interleaving.

### Within the agent loop: UNPROTECTED for channels

The agent loop (`stream_provider_backed_direct_chat`) appends to `conversation_messages` list. Tool execution uses `ThreadPoolExecutor`. The `tool_loop_session_key` is `sage:{workspace_id}:{trace_id}` — but this is only used for loop detection, not for concurrency gating.

---

## 3 — Concurrent Telegram Messages: Full Trace

Scenario: Two Telegram users send messages to the bot 200ms apart. Both route to `ws_c4601e47c95a`, `thread_id=sage-main`.

```
T=0ms    Message A arrives at Telegram webhook
         → routes_sage_telegram_hosted.py:168
         → dispatch_sage_reply_safe()
         → dispatch_sage_reply() at sage_reply_dispatcher.py:142
         → transport.start_typing()  ← typing indicator ON
         → execute_sage_turn() at line 212  ← AWAITED
         → handle_sage_chat()
         → _run_sage_action_loop_v3()
         → load prior_messages from DB (10 turns)
         → LLM call starts (iteration 0)

T=200ms  Message B arrives at Telegram webhook
         → Same path. DIFFERENT coroutine, SAME event loop
         → dispatch_sage_reply() — SECOND instance
         → transport.start_typing()  ← typing RESTARTED (overwrites A's state)
         → execute_sage_turn() — SECOND concurrent call
         → handle_sage_chat()
         → _run_sage_action_loop_v3()
         → load prior_messages from DB (10 turns)  ← READS SAME MESSAGES as A
         → LLM call starts (iteration 0)

T=2s     Message A's LLM returns: tool_call → shell__exec
         → tool executes via ThreadPoolExecutor
         → tool result appended to conversation_messages[A]

T=2s     Message B's LLM returns: tool_call → shell__exec
         → tool executes via ThreadPoolExecutor  ← CONCURRENT tool execution
         → tool result appended to conversation_messages[B]

T=4s     Message A's LLM synthesis: appends AGAIN to conversation_messages[A]
         → reply "Here's your hardware: Ubuntu..." 
         → appends assistant message to conversation_messages[A]
         → final_reply = "Here's your hardware: Ubuntu..."
         → dispatch_sage_reply sends reply via Telegram
         → transport.stop_typing()  ← typing indicator OFF

T=4.5s   Message B's LLM synthesis: reply "Here's your hardware: Ubuntu..."
         → dispatch_sage_reply sends reply via Telegram

RESULT:  Both messages got replies. Both operated on SEPARATE
         conversation_messages lists (each execute_sage_turn call
         creates its own). DEFAULT RISK: conversation state not
         corrupted because lists are local to each call.

         BUT: typing indicator state corrupted (B restarted it).
         AND: both turns persisted to agent_turns DB table independently.
         AND: the user sees TWO replies — possibly interleaved or
         doubled if the transport send_messages overlap.
         AND: if either turn calls a tool that MUTATES state
         (memory_update, file write), the mutations race.
```

---

## 4 — Race Condition Points

### Race 1 — Thread store (agent_turns table)
- **File:** `control_plane_repository.py:10932` — `INSERT INTO agent_turns`
- **Condition:** Two concurrent turns append to the same `thread_id` (`sage-main`). Both read the same prior messages, both append. The UNIQUE constraint `(tenant_id, workspace_id, thread_id, role, request_id)` prevents duplicate rows but does NOT serialize turns.
- **Severity:** Low. Each `execute_sage_turn` builds its own local `conversation_messages` list. The DB stores both turns correctly — they just appear interleaved in history.

### Race 2 — Typing indicator
- **File:** `sage_reply_dispatcher.py:207-208` — `transport.start_typing()` / `transport.stop_typing()`
- **Condition:** Turn B calls `start_typing()` while Turn A is still running. When A finishes, it calls `stop_typing()` — which kills B's typing indicator mid-generation.
- **Severity:** Low — cosmetic. Telegram shows "Sage is typing..." flickering.

### Race 3 — Tool execution with shared state
- **File:** `direct_chat_generation_service.py:1175` — `_tool_fut.result(timeout=...)`
- **Condition:** Both turns call `shell__exec` concurrently on the SAME gateway. The gateway processes commands sequentially, but the agent doesn't know which turn's tool result corresponds to which request.
- **Severity:** Medium. If both turns run `shell__exec` on the same machine, command output could be interleaved or the wrong output returned to the wrong turn.

### Race 4 — Memory/state mutations
- **File:** `memory_service.py` — `memory_update`, `memory_stage_edit`, `memory_apply_edit`
- **Condition:** Turn A calls `memory_update(filename="MEMORY.md", content="...")` while Turn B calls the same function on the same file. Whichever finishes last wins.
- **Severity:** Medium. Lost writes. The agent from Turn B overwrites Turn A's update without knowing it.

### Race 5 — Workspace metadata writes
- **File:** `sage_agent_runtime_service.py` — `set_persisted_model_preference()`
- **Condition:** Turn A runs `/model gpt-5` while Turn B runs `/model deepseek-chat`. Both write to `workspaces.metadata`. Last write wins.
- **Severity:** Low. The `/model` command is rare and user-initiated. Concurrent `/model` calls are unlikely.

---

## 5 — What prevents catastrophe (partial safety)

1. **Local conversation_messages:** Each `execute_sage_turn()` call creates its own `conversation_messages` list (inside `_run_sage_action_loop_v3`). Two concurrent turns don't share the same message buffer.

2. **DB unique constraint:** `(tenant_id, workspace_id, thread_id, role, request_id)` prevents true duplicates in `agent_turns`.

3. **Web gate:** The session manager's `actor_queue.claim()` serializes web turns per (user, workspace, thread). Web users can't race against themselves.

4. **Gateway sequential execution:** The gateway (`empyralis-gateway`) processes tool.invoke frames sequentially on its WebSocket. Tool execution is serialized at the gateway level — only one tool runs at a time.

---

## 6 — Architecture assessment

**The web path is correctly serialized.** The session manager's per-actor lock ensures one turn at a time per (user, workspace, thread).

**The channel paths are unprotected.** Telegram, Discord, Slack, etc. all bypass the session manager. Two concurrent messages produce two concurrent `execute_sage_turn()` calls. This is the correct default for independent users in different chats — but for the same `sage-main` thread, it's a soft race.

**The fix:** The `dispatch_sage_reply` function (shared by ALL channels) should acquire a per-workspace lock before calling `execute_sage_turn()`. The existing `SessionActorQueue` pattern works — it just needs to be instantiated at the channel dispatch level, not only inside the web session manager.

**One-line mitigation (not implementation, just the concept):**
```python
# In dispatch_sage_reply(), before execute_sage_turn():
async with _sage_turn_lock(workspace_id):  # serialize per workspace
    result = await execute_sage_turn(...)
```

The lock would be a global `dict[str, asyncio.Lock]` keyed by `workspace_id:thread_id`, ensuring only one turn per (workspace, thread) executes at a time across ALL channels including web.
