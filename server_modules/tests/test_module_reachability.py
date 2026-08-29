"""Reachability guard — the CI check the silent-failures audit asked for.

docs/design/audit-silent-failures.md's single highest-value recommendation:
"for every server_modules/*.py file, checks whether any of its public
top-level names is referenced from any file outside server_modules/tests/
and outside itself." This is that check, implemented literally as
specified there.

This one check would have caught, on day one of each landing: the
mini-apps subsystem (partially -- see LIMITATIONS below), the dreaming
pipeline (H2), and the already-known retention_enforcement_job.py -- all of
which had fully green unit tests that imported the service module and
called its functions directly, proving the business logic works while
proving nothing about whether anything outside the test suite ever calls
it.

## Method (static analysis only -- no imports, no network)

For every top-level ``server_modules/*.py`` file (not recursing into
subpackages -- matches the audit's own file-level scope):

1. Collect its "public top-level names": module-level ``def``/``async
   def``/``class`` statements not starting with ``_``, plus the module's
   own filename stem (so a module that's only ever consumed as
   ``from server_modules import X; X.whatever()`` -- i.e. reachable via
   the module object itself rather than a specific imported name -- still
   counts).
2. Search every other ``.py`` file in the live tree (excluding
   ``server_modules/tests/`` and the module's own file) for a real AST-level
   reference to any of those names: a ``Name`` load, an ``Attribute``
   access, an import target, or an exact-match string constant (covers
   dynamic dispatch / action-id-style string keys). Comments and
   docstrings do NOT count -- they're not part of the AST. This is
   deliberately stricter than a raw text/grep substring match, which
   produced a false negative in early tuning of this check (a comment in
   workspace_context.py merely *mentioning* ``RuntimeProfileModel`` was
   enough to hide agent_registry_models.py as "reachable" under naive
   grep; the AST version correctly still flags it).
3. A module with zero such references anywhere is an orphan -- unless it's
   in ``ALLOWLISTED_ORPHANS`` below.

## Known limitations (honest, not fixed by this pass)

- **Name-collision false negatives**: this is identifier-name reachability,
  not a true call graph. Two unrelated modules defining a same-named
  function would each "rescue" the other if only one is actually called.
  Acceptable for a fast, dependency-free CI check; a real call-graph
  analysis is out of scope here.
- **Mutually-referencing orphaned clusters are only partially caught**:
  this is a per-file check ("referenced by anything outside itself"), not
  a transitive-closure-from-known-live-roots (server.py/mcp_server.py)
  graph reachability check. A cluster of files that only reference each
  other, with zero live entry point importing ANY of them, would have
  every member flagged -- but if even one non-test, non-cluster file
  imports ONE member for an unrelated reason (e.g. a shared constant),
  that one member is no longer flagged even though it's just as dead in
  practice. Verified concretely: of the 7 files in the audit's H1
  "mini-apps" cluster, this check catches 3 as outright orphans
  (calorie_tracking_service.py, flashcards_tracking_service.py,
  discovery_feed_service.py) but NOT mini_apps_service.py,
  mini_app_host_service.py, mini_app_invoke_service.py, or
  mini_app_token_exchange_service.py -- because other live-looking files
  (data_retention_service.py, marketplace_distribution_service.py,
  workspace_context_memory_adapter.py) import names from
  mini_apps_service.py for unrelated reasons, which transitively "rescues"
  it under this per-file definition even though none of those importers
  are themselves reachable from server.py/mcp_server.py either. Catching
  the *whole* cluster would require a real transitive-closure reachability
  graph from a curated set of live roots -- a materially bigger check,
  intentionally out of scope for this pass (see
  docs/design/audit-silent-failures.md's own "Route-coverage test"
  suggestion for the complementary check that would close this gap for
  HTTP-facing code specifically).
"""

from __future__ import annotations

import ast
import functools
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SERVER_MODULES_DIR = ROOT / "server_modules"
TESTS_DIR = SERVER_MODULES_DIR / "tests"

# Directories that are not live application code -- vendored/cached/archived
# copies would otherwise "rescue" a genuinely dead module by containing an
# old reference to it (this bit us during development: an early version of
# this check that didn't exclude `.claude/` and `legacy/` found ZERO
# orphans at all, because both directories contain full historical copies
# of server_modules with the same identifiers).
EXCLUDED_DIR_NAMES = {
    "__pycache__", "node_modules", ".venv", ".venv-v2", "_archive", "legacy",
    ".git", ".orion-artifacts", ".orion-object-store", ".orion-stack",
    "frontend", "mobile", "graphify-out", "temp_executions", ".pytest_cache",
    "src-tauri", ".claude", ".codex", ".antigravitycli",
}

_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


# ---------------------------------------------------------------------------
# Pure analysis functions -- operate on source text only, no filesystem
# access, so the fixture test below can exercise them entirely in memory.
# ---------------------------------------------------------------------------

def public_top_level_names(source: str) -> set[str]:
    """Module-level def/async def/class names not starting with '_'."""
    tree = ast.parse(source)
    names: set[str] = set()
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            if not node.name.startswith("_"):
                names.add(node.name)
    return names


def referenced_identifiers(source: str) -> set[str]:
    """Every identifier a file's AST could plausibly be *using* -- Name
    loads, attribute access, import targets (module path components and
    aliases), and exact-match string constants (dynamic dispatch /
    action-id-style keys). NOT comments or docstring prose -- those aren't
    AST nodes at all, which is the whole point (see module docstring)."""
    tree = ast.parse(source)
    refs: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            refs.add(node.id)
        elif isinstance(node, ast.Attribute):
            refs.add(node.attr)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                refs.update(alias.name.split("."))
                if alias.asname:
                    refs.add(alias.asname)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                refs.update(node.module.split("."))
            for alias in node.names:
                refs.add(alias.name)
                if alias.asname:
                    refs.add(alias.asname)
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            if _IDENTIFIER_RE.match(node.value):
                refs.add(node.value)
    return refs


def find_orphans(
    module_sources: dict[str, str],
    corpus_sources: dict[str, str],
    *,
    excluded_prefixes: tuple[str, ...] = (),
) -> dict[str, set[str]]:
    """The core check. ``module_sources`` are the files being audited for
    reachability (keyed by a stable string id, e.g. a relative path).
    ``corpus_sources`` is every file searched for a reference, keyed the
    same way -- normally a superset of ``module_sources``. A corpus entry
    counts as "outside" a module if its key differs from the module's own
    key and doesn't start with any of ``excluded_prefixes`` (the tests
    directory).

    Returns ``{module_key: public_names}`` for every module with zero
    outside references -- the orphans.
    """
    module_names: dict[str, set[str]] = {}
    for key, source in module_sources.items():
        try:
            names = public_top_level_names(source)
        except SyntaxError:
            continue
        names.add(Path(key).stem)  # the module itself, imported bare
        module_names[key] = names

    corpus_refs: dict[str, set[str]] = {}
    for key, source in corpus_sources.items():
        try:
            corpus_refs[key] = referenced_identifiers(source)
        except SyntaxError:
            corpus_refs[key] = set()

    # Reverse index: identifier -> set of corpus keys referencing it.
    ref_index: dict[str, set[str]] = {}
    for key, refs in corpus_refs.items():
        for r in refs:
            ref_index.setdefault(r, set()).add(key)

    orphans: dict[str, set[str]] = {}
    for module_key, names in module_names.items():
        reachable = False
        for name in names:
            referencing_keys = ref_index.get(name, set())
            outside = {
                k for k in referencing_keys
                if k != module_key and not k.startswith(excluded_prefixes)
            }
            if outside:
                reachable = True
                break
        if not reachable:
            orphans[module_key] = names
    return orphans


# ---------------------------------------------------------------------------
# Real-repo scan
# ---------------------------------------------------------------------------

def _iter_corpus_files() -> list[Path]:
    files = []
    for p in ROOT.rglob("*.py"):
        # RELATIVE to ROOT, never the absolute path. An agent worktree lives
        # at <repo>/.claude/worktrees/<id>/, and ".claude" is excluded below —
        # so matching absolute parts excluded every file in the repo and this
        # scan found NOTHING. Its own canary caught that rather than reporting
        # a clean pass on an empty corpus, but the guard was still dead for
        # every agent working the way CLAUDE.md requires, which is all of them.
        if any(part in EXCLUDED_DIR_NAMES for part in p.relative_to(ROOT).parts):
            continue
        files.append(p)
    return files


@functools.lru_cache(maxsize=1)
def _load_repo_sources() -> tuple[dict[str, str], dict[str, str]]:
    """Returns (module_sources, corpus_sources) keyed by path relative to
    ROOT (posix-style, so exclusion-prefix matching is stable). Cached --
    this repo-wide scan is the expensive part (~1,300+ files) and both
    real-repo tests below need the identical result; re-reading the whole
    tree twice would double the test's wall-clock cost for no benefit."""
    corpus_sources: dict[str, str] = {}
    for f in _iter_corpus_files():
        key = f.relative_to(ROOT).as_posix()
        try:
            corpus_sources[key] = f.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue

    module_sources = {
        key: text
        for key, text in corpus_sources.items()
        if key.startswith("server_modules/")
        and "/" not in key[len("server_modules/"):]  # top-level files only
        and key.endswith(".py")
    }
    return module_sources, corpus_sources


@functools.lru_cache(maxsize=1)
def _repo_orphans() -> tuple[frozenset[str], frozenset[str]]:
    """Cached (orphan_keys, existing_module_basenames) for the real repo --
    the AST parse of every corpus file (for referenced_identifiers) is the
    expensive part of find_orphans(); both real-repo tests below need the
    identical result, so this runs the whole scan exactly once per test
    session regardless of how many tests consume it."""
    module_sources, corpus_sources = _load_repo_sources()
    orphans = find_orphans(
        module_sources,
        corpus_sources,
        excluded_prefixes=("server_modules/tests/",),
    )
    return frozenset(orphans), frozenset(Path(k).name for k in module_sources)


# ---------------------------------------------------------------------------
# Allowlist -- deliberate, reviewed exceptions. Every entry here is a real,
# currently-orphaned module verified against docs/design/audit-silent-failures.md
# (or, for the newly-checked ones, verified directly: its public names have
# zero AST-level references anywhere outside server_modules/tests/). Adding
# an entry here is a real decision, not a rubber stamp -- prefer wiring the
# module up (a route, a scheduler hook, a real caller) or deleting it.
# ---------------------------------------------------------------------------

ALLOWLISTED_ORPHANS: dict[str, str] = {
    # --- M3: genuinely dead stub, self-contradicting docstring ---
    "approval_contracts.py": (
        "Dead stub -- own docstring claims 'prevents import errors' but "
        "nothing imports it, not even tests. audit-silent-failures.md M3."
    ),
    # --- H1: ~4,800-line mini-apps backend, zero HTTP route ---
    "calorie_tracking_service.py": (
        "Mini-apps cluster (H1): real, tested business logic with no "
        "FastAPI route and no live caller outside its own unit test. "
        "audit-silent-failures.md H1."
    ),
    "flashcards_tracking_service.py": (
        "Mini-apps cluster (H1), same shape as calorie_tracking_service.py. "
        "audit-silent-failures.md H1."
    ),
    "discovery_feed_service.py": (
        "Mini-apps cluster (H1), same shape as calorie_tracking_service.py. "
        "audit-silent-failures.md H1."
    ),
    # --- H2: the dreaming/memory-relief pipeline that never runs ---
    "dreaming_pipeline.py": (
        "Built to relieve the 50-entry Sage memory cap (SAGE_MEMORY_ENTRY_LIMIT) "
        "but never called from any scheduler/route/command dispatcher. "
        "audit-silent-failures.md H2."
    ),
    # --- Known defect, already tracked separately ---
    "retention_enforcement_job.py": (
        "Already-known dead job (founder's list); zero non-test callers, "
        "referenced only by its own test_retention_enforcement_job.py."
    ),
    # --- M1-style: only referenced by an architecture-boundary test's file
    #     list, not by any functional caller ---
    "channel_activity_service.py": (
        "record_result() has zero functional callers; the only outside "
        "reference is test_channel_execution_separation.py listing it as "
        "a 'channel file' for import-direction checking, not calling it. "
        "Same shape as channel_event_journal_service.py in audit M1."
    ),
    "virtual_computer_billing_hook.py": (
        "build_virtual_computer_billing_hook_payload() referenced only "
        "from its own test_virtual_computer_billing_hook.py."
    ),
    # --- Apple Business Messaging: intentional, matches this repo's own
    #     'dead routes preserved, not deleted' decision ---
    "business_messaging_channel_adapter_service.py": (
        "Apple Business Messaging adapter, kept unmounted on purpose "
        "(dead routes are preserved, not deleted, for a one-line remount "
        "when the bridge is ready)."
    ),
    # --- rust-gate-only modules: a dedicated unit test calls the module's
    #     functions directly to exercise Rust-kernel parity, but nothing in
    #     the live app imports them ---
    "agent_registry_models.py": (
        "Model/schema definitions with zero real callers outside tests -- "
        "the one apparent hit (workspace_context.py) is a code COMMENT "
        "mentioning RuntimeProfileModel, not a real reference (this is "
        "exactly the false-negative this check's AST approach exists to "
        "avoid; a naive grep sweep would miss this orphan)."
    ),
    "channel_types.py": "Referenced only by test_channel_types.py.",
    "policy_presets.py": "Referenced only by test_policy_presets_rust_gate.py.",
    "cli_companion_service.py": "Referenced only by test_execution_artifact_state_rust_gate.py.",
    "rust_authorization_shadow_service.py": "Referenced only by test_rust_authorization_shadow_service.py.",
    "session_lifecycle_service.py": "Referenced only by test_session_lifecycle_rust_gate.py.",
}


def test_no_new_reachability_orphans() -> None:
    """The guard. A server_modules/*.py file with none of its public
    top-level names referenced anywhere outside server_modules/tests/ (and
    outside itself) must be in ALLOWLISTED_ORPHANS above -- a reviewed,
    deliberate exception -- or this fails with the exact new orphan(s)
    named, so they can't land silently the way mini_apps_service.py,
    dreaming_pipeline.py, and retention_enforcement_job.py all did.
    """
    orphan_keys, existing_basenames = _repo_orphans()
    assert existing_basenames, "sanity check: found zero server_modules/*.py files -- path resolution is broken"

    orphan_basenames = {Path(key).name for key in orphan_keys}
    new_orphans = orphan_basenames - set(ALLOWLISTED_ORPHANS)

    assert not new_orphans, (
        "New unreachable module(s) found -- at least one public top-level "
        "name in each of these files has ZERO references anywhere outside "
        "server_modules/tests/ and outside the file itself, meaning no "
        "live code path (route, scheduler, tool, turn pipeline) can reach "
        "it, however green its own unit tests are "
        "(docs/design/audit-silent-failures.md is the audit that motivated "
        "this check). Either wire the module up to a real caller, delete "
        "it if it's genuinely dead, or -- only if this is a deliberate, "
        "reviewed exception -- add it to ALLOWLISTED_ORPHANS in "
        "server_modules/tests/test_module_reachability.py with a one-line "
        "reason:\n  " + "\n  ".join(sorted(new_orphans))
    )


def test_allowlist_has_no_stale_entries() -> None:
    """Hygiene check: every allowlisted file must still exist AND still
    actually be an orphan. If a module gets wired up to a real caller
    later, its allowlist entry must be removed in the same change --
    otherwise the allowlist quietly grows into a place where fixes go to
    become invisible again."""
    orphan_keys, existing_basenames = _repo_orphans()

    missing = sorted(set(ALLOWLISTED_ORPHANS) - existing_basenames)
    assert not missing, (
        "ALLOWLISTED_ORPHANS names file(s) that no longer exist under "
        "server_modules/ -- remove these stale entries: " + ", ".join(missing)
    )

    still_orphaned_basenames = {Path(key).name for key in orphan_keys}
    no_longer_orphaned = sorted(set(ALLOWLISTED_ORPHANS) - still_orphaned_basenames)
    assert not no_longer_orphaned, (
        "These ALLOWLISTED_ORPHANS entries are no longer orphans (something "
        "outside server_modules/tests/ now references them) -- remove the "
        "allowlist entry so this check keeps proving something: "
        + ", ".join(no_longer_orphaned)
    )


class TestFixtureDetection:
    """Proves the detection logic itself works, entirely in memory (no
    disk writes) -- a deliberately-orphaned synthetic module must be
    flagged, and a deliberately-referenced one must not be."""

    def test_flags_a_deliberately_orphaned_fixture_module(self) -> None:
        module_sources = {
            "server_modules/fixture_orphan_module.py": (
                "def totally_unreferenced_fixture_function():\n"
                "    return 1\n"
            ),
        }
        corpus_sources = {
            **module_sources,
            "server_modules/some_unrelated_file.py": (
                "def unrelated():\n"
                "    return 'nothing to do with the fixture'\n"
            ),
            "server_modules/tests/test_fixture_orphan_module.py": (
                "from server_modules.fixture_orphan_module import totally_unreferenced_fixture_function\n"
                "\n"
                "def test_it():\n"
                "    assert totally_unreferenced_fixture_function() == 1\n"
            ),
        }

        orphans = find_orphans(
            module_sources,
            corpus_sources,
            excluded_prefixes=("server_modules/tests/",),
        )

        assert "server_modules/fixture_orphan_module.py" in orphans, (
            "A module referenced only by its own test file must be caught "
            "as an orphan -- this is the exact false-confidence shape "
            "(H1/H2) the whole check exists to catch."
        )

    def test_does_not_flag_a_module_with_a_real_outside_caller(self) -> None:
        module_sources = {
            "server_modules/fixture_live_module.py": (
                "def really_called_function():\n"
                "    return 1\n"
            ),
        }
        corpus_sources = {
            **module_sources,
            "server_modules/fixture_caller_module.py": (
                "from server_modules.fixture_live_module import really_called_function\n"
                "\n"
                "def do_the_thing():\n"
                "    return really_called_function()\n"
            ),
            "server_modules/tests/test_fixture_live_module.py": (
                "from server_modules.fixture_live_module import really_called_function\n"
                "\n"
                "def test_it():\n"
                "    assert really_called_function() == 1\n"
            ),
        }

        orphans = find_orphans(
            module_sources,
            corpus_sources,
            excluded_prefixes=("server_modules/tests/",),
        )

        assert "server_modules/fixture_live_module.py" not in orphans

    def test_comment_only_mention_does_not_count_as_a_reference(self) -> None:
        """Regression guard for the exact false-negative found while
        tuning this check: a code COMMENT mentioning a name must not count
        as a reference (only AST-level Name/Attribute/import/string-const
        references count)."""
        module_sources = {
            "server_modules/fixture_comment_only.py": (
                "def only_mentioned_in_a_comment():\n"
                "    return 1\n"
            ),
        }
        corpus_sources = {
            **module_sources,
            "server_modules/fixture_commenter.py": (
                "# this comment mentions only_mentioned_in_a_comment but never calls it\n"
                "def unrelated():\n"
                "    return 'nothing'\n"
            ),
        }

        orphans = find_orphans(
            module_sources,
            corpus_sources,
            excluded_prefixes=("server_modules/tests/",),
        )

        assert "server_modules/fixture_comment_only.py" in orphans


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
