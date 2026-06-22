# Platform Security Model

Status: Active
Owner: Platform
Last verified: 2026-06-06
Source of truth: domain security docs and enforcement code

## Security Goal

Empyralis security must protect the customer account, workspace data, platform
secrets, customer BYOK secrets, hosted credits, local hardware access, and agent
execution surfaces without weakening the product promise. Full Access and local
hardware control can exist, but they must be authenticated, scoped, audited, and
revocable.

## Layers

- Account and workspace access: every customer-visible action must resolve the
  authenticated account and workspace before reading or mutating state.
- Sage: main customer agent. It can use cloud tools and selected Agent Computer
  capabilities only through exposed runtime/tool gates.
- Studio: deployed/specialist agents. They must not inherit Sage personal
  channels or Sage Agent Computer Full Access by default.
- Agent Computer: customer hardware lane through gateway and supervisor.
  Full Access remains powerful but requires Sage scope, selected hardware,
  setup-warning acknowledgement, live gateway readiness, policy checks, and
  audit.
- Runtime: local/cloud/self-hosted execution must use enrollment tokens,
  session tokens, workspace/machine binding, leases, quotas, and rate limits.
- Channels: inbound messages must be authenticated, normalized, deduplicated,
  routed to the correct Sage or Studio lane, and stored with workspace scope.
- Apps: app bridge metadata must not smuggle private owner resources, Sage
  memory, runtime session ids, shell/computer control, or raw tool calls.
- Billing/Credits: hosted AI usage must be attributable, priced, recorded in
  ledgers, and debited without exposing platform provider secrets.
- Secrets: platform-hosted provider secrets and customer BYOK secrets must be
  separate ownership lanes with explicit audit.

## Domain Security Sources

- `docs/domains/sage/security.md`
- `docs/domains/studio/security.md`
- `docs/domains/agent-computer/security.md`
- `docs/domains/runtime/security.md`
- `docs/domains/channels/security.md`
- `docs/domains/apps/security.md`
- `docs/domains/discover/security.md`
- `docs/domains/billing-credits/security.md`

## VPS / Self-Hosted Worker OS-Level Confinement

The self-hosted command worker runs under OS-level confinement enforced by the systemd unit
(`deploy/empyralis-command-worker.service`). The agent physically cannot reach credentials,
system directories, or other tenants — proven on a real Linux VPS:

- Runs as a non-root user (`User=empyralis`).
- `ProtectSystem=strict` — `/usr`, `/boot`, `/etc` are read-only.
- `ProtectHome=read-only` — home directories are inaccessible for write.
- `PrivateDevices=yes` — no access to raw devices (`/dev/sda`, `/dev/mem`, etc.).
- `ProtectProc=invisible` — cannot see other processes' `/proc` entries.
- `CapabilityBoundingSet=` (empty) — all Linux capabilities dropped.
- `ReadWritePaths` scoped to the agent workspace only; the credential vault, `~/.ssh`, and
  `/etc/empyralis` are outside this scope and physically unreachable.

**The systemd unit is the ONLY supported deployment.** Running the worker directly
(`python3 scripts/empyralis_self_hosted_command_worker.py`) bypasses ALL of the above
confinement. Direct invocation is unsupported and unsafe.

## Catastrophic Command Blocking

Catastrophic commands (`rm -rf /`, `mkfs`, fork bombs, etc.) and protected paths (the credential
vault, `~/.ssh`, `/etc/empyralis`) are hard-blocked at the governance kernel level. The agent
cannot destroy the environment or read credentials regardless of what command it is asked to run.

## Credentials and Vault Key Security

- Credentials live in an encrypted vault (Fernet + PBKDF2), backed up off-box to the cloud
  (encrypted; the cloud cannot read it without the key).
- Do NOT put `CREDENTIAL_VAULT_KEY` in a plain `Environment=` line or shell env var. These are
  readable via `/proc/<pid>/environ`. Use a key file with `0600` permissions (see
  `deploy/empyralis-command-worker.service` for the `EnvironmentFile=` pattern). Never print
  real secrets or tokens in example configs — use obvious placeholders.
- The cloud-managed / just-in-time secrets model is the production end-state.

## Non-Negotiable Rules

- Do not solve security by breaking the product contract.
- Do not remove Full Access to make security simpler.
- Do not let Studio, apps, or public channels inherit Sage personal resources.
- Do not expose raw provider keys, connector secrets, local session files, or
  platform secrets to frontend responses.
- Do not allow unauthenticated owner-mode or runtime-token minting paths.
- Do not let a gateway or runtime act outside its workspace/machine binding.
- Do not recommend putting `CREDENTIAL_VAULT_KEY` in a plain env var.
- Do not present direct worker invocation as a normal setup; mark it unsupported/unsafe.
