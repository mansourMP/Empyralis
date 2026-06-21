# Empyralis Development Guide

This document provides essential information for AI assistants working on the Empyralis platform codebase.

## Project Overview

**Empyralis** is an enterprise-grade operating system for building, running, and governing AI workers. The platform provides six integrated workspace surfaces (Sage, Agents, Discover, Applications, Agent Computer, and Settings) backed by:

- **Backend**: Service-oriented Python microservices
- **Frontend**: Next.js 16 with React 19
- **Desktop**: Tauri-based tray application
- **Mobile**: React Native application
- **Runtime**: Rust-based kernel for agent execution
- **Multi-tenant**: Workspace-scoped data isolation and governance

**Key Features**:
- Multi-channel agent deployment (Telegram, WhatsApp, Slack, Discord, Email, Web Chat, iMessage)
- Persistent AI agent identity with layered memory architecture
- Safety control plane with approval gates and policy enforcement
- Skill system for extensible tool capabilities
- Marketplace with pre-configured agent templates
- Cloud session management and hardware execution gateway

---

## Repository Structure

```
/home/user/Empyralis/
├── frontend/                 # Next.js 16 + React 19 web app
│   ├── app/                 # App Router pages (Sage, Agents, etc.)
│   ├── lib/                 # Shared utilities, hooks, types
│   ├── package.json         # Dependencies: gsap, lucide-react, motion, qrcode
│   └── playwright.config.ts # E2E test configuration
├── apps/empyralis-tray/     # Tauri desktop app (macOS/Windows)
│   ├── src/                 # Tauri/React source
│   ├── src-tauri/           # Rust backend
│   └── package.json
├── mobile/                  # React Native mobile application
│   ├── app/                 # React Native app structure
│   └── package.json
├── python_engine/           # Python backend services
│   ├── cognitive_daemon.py  # Main agent executor
│   ├── cognitive_loop.py    # Single-turn execution logic
│   ├── llm_core.py          # LLM provider routing (Anthropic, OpenAI, Gemini, DeepSeek)
│   ├── agency_logic.py      # Multi-agent orchestration
│   ├── agent_identity.py    # Persistent agent state
│   ├── memory_manager.py    # Memory layer management
│   ├── operator_skills.py   # Skill system implementation
│   └── test_*.py            # Test suites
├── empyralis-gateway/       # TypeScript gateway for hardware execution
│   ├── src/                 # Gateway logic
│   └── package.json
├── cloud-session-manager/   # Session lifecycle management
│   ├── src/                 # Session manager implementation
│   ├── tests/               # Test suites
│   └── package.json
├── skills/                  # Installable skills (tools for agents)
│   ├── code-runner/         # Code execution skill
│   ├── web-search/          # Search capability
│   ├── vision-monitor/      # Image processing
│   ├── browser/             # Browser automation
│   ├── telegram-bot/        # Telegram-specific skill
│   ├── file-manager/        # File operations
│   └── business-skill-template/  # Template for custom skills
├── shared/                  # Monorepo shared packages
│   ├── design-system/       # React component library
│   └── api-contract/        # Shared TypeScript types
├── deploy/                  # Deployment configurations
│   └── agent-computer/      # Agent hardware execution Docker setup
├── config/                  # Configuration management
├── marketplace/             # Agent template marketplace
├── runtime/                 # Runtime contract definitions
│   └── contracts/           # API contracts for runtime
├── references/              # Reference documentation
│   └── ui-patterns/         # UI pattern examples
├── docs/                    # Documentation
│   ├── platform/            # Platform documentation
│   ├── domains/             # Domain-specific docs
│   └── decisions/           # Architectural decision records
├── .github/workflows/       # CI/CD pipelines
│   ├── build.yml            # Build and test pipeline
│   ├── ci.yml               # Continuous integration
│   ├── security-baseline.yml # Security checks
│   └── supply-chain.yml     # Supply chain verification
├── scripts/                 # Build and development scripts
├── PLATFORM.md              # Detailed platform specification
├── PHASE_*.md               # Development phase memos
└── package.json             # Root monorepo config (Tauri, Playwright)
```

---

## Technology Stack

### Frontend
- **Framework**: Next.js 16 (App Router)
- **UI Library**: React 19
- **Styling**: Tailwind CSS (inferred from lucide-react usage)
- **Icons**: lucide-react
- **Animation**: GSAP, Motion, Lenis
- **QR Code**: qrcode
- **Testing**: Playwright (E2E)
- **Type System**: TypeScript 5.9
- **Port**: 3000 (localhost development)

### Backend (Python)
- **Core Engine**: Python 3.x
- **LLM Providers**:
  - Anthropic (Claude)
  - OpenAI (GPT)
  - Google Gemini
  - DeepSeek (preferred for Empyralis credits)
- **Provider Routing**: Model tier-based with BYOK (Bring Your Own Key) support
- **Memory**: Multi-tier (Profile, Episodic, Local Private, Cloud Synced)
- **State Management**: File-based (EMPYRALIS_STATE_HOME) + PostgreSQL
- **Testing**: pytest patterns

### Gateway (TypeScript/Node.js)
- **Framework**: Likely Express or similar
- **Purpose**: Hardware execution proxy
- **Connection**: Secure gateway protocol

### Cloud Session Manager
- **Framework**: TypeScript/Node.js
- **Purpose**: Session lifecycle, credential management
- **Testing**: Unit + integration tests

### Desktop (Tauri + Rust)
- **Frontend**: React + TypeScript
- **Backend**: Rust (Tauri 2.0)
- **Targets**: macOS (DMG), Windows
- **Port**: Defined in tauri.conf.json

### Mobile
- **Framework**: React Native
- **Language**: TypeScript
- **Targets**: iOS, Android (via Go commands)

### Database
- **Primary**: PostgreSQL
- **Fallback**: SQLite (local dev, edge cases)
- **Environment**: DATABASE_URL env var

---

## Development Setup

### Prerequisites
- Node.js 18+ (for frontend, gateway, session manager, desktop)
- Python 3.8+ (for python_engine)
- Rust toolchain (for Tauri desktop builds)
- PostgreSQL 12+ (or SQLite for local dev)

### Environment Configuration

Copy `.env.example` to `.env` and configure:

**Required Secrets**:
- `ORION_JWT_SECRET`: 32+ character random string
- `EMPYRALIS_TOOL_BROKER_SECRET`: Broker authentication
- `EMPYRALIS_SECRETS_BROKER_SECRET`: Secrets encryption
- `CREDENTIAL_VAULT_KEY`: Provider API key encryption

**API Keys** (for LLM providers):
- `OPENAI_API_KEY`: OpenAI access
- `ANTHROPIC_API_KEY`: Anthropic Claude access
- `GEMINI_API_KEY`: Google Gemini access

**URLs**:
- `DATABASE_URL`: PostgreSQL connection string
- `EMPYRALIS_STATE_HOME`: State directory path
- `EMPYRALIS_PUBLIC_URL`: Deployment public URL

**CORS**:
- `CONTROL_PLANE_ORIGINS`: Admin UI origins
- `FRONTEND_ORIGINS`: Frontend origins

### Common Development Commands

**Frontend**:
```bash
npm run dev              # Start Next.js dev server (port 3000)
npm run build            # Production build
npm run typecheck        # TypeScript type checking
npm run test:e2e         # Playwright E2E tests
npm run dev:e2e          # Start frontend for E2E testing
```

**Desktop (Tauri)**:
```bash
npm run tray:dev        # Start Tauri dev server
npm run tray:build      # Build desktop app
npm run tray:build:dmg  # Build DMG for macOS
```

**Mobile**:
```bash
npm run mobile:dev      # React Native dev
npm run mobile:ios      # iOS build
```

**Python Backend**:
```bash
cd python_engine
python test_manual.sh   # Run manual tests
pytest test_*.py        # Run unit tests
```

---

## Code Organization Conventions

### Frontend (`/frontend`)

**Page Structure**:
- Pages live in `/app` using Next.js App Router conventions
- Each workspace surface (Sage, Agents, etc.) has a directory
- Child routes follow file-based hierarchy: `[destination]/[route]/page.tsx`

**Component Organization**:
- Shared components in `/lib/components`
- Page-specific components co-located with pages
- Hooks in `/lib/hooks`
- Types in `/lib/types` (or inline when local)
- Utils in `/lib/utils`

**Naming Conventions**:
- Components: PascalCase (e.g., `AgentCard.tsx`)
- Hooks: camelCase prefixed with `use` (e.g., `useAgentState.ts`)
- Utilities: camelCase (e.g., `formatCurrency.ts`)
- Types: PascalCase, exported from `/lib/types` (e.g., `Agent.ts`)

**State Management**:
- No global state framework enforced
- Likely using React Context or URL state (Next.js app router patterns)
- Server components preferred where possible (Next.js 13+ pattern)

**Testing**:
- E2E tests in `/tests/e2e` with Playwright
- Focus on user workflows, not implementation details
- Fixtures for common setup (agents, channels, etc.)

### Python Backend (`/python_engine`)

**Architecture**:
- `cognitive_daemon.py`: Event loop, agent execution orchestrator
- `cognitive_loop.py`: Single-turn execution (parse inputs → tool calls → LLM response)
- `llm_core.py`: Provider abstraction (route to Anthropic, OpenAI, Gemini, DeepSeek)
- `agency_logic.py`: Multi-agent coordination
- `memory_manager.py`: Memory layer (load/persist/query)
- `agent_identity.py`: Identity persistence (Soul.md, Identity.md, Heartbeat.md)
- `operator_skills.py`: Skill registry and execution

**Memory Files**:
- `Soul.md`: Agent personality and identity
- `Identity.md`: Current status, goals, context
- `Heartbeat.md`: Execution log, task state
- `MEMORY.md`: Shared workspace memory

**Conventions**:
- Use type hints (Python 3.8+)
- Logging via structured logs (JSON format encouraged per ORION_LOG_FORMAT)
- Error handling: Explicit exceptions, no silent failures
- Testing: Unit tests with pytest

### TypeScript Components

**Shared API Contract** (`/shared/api-contract`):
- Central place for API request/response types
- Imported by frontend, gateway, session manager
- Avoid duplication of types across services

**Design System** (`/shared/design-system`):
- Reusable React components
- Lucide icons for consistency
- Tailwind utility classes

---

## Key Architectural Patterns

### Multi-Tenant Isolation
- **Workspace Scoping**: All data keyed by workspace_id
- **Memory Isolation**: User memories private by default, permissioned on-demand
- **Provider Routing**: Per-workspace provider entitlements

### Memory Architecture
1. **Profile Memory**: Long-term agent identity (Soul.md, Identity.md)
2. **Episodic Memory**: Conversation history, run traces
3. **Local Private**: On-device state, not synced
4. **Cloud Synced**: Encrypted cloud backup of critical state

### Governance Layers
- **Approval Gates**: Manual approval before agent actions
- **Policy Enforcement**: Runtime execution constraints
- **Quotas & Entitlements**: Credit limits, provider tier access
- **Audit Trail**: Transparent transparency timeline (tool calls, approvals, decisions)

### Channel Architecture
- **Unified Ingress**: Single message router to all channels
- **Channel-Aware Formatting**: Output formatted per channel (Markdown for Slack, HTML for Email, etc.)
- **Sender Identity**: Track sender across channels
- **Outbound Routes**: Per-channel credential binding

### Skill System
- **Installable Tools**: Skills registered in workspace-scoped registry
- **Skill Contracts**: Defined input/output schemas
- **Execution**: Sandbox or trusted per safety policy

---

## Development Workflows

### Creating a New Feature

1. **Create a branch** from `main` following naming: `feature/short-description`
2. **Make changes** in relevant directories (frontend, backend, both, etc.)
3. **Test locally**:
   - Run type checking: `npm run typecheck` (frontend)
   - Run unit tests: `pytest` (backend)
   - Run E2E if UI: `npm run test:e2e`
4. **Commit with clear messages**: Focus on "why" not "what" (code is self-documenting)
5. **Push and create PR**: Link to relevant design docs or issues
6. **Await CI**: build.yml, ci.yml, security-baseline.yml, supply-chain.yml must pass

### Fixing a Bug

1. **Investigate** using available test suites and local reproduction
2. **Locate root cause** before implementing fix (don't paper over)
3. **Fix in minimal scope**: Avoid unrelated refactoring
4. **Add test** if behavior wasn't tested before
5. **Verify fix**: Run related tests + manual testing if UI-related
6. **Commit & PR**: Reference issue if applicable

### Adding an LLM Provider

1. Update `llm_core.py`: Add provider class inheriting from base LLM
2. Add credentials to `.env.example` and vault
3. Add routing logic in `cognitive_loop.py` provider selection
4. Add provider tests in `test_llm_core.py` (if exists)
5. Update PLATFORM.md if this is a user-facing provider option

### Creating a New Skill

1. Create directory in `/skills/{skill-name}/`
2. Follow template: `/skills/business-skill-template/`
3. Define skill contract (inputs, outputs, description)
4. Implement execution logic
5. Add skill manifest to registry
6. Test with agent in studio before marketplace registration

### Database Migrations

1. Create migration in appropriate schema directory
2. Test with PostgreSQL locally
3. Verify fallback behavior with SQLite
4. Update docs if schema changes are user-facing

---

## Testing Strategy

### Frontend (E2E with Playwright)
- **Location**: `/frontend/tests/e2e/`
- **Scope**: User workflows (create agent, deploy, test conversation)
- **Run**: `npm run test:e2e` from frontend directory
- **Coverage**: Deployed agents, non-scaffold surfaces, auth flows

### Backend (Unit + Integration)
- **Location**: `/python_engine/test_*.py`
- **Scope**: Cognitive loop, memory management, provider routing
- **Run**: `pytest test_*.py` from python_engine
- **Mocking**: Mock LLM providers, use in-memory state where possible

### CI/CD
- **build.yml**: Builds frontend, backend, desktop, mobile artifacts
- **ci.yml**: Runs tests, type checks, linters
- **security-baseline.yml**: OWASP checks, secret scanning
- **supply-chain.yml**: Dependency verification

---

## Common Pitfalls to Avoid

### Frontend
- **Don't**: Fetch API routes without error boundaries
- **Don't**: Use client state when URL params could work (Next.js patterns)
- **Don't**: Hardcode origins or URLs (use env vars)
- **Do**: Use Server Components for data fetching
- **Do**: Type all props and return types

### Backend
- **Don't**: Assume provider API availability (always handle errors)
- **Don't**: Store unencrypted API keys (use credential vault)
- **Don't**: Skip memory isolation checks
- **Do**: Log with structured format (JSON)
- **Do**: Handle graceful degradation (fallback providers)

### Database
- **Don't**: Assume PostgreSQL-only (SQLite fallback required)
- **Don't**: Use blocking queries in async contexts
- **Do**: Test migrations with both DB backends
- **Do**: Add indexes for frequently queried columns

---

## Debugging Tips

### Frontend
```bash
# Type checking
npm run typecheck

# Check API calls
# Open DevTools → Network tab, look for /api/* calls
# Check response bodies for error messages

# E2E test debugging
npx playwright test --debug
npx playwright show-report
```

### Backend
```bash
# Check logs (JSON structured)
tail -f logs/empyralis.log | jq .

# Python debugging
import pdb; pdb.set_trace()

# Memory inspection
cat $EMPYRALIS_STATE_HOME/agents/{agent_id}/MEMORY.md
```

### Database
```bash
# PostgreSQL
psql $DATABASE_URL -c "SELECT * FROM agents LIMIT 5;"

# SQLite
sqlite3 empyralis.db "SELECT * FROM agents LIMIT 5;"
```

---

## Style & Code Quality

### Python
- Type hints required (Python 3.8+)
- Docstrings for public functions (single-line if simple)
- Logging via configured logger
- No print() statements (use logging)
- PEP 8 style guide

### TypeScript/JavaScript
- Strict mode enabled
- No `any` types without explicit justification
- Exported types from centralized locations
- Consistent file naming (PascalCase for exports, camelCase for utils)
- Prettier formatting (auto-applied in IDE)

### Comments
- Explain "why" not "what" (code is self-documenting)
- Mark workarounds with `// TODO: Remove after [date/condition]`
- Reference issue numbers for non-obvious decisions

---

## Useful Resources

**Internal Documentation**:
- `PLATFORM.md`: Complete platform specification
- `PHASE_0_GROUND_TRUTH_REPORT.md`: Current implementation status
- `PHASE_1_GAP_MAP_AND_DECISION_MEMO.md`: Architectural decisions
- `/docs/decisions/`: ADRs for major architectural choices

**External References**:
- [Next.js 16 Docs](https://nextjs.org)
- [Tauri Docs](https://tauri.app)
- [Anthropic API Docs](https://docs.anthropic.com)

---

## Quick Reference: Branch Conventions

- **Development**: `claude/claude-md-docs-*` (temporary feature branches)
- **Main**: `main` (stable, deployed)
- **Feature**: `feature/{description}` (new features)
- **Fix**: `fix/{description}` (bug fixes)
- **Refactor**: `refactor/{description}` (non-functional changes)

---

## Getting Help

When stuck on implementation:

1. **Check PLATFORM.md** for feature specification
2. **Look at existing similar code** (find by `grep` or `Glob`)
3. **Run tests locally** to understand expected behavior
4. **Review git history** for context: `git log -p {file}`
5. **Ask in code comments** if approach is unclear

---

## For AI Assistants Working on This Codebase

This guide is written for you. Key principles:

- **Read PLATFORM.md first** when starting a feature
- **Check existing patterns** before introducing new ones
- **Run tests locally** before pushing (no surprise CI failures)
- **Keep changes minimal** and focused (separate concerns into separate PRs)
- **Update docs** when behavior or structure changes
- **Test with real data** when possible (not just happy paths)
- **Consider multi-tenancy** in all changes (workspace isolation)
- **Handle provider failures** gracefully (BYOK fallback logic)
- **Prefer explicit errors** over silent degradation
- **Log structured events** for observability

---

*Last updated: 2026-06-21*
*Branch: claude/claude-md-docs-cjd1qf*
