"""Lint-style guards from docs/design/audit-silent-failures.md's
"Recommended guardrails" section:

1. Ban bare ``except Exception: pass`` (and bare ``except: pass``) -- a
   handler whose ENTIRE body is the single statement ``pass``, with zero
   logging, zero re-raise, zero anything. This is deliberately the
   narrowest, most literal reading of the pattern (not "any broad except
   with no log call") -- the audit itself found that the overwhelming
   majority of this repo's ~280 ``except Exception`` sites are a
   defensible pattern (best-effort telemetry AFTER the real side effect
   already completed, or a safe-default return), and a broader heuristic
   flags hundreds of those legitimate sites as noise. The pure-``pass``
   shape is the one that is NEVER defensible -- it cannot even be argued
   as "logged elsewhere" because there's nothing else in the block at all.
2. Flag ``asyncio.create_task(...)`` / ``asyncio.ensure_future(...)``
   called as a bare statement (its return value discarded) -- the
   fire-and-forget footgun documented at C2 (personal_channels_service.py)
   and M2 (tool_broker.py): the event loop only weakly references such a
   task, so it can be garbage-collected mid-flight with zero warning.

Both checks are seeded with the current, real violation count as of this
pass (docs/design/audit-silent-failures.md's audit date) -- grandfathered
at the FILE level (not file:line) deliberately: a line-number allowlist
would spuriously "break" on every unrelated edit that shifts line numbers
elsewhere in an allowlisted file, which is exactly the kind of brittle CI
check nobody trusts. The trade-off, stated plainly: a NEW violation added
to an ALREADY-allowlisted file is not caught by this check -- only a
violation in a file with zero prior violations is. This is intentionally
the cheap, non-disruptive version the audit asked for, not a ratchet that
drives the count to zero. The count itself (242 bare-except-pass sites
across 96 files; 19 unassigned create_task/ensure_future sites across 12
files, at the time this check was added) is the finding -- see this
module's report in the wave that added it.
"""

from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SERVER_MODULES_DIR = ROOT / "server_modules"

EXCLUDED_DIR_NAMES = {"tests", "__pycache__"}


def _iter_module_files() -> list[Path]:
    files = []
    for p in SERVER_MODULES_DIR.rglob("*.py"):
        if any(part in EXCLUDED_DIR_NAMES for part in p.parts):
            continue
        files.append(p)
    return sorted(files)


def find_bare_except_pass_lines(source: str) -> list[int]:
    """Line numbers of every ``except Exception:`` / bare ``except:``
    handler whose body is EXACTLY ``pass`` -- nothing else, not even a
    docstring-like leading string expression."""
    tree = ast.parse(source)
    lines: list[int] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.ExceptHandler):
            continue
        is_broad = (
            node.type is None
            or (isinstance(node.type, ast.Name) and node.type.id == "Exception")
            or (isinstance(node.type, ast.Attribute) and node.type.attr == "Exception")
        )
        if is_broad and len(node.body) == 1 and isinstance(node.body[0], ast.Pass):
            lines.append(node.lineno)
    return lines


def find_unassigned_fire_and_forget_lines(source: str) -> list[tuple[int, str]]:
    """Line numbers (and function name) of every bare-statement
    ``asyncio.create_task(...)`` / ``ensure_future(...)`` call -- i.e. its
    return value is not assigned to a variable, appended to a set, or
    otherwise kept alive."""
    tree = ast.parse(source)
    hits: list[tuple[int, str]] = []
    parents: dict[int, ast.AST] = {}
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            parents[id(child)] = node
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        fn = node.func
        name = None
        if isinstance(fn, ast.Attribute) and fn.attr in ("create_task", "ensure_future"):
            name = fn.attr
        elif isinstance(fn, ast.Name) and fn.id in ("create_task", "ensure_future"):
            name = fn.id
        if name and isinstance(parents.get(id(node)), ast.Expr):
            hits.append((node.lineno, name))
    return hits


# ---------------------------------------------------------------------------
# Seeded baselines -- real files, verified as of this pass. Grandfathered at
# file granularity (see module docstring for why). Prefer fixing entries
# off this list over adding to it; if a fix removes a file's last
# violation, remove it here too (test_..._allowlist_has_no_stale_entries
# below enforces that removal isn't optional).
# ---------------------------------------------------------------------------

BARE_EXCEPT_PASS_BASELINE_FILES: frozenset[str] = frozenset({
    "server_modules/acp_manager.py",
    "server_modules/agent_completion_notification_service.py",
    "server_modules/agent_memory.py",
    "server_modules/agent_memory_tools.py",
    "server_modules/agent_policy_context.py",
    "server_modules/agent_registry_repository.py",
    "server_modules/agent_turn.py",
    "server_modules/auth.py",
    "server_modules/bounded_scheduler_service.py",
    "server_modules/browser_engine.py",
    "server_modules/cli_companion_service.py",
    "server_modules/command_registry.py",
    "server_modules/compaction_service.py",
    "server_modules/connection_catalog_service.py",
    "server_modules/connectors/autopilot_connector_config.py",
    "server_modules/connectors/autopilot_runtime_support_service.py",
    "server_modules/connectors/autopilot_workflow_setup_service.py",
    "server_modules/connectors/discord_bot_runtime_service.py",
    "server_modules/connectors/discord_connector.py",
    "server_modules/connectors/dropbox_connector.py",
    "server_modules/connectors/github_connector.py",
    "server_modules/connectors/smtp_connector.py",
    "server_modules/connectors/telegram/media.py",
    "server_modules/db.py",
    "server_modules/dedicated_workstation_setup_service.py",
    "server_modules/deployed_agent_service.py",
    "server_modules/deployed_agent_test_turn_service.py",
    "server_modules/direct_chat_generation_service.py",
    "server_modules/direct_chat_handoff_service.py",
    "server_modules/direct_chat_hosted_usage_service.py",
    "server_modules/direct_tool_execution_service.py",
    "server_modules/docker_execution_sandbox.py",
    "server_modules/doctor_gate.py",
    "server_modules/durability_signal.py",
    "server_modules/fleet_tools.py",
    "server_modules/gateway_credential_lock.py",
    "server_modules/gateway_execution_service.py",
    "server_modules/gateway_pairing_service.py",
    "server_modules/gateway_protocol_service.py",
    "server_modules/google_workspace_cli.py",
    "server_modules/hardware_action_broker_service.py",
    "server_modules/hardware_runtime_adapters/gateway_adapter.py",
    "server_modules/hardware_runtime_adapters/self_hosted_node_adapter.py",
    "server_modules/hosted_bot_provisioning_service.py",
    "server_modules/hosted_secure_worker.py",
    "server_modules/installed_solutions.py",
    "server_modules/jwt_secret.py",
    "server_modules/kill_switch_gate.py",
    "server_modules/local_tool_executor.py",
    "server_modules/mcp_registry_service.py",
    "server_modules/memory_service.py",
    "server_modules/mini_app_host_service.py",
    "server_modules/notification_service.py",
    "server_modules/outcome_packs.py",
    "server_modules/personal_channel_sage_bridge_service.py",
    "server_modules/personal_context_engine.py",
    "server_modules/product_catalog_live_data_service.py",
    "server_modules/provider_catalog_service.py",
    "server_modules/provider_profiles.py",
    "server_modules/retention_enforcement_job.py",
    "server_modules/routes_auth.py",
    "server_modules/routes_connections.py",
    "server_modules/routes_fleet.py",
    "server_modules/routes_gateway.py",
    "server_modules/run_service.py",
    "server_modules/runs_core.py",
    "server_modules/runs_execution.py",
    "server_modules/runtime_events.py",
    "server_modules/runtime_heartbeat_service.py",
    "server_modules/runtime_policy.py",
    "server_modules/runtime_route_registration_service.py",
    "server_modules/safe_mode_service.py",
    "server_modules/agent_turn_runtime_service.py",
    "server_modules/sage_chat_api.py",
    "server_modules/sage_command_dispatcher.py",
    "server_modules/sage_proof_log_service.py",
    "server_modules/sage_reply_dispatcher.py",
    "server_modules/sage_skills_api.py",
    "server_modules/sage_telegram_hosted_service.py",
    "server_modules/sage_turn_adapter.py",
    "server_modules/session_diagnostics_service.py",
    "server_modules/session_manager/manager.py",
    "server_modules/shared.py",
    "server_modules/skill_registry.py",
    "server_modules/skills_service.py",
    "server_modules/sync_asyncio_bridge.py",
    "server_modules/telemetry.py",
    "server_modules/tool_broker.py",
    "server_modules/tool_broker_guard_service.py",
    "server_modules/vault_helpers.py",
    "server_modules/vault_store.py",
    "server_modules/workspace_context.py",
    "server_modules/workspace_scope.py",
})  # 96 files, 242 sites at time of seeding

UNASSIGNED_FIRE_AND_FORGET_BASELINE_FILES: frozenset[str] = frozenset({
    "server_modules/connectors/discord_bot_runtime_service.py",
    "server_modules/direct_chat_hosted_usage_service.py",
    "server_modules/durability_signal.py",
    "server_modules/egress_policy.py",
    "server_modules/gateway_protocol_service.py",
    "server_modules/local_tool_executor.py",
    "server_modules/memory_service.py",
    "server_modules/runtime_run_delegation_service.py",
    "server_modules/sage_telegram_hosted_service.py",
    "server_modules/tool_broker.py",
    "server_modules/workspace_scope.py",
})  # 11 files, 18 sites after MAN-266's removal (was 12 files, 19 sites at
    # time of seeding) -- note personal_channels_service.py
    # is NOT here: its one fire-and-forget task (C2) is fixed in this same
    # wave (module-level task set + done-callback, see
    # _ensure_agent_channel_binding_enabled). server_modules/
    # agent_turn_runtime_service.py was removed from this baseline (MAN-266):
    # its one violation, _schedule_post_turn_auto_compaction's
    # asyncio.ensure_future(...) at (what was) line 4151 with the returned
    # Task discarded, was the confirmed root cause of the recurring
    # production "Task was destroyed but it is pending!" errors -- fixed via
    # the same module-level task-set + done-callback pattern
    # (_POST_TURN_COMPACTION_TASKS / _track_post_turn_compaction_task).


def _scan() -> tuple[dict[str, list[int]], dict[str, list[tuple[int, str]]]]:
    bare_except_by_file: dict[str, list[int]] = {}
    fire_forget_by_file: dict[str, list[tuple[int, str]]] = {}
    for p in _iter_module_files():
        try:
            source = p.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        try:
            except_lines = find_bare_except_pass_lines(source)
            task_lines = find_unassigned_fire_and_forget_lines(source)
        except SyntaxError:
            continue
        rel = p.relative_to(ROOT).as_posix()
        if except_lines:
            bare_except_by_file[rel] = except_lines
        if task_lines:
            fire_forget_by_file[rel] = task_lines
    return bare_except_by_file, fire_forget_by_file


def test_no_new_files_with_bare_except_pass() -> None:
    bare_except_by_file, _ = _scan()
    offending_files = set(bare_except_by_file)
    new_files = offending_files - BARE_EXCEPT_PASS_BASELINE_FILES

    total_sites = sum(len(v) for v in bare_except_by_file.values())
    print(
        f"\n[test_exception_and_task_lint] bare `except Exception: pass` "
        f"sites: {total_sites} across {len(offending_files)} files "
        f"(baseline: {len(BARE_EXCEPT_PASS_BASELINE_FILES)} files)"
    )

    assert not new_files, (
        "New file(s) introduce a bare `except Exception: pass` (a handler "
        "whose entire body is the single statement `pass` -- no log, no "
        "re-raise, no evidence anything happened). Add at least a "
        "logging.debug(...)/logging.exception(...) call, or if this is a "
        "genuinely defensible best-effort swallow, add the file to "
        "BARE_EXCEPT_PASS_BASELINE_FILES in "
        "server_modules/tests/test_exception_and_task_lint.py with the "
        "understanding that new violations there won't be re-checked:\n  "
        + "\n  ".join(f"{f} (lines {bare_except_by_file[f]})" for f in sorted(new_files))
    )


def test_no_new_files_with_unassigned_fire_and_forget_tasks() -> None:
    _, fire_forget_by_file = _scan()
    offending_files = set(fire_forget_by_file)
    new_files = offending_files - UNASSIGNED_FIRE_AND_FORGET_BASELINE_FILES

    total_sites = sum(len(v) for v in fire_forget_by_file.values())
    print(
        f"\n[test_exception_and_task_lint] unassigned create_task/"
        f"ensure_future sites: {total_sites} across {len(offending_files)} "
        f"files (baseline: {len(UNASSIGNED_FIRE_AND_FORGET_BASELINE_FILES)} files)"
    )

    assert not new_files, (
        "New file(s) call asyncio.create_task(...)/ensure_future(...) as a "
        "bare statement, discarding the return value -- the event loop "
        "only weakly references the task, so it can be garbage-collected "
        "mid-flight with zero warning (docs/design/audit-silent-failures.md "
        "C2/M2). Store the task in a module-level set with a done-callback "
        "that discards it on completion (see "
        "personal_channels_service.py's _track_channel_binding_enable_task "
        "for the pattern this wave added), or if already tracked via a "
        "pattern this AST check can't see, add the file to "
        "UNASSIGNED_FIRE_AND_FORGET_BASELINE_FILES in "
        "server_modules/tests/test_exception_and_task_lint.py:\n  "
        + "\n  ".join(f"{f} (lines {fire_forget_by_file[f]})" for f in sorted(new_files))
    )


def test_baselines_have_no_stale_file_entries() -> None:
    """Hygiene: if a fix removes every violation from an allowlisted file,
    its entry must be removed here too, so the baseline can't quietly grow
    stale and hide a file that would otherwise start being checked for
    real (any NEW violation added to an already-clean, de-listed file
    would then be caught by the tests above)."""
    bare_except_by_file, fire_forget_by_file = _scan()

    stale_bare_except = sorted(BARE_EXCEPT_PASS_BASELINE_FILES - set(bare_except_by_file))
    assert not stale_bare_except, (
        "These files no longer contain a bare `except Exception: pass` -- "
        "remove them from BARE_EXCEPT_PASS_BASELINE_FILES so future "
        "regressions in these files are actually caught: "
        + ", ".join(stale_bare_except)
    )

    stale_fire_forget = sorted(UNASSIGNED_FIRE_AND_FORGET_BASELINE_FILES - set(fire_forget_by_file))
    assert not stale_fire_forget, (
        "These files no longer contain an unassigned create_task/"
        "ensure_future call -- remove them from "
        "UNASSIGNED_FIRE_AND_FORGET_BASELINE_FILES: "
        + ", ".join(stale_fire_forget)
    )


class TestFixtureDetection:
    """Proves the detection logic itself, entirely in memory."""

    def test_flags_bare_except_pass(self) -> None:
        source = (
            "def f():\n"
            "    try:\n"
            "        do_something()\n"
            "    except Exception:\n"
            "        pass\n"
        )
        assert find_bare_except_pass_lines(source) == [4]

    def test_does_not_flag_except_with_logging(self) -> None:
        source = (
            "import logging\n"
            "_log = logging.getLogger(__name__)\n"
            "def f():\n"
            "    try:\n"
            "        do_something()\n"
            "    except Exception:\n"
            "        _log.exception('failed')\n"
        )
        assert find_bare_except_pass_lines(source) == []

    def test_does_not_flag_a_specific_exception_type(self) -> None:
        source = (
            "def f():\n"
            "    try:\n"
            "        do_something()\n"
            "    except ValueError:\n"
            "        pass\n"
        )
        assert find_bare_except_pass_lines(source) == []

    def test_flags_unassigned_create_task(self) -> None:
        source = (
            "import asyncio\n"
            "def f(loop):\n"
            "    loop.create_task(g())\n"
        )
        assert find_unassigned_fire_and_forget_lines(source) == [(3, "create_task")]

    def test_does_not_flag_assigned_create_task(self) -> None:
        source = (
            "import asyncio\n"
            "def f(loop):\n"
            "    task = loop.create_task(g())\n"
            "    return task\n"
        )
        assert find_unassigned_fire_and_forget_lines(source) == []
