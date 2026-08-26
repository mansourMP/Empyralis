"""Native app login handoff: authorization-code + PKCE.

The problem: a native app (today, iOS) opens the real Empyralis website in an
in-app browser so every web login method (email/password, Google, whatever
comes later) works for free, with no native OAuth client of its own. Once
that web login succeeds, the app needs a real Empyralis session back --
without a bearer token ever appearing in a redirect URL, which is a logging /
browser-history / screen-recording leak (see this module's own tests for the
exact shape).

The fix is the standard authorization-code + PKCE dance, RFC 7636:

    app  -->  https://empyralis.ai/login?native=ios&code_challenge=<S256>&state=<r>
    web  -->  POST /auth/native/handoff   (authenticated by the browser's own
                                            session cookie -- this endpoint
                                            answers "who just logged in",
                                            never "log this person in")
              <-- {code, redirect_uri: "empyralis://auth", expires_in}
    web  -->  window.location.replace(`${redirect_uri}?code=${code}&state=${state}`)
    app  <--  the OS delivers the empyralis:// URL back to the app
    app  -->  POST /auth/native/exchange  {code, code_verifier}
              <-- the same payload a channel="mobile" login returns
                  (bearer token + session_recovery.refresh_token)

Three deliberate design choices, all load-bearing:

1. THE CODE IS NEVER A SESSION. It is an opaque, single-use, 90-second
   ticket that names a user_id and a code_challenge -- nothing else. The
   actual mobile session (the real bearer token, the 180-day refresh token)
   is minted fresh at EXCHANGE time, by calling the exact same
   `auth._login_payload_for_user(channel="mobile", ...)` every other mobile
   login path calls. A leaked code is worthless without the code_verifier
   the app generated and never transmitted anywhere until the exchange call.

2. THE REDIRECT TARGET IS HARDCODED SERVER-SIDE, NEVER CALLER-SUPPLIED.
   `NATIVE_REDIRECT_TARGETS` is the one and only place a scheme is named; the
   caller picks a KEY ("ios"), never a URL. A parameterised redirect_uri
   would turn this endpoint into an open redirect on the one page an
   attacker can most easily get a victim to open (a "log in" link).

3. THE CODE IS CONSUMED BEFORE THE VERIFIER IS CHECKED, under the same
   process-wide `auth.AUTH_LOCK` every other write to this SQLite file
   already takes (auth_session_refresh_tokens, channel_pairing_intents --
   this module follows that exact precedent rather than inventing a new
   storage shape or a Postgres-mirrored table: a native-login code is
   shorter-lived and lower-stakes than a channel pairing intent, which
   already lives SQLite-only). So a code can be PRESENTED exactly once, full
   stop -- a wrong verifier on that one attempt burns the code exactly like
   a correct one would. There is no way to brute-force the verifier against
   a still-live code, because there is never a second attempt to brute-force
   with.

Storage is the SAME auth SQLite file every session/refresh-token/pairing
write already uses (`auth._connect_auth_db()`), a table this module owns and
creates lazily (`_ensure_tables_locked`), exactly like
`channel_pairing_service.py`'s `channel_pairing_intents` table. On the
single-VPS production deployment this file is durable local storage, not
in-memory state that a restart would drop mid-handoff.
"""

from __future__ import annotations

import hashlib
import hmac
import re
import secrets
import sqlite3
import time
from typing import Any, Dict, Optional

from fastapi import HTTPException

from server_modules import auth as auth_module
from server_modules import security_audit_service


# 90s: long enough to survive a real ASWebAuthenticationSession round trip --
# an OS-level custom-URL-scheme handoff, not a network request -- including a
# slow TLS handshake on a bad connection; short enough that a code sitting in
# Safari's address-bar history or a screen recording is worthless by the time
# anyone could act on it. `channel_pairing_service.py`'s pairing codes get 15
# minutes because a person has to read and type them; this code is only ever
# handled programmatically, by exactly one redirect, so it does not need
# anywhere near that long to live.
NATIVE_AUTH_CODE_TTL_SECONDS = 90

# RFC 7636 SS4.1/4.2: a code_verifier is 43-128 chars from the unreserved set
# [A-Za-z0-9-._~]; a code_challenge (S256) is base64url(SHA-256(verifier)),
# unpadded, which is always exactly 43 characters.
_CODE_CHALLENGE_LENGTH = 43
_CODE_CHALLENGE_RE = re.compile(rf"^[A-Za-z0-9_-]{{{_CODE_CHALLENGE_LENGTH}}}$")
_CODE_VERIFIER_RE = re.compile(r"^[A-Za-z0-9._~-]{43,128}$")

_GENERIC_EXCHANGE_FAILURE = "This sign-in code is invalid, expired, or already used."

# The ONLY permitted native redirect targets -- see this module's own header
# comment, point 2. Adding a target (e.g. a future Android app) is adding one
# entry here; it is never expressed as a caller-supplied URL.
NATIVE_REDIRECT_TARGETS: Dict[str, str] = {
    "ios": "empyralis://auth",
}


def _now() -> int:
    return int(time.time())


def _connect() -> sqlite3.Connection:
    return auth_module._connect_auth_db()


def _ensure_tables_locked(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS native_auth_handoff_codes (
            code_hash TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            code_challenge TEXT NOT NULL,
            native_target TEXT NOT NULL,
            state TEXT,
            created_at INTEGER NOT NULL,
            expires_at INTEGER NOT NULL,
            consumed_at INTEGER
        )
        """
    )
    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_native_auth_handoff_codes_expiry
        ON native_auth_handoff_codes(expires_at)
        """
    )


def _hash_native_auth_code(code: str) -> str:
    # Same construction as auth._hash_auth_session_refresh_secret /
    # channel_pairing_service._hash_pairing_code: HMAC-SHA256 keyed on the
    # server's own JWT secret, so the stored value is useless to anyone
    # without that secret even if the SQLite file itself leaked. The raw
    # code is never stored anywhere -- only this hash is.
    digest = hmac.new(
        auth_module._jwt_secret().encode("utf-8"),
        f"native-auth-code:{str(code or '').strip()}".encode("utf-8"),
        hashlib.sha256,
    ).digest()
    return auth_module._b64url_encode(digest)


def _compute_code_challenge(code_verifier: str) -> str:
    digest = hashlib.sha256(code_verifier.encode("utf-8")).digest()
    return auth_module._b64url_encode(digest)


def _validate_native_target(value: Any) -> str:
    token = str(value or "").strip().lower()
    if token not in NATIVE_REDIRECT_TARGETS:
        raise HTTPException(status_code=400, detail="Unrecognized native handoff target.")
    return token


def _validate_code_challenge(value: Any) -> str:
    token = str(value or "").strip()
    if not _CODE_CHALLENGE_RE.match(token):
        raise HTTPException(
            status_code=400,
            detail="code_challenge must be a base64url-encoded SHA-256 value (RFC 7636 S256).",
        )
    return token


def _validate_code_challenge_method(value: Any) -> None:
    token = str(value or "S256").strip()
    if token != "S256":
        raise HTTPException(status_code=400, detail="Only the S256 code_challenge_method is supported.")


def mint_native_auth_handoff(
    current_user: Optional[Dict[str, Any]],
    *,
    native: Any,
    code_challenge: Any,
    code_challenge_method: Any = "S256",
    state: Any = None,
) -> Dict[str, Any]:
    """Mint a single-use handoff code for the user CURRENTLY authenticated in
    this browser session (via `get_current_user`, cookie or bearer). This
    never authenticates anyone -- it only answers "who is already logged in
    here", the same authority any other authenticated GET on this session
    already has. Raises 401 via `auth._current_bearer_user_id` if the caller
    is not a real bearer-authenticated session (the local-dev/no-auth and
    X-API-Key service branches of `get_current_user` are deliberately
    excluded -- this is a real person's browser session or nothing)."""
    user_id = auth_module._current_bearer_user_id(current_user)
    resolved_target = _validate_native_target(native)
    resolved_challenge = _validate_code_challenge(code_challenge)
    _validate_code_challenge_method(code_challenge_method)
    clean_state = str(state or "").strip()[:256] or None

    raw_code = secrets.token_urlsafe(32)
    code_hash = _hash_native_auth_code(raw_code)
    created_at = _now()
    expires_at = created_at + NATIVE_AUTH_CODE_TTL_SECONDS

    with auth_module.AUTH_LOCK:
        with _connect() as connection:
            _ensure_tables_locked(connection)
            connection.execute(
                """
                INSERT INTO native_auth_handoff_codes (
                    code_hash, user_id, code_challenge, native_target, state,
                    created_at, expires_at, consumed_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, NULL)
                """,
                (
                    code_hash,
                    user_id,
                    resolved_challenge,
                    resolved_target,
                    clean_state,
                    created_at,
                    expires_at,
                ),
            )
            connection.commit()

    security_audit_service.emit_security_audit_event(
        action="auth.native.handoff_mint",
        current_user=current_user,
        actor_user_id=user_id,
        actor_email=str((current_user or {}).get("email") or "").strip().lower() or None,
        actor_auth_type="bearer",
        channel=resolved_target,
        idempotency_key=f"auth.native.handoff_mint:{code_hash}",
        metadata={"native_target": resolved_target, "expires_at": expires_at},
    )

    return {
        "code": raw_code,
        "redirect_uri": NATIVE_REDIRECT_TARGETS[resolved_target],
        "expires_in": NATIVE_AUTH_CODE_TTL_SECONDS,
    }


def exchange_native_auth_code(
    *,
    code: Any,
    code_verifier: Any,
    device_id: Optional[str] = None,
    device_name: Optional[str] = None,
    device_platform: Optional[str] = None,
    workspace_id: Optional[str] = None,
    session_ttl_seconds: Optional[int] = None,
) -> Dict[str, Any]:
    """Exchange a handoff code for a real mobile session. Deliberately takes
    NO user identity of any kind from the caller -- the only source of "who
    this is" is the row the code names, which the caller cannot influence
    (it was written server-side at mint time). That is what makes it
    structurally impossible to exchange a code for a different user's
    session: there is no field to smuggle a different user_id into."""
    clean_code = str(code or "").strip()
    if not clean_code:
        raise HTTPException(status_code=401, detail=_GENERIC_EXCHANGE_FAILURE)

    clean_verifier = str(code_verifier or "").strip()
    if not _CODE_VERIFIER_RE.match(clean_verifier):
        # Malformed verifier never reaches the atomic consume step below, so
        # a client-side bug that sends garbage once does not burn an
        # otherwise-good code. This is the ONLY path that does not consume
        # the code; every well-formed attempt (right or wrong verifier) does.
        raise HTTPException(status_code=401, detail=_GENERIC_EXCHANGE_FAILURE)

    code_hash = _hash_native_auth_code(clean_code)
    now_ts = _now()

    # Single-use is enforced by this UPDATE alone, and by nothing else: it
    # marks the row consumed BEFORE the verifier is checked, inside the same
    # AUTH_LOCK every other writer to this file takes -- see this module's
    # own header comment, point 3, for why that ordering is deliberate.
    with auth_module.AUTH_LOCK:
        with _connect() as connection:
            _ensure_tables_locked(connection)
            cursor = connection.execute(
                """
                UPDATE native_auth_handoff_codes
                SET consumed_at = ?
                WHERE code_hash = ? AND consumed_at IS NULL AND expires_at > ?
                """,
                (now_ts, code_hash, now_ts),
            )
            consumed = cursor.rowcount == 1
            row = None
            if consumed:
                row = connection.execute(
                    "SELECT * FROM native_auth_handoff_codes WHERE code_hash = ? LIMIT 1",
                    (code_hash,),
                ).fetchone()
            connection.commit()

    if not consumed or row is None:
        raise HTTPException(status_code=401, detail=_GENERIC_EXCHANGE_FAILURE)

    stored_challenge = str(row["code_challenge"] or "").strip()
    computed_challenge = _compute_code_challenge(clean_verifier)
    # Constant-time, matching every other secret comparison in this codebase
    # (auth._verify_password, auth.refresh_authenticated_session, ...) --
    # both sides are short fixed-shape base64url strings, but there is no
    # reason to be the one comparison in the file that uses `!=`.
    if not secrets.compare_digest(stored_challenge, computed_challenge):
        security_audit_service.emit_security_audit_event(
            action="auth.native.handoff_exchange_failed",
            status="failure",
            actor_user_id=str(row["user_id"] or "").strip() or None,
            idempotency_key=f"auth.native.handoff_exchange:{code_hash}",
            metadata={"reason": "code_verifier_mismatch"},
        )
        raise HTTPException(status_code=401, detail=_GENERIC_EXCHANGE_FAILURE)

    user_id = str(row["user_id"] or "").strip()
    user = auth_module._find_user_by_id(user_id)
    if user is None:
        # The account was deleted between mint and exchange. The code is
        # already consumed above; nothing left to do but refuse.
        raise HTTPException(status_code=401, detail=_GENERIC_EXCHANGE_FAILURE)

    payload = auth_module._login_payload_for_user(
        user,
        channel="mobile",
        device_id=device_id,
        device_name=device_name,
        device_platform=device_platform,
        workspace_id=workspace_id,
        session_ttl_seconds=session_ttl_seconds,
    )
    security_audit_service.emit_security_audit_event(
        action="auth.native.handoff_exchange",
        actor_user_id=user_id,
        actor_email=str(user.get("email") or "").strip().lower() or None,
        actor_auth_type="bearer",
        channel="mobile",
        idempotency_key=f"auth.native.handoff_exchange:{code_hash}",
        metadata={"device_id": str(device_id or "").strip() or None},
    )
    return payload
