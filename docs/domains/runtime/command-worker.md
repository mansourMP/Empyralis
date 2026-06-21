# Self-Hosted Command Worker

Drains the Main Agent's self-hosted command queue — the queue that holds
hardware tool calls (shell, file, screenshot) for user-owned machines
(Mac, Mac mini gateway, VPS) enrolled as self-hosted nodes.

**Architecture:** dial-OUT — the node polls the cloud. Cloud never reaches
in over SSH. See [ADR: Canonical hardware execution](../decisions/canonical-hardware-execution-dial-out-2026-06-21.md).

## Quick Start

```bash
# 1. Set required env vars
export EMPYRALIS_CLOUD_URL=https://empyralis.com
export EMPYRALIS_RUNTIME_PROFILE_ID=rp_xxxx
export EMPYRALIS_NODE_SESSION_TOKEN=ns_xxxx

# 2. Run
python scripts/empyralis_self_hosted_command_worker.py
```

## Env Vars

### Required

| Var | Description |
|-----|-------------|
| `EMPYRALIS_CLOUD_URL` | Base URL of the Empyralis cloud API (no trailing slash) |
| `EMPYRALIS_RUNTIME_PROFILE_ID` | The `runtime_profile_id` from self-hosted node enrollment |
| `EMPYRALIS_NODE_SESSION_TOKEN` | The `node_session_token` from enrollment (acts as API key) |

### Optional

| Var | Default | Description |
|-----|---------|-------------|
| `EMPYRALIS_POLL_INTERVAL` | `2.0` | Seconds between poll cycles |
| `EMPYRALIS_MAX_COMMANDS_PER_POLL` | `5` | Max commands to claim per poll |
| `EMPYRALIS_LEASE_SECONDS` | `120` | Command lease duration (heartbeat = lease/3) |
| `EMPYRALIS_WORKER_LOG_FORMAT` | `json` | Log format: `json` or `text` |

## Dependencies

- **Python:** 3.12+
- **Python packages:** None beyond stdlib (uses `urllib`, `subprocess`, `pathlib`)
- **Rust kernel binary:** Required for node-side governance enforcement.
  The worker calls `rust_runtime_kernel_client.run_runtime_kernel_enforced()`
  before every command execution. If the kernel binary is not found, the
  worker refuses to execute (fail-closed). The kernel binary is built from
  `empyralis-runtime-kernel/` and must be on `PATH` or in the project root.

## Supported Capabilities

| capability_id | Executor | Notes |
|---------------|----------|-------|
| `shell.execute` / `shell__exec` | `subprocess.run()` | Timeout configurable via `arguments.timeout_seconds` |
| `filesystem.read` / `file__read` | `Path.read_text()` | Max 1 MiB per read |
| `filesystem.write` / `file__write` | `Path.write_text()` | Creates parent dirs automatically |

## Lifecycle

```
register/enroll (once, via cloud UI)
  → poll POST .../commands/claim
    → govern (Rust kernel: local-worker-decision)
      → execute (shell | file_read | file_write)
        → complete POST .../commands/{id}/result
```

## Systemd Deployment

```bash
sudo cp deploy/empyralis-command-worker.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable empyralis-command-worker
sudo systemctl start empyralis-command-worker

# Check status and logs
sudo systemctl status empyralis-command-worker
journalctl -u empyralis-command-worker -f
```

## Governance

Before EVERY command execution, the worker calls:
```
rust_runtime_kernel_client.run_runtime_kernel_enforced("local-worker-decision", {
    "operation": "execute_command",
    "workspace_id": "...",
    "runtime_profile_id": "...",
    "command_id": "...",
    "capability_id": "...",
    ...
})
```

The kernel enforces:
- Kill switch (global, workspace, agent scopes)
- Local companion disabled
- Worker / run / workspace validation

If the kernel blocks, the command is completed as `failed` with reason
`governance_blocked`. If the kernel binary is unavailable, the worker
refuses to execute (fail-closed).

## Relationship to Orion Worker

The Orion worker (`scripts/orion_local_worker_runtime.py`) drains the
**studio-agent task/run queue** (`/runtime/tasks/claim`). This worker drains
the **Main Agent command queue** (`/runtime/self-hosted-nodes/{id}/commands/claim`).

They are INDEPENDENT processes that can share a supervisor (systemd, supervisord).
They do NOT share session tokens or registration — each has its own auth model.
