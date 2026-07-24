# Agent Backbone Research — OpenAI Codex & Cursor

> **Terminology note (2026-07-23):** "Sage" is legacy product terminology — the founder ruled the concept removed from the product; the platform has only agents (owner-facing, customer-facing serving the owner, and AskAI). `sage_*` code/variable/string identifiers are legacy code artifacts only, not a live product concept. Anywhere this document's prose says "Sage" or "master," read: the owner-facing agent. This is a lighter-touch terminology note, not a full rewrite — the body below is unchanged and may still use "Sage" throughout.

External research, no Empyralis code touched. Every claim below is sourced
to a live doc page fetched during this research pass (2026-07-22) and
labeled **[official]** (OpenAI/Cursor docs, changelog, or GitHub Pages SDK
docs) or **[secondary]** (community analysis, used only where no official
doc states the fact — flagged explicitly, never presented as official).
Several `developers.openai.com/codex/*` and `docs.cursor.com/*` URLs
308-redirect to `learn.chatgpt.com/docs/*` / `cursor.com/docs/*` — both the
original and resolved URL are given so either can be re-fetched.

Goal: document the concrete mechanisms behind the quality the founder
admires — an agent that keeps working through a task, spins up sub-agents,
manages its own context, and self-corrects — in the two products that do
this best today, so Empyralis's own agent loop (`packages/agent-core`,
`sage_agent_runtime_service.py`, see `docs/design/gap-ai-operation.md`) has
concrete external reference points.

---

## 1. OpenAI Codex

### 1.1 Continuous long-horizon work

- **Two surfaces, one loop, different stopping rules.** Codex CLI/IDE run
  interactively (`codex`) or non-interactively (`codex exec` / `codex e`)
  for "scripted or CI-style runs that should finish without human
  interaction," with `--json` for newline-delimited state-change events,
  `--output-last-message` and `--output-schema` to pin the final answer
  shape. **[official]**
  [developers.openai.com/codex/cli/reference](https://developers.openai.com/codex/cli/reference)
  (→ [learn.chatgpt.com/docs/developer-commands?surface=cli](https://learn.chatgpt.com/docs/developer-commands?surface=cli))
- **`codex resume` / `codex exec resume --last`** continue a prior session
  by ID or "most recent," so a long task can be picked back up without
  re-establishing context from scratch. **[official]** same URL as above.
- **The approval policy is the actual stopping condition**, not a fixed
  step budget: Codex runs until it either finishes or hits an action that
  exceeds its sandbox/approval envelope (see §1.5) — "an approval policy
  that controls when it must stop and ask you before acting." **[official]**
  [developers.openai.com/codex/agent-approvals-security](https://developers.openai.com/codex/agent-approvals-security)
  (→ [learn.chatgpt.com/docs/agent-approvals-security](https://learn.chatgpt.com/docs/agent-approvals-security))
- **Codex cloud = run-to-completion, offline-by-default agent.** A prompt
  provisions a fresh, isolated microVM (cached up to 12h for follow-ups),
  clones the repo at a branch/SHA, then runs a **two-phase runtime**:
  *setup phase* (network on, installs deps via setup script) → *agent
  phase* (network off by default; the agent "runs terminal commands in a
  loop, edits code, runs checks, and tries to validate its work"), ending
  in an opened PR. **[official]**
  [developers.openai.com/codex/cloud/environments.md](https://developers.openai.com/codex/cloud/environments.md)
  (→ [learn.chatgpt.com/docs/environments/cloud-environment.md](https://learn.chatgpt.com/docs/environments/cloud-environment.md))
- **Scheduled self-continuation ("Automations").** Codex can "schedule
  future work for itself and wake up automatically to continue on a
  long-term task, potentially across days or weeks" — schedule-based
  (recurring cadence) or trigger-based (repo/event-driven), combining an
  instruction with optional skills. Used internally at OpenAI for daily
  issue triage, CI-failure summaries, release briefs. **[official, page
  fetch 403'd — content below is the doc's own text as surfaced by search
  indexing, treat as high-confidence but re-verify by fetch if precision
  matters]**
  [openai.com/academy/codex-automations](https://openai.com/academy/codex-automations/)

### 1.2 Context management

- **`AGENTS.md` is read before any work starts**, at multiple scopes:
  global (`~/.codex`) then project root down to cwd, later/closer files
  overriding earlier ones; precedence order `AGENTS.override.md` →
  `AGENTS.md` → `TEAM_GUIDE.md` → `.agents.md`. Bounded by
  `project_doc_max_bytes` (32 KiB default) and extensible via
  `project_doc_fallback_filenames`. **[official]**
  [developers.openai.com/codex/guides/agents-md](https://developers.openai.com/codex/guides/agents-md)
- **`/compact`** — a slash command that "summarize[s] the visible chat to
  free tokens," recommended "after long runs so Codex retains key points
  without blowing the context window." **[official]**
  [developers.openai.com/codex/cli/reference](https://developers.openai.com/codex/cli/reference)
  (→ `learn.chatgpt.com/docs/developer-commands`). Companions: `/clear`
  (wipe), `/new` (fresh session), `/statusline` (live token counter) —
  same source.
- **Auto-compaction near the limit + a "session memory compact" fast
  path.** Community analysis (not an official doc page) describes: Codex
  caps context around 400K tokens (272K input / 128K reserved output);
  auto-compact fires at effective-window-minus-~13K tokens and cannot be
  configured above 90% of the window; when it fires, Codex first tries to
  reuse structured info already captured in session memory before paying
  for an LLM summarization call, and prior summary messages are detected
  and dropped so only the freshest summary persists (no summary-of-summary
  drift). **[secondary — flagged, not confirmed on an official page]**
  [codex.danielvaughan.com — Context Compaction Deep Dive](https://codex.danielvaughan.com/2026/04/14/context-compaction-deep-dive-codex-cli-claude-code-opencode/),
  [getunblocked.com — Codex Context Window](https://getunblocked.com/blog/codex-context-window/)
- **No codebase-embedding/semantic-index layer is documented for Codex** —
  unlike Cursor, the official docs describe context as AGENTS.md +
  conversation history + compaction, not a persistent vector index of the
  repo. Confirmed absent across every official Codex page fetched in this
  pass.

### 1.3 Sub-agents / parallel agents

- **Explicit "Subagents" primitive**, first-class in the docs. Two terms:
  *subagent workflow* (Codex runs parallel agents and combines results)
  and *subagent* (a delegated agent handling one task). Triggered by
  direct ask ("spawn one agent per point") or by project/skill
  instructions; the top intelligence tier can delegate proactively.
  **[official]**
  [developers.openai.com/codex/subagents](https://developers.openai.com/codex/subagents)
  (→ [learn.chatgpt.com/docs/agent-configuration/subagents](https://learn.chatgpt.com/docs/agent-configuration/subagents))
- **Why it exists** — named explicitly as a context-management technique:
  "context pollution" (useful info buried under noisy intermediate output)
  and "context rot" (performance degrades as chat fills with irrelevant
  detail); subagents move noisy work off the main thread and return
  summaries instead of raw output. **[official]** same URL.
- **Orchestration is automatic**: the parent handles spawning, routing
  follow-ups, waiting for results, and closing agent threads; with many
  agents running, Codex blocks until all requested results land.
  **[official]** same URL.
- **Concurrency/nesting caps**: `agents.max_threads` (default 6 concurrent
  open threads), `agents.max_depth` (default 1 — root can spawn children,
  children cannot spawn grandchildren). **[official]** same URL.
- **Built-in agent roles**: `default`, `worker` (execution-focused),
  `explorer` (read-heavy analysis); custom agents override by name.
  **[official]** same URL.
- **Custom agents are TOML files** at `~/.codex/agents/` (personal) or
  `.codex/agents/` (project-scoped), each with `name`, `description`,
  `developer_instructions`, and optional `model`,
  `model_reasoning_effort`, `sandbox_mode`, `mcp_servers`, `skills.config`
  — so a subagent can carry its own model, sandbox policy, and MCP
  toolset distinct from the parent. **[official]** same URL.
- **Batch fan-out**: `spawn_agents_on_csv` (experimental) reads one row
  per work item from a CSV, spawns one worker subagent per row, waits for
  the full batch, exports combined results via `report_agent_job_result`.
  **[official]** same URL.
- **Explicit guidance on when to parallelize**: read-heavy work
  (exploration, tests, triage, summarization) is the sweet spot; parallel
  *write*-heavy workflows are called out as conflict-prone. **[official]**
  same URL. Subagents inherit the parent turn's permission/sandbox mode;
  each subagent burns its own model+tool tokens, so a workflow costs more
  than an equivalent single-agent run. **[official]** same URL.

### 1.4 Tasks / scheduling / environments

- **Container/microVM per cloud task**, from the `universal` base image
  (languages/tools preinstalled; configurable per-project; reference repo
  `openai/codex-universal`), cached up to 12h to speed up follow-up turns
  on the same task. **[official]**
  [developers.openai.com/codex/cloud/environments.md](https://developers.openai.com/codex/cloud/environments.md)
- **Setup script vs. agent phase are strictly separated**: setup script (+
  optional maintenance script on a resumed cached container) has full
  network access and installs deps; runs in its own bash session, so env
  vars must be persisted explicitly (e.g. `~/.bashrc`). Regular env vars
  persist for the whole chat; **secrets are only available during setup
  and are deleted before the agent phase starts** — the agent never sees
  them. **[official]** same URL.
- **Scheduling** — see §1.1 Automations (schedule- or trigger-based,
  multi-day/multi-week horizon).

### 1.5 Tools & approvals

- **Three approval policies**: `on-request` (default for version-controlled
  folders — approve only sandbox escalations), `untrusted` (auto-run only
  known-safe reads, approve everything else), `never` (no prompts, stays
  inside sandbox). A `granular` variant lets you set per-category
  behavior (`sandbox_approval`, `rules`, `mcp_elicitations`,
  `request_permissions`, `skill_approval`) independently. **[official]**
  [developers.openai.com/codex/agent-approvals-security](https://developers.openai.com/codex/agent-approvals-security)
- **Three sandbox modes**: `workspace-write` (default — read/edit/run
  inside the working dir automatically), `read-only`, `danger-full-access`
  (no sandbox, no approvals — explicitly "not recommended"). Protected
  paths (`.git`, `.agents`, `.codex`) stay read-only even in
  `workspace-write`. **[official]** same URL.
- **Sandbox is OS-native, not a Codex-internal emulator**: macOS —
  Seatbelt via `sandbox-exec -p`; Linux — `bwrap` + `seccomp`; Windows —
  native Windows sandbox, or the Linux implementation under WSL2; if the
  host can't run the Linux sandbox, Codex recommends Dev Containers so
  Docker supplies the outer isolation boundary. **[official]** same URL.
- **Cloud sandbox is a different mechanism entirely**: isolated
  OpenAI-managed containers, not OS sandbox policy — see two-phase
  network model in §1.4. **[official]** same URL.
- **Network access is destination-rule based, not just on/off**: exact
  hostnames match only themselves, `*.example.com` matches subdomains,
  `**.example.com` matches apex+subdomains; `network_proxy` further
  constrains already-enabled command network access. **[official]** same
  URL.
- **A reviewer *agent* can sit in the approval path**:
  `approvals_reviewer = "auto_review"` routes eligible approval requests
  (sandbox escalations, blocked requests, destructive tool calls) through
  a reviewer agent that assesses exfiltration/credential/destructive risk
  before Codex proceeds — low/medium risk passes, critical is denied,
  high requires real authorization. **[official]** same URL.
- **MCP support** is real and per-agent: custom subagents declare
  `[mcp_servers.<name>]` blocks, so a `docs_researcher` subagent can carry
  a different MCP toolset than the parent thread. **[official]**
  [developers.openai.com/codex/subagents](https://developers.openai.com/codex/subagents).
  Codex "can also elicit approval for app (connector) tool calls that
  advertise side effects, even when the action isn't a shell command or
  file change" — approval isn't limited to shell/filesystem actions.
  **[official]**
  [developers.openai.com/codex/agent-approvals-security](https://developers.openai.com/codex/agent-approvals-security)
- **A command-rule engine sits under the sandbox**: `prefix_rule()`
  matches a command's argument list against a pattern and returns
  `allow`/`prompt`/`forbidden`; for compound shell scripts made of plain
  words joined by safe operators (`&&`, `||`, `;`, `|`), Codex parses with
  tree-sitter and applies rules per sub-command — but scripts using
  redirection, substitution, env vars, or wildcards are *not* decomposed
  (treated as opaque, so they fall back to coarser policy). **[official]**
  [developers.openai.com/codex/llms-full.txt](https://developers.openai.com/codex/llms-full.txt)
  (→ [learn.chatgpt.com/docs/llms-full.txt](https://learn.chatgpt.com/docs/llms-full.txt)), "Rules" section.

### 1.6 Self-correction / iteration

- **The agent-phase loop is explicitly a validate-and-retry loop**: "runs
  terminal commands in a loop. It edits code, runs checks, and tries to
  validate its work" before opening a PR. **[official]**
  [developers.openai.com/codex/cloud/environments.md](https://developers.openai.com/codex/cloud/environments.md)
- **AGENTS.md is the mechanism for teaching Codex what "done" checks
  look like** — project-specific lint/test commands live there, and the
  cloud agent "uses it to find project-specific lint and test commands"
  to run as part of its own validation. **[official]** same URL.
- **Non-interactive failure handling is explicit, not silent**: in `codex
  exec`/automation contexts, "an action that needs new approval fails and
  Codex surfaces the error back to the parent workflow" rather than
  hanging or guessing. **[official]**
  [developers.openai.com/codex/agent-approvals-security](https://developers.openai.com/codex/agent-approvals-security)
- **Human-in-the-loop as the correction backstop**: interactive approval
  mode is framed as enabling a "correction loop before actions take
  effect" — you can review/deny before a risky action executes, rather
  than after. **[official]** same URL. No dedicated "self-heal from a
  failing test" narrative doc was found beyond this — the loop's
  self-correction is implicit in "runs checks, tries to validate its
  work," not a separately documented retry/backoff policy.

---

## 2. Cursor

### 2.1 Continuous long-horizon work

- **Local Agent has no tool-call cap**: "There is no limit on the number
  of tool calls Agent can make during a task" — it keeps working step by
  step (search → read → edit → run → repeat) until it judges the task
  done or hits a stop condition. **[official]**
  [cursor.com/docs/agent/overview](https://cursor.com/docs/agent/overview)
- **Cloud Agents ("Background Agents") run to completion unattended**:
  each spins up on its own dedicated VM with "your repository,
  dependencies, secrets, and network access," and "plans the task, edits
  code, runs commands, and tests its work over minutes or hours" while
  you're away; results land as a PR plus artifacts (videos, screenshots,
  logs) so you can review without checking out the branch. **[official]**
  [cursor.com/help/ai-features/background-agents](https://cursor.com/help/ai-features/background-agents),
  [cursor.com/docs/cloud-agent](https://cursor.com/docs/cloud-agent)
- **Git isolation, not lock-step with your working branch**: Cloud Agents
  clone from GitHub/GitLab/Azure DevOps/Bitbucket, work on a separate
  branch, and push back for handoff — your local working branch is
  untouched. **[official]**
  [cursor.com/docs/cloud-agent](https://cursor.com/docs/cloud-agent)
- **Queued steering, not blocking interruption**: you can queue follow-up
  instructions mid-task (Enter = queued, executes after current step;
  Cmd+Enter = sent immediately, redirects now) — the loop keeps running
  and absorbs new instructions rather than stopping to ask by default.
  **[official]** [cursor.com/docs/agent/overview](https://cursor.com/docs/agent/overview)
- **Checkpoints, not stop-and-ask, are the safety net for "wrong turn"
  recovery**: Cursor auto-snapshots the codebase before significant
  changes; you can preview/restore any checkpoint, or hit Stop
  (Cmd+Shift+Backspace) mid-task to cancel and redirect. **[official]**
  same URL.
- **Scheduled self-continuation ("Automations")** on Cloud Agents:
  scheduled triggers (preset cadence or cron) or event triggers
  (GitHub/GitLab/Bitbucket PR/push/CI events, Slack messages/reactions,
  generic webhooks, Linear issue/cycle events, Sentry errors, PagerDuty
  incidents) kick off a Cloud Agent run with a repo scope (none/single/
  multi-repo) and optional tool access (Slack post, PR comment, MCP).
  **[official]** [cursor.com/docs/cloud-agent/automations](https://cursor.com/docs/cloud-agent/automations)

### 2.2 Context management

- **Semantic codebase index (embedding-based retrieval)**: Cursor chunks
  code into "meaningful, semantically coherent units (functions, classes,
  logical blocks)," embeds each chunk into a vector DB, and at query time
  embeds the query with the same model to match against stored vectors —
  keeping only *relevant* chunks in the model's window instead of the
  whole repo. Index auto-updates on a ~5-minute cycle (add new files,
  re-embed changed files, drop deleted files); `.gitignore`/`.cursorignore`
  exclude noise; chunks are decrypted client-side at query time.
  **[official]** [cursor.com/docs/context/codebase-indexing](https://cursor.com/docs/context/codebase-indexing)
- **Agent picks its own retrieval strategy per query**: grep for exact
  symbols, semantic search for behavioral/conceptual queries, or chained
  multi-search for open-ended exploration — and for large searches it
  "spawns an Explore subagent" that runs parallel searches and returns a
  *summary* rather than dumping raw file contents into the main thread,
  explicitly "keeping the main conversation focused." **[official]** same
  URL.
- **Chat-level auto-summarization at the context-window boundary**:
  "Cursor automatically summarizes long conversation for you when
  reaching the context window limit," plus an on-demand `/summarize` slash
  command to free space without starting a new chat. **[official]**
  [cursor.com/changelog/1-6](https://cursor.com/changelog/1-6)
- **Smart condensation for large files/folders** — a separate mechanism
  from chat summarization: when an included file/folder is too large for
  remaining context budget, Cursor condenses its presentation rather than
  truncating blindly (per-item, based on size vs. available space).
  **[secondary — described in search-indexed doc content, direct page
  fetch redirected to the docs homepage during this pass; re-verify at**
  [cursor.com/docs](https://cursor.com/docs) **→ Context section if exact
  wording matters]**
- **Rules as durable, injected-not-remembered context**: four rule types —
  Project Rules (`.cursor/rules/*.mdc`, versioned), User Rules (global,
  personal), Team Rules (org-wide, Team/Enterprise), and plain `AGENTS.md`
  — each with an application mode (`alwaysApply`, description-triggered
  "apply intelligently," glob-triggered "apply to specific files," or
  manual `@mention`). Precedence: Team → Project → User. Rules are
  re-injected at the start of every relevant session, not persisted as
  memory. **[official]** [cursor.com/docs/context/rules](https://cursor.com/docs/context/rules)

### 2.3 Sub-agents / parallel agents

- **Native "Subagents" primitive** (Cursor 2.4, Jan 2026): "independent
  agents specialized to handle discrete parts of a parent agent's task.
  They run in parallel, use their own context, and can be configured with
  custom prompts, tool access, and models" — sold explicitly on "faster
  overall execution, more focused context in your main conversation, and
  specialized expertise for each subtask." **[official]**
  [cursor.com/changelog/2-4](https://cursor.com/changelog/2-4)
- **Built-in subagent roles ship by default** for codebase research,
  running terminal commands, and parallel work streams, active in both
  editor and CLI without configuration; custom subagents are also
  supported. **[official]** same URL.
- **Agent Skills complement subagents**: `SKILL.md` files hold "custom
  commands, scripts, and instructions" for procedural how-to knowledge,
  positioned as better suited than Rules for "dynamic context discovery"
  — agents discover and apply skills when relevant rather than having
  them always injected. **[official]** same URL.
- **Separately, whole-agent parallelism (Cursor 2.0, distinct from the
  in-task Subagents primitive above)**: up to **8 agents in parallel on
  one prompt**, each in its own isolated copy of the codebase via git
  worktrees (local) or separate remote machines (cloud) — the isolation
  boundary that prevents concurrent-write conflicts. Useful for
  "comparing results from different models" or attacking independent
  tasks at once. **[official]** [cursor.com/changelog/2-0](https://cursor.com/changelog/2-0)

### 2.4 Tasks / scheduling / environments

- **Cloud Agent environment model**: Cursor owns "VM provisioning,
  isolation, snapshots, startup, artifacts, and capacity." Environment
  setup is agent-led, from a saved snapshot, or from a Dockerfile
  declared in `.cursor/environment.json`; the resulting box mirrors a
  normal dev laptop (cloned repo, deps, secrets, startup commands,
  network access). **[official]** [cursor.com/docs/cloud-agent](https://cursor.com/docs/cloud-agent)
- **Team follow-ups**: admins can let teammates send additional messages
  to an already-running or completed Cloud Agent task, extending it past
  the original ask instead of starting a new one. **[official]** same URL.
- **Scheduling** — see §2.1 Automations (cron/preset schedule or
  event-trigger, with repo scope and permission tiers: Private / Team
  Visible / Team Owned, the last affecting billing allocation).
  **[official]** [cursor.com/docs/cloud-agent/automations](https://cursor.com/docs/cloud-agent/automations)

### 2.5 Tools & approvals

- **Toolset is fixed and broad, not user-assembled**: search files/dirs
  by name or content, web search, read files (incl. images for
  vision-capable models), edit files (auto-applied), run shell commands
  in the user's shell profile, browser control (screenshots, interaction
  testing), image generation, and an explicit "ask clarifying questions"
  tool the agent can invoke mid-task instead of guessing. **[official]**
  [cursor.com/docs/agent/overview](https://cursor.com/docs/agent/overview),
  [cursor.com/docs/agent/tools](https://cursor.com/docs/agent/tools)
- **Run Modes (Cursor 3.6+) replace binary "ask every time" vs. "YOLO"
  with a three-tier model**: **Auto-review** (default) — allowlisted
  actions run instantly, compatible shell commands run inside a sandbox,
  everything else routes to a classifier subagent that can allow a call a
  human would have blocked *or* block one a human would have allowed, and
  escalates to you when it can't decide; **Allowlist** — only
  pre-declared trusted actions run automatically, no classifier, fully
  deterministic; **Run Everything** — zero prompts, zero classifier, full
  autonomy/risk. Applies uniformly to shell, MCP, and Fetch tool calls.
  **[official]** [cursor.com/docs/agent/security/run-modes](https://cursor.com/docs/agent/security/run-modes)
- **Sandboxing is a separate layer from Run Modes**, OS-native: macOS —
  Seatbelt profiles (2.0+); Linux — Landlock + seccomp (kernel 6.2+);
  restricts terminal commands to workspace files, approved network
  domains, and designated paths. Commands needing full system access fall
  back to an approval prompt instead of running sandboxed. **[official]**
  same URL.
- **Config surface**: `permissions.json` at `~/.cursor/` (machine-wide) or
  `<project>/.cursor/` (project-scoped), with plain-English
  `allow_instructions`/`block_instructions` fed to the classifier and a
  `terminalAllowlist` for deterministic commands; team dashboards can
  override local config. **[official]** same URL.
- **MCP is a first-class, governed tool source**: three transports
  (STDIO — local/manual auth; SSE and Streamable HTTP — local or remote,
  multi-user, OAuth), configured via `.cursor/mcp.json` (project) or
  `~/.cursor/mcp.json` (global). MCP tool calls are approval-gated by
  default and follow the same Run Modes as terminal commands (Auto-review
  can allowlist a trusted MCP tool to skip the prompt). Local
  command-based MCP servers additionally get per-server network policy
  (allow-all / allowlist / deny-all / no sandbox). Enterprise admins can
  enforce org-wide MCP server/tool allowlists. **[official]**
  [cursor.com/docs/context/mcp](https://cursor.com/docs/context/mcp)

### 2.6 Self-correction / iteration

- **Iterate on Lints**: after Agent edits code, Cursor runs the
  project's configured linter and the agent automatically attempts to fix
  any new lint errors/warnings it just introduced — a tight
  generate-check-fix loop scoped to the diff it just made, across
  multiple files if needed. Enabled in Settings → Features → Chat.
  **[secondary — consistently described across community docs/forum, not
  found stated verbatim on a currently-live official Cursor doc page in
  this pass; behavior is broadly corroborated and matches the "iterate
  until clean" framing Cursor uses elsewhere]**
- **Plan Mode as a front-loaded correctness gate for complex tasks**:
  before writing code, the agent asks clarifying questions, researches
  the codebase for relevant context, and produces an editable Markdown
  plan/TODO list; plans can be saved to the workspace so future
  agent runs (including different sessions) can resume from a known
  state instead of re-deriving context. Explicitly *not* recommended for
  simple/familiar changes. **[official]**
  [cursor.com/docs/agent/plan-mode](https://cursor.com/docs/agent/plan-mode)
- **Dedicated post-hoc review pass**: after a task completes, "Review →
  Find Issues" runs a line-by-line self-review of the agent's own diff
  and flags potential problems before you merge — separate from the
  in-loop lint-fix cycle. **[official]** [cursor.com/docs/agent/review](https://cursor.com/docs/agent/review)
- **Explicit doctrine on verification, including its limits**: docs push
  "verifiable goals" (tests catch regressions, type-checking catches
  structural errors, linting catches style) as the thing that lets you
  trust autonomous runs, while directly warning "passing tests don't
  guarantee the code works correctly" — tests can assert the wrong
  behavior or miss edge cases, so human review is still called out as
  necessary. **[official]** same URL.
- **Human steering as the recovery path mid-task**: watch the live diff,
  and if the agent heads the wrong way, Stop (Cmd+Shift+Backspace) and
  redirect rather than letting it run to a bad completion; for larger
  problems, the documented recommendation is revert-and-refine-the-plan
  over stacking follow-up correction prompts. **[official]**
  [cursor.com/docs/agent/plan-mode](https://cursor.com/docs/agent/plan-mode),
  [cursor.com/docs/agent/overview](https://cursor.com/docs/agent/overview)

---

## 3. OpenAI Agents SDK primitives

Framework-level primitives that underlie Codex's agent concepts and are
OpenAI's general answer to "how do I build this myself."

Official docs: [openai.github.io/openai-agents-python](https://openai.github.io/openai-agents-python/)
(Python SDK reference) and [developers.openai.com/api/docs/guides/agents](https://developers.openai.com/api/docs/guides/agents)
(platform guide). **[official]**

- **Agent** — an LLM configured with instructions + tools; the base unit
  of work. **[official]** same URLs.
- **Runner** — the orchestration loop: "perform[s] the tool loop,
  switch[es] agents after handoffs, and stop[s] when the run finishes or
  pauses for approval." This is the SDK's explicit answer to "how does the
  loop keep going and when does it stop." **[official]**
  [developers.openai.com/api/docs/guides/agents](https://developers.openai.com/api/docs/guides/agents)
- **Handoffs** — one agent delegates the *entire* conversation to another
  specialist agent; represented to the model as a callable tool
  (`transfer_to_refund_agent`), and the receiving agent gets full prior
  history plus control. Configurable via `handoff()`: `tool_name_override`,
  `tool_description_override`, an `on_handoff` callback, a structured
  `input_type`, and an `input_filter` to condense/filter history passed
  forward. This is triage-style delegation (control moves), distinct from
  subagents (control stays, a worker returns a result). **[official]**
  [openai.github.io/openai-agents-python/handoffs](https://openai.github.io/openai-agents-python/handoffs/)
- **Guardrails** — input guardrails (run on the first agent's input,
  parallel by default for latency, or blocking via `run_in_parallel=False`
  to prevent any token spend before a check completes), output guardrails
  (run on the last agent's final output, always after completion), and
  tool guardrails (wrap individual `function_tool`s — input guardrail
  before execution, output guardrail after; not available for hosted
  tools or handoffs). A **tripwire** (`tripwire_triggered=True`) raises
  `InputGuardrailTripwireTriggered`/`OutputGuardrailTripwireTriggered` and
  halts the run immediately — this is the SDK's structured "pause/stop
  before something bad happens" primitive, parallel to Codex's
  `auto_review` reviewer-agent and Cursor's Run Modes classifier.
  **[official]** [openai.github.io/openai-agents-python/guardrails](https://openai.github.io/openai-agents-python/guardrails/)
- **Sessions** — a persistent memory layer that auto-maintains
  conversation history across runs (pre-run: fetch stored history and
  prepend to new input; post-run: persist all new items) so callers don't
  hand-manage `.to_input_list()`. Multiple backends ship out of the box:
  `SQLiteSession`/`AsyncSQLiteSession` (dev), `RedisSession`,
  `SQLAlchemySession` (Postgres/MySQL), `MongoDBSession`, `DaprSession`
  (30+ cloud-native backends), `OpenAIConversationsSession`
  (server-managed via OpenAI), and an `EncryptedSession` wrapper.
  `RunConfig.session_input_callback` lets you customize how history merges
  into a new run — a hook point conceptually similar to
  Cursor's/Codex's summarization mechanisms but exposed as a
  developer-programmable callback rather than a fixed policy.
  **[official]** [openai.github.io/openai-agents-python/sessions](https://openai.github.io/openai-agents-python/sessions/)

---

## 4. Top 3 patterns worth copying into Empyralis's agent backbone

1. **Approval mode as the stop condition, not a step counter — with an
   explicit "reviewer agent" tier in between "always ask" and "never
   ask."** Both Codex (`approvals_reviewer = "auto_review"`, a dedicated
   reviewer agent scoring exfiltration/credential/destructive risk on
   just the actions that already need approval) and Cursor (Run Modes'
   Auto-review classifier subagent, which can overrule a human-authored
   allow/block list either direction) converge on the same idea: don't
   force a binary choice between babysitting every tool call and full
   YOLO. A cheap, fast classifier model in the approval path — scoped
   only to actions that already crossed a risk threshold — is a small
   build with an outsized reliability/trust payoff, and maps cleanly onto
   Empyralis's existing per-agent tool-approval gaps noted in
   `docs/design/gap-ai-operation.md`.

2. **Sub-agents as a context-hygiene tool, not just a parallelism trick.**
   Both vendors frame subagents primarily as a fix for "context
   pollution"/"context rot" — noisy exploratory output (grep results,
   failed attempts, long file reads) gets contained in a disposable
   subagent thread that returns only a *summary* to the main
   conversation. This is explicitly why Cursor's own indexing/search tool
   spawns an "Explore subagent" for large searches, and why Codex
   documents subagents as best for "read-heavy: exploration, tests,
   triage, summarization" rather than writes. Empyralis's own gap list
   (`gap-ai-operation.md` #6, #9) already flags missing "genuine ephemeral
   multi-agent delegation" and memory consolidation — the Codex/Cursor
   framing suggests the first win isn't parallelism for speed, it's using
   a disposable child thread to keep the parent's context window clean on
   long-running tasks (memory search, repo exploration, log triage).

3. **A durable plan/TODO artifact that survives the session, not just a
   scratch note in context.** Cursor's Plan Mode writes an editable
   Markdown plan, optionally saved to `.cursor/plans/` in the workspace,
   explicitly so "future agents working on the same feature" — a
   different session, possibly a different agent — can resume from a
   known state instead of re-deriving it from chat history. Combined with
   Codex's `AGENTS.md`-driven "how do I validate I'm done" convention,
   the pattern is: persist the plan and the definition-of-done as *files
   in the repo*, not as conversation state that gets compacted/lost. This
   is directly actionable for Empyralis's continuous/24-7 agents, which
   currently have no equivalent of "the task survives a session restart
   or hand-off to a different agent instance" documented in
   `docs/design/memory-context-design.md`.

---

## 5. Source index

**OpenAI Codex (official docs, `developers.openai.com/codex/*` →
`learn.chatgpt.com/docs/*`):**
- [developers.openai.com/codex/subagents](https://developers.openai.com/codex/subagents)
- [developers.openai.com/codex/agent-approvals-security](https://developers.openai.com/codex/agent-approvals-security)
- [developers.openai.com/codex/cloud/environments.md](https://developers.openai.com/codex/cloud/environments.md)
- [developers.openai.com/codex/guides/agents-md](https://developers.openai.com/codex/guides/agents-md)
- [developers.openai.com/codex/cli/reference](https://developers.openai.com/codex/cli/reference)
- [developers.openai.com/codex/llms-full.txt](https://developers.openai.com/codex/llms-full.txt) (full doc dump)
- [openai.com/academy/codex-automations](https://openai.com/academy/codex-automations/) (fetch 403'd; content via search index)

**OpenAI Agents SDK (official):**
- [openai.github.io/openai-agents-python/agents](https://openai.github.io/openai-agents-python/agents/)
- [openai.github.io/openai-agents-python/handoffs](https://openai.github.io/openai-agents-python/handoffs/)
- [openai.github.io/openai-agents-python/guardrails](https://openai.github.io/openai-agents-python/guardrails/)
- [openai.github.io/openai-agents-python/sessions](https://openai.github.io/openai-agents-python/sessions/)
- [developers.openai.com/api/docs/guides/agents](https://developers.openai.com/api/docs/guides/agents)

**Cursor (official docs, `cursor.com/docs/*` and `cursor.com/help/*`):**
- [cursor.com/docs/agent/overview](https://cursor.com/docs/agent/overview)
- [cursor.com/docs/agent/tools](https://cursor.com/docs/agent/tools)
- [cursor.com/docs/agent/plan-mode](https://cursor.com/docs/agent/plan-mode)
- [cursor.com/docs/agent/review](https://cursor.com/docs/agent/review)
- [cursor.com/docs/agent/security/run-modes](https://cursor.com/docs/agent/security/run-modes)
- [cursor.com/docs/context/rules](https://cursor.com/docs/context/rules)
- [cursor.com/docs/context/codebase-indexing](https://cursor.com/docs/context/codebase-indexing)
- [cursor.com/docs/context/mcp](https://cursor.com/docs/context/mcp)
- [cursor.com/docs/cloud-agent](https://cursor.com/docs/cloud-agent)
- [cursor.com/docs/cloud-agent/automations](https://cursor.com/docs/cloud-agent/automations)
- [cursor.com/help/ai-features/background-agents](https://cursor.com/help/ai-features/background-agents)
- [cursor.com/changelog/2-0](https://cursor.com/changelog/2-0) (parallel agents, git worktrees, Composer)
- [cursor.com/changelog/2-4](https://cursor.com/changelog/2-4) (Subagents, Agent Skills)
- [cursor.com/changelog/1-6](https://cursor.com/changelog/1-6) (chat summarization, `/summarize`)

**Secondary (community, flagged inline where used, not treated as
authoritative):**
- [codex.danielvaughan.com — Context Compaction Deep Dive](https://codex.danielvaughan.com/2026/04/14/context-compaction-deep-dive-codex-cli-claude-code-opencode/)
- [getunblocked.com — Codex Context Window](https://getunblocked.com/blog/codex-context-window/)
