"""A structural tripwire for the exact incident CLAUDE.md already warned
about once and it happened again anyway, worse: "Agent worktrees accumulate
and nothing prunes them" (2026-08-07, 177 worktrees, disk to 100%) was a
PROSE reminder, and on 2026-08-19 the count reached 121 worktrees, 421 stale
branches, 9.2G of .git before anyone noticed -- proof that a warning nobody
is forced to look at does not work.

This test is the fix's other half. `scripts/prune-merged-worktrees.sh` is
the actual cleanup tool (safe by construction: only removes a worktree whose
branch has zero commits not on main AND no real uncommitted changes, and
only ever touches paths under this repo's own worktree conventions -- a
worktree belonging to another tool, e.g. a Codex session registered in this
repo's own `git worktree list`, is left alone unconditionally). This test
is what makes running it a NAG instead of a hope: it fails loudly, in the
ordinary pytest run every agent is already told to execute, the moment
sprawl crosses a threshold nobody would let slide if they were looking.

THRESHOLDS ARE GENEROUS ON PURPOSE. A heavy multi-agent session can
legitimately have 15-20 worktrees alive at once (this project's own
"medium" workflow guideline). The point is not to block a busy day --
git worktree/branch counts are read at test time, not enforced at
creation time, so this can never block real work mid-task -- it is to
make the OVERNIGHT-ACCUMULATION case (worktrees nobody deleted after
merging, days or weeks old) visible in a place someone will actually see
it, the same way `test_rls_dml_drift.py` and `test_module_reachability.py`
turn a silent-until-someone-notices class of drift into a red test.

CANARY: if `git worktree list` or `git branch` cannot be run at all (not
a git repo, git missing), that is reported as its own failure rather than
silently passing -- a check that can't see its subject must not report
green, exactly as CLAUDE.md's own "a check that derives its own
expectations from the thing it checks is blind" entry requires elsewhere.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]

# Generous: real heavy-dispatch days should never trip this. It exists to
# catch OVERNIGHT accumulation (merged worktrees nobody deleted), not to
# throttle a busy session.
WORKTREE_WARN_THRESHOLD = 20
BRANCH_WARN_THRESHOLD = 30


def _run_git(*args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=30,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"git {' '.join(args)} failed (exit {result.returncode}): {result.stderr.strip()}"
        )
    return result.stdout


def test_git_commands_are_reachable_canary() -> None:
    """If this fails, the two tests below cannot see their subject at all --
    they must not be trusted to report sprawl-free in that case. Mirrors the
    canary pattern in test_rls_dml_drift.py / test_module_reachability.py."""
    output = _run_git("rev-parse", "--is-inside-work-tree")
    assert output.strip() == "true", (
        "git rev-parse did not report a work tree -- this test file's git "
        "calls cannot be trusted; investigate before treating a green run "
        "of the sprawl checks below as meaningful."
    )


def test_worktree_count_has_not_silently_grown() -> None:
    output = _run_git("worktree", "list", "--porcelain")
    worktree_paths = [
        line.split(" ", 1)[1]
        for line in output.splitlines()
        if line.startswith("worktree ")
    ]
    assert worktree_paths, (
        "`git worktree list` returned no worktrees at all, not even the "
        "primary checkout -- the parse is almost certainly broken, not the "
        "repo. Fix the parse before trusting this test either way."
    )
    count = len(worktree_paths)
    assert count <= WORKTREE_WARN_THRESHOLD, (
        f"{count} worktrees exist (threshold {WORKTREE_WARN_THRESHOLD}). "
        "This is exactly the 2026-08-07 / 2026-08-19 incident shape -- "
        "merged agent worktrees nobody deleted, accumulating until disk "
        "pressure kills running agents. Run "
        "`scripts/prune-merged-worktrees.sh --dry-run` to see what is safe "
        "to remove, then without --dry-run to actually remove it. Anything "
        "the script skips (unmerged commits, real uncommitted changes, or "
        "a path outside this repo's own worktree conventions) is left "
        "alone by design -- read its skip reasons before assuming "
        "something is wrong with the count itself."
    )


def test_branch_count_has_not_silently_grown() -> None:
    output = _run_git("branch", "--format=%(refname:short)")
    branches = [line.strip() for line in output.splitlines() if line.strip()]
    assert branches, (
        "`git branch` returned no branches at all, not even main -- the "
        "parse is almost certainly broken, not the repo."
    )
    non_main = [b for b in branches if b != "main"]
    assert len(non_main) <= BRANCH_WARN_THRESHOLD, (
        f"{len(non_main)} non-main branches exist (threshold "
        f"{BRANCH_WARN_THRESHOLD}). Most of these are very likely already "
        "fully merged and just never deleted -- `scripts/"
        "prune-merged-worktrees.sh` deletes a branch's ref (via the safe "
        "`git branch -d`, which itself refuses anything not fully merged) "
        "as part of removing its worktree. A branch with no worktree "
        "attached needs a plain `git branch -d <name>` instead; check "
        "first with `git rev-list --count main..<name>` -- zero means "
        "safe to delete, non-zero means real work that needs a merge "
        "decision, not a deletion."
    )
