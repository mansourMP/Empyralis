"""Secret comparisons must be constant-time, structurally.

Security review 2026-08-13 (sec/auth-session-secrets): every secret
comparison in this codebase goes through `secrets.compare_digest` /
`hmac.compare_digest` -- JWT signatures (`auth._decode_token_payload`),
password hashes (`auth._verify_password`), the `ORION_API_KEY` bearer check
(`auth.get_current_user`), every webhook/HMAC verifier in
`connectors/*.py`, `billing_service.py`, `secrets_broker.py`,
`tool_broker.py`, `mcp_oauth_provider.py`, `mini_app*_service.py`, and
`control_plane_repository.verify_workspace_invite_token` -- except two,
found by sweeping every secret/token comparison in the auth path:

  * `auth.refresh_authenticated_session` compared the caller-derived
    refresh-token digest against the stored one with a plain `!=`.
  * `routes_auth._validate_platform_invite_code` compared the caller-
    supplied signup invite code against `EMPYRALIS_INVITE_CODE` with a
    plain `!=`.

A plain `!=`/`==` on a secret value is a timing side channel: CPython's
string comparison short-circuits on the first mismatched byte, so an
attacker who can measure response latency across many attempts can recover
the correct value byte-by-byte without ever needing to know it. This test
guards the fix STRUCTURALLY (an AST scan for a raw `Compare` against the
secret-holding variable), not behaviourally -- a behavioural test would
pass identically whether the comparison is constant-time or not, since both
give the same true/false answer on a given input. Only the shape of the
comparison distinguishes them, per this codebase's own established pattern
for exactly this class of defect (see `test_run_state_scope_fails_closed.py`
et al.).

Fails on the pre-fix code: both functions contained a raw
`Compare(ops=[NotEq])` node whose operand was the secret-holding name, and
neither called `secrets.compare_digest` at all.
"""

from __future__ import annotations

import ast
import inspect
import unittest


class _NoRawSecretCompareMixin:
    def _assert_constant_time(self, func, *, banned_names: set[str]) -> None:
        source = inspect.getsource(func)
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Compare):
                continue
            operands = [node.left, *node.comparators]
            hit_names = {n.id for n in operands if isinstance(n, ast.Name)} & banned_names
            if hit_names:
                ops = [type(op).__name__ for op in node.ops]
                self.fail(  # type: ignore[attr-defined]
                    f"{func.__qualname__} compares {sorted(hit_names)} with a raw "
                    f"{ops} operator -- use secrets.compare_digest() for a secret "
                    "comparison, never == or !=."
                )
        self.assertIn(  # type: ignore[attr-defined]
            "secrets.compare_digest",
            source,
            f"{func.__qualname__} no longer calls secrets.compare_digest at all.",
        )


class RefreshTokenHashConstantTimeCompareTests(_NoRawSecretCompareMixin, unittest.TestCase):
    def test_refresh_authenticated_session_uses_constant_time_compare(self):
        from server_modules import auth

        self._assert_constant_time(
            auth.refresh_authenticated_session,
            banned_names={"expected_hash"},
        )


class InviteCodeGateConstantTimeCompareTests(_NoRawSecretCompareMixin, unittest.TestCase):
    def test_validate_platform_invite_code_uses_constant_time_compare(self):
        from server_modules import routes_auth

        self._assert_constant_time(
            routes_auth._validate_platform_invite_code,
            banned_names={"required_code"},
        )


if __name__ == "__main__":
    unittest.main()


class InviteCodeNonAsciiTests(unittest.TestCase):
    """The constant-time fix must not turn a typo into a 500.

    `secrets.compare_digest`'s STR form accepts ASCII only and raises
    TypeError on anything else. `_validate_platform_invite_code` compares raw
    user input from the signup form, so the first pass at the constant-time
    fix — comparing the two as `str` — meant an invite code containing an
    accent, a Cyrillic letter or an emoji raised TypeError out of the route
    instead of the clean 403 the function exists to return. Comparing as
    bytes has no such limit. A behavioural test, because the AST check above
    is satisfied by either form.
    """

    def test_non_ascii_code_is_rejected_not_a_crash(self):
        import os
        from unittest import mock

        from fastapi import HTTPException

        from server_modules import routes_auth

        with mock.patch.dict(os.environ, {"EMPYRALIS_INVITE_CODE": "letmein"}):
            for candidate in ("café", "привет", "🔑", "letmein!"):
                with self.subTest(candidate=candidate):
                    with self.assertRaises(HTTPException) as caught:
                        routes_auth._validate_platform_invite_code(candidate)
                    self.assertEqual(caught.exception.status_code, 403)

    def test_the_correct_code_still_passes(self):
        import os
        from unittest import mock

        from server_modules import routes_auth

        with mock.patch.dict(os.environ, {"EMPYRALIS_INVITE_CODE": "letmein"}):
            routes_auth._validate_platform_invite_code("letmein")
            routes_auth._validate_platform_invite_code("  letmein  ")

    def test_unicode_whitespace_is_stripped_like_any_other_whitespace(self):
        """Documents a real behaviour this test file tripped over.

        `str.strip()` treats U+00A0 NO-BREAK SPACE as whitespace, so a code
        pasted out of a rich-text email as "letmein\xa0" is accepted. That is
        the pre-existing `.strip()` contract, not something the constant-time
        change introduced, and it only ever widens by whitespace — but it is
        surprising enough to pin rather than rediscover.
        """
        import os
        from unittest import mock

        from server_modules import routes_auth

        with mock.patch.dict(os.environ, {"EMPYRALIS_INVITE_CODE": "letmein"}):
            routes_auth._validate_platform_invite_code("letmein\xa0")
