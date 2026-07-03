# Rollback Instructions

## Current State (2026-07-03)

- **Main line:** `refactor-fable` → `main` (the real platform: `server_modules/`, `empyralis-gateway/`, etc.)
- **Tag:** `real-main-1` points at the current main HEAD
- **Intern's V2/legacy line preserved at:** `before-fable-refactor` (remote branch on origin)

### Remote branch layout

| Branch | HEAD | What |
|--------|------|------|
| `main` | `01c429ec0` | Real platform — refactor-fable line (Phases A-Q) |
| `before-fable-refactor` | `e3a0952b6` | Intern's V2 work in `server/` and `legacy/` (preserved, not active) |

---

## fable-merge-1

**Tag:** `fable-merge-1`
**Date:** 2026-07-03
**Previous main HEAD (before refactor-fable merge):** `e6f360a1f4e6d770a8ae7b20c8fbacf38c49245b`

### To roll back to pre-fable state

```sh
git checkout main
git reset --hard e6f360a1f4e6d770a8ae7b20c8fbacf38c49245b
git tag -d fable-merge-1   # optional: remove the merge tag
```

---

## real-main-1

**Tag:** `real-main-1`
**Date:** 2026-07-03
**HEAD:** `01c429ec0` (Phase Q: live verification)

### To roll back to the intern line

```sh
git checkout main
git reset --hard origin/before-fable-refactor
```

This restores the intern's V2 work from `server/` and `legacy/` in case any isolated commit is worth cherry-picking later.

### To cherry-pick a specific intern commit

```sh
git log origin/before-fable-refactor --oneline   # find the commit you want
git cherry-pick <hash>
```

---

## Flag defaults introduced by refactor-fable

| Flag | Default | Purpose |
|------|---------|---------|
| `EMPYRALIS_TELEGRAM_PATH` | `gateway` | Telegram path: `gateway` \| `csm` \| `both` |
| `EMPYRALIS_REQUIRE_AGENT_ID` | `false` | Block tool dispatch without agent_id when `true` |

## What refactor-fable contains

- **Phase A:** Approval system removal
- **Phase B:** Platform voice — pigeon theory
- **Phase C:** Import cycle fixes
- **Phase D:** Honest test harness
- **Phase E-F:** Workspace isolation, per-agent credentials, channel bindings, tool gating
- **Phase G:** Router wired, agent_id threaded, workers scoped
- **Phase K:** Gateway ledger — channel + hardware with hard redaction
- **Phase L:** Hierarchy — operator role, 5 fleet tools, sub-agent gate, per-agent AI binding (4 modes)
- **Phase M:** Telegram single-path gating, fleet-specialist seed, Sage operator bootstrap
- **Phase N:** Stage 5 memory — MEMORY.md as sole context, 3 agent memory tools
- **Phase P:** Triage layers — scope + identity gates before reasoning, opt-in per agent
- **Phase Q:** Live verification — build, boot, migrations, ledger sanity

## Test baseline

- 61 new tests pass (test_ledger_audit.py + test_hierarchy.py + test_triage.py)
- 29 pre-existing failures from Rust runtime kernel (not caused by this line)
