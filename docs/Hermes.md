# Hermes Agent — Comprehensive Architecture Reference

**Researched:** 2026-07-05
**Installed version:** v0.15.2 (latest is v0.17.0 as of June 19, 2026)
**Developer:** Nous Research / AtomicBot-ai
**License:** MIT
**GitHub:** <https://github.com/NousResearch/hermes-agent> (~19.4k stars)
**Official docs:** <https://hermes-agent.nousresearch.com/docs>

> **Purpose of this file:** Cold, static reference for Empyralis architecture
> decisions. Hermes is the most architecturally mature open-source agent
> platform as of mid-2026. This document captures EVERYTHING — isolation,
> delegation, MCP, skills, memory, gateway, cron, security, plugins — at
> implementation detail level. No opinions, just what's built and how.

---

## 1. Package Architecture

```
hermes-agent (pip: hermes-agent)
├── hermes_cli/           # CLI, agent loop, gateway, profiles, tools config
│   ├── main.py           # Core CLI + agent invocation (82KB+)
│   ├── profiles.py       # Profile creation, switching, isolation
│   ├── gateway.py        # Multi-platform messaging gateway
│   ├── config.py         # Configuration, delegation settings
│   ├── hooks.py          # Plugin hooks (pre_tool_call, pre_llm_call, subagent_stop, etc.)
│   ├── models.py         # Provider model definitions
│   ├── cron.py           # Cron scheduler management
│   ├── curator.py        # Autonomous skill curator
│   ├── plugins.py        # Plugin system
│   ├── skills_config.py  # Skills configuration
│   ├── tools_config.py   # Tool configuration
│   ├── mcp_catalog.py    # MCP server catalog
│   ├── mcp_config.py     # MCP configuration
│   ├── mcp_picker.py     # MCP server picker UI
│   ├── memory_setup.py   # Memory provider setup
│   ├── security_audit.py # Security auditing
│   ├── kanban*.py        # Kanban task management (kanban_db, kanban_decompose, etc.)
│   ├── codex*.py         # Codex integration
│   ├── web_server.py     # OpenAI-compatible API server
│   ├── web_dist/         # Web UI distribution
│   └── tui_dist/         # Terminal UI (Ink/React)
├── hermes_state.py       # SQLite state store + WAL + FTS5
├── hermes_bootstrap.py   # Bootstrap/install
├── hermes_time.py        # Time utilities
├── hermes_logging.py     # Logging
└── hermes_constants.py   # Constants
```

**Key observation:** The agent loop, tool dispatch, delegation, and sub-agent
spawning are all in `hermes_cli/main.py` — a single 82KB+ file. Hermes is NOT
a microservices architecture. It's a monolithic Python CLI with plugin hooks.

---

## 2. The Agent Loop (ReAct Pattern)

### Core flow

```
1. Build system prompt: SOUL.md → memory snapshot → skills catalog
2. Compress context if needed (token budget management)
3. Interruptible LLM call (streaming)
4. Execute tool calls:
   - Independent calls → concurrent via ThreadPoolExecutor (max 8 workers)
   - Interactive tools (clarify) → sequential
   - Path-scoped (read_file, write_file, patch) → concurrent only on independent paths
5. Repeat until done OR turn cap reached (default 90 turns)
```

### Turn budget

- **Default max turns:** 90 (shared between parent and all sub-agents)
- **Sub-agent default iterations:** 50
- Context compression kicks in before token limits are hit

### Concurrent execution

```python
# Independent tool calls run in parallel
ThreadPoolExecutor(max_workers=8)

# Sequential enforcement
# clarify() forces sequential — blocks concurrent batch

# Path-scoped concurrency
# read_file, write_file, patch — concurrent ONLY if independent file paths
```

---

## 3. Profiles — Multi-Instance Isolation

This is the architecture that answers "Agent 1 for mom, Agent 2 for dad."

### Directory structure

```
~/.hermes/
├── config.yaml          # Default profile config
├── .env                 # Default profile secrets
├── MEMORY.md            # Default profile memory
├── USER.md              # Default profile user model
├── SOUL.md              # Default profile identity
├── state.db             # Default profile state (SQLite)
├── sessions/            # Default profile sessions
├── skills/              # Default profile skills
├── cron/                # Default profile cron jobs
├── active_profile       # Currently active profile name
├── profiles/            # Named profile directory
│   ├── mom/
│   │   ├── config.yaml
│   │   ├── .env         # Mom's Telegram bot token
│   │   ├── MEMORY.md
│   │   ├── USER.md
│   │   ├── SOUL.md
│   │   ├── state.db
│   │   ├── sessions/
│   │   └── skills/
│   ├── dad/
│   │   ├── config.yaml
│   │   ├── .env         # Dad's Telegram bot token
│   │   └── ... (same structure)
│   └── business/
│       └── ...
```

### What isolation means per profile

| Resource | Isolated? | Detail |
|----------|-----------|--------|
| `HERMES_HOME` | ✅ Fully separate | Each profile has its own directory |
| `config.yaml` | ✅ Separate | Different models, providers, gateways, terminal backends per profile |
| `.env` | ✅ Separate | Different API keys, bot tokens per profile |
| `MEMORY.md` | ✅ Separate | Different memories per profile |
| `USER.md` | ✅ Separate | Different user models per profile |
| `SOUL.md` | ✅ Separate | Different identities per profile |
| `state.db` | ✅ Separate | Different session history per profile |
| `skills/` | ✅ Separate | Different learned skills per profile |
| `cron/` | ✅ Separate | Different scheduled jobs per profile |
| Gateway | ✅ Separate | Each profile can bind different platform accounts |
| Token lock | ✅ Enforced | Two profiles cannot share the same bot token |
| Subprocess env | ✅ Isolated | `HERMES_HOME` injected into child processes |

### Profile commands

```bash
hermes profile create mom          # Create new profile
hermes profile list                # List all profiles
hermes -p mom                      # Switch to mom profile
hermes -p dad                      # Switch to dad profile
hermes profile delete mom          # Delete profile
hermes profile export mom          # Export profile (backup)
hermes profile import mom.tar.gz   # Import profile

# Clone semantics
hermes profile create mom --clone dad         # Copy identity/config only
hermes profile create mom --clone-all dad     # Deep copy everything
```

### Profile resolution priority

1. Explicit `--profile <name>` / `-p <name>` flag (highest)
2. Sticky active profile from `~/.hermes/active_profile`
3. Default fallback to `~/.hermes/`

### Wrapper scripts

Auto-generated at `~/.local/bin/<profile-name>`:
```bash
#!/bin/bash
exec hermes -p <name> "$@"
```

So `mom` in terminal → runs hermes with mom's profile directly.

### Topic-to-Profile Routing (in development, PR #20096)

Routes Telegram forum topics to different profiles using ONE bot token:

```yaml
telegram:
  topic_profiles:
    - match:
        chat_id: "-1001234567890"
        thread_id: "1939"
      profile: "coding-assistant"
    - match:
        chat_id: "-1001234567890"
        thread_id: "42"
      profile: "research-agent"
```

- Hierarchical matching with specificity scoring
- Full profile isolation per topic
- Single Telegram bot token, multiple specialized agents

---

## 4. Sub-Agent Delegation System

### Core concept

Hermes spawns **isolated child agents** for parallel work. Each sub-agent gets:
- Own conversation context (no parent context inheritance)
- Own terminal session and working directory
- Restricted toolset (dangerous tools stripped)
- Only the **final summary** returns to parent

### The `delegate_task` tool

```python
# Single sub-agent
delegate_task(
    goal="Review src/auth/ for security issues",
    context="Project at /home/user/webapp. Python 3.11, Flask backend.",
    toolsets=["terminal", "file"]
)

# Parallel batch (default max 3 concurrent)
delegate_task(tasks=[
    {"goal": "Research WebAssembly runtime options", "context": "...", "toolsets": ["web"]},
    {"goal": "Research RISC-V emulation", "context": "...", "toolsets": ["web"]},
    {"goal": "Research quantum computing simulators", "context": "...", "toolsets": ["web"]}
])
```

### Sub-agent constraints

| Parameter | Default | Range | Meaning |
|-----------|---------|-------|---------|
| `max_concurrent_children` | 3 | ≥1 | Parallel batch size per `delegate_task` call |
| `max_spawn_depth` | 1 | 1–3 | Delegation depth (1 = parent→child only) |
| Default iterations | 50 | — | Per-sub-agent turn budget |

### Leaf vs Orchestrator sub-agents

**Leaf (default):** Cannot call `delegate_task`, `clarify`, `memory`, `send_message`, or `execute_code`. Can only use the toolsets explicitly granted.

**Orchestrator (`role="orchestrator"`):** Retains `delegate_task` for further delegation. Can spawn its own sub-agents to depth limit.

### Toolset selection

| Toolsets | Includes | Best for |
|----------|----------|----------|
| `["web"]` | web_search + web_extract only | Research |
| `["terminal", "file"]` | Shell + file operations | Code work |
| `["terminal", "file", "web"]` | Everything except messaging | Full-stack |
| `["file"]` | Read-only file access | Read-only analysis |

### Async sub-agents (shipped June 2026)

Before June 2026, `delegate_task` was synchronous — parent froze until children completed. The async toolset eliminated this:

| Tool | Purpose |
|------|---------|
| `delegate_task_async` | Spawn background agent, return `task_id` immediately |
| `check_task` | Non-blocking status poll with current state + recent output |
| `steer_task` | Inject new instruction into running sub-agent mid-flight |
| `collect_task` | Block until completion, return full result |
| `cancel_task` | Stop a running task |
| `list_tasks` | List all active async tasks in the session |

**TUI overlay:** `/agents` command shows live tree view of all running and completed sub-agents.

**Key limitation:** Async sub-agents are scoped to session lifetime. They don't persist across session boundaries (cross-turn persistence tracked in issue #4949).

### When to use what

| Approach | Best for |
|----------|----------|
| `delegate_task` | Reasoning-heavy subtasks, parallel research, code review, multi-file refactoring |
| Direct tool call | Single operations |
| `execute_code` | Mechanical multi-step work with logic between steps (zero context cost via RPC) |
| `cronjob` / `terminal(background=True)` | Durable long-running work that must outlive the current turn |

### Delegation patterns (6 official patterns)

1. **Parallel Research** — Research N topics simultaneously, synthesize at parent
2. **Code Review** — Delegate security/code review to fresh-context sub-agent
3. **Compare Alternatives** — Evaluate multiple approaches in parallel
4. **Multi-File Refactoring** — Split large refactoring across parallel sub-agents
5. **Gather Then Analyze** — `execute_code` for mechanical data gathering, then `delegate_task` for reasoning
6. **Kanban Swarm** — Kanban board → decompose → parallel worker agents (kanban_swarm.py)

### Inter-agent communication levels (roadmap)

| Level | Mechanism | Status |
|-------|-----------|--------|
| L0: Isolated | No sharing, parent relays | **Current** |
| L1: Result passing | Upstream → downstream context injection | Phase 1 |
| L2: Shared scratchpad | Read/write shared KV store | Phase 3 |
| L3: Live dialogue | Turn-based agent-to-agent conversation | Phase 4 |

### Failure recovery (3-level escalation)

```
Retry → Replan → Decompose Further
```

- Checkpointing after each tool call
- Stuck detection: if no tool calls for N seconds → intervene
- Health monitoring with inception prompting

---

## 5. MCP Integration

### Client mode

Hermes is a **native MCP client** with full protocol support:

| Feature | Detail |
|---------|--------|
| **stdio transport** | Local MCP servers (`npx`, `uvx`, any command) |
| **HTTP transport** | Remote MCP servers via URL |
| **OAuth 2.1 PKCE** | Remote MCP authentication flow |
| **Selective tool loading** | Per-server tool filters with utility policies |
| **Sampling** | Server-initiated LLM requests |
| **Auto-reload** | Detects `mcp_servers` config changes |

### Configuration

```yaml
# Local stdio MCP server
mcp_servers:
  filesystem:
    command: npx
    args:
      - "-y"
      - "@modelcontextprotocol/server-filesystem"
      - "/home/user/projects"

# Remote HTTP MCP server
  docs:
    url: "https://mcp.example.com/mcp"
    headers:
      Authorization: "Bearer ${DOCS_API_KEY}"
    # Optional OAuth
    oauth:
      authorization_url: "https://mcp.example.com/oauth/authorize"
      token_url: "https://mcp.example.com/oauth/token"
      client_id: "${MCP_CLIENT_ID}"
```

### Server mode (v0.6.0+)

Expose Hermes sessions to MCP-compatible clients:

```bash
hermes mcp serve              # Run as MCP server over stdio
```

Clients (Claude Desktop, Cursor, VS Code, Zed, JetBrains) can connect to Hermes as an MCP server and interact with its sessions.

### Tool naming

MCP tools are exposed as `mcp_<server>_<tool_name>` and are configurable interactively:

```bash
hermes tools                   # Interactive tool & MCP configuration
hermes mcp install             # Install & configure MCP servers
```

---

## 6. Skills System — The Closed Learning Loop

### Architecture

Skills are **procedural memory** — Markdown playbooks (`SKILL.md` + YAML frontmatter) that capture *how* to do things, not *what* facts to remember.

### Lifecycle

```
Task Completion (5+ tool calls)
  → Pattern Extraction (agent analyzes steps, identifies reusable patterns)
  → Skill Creation (writes SKILL.md via skill_manage)
  → Skill Refinement (patched when outdated, incomplete, or wrong during use)
  → Curator Pruning (background process reviews agent-authored skills)
```

### Skill anatomy

```markdown
---
name: k8s-pod-debug
description: Activate for crashing pods, CrashLoopBackOff, container failures.
version: 1.2.0
author: agent
platforms: [linux, macos]
---
## Procedure
1. Get pod status → check events → pull logs
2. Look for OOMKilled, ImagePullBackOff, config errors

## Pitfalls
- Forgetting --previous flag on restarted containers
- Not checking node capacity for scheduling failures

## Verification
- Pod stays Running with 0 restarts for 5+ minutes
- Application health check passes
```

### Progressive disclosure

| Level | Content | Token cost |
|-------|---------|------------|
| L0 | Catalog descriptions only | ~3k tokens |
| L1 | Full skill loaded when matched by context | On-demand |
| L2 | Optional references and drill-down | Explicit request |

### Skills ecosystem

- **166 tracked skills** (87 bundled + 79 optional) across 26+ categories
- Compatible with **agentskills.io** open standard
- **Skills Hub** integrates with ClawHub and skills.sh
- Per-platform enable/disable, conditional activation, prerequisite validation
- Version tracking: `hermes curator` manages stale/archived skills

### Autonomous curator

Background process that prunes agent-authored skills (never bundled/Hub skills):

| Trigger | Action |
|---------|--------|
| 7+ days since last pass AND 2+ hours idle | Run curator review |
| 30 days unused | Mark stale (automatic) |
| 90 days unused | Archive (automatic) |
| LLM review | Up to 8 iterations per skill |
| Snapshot | Before each pass |
| `hermes curator pin` | Protect favorites from pruning |

---

## 7. Memory Architecture

### Three-tier system

| Tier | Implementation | Capacity | Behavior |
|------|---------------|----------|----------|
| **Tier 1: Prompt memory** | `MEMORY.md` (~2,200 chars) + `USER.md` (~1,375 chars) | ~3,575 chars total | Frozen snapshot injected at session start. Mid-session writes persist to disk, appear NEXT session. At ~80% capacity, agent consolidates entries. |
| **Tier 2: Session search** | SQLite + FTS5 full-text search | Unlimited | All conversations in `state.db`. LLM-summarized cross-session recall on demand. |
| **Tier 3: External plugins** | Pluggable providers (Honcho, vector stores, custom) | Varies | Prefetch before each turn, sync after each response, extract on session end. Only one active at a time. |

### Memory flow

```
Session start → freeze system prompt snapshot (cache-aware, keeps token costs flat)
  → Mid-session: agent writes facts to MEMORY.md (appear next session, not current)
  → Periodic nudges: agent reminds itself to persist durable knowledge
  → Cross-session: FTS5 search + LLM summarization for historical recall
```

### Pluggable memory providers (v0.7.0+)

```bash
hermes memory setup  # Interactive provider selection
# Options: built-in, Honcho, vector store, custom database
```

### Honcho integration

Dialectic user modeling — builds and maintains a persistent model of who you are across sessions, distinct from factual `MEMORY.md` storage. Continuously refines understanding of user preferences, communication style, and recurring responsibilities.

### Context files

- **`SOUL.md`** — Agent identity. Slot #1 in system prompt (before memory and skills). Defines personality, communication style, boundaries.
- **`AGENTS.md`** — Project-level context. Injected at session start for project-aware behavior.

---

## 8. Gateway — Multi-Platform Messaging

### Architecture

Single gateway process connects the same agent core to **24+ platforms**:

```
Gateway (single process, ticks every 60 seconds)
├── Telegram       (Bot API webhook + polling)
├── Discord        (Bot API)
├── Slack          (Socket Mode + Events API, multi-workspace OAuth)
├── WhatsApp       (Baileys web)
├── Signal         (signal-cli)
├── iMessage       (BlueBubbles)
├── SMS            (Twilio)
├── Email          (IMAP/SMTP)
├── Matrix         (Federation)
├── DingTalk       (钉钉)
├── Feishu/Lark    (飞书)
├── WeCom          (企业微信)
├── Weixin         (微信)
├── QQBot          (QQ)
├── LINE
├── Microsoft Teams
├── Google Chat
├── IRC
├── Mattermost
├── SimpleX Chat
├── Webhook        (generic)
├── Home Assistant
└── Yuanbao        (元宝)
```

### Per-platform controls

| Feature | Detail |
|---------|--------|
| **User allowlists** | Restrict to specific user IDs per platform |
| **DM pairing** | Pairing codes with cryptographic TTL for DM authorization |
| **Group @mention gating** | Always respond / only when mentioned / regex trigger |
| **Slack multi-workspace OAuth** | Each workspace resolved dynamically per incoming event |
| **Topic/thread routing** | Telegram forum topics → different profiles (in development) |

### Cross-platform consistency

Slash commands (`/model`, `/compress`, `/skills`, `/memory`, `/cron`) work identically across ALL platforms. Every channel gets the same agent interface.

---

## 9. Cron Scheduler

### Architecture

Built-in cron engine runs jobs in **isolated sessions** and delivers output to any gateway platform.

### Job types

| Type | Example | Use case |
|------|---------|----------|
| **One-shot** | `/cron add 30m "check server status"` | Delayed execution |
| **Interval** | `"every 2h"` | Recurring checks |
| **Cron expression** | `"0 9 * * 1-5"` | Weekday morning reports |
| **Natural language** | "Every weekday at 8am India time..." | User-friendly scheduling |

### Natural language example

```
Every weekday at 8am India time, prepare a deep digest of what's new
in the AI and machine learning space over the last 24 hours. Cover
four streams: GitHub trends, lab announcements, papers, social pulse.
Cite every claim with a URL. Keep under 800 words. Deliver to Telegram.
Set this up as a recurring cron job.
```

### Cron management

```bash
hermes cron list                    # List all scheduled jobs
hermes -p researcher cron list      # Per-profile cron jobs
hermes cron add ...                 # Create new job
hermes cron remove <id>             # Delete job
```

### API server cron

The `/v1/chat/completions` API server exposes a `/api/jobs` REST API for cron management with full CRUD.

### Advanced features

- **Attach skills**: `--skill blogwatcher` associates a skill with a cron job
- **Chain jobs**: `context_from` links job output to the next job
- **Gateway ticks**: gateway process ticks every 60 seconds, runs due jobs
- **Separate sessions**: each cron run gets its own isolated session
- **Delivery**: output delivered to configured gateway platform, not just logged

---

## 10. Provider System

### 18+ providers

| Provider | Auth method |
|----------|------------|
| Nous Portal | Platform API |
| OpenRouter | API key |
| Anthropic (native) | API key + OAuth PKCE |
| OpenAI | API key |
| AWS Bedrock | IAM credentials |
| NVIDIA NIM | API key |
| Google Gemini | OAuth |
| Hugging Face | API key |
| DeepSeek | API key |
| GitHub Copilot | OAuth |
| Vercel ai-gateway | API key |
| LM Studio | Local (no auth) |
| xAI (Grok) | API key |
| Mistral | API key |
| Groq | API key |
| Arcee AI | API key |
| Azure AI Foundry | API key |
| Tencent Tokenhub | API key |
| Any OpenAI-compatible endpoint | API key |

### Fallback provider chain

Ordered fallback: if provider A fails, provider B is tried automatically. Chain is user-configurable. No silent fallback to platform credits — the chain is explicit and user-controlled.

### Model switching

```bash
hermes model                       # Interactive model picker
hermes -m "anthropic/claude-sonnet-4-6"  # Direct model selection
```

---

## 11. Tool Runtime — 6 Terminal Backends

| Backend | Isolation | Dangerous cmd check | Best for |
|---------|-----------|---------------------|----------|
| `local` | None — runs on host | ✅ Yes | Development, trusted users |
| `ssh` | Remote machine | ✅ Yes | Separate server |
| `docker` | Container | ❌ Skipped (container IS boundary) | **Production gateway** |
| `singularity` | Container | ❌ Skipped | HPC environments |
| `modal` | Cloud sandbox | ❌ Skipped | Scalable cloud isolation |
| `daytona` | Cloud sandbox | ❌ Skipped | Persistent cloud workspaces |

### ProCode mode

Agent writes Python scripts that batch-call tools via RPC — collapsing multi-step pipelines into **zero-context-cost turns**. The script runs, tools are called, results are collected, only the final output consumes context.

---

## 12. Security — 7-Layer Defense-in-Depth

### The seven layers

| Layer | Mechanism |
|-------|-----------|
| **1. User Authorization** | Platform-specific allowlists, DM pairing codes with cryptographic TTL, global allow/deny rules |
| **2. Dangerous Command Approval** | Human-in-the-loop for destructive ops. Modes: `manual` / `smart` (LLM-assessed) / `off` |
| **3. Container Isolation** | Docker/Singularity/Modal sandboxing with hardened settings |
| **4. MCP Credential Filtering** | Environment variable isolation for MCP subprocesses |
| **5. Context File Scanning** | Prompt injection detection in project files |
| **6. Cross-Session Isolation** | Sessions cannot access each other's data or state |
| **7. Input Sanitization** | Working directory parameters validated against allowlists |

### Docker hardening (applied automatically)

```yaml
--cap-drop ALL                    # Drop ALL Linux capabilities
--cap-add DAC_OVERRIDE            # Minimal: write to bind mounts
--cap-add CHOWN, FOWNER           # Minimal: package manager needs
--security-opt no-new-privileges  # Block privilege escalation
--pids-limit 256                  # Process count limit
--tmpfs /tmp:rw,nosuid,size=512m
--tmpfs /var/tmp:rw,noexec,nosuid,size=256m
```

### Hardline blocklist (always-on floor)

Catastrophic commands NEVER executed regardless of YOLO mode or approval settings:
- `rm -rf /`
- Fork bombs
- Block-device writes (`dd if=... of=/dev/sda`)
- Systemctl/service manipulations on host

### Resource limits (config.yaml)

```yaml
terminal:
  backend: docker
  docker_image: "nikolaik/python-nodejs:python3.11-nodejs20"
  docker_forward_env: []              # Empty = no secrets leak into container
  container_cpu: 1
  container_memory: 5120              # MB
  container_disk: 51200               # MB
  container_persistent: true          # Persist filesystem across sessions
```

### Additional protections

- **Secret redaction** — API keys (ElevenLabs, Tavily, Exa, etc.) redacted in logs and outputs
- **SSRF protection** — fail-closed, blocks private networks and cloud metadata endpoints
- **Vision file rejection** — non-image files rejected to prevent information disclosure
- **Cron delivery hardening** — agents can't suppress delivery with `[SILENT]` prefix manipulation
- **Atomic config writes** — prevents data loss during crashes

---

## 13. Plugin System

### Architecture

Drop Python files into `~/.hermes/plugins/` to extend Hermes. No forking required.

### Plugin extension points

| Hook | Purpose |
|------|---------|
| Custom tools | Add new tool functions |
| Custom commands | Add new CLI commands |
| Hooks | `pre_tool_call`, `pre_llm_call`, `subagent_stop`, etc. |
| Dashboard tabs | Add panels to web UI |
| Gateway platforms | Add new messaging platforms |
| Provider backends | Add new LLM providers |
| Image-generation backends | Add image generation |
| Video-generation backends | Add video generation |

### Plugin capabilities (v0.14.0+)

- `ctx.llm` — plugins can make their own LLM calls
- `tool_override` — plugins can override built-in tools
- Lifecycle hooks: startup, shutdown, pre/post turn

---

## 14. API Server

### OpenAI-compatible endpoint

```bash
hermes serve                      # Start API server
curl http://localhost:8080/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model": "hermes", "messages": [{"role": "user", "content": "Hello"}]}'
```

### Features

- **Session persistence** — conversation state preserved across API calls (`session_id` parameter)
- **Streaming** — SSE support
- **Cron REST API** — `/api/jobs` for full cron CRUD
- **Multiple sessions** — multiple concurrent conversations via separate session IDs

---

## 15. Enterprise Multi-Tenant Architecture (AWS Reference)

A production deployment pattern on **Amazon ECS Fargate + Graviton**:

| Layer | Mechanism |
|-------|-----------|
| **Routing** | ALB path-based routing prevents cross-tenant URL access |
| **Compute** | Independent ECS Services + Fargate Tasks (no shared kernel/CPU/memory/network) |
| **Storage** | EFS Access Points with per-tenant paths + uid/gid + POSIX permissions |
| **Allocation** | DynamoDB with `ConditionExpression` for atomic slot assignment |

---

## 16. Version Timeline (2026)

| Version | Date | Key additions |
|---------|------|---------------|
| v0.3.0 | ~March | Streaming, persistent shell, concurrent tools, Ink TUI, ACP, voice, plugins, Anthropic native, PII redaction |
| v0.4.0 | ~March | API server, cron REST, MCP CLI, @context references, GitHub Copilot, context compression |
| v0.5.0 | ~March | Plugin lifecycle hooks, Nix flake, HuggingFace, idempotency keys |
| v0.6.0 | March 30 | **Profiles (multi-instance)**, MCP server mode, Docker container, fallback provider chain |
| v0.7.0 | April | Pluggable memory providers, credential pools, Camofox browser, Honcho, API session continuity |
| v0.10.0 | ~April | AWS Bedrock native, Nous Tool Gateway |
| v0.11.0 | ~May | Ink TUI rewrite (React/Ink), pluggable transport, NVIDIA NIM, Arcee, Step Plan, Gemini OAuth, Codex OAuth |
| v0.12.0 | ~May | Spotify, Google Meet, Piper TTS, GMI Cloud, Azure AI Foundry, LM Studio, MiniMax, Tencent Tokenhub |
| v0.13.0 | ~May | Curator archive/prune/list-archived, synchronous manual runs |
| v0.14.0 | May 16 | Native Windows beta, subscription proxy, `x_search`, `computer_use`, LSP diagnostics, `vision_analyze`, video generation |
| v0.15.2 | May 29 | **Currently installed version** |
| v0.17.0 | June 19 | Latest release |

---

## 17. Comparison: Hermes vs Empyralis

| Dimension | Hermes | Empyralis |
|-----------|--------|-----------|
| **Install base** | pip package + curl installer | GitHub repo, manual setup |
| **Agent isolation** | Profiles (HERMES_HOME per instance) | Workspace-scoped, per-agent model_config |
| **Per-agent channels** | ✅ Per-profile gateway binding | ❌ Planned (ROADMAP.md) |
| **Container isolation** | ✅ Docker/Singularity/Modal with hardening | ❌ Planned (ROADMAP.md) |
| **Delegation depth** | 1–3 levels, async since June 2026 | 1 level (operator→specialist) |
| **MCP** | Client + Server mode, OAuth 2.1 PKCE | Client (30 apps), Server mode (9 tools) |
| **Provider model** | 18+ providers, BYOK, fallback chain | 17 providers, 4 payment modes, platform credits |
| **Payment** | User's own keys only | Platform credits + BYOK + subscription + local |
| **Skills** | Self-improving closed loop, 166 skills, curator | None |
| **Memory** | 3-tier (prompt + FTS5 + pluggable), Honcho | File-based (SOUL.md/MEMORY.md), 3-tier config/outputs/private |
| **Gateway** | 24+ platforms, single process | 7 platforms in UI, separate adapters per platform |
| **Cron** | Built-in scheduler, natural language, per-profile | schedule_task backend, no UI yet |
| **UI** | TUI (Ink/React) + Web UI + API | Next.js fleet UI (7-tab modal, wizard, Model tab) |
| **Security** | 7-layer defense-in-depth | Governance gate + policy manifests |
| **Plugins** | Drop-in Python files, 8 extension points | None (MCP-based extensibility) |
| **Deployment** | pip install, runs anywhere | python server.py + npm run dev |
| **API** | OpenAI-compatible `/v1/chat/completions` | FastAPI REST + MCP server |
| **License** | MIT | Proprietary |

---

## 18. Key Architectural Decisions in Hermes

1. **Monolithic agent loop** — 82KB+ `main.py`, not microservices. Simplicity over distributed complexity.
2. **Profiles over multi-tenancy** — Filesystem isolation, not database-level. `HERMES_HOME` is the boundary.
3. **Filesystem as state** — `MEMORY.md`, `USER.md`, `SOUL.md` are plain Markdown files. Git-trackable, human-readable.
4. **Container as security boundary** — Docker isn't for deployment, it's for blast radius. `--cap-drop ALL`.
5. **Sub-agents as threads, not processes** — ThreadPoolExecutor, not separate Python processes. Credential pool shared via leasing.
6. **Skills as procedural memory** — Markdown + YAML, not vector DB. Human-readable, git-diffable.
7. **Plugin system over internal extensibility** — Drop files in `~/.hermes/plugins/`, no PR needed.
8. **No platform credits** — User always brings their own keys. No billing system, no credit ledger.
9. **TUI-first, API-second, Web-third** — Primary interface is terminal. Web UI is secondary. API for integration.

---

## Sources

- Official docs: <https://hermes-agent.nousresearch.com/docs>
- GitHub: <https://github.com/NousResearch/hermes-agent>
- Community docs: <https://github.com/mudrii/hermes-agent-docs>
- Installed package: `hermes-agent v0.15.2` at `/opt/homebrew/lib/python3.14/site-packages/hermes_cli/`
- Security: <https://hermes-agent.nousresearch.com/docs/user-guide/security>
- Delegation patterns: <https://github.com/NousResearch/hermes-agent/blob/main/website/docs/guides/delegation-patterns.md>
- Multi-agent vision: <https://github.com/NousResearch/hermes-agent/issues/344>
- Topic-to-profile routing: <https://github.com/NousResearch/hermes-agent/issues/10143>
- Async sub-agents announcement: <https://www.techtimes.com/articles/318549/20260617/hermes-agent-ships-async-subagents-delegated-work-no-longer-blocks-chat.htm>
