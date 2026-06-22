# Gateway Architecture

Last verified: 2026-06-07
Status: Live architecture with implemented baseline runtime

This document freezes the canonical local-gateway architecture for Empyralis.
It exists to stop future local-runtime work from drifting back into the old
`local_companion` transport assumptions or into the Studio webhook connector
stack.

## Current Repo Truth

The active platform already has three relevant realities:

1. `/Users/mansur/empyralis/server_modules` is the live
   cloud control-plane and run-engine surface.
2. `/Users/mansur/empyralis/empyralis-supervisor` is a
   real local loopback daemon for privileged device capabilities.
3. `/Users/mansur/empyralis/server_modules/connectors`
   is the active cloud/business webhook connector stack.
4. `/Users/mansur/empyralis/empyralis-gateway` now
   exists as a real persistent local runtime process with local state,
   pairing/session bootstrap, personal-channel runtimes, and browser/runtime
   routing.

The repo now has canonical local-gateway building blocks for:
- personal channel sessions
- one outbound cloud control-plane socket
- durable local journal / reconnect state
- unified local routing to the supervisor
- gateway-governed local browser sessions with approvals, checkpoints, and
  fallback

The implemented baseline is visible in:
- `empyralis-gateway/src/index.ts`
- `server_modules/routes_gateway.py`
- `server_modules/routes_personal_channels.py`
- `server_modules/channel_lane_contract_service.py`

The existing `local_companion` target is real, but the current machine-session
auth model must not remain the long-term architecture.

## Frozen Component Model

| Component | Canonical role | Explicitly not responsible for |
| --- | --- | --- |
| Cloud control plane | Primary identity authority, pairing authority, gateway registry, run/orchestration truth, policy, memory, approvals, billing | Owning personal session files directly on the device |
| `empyralis-gateway` | Persistent local gateway, personal channel session owner, outbound WSS client, local journal/outbox/checkpoint owner, local bridge to supervisor | Becoming a second product brain or separate auth system |
| `empyralis-supervisor` | Narrow local capability daemon for screenshot, OCR, mouse, keyboard, clipboard, app launch, OS-local actions | Owning channel sessions, cloud sockets, identity, or durable messaging state |
| `server_modules/connectors/*` | Studio/business cloud connector lane for webhook/API-managed channels | Personal account session ownership |

## Canonical Boundary Decisions

### 1. Cloud Stays The Primary Identity Authority

The user, tenant, workspace, and session model remains cloud-owned.
`empyralis-gateway` is a paired device/runtime attached to that primary model.

The canonical identity tuple is:
- `tenant_id`
- `workspace_id`
- `user_id`
- `device_id`
- `gateway_id`

`gateway_id` and `device_id` are **not** a second user identity system.

### 2. `empyralis-gateway` Is The Persistent Local Runtime Edge

`empyralis-gateway` is the only local process that may:
- own personal channel sessions
- maintain the outbound cloud control-plane connection
- persist local gateway journal/outbox/checkpoint state
- route cloud-issued local work toward `empyralis-supervisor`

If a future local feature needs durable local connectivity, it belongs behind
`empyralis-gateway`, not behind ad hoc workers or webhook adapters.

### 3. `empyralis-supervisor` Stays Narrow

`empyralis-supervisor` remains a loopback capability executor.
It may expose:
- local device control
- screenshot / OCR
- clipboard
- app launch
- other tightly scoped machine capabilities

It may **not** become:
- the personal channel gateway
- the cloud session owner
- the local policy engine
- the pairing authority

### 4. Personal Channels And Studio Channels Stay Separate

Personal channels are local-session products.
Studio channels are cloud/business connector products.

They may eventually converge on lower run-engine contracts, but they must not
share the same ingress/auth/session architecture.

### 5. Legacy Socket.IO Bridge Is Removed

The old Conductor-era Socket.IO execution bridge has been removed. Do not
recreate it or route new local execution through a second bridge runtime; use
the canonical gateway plus supervisor boundary instead.

### 6. No Second Auth Plane

The new gateway architecture must remove the current `local_companion`
machine-session split-brain model.

Gateway runtime trust must remain anchored to:
- primary tenant/workspace/user identity
- paired device registration
- revocable gateway/device trust tokens
- explicit heartbeat/session state

## Canonical Lifecycle

The lifecycle is frozen as:

1. Pair
   - a signed-in Empyralis shell or control-plane flow issues a pairing grant
   - the grant is scoped to `tenant_id`, `workspace_id`, and `user_id`
2. Register
   - `empyralis-gateway` creates local persistent state
   - cloud registers `device_id` and `gateway_id`
3. Connect
   - gateway opens one outbound WSS session to cloud
   - cloud binds the live socket to the paired identity tuple
4. Heartbeat
   - gateway sends regular liveness / capability / health updates
   - cloud tracks online/offline/degraded state
5. Operate
   - cloud sends local tool work, approvals, or personal-channel work through
     the gateway session
   - gateway fans out locally to channel runtimes and the supervisor
6. Degrade
   - if the socket drops, the gateway records local journal state
   - local work may be paused, retried, or held according to policy
7. Recover
   - gateway reconnects
   - cloud and gateway resume from acknowledged sequence / checkpoint state
8. Revoke
   - cloud can revoke the gateway or device
   - revoked sessions must stop working immediately on reconnect

## Canonical Ownership Map

### Cloud Control Plane

Owns:
- pairing grants
- gateway registry
- device trust and revocation
- run and approval truth
- workspace / tenant policy
- activity and billing truth

Does not own:
- personal auth/session files stored on the local machine
- OS-level device control primitives

### `empyralis-gateway`

Owns:
- local gateway process lifecycle
- outbound WSS client
- local journal / outbox / checkpoint state
- personal channel session state
- gateway-side reconnect handling
- local routing to `empyralis-supervisor`

Does not own:
- a separate user model
- a separate billing model
- primary memory truth
- Studio/business webhook ingress

### `empyralis-supervisor`

Owns:
- local loopback capability execution
- authenticated requests from the gateway
- narrow capability results and interrupts

Does not own:
- personal channel connections
- cloud socket state
- pairing or heartbeat state

## Mapping To Current Repo

These current pieces stay relevant:
- `empyralis-gateway/src/index.ts`
  - boots the WSS client, journal/outbox/checkpoints, supervisor client,
    Telegram personal runtime, local-bridge personal
    runtimes, and gateway browser runtime (WhatsApp personal runtime exists as legacy dead code)
- `server_modules/routes_gateway.py`
  - exposes pairing, registration, token rotation/revocation, tool execution,
    approvals, doctor, event history, and browser control routes
- `server_modules/routes_personal_channels.py`
  - exposes the clean personal-channel routing lane for Telegram,
    Signal, iMessage, and WeChat (WhatsApp personal is not a supported channel)
- `server_modules/channel_lane_contract_service.py`
  - enforces personal-vs-Studio execution-lane boundaries
- `server_modules/runtime_attachment_service.py`
  - `local_companion` remains the current logical local-runtime attachment kind
    in older placement paths, but it must converge toward the gateway-backed
    model rather than become a second architecture
- `server_modules/outbox_service.py`
  - existing local outbox ideas are valid implementation references, not the
    future public contract
- `server_modules/routes_connectors.py`
  - remains the Studio/business webhook ingress lane
- `empyralis-supervisor/src/main.rs`
  - remains the narrow local capability daemon

Implemented current-state truth:
- `empyralis-gateway` already exists in the repo
- the cloud-backed pairing/session/WSS path already exists
- the supervisor already runs behind the gateway control path
- personal Telegram already terminates at the selected Agent Computer gateway lane
  (WhatsApp personal code exists in the repo but is not a supported channel — Baileys is banned
  and WhatsApp Business API bars third-party AI assistants as of Jan 15 2026)
- Signal, iMessage, and WeChat are planned/private Agent Computer bridge
  contracts until real-account setup, durable replay, outbound approval, health
  reporting, and account lifecycle are certified
- gateway-governed browser runtime surfaces already exist
- hosted mini-app and governed marketplace layers already exist elsewhere in
  the platform and should not be mistaken for missing foundation work

This document still matters because it freezes the product boundary the live
implementation must continue to respect.

## Historical Phase Boundary

The original Phase 0 version of this document did **not** implement the gateway,
personal Telegram, frontend UI, or marketplace/app changes. That is no
longer the current repo truth.

The live repo now contains baseline implementations for the selected Agent
Computer gateway, personal Telegram (personal WhatsApp code exists but the channel is not
supported — see note above), local-bridge personal runtimes,
browser/governed local capability routing, and the related UI surfaces. This
document now serves as the boundary contract those live implementations must
continue to respect.
