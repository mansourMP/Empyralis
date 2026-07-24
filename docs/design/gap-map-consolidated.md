# Consolidated Gap Map — OpenClaw vs Empyralis + Claude-Code-parity build order

> **Terminology note (2026-07-23):** "Sage" is legacy product terminology — the founder ruled the concept removed from the product; the platform has only agents (owner-facing, customer-facing serving the owner, and AskAI). `sage_*` code/variable/string identifiers are legacy code artifacts only, not a live product concept. Anywhere this document's prose says "Sage" or "master," read: the owner-facing agent. This is a lighter-touch terminology note, not a full rewrite — the body below is unchanged and may still use "Sage" throughout.

**Purpose:** the single prioritized build order synthesized from the five per-area gap
reports and the memory/context design doc. Every item is traceable to a source doc
(with its own file:line citations on both codebases). This file is the decision surface;
the source docs are the evidence.

**Source docs (all in `docs/design/`):**
- `gap-ai-operation.md` — the agent loop, tool use, memory, envelope, streaming, scheduling, multi-agent (902 lines)
- `gap-personal-channels.md` — Telegram/WhatsApp/Signal/iMessage/WeChat personal bridges
- `gap-bot-channels.md` — Telegram-bot/Discord/Slack/WeChat-official/Email cloud channels
- `gap-hardware-gateway.md` — provisioning, gateway health, self-update, doctor
- `gap-byo-subscription.md` — BYO providers, CLI subscriptions, rotation, failover
- `memory-context-design.md` — faithful Claude-Code memory + context replication

**Organizing principle:** correctness/security defects first (the product is *silently
wrong* today), then reliability holes (a hosted product strands things with no human at a
terminal), then capability gaps (agents feel less capable than OpenClaw), then
context/memory faithfulness, then polish/breadth/cost. Build sizes are the source docs'.

---

## TIER 0 — Broken correctness / security (fix immediately; all on primary paths)

These are not missing features. They are active defects: the agent does the wrong thing today.

| # | Defect | Evidence | Build | Source |
|---|---|---|---|---|
| 0.1 | **Tool-calling loop caps at ONE round for every provider.** `stream_provider_backed_direct_chat` strips all tool definitions the moment any tool executes once (`direct_chat_generation_service.py:1186-1193`, `executed_any_tools` set at :1502/:1681, never reset). Comment blames a DeepSeek-only bug but there's **no provider check** — fires for all ~15 providers. Iteration 0 can call tools once (possibly parallel); iterations 1-4 have tools stripped and can only synthesize. **Multi-step tool use (call → observe → call again) is architecturally impossible** on the main path — and this IS the main path (`sage_agent_runtime_service.py:3079` calls exactly this fn). VERIFIED this session. OpenClaw's loop (`agent-loop.ts:262-436`) has no turn cap. | direct_chat_generation_service.py:1186 | Small–Med (careful: keep the DeepSeek workaround provider-gated, add regression coverage) | ai-operation P0-1 |
| 0.2 | **Agent runs without its own operating instructions on the main chat surface.** `SOUL.md`/`AGENTS.md`/`TOOLS.md` vanish from context entirely on `sage_chat` (`sage_instruction_compiler_service.py` `build_root_memory_brief_sections`). | memory-context §1 | Small | memory-context #1 |
| 0.3 | **`memory_write` persists secrets with zero redaction** (`agent_memory_tools.py:149-209`) — a live API key / password in a tool result can be saved verbatim to long-term memory. OpenClaw redacts before memory ingestion (`session-files.ts:867`). | agent_memory_tools.py:149 | Small | ai-operation P0-2 |
| 0.4 | **WeChat/WeCom is fully-written dead code.** `routes_wechat_official.py` (6.6KB) + `wechat_official_service.py` (35KB) implement the full Official-Account/WeCom protocol (signature verify, XML, token lifecycle) but `server.py` **never imports or mounts the router** (`grep -c wechat server.py` = 0; Telegram's router IS mounted, server.py:405). VERIFIED this session. The channel you've asked about ~20× is 2 wiring lines + a connect form away from live. | server.py (no mount) | Small | bot-channels CRITICAL |

---

## TIER 1 — Reliability holes (hosted product; no human watching a terminal)

| # | Gap | Build | Source |
|---|---|---|---|
| 1.1 | **Self-update has no health gating** — post-restart "check" is a bare `isPidAlive()` after 8s (`gateway-restart-handoff.ts`), not a real reachability/heartbeat probe. A bad artifact can strand a box "process alive, never reconnected" while the UI shows updated:true. OpenClaw gates restart on `doctor --fix` + `waitForGatewayHealthyRestart()` (4 gates). `GatewayDoctorRuntime` already exists — just call it post-handoff. | Medium | hardware H1 |
| 1.2 | **No in-product gateway restart** — the UI literally tells the operator to SSH in and `systemctl restart` (`hardware/[gatewayId]/page.tsx:693`). The most common non-technical ask ("restart my agent's computer") requires SSH or a full VPS destroy+recreate. Handoff machinery already exists; needs a `gateway.restart` capability + route + button. | Small–Med | hardware H2 |
| 1.3 | **Telegram hosted bot has no 401 circuit breaker** — a revoked token causes an infinite retry hammer instead of detect-and-suspend. | Small | bot-channels |
| 1.4 | **Discord has no outbound rate-limit retry and no gateway-reconnect supervision** — a 429 fails hard; a dead websocket never restarts. | Medium | bot-channels |
| 1.5 | **BYO API-key rotation on rate-limit** — one key per provider; a 429 has no second credential to fail over to. | Medium | byo 5.1 |

---

## TIER 2 — Capability gaps (agents feel less capable than OpenClaw)

| # | Gap | Build | Source |
|---|---|---|---|
| 2.1 | **No mid-turn steering / interrupt** — a new inbound message during an active turn waits on a per-thread lock (`sage_reply_dispatcher.py:40-76`) until the whole turn finishes. OpenClaw has `Agent.steer()` + `QueueMode` (steer/interrupt/abort). | Med–Large | ai-operation P0-3 |
| 2.2 | **No parallel tool execution** — strictly sequential even when the LLM requests several calls at once. OpenClaw is parallel-by-default. | Medium | ai-operation P1-4 |
| 2.3 | **No progressive channel delivery** — replies to Telegram/Discord/Slack/bridges are fully buffered until complete. OpenClaw block-chunks at 800–1200 chars. | Medium | ai-operation P1-5 |
| 2.4 | **No interactive buttons anywhere.** Telegram: reply-keyboard only, no `inline_keyboard`/`callback_query`. Discord: button clicks parsed then dropped (`discord_bot_runtime_service.py:590-603`). Slack: `interactivity:false` at the manifest level. | Medium | bot-channels |
| 2.5 | **No reactions / reaction-based approvals** platform-wide — an approval flow ("👍 to approve") is impossible on any channel. | Medium | personal-channels |
| 2.6 | **Quoted-reply context is invisible to the agent** — Telegram/WhatsApp: the agent can't see *what* a user replied to. WhatsApp outbound quoting is **dead code** (near-free fix). | Small–Med | personal-channels |
| 2.7 | **No ephemeral multi-agent delegation** — `fleet_message_agent` is fire-and-forget, inbox-only, no blocking/reply-back. OpenClaw has `sessions_spawn`/`sessions_send` (async spawn + steer-into-running + block-for-reply). **Biggest single capability gap.** | Large | ai-operation P1-6 |
| 2.8 | **No reasoning-visibility toggle** — reasoning is unconditionally stripped; the "Thinking…" UI is a hardcoded fake label. OpenClaw has two independent axes (effort vs visibility, `/think` vs `/reasoning`). | Small–Med | ai-operation P1-7 |

---

## TIER 3 — Context / memory faithful Claude-Code replication (compounds every turn's quality)

| # | Gap | Build | Source |
|---|---|---|---|
| 3.1 | **Collapse the two context-assembly pipelines into one** — `direct_chat` still uses old full-file loading (`workspace_context_memory_adapter.py`) while `sage_chat` uses the new index-only path; the same workspace behaves differently per surface. | Medium | memory-context #4 |
| 3.2 | **Rewrite the kernel prompt's memory rule** to teach index + topic-files instead of flat one-fact-per-line appending to `MEMORY.md`. The topic-file tree (`agent_memory_tree_service.py`) already works; the agent is just never told to use it. | Small | memory-context #2 |
| 3.3 | **Over-budget self-correction for `MEMORY.md`** — currently silently truncates at 4,800 chars with no signal, unlike Claude Code's explicit "rewrite your index" error. | Small–Med | memory-context #3 |
| 3.4 | **Literal inbound envelope** (channel/sender/timestamp prefix, ported from OpenClaw's `formatInboundEnvelope`, kept in the *user* role as untrusted context per their injection-defense boundary). Sender identity currently only gates tools invisibly. Directly closes tasks #36/#37. | Medium | memory-context #6 |
| 3.5 | **Delete the legacy regex-goal memory tool surface** (`agent_memory_tools.py` + `tool_broker.py` `_dispatch_memory_tool`) — a second, fragile memory path beside the real typed one; repoint the one live UI route (`routes_fleet.py:607`) first. | Small | memory-context #5 |
| 3.6 | **Fix the `REFLECTION.md` self-contradiction** — scaffold says "not loaded every turn," the code comment above says the opposite. Bundle with 3.1. | Trivial | memory-context #7 |

---

## TIER 4 — Polish / breadth / cost (last; several optional)

| # | Gap | Build | Source |
|---|---|---|---|
| 4.1 | iMessage doesn't reach OpenClaw's *basic* mode — `attachments:false`, no media send. | Medium | personal-channels |
| 4.2 | Slack auto-reply is unthreaded, block-less, no retry (threaded/rich send fns exist, just uncalled, `connectors_actions.py:1232`); inbound file/image attachments dropped. | Small–Med | bot-channels |
| 4.3 | Diagnostics-export bundle (sanitized logs+config+health zip for support). | Medium | hardware H3 |
| 4.4 | `vertex` has no frontend model-picker entry (catalog drift, quick fix). | Small | byo 5.6 |
| 4.5 | OAuth token refresh for CLI-subscription re-auth. | Medium | byo 5.2 |
| 4.6 | Prompt-cache stable/dynamic content split (cost/latency, borrowed from OpenClaw's `SYSTEM_PROMPT_CACHE_BOUNDARY`). | Medium | memory-context #8 |
| 4.7 | Provider/regional breadth (15 vs 45+), configured model-fallback chains, multiple named credentials per provider. Low per-item leverage; fallback chains arguably a deliberate non-goal. | Large aggregate | byo 5.3/5.4/5.5 |

---

## DON'T BUILD — OpenClaw doesn't have these either (confirmed by source dig)

- **Per-message sender/channel attribution inside the memory index** — OpenClaw's `memory_index_chunks` schema has no sender/speaker/channel columns. Build the inbound *envelope* (3.4); do **not** chase an index-attribution feature neither product has.
- **A general autonomous-action approval gate** — OpenClaw's `approvals.exec/plugin` only *relay* a different product's (Claude Code/Codex) CLI approval UI into chat; it is not a native gate. (And Empyralis's `bounded_scheduler_service.py:658-678` already has a real native approval gate for privileged self-proposed wake-ups — we're ahead here.)
- **Cost/budget-based loop termination** — OpenClaw defines an SDK hook (`shouldStopAfterTurn`) but never wires it.

---

## WHERE WE ARE ALREADY AHEAD OR AT PARITY — do not re-solve

- **Cloud VPS provisioning**: fully automated OAuth/token multi-cloud (`vps_provisioning_service.py`); OpenClaw hands this to the human. **Ahead.**
- **In-gateway doctor** (detect→repair→re-validate, 5 checks, UI trigger); **process crash guards**; **live capability re-advertising**; **channel reconnect that never permanently gives up** (deliberate hosted-product choice vs OpenClaw's give-up-after-10). Reliability wave already landed these.
- **Bounded-scheduler native approval gate** — genuinely ahead of OpenClaw.
- **Telegram 503-forced redelivery**; **Discord DB-enforced one-bot-per-agent** (unique index + host lock); **Slack dynamic multi-workspace matching + OAuth refresh**; **Email provider breadth** (raw SMTP + OAuth Gmail/M365 vs OpenClaw's Gmail-only closed binary).
- **Conversation compaction**: near-identical design (same 16384-token reserve; module notes it's modeled on OpenClaw). **Parity.**

---

## Recommended sequencing

1. **Tier 0 now** — all small except 0.1; all on primary paths; 0.4 (WeChat) is begged-for and 2 lines. 0.1 (tool loop) is the one requiring care (provider-gate the strip, regression-test DeepSeek) — highest leverage of the whole map.
2. **Tier 1** — reliability, mostly small/medium; matters for the "is our hardware reliable" worry.
3. **Tier 2** — capability; 2.7 (multi-agent delegation) is the big one, do it deliberately.
4. **Tier 3** — memory/context faithfulness; cheap and compounds quality.
5. **Tier 4** — polish/breadth/cost; several optional.

Cross-reference to existing task list: 0.4→#12, 1.x→#16 family, 2.4/2.5/2.6→#17, 3.4→#36/#37, 4.1→#13, 4.4→#30.
