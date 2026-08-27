# Research task: retire the "Sage" name

**This is a research task. Do not change any code. The deliverable is a written proposal.**

## The goal

"Sage" was the name of an agent concept that was removed from this product months ago. The concept is gone; the name is still everywhere in the codebase. We want the naming to match what the thing actually is now, in ordinary industry terms that a newly hired engineer would recognise on their first day without having to learn our history.

The founder's framing: he expects to hire people, and he wants a codebase where a competent engineer understands what a module does from its name. Not a house vocabulary they have to be initiated into.

Two things this rename is **not**:
- It is not a rebrand of the product. The product is Empyralis. This is about internal module, route, and identifier naming.
- It is not an invitation to invent a new house vocabulary. If a boring, conventional name exists for what a thing does, that is the right name.

## What the name currently covers

This matters, because "Sage" is not one concept. Establishing what distinct things wear the name is the first half of the research.

At minimum it appears to span:
- The **workspace-level agent** every workspace gets — currently surfaced to customers as "Ask AI". Every workspace member gets their own; conversations are private per person.
- The **turn runtime** — the code that assembles a prompt, runs a model turn, dispatches tools, and persists the result (`sage_agent_runtime_service.py` and neighbours).
- **HTTP routes** — `/api/sage-chat/*`, `/api/sage-memory/*`, `/api/sage-capabilities`, `/api/sage-heartbeat`.
- **Stored values and identifiers** — e.g. `SAGE_MAIN_AGENT_ID = "sage_main_agent"`, a thread id literal `"sage-main"`, a `WIPE SAGE MEMORY` confirmation string a human types.
- Various services, adapters, dispatchers and tests named `sage_*`.

Whether those should end up sharing one new name or being split into several differently-named things is a question for the proposal, not a given.

## Measured scale (verified, not estimated)

- ~28,700 textual occurrences of "sage" across ~1,170 files (excluding `node_modules`, build output, and vendored reference source).
- 24 Python modules named `sage_*.py` in `server_modules/`.
- The name reaches `migrations/*.sql`, so some of it is in the database, not only in code.

## Required reading before proposing

`CLAUDE.md` at the repo root is the project's standing-decisions file and is binding. It is long; the sections that bear directly on this work are the ones about naming drift, the two-engine seam, "built, tested, and never wired", stale string matching, and the deploy/migration rules. Two of its rules apply with unusual force here:

1. **A renamed table or column that carries `tenant_id`/`workspace_id` is a two-part change**, because a boot-time RLS coverage check is keyed by table name and fails closed on an unknown table. Doing it in one step has already taken production down once, for a cosmetic rename.
2. **Stale string matching is a documented recurring failure here.** Anything that matches on a literal — an error bucket, a channel key, a thread id, a confirmation phrase a human types — is a place a rename silently changes behaviour rather than failing loudly.

## What we want back from you

A written proposal covering:

1. **An inventory.** What distinct concepts currently share this name, and what each one actually does. Name them by behaviour, not by module path.

2. **Proposed names**, with reasoning. Conventional over clever. If two things should stop sharing a name, say so and say why.

3. **A blast-radius map, split by reversibility.** Specifically separate:
   - Internal code identifiers (cheap, mechanical, low risk)
   - HTTP routes (breaking for any existing client — including the desktop app and the gateway, which are separately deployed and update on their own schedule)
   - Stored data: database table/column names, and *values* stored in rows (an id like `sage_main_agent` already written into production rows is not a rename, it is a migration)
   - Human-facing strings, including any a person is asked to type
   - Environment variables and deployment configuration

4. **A sequenced plan** where each step is independently deployable and independently revertible, with the two-part rule above respected wherever it applies. Say explicitly which steps require a migration and which require a deploy ordering constraint.

5. **What you would NOT rename, and why.** A rename that buys nothing but costs a migration or breaks a deployed client should be argued against, not performed. We would rather ship 80% of this safely than 100% of it with an outage.

6. **How correctness gets verified at each step** — how we would know the rename did not silently change behaviour, given the stale-string-matching failure mode above.

## Constraints

- Production is a single VPS. Frontend and backend deploy together; the **gateway and the desktop app do not** — they are separately distributed and can be running an older build when the backend changes. Any route or protocol rename has to survive that skew.
- There is a large known-failing Python test baseline. Measure before/after the same way rather than reporting raw failure counts as regressions.
- Assume the work will be executed incrementally, reviewed, and merged by someone else. Optimise the plan for reviewability.

## Deliverable

A single markdown document. No code changes, no branches, no commits.
