#!/usr/bin/env bash
# Prunes agent worktrees and branches that are safe to remove — never a
# blanket sweep. CLAUDE.md already warned about this once ("Agent worktrees
# accumulate and nothing prunes them... 177 of them... filled the disk to
# 100%") and the warning alone did not stop it happening again, worse:
# 2026-08-19, 121 worktrees, 421 stale branches, 9.2G .git. A prose reminder
# an agent has to remember mid-task does not work. This script is the actual
# fix — cheap enough to run at the start or end of any session, and it is
# the thing test_worktree_sprawl_guard.py's failure message points at.
#
# SAFE BY CONSTRUCTION, not by care taken when invoking it:
#   1. Only considers worktrees whose path matches this repo's own known
#      conventions (empyralis-worktrees/, empyralis-wt*/, .claude/worktrees/).
#      A worktree under any other path — another tool's session directory,
#      e.g. `~/.codex/worktrees/...` (a real one was found registered in
#      this repo's own `git worktree list` on 2026-08-19 and correctly
#      skipped) — is left alone, unconditionally, no exceptions list needed.
#   2. Only removes a worktree whose branch has ZERO commits not on main
#      (`git rev-list --count main..<branch>` == 0) — never force-deletes
#      real work. Branch deletion uses `git branch -d` (not -D), which
#      itself refuses anything not fully merged — a second, independent
#      check, not just this script's own arithmetic.
#   3. Uncommitted changes are inspected, not assumed away: build artifacts
#      and known noise (frontend/next-env.d.ts, frontend/tsconfig.json,
#      test-results/, node_modules symlinks, the kernel target/ symlink)
#      are ignored; anything else uncommitted in a worktree is left alone
#      and reported, never silently discarded.
#
# Usage: scripts/prune-merged-worktrees.sh [--dry-run]

set -euo pipefail
cd "$(git rev-parse --show-toplevel)"

DRY_RUN=false
[ "${1:-}" = "--dry-run" ] && DRY_RUN=true

NOISE_PATTERN='next-env\.d\.ts|tsconfig\.json|empyralis-runtime-kernel/target$|node_modules$|test-results/|\.next-preview|bun\.lock$'
OWN_PATH_PATTERN='empyralis-worktrees/|empyralis-wt|/\.claude/worktrees/'

removed=0
skipped_foreign=0
skipped_uncommitted=0
skipped_unmerged=0

while IFS= read -r wt; do
  [ "$wt" = "$(pwd)" ] && continue

  if ! echo "$wt" | grep -qE "$OWN_PATH_PATTERN"; then
    skipped_foreign=$((skipped_foreign + 1))
    continue
  fi

  branch=$(git -C "$wt" rev-parse --abbrev-ref HEAD 2>/dev/null || echo "")
  if [ -z "$branch" ] || [ "$branch" = "HEAD" ]; then
    # Detached HEAD worktree (a baseline/probe worktree from a verification
    # pass). No branch to check merge status against — real uncommitted
    # changes are still the deciding factor.
    real_changes=$(git -C "$wt" status --porcelain=v1 2>/dev/null | { grep -vE "$NOISE_PATTERN" || true; } | wc -l | tr -d ' ')
    if [ "$real_changes" != "0" ]; then
      skipped_uncommitted=$((skipped_uncommitted + 1))
      echo "SKIP (uncommitted, detached): $wt"
      continue
    fi
    $DRY_RUN && { echo "WOULD REMOVE (detached, clean): $wt"; continue; }
    git worktree remove --force "$wt" && removed=$((removed + 1))
    continue
  fi

  real_changes=$(git -C "$wt" status --porcelain=v1 2>/dev/null | { grep -vE "$NOISE_PATTERN" || true; } | wc -l | tr -d ' ')
  if [ "$real_changes" != "0" ]; then
    skipped_uncommitted=$((skipped_uncommitted + 1))
    echo "SKIP (uncommitted work): $wt [$branch]"
    continue
  fi

  unmerged=$(git rev-list --count main.."$branch" 2>/dev/null || echo "?")
  if [ "$unmerged" != "0" ]; then
    skipped_unmerged=$((skipped_unmerged + 1))
    echo "SKIP (unmerged, $unmerged commit(s)): $wt [$branch]"
    continue
  fi

  if $DRY_RUN; then
    echo "WOULD REMOVE: $wt [$branch]"
    continue
  fi

  git worktree remove --force "$wt" && removed=$((removed + 1))
  git branch -d "$branch" >/dev/null 2>&1 || true
done < <(git worktree list --porcelain | awk '/^worktree/{print $2}')

echo
echo "removed=$removed  skipped_foreign=$skipped_foreign  skipped_uncommitted=$skipped_uncommitted  skipped_unmerged=$skipped_unmerged"
$DRY_RUN && echo "(dry run — nothing was actually removed)"
