# Memory Placement Scope — "Memory lives where the agent lives"

Scoping the founder ruling (2026-07-23): hardware-backed agents (paired
laptop / provisioned VPS) must store memory files ON their own hardware —
the box runs like a headless Claude Code, platform is UI only. Cloud-only
agents keep a small platform-side memory. This document is verdict-first,
file:line-verified against the working tree (some files were mid-edit by
concurrent agents; those reads used `git show HEAD:<path>` and are marked).

**Bottom line up front:** today there is **zero** placement-based storage
routing. Every byte of every agent's memory — file and SQLite, cloud-only
or hardware-bound, `cli_subscription` or not — lives on one disk: the
Empyralis platform server's own filesystem, under one repo-relative
directory. The only placement-awareness that exists anywhere in the
codebase as of this pass is a **same-day, not-yet-wired** cap-size ruling
(40 topic files vs. 5) with an explicit `TODO` admitting the wiring isn't
done. The ruling under review is not a refinement of existing work — it is
a new architecture layer with no prior art in the storage path, though a
real, live, already-proven **transport** (the Gateway) exists to build it
on.

---

## Where memory lives today (per placement, file:line)

### The single physical root, regardless of placement

`server_modules/workspace_context.py:10-11`:
```python
_REPO_ROOT = Path(__file__).resolve().parents[1]
_WORKSPACE_DIR = _REPO_ROOT / ".orion-stack" / "workspace"
```
`agent_workspace_context_dir()` (`workspace_context.py:279-297`) resolves
every agent's context-file directory (`SOUL.md`, `MEMORY.md`,
`memory/files/**.md`, daily notes) to
`.orion-stack/workspace/workspaces/<workspace_token>/agents/<install_token>/`
under that same repo root — **on the machine running the FastAPI backend
process**, i.e. the Empyralis platform server. There is no branch on
placement type anywhere in this function or its caller chain.

The SQLite `memory_entries` layer is the same story, one directory over:
`server_modules/agent_memory.py:21-22`:
```python
_REPO_ROOT = Path(__file__).resolve().parents[1]
_MEMORY_DIR = _REPO_ROOT / ".orion-stack" / "memory"
```
`_memory_db_path()` (`agent_memory.py:302-309`) writes each install's
`memory.db` to `.orion-stack/memory/<workspace_token>/agents/<install_token>/memory.db`
— a real, physically-separate file per `(workspace_id, agent_install_id)`
(this per-file separation is what Part 27 of PLATFORM-MAP.md verified as
the cross-agent isolation boundary — see below), but still on the platform
server's disk, never the customer's.

**Confirmed by checking all three placement types against this same
function — none diverge:**
- **Cloud-only** (`hardware_access = "none"`, no Gateway) — obviously
  server-side; nothing to compare against.
- **Paired computer** (`hardware_access = "gateway"`) — `agent_workspace_context_dir`
  and `_memory_db_path` are called identically; `hardware_access` is never
  read by either function or by anything in their call chain
  (confirmed by grep — zero hits for `hardware_access` in
  `workspace_context.py` or `agent_memory.py`).
- **Cloud VPS** (`hardware_access = "vps"`) — same: a VPS install still
  runs the `empyralis-gateway` Node process (VPS is a hosted machine
  running the identical Gateway software as a paired laptop — confirmed
  via `fleet_tools.py:156-174`'s `resolve_hardware_access`, which treats
  `gateway`/`vps` as the same three-way vocabulary with no separate
  storage-routing logic), and its memory is stored exactly the same
  server-side way.

### The one placement-aware thing that exists — a cap, not a location, and not wired

`workspace_context.py:38-100` (comment + constants) records a **same-day
founder ruling** that is real but incomplete:
```python
MEMORY_TOPIC_FILE_MAX_COUNT = 40                 # hardware-backed default
MEMORY_TOPIC_FILE_MAX_COUNT_CLOUD_ONLY = 5        # cloud-only cap
```
The comment at `:60-83` states plainly that selecting between the two
"requires knowing the CALLING agent's placement/hardware binding," that
resolving it needs a SQLAlchemy+Postgres async lookup this module
deliberately avoids (it is a sync, filesystem-only primitive used from
sync call sites and tests with no DB configured at all), and ends with an
explicit `TODO(placement wiring)`: `write_workspace_context_file` accepts
an optional `topic_file_max_count` override (`:620-640`) but **every
current caller omits it**, so every agent — cloud-only or hardware-bound —
gets the generous 40-file cap today. Confirmed empty: grepping
`server_modules/tests/test_workspace_context_files.py` (12 tests) finds
no test exercising `topic_file_max_count` or the cloud-only constant.

**This means the founder ruling, as scoped here, is not "extend an
existing placement-routed system" — it is greenfield.** The one piece of
placement-awareness in the codebase governs a number (how many files), not
a location (which disk).

### `cli_subscription` doesn't change this — verified, and it's the sharpest counter-evidence for "memory lives where the agent lives" today

The founder's own reference case is Claude Code: memory sits on the user's
laptop because the CLI *reads local files itself*. Checked whether
Empyralis's `cli_subscription` mode — which does run the real `claude`/
`codex` binary on the customer's own box (PLATFORM-MAP.md Part 26) —
already does this. It does not.

`empyralis-gateway/src/llm/cli-runner.ts` `buildInvocation` (`:244-260`)
passes `--system-prompt <text>` and a fully-flattened message list over the
wire; PLATFORM-MAP.md Part 26.5 confirms directly: *"`sage_agent_runtime_service.py`
already re-flattens the **entire** conversation into one prompt every
single turn"* and the wire protocol carries no stable per-conversation
identity, only `runtime`/`model`/`messages`/`timeoutMs`. **The box's own
CLI process never reads `MEMORY.md` or any local file for context — the
server assembles the entire memory brief server-side and ships it down as
prompt text on every turn**, the same as for a pure-cloud agent. Grepping
`empyralis-gateway/src/` for `MEMORY.md`, `memory_read`, `memory_write`
returns zero hits — confirmed, not inferred. Even the one placement type
that already runs a real local CLI treats that CLI as a stateless
generation engine, not a Claude-Code-style local agent with its own
filesystem memory.

### How the brief reaches the model, and what that costs today

`sage_instruction_compiler_service.py:335` `build_root_memory_brief_sections`
formats an already-in-memory `context_files` dict — the actual disk read
happens upstream and is **cached per (workspace_id, session_id)**
(`:307-324`, `_cached_build_root_memory_brief_sections`): the first turn of
a session pays the read cost, every subsequent turn in that session reuses
the cached result until `invalidate_session_context_cache` fires (context
writes, daily reset, `/new`). This caching detail matters directly for
the latency section below.

---

## The gateway seam

**Verdict: a real, live, already-proven remote-execution seam exists and
memory tools could ride it — but it has zero agent-level scoping today,
which is exactly the class of gap Part 27 of PLATFORM-MAP.md found and
fixed for the *existing* memory tools.**

### The capability that would carry it: `filesystem.read_write`

`empyralis-gateway/src/shell/runtime.ts` (`GatewayShellRuntime`) exposes
two capabilities end-to-end: `shell.execute` and `filesystem.read_write`
(`:11-14`). Routed through `capability-router.ts:33,167-178` alongside
`llm.generate` (the same executor list `cli_subscription` turns already
use). `filesystem.read_write` (`:216-300`) supports `read`/`write`/
`append` modes; in **sandbox mode** (the default — Docker required) it
bind-mounts a real host directory,
`<gateway-stateDir>/mounts/<mount>/<workspaceId>/`, into an ephemeral,
`--rm`, network-`none`, capability-dropped container (`docker-sandbox.ts:27-45`
hardened flags), so writes genuinely persist on the customer's own disk,
not just inside the throwaway container. In **full_access mode** (opt-in,
both a local box flag and a server-asserted authorization required —
`runtime.ts:71-101`) it reads/writes the host filesystem directly.

**This is not a paper capability — it is already wired into the live tool
loop**, one level up from the Gateway itself:
- `server_modules/gateway_execution_service.py:61-84`
  `_normalize_gateway_capability` maps `shell.execute`, `filesystem.read`,
  and `filesystem.write` all onto this one Gateway capability.
- `server_modules/skills_service.py:2881-2894`
  `_gateway_capability_for_direct_local_tool` performs the identical
  mapping for the model-facing `file`/`shell` direct-tool connectors,
  reached from the live per-turn tool dispatch (`skills_service.py:3764,3873,4007`
  call `local_tool_executor.shell_execute` / dispatch `shell.execute`
  through this path today for hardware-placed agents).
- `server_modules/hardware_runtime_target_resolver.py:25-68`
  (`_LOCAL_HARDWARE_ACTION_PREFIXES`/`_LOCAL_HARDWARE_ACTION_IDS`) already
  lists `filesystem.*` and `shell.*` as "requires local hardware" actions,
  and `resolve_runtime_target` (`:117-134`) already implements the exact
  placement-branch this ruling needs: if the action requires local
  hardware and a Gateway is live, route to `user_device_gateway`; if not
  live, return a structured **offline** result
  (`AGENT_COMPUTER_OFFLINE_ERROR`) rather than crashing the turn.

**This existing precedent is the strongest argument for "extend the seam,
don't rebuild it."** The routing decision (hardware-required action → is a
Gateway live? → dispatch or offline-degrade) is already built, tested by
virtue of being live for the `file`/`shell` connectors, and sits one
function call away from being reused for memory tool dispatch.

### What the seam does NOT have: agent-level isolation

Grepped `gateway_execution_service.py` end-to-end for `agent_install_id`
and `mount` — **zero hits**. `executeFilesystem` in `runtime.ts` scopes
storage by `workspace_id` + an arbitrary `mount` string the *caller*
supplies (`:130-131`, `sanitizeMountName`/`sanitizeWorkspaceId`) — nothing
server-side or Gateway-side currently forces that mount name to be
agent-install-scoped. Compare this to the file/SQLite memory layers today,
where isolation is either enforced by a hard filesystem-path check
(`agent_memory_tools.py`'s `_resolve_safe_path`, verified in PLATFORM-MAP.md
§27.3 to correctly reject `..` and symlink escapes) or by physically
separate files per install (§27.4). **Routing memory writes through
`filesystem.read_write` as-is, with multiple agents sharing one box, would
recreate exactly the class of cross-agent leak PLATFORM-MAP.md's Part 27.8
flagged as CRITICAL for connector credentials (vector D) — a resolver that
picks "whichever `mount` string was passed" instead of enforcing identity.**
Any extension of this seam for memory MUST add install-scoped path
derivation (e.g. mount = `agent_install_id`, never caller-suppliable) as a
hard, non-bypassable rule — not an afterthought.

### The real cost of any gateway round trip, measured

PLATFORM-MAP.md Part 26.3/26.4 measured the `llm.generate` capability
(same durable-dispatch, enqueue-then-flush mechanism `filesystem.read_write`
would use) at **~4.4s per turn on the fast/warm path**, with a **40-second
worst-case deadline** (`sage_agent_runtime_service.py:1144`,
`durable_deadline_seconds=40`) before the call fails outright. This is not
a network call to a nearby colocated service — it is enqueue → wait for a
WSS flush cycle → real cold-spawn/process work on a customer's own,
possibly-underpowered machine (Part 26.4 notes the prod-box Gateway itself
shares a 1 vCPU/1.9GB droplet). Any memory read that goes through this
same mechanism inherits this latency profile, not a database-query-speed
one.

---

## Offline + loss + migration semantics

### Turn-time read cost — the sharpest open tension

The founder's Claude-Code analogy assumes memory reads are as cheap as a
local filesystem read. On a customer's own box reached over the internet
through a durable dispatch queue, they are not. Two separate load-bearing
facts collide here:

1. MEMORY.md injection happens **before the model call**, synchronously,
   as part of prompt assembly (`sage_instruction_compiler_service.py`,
   PLATFORM-MAP.md Part 16) — it is not a mid-turn tool call with a
   natural request/response/error shape the way `filesystem.read_write`
   is used today.
2. The existing offline precedent (`hardware_runtime_target_resolver.py`)
   is built entirely around **tool calls**, which fail gracefully and let
   the turn continue with a degraded/offline JSON result. There is no
   equivalent fallback shape for "the system prompt itself couldn't be
   assembled because the box is offline."

**Recommended answer:** do not make MEMORY.md a synchronous per-turn
gateway round trip at all. Two credible shapes, both requiring a real
build decision, not a default:
- **(a) Box-authoritative with a platform-side read cache.** The box
  remains the only writer and the source of truth; every write also
  pushes (or the platform pulls-and-caches on a schedule/webhook) a copy
  into the existing `.orion-stack` cache location purely for turn-start
  speed. This preserves "memory lives where the agent lives" for
  ownership/loss semantics while keeping turn latency at today's level —
  at the cost of a cache-staleness window between a box-side edit and the
  next cached read, and it means the platform still holds a durable copy,
  which is arguably against the spirit of "the box dies, memory dies."
- **(b) Fully box-authoritative, no cache — genuinely Claude-Code-like.**
  Every session-start pays a real gateway round trip (the existing
  per-session cache in `sage_instruction_compiler_service.py:307-324`
  already limits this to once per session, not every turn — meaningfully
  softens but does not eliminate the cost). Box offline at session start
  must then have an explicit, product-visible behavior: **degraded turn**
  (reply without memory context, clearly flagged) is the only option
  consistent with "box down = memory unavailable," not a failed turn —
  matching the existing `AGENT_COMPUTER_OFFLINE_ERROR` pattern's spirit
  (structured offline result, turn continues) rather than inventing a new
  failure mode.

This is a real product tradeoff (latency/complexity vs. purity of "only
lives on the box") that needs an explicit founder call, not just an
engineering default — flagging it rather than picking silently.

### Box offline mid-conversation (not just at session start)

For an already-loaded session (cache hit), an offline box does not block
the turn at all under the existing cache design — the memory brief is
already in the cache key. The problem is scoped narrowly to session start/
cache-miss, which somewhat de-risks the "agent totally unreachable"
scenario the task worried about — but a NEW session-start (e.g. `/new`)
while the box is offline is exactly the failure mode above with no
current handling to extend from.

### Loss semantics

Today: server disk loss is the only loss vector (backed by whatever the
platform's own disk/backup posture is — not audited in this pass, out of
scope). Under the ruling: box death becomes a real, first-class loss
vector, matching Claude Code exactly (uninstall Claude Code, CLAUDE.md
memory is gone unless the user backed it up themselves) — the founder's
own reference point already accepts this as correct behavior, not a gap.
**Open question, not yet answered anywhere in the codebase:** does the
owner get any export/backup affordance before that acceptance is asked of
them, or is silent, permanent loss on de-pair/hardware-failure acceptable
as shipped? No export mechanism for hardware-side memory exists to check
against (there is no "box side" memory yet at all). Recommend: at minimum,
the existing MemoryTab UI (`frontend/lib/workspace/fleet/tabs/MemoryTab.tsx`,
PLATFORM-MAP.md Part 16) should surface "this agent's memory lives on
[device name]; if it's disconnected/removed, this memory is gone" — an
honesty affordance rather than a silent trap, consistent with this
codebase's own stated ethic (the "starter scaffold" honesty banner,
`is_default_context_content()`, is the exact same kind of surfaced-truth
pattern already shipped for a different gap).

### Migration of existing server-side memories

Zero migration tooling exists. Any agent already running with
`hardware_access = "gateway"`/`"vps"` today has real memory content sitting
under `.orion-stack/workspace/.../agents/<install_id>/` server-side (not
synthetic — this is the live MEMORY.md/topic-file/SQLite content those
agents have already accumulated). Implementing the ruling without a
one-time migration step means those agents' existing memory either (a)
silently stays server-side forever (contradicts the ruling for anyone
already using the product) or (b) is dropped/needs manual re-entry on
first box-routed write (data loss). This needs an explicit one-time
copy-to-box step gated on the box coming online and a Gateway
`filesystem.read_write` write succeeding, with the server-side copy kept
read-only/archived rather than deleted until the box-side copy is
confirmed — not designed here, flagged as a required build item.

### Per-agent isolation with multiple agents on one box

PLATFORM-MAP.md Part 27 proved the *current* file/SQLite layers hold this
invariant (19/19 tests, per-file physical separation). Extending storage
onto the Gateway's `filesystem.read_write` seam **must** re-derive the
same invariant there, since (per "What the seam does NOT have" above) it
does not hold today. Concretely: mount/path derivation for any memory
write dispatched through the Gateway must be a server-computed function of
`agent_install_id` alone, never a caller-suppliable argument threaded
through `arguments.mount` the way the existing `file`/`shell` connectors
allow today — otherwise this becomes vector D (PLATFORM-MAP.md Part 27.8)
again, for memory instead of Stripe keys.

---

## Smallest real implementation

**Verdict: seam extension, not rebuild — with three concrete new pieces,
none of them small, all of them scoped.** The transport
(`gateway_execution_service.py` → `capability-router.ts` →
`GatewayShellRuntime.filesystem.read_write`) and the placement-routing
pattern (`hardware_runtime_target_resolver.py`'s "requires local hardware →
Gateway live? dispatch : offline-degrade") already exist and are already
live for a structurally identical case (the `file` connector). Building a
parallel system would ignore working, proven infrastructure for no
benefit.

The smallest real version, in the order it would need to be built:

1. **Agent-scoped path derivation on the Gateway seam.** Before anything
   memory-specific: close the isolation gap above. A memory-write call
   must resolve its on-box path from `agent_install_id` server-side, never
   from a client-suppliable `mount`. This is prerequisite infrastructure,
   not memory-specific work, and it's the one piece with a real security
   consequence if skipped.
2. **Placement-routed dispatch point for memory reads/writes.** One
   function, following the exact shape `hardware_runtime_target_resolver.resolve_runtime_target`
   already established: given `agent_install_id`, resolve
   `hardware_access` (`fleet_tools.resolve_hardware_access`, already
   exists) → `none` stays on today's `.orion-stack` path unchanged;
   `gateway`/`vps` with a live Gateway dispatches through
   `filesystem.read_write` to the box; `gateway`/`vps` with no live
   Gateway returns the same structured offline result the `file`
   connector already returns today (reuse `AGENT_COMPUTER_OFFLINE_ERROR`,
   don't invent a new shape). This one dispatch point is what every memory
   tool (`memory_read`/`memory_write`/`memory_list`/the SQLite layer) and
   the turn-start brief-assembly path would call through.
3. **A session-start caching strategy for the brief-assembly path**,
   decided per the "Turn-time read cost" tension above — this is a real
   design decision (cache vs. no-cache) that gates whether turn latency
   for hardware-placed agents changes materially, and needs to be made
   explicitly rather than defaulted.
4. **One-time migration** for agents that already have server-side memory
   and are already hardware-bound, per the section above.
5. **Cloud-only cap wiring** (`MEMORY_TOPIC_FILE_MAX_COUNT_CLOUD_ONLY`,
   already defined, unwired) is a small, independent, low-risk piece that
   could ship on its own before any of the above — it only requires
   resolving placement at the `write_workspace_context_file` call site in
   `skills_service.py` (already flagged in the `workspace_context.py:76-78`
   TODO as the natural place to do it) and passing the existing override
   parameter through. Worth doing first as a cheap, real, visible step in
   the right direction while the harder box-routing work is scoped.

**What this smallest version deliberately does NOT attempt:** a
box-authoritative index the platform never caches (deferred to the latency
decision above), true zero-latency memory reads for hardware agents
(structurally impossible over a WAN round trip without a cache), or
solving loss/export UX as part of the storage-routing work (a separate,
smaller UI task once the routing exists).

---

## Risks

- **Latency regression is the highest-probability risk**, not a hypothetical
  one — `llm.generate`'s own measured 4.4s/40s-worst-case numbers on the
  *exact same dispatch mechanism* this would reuse are the calibration
  point. Shipping session-start memory-on-box without deciding the caching
  question first risks turning every "/new" or fresh session for a
  hardware-placed agent into a multi-second-to-timeout stall before the
  model even starts.
- **Cross-agent leak recreation** if agent-scoped path derivation (item 1
  above) is skipped or done casually — this is not a new risk category,
  it is the *same* bug class Part 27.8 rated CRITICAL for connector
  credentials, now available on a new attack surface (memory files)
  because the underlying seam was never agent-scoped to begin with.
- **Silent data loss on de-pair/box failure** if no export/backup
  affordance ships alongside the ruling — acceptable in principle
  (matches Claude Code), but currently the codebase has zero honesty
  surfacing for it (contrast with the "starter scaffold" banner pattern
  already shipped elsewhere for a different honesty gap).
- **Migration debt compounds** the longer this waits — every day more
  agents accumulate real server-side memory under `hardware_access =
  "gateway"/"vps"` with no migration path defined, making the eventual
  one-time copy larger and riskier the later it's built.
- **`cli_subscription`'s stateless-CLI architecture is a bigger rebuild
  than it looks from the founder's framing.** "The box runs like a
  headless Claude Code" implies the box's own CLI process reads its own
  memory files — but today, per the verified trace above, the box's CLI
  never touches local files at all; the server flattens and ships
  everything as prompt text every turn. Actually closing that gap (CLI
  reads `MEMORY.md` itself, the way real Claude Code does) is a
  fundamentally different, larger change than "route memory tool calls
  through the Gateway" — worth naming explicitly so the ruling isn't
  scoped as smaller than it is if that literal reading is what's wanted
  for `cli_subscription` agents specifically.
