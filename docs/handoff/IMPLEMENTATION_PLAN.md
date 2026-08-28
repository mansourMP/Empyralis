# Implementation Plan — Post-Audit


> **HISTORICAL — the baseline this prompt reads no longer exists (2026-08-28).**
> `docs/PLATFORM-MAP.md` was deleted: a commit-pinned 2026-07-13 snapshot
> that had gone stale (122 references to 23 modules that no longer exist),
> per this repository's standing rule that snapshot documents are not kept.
> Every instruction below to read or update it, and every reference to its
> section numbers, is therefore unfollowable. Read this file as a record of
> how the map was produced, not as a work order to run.

**Date:** 2026-07-03  
**Based on:** `concerns.md` audit findings  
**Rule:** fixes go straight to code. Decisions come to you.

---

## Phase A: Unblock the Demo (the 3 hard blockers)

These must be fixed before a single real user can get value. No Phase W makes sense until these work.

### A1. Unblock specialist → Telegram routing

**Problem:** `sage_agent_runtime_service.py` hardcodes `_COMMUNICATION_SCOPES` / `_CONNECTOR_ROUTE_KEYWORDS` so only slack/discord/github route for studio agents. A specialist bound to a Telegram bot returns `channel_unavailable`. The flagship demo is dead.

**Fix:** Add telegram_bot to the studio-allowed channel set. This is a configuration change in the routing keywords, not a new feature — the Telegram bot infrastructure already works for Sage. The specialist just needs to be allowed to use it.

**Files:** `server_modules/sage_agent_runtime_service.py` (routing keywords), `server_modules/agent_channel_router.py` (if gated there too)

**Acceptance:** Create a specialist → bind a Telegram bot token → send a message to the bot → specialist replies.

### A2. Split `EMPYRALIS_MCP_WRITE_ENABLED` to per-workspace

**Problem:** One global env flag gates MCP writes for every workspace. Enable it for one paying customer and a leaked API key from any workspace can create agents/write memory everywhere.

**Fix:** Move the write gate into the API key itself. When creating a key via `POST /api/connections/mcp-keys`, add a `writes_enabled: bool` field. Store it alongside the hashed key. `_resolve_workspace()` returns both workspace_id and whether writes are allowed. Each tool checks the per-key flag instead of the global env var. Keep the env var as a global off-switch (emergency disable for all writes).

**Files:** `server_modules/mcp_server_auth.py` (key schema + resolution), `mcp_server.py` (write tool gating), `server_modules/routes_connections.py` (key creation endpoint)

**Acceptance:** Create two API keys — one with writes, one without. Write tools succeed with the first, fail with the second. Revoking the write key doesn't affect the read key.

### A3. Fix the PLATFORM-MAP.md Gateway contradiction

**Problem:** §5.1 says personal channels "PROVEN via Gateway WSS," §9.2 says "Gateway never compiled/run — no user has ever run Gateway + Supervisor together." Both can't be true. The source-of-truth document contradicts itself on the platform's riskiest claim.

**Fix:** One clean statement. The Node.js Gateway has run in dev and powers Telegram/WhatsApp personal channels (PROVEN). The Rust Supervisor has never been compiled. No external user has run either. Reword both sections.

**Files:** `docs/handoff/PLATFORM-MAP.md` (§5.1, §9.2, §5.4)  
**Also fix:** The 2-channels-vs-7-channels staleness. Map says frontend shows 2; screenshots show 7. Update to current reality.

**Acceptance:** Map sections no longer contradict. Gateway status is clear: what's proven, what's not, who ran what.

---

## Phase B: Fix the Known Bugs (no decisions needed — just fix)

### B1. Fix `hardware_runtime_target_resolver.py` bugs

**Problem:** Two documented bugs still open in the violations catalog (§7.4): `self_hosted_node` falls through to wrong label `cloud_provider`; Gateway-offline fallback loses execution environment info.

**Files:** `server_modules/hardware_runtime_target_resolver.py`

**Acceptance:** Unit test for each bug. Resolver returns correct target for self_hosted_node. Gateway-offline preserves execution environment in the fallback path.

### B2. Fix `discord_personal` lane contradiction

**Problem:** `runtime_lane: personal_gateway` but `session_owner: cloud_connector`. Per audit analysis: the bot-token design is correct (Discord ToS prohibits user-account automation). Fix the metadata — relabel the lane, don't change the session owner.

**Files:** `server_modules/channel_lane_contract_service.py`

**Acceptance:** `discord_personal` metadata is internally consistent. Lane label matches session owner.

### B3. Fix the provider-routing UI confusion

**Problem:** Screenshot 3 shows "DeepSeek through Workspace AI — Needs setup" and "Active / Selected" for DeepSeek API key simultaneously. Two routing states disagree in the shipped UI.

**Files:** `frontend/lib/workspace/` (provider status display), `server_modules/provider_profiles.py` (status resolution)

**Acceptance:** UI shows one consistent status per provider. No "Needs setup" + "Active" at the same time.

---

## Phase C: Safety (high-impact, no new architecture)

### C1. Add kill-switch test

**Problem:** `kill_switch_gate.py` exists as "emergency stop" but has zero tests. The emergency brake is untested.

**Fix:** Write tests for GLOBAL_KILL_KEY, WORKSPACE_KILL_PREFIX, AGENT_KILL_PREFIX. Verify each blocks the correct scope and nothing more.

**Files:** `server_modules/tests/test_kill_switch.py` (new)

**Acceptance:** 3 tests pass: global kill stops everything, workspace kill stops one workspace, agent kill stops one agent. Other workspaces/agents unaffected.

### C2. Add webhook signature verification tests

**Problem:** Webhook signature verification is undocumented for every cloud channel — the most exposed attack surface.

**Fix:** Verify that existing signature checks work. If missing, add them. The connectors (Telegram, Discord, Slack, GitHub) likely already verify signatures — confirm and document.

**Files:** Audit `connectors/telegram/webhook.py`, `connectors/discord_connector.py`, `connectors/slack_connector.py`, `connectors/github_connector.py` for existing verification. Add tests. If any are missing verification, add it.

**Acceptance:** Test for each cloud channel: valid signature passes, invalid signature rejected, missing signature rejected.

### C3. Add kernel integration test

**Problem:** The kernel binary exists (preflight confirms it), but all 108 tests mock `run_runtime_kernel`. 52 policy decisions have zero exercised tests.

**Fix:** Write one integration test that calls the real kernel binary for a simple decision (e.g., validate-policy with a deny_all preset). Proves the binary is real and gives correct answers.

**Files:** `server_modules/tests/test_kernel_integration.py` (new)

**Acceptance:** One test marked `@pytest.mark.kernel` that invokes the real binary, passes real JSON, and asserts on the decision. If kernel binary absent, test skips (existing conftest behavior).

### C4. Per-workspace MCP write authorization

**Already covered in A2.** No additional work here.

---

## Phase D: Decision Required

These cannot proceed without your call. Each has a recommendation.

### D1. The 600-second run cap

**Current state:** Kernel `runs.rs` enforces max 3 attempts, 1000-cent budget, 600-second runtime. A single run cannot exceed 10 minutes. The product promise is "agents work continuously until done."

**Options:**
- **A — Raise the caps.** 3600s runtime, 5 attempts. Quick fix. Still has a ceiling; just a higher one.
- **B — Build run chaining.** Agent finishes a run, auto-resumes a new run from where it left off. `runtime_run_resume_service.py` exists. No hard cap — truly continuous. More work but honors the vision.
- **C — Do both.** Raise caps now (quick win), build chaining in Phase W.

**Recommendation: C.** Raise caps today so the 10-minute ceiling doesn't block demos. Build chaining into Phase W as part of the durable-run path hardening.

### D2. Agent planning capability

**Current state:** No planner service, no plan artifact, no plan-then-execute contract. The vision says "agent makes a plan, executes phase by phase." Zero primitives exist for this.

**Options:**
- **A — Build a planner service.** New `planning_service.py`. Agent produces a `Plan` (ordered steps with dependencies). Each step becomes a durable run or scheduled task. Agent reports progress against the plan.
- **B — Rely on the LLM's internal reasoning.** Let the agent think through steps inside its reasoning loop. No explicit plan artifact. Simpler, but no visibility for the user.
- **C — Defer to Phase Z.** Planning is a power feature. Get one real user first with single-turn tasks.

**Recommendation: C for now, A for Phase Z.** The product works without explicit planning for the first user. Schedule it for the "managed agency" phase when multi-step workflows are the norm.

### D3. per-agent BYOK (bring your own key)

**Current state:** AI provider credentials are workspace-scoped. No per-agent provider binding exists. The vision says each specialist can use its own API key.

**Options:**
- **A — Build per-agent credential binding.** Add an `agent_id` dimension to `provider_profiles.py`, `secrets_broker.py`, and the vault. Each agent can have its own API key for each provider. Sage resolves per-agent first, falls back to workspace default.
- **B — Keep workspace-scoped, add cost-center tagging.** All agents share workspace credentials, but usage is tagged per-agent for billing. Simpler but no isolation.
- **C — Defer to Phase Z.**

**Recommendation: C.** This is a Phase Z concern. One real user doesn't need per-agent keys — they'll use workspace defaults. Build the fleet first, then add credential isolation.

---

## Phase E: Map Maintenance

### E1. Update PLATFORM-MAP.md with audit corrections

Apply all factual corrections the audit found:
- Gateway status: one clean statement (Node proven in dev, Rust never compiled, zero external users)
- Frontend channels: 7, not 2
- Discord/Slack personal: no Gateway variants planned (ToS risk documented)
- WhatsApp Business: not flat "BLOCKED" — task-specific bots allowed
- `bounded_scheduler_service.py` quiet hours: document if real

**Files:** `docs/handoff/PLATFORM-MAP.md`

### E2. Add the missing items to the phase plan

Per audit §13.3, the phase plan is missing:
- 600s cap redesign (or chaining) → add to Phase W
- Specialist channel expansion → add to Phase W
- Per-workspace MCP write auth → add to Phase Y
- MCP client E2E test harness → add to Phase Y
- Channel health monitoring → add to Phase Y
- `is_platform_error` flag on AgentTurnResponse → add to Phase Y
- Kernel integration tests → add to Phase Y
- Tenant isolation test → add to Phase Y

**Files:** `docs/handoff/PLATFORM-MAP.md` (§9.3, §6b, §13) and `docs/handoff/concerns.md` (§13.2)

---

## Phase F: Fleet Console UI (Phase W — the build)

All backend blockers from Phase A are resolved. The backend primitives already exist (agent registry API, activity ledger, fleet tools, MCP catalog). The UI work is:

### F1. Fleet home — agent cards
- Component: `frontend/lib/workspace/fleet-home.tsx` (new)
- Data source: `fleet_list_agents` → agent cards with status indicator, channel count, last activity timestamp
- States: empty (no agents), loading, populated, error

### F2. Agent detail
- Component: `frontend/lib/workspace/agent-detail.tsx` (new)
- Tabs: Channels, Model, Tools, Memory, Triage
- Data source: `fleet_get_agent_activity` + agent registry read
- States: loading, populated, error per tab

### F3. Activity feed
- Component: `frontend/lib/workspace/activity-feed.tsx` (new)
- Data source: activity ledger API, filterable by agent + event type
- Builds on `codex-chat/` timeline layer (already partially built)

### F4. Agent creation wizard
- Component: `frontend/lib/workspace/agent-wizard.tsx` (new)
- Steps: Name & Role → Channels → Tools → Model → Review → Create
- Calls: `fleet_create_agent` + `fleet_configure_agent`
- States: each step validates before proceeding

### F5. Channel catalog (update existing)
- Component: update `workspace-channel-pairing-surface.tsx`
- Data source: `GET /api/connections/mcp-catalog` (already data-driven)
- Show all backend channels, not just 7
- Show Gateway-required vs Cloud-only badging (already partially done per screenshots)

### F6. Onboarding flow
- Update `onboarding/page.tsx`:
- Step 1: Create workspace (existing)
- Step 2: Create first agent (new)
- Step 3: Bind first channel (new)
- Step 4: Send test message (new)
- End state: user sees their agent in the fleet view, channel is live, test message worked

---

## Phase G: Production Deploy

### G1. Production build
- `frontend/`: `npm run build` → production-optimized Next.js
- `server_modules/`: already runs on VPS (:8001)
- Configure: HTTPS, proper CORS, rate limiting at reverse proxy

### G2. Deploy
- Push frontend build to VPS
- Wire nginx: frontend on :443, backend proxied to :8001
- Health check: `GET /health` returns 200 from public URL

---

## Phase Y: One Real User

After Phase F and G:

1. Deploy to production URL
2. Onboard one business owner
3. Walk them through: create agent → bind Telegram bot → send message → get AI reply
4. Watch the activity feed populate
5. Fix whatever breaks
6. Ask them to come back tomorrow

---

## Phase Z: Managed Agency (future)

- Multi-agent fleet management
- Per-agent credentials and tools
- Task monitoring dashboard
- MCP-first path with full parity
- White-label
- `server_modules/` refactoring: ONE of each primitive

---

## Summary: Order of Execution

```
A1 (specialist→Telegram)  ── 1 file, unblocks the demo
A2 (per-workspace MCP writes) ── 3 files, fixes security gap
A3 (map contradiction fix) ── docs only
    │
    ▼
B1 (resolver bugs) ── 1 file
B2 (discord_personal lane) ── 1 file
B3 (provider UI confusion) ── 2 files
    │
    ▼
C1 (kill-switch test) ── new test file
C2 (webhook signature tests) ── new test file
C3 (kernel integration test) ── new test file
    │
    ▼
D1-D3 (your decisions) ── no code until you answer
    │
    ▼
E1-E2 (map updates) ── docs only
    │
    ▼
F1-F6 (Fleet Console UI) ── Phase W, the main build
    │
    ▼
G1-G2 (production deploy)
    │
    ▼
Phase Y (one real user)
```

Phases A through C are pure execution — no decisions needed, just fix. Phase D waits on you (3 questions above). Phase E is docs. Phase F is the build. Phase G is deploy.

---

## Decision Needed From You

1. **600s run cap** — Raise now + build chaining later? (Recommendation: yes)
2. **Agent planning** — Defer to Phase Z? (Recommendation: yes — get one user first)
3. **Per-agent BYOK** — Defer to Phase Z? (Recommendation: yes — workspace-scoped is fine for first users)
