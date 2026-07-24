# Gap Analysis: Hardware / Gateway / Host Provisioning + Reliability — OpenClaw vs Empyralis (post reliability-wave)

> **Terminology note (2026-07-23):** "Sage" is legacy product terminology — the founder ruled the concept removed from the product; the platform has only agents (owner-facing, customer-facing serving the owner, and AskAI). `sage_*` code/variable/string identifiers are legacy code artifacts only, not a live product concept. Anywhere this document's prose says "Sage" or "master," read: the owner-facing agent. This is a lighter-touch terminology note, not a full rewrite — the body below is unchanged and may still use "Sage" throughout.

Read-only comparison. No code changed. Every claim is `file:line` cited on both sides.
Empyralis paths relative to `/Users/mansur/empyralis/`; OpenClaw paths relative to
`/Users/mansur/openclaw/` (read-only reference checkout, MIT, zero shared code).

**Baseline used**: this report compares against Empyralis's code **as of HEAD** (commit
`89b1eb7bc`, branch `fix/hardware-detail-width`), i.e. **after** the four reliability-wave
commits (`4701c44e7`, `920865a70`, `3122de6f0`, `233a0dc18`). `docs/design/reliability-audit-1-
gateway-health.md` is reused for facts about **static** OpenClaw source (which doesn't change),
but its Empyralis-side prose is **stale** — that file was committed at `4701c44e7`, the *first*
of the four wave commits, so its "what Empyralis lacks" list does not reflect the three wave
commits that landed after it. Every Empyralis claim below was re-verified directly against
current file contents in this pass, not copied from that doc's prose.

---

## Part 0 — Where Empyralis is at parity or AHEAD (verified, not assumed)

These matter because the founder's worry is "is our hardware reliable" — it's equally load-bearing to know what's *already* solid so effort isn't wasted re-solving it.

| Area | Empyralis | OpenClaw | Verdict |
|---|---|---|---|
| **Cloud VPS provisioning automation** | Fully automated: OAuth/token-based multi-cloud provisioning (DigitalOcean OAuth, Google "bootstrap-then-impersonate", Hetzner/Vultr API tokens, AWS CloudFormation cross-account role) drives `provision_vps()` (`server_modules/vps_provisioning_service.py:1152-1227`), which builds a `cloud_init_script()` (`:1123-1143`) embedding the pairing token and hands it as `user_data` to the provider's create-droplet/VM API (`_provision_digitalocean`:1582-1634, `_provision_hetzner`:1695-1734, `_provision_vultr`:1735-1772). A background lifecycle poller (`run_vps_provisioning_lifecycle`, `:1228-1386`) tracks the box until it registers, with `mark_vps_provision_failed()` (`:1522-1572`) and `delete_recorded_vps()` (`:1504-1521`) cleaning up on failure. | No self-service cloud provisioning exists at all. `docs/vps.md:15-29` is a **provider picker** — the user manually creates the VPS in DigitalOcean/Hetzner/Fly/etc.'s own console/CLI, then runs the install script by hand. There is no OpenClaw code that calls a cloud provider's API to create a machine. | **Empyralis ahead.** This is a real, automated one-click provisioning pipeline; OpenClaw's docs explicitly hand this step to the human. |
| **In-gateway doctor (detect → repair → re-validate)** | `empyralis-gateway/src/health/gateway-doctor.ts:402-410` registers 5 checks (`cloud_connection`, `capability_readiness`, `cli_subscription`, `personal_channel_imessage`, `supervisor_presence`); `runGatewayDoctor()` (`:420-474`) re-runs `detect()` after every `repair()` and only marks `repaired:true` if the re-validated status is `"pass"` (`:451-454`) — same re-validate-after-repair contract as OpenClaw's `doctor-repair-flow.ts:293-300` (cited in reliability-audit-1 §7). Invoked from the UI: `GatewayDoctorControl` (`frontend/app/(account)/w/[workspaceId]/hardware/[gatewayId]/page.tsx:1039-1095`) posts to `/api/gateway/registrations/{id}/doctor/run` (`:1051`), which reaches `GET/POST .../doctor` (`server_modules/routes_gateway.py:3649`). | `openclaw doctor` (`src/commands/doctor.ts:7-27` → `src/flows/doctor-health.ts:21-102`), ~142 check/repair files (per reliability-audit-1 §7). | **Empyralis now has a real doctor** (didn't exist at all when reliability-audit-1 was written) — narrower in breadth (5 checks vs ~142) but the same detect/repair/re-validate contract, and it has a working UI trigger. Not full parity on breadth, but the "no doctor at all" gap from audit-1 §7 is closed. |
| **Self-repairing OS supervision (launchd/systemd)** | `empyralis-gateway/src/update/gateway-supervisor-install.ts` (429 lines): `auditAndRepairGatewaySupervisorInstall()` writes a missing unit, rewrites a drifted one, reports `permissionDenied:true` on EACCES, and — critically — **never touches an already-running job** (module doc comment `:26-58`). Wired into the doctor's `supervisor_presence` check (`health/gateway-doctor.ts:372-400`), which can call it with `attemptRepair:true` from the UI. | `doctor-gateway-daemon-flow.ts:187-505` / `doctor-gateway-services.ts:400-693` (cited reliability-audit-1 §2). | **Parity in mechanism** (both audit+repair an installed unit/plist conservatively), narrower in scope (Empyralis: one unit; OpenClaw: systemd+launchd+Windows Scheduled Task + entrypoint-drift/PATH-drift/Bun→Node migration checks). This closes the "systemd unit written once, never audited" gap from audit-1 §2/row 4. |
| **Process crash guards** | `empyralis-gateway/src/index.ts:99-129` (`installProcessCrashGuards`) now installs real `process.on("uncaughtException"/"unhandledRejection")` handlers, with a narrow, explicit `FATAL_PROCESS_ERROR_PATTERNS` allowlist (`:46-54`, stack/heap corruption signatures only) that exits for supervisor restart; everything else is logged and the process keeps running. | No single equivalent construct audited; OpenClaw's crash safety is per-callsite (reliability-audit-1 §1). | **Empyralis now has this** — did not exist when reliability-audit-1 was written (that audit's §1/row 3 explicitly flagged its absence; it is now closed, confirmed by direct read of `index.ts`). |
| **Live capability re-advertising** | `index.ts:259-267` comment + `GatewayWsClient.syncRequestedCapabilities()` (referenced, `cloud/ws-client.ts`, per commit `920865a70`'s own description) re-evaluates `capabilityRouter.supportedCapabilities()` on every heartbeat and mutates `runtimeMetadata` in place if the advertised **set** changed — a capability that becomes available post-boot (Docker installed, CLI signed in) no longer needs a full restart. | Not directly comparable; OpenClaw's per-channel transport has its own restart policy instead of a single static snapshot (reliability-audit-1 §5). | **Closes** the audit-1 §5/row 9 gap ("computed once at startup, never recomputed") — confirmed via the updated `index.ts` comment describing the new behavior, contradicting the old comment audit-1 quoted. |
| **Channel-level reconnect/health-check** | Telegram: `reconnectAttempts`/`healthCheckTimer` (`channels/telegram/runtime.ts:726-733`). WhatsApp: `resolveWhatsAppReconnectState()` distinguishes logout vs "stream conflict" (440, another device) vs generic disconnect (`channels/whatsapp/reconnect.ts:14-58`) — a case OpenClaw's own docs don't appear to name as distinctly. Signal: bounded fast-retry via `foundation/reconnect-utils.ts`'s `DEFAULT_RECONNECT_POLICY`, falls back to a **steady retry that never permanently gives up** once the fast cap is exhausted (`bridges/signal-cli-bridge.ts:464-524`, explicit comment at `:464-467` citing this as a fix for "the reliability audit's signal-cli-reconnect-exhaustion gap"). | Generic `CHANNEL_RESTART_POLICY` with a **hard cap and give-up** (`MAX_RESTART_ATTEMPTS=10`, logs `"giving up after N restart attempts"`, `src/gateway/server-channels.ts:32-38,724`, cited reliability-audit-1 §1/row1). | **Different tradeoff, not a strict Empyralis deficit.** OpenClaw gives up after 10 attempts (channel then requires a human to notice and restart it); Empyralis's per-channel logic explicitly chose to never permanently give up, on the reasoning that a hosted product has no human watching a terminal. Reasonable for the product shape — flagged as a design difference, not ranked as a gap below. |
| **Shell-sandbox resource limits** | `GatewayShellRuntimeConfig` carries `memoryMb`/`cpus` (`empyralis-gateway/src/shell/runtime.ts:34-35`), plus a hard `DEFAULT_TIMEOUT_SECONDS=60` / `MAX_TIMEOUT_SECONDS=300` (`:16-17`) on every exec call. | `tools.exec.timeoutSec` (default 1800s) + background job TTL/output caps (`docs/gateway/background-process.md:51-58`). | **Parity** — both cap CPU/mem/time on sandboxed exec; different defaults, not a gap. |

---

## Part 1 — Prioritized gaps still open post-wave

### 1. No health gating around self-update; post-restart "health check" is a bare PID-alive check, not an actual health probe — **HIGH**

**Empyralis today**: `GatewaySelfUpdateRuntime.handleCapabilityInvoke()` (`empyralis-gateway/src/update/gateway-self-update-runtime.ts:122-237`) never calls `GatewayDoctorRuntime` (which exists, `health/gateway-doctor.ts`) at any point before or after the artifact swap — confirmed by reading the full file: no reference to `doctorRuntime`, `runGatewayDoctor`, or any capability-readiness check anywhere in the update flow. The only "verification" step is `gateway-restart-handoff.ts`'s `HANDOFF_SCRIPT` (`:150-156`), which after spawning the new build just checks `isPidAlive(child.pid)` after an 8-second `DEFAULT_HEALTH_GRACE_MS` (`:211`) — this proves the process didn't immediately crash, **not** that it reconnected to the cloud WS, that its heartbeat is flowing, or that it's actually serving anything. A gateway that starts, holds a PID for 8s, then hangs on WS handshake or throws inside `main()` after `installProcessCrashGuards()` swallows the error, would be reported as a successful update.

**OpenClaw**: runs health checks at **4 distinct gates** during one update — pre-apply build preflight in an isolated worktree (git mode only, `update-runner.ts:1180-1350`), `openclaw doctor --non-interactive --fix` as a **mandatory gating step** before restart (3 call sites: `update-runner.ts:1568`, `update-runner.ts:1708`, `update-command.ts:1687`), a plugin-payload smoke check that can block the restart (`post-core-plugin-convergence.ts:94-95,173`), and `waitForGatewayHealthyRestart()` (`src/cli/daemon-cli/restart-health.ts:38-58` for the exported constants, `:517-585` for the wait loop — re-read and confirmed present in this pass) which polls up to `DEFAULT_RESTART_HEALTH_ATTEMPTS` (≈120 attempts × `DEFAULT_RESTART_HEALTH_DELAY_MS=500ms` = 60s) checking `probeGateway()` reachability and `classifyPortListener()`/`inspectPortUsage()` (imported at `:11-17`) to catch a stale/orphaned listener on the port — a materially stronger signal than "the OS still has this PID."

**Why it matters**: this is exactly the failure mode a founder worried about hardware reliability should worry about most — a bad self-update artifact could silently strand a box in a half-alive state (process running, PID alive, but never actually reconnected) with the UI showing "updated: true" and no automatic detection.

**Build size**: medium. `GatewayDoctorRuntime` already exists and is already wired to read live connection state (`checkpoints.currentHealthState()`) — the missing piece is calling `runGatewayDoctor()` once after the handoff/restart confirms alive, and reporting a `health_check: "pass"|"fail"` field in the self-update result instead of just `restart_mode`. The harder half is replacing the crude `isPidAlive()` grace check in `gateway-restart-handoff.ts`'s `HANDOFF_SCRIPT` with an actual reachability probe (the handoff script runs as a detached, dependency-free CommonJS script by design — see its own doc comment at `gateway-restart-handoff.ts:80-83` — so it would need to open a real WS/HTTP probe against the new process, not just `process.kill(pid, 0)`).

---

### 2. No way to restart the gateway process without SSH — **HIGH**

**Empyralis today**: the hardware detail page's own guided instructions for applying a new `CLAUDE_CODE_OAUTH_TOKEN` literally tell the operator to SSH in and run the command by hand: `'echo \'CLAUDE_CODE_OAUTH_TOKEN="paste-your-token-here"\' | sudo tee -a /etc/empyralis/agent-computer.env\nsudo systemctl restart empyralis-gateway.service'` (`frontend/app/(account)/w/[workspaceId]/hardware/[gatewayId]/page.tsx:693`). Grepping `server_modules/routes_gateway.py`'s full endpoint list (lines 1320-3649) turns up `self-update` (`:2846`), `doctor` (`:3649`), `emergency-stop`/`clear-emergency-stop` (`:1459,1504` — these gate *agent execution*, not the gateway process), `dedicated-workstation/kill`/`clear-kill` (`:2601,2633`), and `DELETE /hardware/vps/{vps_id}` (`:2254`, full VPS teardown) — but **no plain "restart the gateway software" endpoint** exists between those two extremes. The only way to restart the gateway without destroying the box is: trigger a self-update to the *same* version (untested/unsupported use, `gateway-self-update-runtime.ts:134-141` short-circuits identical-version requests as a no-op and returns without restarting), or SSH in.

**OpenClaw**: `openclaw gateway restart`, `openclaw node restart` are first-class CLI commands (referenced throughout `docs/nodes/index.md:127-129`); the doctor flow can restart a stopped service directly (`doctor-gateway-daemon-flow.ts:187-505`, cited reliability-audit-1 §2).

**Why it matters**: "restart my agent's computer because something's stuck" is one of the most common asks a non-technical operator will have, and today it requires either a full VPS destroy+recreate (data loss, minutes of downtime, re-pairing) or SSH access the product's own UI otherwise never asks the user to use.

**Build size**: small-to-medium. The building blocks already exist — `gateway.self_update`'s restart-handoff machinery (`gateway-restart-handoff.ts`) already knows how to cleanly exit a supervised process and let `Restart=always` bring it back, or spawn a detached handoff for the unsupervised path. A dedicated `gateway.restart` capability (no artifact download, just re-exec the current build) plus a `POST /gateway/registrations/{id}/restart` route and a UI button next to the existing Doctor/Self-update controls would close this without inventing new infrastructure.

---

### 3. No diagnostics-export bundle for support/debugging — **MEDIUM**

**Empyralis today**: confirmed absent by repo-wide grep for `diagnostics.*export|export.*bundle|stability.*bundle|StabilityRecorder` across `empyralis-gateway/src` — zero hits. There is no single command or endpoint that assembles a sanitized snapshot (logs + config shape + health state) for a support/debugging handoff. The closest thing is the backend's read-only `GET /gateway/registrations/{gateway_id}/doctor` (`server_modules/gateway_health_service.py:397-775`), which aggregates *stored* state, not a downloadable bundle a user or support engineer can attach to a ticket.

**OpenClaw**: `openclaw gateway diagnostics export` (`docs/gateway/health.md:34`) produces a zip combining a Markdown summary, the newest stability bundle, sanitized log metadata, sanitized status/health snapshots, and config shape — explicitly designed to be shared, with chat text/webhook bodies/credentials/tokens omitted or redacted (same doc line).

**Why it matters**: when a box is actually broken (the reliability scenario the founder is worried about), the fastest path to a fix is a founder or support engineer being able to pull one bundle instead of grepping logs live over SSH on a production box.

**Build size**: medium. Empyralis already has most of the raw ingredients scattered across existing modules — `gateway_doctor_payload()` (`server_modules/gateway_health_service.py:397-775`), the passive inventory snapshot (`health/service-inventory.ts`), and journal/checkpoint state (`state/journal.ts`, `state/checkpoints.ts`). The work is assembling+redacting them into one exportable artifact and a UI/API entrypoint, not building new probes.

---

### 4. No memory/resource-pressure monitoring or pre-crash stability snapshot — **MEDIUM**

**Empyralis today**: confirmed absent by grep for `process.memoryUsage|heapUsed|eventLoopUtilization|event.loop.delay|OOM|oom_score` across `empyralis-gateway/src` — zero hits anywhere in production code. The gateway process has no visibility into its own memory growth, event-loop saturation, or an impending OOM kill before it happens.

**OpenClaw**: diagnostics record RSS/heap byte counts, threshold pressure, and growth pressure; critical memory pressure (when `diagnostics.memoryPressureSnapshot:true`) writes a pre-OOM stability bundle with V8 heap stats, Linux cgroup counters, active resource counts, and the largest session/transcript files; liveness warnings separately record event-loop delay, event-loop utilization, CPU-core ratio, and active/waiting/queued session counts when the process is running but saturated (`docs/gateway/health.md:33`). Fatal exits and shutdown timeouts persist the latest snapshot to `~/.openclaw/logs/stability/`, inspectable via `openclaw gateway stability --bundle latest` (same doc line).

**Why it matters**: a gateway that's slowly leaking memory or event-loop-starved on a small VPS (Empyralis's own installer defaults suggest cost-sensitive small droplets) degrades invisibly today — the only signal Empyralis currently has is the binary "did the process crash," with the crash guards in `index.ts` catching the crash itself but nothing upstream warning that one is coming.

**Build size**: medium-large. Node's `process.memoryUsage()`/`perf_hooks.monitorEventLoopDelay()` are built-in and cheap to sample periodically (this part is small); the larger piece is threading that into the existing heartbeat/health-state plumbing (`heartbeat-payload.ts`, `gateway-doctor.ts`) and deciding what threshold triggers a snapshot write, matching the "small, cheap, opt-in" shape OpenClaw uses rather than building a full new subsystem.

---

### 5. BYO-machine installer (`agent_computer.sh`) has no dependency bootstrap — user must pre-install Node themselves — **LOW-MEDIUM**

**Empyralis today**: `scripts/agent_computer.sh` only searches fixed candidate paths for Node (`NODE_BIN_CANDIDATES=("/opt/homebrew/bin/node" "/usr/local/bin/node" "/usr/bin/node")`, `:13`) and, if none resolve, prints `"[Agent Computer] Node.js was not found. Install Node.js or set EMPYRALIS_AGENT_COMPUTER_NODE_BIN."` and stops (`:553,1003`) — confirmed no `brew install`/`apt-get install` fallback anywhere in the script via grep. This script does support macOS launchd (`launchd-install`/`launchd-uninstall`/`launchd-run`, usage block `:18-24`) and Linux/macOS `service-install --system`, so the **service-supervision** side is actually reasonably multi-OS already — the gap is narrower than "no macOS support": it's specifically "doesn't install its own prerequisites." (The fully-automated cloud path, `scripts/install-agent-computer.sh`, *does* self-install Node 20 via NodeSource — `install_node20()`, `:99-111` — but is Ubuntu-22.04/24.04-only by explicit `detect_ubuntu()` gate, `:59-70`; there is no equivalent auto-provisioned cloud path for a macOS or generic-Linux box.)

**OpenClaw**: `install.sh` installs Node 24 (Homebrew on macOS, NodeSource on apt/dnf/yum, apk on Alpine) and Git if missing, on macOS/Linux/WSL (`docs/install/installer.md:73-79`); `install.ps1` does the same on Windows via winget→Chocolatey→Scoop→portable-zip fallback (`docs/install/installer.md:287-292`).

**Why it matters**: for the "pair your own existing machine" flow (as opposed to Empyralis's own-provisioned cloud VPS), a user without Node already installed hits a dead end with a manual-install instruction instead of a working setup.

**Build size**: small. This is a narrow, well-scoped script addition (detect-then-install-Node, mirroring the existing `install_node20()` pattern already proven in `install-agent-computer.sh`) rather than new architecture.

---

### 6. Doctor breadth: 5 checks vs OpenClaw's ~142 — **LOW (tracked, not urgent)**

**Empyralis today**: `buildDefaultGatewayDoctorChecks()` (`health/gateway-doctor.ts:402-410`) covers cloud connection, capability readiness, CLI subscription, iMessage, and supervisor presence — five checks, all report-only except capability-cache-invalidation and supervisor-install-repair.

**OpenClaw**: ~142 doctor-related files (58 `doctor-*.ts` + 84 under `doctor/`) covering auth/OAuth repair, config migration, plugin manifest repair, sandbox/security checks, session lock/snapshot integrity, disk space, browser/CLI checks, cron store migrations (reliability-audit-1 §7, breadth count independently re-derivable from `ls src/commands/doctor-*.ts src/commands/doctor/` in the checkout).

**Why it matters less urgently than #1-#4**: the doctor *contract* (detect/repair/re-validate) is now in place and extensible — this is a matter of adding more checks over time as specific failure modes are observed on real boxes, not a structural gap. Listed for completeness per the task's request to note where Empyralis still trails, not because it needs to be closed before anything else.

**Build size**: incremental — each new check is "one more object pushed into the array" per `gateway-doctor.ts`'s own doc comment (`:34-38`).

---

## Part 2 — Architecture notes (not gaps, context for the above)

- **"Nodes" vs "hardware."** OpenClaw's reliability/observability model for peripherals (`docs/nodes/index.md`) is built around lightweight **paired companion devices** (phones, a Mac in "node mode") that expose narrow capability surfaces (`canvas.*`, `camera.*`, `system.run`) to a gateway that can live entirely in the cloud — a fundamentally different shape from Empyralis's "hardware" concept, where the paired machine **is** the gateway host itself (`empyralis-gateway/src/index.ts`'s `main()` runs the whole gateway process on the provisioned box). This means OpenClaw's node-pairing reliability features (per-command policy gates, `gateway.nodes.allowCommands`/`denyCommands`, device-pairing approval flow) don't map onto Empyralis's provisioning model as direct ports — they answer a different question ("can this peripheral run this command") than Empyralis's hardware layer needs to answer ("is the box running the gateway alive and supervised").
- **Multi-cloud vs BYO-VPS.** OpenClaw's docs (`docs/vps.md:17-29`) list 11 provider guides but implement none of the automation — Empyralis's `vps_provisioning_service.py` implements real API-driven provisioning for 5 providers (DigitalOcean, Google, Hetzner, Vultr, AWS). This is the report's clearest "ahead" finding and is worth remembering when the founder's worry is specifically about hardware reliability rather than provisioning UX — the provisioning *path in* is already strong; the gaps above are concentrated in what happens *after* the box exists (self-update safety, manual restart, diagnosability, resource visibility).

---

## Summary: prioritized punch list

| # | Gap | Why it matters | Build size |
|---|---|---|---|
| 1 | Self-update has no doctor gate before/after swap; post-restart "health check" is a bare PID-alive check | Silent half-alive box after a bad update, reported as success | Medium |
| 2 | No "restart the gateway" UI/API action short of SSH or full VPS destroy | Most common operator ask ("it's stuck, restart it") has no in-product path | Small-Medium |
| 3 | No diagnostics-export bundle | Debugging a broken box requires live SSH access instead of one shareable file | Medium |
| 4 | No memory/event-loop/resource-pressure monitoring | Slow degradation (leak, saturation) on small VPS boxes is invisible until crash | Medium-Large |
| 5 | BYO-machine installer doesn't bootstrap Node itself | Dead-end manual-install message on the "pair your own machine" flow | Small |
| 6 | Doctor covers 5 checks vs OpenClaw's ~142 | Contract is right, breadth will grow with real incidents | Incremental |

**Already at parity or ahead** (verified, see Part 0): automated multi-cloud VPS provisioning, in-gateway doctor with repair+re-validate, self-repairing launchd/systemd supervision, process-wide crash guards, live capability re-advertising without restart, per-channel reconnect/health-check logic (Telegram/WhatsApp/Signal), and shell-sandbox resource limits.
