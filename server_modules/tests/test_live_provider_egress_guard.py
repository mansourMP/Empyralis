"""Tests for the live-provider egress guard in conftest.py.

A guard nobody tests is the "built, tested, and never wired" failure mode
this repo keeps hitting -- except worse, because a silently-broken guard
reads as protection while providing none. These assert the four properties
the guard actually has to hold:

  1. a remote connect is blocked, loudly, naming the destination
  2. loopback still works (local Postgres, the openai_compat_adapter proxy)
  3. a network-egress subprocess (curl, the claude/codex CLIs) is blocked
  4. the explicit opt-in genuinely disarms it

Nothing here opens a real connection: every "blocked" case is asserted by
the exception the guard raises BEFORE the syscall, and the loopback case
connects to a socket this test binds itself.
"""

from __future__ import annotations

import os
import socket
import subprocess

import pytest

# NOT `import conftest`: server_modules/tests/e2e/conftest.py has no
# __init__.py either, so both conftests are imported under the same bare
# top-level name "conftest" and whichever lands in sys.modules last wins --
# which pointed this file at the wrong module during a full-suite run. The
# guard's conftest re-registers itself under this unambiguous alias (see the
# bottom of server_modules/tests/conftest.py) so the arm/disarm state asserted
# here is the same state pytest is actually driving.
import empyralis_tests_egress_guard as tests_conftest

# The whole file describes an ARMED guard. A deliberate session-wide opt-in
# run disarms it on purpose, so there is nothing here to assert -- skip rather
# than report a wall of failures that mean "you asked for this".
pytestmark = pytest.mark.skipif(
    str(os.environ.get(tests_conftest._LIVE_PROVIDER_ALLOW_ENV) or "").strip().lower()
    in {"1", "true", "yes", "on"},
    reason=f"{tests_conftest._LIVE_PROVIDER_ALLOW_ENV} is set -- the guard is deliberately disarmed",
)


class TestRemoteConnectIsBlocked:
    def test_connect_to_a_provider_host_raises(self):
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            with pytest.raises(tests_conftest.LiveProviderCallBlocked) as excinfo:
                sock.connect(("1.2.3.4", 443))
        finally:
            sock.close()
        message = str(excinfo.value)
        assert "1.2.3.4:443" in message
        assert "EMPYRALIS_TEST_ALLOW_LIVE_PROVIDER_CALLS" in message
        # The violation is also recorded, which is what fails the test at
        # teardown even when the code under test swallows the exception.
        assert tests_conftest._LIVE_EGRESS_VIOLATIONS
        del tests_conftest._LIVE_EGRESS_VIOLATIONS[:]

    def test_block_is_not_catchable_as_an_ordinary_exception(self):
        """The whole reason this is a BaseException: the personal-channel and
        runtime paths are full of broad `except Exception:` handlers, and a
        guard those swallow is a guard that converts a live call into a silent
        wrong answer -- exactly what it exists to prevent."""
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        blocked = False
        try:
            try:
                sock.connect(("1.2.3.4", 443))
            except Exception:  # noqa: BLE001 -- deliberately the wrong catch
                pytest.fail("an `except Exception:` handler swallowed the guard")
            except tests_conftest.LiveProviderCallBlocked:
                blocked = True
        finally:
            sock.close()
        assert blocked
        del tests_conftest._LIVE_EGRESS_VIOLATIONS[:]

    def test_dns_resolution_names_the_host_in_the_error(self):
        """getaddrinfo is recorded, not blocked, so the message can say
        'api.example.invalid' instead of an opaque IP."""
        tests_conftest._EGRESS_HOSTNAMES_BY_IP["203.0.113.7"] = "api.example.invalid"
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            with pytest.raises(tests_conftest.LiveProviderCallBlocked) as excinfo:
                sock.connect(("203.0.113.7", 443))
        finally:
            sock.close()
            tests_conftest._EGRESS_HOSTNAMES_BY_IP.pop("203.0.113.7", None)
        assert "api.example.invalid" in str(excinfo.value)
        del tests_conftest._LIVE_EGRESS_VIOLATIONS[:]


class TestLoopbackStillWorks:
    def test_loopback_connect_is_allowed(self):
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        client = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            client.connect(listener.getsockname())  # must NOT raise
        finally:
            client.close()
            listener.close()
        assert not tests_conftest._LIVE_EGRESS_VIOLATIONS


class TestSubprocessEgressIsBlocked:
    def test_curl_is_blocked(self):
        with pytest.raises(tests_conftest.LiveProviderCallBlocked) as excinfo:
            subprocess.run(["curl", "https://api.example.invalid"], capture_output=True)
        assert "'curl'" in str(excinfo.value)
        del tests_conftest._LIVE_EGRESS_VIOLATIONS[:]

    def test_the_claude_cli_is_blocked(self):
        """The Agent SDK bridge spawns the Node `claude` CLI, and all of that
        turn's model HTTP happens inside Node where no Python-level patch can
        see it -- argv is the only place to catch it."""
        with pytest.raises(tests_conftest.LiveProviderCallBlocked):
            subprocess.Popen(["claude", "-p", "hello"])
        del tests_conftest._LIVE_EGRESS_VIOLATIONS[:]

    def test_curl_at_a_loopback_url_is_allowed(self):
        """test_web_tools_ssrf_redirect_guard starts a real ThreadingHTTPServer
        on 127.0.0.1 and drives web_tools' curl fallback at it. A URL argument
        that is demonstrably loopback is not egress."""
        completed = subprocess.run(
            ["curl", "-s", "-o", "/dev/null", "http://127.0.0.1:1/nothing-here"],
            capture_output=True,
        )
        assert completed.returncode != 0  # nothing is listening -- the point is it RAN
        assert not tests_conftest._LIVE_EGRESS_VIOLATIONS

    def test_a_local_cli_capability_probe_is_allowed(self):
        """provider_profiles.claude_code_cli_status runs `claude auth status
        --json` on the availability path every direct-chat test crosses. It is
        a local capability probe, not a billed model call -- blocking it broke
        availability resolution across ~30 tests while catching nothing."""
        assert tests_conftest._argv_is_local_cli_probe(["claude", "auth", "status", "--json"])
        assert not tests_conftest._argv_is_local_cli_probe(["claude", "-p", "hello"])
        assert not tests_conftest._argv_is_local_cli_probe(["codex", "exec", "do the thing"])
        # a probe-looking flag late in an inference command line must not buy
        # the whole invocation a pass
        assert not tests_conftest._argv_is_local_cli_probe(["claude", "-p", "hello", "--help"])

    def test_ordinary_subprocesses_still_run(self):
        """Denylist, not allowlist: this suite legitimately shells out to
        openssl (vault encryption), git, and the compiled runtime kernel."""
        completed = subprocess.run(["echo", "still working"], capture_output=True)
        assert completed.returncode == 0


class TestExplicitOptIn:
    @pytest.mark.live_provider
    def test_marker_disarms_the_guard(self):
        assert tests_conftest._LIVE_EGRESS_ALLOWED is True

    def test_guard_is_armed_without_the_marker(self):
        assert tests_conftest._LIVE_EGRESS_ALLOWED is False
