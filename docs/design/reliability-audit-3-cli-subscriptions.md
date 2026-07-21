# Reliability Audit 3 — CLI Subscriptions (Claude Code / Codex on a Gateway)

**Scope:** READ-ONLY fact-gathering. No fixes proposed. Every claim is
file:line-cited against the `fix/slack-channel-uniqueness` branch as checked
out at audit time (2026-07-21). Where a fact comes from prior verified work
already recorded in `docs/PLATFORM-MAP.md`, that section is cited directly
rather than re-derived.

---

## 1. What "cli_subscription" is

A `model_config.mode == "cli_subscription"` agent runs its turns on the
*customer's own machine*, under the *customer's own* Claude Code / Codex
login — never a platform-held credential. `docs/CLI_SUBSCRIPTION_SPEC.md:1-8`
states the governing principle: "Subscription-powered turns are ONLY
legitimate when the CLI runs on the USER's own hardware with the USER's own
login. Platform never stores, proxies, or pools subscription credentials. No
fallback." That spec is marked `Status: Spec — not built` as of 2026-07-03,
but the implementation described below is now built and live
(`docs/PLATFORM-MAP.md:2813-3120`, Part 26).

---

## 2. The three gateway capabilities: `cli.install`, `cli.login.start`, `cli.login.input`

Defined in `empyralis-gateway/src/llm/cli-setup-runtime.ts:13-17` and mirrored
in `server_modules/cli_setup_service.py:9-11`. Routed through the capability
router gated on the `cli_setup` desktop permission
(`empyralis-gateway/src/runtime/desktop-permissions.ts:84-86`).

### 2.1 `cli.install` — `empyralis-gateway/src/llm/cli-installer.ts`

- Runs `npm install -g <package>` directly on the host (never inside the
  Docker shell-sandbox), because the later `claude`/`codex` spawn is also
  host-side and needs the binary on PATH (`cli-installer.ts:5-16`).
- Exactly two hardcoded packages, no caller-supplied install string:
  `@anthropic-ai/claude-code` and `@openai/codex`
  (`cli-installer.ts:56-59`).
- Preflights that `npm` is on PATH; fails fast with `npm_missing` otherwise
  (`cli-installer.ts:229-236`).
- Passes `--prefix` explicitly when `NPM_CONFIG_PREFIX` is set, because a
  stray `.npmrc` on the box can otherwise override the env var and the
  install lands in a location the systemd unit's `ReadWritePaths` doesn't
  grant (`cli-installer.ts:238-249`; the systemd unit itself at
  `scripts/install-agent-computer.sh:384-432`).
- 180s timeout (`DEFAULT_TIMEOUT_MS`, `cli-installer.ts:68`), SIGTERM then
  SIGKILL after a 5s grace (`cli-installer.ts:69,151-168`).
- Classifies failure into `npm_missing | permission_denied | network_error |
  timeout | crash` by string-matching stderr/stdout
  (`cli-installer.ts:198-212`).
- After `npm install -g` exits 0, it **re-verifies** the binary actually
  resolves on PATH via the shared `resolveCommandPath` before declaring
  success — npm can exit 0 with a prefix mismatch that leaves the binary
  unreachable (`cli-installer.ts:267-279`).
- On success, `cli-setup-runtime.ts:124-128` calls
  `invalidatePassiveInventoryCache()` so the UI's readiness row doesn't sit
  on a stale "not installed" for up to 60s (the passive-inventory cache TTL,
  §5).

### 2.2 `cli.login.start` / `cli.login.input` — `empyralis-gateway/src/llm/cli-login-session.ts`

- A *held-open*, stdin-piped child process per `run_id`
  (`cli-login-session.ts:5-16,504-511`) — deliberately different from the
  one-shot `stdio: ["ignore", ...]` spawn used for actual generation
  (`cli-runner.ts`).
- Five auth methods across the two runtimes
  (`cli-login-session.ts:63-68,129-202,207-210`):
  - `codex`: `device_auth` (default), `api_key`, `access_token`.
  - `claude_code`: `claudeai` (default — Claude subscription/Pro/Max/Team),
    `console` (Anthropic Console/API billing), `api_key`.
- `claude setup-token` is **explicitly excluded** as a gateway-spawned
  method: that command never persists the token anywhere, only prints it to
  stdout for a human to copy — spawned headlessly it would produce "a
  guaranteed, silent dead end" (`cli-login-session.ts:29-39`). It is instead
  surfaced as an owner-run, owner-pasted step in the frontend's own guided
  panel, entirely outside the Gateway.
- Credential-safety allowlist: the module never forwards raw stdout/stderr.
  It only relays a line that positively matches a URL pattern or a
  "paste/enter/device code" prompt pattern; everything else — including
  whatever prints after a successful exchange — is buffered locally
  (bounded, diagnostics-only) and never emitted
  (`cli-login-session.ts:17-27,341-416`).
- Session timeout: 5 minutes (`DEFAULT_SESSION_TIMEOUT_MS = 5 * 60_000`,
  `cli-login-session.ts:231`), SIGTERM then SIGKILL after a 5s grace
  (`cli-login-session.ts:232,634-655`).
- `cli.login.input` writes the pasted code / API key / access token verbatim
  to the child's stdin + `\n` (`cli-login-session.ts:565-591`). For
  stdin-secret methods (`api_key`/`access_token`) only one write is ever
  allowed per session (`secretSubmitted` guard,
  `cli-login-session.ts:579-584`).
- `stdbuf -oL -eL` is prefixed when available to force line-buffered output
  — without it, Node's piped stdio causes libc to block-buffer the child's
  stdout until process exit, so the URL/code would sit invisibly in a
  kernel buffer for minutes (`cli-login-session.ts:418-438`).
- A `tool.interrupt` on the same `run_id` can cancel an in-flight login
  session (`cli-setup-runtime.ts:101-110`, `cli-login-session.ts:596-603`).

**Where the credential actually lands, per the CLI's own docs
(`cli-login-session.ts:44-50`):** macOS Keychain always (never a file,
`claude_cli`); Linux/Windows write `~/.claude/.credentials.json` (or
`$CLAUDE_CONFIG_DIR/.credentials.json`). Codex writes `~/.codex/auth.json`.
The Gateway process never reads these contents — only their *existence* is
checked (§5).

---

## 3. How a real turn invokes the CLI

### 3.1 Cold-spawn path (the default) — `empyralis-gateway/src/llm/cli-runner.ts`

`runCliSubscription()` spawns the CLI **fresh, per turn**, with `stdio:
["ignore", "pipe", "pipe"]` — stdin is closed so the process can never block
on interactive input (`cli-runner.ts:198-202`).

- Claude Code invocation: `claude -p <prompt> --output-format stream-json
  --verbose --tools ""` (`cli-runner.ts:155`) — tool-free, single-turn,
  parses the terminal `result` JSONL event (`cli-runner.ts:280-329`).
- Codex invocation: `codex exec <prompt> --json --skip-git-repo-check
  --sandbox read-only` (`cli-runner.ts:167`) — parses `turn.completed` /
  `turn.failed` JSONL events (`cli-runner.ts:355-396`).
- Auth failure detection is a **string match** against known marker phrases
  in stdout/stderr (`CLAUDE_AUTH_MARKERS` / `CODEX_AUTH_MARKERS`,
  `cli-runner.ts:278,331-333`) — not a structured signal from the CLI.
- One hard timeout (caller-supplied, default 120s per
  `llm/runtime.ts:26`, max 600s per `runtime.ts:27`), SIGTERM then SIGKILL
  after a 5s grace (`cli-runner.ts:101,216-233`).
- **No retry of any kind.** A single spawn attempt; any failure (crash,
  timeout, not-installed, not-authenticated) is thrown once as a typed
  `CliRunError` and propagated (`cli-runner.ts:401-423`).

### 3.2 Dispatch entry point — `empyralis-gateway/src/llm/runtime.ts`

`GatewayLLMRuntime.generateViaCli()` (`runtime.ts:341-422`) is the single
call site. It picks one of three backends per turn:

1. Codex warm daemon, if `EMPYRALIS_GATEWAY_CODEX_APP_SERVER=1`
   (`runtime.ts:358`, backend in `codex-app-server.ts`).
2. Claude prewarm pool, if `EMPYRALIS_GATEWAY_CLAUDE_PREWARM=1`
   (`runtime.ts:362`, backend in `claude-cli-prewarm.ts`).
3. Otherwise the cold-spawn path above (`runtime.ts:399-407`).

All three funnel into the same typed-error → platform-voice mapping,
`cliErrorMessage()` (`runtime.ts:159-175`): `not_installed` →
"is not installed", `not_authenticated` → "is not signed in", `timeout` →
"timed out", anything else → "exited unexpectedly on this Gateway
(<message>)". That exact phrase set is what the backend's
`_friendly_cli_subscription_error()` pattern-matches on
(`server_modules/sage_agent_runtime_service.py:933-989`).

### 3.3 Control-plane dispatch — `server_modules/sage_agent_runtime_service.py`

`_dispatch_cli_subscription_gateway_brain()` (`sage_agent_runtime_service.py:1161-1370`):

1. Rejects unsupported runtimes and missing `gateway_binding` up front
   (`:1203-1219`), each ledgered as a failure even before any dispatch
   attempt (`_ledger_cli_subscription_failure`, `:1033-1074`).
2. Reads the Gateway's own self-reported per-runtime `llm_runtimes`
   readiness (installed/authenticated) from its last heartbeat/capability
   payload via `_cli_subscription_readiness_reason()` (`:992-1030`) —
   **this is a cached signal from the last heartbeat, not a live probe at
   turn time.**
3. Calls `gateway_execution_service.execute_tool_via_gateway(capability_id=
   "llm.generate", durable=True, durable_deadline_seconds=40, ...)`
   (`:1309-1337`).
4. On any exception, or an empty completion, ledgers the failure and raises
   a `RuntimeError` with the platform-voice message. **There is no retry,
   no fallback runtime, and no fallback to platform credits** — the code
   comment states this explicitly: "HARD RULE — no fallback"
   (`:1190-1196`).
5. On success, ledgers the turn as `execution_tier=gateway_brain` and
   records usage (real token counts if the CLI reported them, otherwise
   `tokens_known=False` rather than a fabricated zero — `:1094-1108`).

---

## 4. Delivery transport reliability (Gateway ⇄ backend WSS)

This is a distinct layer from CLI reliability, but a `cli_subscription`
turn's dispatch rides on it, so its properties directly bound end-to-end
reliability.

- **Durable, queue-based delivery**, not push-and-retry: `_PendingInvoke`
  objects are enqueued to `_PENDING_GATEWAY_INVOKES[gateway_id]`
  (`gateway_protocol_service.py:105-126`) and flushed either immediately on
  enqueue (`_kick_immediate_flush`) or on the next heartbeat/reconnect as a
  backstop (`docs/PLATFORM-MAP.md:2946-2986`, §26.3, with exact line
  citations into `gateway_protocol_service.py`).
- Gateway → backend heartbeat interval: **10s**
  (`DEFAULT_GATEWAY_HEARTBEAT_INTERVAL_SECONDS = 10`,
  `server_modules/gateway_registry_service.py:28`). A connection is
  considered stale after `10 * 4 + 10 = 50s` of no inbound frame
  (`_LIVE_GATEWAY_STALE_SECONDS`, `gateway_protocol_service.py:42-44`).
- The Gateway process's own outbound heartbeat cadence defaults to **20s**
  (`heartbeatIntervalMs`, `empyralis-gateway/src/config.ts:144`,
  overridable via `EMPYRALIS_GATEWAY_HEARTBEAT_MS`) — i.e. the Gateway's own
  default heartbeat period (20s) is wider than the backend's staleness
  threshold's implicit expectation of ~10s cadence; the 50s stale window
  still comfortably absorbs one or two missed 20s beats.
- Gateway reconnect backoff: min 1s, max 30s, exponential with jitter
  (`config.ts:145-146`, `empyralis-gateway/src/cloud/reconnect.ts:13-35`).
  Reconnection itself is automatic and unbounded in attempt count (no cap on
  retry count found in `ws-client.ts`).
- The current `durable_deadline_seconds=40` for a `cli_subscription` turn
  (`sage_agent_runtime_service.py:1144` per `PLATFORM-MAP.md:2987-2997`) was
  arrived at after three prior values (240 → 600 → 60 → 120 → 40) driven by
  root-causing three real bugs in this transport (map-eviction race,
  auth-retry storm, and an event-loop freeze that made the WSS socket look
  dead when it was actually alive) — full account in
  `docs/PLATFORM-MAP.md:2904-2945` (§26.2).
- **Gateway process supervision**: the Gateway itself runs under systemd
  with `Restart=always`, `RestartSec=5`
  (`scripts/install-agent-computer.sh:400-401`) — if the Node.js Gateway
  process crashes, systemd restarts it in 5s. This supervises the Gateway
  process only, not the `claude`/`codex` child processes it spawns per turn
  (those are one-shot and simply return an error on crash, per §3.1).

---

## 5. Passive health check for CLI readiness (`capability_readiness.passive_services`)

`empyralis-gateway/src/health/service-inventory.ts` computes `codex_cli` and
`claude_cli` inventory items on every heartbeat cycle (subject to a 60s
in-process cache, `PASSIVE_INVENTORY_CACHE_TTL_MS`,
`service-inventory.ts:86,704-720`).

- **`probeCodexCli`** (`:477-525`): finds the binary via
  `CODEX_CLI_PATH` env, `codex` on PATH, or the macOS app bundle path; runs
  `codex --version` as a best-effort liveness/version string (a slow cold
  start never flips `installed → false`, `:502-505`); `authenticated` is
  existence-only: `~/.codex/auth.json` present OR `CODEX_API_KEY` set
  (`:506-510`) — **the file's contents are never read.**
- **`probeClaudeCli`** (`:549-639`): same PATH-resolution pattern; auth
  presence checks, in order: `ANTHROPIC_API_KEY` / `CLAUDE_CODE_OAUTH_TOKEN`
  env vars, then credential-file existence
  (`~/.claude/.credentials.json`), then — macOS only — a
  `security find-generic-password -s "Claude Code-credentials"` existence
  check against the Keychain (`:591-624`, **never `-w`, never reads the
  secret**). The code's own comment flags a known caveat: Keychain
  access-control is enforced per-requesting-application *at use time*, not
  at this existence-query time, and a background/headless process can be
  denied differently than an interactive Terminal session was — i.e. this
  probe can report "ready" even when a later headless `claude -p ...` spawn
  is denied Keychain access (`:598-608`).
- Status values: `missing` (binary not found), `degraded` (installed but
  not authenticated), `ready` (installed and authenticated) — never a
  distinct "auth expired" state; an expired/revoked credential is
  indistinguishable in this probe from "never logged in" unless the file
  itself was deleted or the Keychain item removed (`:515,629`).
- This probe's result directly gates `llm_runtime` capability readiness for
  `claude_code`/`codex` (`setLlmRuntimeClaudeCodeReady` /
  `setLlmRuntimeCodexReady`, `:752-755`) and is exactly what
  `_cli_subscription_readiness_reason()` (§3.3) reads on the backend side
  via the last heartbeat's public payload — **so backend readiness checking
  is only as fresh as the last heartbeat (≤60s stale cache + heartbeat
  interval), not a live check at turn dispatch time.**

---

## 6. Is there any "keep the session warm" / auto-recovery logic?

**For CLI *process* warmth (latency), yes — two opt-in, flag-gated paths,
both off by default:**

- `empyralis-gateway/src/llm/codex-app-server.ts`: a persistent `codex
  app-server` JSON-RPC daemon, spawned once and reused across **many**
  turns (`thread/start` gives each turn a fresh, isolated context on the
  same warm process). 10-minute idle reap (`IDLE_REAP_MS`, `:31`). Enabled
  via `EMPYRALIS_GATEWAY_CODEX_APP_SERVER=1` (`:398-400`). If the daemon
  process exits or errors, `teardown()` rejects every pending
  request/turn with a `CliRunError("crash", ...)` and the daemon
  re-spawns lazily on the *next* `generate()` call via `ensureReady()`
  (`:86-98,126-156`) — i.e. daemon death self-heals on the next turn, at
  the cost of that one turn failing.
- `empyralis-gateway/src/llm/claude-cli-prewarm.ts`: a pool of
  **single-use** pre-spawned `claude` processes keyed by
  `sha256(model, systemPrompt, reasoningEffort)`. Deliberately NOT a reused
  multi-turn daemon — Claude's `stream-json` input has no per-turn
  isolation primitive; a second stdin message to an already-running process
  is a *continuation* of the same conversation, confirmed live (identical
  `session_id` echoed across two turns sent to one process,
  `claude-cli-prewarm.ts:1-21`, `docs/PLATFORM-MAP.md:3041-3070` §26.5).
  Each entry serves exactly one turn then is torn down; a replacement is
  spawned in the background afterward so the *next* turn for that
  (model, systemPrompt) key often finds a warm process. 10-minute idle reap
  for an unclaimed spare (`IDLE_REAP_MS`, `:44`). Enabled via
  `EMPYRALIS_GATEWAY_CLAUDE_PREWARM=1` (`:353-355`).
- Per `docs/PLATFORM-MAP.md:3016`, the Claude prewarm path has **never been
  exercised with a real successful reply** on the production box — the only
  Gateway that ever had a working Claude login had its registration
  `status: revoked`; the currently-bound test gateway shows
  `claude_code: {installed: true, authenticated: false}`.
- Per `docs/PLATFORM-MAP.md:3029-3039`, the Codex daemon-reuse path (same
  process across 3 turns, confirmed via `ps`) has been verified live, but
  every one of those verifying turns hit Codex's own account-level usage
  limit (quota exhausted until 2026-08-01) — so a real, successful
  *generation* through the warm daemon has also not yet been observed on
  the production box.

**For CLI *authentication* warmth (session/token expiry), no automated
recovery exists anywhere in the codebase.** Concretely:

- No code path calls `cli.login.start` automatically. It is only invoked
  from the frontend's Hardware/Gateway detail page in response to a user
  clicking a "Sign in" control
  (`frontend/app/(account)/w/[workspaceId]/hardware/[gatewayId]/page.tsx:289,397`
  — `postCliAction(.../cli/install)` and `.../cli/login/start`, both inside
  user-initiated `onClick` handlers, polled via `pollLoginEvents`/interval
  only after that click).
- Grepping the Gateway and `sage_agent_runtime_service.py` for
  `refresh|expire|expiry|reauth` found **zero** token-refresh or
  auto-reauth logic; the only "expire"/"refresh" hits are unrelated
  (passive-inventory cache TTL comment, `gateway_registry_service.py`
  session field names for pairing tokens — not CLI subscription auth).
- `sage_agent_runtime_service.py:484-490`'s own docstring states the
  governing rule directly: "If the bound mode/provider is unavailable
  (missing key, local model down, **subscription expired**), the turn fails
  with a platform-voice error ... **NEVER auto-switch to another
  provider.**" Expiry is a documented, expected failure mode, not something
  the system attempts to self-heal.
- Consequence: when a Claude Code or Codex CLI session on the box expires
  or is revoked, every subsequent turn fails immediately with "Heads up:
  Claude Code on your Gateway is not signed in" (or the Codex equivalent),
  ledgered every time (`_ledger_cli_subscription_failure`), until a human
  opens the Hardware page and re-runs the sign-in flow by hand.

---

## 7. Confirmed failure modes (with the exact code path)

| Failure | Detection | User-facing text | Source |
|---|---|---|---|
| npm missing | preflight `commandExists("npm", ...)` | `CliInstallError("npm_missing", ...)` | `cli-installer.ts:229-236` |
| Install exits non-zero | stderr/stdout keyword match | `permission_denied`/`network_error`/`crash` | `cli-installer.ts:198-212,261-265` |
| Install "succeeds" but binary unreachable | post-install `commandExists` re-check | `crash`, explicit prefix-mismatch hint | `cli-installer.ts:267-279` |
| Binary not on PATH at login time | `commandExists` before spawn | `CliLoginError("not_installed", ...)` | `cli-login-session.ts:495-502` |
| Login session exceeds 5 min | `killTimer` | `CliLoginError("timeout", ...)` | `cli-login-session.ts:605-613` |
| Login child exits non-zero | exit code check | `CliLoginError("crash", ...)`, exit code only (no stdout detail — deliberate, see credential-safety note) | `cli-login-session.ts:623-631` |
| CLI binary missing at generate time | `spawnError.code === "ENOENT"` | `CliRunError("not_installed", ...)` | `cli-runner.ts:412-414` |
| CLI reports auth failure | stdout/stderr string-marker match | `CliRunError("not_authenticated", ...)` | `cli-runner.ts:278,293-306,331-333,385-386` |
| Turn exceeds timeout (default 120s / max 600s) | `killTimer` → SIGTERM → SIGKILL | `CliRunError("timeout", ...)` | `cli-runner.ts:418-419`, `runtime.ts:26-27` |
| CLI crashes / unparseable output | exit code / no `result` event | `CliRunError("crash", ...)` | `cli-runner.ts:308-313,391-395` |
| Codex warm daemon process dies | `child.on("exit"/"error")` | all pending requests/turns rejected with `crash`; daemon re-spawns on next call | `codex-app-server.ts:109-110,126-156` |
| Gateway offline / not bound / heartbeat stale | `_cli_subscription_readiness_reason` + `execute_tool_via_gateway` readiness check | platform-voice error, ledgered, **no fallback** | `sage_agent_runtime_service.py:933-1237` |
| Empty completion returned | `if not reply:` | `empty_completion` error, ledgered | `sage_agent_runtime_service.py:1354-1363` |
| Scheduled/autonomous (heartbeat/wake-up) turn for a `cli_subscription` agent | architectural — the durable/scheduled run path (`runs_execution.py`) never reaches `_dispatch_cli_subscription_gateway_brain` at all; it has no concept of `model_config.mode` | explicit `RuntimeError` naming the gap (post-fix); previously silently defaulted to provider `"openai"` with no credentials | `docs/PLATFORM-MAP.md:3354-3477` (§28.2-28.3) |

**None of these failure modes trigger a retry, a fallback runtime, or a
fallback to platform credits.** Every one is a single attempt that either
succeeds or produces one ledgered, user-visible failure.

---

## 8. OpenClaw comparison (factual, `/Users/mansur/openclaw`, read-only)

OpenClaw's CLI-backed session manager for Claude
(`/Users/mansur/openclaw/src/agents/cli-runner/claude-live-session.ts`) is
architecturally different in ways directly relevant to reliability:

- **Genuine multi-turn session reuse**, not single-use: `ClaudeLiveSession`
  objects are kept in a map, reused across turns for the same session key,
  and only closed on `idle`, `restart`, or explicit close reasons
  (`claude-live-session.ts:63-80,443-475`). Idle timeout: 10 minutes
  (`CLAUDE_LIVE_IDLE_TIMEOUT_MS`, `:110`) — the same 10-minute figure
  Empyralis's two prewarm paths independently use for their idle reap, but
  applied to an actually-reused session rather than a single-use pool.
- Spawns run under a dedicated `ProcessSupervisor`
  (`import("../../process/supervisor/index.js").getProcessSupervisor`,
  `:42-45`) rather than a bare `child_process.spawn` call.
- A **no-output watchdog** (`noOutputTimer`) and a separate overall
  **timeout timer** are tracked per in-flight turn
  (`ClaudeLiveTurn.noOutputTimer` / `.timeoutTimer`, `:54-55`), distinct
  concepts Empyralis's `cli-runner.ts` does not separate (it has one
  overall timeout only, no separate "has the CLI gone silent mid-turn"
  detector).
- A dedicated reliability test suite
  (`src/agents/cli-runner.reliability.test.ts`) explicitly exercises retry
  and failover behavior absent anywhere in the Empyralis Gateway code
  reviewed above: `"fails with timeout when no-output watchdog trips"`,
  `"does not retry recoverable failover when no reusable CLI session was
  used"`, `"does not retry a resumed CLI session after the hard overall
  timeout"`, `"rethrows the retry failure when session-expired recovery
  retry also fails"` (test names at
  `cli-runner.reliability.test.ts:309,450,479,1583`). The last of these
  confirms OpenClaw has an explicit **session-expired recovery retry**
  code path — something grepped for and not found anywhere in
  `empyralis-gateway/src/llm/` or `server_modules/sage_agent_runtime_service.py`.
- OpenClaw imports a dedicated error-classification module
  (`embedded-agent-helpers.ts:21-51`) with named predicates including
  `isAuthErrorMessage`, `isAuthPermanentErrorMessage`,
  `isRateLimitAssistantError`, `isTransientHttpError`,
  `isOverloadedErrorMessage`, and `classifyFailoverReason` — a materially
  richer failure taxonomy than Empyralis's four-way
  `not_installed | not_authenticated | timeout | crash` (`cli-runner.ts:28`).
- `docs/PLATFORM-MAP.md:3006-3070` (§26.4-26.5) independently documents that
  this comparison was already made during the Phase 1 latency work: measured
  turn latency was "~4.4s ... still ~4x OpenClaw/'Hermes' (~1s)," attributed
  to OpenClaw never spawning a fresh process per message, and Empyralis's
  Claude path was deliberately kept single-use (not copying OpenClaw's
  session-reuse model) because the wire protocol carries no stable
  per-conversation identity today (`GatewayToolInvokePayload` — confirmed by
  code citation at `PLATFORM-MAP.md:3076-3078`).

---

## 9. Summary of what exists vs. what doesn't (facts only)

**Exists, built, and live:**
- Real headless install (`npm install -g`) with typed failure classification
  and post-install PATH re-verification.
- Real interactive login sessions for 5 auth methods across 2 runtimes, with
  a credential-safety output allowlist and a 5-minute session timeout.
- Real, non-shell `child_process.spawn` of the actual `claude`/`codex`
  binary for every turn, under the customer's own environment/auth.
- A passive, heartbeat-driven (≤60s-stale) install/auth-presence probe that
  gates capability advertisement.
- A durable, queue-based WSS delivery layer for the dispatch request itself,
  with automatic reconnect (unbounded attempts, exponential backoff) and
  systemd-level `Restart=always` supervision of the Gateway process.
- Two flag-gated (off by default), unverified-with-real-traffic warm-process
  paths for latency (Codex daemon: reused across N turns; Claude: single-use
  pool with background refill).
- Every failure mode is ledgered (not just successes), with a distinct
  platform-voice message per failure kind.

**Does not exist, anywhere in the reviewed code:**
- Any retry of a failed/timed-out/crashed CLI spawn.
- Any automatic re-authentication, token refresh, or "session expired"
  recovery — auth expiry is a documented, permanent-until-human-action
  failure state (`sage_agent_runtime_service.py:484-490`).
- Any live (turn-time) readiness probe of the specific Gateway being
  dispatched to — readiness is read from the last heartbeat's cached
  self-report, up to a heartbeat interval + 60s cache TTL stale.
- Any path from a scheduled/autonomous (heartbeat/wake-up) turn to a
  `cli_subscription` agent's actual gateway brain — that entire trigger
  class cannot reach `_dispatch_cli_subscription_gateway_brain` today
  (`docs/PLATFORM-MAP.md:3354-3477`).
- Any no-output (mid-turn silence) watchdog distinct from the overall
  timeout — unlike OpenClaw's separate `noOutputTimer`.
