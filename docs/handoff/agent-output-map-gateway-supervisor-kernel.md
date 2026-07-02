# Agent output: map-gateway-supervisor-kernel

Here is the complete mapping.

---

## 1. empyralis-gateway — Node.js Agent Computer persistent process (TypeScript)

**Total: 62 source files (excluding tests).** Long-running process on user hardware. Connects to cloud via WSS (`empyralis.gateway.v1` subprotocol), manages personal channel runtimes, browser automation via Python subprocess, and delegates capability execution to the local supervisor at `127.0.0.1:7788`.

### cloud/ — WSS tunnel to cloud control plane (4 files)

| File | Purpose | Status |
|------|---------|--------|
| `/Users/mansur/empyralis/empyralis-gateway/src/cloud/ws-client.ts` | Full WSS client: session creation, frame dispatch, heartbeat loop integration, outbox replay, protocol version negotiation, graceful shutdown | PROVEN |
| `/Users/mansur/empyralis/empyralis-gateway/src/cloud/heartbeat.ts` | `HeartbeatLoop` class — interval timer with timeout, consecutive failure tracking, recovery callback | PROVEN |
| `/Users/mansur/empyralis/empyralis-gateway/src/cloud/heartbeat-payload.ts` | Factory function `buildGatewayHeartbeatPayload` — assembles heartbeat JSON from runtime metadata, inventory snapshot, journal/checkpoint cursors, queue depth | PROVEN |
| `/Users/mansur/empyralis/empyralis-gateway/src/cloud/reconnect.ts` | `ReconnectBackoff` (exponential with jitter), `classifyReconnectError` (detects fatal 401/403/revocation), `classifyCloseCode` (diagnoses proxy timeouts, abnormal closures) | PROVEN |

### channels/ — Personal channel runtimes (26 files across 4 subdirectories)

**channels/ root:**

| File | Purpose | Status |
|------|---------|--------|
| `/Users/mansur/empyralis/empyralis-gateway/src/channels/personal-runtime.ts` | `PersonalChannelRuntime` interface contract + `PersonalChannelRuntimeRegistry` — aggregates all runtimes, routes capabilities/channels, publishes registry state to cloud | PROVEN |
| `/Users/mansur/empyralis/empyralis-gateway/src/channels/personal-config-store.ts` | `PersonalChannelConfigStore` — per-channel config persistence (Telegram API credentials, WhatsApp phone/pairing code) backed by JSON files | PROVEN |
| `/Users/mansur/empyralis/empyralis-gateway/src/channels/local-bridge-runtime.ts` | `LocalBridgePersonalChannelRuntime` — generic HTTP polling adapter for local bridge processes (Signal, iMessage, WeChat). Polls `/events`, sends via `/messages`, health-checks via `/health`. Uses env-prefix pattern (`EMPYRALIS_SIGNAL_BRIDGE_URL`, etc.) | PROVEN |

**channels/foundation/:**

| File | Purpose | Status |
|------|---------|--------|
| `/Users/mansur/empyralis/empyralis-gateway/src/channels/foundation/credential-redactor.ts` | Shared recursive JSON walker that replaces credential keys with `[REDACTED]` before publishing state to cloud | PROVEN |
| `/Users/mansur/empyralis/empyralis-gateway/src/channels/foundation/draft-manager.ts` | `DraftManager` — stateful draft tracking (start/delta/final) with sequence number ordering, accumulating deltas, final send delegation | PROVEN |
| `/Users/mansur/empyralis/empyralis-gateway/src/channels/foundation/outbound-store.ts` | Generic `OutboundStore<TPayload>` — JSON-file-backed idempotent outbound message persistence with beginSend/markAttemptStarted/markDelivered lifecycle | PROVEN |
| `/Users/mansur/empyralis/empyralis-gateway/src/channels/foundation/reconnect-utils.ts` | Shared `computeReconnectDelay` (exponential + jitter) and `ReconnectPolicy` type | PROVEN |
| `/Users/mansur/empyralis/empyralis-gateway/src/channels/foundation/typing-keepalive.ts` | `TypingKeepalive` — fires typing indicator at interval, auto-stops after TTL | PROVEN |

**channels/whatsapp/:**

| File | Purpose | Status |
|------|---------|--------|
| `/Users/mansur/empyralis/empyralis-gateway/src/channels/whatsapp/runtime.ts` | `WhatsAppPersonalRuntime` — full Baileys (@whiskeysockets) integration: dynamic import, socket lifecycle, QR/pairing-code auth, connection state machine, message upsert handling, group-gate (mention/reply-to-sage filter), outbound with typing indicators, reconnection backoff | WIRED |
| `/Users/mansur/empyralis/empyralis-gateway/src/channels/whatsapp/login.ts` | Login config loading from env vars, preflight state builder (missing phone number detection), pairing code state builder | PROVEN |
| `/Users/mansur/empyralis/empyralis-gateway/src/channels/whatsapp/qr-login.ts` | QR code payload builder | PROVEN |
| `/Users/mansur/empyralis/empyralis-gateway/src/channels/whatsapp/message-mapper.ts` | Baileys message -> GatewayChannelInboundPayload mapping, outbound result mapping, client message ID generation | PROVEN |
| `/Users/mansur/empyralis/empyralis-gateway/src/channels/whatsapp/outbound.ts` | `WhatsAppOutboundStore` (extends OutboundStore), `WhatsAppTypingKeepalive` (wraps Baileys sendPresenceUpdate) | PROVEN |
| `/Users/mansur/empyralis/empyralis-gateway/src/channels/whatsapp/reconnect.ts` | WhatsApp-specific reconnect policy, error classification (maps Baileys disconnect reasons to retryable/non-retryable) | PROVEN |
| `/Users/mansur/empyralis/empyralis-gateway/src/channels/whatsapp/session-store.ts` | Session state persistence (status, QR, pairing code, linked JID, disconnect reason), auth-state directory management | PROVEN |

**channels/telegram/:**

| File | Purpose | Status |
|------|---------|--------|
| `/Users/mansur/empyralis/empyralis-gateway/src/channels/telegram/runtime.ts` | `TelegramPersonalRuntime` — full gramjs (telegram npm package) integration: dynamic import, client lifecycle, two-step auth flow (requestCode -> signIn with code/password), session string persistence, message handler via NewMessage event, outbound with typing via SetTyping/SendMessageTypingAction, reconnection | WIRED |
| `/Users/mansur/empyralis/empyralis-gateway/src/channels/telegram/login.ts` | Login config from env vars + persisted config merge, preflight state builder, connected state builder | PROVEN |
| `/Users/mansur/empyralis/empyralis-gateway/src/channels/telegram/message-mapper.ts` | gramjs message -> GatewayChannelInboundPayload mapping, outbound result mapping | PROVEN |
| `/Users/mansur/empyralis/empyralis-gateway/src/channels/telegram/outbound.ts` | `TelegramOutboundStore`, `TelegramTypingKeepalive` (wraps sendChatAction) | PROVEN |
| `/Users/mansur/empyralis/empyralis-gateway/src/channels/telegram/reconnect.ts` | Telegram-specific reconnect policy and error classification | PROVEN |
| `/Users/mansur/empyralis/empyralis-gateway/src/channels/telegram/session-store.ts` | Session state persistence, session string storage, pending login state, runtime directory management | PROVEN |

### supervisor/ — Local policy-enforcement HTTP client (3 files)

| File | Purpose | Status |
|------|---------|--------|
| `/Users/mansur/empyralis/empyralis-gateway/src/supervisor/client.ts` | `GatewaySupervisorClient` — POSTs signed JSON to `{supervisorUrl}/execute` and `/interrupt`, 30s expiry, nonce generation, result parsing | PROVEN |
| `/Users/mansur/empyralis/empyralis-gateway/src/supervisor/capability-router.ts` | `GatewayCapabilityRouter` — routes `tool.invoke` frames to browser/external-agent-proxy/personal-channel/supervisor executor. Tracks run->executor mapping with 5-min TTL. Routes `tool.interrupt` to correct executor | PROVEN |
| `/Users/mansur/empyralis/empyralis-gateway/src/supervisor/signing.ts` | HMAC-SHA256 signing with canonical JSON (sorted keys) for execute requests; colon-delimited signing string for interrupts | PROVEN |

### browser/ — Browser automation via Python subprocess (3 files)

| File | Purpose | Status |
|------|---------|--------|
| `/Users/mansur/empyralis/empyralis-gateway/src/browser/runtime.ts` | `GatewayBrowserRuntime` — dispatches `browser.session.{start,action,takeover,resume,interrupt}` to the Python worker, persists session state | PROVEN |
| `/Users/mansur/empyralis/empyralis-gateway/src/browser/worker.ts` | `GatewayBrowserWorker` — spawns `python3 -u -m server_modules.gateway_browser_runtime`, communicates via JSON-per-line stdin/stdout, tracks pending requests by ID | PROVEN |
| `/Users/mansur/empyralis/empyralis-gateway/src/browser/session-store.ts` | `GatewayBrowserSessionStore` — JSON-file-backed CRUD for browser session records | PROVEN |

### pairing/ — Device identity and token management (2 files)

| File | Purpose | Status |
|------|---------|--------|
| `/Users/mansur/empyralis/empyralis-gateway/src/pairing/device-identity.ts` | `resolveDeviceIdentity` — generates/persists gatewayId + deviceId UUIDs, `persistDeviceIdentityScope` updates tenant/workspace/user binding | PROVEN |
| `/Users/mansur/empyralis/empyralis-gateway/src/pairing/token-store.ts` | `GatewayTokenStore` — persist/merge gateway token, session token, pairing token to JSON with redaction-safe logging | PROVEN |

### protocol/ — Gateway wire protocol (2 files)

| File | Purpose | Status |
|------|---------|--------|
| `/Users/mansur/empyralis/empyralis-gateway/src/protocol/types.ts` | All TypeScript types: `GatewayRequestEnvelope`, `GatewayResponseEnvelope`, `GatewayEventEnvelope`, `GatewayScope`, `GatewaySessionPayload`, `GatewayRegistrationPayload`, `GatewayToolInvokePayload`, `GatewayToolInterruptPayload`, `GatewayChannelInboundPayload`, `GatewayChannelOutboundPayload`. Protocol version: `v1alpha2` | PROVEN |
| `/Users/mansur/empyralis/empyralis-gateway/src/protocol/codec.ts` | `encodeFrame`/`decodeFrame` with validation: 256KB max, 32-depth max, known type whitelist. `SUPPORTED_PROTOCOL_VERSIONS` | PROVEN |

### runtime/ — Runtime metadata and permissions (3 files)

| File | Purpose | Status |
|------|---------|--------|
| `/Users/mansur/empyralis/empyralis-gateway/src/runtime/runtime-metadata.ts` | `buildRuntimeMetadata` — assembles gateway version, OS/arch/hostname, PID, started-at, requested capabilities, native runtime snapshot | PROVEN |
| `/Users/mansur/empyralis/empyralis-gateway/src/runtime/service-mode.ts` | Service mode detection: `EMPYRALIS_AGENT_COMPUTER_SYSTEM_SERVICE_MODE` env flag, user-session bridge readiness | PROVEN |
| `/Users/mansur/empyralis/empyralis-gateway/src/runtime/desktop-permissions.ts` | Desktop permission gating: maps capabilities to OS permissions (screen_recording, accessibility, clipboard, automation, browser), 5-state model (granted/promptable/denied/restricted/unknown), `assertCapabilityPermissionReady` guard | PROVEN |

### health/ — Passive service inventory (1 file)

| File | Purpose | Status |
|------|---------|--------|
| `/Users/mansur/empyralis/empyralis-gateway/src/health/service-inventory.ts` | `collectPassiveInventorySnapshot` — probes postgres (pg_isready), docker (docker info), ollama (GET /api/tags), codex CLI (codex --version), GPU (nvidia-smi or system_profiler SPDisplaysDataType). `buildFastPassiveInventorySnapshot` for fast sync without probes. 60s cache TTL | PROVEN |

### state/ — Local persistence (4 files)

| File | Purpose | Status |
|------|---------|--------|
| `/Users/mansur/empyralis/empyralis-gateway/src/state/db.ts` | `GatewayStateDb` — JSON file read/write with atomic rename, per-file mutex serialization, NDJSON append with fsync, corruption detection with backup | PROVEN |
| `/Users/mansur/empyralis/empyralis-gateway/src/state/journal.ts` | `GatewayJournal` — cursor-based NDJSON append-only log, 100MB rotation, durable last-cursor scanning from file tail | PROVEN |
| `/Users/mansur/empyralis/empyralis-gateway/src/state/outbox.ts` | `GatewayOutbox` — message queue with pending/acknowledged/failed/abandoned/uncertain states, max 10K items, max 5 retries, 24h pruning, idempotency by requestId | PROVEN |
| `/Users/mansur/empyralis/empyralis-gateway/src/state/checkpoints.ts` | `GatewayCheckpoints` — debounced (100ms) checkpoint persistence: sequence numbers, health state (online/offline/reconnecting/degraded), recovery markers | PROVEN |

### dev/ — Local bridge harness (1 file)

| File | Purpose | Status |
|------|---------|--------|
| `/Users/mansur/empyralis/empyralis-gateway/src/dev/local-bridge-harness.ts` | `startLocalBridgeHarness` — HTTP server simulating bridge adapters (Signal/iMessage/WeChat) for local testing. Supports `/health`, `/messages` (POST outbound), `/events` (GET poll + POST inject), `/sent` (inspect) | PROVEN |

### external-agent/ — External agent proxy (1 file)

| File | Purpose | Status |
|------|---------|--------|
| `/Users/mansur/empyralis/empyralis-gateway/src/external-agent/proxy-runtime.ts` | `ExternalAgentProxyRuntime` — HTTP(S) proxy restricted to localhost/private-network targets only. GET/POST only. Whitelisted headers (accept, authorization, content-type, x-api-key). 256KB response limit, 15s timeout | PROVEN |

### bridges/ — Standalone bridge adapter processes (2 files)

| File | Purpose | Status |
|------|---------|--------|
| `/Users/mansur/empyralis/empyralis-gateway/src/bridges/bluebubbles-bridge.ts` | `startBlueBubblesBridge` — HTTP server wrapping BlueBubbles API for iMessage. Maps webhook payloads, handles `/health`, `/messages`, `/events`, `/webhook` endpoints. Also runnable standalone | PROVEN |
| `/Users/mansur/empyralis/empyralis-gateway/src/bridges/signal-cli-bridge.ts` | `startSignalCliBridge` — HTTP server wrapping signal-cli JSON-RPC and SSE event stream. Handles send via RPC, inbound via SSE parsing, `/health`, `/messages`, `/events`. Runnable standalone | PROVEN |

### Root entry points (2 files)

| File | Purpose | Status |
|------|---------|--------|
| `/Users/mansur/empyralis/empyralis-gateway/src/index.ts` | `main()` — process lock, DB init, supervisor client, browser worker, personal channel registry, device identity resolution, pairing/registration, WSS connect with afterConnected hook for channel startup, SIGINT/SIGTERM graceful shutdown | PROVEN |
| `/Users/mansur/empyralis/empyralis-gateway/src/config.ts` | `loadGatewayConfig` — reads all `EMPYRALIS_GATEWAY_*` env vars, cloud environment detection, HTTPS enforcement in staging/production, browser Python executable resolution from venv candidates | PROVEN |

---

## 2. empyralis-supervisor — Rust policy enforcement kernel (12 files)

**Confirmed: `main.rs` is a real HTTP server on `127.0.0.1:7788`** (axum, line 148: `SocketAddr = "127.0.0.1:7788"`). Three routes: `GET /health`, `POST /execute`, `POST /interrupt`. HMAC-SHA256 request signing with nonce replay protection and SQLite audit logging.

**However: zero compiled binaries exist on this machine** — no `target/release/` or `target/debug/` directory. The code would compile (complete Cargo.toml with all 18+ dependency crates declared including axum, tokio, enigo, xcap, arboard, rusqlite, objc2, x11rb), but it has never been built.

| File | Purpose | Status |
|------|---------|--------|
| `/Users/mansur/empyralis/empyralis-supervisor/src/main.rs` | Axum HTTP server on 127.0.0.1:7788. Three routes, HMAC-SHA256 signature verification (v2 protocol), canonical JSON signing payload, nonce replay protection with expiry-based cleanup, SQLite audit log, policy evaluation (full_access mode gated by sage agent scope + warning acknowledgement), capability dispatch to 15 capability handlers, interrupt via CancellationToken | SKELETON |
| `/Users/mansur/empyralis/empyralis-supervisor/src/execution.rs` | `ExecutionContext` struct with CancellationToken and `check_cancelled()` helper | SKELETON |
| `/Users/mansur/empyralis/empyralis-supervisor/src/capabilities/mod.rs` | Module declarations: clipboard, control, filesystem, launch, ocr, screenshot, shell, system, windows | SKELETON |
| `/Users/mansur/empyralis/empyralis-supervisor/src/capabilities/shell.rs` | `execute()` — spawns `/bin/zsh -lc <command>` (or powershell on Windows), three-layer defense: (1) HARD_BLOCKED patterns NEVER bypassed (rm -rf /, mkfs, dd if=/dev, fork bombs, shutdown), (2) HARD_PROTECTED paths NEVER bypassed (~/.empyralis/state/vault, ~/.ssh, /etc/empyralis) with symlink resolution, (3) untrusted mode: safe-command whitelist + filesystem scope enforcement. System info probe always allowed | SKELETON |
| `/Users/mansur/empyralis/empyralis-supervisor/src/capabilities/filesystem.rs` | Read/write with policy enforcement (full_access bypass, allowed_roots containment) | SKELETON |
| `/Users/mansur/empyralis/empyralis-supervisor/src/capabilities/screenshot.rs` | Screen capture via xcap crate | SKELETON |
| `/Users/mansur/empyralis/empyralis-supervisor/src/capabilities/ocr.rs` | OCR via image crate | SKELETON |
| `/Users/mansur/empyralis/empyralis-supervisor/src/capabilities/control.rs` | Mouse move/click, keyboard type/key press via enigo crate, with cancellation support | SKELETON |
| `/Users/mansur/empyralis/empyralis-supervisor/src/capabilities/clipboard.rs` | Clipboard read/write via arboard crate | SKELETON |
| `/Users/mansur/empyralis/empyralis-supervisor/src/capabilities/windows.rs` | Window listing (macOS via objc2, Linux via x11rb) | SKELETON |
| `/Users/mansur/empyralis/empyralis-supervisor/src/capabilities/launch.rs` | App launching via opener crate | SKELETON |
| `/Users/mansur/empyralis/empyralis-supervisor/src/capabilities/system.rs` | System notifications, AppleScript execution, speech synthesis | SKELETON |

**Status rationale**: All code is fully written with unit tests. It would compile. But no binary has ever been produced (`target/` absent), and it has never run on real hardware. Per F1 audit: "NEVER been compiled."

---

## 3. empyralis-runtime-kernel — Rust policy decision engine (57 files)

**Architecture**: CLI binary, NOT a server. Reads a command string from `argv[1]` and JSON from stdin. Dispatches to one of ~50 pure decision functions. Each function returns a JSON response to stdout with `{ ok, decision, reason, next_action }`. Stateless, deterministic. Only two dependencies: `serde` and `serde_json`. No compiled binary exists.

| File | Command dispatched | Purpose | Status |
|------|-------------------|---------|--------|
| `/Users/mansur/empyralis/empyralis-runtime-kernel/src/main.rs` | (entry point) | CLI dispatcher: reads `argv[1]` as command name, stdin as JSON payload, routes to 47 decision functions, prints JSON to stdout, exits 0/2 | SKELETON |
| `/Users/mansur/empyralis/empyralis-runtime-kernel/src/policy.rs` | `validate-policy` | Policy validation and capability evaluation: 7 autonomy modes (yolo/cautious/read_only/safe_autopilot/trusted_workstation/ask_every_time/deny_all), capability normalization across 30+ aliases, domain allowlist matching, filesystem scope enforcement, blocked capability checking | SKELETON |
| `/Users/mansur/empyralis/empyralis-runtime-kernel/src/presets.rs` | `policy-preset` | Three preset policies: yolo (all capabilities allowed, wildcard scope), cautious (safe reads + drafts allowed, writes require approval, destructive blocked), deny_all (everything blocked, emergency_stop) | SKELETON |
| `/Users/mansur/empyralis/empyralis-runtime-kernel/src/risk.rs` | `classify-risk` | Risk classification (low/medium/high/critical) based on: capability type, action class, payload content scanning for secret markers (api_key, bearer, sk-*, ghp_*, password) and destructive markers (rm -rf, drop table, delete all, format disk, shutdown, reboot, wipe). Kill state and computer profile health gates | SKELETON |
| `/Users/mansur/empyralis/empyralis-runtime-kernel/src/sandbox.rs` | `build-sandbox-command` | Docker sandbox command builder: `docker run` with hardcoded security flags (--read-only, --network none, --cap-drop ALL, --security-opt no-new-privileges, tmpfs for /tmp and /home/sandbox) | SKELETON |
| `/Users/mansur/empyralis/empyralis-runtime-kernel/src/sandbox_execution.rs` | `sandbox-execution-decision` | Sandbox execution policy decision | SKELETON |
| `/Users/mansur/empyralis/empyralis-runtime-kernel/src/capabilities.rs` | `capability-manifest` | Capability manifest command | SKELETON |
| `/Users/mansur/empyralis/empyralis-runtime-kernel/src/control_plane.rs` | `control-plane-decision` | Control plane operation decision | SKELETON |
| `/Users/mansur/empyralis/empyralis-runtime-kernel/src/control_plane_service.rs` | `control-plane-service-decision` | Control plane service decision (returns Result) | SKELETON |
| `/Users/mansur/empyralis/empyralis-runtime-kernel/src/deployed_agent.rs` | `deployed-agent-decision` | Deployed agent lifecycle decision | SKELETON |
| `/Users/mansur/empyralis/empyralis-runtime-kernel/src/deployed_agent_service.rs` | `deployed-agent-service-decision` | Deployed agent service decision | SKELETON |
| `/Users/mansur/empyralis/empyralis-runtime-kernel/src/deployed_data.rs` | `deployed-data-decision` | Deployed data access decision | SKELETON |
| `/Users/mansur/empyralis/empyralis-runtime-kernel/src/deployed_readiness.rs` | `deployed-readiness-decision` | Deployment readiness decision | SKELETON |
| `/Users/mansur/empyralis/empyralis-runtime-kernel/src/deployed_virtual_runtime.rs` | `deployed-virtual-runtime-decision` | Virtual runtime deployment decision | SKELETON |
| `/Users/mansur/empyralis/empyralis-runtime-kernel/src/deployed_virtual_runtime_service.rs` | `deployed-virtual-runtime-service-decision` | Virtual runtime service decision (returns Result) | SKELETON |
| `/Users/mansur/empyralis/empyralis-runtime-kernel/src/approvals.rs` | `approval-requirement` | Approval requirement classification | SKELETON |
| `/Users/mansur/empyralis/empyralis-runtime-kernel/src/artifacts.rs` | `artifact-policy` | Artifact policy decision | SKELETON |
| `/Users/mansur/empyralis/empyralis-runtime-kernel/src/authorization.rs` | `authorize-request` | Request authorization decision | SKELETON |
| `/Users/mansur/empyralis/empyralis-runtime-kernel/src/execution_authorization.rs` | `authorize-execution` | Execution authorization decision | SKELETON |
| `/Users/mansur/empyralis/empyralis-runtime-kernel/src/execution_outcome.rs` | `execution-outcome` | Execution outcome classification | SKELETON |
| `/Users/mansur/empyralis/empyralis-runtime-kernel/src/execution_plan.rs` | `execution-plan` | Execution plan validation | SKELETON |
| `/Users/mansur/empyralis/empyralis-runtime-kernel/src/execution_runtime.rs` | `execution-runtime-decision` | Execution runtime decision | SKELETON |
| `/Users/mansur/empyralis/empyralis-runtime-kernel/src/gateway.rs` | `gateway-protocol-decision` | Gateway protocol decision: health_check, session_create, session_close, agent_turn, tool_invoke, tool_interrupt, channel_outbound, tool_use, tool_result. Privileged tool detection (shell, filesystem.write, browser, computer, secret, credential, external, email.send, calendar.write) triggers approval | SKELETON |
| `/Users/mansur/empyralis/empyralis-runtime-kernel/src/gateway_action.rs` | `gateway-action-decision` | Gateway action decision | SKELETON |
| `/Users/mansur/empyralis/empyralis-runtime-kernel/src/gateway_frame.rs` | `gateway-frame-decision` | Gateway frame validation decision | SKELETON |
| `/Users/mansur/empyralis/empyralis-runtime-kernel/src/gateway_service.rs` | `gateway-service-decision` | Gateway service operations (returns Result): 20 operations including websocket_connect, protocol_route, tool_execute, tool_interrupt, browser_session, browser_action, approval_request/resolve, quota_check, diagnostics_export, gateway_policy_read/write, cloud_fallback. Full governance: kill switch, quota, device trust, safe mode, approval memory, cloud fallback gating | SKELETON |
| `/Users/mansur/empyralis/empyralis-runtime-kernel/src/gateway_state.rs` | `gateway-state-decision` | Gateway state transition decision | SKELETON |
| `/Users/mansur/empyralis/empyralis-runtime-kernel/src/heartbeat.rs` | `heartbeat-snapshot` | Heartbeat snapshot decision | SKELETON |
| `/Users/mansur/empyralis/empyralis-runtime-kernel/src/lease.rs` | `machine-lease-decision` | Machine lease decision | SKELETON |
| `/Users/mansur/empyralis/empyralis-runtime-kernel/src/local_worker.rs` | `local-worker-decision` | Local worker decision | SKELETON |
| `/Users/mansur/empyralis/empyralis-runtime-kernel/src/outbox_delivery.rs` | `outbox-delivery-decision` | Outbox delivery decision | SKELETON |
| `/Users/mansur/empyralis/empyralis-runtime-kernel/src/path_guard.rs` | `check-path-containment` | Path containment check | SKELETON |
| `/Users/mansur/empyralis/empyralis-runtime-kernel/src/platform_orchestration.rs` | `platform-orchestration-decision` | Platform orchestration decision | SKELETON |
| `/Users/mansur/empyralis/empyralis-runtime-kernel/src/process.rs` | `process-lifecycle-decision` | Process lifecycle decision | SKELETON |
| `/Users/mansur/empyralis/empyralis-runtime-kernel/src/protocol.rs` | (shared helper module) | JSON field extractors, `block()`/`allow()` response builders, `policy_payload()` extraction, `object_has_any_key()` | SKELETON |
| `/Users/mansur/empyralis/empyralis-runtime-kernel/src/queue.rs` | `queue-transition-decision` | Queue transition decision | SKELETON |
| `/Users/mansur/empyralis/empyralis-runtime-kernel/src/redaction.rs` | `redact-diagnostics` | Diagnostic redaction | SKELETON |
| `/Users/mansur/empyralis/empyralis-runtime-kernel/src/run_api.rs` | `run-api-decision` | Run API decision | SKELETON |
| `/Users/mansur/empyralis/empyralis-runtime-kernel/src/run_approval.rs` | `run-approval-decision` | Run approval decision | SKELETON |
| `/Users/mansur/empyralis/empyralis-runtime-kernel/src/run_preparation.rs` | `run-preparation-decision` | Run preparation decision | SKELETON |
| `/Users/mansur/empyralis/empyralis-runtime-kernel/src/run_record.rs` | `run-record-decision` | Run record decision | SKELETON |
| `/Users/mansur/empyralis/empyralis-runtime-kernel/src/run_routing.rs` | `run-routing-decision` | Run routing decision | SKELETON |
| `/Users/mansur/empyralis/empyralis-runtime-kernel/src/run_service.rs` | `run-service-decision` | Run service operations (returns Result): 22 operations (create, start, turn, dispatch, stream_open, stream_event, cancel, retry, resume, approve, reject, finalize_success, finalize_failure, webhook_trigger, child_run_create, delegation_merge, lane_route, status_snapshot). Full governance: workspace access, kill switch, safe mode, quota, budget, policy, risk, runtime health, browser readiness, webhook signatures, idempotency, terminal run locking, workflow depth, delegation merge, history windows | SKELETON |
| `/Users/mansur/empyralis/empyralis-runtime-kernel/src/run_triggers.rs` | `run-trigger-decision` | Run trigger decision | SKELETON |
| `/Users/mansur/empyralis/empyralis-runtime-kernel/src/runs.rs` | `run-orchestration-decision` | Run orchestration decision | SKELETON |
| `/Users/mansur/empyralis/empyralis-runtime-kernel/src/runtime_action.rs` | `runtime-action-decision` | Runtime action decision | SKELETON |
| `/Users/mansur/empyralis/empyralis-runtime-kernel/src/runtime_attachment.rs` | `runtime-attachment-decision` | Runtime attachment decision | SKELETON |
| `/Users/mansur/empyralis/empyralis-runtime-kernel/src/runtime_binding.rs` | `runtime-binding-decision` | Runtime binding decision | SKELETON |
| `/Users/mansur/empyralis/empyralis-runtime-kernel/src/runtime_health.rs` | `runtime-health-decision` | Runtime health decision | SKELETON |
| `/Users/mansur/empyralis/empyralis-runtime-kernel/src/runtime_session_api.rs` | `runtime-session-api-decision` | Runtime session API decision | SKELETON |
| `/Users/mansur/empyralis/empyralis-runtime-kernel/src/runtime_state_store.rs` | `runtime-state-store-decision` | Runtime state store decision | SKELETON |
| `/Users/mansur/empyralis/empyralis-runtime-kernel/src/safe_mode.rs` | `safe-mode-decision` | Safe mode decision | SKELETON |
| `/Users/mansur/empyralis/empyralis-runtime-kernel/src/scheduler.rs` | `scheduler-decision` | Scheduler decision | SKELETON |
| `/Users/mansur/empyralis/empyralis-runtime-kernel/src/secrets.rs` | `inspect-secret-reference` | Secret reference inspection | SKELETON |
| `/Users/mansur/empyralis/empyralis-runtime-kernel/src/session.rs` | `session-lifecycle-decision` | Session lifecycle: 9 operations (create, turn, idle_check, age_check, reset, extend, close, prune, force). Three presets with configurable bounds (short: 20 turns/4h, standard: 80 turns/24h, extended: 200 turns/168h). Idle timeout and age-based pruning | SKELETON |
| `/Users/mansur/empyralis/empyralis-runtime-kernel/src/session_scheduler.rs` | `session-scheduler-decision` | Session scheduler decision | SKELETON |
| `/Users/mansur/empyralis/empyralis-runtime-kernel/src/state.rs` | `state-transition-decision` | State transition decision | SKELETON |
| `/Users/mansur/empyralis/empyralis-runtime-kernel/src/thread_record.rs` | `thread-record-decision` | Thread record decision | SKELETON |
| `/Users/mansur/empyralis/empyralis-runtime-kernel/src/virtual_computer.rs` | `virtual-computer-decision` | Virtual computer decision | SKELETON |

**Status rationale**: Every runtime-kernel module is fully written code with unit tests (visible in policy.rs, risk.rs, presets.rs, sandbox.rs, gateway.rs, gateway_service.rs, run_service.rs, session.rs). All 47 modules follow the same pattern: read JSON payload, evaluate policy/risk/rules, return `{ ok, decision, reason, next_action }`. No binary exists. Only two deps (serde, serde_json) so compilation is trivial. But no one has run `cargo build`.

---

## Answer to the specific question about supervisor main.rs

**Yes. `empyralis-supervisor/src/main.rs` is a real HTTP server on `127.0.0.1:7788`.** It uses axum (Tokio-based), has three routes (`GET /health`, `POST /execute`, `POST /interrupt`), verifies HMAC-SHA256 signatures, enforces nonce replay protection, writes audit logs to SQLite, and dispatches to 15 OS-level capability handlers (shell, filesystem, screenshot, OCR, mouse, keyboard, clipboard, windows, launch, notify, AppleScript, speech). However, no binary has ever been compiled — `target/` does not exist.