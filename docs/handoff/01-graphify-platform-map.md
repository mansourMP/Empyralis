# Prompt: Produce a Complete Platform Map of Empyralis

This prompt is for a Claude Code session. You are mapping the ENTIRE Empyralis platform — every subsystem, every file of significance, every connection between them. The output is a document an outside engineer can read cold and understand the whole system.

## Context you need first

Read these files before doing anything else:

1. `docs/PLATFORM-MAP.md` — the current architecture map and file index (what's deployed; this file is what this very prompt originally produced — subsequent sessions have kept it updated in place rather than regenerating it from scratch). This is your baseline.

2. `docs/graphify-report.md` — the auto-generated knowledge graph report from the last `graphify update .` run. Has node/edge/community counts, god object rankings, import cycles, isolated nodes, thin communities, low-cohesion communities, and inferred edges. This is your raw data.

3. The memory files in `.claude/projects/-Users-mansur-empyralis/memory/` — especially:
   - `empyralis-platform-vision.md` (north star, tiers)
   - `v2-rebuild-2026.md` (why there are two backends)
   - `channel-strategy.md` (channel families)
   - `tool-model-shell-first.md` (tool philosophy)
   - `safety-and-multiagent-shape.md` (blast radius, isolation)
   - `verified-status-channels-hardware.md` (F1 ground-truth audit — what actually works vs what was overclaimed)
   - `agent-as-worker-no-approval.md` (governance model)
   - `agent-vs-platform-voice.md` (pigeon theory)
   - `ai-credit-model-no-fallback.md` (credit model)

4. The Linear documents (use the Linear MCP tools to fetch these):
   - PLATFORM OVERVIEW (slug: `platform-overview-empyralis-one-agent-one-service-many-configurations-86a18989075a`)
   - Agent model (slug: `product-shape-agent-model-one-agent-class-user-facing-configurations-09aad1212721`)
   - Channel families (slug: `product-shape-channel-families-cloud-channels-gateway-required-per-87f79a94df41`)
   - Execution layers (slug: `product-shape-execution-layers-brain-hands-memory-8293a6c89488`)
   - MCP integration (slug: `product-shape-mcp-integration-cloud-to-cloud-no-user-hardware-b8322eecf6d0`)
   - Supported MCP apps (slug: `product-shape-supported-mcp-apps-30-wired-6-removed-9c37c3a4e8cb`)
   - AI provider strategy decision (slug: `decision-ai-provider-strategy-platform-credits-byok-one-road-no-silent-2f13c73882e7`)
   - Agent simplification decision (slug: `decision-agent-simplification-synthesis-from-anthropic-openai-research-ce005e458c1e`)

## What to produce

A COMPLETE platform map. This is not a summary. This is exhaustive. The document should live at `docs/handoff/PLATFORM-MAP.md`.

### Part 1: Architecture Diagram (text-based)

Reproduce and update the ASCII architecture diagram from `docs/PLATFORM-MAP.md` Part 1. Verify it against the current state of the code (not against what the doc claimed days ago). If anything changed, fix it. Include:

- Consumer surfaces (channels — all of them, not just the 3 that work)
- Control plane (the real files, not the ideal ones)
- Execution runtimes (cloud, Gateway, VPS — mark what's proven vs skeleton)
- Data flow from inbound message to outbound response

### Part 2: Complete File Map — Every Significant File

For each of these directories, list EVERY file with its actual purpose (not guessed — verified by reading the file):

**`server_modules/`** (~190 files — production backend, what actually runs on the VPS):
- Group by subsystem: agent runtime, chat/direct-chat, channels/routing, connectors, MCP/registry, memory, tools/brokering, vault/OAuth, gateway, hardware, runtime/sessions, workflows, billing/quotas, governance/safety, utilities.
- For each file: one line stating what it actually does. No guessing — read imports, function names, and docstrings.
- Mark files that are dead (zero callers, zero route registrations in server.py).

**`frontend/`** (the actual UI — legacy frontend rewired onto the backend):
- `app/` — every page, every layout, every API route.
- `lib/workspace/` — every component, every service, every pane. Include the `codex-chat/` subdirectory fully (it was undocumented until 2026-07-02).
- `lib/ui/` — design system primitives.
- Flag `frontend/v2/` as a dead skeleton (build artifacts only, no source — per Linear doc it dies once legacy frontend is rewired).

**`empyralis-gateway/src/`** (Node.js — the Gateway that runs on user hardware):
- Every file. Group by subsystem: channels (telegram, whatsapp, local-bridge), supervisor client, browser, pairing, protocol, runtime, health.

**`empyralis-supervisor/src/`** (Rust — policy kernel, uncompiled):
- Every file. Note that the binary has NEVER been compiled (per F1 audit).

**`empyralis-runtime-kernel/src/`** (Rust — execution runtime):
- Every file.

**`server/`** (v2 target backend — skeletal, nowhere near feature parity):
- Every file. Compare against `server_modules/` and explicitly list what's MISSING (e.g. "no channel router yet — only a stub in channels/router.py").

**`legacy/`** (v1 reference, not yet pruned to clean backend+frontend split):
- Top-level contents. Note what's a copy vs what's unique.

**`shared/`** (API contracts, design tokens, nav manifest):
- Every file.

**Configuration and deploy:**
- `config/`, `deploy/`, `scripts/`, `mcp.json`, `Dockerfile.*`, `render.yaml`, `main.py`, `server.py`, `mcp_server.py` — what each does.

### Part 3: Subsystem Connection Map

Using the graphify knowledge graph (`graphify-out/graph.json` — parse it directly, it's JSON), trace how every major subsystem connects:

1. **Entry points** — what kicks off a turn? List every file that receives an inbound message (Telegram webhook, Discord interaction, Slack event, Gateway WSS message, VPS poll, web chat POST, direct API call). For each, trace the call chain to `agent_turn.py` or `turn_runtime.py`.

2. **The turn engine** — `agent_turn.py` → `turn_runtime.py` → what branches from there? Map the direct-chat path vs the durable-run path. Show every service each path touches.

3. **Tool dispatch** — when the LLM says "call tool X," what happens? Trace from the LLM response through `tool_broker.py` → MCP registry OR Gateway WSS OR VPS worker. Show the full tree.

4. **Channel response** — when the agent produces a reply, how does it get back to the user? Trace from agent output through reply dispatch → channel adapter → transport delivery. Show the full tree.

5. **OAuth/MCP connector flow** — user clicks "Connect Gmail" → OAuth dance → token vault → MCP server registration → tool discovery → approval → invocation. Every file in the chain.

6. **Memory flow** — agent writes memory → where does it go? Agent reads memory → what does it see? Three-tier scoping (config/outputs/private). Show the files.

7. **Gateway connection lifecycle** — Gateway install → pairing → WSS connect → capability advertisement → command dispatch → result return. Every file.

8. **VPS worker lifecycle** — worker install → HTTP poll → claim → execute → heartbeat → result. Every file.

### Part 4: God Objects and Fragility Points

From the graphify data:

1. **God objects** — list the top 20 nodes by edge count. For each, explain what would break if it changed. `PATH` (657 edges, 80+ communities) needs special attention.

2. **Import cycles** — list ALL cycles (the graphify report from 2026-06-30 found 19). For each, state the files involved and what kind of refactor would break the cycle.

3. **Dead code candidates** — from the isolated nodes list (1,079 nodes with ≤1 connection), audit the top 50 by file size. Each one is either: genuinely dead (delete), dynamically called (document the dispatch mechanism), or test-only (mark as such).

4. **Low-cohesion communities** — the 2 communities with cohesion 0.02 (Agent Settings UI, 245 nodes; Run Service Management, 175 nodes). What files are in them? Should they be split?

5. **Inferred edges** — 1,617 edges at 0.62 avg confidence. Review the ones involving god objects — a wrong inferred edge misleads everyone.

### Part 5: Channel System — Truth Table

For every channel (Telegram bot, Telegram personal, Discord bot, Discord personal, Slack, WhatsApp Business, WhatsApp personal, Signal, iMessage, WeChat, Email/Gmail, Email/SMTP, Web Chat, Apple Messages for Business, Teams, Matrix):

| Column | What to fill |
|--------|-------------|
| Channel | Name |
| Transport | How messages actually move (webhook? WSS? poll? HTTP?) |
| Status | PROVEN (E2E test passed) / WIRED (code exists, untested) / SKELETON (partial) / DEAD (route exists but not mounted) / PLANNED (no code) |
| Session owner | cloud_connector / paired_gateway / self_hosted_node / none |
| Routes through Sage? | yes / no / partial |
| Requires hardware? | yes / no / optional |
| File(s) | Exact file paths that handle this channel |
| Known bugs/gaps | From verify-status-channels-hardware memory and F1 audit |

Cross-reference every claim against the ACTUAL code. The F1 audit found that a prior map overclaimed — do not repeat that.

### Part 6: MCP/Apps Layer — Truth Table

For every connector (the ~38 in the platform, including the 30 verified ones from MAN-14):

| Column | What to fill |
|--------|-------------|
| App | Name |
| Provider | google_workspace / github / notion / linear / slack / etc |
| OAuth? | yes / no |
| MCP endpoint | URL if known |
| MCP tools discovered | count and list if available |
| Frontend shows it? | yes / no / partial |
| Backend can invoke it? | yes / no / untested |
| Credential in vault? | yes / no |
| Status | WIRED / OAuth-only-no-MCP / frontend-only / backend-only / DEAD |

### Part 7: Violations — The Full Catalog

Reproduce the 75 violations from `docs/PLATFORM-MAP.md` Part 7. Then add any NEW ones you find during your audit. Categories:

- "I"/"my" strings (platform impersonating agent — pigeon theory violation)
- "Your"/"you've" strings (same)
- Channel logic bleeding into control plane (channel names hardcoded in agent brain)
- Gateway logic misplaced (channel messaging bundled with hardware execution)
- Structural problems (duplicated MCP URLs, hardcoded frontend types, 12-file touch for new channel)

Each violation: exact file, exact line, exact string, what it should say instead.

### Part 8: The Gap — Current vs Target

The Linear PLATFORM OVERVIEW (2026-07-01) declares the target architecture:
- ONE Agent class
- ONE channel router
- ONE OAuth vault
- ONE MCP client
- ONE session store
- ONE memory system
- ONE remote-hands protocol
- `server/` as THE backend
- `legacy/frontend` rewired onto `server/` as THE UI
- `server_modules/` → reference-only (eventually deleted)
- `frontend/v2/` → deleted

Compare this against reality. For each "ONE primitive" in the target, count how many ACTUAL implementations exist in `server_modules/` today. E.g. "channel router": are there 3? 5? 12? List every duplicate.

### Part 9: What's Missing for "One Real User"

Per the platform vision (`validate-with-one-user` memory): the goal is ONE real user with a real workflow who finds it useful enough to come back the next day. What's actually blocking that? List every gap between "deployed and proven" and "one user can onboard, connect something real, and get value."

## Rules

1. **Verify, don't assume.** Read files. Check imports. Trace call chains. The F1 audit already proved a prior map overclaimed — do not repeat that.

2. **Mark uncertainty.** If you can't verify something (e.g. a route that requires a live API key to test), say so explicitly. "Status: UNVERIFIED — needs live Gmail OAuth token to confirm" is better than a guess.

3. **Cite everything.** File:line for every claim. "The Telegram bot webhook is at `server_modules/routes_sage_telegram_hosted.py:142`" not "Telegram uses webhooks."

4. **No summarization shortcuts.** This is an exhaustive map. If a directory has 190 files, list 190 files. If a subsystem has 15 steps, trace all 15.

5. **Output to `docs/handoff/PLATFORM-MAP.md`.** Write it there. Make it readable — sections, tables, ASCII diagrams. An outside engineer should be able to read this file cold and understand the entire platform.

6. **Graphify first.** Before doing anything else, run `graphify update .` in the repo root to refresh the AST graph. Then `graphify cluster-only .` to regenerate GRAPH_REPORT.md with fresh community analysis (this costs API credits — confirm with the user before running cluster-only; update alone is free and instant). The graph is your primary data source for Part 3 and Part 4.

7. **This will take multiple turns.** That's fine. Work through it systematically. Don't rush.
