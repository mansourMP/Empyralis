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
| Persisted/protocol/high-signal literals | 3,500 |
| Frontend build-route strings | 22 |
| Frontend CSS class references | 1,347 |

The database snapshot was taken from the local Postgres catalog using the same
information-schema query the tool runs against the configured runtime database.
No database writes were performed.

## Determinism check

Command:

    venv/bin/python scripts/rename_contract_snapshot.py \
      --output /tmp/rename-contract-repeat.json \
      --compare docs/rename-contract-baseline.json

Result: exit 0. The repeat output was byte-identical to the checked-in
baseline. The collector sorts every set-like surface and emits canonical JSON.

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

## Step 0 boundary

This commit adds only the guard, its checked-in baseline, and this evidence.
Steps 1–2 have not started. In particular, no HTTP route string, persisted
value, protocol/tool name, human confirmation phrase, environment variable,
database table/column name, migration, historical comment, or archived record
was renamed.
