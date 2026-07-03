# cli_subscription — Gateway Brain Implementation Spec

**Status:** Spec — not built.
**Date:** 2026-07-03
**Principle:** Subscription-powered turns are ONLY legitimate when the
CLI runs on the USER's own hardware with the USER's own login. Platform
never stores, proxies, or pools subscription credentials. No fallback.

---

## Architecture

```
User's Machine (Gateway)                  Empyralis Cloud (Control Plane)
────────────────────────                  ───────────────────────────────

1. Gateway starts → detects installed
   CLIs → advertises llm_runtime in
   capability heartbeat:
   {
     runtime: "claude_code",
     version: "2.1.154",
     authenticated: true,
     plan: "pro"           // parsed from auth.json
   }

2. Agent turn arrives with
   model_config: {
     mode: "cli_subscription",
     runtime: "claude_code",  // or "codex"
     gateway_binding: "gateway_abc123"
   }

3. Control plane resolves
   provider → sees cli_subscription
   → looks up Gateway by binding
   → sends WSS message:
   {
     type: "llm_generate",
     prompt: "<system prompt>",
     user_message: "<turn text>",
     max_tokens: 4096,
     run_id: "run-xyz"
   }

4. Gateway spawns CLI:
   $ claude -p "<prompt>"
     --output-format stream-json
     --max-turns 1
     --allowedTools "[]"

   or:

   $ codex exec "<prompt>"
     --json
     --skip-git-repo-check

5. Gateway streams response back
   via WSS (chunked JSONL)

6. Control plane assembles reply
   → delivers to channel

7. Ledger:
   event_class: "turn"
   execution_tier: "gateway_brain"
   runtime: "claude_code"
   gateway_id: "gateway_abc123"
```

---

## What to Build (in order)

### Phase G1 — Gateway `llm_runtime` capability detection

**File:** `empyralis-gateway/src/cloud/heartbeat-payload.ts`

Add to capability inventory:

```typescript
interface LLMRuntimeCapability {
  runtime: "claude_code" | "codex";
  version: string;
  authenticated: boolean;
  path: string;  // absolute path to binary
}

// Detection logic:
// 1. which claude → parse version
// 2. Check ~/.claude/credentials.json or env ANTHROPIC_API_KEY
// 3. which codex → parse version
// 4. Check ~/.codex/auth.json or env CODEX_API_KEY
// 5. If authenticated → llm_runtime: "ready"
//    If installed but not authenticated → llm_runtime: "unauthenticated"
//    If not installed → capability absent
```

**Effort:** ~100 lines TypeScript. Read-only, no credentials transmitted.
**Test:** `gateway --capabilities` shows `llm_runtime` when CLI installed.

---

### Phase G2 — WSS message types

**File:** `empyralis-gateway/src/cloud/messages.ts` (or equivalent)

Add two message types:

```typescript
// Control plane → Gateway
interface LLMGenerateRequest {
  type: "llm_generate";
  run_id: string;
  runtime: "claude_code" | "codex";
  prompt: string;           // full system + user prompt
  max_tokens?: number;
  timeout_ms?: number;      // default 120000
}

// Gateway → Control plane
interface LLMGenerateChunk {
  type: "llm_generate_chunk";
  run_id: string;
  chunk: string;            // text delta
  done: boolean;
}

interface LLMGenerateResult {
  type: "llm_generate_result";
  run_id: string;
  ok: boolean;
  text?: string;            // full response
  error?: string;
  usage?: { input_tokens: number; output_tokens: number };
}
```

**File:** `server_modules/routes_gateway.py`

Add WSS handler for `llm_generate_result` → routes back to waiting turn.

---

### Phase G3 — Gateway CLI spawner

**File:** `empyralis-gateway/src/runtime/cli-runner.ts` (new)

```typescript
// Spawn CLI headlessly, stream stdout, kill on timeout.
async function runCLI(params: {
  runtime: "claude_code" | "codex";
  prompt: string;
  maxTokens?: number;
  timeoutMs?: number;
}): Promise<{ text: string; usage?: Usage }> {
  // Claude Code:
  //   claude -p "<prompt>" --output-format stream-json --max-turns 1
  // Codex:
  //   codex exec "<prompt>" --json --skip-git-repo-check

  // 1. Build args array
  // 2. child_process.spawn(binary, args)
  // 3. Collect stdout lines, parse JSON/JSONL
  // 4. Timeout → SIGTERM → SIGKILL
  // 5. Return assembled text + usage
}
```

**Key rules:**
- No shell injection — use `spawn` with arg array, never `exec` with string
- Timeout hard kill (default 120s, configurable)
- Environment: inherit user's PATH, HOME, auth files
- NEVER read or transmit auth files — spawn only, the CLI reads its own auth

**Effort:** ~150 lines TypeScript.

---

### Phase G4 — Control plane dispatch

**File:** `server_modules/sage_agent_runtime_service.py`

Replace the current `cli_subscription` RuntimeError with actual dispatch:

```python
if mode == "cli_subscription":
    gateway_id = agent_config.get("gateway_binding")
    if not gateway_id:
        raise RuntimeError(
            "cli_subscription mode requires a Gateway binding. "
            "Bind a Gateway to this agent via fleet_configure_agent."
        )

    # Check Gateway is online + has llm_runtime capability
    gateway = await get_gateway_status(gateway_id)
    if not gateway or not gateway.get("online"):
        raise RuntimeError(
            "Gateway is offline. cli_subscription requires the "
            "Gateway to be running on your machine."
        )
    runtime = agent_config.get("runtime", "claude_code")
    if runtime not in gateway.get("capabilities", {}).get("llm_runtime", []):
        raise RuntimeError(
            f"{runtime} is not installed or authenticated on your "
            "Gateway. Install the CLI and log in, then retry."
        )

    # Send to Gateway via WSS, await response
    result = await gateway_llm_generate(
        gateway_id=gateway_id,
        runtime=runtime,
        prompt=system_prompt,
        user_message=message,
        run_id=run_id,
        timeout=120,
    )
    if not result["ok"]:
        raise RuntimeError(f"Gateway generation failed: {result['error']}")

    # Ledger the turn as gateway_brain
    await ledger_turn(
        execution_tier="gateway_brain",
        runtime=runtime,
        gateway_id=gateway_id,
        usage=result.get("usage"),
    )
    return result["text"]
```

**Effort:** ~200 lines Python. New functions for gateway WSS dispatch, status check.

---

### Phase G5 — Error paths

Every failure mode has a clear, platform-voiced response:

| Condition | User sees |
|-----------|-----------|
| No Gateway bound | "Heads up: cli_subscription requires a Gateway. Bind one in agent settings." |
| Gateway offline | "Heads up: your Gateway is offline. Start it on your machine and retry." |
| CLI not installed | "Heads up: Claude Code is not installed on your Gateway. Run `npm install -g @anthropic-ai/claude-code`." |
| CLI not authenticated | "Heads up: Claude Code on your Gateway is not logged in. Run `claude login` on that machine." |
| CLI timeout | "Heads up: generation timed out. The CLI may be rate-limited or busy. Retry." |
| CLI crash | "Heads up: the CLI exited unexpectedly. Check Gateway logs." |

All errors are ledgered with `event_class: "gateway_hardware"`, `action: "llm_generate_failed"`.

---

### Phase G6 — fleet_configure_agent fields

Add to `_ALLOWED_CONFIGURE_KEYS`:

```python
"gateway_binding",  # Gateway ID for cli_subscription
"runtime",           # "claude_code" | "codex" — which CLI to use
```

Already exists: `cli_subscription` in `_VALID_MODEL_MODES`.

---

## Test Plan

| Test | What it proves |
|------|---------------|
| Gateway advertises `llm_runtime` when CLI installed | Detection works, no creds transmitted |
| Gateway advertises `unauthenticated` when CLI installed but not logged in | Auth detection separate from binary detection |
| Gateway absent from capability when CLI not installed | Clean absence, no error spam |
| Turn with `cli_subscription` + online Gateway → real completion | End-to-end works |
| Turn with `cli_subscription` + offline Gateway → platform-voice error | No silent hang |
| Turn with `cli_subscription` + no Gateway bound → platform-voice error | Clear setup instruction |
| CLI timeout → SIGKILL → error returned | Timeout enforcement |
| Credential files never leave Gateway | grep for `credentials.json`, `auth.json` in WSS payloads |
| Ledger row has `execution_tier: "gateway_brain"` | Audit trail shows where brain ran |

---

## Latency Estimate

| Step | Time |
|------|------|
| WSS round-trip (control → Gateway) | ~25-50ms |
| CLI cold start (node/python process) | ~200-500ms |
| LLM generation (first token) | ~500-2000ms |
| LLM generation (full response) | 2-30s (same as any API call) |
| WSS round-trip (Gateway → control) | ~25-50ms |
| **Total added vs direct API call** | **~300ms** |

The Gateway relay adds ~300ms. LLM generation dominates (2-30s). The overhead is **1-15% of total turn time** — imperceptible to users.

---

## Why This Is the Right Architecture

1. **ToS compliant**: Binary runs on user's hardware, user's login, user's subscription. Neither provider's terms are violated.
2. **Industry standard**: GitHub Actions runs `claude -p` in CI. Codex `exec` is designed for headless. This is the pattern both providers ship themselves.
3. **No credential risk**: The platform never touches OAuth tokens. If the platform is compromised, zero subscription credentials are exposed.
4. **Gateway additive**: Every change is in the Gateway or a new dispatch path. Zero changes to existing turn pipeline, triage, or channel delivery.
5. **Honest failure**: When Gateway is offline, the user gets a clear message — not a silent fallback to platform credits they didn't ask for.

---

## Build Order (recommended)

```
G1 (capability detection) → 1 day
    ↓
G2 (WSS messages) → 1 day
    ↓
G3 (CLI spawner) → 1 day
    ↓
G4 (dispatch + resolve) → 2 days
    ↓
G5 (error paths) → 1 day
    ↓
G6 (configure fields) → 0.5 day
    ↓
Tests + acceptance → 1 day

Total: ~7-8 days of focused work
```
