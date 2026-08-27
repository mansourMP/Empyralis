# Execute: retire the "Sage" name — the whole job, Steps 0–5

Supersedes `CODEX-RENAME-EXECUTE.md`, which limited you to Steps 0–2. The
founder wants the complete rename. All five steps of your own proposal
(`CODEX-RENAME-PROPOSAL.md`) are approved, with the guardrails below.

Read `CLAUDE.md` at the repo root first. It is binding.

## The rule that governs all of it

**Every step must be provably inert or provably reversible.** Not "the tests
pass" — proven, with evidence, per batch. There is a demo coming. A step you
cannot prove is a step you do not ship; stop and report instead.

Steps 1–3 must be inert (nothing observable changes).
Steps 4–5 change things on purpose, so they must instead be *reversible at
every moment*, with the old form still readable until the new one is proven.

## Fix this in the harness before continuing — it is a real flaw

Your Step 0 tool produces a **false diff when no database is reachable**. Run
without `DATABASE_URL`, the `database_schema` section comes back empty and the
comparison reports thousands of changes that did not happen.

That is the failure mode this codebase documents repeatedly: a checker that
cries wolf is a checker people learn to ignore, and it is the one thing that
must stay trustworthy for the next five steps.

Fix it so an unavailable database is reported as **unknown**, distinct from
**changed** and distinct from **unchanged** — three states, never two. A run
that cannot see the schema must say so and refuse to claim the contract is
intact, rather than either passing or failing on an absence. Record in the
evidence file exactly how the tool must be invoked.

## Steps 1–2 — internal rename, must be inert

Per batch, without exception: snapshot → rename one small domain → snapshot →
**diff must be empty** → full Python suite and frontend unit+typecheck against
a baseline measured the same way on the parent commit → commit alone.

Never accept a non-empty diff by explaining it. Either the rename crossed a
contract boundary (revert it) or the harness is wrong (fix it first).

Batch order, smallest blast radius first: turn runtime → dispatcher/adapter →
memory → profile/skills/services/health/audit → tests, comments, CSS, docs.

UI work is serial. `fleet-theme.css` and the shared fleet components are
touched by nearly everything; file sets that look disjoint are not.

## Step 3 — route aliases, additive only

Add conventional routes **alongside** the `/api/sage-*` ones. Every old route
keeps working and keeps its behaviour. This step removes nothing.

Deploy ordering is a hard constraint: backend first, then clients. The gateway
and the desktop app are separately distributed and can be running an older
build for a long time — the desktop app self-updates on its own schedule and a
customer's copy may be days behind. No client may prefer a new path until the
backend carrying it is live everywhere.

Prove: every old path still resolves, with the same response shape, after the
aliases exist.

## Step 4 — persisted values, dual-write and never destructive

This is the step that can actually hurt, so it gets the most care.

**Two of the affected tables are money.** Verified in production:
`credit_ledger_events.surface` (309 rows) and `usage_events.surface`/`id`
(257 rows) carry the old name, alongside `agent_trace_events.agent_id` (463),
`agent_traces.root_agent_id` (176) and ~10 more columns. Renaming a value in a
billing ledger edits financial history.

Therefore, per value:

1. **Dual-read first.** Teach every reader to accept both forms, deploy that
   alone, and prove it with the old data still in place. Nothing is written yet.
2. **Dual-write second.** New rows carry the new form; readers already handle
   both. Deploy alone.
3. **Backfill third**, in a separately reviewed migration, with row counts
   captured before and after and per-workspace invariants checked. For any
   scoped table, obey the RLS two-part rule — add the new preflight key, deploy,
   *then* rename — and keep the old key so the revert path also boots.
4. **Keep the old-form reader** until there is evidence nothing needs it.

Do not migrate a value whose only cost is untidy grep output. A billing row's
`surface` column is read by reporting; changing it must be worth something
concrete or it should stay.

One correction to your inventory, verified in production: `agent_threads` is
**empty** (zero rows) while `agent_traces.thread_id` holds 98 sage-ish values.
Do not assume those two move together.

## Step 5 — deprecation removal, evidence-gated

Remove an alias or a legacy reader only when you can show it is unused: observed
traffic on the old route at zero (or a threshold the founder accepts), and no
remaining rows in the old form. Each removal is its own commit, separately
revertible. This step is optional and is not required for the rename to be
worth having.

## Never, at any step

- Change a human-typed phrase (e.g. the `WIPE SAGE MEMORY` confirmation)
  without saying so explicitly — someone has muscle memory for it.
- Rewrite historical migration comments or archived evidence as though the old
  name never existed. Annotate; preserve provenance.
- Rename the product (Empyralis) or the customer-facing label "Ask AI".
- Break a separately-deployed client to make a name tidier.

## Subagents

You may use your own subagents; parallelising mechanical batches is sensible.
Use the **Luna** model. One agent = one worktree = one branch, and parallel only
where file sets are genuinely disjoint. UI serially.

## Definition of done, per batch

- Steps 1–3: snapshot diff empty.
- Steps 4–5: old form still readable, rollback path stated and tested.
- Test counts equal to or better than the measured baseline.
- Committed alone, revertible alone.
- The commit message states what was **verified**, not what was intended.

## Report

Per batch: what the diff showed, before/after test counts and how the baseline
was measured, the commit SHA, and for Steps 4–5 the rollback path. Say plainly
what you skipped and why. If a step cannot be proven, stop and report — an
unprovable rename is not worth the demo.
