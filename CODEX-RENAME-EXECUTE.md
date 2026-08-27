# Execute: retire the "Sage" name — Steps 0–2 only

Your own proposal (`CODEX-RENAME-PROPOSAL.md`) is approved for **Steps 0–2 only**.
Steps 3, 4 and 5 are explicitly NOT approved. Do not add route aliases, do not
migrate persisted values, do not remove anything for deprecation.

Read `CLAUDE.md` at the repo root first. It is binding.

## The one rule that governs this work

**A rename must provably change nothing observable.** Not "the tests still pass"
— provably nothing. So the first deliverable is not a rename. It is the tool
that proves a rename was inert, and evidence that the tool actually detects a
change.

There is a demo coming. The bias is overwhelmingly toward safety: shipping 60%
of this with certainty beats 100% with a surprise.

## Step 0 — build the contract snapshot FIRST, and prove it works

Write a script that emits a canonical, deterministically-ordered snapshot of
everything a rename could accidentally alter. At minimum:

- Every registered HTTP route: method + path template, from the live app object,
  not from grepping source.
- Every tool name the model can be offered — the live registry, not a literal
  list. `skills_service` exposes a registered-tool-names function used for
  logging; use the real source of truth.
- Every MCP tool name, from the live server, not from `EMPYRALIST_MCP_TOOLS`.
- Database schema: table names plus column names.
- Every environment variable the code reads.
- Persisted/protocol string constants and any phrase a human is asked to type.
- Frontend: the route table from the build output, and the set of CSS class
  names actually referenced.

Then **prove the snapshot detects change**. Plant one deliberate break of each
kind — rename a single route, rename one tool, rename one CSS class — confirm
the diff is non-empty and names the right thing, then revert. A verifier nobody
has seen fail is not a verifier. This codebase has shipped guards that silently
enforced nothing; do not add another.

Commit the tool and the evidence. No rename in this step.

## Steps 1–2 — rename in small batches, each one proven inert

For every batch, without exception:

1. Snapshot.
2. Rename one small domain.
3. Snapshot again.
4. **The diff must be empty.** If it is not, either the rename crossed a
   contract boundary — revert it — or the snapshot is wrong and needs fixing
   before continuing. Never accept a non-empty diff by explaining it.
5. Run the full Python suite and the frontend unit + typecheck. Compare against
   a baseline you measured the same way on the parent commit. Report
   before/after counts, never raw counts.
6. Commit. One batch per commit, each independently revertible.

Suggested batch order, smallest blast radius first: turn runtime → dispatcher/
adapter → memory → profile/skills/services/health/audit → tests, comments, CSS,
docs.

## Never touch in this work

- HTTP route strings, including `/api/sage-*`.
- Persisted values: `sage-main`, `sage_main_agent`, and any value already
  written into production rows.
- Tool names and protocol field names on the wire.
- Human-typed phrases, e.g. the `WIPE SAGE MEMORY` confirmation.
- Environment variable names.
- Database table and column names.
- Historical migration comments and archived evidence — annotate, do not
  rewrite history.

**Verified in production while reviewing your proposal, and it strengthens your
own recommendation:** stored "sage" values are not incidental. They appear in
15+ columns across thousands of live rows, and two of those tables are money —
`credit_ledger_events.surface` (309 rows) and `usage_events.surface`/`id` (257).
Renaming a persisted value there would touch billing history. Your proposal
argued against this on general caution; the real reason is stronger, and it is
why Step 4 stays unapproved.

One correction to your inventory: `agent_threads` is **empty** in production
(zero rows) while `agent_traces.thread_id` holds 98 sage-ish values. Do not
assume those two move together.

## Subagents

You may use your own subagents, and parallelising the mechanical batches is
sensible. Use the **Luna** model for them. Two constraints from this repo's
operating rules, which apply to subagents as much as to you:

- One agent = one worktree = one branch. Never two agents editing the same
  working tree.
- Parallel only if the file sets are genuinely disjoint. Frontend UI files are
  never disjoint in practice — `fleet-theme.css` and the shared fleet
  components are touched by almost everything. Do UI serially.

## Definition of done, per batch

- Snapshot diff empty.
- Test counts equal to or better than the measured baseline.
- Committed alone, revertible alone.
- The batch's own commit message states what was verified, not what was
  intended.

## Report

For each batch: what the snapshot diff showed, the before/after test counts and
how you measured the baseline, and the commit SHA. State plainly anything you
skipped and why. If a batch cannot be proven inert, stop and report rather than
working around it — an unprovable rename is not worth the demo risk.
