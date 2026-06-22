# Bible

Last verified: 2026-06-23

## Non-Negotiable Rules

### 1. One Platform, One Sage

Mobile, desktop, web, local, cloud, and hybrid are one platform.
They are not separate brains.

There is one Sage identity per user and workspace.

### 2. Four-Layer Separation Is Mandatory

The platform must stay separated into:
- Sage / captain
- specialist workers
- applications
- platform control plane

These layers must not collapse into one undifferentiated blob.

### 3. Tenant Isolation Is Absolute

Tenant and workspace isolation is mandatory.

Control-plane data must remain tenant-scoped and workspace-scoped, backed by Postgres RLS where applicable.
Any fallback storage must preserve the same logical tenant and workspace boundaries.

There is no acceptable shortcut that leaks cross-tenant or cross-workspace data.

### 4. BYOK Security Is Mandatory

Bring-your-own-key and provider credential handling must remain server-side and brokered.

Rules:
- secrets never become casual client payloads
- credentials flow through vault and broker boundaries
- runtime workers receive only the scope they need
- connector access is auditable

### 5. Fail Closed, Not Open

If policy, placement, sync, entitlement, or approval state is unclear, the system must fail closed.

Examples:
- unknown sync class: deny sync
- invalid placement: deny run
- missing entitlement: deny premium hosted action
- unclear approval state: pause or deny
- unavailable durable state path: do not silently continue with unsafe runtime state
- `ORION_AUTH_REQUIRED=0`: enter explicit local-dev identity mode only; never silently grant anonymous owner/admin power
- `ENV=production` with auth disabled: reject the request path rather than silently running without auth
- public webhooks without required provider verification headers or keys: reject or disable the path before parsing, dispatch, or run creation

### 6. Strict API Gateways Only

All surfaces must use strict backend contracts.

Rules:
- no UI-side bypasses
- no direct private-memory reads from clients
- no connector or secret access from the surface layer
- no app-specific hidden side channels
- protected API routes default to backend auth
- intentional public ingress must be explicitly documented and limited to provider-verified webhook boundaries

Every privileged action must enter through an explicit API or broker boundary.

### 7. No Surface-Specific Capability Downgrade

Capability is determined by:
- runtime availability
- policy
- memory scope
- connector scope
- approval state

Capability is not determined by:
- whether the request came from mobile
- whether the request came from desktop
- whether the action started inside an app shell

### 8. Applications Are Not Sage

Applications are product modules.
They do not inherit captain memory by default.
They do not inherit specialist memory by default.

All app-to-agent behavior must go through typed bridge contracts.

### 9. Specialists Are Real, Scoped Agents

Specialists must remain:
- powerful
- scoped
- auditable
- policy-bound

They are not prompt wrappers.
They are not allowed to inherit broad personal memory by default.

### 10. Local-Private Memory Stays Local By Default

Hybrid is allowed.
Accidental sync is not.

Local-private memory must remain local unless policy explicitly allows:
- `sync_allowed`
- `summary_bridge_only`
- `explicit_opt_in`

### 11. Tools, Secrets, And Runtime Access Stay Brokered

No agent, app, or UI surface gets direct uncontrolled access to:
- tools
- secrets
- runtime routing
- connector egress

Broker boundaries are part of the product, not optional plumbing.

### 12. Activity Must Be Durable And Attributable

Important actions must be captured in the activity ledger with actor identity.

That includes:
- Sage actions
- specialist actions
- app actions
- delegation
- approvals
- blocked actions
- artifacts
- memory updates

### 13. Full Trust Requires Explicit Owner Control

High-trust execution is allowed only on explicitly authorized machines and under explicit owner or tenant policy.

It still requires:
- audit trails
- kill paths
- security controls
- scoped capability enforcement

### 14. The UI Must Stay Dumb

The UI may render, guide, animate, and collect intent.
The UI must not become the place where policy, memory, runtime, or authorization truth lives.

Backend truth wins.
