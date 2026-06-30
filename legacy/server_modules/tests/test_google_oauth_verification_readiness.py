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


def test_google_workspace_default_oauth_scopes_are_gmail_calendar_only(monkeypatch) -> None:
    monkeypatch.delenv("GOOGLE_WORKSPACE_OAUTH_SCOPES", raising=False)
    monkeypatch.delenv("GOOGLE_OAUTH_SCOPES", raising=False)
    monkeypatch.delenv("GOOGLE_WORKSPACE_ENABLE_DRIVE_SCOPE", raising=False)
    monkeypatch.delenv("GOOGLE_OAUTH_ENABLE_DRIVE_SCOPE", raising=False)

    config = connection_oauth_service.OAUTH_PROVIDER_CONFIGS["google_workspace"]
    query = {
        "scope": connection_oauth_service._joined_scopes("google_workspace", config),
    }
    scopes = urlparse.parse_qs(urlparse.urlencode(query))["scope"][0].split()

    assert "openid" in scopes
    assert "email" in scopes
    assert "profile" in scopes
    assert "https://www.googleapis.com/auth/gmail.modify" in scopes
    assert "https://www.googleapis.com/auth/calendar" in scopes
    assert "https://www.googleapis.com/auth/drive.file" not in scopes


def test_google_workspace_drive_scope_requires_explicit_opt_in(monkeypatch) -> None:
    monkeypatch.delenv("GOOGLE_WORKSPACE_OAUTH_SCOPES", raising=False)
    monkeypatch.delenv("GOOGLE_OAUTH_SCOPES", raising=False)
    monkeypatch.setenv("GOOGLE_WORKSPACE_ENABLE_DRIVE_SCOPE", "true")

    config = connection_oauth_service.OAUTH_PROVIDER_CONFIGS["google_workspace"]
    scopes = connection_oauth_service._joined_scopes("google_workspace", config).split()

    assert "https://www.googleapis.com/auth/drive.file" in scopes
