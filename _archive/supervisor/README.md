# Supervisor Archive — Disabled by product decision 2026-07-04

The Rust empyralis-supervisor daemon (mouse/keyboard/screen/local-fs control)
is DISABLED, not deleted. The code is preserved here for auditability.

**Status:** Fully functional as of commit `44451aa9c` (Phase P3, the commit
immediately before Phase U1 removal).

**Owner decision:** Empyralis agents do NOT control user desktops. The Gateway
STAYS (personal channels + VPS pairing). Desktop control via supervisor is OUT
of the product.

## Revival checklist

If desktop control is ever brought back:

1. Restore files from this archive to their original locations:
   - `_archive/supervisor/empyralis-supervisor/` → `empyralis-supervisor/`
   - `_archive/supervisor/supervisor_client.py` → `server_modules/supervisor_client.py`
   - `_archive/supervisor/computer_control.py` → `server_modules/computer_control.py`
   - `_archive/supervisor/gateway/client.ts` → `empyralis-gateway/src/supervisor/client.ts`
   - `_archive/supervisor/gateway/signing.ts` → `empyralis-gateway/src/supervisor/signing.ts`
2. Revert `empyralis-gateway/src/supervisor/capability-router.ts`:
   - Add "supervisor" back to `ExecutorName` type
   - Restore `GatewaySupervisorClient` import and constructor param
   - Restore supervisor fallback in `handleToolInvoke` (line ~120)
   - Restore supervisor + personal_channel interrupt handling
3. Revert `empyralis-gateway/src/config.ts`:
   - Restore `supervisorUrl`, `supervisorSecret`, `supervisorTimeoutMs` fields
4. Revert `empyralis-gateway/src/index.ts`:
   - Restore `GatewaySupervisorClient` import and init
   - Restore supervisorClient param in `GatewayCapabilityRouter` constructor
5. Revert `empyralis-gateway/src/cloud/ws-client.ts`:
   - Restore `checkLocalRunnerHealth()` to actually call supervisor health endpoint
6. Revert `server_modules/skills_service.py`:
   - Restore 3 supervisor shortcut blocks (search for "ARCHIVED (Phase U1)")
7. Revert `server_modules/gateway_execution_service.py`:
   - Rename `_normalize_gateway_capability` back to `_gateway_supervisor_capability`
8. Revert `server_modules/sage_agent_runtime_service.py`:
   - Restore agent machine supervisor override block
9. Revert `server_modules/sage_telegram_hosted_service.py`:
   - Restore supervisor screenshot import and routing
10. Revert `server_modules/direct_chat_provider_service.py`:
    - Restore agent machine supervisor override
11. Revert frontend: restore `supervisor_running` fields and supervisor capability UI
12. Revert `.github/workflows/ci.yml`: restore `supervisor-build` job
13. Revert `scripts/orion_local_worker_execution.py`: restore real supervisor_client import
14. Revert `config/local_runtime_cluster_map.json`: rename back to `local_runtime_supervisor`
15. Compile the Rust crate: `cargo build --manifest-path empyralis-supervisor/Cargo.toml`
16. Set `EMPYRALIS_SUPERVISOR_SECRET` in `.env`
17. Start the supervisor: `cargo run --manifest-path empyralis-supervisor/Cargo.toml`
18. Run the supervisor test suite

## Archive contents

- `empyralis-supervisor/` — Rust crate (12 .rs files, full Cargo.lock + Cargo.toml)
- `gateway/client.ts` — Gateway HTTP client to supervisor daemon
- `gateway/signing.ts` — HMAC-SHA256 signing for supervisor requests
- `supervisor_client.py` — Direct loopback HTTP client (Python)
- `computer_control.py` — Legacy facade around supervisor_client
- `empyralis_supervisor_minimal.py` — Minimal Python supervisor replacement
- `autonomous_computer_control_loop.py` — Autonomous screen→vision→action loop
- `demo_supervisor_empyralis_alive.py` — Demo script
- `test_supervisor_local_control.py` — Integration test

## Runtime status

The runtime has ZERO live supervisor surface (Phase U1). Gateway channels
(Telegram, WhatsApp, browser) are UNAFFECTED. All capability dispatch that
previously fell through to supervisor now throws a clear error.
