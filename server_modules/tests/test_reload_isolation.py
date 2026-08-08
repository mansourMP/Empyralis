"""The suite's reload isolation, asserted directly.

Sibling test modules call ``importlib.reload()`` on parts of
``server_modules``.  ``reload()`` re-executes the module into the SAME module
object, so every function, class and constant in it becomes a NEW object while
every other module that already captured one by value keeps the dead one -- a
FastAPI route's baked ``Depends(auth_module.get_current_user)``, an
``except SomeError`` clause, a re-exported constant.  Which of the two a given
test sees then depends only on which files ran before it, which is how the
suite ends up reporting a different number for the same commit.

``conftest._restore_reload_sensitive_modules`` undoes every reload at the end
of the test that did it.  These tests assert the mechanism is armed and that
it covers modules nobody listed by hand, because the failure it prevents is
silent by construction: nothing raises, some unrelated test just starts
answering differently.
"""

from __future__ import annotations

import importlib
import os
import re
import sys
from pathlib import Path

_TESTS_DIR = Path(__file__).resolve().parent
# conftest re-registers itself under this unambiguous name; see the note at
# the bottom of conftest.py for why a plain `import conftest` is not safe.
_CONFTEST = sys.modules["empyralis_tests_egress_guard"]


def test_importlib_reload_is_wrapped_for_every_test() -> None:
    """Without the wrapper, a reload of any module outside the hand-written
    ``_RELOAD_SENSITIVE_MODULE_NAMES`` seed leaks for the rest of the session."""
    assert getattr(importlib.reload, "_empyralis_reload_tracked", False) is True, (
        "importlib.reload is not wrapped -- conftest._restore_reload_sensitive_modules "
        "is no longer arming the reload tracker, so reloads of unlisted modules will "
        "again leak into every later test"
    )


def test_reloading_an_unlisted_module_is_snapshotted_before_the_reload() -> None:
    """End-to-end on the module the gap was actually found on.

    ``billing_credit_config`` is deliberately NOT in the seed tuple:
    ``test_core_loop_no_fallback.py`` reloads it under a patched environment,
    and (until this was fixed) its own restoring reload ran while that patch
    was still in force, leaving the module holding the overridden value.  The
    tracker has to notice a module it was never told about, and it has to take
    the snapshot BEFORE the reload -- a snapshot taken afterwards would
    faithfully restore the leak.
    """
    from server_modules import billing_credit_config

    assert billing_credit_config.__name__ not in _CONFTEST._RELOAD_SENSITIVE_MODULE_NAMES, (
        "this test is only meaningful while billing_credit_config is NOT on the seed "
        "list -- it exists to prove the tracker covers modules the list does not"
    )

    before = billing_credit_config.NEW_ACCOUNT_SIGNUP_CREDIT_USD
    assert before != 0.0, "need a non-zero starting value for the assertions below to bite"

    os.environ["EMPYRALIS_NEW_ACCOUNT_SIGNUP_CREDIT_USD"] = "0"
    try:
        importlib.reload(billing_credit_config)
        # The wrapper must not neuter the reload itself.
        assert billing_credit_config.NEW_ACCOUNT_SIGNUP_CREDIT_USD == 0.0
    finally:
        os.environ.pop("EMPYRALIS_NEW_ACCOUNT_SIGNUP_CREDIT_USD", None)

    snapshots = _CONFTEST._ACTIVE_RELOAD_SNAPSHOTS
    assert billing_credit_config.__name__ in snapshots, (
        "the tracker did not snapshot a module it had not been told about -- the "
        "overridden value above would leak into every later test in the session"
    )
    assert snapshots[billing_credit_config.__name__]["NEW_ACCOUNT_SIGNUP_CREDIT_USD"] == before, (
        "the snapshot was taken AFTER the reload, so restoring it restores the leak"
    )

    # Leave the module dirty on purpose: the fixture's teardown is what has to
    # clean it up, and test_the_previous_reload_did_not_survive below is what
    # notices if it stopped.
    _ReloadProbe.value_before_the_dirty_reload = before


class _ReloadProbe:
    value_before_the_dirty_reload: float | None = None


def test_the_previous_reload_did_not_survive_into_this_test() -> None:
    """Second layer: observe from a different test that the teardown ran.

    Deliberately does NOT skip when the sibling above did not run -- it falls
    back to asserting the module holds its own unpatched default, so this test
    is never vacuous no matter how it is selected.
    """
    from server_modules import billing_credit_config

    expected = _ReloadProbe.value_before_the_dirty_reload
    if expected is None:
        # Selected alone: nothing dirtied the module, so the only honest
        # assertion is that it agrees with the environment it was imported in.
        assert os.environ.get("EMPYRALIS_NEW_ACCOUNT_SIGNUP_CREDIT_USD") is None
        assert billing_credit_config.NEW_ACCOUNT_SIGNUP_CREDIT_USD != 0.0
        return

    assert billing_credit_config.NEW_ACCOUNT_SIGNUP_CREDIT_USD == expected, (
        "a reload performed by the previous test survived into this one -- "
        "conftest._restore_reload_sensitive_modules is not restoring"
    )


def test_control_plane_repository_agrees_with_billing_credit_config() -> None:
    """The two modules re-export one constant, and only one of them used to be
    restored.  A disagreement between them is the observable form of the leak,
    and it is worth asserting on its own: it fails loudly here instead of
    turning into a wrong number in a billing test hundreds of files away."""
    from server_modules import billing_credit_config, control_plane_repository

    assert (
        control_plane_repository.NEW_ACCOUNT_SIGNUP_CREDIT_USD
        == billing_credit_config.NEW_ACCOUNT_SIGNUP_CREDIT_USD
    )


def test_every_reload_target_in_the_test_tree_is_a_plain_module_object() -> None:
    """Drift guard, derived from the real call sites rather than a copied list.

    The tracker keys off ``module.__name__``; a reload whose argument is not a
    live module object escapes it silently.
    """
    pattern = re.compile(r"importlib\.reload\(\s*([^)]+?)\s*\)")
    sites: list[tuple[str, int, str]] = []
    for path in sorted(_TESTS_DIR.rglob("test_*.py")):
        for lineno, line in enumerate(path.read_text(errors="replace").splitlines(), 1):
            for match in pattern.finditer(line):
                sites.append((path.name, lineno, match.group(1)))

    assert sites, "no importlib.reload() call sites found -- has this scraper gone stale?"
    for name, lineno, target in sites:
        assert "sys.modules[" not in target, (
            f"{name}:{lineno} reloads {target!r}. A reload target must be a module object so "
            "conftest's reload tracker can read __name__ off it and restore the module "
            "afterwards; resolve it with importlib.import_module() first."
        )
