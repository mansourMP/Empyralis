"""Structural guard for the "outcome honesty" law (CLAUDE.md, "Product
laws"): after an action, the product must tell the person what actually
happened. "The action failed" and "the action may have succeeded but I
lost track of it" are different facts and must never share one message.

This is the BACKEND half of `frontend/lib/workspace/outcome-honesty-
drift.test.ts` (2026-08-14), built the same night for the same reason: that
file's own docstring names this exact gap — "Backend (Python) instances of
the same shape... a frontend text scanner is the wrong tool for that
surface." CLAUDE.md's own "Recurring failure modes" section already
documents several hand-found instances of this family here, each caught
after it had already shipped: `_build_error_reply_dict` returned
`{"text": ""}` on ANY turn failure, byte-identical to a turn that ran fine
and deliberately said nothing ("an empty string is not a decision");
`filter_channel_outbound_reply` was being asked "may this be sent" AND "did
the agent answer" at once ("nothing to send" is three facts, not one); a
filtered worker-item list sat beside an unfiltered summary computed a
different way ("a filtered list beside an unfiltered summary is still a
disclosure"). Those were each found by hand, one incident at a time, after
shipping. This file exists so the NEXT one is caught before that.

WHAT IT CATCHES
----------------
The direct structural analog of the frontend check's shape 1
(collapsed-catch): a `try:` block (sync or async — this codebase mixes
both for real writes, unlike the frontend's fetch-only surface) whose body
calls 2+ DISTINCT functions, where at least one callee name looks like a
real WRITE (create/insert/update/patch/delete/save/write/record/debit/
charge/grant/revoke/register/provision/... — see MUTATION_NAME_PATTERN),
and whose `except` handler(s) collectively produce at most one
distinguishable outcome (counting `return`/`raise` statements across every
handler, plus crediting 2 for having 2+ handlers at all, since dispatching
by exception TYPE is itself a form of distinguishing). That is precisely
the shape CLAUDE.md's own "A second engine behind one dispatch seam must
be symmetric about MONEY" entry describes as the near-miss that WOULD have
shipped a double-debit or a silent no-debit, had `_meter_and_debit_turn`
not fused metering and debiting into one function with one call site.

Like `find_bare_except_pass_lines`'s sibling check in
test_exception_and_task_lint.py, nested try/except is excluded from the
outer block's own call count: a nested `try/except` that swallows its own
error already stops it from ever reaching the OUTER except, so its calls
must not count toward whether the outer one "collapses" anything — the
exact false-positive class `_finalize_workspace_invite_acceptance`
(routes_workspaces.py) would otherwise trip: its inner
`try: await grant_invite_project_access(...) except Exception:
LOGGER.exception(...)` is the CORRECT pattern (log, do not raise, the
outer membership grant already succeeded) and must never be flagged.

THE FIX THIS FILE POINTS AT IS A PATTERN, NOT A FUNCTION. The frontend
sibling (outcome-honesty-drift.test.ts) names a real, importable helper
(`runMutationWithBestEffortRefresh`) in its own failure message, because
JS's own syntax makes one flat try/catch around two awaits the path of
least resistance -- the helper exists to make the CORRECT shape the EASY
one. Python does not have that problem: a nested `try: ... except
Exception: LOGGER.exception(...)` around the follow-up step is ALREADY
the idiomatic, easy way to say "this part is best-effort" -- wrapping a
function around it would only hide what one `except:` clause already
says directly. So this file's own failure message points at the PATTERN
above (`_finalize_workspace_invite_acceptance`'s own inner try/except),
never at a shared helper that does not and should not exist.

WHAT IT DELIBERATELY CANNOT CATCH (do not extend this file to chase these
— they need a different tool, not a bigger AST walk):
  - A function whose RETURN VALUE collapses two facts into one shape
    without ever going through a try/except at all — `_build_error_reply_
    dict` returning `{"text": ""}` on every failure path is a plain `def`
    with ordinary `if`/`return` branches, not a try/except this scanner
    inspects. That bug needs a hand-reviewed sweep or a narrower,
    purpose-built check against the specific return-type contract, the
    same way `test_default_engine_credit_debit.py`'s AST checks target
    ONE named function's call-site symmetry rather than scanning generally.
  - A guard applied on some branches and not others (test_unguarded_
    reply_paths.py's own subject) — that is a call-site-COUNT question
    ("does exactly one path reach the guard"), not a try/except shape.
  - Money-symmetry across two independent code paths behind one dispatch
    seam (the SDK-engine double-debit shape) — that needs the call-count-
    and-mutual-exclusion AST assertions test_default_engine_credit_debit.py
    already has, purpose-built per seam, not a general scan.
  - Anything expressed only in prose (a docstring or log message that LIES
    about what happened while the code path is structurally fine).

Like test_exception_and_task_lint.py, this is seeded with the CURRENT
violation count, grandfathered at file granularity: a NEW violation added
to an ALREADY-allowlisted file is not caught, only a violation in a file
with zero prior violations is. The count itself (see BASELINE_FILES below)
is a finding, not a promise to fix — prefer removing entries by actually
fixing the shape over adding to this docstring.

Run: DATABASE_URL= venv/bin/python -m pytest server_modules/tests/test_outcome_honesty_lint.py
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SERVER_MODULES_DIR = ROOT / "server_modules"

EXCLUDED_DIR_NAMES = {"tests", "__pycache__"}

# Mirrors frontend outcome-honesty-drift.test.ts's MUTATION_NAME_PATTERN,
# adjusted for this codebase's snake_case convention and its own vocabulary
# for a real write (debit/charge/mark/record/grant/revoke/upsert/persist —
# see control_plane_repository.py and thread_service.py for how these show
# up in practice). Deliberately broad: a false positive here just costs a
# reviewed ALLOWLIST entry; a false negative ships silently.
#
# "install" is deliberately ABSENT despite being in the frontend list — in
# THIS codebase it is overwhelmingly a domain NOUN (agent_install_id,
# agent_registry_repository's whole vocabulary, `_get_workspace_agent_
# install_bundle_local`), not a mutation verb, and matching it as a
# substring flagged dozens of files on calls like `_row_to_install_summary`
# that never write anything. Measured directly: removing it was the single
# largest noise reduction of the calibration pass that produced this list.
MUTATION_NAME_PATTERN = (
    "create|accept|join|decline|delete|remove|add|update|submit|save|signup|"
    "sign_up|register|provision|purchase|credit|invite|revoke|cancel|"
    "attach|assign|patch|write|send|configure|rotate|reset|grant|approve|"
    "deploy|commit|apply|toggle|enable|disable|move|archive|publish|pair|"
    "mutate|post|put|insert|debit|charge|mark|record|notify|upsert|store|"
    "persist|dispatch|provision|terminate|revoke|schedule|cancel"
)

_MUTATION_RE = re.compile(MUTATION_NAME_PATTERN, re.IGNORECASE)


def _iter_module_files() -> list[Path]:
    files = []
    for p in SERVER_MODULES_DIR.rglob("*.py"):
        if any(part in EXCLUDED_DIR_NAMES for part in p.parts):
            continue
        files.append(p)
    return sorted(files)


class _OuterCallCollector(ast.NodeVisitor):
    """Collects every ast.Call reachable from a Try node's OWN body without
    descending into a NESTED Try — a nested try/except that swallows its
    own error already decides what happens to anything inside it, so its
    calls cannot reach (and must not count toward) the outer except.

    Only `await`-wrapped calls are collected, mirroring the frontend
    check's own `awaitedCalleeNames` exactly (it matches a leading "await"
    keyword, never a bare call). This is a DELIBERATE precision choice, not an
    oversight: an unfiltered scan of every `ast.Call` in a try body picks
    up `str(x)`, `dict(y)`, `.strip()`, `now()`, `.isoformat()` and every
    other trivial local/stdlib call sitting beside a real mutation —
    contributing noise to the "2+ distinct calls" threshold without adding
    the signal the law actually cares about (a second STEP that can
    independently fail after the first one already committed). Measured
    directly against this codebase: the unfiltered version flagged
    dozens of files on calls like `str`/`dict`/`strip`/`isoformat` next to
    one real write, which would have buried real findings in noise and
    made the baseline meaningless. The cost, stated plainly: a purely
    SYNC mutation path (this codebase's SQLite-fallback branches call
    `.execute()`/`.commit()` without `await`) is invisible to this check —
    see the module docstring's "what it deliberately cannot catch"."""

    def __init__(self) -> None:
        self.calls: list[ast.Call] = []

    def visit_Try(self, node: ast.Try) -> None:  # noqa: N802 - ast.NodeVisitor API
        return  # deliberately do not descend

    def visit_Await(self, node: ast.Await) -> None:  # noqa: N802
        if isinstance(node.value, ast.Call):
            self.calls.append(node.value)
        self.generic_visit(node)


def _callee_name(call: ast.Call) -> str | None:
    fn = call.func
    if isinstance(fn, ast.Attribute):
        return fn.attr
    if isinstance(fn, ast.Name):
        return fn.id
    return None


def _outer_call_names(stmts: list[ast.stmt]) -> list[str]:
    collector = _OuterCallCollector()
    for stmt in stmts:
        collector.visit(stmt)
    names = []
    for call in collector.calls:
        name = _callee_name(call)
        if name:
            names.append(name)
    return names


def _distinguishing_signal_count(handlers: list[ast.ExceptHandler]) -> int:
    """How many ways the except side of this Try can tell one outcome from
    another. Two or more except CLAUSES already distinguish by exception
    TYPE (worth 2 on its own — dispatching on what kind of error occurred
    is itself a real distinction, even before counting statements inside
    either body). Within each handler, every `return`/`raise` reachable
    WITHOUT crossing into a further-nested try/except is counted directly
    (not via ast.walk, which would also count them inside a nested
    try/except that already handles its own outcome) — a handler with two
    or more such statements is branching on something (an `if`, a
    dispatch table) rather than producing one flat answer."""
    if len(handlers) >= 2:
        return 2

    def _count_return_and_raise(stmts: list[ast.stmt]) -> int:
        """Depth-first count of Return/Raise statements reachable from
        `stmts`, pruned at a nested Try (its own except already decides its
        own outcome, so what's inside must not count toward THIS handler's
        signal). `ast.iter_child_nodes` yields every statement inside a
        compound statement's body/orelse/finalbody individually, so
        recursing into each yielded `ast.stmt` walks If/For/While/With
        bodies without any special-casing per compound-statement type."""
        total = 0
        for stmt in stmts:
            if isinstance(stmt, (ast.Return, ast.Raise)):
                total += 1
            if isinstance(stmt, ast.Try):
                continue
            total += _count_return_and_raise(
                [child for child in ast.iter_child_nodes(stmt) if isinstance(child, ast.stmt)]
            )
        return total

    return sum(_count_return_and_raise(handler.body) for handler in handlers)


class TryHit:
    __slots__ = ("lineno", "callee_names")

    def __init__(self, lineno: int, callee_names: list[str]) -> None:
        self.lineno = lineno
        self.callee_names = callee_names


def _has_mutation_that_is_not_the_last_call(ordered_names: list[str]) -> bool:
    """True when either (a) 2+ DIFFERENT calls in the sequence are
    mutation-shaped (so more than one write's own outcome is at stake,
    regardless of order -- record_assistant_turn then record_user_turn is
    exactly this: either half of a two-sided conversation record could
    fail independently, and a caller needs to know which), or (b) a single
    mutation-shaped call has something else awaited AFTER it (a real
    step that could still fail once the mutation already committed).

    The case this excludes on purpose: `await _resolve_tenant(...)` then
    `await create_project(...)`, where create_project is the LAST call and
    the only mutation. Nothing has committed yet when _resolve_tenant could
    fail, and if create_project itself fails, "could not create project"
    is simply the truth -- there is no later step to lose track of.
    Measured directly: this was the single largest source of false
    positives in routes_fleet.py's `_resolve_tenant, <one write>` pattern,
    repeated at nearly every route in that file."""
    mutation_indices = [i for i, name in enumerate(ordered_names) if _MUTATION_RE.search(name)]
    if not mutation_indices:
        return False
    distinct_mutation_names = {ordered_names[i] for i in mutation_indices}
    if len(distinct_mutation_names) >= 2:
        return True
    return max(mutation_indices) != len(ordered_names) - 1


def find_collapsed_except_blocks(source: str) -> list[TryHit]:
    """Every `try:` block whose body calls 2+ distinct functions, where a
    mutation-shaped call is not simply the LAST thing that could fail
    (see _has_mutation_that_is_not_the_last_call), but whose except side
    produces at most one distinguishable outcome."""
    tree = ast.parse(source)
    hits: list[TryHit] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Try):
            continue
        names = _outer_call_names(node.body)
        distinct_names = sorted(set(names))
        if len(distinct_names) < 2:
            continue
        if not _has_mutation_that_is_not_the_last_call(names):
            continue
        if not node.handlers:
            continue  # try/finally with no except -- errors propagate whole
        if _distinguishing_signal_count(node.handlers) > 1:
            continue
        hits.append(TryHit(lineno=node.lineno, callee_names=distinct_names))
    return hits


# ---------------------------------------------------------------------------
# Seeded baseline -- real files, verified as of this pass (2026-08-14).
# Grandfathered at file granularity, same trade-off and same reasoning as
# test_exception_and_task_lint.py's own BARE_EXCEPT_PASS_BASELINE_FILES:
# a line-number allowlist breaks on every unrelated edit that shifts lines
# elsewhere in the file, which nobody would trust. Prefer fixing entries off
# this list over adding to it; if a fix removes a file's last violation,
# remove it here too (test_baseline_has_no_stale_file_entries enforces that
# removal isn't optional).
#
# One entry is not "haven't gotten to it yet" -- routes_fleet.py's
# fleet_patch_project is a CONFIRMED real instance (rename_project, then
# conditionally set_project_archived, then conditionally set_project_
# default_gateway, all in one try/except -- if the first field's write
# succeeds and a later one throws, the caller cannot tell which fields
# actually applied) that the founder has explicitly ruled should STAY
# flagged rather than get a speculative fix: it is unreachable on the live
# path today (every current caller patches exactly one field), and a fix
# written without a real multi-field caller to verify against is exactly
# the kind of speculative change CLAUDE.md warns against. Do not "clean
# this up" without checking with the founder first -- the visibility IS
# the point.
# ---------------------------------------------------------------------------

BASELINE_FILES: frozenset[str] = frozenset({
    "server_modules/connectors/discord_connector.py",
    "server_modules/connectors_actions.py",
    "server_modules/fleet_tools.py",
    "server_modules/gateway_execution_service.py",
    "server_modules/gateway_protocol_service.py",
    "server_modules/hardware_runtime_adapters/cloud_computer_adapter.py",
    "server_modules/hosted_bot_provisioning_service.py",
    "server_modules/routes_fleet.py",  # fleet_patch_project -- see comment above
    "server_modules/routes_personal_channels.py",
    "server_modules/run_service.py",
    "server_modules/run_state_repository.py",
    "server_modules/agent_turn_runtime_service.py",
    "server_modules/sage_telegram_hosted_service.py",
    "server_modules/wechat_official_service.py",
})  # 14 files, 40 sites at time of seeding


def _scan() -> dict[str, list[TryHit]]:
    by_file: dict[str, list[TryHit]] = {}
    for p in _iter_module_files():
        try:
            source = p.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        try:
            hits = find_collapsed_except_blocks(source)
        except SyntaxError:
            continue
        if hits:
            rel = p.relative_to(ROOT).as_posix()
            by_file[rel] = hits
    return by_file


def test_no_new_files_with_collapsed_except_blocks() -> None:
    by_file = _scan()
    offending_files = set(by_file)
    new_files = offending_files - BASELINE_FILES

    total_sites = sum(len(v) for v in by_file.values())
    print(
        f"\n[test_outcome_honesty_lint] collapsed-except sites: {total_sites} "
        f"across {len(offending_files)} files (baseline: {len(BASELINE_FILES)} files)"
    )

    assert not new_files, (
        "New file(s) introduce a try/except that calls 2+ distinct functions "
        "(at least one mutation-shaped) but whose except side cannot tell "
        "which one failed -- CLAUDE.md's 'after an action, the product must "
        "tell the person what actually happened' law.\n"
        "  FIRST thing to reach for: there is NO shared helper function for "
        "this in Python, and there should not be one -- unlike the frontend "
        "(JS's own syntax makes one flat try/catch around two awaits the "
        "EASY shape, which is why frontend/lib/workspace/mutation-outcome.ts's "
        "runMutationWithBestEffortRefresh exists to make the correct shape "
        "the easy one), Python's nested try/except is ALREADY the easy, "
        "idiomatic way to say 'this specific step is best-effort, log and "
        "move on' -- wrapping a helper function around it would only hide "
        "what one `except:` clause already says directly. Give the follow-up "
        "step its OWN try/except that logs and does not re-raise, exactly "
        "like routes_workspaces.py's _finalize_workspace_invite_acceptance "
        "does around its own project-access grant "
        "(`try: await grant_invite_project_access(...) except Exception: "
        "LOGGER.exception(...)` -- the membership grant right above it is "
        "already committed, so a failure here must not read as the whole "
        "acceptance failing). That single pattern, repeated per call site, "
        "is the fix -- not a function to import.\n"
        "  If instead this is 2+ REAL writes whose outcomes are both worth "
        "knowing (see agent_turn_runtime_service.py's record_assistant_turn "
        "+ record_user_turn, in this file's own baseline), give the except "
        "a way to distinguish them (separate except clauses, or a branch "
        "inside one). Only once neither applies -- a self-contained nested "
        "try/except this scanner cannot see already handles the ambiguity --"
        " add the file to BASELINE_FILES in "
        "server_modules/tests/test_outcome_honesty_lint.py with a written "
        "reason:\n  "
        + "\n  ".join(
            f"{f} (lines {[h.lineno for h in by_file[f]]}, callees {[h.callee_names for h in by_file[f]]})"
            for f in sorted(new_files)
        )
    )


def test_baseline_has_no_stale_file_entries() -> None:
    by_file = _scan()
    stale = sorted(BASELINE_FILES - set(by_file))
    assert not stale, (
        "These files no longer contain a collapsed-except site -- remove "
        "them from BASELINE_FILES so future regressions in these files are "
        "actually caught: " + ", ".join(stale)
    )


class TestFixtureDetection:
    """Proves the detection logic itself, entirely in memory -- same
    discipline as test_exception_and_task_lint.py's own fixture class."""

    def test_flags_two_distinct_mutation_calls_with_one_flat_except(self) -> None:
        source = (
            "async def f():\n"
            "    try:\n"
            "        await create_thing()\n"
            "        await notify_thing()\n"
            "    except Exception as e:\n"
            "        return {'ok': False, 'error': str(e)}\n"
        )
        hits = find_collapsed_except_blocks(source)
        assert len(hits) == 1
        assert hits[0].callee_names == ["create_thing", "notify_thing"]

    def test_does_not_flag_two_read_only_calls(self) -> None:
        source = (
            "async def f():\n"
            "    try:\n"
            "        a = await get_thing()\n"
            "        b = await list_things()\n"
            "    except Exception:\n"
            "        return None\n"
        )
        assert find_collapsed_except_blocks(source) == []

    def test_does_not_flag_a_single_mutation_call(self) -> None:
        source = (
            "async def f():\n"
            "    try:\n"
            "        await create_thing()\n"
            "    except Exception:\n"
            "        return None\n"
        )
        assert find_collapsed_except_blocks(source) == []

    def test_does_not_flag_when_multiple_except_clauses_dispatch(self) -> None:
        source = (
            "async def f():\n"
            "    try:\n"
            "        await create_thing()\n"
            "        await notify_thing()\n"
            "    except ValueError:\n"
            "        return {'ok': False, 'error': 'bad input'}\n"
            "    except Exception:\n"
            "        return {'ok': False, 'error': 'unknown'}\n"
        )
        assert find_collapsed_except_blocks(source) == []

    def test_does_not_flag_when_the_single_handler_branches(self) -> None:
        source = (
            "async def f():\n"
            "    try:\n"
            "        await create_thing()\n"
            "        await notify_thing()\n"
            "    except Exception as e:\n"
            "        if isinstance(e, ValueError):\n"
            "            return {'ok': False, 'error': 'bad input'}\n"
            "        return {'ok': False, 'error': 'unknown'}\n"
        )
        assert find_collapsed_except_blocks(source) == []

    def test_does_not_flag_a_nested_self_contained_try_except(self) -> None:
        """The _finalize_workspace_invite_acceptance shape: the inner
        try/except logs and does not raise, so the OUTER except can only
        ever fire from the first call -- not a collapse."""
        source = (
            "async def f():\n"
            "    await create_thing()\n"
            "    try:\n"
            "        await notify_thing()\n"
            "    except Exception:\n"
            "        LOGGER.exception('best effort notify failed')\n"
        )
        assert find_collapsed_except_blocks(source) == []

    def test_nested_try_except_does_not_count_toward_the_outer_blocks_calls(self) -> None:
        source = (
            "async def f():\n"
            "    try:\n"
            "        await create_thing()\n"
            "        try:\n"
            "            await notify_thing()\n"
            "        except Exception:\n"
            "            LOGGER.exception('best effort')\n"
            "    except Exception as e:\n"
            "        return {'ok': False, 'error': str(e)}\n"
        )
        # Only ONE distinct mutation-shaped callee reaches the outer try
        # (create_thing) -- notify_thing is inside its own nested, self-
        # contained try/except and must not be counted toward the outer
        # block's "2+ distinct calls" threshold.
        assert find_collapsed_except_blocks(source) == []

    def test_does_not_flag_try_finally_with_no_except(self) -> None:
        source = (
            "async def f():\n"
            "    try:\n"
            "        await create_thing()\n"
            "        await notify_thing()\n"
            "    finally:\n"
            "        cleanup()\n"
        )
        assert find_collapsed_except_blocks(source) == []

    def test_does_not_flag_a_read_then_a_single_write_that_is_last(self) -> None:
        """routes_fleet.py's own `_resolve_tenant(...)` then one write,
        repeated at nearly every route -- nothing has committed when the
        resolve step could fail, and if the write itself fails, reporting
        that failure is simply accurate. There is no later step to lose
        track of, so this must not be flagged even though there are 2
        distinct calls and one is mutation-shaped."""
        source = (
            "async def f():\n"
            "    try:\n"
            "        tenant_id = await resolve_tenant(workspace_id)\n"
            "        await create_project(tenant_id)\n"
            "    except Exception as e:\n"
            "        raise HTTPException(500, str(e))\n"
        )
        assert find_collapsed_except_blocks(source) == []

    def test_flags_a_single_write_followed_by_a_read_only_step(self) -> None:
        """The mirror case: the mutation happens FIRST and something else
        follows it -- exactly signup's own shape (signup() then a
        readiness poll) one layer down. A failure in the second step must
        not read as the first one failing."""
        source = (
            "async def f():\n"
            "    try:\n"
            "        await create_project(tenant_id)\n"
            "        await refresh_project_cache(tenant_id)\n"
            "    except Exception as e:\n"
            "        raise HTTPException(500, str(e))\n"
        )
        hits = find_collapsed_except_blocks(source)
        assert len(hits) == 1

    def test_flags_two_distinct_mutations_regardless_of_order(self) -> None:
        """record_assistant_turn / record_user_turn's own shape: BOTH calls
        are real writes, so either one failing independently matters, even
        though the mutation IS the last call in the sequence."""
        source = (
            "async def f():\n"
            "    try:\n"
            "        await record_user_turn(thread_id)\n"
            "        await record_assistant_turn(thread_id)\n"
            "    except Exception:\n"
            "        LOGGER.exception('turn recording failed')\n"
        )
        hits = find_collapsed_except_blocks(source)
        assert len(hits) == 1
