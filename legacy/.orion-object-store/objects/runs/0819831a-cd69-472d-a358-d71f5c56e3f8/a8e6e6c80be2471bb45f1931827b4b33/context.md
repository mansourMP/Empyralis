# Context

Last verified: 2026-04-11
Latest verified green commit: `b3eca81`

## Product Identity

Empyralis is an AI operating system for authorized digital work.

It is one platform with one Sage identity, one workspace model, one runtime policy model, one memory model, and one audit trail across mobile, desktop, web, local, cloud, and hybrid operation.

The platform is not a pile of separate bots, not a workflow toy, and not a mobile app plus a different desktop brain.

## The 4-Layer OS Model

### 1. Sage / Personal Captain

Sage is the user's main agent and the only primary intelligence the user should feel.

Sage owns:
- the main relationship with the user
- personal memory facade
- orchestration of specialists
- summary-level visibility into specialist and app activity
- approvals, delegation, and review flow

Sage does not get to bypass runtime, tool, secret, or policy brokers.

### 2. Specialist Workers

Specialists are real agents, not wrappers.

Each specialist has:
- install-scoped short-term and long-term memory
- its own tool scope
- its own connector scope
- its own artifact history
- its own runtime policy
- its own activity stream

Specialists are powerful, but scoped. They do not inherit Sage memory by default, and they do not automatically inherit each other's memory.

### 3. Applications

Applications are product modules, not the personal brain.

Applications may:
- run workflows
- call models
- perform structured backend actions
- call specialists or Sage through explicit bridge contracts

Applications may not, by default:
- read Sage memory
- read specialist memory
- read private user context without an explicit contract

### 4. Platform Control Plane

The control plane governs all three layers above.

It owns:
- tenant and workspace identity
- runtime profiles and runtime attachments
- entitlements and quotas
- memory routing rules
- tool and secret brokering
- activity ledgering
- approvals
- security controls
- hybrid sync and placement policy

## Hybrid Cluster Model

One Sage identity must survive across all deployment modes:
- cloud-only
- local-only
- hybrid

### Local Companion / Mac Mini Cluster

The local cluster is not one vague process. It is:
- a local Sage runtime
- separate local specialist runtimes
- a local runtime supervisor
- local memory stores
- a local artifact bridge

Lifecycle is explicit:
- register
- start
- health
- heartbeat
- stop
- revoke
- recover

### Cloud / Hybrid Behavior

Hybrid placement is governed by explicit sync and placement policy classes:
- `local_only`
- `sync_allowed`
- `summary_bridge_only`
- `explicit_opt_in`

Placement priority is:
1. privacy and sync class
2. required capabilities and connectors
3. data dependency
4. availability and always-on need
5. user or workspace preference

The system must fail closed if a placement or sync rule cannot be satisfied.

## Strict Sandbox And Memory Boundaries

### Memory Boundaries

Sage can read:
- personal captain memory
- workspace-level summary context
- specialist summaries, status, and artifacts
- app and connector history summaries

Sage does not automatically read:
- raw unrestricted specialist internals
- raw app-private state
- local-private memory that policy marks as local-only

Specialists can read:
- their own scoped memory
- explicitly shared inputs and artifacts

Specialists cannot read by default:
- Sage private memory
- other specialists' scoped memory
- app-private context not explicitly shared

Applications can read:
- user-selected inputs
- app-owned history
- scoped documents and payloads
- explicit imports granted through app bridge contracts

Applications cannot read by default:
- Sage memory
- specialist memory

### Runtime And Broker Boundaries

All execution remains brokered through policy-bound boundaries:
- tool access goes through `tool_broker`
- secrets go through `secrets_broker` and vault paths
- runtime placement goes through runtime attachment and hybrid policy services
- connector and egress actions are auditable

No UI surface is allowed to bypass these boundaries.

## Auth And Public Ingress Boundary

Protected backend routes default to backend auth.

Current auth truth:
- `ORION_AUTH_REQUIRED=1` is the normal protected mode
- missing or invalid protected-route auth fails closed
- `ORION_AUTH_REQUIRED=0` is explicit local-dev mode only
- `ENV=production` rejects auth-disabled mode
- local-dev defaults are:
  - `user_id=local-dev`
  - `email=local-dev@empyralis.local`
  - `role=member`
  - `workspace_ids=["default"]`
- explicit local-dev scope knobs are:
  - `ORION_LOCAL_DEV_AUTH_ROLE`
  - `ORION_LOCAL_DEV_WORKSPACE_IDS`
  - `ORION_LOCAL_DEV_USER_ID`
  - `ORION_LOCAL_DEV_EMAIL`
- local-dev owner/admin power requires explicit owner role configuration

Intentional public ingress is limited to verified provider webhooks:
- `/channels/slack/events`
  - Slack signing-secret verification runs before payload parse or activity append
- `/channels/github/webhook`
  - configured GitHub `webhook_secret` plus valid `X-Hub-Signature-256` runs before payload parse or activity append
- `/connectors/discord/webhook`
  - Discord signature, timestamp, and configured public key verification run before parse, dispatch, or run creation
- `/channels/whatsapp/twilio/webhook`
  - configured `ORION_WHATSAPP_AUTOPILOT_WEBHOOK_SECRET` via query or header is required before Twilio form parse or inbound handling

Operational status and admin surfaces remain protected:
- `/channels/telegram/autopilot/status`
- `/channels/whatsapp/autopilot/status`
- `/channels/autopilot/profiles`

## Surface Truth

Mobile is the daily-use default.
Desktop is the power-user and control-depth surface.

They are still the same platform.

Allowed differences:
- navigation
- information density
- control depth
- debug and admin ergonomics

Forbidden differences:
- weaker mobile execution semantics
- different memory rules by surface
- different app-agent contracts by surface
- different runtime power by surface

Mobile tabs are fixed:
- Home
- Chat
- Applications
- Notifications
- Profile

## Proven Runtime Truth Through Phase 44

Verified backend slices now proven in this workspace:
- auth-disabled mode is explicit local-dev only; production rejects auth-disabled mode
- public webhook ingress fails closed and verifies before parse, dispatch, or run creation
- the canonical backend golden path is proven: `/turn -> parent run -> delegated specialist -> artifact -> activity -> approval -> /runs`
- canonical install-backed parent and delegated child runs carry enforced placement metadata
- hybrid summary-bridge publish and ingest are validated for allowed summary payloads only
- `/runs`, `/activity/timeline`, `/approvals`, and notifications expose aligned backend truth
- `app_to_sage` and `app_to_specialist` typed bridge requests can execute through canonical install-backed turns

These proofs do not yet mean:
- rendered mobile or web UI proof exists on this audit machine
- full cloud-only, local-only, and hybrid demo coverage exists
- automatic local summary publish and remote replication are complete

## Implemented Core Through Phase 44

Verified backend and architecture work already landed:
- entitlements and quota service
- open-core boundary definition
- mobile-first / desktop-power product surface contract
- captain / specialist / application boundary
- application runtime and app-agent separation contract
- durable activity ledger
- local runtime cluster control APIs
- hybrid sync and placement policy enforcement
- app-agent bridge enforcement
- fail-closed auth and public webhook hardening
- canonical backend golden-path smoke proof
- contract-aligned run, activity, approval, and notification visibility
- canonical `app_to_sage` and `app_to_specialist` bridge execution
- specialist service architecture
- first parity-oriented mobile and desktop surface implementation

## Immediate Reality For The Next Session

The architecture backbone is done.
The platform is ready for execution-focused work, not more sprawling architecture notes.

The next session should assume:
- `docs/` is the only canonical handoff
- archived documents are history, not source of truth
- runtime, policy, and memory boundaries above are non-negotiable
