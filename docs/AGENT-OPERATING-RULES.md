# Agent operating rules

Binding process law for running multiple agents (or multiple sessions of the
same agent) against this repository. This governs *how* work gets divided
and landed, not what the work is — see `docs/UI-CONTRACT.md` for the UI
design law.

## The collision protocol

- **One agent = one worktree = one branch.** Never two agents editing the
  same working tree live. A worktree is cheap; a corrupted merge from two
  agents racing on the same files on disk is not.

- **Parallel ONLY if file-sets are disjoint.** Before starting agents
  side by side, name the files each one will touch. If there's any overlap
  — even a single shared file — don't run them in parallel. If the two
  file-sets *can* touch the same file, treat it as overlap and run
  SEQUENTIALLY, even if this particular pair of tasks happens not to.

- **All fleet UI shares files.** `fleet-theme.css`, `FleetAgentDetail.tsx`,
  `PrimaryRail.tsx`, and the list/toolbar components
  (`AgentsList.tsx`, `FleetToolbar.tsx`, `FleetRightPanel.tsx`, and
  friends) are load-bearing across nearly every fleet surface. UI work is
  SERIAL: one agent, commit to `verify`, then the next. No parallel UI
  agents, ever — the file-set is never actually disjoint once
  `fleet-theme.css` is in play, no matter how unrelated two UI tasks look
  on paper.

- **Backend/Gateway-only work may run in parallel**, each in its own
  worktree, as long as it touches none of the files above (or any other
  file a concurrent agent is also touching).

- **Commit before starting the next agent.** The commit is the save point.
  Don't hand off a worktree with uncommitted changes — the next agent (or
  the next you) should be able to `git log` and see exactly what state the
  branch was in when the handoff happened.

- **Before merging: grep for the pattern the change fixed.** Don't trust a
  clean merge — auto-merge reconciles text, not intent. A merge with zero
  conflicts can still silently reintroduce the exact bug a parallel commit
  just fixed (e.g. one branch re-adds a stale usage of something the other
  branch just renamed or removed). Re-run the search that found the
  original problem against the merged tree before considering the merge
  done.

## Why this exists

Every rule above maps to a real failure mode, not a hypothetical:

- The "one worktree, one branch" rule exists because two agents writing to
  the same files on disk produces silent, non-conflicting corruption —
  neither agent's edits show up as a normal merge conflict, they just
  partially clobber each other in whatever order the writes happened to
  land.
- The "fleet UI is serial" rule exists because `fleet-theme.css` alone is
  the shared stylesheet for the rail, every list surface, every dialog,
  and the mobile drawer — two UI tasks that look unrelated by feature name
  ("fix the wizard footer" vs. "resize the drawer") routinely turn out to
  both touch the same file, often the same few-hundred-line region of it.
- The "grep before merging" rule exists because a text-level clean merge
  is not the same claim as "the bug is still fixed." Verify the property
  the commit established, not just the diff's absence of `<<<<<<<` markers.
