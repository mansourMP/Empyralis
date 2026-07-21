# Reliability Audit 4 — BYO Subscriptions/Keys, Billing, and Model Catalogue

Read-only fact audit. Every claim is file:line-cited against the working tree at
`/Users/mansur/empyralis` on branch `fix/slack-channel-uniqueness` (2026-07-21).
No fixes or recommendations — current reality only.

---

## 1. BYO providers supported today

### 1.1 Backend provider catalog

The single backend source of truth is `PROVIDER_CATALOG` in
`server_modules/provider_profiles.py:460-699`. It lists 15 provider entries:
`openai`, `openai-codex`, `anthropic`, `claude_code_cli` (hidden alias for
`anthropic`, `server_modules/provider_profiles.py:509-520`), `gemini`,
`vertex`, `groq`, `openrouter`, `xai`, `azure_openai`, `bedrock`, `qwen`,
`deepseek`, `mistral`, `ollama`, `ollama_cloud`, `custom_openai_compatible`.

`WORKSPACE_USER_FACING_AI_PROVIDERS` (`server_modules/provider_profiles.py:441-457`)
— the set surfaced to workspace users for BYO credential connection — contains:
`openai, openai-codex, anthropic, gemini, vertex, groq, openrouter, xai,
azure_openai, bedrock, qwen, deepseek, mistral, custom_openai_compatible,
ollama_cloud`.

**xAI/Grok IS present** as a first-class BYO provider:
- `server_modules/provider_profiles.py:585-597` — provider entry `"xai"`:
  `label: "xAI"`, `auth: ["api_key"]`, `default_model: "grok-4"`,
  `base_url: "https://api.x.ai/v1"`, `models: ["grok-4", "grok-4-0709",
  "grok-4-latest", "grok-3"]`, note: "Direct xAI API key using xAI's
  OpenAI-compatible endpoint."
- Full per-model metadata for xAI is in `PROVIDER_MODEL_CATALOG["xai"]`
  (`server_modules/provider_profiles.py:1246-1279`): `grok-4`, `grok-4-0709`,
  `grok-4-latest`, `grok-3`, each with `capability_labels` including
  `"Reasoning"`, `"Tools"`, `"Structured outputs"`.
- `xai` is also listed inside `MANUAL_MODEL_ID_PROVIDERS` and
  `LEDGER`-adjacent sets in `server_modules/provider_catalog_service.py:14-20`
  (free-text/manual model-id entry allowed, same as `azure_openai`,
  `custom_openai_compatible`, `groq`, `openrouter`).
- Grok also appears indirectly via OpenRouter's catalog: `"x-ai/grok-4"`
  (`server_modules/provider_profiles.py:578`, `1224-1230`).

### 1.2 Frontend provider catalog (agent Model tab / create-agent wizard)

`frontend/lib/workspace/fleet/fleet-provider-constants.ts` is documented as a
mirror of the backend catalog (line 1: "matches backend
provider_profiles.py PROVIDER_CATALOG").

- `BYOK_PROVIDERS` (`frontend/lib/workspace/fleet/fleet-provider-constants.ts:8-22`)
  includes `{ id: "xai", label: "xAI (Grok)" }` at line 15, alongside
  anthropic, openai, deepseek, gemini, groq, openrouter, azure_openai,
  bedrock, qwen, mistral, ollama_cloud, custom_openai_compatible.
- `MODELS_BY_PROVIDER.xai` (`frontend/lib/workspace/fleet/fleet-provider-constants.ts:100`):
  `["grok-4", "grok-4-0709", "grok-4-latest", "grok-3"]`.
- `DEFAULT_MODEL_BY_PROVIDER.xai` (`frontend/lib/workspace/fleet/fleet-provider-constants.ts:115`):
  `"grok-4"`.

**Conclusion: Grok/xAI is fully wired as a supported BYO provider on both
backend and frontend today** — not a gap.

### 1.3 Other BYO modes (non-API-key)

Beyond direct API keys, the frontend distinguishes provider "modes"
(`frontend/lib/workspace/fleet/fleet-provider-constants.ts:4`,
`ProviderMode = "platform_credits" | "byok_api" | "cli_subscription" | "local"`):
- `SUBSCRIPTION_PROVIDERS` (`fleet-provider-constants.ts:24-27`):
  `claude_code_cli` ("Runs on your own hardware, using your own Claude Pro
  subscription") and `openai-codex` ("...using your own ChatGPT/Codex
  subscription") — these are the "bring your own subscription" (not API key)
  path, executed on the user's own paired Gateway box.
- `LOCAL_PROVIDERS` (`fleet-provider-constants.ts:29-31`): `ollama` (local,
  no credential).
- `COMING_SOON_MODES` is an **empty set**
  (`fleet-provider-constants.ts:56`) — the code comment states
  `cli_subscription` and `local` both already "shipped" and are fully
  savable/dispatchable, not placeholders.

---

## 2. BYO billing/metering behavior — the critical fact

**Current fact: usage paid for by a user's own BYO key or subscription is
recorded/logged for observability but is NOT charged against Empyralis
platform credits. `credits_debited` and `platform_cost_usd` are hard-coded
to `0` for every non-`platform_credits` payer, everywhere this is wired.**

### 2.1 The payer taxonomy

`LEDGER_PAYERS` in `server_modules/credit_ledger_contract.py:41`:
`("platform_credits", "BYOK", "local", "subscription_passthrough")`.

`_canonical_payer()` in `server_modules/provider_catalog_service.py:48-58`
maps free-form tokens (`byok`, `workspace_api_key`, `workspace_connection` →
`BYOK`; `local`, `local_model`, `local_companion` → `local`; `subscription`,
`subscription_passthrough`, `codex_cli`, `claude_code_cli` →
`subscription_passthrough`; `empyralis_credits`, `empyralis`,
`platform_credits` → `platform_credits`).

### 2.2 The credit multiplier for BYO usage is 0 by construction

`_default_multiplier_for_item_type()` in `server_modules/credit_ledger_contract.py:134-141`:
```python
def _default_multiplier_for_item_type(item_type: str) -> float:
    if item_type == "ai_light_tokens":
        return 0.5
    if item_type == "ai_max_tokens":
        return 2.0
    if item_type == "ai_pro_tokens":
        return 1.0
    return 0.0
```
`custom_api_key_usage` (the ledger item type BYO usage maps to — see
`_tier_item_type()`, `server_modules/credit_ledger_contract.py:116-131`,
which returns `"custom_api_key_usage"` for `public_tier in
{"my_api_key", "my_ai_account"}` or `billing_source in {"user_api_key",
"user_ai_account"}`) falls through to the `return 0.0` branch — no multiplier
is defined for it, unlike the three `platform_credits` tiers.

### 2.3 Every call site zeroes credits/cost when payer isn't platform_credits

- `server_modules/deployed_agent_cost_cap_service.py:474-495` — building the
  unified ledger event for a deployed-agent run:
  ```python
  payer=ai_payer or ("platform_credits" if platform_paid_ai else "BYOK"),
  ...
  platform_cost_usd=usage_row.get("estimated_cost_usd") if platform_paid_ai else 0,
  ...
  credits_debited=usage_row.get("retail_credits_charged") if platform_paid_ai else 0,
  ```
  (`platform_paid_ai` is computed at `deployed_agent_cost_cap_service.py:410-412`
  as `bool(ai_source.get("uses_platform_credits")) or
  usage_accounting_service.is_platform_paid_usage_value(...)`.)

- `server_modules/deployed_agent_test_turn_service.py:534-551` — Studio's
  "test agent" turn: `payer="platform_credits" if
  usage_accounting_service.is_platform_paid_usage_value(payer) else payer`,
  `platform_cost_usd=... if is_platform_paid_usage_value(payer) else 0`,
  `credits_debited=... if is_platform_paid_usage_value(payer) else 0`.

- `server_modules/runtime_attachment_service.py:462-484` — compute-runtime
  (virtual browser/desktop/sandbox) minutes: `payer = "platform_credits" if
  billing_source == "empyralis_credits" else "local"`,
  `platform_cost_usd=cost if payer == "platform_credits" else 0`,
  `credits_debited=line_item.get("quantity") if payer == "platform_credits"
  else 0`.

- `server_modules/direct_chat_hosted_usage_service.py:390` —
  `credits_debited=0` (unconditional, non-platform-credit direct-chat path).

- `server_modules/knowledge_rag_service.py:664` and
  `server_modules/deployed_agent_virtual_runtime_service.py:2676` —
  `credits_debited=0.0`.

### 2.4 Unit test directly asserts this behavior

`server_modules/tests/test_credit_ledger_contract.py:143-171` —
`test_unified_ledger_event_carries_measurement_dimensions`: builds an event
with `payer="BYOK"`, real `provider_reported_cost=0.0003` (the real dollar
cost of the call is still captured for transparency), but
`platform_cost_usd=0`, `credits_debited=0`, and asserts
`event["credits_debited"] == 0.0` (line 171) even though a real cost was
recorded. Lines 175-193 show the identical pattern for `payer="local"`
(`knowledge_retrieval` credit type): `credits_debited == 0.0`.

### 2.5 Cost caps also only apply to platform-credit usage

`server_modules/deployed_agent_cost_cap_service.py:406-428` — the
monthly-cost-cap enforcement / fail-closed pause logic
(`deployed_agent_monthly_cost_cap_usd`, `_pause_deployment_fail_closed`) is
gated on `platform_paid_ai` (line 410-412) and `cap_usd is not None or
platform_paid_ai` (line 415). A BYOK-paid deployed agent's spend is not
enforced against the platform's monthly cost cap the same way a
platform-credit agent's is.

### 2.6 What the billing-credit-economy module itself says

`server_modules/billing_credit_config.py:1-71` (module docstring) — describes
itself as "Single source of truth for the Empyralis **hosted-AI** credit
economy" and walks through the retail exchange rate, per-turn margin
multiplier, and free allowance entirely in terms of "real hosted-AI turn[s]"
priced off provider cost (its own worked example uses `deepseek-chat`, a
platform-credit model). The module contains no BYOK-specific pricing or
debit logic — consistent with §2.2–2.4: BYOK usage never reaches this
credit-debiting path.

### 2.7 Net fact

Usage is still **recorded** (usage rows, `provider_usage`,
`provider_reported_cost` for the real off-platform dollar cost the user's own
key/subscription incurred) whenever payer ≠ `platform_credits`, but it is
**not metered against Empyralis credits and does not charge the workspace's
Empyralis credit balance** — `credits_debited` and `platform_cost_usd` are
zeroed by every ledger-event call site inspected above, and this is
locked in by a passing unit test. The founder's ask ("BYO subscriptions must
ALSO be billed/metered [by the platform]") is **not implemented today** — BYO
usage is currently free/unmetered from the platform's own credit-billing
perspective.

---

## 3. Model catalogue — contents and location

### 3.1 Backend catalog (canonical)

`server_modules/provider_profiles.py`:
- `PROVIDER_CATALOG` (lines 460-699) — per-provider auth modes, default
  model, flat model-id list, provider scopes, note.
- `PROVIDER_MODEL_CATALOG` (lines 784 onward, continues past the read
  window used in this audit — confirmed through `xai` at lines 1246-1279 and
  `deepseek` at lines 1309-1347) — per-model metadata: `label`,
  `context_window_tokens`, `input_cost_per_1k_usd` /
  `output_cost_per_1k_usd` (where known), `supports_tools`,
  `supports_vision`, `supports_json`, `supports_reasoning`,
  `reasoning_levels`, `capability_labels`.
- `PROVIDER_GOVERNANCE_CATALOG` (lines 701-782) — privacy posture,
  jurisdiction, residency, enterprise risk note, capability labels, and
  `local_self_hosted_compatible` per provider (e.g. DeepSeek's entry at
  lines 750-757 flags PRC data residency explicitly). **xai has no entry in
  `PROVIDER_GOVERNANCE_CATALOG`** — confirmed absent from the dict shown at
  lines 701-782 (openai, openai-codex, anthropic, gemini, vertex, qwen,
  deepseek, mistral, ollama, ollama_cloud only) — so a workspace connecting
  xAI/Grok gets no privacy/jurisdiction governance projection today (this is
  a factual gap, not a fix proposal).
- `provider_catalog_service.py` wraps this into a workspace-facing API:
  `list_workspace_provider_catalog()` (`server_modules/provider_catalog_service.py:587-626`)
  merges connection/runtime truth + hosted-AI policy + cached models per
  provider, projected via `_provider_catalog_projection()` (lines 360-392).
- `model_route_policy()` / `assert_model_route_policy()`
  (`server_modules/provider_catalog_service.py:194-262`) — enforce which
  provider/model combos are allowed for platform credits:
  `PLATFORM_CREDIT_MODEL_ALLOWLIST` (lines 28-34) restricts platform-credit
  billing to exactly three DeepSeek models: `deepseek-chat`,
  `deepseek-v4-pro`, `deepseek-reasoner`. `BYOK_FIRST_PROVIDERS` (lines
  21-26: `azure_openai`, `custom_openai_compatible`, `groq`, `openrouter`)
  are hard-blocked from ever using platform credits (`provider_catalog_service.py:250-252`:
  "is BYOK/workspace-key only and cannot use Empyralis credits.").

### 3.2 Frontend catalog (agent Model tab / create-agent wizard)

`frontend/lib/workspace/fleet/fleet-provider-constants.ts` (documented at
lines 1-2 as a manual mirror of the backend catalog, kept in sync by hand —
line 76: "Keep in sync with provider_profiles.py"):
- `MODELS_BY_PROVIDER` (lines 78-106) — flat model-id lists per provider,
  including `xai: ["grok-4", "grok-4-0709", "grok-4-latest", "grok-3"]`
  (line 100).
- `DEFAULT_MODEL_BY_PROVIDER` (lines 108-121).
- `FREEFORM_MODEL_PROVIDERS` (lines 125-128): `custom_openai_compatible`,
  `azure_openai` — these render a free-text field instead of a `<select>`.
- Consumed by `modelsForProvider()` / `defaultModelForProvider()`
  (lines 130-136), used in `FleetCreateAgentWizard.tsx` and
  `FleetAgentDetail.tsx`'s Model tab (see §4).
- Reasoning-effort vocabulary is also defined here:
  `REASONING_EFFORT_OPTIONS` (lines 146-152) for
  `platform_credits`/`byok_api` modes, and a separate
  `CLI_REASONING_EFFORT_OPTIONS_BY_RUNTIME` (lines 206-225) for
  `cli_subscription` (claude_code vs. codex have different vocabularies).

---

## 4. Model selection UI — where it lives

### 4.1 The Model tab

Single file: `frontend/lib/workspace/fleet/FleetAgentDetail.tsx`. There is
**no separate `ModelTab.tsx` file** — it is one function,
`ModelTab()`, defined inline at `FleetAgentDetail.tsx:2689-` (component
signature and hydration/save logic through at least line 2900+; not fully
read past that point in this audit).

- Tab registration: `TABS` array, `FleetAgentDetail.tsx:172-182` — `{ id:
  "model", label: "Model", icon: Sparkles }` at line 174, positioned
  second (right after "Overview"), per the comment at lines 165-171
  explaining it was moved forward from 7th-of-8 because a user "live
  complaint" was not finding it.
- Tab render dispatch: `FleetAgentDetail.tsx:521` —
  `{activeTab === "model" && <ModelTab workspaceId={workspaceId}
  agentId={agentId} agent={agent} onSaved={onRenamed} />}`.
- Inside `ModelTab`, provider/model selection state:
  `provider` (`FleetAgentDetail.tsx:2704`), `selectedModel`
  (`:2708`), sourced from `modelsForProvider` /
  `defaultModelForProvider` in `fleet-provider-constants.ts` (imported at
  top of file, confirmed via `providerLabel` import at
  `FleetAgentDetail.tsx:64`).
- Mode picker covers all four `ProviderMode` values (`platform_credits`,
  `byok_api`, `cli_subscription`, `local`) — `resolveDisplayMode(config)`
  seeds it (`FleetAgentDetail.tsx:2703`).

### 4.2 The right-side Properties panel

`FleetAgentDetail.tsx:405` — `<PanelRow label="Model" value={resolvedModel}
/>` — this is the row the founder referenced as showing "Platform default ·
deepseek-reasoner". It lives inside the `PanelSection title="Properties"`
block (`FleetAgentDetail.tsx:393`, rendered in the collapsible/desktop right
rail and the mobile `FleetRightPanel` drawer, lines 424-529).

`resolvedModel` is computed once per agent at `FleetAgentDetail.tsx:317`:
```ts
const resolvedModel = formatModelSummaryLine(resolveAgentModelSummary(agent?.model_config));
```
- `resolveAgentModelSummary()` (`FleetAgentDetail.tsx:73-108`) — the single
  source of truth (per its own doc comment, lines 67-72) shared by both the
  Properties-panel Model row and the Model tab's own display, so the two
  can't independently drift. Branches on `model_config.mode`:
  `cli_subscription` → runtime label (`claude_code`/`codex`) or
  `providerLabel(config.provider)`; `local` → `"Local"` +
  `config.model || "Ollama"`; explicit `provider`/`model`/`resolved_model`
  present → those values; otherwise falls through to `{ provider:
  "Platform default", model: "Platform default", isPlatformDefault: true }`
  (line 107).
- `formatModelSummaryLine()` (`FleetAgentDetail.tsx:110-117`) — renders
  `"Platform default"` when `isPlatformDefault`, else `"{provider} ·
  {model}"`, appending `" · {reasoningLabel} reasoning"` if a
  reasoning-effort override is set.
- The **exact string the founder saw** ("Platform default ·
  deepseek-reasoner") does not match this component's own `isPlatformDefault`
  branch (which renders literally `"Platform default"` with no model name
  appended, line 111-112) nor the `{provider} · {model}` branch (which would
  show a provider label, not the literal word "Platform default", before the
  model). This exact composite string is not produced by
  `formatModelSummaryLine` as read in this audit — it likely reflects a
  `model_config` where `provider` was empty/unset but `config.resolved_model
  = "deepseek-reasoner"` combined with `config.resolved_provider_label` also
  being the literal string `"Platform default"` (branch at
  `FleetAgentDetail.tsx:99-106`, where `provider:
  config.resolved_provider_label || "Platform default"` and `model:
  config.model || config.resolved_model || "Default"` — if the backend
  populated `resolved_provider_label` with `"Platform default"` and
  `resolved_model` with `"deepseek-reasoner"`, `formatModelSummaryLine`
  would render exactly `"Platform default · deepseek-reasoner"`). This
  inference is noted as such — the audit did not trace the backend field
  that would set `model_config.resolved_provider_label`.

### 4.3 Per-agent override mechanism

`model_config` is a per-agent JSON blob (`agent.model_config`,
`FleetAgentDetail.tsx:2702` `const config = agent?.model_config || {}`).
Saving happens through `ModelTab`'s own `save()` function
(`FleetAgentDetail.tsx:2785-`), which PATCHes the agent's `model_config` and
optionally writes a new BYOK credential to the vault first
(`FleetAgentDetail.tsx:2814-2828` references `/credentials/vault` then
`/providers/profiles`). New agents get their `model_config` seeded at
creation time server-side (see §5).

---

## 5. Current default model

**New Fleet specialist agents default to `platform_credits` / DeepSeek
Reasoner**, set explicitly in `seed_specialist_metadata()`:

`server_modules/fleet_tools.py:183-198`:
```python
def seed_specialist_metadata() -> Dict[str, Any]:
    """...model is explicit here...deny-a-successful-tool rate empirically
    measured this session: deepseek-chat 5/5, deepseek-reasoner 1/5..."""
    return {
        "role": SPECIALIST_ROLE,
        "subagents_enabled": False,
        "model_config": {"mode": "platform_credits", "model": "deepseek-reasoner"},
    }
```
The operator/Sage agent, by contrast, is seeded with no explicit model
(`seed_operator_metadata()`, `server_modules/fleet_tools.py:174-180`:
`"model_config": {"mode": "platform_credits"}` — no `"model"` key), which
falls through to the runtime provider-resolution default described next.

**Sage's own direct-chat provider fallback** (when a workspace has not set
an explicit `sage_ai_provider` in workspace admin metadata) defaults to
`deepseek` too: `server_modules/sage_agent_runtime_service.py:401-429` —
comment at line 401, `"── Default: PLATFORM provider (DeepSeek,
credit-gated) ──"`; it checks `DEEPSEEK_API_KEY` env presence (line 404-406),
then hosted-AI entitlement (`hosted_sage_ai_access_state_for_workspace_id`,
line 408-413), and returns `("deepseek", credentials)` at line 417 (or line
429 as a no-entitlement fallback) if usable.

At the model-catalog level, `PROVIDER_CATALOG["deepseek"]["default_model"]`
is `"deepseek-chat"` (`server_modules/provider_profiles.py:642`) — i.e. the
provider-catalog's own per-provider default differs from the
fleet-specialist-seed default (`deepseek-reasoner`); the specialist seed
explicitly overrides the provider-catalog default per the comment reasoning
at `fleet_tools.py:186-192` about tool-denial rates.

`PLATFORM_CREDIT_MODEL_ALLOWLIST` (`server_modules/provider_catalog_service.py:28-34`)
confirms only three models can ever be billed via platform credits today:
`deepseek-chat`, `deepseek-v4-pro`, `deepseek-reasoner` — no other provider's
model (including Grok) can be run on Empyralis's own hosted credits; every
other provider is BYOK-only for the reasons in §2.

---

## Summary of facts (no recommendations)

1. **Which providers can users bring today**: 15 providers in the backend
   catalog (`server_modules/provider_profiles.py:460-699`), 13 in the
   frontend BYOK list plus 2 subscription-passthrough plus 1 local
   (`frontend/lib/workspace/fleet/fleet-provider-constants.ts:8-31`):
   OpenAI, OpenAI Codex (subscription), Anthropic (API key or Claude
   subscription), Gemini, Vertex AI, Groq, OpenRouter, xAI, Azure OpenAI,
   AWS Bedrock, Qwen, DeepSeek, Mistral, Ollama (local), Ollama Cloud,
   Custom OpenAI-compatible.
2. **Is Grok among them**: Yes — `xai` is a fully-specified provider on both
   backend (`server_modules/provider_profiles.py:585-597`,
   `1246-1279`) and frontend (`fleet-provider-constants.ts:15, 100, 115`),
   with 4 models (`grok-4`, `grok-4-0709`, `grok-4-latest`, `grok-3`). One
   gap found: xAI has no entry in `PROVIDER_GOVERNANCE_CATALOG`
   (`server_modules/provider_profiles.py:701-782`), so it lacks a
   privacy/jurisdiction projection that every other listed provider has.
3. **Is BYO usage billed or free today**: Free/unmetered against Empyralis
   platform credits. `credits_debited` and `platform_cost_usd` are
   hard-zeroed for every payer other than `platform_credits` at every ledger
   call site found (`deployed_agent_cost_cap_service.py:474-495`,
   `deployed_agent_test_turn_service.py:534-551`,
   `runtime_attachment_service.py:462-484`,
   `direct_chat_hosted_usage_service.py:390`,
   `knowledge_rag_service.py:664`), and this is locked in by a passing unit
   test (`server_modules/tests/test_credit_ledger_contract.py:143-171`). The
   real off-platform dollar cost is still captured for observability
   (`provider_reported_cost`), just not charged to the workspace's credit
   balance. Monthly cost caps on deployed agents are also only enforced for
   `platform_paid_ai` usage (`deployed_agent_cost_cap_service.py:406-428`).
4. **Where the model catalogue + picker live**:
   Catalogue: `server_modules/provider_profiles.py` (`PROVIDER_CATALOG`,
   `PROVIDER_MODEL_CATALOG`, `PROVIDER_GOVERNANCE_CATALOG`), surfaced via
   `server_modules/provider_catalog_service.py`; mirrored by hand in
   `frontend/lib/workspace/fleet/fleet-provider-constants.ts`.
   Picker: the "Model" tab (`frontend/lib/workspace/fleet/FleetAgentDetail.tsx:2689` onward,
   tab registered at line 174, rendered at line 521); the right-side
   Properties panel shows a read-only one-line summary at
   `FleetAgentDetail.tsx:405` (`<PanelRow label="Model" value={resolvedModel}
   />`), computed by `resolveAgentModelSummary`/`formatModelSummaryLine`
   (`FleetAgentDetail.tsx:73-117`).
5. **Current default model**: New Fleet specialist agents are seeded with
   `model_config = {"mode": "platform_credits", "model":
   "deepseek-reasoner"}` (`server_modules/fleet_tools.py:194-198`). The
   operator/Sage agent has no explicit model in its seed
   (`fleet_tools.py:174-180`) and falls back to DeepSeek via
   `sage_agent_runtime_service.py:401-429` when no workspace-level provider
   is configured. Only three models can ever bill against platform credits:
   `deepseek-chat`, `deepseek-v4-pro`, `deepseek-reasoner`
   (`server_modules/provider_catalog_service.py:28-34`).
