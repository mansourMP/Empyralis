import os

from server_modules.blackbox_runtime_support import _apply_common_runtime_environment


def test_blackbox_runtime_support_does_not_read_legacy_backend_env(monkeypatch, tmp_path):
    monkeypatch.delenv("DATABASE_URL", raising=False)

    _apply_common_runtime_environment(
        monkeypatch,
        state_home=tmp_path / "state",
        webhook_secret="secret",
    )

    assert "DATABASE_URL" not in os.environ


def test_blackbox_runtime_support_preserves_explicit_database_url(monkeypatch, tmp_path):
    _apply_common_runtime_environment(
        monkeypatch,
        state_home=tmp_path / "state",
        webhook_secret="secret",
        extra_env={"DATABASE_URL": "postgresql://example.invalid/empyralis"},
    )

    assert os.environ["DATABASE_URL"] == "postgresql://example.invalid/empyralis"
