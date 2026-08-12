from pathlib import Path
from urllib import parse as urlparse

from server_modules import connection_oauth_service


ROOT = Path(__file__).resolve().parents[2]


def _page_text(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_public_privacy_page_contains_google_verification_terms() -> None:
    text = _page_text("frontend/app/privacy/page.tsx")

    for phrase in (
        "Google user data only after you give consent",
        "read, organize, draft, send, archive, or label email",
        "view availability, read events, create events, or update events",
        "does not sell Google user data",
        "does not use Google user data for advertising",
        "does not use Google user data to train third-party AI models",
        "Disconnecting a Google account stops Empyralis from making new Google API requests",
        "mansurao886@gmail.com",
    ):
        assert phrase in text


def test_public_terms_page_contains_verification_contact_and_beta_terms() -> None:
    text = _page_text("frontend/app/terms/page.tsx")

    for phrase in (
        "AI assistant and workspace product",
        "responsible for connected accounts, requested actions, approved actions",
        "early access or beta",
        "We do not guarantee uninterrupted or error-free service",
        "mansurao886@gmail.com",
    ):
        assert phrase in text


def _clear_google_scope_env(monkeypatch) -> None:
    for name in (
        "GOOGLE_WORKSPACE_OAUTH_SCOPES",
        "GOOGLE_OAUTH_SCOPES",
        "GOOGLE_WORKSPACE_OAUTH_ENABLED_SCOPES",
        "GOOGLE_OAUTH_ENABLED_SCOPES",
        "GOOGLE_WORKSPACE_ENABLE_DRIVE_SCOPE",
        "GOOGLE_OAUTH_ENABLE_DRIVE_SCOPE",
    ):
        monkeypatch.delenv(name, raising=False)


def test_google_workspace_default_oauth_scopes_are_the_live_non_sensitive_set(monkeypatch) -> None:
    """As of 2026-08-12 the live consent screen for this deployment's Google
    OAuth app (empyralis-gws-cli) declares ONLY the non-sensitive scopes --
    identity plus Drive. Gmail (restricted) and Calendar (sensitive) were
    removed so sign-in and Drive stay unverified-clean; they come back
    later behind a separate, verified OAuth app. This is the default
    (nothing configured) case -- see fix/google-connectors-honest-when-
    scopes-unavailable."""
    _clear_google_scope_env(monkeypatch)

    config = connection_oauth_service.OAUTH_PROVIDER_CONFIGS["google_workspace"]
    query = {
        "scope": connection_oauth_service._joined_scopes("google_workspace", config),
    }
    scopes = urlparse.parse_qs(urlparse.urlencode(query))["scope"][0].split()

    assert "openid" in scopes
    assert "email" in scopes
    assert "profile" in scopes
    assert "https://www.googleapis.com/auth/drive.file" in scopes
    assert "https://www.googleapis.com/auth/gmail.modify" not in scopes
    assert "https://www.googleapis.com/auth/calendar" not in scopes


def test_google_workspace_gmail_and_calendar_are_available_once_enabled(monkeypatch) -> None:
    """The return path once a second, verified OAuth app exists for the
    sensitive/restricted scopes: flipping GOOGLE_WORKSPACE_OAUTH_ENABLED_
    SCOPES is the one edit that brings them back."""
    _clear_google_scope_env(monkeypatch)
    monkeypatch.setenv("GOOGLE_WORKSPACE_OAUTH_ENABLED_SCOPES", "drive gmail calendar")

    config = connection_oauth_service.OAUTH_PROVIDER_CONFIGS["google_workspace"]
    scopes = connection_oauth_service._joined_scopes("google_workspace", config).split()

    assert "https://www.googleapis.com/auth/gmail.modify" in scopes
    assert "https://www.googleapis.com/auth/calendar" in scopes
    assert "https://www.googleapis.com/auth/drive.file" in scopes


def test_google_workspace_drive_scope_legacy_flag_still_honored(monkeypatch) -> None:
    """The pre-fix opt-in flag (GOOGLE_WORKSPACE_ENABLE_DRIVE_SCOPE) is still
    read when the new GOOGLE_WORKSPACE_OAUTH_ENABLED_SCOPES is unset, so an
    environment that already set it either way keeps behaving exactly as it
    did before this fix."""
    _clear_google_scope_env(monkeypatch)
    monkeypatch.setenv("GOOGLE_WORKSPACE_ENABLE_DRIVE_SCOPE", "true")

    config = connection_oauth_service.OAUTH_PROVIDER_CONFIGS["google_workspace"]
    scopes = connection_oauth_service._joined_scopes("google_workspace", config).split()

    assert "https://www.googleapis.com/auth/drive.file" in scopes


def test_google_workspace_drive_scope_legacy_flag_can_still_disable_drive(monkeypatch) -> None:
    _clear_google_scope_env(monkeypatch)
    monkeypatch.setenv("GOOGLE_WORKSPACE_ENABLE_DRIVE_SCOPE", "false")

    config = connection_oauth_service.OAUTH_PROVIDER_CONFIGS["google_workspace"]
    scopes = connection_oauth_service._joined_scopes("google_workspace", config).split()

    assert "https://www.googleapis.com/auth/drive.file" not in scopes
    assert "openid" in scopes
