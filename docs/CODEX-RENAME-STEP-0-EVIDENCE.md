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

## Phase 2, batches 5-6 (2026-08-28)

Two more module renames, each committed alone and each verified before commit.

| Batch | Domain | Commit |
|---|---|---|
| 5 | daily operator, instruction compiler | `d4183174` |
| 6 | agent-computer selection | `08631d99` |

`server_modules/sage_*.py` is down from 7 modules to 4. Nothing observable
moved: all 955 live HTTP route entries (the 56 `/api/sage-*` among them), the
73 direct-chat tool names (all three `sage_service__*`), 29 MCP tools, 1,264
schema table.column entries, 640 environment names, 5,015 persisted/protocol
literal VALUES, 97 frontend build routes and 1,368 CSS class references are
byte-identical to the pre-batch snapshot.

**The guard was proven in this worktree before it was trusted, in both
directions.** `--demo` detects an injected route, tool and CSS class; a repeat
run against an unchanged tree is byte-identical to its own baseline (exit 0).
A verifier that has only ever been seen to pass is not a verifier.

### The snapshot records provenance, so renaming a FILE is a non-empty diff

`environment_variables.dynamic_reads` and `persisted_protocol_literals` are
stored as `path:line:value`. Renaming a file that contains a collected literal
therefore moves those entries even when every value is unchanged. That is not
an excuse to wave a diff through: the check is now that the VALUE sets are
equal with the `path:line:` prefix removed, and the script doing it exits
non-zero if any value is added or removed. It DID exit non-zero on the first
attempt at batch 6, which is how the next item was found.

### The guard's own coverage rule contains the word being renamed

`collect_contract_literals`'s `key_hint` (scripts/rename_contract_snapshot.py)
lists `SAGE` among the tokens that make a constant "contract-shaped". So a
literal is collected only while the constant holding it still has a qualifying
name — and renaming a `SAGE_*` constant silently narrows the very guard the
rename is verified with.

Measured, not inferred: renaming `SAGE_AGENT_COMPUTER_SELECTION_DB_FILE`
dropped `EMPYRALIS_SAGE_AGENT_COMPUTER_SELECTION_DB` and
`sage-agent-computer-selection.sqlite3` out of the literal set (5,015 ->
5,013) while both strings remained in the source. Restoring the constant name
restored them. Across the repo, **67 constants holding 360 literal values are
collected only because of that token — 33 genuinely `SAGE_*`, and 34 that
match by accident because `MESSAGE` contains "sage"**.

The constant keeps its name here for an independent reason: it names the
environment variable it reads, which is a frozen contract, so keeping them
matching keeps the path greppable from env var to reader. But the general
problem stands and should be fixed before any further `SAGE_*` constant is
renamed — widen `key_hint` with `DB|FILE|PATH|DIR|URL` so coverage follows
what a constant HOLDS rather than what it is called, regenerate the baseline
in its own commit, and only then rename.

### Two flaws in the pass's own measurement, both found and fixed

Neither is in the repository; both are recorded because the next person will
build the same instrument.

1. `grep '^FAILED\|^ERROR'` over pytest output also matches LOG lines
   beginning `ERROR ` — which carry random UUIDs, so the "failing set"
   differed on every run and could never report unchanged. Requiring the token
   after the verb to be a `server_modules/tests/` path took the baseline from
   613 entries to 489 real node ids.
2. A concurrent log write with no trailing newline glues itself onto a summary
   line, corrupting one node id in whichever run it lands in and reading as a
   regression. One such case existed in the baseline and was cut at the
   timestamp.

### The suite has an order-dependent hang, and it is not new

`test_mcp_oauth_provider.py::test_resolve_workspace_read_only_scope_blocks_writes`
blocks forever when reached ~5,341 tests into a single 10,333-test run, and
passes in 1.86s in isolation. That is why this pass measured the suite as
eight fixed chunks of the sorted test-file list rather than one process. The
split is identical before and after, so the comparison is like-for-like; it is
NOT comparable to a single-process run's numbers.

Test FILENAMES were deliberately not renamed in these two batches: renaming
one changes the sorted list the chunking derives from, which would invalidate
the before/after node-id comparison. They belong in their own batch, compared
through an explicit old->new path map.

### Result

Full Python suite, 849 test files, measured as the same 8 chunks before and
after: **489 distinct FAILED/ERROR node ids on both sides, set delta 0** —
zero added, zero removed. Counts alone would not prove that; the set does.
Frontend `npm run test:unit` and `npx tsc --noEmit` both exit 0 before and
after (and both batches touch zero frontend files, so that is a control rather
than a claim).

Separately, every one of the 72 changed lines across the two commits is
explained by applying the 5-entry rename map to the removed line — 72 removed,
72 added, zero lines unaccounted for. A rename batch hiding a real edit would
show up there and does not.

The `preflight._STATE_HOME_BAKED_AT_IMPORT_EXCEPTIONS` key move was proven
red-before-green in memory: with the old key the boot check reports a
violation, with the new key it returns clean.

## Phase 2, batch 8 — test filenames and CSS classes (2026-08-28)

Two rename batches and one comment fix, each committed alone.

| Batch | Domain | Commit |
|---|---|---|
| 8a | 40 sage-named pytest files | `ad4c54a9` |
| 8b | 33 `fleet-sage-*` CSS classes | `3249a26a` |
| 8e | the governance gate's lying `except` comment | `4473dbd0` |

`server_modules/tests/test_sage_*.py` is down from 42 to 3, and every
`fleet-sage-*` class name is gone from the product.

### The measurement, and how the deferred test-filename batch was unblocked

Renaming a test file moves the sorted file list that batches 5-7 chunked their
suite measurement by, which is why they deferred it. The unblocking device is
an explicit old->new path map used in three places at once:

```
BEFORE   sorted(git ls-files server_modules/tests/test_*.py)   849 files, 8 chunks
AFTER    the SAME ordered list with the map applied per entry
         -> every file stays in the same chunk, same position within it
```

So the order-dependent hang cannot move around underneath the comparison, and
the before/after failing sets are directly comparable through the map.

Result: **489 distinct FAILED/ERROR node ids on both sides, set delta 0**
(75 of them moved). Collection: 10,333 node ids both sides, the map explains
all 541 that moved, zero added, zero removed.

### Two measurement flaws found in this pass's own instrument

Neither is in the repository. Both are recorded because the next person will
build the same instrument and hit them.

1. **The previous pass's fix for glued log lines was not enough.** It cut a
   node id at whitespace. The glue that actually occurs here is an ISO
   timestamp with NO separator — `...unavailable2026-08-28 21:5...` — so the
   whitespace cut leaves the date attached. It landed on a DIFFERENT node id in
   each run, so the naive parse reported **2 regressions and 2 fixes** in
   `test_session_service.py` where nothing had changed. Cutting the id at the
   first `yyyy-mm-dd` as well takes both sides to a clean 489/489.
2. **The snapshot's `frontend_build_routes` section needs a Next build**, and a
   worktree has none. Built with `next build --webpack` (turbopack refuses the
   symlinked `node_modules`, exactly as this file records for the dev server).
   The section is route paths, not hashed chunk names, so it is stable across
   rebuilds — verified by rebuilding for batch 8b and getting the same 97.

### The contract database is `empyralis_first_customer`

There is no `empyralis` database on this box and `postgres:postgres` is not a
valid role, so the command in the Step 0 section above exits 2 (`unknown`) —
which is the three-state behaviour working, not a failure. The local database
whose public schema is 89 tables / 1,264 columns is `empyralis_first_customer`,
and 1,264 is the exact figure the batch 5-6 evidence records, so it is the one
that keeps this measurement comparable with that history. Read-only;
information_schema only.

### 8a — test filenames

A test is named after its SUBJECT, so the three left behind are the three whose
subject module still carries the stem: `sage_telegram_hosted_service.py`,
`routes_sage_telegram_hosted.py`, `personal_channel_sage_bridge_service.py`.
Where an exact module rename exists the test takes that module's new name.
ONE name is not a mechanical stem swap and is called out rather than buried:
`test_sage_heartbeat_runtime_health.py` -> `test_assistant_health_runtime_gate.py`,
because the mechanical result would have been "assistant_health_runtime_health".

Contract snapshot: EVERY section byte-identical, including
`persisted_protocol_literals` on the RAW `path:line:value` entries and not only
on values with the prefix stripped — i.e. no renamed test file contributes a
collected literal at all. Pure substitution: 23 removed / 23 added / 0
unexplained; all 40 moves were 100%-similarity renames.

23 pinned references moved with the files across 18 files. Only two are
executable — `scripts/local_certification_harness.sh` actually runs one, and
`docs/design/mcp-current-state.md` documents a pytest command line. The rest
are cross-reference comments, plus 3 in CLAUDE.md. The rewrite uses a
word-boundary rule so a test FUNCTION sharing a prefix
(`test_sage_turn_success` vs the `test_sage_turn_adapter` file) is untouched.

Test FUNCTION names containing "sage" are deliberately untouched: they are not
filenames, and moving them changes node ids the path map cannot express.

### 8b — CSS classes, proven a different way

The snapshot treats a CSS class name as contract and is right to, so this batch
cannot be proven inert. It is proven by an exact swap instead.

```
33 distinct tokens   113 occurrences -> 113
old tokens remaining repo-wide: 0
new tokens with no old twin:    0
declared set / used set: byte-identical once expressed in the old names
   32 declared · 26 used · the same 7 dead rules still dead
   `launcher-btn` still the one class used without a rule (pre-existing)
```

One substitution rule (`fleet-sage-` -> `fleet-assistant-`) covers all 33
because they share the stem, so the swap cannot answer differently for
`fleet-sage-chat` than for `fleet-sage-chat-list`. Nothing builds one of these
names by concatenation — the four template literals that carry one keep the
class part whole.

Snapshot diff: `frontend_css_references` is the ONLY section that moved,
1368 -> 1368, exactly -22 / +22, every pair a prefix swap.

**In a real browser** (1680x1050, both themes, seeded disposable stack on
8507/3507 with its own database and placeholder provider keys — the Telegram
401 in that log is the proof no real bot was touched): the Ask AI console
opens and renders identically in both themes, and every renamed selector
resolves to its authored value rather than a default. The decisive check is an
injected probe element per class read against an unclassed control in the live
document:

```
31 of 32 fleet-assistant-* names match a real rule
   the one that does not is `launcher-btn`, which had no rule before either
 0 of 32 fleet-sage-*      names match any rule   <- nothing left orphaned
```

Not verified visually, and named rather than glossed:
`fleet-assistant-console-action` / `-actions` and the history-row family only
render after a real LLM turn has produced a conversation, and this stack's
provider keys are deliberately blocked. They are covered by the token census,
the declared/used relation and the probe — not by a screenshot.

**The checked-in baseline is PATCHED, not regenerated.** The CSS surface
genuinely changes so the baseline must follow, but regenerating the whole file
would silently rewrite sections this worktree cannot reproduce byte-for-byte
(`frontend_build_routes` depends on which bundler produced `.next`;
`database_schema` on which local database was reachable). The patcher applies
the same one-token substitution to `frontend_css_references` and REFUSES to
write if any other baseline line would change: 44 lines changed, 0 unrelated.
The patched baseline then equals a freshly collected snapshot exactly.

### 8e — a comment that named a cause which had stopped being the cause

`unified_governance_gate.evaluate_action_policy`'s Step 3 imports
`agent_computer_approval_decision_service` inside a `try`, and its
`except Exception:` explained itself as "capability not recognized by the
agent-computer risk classifier". That module was DELETED in `0820a732`
("Remove approval system"), so the import — the first statement in the try —
always raises `ModuleNotFoundError` (measured, not reasoned). The handler is
not a fallback: it is the ONLY path, on every call, for every capability.

**Reported, deliberately not fixed:** the handler returns on both branches, so
lines 268-331 of that function (64 lines, 6 statements — the post-classifier
registry check and the final `ActionPolicyDecision`, everything that reads
`approval_decision`) are UNREACHABLE. Deleting the try/except is a decision
about whether the risk-classifier seam is coming back, so the code is
untouched and the comment now says so out loud.

### chrome.css CANNOT be deleted, and the premise that it can is wrong by ~10x

Measured before touching anything:

| | |
|---|---:|
| `frontend/lib/ui/chrome.css` | **26,533 lines** |
| distinct class selectors declared | **2,250** |
| of those, LIVE (a consumer in `.tsx`/`.ts`, or another `.css`) | **348** |
| CSS custom properties it DEFINES | 196 |
| of those, read by another file (`--app-accent`, `--app-bg-page`, `--app-font-*`) | **69** |
| `sage-*` classes declared | 433 |
| of those, live as a CSS class | **0** |

It is imported globally by `frontend/app/layout.tsx`, it carries `body {}` and
`textarea {}` rules, `theme-tokens.css` and `landing.css` both name it in their
own comments as the file they consume tokens from, and two drift tests read it
by path — `no-focus-ring-drift.test.ts` asserts `--app-shadow-focus` resolves
to `none` there, and `accent-restraint.test.ts` derives its accent-alias graph
from it. The 348 live classes are the `app-auth-*` family: the login and signup
surface, i.e. the product's front door.

The three `sage-*` classes an automated sweep reports as live are all false
positives: `sage-agent-computer` matches only the HTTP path
`/api/connections/sage-agent-computer` in `workstation-client.ts`, and
`sage-unified-card` / `sage-unified-section` match only comments in
`accent-restraint.test.ts` and an e2e spec. **All 433 are dead as CSS.**

The provable, narrow alternative: **534 rules occupying 3,418 lines (13% of the
file)** have selectors composed only of dead `sage-*` classes. That is the
deletion CLAUDE.md's accent-restraint note is actually asking for ("do not
widen the scan there without deleting the dead CSS first"). Not done here —
this is a founder decision, and the instruction as given would have taken the
login screen down.

### docs/PLATFORM-MAP.md: deletable on the standing rule, but it has ~40 referrers

4,819 lines, header pinned to commit `01c6081ce` and dated 2026-07-13, with its
own graph statistics marked "predates the changes in this refresh". It names
**122 occurrences of 23 modules that no longer exist**, plus five that never
existed under those names at all (`sage_service.py`, `sage_events_repository.py`,
`sage_reporting.py`, `sage_accounting_service.py`, `sage_bridge_service.py`).
It also carries its own 2026-07-23 note saying the Sage concept is dead.

CLAUDE.md's standing rule says snapshot/audit documents are not kept. The cost
of applying it here is that roughly 40 "see docs/PLATFORM-MAP.md" pointers go
dangling, and they are not all in docs — `agent_turn_runtime_service.py`,
`skills_service.py`, `personal_channels_service.py`, `workspace_context.py`,
`wechat_official_service.py`, `deployed_agent_service.py`,
`test_module_reachability.py`, a gateway `.ts` file, `FleetAgentDetail.tsx`,
`ConnectorPicker.tsx` and 18 brand-asset SVGs all cite it as the explanation
for why some code is the way it is. Deleting the file is one line; deleting it
honestly means stripping those clauses in the same commit.

### The single-process hang DID NOT REPRODUCE, and the note above is now a claim with an expiry date

The section "The suite has an order-dependent hang, and it is not new" (above)
records `test_mcp_oauth_provider.py::test_resolve_workspace_read_only_scope_blocks_writes`
blocking forever ~5,341 tests into a single 10,333-test run. **Measured again
on 2026-08-28 at `4473dbd0`, twice, it does not happen.**

```
PROBE 1  files 1..417 of the sorted list (up to and including the named file)
         5,348 tests   completed in 348s   no stall
PROBE 2  ALL 849 files, ONE process -- the exact described condition
         10,333 tests  completed in 723s   495 failed / 9,663 passed / 174 skipped
         495 pytest failure LINES de-duplicate to 490 distinct node ids
```

Both runs were made with `-o faulthandler_timeout=300` armed. pytest's built-in
faulthandler plugin dumps every thread's stack when one test blocks that long —
that is the only thing that turns "it hangs" into "it is waiting on X", and
`pytest-timeout` is not installed here so it is also the only option. **It never
fired.** `test_mcp_oauth_provider.py` was file 417 of 849 in both runs and
appears nowhere in either failure list, i.e. it passed.

**Reading the test itself says why a hang there is surprising.** With the
contextvar set, `mcp_server._resolve_workspace(ctx=None)` returns from its FIRST
branch, before any `await`:

```
access_token = get_access_token()        <- a contextvar read, set by the test
workspace_id = "ws-scoped"               <- non-empty, so ...
return {...}                             <- ... it returns here. no await at all.
```

So the coroutine cannot block. Anything that stalls has to be the surrounding
machinery — `import mcp_server` (a 107KB module that builds the MCP server and
pulls the FastAPI app in), `asyncio.run()`'s loop setup/teardown, or a lock or
non-daemon thread left behind by an earlier test in the same process. That is
also why it is order-dependent rather than a property of the test.

**Order-dependence is real but now tiny, and this is the number worth keeping:**

```
one process   490 distinct failing node ids
8 chunks      489
delta          1, in one direction only
   ONLY in one process:
     test_assistant_channel_certification_core.py::DiscordCertification::test_discord_setup_readiness
   ONLY when chunked:   (none)
```

**What a fix would need, if it comes back.** Do not start by editing the test.
Reproduce with `faulthandler_timeout` armed and read the dump — it names the
frame, and until something has, every explanation is a guess. If it names
`import mcp_server`, the suspect is this repository's own documented
`sys.modules["server"]` stand-in leak (an earlier test whose cleanup block never
ran). If it names `asyncio.run`, the suspect is loop teardown waiting on a
non-daemon thread or an un-`unref`'d timer from an earlier test, which is the
same family as the gateway's `ws-client-event-seq-race` hang recorded in
CLAUDE.md. If it names a socket read, the egress guard in `conftest.py` is the
place to look, because a blocked connect with no timeout looks exactly like this.

**What can be said today, plainly: the suite is usable in one process at this
commit — 12 minutes, exit 1 on real failures, no stall.** The chunked
measurement in this document is still the right instrument for a before/after
comparison (it isolates module-level state), but it is no longer a workaround
for an unusable single-process run.

Not ruled out, and stated rather than glossed: a hang that depends on machine
load, on a concurrently running stack, or on state under `~/.empyralis` that
differed on the day it was seen. Two clean runs are evidence, not proof.
