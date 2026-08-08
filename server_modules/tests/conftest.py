from __future__ import annotations

import asyncio
import inspect
import os
import sys
import uuid
import warnings
from pathlib import Path

import pytest

asyncio.iscoroutinefunction = inspect.iscoroutinefunction


warnings.filterwarnings(
    "error",
    category=DeprecationWarning,
    module=r"server_modules(\..*)?",
)


# ---------------------------------------------------------------------------
# MAN-139: structural guard against a test session silently reaching a real
# -- and possibly production -- Postgres database.
#
# Production's `projects` table was found holding ~143 rows carrying plain
# random UUIDs instead of the real ws_/tenant_ id format, alongside ~3 rows
# using the genuine format -- consistent with a test run's fixtures having
# written there directly. server_modules/runtime_config.py calls
# load_dotenv() at import time whenever EMPYRALIS_DEPLOY_ENV / ORION_ENV /
# ENV / NODE_ENV resolves to one of {dev, development, local, test,
# testing} -- an ordinary thing to have set in a developer's shell -- so a
# stray .env file (one copied from a production host for a one-off
# debugging session, a leftover from an ops task, anything) can hand a
# real, remote DATABASE_URL to every test process without the developer
# ever exporting anything themselves. server_modules/db.py's get_pool()
# then connects to whatever that DSN says, no questions asked; several
# tests (see test_agent_registry_install_metadata_concurrency.py's own
# _database_url() helper) additionally call load_dotenv() themselves on
# top of that.
#
# This hook runs at pytest_configure -- before any test module is
# imported, before any fixture can run, before collection even starts --
# and hard-aborts the WHOLE session if a configured DATABASE_URL doesn't
# look like an obvious, dedicated test database, rather than letting a
# single test quietly reach whatever it points at. It does not delete,
# modify, or connect to anything itself; it only inspects the URL string.
#
# Posture is fail-closed and deliberately has NO override flag: an
# ordinary developer .env pointed at their normal local Postgres (e.g. the
# `postgresql://postgres:postgres@localhost:5432/empyralis` default in
# .env.example) is exactly the kind of "not production, but not a test
# database either" case that should NOT be trusted implicitly -- it's
# real dev data, and blackbox_db tests are free to write into and mutate
# whatever database DATABASE_URL names. The only thing this guard accepts
# is a database whose name makes that consequence obvious up front:
# something with "test" in it (e.g. `empyralis_test`), trivial to create
# once (`createdb empyralis_test`) and reuse forever.
_DATABASE_URL_DENYLIST_HOSTS = frozenset({
    # The Empyralis production host -- see docs/AGENT-OPERATING-RULES.md /
    # every agent's HARD CONSTRAINTS: no production, ever. Hardcoded so
    # this still fires even if the database-name signal below is somehow
    # ambiguous.
    "165.227.25.201",
})


def _resolve_candidate_database_url() -> str:
    """An explicit shell export always wins. Otherwise, fall back to
    whatever this checkout's own repo-root .env file would supply -- the
    same explicit path runtime_config.py's module-level load_dotenv() call
    now reads from (MAN-202 scoped it there so it can no longer search UP
    the directory tree into a *different* checkout's .env; see that file
    for the full incident writeup). Mirroring the exact path here -- rather
    than a bare load_dotenv() that would search upward on its own -- keeps
    this guard definitionally correct instead of a re-derivation that could
    drift from the real app behavior.

    python-dotenv's load_dotenv() never overrides an already-set
    environment variable (override=False is the default), so calling it
    unconditionally here is safe: it only ever fills a gap, and never
    changes a value some other code already set. Checking unconditionally
    -- rather than trying to replicate runtime_config.py's
    EMPYRALIS_DEPLOY_ENV/ORION_ENV/ENV/NODE_ENV gate here -- is the
    fail-safe direction: the worst case is this guard blocks a
    DATABASE_URL the app wouldn't actually have loaded either, never the
    reverse.
    """
    explicit = str(os.environ.get("DATABASE_URL") or "").strip()
    if explicit:
        return explicit
    try:
        from dotenv import load_dotenv

        repo_root_env = Path(__file__).resolve().parent.parent.parent / ".env"
        load_dotenv(repo_root_env)
    except Exception:
        pass
    return str(os.environ.get("DATABASE_URL") or "").strip()


def _database_url_unsafe_reason(raw_url: str) -> str:
    """Empty return means safe. Fail-closed: a parse error, or a host/db
    name this guard can't positively vouch for, is UNSAFE -- only an
    explicit, parseable, obviously-a-test-database URL passes."""
    try:
        from urllib.parse import urlsplit

        parsed = urlsplit(raw_url)
    except Exception:
        return "DATABASE_URL could not be parsed"
    host = str(parsed.hostname or "").strip().lower()
    if host in _DATABASE_URL_DENYLIST_HOSTS:
        return f"host {host!r} is a known non-test host (see AGENT-OPERATING-RULES.md)"
    db_name = str(parsed.path or "").lstrip("/").strip().lower()
    if "test" not in db_name:
        return (
            f"database name {db_name!r} does not contain 'test' -- point DATABASE_URL "
            "at a dedicated test database (e.g. 'empyralis_test') rather than a shared "
            "dev/production one"
        )
    return ""


def pytest_configure(config: "pytest.Config") -> None:
    _install_network_egress_guard()
    raw_url = _resolve_candidate_database_url()
    if not raw_url:
        return  # no Postgres configured at all -- SQLite fallback, nothing to guard
    unsafe_reason = _database_url_unsafe_reason(raw_url)
    if unsafe_reason:
        pytest.exit(
            "Refusing to run: DATABASE_URL is configured but does not look like an "
            f"obvious test database ({unsafe_reason}). This guard exists because "
            "production's database was previously polluted by a test run that reached "
            "it silently (MAN-139) -- see server_modules/tests/conftest.py. Unset "
            "DATABASE_URL to use the SQLite/mock fallback, or point it at a database "
            "whose name contains 'test'.",
            returncode=1,
        )


# ---------------------------------------------------------------------------
# Sibling guard to the DATABASE_URL one above, same posture, same reason for
# existing: a test session must not reach a real, billed, third-party service
# just because the developer running it happens to have credentials in their
# environment. The DB guard was added after a test run silently polluted
# production Postgres (MAN-139); this one exists because a test run was
# silently making REAL, BILLED LLM provider calls -- confirmed by a captured
# DeepSeek response (prompt_tokens 176, completion_tokens 9, a real model
# reply) inside an ordinary unit test whose whole point was to assert the
# agent said NOTHING.
#
# Two things made that possible and both are fixed here:
#
#   1. Nothing stopped a unit test from opening a socket to api.deepseek.com.
#   2. A test that patches one function is not protected when the code path
#      moves out from under the patch -- and the resulting live call FAILS
#      (no credentials on the CI/clean box), the caller swallows the failure,
#      and the assertion passes for a completely unrelated reason. Green,
#      meaningless, and silent about it. That is the exact "silent
#      misrouting beats loud failure" failure mode CLAUDE.md names.
#
# WHY THE SOCKET LAYER AND NOT A PROVIDER MODULE. This backend has no single
# LLM chokepoint to patch. Provider traffic leaves through at least four
# structurally independent transports:
#
#     scripts/orion_local_worker_llm.py     urllib + a curl subprocess fallback
#     server_modules/runtime_common.py      http_json_request (the one that IS
#                                           egress_policy-enforced)
#     server_modules/openai_compat_adapter  httpx.AsyncClient (loopback proxy
#                                           for the Claude Agent SDK engine)
#     claude_agent_sdk_bridge.py            spawns the Node `claude` CLI, whose
#                                           HTTP is invisible to Python entirely
#
# ...plus one-off SDK clients (openai.AsyncOpenAI in
# sage_telegram_hosted_service.py, boto3 in provider_profiles.py, httpx in
# multimodal_provider_service.py / tools_image_gen.py). Guarding any one of
# them is guarding a call site, and a future code path slips around it -- which
# is precisely how the broken test got here. `socket.socket.connect` is the
# one place every in-process transport must pass through, whatever library
# it is built on; the subprocess denylist below covers the transports that
# leave the process instead (curl, the claude/codex CLIs).
#
# FAIL-LOUD MECHANICS. The violation raises a BaseException subclass, not an
# Exception, because the personal-channel and runtime paths are full of broad
# `except Exception:` handlers that would otherwise convert this guard into
# exactly the silent degradation it exists to eliminate. As a second layer
# (a bare `except:` would still swallow a BaseException), every violation is
# also recorded, and the autouse fixture below fails the test at teardown
# even if the exception never made it out. Both layers together are what make
# the guard unavoidable rather than merely present.
_LIVE_PROVIDER_ALLOW_ENV = "EMPYRALIS_TEST_ALLOW_LIVE_PROVIDER_CALLS"
_LIVE_PROVIDER_MARKER = "live_provider"

# argv[0] basenames that exist to move bytes over the network. Deliberately a
# DENYLIST, not an allowlist: this suite legitimately shells out to openssl
# (vault encryption), git, cargo, and the compiled runtime kernel, and those
# must keep working. `claude`/`codex` are here because the Agent SDK bridge
# spawns them as a subprocess and all of that turn's model traffic happens
# inside Node, where no Python-level patch can see it.
_NETWORK_EGRESS_EXECUTABLES = frozenset({
    "curl", "wget", "http", "https", "httpie", "claude", "codex",
})

# The agent CLIs are also used as LOCAL capability probes, not only as
# inference entry points -- provider_profiles.claude_code_cli_status runs
# `claude auth status --json` on the availability path that every direct-chat
# test crosses. Those probes are not billed model calls and blocking them
# would break availability resolution everywhere without catching anything
# this guard is for. Matched on the first non-flag subcommand, and kept to
# the invocations this repo actually makes; anything else (notably
# `claude -p <prompt>`, `claude ... text --model ...`, `codex exec ...`, and
# whatever argv the Agent SDK builds for its own spawn) stays blocked.
# `ollama` is deliberately absent from the denylist entirely: the CLI talks
# to 127.0.0.1:11434, and the socket guard is what catches ollama.com.
_LOCAL_PROBE_CLI_TOKENS = frozenset({
    "auth", "doctor", "mcp", "--version", "-v", "--help", "-h", "config",
})

_LIVE_EGRESS_ALLOWED = False          # flipped per-test by the fixture below
_LIVE_EGRESS_VIOLATIONS: list[str] = []
_EGRESS_HOSTNAMES_BY_IP: dict[str, str] = {}
_EXTRA_ALLOWED_EGRESS_HOSTS: set[str] = set()

# Hosts we can positively vouch for. Loopback is a local Ollama, the
# openai_compat_adapter's own 127.0.0.1 proxy, or a local Postgres -- none of
# them a billed third party, and the adapter's *upstream* hop is a separate,
# non-loopback connect that this guard still catches.
_ALWAYS_ALLOWED_EGRESS_HOSTS = frozenset({
    "127.0.0.1", "::1", "0.0.0.0", "localhost", "", "::",
})


class LiveProviderCallBlocked(BaseException):
    """Raised when a test tries to reach a real network service.

    Intentionally a BaseException -- see the block comment above.
    """


def _describe_egress_target(host: str, port: object) -> str:
    hostname = _EGRESS_HOSTNAMES_BY_IP.get(host, "")
    if hostname and hostname != host:
        return f"{hostname} ({host}:{port})"
    return f"{host}:{port}"


def _record_and_raise_egress_violation(target: str, transport: str) -> None:
    message = (
        f"BLOCKED: a test attempted a live network call to {target} via {transport}.\n"
        "\n"
        "Unit tests must never reach a real LLM provider -- those calls are billed, "
        "they make results depend on whose machine the suite runs on, and when "
        "credentials are absent they fail quietly and let an assertion pass for the "
        "wrong reason (which is how test_genuinely_silent_turn_still_returns_none "
        "spent months proving nothing).\n"
        "\n"
        "Fix it by mocking the provider call. If execution is reaching a provider "
        "despite a patch, the code path has MOVED OUT FROM UNDER your patch -- find "
        "the function that actually runs now rather than re-pointing the mock "
        "blindly; the move itself may be the bug.\n"
        "\n"
        "If a test genuinely needs a live call, opt in explicitly and narrowly with "
        f"@pytest.mark.{_LIVE_PROVIDER_MARKER} on that one test, or set "
        f"{_LIVE_PROVIDER_ALLOW_ENV}=1 for a deliberate local run. Never set that "
        "variable in CI.\n"
        "\n"
        "Guard lives in server_modules/tests/conftest.py."
    )
    _LIVE_EGRESS_VIOLATIONS.append(message)
    raise LiveProviderCallBlocked(message)


def _egress_host_is_allowed(host: str) -> bool:
    normalized = str(host or "").strip().lower()
    if normalized in _ALWAYS_ALLOWED_EGRESS_HOSTS:
        return True
    if normalized.startswith("127.") or normalized.startswith("::ffff:127."):
        return True
    if normalized in _EXTRA_ALLOWED_EGRESS_HOSTS:
        return True
    return _EGRESS_HOSTNAMES_BY_IP.get(normalized, "") in _EXTRA_ALLOWED_EGRESS_HOSTS


def _argv_targets_only_loopback(argv: list[str]) -> bool:
    """True when a denylisted binary is demonstrably pointed at loopback only.

    Carve-out for the tests that legitimately shell out to `curl` against a
    local HTTP server they started themselves -- test_web_tools_ssrf_redirect_guard
    runs a real ThreadingHTTPServer on 127.0.0.1 and drives web_tools' curl
    fallback at it. Requires at least one URL argument AND every URL to be
    loopback: an invocation with no URL to inspect (`claude -p "..."`, which
    reads its destination from the environment) cannot be vouched for and
    stays blocked.
    """
    from urllib.parse import urlsplit

    urls = [a for a in argv if a.startswith("http://") or a.startswith("https://")]
    if not urls:
        return False
    for url in urls:
        try:
            host = str(urlsplit(url).hostname or "").strip().lower()
        except Exception:
            return False
        if not _egress_host_is_allowed(host):
            return False
    return True


def _argv_is_local_cli_probe(argv: list[str]) -> bool:
    """True for `claude auth status --json` and friends -- see
    _LOCAL_PROBE_CLI_TOKENS. Only the FIRST argument after the executable is
    consulted, so an inference invocation cannot smuggle itself past by
    carrying a probe-looking flag somewhere later in the command line."""
    if len(argv) < 2:
        return False
    return argv[1].strip().lower() in _LOCAL_PROBE_CLI_TOKENS


def _install_network_egress_guard() -> None:
    """Patch the process's real egress points. Installed at pytest_configure --
    before collection, so it also covers a module that fires a provider call at
    IMPORT time, which no fixture could ever catch."""
    import socket
    import subprocess

    # A remote Postgres named by DATABASE_URL is a legitimate destination: the
    # DATABASE_URL guard above has already established it is a dedicated test
    # database, and the blackbox_db tests exist to talk to it.
    raw_db_url = _resolve_candidate_database_url()
    if raw_db_url:
        try:
            from urllib.parse import urlsplit

            db_host = str(urlsplit(raw_db_url).hostname or "").strip().lower()
            if db_host:
                _EXTRA_ALLOWED_EGRESS_HOSTS.add(db_host)
        except Exception:
            pass

    real_getaddrinfo = socket.getaddrinfo
    real_connect = socket.socket.connect
    real_connect_ex = socket.socket.connect_ex
    real_popen_init = subprocess.Popen.__init__

    def _guarded_getaddrinfo(host, port, *args, **kwargs):
        # Resolution itself is harmless; recording it is what lets the error
        # message name "api.deepseek.com" instead of an opaque IP.
        results = real_getaddrinfo(host, port, *args, **kwargs)
        hostname = str(host or "").strip().lower()
        if hostname:
            for entry in results:
                try:
                    address = entry[4][0]
                except Exception:
                    continue
                _EGRESS_HOSTNAMES_BY_IP.setdefault(str(address).strip().lower(), hostname)
        return results

    def _check_socket_address(address, transport: str) -> None:
        if _LIVE_EGRESS_ALLOWED:
            return
        if not isinstance(address, (tuple, list)) or not address:
            return  # AF_UNIX / AF_NETLINK and friends never leave the machine
        host = str(address[0] or "").strip().lower()
        port = address[1] if len(address) > 1 else "?"
        if _egress_host_is_allowed(host):
            return
        _record_and_raise_egress_violation(_describe_egress_target(host, port), transport)

    def _guarded_connect(self, address):
        _check_socket_address(address, "socket.connect")
        return real_connect(self, address)

    def _guarded_connect_ex(self, address):
        _check_socket_address(address, "socket.connect_ex")
        return real_connect_ex(self, address)

    def _guarded_popen_init(self, args, *rest, **kwargs):
        if not _LIVE_EGRESS_ALLOWED:
            argv = [args] if isinstance(args, (str, bytes)) else list(args or [])
            argv = [
                a.decode("utf-8", "replace") if isinstance(a, bytes) else str(a)
                for a in argv
            ]
            argv0 = argv[0] if argv else ""
            basename = os.path.basename(argv0.strip().split(" ")[0]).lower()
            if (
                basename in _NETWORK_EGRESS_EXECUTABLES
                and not _argv_targets_only_loopback(argv)
                and not _argv_is_local_cli_probe(argv)
            ):
                _record_and_raise_egress_violation(
                    f"the {basename!r} subprocess ({' '.join(argv[:3])!r})",
                    "subprocess.Popen",
                )
        return real_popen_init(self, args, *rest, **kwargs)

    socket.getaddrinfo = _guarded_getaddrinfo
    socket.socket.connect = _guarded_connect
    socket.socket.connect_ex = _guarded_connect_ex
    subprocess.Popen.__init__ = _guarded_popen_init


_CALL_PHASE_ALREADY_FAILED = [False]


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    """Lets the fixture below tell "the guard's exception reached pytest" apart
    from "the code under test swallowed it", so a blocked call is reported once
    rather than twice."""
    outcome = yield
    report = outcome.get_result()
    if report.when == "call" and report.failed:
        _CALL_PHASE_ALREADY_FAILED[0] = True


@pytest.fixture(autouse=True)
def _block_live_provider_calls(request: pytest.FixtureRequest):
    """Per-test arm/disarm of the egress guard, plus the second failure layer.

    The recorded-violation check is what survives a `except BaseException:` or
    bare `except:` somewhere in the code under test: even if the raise never
    reaches pytest, the test still fails here, loudly, naming what it called.
    Without it a swallowed block would leave the test green -- the same
    pass-for-the-wrong-reason this guard exists to end.
    """
    global _LIVE_EGRESS_ALLOWED

    opted_in = (
        request.node.get_closest_marker(_LIVE_PROVIDER_MARKER) is not None
        or str(os.environ.get(_LIVE_PROVIDER_ALLOW_ENV) or "").strip().lower()
        in {"1", "true", "yes", "on"}
    )
    _LIVE_EGRESS_ALLOWED = opted_in
    _CALL_PHASE_ALREADY_FAILED[0] = False
    del _LIVE_EGRESS_VIOLATIONS[:]
    try:
        yield
    finally:
        _LIVE_EGRESS_ALLOWED = False
        violations = list(_LIVE_EGRESS_VIOLATIONS)
        del _LIVE_EGRESS_VIOLATIONS[:]
        if violations and not opted_in and not _CALL_PHASE_ALREADY_FAILED[0]:
            raise AssertionError(
                f"{len(violations)} blocked live network call(s) during this test, and "
                "the code under test SWALLOWED the guard's exception -- the test would "
                "otherwise have passed while silently reaching a provider.\n\n"
                + "\n\n".join(violations)
            )


# ---------------------------------------------------------------------------
# Real per-operation next_action tables, ported from the Rust kernel so the
# non-kernel test mock below can reproduce its "allow"-path behavior exactly
# instead of a blanket echo. Each table cites the kernel source it mirrors;
# re-derive it from there if the kernel's operation set changes.
# ---------------------------------------------------------------------------

# empyralis-runtime-kernel/src/runtime_state_store.rs next_action(), lines
# 1106-1216 (decision == "allow" arm only — "block"/"require_approval" return
# fixed sentinels handled separately below). Most operations are identity,
# but several are not (e.g. "upsert_workspace_memory" -> "write_workspace_memory",
# "upsert_sage_memory_entry" -> "write_sage_memory_entry") — that mismatch was
# the root cause of the memory_service.py / sage_memory_service.py failures.
_RUNTIME_STATE_STORE_NEXT_ACTIONS: dict[str, str] = {
    "init_schema": "initialize_state_schema",
    "upsert_live_run": "write_live_run_state",
    "delete_live_run": "delete_live_run_state",
    "archive_run": "write_run_archive",
    "create_or_update_approval_request": "write_run_approval_request",
    "resolve_approval_if_pending": "resolve_run_approval",
    "record_approval_resolution": "record_run_approval_resolution",
    "upsert_runtime_registration": "write_runtime_registration",
    "upsert_fleet_queue_partition": "write_fleet_queue_partition",
    "upsert_runtime_session": "write_runtime_session",
    "delete_runtime_session": "delete_runtime_session",
    "upsert_runtime_session_turn": "write_runtime_session_turn",
    "delete_runtime_session_turn": "delete_runtime_session_turn",
    "upsert_chat_stream_state": "write_chat_stream_state",
    "append_channel_event": "append_channel_event",
    "upsert_local_claim": "write_local_claim",
    "release_local_claim": "release_local_claim",
    "upsert_notification": "write_notification",
    "mark_notification_read": "mark_notification_read",
    "register_notification_device": "write_notification_device",
    "update_notification_delivery": "write_notification_delivery",
    "set_kill_switch": "set_kill_switch",
    "clear_kill_switch": "clear_kill_switch",
    "set_runtime_tool_enabled": "set_runtime_tool_enabled",
    "upsert_agent_computer_policy": "upsert_agent_computer_policy",
    "set_safe_mode_state": "set_safe_mode_state",
    "upsert_security_control_state": "upsert_security_control_state",
    "upsert_sage_profile": "upsert_sage_profile",
    "save_mcp_server_registry": "save_mcp_server_registry",
    "save_installed_skill_registry": "save_installed_skill_registry",
    "write_vault_key_file": "write_vault_key_file",
    "write_vault_blob_backup": "write_vault_blob_backup",
    "read_vault_blob_backup": "read_vault_blob_backup",
    "write_channel_pairings_backup": "write_channel_pairings_backup",
    "write_jwt_secret_file": "write_jwt_secret_file",
    "persist_artifact_record": "persist_artifact_record",
    "write_hosted_sandbox_base_image": "write_hosted_sandbox_base_image",
    "save_cli_companion_state": "save_cli_companion_state",
    "write_hosted_worker_output": "write_hosted_worker_output",
    "save_marketplace_distribution_state": "save_marketplace_distribution_state",
    "write_sage_dreaming_memory_state": "write_sage_dreaming_memory_state",
    "write_sage_dreaming_staging_file": "write_sage_dreaming_staging_file",
    "save_mini_apps_state": "save_mini_apps_state",
    "initialize_workspace_context_file": "initialize_workspace_context_file",
    "save_workspace_context_file": "save_workspace_context_file",
    "save_agent_computer_profile_state": "save_agent_computer_profile_state",
    "write_profile_api_file": "write_profile_api_file",
    "write_runtime_common_json": "write_runtime_common_json",
    "write_acp_manager_json": "write_acp_manager_json",
    "write_session_diagnostics_file": "write_session_diagnostics_file",
    "append_agent_memory_daily_log": "append_agent_memory_daily_log",
    "write_no_provider_summary": "write_no_provider_summary",
    "write_public_bot_drill_report": "write_public_bot_drill_report",
    "write_telegram_media_file": "write_telegram_media_file",
    "write_machine_capability_probe_file": "write_machine_capability_probe_file",
    "write_dropbox_download_file": "write_dropbox_download_file",
    "write_generated_image_file": "write_generated_image_file",
    "write_telegram_poll_lock_file": "write_telegram_poll_lock_file",
    "write_skills_registry_file": "write_skills_registry_file",
    "write_deployed_agent_knowledge_file": "write_deployed_agent_knowledge_file",
    "write_outcome_pack_spreadsheet_file": "write_outcome_pack_spreadsheet_file",
    "write_outcome_pack_document_file": "write_outcome_pack_document_file",
    "write_outcome_pack_remote_sync_file": "write_outcome_pack_remote_sync_file",
    "write_artifact_content_file": "write_artifact_content_file",
    "execute_external_write_once": "execute_external_write_once",
    "create_gateway_tool_approval": "create_gateway_tool_approval",
    "resolve_gateway_tool_approval": "resolve_gateway_tool_approval",
    "expire_gateway_tool_approval": "expire_gateway_tool_approval",
    "fail_gateway_tool_approval": "fail_gateway_tool_approval",
    "execute_gateway_tool_approval": "execute_gateway_tool_approval",
    "send_gateway_protocol_request_frame": "send_gateway_protocol_request_frame",
    "acquire_channel_execution_lease": "acquire_channel_execution_lease",
    "release_channel_execution_lease": "release_channel_execution_lease",
    "start_direct_tool_execution": "start_direct_tool_execution",
    "block_direct_tool_execution": "block_direct_tool_execution",
    "fail_direct_tool_execution": "fail_direct_tool_execution",
    "complete_direct_tool_execution": "complete_direct_tool_execution",
    "upsert_workspace_memory": "write_workspace_memory",
    "delete_workspace_memory": "delete_workspace_memory",
    "append_workspace_daily_log": "append_workspace_daily_log",
    "update_workspace_context_file": "write_workspace_context_file",
    "upsert_approval_memory_rule": "write_approval_memory_rule",
    "consume_approval_memory_rule": "consume_approval_memory_rule",
    "create_sage_approval": "create_sage_approval",
    "resolve_sage_approval": "resolve_sage_approval",
    "consume_sage_approval": "consume_sage_approval",
    "expire_sage_approvals": "expire_sage_approvals",
    "update_sage_service_profile": "update_sage_service_profile",
    "create_sage_service_entry": "create_sage_service_entry",
    "update_sage_service_entry": "update_sage_service_entry",
    "delete_sage_service_entry": "delete_sage_service_entry",
    "set_sage_service_entry_pinned": "set_sage_service_entry_pinned",
    "upsert_sage_memory_entry": "write_sage_memory_entry",
    "update_sage_memory_entry": "update_sage_memory_entry",
    "delete_sage_memory_entry": "delete_sage_memory_entry",
    "wipe_sage_memory": "wipe_sage_memory",
    "append_session_transcript": "append_session_transcript",
    "append_activity_ledger_event": "append_activity_ledger_event",
    "write_shared_operational_board_entry": "write_shared_operational_board_entry",
    "checkpoint_snapshot": "write_checkpoint_snapshot",
    "prune_records": "prune_state_records",
}
_RUNTIME_STATE_STORE_DEFAULT_NEXT_ACTION = "review_state_store_write"  # rust line 1215


def _mock_runtime_state_store_next_action(operation: str) -> str:
    op = str(operation or "").strip()
    return _RUNTIME_STATE_STORE_NEXT_ACTIONS.get(op, _RUNTIME_STATE_STORE_DEFAULT_NEXT_ACTION)


# empyralis-runtime-kernel/src/control_plane_service.rs, allow-path only
# (control_plane_service_decision_command(), lines 289-537). The set
# membership tables below are copied verbatim from the Rust consts; the
# next_action derivation mirrors lines 517-537 for the case this mock always
# produces — decision == "allow" (no block_reasons / approval_reasons).
_CONTROL_PLANE_SERVICE_DESTRUCTIVE_OPERATIONS = {  # rust lines 102-113
    "workspace_archive",
    "workspace_delete",
    "membership_remove",
    "invite_revoke",
    "thread_archive",
    "thread_delete",
    "workspace_emergency_stop",
    "external_user_privacy_delete",
    "deployed_agent_scope_data_delete",
    "workspace_scope_data_delete",
}
_CONTROL_PLANE_SERVICE_OWNER_REQUIRED_OPERATIONS = {  # rust lines 115-167
    "tenant_update",
    "transparency_settings_update",
    "workspace_policy_update",
    "workspace_delete",
    "billing_update",
    "workspace_billing_plan_update",
    "workspace_billing_account_write",
    "workspace_billing_subscription_write",
    "security_control_state_write",
    "governance_hold_write",
    "governance_hold_release",
    "channel_user_acquisition_touch_write",
    "channel_user_acquisition_conversion_write",
    "deployed_agent_upgrade_click_write",
    "agent_channel_event_write",
    "personal_context_event_write",
    "personal_context_event_seen_update",
    "agent_action_event_write",
    "agent_secret_access_event_write",
    "agent_egress_event_write",
    "activity_ledger_event_write",
    "agent_trace_create",
    "agent_trace_event_write",
    "agent_trace_finish",
    "agent_turn_transcript_event_append",
    "agent_thread_ensure",
    "agent_session_upsert",
    "agent_session_terminate",
    "agent_turn_upsert",
    "knowledge_source_upsert",
    "knowledge_source_chunks_replace",
    "knowledge_retrieval_event_write",
    "compiled_workflow_artifact_create",
    "workspace_agent_install_compiled_artifact_update",
    "self_hosted_enrollment_intent_create",
    "self_hosted_runtime_enroll",
    "self_hosted_runtime_approve",
    "self_hosted_runtime_heartbeat",
    "self_hosted_command_enqueue",
    "self_hosted_command_claim",
    "self_hosted_command_complete",
    "quota_update",
    "admin_impersonation",
    "workspace_emergency_stop",
    "public_route_update",
    "workspace_scope_data_delete",
    "deployed_agent_daily_message_quota_consume",
    "deployed_agent_daily_message_warning_update",
    "deployed_agent_cost_ledger_write",
    "workspace_hosted_ai_cost_ledger_write",
    "credit_ledger_event_write",
}
_CONTROL_PLANE_SERVICE_ADMIN_REQUIRED_OPERATIONS = {  # rust lines 169-221
    "workspace_create",
    "workspace_update",
    "workspace_archive",
    "membership_add",
    "membership_update",
    "membership_remove",
    "invite_create",
    "invite_revoke",
    "pilot_invite_create",
    "pilot_invite_revoke",
    "workspace_tenant_binding_ensure",
    "compiled_workflow_artifact_create",
    "workspace_agent_install_compiled_artifact_update",
    "self_hosted_enrollment_intent_create",
    "self_hosted_runtime_approve",
    "self_hosted_command_enqueue",
    "deployed_agent_record_write",
    "gateway_record_write",
    "session_record_write",
    "audit_export",
    "secret_reference_write",
    "external_user_privacy_delete",
    "deployed_agent_scope_data_delete",
    "workspace_scope_data_delete",
    "workspace_billing_account_write",
    "workspace_billing_subscription_write",
    "security_control_state_write",
    "governance_hold_write",
    "governance_hold_release",
    "channel_user_acquisition_touch_write",
    "channel_user_acquisition_conversion_write",
    "deployed_agent_upgrade_click_write",
    "agent_channel_event_write",
    "personal_context_event_write",
    "personal_context_event_seen_update",
    "agent_action_event_write",
    "agent_secret_access_event_write",
    "agent_egress_event_write",
    "activity_ledger_event_write",
    "agent_trace_create",
    "agent_trace_event_write",
    "agent_trace_finish",
    "agent_turn_transcript_event_append",
    "agent_thread_ensure",
    "agent_session_upsert",
    "agent_session_terminate",
    "agent_turn_upsert",
    "knowledge_source_upsert",
    "knowledge_source_chunks_replace",
    "knowledge_retrieval_event_write",
    "workspace_tenant_binding_ensure",
}
_CONTROL_PLANE_SERVICE_EXTERNAL_WRITE_OPERATIONS = {  # rust lines 223-274
    "webhook_ingest",
    "deployed_agent_record_write",
    "gateway_record_write",
    "session_record_write",
    "public_route_update",
    "workspace_ai_route_update",
    "secret_reference_write",
    "external_user_privacy_request_write",
    "external_user_privacy_audit_write",
    "external_user_privacy_delete",
    "deployed_agent_scope_data_delete",
    "workspace_scope_data_delete",
    "workspace_billing_defaults_write",
    "workspace_billing_plan_update",
    "workspace_billing_account_write",
    "workspace_billing_subscription_write",
    "security_control_state_write",
    "governance_hold_write",
    "governance_hold_release",
    "channel_user_acquisition_touch_write",
    "channel_user_acquisition_conversion_write",
    "deployed_agent_upgrade_click_write",
    "agent_channel_event_write",
    "personal_context_event_seen_update",
    "agent_action_event_write",
    "agent_secret_access_event_write",
    "agent_egress_event_write",
    "activity_ledger_event_write",
    "agent_trace_create",
    "agent_trace_event_write",
    "agent_trace_finish",
    "agent_turn_transcript_event_append",
    "agent_thread_ensure",
    "agent_session_upsert",
    "agent_session_terminate",
    "agent_turn_upsert",
    "knowledge_source_upsert",
    "knowledge_source_chunks_replace",
    "knowledge_retrieval_event_write",
    "workspace_tenant_binding_ensure",
    "user_profile_update",
    "compiled_workflow_artifact_create",
    "workspace_agent_install_compiled_artifact_update",
    "self_hosted_enrollment_intent_create",
    "self_hosted_runtime_enroll",
    "self_hosted_runtime_approve",
    "self_hosted_runtime_heartbeat",
    "self_hosted_command_enqueue",
    "self_hosted_command_claim",
    "self_hosted_command_complete",
}


def _mock_control_plane_service_next_action(operation: str, payload: dict) -> str:
    """Mirrors control_plane_service_decision_command()'s allow-path next_action
    (rust lines 517-537) — this mock never produces block/require_approval, so
    those branches (lines 517-520) are intentionally omitted."""
    op = str(operation or "").strip()
    target_status = str(payload.get("target_status") or "active").strip()
    source = str(payload.get("source") or "internal").strip()
    existing_record_idempotent_return = op == "invite_revoke" and target_status == "revoked"  # line 489-490
    if payload.get("dry_run"):
        return "return_control_plane_dry_run"
    if existing_record_idempotent_return:
        return "return_existing_control_plane_record"
    if op == "webhook_ingest":
        return "persist_webhook_event"
    if op == "entitlement_check":
        return "return_entitlement_snapshot"
    if op == "audit_export":
        return "create_audit_export_job"
    if op in _CONTROL_PLANE_SERVICE_DESTRUCTIVE_OPERATIONS:
        return "apply_control_plane_destructive_write"
    external_write = op in _CONTROL_PLANE_SERVICE_EXTERNAL_WRITE_OPERATIONS or source == "webhook"
    if (
        external_write
        or op in _CONTROL_PLANE_SERVICE_ADMIN_REQUIRED_OPERATIONS
        or op in _CONTROL_PLANE_SERVICE_OWNER_REQUIRED_OPERATIONS
    ):
        return "apply_control_plane_write"
    return "allow_control_plane_read"


# next_action values control_plane_service_decision_command() (rust lines
# 517-537) produces for a request this mock does NOT treat as a mutation --
# every other next_action corresponds to `mutating_operation=true` on the
# allow path (rust lines 561-565). Mirrors the "mutation_plan" object rust
# lines 574-585 attach to every response (not just next_action) -- several
# non-test callers (workspace_admin_service.py, billing_service.py,
# routes_workspaces.py, workspace_ai_route_service.py) read
# decision["mutation_plan"]["apply"] directly and 423 with
# "missing_rust_mutation_plan" if it isn't exactly True, so a mock that only
# set top-level next_action (and never mutation_plan at all) silently failed
# every one of those callers even on an "allow" decision -- MAN-139 phase 2.
_CONTROL_PLANE_SERVICE_NON_MUTATING_NEXT_ACTIONS = {
    "return_control_plane_dry_run",
    "return_existing_control_plane_record",
    "allow_control_plane_read",
    "return_entitlement_snapshot",
}


def _mock_control_plane_service_mutation_plan(operation: str, payload: dict, next_action: str) -> dict:
    op = str(operation or "").strip()
    mutating = next_action not in _CONTROL_PLANE_SERVICE_NON_MUTATING_NEXT_ACTIONS
    return {
        "operation": op,
        "record_type": payload.get("record_type"),
        "apply": mutating and not payload.get("dry_run"),  # rust line 578
        "next_action": next_action,
        "idempotency_key": payload.get("idempotency_key"),
        "target_status": payload.get("target_status") or "active",
        "mutating": mutating,
        "destructive": op in _CONTROL_PLANE_SERVICE_DESTRUCTIVE_OPERATIONS,
        "external_write": op in _CONTROL_PLANE_SERVICE_EXTERNAL_WRITE_OPERATIONS,
        "billing_gated": False,
    }


# empyralis-runtime-kernel/src/control_plane.rs control_plane_decision_command(),
# lines 22-213. Identity-ish but with a "_record" suffix (and a
# read/status_transition special case) — not a blanket echo either.
_CONTROL_PLANE_RECORD_NEXT_ACTIONS = {  # rust lines 132-141
    "create": "create_record",
    "upsert": "upsert_record",
    "update": "update_record",
    "archive": "archive_record",
    "restore": "restore_record",
    "lock": "lock_record",
    "unlock": "unlock_record",
}


def _mock_control_plane_record_next_action(operation: str, payload: dict) -> str:
    op = str(operation or "").strip()
    if op in ("read", "list", "lookup"):  # rust lines 72-85
        return "read_record"
    if op == "status_transition":  # rust lines 158-213
        current_status = str(payload.get("current_status") or "").strip()
        next_status = str(payload.get("next_status") or "").strip()
        if current_status and next_status and current_status == next_status:
            return "noop"
        return "write_status_transition"
    return _CONTROL_PLANE_RECORD_NEXT_ACTIONS.get(op, "")


# ---------------------------------------------------------------------------
# Gateway command family (MAN-139). Until this section was added, NONE of the
# five "gateway-*" commands below were handled by this mock at all -- every
# gateway-service/-action/-protocol/-frame/-state call silently fell through
# to next_action="" (the same default the mock used for anything unrecognized),
# which never equals what the real caller checks for. That gap looked like
# protection (the autouse mock runs for every non-kernel test) but provided
# none: it silently killed 39 of test_gateway_routes.py's 42 tests at the very
# first kernel call (gateway-service-decision's pairing_bootstrap), including
# the heartbeat/reconnect coverage for the exact subsystem MAN-140 (2GB disk
# fill from reconnect churn) turned out to have a real bug in. Ported from the
# Rust kernel the same way the tables above are: allow-path next_action only,
# per-command citations to the rust source below.
# ---------------------------------------------------------------------------

# empyralis-runtime-kernel/src/gateway_service.rs gateway_service_decision_command(),
# lines 51-238 (allow-path next_action only, lines 224-238 -- like the mocks
# above, this never produces block/require_approval).
_GATEWAY_SERVICE_TOOL_OR_BROWSER_OPERATIONS = {  # rust lines 29-37
    "protocol_route",
    "tool_execute",
    "tool_interrupt",
    "browser_session",
    "browser_action",
    "browser_fallback",
    "cloud_fallback",
}


def _mock_gateway_service_next_action(operation: str) -> str:
    op = str(operation or "").strip()
    if op == "approval_request":  # rust line 226 (checked ahead of approval_required)
        return "request_gateway_owner_approval"
    if op == "health_check":  # rust line 230
        return "publish_gateway_health"
    if op == "approval_resolve":  # rust line 232
        return "persist_approval_decision"
    if op in _GATEWAY_SERVICE_TOOL_OR_BROWSER_OPERATIONS:  # rust line 234
        return "dispatch_gateway_operation"
    return "allow_gateway_service_operation"  # rust line 237


# empyralis-runtime-kernel/src/gateway_action.rs gateway_action_decision_command(),
# lines 51-131 dispatch by operation; each per-operation handler's allow() call
# supplies the next_action literal cited per entry below.
_GATEWAY_ACTION_NEXT_ACTIONS = {
    "health_check": "return_health",  # line 55
    "websocket_connect": "accept_websocket",  # line 153
    "tool_execute": "dispatch_tool_invoke",  # line 205
    "browser_session_start": "start_browser_session",  # line 289
    "browser_action": "dispatch_browser_action",  # line 297
    "browser_session_stop": "stop_browser_session",  # line 291
    "browser_session_resume": "resume_browser_session",  # line 293
    "browser_session_takeover": "takeover_browser_session",  # line 295
    "approval_resolve": "resolve_gateway_approval",  # line 330
    "acp_turn": "route_acp_turn",  # line 362
    "diagnostics_export": "export_diagnostics_bundle",  # line 390
}


def _mock_gateway_action_next_action(operation: str) -> str:
    op = str(operation or "").strip()
    return _GATEWAY_ACTION_NEXT_ACTIONS.get(op, "")


# empyralis-runtime-kernel/src/gateway.rs gateway_protocol_decision_command(),
# lines 59-238 (allow-path next_action only). Keys off "message_type", falling
# back to "method"/"operation" the same way the rust side
# (protocol::string_field chain) and every Python caller do.
_GATEWAY_PROTOCOL_NEXT_ACTIONS = {
    "health_check": "health",  # line 64
    "session_create": "create_session",  # line 80
    "session_close": "close_session",  # line 97
    "agent_turn": "route_to_agent",  # line 117
    "tool_result": "attach_tool_result",  # line 134
    "tool_invoke": "dispatch_tool_invoke",  # line 154
    "tool_interrupt": "dispatch_tool_interrupt",  # line 174
    "channel_outbound": "dispatch_channel_outbound",  # line 194
    "tool_use": "route_tool_use",  # line 228 (privileged-tool approval branch omitted -- allow-path only)
}


def _mock_gateway_protocol_next_action(payload: dict) -> str:
    message_type = str(
        payload.get("message_type") or payload.get("method") or payload.get("operation") or ""
    ).strip()
    return _GATEWAY_PROTOCOL_NEXT_ACTIONS.get(message_type, "")


# empyralis-runtime-kernel/src/gateway_frame.rs gateway_frame_decision_command(),
# lines 106-112 -- the allow-path next_action is a pure function of (kind,
# message_type); there is no block/require_approval branch to omit here, and
# the two tests that exercise oversized/over-deep frames never reach the
# kernel at all (gateway_protocol_service.py rejects those in Python before
# calling gateway-frame-decision), so this mock's allow-path fidelity is the
# whole story for frame tests.
def _mock_gateway_frame_next_action(payload: dict) -> str:
    kind = str(payload.get("kind") or payload.get("frame_kind") or "").strip().lower()
    message_type = str(payload.get("type") or payload.get("message_type") or "").strip()
    if kind == "request" and message_type == "gateway.connect":
        return "accept_gateway_connect"
    if kind == "request":
        return "route_gateway_request"
    if kind == "response":
        return "resolve_gateway_response"
    if kind == "event":
        return "handle_gateway_event"
    return "record_gateway_frame"


# empyralis-runtime-kernel/src/gateway_state.rs gateway_state_decision_command(),
# lines 27-55 dispatch by operation; each handler's allow() next_action is
# cited per entry below. This table is cross-checked against (not re-derived
# from) the *expected* next_action sets gateway_state_repository.py's own
# _enforce_gateway_state_decision() already hardcodes for production use -- a
# drift between the two would be a real bug, not a mock bug.
_GATEWAY_STATE_NEXT_ACTIONS = {
    "create_pairing_intent": "create_pairing_intent",  # line 86
    "expire_pairing_intent": "mark_pairing_intent_expired",  # line 119
    "register_gateway": "consume_pairing_and_register_gateway",  # line 144
    "issue_session": "issue_gateway_session",  # line 171
    "validate_session": "validate_gateway_session",  # line 203
    "mark_session_connected": "mark_gateway_session_connected",  # line 228
    "mark_session_disconnected": "mark_gateway_session_disconnected",  # line 37
    "touch_session": "touch_gateway_session",  # line 283
    "rotate_token": "rotate_gateway_token",  # line 42
    "revoke_registration": "revoke_gateway_registration",  # line 333 (allow path; reason present)
    "update_registration_state": "update_gateway_registration_state",  # line 379 (allow path; reason present)
    "record_event": "record_gateway_event",  # line 406
    "create_approval": "create_gateway_action_approval",  # line 427
    "resolve_approval": "resolve_gateway_action_approval_atomic",  # line 456
    "update_browser_session": "upsert_gateway_browser_session",  # line 513 (allow path; no unconfirmed manual_takeover)
    "summarize_outbox": "summarize_gateway_outbox",  # line 51
}


def _mock_gateway_state_next_action(operation: str, payload: dict) -> str:
    op = str(operation or "").strip()
    if op == "sweep_stale_sessions":  # rust lines 541-548 -- the one operation whose
        # allow-path next_action is NOT a constant; it depends on stale_count.
        raw_stale_count = payload.get("stale_count", payload.get("candidate_count", 0))
        try:
            stale_count = int(raw_stale_count or 0)
        except (TypeError, ValueError):
            stale_count = 0
        return "noop" if stale_count == 0 else "sweep_stale_gateway_sessions"
    return _GATEWAY_STATE_NEXT_ACTIONS.get(op, "")


# empyralis-runtime-kernel/src/session.rs session_lifecycle_decision_command().
# Not a "gateway-*" command itself, but directly on the gateway
# heartbeat/reconnect test path (session_service.py's create_session /
# terminate_session both go through this), so it was equally silently
# unhandled and equally necessary to fix here. create_decision (lines
# 141-177) and close_decision (lines 371-394) are the only two operations
# session_service.py's _enforce_session_lifecycle_mutation actually checks
# next_action for -- both are the identity of the operation name.
_SESSION_LIFECYCLE_NEXT_ACTIONS = {
    "create": "create",  # line 166
    "close": "close",  # line 383
}


def _mock_session_lifecycle_next_action(operation: str) -> str:
    return _SESSION_LIFECYCLE_NEXT_ACTIONS.get(str(operation or "").strip(), "")


# ---------------------------------------------------------------------------
# Non-gateway command families (MAN-139 follow-on). Same gap as the
# gateway-* family above -- until this section was added, run-record,
# thread-record, runtime-attachment, machine-lease, queue-transition,
# outbox-delivery, and run-routing all fell through to next_action="" the
# same way gateway-* used to, because this mock's dispatch simply had no
# branch for their command names. These were the single largest failure
# category in the post-84e3cb0f7 full suite run (~380 of ~900 failures),
# all presenting as "unexpected_next_action:missing" or "unexpected next_
# action for X-decision" -- a harness gap, not a product bug or a stale
# assertion. Ported the same way as the gateway-* tables: allow-path
# next_action only, per-command citations to the rust source, cross-checked
# against each operation's OWN expected-next-action table already declared
# in its Python caller where one exists (that caller table is what the
# test assertion actually checks against, so mirroring it directly is more
# reliable than re-deriving from rust independently).
# ---------------------------------------------------------------------------

# empyralis-runtime-kernel/src/thread_record.rs thread_record_decision_command(),
# lines 25-33 dispatch; each handler's allow-path next_action cited below
# (block-path branches omitted, per this mock's scope). Cross-checked
# against server_modules/thread_service.py's and runtime_runs_api.py's own
# _require_thread_next_action(decision, *allowed_actions) call sites.
_THREAD_RECORD_NEXT_ACTIONS = {
    "list_threads": "list_workspace_threads",  # rust line 52
    "normalize_thread": "normalize_thread_record",  # rust line 154
    "normalize_turn": "normalize_thread_turn_record",  # rust line 188
    "history_filter": "include_history_record",  # rust line 218
    "create_turn": "create_thread_turn",  # rust line 127
}


def _mock_thread_record_next_action(operation: str, payload: dict) -> str:
    op = str(operation or "").strip()
    if op == "get_thread":  # rust lines 65-106 -- the one operation whose
        # allow-path next_action is NOT a constant; record_missing + a
        # thread_id of "primary" hits the empty-primary-thread fallback,
        # everything else on the allow path is a real thread lookup.
        if payload.get("record_missing") and str(payload.get("thread_id") or "").strip() == "primary":
            return "return_empty_primary_thread"  # rust line 81
        return "get_thread_with_turns"  # rust line 98
    return _THREAD_RECORD_NEXT_ACTIONS.get(op, "")


# empyralis-runtime-kernel/src/runtime_attachment.rs (allow-path next_action
# only). Ported directly from server_modules/runtime_attachment_service.py's
# own _RUNTIME_ATTACHMENT_EXPECTED_NEXT_ACTIONS table -- the exact dict
# _enforce_rust_runtime_attachment_decision() checks the real kernel's
# response against, so mirroring it here is definitionally correct rather
# than a re-derivation that could drift from it.
_RUNTIME_ATTACHMENT_NEXT_ACTIONS = {
    "normalize_target": "normalize_runtime_target_id",
    "build_targets": "build_workspace_runtime_targets",
    "select_attachment": "select_runtime_attachment",
    "self_hosted_gate": "ensure_self_hosted_node_gate",
    "local_companion_gate": "select_local_companion_attachment",
    "usage_credit_event": "build_runtime_usage_credit_event",
}


def _mock_runtime_attachment_next_action(operation: str) -> str:
    return _RUNTIME_ATTACHMENT_NEXT_ACTIONS.get(str(operation or "").strip(), "")


# empyralis-runtime-kernel/src/lease.rs, allow-path next_action table at
# lines 342-344 (`("acquire", "allow") | ("renew", "allow") => ...`).
# Matches server_modules/machine_lease_service.py's own inline
# expected_next_action_map in _enforce_machine_lease_decision().
_MACHINE_LEASE_NEXT_ACTIONS = {
    "acquire": "persist_machine_lease_transition",
    "renew": "persist_machine_lease_transition",
    "release": "release_machine_lease_transition",
    "heartbeat": "touch_machine_lease",
}


def _mock_machine_lease_next_action(operation: str) -> str:
    return _MACHINE_LEASE_NEXT_ACTIONS.get(str(operation or "").strip(), "")


# empyralis-runtime-kernel/src/queue.rs, allow-path next_action table at
# lines 611-619. Matches server_modules/machine_lease_service.py's
# _enforce_queue_transition_decision() inline dict and
# server_modules/run_state_repository.py's
# _QUEUE_CLAIM_TRANSITION_NEXT_ACTIONS (a subset: claim/release/dead_letter).
_QUEUE_TRANSITION_NEXT_ACTIONS = {
    "enqueue": "enqueue_queue_item",
    "claim": "claim_queue_item",
    "complete": "complete_queue_item",
    "retry": "retry_queue_item",
    "cancel": "cancel_queue_item",
    "release": "release_queue_item",
    "dead_letter": "dead_letter_queue_item",
}
_QUEUE_TRANSITION_TERMINAL_STATUSES = {"completed", "failed", "cancelled"}  # rust line 283


def _mock_queue_transition_next_action(operation: str, payload: dict | None = None) -> tuple[str, str]:
    """Returns (next_action, reason). "fail" isn't a static lookup like every
    other operation here -- machine_lease_service.py's
    _enforce_queue_transition_decision() derives ITS expected next_action
    for "fail" from decision["retryable"]/decision["next_status"] rather
    than a fixed table (rust fail_decision(), lines 272-330+): an
    already-terminal item is idempotently re-reported as "fail_queue_item"
    (reason "queue_item_already_terminal", same shape as the state-
    transition "already_terminal" case above), an active item that hasn't
    hit max_attempts gets "schedule_queue_retry", and one that has gets
    "fail_queue_item" for real."""
    op = str(operation or "").strip()
    if op != "fail":
        return _QUEUE_TRANSITION_NEXT_ACTIONS.get(op, ""), ""
    data = payload or {}
    status = str(data.get("status") or data.get("current_status") or "").strip().lower()
    if status in _QUEUE_TRANSITION_TERMINAL_STATUSES:
        return "fail_queue_item", "queue_item_already_terminal"
    attempts = int(data.get("attempts") or 0)
    max_attempts = max(1, int(data.get("max_attempts") or 1))
    if attempts + 1 < max_attempts:
        return "schedule_queue_retry", "queue_failure_retry_scheduled"
    return "fail_queue_item", "queue_failure_terminal"


# empyralis-runtime-kernel/src/outbox_delivery.rs, allow-path next_action
# table (persist_event_decision et al., lines 33-~200). Ported directly from
# server_modules/outbox_service.py's own
# _OUTBOX_DELIVERY_EXPECTED_NEXT_ACTIONS table for the same reason as
# runtime-attachment above: it's the exact table the real caller checks
# against.
_OUTBOX_DELIVERY_NEXT_ACTIONS = {
    "persist_event": "persist_outbox_event",
    "list_undelivered": "list_undelivered_outbox_events",
    "claim_due": "claim_due_outbox_events",
    "patch_payload": "patch_outbox_event_payload",
    "mark_delivered": "mark_outbox_event_delivered",
    "record_failure": "record_outbox_delivery_failure",
    "list_poisoned": "list_poisoned_outbox_events",
    "delivery_status": "get_outbox_delivery_status",
}


def _mock_outbox_delivery_next_action(operation: str) -> str:
    return _OUTBOX_DELIVERY_NEXT_ACTIONS.get(str(operation or "").strip(), "")


# empyralis-runtime-kernel/src/run_record.rs run_record_decision_command(),
# lines 48-69 dispatch. Cross-checked against server_modules/
# run_state_repository.py's own _RUN_RECORD_NEXT_ACTIONS table -- the
# authoritative, complete (7-operation) version of this map that repository
# already declares and checks the real kernel's response against; two other
# callers (server_modules/run_execution_handle.py's
# _enforce_run_record_decision, server_modules/outbox_service.py's
# _enforce_run_record_outbox_decision) each independently redeclare a subset
# of the same table for their own operations.
def _mock_run_record_next_action(operation: str, payload: dict) -> str:
    op = str(operation or "").strip()
    if op == "register_live_run":  # rust lines 72-94, flat allow-path constant
        return "create_live_run_initial"
    if op == "persist_snapshot":  # rust lines 114-135, flat allow-path constant
        return "update_live_run_if_version_matches"
    if op == "archive_payload":  # rust lines 216-253, flat allow-path constant
        return "archive_run"
    if op == "record_transition":  # rust lines 154-205 -- noop iff from==to,
        # otherwise the transition literal (block-path branches -- terminal-
        # state lock, disallowed transition -- omitted, per this mock's scope)
        from_state = str(
            payload.get("from_state") or payload.get("previous_state") or payload.get("current_state") or ""
        ).strip()
        to_state = str(
            payload.get("to_state") or payload.get("next_state") or payload.get("state") or payload.get("status") or ""
        ).strip()
        return "noop" if from_state == to_state else "record_transition"
    if op == "emit_transition_outbox":  # rust lines 270-274
        from_state = str(payload.get("from_state") or "").strip()
        to_state = str(payload.get("to_state") or "").strip()
        return "noop" if from_state == to_state else "emit_run_transition_event"
    if op == "emit_artifact_outbox":  # rust lines 298-301
        try:
            artifact_count = int(payload.get("artifact_count") or 0)
        except (TypeError, ValueError):
            artifact_count = 0
        return "noop" if artifact_count == 0 else "emit_artifact_created_events"
    if op == "activate_live_run":  # rust lines 331-355
        selected_target = str(payload.get("selected_target") or payload.get("execution_target") or "").strip().lower()
        local_target = str(payload.get("local_companion_target") or payload.get("local_target") or "").strip().lower()
        if local_target and selected_target == local_target:
            return "hydrate_local_memory_context" if payload.get("defer_local_enqueue") else "enqueue_local_companion_run"
        return "start_background_run"
    return ""


# empyralis-runtime-kernel/src/run_routing.rs run_routing_decision_command(),
# lines 45-84 dispatch. execution_boundary and delegation_child are flat
# allow-path constants (block-path branches omitted, per this mock's
# scope); local_confirmation and delegation_merge are payload-conditional --
# both ported from their real rust conditions below, cross-checked against
# server_modules/run_service.py's and
# server_modules/runtime_run_delegation_service.py's next_action checks.
def _mock_run_routing_next_action(operation: str, payload: dict) -> str:
    op = str(operation or "").strip()
    if op == "execution_boundary":  # rust lines 141-153
        return "write_execution_boundary_metadata"
    if op == "routing_preview":  # rust lines 172-184
        return "build_routing_preview"
    if op == "delegation_child":  # rust lines 286-298
        return "create_delegated_child_run"
    if op == "local_confirmation":  # rust lines 196-252
        selected_target = str(
            payload.get("execution_target_selected") or payload.get("execution_target") or ""
        ).strip().lower()
        outcome_pack = str(payload.get("outcome_pack") or "").strip().lower()
        if selected_target != "local_companion" or outcome_pack != "local_execution":
            return "continue_without_confirmation"  # rust lines 199-228
        # blocked_count/require_confirmation_count/approval_required_count > 0
        # are block/require_approval branches (rust lines 230-247) -- out of
        # this mock's allow-path-only scope, matching the file's established
        # convention; a test exercising those needs its own explicit mock.
        return "continue_local_execution"  # rust lines 248-252
    if op == "delegation_merge":  # rust lines 310-363
        try:
            active_children = int(payload.get("active_children") or 0)
        except (TypeError, ValueError):
            active_children = 0
        if active_children > 0:
            return "wait_for_children"  # rust lines 310-323
        # waiting_children > 0 is a require_approval branch (rust lines
        # 325-336) -- out of scope, same reasoning as local_confirmation.
        try:
            failed_children = int(payload.get("failed_children") or 0)
        except (TypeError, ValueError):
            failed_children = 0
        if failed_children > 0:
            return "retry_failed_children"  # rust lines 338-349 (require_approval,
            # but the only caller checks next_action directly with
            # allow_approval_required=True and doesn't gate on decision --
            # see runtime_run_delegation_service.py's
            # _enforce_delegation_merge_retry_decision)
        return "merge_child_results"  # rust lines 351-363
    return ""


# empyralis-runtime-kernel/src/deployed_agent.rs / deployed_agent_service.rs
# (allow-path next_action only). Ported directly from two independently-
# declared Python tables that agree on every operation both cover:
# server_modules/deployed_agent_service.py's own
# _DEPLOYED_AGENT_SERVICE_NEXT_ACTIONS (the more complete one -- 20
# operations, single-value sets, i.e. flat/unconditional on the allow path)
# and server_modules/routes_deployed_agents.py's inline dict (15 operations,
# a subset, plus business_insight_review/business_insight_apply which
# deployed_agent_service.py's table doesn't cover). Not independently
# re-derived from the rust source line-by-line the way run-routing/
# run-record above were -- lower confidence than those two, flagged as such
# in this session's report.
_DEPLOYED_AGENT_DECISION_NEXT_ACTIONS = {
    "create_draft": "create_draft",
    "list": "read_agent",
    "read": "read_agent",
}

_DEPLOYED_AGENT_SERVICE_NEXT_ACTIONS = {
    "telegram_readiness": "read_telegram_readiness",
    "analytics_read": "read_deployed_agent_analytics",
    "admin_dashboard": "read_deployed_agent_admin_dashboard",
    "audit_export": "export_deployed_agent_audit_logs",
    "memory_list": "list_deployed_agent_memory",
    "activity_list": "list_deployed_agent_activity",
    "conversation_list": "list_deployed_agent_conversations",
    "conversation_detail": "read_deployed_agent_conversation_detail",
    "external_user_delete": "delete_deployed_agent_external_user_data",
    "knowledge_verify": "verify_deployed_agent_knowledge",
    "knowledge_upload": "upload_deployed_agent_knowledge_reference",
    "business_insight_review": "review_deployed_agent_business_insight",
    "business_insight_apply": "apply_deployed_agent_business_insight",
    "shop_evaluate": "evaluate_shop_assistant",
    "test_turn": "execute_deployed_agent_test_turn",
    "deploy": "deploy_deployed_agent",
    "pause": "pause_deployed_agent",
    "kill": "kill_deployed_agent",
    "recover": "recover_deployed_agent",
    "archive": "archive_deployed_agent",
    "recovery_action": "apply_deployed_agent_recovery_action",
    "runtime_session_kill": "kill_deployed_agent_runtime_session",
    "emergency_stop": "emergency_stop_workspace_deployed_agents",
}


def _mock_deployed_agent_decision_next_action(operation: str) -> str:
    return _DEPLOYED_AGENT_DECISION_NEXT_ACTIONS.get(str(operation or "").strip(), "")


def _mock_deployed_agent_service_next_action(operation: str) -> str:
    return _DEPLOYED_AGENT_SERVICE_NEXT_ACTIONS.get(str(operation or "").strip(), "")


# empyralis-runtime-kernel/src/deployed_readiness.rs deployed_readiness_
# decision_command(), dispatches by a "stage" field (not "operation" like
# every other command above). Only "stage": "test_turn" is actually called
# from Python (server_modules/deployed_agent_test_turn_service.py's
# _enforce_deployed_test_turn_readiness) even though the rust command
# handles other stages too -- rust line 271 confirms the allow-path
# next_action for test_turn.
def _mock_deployed_readiness_next_action(payload: dict) -> str:
    stage = str(payload.get("stage") or "").strip()
    if stage == "test_turn":
        return "execute_studio_test_turn"
    return ""


# ---------------------------------------------------------------------------
# "run-api-decision" and "run-service-decision" (MAN-139 phase 2). Neither
# command had a branch in the mock below at all -- same shape of gap the
# five "gateway-*" commands had before MAN-139 phase 1 (see the block
# comment above _mock_gateway_service_next_action): every caller
# (~12 for run-api-decision -- runtime_runs_api.py, turn_ingress_service.py,
# runtime_run_replay_service.py, runtime_usage_service.py,
# runtime_history_service.py, runtime_run_query_service.py,
# runtime_run_entry_service.py, runtime_run_resume_service.py,
# runtime_route_registry_service.py; run_service.py for run-service-decision)
# silently got next_action="" and rejected every non-kernel test that
# exercised it. Allow-path only, per the same convention as every mock
# above; both are ported from the cited rust sources.
# ---------------------------------------------------------------------------

# empyralis-runtime-kernel/src/run_api.rs run_api_decision_command(), allow()
# call sites only (lines 106-266) -- block()/require_approval() branches
# intentionally omitted, same as every other mock in this file.
_RUN_API_NEXT_ACTIONS = {
    "list_runs": "list_runs",
    "get_run": "get_run",
    "start_turn": "start_turn",
    "start_run": "start_run",
    "stream_chat": "start_chat_stream",
    "cancel_run": "cancel_run",
    "pause_run": "pause_run",
    "retry_run": "retry_run",
    "resume_run": "resume_run",
    "approve_run": "resolve_run_approval",
    "webhook_trigger": "trigger_webhook_run",
}


def _mock_run_api_next_action(operation: str) -> str:
    return _RUN_API_NEXT_ACTIONS.get(str(operation or "").strip(), "")


# empyralis-runtime-kernel/src/run_service.rs run_service_decision_command(),
# allow-path next_action derivation only (rust lines 272-304 minus the
# blocked/approval_required arms, which this mock never produces).
def _mock_run_service_next_action(operation: str, payload: dict) -> str:
    op = str(operation or "").strip()
    trigger_source = str(payload.get("trigger_source") or "user").strip()
    external_trigger = trigger_source == "webhook" or op == "webhook_trigger"
    if payload.get("duplicate_request"):
        return "return_idempotent_run_result"
    if op == "create":
        return "create_run_record"
    if op in ("start", "dispatch", "lane_route"):
        return "dispatch_run_to_runtime"
    if op == "turn":
        return "append_run_turn"
    if op in ("stream_open", "stream_event"):
        return "open_or_forward_run_stream"
    if op == "cancel":
        return "cancel_run"
    if op == "retry":
        return "retry_run"
    if op == "resume":
        return "resume_run"
    if op in ("approve", "reject"):
        return "persist_run_approval_decision"
    if op in ("finalize_success", "finalize_failure"):
        return "finalize_run"
    if external_trigger:
        return "create_run_from_webhook"
    if op == "child_run_create":
        return "create_child_run"
    if op == "delegation_merge":
        return "merge_delegated_run"
    return "return_run_status"


# empyralis-runtime-kernel/src/local_worker.rs local_worker_decision_command(),
# allow-path next_action only (block()/require_approval() branches
# intentionally omitted, same convention as every mock above). Also missing
# from this mock entirely until MAN-139 phase 2 -- worker_dispatch_service.py
# calls "local-worker-decision" for claim_run/worker_heartbeat/run_heartbeat/
# complete_run/pause_run/fail_run/control_state/get_queue/cleanup_queue/
# execute_command, none of which this mock recognized, so every one of those
# non-kernel tests got next_action="" and either an unexpected-next-action
# 423 or (for claim_local_run, which swallows that 423 into a plain None
# return) a silent wrong-result failure.
_LOCAL_WORKER_TERMINAL_RUN_STATUSES = {  # rust lines 3-11
    "completed",
    "succeeded",
    "failed",
    "cancelled",
    "canceled",
    "timeout",
    "timed_out",
}


def _mock_local_worker_next_action(operation: str, payload: dict) -> str:
    op = str(operation or "").strip()
    if op == "get_queue":
        return "read_local_queue"  # rust line 158
    if op == "cleanup_queue":  # rust lines 168-208
        if not payload.get("dry_run", True) and not payload.get("operator_confirmed", False):
            return "request_cleanup_confirmation"
        return "cleanup_stale_local_queue"
    if op == "worker_status":
        return "read_worker_status"  # rust line 69
    if op in ("worker_heartbeat", "runtime_heartbeat"):  # rust lines 232-236
        return "record_runtime_heartbeat" if op == "runtime_heartbeat" else "record_worker_heartbeat"
    if op == "claim_run":  # rust lines 259-282
        return "return_backpressure" if payload.get("queue_empty") else "claim_local_run"
    if op == "run_heartbeat":
        return "record_run_heartbeat"  # rust line 306
    if op == "control_state":  # rust lines 331-354
        run_status = str(payload.get("run_status") or payload.get("status") or "").strip().lower()
        if run_status in _LOCAL_WORKER_TERMINAL_RUN_STATUSES:
            return "return_terminal_control_state"
        return "read_run_control_state"
    if op == "complete_run":
        return "complete_local_run"  # rust line 384
    if op == "pause_run":
        return "pause_local_run"  # rust line 418
    if op == "fail_run":
        return "fail_local_run"  # rust line 452
    if op == "execute_command":
        return "execute_hardware_command"  # rust line 642
    return ""


# empyralis-runtime-kernel/src/state.rs state_transition_decision_command(),
# "complete"/"fail" arms only -- the only two operations
# worker_dispatch_service.py's _enforce_run_state_transition_decision()
# actually calls ("state-transition-decision" was, like "local-worker-
# decision" above, entirely unhandled by this mock until MAN-139 phase 2).
# Mirrors complete_decision()/fail_decision()'s already-terminal short
# circuit (rust lines 175-237) and next_action()'s operation/next_state
# derivation (rust lines 410-419): "complete" always resolves to
# "complete_state_transition", but "fail" only resolves to
# "fail_state_transition" when the run is already failed or freshly
# transitioning to failed -- any OTHER already-terminal state (completed,
# cancelled, archived) resolves to "review_state_transition" instead, since
# the kernel refuses to silently relabel a completed/cancelled run as
# failed.
#
# Returns (next_action, reason) -- worker_dispatch_service.py's idempotent-
# terminal short circuit (complete_local_run/fail_local_run, "...already_
# terminal" checks) keys off decision["reason"] == "state_already_terminal",
# not next_action (next_action is identical whether this is a fresh
# transition or a no-op replay of an already-terminal state), so the mock
# has to reproduce the real "reason" string here too, not just next_action
# like every other mock in this file gets away with.
_STATE_TRANSITION_TERMINAL_STATES = {"completed", "failed", "cancelled", "archived"}  # rust line 5


def _mock_state_transition_next_action(operation: str, payload: dict) -> tuple[str, str]:
    op = str(operation or "").strip()
    current_state = str(payload.get("current_state") or "").strip().lower()
    already_terminal = current_state in _STATE_TRANSITION_TERMINAL_STATES
    reason = "state_already_terminal" if already_terminal else ""  # rust lines 184/216
    if op == "complete":
        return "complete_state_transition", reason
    if op == "fail":
        next_state = current_state if already_terminal else "failed"
        next_action = "fail_state_transition" if next_state == "failed" else "review_state_transition"
        return next_action, reason
    return "", ""


# empyralis-runtime-kernel/src/authorization.rs authorize_request_command(),
# execution_authorization.rs authorize_execution_command(), and
# execution_plan.rs execution_plan_command() -- three separate commands
# policy_service.py's evaluate_tool_policy_decision() calls (only "authorize-
# execution"/"execution-plan" for tool_id == "shell.execute" with a raw
# command; "authorize-request" for every other tool, which is most of them).
# None were handled by this mock at all until MAN-139 phase 2, so EVERY
# workflow-graph tool node (runs_execution.py) and every direct/skill tool
# call that goes through evaluate_tool_policy_decision() got next_action=""
# and hit "Tool node '...' is blocked by runtime policy." on an otherwise
# uneventful allow.
#
# Each of these three commands' rust source is a multi-stage pipeline
# (safe-mode gate -> policy-context gate -> capability/domain/path policy
# rules -> risk classification -> approval-requirement check, each its own
# ~200-450 line module: policy.rs, risk.rs, approvals.rs, safe_mode.rs) --
# genuinely not worth hand-porting into this mock the way the simpler
# operation-keyed commands above are, especially since none of the three
# tests it would need to pass a specific block/require_approval scenario
# rely on the AUTOUSE mock for that (they patch run_runtime_kernel_enforced
# directly, same as every other block/require_approval test in this file).
# All three commands resolve to the exact same next_action on their allow
# path regardless of which one is called (rust authorization.rs line 137,
# execution_authorization.rs, execution_plan.rs's `expected_plan_next_
# action` counterpart), so this only needs to cover that one shared value.
_TOOL_AUTHORIZATION_COMMANDS = {"authorize-request", "authorize-execution", "execution-plan"}
_TOOL_AUTHORIZATION_ALLOW_NEXT_ACTION = "allow_tool_execution"


@pytest.fixture(autouse=True)
def _skip_kernel_tests_when_binary_missing(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch):
    """Skip @pytest.mark.kernel tests when the Rust kernel binary is absent.

    For non-kernel tests, monkeypatch run_runtime_kernel to return a mock
    "allow" decision so business-logic tests are not blocked by a missing
    compiled binary.  Production kernel gates are NOT modified — only the
    test-time call to the kernel is replaced.
    """
    from server_modules.rust_runtime_kernel_client import runtime_kernel_binary

    if request.node.get_closest_marker("kernel") is not None:
        if runtime_kernel_binary() is None:
            pytest.skip(
                "requires empyralis-runtime-kernel binary (not built in this environment)"
            )
        return  # kernel binary present — let the test use the real kernel

    # Non-kernel tests: mock out run_runtime_kernel so the missing binary
    # does not block business-logic test paths.
    def _mock_run_runtime_kernel(command: str, payload, timeout_seconds: int = 5):
        import copy
        normalized_payload = payload if isinstance(payload, dict) else {}

        # Several Rust kernel commands derive `next_action` from the
        # requested `operation` on an "allow" decision, but the mapping is
        # NOT always the identity function — callers (memory_service.py,
        # sage_memory_service.py, control_plane_repository.py,
        # gateway_pairing_service.py, gateway_state_repository.py,
        # routes_gateway.py, etc.) compare next_action against the real
        # kernel's per-operation contract before they'll persist state or
        # let a request through, so a blanket echo (or an unhandled command
        # family, which is what left the five "gateway-*" commands returning
        # next_action="" until MAN-139) produces false gate failures. Each
        # branch below reproduces the real kernel source it's named after;
        # see the per-command helper functions above for file:line citations.
        next_action = ""
        reason_override = ""
        if command == "runtime-state-store-decision":
            next_action = _mock_runtime_state_store_next_action(normalized_payload.get("operation"))
        elif command == "control-plane-service-decision":
            next_action = _mock_control_plane_service_next_action(
                normalized_payload.get("operation"), normalized_payload
            )
        elif command == "control-plane-decision":
            next_action = _mock_control_plane_record_next_action(
                normalized_payload.get("operation"), normalized_payload
            )
        elif command == "gateway-service-decision":
            next_action = _mock_gateway_service_next_action(normalized_payload.get("operation"))
        elif command == "gateway-action-decision":
            next_action = _mock_gateway_action_next_action(normalized_payload.get("operation"))
        elif command == "gateway-protocol-decision":
            next_action = _mock_gateway_protocol_next_action(normalized_payload)
        elif command == "gateway-frame-decision":
            next_action = _mock_gateway_frame_next_action(normalized_payload)
        elif command == "gateway-state-decision":
            next_action = _mock_gateway_state_next_action(
                normalized_payload.get("operation"), normalized_payload
            )
        elif command == "session-lifecycle-decision":
            next_action = _mock_session_lifecycle_next_action(normalized_payload.get("operation"))
        elif command == "thread-record-decision":
            next_action = _mock_thread_record_next_action(
                normalized_payload.get("operation"), normalized_payload
            )
        elif command == "runtime-attachment-decision":
            next_action = _mock_runtime_attachment_next_action(normalized_payload.get("operation"))
        elif command == "machine-lease-decision":
            next_action = _mock_machine_lease_next_action(normalized_payload.get("operation"))
        elif command == "queue-transition-decision":
            next_action, reason_override = _mock_queue_transition_next_action(
                normalized_payload.get("operation"), normalized_payload
            )
        elif command == "outbox-delivery-decision":
            next_action = _mock_outbox_delivery_next_action(normalized_payload.get("operation"))
        elif command == "run-record-decision":
            next_action = _mock_run_record_next_action(
                normalized_payload.get("operation"), normalized_payload
            )
        elif command == "run-routing-decision":
            next_action = _mock_run_routing_next_action(
                normalized_payload.get("operation"), normalized_payload
            )
        elif command == "deployed-agent-decision":
            next_action = _mock_deployed_agent_decision_next_action(normalized_payload.get("operation"))
        elif command == "deployed-agent-service-decision":
            next_action = _mock_deployed_agent_service_next_action(normalized_payload.get("operation"))
        elif command == "deployed-readiness-decision":
            next_action = _mock_deployed_readiness_next_action(normalized_payload)
        elif command == "run-api-decision":
            next_action = _mock_run_api_next_action(normalized_payload.get("operation"))
        elif command == "run-service-decision":
            next_action = _mock_run_service_next_action(
                normalized_payload.get("operation"), normalized_payload
            )
        elif command == "local-worker-decision":
            next_action = _mock_local_worker_next_action(
                normalized_payload.get("operation"), normalized_payload
            )
        elif command == "state-transition-decision":
            next_action, reason_override = _mock_state_transition_next_action(
                normalized_payload.get("operation"), normalized_payload
            )
        elif command in _TOOL_AUTHORIZATION_COMMANDS:
            next_action = _TOOL_AUTHORIZATION_ALLOW_NEXT_ACTION

        result = {
            "ok": True,
            "decision": "allow",
            "command": command,
            "decision_id": "rkd_mock_non_kernel_test",
            "reason": reason_override or "mock allow (non-kernel test fixture)",
            "next_action": next_action,
            "payload": copy.deepcopy(normalized_payload),
        }
        # See _mock_control_plane_service_mutation_plan's docstring comment
        # above: several non-test callers read decision["mutation_plan"]
        # directly (not just top-level next_action) and 423 if it's absent.
        # Only control-plane-service-decision's consumers were found to
        # require this; every other command's real "mutation_plan" shape (if
        # it has one at all) differs enough that a one-size mock would be
        # wrong, so this is deliberately scoped to the one command that
        # needed it rather than added to every branch above.
        if command == "control-plane-service-decision":
            result["mutation_plan"] = _mock_control_plane_service_mutation_plan(
                normalized_payload.get("operation"), normalized_payload, next_action
            )
        return result

    try:
        from server_modules import rust_runtime_kernel_client as _rk
    except Exception:
        return
    monkeypatch.setattr(_rk, "run_runtime_kernel", _mock_run_runtime_kernel)


# A handful of tests (test_auth.py, test_channel_pairing.py,
# test_cross_surface_continuity_phase80.py) call importlib.reload() on
# server_modules.auth and a few modules it depends on, to get genuinely
# fresh module-level state for env-var-driven behavior (JWT secret file
# path, DSN caching, rate-limit buckets). reload() mutates the SAME module
# object's __dict__ in place -- every OTHER module that already did
# `from server_modules import auth as auth_module` and baked
# `Depends(auth_module.get_current_user)` into a FastAPI route (at that
# module's own first import, during collection, long before any test body
# runs) keeps pointing at the pre-reload function object forever after,
# for the rest of the pytest session. A test elsewhere in the suite that
# does `app.dependency_overrides[routes_x.auth_module.get_current_user] =
# ...` reads the CURRENT (reloaded) function as its override key, which no
# longer matches what's baked into the route's dependant tree -- FastAPI
# falls through to the REAL get_current_user, and an unrelated test many
# files away starts getting a real 401/403 instead of what it mocked.
#
# Confirmed responsible for 56 of the 613 MAN-139 failures: diffing a full
# run against one with only those three files excluded via --ignore drops
# 613 -> 557, and none of the 56 are in the three excluded files
# themselves (their own tests all pass) -- pure collateral damage on test
# order. Reproducible directly too: test_routes_fleet_member_rbac.py's 30
# tests all pass standalone or as a file, and all fail when the full
# ordered suite reaches them, because test_auth.py already ran and
# reloaded auth.py by then.
#
# Snapshotting + restoring these modules' __dict__ around every test
# (not just the three offending files, so this is self-healing regardless
# of which file reloads what and regardless of collection order) fixes it
# without touching those three files' own reload-based test strategy --
# they still get a genuinely fresh module for the duration of their own
# test, they just stop leaking that fresh module to everyone downstream.
_RELOAD_SENSITIVE_MODULE_NAMES = (
    "server_modules.auth",
    "server_modules.db",
    "server_modules.control_plane_repository",
    "server_modules.jwt_secret",
    "server_modules.channel_pairing_service",
)


@pytest.fixture(autouse=True)
def _restore_reload_sensitive_modules():
    import sys

    snapshots: dict[str, dict] = {}
    for module_name in _RELOAD_SENSITIVE_MODULE_NAMES:
        module = sys.modules.get(module_name)
        if module is not None:
            snapshots[module_name] = dict(vars(module))
    yield
    for module_name, snapshot in snapshots.items():
        module = sys.modules.get(module_name)
        if module is None:
            continue
        current = vars(module)
        for key, value in snapshot.items():
            if current.get(key) is not value:
                try:
                    setattr(module, key, value)
                except Exception:
                    pass
        for key in list(current):
            if key not in snapshot and not key.startswith("__"):
                try:
                    delattr(module, key)
                except Exception:
                    pass


@pytest.fixture(autouse=True)
def _isolate_empyralis_state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Keep tests from reading or writing the developer's real local state."""
    state_home = tmp_path / "empyralis-state"
    auth_db = state_home / "auth" / "users.db"
    gateway_db = state_home / "gateway" / "gateway-state.sqlite3"
    personal_channels_db = state_home / "channels" / "personal-channels.sqlite3"
    runtime_db = state_home / "runtime" / "state.db"
    setup_sessions_file = state_home / "setup" / "sessions.json"
    provider_profiles_file = state_home / "providers" / "profiles.json"
    idempotency_file = state_home / "runtime" / "idempotency.json"
    for path in (auth_db, gateway_db, personal_channels_db, runtime_db, setup_sessions_file, provider_profiles_file, idempotency_file):
        path.parent.mkdir(parents=True, exist_ok=True)

    monkeypatch.setenv("EMPYRALIS_STATE_HOME", str(state_home))
    monkeypatch.setenv("ORION_RUNTIME_STATE_DB", str(runtime_db))
    monkeypatch.setenv("EMPYRALIS_GATEWAY_STATE_DB", str(gateway_db))
    monkeypatch.setenv("ORION_SETUP_SESSIONS_FILE", str(setup_sessions_file))
    monkeypatch.setenv("ORION_PROVIDER_PROFILES_FILE", str(provider_profiles_file))
    monkeypatch.setenv("ORION_IDEMPOTENCY_FILE", str(idempotency_file))

    try:
        from server_modules import auth

        monkeypatch.setattr(auth, "AUTH_DB_FILE", auth_db, raising=False)
    except Exception:
        pass
    try:
        from server_modules import control_plane_repository

        monkeypatch.setattr(control_plane_repository, "LOCAL_IDENTITY_DB_FILE", auth_db, raising=False)
    except Exception:
        pass
    try:
        from server_modules import gateway_state_repository

        monkeypatch.setattr(gateway_state_repository, "GATEWAY_STATE_DB_FILE", gateway_db, raising=False)
    except Exception:
        pass
    try:
        from server_modules import personal_channels_repository

        monkeypatch.setattr(personal_channels_repository, "PERSONAL_CHANNELS_DB_FILE", personal_channels_db, raising=False)
    except Exception:
        pass
    try:
        from server_modules import runtime_config

        monkeypatch.setattr(runtime_config, "ORION_RUNTIME_STATE_DB", runtime_db, raising=False)
        monkeypatch.setattr(runtime_config, "ORION_SETUP_SESSIONS_FILE", setup_sessions_file, raising=False)
        monkeypatch.setattr(runtime_config, "ORION_PROVIDER_PROFILES_FILE", provider_profiles_file, raising=False)
        monkeypatch.setattr(runtime_config, "ORION_IDEMPOTENCY_FILE", idempotency_file, raising=False)
    except Exception:
        pass
    try:
        from server_modules import shared

        shared.sync_acp_manager_paths(
            runtime_db_path=runtime_db,
            setup_sessions_path=setup_sessions_file,
            provider_profiles_path=provider_profiles_file,
            idempotency_path=idempotency_file,
        )
    except Exception:
        pass


class _InMemoryDcrVault:
    """Stand-in for `vault_store` used by oauth_dynamic_client_store in tests.

    Rows live in a plain dict instead of Postgres, but the CIPHERTEXT is
    produced by the real vault encryption routine under a fixed test
    passphrase — so a test can still assert that a client_secret is stored
    encrypted rather than in the clear, without a database and without
    touching the developer's real vault.
    """

    PASSPHRASE = "test-only-dcr-vault-passphrase"

    def __init__(self) -> None:
        self.rows: dict = {}

    # -- encryption (real algorithm, test passphrase) --
    def _openssl_encrypt(self, plaintext: str) -> str:
        from server_modules import vault_store

        return vault_store._openssl_encrypt_with_passphrase(plaintext, self.PASSPHRASE)

    def _openssl_decrypt(self, ciphertext: str) -> str:
        from server_modules import vault_store

        return vault_store._openssl_decrypt_with_passphrase(ciphertext, self.PASSPHRASE)

    # -- single-row storage API --
    def get_credential(self, credential_id: str):
        return self.rows.get(str(credential_id))

    def list_credentials(self) -> list:
        return list(self.rows.values())

    def upsert_credential(self, entry: dict) -> dict:
        import copy

        stored = copy.deepcopy(entry)
        stored.setdefault("created_at", "2026-01-01T00:00:00Z")
        stored["updated_at"] = f"2026-01-01T00:00:{len(self.rows):02d}Z"
        self.rows[str(entry.get("id"))] = stored
        return stored

    def delete_credential(self, credential_id: str) -> bool:
        return self.rows.pop(str(credential_id), None) is not None


@pytest.fixture(autouse=True)
def dcr_client_vault(monkeypatch: pytest.MonkeyPatch) -> _InMemoryDcrVault:
    """Autouse guard: dynamically-registered OAuth clients (MAN-124) are now
    persisted in the encrypted credential vault. Without this, any test that
    exercises the DCR path would write real rows into whatever database
    DATABASE_URL points at — and read them back in the NEXT test session,
    which is exactly how these tests started failing for each other.

    Tests that want to inspect what was persisted can request this fixture.
    """
    fake = _InMemoryDcrVault()
    try:
        from server_modules import oauth_dynamic_client_store

        monkeypatch.setattr(oauth_dynamic_client_store, "_vault", lambda: fake)
    except Exception:
        pass
    return fake


@pytest.fixture
def second_real_user_in_workspace():
    """Factory fixture for multiplayer-correctness tests: register a REAL,
    distinct second user and (by default) attach them to a target
    workspace_id, all through the real repository/DB path -- never the
    synthetic current_user dict pattern used elsewhere (e.g.
    test_routes_fleet_delete_agent.py's _viewer_user()/_member_user(), which
    hand-builds a workspace_access map and never touches a database).

    Concretely, per call:
      1. auth.register_user(email, password) -- the exact function
         POST /auth/register calls -- persists a brand-new user row (and
         its own bootstrap workspace) into the SAME per-test SQLite file
         _isolate_empyralis_state above already points both auth.AUTH_DB_FILE
         and control_plane_repository.LOCAL_IDENTITY_DB_FILE at.
      2. auth.upsert_workspace_membership(user_id, workspace_id, role) --
         which wraps control_plane_repository.ensure_workspace_membership,
         the real repository write -- attaches that real user to the target
         workspace_id as a second, distinct member.
      3. auth._effective_workspace_access(...) reads the resulting
         membership rows back out of the real DB (via
         auth._list_workspace_memberships ->
         control_plane_repository.list_workspace_memberships_for_user) the
         same way auth.get_current_user builds workspace_access for a
         genuine bearer session -- so the returned current_user dict is
         DB-derived, not hand-typed.

    Returns {"user_id", "email", "current_user"}; hand `current_user` to
    app.dependency_overrides[get_current_user] = lambda: result["current_user"].

    Pass join_workspace=False to get a real, authenticated user who is NOT
    yet a member of workspace_id -- e.g. to drive the invite-accept endpoint
    itself and assert IT is what creates the membership.
    """

    def _make(
        workspace_id: str,
        *,
        role: str = "member",
        email: str | None = None,
        name: str | None = None,
        password: str = "Sup3r-Secret-Passw0rd!",
        join_workspace: bool = True,
    ) -> dict:
        from server_modules import auth

        clean_email = (email or f"invitee-{uuid.uuid4().hex[:10]}@example.com").strip().lower()
        auth.register_user(clean_email, password, name=name)
        user = auth._find_user_by_email(clean_email)
        assert isinstance(user, dict) and user.get("id"), (
            "real registration did not persist a user row for " + clean_email
        )
        user_id = str(user["id"]).strip()

        if join_workspace:
            auth.upsert_workspace_membership(user_id, workspace_id, role)

        workspace_access = auth._effective_workspace_access(
            user_id=user_id,
            email=clean_email,
            role=role,
            auth_type="bearer",
            is_admin=False,
            workspace_ids=[workspace_id] if join_workspace else [],
        )
        current_user = {
            "user_id": user_id,
            "auth_type": "bearer",
            "email": clean_email,
            "workspace_ids": list(workspace_access.keys()),
            "workspace_roles": {
                wid: entry.get("role") for wid, entry in workspace_access.items() if isinstance(entry, dict)
            },
            "workspace_access": workspace_access,
            "role": (workspace_access.get(workspace_id) or {}).get("role", "viewer"),
            "is_admin": False,
            "auth_admin": False,
        }
        return {"user_id": user_id, "email": clean_email, "current_user": current_user}

    return _make


# ---------------------------------------------------------------------------
# Stable alias so a test can reach THIS module's state (notably the egress
# guard's arm/disarm flag and its recorded violations, which live in these
# globals). A plain `import conftest` is not safe here: server_modules/tests/
# e2e/conftest.py has no __init__.py either, so both files are imported under
# the same bare top-level name and whichever lands in sys.modules last wins --
# which silently pointed test_live_provider_egress_guard.py at the wrong
# module during a full-suite run. Re-registering the already-loaded module
# object under an unambiguous name keeps the test looking at the same globals
# pytest is actually driving, never a second copy.
sys.modules.setdefault("empyralis_tests_egress_guard", sys.modules[__name__])
