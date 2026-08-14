"""sage_telegram_hosted_service used to hardcode its state directory as
os.path.expanduser('~') / '.empyralis' / 'state', ignoring EMPYRALIS_STATE_HOME
entirely — the third credential/state leak route found the night of
2026-08-13/14. A throwaway stack with EMPYRALIS_STATE_HOME pointed at a
fresh temp dir still silently loaded the founder's real hosted-Telegram
pairing records from his home directory (confirmed live: the module's own
"loaded N pairs, M pending codes, K pending tokens from disk" log line was
observed against a disposable test stack whose EMPYRALIS_STATE_HOME was
explicitly set elsewhere). No write occurred and no token was set that run,
so nothing leaked that time — but a run with a bot token configured, or one
that reaches _save_state(), would read or mutate his real pairing state
from a disposable process.
"""

from __future__ import annotations

import importlib
from pathlib import Path


def test_state_dir_honors_empyralis_state_home(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("EMPYRALIS_STATE_HOME", str(tmp_path))
    module = importlib.import_module("server_modules.sage_telegram_hosted_service")
    importlib.reload(module)
    try:
        assert module._state_dir() == str(tmp_path)
        assert module._state_file() == str(tmp_path / "sage_telegram_hosted_pairs.json")
    finally:
        monkeypatch.delenv("EMPYRALIS_STATE_HOME", raising=False)
        importlib.reload(module)


def test_state_dir_falls_back_to_home_only_when_unset(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("EMPYRALIS_STATE_HOME", raising=False)
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
    module = importlib.import_module("server_modules.sage_telegram_hosted_service")
    importlib.reload(module)
    try:
        assert module._state_dir() == str(tmp_path / ".empyralis" / "state")
    finally:
        importlib.reload(module)


def test_save_and_load_state_round_trip_under_isolated_state_home(tmp_path: Path, monkeypatch) -> None:
    # The load-bearing regression proof: a disposable EMPYRALIS_STATE_HOME
    # must be where reads AND writes actually land — not merely where
    # _state_dir() reports, in case some caller still bypassed it.
    monkeypatch.setenv("EMPYRALIS_STATE_HOME", str(tmp_path))
    module = importlib.import_module("server_modules.sage_telegram_hosted_service")
    importlib.reload(module)
    try:
        module._SAGE_HOSTED_PAIRS.clear()
        module._PENDING_PAIRING_CODES.clear()
        module._PENDING_DEEP_LINK_TOKENS.clear()
        module._SAGE_HOSTED_PAIRS["workspace-1"] = {"chat_id": 12345}

        module._save_state()

        written_path = tmp_path / "sage_telegram_hosted_pairs.json"
        assert written_path.exists(), "state file must be written under the isolated state home, not ~/.empyralis"

        module._SAGE_HOSTED_PAIRS.clear()
        module._load_state()
        assert module._SAGE_HOSTED_PAIRS.get("workspace-1") == {"chat_id": 12345}
    finally:
        monkeypatch.delenv("EMPYRALIS_STATE_HOME", raising=False)
        importlib.reload(module)


def test_does_not_read_the_real_home_directory_pairing_file_when_isolated(tmp_path: Path, monkeypatch) -> None:
    # Direct reproduction of the incident: seed a decoy "real home" pairing
    # file and prove an isolated EMPYRALIS_STATE_HOME never touches it.
    fake_home = tmp_path / "fake-real-home"
    fake_home.mkdir()
    fake_state_dir = fake_home / ".empyralis" / "state"
    fake_state_dir.mkdir(parents=True)
    (fake_state_dir / "sage_telegram_hosted_pairs.json").write_text(
        '{"pairs": {"founders-real-workspace": {"chat_id": 999}}, "pending_codes": {}, "pending_tokens": {}}',
        encoding="utf-8",
    )
    monkeypatch.setattr(Path, "home", staticmethod(lambda: fake_home))

    isolated_state_home = tmp_path / "isolated-throwaway-state"
    monkeypatch.setenv("EMPYRALIS_STATE_HOME", str(isolated_state_home))
    module = importlib.import_module("server_modules.sage_telegram_hosted_service")
    importlib.reload(module)
    try:
        module._SAGE_HOSTED_PAIRS.clear()
        module._load_state()
        assert "founders-real-workspace" not in module._SAGE_HOSTED_PAIRS
        assert module._state_dir() == str(isolated_state_home)
    finally:
        monkeypatch.delenv("EMPYRALIS_STATE_HOME", raising=False)
        importlib.reload(module)
