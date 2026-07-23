# Gap Analysis — Bring-Your-Own Model / Subscription Support: OpenClaw vs Empyralis

> **Terminology note (2026-07-23):** "Sage" is legacy product terminology — the founder ruled the concept removed from the product; the platform has only agents (owner-facing, customer-facing serving the owner, and AskAI). `sage_*` code/variable/string identifiers (e.g. `"sage-main"`, `sage_agent_runtime_service.py`) are legacy code artifacts only, not a live product concept. Anywhere this document's prose says "Sage" or "master," read: the owner-facing agent. This is a lighter-touch terminology note, not a full rewrite — the body below is unchanged and may still use "Sage" throughout.

Read-only fact audit, no code changed. Every claim is file:line-cited against
`/Users/mansur/openclaw` (checked-out source tree, `package.json` version
2026.6.11) and `/Users/mansur/empyralis` on branch `fix/hardware-detail-width`
(2026-07-21). Prior art folded in: `docs/OpenClaw.md` (STEP 9) and
`docs/design/reliability-audit-4-byo-billing-models.md`.

---

## 1. Provider/subscription counts — both sides

### 1.1 Empyralis: 15 backend providers, all explicitly enumerated

`PROVIDER_CATALOG` — `server_modules/provider_profiles.py:460-699` — exactly
**17 dict entries** (verified by direct extraction of every top-level key in
the range): `openai` (:461), `openai-codex` (:475), `anthropic` (:487),
`claude_code_cli` (:509, hidden alias for `anthropic`), `gemini` (:521),
`vertex` (:534), `groq` (:546), `openrouter` (:559), `xai` (:585),
`azure_openai` (:598), `bedrock` (:610), `qwen` (:622), `deepseek` (:635),
`mistral` (:648), `ollama` (:661), `ollama_cloud` (:674),
`custom_openai_compatible` (:687). One of these (`claude_code_cli`) is a
hidden alias for `anthropic`, not a distinct provider — **16 distinct real
providers** in the catalog.

`WORKSPACE_USER_FACING_AI_PROVIDERS` (`provider_profiles.py:441-457`) — the
set actually surfaced in the BYOK-connection UI — is 15 entries: the 16
distinct providers above, minus `ollama` (local, no "connection" concept
needed). (**Correction to the 2026-07-21
`reliability-audit-4-byo-billing-models.md:14`**, whose prose says "15
provider entries" while its own enumerated list in the same sentence names
all 17 catalog keys — the "15" there conflates the two different counts;
this report uses the precise ones above: 17 catalog keys / 16 distinct
providers / 15 workspace-facing.)

Frontend mirror `frontend/lib/workspace/fleet/fleet-provider-constants.ts`
splits the same set into three UX buckets:
- `BYOK_PROVIDERS` (:8-22) — 13 API-key providers: anthropic, openai,
  deepseek, gemini, groq, openrouter, xai, azure_openai, bedrock, qwen,
  mistral, ollama_cloud, custom_openai_compatible.
- `SUBSCRIPTION_PROVIDERS` (:24-27) — 2: `claude_code_cli` ("Claude Code" —
  Claude Pro/Max subscription) and `openai-codex` ("OpenAI Codex" —
  ChatGPT/Codex subscription).
- `LOCAL_PROVIDERS` (:29-31) — 1: `ollama` (local, no credential).

**Total: 15 providers / 2 subscription-passthrough runtimes / 1 local.**
`COMING_SOON_MODES` is an empty set (`fleet-provider-constants.ts:56`) — every
listed mode is live and dispatches for real, none are placeholders.

### 1.2 OpenClaw: at least 45+ provider plugins, formally documented

`ls /Users/mansur/openclaw/extensions/` returns 139 directories total.
Filtering out channel/chat-app plugins (telegram, whatsapp, signal, discord,
slack, imessage, matrix, mattermost, irc, line, feishu, msteams, qqbot, zalo,
etc. — 30+ dirs) and non-model tooling (memory-core, canvas, browser,
diagnostics-*, webhooks, workboard, policy, etc. — another 30+ dirs) leaves
**61 provider/model-shaped extension directories**, e.g.: `alibaba`,
`amazon-bedrock`, `amazon-bedrock-mantle`, `anthropic`, `anthropic-vertex`,
`arcee`, `byteplus`, `cerebras`, `chutes`, `cloudflare-ai-gateway`, `codex`,
`codex-supervisor`, `cohere`, `copilot`, `copilot-proxy`, `deepinfra`,
`deepseek`, `fireworks`, `github-copilot`, `gmi`, `google`, `gradium`, `groq`,
`huggingface`, `kimi-coding`, `litellm`, `llama-cpp`, `lmstudio`, `lobster`,
`microsoft`, `microsoft-foundry`, `minimax`, `mistral`, `moonshot`, `novita`,
`nvidia`, `ollama`, `openai`, `opencode`, `opencode-go`, `openrouter`,
`perplexity`, `qianfan`, `qwen`, `sglang`, `stepfun`, `synthetic`, `together`,
`venice`, `vercel-ai-gateway`, `vllm`, `volcengine`, `voyage`, `vydra`, `xai`,
`xiaomi`, `zai` (a handful of these — `voyage` embeddings, `deepgram`
speech-to-text excluded, `tavily`/`web-readability` search-tool proxies — are
adjacent rather than chat-model providers, but the great majority are text
LLM backends).

`docs/concepts/model-providers.md` formally documents ~30 of these with
setup steps: OpenAI (`:87-108`), Anthropic (`:110-131`), OpenAI ChatGPT/Codex
OAuth (`:133-175`), Z.AI/GLM, MiniMax, Qwen Cloud (`:177-189`), OpenCode
(`:191-203`), Google Gemini API key (`:205-216`), Google Vertex + Gemini CLI
(`:217-259`), Z.AI (`:265-272`), Vercel AI Gateway (`:274-279`), plus a table
of 17 more at `:283-302` (BytePlus, Cohere, GitHub Copilot, Hugging Face,
MiniMax, Mistral, Moonshot, NVIDIA, NovitaAI, Ollama Cloud, OpenRouter, Qwen
OAuth, Together, Venice, Vercel AI Gateway, Volcano Engine, xAI, Xiaomi), plus
Moonshot/Kimi, Kimi coding, Volcano Engine, BytePlus, Synthetic, MiniMax,
LM Studio, Ollama, vLLM, SGLang, and generic OpenAI-compatible local proxies
(`:334-680`).

**Total: OpenClaw ships 45-61 provider-shaped extensions (61 directories
after filtering out channels/tools; ~30 formally documented with setup
flows) vs Empyralis's 15.** OpenClaw's catalog is roughly 3-4x larger by
provider count, dominated by breadth into Chinese/regional providers
(Alibaba, BytePlus, Volcano Engine, Xiaomi, Qianfan, Tencent, StepFun,
Zhipu/Z.AI, Moonshot/Kimi) and aggregator/proxy providers (OpenRouter,
Vercel AI Gateway, Cloudflare AI Gateway, LiteLLM, Synthetic) that Empyralis
has no equivalent of (Empyralis has only OpenRouter as an aggregator, plus
Qwen but no Alibaba-family breadth, no BytePlus/Volcengine/Xiaomi/Tencent at
all).

### 1.3 Subscription-passthrough (CLI, not API key) — comparable in kind, narrower in breadth

Empyralis: exactly 2 runtimes — `claude_code` and `codex`
(`_VALID_CLI_SUBSCRIPTION_RUNTIMES`, referenced at
`server_modules/sage_agent_runtime_service.py:1239`;
`RUNTIME_FOR_PROVIDER` in `fleet-provider-constants.ts:57-64` maps
`claude_code_cli → claude_code`, `openai-codex → codex`).

OpenClaw: same two CLI-backed subscription runtimes are the flagship case
(`claude-cli` runtime for `anthropic/*` refs — `docs/concepts/
model-providers.md:118-121,124`; Codex app-server harness for `openai/*` —
`:29-46,133-152`) — but OpenClaw's "subscription-style" bucket is wider,
explicitly naming Z.AI Coding Plan, MiniMax Coding Plan OAuth, Qwen Cloud
Coding Plan, GitHub Copilot token auth, and SuperGrok/X Premium OAuth for
xAI (`docs/concepts/model-providers.md:177-189,319-321`, table row `:301`)
as additional non-API-key subscription paths beyond the Anthropic/OpenAI
pair Empyralis has.

---

## 2. Operational differences — how BYO actually works

### 2.1 Configuration/authorization scope

**Empyralis**: BYO credentials are scoped per-agent via a per-agent JSON blob
`agent.model_config` (`FleetAgentDetail.tsx:2702`,
`{"mode": "platform_credits"|"byok_api"|"cli_subscription"|"local", ...}`).
API keys are written to a credentials vault (`FleetAgentDetail.tsx:2814-2828`
references `/credentials/vault` then `/providers/profiles`). CLI-subscription
mode never touches the vault at all — the credential lives only on the
user's own paired Gateway box, read from that box's own filesystem
(`~/.claude/`, `~/.codex/` — comment at `empyralis-gateway/src/llm/
cli-runner.ts:8-10`) and is never read or transmitted by the platform
(`sage_agent_runtime_service.py:1231-1233`: "No subscription credential is
ever read or transmitted by the platform — the CLI reads its own auth on the
box it's spawned on.").

**OpenClaw**: credentials are **auth profiles**, not vault rows — stored in
a per-agent SQLite database, `~/.openclaw/agents/<agentId>/agent/
openclaw-agent.sqlite` (`docs/concepts/model-failover.md:107-121`). Config
(`auth.profiles`/`auth.order`) is metadata-and-routing-only, no secrets in
config files. Two credential shapes: `type: "api_key" → {provider, key}` and
`type: "oauth" → {provider, access, refresh, expires, email?}` (`:118-121`).
**OAuth logins create distinct, named profiles so multiple accounts for the
same provider can coexist** (`:123-130`, profile id = `provider:<email>`) —
Empyralis has no equivalent of multiple named credentials per provider per
agent; each agent's `model_config` holds exactly one credential reference.

### 2.2 Auth UX — OAuth vs paste-a-key

**Empyralis**: mix of paste-a-key (`api_key` auth mode, the default for 13 of
15 providers) and a few OAuth/local-CLI modes: `anthropic` accepts
`local_cli` ("Claude Subscription", `secret_required: False`,
`provider_profiles.py:487-508`), `gemini` accepts `gemini_cli_oauth`
(`:521-533`), `openai`/`openai-codex` accept `oauth_token`
(`:461-486`). The CLI-subscription flow itself (`claude_code_cli`/
`openai-codex`) is a **real interactive OAuth/device-code login the platform
brokers over WebSocket**, detailed in §3 below — not a plain "paste your key"
form for those two.

**OpenClaw**: same paste-a-key default for most providers (`<PROVIDER>_API_KEY`
env var pattern, `docs/concepts/model-providers.md:61-81`), but OAuth is
first-class and CLI-driven for a much longer list: OpenAI ChatGPT/Codex
(`openclaw models auth login --provider openai`), Google Gemini CLI
(`openclaw models auth login --provider google-gemini-cli --set-default`,
`:248-253`), MiniMax Global/CN OAuth (`:504-508`), OpenRouter OAuth
(table row `:295`), Qwen OAuth (`:296`), xAI SuperGrok/X Premium OAuth
(`:301,319-321`) — 6+ providers with a scripted OAuth login path vs
Empyralis's 3 (anthropic local_cli, gemini_cli_oauth, openai-codex oauth).

### 2.3 API key rotation — OpenClaw has it, Empyralis does not

**OpenClaw**: explicit, documented multi-key rotation per provider —
`OPENCLAW_LIVE_<PROVIDER>_KEY` (single override, highest priority),
`<PROVIDER>_API_KEYS` (comma/semicolon list), `<PROVIDER>_API_KEY_1/_2/...`
(numbered list) — `docs/concepts/model-providers.md:61-81`. Rotation
triggers specifically on rate-limit-class responses (429, quota, throttling
messages); non-rate-limit failures fail immediately with no rotation
(`:75-78`). This is backed by a full per-agent SQLite `usageStats` table
tracking `lastUsed`/`cooldownUntil`/`errorCount`/`disabledUntil` per
`provider:profile` pair with exponential cooldown backoff (1min → 5min →
25min → 1hr cap) and separate longer billing-disable backoff (5hr → doubles
→ 24hr cap) — `docs/concepts/model-failover.md:197-274`.

**Empyralis**: `grep -n "rotation\|rotate\|multiple.*key\|fallback_key"
server_modules/provider_profiles.py server_modules/provider_catalog_service.py`
returns **zero matches**. Each provider connection is exactly one credential;
there is no concept of multiple keys per provider, no automatic rotation on
rate-limit, and no cooldown/backoff state tracked per credential. This is a
real, confirmed gap — not a difference in emphasis.

### 2.4 Governance/privacy metadata — Empyralis has this, OpenClaw's docs don't foreground it

`PROVIDER_GOVERNANCE_CATALOG` (`server_modules/provider_profiles.py:701-782`)
attaches `privacy_posture`, `jurisdiction`, `residency`,
`enterprise_risk_note`, and `local_self_hosted_compatible` to 11 of the 15
providers (missing: groq, openrouter, azure_openai, bedrock,
custom_openai_compatible) — e.g. DeepSeek's entry explicitly flags PRC data
residency (`:750-757`). **Correction to the prior 2026-07-21 audit**: that
audit stated xAI had no governance-catalog entry; it now does
(`provider_profiles.py:742-748`, confirmed by direct read of the current
working tree) — this gap has since been closed. Nothing in
`docs/concepts/model-providers.md` or the extensions surveyed shows an
equivalent structured jurisdiction/residency table surfaced to OpenClaw
operators — this looks like an area where Empyralis is ahead (see §5).

---

## 3. CLI-subscription lifecycle — install, login, warmth, re-auth, failover

This is the newest part of both codebases and the most directly comparable,
since Empyralis's own code comments confirm it was explicitly modeled on
OpenClaw's pattern (see below).

### 3.1 Install

**Empyralis**: `installCliSubscriptionRuntime()` —
`empyralis-gateway/src/llm/cli-installer.ts:217-288` — runs `npm install -g
@anthropic-ai/claude-code` or `npm install -g @openai/codex`
(`INSTALL_PACKAGE`, `:56-59`) directly on the paired Gateway host (never
inside a sandboxed container, `:5-9`), with an npm preflight check
(`:230-236`), an explicit `--prefix` flag when `NPM_CONFIG_PREFIX` is set to
dodge EACCES on shared installs (`:238-249`), a 180s timeout with SIGTERM→5s→
SIGKILL escalation (`:68-69,151-168`), and a post-install PATH-landing
verification via `commandExists()` so an `npm exit 0` that didn't actually
land the binary on PATH is reported honestly rather than silently
"succeeding" (`:267-279`).

**OpenClaw**: no equivalent single "install" RPC found in the docs surveyed —
OpenClaw's model is "detect what's already installed" via CLI onboarding
(`openclaw onboard`) rather than a platform-triggered remote npm install;
Gemini CLI's doc explicitly tells the operator to `brew install gemini-cli`
or `npm install -g @google/gemini-cli` themselves first
(`docs/concepts/model-providers.md:228-241`). Empyralis's remote-install RPC
is a genuine capability OpenClaw's docs don't show an equivalent of (OpenClaw
assumes CLI installation is a human, local, pre-onboarding step).

### 3.2 Login

**Empyralis**: `CliLoginSessionManager` —
`empyralis-gateway/src/llm/cli-login-session.ts:444-666` — a held-open,
stdin-piped child process per login attempt (`start()`, `:486-552`), 5
distinct auth methods across the two runtimes (`LOGIN_METHODS`,
`:207-210`: Codex `device_auth`/`api_key`/`access_token`; Claude Code
`claudeai`/`console`/`api_key`), a hard allowlist on what output the Gateway
is permitted to relay back to the control plane — only lines that positively
match a URL pattern or a "paste code" prompt pattern are ever forwarded,
everything else (including the post-login success banner) is buffered
locally only and never transmitted (`:17-27,341-416`) — this is the
enforcement point for "the platform never sees the credential." 5-minute
session timeout with SIGTERM→5s→SIGKILL (`:231-232,605-613`). Deliberately
does NOT support `claude setup-token` (Claude's long-lived-token generator)
because that command only ever prints the token to stdout for a human to
copy — spawning it headlessly would produce a "successful" run that leaves
nothing usable behind (`:29-50`).

**OpenClaw**: `openclaw models auth login --provider <id>` CLI command
(`docs/concepts/model-providers.md:94,116,142,213,250,270,279,341,401,441,
479` — appears per-provider throughout). OAuth flows for the same two flagship
runtimes (Anthropic Claude CLI, OpenAI Codex OAuth) plus 6+ more providers
(§2.2). Distinct from Empyralis's, OpenClaw treats "Claude CLI reuse and
`claude -p` usage as sanctioned" per direct communication from Anthropic staff
(`docs/concepts/model-providers.md:123-125`) and **prefers CLI-session reuse
over its own long-lived setup-token path** — the opposite ordering from
Empyralis, which explicitly avoids ever spawning `claude setup-token`
headlessly (see above) and instead surfaces that path as an owner-run,
owner-pasted step in the frontend (`cli-login-session.ts:40-42`).

### 3.3 Session warmth (cold-start latency)

**Empyralis**: `ClaudeCliPrewarmPool` —
`empyralis-gateway/src/llm/claude-cli-prewarm.ts:136-361` — a pool of
pre-spawned, single-use `claude` CLI child processes keyed by
`(model, systemPrompt, reasoningEffort)` (`poolKey`, `:66-68`). Each entry
serves exactly one turn then is torn down and immediately replaced in the
background (`refillSpare`, `:270-290`) — explicitly NOT a multi-turn
reusable session, because Claude's `--input-format stream-json` treats a
second stdin message as a continuation of the same conversation, and
Empyralis's wire protocol has no stable per-conversation key to safely
route a second turn to the right pre-warmed process (`:1-21`). Codex is
excluded from this pool because its app-server (`codex-app-server.ts`)
already supports a true long-lived multi-turn daemon via `thread/start`
(`:4-7`). Feature-flagged off by default (`EMPYRALIS_GATEWAY_CLAUDE_PREWARM=1`,
`:363-368`) — soft cap of 8 concurrent pool processes (`MAX_POOL_ENTRIES`,
`:48`), 10-minute idle reap (`IDLE_REAP_MS`, `:44`).

**OpenClaw**: session stickiness, not process pre-warming — "OpenClaw pins
the chosen auth profile per session to keep provider caches warm. It does
not rotate on every request" (`docs/concepts/model-failover.md:154-166`).
This is a materially different mechanism (server-side prompt-cache/session
affinity at the auth-profile level, not a literal pre-spawned OS process
pool) — not a direct comparison point, but it means OpenClaw gets caching
benefits for every provider uniformly, while Empyralis's prewarm pool is
Claude-Code-only, off-by-default, and doesn't exist for the Codex or any
API-key path.

### 3.4 Re-authentication

**Empyralis**: `attemptSessionExpiredRecovery` (referenced in
`cli-runner.ts:173` doc comment, `:58`) — when a CLI reports
`auth_expired`, the runner attempts **exactly one bounded, silent re-spawn**
on the theory that the CLI may have silently refreshed its own token between
attempts, then surfaces a clear auth failure if that also fails
(`cli-runner.ts:56-58,683-691`). This is explicitly a re-spawn, not a
re-login — if the credential is genuinely dead, a human must re-run the
login flow (§3.2) manually; there is no automatic re-authentication.
Readiness state (`installed`/`authenticated` per runtime) is read fresh per
turn from `gateway_registry_service.gateway_registration_public_payload()`'s
`llm_runtimes` dict (`sage_agent_runtime_service.py:1057-1066`,
`gateway_registry_service.py:225`, built by `_llm_runtime_summary()` at
`gateway_registry_service.py:156-191`), fed by passive gateway-side probes
`probeCodexCli`/`probeClaudeCli`
(`empyralis-gateway/src/health/service-inventory.ts:477-525,549-639`,
`status: "ready"|"degraded"|"missing"`, cached
`PASSIVE_INVENTORY_CACHE_TTL_MS = 60_000` at `:86`). A turn that hits
`not_installed`/`not_authenticated` force-invalidates that cache so the next
heartbeat reflects it instead of serving stale data for up to a minute
(`runtime.ts:418-440`). A successful login also force-invalidates it
immediately (`cli-setup-runtime.ts:82-90`) so the UI's "authenticated" flag
flips on the next heartbeat rather than waiting out the TTL. **This
CLI-installed/authenticated health signal exists ONLY for the two
`cli_subscription` runtimes** — no equivalent "is this key still valid" live
probe exists for any of the 13 BYOK API-key providers (confirmed no matching
probe in `service-inventory.ts` for anthropic/openai/gemini/etc. via direct
API key).

**OpenClaw**: no automatic re-authentication either — expired/invalid OAuth
tokens land in the same auth-failure cooldown/rotation machinery as any
other failure (§2.3); a `refresh` field exists on OAuth credentials
(`docs/concepts/model-failover.md:121`, `{provider, access, refresh,
expires, email?}`) implying OpenClaw performs standard OAuth token refresh
using the stored refresh token — a materially stronger position than
Empyralis's "hope the CLI refreshed itself, else fail" approach, since
OpenClaw's plugin layer owns "OAuth refresh" as an explicit documented hook
(`docs/concepts/model-providers.md:53`: "Plugins own onboarding, model
catalogs, auth env-var mapping, transport/config normalization, tool-schema
cleanup, failover classification, **OAuth refresh**, usage reporting").

### 3.5 Failover / retry / watchdog

**Empyralis** (`empyralis-gateway/src/llm/cli-runner.ts`, built in commits
`d9f11090e` and `b3ddbc782` this session):
- Failure taxonomy: `auth_expired | rate_limited | overloaded | transient |
  fatal` (`:70`), classified from exit code + stdout/stderr text via regex
  pattern lists — `RATE_LIMIT_PATTERNS` (`:443-452`), `OVERLOADED_PATTERNS`
  (`:453-458`), `TRANSIENT_PATTERNS` (`:459-472`) — through
  `classifyFailureText()`/`evaluateOutcome` (`:485-496,631-668`);
  unrecognized failures default to `fatal`, never assumed retryable.
- Bounded retry with backoff for `transient`/`overloaded`/`rate_limited`
  only; `fatal` and hard-timeout never retry (`:673-680,757-759`). Backoff
  schedule: `[1000, 4000]`ms for `rate_limited`, `[500, 2000]`ms for
  everything else retryable (`backoffMsFor`, `:673-680`).
  `DEFAULT_MAX_RETRIES = 2` (`cli-runner.ts:183`, i.e. 3 attempts total).
- A **no-output watchdog** distinct from the overall turn timeout —
  `DEFAULT_NO_OUTPUT_TIMEOUT_MS = 45_000` (`:182`), only armed when strictly
  less than the overall per-attempt timeout, and reset on every stdout/
  stderr chunk (`resetNoOutputTimer`, `:383-391`). A process producing
  literally zero output for 45s is killed and classified `transient`
  (retryable), separate from a process that was alive-but-slow past the
  full timeout budget, which is classified `fatal` (never retried) —
  matching, per its own code comment, "OpenClaw's own stance (its
  reliability suite has a test explicitly titled 'does not retry a resumed
  CLI session after the hard overall timeout')" (`:653-666`).
- Session-expired recovery uses a fixed `AUTH_RECOVERY_DELAY_MS = 750`
  (`:187`) before the single silent re-spawn described in §3.4.
- **Explicitly modeled on OpenClaw**: the file header states this pattern
  "mirrors OpenClaw's ClaudeLiveSession / embedded-agent-helpers
  (`/Users/mansur/openclaw/src/agents/cli-runner/claude-live-session.ts`,
  `embedded-agent-helpers/errors.ts`, `failover-matches.ts` — read-only
  reference, never imported)" (`cli-runner.ts:16-31`).
- Every dispatch failure is ledgered, not just successes
  (`_ledger_cli_subscription_failure`,
  `sage_agent_runtime_service.py:1069-1110`) — "G5: Ledger every failure."
- **Hard rule: no cross-runtime/cross-mode fallback.** If `claude_code`
  fails, the turn is denied — it never silently falls back to `codex`, to
  platform credits, or to a different provider
  (`sage_agent_runtime_service.py:1226-1233`: "It NEVER falls back to
  platform credits or a different runtime.").
- Adversarial-verification follow-up (`b3ddbc782`) fixed 4 concrete leaks in
  this wave: the prewarm pool leaking a hung child on timeout instead of
  killing it (`claude-cli-prewarm.ts`, now fixed per commit), plus 3
  unrelated channel/supervisor leaks (Signal SSE reconnect giving up after
  12 attempts, WhatsApp health-check socket leak, wedged
  launchctl/systemctl repair hang) bundled into the same reliability wave.

**OpenClaw** (`docs/concepts/model-failover.md`, full doc read):
- Two-stage failover: (1) auth-profile rotation within a provider, (2) model
  fallback to the next model in `agents.defaults.model.fallbacks`
  (`:11-16`).
- Much richer failure-reason taxonomy surfaced to operators:
  `rate_limit`, `overloaded`, `billing`, `auth`, `model_not_found`,
  `empty_response`, `no_error_details`, `unclassified` (`:278,377-386`).
- Exponential cooldown backoff **identical shape** to Empyralis's but with
  4 explicit steps and a documented cap: 1min → 5min → 25min → 1hr
  (`:225-230`) vs Empyralis's 2-step `[500,2000]`/`[1000,4000]`ms schedule —
  OpenClaw's is tuned for provider-side rate limits measured in minutes,
  Empyralis's for a locally-spawned CLI process measured in seconds; not
  directly comparable in magnitude but OpenClaw's is far more elaborated
  (billing-specific 5hr→24hr backoff, `:269-274`; per-model cooldown
  scoping so a sibling model on the same provider isn't blocked,
  `:215-223`).
- **Model fallback OpenClaw has that Empyralis explicitly refuses**:
  `agents.defaults.model.fallbacks` lets an operator configure a chain of
  alternate models/providers to fall through to (`:276-298`) — this is the
  exact behavior Empyralis's `cli_subscription` path deliberately does NOT
  implement (§ hard rule above). This is a genuine, deliberate product
  difference, not an oversight on Empyralis's side — the code comment frames
  it as a safety/billing-honesty choice (never silently switch a user from
  their own subscription onto platform-billed credits) — but it does mean
  OpenClaw's CLI-runtime turns are more resilient to any single provider
  outage.
- User-visible fallback notices in-channel (`"↪️ Model Fallback: <fallback>
  (selected <primary>; <reason>)"`, `:91-105`) — Empyralis has no equivalent
  chat-visible notice for the CLI-subscription path (only the ledger event
  and a denial error message).
- `runWithModelFallback(...)` records structured per-attempt
  `model_fallback_decision` logs with `fallbackStepFromModel`/
  `fallbackStepToModel`/`fallbackStepFinalOutcome` fields (`:375-386`) —
  richer structured observability than Empyralis's activity-ledger rows.

### 3.6 Frontend model picker (commit `0eeb30a91`)

Empyralis added a Model picker to the agent's right-side Properties panel
(`frontend/lib/workspace/fleet/FleetAgentDetail.tsx`, +555 lines per
`git show --stat 0eeb30a91`) reusing the existing Model-tab save path so
Grok/xAI and every other catalog provider became selectable from that
surface too, and fixed a "Cost today = all-time" display bug on the same
panel (commit message, `0eeb30a91`). This is a UI convenience improvement,
not a new provider or protocol — noted for completeness since the task
named it explicitly.

---

## 4. Model routing/selection — how each picks provider+model per turn

**Empyralis**: purely mode-based, no automatic routing. `model_config.mode`
(`platform_credits | byok_api | cli_subscription | local`) is set once per
agent and dictates exactly one dispatch path
(`sage_agent_runtime_service.py:635-665`); there is no per-turn
provider-selection heuristic, no cost-based routing, and (per §3.5) no
cross-provider fallback for `cli_subscription`. For `platform_credits`, only
3 DeepSeek models are ever billable
(`PLATFORM_CREDIT_MODEL_ALLOWLIST`, `provider_catalog_service.py:28-34`):
`deepseek-chat`, `deepseek-v4-pro`, `deepseek-reasoner`. New specialist
agents default to `deepseek-reasoner`
(`server_modules/fleet_tools.py:194-198`); Sage's own fallback (when no
workspace provider is set) also resolves to DeepSeek
(`sage_agent_runtime_service.py:401-429`).

**OpenClaw**: `resolveDefaultModelForAgent()` (per `docs/OpenClaw.md`
STEP 9, `model-selection-DF4MbMUd.js:91` in the compiled dist) resolves an
agent-level override, falls back to global config default, and resolves
against a dynamically-built catalog scanning all configured/enabled provider
plugins. Session-level `/model` overrides propagate across every channel
because they write to the shared session-store entry
(`docs/OpenClaw.md` STEP 9, `:474`). On top of static selection, the full
auto-fallback machinery in §3.5 runs per-turn: a 5-minute periodic re-probe
of a failing primary (`model-failover.md:63`), automatic model-fallback-chain
walking on failover-worthy errors, and automatic reversion to the primary
once it recovers, with in-channel notices at each transition
(`:91-105`). This is a fundamentally more dynamic, self-healing router than
Empyralis's static per-agent mode assignment.

---

## 5. Prioritized gaps — what OpenClaw does that Empyralis lacks

Ordered by leverage (highest-impact / most user-visible first), each with
file:line both sides and a rough build-size estimate.

### 5.1 API key rotation on rate-limit (HIGH leverage, MEDIUM build)
- **OpenClaw has it**: `<PROVIDER>_API_KEYS` / `<PROVIDER>_API_KEY_N` /
  `OPENCLAW_LIVE_<PROVIDER>_KEY` env-var priority chain, auto-rotates on
  429/quota/throttle responses only (`docs/concepts/
  model-providers.md:61-81`).
- **Empyralis has nothing**: confirmed zero matches for
  `rotation|rotate|multiple.*key|fallback_key` across
  `server_modules/provider_profiles.py` and
  `provider_catalog_service.py`. Each BYOK connection is exactly one key;
  a rate-limited workspace has no automatic recourse today.
- **Build size**: MEDIUM. Needs (a) a schema change to allow N credentials
  per (workspace, provider) in the vault instead of 1, (b) a rotation/cooldown
  state table (OpenClaw's is per-agent SQLite `usageStats`; Empyralis would
  need an equivalent, e.g. a new small table or a JSON column on the
  existing connection row), (c) wiring the actual HTTP-call sites (wherever
  `provider_catalog_service.py`'s BYOK dispatch calls out to a provider) to
  catch rate-limit-class errors and retry with the next key. No CLI-runner
  changes needed — this is orthogonal to the `cli_subscription` self-heal
  work already shipped.

### 5.2 OAuth token refresh for CLI-subscription re-auth (MEDIUM leverage, MEDIUM build)
- **OpenClaw has it**: OAuth credentials carry a `refresh` token field
  (`docs/concepts/model-failover.md:121`) and refresh is an explicit
  plugin-owned hook (`model-providers.md:53`).
- **Empyralis has a weaker substitute**: `attemptSessionExpiredRecovery`
  only re-spawns the CLI process once and hopes it silently refreshed itself
  (`cli-runner.ts:56-58`); there is no platform-driven token refresh call at
  all — deliberately, since the platform is designed to never read or hold
  the subscription credential (`sage_agent_runtime_service.py:1231-1233`).
- **Build size**: MEDIUM, but constrained by a real architectural choice:
  Empyralis's whole `cli_subscription` design rests on the platform never
  touching the credential (a stated security/trust boundary, not an
  oversight — see `cli-runner.ts` header comments). Any real refresh
  mechanism would have to run on the Gateway box itself (where `claude`/
  `codex` already live) rather than being brokered by the platform — e.g. a
  Gateway-local scheduled `claude auth login --claudeai` re-check, or
  relying on the CLI's own background refresh (Claude Code and Codex CLIs
  both refresh their own tokens automatically in normal interactive use;
  the open question is whether that self-refresh reliably fires in a
  headless, non-interactively-spawned context, which the `d9f11090e` commit
  message flags as unverified: "Needs a live subscription to tune stderr
  patterns + confirm real token refresh. Not deployed.").

### 5.3 Configured model-fallback chains (LOW-MEDIUM leverage, LARGE build — and arguably a deliberate non-goal)
- **OpenClaw has it**: `agents.defaults.model.fallbacks`, full candidate-chain
  walking, sticky auto-override with periodic primary re-probe, in-channel
  fallback notices (`docs/concepts/model-failover.md:276-298,91-105`).
- **Empyralis explicitly refuses it for `cli_subscription`**: "no fallback —
  turn denied" is a hard rule with its own ledger event
  (`sage_agent_runtime_service.py:1226-1233`,
  `_ledger_cli_subscription_failure` summary text at `:1096-1097`: "No
  fallback — turn denied.").
- **Build size**: LARGE if pursued platform-wide (new config surface,
  candidate-chain resolution, session-override persistence, notice
  delivery) — and this gap is **not** obviously worth closing for
  `cli_subscription` specifically, since the whole point of that mode is
  "run on the user's own subscription, never silently cross-bill platform
  credits." A narrower, lower-risk version — model fallback *within* a
  single BYOK provider connection (e.g. deepseek-chat → deepseek-reasoner on
  failure) — would be a MEDIUM build and wouldn't cross the
  subscription/credits trust boundary.

### 5.4 Multiple named credentials per provider per agent (LOW leverage, MEDIUM build)
- **OpenClaw has it**: OAuth profile IDs are `provider:<email>`, so a
  workspace can hold several accounts for the same provider simultaneously
  and pick one via `/model …@<profileId>`
  (`docs/concepts/model-failover.md:123-130,162`).
- **Empyralis**: one credential per (agent, provider) via `model_config`
  (`FleetAgentDetail.tsx:2702,2814-2828`) — no multi-account concept.
- **Build size**: MEDIUM — mostly a vault/credential-store data-model change
  plus a picker UI; lower priority than §5.1/§5.2 since it's a power-user
  feature, not a reliability gap.

### 5.5 Provider/regional breadth (LOW leverage per-provider, but large aggregate)
- OpenClaw supports ~30-60 providers with real docs vs Empyralis's 15,
  concentrated in gaps around Chinese/regional providers (BytePlus, Volcano
  Engine, Xiaomi, Tencent, Qianfan, StepFun, Zhipu/Z.AI, Moonshot/Kimi — none
  present in Empyralis's `PROVIDER_CATALOG`) and proxy/aggregator providers
  (Vercel AI Gateway, Cloudflare AI Gateway, LiteLLM, Synthetic — Empyralis
  has only OpenRouter). Each individual provider is a SMALL build (Empyralis's
  own `custom_openai_compatible` entry, `provider_profiles.py:687-698`,
  already proves any OpenAI-compatible endpoint can be onboarded with just a
  base URL + key — no new code path needed for API-key-shaped providers, only
  a new catalog entry). The aggregate gap is large only because of the
  provider count, not because any one addition is hard.

### 5.6 Frontend/backend catalog drift — `vertex` has no frontend picker entry (LOW leverage, SMALL build, quick fix)
- **Backend**: `vertex` (Google Vertex AI) is a full `PROVIDER_CATALOG` entry
  (`provider_profiles.py:534-545`) and is in
  `WORKSPACE_USER_FACING_AI_PROVIDERS` (`:441-457`).
- **Frontend**: `grep -n "vertex" frontend/lib/workspace/fleet/
  fleet-provider-constants.ts` returns **zero matches** in the file's 225
  lines — `vertex` is absent from `BYOK_PROVIDERS`, `MODELS_BY_PROVIDER`, and
  `DEFAULT_MODEL_BY_PROVIDER` alike. A workspace cannot select Vertex AI
  through the agent Model tab or create-agent wizard today even though the
  backend fully supports it.
- **Build size**: SMALL — this isn't an OpenClaw-comparison gap so much as
  an internal catalog-sync bug (the file's own header comment claims it
  mirrors the backend catalog by hand, `fleet-provider-constants.ts:1-2,76`).
  Fix is adding one entry to each of the three constant maps.

### 5.7 Remote CLI install RPC — Empyralis is ahead here, flagged for completeness
Not a gap — Empyralis's `installCliSubscriptionRuntime()`
(`cli-installer.ts:217-288`) lets the platform trigger `npm install -g` on
the user's paired Gateway remotely with real preflight/verification; nothing
in OpenClaw's docs shows an equivalent platform-triggered remote install —
OpenClaw assumes the operator installs CLIs themselves before `openclaw
onboard`. Listed here so the report isn't one-directional.

---

## 6. Where Empyralis is ahead

1. **Provider governance/privacy metadata** (§2.4) —
   `PROVIDER_GOVERNANCE_CATALOG` (`provider_profiles.py:701-782`) gives every
   major provider a structured `jurisdiction`/`residency`/
   `enterprise_risk_note`/`local_self_hosted_compatible` projection,
   including an explicit PRC-data-residency flag for DeepSeek
   (`:750-757`). No equivalent structured table was found in OpenClaw's
   docs or extensions.
2. **Remote CLI install RPC** (§5.6) — platform-triggered `npm install -g`
   on the user's own Gateway with PATH-landing verification
   (`cli-installer.ts:217-288`); OpenClaw's CLI-onboarding model assumes
   local, human-run installation.
3. **Fail-loud, ledgered-every-failure discipline** for `cli_subscription` —
   every dispatch failure (not just successes) writes an activity-ledger row
   with a machine-readable reason
   (`_ledger_cli_subscription_failure`,
   `sage_agent_runtime_service.py:1069-1110`), and the "no silent fallback to
   platform credits" rule is a deliberate trust-boundary choice OpenClaw
   doesn't need to make (it has no equivalent "your own subscription, never
   cross-billed" product concept — OpenClaw is a self-hosted tool the
   operator runs for themselves, not a multi-tenant platform brokering
   access to a user's own subscription on their behalf).
4. **Secret-forwarding allowlist during login** — `cli-login-session.ts`'s
   `extractSafeLines()` (`:341-416`) is a positive-match allowlist (only URL
   and code-prompt shaped lines are ever relayed) rather than a raw stdout
   passthrough — a narrower, more deliberately audited security boundary
   than what OpenClaw's docs describe for its own CLI login flows (which
   don't discuss output-filtering at this level of detail).

---

## Summary table

| Dimension | OpenClaw | Empyralis |
|---|---|---|
| Backend providers (formal catalog entries) | ~30 documented, 61 extension dirs | 15 (`provider_profiles.py:460-699`) |
| Subscription-passthrough (CLI, non-API-key) runtimes | 2 flagship (Claude CLI, Codex OAuth) + Z.AI/MiniMax/Qwen/Copilot/xAI coding-plan variants | 2 (`claude_code`, `codex`) |
| Local/self-hosted | Ollama, LM Studio, vLLM, SGLang, llama-cpp | Ollama only |
| Credential storage | Per-agent SQLite auth-profile store, multi-profile per provider | Vault (API keys) / nothing stored for CLI-subscription (lives on user's box) |
| API key rotation | Yes — env-var priority chain, rate-limit-triggered | No — confirmed zero code |
| OAuth token refresh | Explicit plugin hook | No — one bounded re-spawn only, no real refresh |
| Model fallback chains | Yes, full candidate-chain + sticky override + notices | No — explicit "no fallback, turn denied" rule for cli_subscription |
| CLI install automation | Operator installs locally before onboarding | Platform-triggered remote `npm install -g` with verification |
| Per-provider governance/privacy metadata | Not found | Yes — jurisdiction/residency/risk-note per provider |
| Self-heal (retry/watchdog/crash recovery) | Mature, general reference model | Newly built (`d9f11090e`, `b3ddbc782`), explicitly modeled on OpenClaw's pattern, not yet deployed/live-tested |
