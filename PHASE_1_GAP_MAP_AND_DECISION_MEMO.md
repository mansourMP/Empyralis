# Phase 1 — Gap Map + Architecture Decision Memo

> Read-only. No code changed. Wait for owner decision on Option A vs B before
> any implementation.

---

## Deliverable 1 — Gap Map: Current State → Desired Spine

Desired spine =
**(a)** ONE clean inbound Main Agent path,
**(b)** channel plane cleanly separated from execution plane,
**(c)** governance enforced through a SINGLE mandatory choke point,
**(d)** desktop/tool actions not shipped one-click-at-a-time.

| # | Gap | Current state | Target state | Effort | Risk | Needs owner decision? |
|---|---|---|---|---|---|---|
| **1** | **Four inbound pipelines for the Main Agent** | Path A (Simple Sage Chat), Path B (Gateway Personal), and Path D (Hosted Telegram Bot) all call `handle_sage_chat()` but through completely different entry points, auth, and delivery. Path C (Connector Webhooks) routes to deployed specialist agents — not Sage — and is a separate concern. | One Sage ingress that handles all Main Agent channels. Single `execute_sage_turn()` entry function. Path C stays separate since it serves a different agent class (deployed specialists). | **M** | **M** — could break existing channel delivery if not careful | No — clear win. Technical decision only. |
| **2** | **Channel plane entangled with execution plane** | Channel route handlers (`routes_sage_telegram_hosted.py`, `routes_wechat.py`, `routes_slack.py`) directly import and call `handle_sage_chat()`. Gateway channel handlers (`agent_channel_router.py`) directly call gateway protocol dispatch. Channels reach into both the agent runtime AND the hardware gateway. | Channel handlers only normalize the inbound message and hand it to a "Main Agent ingress" layer. The ingress layer (not the channel) decides: text reply, tool-using loop, or specialist routing. Channels never touch hardware dispatch directly. | **M** | **M** — requires restructuring how channels call the agent runtime, but the underlying functions already exist | No — this is the architectural cleanup the brief demands |
| **3** | **Governance is spot-check, not a single choke point** | Three different approval/enforcement implementations: `decide_agent_computer_action()` in Sage path (line 527), separate inlined checks in `routes_gateway.py`, separate checks in `self_hosted_node_adapter.py`. Kill switch is checked consistently but at multiple call sites. No middleware or decorator guarantees coverage. | One mandatory governance gate that EVERY action passes through before execution. Single function: `evaluate_action_policy(action, context) → allow | approve | block`. All execution paths call this ONE function. The existing policy rules, kill switch, and Rust kernel enforcement are preserved — they just get a single entry point. | **M** | **L** — governance is a safety-critical path; getting the single choke point wrong could either block legitimate actions or allow dangerous ones | No — this is the brief's non-negotiable guardrail |
| **4** | **Per-action shipping for local hardware (chatty)** | For the My Computer profile, every mouse click, keystroke, and screenshot is a separate cloud→node WebSocket round-trip. The LLM on the cloud decides each micro-action, ships it, waits for the result, then decides the next. A simple "open Chrome and search for X" can be 6+ round-trips. | Node can execute multi-step sub-tasks locally. Cloud sends: "navigate to URL, type search term, press Enter, take screenshot." Node executes the sequence, returns the final screenshot. This is the "smarter hands" approach — the supervisor gets a small action-chaining capability without needing a full LLM runtime on the node. | **S-M** | **L** — adding a chainer to the supervisor is straightforward (~200-400 lines of Rust) but the design of what sub-tasks look like needs care | **YES** — this is the core Option A vs B question |
| **5** | **No public web endpoint for consumers** | The web UI (`frontend/`) is the owner's control room — a Next.js admin dashboard. There is no endpoint where a consumer can message the Main Agent through a website. | A public-facing web chat endpoint. Consumer visits a URL, is prompted for identity (optional), sends a message, gets a reply. This is a channel like any other — it goes through the same Sage ingress. | **S-M** | **L** | **YES** — which channel to make bulletproof first (Phase 2 proof)? |
| **6** | **WeChat connector is bridge-dependent, no hosted option** | WeChat only works via a local bridge (Path B). No hosted/cloud WeChat option exists. | Add a hosted WeChat ingress (similar to the Telegram Hosted Bot model) for consumers who don't run their own bridge. | **M** | **M** | **YES** — priority depends on channel strategy |

### Notes on effort ratings

- **S (Small):** 1-3 files changed, well-understood, low risk. Example: adding a wrapper function.
- **M (Medium):** 3-8 files changed, some restructuring, moderate risk. Example: consolidating pipelines.
- **L (Large):** New components, architectural shifts, high risk. Example: building a node-resident agent runtime.

---

## Deliverable 2 — Architecture Decision Memo: Option A vs Option B

### What the investigation revealed (facts that shape this decision)

**Fact 1: The visual desktop tools REQUIRE a tight perceive-act loop.**
Every visual interaction is: see screen → decide → click/type → see result. The
Rust supervisor's `click` with text-finding mode already does screenshot+OCR+click
in one call as an optimization, but the model must still verify the result before
the next action. This loop is **inherent to GUI automation** — it's not an
artifact of the cloud architecture.

**Fact 2: The non-visual tools do NOT require a tight loop.**
Shell commands, file operations, memory search, web fetch, API calls — these can
be planned as multi-step sequences and executed without a perceive-act cycle.
They are currently shipped one-at-a-time from the cloud unnecessarily.

**Fact 3: The node today has ZERO intelligence.**
Every node-side component (gateway, supervisor, runtime kernel) handles exactly
one atomic action per call. There is no chaining, no planning, no autonomy.
Adding intelligence to the node means building something new in every case — the
question is how much.

**Fact 4: Moving reasoning to the node does NOT add a data-privacy guarantee.**
The LLM call still goes to a cloud provider (DeepSeek, Anthropic, OpenAI) regardless
of where the agent runtime sits. The only privacy difference is whether the prompt
passes through our cloud server or goes directly from the node to the model provider.
Screenshots and file contents are in the prompt either way.

**Fact 5: The execution already happens on the user's hardware.**
Files, screen capture, mouse, keyboard, shell — all tool execution runs on the
node through the supervisor. The privacy win ("your data stays on your hardware")
already exists today for tool actions.

---

### Option A — "Cloud brain, smarter hands" (RECOMMENDED)

**What changes:**
1. **Consolidate the Main Agent inbound path** (Gap #1, #2): Paths A, B, D merge into one Sage ingress. Every Main Agent message flows through `execute_sage_turn()` → `handle_sage_chat()`. Channels only normalize and hand off — they never call gateway dispatch directly.
2. **Build a unified governance gate** (Gap #3): Single `evaluate_action_policy()` function. All execution paths (Sage chat, gateway tools, VPS commands, browser actions) call this ONE function. The existing policy rules, kill switch, Rust kernel enforcement, and approval logic all sit behind it — they don't change, they just get a single entry point.
3. **Add action chaining to the node** (Gap #4, non-visual): A new small capability in the Rust supervisor (~200-400 lines) that accepts a sequence of operations and executes them sequentially with simple control flow (sequence, if-result-contains, retry-on-error). This eliminates per-action round-trips for shell/file/API/browser sequences. The cloud LLM plans the sequence; the node executes it locally.
4. **Keep the per-action path for visual loops**: For screen+mouse+keyboard work, the perceive-act cycle is inherent. We keep the current per-action WebSocket path for visual actions but optimize it by having the click-with-text-finding already bundled (which it is). The "chattiness" here is the nature of GUI automation, not an architectural flaw.
5. **Optionally add a web endpoint** (Gap #5): A simple HTTP endpoint that normalizes a web message and routes it through the same Sage ingress. This is hours of work once the ingress is unified.

**Effort:** Medium (3-5 weeks of focused work).
- Pipeline consolidation: ~1 week
- Unified governance gate: ~1 week
- Node action chainer: ~1 week
- Testing + hardening: ~1-2 weeks

**Risk:** Low-Medium. We are reorganizing existing code, not building new subsystems. The risk is in breaking existing channel delivery during consolidation — mitigated by keeping all existing entry points as thin wrappers around the new unified path during transition.

**What it buys:**
- One clean Main Agent path (the "spine" the brief demands)
- Governance enforced through a single mandatory choke point (the differentiation from OpenClaw, made architecturally solid)
- Dramatically less chattiness for shell/file/API/browser work (a 6-step sequence becomes 1 cloud round-trip instead of 6)
- Clean separation: channels → ingress → agent runtime → governance gate → execution
- The codebase's OWN architecture (three planes) is finally reflected in the code structure

**What it does NOT buy:**
- The agent does NOT work offline. Reasoning still needs cloud connectivity.
- The agent does NOT have lower latency for visual desktop work. Each "see-decide-act" still needs a cloud round-trip for the "decide" step (though the "act" can be a bundled chain).
- The agent does NOT keep your prompts off our cloud server. The LLM call path is unchanged.

**Consumer experience:** Identical to today for text chat. Faster for "do a sequence of things" requests (the agent can batch shell/file/web operations without pausing between each step). Visual desktop automation works the same as today — the tight loop is inherent.

---

### Option B — "Node-resident agent"

**What changes:**
Everything in Option A (the spine work is still needed regardless), PLUS:
1. **Build a local agent runtime**: A new component on the node (likely Rust, in or alongside the supervisor) that runs a tool-use loop locally. It receives a task/message from the cloud, calls the local LLM (or proxies to a cloud provider), executes tools via the supervisor, and returns results.
2. **Move prompt assembly to the node**: The system prompt, memory context, profile context, and tool definitions must be assembled on the node instead of the cloud.
3. **Replicate observability**: The node must emit trace events, transparency events, and audit records that flow back to the cloud.
4. **Dual governance**: Policy is enforced on the node (for autonomy) AND reported to the cloud (for the transparency timeline and kill switch). The existing Rust kernel decision functions provide a foundation for this.
5. **Handle offline/retry**: The node needs to handle cloud disconnection gracefully — queue results, retry, sync when reconnected.

**Effort:** Large (8-14 weeks of focused work).
- Option A spine work: 3-5 weeks (prerequisite)
- Local agent runtime: 3-5 weeks
- Prompt assembly on node: 1 week
- Observability bridge: 1-2 weeks
- Dual governance: 1-2 weeks
- Testing + hardening: 2-3 weeks

**Risk:** High. We are building a second agent runtime (node-side) that must stay in sync with the cloud-side runtime. Two codebases implementing the same logic (tool-use loop, prompt assembly, governance) creates a maintenance burden and risk of divergence. Debugging node-side issues is harder than cloud-side issues.

**What it buys (beyond Option A):**
- **Lower latency for visual desktop automation**: The "decide" step in the perceive-act loop runs locally — no cloud round-trip. For tight GUI work, this could be noticeably snappier.
- **Theoretical offline capability**: The agent could keep working during brief cloud disconnections (though the LLM call still needs internet).
- **Privacy posture**: Prompts go directly from the node to the model provider, bypassing our cloud server. This is a meaningful privacy improvement IF the owner wants to market that.

**What it does NOT buy (honest assessment):**
- It does NOT eliminate cloud dependency. The LLM call still goes to a cloud provider. The cloud control plane is still needed for channel message delivery, identity, memory sync, and the transparency timeline.
- It does NOT change the consumer experience for text chat. The 1-2 second cloud round-trip for reasoning is invisible compared to LLM inference time (which is 3-30 seconds regardless).
- It does NOT make the agent "fully local" in any meaningful sense. The model is remote, the channels are remote, the memory is remote. Only the reasoning loop moves.
- It does NOT eliminate the perceive-act cycle for visual work. It only removes one network hop from each cycle. The see→decide→act→verify loop is still there — it's just faster.

---

### Recommendation: Start with Option A, design for Option B later

**Option A gives you 90% of the user-visible improvement for 40% of the effort.**
The main things consumers will notice — faster multi-step execution, consistent
behavior across channels, reliable approvals — all come from Option A. The
remaining 10% (snappier visual desktop loops) only matters for a specific use
case that may or may not be the Main Agent's primary value.

**Option B is not wrong — it's just expensive relative to what it adds on top of A.**
If, six months from now, visual desktop automation proves to be the Main Agent's
killer feature and the latency matters, you can build Option B on top of Option A's
clean spine. The action chainer from Option A is a necessary building block for
Option B anyway.

**What to preserve now so Option B is possible later:**
- The unified governance gate must accept calls from both cloud-side (Python) and
  node-side (Rust) — passed as a signed decision request, not a Python function call.
  The Rust runtime kernel already works this way.
- The channel → ingress handoff must be a data structure (task + metadata), not a
  Python function call. This way a node-resident agent can receive the same structure.
- Keep the `my_computer_agent` binding in the Rust kernel's runtime action decision
  (it's already recognized but returns a block). Don't delete it.

---

## Owner Decision 1: Default Runtime for Simple Users

### The situation

The repo already supports three runtime profiles:
- **Cloud Computer Agent** — we host a sandbox, the agent runs there
- **My Computer Agent** — runs on the user's own Mac/PC (via gateway + supervisor)
- **Self-Hosted Agent** — runs on the user's VPS (via queue + worker)

Simple users do not own a VPS and may not want to install a gateway on their machine.

### Options

**A) Hosted Cloud Computer as default, "Bring Your Own Hardware" as premium tier.** 
Simple users get a cloud sandbox. Power users who care about privacy/control can
install the gateway on their Mac or VPS. This matches how the market works (OpenClaw
is the "bring your own" option; we offer both).

**B) "Bring Your Own Hardware" only.**
Everyone must install something. This limits the addressable market but keeps the
privacy posture pure ("we never touch your data").

**C) Cloud Computer only (no local option).**
Simplest to build but removes the OpenClaw differentiator entirely.

### Recommendation: Option A

- The Cloud Computer Agent already works (BrowserBase, E2B, Daytona, Docker providers exist)
- The My Computer Agent already works (gateway + supervisor, the most developed path)
- The Self-Hosted Agent is partially implemented (queue-based, needs testing)
- Offering both lets you compete with OpenClaw on privacy ("your data, your hardware")
  while not turning away users who just want it to work
- This is exactly the "both, with a default" the brief suggests

---

## Owner Decision 2: How Much of the Main Agent's Value Is Visual Desktop Automation?

### What the code says

From the capability analysis:

| Category | Count in registry | % of LLM-visible tools | Risk level |
|---|---|---|---|
| Visual desktop (screen, mouse, keyboard, OCR, clipboard, app control) | 12 capabilities | ~32% when Agent Computer is online | ALL "critical" — all require approval |
| Non-visual (shell, files, memory, web, API, browser, SaaS connectors) | 47 capabilities | ~68% | Mixed low/medium/critical |

**When the Agent Computer is OFFLINE**, all visual-desktop tools are stripped from
the LLM's tool set. The agent still has ~15 non-visual tools (memory, web search,
HTTP, LLM sub-tasks, image generation). This is the most common state — the agent
computer must be explicitly connected.

**The tight perceive-act cycle is inherent to visual work.** Every GUI interaction
requires: capture screen → analyze → decide action → execute → verify. This cannot
be batched — you must see the result of each action before the next one.

### My estimate from the code (not a guess)

The Main Agent's tool palette is **~70% non-visual, ~30% visual-desktop** by count.
But usage patterns matter more than counts. Based on the action loop code:

- The `_run_sage_action_loop_v3()` first tries `sage_daily_operator_service` — a
  cloud-side recipe executor for common non-visual tasks (search, summarize, file
  operations). This suggests the most common requests are non-visual.
- The action loop v3 has a 25-iteration max — visual desktop work can easily
  consume 10-15 of those iterations in a single perceive-act cycle.
- The `_message_might_need_sage_action_loop()` function gates whether the action
  loop is even entered — many messages get a direct LLM reply with no tools.

**Bottom line: visual desktop automation is ~30% of the tool palette and likely
<20% of actual usage, but it is the most latency-sensitive use case.** When someone
says "show me what's on my screen and click the login button," the per-action
shipping is noticeable. For "search my files for the Q4 report," it's not.

### What this means for Option A vs B

Option A improves latency for the 70% of tools that don't need a perceive-act loop
(shell sequences, file operations, web searches) by batching them into sub-tasks.
The 30% visual tools stay the same — the perceive-act loop is inherent.

Option B improves latency for the visual 30% by removing one network hop per cycle.
But the LLM inference time (3-30 seconds) dominates the total cycle time anyway —
a 100ms network round-trip savings is invisible next to a 5-second model call.

**This reinforces the Option A recommendation.** The visible performance win comes
from batching the batchable work, not from moving the reasoning loop.

---

## Recommended Phase 2 Sequence

Assuming Option A is chosen, here is the ordered implementation sequence:

| Step | What | Why first | Effort |
|---|---|---|---|
| **1** | **Build the unified governance gate** | This is the non-negotiable guardrail. Everything else can be built around it. Single `evaluate_action_policy()` function. | M |
| **2** | **Consolidate Main Agent pipelines** | Merge Paths A, B, D into one Sage ingress. All channels funnel through `execute_sage_turn()`. Keep Path C separate. | M |
| **3** | **Separate channel plane from execution plane** | Channels hand off a task + metadata to the ingress. Channels never call hardware dispatch directly. | M |
| **4** | **Add node-side action chaining** | New `plan.execute` capability in the Rust supervisor. Cloud sends a sequence; node executes locally. Eliminates per-action round-trips for non-visual work. | S-M |
| **5** | **End-to-end proof on one channel** | Prove the full loop on Telegram (or web endpoint): message in → ingress → governance gate → tool execution with chaining → reply out. Approval gate fires on risky action. Kill switch stops it. | M |
| **6** | **Add web endpoint** (if chosen as first channel) | Simple HTTP endpoint that normalizes a web message and routes through the Sage ingress. | S |
| **7** | **Add WeChat hosted ingress** (Phase 3) | Hosted WeChat connector so consumers don't need their own bridge. | M |

---

## Technical Appendix (for reference)

### Key files that will change in Phase 2

**Governance consolidation:**
- `server_modules/policy_service.py:967` — `evaluate_tool_policy_decision()` → becomes the single gate
- `server_modules/agent_computer_approval_decision_service.py:220` — `decide_agent_computer_action()` → merged into the single gate
- `server_modules/kill_switch_gate.py:178` — `evaluate_kill_switch()` → called from the single gate
- `server_modules/safe_mode_service.py:686` — `resolve_capability_disable_state()` → called from the single gate
- `server_modules/capability_risk_classifier_service.py:346` — `classify_capability_risk()` → called from the single gate
- `server_modules/routes_gateway.py:2270` — inlined gateway checks → replaced with call to single gate

**Pipeline consolidation:**
- `server_modules/routes_sage_telegram_hosted.py:88` — webhook → thin wrapper around new ingress
- `server_modules/routes_wechat.py:17` — `wechat_inbound()` → thin wrapper
- `server_modules/routes_imessage.py:17` — `imessage_inbound()` → thin wrapper
- `server_modules/routes_slack.py:62` — `slack_inbound()` → thin wrapper
- `server_modules/agent_channel_router.py:1075` — `handle_gateway_channel_inbound()` → routes to ingress
- `server_modules/sage_turn_adapter.py` — NEW or expanded: the single Sage ingress
- `server_modules/channel_adapter.py:68` — `normalize_sage_inbound()` — stays, used by all paths

**Node-side action chaining:**
- `empyralis-supervisor/src/main.rs` — new `/execute_plan` endpoint
- `empyralis-supervisor/src/capabilities/plan.rs` — NEW: action chainer
- `empyralis-gateway/src/supervisor/capability-router.ts` — register `plan.execute` capability
- `server_modules/hardware_runtime_adapters/gateway_adapter.py` — dispatch `plan.execute` instead of individual actions

**Not touched (governance preserved):**
- `empyralis-runtime-kernel/src/` — all Rust decision functions remain as-is
- `server_modules/agent_computer_policy_service.py` — policy definitions unchanged
- `server_modules/activity_ledger_service.py` — audit trail unchanged
- `server_modules/agent_trace_service.py` — trace unchanged
- `server_modules/secret_redaction_service.py` — redaction unchanged
