# ADR: Canonical hardware execution — dial-out (gateway + polling worker), reject SSH-in

Status: Accepted
Owner: Platform
Last verified: 2026-06-21
Source of truth: explicit decision

## Decision

Empyralis hardware execution MUST use a dial-out model: the user's machine (Mac, Mac
mini gateway, or VPS) initiates and maintains an outbound connection to the cloud
(gateway WebSocket or a polling worker). Cloud-reaches-in over SSH is REJECTED as a
canonical execution path. The VPS SSH fallback in `skills_service.py` (lines
3377–3477, never functional because the imported functions were never defined) is
removed.

## Context

Two execution models were considered for the VPS/hardware tier:

1. **Dial-in (SSH):** The cloud connects to the user's VPS over SSH to run commands,
   read/write files, and capture screenshots. This was prototyped as a "VPS SSH
   fallback" in `skills_service.py` but the required functions (`vps_ssh_execute`,
   `vps_ssh_file_read`, `vps_ssh_file_write`, `vps_ssh_screenshot`, `is_vps_configured`)
   were never implemented — the import always raised `ImportError` and the block was
   silently skipped.

2. **Dial-out (gateway + polling worker):** The user's machine runs a local agent
   (gateway or polling worker) that maintains an outbound WebSocket or polls the
   cloud for work. This is the architecture used by the Main Agent's gateway and by
   the Orion studio/run worker (`scripts/orion_local_worker_runtime.py`).

The dial-out model is chosen as canonical because:

- **Gateway is the backbone.** The Mac/Mac mini gateway already uses dial-out
  WebSockets. A VPS tier should follow the same architecture rather than introduce a
  second, incompatible path.
- **Works behind NAT/firewalls.** Outbound connections from the user's machine
  require no inbound port forwarding, SSH key distribution, or firewall rules.
- **One node install serves both surfaces.** The same local agent can drain the
  self-hosted command queue (for Main Agent tool calls) AND the runtime task queue
  (for studio/Orion runs). A single install covers both use cases.
- **Aligned with OpenClaw.** The reference open-source agent uses a local daemon
  that dials out. Following the same model keeps Empyralis compatible with that
  ecosystem.

The SSH-in model was rejected because it requires the user to expose SSH ports,
manage key pairs, and maintain static IPs — all of which the managed platform is
meant to abstract away. The dead VPS SSH code in `skills_service.py` (101 lines,
never executed) is removed in this decision.

## Consequences

- **Removed:** The VPS SSH fallback block in `server_modules/skills_service.py`
  (formerly lines 3377–3477). No live code path is affected — the block was always
  skipped via `ImportError`.
- **No env vars needed:** `EMPYRALIS_VPS_HOST`, `EMPYRALIS_VPS_USER`, and
  `EMPYRALIS_VPS_SSH_KEY_PATH` were only referenced in the deleted comment and never
  defined in any `.env.example` or config. They are fully retired.
- **Unchanged:** `server_modules/vps_provisioning_service.py` — this handles VPS
  instance lifecycle (create/destroy), not SSH execution, and is a separate concern.
- **Required future work:** A production dial-out worker must be built to drain the
  self-hosted command queue (see recommendation memo). The queue's HTTP API
  (enqueue/claim/complete) and Rust governance already exist; only the worker is
  missing.
- **Test suite:** Must remain green after the dead-code removal.

## Rejected Alternatives

- **Cloud-reaches-in over SSH:** Rejected because it introduces a second execution
  path with different security, networking, and operational requirements. SSH key
  management, static IPs, and port forwarding are exactly the complexity the managed
  platform should eliminate.
- **Keep the dead code as a "placeholder":** Rejected because dead code that
  references non-existent imports is misleading. The import path
  (`hardware_runtime_target_resolver`) is already consumed by real resolution logic;
  grafting SSH functions onto it would conflate concerns. A clean deletion followed
  by a proper dial-out worker is the right sequence.

## Enforced By

- Code: the VPS SSH fallback has been removed from `server_modules/skills_service.py`
- Code: `server_modules/hardware_runtime_target_resolver.py` defines no SSH functions
- Code: the self-hosted command queue API (`runtime_runtime_api.py`) enforces the
  claim/complete flow — any future worker must conform to it
- Review: future PRs introducing SSH-in execution paths must be rejected under this
  ADR
