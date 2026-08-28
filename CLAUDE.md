# Empyralis — standing decisions

**This file loads into every agent's context on every turn. It stays under 200 lines.**
Only three things earn a line: a rule someone would break if they didn't know it, a
founder decision that would otherwise be silently reversed, or a trap that costs real
time and is invisible in the code. Reasoning goes in git history. Findings go in Linear.
A rule that has a drift test lives in the test and is named here in one line. Design,
audit and gap documents are not kept — they go stale and get cited as present truth.
When you learn something durable, add ONE line. A paragraph is archaeology.

**A line here is a claim with a timestamp, not live state.** Grep the symptom against
`git log --oneline --all` before re-diagnosing anything this file calls unfixed.

## How to work

- Lead with the verdict — one line, then the detail.
- Cofounder, not status report: find a real problem and fix it. Never report gaps and sit still. If the call is genuinely his, ask ONE question with your recommendation — never a menu.
- Real markdown tables, never ASCII art. Code blocks are for code, paths and command output.
- Linear is the system of record for issues, plans and status.
- Never weaken an assertion to go green, and never pin broken behaviour as intended.
- Verify before reporting: read the diff, run the test, call the real function yourself.
- Never add an AI co-author trailer to a commit.

## Working agreements

- **One agent = one worktree = one branch.** Dispatch subagents with `isolation: "worktree"` — a subagent cannot tell whose dirty files it is looking at. This has destroyed uncommitted work twice.
- **Never `git stash`.** Worktrees share one stash stack, and a stash mid-merge silently clears `MERGE_HEAD`, producing a single-parent commit that re-conflicts forever. Use `git diff > /tmp/x.patch` + `git checkout --`.
- **Never prune worktrees while an agent is live.** "Merged" is a fact about git; "finished" is a fact about a process only the orchestrator can see.
- **Isolation covers files and git — not ports, browser tabs, the scratchpad, or `~/.empyralis/state`.** Pick a non-default port up front, scope browser calls to your own `tabId`, give logs a session-unique name, and never kill a process on a default port assuming it is yours.
- Worktree setup: symlink `venv/` and `empyralis-runtime-kernel/target/`. Do NOT symlink `frontend/node_modules` — Turbopack refuses a symlink outside its pinned root; use `npm run dev:e2e` (webpack) or a real install.
- Never commit `frontend/next-env.d.ts` or `frontend/tsconfig.json`.
- **Never create, edit, move or copy a secret** — an `.env`, an OAuth secret, an API key, a token. Not into a worktree, not to a gitignored path, not "just for this throwaway checkout". If running something needs a credential you don't have, ask for it in the person's own shell. One well-guarded secret becomes five unguarded copies exactly this way.
- A branch is merged or deleted. Left behind, its work gets rebuilt from scratch by someone else.
- **When a system is being replaced, stop building on it.** An outgoing system's defect is one line to the founder, never a fix. There are no production users on the gateway — do not invent a customer base to justify the work.
- Adopt a system whole or argue against adopting it; never curate a subset and call it adoption.
- Delete a superseded work order, never archive it. A stale plan gets executed.
- Commit any document you expect an agent to read, and put load-bearing constraints in the prompt — a prompt always arrives, a file reference may not.

## Testing

- **Seed your own data. Never the founder's account, database, session, or credentials.**
- **Never test against his personal Claude subscription or his real Telegram bot. Not once.** Company-billed API credentials are fine.
- The one blessed stack bring-up is `frontend/scripts/start-e2e-backend.sh`. `DATABASE_URL` is always explicit and disposably named; never copy a value you found somewhere.
- Boot with placeholder provider keys; never set `EMPYRALIS_ALLOW_LOCAL_STACK_LIVE_SECRETS=true`. Pin `EMPYRALIS_E2E_STATE_HOME` to an existing dir or every restart invalidates every live session.
- A test may never reach a live LLM provider (`conftest.py` socket + subprocess guard).
- On an empty database: boot once (it crashes in preflight — expected; its job is to lazily create the base tables), apply `migrations/*.sql` twice, boot again.
- Nothing gates on the Python suite; it carries ~450 known failures and is not a gate. Run it alone: `DATABASE_URL= venv/bin/python -m pytest server_modules/tests`.
- Python tests passing is not evidence the UI works. Drive it in a real browser: click it, watch the network tab, read the console.
- Tests still write to the developer's real `~/.empyralis/state` — nineteen modules bake the path at import time.
- Confirm an empty state against the DATA before "fixing" it. A decode throw eaten by `try?` looks identical to an empty list.

## Positioning

- **The WORKSPACE is the product.** Agent-hosting for others is the second thing, never the headline. Empyralis is the owned-context layer for a team, with execution attached — models are rented and commoditizing; accumulated context is not.
- **Never say "AI".** Linear says "track your issues, work with your teammates". Lead with what a team owns and does.
- Never build a coding surface. Claude Code / Codex / Cursor are the execution layer; we are the layer above.
- **The board is the product; chat is only input. Nothing of value may exist only in a conversation.**
- **The platform is NOT a chat product.** No message composer anywhere. Conversation happens in channels — you watch an agent work, you never type at it. Keep the live tool-call and reasoning streaming; that is the reason to open the platform at all.
- The workspace assistant is **"Ask AI"** and it is per-user. The old name is dead — remove it wherever it survives.
- "Agents working alongside a team" and "hosting agents for others" are one product. The only axis that differs is who may talk to the agent (`audience: owner | external`).
- Execution locality: identity lives in the workspace, execution happens where the agent is placed, the connection carries only jobs and results. Move the work; never patch the connection.

## Founder decisions — do not silently reverse

- **An agent belongs to the WORKSPACE, never to a project.** Do not re-nest agents under projects and do not add an Agents tab to a project.
- **Context is GRANTED per agent, never inherited**, in `workspace_agent_installs.metadata["context_project_ids"]`. Four states: key ABSENT = legacy (pre-grant behaviour), `[]` = granted nothing, `[ids]` = those only, unreadable = no reach at all (never falls back to legacy). Read many, write one. The model cannot widen its own grant — the only writer is the owner-gated `PUT .../context-projects`.
- **Hardware attaches to its owner, never to the project.** Sharing a machine is an explicit per-machine opt-in, default off; an agent with no opted-in hardware runs cloud-side, which is a clean degradation and never an error.
- **Conversations are private. Work is shared.** Non-owners never see personal or self-chat threads.
- **No approval system.** No approve/deny buttons, no approval-pending states. Guardrails are named and narrow, never a blanket gate.
- **No tool tier.** Allow every tool by default; reserve only `fleet__*`, `goal__*` and `empyralis_configure_agent` to the owner. The membership test is "does this administer the platform itself", never "could this be misused". State the consequence plainly: anyone who can message an agent can do anything that agent can do — the channel gates decide who may message it.
- **Destructive-action awareness is judgment, not a mechanism.** Guidance in the system prompt; never a command blocklist. `rm -rf ~/Documents` is the same string whether or not he asked for it.
- **Docker chooses HOW a command runs, never WHETHER.** No Docker → the command runs on the host and works. macOS never uses Docker at all, so never blame Docker in the host-mode copy. `host` is not `full_access`; full_access keeps its unchanged two-part opt-in. `command-policy.ts` (blocked commands, protected paths) is checked in every mode.
- **Channels: Telegram (recommended) and Slack. Discord is OUT.** Both work with no paired hardware; Discord would need a standing per-customer WebSocket.
- **Windows is out. macOS + Linux only.**
- **Payment processor is Polar, not Stripe.** Stripe is not available as a standalone merchant account in Uzbekistan; Polar is Merchant of Record. The Stripe integration in this repo is dead code — read it for the credit logic, swap the processor calls.
- AWS provisioning is deliberately unwired until the founder is in SF. Its 500 is the intended state, not a defect. DigitalOcean is the working provider.
- Projects hold members directly. There is no Teams layer.
- **Row counts are never capped** — context is the product, never the paywall. Only bytes and seats are capped.
- A customer may upload notes and pictures; never code, archives or binaries. The extension decides and the bytes refute. SVG is not an accepted picture.
- **"Channels" is ONE system.** If adding a channel requires an Empyralis code change, it is wired wrong. Derive the channel set from the transport, never hand-list it.
- Agent creation is four steps — identity+placement → brain → channels → apps — and **placement is first**, because step 2 cannot honestly offer subscription/local modes without it. No project field. Channels blocks *advancing*, but leaving is never blocked: the exit offers "Leave it for now" beside "Delete agent", and no label says "skip".

## Product laws

- **Best, not most.** Match the discipline of the tools we're measured against, not their feature surface.
- **A surface must earn its place.** If it can live one level down, it should.
- **No dead controls.** A control that cannot be used in the current state is not rendered. A caption admitting it does nothing is a design bug.
- **After an action, say what actually happened.** "failed" and "may have succeeded, but I lost track of it" are different facts; so are "empty" and "I could not load this". Reporting failure on success is the worst case, not the safest — the person retries something already done. When you genuinely cannot tell, verify the real state before speaking, or say "couldn't confirm, safe to retry".
- A professional tool labels; it does not lecture. A setup control *does* the work; it does not explain the mechanism.
- Only ONE surface may be the picker at a time. The rail is where you pick; the content is what you picked.

## Design

- **Accent violet only on the single primary filled button.** Not on selected states, tabs, badges, dots, links, rings or hover washes. Selection is weight and shape — border steps to `--text-primary` plus a drawn checkmark — never hue. Guard: `frontend/lib/ui/accent-restraint.test.ts`.
- **There is no focus ring.** Four attempts were rejected; the objection is the rectangle, not its colour. A field's border steps up one level and its caret blinks. This overrides the accessibility argument — it is his decision, not a bug to fix. Guard: `frontend/lib/ui/no-focus-ring-drift.test.ts`.
- Dense inside a group, airy between groups. Motion 100–150ms ease-out on state change only. Real `h1`/`h2`. Primary navigation is real links so cmd-click works.
- A component that moves focus programmatically owns the obligation to clear it.
- The Agents surface is cards. **A card face is two facts and refuses a third**: state (stopped/blocked/working/idle) and reach (the task it is on > tasks waiting > where it answers > neither). The two slots may disagree — that is the point. Sort by attention rank then name, never recency.
- Agents has three layouts behind one view-options popover — **Cards (default) · Board · List** — the shape Projects already has. Cards is a real layout VALUE, never the absence of a grouping, and all three draw the same reach line: `activity_preview` is a lifecycle verb, true of every agent, and putting it back is the defect this surface has regressed to twice. Guard: `agent-view-options.test.ts`.
- Channels is a square-card grid; a face is icon + label + ONE pill, everything else in the panel it opens. Apps is a row card: name left, action right, whole card opens the detail.
- One platform = one card; a variant is always a door. The door COUNT decides direct-vs-picker, never a named channel. A door states its consequence on its face.
- Any auto-fill grid on a `.fleet-content` page needs `--wide`, or it collapses to one column with dead space either side.
- A brand mark is SOURCED from the owner's own brand page, or it is a monogram. Never drawn, traced or recoloured.
- iOS is a **companion**, local-first, tokens ported verbatim from `theme-tokens.css` — with one sanctioned divergence: dark mode is pure black, and the whole dark ramp is re-derived against it rather than patched.

## Recurring failure modes

- **Built, tested, and never wired** is the most common defect here — complete, correct, tested code with zero callers. Grep for callers before believing a feature exists. Schema variant: a migration can ship without the code that fills its columns.
- "Code exists" is not "reachable on the live path". Trace from the real entry point to the real call site.
- **A `next.config` redirect resolves BEFORE the router**, so it can make a real page unreachable with no React error anywhere. Grep `LEGACY_REDIRECTS` before concluding a route is broken in React.
- A permission ternary that picks between two RENDERINGS means the page has two designs and only one was reviewed — check which one the person the feature exists for actually lands on.
- **A compiled artifact is a live-path risk `grep` cannot see** (the Rust kernel, the gateway `dist/`). Preflight refuses to boot on a stale kernel; the deploy runbook rebuilds it.
- **A check that derives its expectations from the thing it checks is blind and reports "passed".** The expected set and the actual set must come from different sources. Give every source-scanning test a canary that fails loudly when the scan reaches nothing.
- `WHERE ($1 = '' OR tenant_id = $1)` **fails OPEN**. A scope column with a default is a loaded gun — make it a required keyword with no default.
- `require_api_key` is not an authorization check; it answers "is someone logged in". Use `enforce_workspace_access` (with an explicit `minimum_role`), `current_user_has_auth_admin_access`, or `has_platform_fleet_operator_access`. A "visible" filter is a filter, not a gate.
- `users.tenant_id` / `users.workspace_id` are the HOME workspace, written once at signup and never updated. Resolve the tenant per-workspace instead.
- **A mock protects a seam, not a path. A fixture protects a shape, not a path.** Build fixtures from the PRODUCER, or the test and the code agree perfectly and are both wrong about the caller.
- A test asserting an ABSENCE must also assert the call COUNT, or it cannot tell "nothing happened" from "something else happened". A money path needs `== 1`.
- **Silence is a decision, never a failure.** An empty string cannot express one — if two callers must tell "chose not to" from "could not", the producer has to say which.
- **Two different facts may never share one signal.** Delivery outcome, invite mail, update advertisement, absent-vs-empty capability lists. Never advertise an update whose success could not be observed.
- **Put a safety filter on the narrow waist, never on each branch** — a per-branch call is a rule the next author has to know; a wrapper is one they cannot reach around. Guard display and persistence separately.
- Match on stable CODES, never on prose. A reworded message silently breaks a keyword bucket. Provider failures have ONE code vocabulary — `provider_failure_classification` — and every code in it is already an `agent_command_dispatcher.classify_error` keyword. Never mint a second name for a failure that has one.
- **Read the whole response, not the one field you came for** — and the provider's OWN error code, not just the status: OpenAI answers 429 for both throttling and an empty balance, which need opposite advice.
- **An identity that stops at the emit site never reaches the row.** A surface can only render what it was handed; carry the fact from wherever it is already in hand.
- **A migration that BACKFILLS a tenant-scoped table under RLS silently writes zero rows** and exits 0 — DDL applies, DML addresses the empty set. Use `SET LOCAL app.rls_bypass = 'on'`, or move the backfill into boot code.
- **Apply production migrations as `empyralis_app`, not as the Postgres superuser** — a superuser-applied migration leaves the table owned by `postgres` and the app crash-loops. Re-run `migrations/enable_rls.sql` after adding any table.
- Renaming a table that carries `tenant_id`/`workspace_id` is a TWO-PART change: add the new key to `preflight._RLS_COVERAGE_EXCEPTIONS`, deploy, *then* rename. Keep the old key so the revert also boots.
- **Production never loads `.env`** (`EMPYRALIS_DEPLOY_ENV=self-hosted` skips dotenv) — the config is pm2's saved env, and `/root/pm2-env.sh` is the authoritative snapshot. `pm2 restart --update-env` from a bare shell REPLACES the process env and strips every secret.
- The production frontend build must be detached (`nohup`) or a dropped SSH session kills it. See `docs/DEPLOY-RUNBOOK.md`.
- **Cloudflare fronts production**, undocumented in the nginx config. Its ~100s idle timeout, not nginx's, is the real ceiling on any long request; a silent SSE stream is cut at ~125s.
- `nginx.conf` includes `sites-enabled/*` extension and all — never leave a backup file there.
- **A bug you document becomes a constraint the next reader inherits.** When you route around a defect, the note says the defect is UNFIXED, never that the capability is impossible — and never add a test asserting the broken behaviour.
- The two-engine seam (legacy vs. Claude Agent SDK) keeps diverging: enumerate the SIDE EFFECTS of the old branch's callees, not just its return value. Identical return contracts are exactly what makes such a review pass.
- **Verifying against DOCS is still transcription.** Ask the running harness first (schema dump, `--help`, `app-server` RPC), then a live API the customer's own credential can call, then a dated pinned fallback that is visibly the degraded path. Documentation is last, and is a hint about where to look.
- **Measure from a customer's seat, not the founder's.** His China latency is filtering, not the product; a US customer pays almost no network tax. Cut round TRIPS, not milliseconds.
- A permission exemption needs a test that fails when the exemption is too WIDE. Every other assertion passes under the naive fix too.
- Changing a pinned literal means grepping the LITERAL repo-wide and running the changed package's whole suite — hand-copied pins are invisible to a grep for the constant.
- A stand-in left in `sys.modules` becomes production's `server` for the rest of the process; a test that fails before its cleanup block poisons every later test.

## Guards — the rule lives in the test, not in prose

`frontend/lib/`: `ui/accent-restraint`, `ui/no-focus-ring-drift`, `ui/error-message-drift`,
`workspace/outcome-honesty-drift`, `workspace/authorized-fetch-drift`,
`workspace/fleet/{agent-card-face,connector-card-face,primary-rail-nav,channel-hardware-tier,agent-detail-tabs}`,
`security/content-security-policy`, plus `frontend/next.config.test.ts`. All wired into `npm run test:unit`.

`server_modules/tests/`: `test_rls_dml_drift`, `test_module_reachability`,
`test_worktree_branch_sprawl_guard`, `test_live_provider_egress_guard`,
`test_outcome_honesty_lint`, `test_exception_and_task_lint`, `test_agent_reachability_guard`,
`test_agent_context_grant`, `test_unguarded_reply_paths`, `test_tool_honesty_guard`,
`test_default_engine_credit_debit`, `test_authority_mandate_service`,
`test_run_state_scope_fails_closed`, `test_tool_name_secret_redaction`, `test_reload_isolation`,
`test_provider_failure_classification`, `test_failed_run_identity`,
`test_trace_outcome_honesty`, `test_invite_existing_member`.

When a guard exists, do not restate its rule here — extend the guard instead.

## Open founder decisions — do not guess

- Whether "faces the public" should be DERIVED from being wired to a public channel rather than declared in a form, and whether the owner-facing setting should exist at all. (No security consequence since the tool tier was deleted.)
- Whether Settings ▸ Account gets the light/dark preference, or the row and its redirect are both removed. It is currently a live route with zero controls.
- Whether `channel_concurrency_service` — a complete lease/quota gate with zero production callers and rotted tests — is revived or deleted.
- Tier pricing. $20/$100/$200 was thinking aloud, explicitly not final.
- Whether a gateway may auto-update itself unattended. The mechanism is built and safe; nothing is enabled in production.

When he raises a product question, add it here in the same turn. Do not answer it with a guess, and do not let it live only in chat.
