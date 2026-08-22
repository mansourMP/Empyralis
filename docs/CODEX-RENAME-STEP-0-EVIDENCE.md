# Sage rename Step 0 evidence

The contract snapshot tool is `scripts/rename_contract_snapshot.py`. It reads
the live FastAPI app, live direct-chat registry, live MCP registry, connected
Postgres catalog, source environment reads, frontend build manifests, and
frontend CSS references. The checked-in baseline is
`docs/rename-contract-baseline.json`.

## Baseline

Captured on 2026-08-22 from the parent checkout with no rename applied:

| Surface | Entries |
|---|---:|
| Live HTTP route method/path entries | 946 |
| Live direct-chat tool names | 73 |
| Live MCP tool names | 29 |
| Database schema table/column entries | 1,397 |
| Environment variable names plus dynamic reads | 2 lists |
| Persisted/protocol/high-signal literals | 4,979 |
| Frontend build-route strings | 22 |
| Frontend CSS class references | 1,347 |

The database snapshot was taken from the local Postgres catalog using the same
information-schema query the tool runs against the configured runtime database.
No database writes were performed.

## The baseline was machine-dependent, and every worktree saw a false diff

Re-measured on 2026-08-22 in a clean worktree of the same commit: the
comparison reported **changed** before a single line was renamed. Two scanner
flaws, both fixed here, both of the family this repository already documents —
a checker that cries wolf is one people stop reading, and this is the checker
the whole rename depends on.

**1. It scanned git-ignored local junk.** The walk kept anything on disk with a
source suffix. The primary checkout carries `empyralis-gateway/dist.bak`
(ignored by `*.bak`), 167 stale compiled files, and the original baseline was
captured with them in scope. So the baseline pinned contract items that exist
in no tracked file — `PHONE_CODE_EXPIRED`, `SESSION_PASSWORD_NEEDED`,
`AUTH_KEY_UNREGISTERED` and the rest of the gramjs Telegram vocabulary this
repository deleted — plus 12 environment names from the same dead build. The
scan is now restricted to `git ls-files`, and raises rather than falling back
if git cannot answer. Measured after the fix: the primary checkout and a clean
worktree produce byte-identical file sets (2,228), environment names (639) and
literals (4,979).

**2. It treated a source filename as a persisted contract.** Fifteen values
were the basename of a tracked source file (`sage_agent_runtime_service.py`,
`channel_adapter.py`, `agent-create-model.ts`, …), collected only because the
constant holding them happens to be uppercase (`_SAGE_RUNTIME_PATH`,
`LIVE_HANDLER`). A module path is not a persisted value, a wire field, or a
phrase anyone types, and keeping it would make the guard fire on the one thing
an internal rename is supposed to change while proving nothing: what a renamed
route module exposes is measured directly by `http_routes` off the live
`server.app`. The exclusion is an exact match against tracked-file basenames,
never a `.py` suffix test, so a persisted value that merely looks like a
filename stays in the guard.

Both fixes were applied and the baseline regenerated **before** any rename, so
neither can be mistaken for accommodating one.

## Determinism check

Command (the comparison baseline must use an explicitly reachable database):

    DATABASE_URL=postgresql://postgres:postgres@127.0.0.1:5432/empyralis \
    venv/bin/python scripts/rename_contract_snapshot.py \
      --output /tmp/rename-contract-repeat.json \
      --compare docs/rename-contract-baseline.json

Result: exit 0. The repeat output was byte-identical to the checked-in
baseline. The collector sorts every set-like surface and emits canonical JSON.
Literal collection is limited to contract-shaped constant assignments, known
persisted IDs, and human confirmation phrases; it excludes comments,
docstrings, test prose, and quoted internal function references.

## Mutation-detection check

Command:

    venv/bin/python scripts/rename_contract_snapshot.py \
      --demo --output /tmp/rename-contract-demo.json

The demo temporarily:

1. registers a deliberate live HTTP route;
2. adds a deliberate direct-chat tool to the live registry view; and
3. adds a deliberate CSS class reference in a temporary frontend fixture.

It asserts that each mutation produces a non-empty diff naming the injected
route, tool, or CSS class. The route is removed in `finally`, the tool registry
is restored by the patch context, and the CSS fixture is deleted with its
temporary directory. Output:

    demo: route change detected
    demo: direct-chat tool change detected
    demo: CSS reference change detected

No production route, tool, CSS class, database value, or application source
was changed by the demo.

## Database unknown state

The database section has three meaningful states: `known`, `unknown`, and a
known snapshot whose entries differ from the baseline. An unreachable database
is recorded as `{"state": "unknown", "reason": "..."}`; it is never encoded as
an empty schema. A comparison with an unknown current or baseline schema exits
with code 2 and says `contract snapshot unknown`, so it cannot claim the
contract is intact.

Example verification command:

    DATABASE_URL=postgresql://postgres:postgres@127.0.0.1:1/does_not_exist \
    venv/bin/python scripts/rename_contract_snapshot.py \
      --output /tmp/rename-contract-unknown.json

Observed result: exit 2, `state: unknown`, reason `ConnectionRefusedError`.
Do not use an unknown snapshot as a rename baseline.

## Step 0 boundary

This commit adds only the guard, its checked-in baseline, and this evidence.
Steps 1–2 have not started. In particular, no HTTP route string, persisted
value, protocol/tool name, human confirmation phrase, environment variable,
database table/column name, migration, historical comment, or archived record
was renamed.

## Where the rename stands (2026-08-22)

Steps 1–2 of `CODEX-RENAME-PROPOSAL.md`, internal module names only. Seventeen
of the twenty-four `server_modules/sage_*.py` modules are renamed, in four
commits, each one verified with a clean contract snapshot and a full Python
suite whose FAILING SET is byte-identical to the parent commit's — 533
FAILED/ERROR node ids, zero added, zero removed. Counts alone would not prove
that; the set does.

| Batch | Domain | Commit |
|---|---|---|
| 0 | harness fix + baseline regeneration | `17ca59ed` |
| 1 | turn runtime | `9ffc2eb8` |
| 2 | command dispatcher / turn adapter / reply dispatcher | `598f42cf` |
| 3 | assistant memory | `5ebf54f9` |
| 4 | profile, skills, services, health, audit, transparency, doctor | `7c31fcf7` |

Nothing observable moved: the 56 `/api/sage-*` route entries, the three
`sage_service__*` wire tool names, every persisted value (`sage-main`,
`sage_main_agent`, the memory audit action names), the `WIPE SAGE MEMORY`
confirmation phrase and every `*SAGE*` environment key are byte-identical to
the baseline.

**Still carrying the old stem, and why each was left:**

| Module | Reason |
|---|---|
| `sage_chat_api.py`, `sage_context_files_api.py` | `test_workspace_storage_accounting.py` holds both as real filesystem paths (`LIVE_HANDLER` / `SHADOWED_TWIN`) to prove which of the twin `POST /api/sage-chat/attachments` registrations wins. Renaming means editing that test in the same commit — mechanical, but not a pure substitution. |
| `sage_dreaming_pipeline.py` | Named in `test_module_reachability.ALLOWLISTED_ORPHANS`, which asserts the file still exists. Same one-line coupling. |
| `sage_telegram_hosted_service.py` | Provider-specific hosted-channel surface. The proposal says review these independently rather than sweeping them into the core rename. |
| `sage_agent_computer_selection_service.py`, `sage_daily_operator_service.py`, `sage_instruction_compiler_service.py` | Straight substitutions, simply not reached. |

Also untouched, deliberately: identifiers such as `handle_sage_chat`,
`execute_sage_turn`, `dispatch_sage_reply_safe`, `build_sage_memory_context_
block` and `SAGE_THREAD_ID`. Several share a name with a persisted audit action
string, so they belong to Step 4's dual-read/dual-write, not to a textual edit.
Test filenames (`test_sage_*.py`) and the `.md` documentation are the later
cosmetic batch; the historical comment in
`migrations/unify_fleet_tool_toggle_ids.sql` stays as written — migration
history is annotated, never rewritten.

**Frontend verification, stated plainly:** `npm run test:unit` and
`npx tsc --noEmit` were NOT run. A git worktree has no `node_modules`, and this
repository's own notes forbid symlinking one under Turbopack. What was proven
instead is stronger for these commits than a green test run would have been:
across the whole branch, **zero** changed `.ts`/`.tsx` lines are anything other
than `//`, `*` or `/*` — every frontend touch is a comment. A batch that
changes real frontend code must install dependencies in the worktree first.
