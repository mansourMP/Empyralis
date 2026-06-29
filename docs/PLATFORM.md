# Empyralis

A managed, reliable, safe version of OpenClaw. Consumers get their own agent living in their channels. Cloud-first, hardware as the upgrade path.

**Stack:** Python (FastAPI) + TypeScript (Next.js 16) + Rust (policy kernel + supervisor)
**License:** Proprietary
**Repo:** github.com/mansourMP/Empyralis
**See also:** `OpenClaw.md` (competitor forensic audit)

---

## What's Alive (shippable)

| Surface | Status | Notes |
|---|---|---|
| **Sage** (main agent chat) | ✅ Live | Web UI at `frontend/`, API via `server.py` |
| **Telegram hosted bot** | ✅ Live | One bot token, all customers DM it. Real use 6/23. |
| **Discord DM** | ✅ Wired | 11 tests green |
| **VPS shell/file** | ✅ Live | `scripts/empyralis_self_hosted_command_worker.py` |
| **Cloud Computer** (droplet) | ✅ Proven | Browser control on DO, 150 tests green |
| **Memory** (LanceDB) | ✅ Live | `server_modules/memory_service.py`, `unified_memory_service.py` |
| **Governance** | ✅ Live | Kill switch, safe mode, approvals, audit. Internalized — no UX. |
| **Credits** | ✅ Live | 10k free DeepSeek on signup. Single bucket, no fallback. |
| **Rust kernel** | ✅ Live | `empyralis-runtime-kernel/` — 53-command policy decision engine |
| **Supervisor** | ✅ Live (uncompiled) | `empyralis-supervisor/` — desktop automation daemon |
| **Gateway** | ✅ Live | `empyralis-gateway/` — WSS pairing, supervisor bridge |

## What's Frozen

| Surface | Why |
|---|---|
| **Mobile** | Expo app deleted. Revisit after web+cloud ship. |
| **Marketplace/Discover** | Deleted. Not needed for v1. |
| **Apps/Mini-apps** | Deleted. MCP bridge contracts preserved but frozen. |
| **WhatsApp** (all forms) | Meta banned third-party AI assistants Jan 15 2026. Dead channel. |
| **Tauri desktop tray** | Deleted. Secondary to web. |
| **Cloud Session Manager** | Unused. Deleted. |
| **MCP** | Not priority right now. |

## What's Broken / P0

- `agent_channel_router.route_inbound_channel_message` is UNDEFINED — crashes Slack, Discord-guild, WhatsApp-Business paths. Fix or delete routes.
- Some inbound Telegram messages get silent drops — every message must get a response.
- Mac gateway supervisor binary never compiled — needs a real Mac run.
- DeepSeek-specific hacks in the generation loop (provider workaround baked into core).
- Model state (approval memory) is in-memory — lost on server restart.

---

## Architecture Map

```
empyralis/
├── server.py              # Composition root (FastAPI). Don't put product logic here.
├── mcp_server.py          # MCP server for vision-monitor
├── main.py                # Standalone entry (rarely used)
│
├── server_modules/        # ~420 Python service modules — THE BACKEND
│   ├── agent_turn.py      # Canonical AgentTurnRequest — all channels converge here
│   ├── direct_chat_*.py   # ~20 files for web chat path (over-engineered — aim for 3)
│   ├── agent/             # Agent runtime logic
│   ├── connectors/        # Channel connectors (Telegram, Discord, Slack, etc.)
│   ├── runtime_*.py       # Run lifecycle, execution, local queue
│   ├── memory_service.py  # Memory/embedding
│   ├── secrets_broker.py  # Vault, provider secrets
│   ├── tool_broker.py     # Tool access gateway
│   └── tests/             # ~270 test files
│
├── frontend/              # Next.js 16 + React 19 — THE WEB UI
│   ├── app/(account)/w/[workspaceId]/  # Workspace shell
│   ├── app/auth/ login/ signup/        # Auth flows
│   ├── lib/workspace/     # Chat, shell, channels UI
│   ├── lib/ui/            # Design system, tokens
│   └── shared/            # nav-manifest.ts, design tokens (REAL shared/)
│
├── empyralis-gateway/     # TypeScript — local machine → cloud bridge
│   └── src/               # WSS pairing, supervisor forwarding, channel bridges
│
├── empyralis-supervisor/  # Rust — local desktop daemon (127.0.0.1:7788)
│                          # Shell, FS, screenshot, OCR, clipboard, AppleScript, speech
│
├── empyralis-runtime-kernel/ # Rust — JSON-in/JSON-out policy decision engine
│
├── scripts/               # ~49 scripts — workers, tests, migrations, dev tools
│   ├── empyralis_self_hosted_command_worker.py  # VPS worker
│   └── orion_local_worker_runtime.py            # Local worker
│
├── config/                # Environment/config
├── deploy/                # Worker service files, recovery tests
├── skills/                # 9 markdown skill templates for the LLM
├── references/            # Study projects, UI patterns
└── OpenClaw.md            # Competitor forensic audit
```

---

## Key Contracts (don't break these)

1. **One turn engine** — all shells converge on `agent_turn.py`. No parallel turn contracts.
2. **Brokered everything** — tools → `tool_broker`, secrets → `secrets_broker`, runtime → policy-bound.
3. **Shell-first tool model** — agent uses shell + browser on a computer. Minimal bespoke tools. Only keep: memory, channel plumbing, OAuth connectors.
4. **Sage ≠ Studio** — Sage is the personal AI. Studio agents are independent workers. They don't share memory or context. Only shared layer: auth, billing, channels.
5. **No fallback ever** — if credits are zero, hard stop. No fallback to cheaper model.
6. **Internalized governance** — no approval UX for consumers. Agent internalizes rules.

---

## Current Priorities

1. **Ship what works** — Telegram hosted bot + web chat + Discord DM + memory + governance
2. **Fix silent drops** — every inbound message must get a response
3. **Commit C+D engine hardening** — done but uncommitted (Discord/Slack error parity, security lockdown, skills honesty)
4. **Live E2E proof** — the live run that never happened (needs .env, DeepSeek key, Postgres, channel tokens)
5. **Simplify** — merge 3 execution paths into one, one command registry, one tool list

---

## Agent Rules

- Read this file first. Every session.
- `OpenClaw.md` is the competitor reference — read it when changing architecture.
- Don't resurrect deleted features (mobile, marketplace, apps, WhatsApp).
- Don't add new tools — shell + browser is the universal tool.
- Don't introduce parallel turn contracts — everything converges on `agent_turn.py`.
- When in doubt: ship, don't build.
