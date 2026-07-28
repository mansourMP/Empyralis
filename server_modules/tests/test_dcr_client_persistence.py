"""MAN-124: dynamically-registered OAuth clients must outlive the process.

The bug these tests lock down
-----------------------------
An RFC 7591 client registration used to live ONLY in
`connection_oauth_service._DYNAMIC_CLIENT_CACHE`, a plain in-process dict. The
OAuth flow spans two separate HTTP requests — `start_oauth` (register + send
the user to the provider) and the callback (exchange the auth code) — so a
deploy, crash, or a callback landing on a different worker meant the second
half registered a DIFFERENT client_id than the auth code was issued to. The
provider answered `invalid_grant` / `invalid_client` and the user saw "this
MCP application just doesn't work", intermittently.

Registrations are now persisted encrypted in the credential vault
(`oauth_dynamic_client_store`). Each test below simulates "a different
process" the only way that matters here: by clearing the in-process cache and
proving the flow still resolves the SAME client.

The `dcr_client_vault` fixture (conftest) swaps the vault for an in-memory
store that still performs real Fernet encryption — so nothing here touches a
database, and the encryption assertions are meaningful.
"""

from __future__ import annotations

import json
import time

import pytest
from fastapi import HTTPException

from server_modules import connection_oauth_service as service
from server_modules import oauth_dynamic_client_store as store


_REDIRECT_URI = "https://app.example.com/api/connections/oauth/linear/callback"

_ENV_VARS = (
    "LINEAR_OAUTH_CLIENT_ID", "LINEAR_CLIENT_ID",
    "LINEAR_OAUTH_CLIENT_SECRET", "LINEAR_CLIENT_SECRET",
    "LINEAR_OAUTH_ENABLED", "LINEAR_MCP_ENABLED",
)


def _clear_env(monkeypatch) -> None:
    for name in _ENV_VARS:
        monkeypatch.delenv(name, raising=False)


def _stub_registration(monkeypatch, *, client_id: str, client_secret: str, expires_at: int = 0) -> list:
    """Replace the network call to the provider's /register endpoint."""
    calls: list = []

    def fake_post_json(url, payload, *, headers=None):
        calls.append((url, payload))
        response = {"client_id": client_id, "client_secret": client_secret}
        if expires_at:
            response["client_secret_expires_at"] = expires_at
        return response

    monkeypatch.setattr(service, "_post_json", fake_post_json)
    return calls


# ---------------------------------------------------------------------------
# (a) A client registered in one "process" is resolvable in the next one.
# ---------------------------------------------------------------------------

def test_registration_survives_a_process_restart(monkeypatch) -> None:
    """THE core regression test. Register (process 1), wipe the in-process
    cache (process 2 = the callback landing on a fresh worker), resolve again:
    the same client_id must come back and NO second registration may happen —
    a second registration is precisely what invalidates the pending auth
    code."""
    _clear_env(monkeypatch)
    monkeypatch.setattr(service, "_DYNAMIC_CLIENT_CACHE", {})
    calls = _stub_registration(monkeypatch, client_id="client-A", client_secret="secret-A")

    # --- process 1: start_oauth's client resolution ---
    first_id, first_secret = service._resolve_oauth_client("linear", _REDIRECT_URI)
    assert (first_id, first_secret) == ("client-A", "secret-A")
    assert len(calls) == 1

    # --- process restart: the in-process dict is gone ---
    monkeypatch.setattr(service, "_DYNAMIC_CLIENT_CACHE", {})

    # --- process 2: the OAuth callback's token exchange ---
    second_id, second_secret = service._resolve_oauth_client("linear", _REDIRECT_URI)
    assert (second_id, second_secret) == ("client-A", "secret-A")
    assert len(calls) == 1, "a restart must not mint a second OAuth client"


def test_restart_recovery_repopulates_the_in_process_cache(monkeypatch) -> None:
    """The dict stays a read-through cache: after the vault serves a restart,
    the entry is back in memory so the next call costs no round trip."""
    _clear_env(monkeypatch)
    monkeypatch.setattr(service, "_DYNAMIC_CLIENT_CACHE", {})
    _stub_registration(monkeypatch, client_id="client-A", client_secret="secret-A")
    service._resolve_oauth_client("linear", _REDIRECT_URI)

    monkeypatch.setattr(service, "_DYNAMIC_CLIENT_CACHE", {})
    service._resolve_oauth_client("linear", _REDIRECT_URI)

    cached = service._DYNAMIC_CLIENT_CACHE[("linear", _REDIRECT_URI)]
    assert cached.client_id == "client-A"


def test_a_different_redirect_uri_gets_its_own_registration(monkeypatch) -> None:
    """A registration is bound to the redirect_uris declared at registration
    time, so a different origin must not reuse another origin's client."""
    _clear_env(monkeypatch)
    monkeypatch.setattr(service, "_DYNAMIC_CLIENT_CACHE", {})
    calls = _stub_registration(monkeypatch, client_id="client-A", client_secret="secret-A")

    service._resolve_oauth_client("linear", _REDIRECT_URI)
    monkeypatch.setattr(service, "_DYNAMIC_CLIENT_CACHE", {})
    service._resolve_oauth_client("linear", "https://other.example.com/api/connections/oauth/linear/callback")

    assert len(calls) == 2


def test_storage_failure_degrades_instead_of_breaking_oauth(monkeypatch) -> None:
    """If the vault is unreachable the connect must still work (it just isn't
    restart-proof) — a storage outage must never become an OAuth outage."""
    _clear_env(monkeypatch)
    monkeypatch.setattr(service, "_DYNAMIC_CLIENT_CACHE", {})
    _stub_registration(monkeypatch, client_id="client-A", client_secret="secret-A")

    def boom(*_args, **_kwargs):
        raise RuntimeError("vault down")

    monkeypatch.setattr(store, "load", boom)
    monkeypatch.setattr(store, "save", boom)

    client_id, client_secret = service._resolve_oauth_client("linear", _REDIRECT_URI)
    assert (client_id, client_secret) == ("client-A", "secret-A")


# ---------------------------------------------------------------------------
# (b) The background refresh path recovers instead of dying forever.
# ---------------------------------------------------------------------------

def test_refresh_resolves_a_stored_client_after_a_restart(monkeypatch) -> None:
    """Background token refresh has no live Request. Before MAN-124 it could
    only read the in-process dict, so after a restart every DCR connection was
    unrefreshable and the user had to reconnect by hand."""
    _clear_env(monkeypatch)
    monkeypatch.setattr(service, "_DYNAMIC_CLIENT_CACHE", {})
    calls = _stub_registration(monkeypatch, client_id="client-A", client_secret="secret-A")
    service._resolve_oauth_client("linear", _REDIRECT_URI)

    monkeypatch.setattr(service, "_DYNAMIC_CLIENT_CACHE", {})

    client_id, client_secret = service._resolve_oauth_client_for_refresh("linear")
    assert (client_id, client_secret) == ("client-A", "secret-A")
    assert len(calls) == 1


def test_refresh_reregisters_when_the_stored_secret_expired(monkeypatch) -> None:
    """Linear's DCR client_secret expires after 24h (live-verified
    2026-07-19). The stored registration keeps its redirect_uri, which is what
    makes re-registration possible from a path that has no Request."""
    _clear_env(monkeypatch)
    monkeypatch.setattr(service, "_DYNAMIC_CLIENT_CACHE", {})

    store.save(
        provider="linear",
        redirect_uri=_REDIRECT_URI,
        client_id="expired-client",
        client_secret="expired-secret",
        client_secret_expires_at=int(time.time()) - 60,
    )

    calls = _stub_registration(monkeypatch, client_id="client-B", client_secret="secret-B")

    client_id, client_secret = service._resolve_oauth_client_for_refresh("linear")

    assert (client_id, client_secret) == ("client-B", "secret-B")
    assert len(calls) == 1
    assert calls[0][1]["redirect_uris"] == [_REDIRECT_URI]

    # ...and the replacement is itself persisted, so the next restart is fine.
    stored = store.load("linear", _REDIRECT_URI)
    assert stored["client_id"] == "client-B"


def test_refresh_reregisters_when_nothing_is_stored_but_cache_knows_the_uri(monkeypatch) -> None:
    """Covers the deploy in which existing connections predate persistence:
    an in-memory entry exists, the vault has nothing. The redirect_uri from
    memory is still enough to recover."""
    _clear_env(monkeypatch)
    monkeypatch.setattr(service, "_DYNAMIC_CLIENT_CACHE", {})
    service._DYNAMIC_CLIENT_CACHE[("linear", _REDIRECT_URI)] = service._DynamicClientRegistration(
        client_id="legacy-client",
        client_secret="legacy-secret",
        client_secret_expires_at=int(time.time()) - 1,
    )
    calls = _stub_registration(monkeypatch, client_id="client-C", client_secret="secret-C")

    client_id, _secret = service._resolve_oauth_client_for_refresh("linear")

    assert client_id == "client-C"
    assert len(calls) == 1


def test_refresh_prefers_a_live_stored_client_over_reregistering(monkeypatch) -> None:
    _clear_env(monkeypatch)
    monkeypatch.setattr(service, "_DYNAMIC_CLIENT_CACHE", {})
    store.save(
        provider="linear",
        redirect_uri=_REDIRECT_URI,
        client_id="live-client",
        client_secret="live-secret",
        client_secret_expires_at=int(time.time()) + 86400,
    )

    def fail_post_json(*_args, **_kwargs):
        raise AssertionError("must not re-register while a live client is stored")

    monkeypatch.setattr(service, "_post_json", fail_post_json)

    assert service._resolve_oauth_client_for_refresh("linear") == ("live-client", "live-secret")


def test_refresh_still_raises_for_a_provider_with_no_dcr_support(monkeypatch) -> None:
    """Zoom advertises no registration_endpoint — it must keep failing with
    the ordinary 'not configured' error rather than falling into any of the
    new recovery paths."""
    monkeypatch.delenv("ZOOM_CLIENT_ID", raising=False)
    monkeypatch.delenv("ZOOM_CLIENT_SECRET", raising=False)
    with pytest.raises(HTTPException):
        service._resolve_oauth_client_for_refresh("zoom")


# ---------------------------------------------------------------------------
# (c) The client_secret is stored ENCRYPTED, never in the clear.
# ---------------------------------------------------------------------------

def test_client_secret_is_encrypted_at_rest(monkeypatch, dcr_client_vault) -> None:
    _clear_env(monkeypatch)
    monkeypatch.setattr(service, "_DYNAMIC_CLIENT_CACHE", {})
    _stub_registration(monkeypatch, client_id="client-A", client_secret="super-secret-value")
    service._resolve_oauth_client("linear", _REDIRECT_URI)

    row_id = store.credential_id("linear", _REDIRECT_URI)
    row = dcr_client_vault.rows[row_id]

    # The whole row, serialized, must not contain the plaintext secret
    # anywhere — not in the ciphertext field, and not in plaintext metadata.
    assert "super-secret-value" not in json.dumps(row)
    assert "super-secret-value" not in row["encrypted_secret"]

    # ...and it is genuinely recoverable by decrypting, not by reading a field.
    decrypted = json.loads(dcr_client_vault._openssl_decrypt(row["encrypted_secret"]))
    assert decrypted["client_secret"] == "super-secret-value"


def test_metadata_carries_only_non_secret_bookkeeping(monkeypatch, dcr_client_vault) -> None:
    """metadata is plaintext JSONB in Postgres, so it may hold the client_id
    and the redirect_uri (both public) but never the client_secret."""
    _clear_env(monkeypatch)
    monkeypatch.setattr(service, "_DYNAMIC_CLIENT_CACHE", {})
    _stub_registration(monkeypatch, client_id="client-A", client_secret="super-secret-value")
    service._resolve_oauth_client("linear", _REDIRECT_URI)

    row = dcr_client_vault.rows[store.credential_id("linear", _REDIRECT_URI)]
    assert "client_secret" not in row["metadata"]
    assert row["metadata"]["client_id"] == "client-A"
    assert row["metadata"]["redirect_uri"] == _REDIRECT_URI


def test_rows_use_a_sentinel_provider_so_they_never_pose_as_user_credentials(
    monkeypatch, dcr_client_vault
) -> None:
    """These platform-owned rows must not be discoverable by any resolver that
    looks a credential up by provider name (resolve_default_vault_credential),
    or a DCR client would be handed out as if it were the user's Linear
    connection."""
    _clear_env(monkeypatch)
    monkeypatch.setattr(service, "_DYNAMIC_CLIENT_CACHE", {})
    _stub_registration(monkeypatch, client_id="client-A", client_secret="secret-A")
    service._resolve_oauth_client("linear", _REDIRECT_URI)

    row = dcr_client_vault.rows[store.credential_id("linear", _REDIRECT_URI)]
    assert row["provider"] == store.INTERNAL_DCR_PROVIDER
    assert row["provider"] != "linear"
    assert row["platform_scoped"] is True


def test_credential_id_is_deterministic_across_processes() -> None:
    """Two workers racing the same registration must converge on ONE row
    rather than accumulating duplicates."""
    assert store.credential_id("linear", _REDIRECT_URI) == store.credential_id("linear", _REDIRECT_URI)
    assert store.credential_id("linear", _REDIRECT_URI) != store.credential_id("notion", _REDIRECT_URI)


# ---------------------------------------------------------------------------
# Registration payload: scopes are declared at registration time.
# ---------------------------------------------------------------------------

def test_registration_declares_the_scopes_the_authorize_url_will_request(monkeypatch) -> None:
    """Some authorization servers bind the grantable scope set at
    registration. Omitting `scope` left them with a client that could not ask
    for what our authorize URL asks for."""
    _clear_env(monkeypatch)
    monkeypatch.setattr(service, "_DYNAMIC_CLIENT_CACHE", {})
    calls = _stub_registration(monkeypatch, client_id="client-A", client_secret="secret-A")

    service._resolve_oauth_client("linear", _REDIRECT_URI)

    payload = calls[0][1]
    config = service.OAUTH_PROVIDER_CONFIGS["linear"]
    # RFC 7591 §2: space-delimited, regardless of the provider's authorize-URL
    # separator quirk.
    assert payload["scope"] == " ".join(service._effective_scopes("linear", config))
