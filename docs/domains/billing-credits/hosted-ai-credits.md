# Hosted AI Credits

Status: Active
Owner: Platform
Last verified: 2026-06-06
Source of truth: hosted usage, billing, and entitlement services

## Hosted AI Credit Flow

Hosted AI credit accounting happens in
`server_modules/direct_chat_hosted_usage_service.py` when provider availability
has `credential_plane=platform_runtime`.

The service requires:

- platform runtime allowed
- non-empty usage payload
- workspace id
- request id
- tenant id
- usage precise enough for platform-paid accounting

It builds a usage snapshot, normalizes it through
`usage_accounting_service.usage_row_from_snapshot(...)`, rejects unknown or
insufficient pricing, writes a workspace hosted AI monthly cost ledger entry,
writes a unified credit ledger event, then calls
`billing_service.debit_workspace_credit_balance_for_hosted_usage(...)`.

Credit debit happens after ledger persistence. Failure to write the cost ledger,
write the unified ledger, or debit credit raises runtime errors.

## Entitlement Fields

`server_modules/entitlements_service.py` exposes hosted Sage AI state including
policy, monthly cap USD, monthly cost USD, monthly remaining USD, credit balance,
total available credits, and reason. Chat tiers expose light/pro/max monthly
credit caps.

## Default Provider: DeepSeek (Internal/Operator Doc)

> **Consumer-facing surfaces must say "Platform AI," not the provider name.** The provider identity
> is never exposed to consumers. This section is for operators and internal documentation only.

Empyralis credits always route to **DeepSeek**. All three platform tiers (light, pro, max) use DeepSeek internally — there is no other provider behind Empyralis credits. There is NO fallback to other providers.

**Why:** DeepSeek offers frontier-model quality at roughly 1/10th the cost of alternatives (Anthropic, OpenAI). This lets Empyralis offer affordable credit-based pricing while maintaining quality.

**BYO:** Users who want a different provider select "My API Key" or "My AI Account" from the model picker — those tiers bypass Empyralis credits entirely and use the user's own key/account. This is documented in `byok-vs-platform-keys.md`.

**Do not add a second provider to `CLOUD_PROVIDER_IDS` in `sage_agent_runtime_service.py` without explicit product approval.**

## Refunds

Not implemented in inspected code: no automatic refund path was verified in
this pass.
