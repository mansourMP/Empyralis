"""native_auth_service.py -- the authorization-code + PKCE handoff a native
app uses to turn "the browser just logged in" into a real mobile session,
without a bearer token ever appearing in a redirect URL. See that module's
own header comment for the full design.

Isolation follows test_channel_pairing.py's own `_isolated_modules` pattern
exactly (own EMPYRALIS_STATE_HOME under tmp_path, reloaded modules) -- this
module writes to the same auth SQLite file `channel_pairing_intents` and
`auth_session_refresh_tokens` already share, so it needs the identical
isolation or it pollutes (and is polluted by) whichever test ran last.
"""

from __future__ import annotations

import base64
import hashlib
import importlib
import os
import secrets
import time
from contextlib import contextmanager

import pytest
from fastapi import HTTPException


class _Request:
    headers = {}
    client = None


def _pkce_pair() -> tuple[str, str]:
    """A code_verifier/code_challenge pair computed independently of
    native_auth_service's own _compute_code_challenge, so a bug in that
    function's encoding would actually be caught rather than the test and
    the code agreeing with each other by construction."""
    verifier = secrets.token_urlsafe(64)[:100]
    digest = hashlib.sha256(verifier.encode("utf-8")).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("utf-8")
    return verifier, challenge


@contextmanager
def _isolated_modules(monkeypatch: pytest.MonkeyPatch, tmp_path):
    tracked_keys = (
        "EMPYRALIS_STATE_HOME",
        "EMPYRALIS_JWT_SECRET_FILE",
        "DATABASE_URL",
        "ORION_JWT_SECRET",
        "JWT_SECRET",
        "ORION_API_KEY",
        "RUNTIME_KEY",
    )
    original_env = {key: os.environ.get(key) for key in tracked_keys}
    state_home = tmp_path / "state"
    monkeypatch.setenv("EMPYRALIS_STATE_HOME", str(state_home))
    monkeypatch.delenv("EMPYRALIS_JWT_SECRET_FILE", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    for key in ("ORION_JWT_SECRET", "JWT_SECRET", "ORION_API_KEY", "RUNTIME_KEY"):
        monkeypatch.delenv(key, raising=False)

    db_module = importlib.import_module("server_modules.db")
    control_plane_repository_module = importlib.import_module("server_modules.control_plane_repository")
    jwt_secret_module = importlib.import_module("server_modules.jwt_secret")
    auth_module = importlib.import_module("server_modules.auth")
    native_auth_module = importlib.import_module("server_modules.native_auth_service")

    runtime_db = importlib.reload(db_module)
    runtime_db._POOLS_BY_LOOP.clear()
    runtime_db._ENV_DSN_LOADED = True
    importlib.reload(control_plane_repository_module)
    importlib.reload(jwt_secret_module)
    auth = importlib.reload(auth_module)
    native_auth = importlib.reload(native_auth_module)
    auth.USER_RATE_LIMIT_BUCKETS.clear()
    auth.LOGIN_RATE_LIMIT_BUCKETS.clear()
    try:
        yield auth, native_auth
    finally:
        for key, value in original_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        db_module = importlib.import_module("server_modules.db")
        control_plane_repository_module = importlib.import_module("server_modules.control_plane_repository")
        jwt_secret_module = importlib.import_module("server_modules.jwt_secret")
        auth_module = importlib.import_module("server_modules.auth")
        native_auth_module = importlib.import_module("server_modules.native_auth_service")
        runtime_db = importlib.reload(db_module)
        runtime_db._POOLS_BY_LOOP.clear()
        importlib.reload(jwt_secret_module)
        importlib.reload(control_plane_repository_module)
        importlib.reload(auth_module)
        importlib.reload(native_auth_module)


def _current_user(auth, token: str):
    return auth.get_current_user(_Request(), authorization=f"Bearer {token}")


def _register_and_login(auth, email: str, name: str):
    created = auth.register_user(email, "password-123", name=name)
    current_user = _current_user(auth, created["token"])
    return created, current_user


def _mint(native_auth, current_user, *, challenge: str, native: str = "ios", state: str = "s-1"):
    return native_auth.mint_native_auth_handoff(
        current_user,
        native=native,
        code_challenge=challenge,
        state=state,
    )


def test_happy_path_exchange_returns_a_real_mobile_session(monkeypatch: pytest.MonkeyPatch, tmp_path):
    with _isolated_modules(monkeypatch, tmp_path) as (auth, native_auth):
        created, current_user = _register_and_login(auth, "happy@example.com", "Happy Path")
        verifier, challenge = _pkce_pair()

        handoff = _mint(native_auth, current_user, challenge=challenge)
        assert handoff["redirect_uri"] == "empyralis://auth"
        assert handoff["code"] and handoff["code"] != challenge

        payload = native_auth.exchange_native_auth_code(
            code=handoff["code"],
            code_verifier=verifier,
            device_id="device-1",
            device_name="iPhone",
            device_platform="ios",
        )
        assert payload["ok"] is True
        assert payload["user"]["id"] == created["user"]["id"]
        assert payload["token"]
        # channel="mobile" always mints a real refresh token (see
        # auth._issue_authenticated_user_payload's own `normalized_channel
        # == "mobile"` branch) -- this is the whole point of routing the
        # exchange through _login_payload_for_user(channel="mobile") rather
        # than hand-rolling a token here.
        assert payload["session_recovery"]["refresh_token"].startswith("esr_")


def test_redirect_target_is_hardcoded_server_side(monkeypatch: pytest.MonkeyPatch, tmp_path):
    with _isolated_modules(monkeypatch, tmp_path) as (auth, native_auth):
        _created, current_user = _register_and_login(auth, "redirect@example.com", "Redirect Target")
        _verifier, challenge = _pkce_pair()

        # A caller cannot name its own redirect_uri -- only a key into the
        # server's own table. An unrecognized key is refused outright.
        with pytest.raises(HTTPException) as exc_info:
            native_auth.mint_native_auth_handoff(
                current_user,
                native="https://evil.example/steal",
                code_challenge=challenge,
            )
        assert exc_info.value.status_code == 400

        # The one real target resolves to the one hardcoded URL, verbatim.
        handoff = _mint(native_auth, current_user, challenge=challenge)
        assert handoff["redirect_uri"] == native_auth.NATIVE_REDIRECT_TARGETS["ios"]


def test_code_is_single_use_second_exchange_fails_and_mints_no_new_session(
    monkeypatch: pytest.MonkeyPatch, tmp_path
):
    with _isolated_modules(monkeypatch, tmp_path) as (auth, native_auth):
        _created, current_user = _register_and_login(auth, "single-use@example.com", "Single Use")
        verifier, challenge = _pkce_pair()
        handoff = _mint(native_auth, current_user, challenge=challenge)

        # Call-count assertion, not just a truthy check (CLAUDE.md: "a test
        # asserting an absence must also assert the call count, or it
        # cannot tell 'nothing happened' from 'something else happened'").
        # If the second exchange somehow slipped past the single-use guard,
        # this counter -- not just the HTTP-shaped outcome -- would catch it.
        call_count = {"n": 0}
        real_login_payload_for_user = auth._login_payload_for_user

        def _counting_login_payload_for_user(*args, **kwargs):
            call_count["n"] += 1
            return real_login_payload_for_user(*args, **kwargs)

        monkeypatch.setattr(auth, "_login_payload_for_user", _counting_login_payload_for_user)

        first = native_auth.exchange_native_auth_code(code=handoff["code"], code_verifier=verifier)
        assert first["ok"] is True
        assert call_count["n"] == 1

        with pytest.raises(HTTPException) as exc_info:
            native_auth.exchange_native_auth_code(code=handoff["code"], code_verifier=verifier)
        assert exc_info.value.status_code == 401
        # The underlying session-minting function was never reached a
        # second time -- the second call failed BEFORE it could mint
        # anything, not merely returned an error after minting one anyway.
        assert call_count["n"] == 1


def test_expired_code_fails(monkeypatch: pytest.MonkeyPatch, tmp_path):
    with _isolated_modules(monkeypatch, tmp_path) as (auth, native_auth):
        _created, current_user = _register_and_login(auth, "expired@example.com", "Expired Code")
        verifier, challenge = _pkce_pair()
        handoff = _mint(native_auth, current_user, challenge=challenge)

        # Force the row into the past directly -- exercises the real
        # `expires_at > now` predicate in the UPDATE, not a mocked clock.
        code_hash = native_auth._hash_native_auth_code(handoff["code"])
        with native_auth.auth_module.AUTH_LOCK:
            with native_auth._connect() as connection:
                connection.execute(
                    "UPDATE native_auth_handoff_codes SET expires_at = ? WHERE code_hash = ?",
                    (int(time.time()) - 5, code_hash),
                )
                connection.commit()

        with pytest.raises(HTTPException) as exc_info:
            native_auth.exchange_native_auth_code(code=handoff["code"], code_verifier=verifier)
        assert exc_info.value.status_code == 401

        # Confirm this is genuinely the EXPIRY predicate refusing, not the
        # single-use one: the row must still read un-consumed.
        with native_auth._connect() as connection:
            row = connection.execute(
                "SELECT consumed_at FROM native_auth_handoff_codes WHERE code_hash = ?",
                (code_hash,),
            ).fetchone()
        assert row["consumed_at"] is None


def test_wrong_code_verifier_fails_closed_and_still_burns_the_code(monkeypatch: pytest.MonkeyPatch, tmp_path):
    with _isolated_modules(monkeypatch, tmp_path) as (auth, native_auth):
        _created, current_user = _register_and_login(auth, "wrong-verifier@example.com", "Wrong Verifier")
        real_verifier, challenge = _pkce_pair()
        wrong_verifier, _wrong_challenge = _pkce_pair()
        assert wrong_verifier != real_verifier

        handoff = _mint(native_auth, current_user, challenge=challenge)

        with pytest.raises(HTTPException) as exc_info:
            native_auth.exchange_native_auth_code(code=handoff["code"], code_verifier=wrong_verifier)
        assert exc_info.value.status_code == 401

        # The mismatch already consumed the code (native_auth_service's own
        # deliberate "consume before verifying" design, see its header
        # comment point 3) -- so even presenting the CORRECT verifier next
        # must also fail. A verifier cannot be brute-forced against a
        # single still-live code.
        with pytest.raises(HTTPException) as exc_info_retry:
            native_auth.exchange_native_auth_code(code=handoff["code"], code_verifier=real_verifier)
        assert exc_info_retry.value.status_code == 401


def test_malformed_code_verifier_is_refused_without_consuming_the_code(monkeypatch: pytest.MonkeyPatch, tmp_path):
    with _isolated_modules(monkeypatch, tmp_path) as (auth, native_auth):
        _created, current_user = _register_and_login(auth, "malformed@example.com", "Malformed Verifier")
        real_verifier, challenge = _pkce_pair()
        handoff = _mint(native_auth, current_user, challenge=challenge)

        with pytest.raises(HTTPException) as exc_info:
            native_auth.exchange_native_auth_code(code=handoff["code"], code_verifier="too-short")
        assert exc_info.value.status_code == 401

        # A malformed (not merely wrong) verifier is rejected before the
        # atomic consume step, so the code is still good for a real attempt.
        payload = native_auth.exchange_native_auth_code(code=handoff["code"], code_verifier=real_verifier)
        assert payload["ok"] is True


def test_code_minted_for_user_a_cannot_yield_a_session_for_user_b(monkeypatch: pytest.MonkeyPatch, tmp_path):
    with _isolated_modules(monkeypatch, tmp_path) as (auth, native_auth):
        created_a, current_user_a = _register_and_login(auth, "user-a@example.com", "User A")
        created_b, _current_user_b = _register_and_login(auth, "user-b@example.com", "User B")
        assert created_a["user"]["id"] != created_b["user"]["id"]

        verifier, challenge = _pkce_pair()
        # Minted for A's own browser session. The exchange REQUEST carries
        # no user identity field at all (see AuthNativeExchangeRequest /
        # exchange_native_auth_code's own signature) -- there is nothing for
        # user B to pass to claim A's code, so this test documents that
        # structural fact rather than probing for a parameter that does not
        # exist.
        handoff = _mint(native_auth, current_user_a, challenge=challenge)

        payload = native_auth.exchange_native_auth_code(code=handoff["code"], code_verifier=verifier)
        assert payload["user"]["id"] == created_a["user"]["id"]
        assert payload["user"]["id"] != created_b["user"]["id"]


def test_mint_requires_a_real_authenticated_session(monkeypatch: pytest.MonkeyPatch, tmp_path):
    with _isolated_modules(monkeypatch, tmp_path) as (_auth, native_auth):
        _verifier, challenge = _pkce_pair()
        with pytest.raises(HTTPException) as exc_info:
            native_auth.mint_native_auth_handoff(None, native="ios", code_challenge=challenge)
        assert exc_info.value.status_code == 401

        with pytest.raises(HTTPException) as exc_info_service:
            native_auth.mint_native_auth_handoff(
                {"auth_type": "service"}, native="ios", code_challenge=challenge
            )
        assert exc_info_service.value.status_code == 401


def test_malformed_code_challenge_is_refused_at_mint_time(monkeypatch: pytest.MonkeyPatch, tmp_path):
    with _isolated_modules(monkeypatch, tmp_path) as (auth, native_auth):
        _created, current_user = _register_and_login(auth, "malformed-challenge@example.com", "Malformed Challenge")
        with pytest.raises(HTTPException) as exc_info:
            native_auth.mint_native_auth_handoff(current_user, native="ios", code_challenge="not-a-real-challenge")
        assert exc_info.value.status_code == 400
