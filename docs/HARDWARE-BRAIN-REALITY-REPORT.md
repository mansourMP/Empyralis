# Hardware & Brain — Reality Map

> **OUTDATED (2026-07-23):** this is a point-in-time research report from
> 2026-07-10. `docs/PLATFORM-MAP.md` (§22, and the note near line 81) already
> flags this document's contrary claims as predating a real fix — treat this
> doc as superseded, not current reality. It also predates the 2026-07-23
> founder ruling that "Sage" is dead product terminology (the platform has
> only agents — owner-facing, customer-facing serving the owner, and AskAI);
> this document uses "Sage" throughout as a live concept. Kept for history;
> do not build from this.

**Date:** 2026-07-10 | **Branch:** `verify` | **Scope:** Read-only research, no code changes

Three independent, file:line-verified investigations into what's actually wired vs. spec-only across the agent Hardware tab, the `cli_subscription` brain mode, and the VPS provisioning flow — so the next design pass works from ground truth, not assumption.

---

## Part 1 — Agent Hardware Tab

### The tab itself is flat. The "three-layer nested wrapper" is cross-tab, not in-tab.

`frontend/lib/workspace/fleet/tabs/HardwareTab.tsx` (89 lines, read in full) is a small, **entirely read-only, presentational** component — no `fetch`, no hooks, no API calls of its own. Everything comes from the `agent` prop already fetched by the parent. It renders one card with three static rows (Access level, Runtime, Computer status) plus one conditional note that tells the user to go manage things on a *different page* — including the sentence "then grant it here," which is **aspirational/inaccurate**: there is no grant control anywhere in this tab.

The real nesting the owner is feeling spans tabs:

| Layer | Where | What |
|---|---|---|
| 1. Hardware **tab** | `HardwareTab.tsx`, rendered at `FleetAgentDetail.tsx:270` | Read-only summary, no edit control despite implying one exists |
| 2. `HardwareBindingSection` | `FleetAgentDetail.tsx:1503-1582`, but rendered **inside the Model tab** at `:1932` | The *actual* editable control for `hardware_access`/`preferred_gateway_id` — titled "Hardware" again, on a different tab |
| 3. `GatewayBoxPicker` | `frontend/lib/workspace/fleet/gateway-box-picker.tsx:84-151`, nested inside layer 2 at `FleetAgentDetail.tsx:1568` | Self-fetching `<select>` — its own independent `GET /api/gateway/registrations` call via `useWorkspaceGateways()` |

Compounding it: `GatewayBoxPicker` is reused a **second time on the same Model tab** (`FleetAgentDetail.tsx:1892` and `:1913`) for a semantically different field (`model_config.gateway_binding` — which box hosts the AI brain — vs. `preferred_gateway_id` — which box tool calls prefer). Two identical-looking pickers, two different fields, neither on the Hardware tab. A third independent "fetch registrations → label a box" implementation exists in the wizard (`FleetCreateAgentWizard.tsx:36-90`), and a fourth inline version lives on the workspace Hardware **page** (`hardware/page.tsx:114-128`). None of the four is used by `HardwareTab.tsx`.

### Backend data model

**`hardware_access`** — column on `workspace_agent_installs` (`fleet_tools.py:894-897`), values `none|gateway|vps|all`, validated at `fleet_tools.py:869-873`. Set from **three inconsistent paths**: the wizard's Placement step (`FleetCreateAgentWizard.tsx:206-209` — the *only* path that can set `"vps"`), `HardwareBindingSection` (`FleetAgentDetail.tsx:1523` — **only ever writes `"none"` or `"gateway"`**, collapsing a 4-value field into a boolean), and capability-preset defaults (`capability_presets.py:57-99`). **Confirmed bug:** saving `HardwareBindingSection` on an agent whose placement is `"vps"` silently rewrites it to `"gateway"` (line 1507 collapses `vps`/`gateway`/`all` into one boolean before round-tripping).

**`runtime_target` — the key finding.** `_resolve_runtime_target()` (`fleet_tools.py:222-260`) reads exclusively from `inst["runtime_profile"]`, populated only from a `runtime_profile_id` foreign key. Every agent gets a `runtime_profile_id` **at creation** that resolves to the workspace-seeded `"empyralis-cloud"` profile (`agent_registry_repository.py:527-536`, target `"cloud"`) — and **nothing in `fleet_configure_agent`'s `hardware_access` handler ever touches `runtime_profile_id`** (confirmed by grep). The only writers of a non-default profile are a separate, UI-less "self-hosted business node enrollment" subsystem (`agent_registry_repository.py:929-1209`) with no Fleet-UI entry point.

**Net effect: `runtime_target` is permanently pinned to `"cloud"` for essentially every real agent**, regardless of `hardware_access`/`preferred_gateway_id`. `derivePlacement()` (`fleet-presentation.ts:157-163`, used on the Overview tab and list cards — never called from `HardwareTab.tsx`) is therefore effectively constant ("Cloud" or "Ready to configure") today. Same root cause breaks `hardware_status` (`fleet_tools.py:263-297`, keys off the same empty `machine_id` → falls to `"unknown"` structurally, not just cosmetically stale).

**Gateway registrations** (`GET /gateway/registrations` → `routes_gateway.py:1852-1862` → `gateway_registry_service.list_workspace_gateways()`) *is* healthy and live: real `connection_status` (`online|offline|degraded|reconnecting|revoked`, derived from actual WSS session + heartbeat freshness, `gateway_registry_service.py:36-91`) and a VPS `hardware_label` of `"{Provider} · {Region}"` (`:126-140`) — **not a user-chosen name**; no nickname field exists anywhere in the VPS provisioning request (`routes_gateway.py:604-613`). This endpoint is already fetched by `GatewayBoxPicker` and the wizard — **just never by `HardwareTab.tsx`.**

### Feasibility: "Running on: {Cloud | VPS name | Gateway name} · health · [change placement]"

| Piece | Status | Note |
|---|---|---|
| Location — Cloud | **Wired** | `hardware_access === "none"` already read at `HardwareTab.tsx:29` |
| Location — Gateway name | **Partial** | `preferred_gateway_id` exists but is an id only; name-join logic already written (`gatewayLabel()`, `gateway-box-picker.tsx:35-43`), just not called from this tab. Pure frontend composition. |
| Location — VPS name | **Missing (small backend gap)** | Same join mechanism works, but resolves to `"Provider · Region"`, not a name — no nickname field exists to resolve to one |
| Health signal | **Partial, and today's displayed one is wrong** | `agent.hardware_status` (shown today) is structurally `"unknown"` per the `runtime_profile` bug above. The correct live signal (`connection_status`) is one join away via the same registrations call, already consumed elsewhere (`gatewayIsOnline()`, `gateway-box-picker.tsx:45-47`) |
| Change-placement action | **Partial, inconsistent, buggy** | A live PATCH-backed control exists but lives on the wrong tab, only toggles 2 of 4 states, and silently downgrades `"vps"`→`"gateway"` on save. No post-creation UI path can set `"vps"` at all — only the create wizard can, and it's never reopened against an existing agent. |

**Do not build the new label on `runtime_target`/`derivePlacement()`/`agent.hardware_status`** — all three are wired to the disconnected `runtime_profile` FK, not to `hardware_access`/`preferred_gateway_id`, and will silently show "Cloud" for everyone regardless of real placement.

**Buildable now, frontend-only:** Cloud/Gateway-name labels, swapping the health source to `connection_status`, relocating a *fixed* (non-collapsing) 3-way placement control onto the Hardware tab itself.
**Needs backend work:** a real owner-chosen VPS name field; optionally one pre-joined "placement + health" endpoint so the tab isn't doing the registrations join client-side.

---

## Part 2 — CLI-Subscription Brain (Claude Code CLI / OpenAI Codex)

### Spec vs. reality

`docs/CLI_SUBSCRIPTION_SPEC.md:4` self-declares **"Status: Spec — not built,"** defining phases G1–G6. Checked against the tree:

| Phase | Spec proposes | Actual |
|---|---|---|
| G1 capability detection | New heartbeat field | **Built**, via a different route: `empyralis-gateway/src/health/service-inventory.ts:475-598` (`probeCodexCli()`/`probeClaudeCli()`, real PATH/auth-file checks) → `gateway_registry_service.py:143-178` → exposed as `llm_runtimes.claude_code`/`.codex` in `/api/gateway/registrations` (`:206`) |
| G2 WSS message types | New `llm_generate*` types | **Not built** — `local` mode reuses the existing generic capability-invoke rail (`capability_id: "llm.generate"`, `sage_agent_runtime_service.py:690-692`); no new message types exist |
| G3 Gateway CLI spawner | `cli-runner.ts` spawning `claude -p`/`codex exec` | **Missing entirely** — no such file exists; `empyralis-gateway/src/llm/runtime.ts:136-142` explicitly refuses any runtime but `"ollama"` with an inline comment calling this "a later phase" |
| G4 control-plane dispatch | Real Gateway dispatch | **Not built** — see stub below |
| G5 differentiated errors | 6-condition matrix | **Not built** — one blanket message |
| G6 `fleet_configure_agent` fields | `gateway_binding`/`runtime` | **Built** — `fleet_tools.py:36-42`, validated at `:754-772` |

### Wired end-to-end or spec-only? **Spec-only — a clean, unconditional stub**

`sage_agent_runtime_service.py:449-461`, inside `_resolve_agent_cloud_provider()`:
```python
if mode == "cli_subscription":
    await _ledger_provider_unavailable(...)
    raise RuntimeError(
        "CLI subscription mode is not yet available on this deployment. "
        "Switch this agent to platform_credits or byok_api."
    )
```
Fires unconditionally — never inspects `gateway_binding`, never checks if a Gateway is online, never checks if the CLI is installed. Confirmed via exhaustive search: no `spawn`/`child_process` invoking `claude`/`codex` for generation anywhere, no `cli-runner` symbol, no `llm_generate` WSS handler.

**The working analog to extend:** `local` mode's real path — `_dispatch_local_gateway_brain` (`sage_agent_runtime_service.py:642-721`) → `gateway_execution_service.execute_tool_via_gateway(capability_id="llm.generate")` → `GatewayLLMRuntime.handleCapabilityInvoke()` (`empyralis-gateway/src/llm/runtime.ts:127-158`) → Ollama HTTP call. Fully wired, just Ollama-only — extending it means replacing the `runtime !== "ollama"` throw at `runtime.ts:137-141` with real CLI-spawn logic.

### Where the mode list lives, and how the lock renders

`frontend/lib/workspace/fleet/fleet-provider-constants.ts` (135 lines) is the single shared source. `cli_subscription` is **fully present in the data** (`SUBSCRIPTION_PROVIDERS`, lines 24-27: Claude Code + OpenAI Codex) — **not hidden**. The lock is one line: `COMING_SOON_MODES = new Set(["cli_subscription"])` (lines 52-54; a comment notes `local` used to be in this set too, removed once it shipped — confirming this Set is the literal on/off switch). Rendered via `COMING_SOON_NOTE = "Coming soon — requires a paired box"` (line 56).

- **Wizard** (`FleetCreateAgentWizard.tsx:448-452`): lock icon + note shown, and the step's Next button is **hard-disabled** whenever this mode is selected (line 569) — can't even advance past Brain step with it picked.
- **Model tab** (`FleetAgentDetail.tsx:1674,1825-1833,1918`): same `isComingSoon` check disables Save, but the config panel (CLI picker + `GatewayBoxPicker`) **still renders and is interactive** — a user can pick Claude Code and a box, just can't save.

**"Unlocking" it is a literal one-line change** (remove from `COMING_SOON_MODES`) — **but doing so alone would be actively harmful**: saves would succeed (§ below — no gateway validation exists), and then every turn for that agent fails with the blanket stub error. The flag and the capability are fully decoupled; real unlock needs G3–G5 first.

### Is the Gateway dependency enforced, or just a UI label? **Enforced as a blanket wall, not gateway-aware — and there's a real config-save bypass**

`fleet_configure_agent` validates `mode`/`runtime` are in allowed sets but **only type-checks `gateway_binding`** (`fleet_tools.py:767-772` — `isinstance(..., str)`, never checked for existence/pairing/online status). Confirmed bypass: a raw `PATCH .../fleet/agents/{id}` with `cli_subscription` + a blank/garbage `gateway_binding` **saves successfully** — no route in the chain adds extra gating.

At turn-time, the stub (`sage_agent_runtime_service.py:449-461`) rejects identically whether the binding is empty, garbage, or points to a real ready Gateway — it never gets far enough to check. Contrast with `local` mode, which has real, state-aware enforcement (`:469-484`, plus live failure surfacing through `_dispatch_local_gateway_brain`'s own error path) — that's the enforcement *pattern* `cli_subscription` needs but doesn't have.

### Reusable "needs hardware" UI pattern

`channelStatePill()` (`FleetAgentDetail.tsx:904-910`) already has a **dynamic**, backend-computed `"gateway"` tone ("Needs Gateway", shown only when live online-Gateway count is actually zero) — used for WhatsApp/WeChat channels. `cli_subscription`'s lock is **static** by comparison (same message regardless of how many Gateways are online with the CLI ready) and doesn't use this pattern.

Closer still: `GatewayBoxPicker` already has a readiness-annotation pattern for Ollama (`gatewayLocalModelReady()`, lines 27-29, warns inline if the selected box has no local model runtime) — and **the backend data for Claude/Codex readiness is already flowing end-to-end** (`llm_runtimes.claude_code`/`.codex` reaches the frontend type at `gateway-box-picker.tsx:7-12`) but is **never read** in the render logic. This is the natural extension point, not new plumbing.

**Buildable now, pure UI, zero backend risk:** better lock copy/tooltip; reusing `channelStatePill`'s dynamic `"gateway"` tone instead of the static note; surfacing the already-flowing `llm_runtimes.claude_code`/`.codex` readiness in `GatewayBoxPicker`. **Do not flip `COMING_SOON_MODES`** without the backend work below.
**Needs real backend/Gateway work (spec's own estimate: ~4-5 of 7-8 days):** Gateway-side CLI spawner; control-plane dispatch replacing the blanket stub with `local`-style state-aware logic; differentiated error paths; tightening `fleet_configure_agent` to actually validate `gateway_binding` resolves to a paired Gateway before persisting.

---

## Part 3 — Hardware-Page VPS Flow (DigitalOcean)

**Headline: far more built than a "hardcoded single size" assumption — this is a real, live, billable flow already, with one well-scoped gap.**

### What `vps_provisioning_service.py` supports after DO OAuth — **wired, not hardcoded**

Real DigitalOcean HTTP calls (via `_http_json`):

| Call | Endpoint | file:line |
|---|---|---|
| List real sizes (RAM/vCPU/disk/price) | `GET /v2/sizes` | `vps_provisioning_service.py:310`, inside `fetch_provider_plans` (:294-323) |
| Create droplet | `POST /v2/droplets` | `:501-507`, inside `_provision_digitalocean` (:482-519) |
| Delete droplet | `DELETE /v2/droplets/{id}` | `:700-705` |
| OAuth authorize / token exchange | `.../oauth/authorize`, `.../oauth/token` | `:25-26`, exchanged at `:852-868` |

`fetch_provider_plans` normalizes the real `/v2/sizes` response (vcpus/memory/disk/price_monthly/slug/available) in `_normalize_digitalocean_plans` (`:871-898`). `default_size="s-1vcpu-2gb"` (`:103`) is only a **fallback** when no size is passed — the user-selected plan flows straight through to the real create payload. Confirmed by test contract: `test_fetch_provider_plans_normalizes_digitalocean_sizes` and `test_digitalocean_provisioning_payload_uses_curated_region` (`test_vps_provisioning_service.py:65-136`) both assert real DO request/response shapes, not stubs.

**The one real gap: regions are a static, hardcoded 6-slug list** (`PROVIDER_CONFIGS["digitalocean"].regions`, `:106-113` — nyc3/sfo3/lon1/fra1/sgp1/blr1, real DO slugs but a curated subset; DO has more). The codebase's own test names this `test_hardware_vps_regions_route_returns_curated_provider_list` — it's labeled "curated," not live.

### Catalog endpoint — **wired for plans, static for regions**

`GET /hardware/vps/plans` → `routes_gateway.py:1613-1638` → real DO `/v2/sizes` call. **Wired.**
`GET /hardware/vps/regions` → `routes_gateway.py:1740-1753` → `provider_catalog()`, a static dict, no DO call. **Partial/static.**

DO's public API does expose `GET /v2/regions` — not called anywhere in this codebase. **OAuth scope is already `read write`** (`vps_provisioning_service.py:200`) — DigitalOcean's *maximum* grant (it offers no finer-grained scoping), so **adding live regions needs no scope change and no existing account would need to re-auth.** Sizing the gap: one new function mirroring the existing `_normalize_digitalocean_plans` (~30-40 lines) hitting `/v2/regions`, swapped into the already-existing `/hardware/vps/regions` route. Small, self-contained.

Bonus finding: DO's real `/v2/sizes` response includes a `regions` array per size (which regions offer that size) — `_normalize_digitalocean_plans` currently **discards it** (`:871-898`). Threading it through would let the UI prevent submitting an invalid (plan, region) pair, using data already being fetched.

### Where the hardcoded prices come from

`frontend/lib/workspace/cloud-vps-setup-panel.tsx`, `CLOUD_VPS_PROVIDERS` (`:104-135`): DO `$12/mo` (`:108`), Hetzner `€4/mo` (`:118`), Vultr `$12/mo` (`:128`). **Scope: pre-connection marketing badges only** (`hardware/page.tsx:153`, `cloud-vps-setup-panel.tsx:664`) — the actual plan-selection step (`:749-778`) already renders **live** `plan.price_label` from the real `/hardware/vps/plans` call. The badges are real-ish, not made up: each matches that provider's actual `default_size` SKU (DO's `$12` matches the `s-1vcpu-2gb` tier the test fixture independently prices at `$12`; Vultr's matches `vc2-1c-2gb`; Hetzner's approximates `cx22`). They're static strings that will drift from DO's actual current price over time since nothing recomputes them — but fixing that needs a service-level DO credential to pre-fetch/cache a reference price before any user has connected an account (DO's `/v2/sizes` is bearer-gated, no anonymous pricing endpoint) — a small caching addition, not a one-liner.

### Real provisioning entry point — **wired end-to-end, creates a real billable droplet**

Full trace: `createServer()` (`cloud-vps-setup-panel.tsx:511-570`) → `POST /api/hardware/vps/provision` → Next.js catch-all proxy (`app/api/[...path]/route.ts` → `control-plane-proxy.ts:225-301`) → FastAPI (`server.py:387`, gateway router) → `provision_hardware_vps` (`routes_gateway.py:1641-1737`, owner-role enforced) → `vps_provisioning_service.provision_vps` (`:367-387`) → `_provision_digitalocean` (`:482-519`) → real `POST /v2/droplets` with region/size/image=`ubuntu-24-04-x64`/cloud-init pairing script (`:490-507`). Frontend then polls `GET /api/hardware/vps/{id}/status` until the paired gateway registration confirms connection.

**This is a real resource — the user is billed by DigitalOcean, exactly as if they'd clicked "Create Droplet" in DO's own console.** Confirmed independently by both the backend test suite and a frontend e2e spec (`frontend/tests/e2e/gateway-operator-state.spec.ts:146-296`) mocking the identical real chain. Teardown is equally real (`DELETE /hardware/vps/{id}` → real `DELETE /v2/droplets/{id}`, `routes_gateway.py:1787-1814`).

---

## Consolidated: buildable now vs. needs backend work

**Buildable now, frontend-only, using data that already exists and is already fetched somewhere in the app:**
- Hardware tab: Cloud/Gateway-name placement labels, swap health source to `connection_status`, relocate a fixed 3-way placement control onto the tab itself.
- CLI-subscription: better lock messaging using the already-flowing `llm_runtimes.claude_code`/`.codex` readiness data and the existing dynamic `"gateway"` pill pattern.
- VPS: nothing further needed here — the flow is real end-to-end already.

**Needs a small, scoped backend addition (one new call/field, no new subsystem):**
- Hardware tab: an owner-chosen VPS name/nickname field (currently only "Provider · Region" exists).
- VPS regions: one new DO `/v2/regions` call + normalizer, same shape as the already-shipped plans call, no OAuth scope change needed.
- VPS: thread the already-fetched per-size `regions` array through to prevent invalid (plan, region) submissions.

**Needs real, multi-day backend/infra work (do not attempt as a UI change):**
- CLI-subscription generation path: Gateway-side CLI spawner, control-plane dispatch, differentiated errors (spec's own estimate ~4-5 of 7-8 days) — **flipping the UI lock without this ships a silently broken feature** (config saves, agent never replies).
- VPS pre-connection live pricing: needs a server-held reference DO credential to cache pricing before a user has connected their own account.

**A structural trap to avoid regardless of which piece is built first:** `runtime_target`, `derivePlacement()`, and `agent.hardware_status` all read from a `runtime_profile` foreign key that real Fleet agents never populate — they will silently show "Cloud"/"unknown" forever regardless of actual placement. Any new "where does this agent run" UI must build from `hardware_access` + `preferred_gateway_id` + a live gateway-registrations join instead, not from that existing-looking but structurally inert pipeline.
