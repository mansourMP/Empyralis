# Proposal: retire the “Sage” name

## Executive recommendation

Do not perform a global search-and-replace. “Sage” currently names several
different behaviours. Rename internal code in small, mechanical batches, but
keep public routes and persisted identifiers as compatibility contracts unless
there is a separate product-versioning decision.

The recommended vocabulary is:

| Current concept | Recommended name |
|---|---|
| Workspace-level agent surfaced as “Ask AI” | workspace assistant |
| Prompt/model/tool/persistence execution | agent turn runtime |
| Shared command and channel dispatch | agent command dispatcher / agent turn adapter |
| Workspace memory feature | assistant memory |
| Profile, skills, services, heartbeat and proof-log surfaces | assistant profile, assistant skills, assistant services, assistant health, assistant audit logs |
| Hosted channel integrations | hosted channel agent integration |

The public product name remains Empyralis. “Assistant” describes the
workspace-level product capability; “agent” remains the conventional technical
term for an autonomous runtime or deployed/channel worker.

## 1. Inventory by behaviour

### Workspace assistant

Every workspace receives a logical master agent, with private conversations per
workspace member. The UI presents this as “Ask AI”. It is not a named persona
any more, so “workspace assistant” is clearer than a proper name. Existing
code also uses `agent_kind = 'master'` and registry/bootstrap logic for this
object.

### Agent turn runtime

`sage_agent_runtime_service` and its contract/adapter neighbours assemble the
prompt envelope, load context, invoke the model, dispatch tools, enforce policy,
and persist the turn. This is runtime infrastructure, not the workspace
assistant itself. `agent_turn_runtime` (or `agent_turn_service` where the
module is a service façade) is conventional and distinguishes it from the
logical agent.

### Command, channel, and reply plumbing

The command dispatcher normalizes commands and status/error outcomes across web
and external channels. The turn adapter is the common channel-to-runtime seam;
the reply dispatcher handles retries and outbound formatting. These should be
named for their boundaries: `agent_command_dispatcher`, `agent_turn_adapter`,
and `agent_reply_dispatcher`.

### Assistant memory

The memory API/service manages workspace-scoped entries, categories, export,
wipe, and the context block supplied to turns. The implementation currently
uses a workspace-scoped `sage_memory.json` path and literal action names. The
behaviour is ordinary assistant memory, not a separate Sage subsystem.

### Assistant configuration and observability surfaces

Profile, skills, services, heartbeat, doctor, transparency, and proof-log
modules expose configuration, capability discovery, runtime health, and audit
evidence for the workspace assistant. These should be split by function rather
than given one replacement stem: `assistant_profile`, `assistant_skills`,
`assistant_services`, `assistant_health`, and `assistant_audit`.

### Hosted channel integrations

Telegram/Discord and the personal-channel bridge connect an external channel to
the assistant/agent runtime. “Hosted channel agent” or the specific provider
name is more informative than “Sage”; provider-specific route names should be
reviewed independently rather than swept into the core rename.

### Cosmetic and historical vocabulary

Frontend CSS selectors, screenshot names, comments, test filenames, docs, and
dead/archive references account for a large portion of the measured ~28,700
occurrences. They are low-risk after live contracts are mapped, but they must
not be treated as proof that a runtime path is active. The repository’s
“built, tested, and never wired” warning requires caller tracing before
assigning semantic importance.

## 2. Blast-radius map

### Internal identifiers — reversible and low risk

Rename Python/TypeScript module names, imports, functions, classes, constants,
CSS selectors, comments, and test names in reviewed batches. This includes the
24 `server_modules/sage_*.py` modules, but each module should receive a
behavioural name rather than a universal `assistant_` prefix. These changes are
reversible by reverting the commit and do not require a database migration.

The risk is behavioural rather than textual: imports, dynamic imports, module
registries, string-based test lookups, event names, log/error buckets, tool
names, and serialized metadata can silently change. Every batch needs a
literal-string audit and caller/registration audit.

### HTTP routes — externally breaking

The backend currently exposes routes including `/api/sage/chat`,
`/api/sage-chat/attachments`, `/api/sage-memory`, `/api/sage-profile`,
`/api/sage-skills`, `/api/sage-capabilities`, `/api/sage-services`,
`/api/sage-heartbeat`, proof-log routes, and hosted-channel routes. The
frontend workstation client and fleet UI call these paths directly; tests and
the separately deployed gateway/desktop clients may be older than the backend.

Do not remove or silently repurpose the old routes. If new routes are desired,
add new conventional routes as aliases, have both paths call the same handler,
instrument old-path usage, update clients, and retire old paths only after the
oldest supported client/version window has expired. Route aliases are a deploy
compatibility concern, not a migration.

### Stored database schema and row values — migration risk

The research sweep found historical migration comments and identifiers using
Sage, but no evidence that a table itself is named `sage_*`. Confirm this with
the production schema inventory before changing any table or column.

Any table/column rename is a real migration. If the object carries
`tenant_id`/`workspace_id`, first deploy the preflight/RLS coverage changes with
both old and new table-name keys, then rename the database object, preserving
the old key for rollback. Update `migrations/enable_rls.sql`, policy/index
references, boot checks, and all SQL in the same reviewed change set. Apply as
the app database role and re-run the RLS checks.

Row values are separate migrations. `sage_main_agent`, `sage-main`, serialized
JSON keys, filesystem paths, audit action names, and similar values already
written to production cannot be changed by renaming code. Use dual-read/dual-
write or an explicit backfill with counts, then remove legacy writes only after
all readers support the new value. Do not change `sage-main` casually: it is a
thread-routing key and changing it can split or merge conversations.

### Human-facing strings — behaviour and UX risk

“Ask AI” should remain the customer-facing label unless product explicitly
chooses a different label. The memory wipe phrase `WIPE SAGE MEMORY` is a
literal confirmation contract. Keep accepting it during a transition; if the
displayed phrase changes, accept both old and new phrases, log the normalized
operation, and test rejection of near misses. Do not infer behaviour from
prose matching; stable operation codes should drive classification.

### Environment variables and deployment configuration

No rename should be assumed safe merely because it is an environment variable.
Inventory `os.getenv`/deployment manifests/compose, systemd, nginx, packer,
desktop, and gateway configuration before changing any `SAGE_*` key. Use a
dual-read period with the new variable preferred and the old variable as a
deprecated fallback, emit a startup warning, then remove the old key only in a
separate release. Route/base-URL changes also require gateway and desktop
version-skew testing.

## 3. Independently deployable sequence

### Step 0 — freeze the contract inventory

Record the exact baseline occurrence count using the same exclusions as the
research file. Enumerate route registrations and callers, module registries,
dynamic imports, database tables/columns/values, JSON/file paths, environment
keys, and external clients. Capture baseline unit/test collection and the known
failing-test signature. No production change.

### Step 1 — add stable semantic vocabulary internally

Introduce behavioural names for new code paths and compatibility aliases for
old Python imports/constants where needed. Move callers in small domains:
runtime, dispatcher/adapter, memory, then profile/skills/services/health/audit.
Do not change route strings, persisted values, tool protocol names, or human
confirmation phrases in this step. Deploy and revert are both ordinary code
deploys.

### Step 2 — remove stale internal references

Rename tests, CSS selectors, comments, docs, screenshot names, and dead code
only after Step 1 proves the live path. Retain historical migration comments
when they describe existing production values; annotate them rather than
rewriting history. Verify no live caller, registry, or protocol field was
changed by the cosmetic sweep.

### Step 3 — optional route alias release

Add conventional route aliases while retaining every old route. Deploy the
backend first, then release frontend/gateway/desktop clients that prefer the
new paths. Monitor old-path traffic and errors. Rollback is safe because old
routes remain. There is a deploy ordering constraint: clients must not switch
to new paths until the backend alias release is live.

### Step 4 — optional persisted-value migration

Only if the value itself has user-visible or operational cost, introduce
dual-read/dual-write, backfill in a separately reviewed migration/job, verify
row counts and per-workspace invariants, then stop writing the legacy value.
For scoped tables, apply the RLS two-part rule before any table rename. Keep a
rollback reader and old-value support until the migration is proven.

### Step 5 — deprecation removal

After client support windows, observed old-route traffic reaches zero or an
explicitly accepted threshold, and stored-value readers no longer need the
legacy form, remove aliases and compatibility code in separate changes. This
step is intentionally not required to achieve most of the naming benefit.

## 4. Correctness verification

At every step:

- Compare the same occurrence-count command and classify remaining matches as
  live contract, persisted historical value, compatibility alias, or stale
  documentation.
- Run the same test collection and report the delta from the known baseline,
  not raw failure totals. Add focused tests for route registration, module
  import/registration, thread keying, error classification, tool dispatch,
  memory export/wipe, and per-workspace isolation.
- Trace each changed entry point from the real frontend/channel/gateway caller
  to the runtime and persistence boundary; existence of a function or test is
  not evidence it is wired.
- For routes, exercise both old and new paths with an old-client-shaped request
  and a current client, including attachments, auth, streaming, errors, and
  route-specific query parameters.
- For stored values, take before/after counts grouped by workspace and agent,
  verify no duplicate or orphaned threads, and test rollback reads against old
  rows.
- Run boot/preflight and RLS coverage checks against a non-superuser app role;
  if a scoped table name changes, verify both the forward and rollback boot
  paths.
- Search for literal matching of renamed messages, channel keys, thread IDs,
  action names, and confirmation phrases. Replace prose-based classification
  with stable codes where the rename exposes it, but treat that as a separate
  behavioural change and test it independently.

## 5. What not to rename

- Do not rename the product Empyralis or the established customer label “Ask
  AI” as part of this internal cleanup.
- Do not rename public `/api/sage-*` routes in place. Compatibility aliases
  provide most of the value without breaking separately deployed clients.
- Do not rename `sage-main`, `sage_main_agent`, or historical row values merely
  to make grep output clean. They are persisted protocol/data values and need a
  measured migration, not a textual edit.
- Do not rename database tables/columns unless the schema itself is genuinely
  misleading. A cosmetic scoped-table rename costs a migration, RLS/preflight
  coordination, and rollback complexity.
- Do not rename provider-specific hosted-channel routes or protocol fields
  solely because a nearby Python module has the old stem. Provider identity and
  wire compatibility are more informative than uniformity.
- Do not rewrite historical migration comments or archived evidence as though
  the old name never existed. Preserve provenance and annotate current
  terminology.

## Decision

Approve Steps 0–2 as the safe core. Treat Steps 3–5 as optional compatibility
and lifecycle work requiring explicit release-window ownership. The practical
target is to eliminate “Sage” from new and internal code while preserving old
routes, persisted values, and deployed-client compatibility until there is a
separate, evidenced reason to migrate them.
