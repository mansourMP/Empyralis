# Current Tasks

Status: 2026-06-29

## 1. Ship what's proven

- Telegram hosted bot + web chat + Discord DM
- Memory + governance + credits
- **Gate:** live E2E proof (needs .env, DeepSeek key, Postgres, channel tokens)

## 2. Fix silent drops

- Every inbound Telegram message must get SOME response
- Add typing indicator
- Clean formatting

## 3. Commit C+D engine hardening

- Discord/Slack error parity ✅ (done, uncommitted)
- Security lockdown ✅ (done, uncommitted)
- Skills honesty pass ✅ (done, uncommitted)
- CI auto-run ✅ (done, uncommitted)

## 4. Simplify execution paths

- Merge 3 LLM execution paths into one `agent_loop.py`
- One command registry (replace 3 if/elif chains)
- One tool list

## 5. Linear setup

- Create Linear project for Empyralis
- Migrate these tasks to Linear
