# Build — live-chat parity + mobile squeeze fix

SOLO UI on `verify` (branch `build/chat-parity-mobile`, based on
`1976a63ef`). Five numbered problems on the live Fleet agent-detail
surface. Two turned out to already be fixed by other same-day work on
`verify` before this branch point — reported honestly rather than
re-fixing something that wasn't broken. Verified against a real backend
running this branch's own code (not the shared `backend-verify`, which
does not `cd` into any worktree and would have silently served the
`verify`-branch backend instead — see Environment note below).

## 1. Chat renders + doesn't lie

**Markdown.** `WorkTab.tsx`'s bespoke renderer (`renderInline`/`parseBlocks`/
`MessageBody`) was local to that file; the live chat pipeline
(`chat-message.tsx` → `ChatMessage`) rendered `{text}` raw, so `**bold**`
showed as `**bold**`. Extracted the renderer to
`lib/workspace/markdown-lite.tsx` (`MarkdownLiteText`, `renderMarkdownLiteInline`,
`parseMarkdownLiteBlocks`) and wired it into both `WorkTab.tsx` and
`chat-message.tsx`. CSS for the four markdown classes
(`fleet-md-p`/`fleet-md-list`/`fleet-md-code`/`fleet-link`) was previously
only in `fleet-theme.css`, which the live chat page doesn't load — added a
matching rule block directly in `chrome.css` under `.app-chat-message` so
it resolves wherever `ChatMessage` renders.

Live-verified: sent "give me a **bold** word, an *italic* word, a `code`
snippet, and a bullet list" to a real DeepSeek-backed agent — bold, italic,
code, and the list all rendered correctly. Both themes.

**Tool honesty.** Traced the pipeline first rather than assuming the
task's "two pipelines, one missing the guard" premise: `sage_agent_runtime_service.handle_sage_chat`
and the Work-tab-driven channel replies both converge on the same
`apply_tool_honesty_guard` call — there's only one live pipeline, already
guarded. The real gap was `_CLAIM_PATTERNS` regex coverage: it caught
*retrospective* fabrication ("Based on the search results...") but not the
*forward-looking* promise the screenshot showed ("Let me search your
Gmail" with nothing in the trace). Added three narrowly-scoped patterns
("let me/I'll/I will search/check/look up/... your/the ___") to
`tool_honesty_guard.py`, plus `server_modules/tests/test_tool_honesty_guard.py`
(9 tests: the exact Gmail/calendar/Notion promise cases, the false-positive
guard for a promise phrase backed by a real tool result, ordinary-filler
non-triggers, and `_decide()` routing).

Live-verified the surrounding system, not the guard directly: asked a real
agent (0 connectors) "Can you search my Gmail for the latest invoice?" —
it answered honestly zero-shot ("I'm not able to search your Gmail...",
correctly enumerating its real tools). The guard is a defensive backstop
for when a model doesn't self-correct; a live prompt can't reliably force
the exact bad phrasing, so the 9 unit tests (pinning the literal reported
case) are the real proof here, not a chat transcript.

## 2. Bubbles — already compliant, no change made

Measured live via `getBoundingClientRect()`/`getComputedStyle()`, not
eyeballed: user bubble `max-width: 75%` of the transcript column (under
the 85% cap), `font-size: 15px`, `padding: 8px 14px`, `border-radius: 18px`,
flush to the transcript's right content edge. A one-word message ("Hello")
sizes to content (`width: 62px`) — not stretched, not oversized. Assistant
text reaches the full content width. Identical at 390px and both themes.
No CSS changed for this item — the reported "oversized and misaligned"
state was not reproducible here; whatever the screenshot in the task
showed, this build's baseline already renders within contract.

## 3. Mobile properties column — toggle drawer replaces bare `display:none`

An existing same-day (`2026-07-11`) mobile pass already made
`.fleet-detail-properties` stack below content on every tab **except**
Chat, where it was a straight `display: none` — hidden with no route back,
satisfying "never squeeze" but not "stack below or collapse to a toggle."
(Chat's own fixed-height flex layout, needed for the pinned composer,
doesn't leave room to stack a block below it — the actual reason it was
special-cased.)

Fix: reused the existing `FleetRightPanel` drawer (the same component the
list pages already use for their `panelOpen`/`onTogglePanel` toggle) rather
than inventing a new mechanism. `FleetAgentDetail.tsx`'s properties JSX is
now `propertiesContent`, rendered by both the permanent `<aside>` (desktop/
tablet/every-tab-but-Chat) and the drawer (Chat tab, ≤768px only). A
`.fleet-detail-properties-toggle` icon button appears in the tab bar only
when `activeTab === "chat"`, CSS-gated to ≤768px.

Defensive fix caught during verification: `mobilePropertiesOpen` is
component state, so it can stay `true` across a resize from mobile back to
desktop (e.g., rotating a tablet) — without a guard the drawer would float
over the permanent column at any width. Added
`.fleet-detail-columns .fleet-properties-drawer { display: none }` (and
the scrim) above 768px, scoped to `.fleet-detail-columns` specifically so
it doesn't touch the *list* pages' identical drawer classes, which are a
toggle at every width by design.

Live-verified at 390px, both themes: toggle button appears only on Chat;
opens a fully opaque drawer with all 8 properties rows; close (×) button
dismisses it cleanly; chat composer and transcript stay fully usable the
whole time (never squeezed). Verified at 1280px: permanent column shows
normally, toggle button is `display:none`, and — after the defensive fix —
the drawer stays `display:none` even with stale `open=true` state.

## 4. Memory tab honesty — scaffold banner, backend flag

`workspace_context.py`: new `is_default_context_content(filename, content)`
— true iff content is byte-for-byte the seeded
`DEFAULT_CONTEXT_FILE_CONTENTS[filename]` string. No new stored flag (which
could drift); any edit at all, even trivial, flips it `False` from the
single existing source of truth. Threaded through
`memory_service.memory_read_file()` → `agent_memory_tree_service.read_file()`
→ the existing `/memory/file` GET route (additive key, no shape change for
other callers). `MemoryTab.tsx` reads `is_default` from the fetch response
and shows a labeled note — "Starter scaffold — nobody has written to this
file yet. This is Empyralis' default template, not saved content." — above
the textarea; disappears the instant the file is saved (`isDefault` is
explicitly cleared on a successful `save()`, since the owner's own save is
never "default" regardless of what it says).

Live-verified: a real, never-touched agent's Memory tab shows the note
next to the exact seeded `MEMORY.md` text. Both themes.

## 5. Login mobile ("Almost there / Opening Sage") — real bug, real fix

Reproduced live at 390px by temporarily short-circuiting the page's own
redirect (`window.location.replace`) for inspection, reverted immediately
after (confirmed via `git diff` showing zero net change to
`app/auth/complete/page.tsx`). At 390px and 375px, nothing was clipped or
overflowing — the actual defect was typographic, not layout: `.app-auth-title`
uses `font-size: var(--app-font-24)`, and **`--app-font-24` was never
defined** (11/12/13/14/15/16/18/20 all exist, 24 doesn't — same class of
bug as the already-fixed `--app-font-15`). `font-size` on an undefined
custom property is invalid, so the browser used the inherited size instead
— the "Opening Sage" heading rendered at 16px, barely bigger than the body
text around it, collapsing the card's whole visual hierarchy into one
dense, undifferentiated block. Separately, `.app-auth-kicker` ("Almost
there") had zero CSS rule at all — same weight/color/size as ordinary
text, not read as a kicker.

Fixed both in `chrome.css`: added `--app-font-24: 24px` (matching the
existing `--app-font-18: 18px` hardcoded-value precedent), and styled
`.app-auth-kicker` (small, uppercase, accent-colored, letter-spaced) —
a real eyebrow label distinct from the title beneath it. This CSS is
shared, not `auth/complete`-specific: `--app-font-24` also fixes
`.studio-agent-overview__hero-copy strong`, `.studio-ai-settings__summary strong`,
and `.studio-template-detail__title`, all of which had the identical
silent fallback; `.app-auth-kicker` also fixes signup's identical,
identically-broken kicker.

Live-verified before/after at 390px and 375px, both themes, plus signup
(shares the classes) and desktop (unaffected) — kicker now reads as a
clear small-caps label, title is a real 24px heading, hierarchy is
legible. No page padding/breakpoint change was needed once the
typography was fixed.

## Environment note (not a UI change, but shapes how "verified live" is proved)

`backend-verify` (launch.json) has no `cd` — it always runs whatever
`server_modules/*.py` sits in the process's actual spawn directory. Early
in this session it was pointed at as this build's backend, which meant
Bug 4's `is_default` field genuinely wasn't in the response — not a code
bug, but a wrong-backend test setup. Added `backend-chatparity` (port
8911), `cd`'d into this worktree with `EMPYRALIS_RUNTIME_KERNEL_BIN` and an
explicit `DATABASE_URL` (the plain local `postgres:postgres@localhost:5432`
default — never touched the real `.env` file, per the secrets rule) so
Postgres/vault preflight succeeds the same way `backend-verify`'s already
does. `web-chatparity`'s `EMPYRALIS_API_URL` now points at 8911. Re-verified
Bug 4 against this corrected backend and it worked as designed.

## Verification

- **tsc**: identical 3 pre-existing baseline errors
  (`workstation-chat-pane-model.ts` `CanonicalApprovalSummary`,
  `next.config.ts` `eslint` key, `fleet-restyle-proof.spec.ts` implicit
  `any`) — 0 net-new.
- **Backend suite** (`pytest server_modules/tests/ -k "memory or workspace_context or tool_honesty"`,
  excluding two pre-existing collection errors unrelated to this build —
  missing `scripts/autonomous_computer_control_loop.py`, missing
  `sqlalchemy` — confirmed via the same exclusion against baseline):
  rigorous A/B via `git stash` — 87 failures with this branch's changes,
  91 without. The diff is exactly the 4 new `test_tool_honesty_guard.py`
  forward-looking-promise tests (fail without the fix, pass with it); the
  other 87 fail identically on both sides (pre-existing Postgres/vault
  test-harness gap, unrelated to this build). Zero net-new failures.
- `npm run test:unit`: 37/37 passed (unrelated file, unaffected).
- **Live, both themes, desktop (1280px) + mobile (390px)**: all 5 items
  above, plus signup/login pages for the shared CSS fixes.
- e2e Playwright: not run — its `webServer` config needs a `./venv` that
  doesn't exist in this environment (pre-existing gap, not touched here).

## Files changed

- `frontend/lib/workspace/markdown-lite.tsx` (new)
- `frontend/lib/workspace/fleet/tabs/WorkTab.tsx`
- `frontend/lib/workspace/chat-message.tsx`
- `frontend/lib/ui/chrome.css`
- `frontend/lib/workspace/fleet/FleetAgentDetail.tsx`
- `frontend/lib/workspace/fleet/fleet-theme.css`
- `frontend/lib/workspace/fleet/tabs/MemoryTab.tsx`
- `server_modules/tool_honesty_guard.py`
- `server_modules/tests/test_tool_honesty_guard.py` (new)
- `server_modules/workspace_context.py`
- `server_modules/memory_service.py`
- `server_modules/agent_memory_tree_service.py`
- `.claude/launch.json` (added `web-chatparity` + `backend-chatparity` dev
  entries — not part of the product, dev-environment only)
