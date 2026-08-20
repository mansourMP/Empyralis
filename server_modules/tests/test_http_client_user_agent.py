"""urllib's default User-Agent gets us banned by Cloudflare, and it presents
as an auth failure.

Observed live 2026-08-20: preflight's Resend liveness check reported
"PLATFORM EMAIL PROVIDER KEY DEAD -- rotate or restore the key" while that
same key sent mail successfully through the product's own httpx path and
through curl. The real response was Cloudflare's `error code: 1010`
("banned your access based on your browser's signature"), returned before
the request reached Resend at all.

The damage was the REMEDIATION: it told an operator to revoke a working
credential to fix a defect in our own HTTP client. A behavioural test
against a live endpoint cannot guard this (it needs network, and the block
is Cloudflare-side), so this asserts the header is set at the seam.
"""

from unittest import mock

from server_modules import runtime_common


def _capture_request(**kwargs):
    """Run http_json_request far enough to build the urllib Request, and
    return it without any network access."""
    captured = {}

    class _FakeRequest:
        def __init__(self, url, data=None, headers=None, method=None):
            captured["headers"] = dict(headers or {})
            captured["url"] = url

    with mock.patch.object(runtime_common.urlrequest, "Request", _FakeRequest), \
         mock.patch.object(runtime_common.egress_policy, "enforce_outbound_request", lambda **_: None), \
         mock.patch.object(runtime_common.urlrequest, "urlopen", side_effect=RuntimeError("no network")):
        try:
            runtime_common.http_json_request(**kwargs)
        except Exception:
            pass
    return captured


def test_user_agent_is_sent_on_every_request():
    captured = _capture_request(url="https://api.example.com/x")
    assert captured["headers"].get("User-Agent") == runtime_common.USER_AGENT


def test_user_agent_is_not_pythons_default():
    # "Python-urllib/..." is the exact signature Cloudflare 1010s.
    assert "urllib" not in runtime_common.USER_AGENT.lower()
    assert runtime_common.USER_AGENT.strip() != ""


def test_user_agent_identifies_the_product_rather_than_impersonating_a_browser():
    # The goal is to stop looking like an unidentified script, never to
    # evade a bot check by pretending to be Chrome.
    ua = runtime_common.USER_AGENT.lower()
    assert "empyralis" in ua
    for browser in ("mozilla", "chrome", "safari", "gecko", "webkit"):
        assert browser not in ua


def test_a_caller_may_still_override_it():
    captured = _capture_request(
        url="https://api.example.com/x", headers={"User-Agent": "Custom/9"}
    )
    assert captured["headers"].get("User-Agent") == "Custom/9"
