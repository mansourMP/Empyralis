# BYOK Strategy — Subscription vs API Key Decision

> **OUTDATED (2026-07-23):** this pending-decision doc is from 2026-06-30 —
> the decision it poses has since been made and built (`cli_subscription` is
> live; see `docs/design/reliability-audit-4-byo-billing-models.md` and
> `docs/PLATFORM-MAP.md` Part 25/26). It also predates the 2026-07-23 founder
> ruling that "Sage" is dead product terminology (the platform has only
> agents — owner-facing, customer-facing serving the owner, and AskAI); this
> document uses "Sage" throughout as a live concept. Kept for history; do
> not build from this.

**Created:** 2026-06-30
**Status:** pending decision
**Decision to make:** Should Empyralis support subscription/CLI-based auth, or API-key-only?

---

## What exists in the codebase

### Credential modes (backend)

| File | Line | What |
|------|------|------|
| `runtime_models.py` | 473 | `mode: str = "byok"` — three modes: `byok`, `managed`, `vertex` |
| `runtime_models.py` | 484 | Validation: only `byok`, `managed`, `vertex` allowed |
| `entitlements_service.py` | 512 | Message: "Add credits or use your own API key" |

### Provider grouping (frontend)

| File | Line | What |
|------|------|------|
| `workstation-sage-connectors-pane.tsx` | 95 | Groups: `byok`, `hosted`, `local` |
| `workstation-sage-connectors-pane.tsx` | 1830 | `defaultAuthMode` per provider: `api_key`, `oauth_token`, `none`, `local_cli`, `local_subscription` |
| `workstation-sage-connectors-pane.tsx` | 3428 | `byokItems` = providers where `!providerIsLocalOnly(record)` |
| `workstation-chat-pane.tsx` | 1886 | Model section ordering: `empyralis` → `local_ai` → `my_api_key` → `my_ai_account` |
| `workstation-chat-pane.tsx` | 217 | Provider list includes `{ id: 'codex', label: 'Codex CLI' }` |

### Model tiers (API contract)

| File | Line | What |
|------|------|------|
| `shared/api-contract/model-tier-contract.ts` | 6 | `'my_api_key'` tier |
| `shared/api-contract/model-tier-contract.ts` | 12 | `'user_api_key'` tier |
| `shared/api-contract/model-tier-contract.ts` | 34 | User-owned tiers: `local_ai`, `my_api_key`, `my_ai_account` |

### Branding logic

| File | Line | What |
|------|------|------|
| `platform-brand.ts` | 51 | BYOK path → show provider's real label |
| `platform-brand.ts` | 75 | BYOK path → show provider's real logo |

---

## What actually works vs what's wired but untested

### Works (proven)

| Auth method | How | Hardware needed? | Status |
|-------------|-----|:---:|--------|
| **Empyralis credits (DeepSeek)** | Platform-managed API key | No | Works |
| **BYOK via API key** | User pastes their OpenAI/Anthropic/DeepSeek API key | No | Wired, partially tested |

### Wired but unverified

| Auth method | How | Hardware needed? | Status |
|-------------|-----|:---:|--------|
| **BYOK via OAuth token** | Google OAuth for workspace login | No | Wired for auth, not for AI providers |
| **BYOK via local CLI** | Detects `claude` or `codex` CLI on user's machine | **Yes** | UI has `local_cli` mode, backend not built |
| **BYOK via local subscription** | Extracts OAuth token from logged-in Claude/Codex desktop app | **Yes** | UI has `local_subscription` mode, backend not built |

### Not built

| Auth method | What it would take |
|-------------|-------------------|
| **Subscription proxy (like BYOKEY)** | Gateway runs a local OAuth→API proxy. User logs into Claude Pro via Gateway, Gateway exposes OpenAI-compatible endpoint to cloud agent |
| **Agent SDK credits** | Track Anthropic's per-user Agent SDK credit pool, warn user before it exhausts |

---

## Market reality (June 30, 2026)

1. **API keys are stable.** No AI company has restricted API key usage. It's a pay-per-token contract.
2. **Subscriptions are under attack.** Anthropic now meters third-party usage into a separate credit pool (Pro=$20/mo, Max=$100-200/mo). The "all-you-can-eat" arbitrage is dead.
3. **OpenAI is heading the same direction.** Codex CLI works with third-party providers via API key, but ChatGPT subscription tokens are locked to official apps.
4. **BYOKEY exists** — open-source Rust tool that converts subscriptions into API endpoints. Requires local hardware. 103 GitHub stars. Niche but working.
5. **OpenClaw docs now recommend OpenAI Codex subscriptions** over Anthropic — the Anthropic relationship is damaged.

### The trend line

```
2024: "Use any model with your subscription"
2025: "Use our models with our tools"
2026: "Use our models with our tools, metered separately for third-party"
2027: TBD — likely metered-only, subscription bundles shrink
```

---

## The decision

**Should Empyralis support subscription/CLI-based auth, or API-key-only?**

### Option A: API-key-only

- Simpler to build, ship, and maintain
- Works cloud-only, no hardware requirement
- Immune to AI company policy changes
- Covers 100% of the "I pay for AI" market
- **Risk:** Excludes users who only have subscriptions (Pro/Max/Plus), no API account

### Option B: API-key + subscription (via Gateway)

- Covers both API users AND subscription-only users
- Gateway runs a local OAuth proxy (like BYOKEY) → cloud agent uses it
- This is the differentiation: "Bring any AI account, not just API keys"
- **Risk:** Building and maintaining OAuth extraction for multiple providers (Claude, Codex, Gemini). Each provider changes their auth flow periodically. High maintenance burden.
- **Risk:** Subscription credits are shrinking — by the time you build it, the economics may not justify it

### Option C: API-key + best-effort subscription

- Build API-key path fully (ship v1)
- For subscription: document how to use BYOKEY alongside the Gateway. Don't build it yourself — point users at the open-source tool
- When a user connects Gateway + BYOKEY, the platform detects the local proxy and uses it
- **Risk:** Dependency on a third-party tool (BYOKEY) with 103 GitHub stars

---

## Files to review before deciding

| File | Why |
|------|-----|
| `runtime_models.py:469-487` | Credential modes — is `byok` vs `managed` vs `vertex` the right split? |
| `workstation-sage-connectors-pane.tsx:1820-1840` | Default auth modes per provider |
| `workstation-sage-connectors-pane.tsx:3420-3445` | How BYOK items are grouped in the UI |
| `entitlements_service.py:500-560` | Credit exhaustion logic and messaging |
| `platform-brand.ts:45-160` | How BYOK branding differs from hosted |
| `model-tier-contract.ts:1-40` | Model tier definitions |

---

## Recommendation (for the frontier model to evaluate)

**Ship Option A (API-key-only) for v1.** The subscription path adds months of work and the AI companies are actively making it worse. API keys are the stable primitive. Document BYOKEY as a Gateway companion for power users. If subscription demand proves real post-launch, build it then — but don't delay v1 for a feature with a shrinking window.
