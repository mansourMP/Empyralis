# Agent Computer Access Modes

Status: Active
Owner: Platform
Last verified: 2026-06-07
Source of truth: Agent Computer policy code

## Implemented Modes

`server_modules/agent_computer_policy_service.py` defines autonomy modes:

- `read_only`
- `ask_every_time`
- `safe_autopilot`
- `trusted_workstation`
- `emergency_stop`
- `yolo`

Gateway dispatch normalizes runtime access mode through
`server_modules/gateway_execution_service.py`. The policy payload sent to the
gateway reports mode as `default`, `custom`, or `full_access`.

## Product Boundary

Agent Computer is not the default requirement for Sage. Normal tasks should use
chat, connected apps, MCP, hosted browser, or cloud computer first. Agent
Computer is selected only when Sage needs local/private computer access:

- local files, folders, projects, apps, browser profiles, cookies, or desktop
  state;
- local terminal, local network, SSH keys, machine secrets, or hardware;
- personal Telegram/iMessage/Signal/WeChat sessions (WhatsApp personal is not a supported channel);
- dedicated Agent Computer hardware intentionally assigned to Sage.

Studio agents and external agents do not inherit Sage Full Access. External
agents remain connector/proxy/showing surfaces unless a separate native grant is
designed later.

## Full Access

Full Access is intentionally powerful. The security model is not to weaken the
mode; it is to gate and audit it:

- Full Access dispatch requires Sage scope.
- Full Access requires gateway registration metadata showing
  `runtime_access_mode=full_access`, `agent_scope=sage`, and
  `autonomous_agent_setup_warning_acknowledged=true`.
- Default Full Access policy uses filesystem scope `/`.
- The Rust supervisor also rejects Full Access unless the request is Sage scoped
  and the warning acknowledgement is present.

## Capability Policy

Policy capabilities are grouped as safe-read, mutating, and critical in
`agent_computer_policy_service.py`. Critical capability classes include
credential access, software/extension install, system permission changes,
payments, and production deploys.
