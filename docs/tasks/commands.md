# Slash Commands — Audit & Fix List

**Created:** 2026-06-30
**Status:** pending

---

## Scope

27 slash commands across 4 dispatch surfaces. 22 of 27 return raw hardcoded strings instead of PlatformEvent. 3 leak internals. 1 has "I"/"you" in user-visible text.

---

## Commands that already use PlatformEvent (5)

| Command | Event constant |
|---------|---------------|
| `/compact` | `SAGE_COMPACTED`, `SAGE_COMPACT_NOT_NEEDED` |
| `/new` | `SAGE_NEW_SESSION` |
| `/main` | `SAGE_MAIN_RETURN` |
| `/approve` | `SAGE_NO_PENDING_APPROVALS` |
| `/deny` | `SAGE_NO_PENDING_APPROVALS` |

## Commands that need PlatformEvent migration (22)

`/agents` `/bash` `/clear` `/commands` `/config` `/debug` `/export` `/forget` `/help` `/mcp` `/memory` `/model` `/plugins` `/skills` `/status` `/stop` `/tasks` `/thinking` `/tools` `/tts` `/usage` `/whoami`

Plus 5 personal-channel thread commands from `personal_channel_thread_command_service.py`: `/new` `/threads` `/use` `/status` `/help`

---

## Leaks to fix (3)

### 1. `/bash` — raw exception exposed

- **File:** `server_modules/command_registry.py:1198`
- **Current:** `f"Shell command failed: {exc}"`
- **Fix:** PlatformEvent with generic `"Shell command could not be executed right now."`. Log the real exception, never send it to the user.

### 2. `/export` — internal API path exposed

- **File:** `server_modules/direct_chat_response_service.py:103`
- **Current:** `"GET /api/diagnostics/sessions/{session_id}/export"`
- **Fix:** `"Session transcript is being compiled."` — drop the API route.

### 3. `/mcp` — endpoint URLs exposed

- **File:** `server_modules/command_registry.py:941`
- **Current:** `f"endpoint: {endpoint}"`
- **Fix:** Drop the endpoint line from user output, or mask it. Owner-gated but still shouldn't leak URLs to chat.

---

## "I"/"my" to fix (1)

### `/memory` — description has "I" and "you"

- **File:** `server_modules/command_registry.py:417`
- **Current:** `description="Show what I remember about you"`
- **Fix:** `"Show stored memory facts"`

---

## Dispatch surfaces (4)

| Surface | File | Notes |
|---------|------|-------|
| Unified registry | `command_registry.py` | 22 commands, canonical store, `dispatch()` |
| Legacy web | `direct_chat_response_service.py` | Hardcoded if/elif for status/memory/forget/model/clear/help/export/thinking |
| Legacy channel | `sage_command_dispatcher.py` | Delegates to registry for dispatch, internal handlers for compact/new/main/approve/deny/memory |
| Personal thread | `personal_channel_thread_command_service.py` | Regex parser for new/threads/use/status/help in personal messaging lanes |

---

## Fix plan

1. Create PlatformEvent constants for every command response currently using raw strings
2. Rewrite all 22 command handlers to return `event.channel_text`
3. Patch the 3 leaks
4. Fix the 1 "I"/"you" description
5. Merge legacy web handler (`direct_chat_response_service.py`) into the unified registry — no reason for two dispatch paths
6. Register `/approve`, `/deny`, `/main`, `/clear`, `/forget`, `/export` in the unified registry
