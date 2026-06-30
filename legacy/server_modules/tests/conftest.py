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
