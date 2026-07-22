from __future__ import annotations

import asyncio
import inspect
import warnings
from pathlib import Path

import pytest

asyncio.iscoroutinefunction = inspect.iscoroutinefunction


warnings.filterwarnings(
    "error",
    category=DeprecationWarning,
    module=r"server_modules(\..*)?",
)


@pytest.fixture(autouse=True)
def _skip_kernel_tests_when_binary_missing(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch):
    """Skip @pytest.mark.kernel tests when the Rust kernel binary is absent.

    For non-kernel tests, monkeypatch run_runtime_kernel to return a mock
    "allow" decision so business-logic tests are not blocked by a missing
    compiled binary.  Production kernel gates are NOT modified — only the
    test-time call to the kernel is replaced.
    """
    from server_modules.rust_runtime_kernel_client import runtime_kernel_binary

    if request.node.get_closest_marker("kernel") is not None:
        if runtime_kernel_binary() is None:
            pytest.skip(
                "requires empyralis-runtime-kernel binary (not built in this environment)"
            )
        return  # kernel binary present — let the test use the real kernel

    # Non-kernel tests: mock out run_runtime_kernel so the missing binary
    # does not block business-logic test paths.
    def _mock_run_runtime_kernel(command: str, payload, timeout_seconds: int = 5):
        import copy
        normalized_payload = payload if isinstance(payload, dict) else {}

        # The real kernel's runtime-state-store-decision command derives
        # `next_action` from the requested `operation` on an "allow"
        # decision (empyralis-runtime-kernel/src/runtime_state_store.rs
        # next_action(), lines 1106-1216): for every state-store operation
        # gated this way (e.g. "save_installed_skill_registry" ->
        # "save_installed_skill_registry", see line 1143;
        # "write_skills_registry_file" -> itself, line 1172;
        # "save_mcp_server_registry" -> itself, line 1142) that mapping is
        # the identity function. Callers such as
        # server_modules/installed_skills.py:134-136 and
        # server_modules/skills_registry.py:83 require next_action to
        # equal the operation they asked for before they'll persist state,
        # so the mock must echo it back rather than leaving it blank.
        next_action = ""
        if command == "runtime-state-store-decision":
            next_action = str(normalized_payload.get("operation") or "").strip()

        return {
            "ok": True,
            "decision": "allow",
            "command": command,
            "decision_id": "rkd_mock_non_kernel_test",
            "reason": "mock allow (non-kernel test fixture)",
            "next_action": next_action,
            "payload": copy.deepcopy(normalized_payload),
        }

    try:
        from server_modules import rust_runtime_kernel_client as _rk
    except Exception:
        return
    monkeypatch.setattr(_rk, "run_runtime_kernel", _mock_run_runtime_kernel)


@pytest.fixture(autouse=True)
def _isolate_empyralis_state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Keep tests from reading or writing the developer's real local state."""
    state_home = tmp_path / "empyralis-state"
    auth_db = state_home / "auth" / "users.db"
    gateway_db = state_home / "gateway" / "gateway-state.sqlite3"
    personal_channels_db = state_home / "channels" / "personal-channels.sqlite3"
    runtime_db = state_home / "runtime" / "state.db"
    setup_sessions_file = state_home / "setup" / "sessions.json"
    provider_profiles_file = state_home / "providers" / "profiles.json"
    idempotency_file = state_home / "runtime" / "idempotency.json"
    for path in (auth_db, gateway_db, personal_channels_db, runtime_db, setup_sessions_file, provider_profiles_file, idempotency_file):
        path.parent.mkdir(parents=True, exist_ok=True)

    monkeypatch.setenv("EMPYRALIS_STATE_HOME", str(state_home))
    monkeypatch.setenv("ORION_RUNTIME_STATE_DB", str(runtime_db))
    monkeypatch.setenv("EMPYRALIS_GATEWAY_STATE_DB", str(gateway_db))
    monkeypatch.setenv("ORION_SETUP_SESSIONS_FILE", str(setup_sessions_file))
    monkeypatch.setenv("ORION_PROVIDER_PROFILES_FILE", str(provider_profiles_file))
    monkeypatch.setenv("ORION_IDEMPOTENCY_FILE", str(idempotency_file))

    try:
        from server_modules import auth

        monkeypatch.setattr(auth, "AUTH_DB_FILE", auth_db, raising=False)
    except Exception:
        pass
    try:
        from server_modules import control_plane_repository

        monkeypatch.setattr(control_plane_repository, "LOCAL_IDENTITY_DB_FILE", auth_db, raising=False)
    except Exception:
        pass
    try:
        from server_modules import gateway_state_repository

        monkeypatch.setattr(gateway_state_repository, "GATEWAY_STATE_DB_FILE", gateway_db, raising=False)
    except Exception:
        pass
    try:
        from server_modules import personal_channels_repository

        monkeypatch.setattr(personal_channels_repository, "PERSONAL_CHANNELS_DB_FILE", personal_channels_db, raising=False)
    except Exception:
        pass
    try:
        from server_modules import runtime_config

        monkeypatch.setattr(runtime_config, "ORION_RUNTIME_STATE_DB", runtime_db, raising=False)
        monkeypatch.setattr(runtime_config, "ORION_SETUP_SESSIONS_FILE", setup_sessions_file, raising=False)
        monkeypatch.setattr(runtime_config, "ORION_PROVIDER_PROFILES_FILE", provider_profiles_file, raising=False)
        monkeypatch.setattr(runtime_config, "ORION_IDEMPOTENCY_FILE", idempotency_file, raising=False)
    except Exception:
        pass
    try:
        from server_modules import shared

        shared.sync_acp_manager_paths(
            runtime_db_path=runtime_db,
            setup_sessions_path=setup_sessions_file,
            provider_profiles_path=provider_profiles_file,
            idempotency_path=idempotency_file,
        )
    except Exception:
        pass
