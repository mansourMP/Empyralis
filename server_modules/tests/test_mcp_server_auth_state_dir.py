"""mcp_server_auth.py stores hashed per-workspace MCP API keys and used to
fall back to os.path.expanduser('~') / '.empyralis' / 'state' / ... when
EMPYRALIS_MCP_KEYS_FILE wasn't set — ignoring EMPYRALIS_STATE_HOME entirely.
Found by preflight's new state-home-resolution scan
(server_modules/preflight.py's _check_local_stack_state_home_resolution) on
its first real run, 2026-08-14 — the same shape as
sage_telegram_hosted_service.py's hardcoded pairing-state path, except what
this file stores is real API key material rather than pairing metadata.
"""

from __future__ import annotations

import importlib
from pathlib import Path


def test_keys_file_honors_empyralis_state_home(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("EMPYRALIS_MCP_KEYS_FILE", raising=False)
    monkeypatch.setenv("EMPYRALIS_STATE_HOME", str(tmp_path))
    module = importlib.import_module("server_modules.mcp_server_auth")
    importlib.reload(module)
    try:
        assert module._mcp_api_keys_file() == tmp_path / "runtime" / "mcp_api_keys.json"
    finally:
        monkeypatch.delenv("EMPYRALIS_STATE_HOME", raising=False)
        importlib.reload(module)


def test_explicit_override_still_wins_over_state_home(tmp_path: Path, monkeypatch) -> None:
    override = tmp_path / "custom" / "keys.json"
    monkeypatch.setenv("EMPYRALIS_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setenv("EMPYRALIS_MCP_KEYS_FILE", str(override))
    module = importlib.import_module("server_modules.mcp_server_auth")
    importlib.reload(module)
    try:
        assert module._mcp_api_keys_file() == override
    finally:
        monkeypatch.delenv("EMPYRALIS_STATE_HOME", raising=False)
        monkeypatch.delenv("EMPYRALIS_MCP_KEYS_FILE", raising=False)
        importlib.reload(module)


def test_does_not_touch_the_real_home_directory_key_registry_when_isolated(tmp_path: Path, monkeypatch) -> None:
    # Direct reproduction of the risk: seed a decoy "real home" key registry
    # and prove an isolated EMPYRALIS_STATE_HOME never reads or writes it.
    fake_home = tmp_path / "fake-real-home"
    fake_keys_dir = fake_home / ".empyralis" / "state" / "runtime"
    fake_keys_dir.mkdir(parents=True)
    (fake_keys_dir / "mcp_api_keys.json").write_text(
        '{"keys": {"real-workspace-key-hash": {"workspace_id": "founders-real-workspace"}}}',
        encoding="utf-8",
    )
    monkeypatch.setattr(Path, "home", staticmethod(lambda: fake_home))

    isolated_state_home = tmp_path / "isolated-throwaway-state"
    monkeypatch.delenv("EMPYRALIS_MCP_KEYS_FILE", raising=False)
    monkeypatch.setenv("EMPYRALIS_STATE_HOME", str(isolated_state_home))
    module = importlib.import_module("server_modules.mcp_server_auth")
    importlib.reload(module)
    try:
        keys = module._load_keys()
        assert keys == {"keys": {}}, "must not have loaded the decoy real-home key registry"
        assert module._mcp_api_keys_file() == isolated_state_home / "runtime" / "mcp_api_keys.json"

        module._save_keys({"keys": {"isolated-hash": {"workspace_id": "throwaway-workspace"}}})
        assert not (fake_keys_dir / "mcp_api_keys.json.tmp").exists()
        real_home_contents = (fake_keys_dir / "mcp_api_keys.json").read_text(encoding="utf-8")
        assert "founders-real-workspace" in real_home_contents, "the decoy file must be untouched, not overwritten"
        assert "isolated-hash" not in real_home_contents
    finally:
        monkeypatch.delenv("EMPYRALIS_STATE_HOME", raising=False)
        importlib.reload(module)
