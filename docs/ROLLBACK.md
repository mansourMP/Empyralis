# Rollback Instructions

## fable-merge-1

**Tag:** `fable-merge-1`
**Date:** 2026-07-03
**Previous main HEAD:** `e6f360a1f4e6d770a8ae7b20c8fbacf38c49245b`

### To roll back

```sh
git checkout main
git reset --hard e6f360a1f4e6d770a8ae7b20c8fbacf38c49245b
git tag -d fable-merge-1   # optional: remove the merge tag
```

This reverts main to the state before the Phase K/L/M/N merge.

### Flag defaults introduced by this merge

| Flag | Default | Purpose |
|------|---------|---------|
| `EMPYRALIS_TELEGRAM_PATH` | `gateway` | Telegram path: `gateway` \| `csm` \| `both` |
| `EMPYRALIS_REQUIRE_AGENT_ID` | `false` | Block tool dispatch without agent_id when `true` |

### What this merge contains

- **Phase K:** Gateway outbound + hardware ledger (redacted), agent_id in capability tokens
- **Phase L:** Hierarchy — operator role, 5 fleet tools, sub-agent gate, per-agent AI binding (4 modes: platform_credits/byok_api/cli_subscription/local)
- **Phase M:** Telegram single-path gating, fleet-specialist seed, Sage operator bootstrap
- **Phase N:** Stage 5 memory — MEMORY.md as sole context injection, 3 agent memory tools (memory_read/write/list)

### Test baseline

- 46 new tests pass (test_ledger_audit.py + test_hierarchy.py)
- ~1156 pre-existing test failures from Rust runtime kernel (not caused by this merge)
