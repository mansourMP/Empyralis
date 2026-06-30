from __future__ import annotations

from pathlib import Path

from server_modules import runtime_config
from server_modules.connectors import autopilot_connector_config
from server_modules.state_paths import cognitive_db_path, resolve_state_path, runtime_state_db_path


def test_state_path_defaults_resolve_under_empyralis_state_home(tmp_path: Path) -> None:
    state_home = tmp_path / "state"

    def env_get(key: str, default: str = "") -> str:
        values = {"EMPYRALIS_STATE_HOME": str(state_home)}
        return values.get(key, default)

    assert runtime_state_db_path(env_get) == state_home / "runtime" / "state.db"
    assert cognitive_db_path(env_get) == state_home / "memory" / "agency_memory.db"
    assert resolve_state_path("ORION_HISTORY_FILE", "runtime/run_history.json", env_get) == state_home / "runtime" / "run_history.json"


def test_state_path_env_override_still_wins(tmp_path: Path) -> None:
    override = tmp_path / "custom" / "runtime.sqlite3"

    def env_get(key: str, default: str = "") -> str:
        values = {
            "EMPYRALIS_STATE_HOME": str(tmp_path / "state"),
            "ORION_RUNTIME_STATE_DB": str(override),
        }
        return values.get(key, default)

    assert runtime_state_db_path(env_get) == override


def test_runtime_config_does_not_fall_back_to_repo_root_legacy_files(tmp_path: Path, monkeypatch) -> None:
    state_home = tmp_path / "state"
    legacy_file = tmp_path / ".orion_run_history.json"
    legacy_file.write_text("[]", encoding="utf-8")
    monkeypatch.setattr(runtime_config, "EMPYRALIS_STATE_HOME", state_home, raising=False)

    resolved = runtime_config._resolve_state_file(
        "EMPYRALIS_TEST_HISTORY_FILE",
        "runtime/run_history.json",
        str(legacy_file),
    )

    assert resolved == state_home / "runtime" / "run_history.json"


def test_autopilot_connector_config_does_not_fall_back_to_repo_root_legacy_files(tmp_path: Path, monkeypatch) -> None:
    state_home = tmp_path / "state"
    legacy_file = tmp_path / ".orion_telegram_autopilot_state.json"
    legacy_file.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(autopilot_connector_config, "EMPYRALIS_STATE_HOME", state_home, raising=False)

    resolved = autopilot_connector_config._resolve_state_file(
        "EMPYRALIS_TEST_AUTOPILOT_FILE",
        "channels/telegram/autopilot_state.json",
        str(legacy_file),
    )

    assert resolved == state_home / "channels" / "telegram" / "autopilot_state.json"
