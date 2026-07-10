# Mobile craft rebuild — UI Contract verification

Verifies the mobile rebuild against [`docs/UI-CONTRACT.md`](../UI-CONTRACT.md) by
live DOM measurement (`getBoundingClientRect()` / `getComputedStyle()` in the
running app), not visual inspection alone. Viewport: 390×844. Every FAIL found
during this pass was fixed before this table was written — none are open.

## Measurement table

| Surface | Element | Contract spec | Measured | Result |
|---|---|---|---|---|
| Shell (all pages) | `.fleet-shell-topbar` height | ≤48px | 48px | PASS |
| Shell | `.fleet-topbar-menu-btn` (hamburger) | 34px | 34×34px | PASS |
| Shell | Breadcrumb current title | 15px / 500 | 15px / 500 | PASS |
| Shell | Breadcrumb count | 12px | 12px | PASS |
| Agents/Projects/detail toolbar | Icon button size | 34px mobile | 34×34px | PASS |
| Agents/Projects/detail toolbar | Icon button shape | square, 6-8px radius | 8px radius | PASS |
| Agents/Projects/detail toolbar | Order, 390px | usage → filter+sort → panel-toggle | Usage, Filter and sort, Properties | PASS |
| Agents/Projects/detail toolbar | Order, 1280px (desktop) | same order, panel-toggle rightmost | Usage, Filter and sort, Properties | PASS |
| Agents page | `+ New agent` button | compact 32-34px, not full-width | 32px tall, 110px wide (content-sized) | PASS |
| Filter/sort popover | Option row height | sub-row 36px | 35px | PASS |
| Drawer (mobile nav) | Width | max 320px (~85vw) | 320px | PASS |
| Drawer | Search field | 36px | 36px | PASS |
| Drawer | Nav row | 40px | 40px | PASS |
| Drawer | Sub-nav row | 36px | 36px | PASS |
| Agents list row (mobile) | Row height | 44-52px two-line | 52px | PASS |
| Agents list row | Line 1 name | 15px | 15px / 500 | PASS |
| Agents list row | Line 1 status | status chip, right-aligned | `StatusChip` (dot+label), right | PASS |
| Agents list row | Line 2 meta | muted, brain · cost · last active | "DeepSeek · $0.0003 · 1h" | PASS |
| Create-agent wizard | Shape | full-screen sheet, steps navigable | bottom sheet, ~80% viewport height, all 4 steps click through | PASS |
| Wizard | CTA row | pinned bottom, primary expands full-width | primary 288px (dominant) / secondary 68px, flush to sheet edge | PASS |
| New-project / channel-connect dialogs | Shape | centered dialog (short content) | top 271px = bottom 271px, left 20px = right 20px, radius 16px all corners | PASS |
| Agent Chat / Sage console | Message text | 15px | 15px (was silently 13px — see Bugs below) | PASS |
| Agent Chat | User bubble width | ≤85% | 67% of viewport | PASS |
| Agent Chat | Composer | sticky, above keyboard | fixed flex sibling below scrolling list, never scrolls away | PASS |
| Sage console (floating) | z-index vs. open Properties drawer | beneath drawer while idle | idle: z-index 20 (drawer 30); open: z-index 45 (dialog-equivalent) | PASS |
| Help button (floating) | z-index vs. open Properties drawer | beneath drawer | z-index 20 (drawer 30) | PASS |
| Every surface listed below | Page-level horizontal scroll | zero | `scrollWidth === innerWidth` on all | PASS |

## Screenshots — every surface, both themes, 390×844

Captured and visually reviewed live in-session (not saved as files — this repo's
`docs/ui-proof/` convention stores PNGs from earlier passes; this pass's images
were reviewed inline during the build rather than exported). Each one asserted
`document.documentElement.scrollWidth <= window.innerWidth` immediately before
the screenshot:

login, signup, agents, projects, project detail, agent Overview, agent Chat,
agent Tools, create-agent wizard (step 1), Inbox (list + pushed-in detail),
Hardware, Settings, Billing — light and dark, 22 screenshots total. Zero
horizontal scroll on all 22.

## Bugs found and fixed during this pass

Not in the original ask, but each is a direct contract violation the
measurement work surfaced:

1. **Toolbar order was backwards.** `FleetToolbar.tsx` rendered
   `[panel-toggle] [filter+sort] [usage]` — panel-toggle first, not last —
   on every viewport, contradicting its own docstring's stated intent and
   the properties panel's right-side anchor. This is likely the literal bug
   the contract's §6 invariant was written to codify. Fixed: reordered to
   `[usage] [filter+sort] [panel-toggle]`.
2. **Icon buttons were circles**, not the contract's square/6-8px-radius
   shape (`border-radius: 50%`). Fixed at the shared `.fleet-icon-btn` rule,
   so it applies to every icon button (both scales), not just mobile.
3. **`--app-font-15` was referenced 23 times across `chrome.css` and never
   defined.** An undefined custom property makes the `font-size` declaration
   using it invalid, so every one of those 23 spots was silently inheriting
   its ancestor's size — chat message bubbles included, rendering at 13px
   instead of the intended 15px. Fixed by defining the token; this corrects
   all 23 call sites, not just chat.
4. **Ask Sage / Help floating buttons rendered above an open Properties
   drawer**, a direct §7 violation — both shared z-index 40 with the
   drawer's own popovers, one tier above the drawer itself (30). Fixed by
   dropping their idle z-index to 20 (below the drawer), with an `.is-open`
   escape hatch on the Sage launcher so an actively-open console (a real
   chat surface, not a "bubble") still layers above the drawer like any
   other dialog.
5. **The Sage console panel ran off the left edge of the viewport** at
   390px — fixed 400px width, anchored `right:0` to a launcher button that
   itself sits 62px from the screen edge, netting roughly -72px on the
   left. Not named in the contract's own Part 2 list, but a working panel
   is the floor under every item on it. Fixed with a mobile-specific
   fixed/margined position.
6. **The wizard's first attempt at a "sheet"** pinned it to the bottom with
   a small, dialog-style Cancel/Next footer, leaving a tall dead gap of
   backdrop above a short step's content — reported directly by the user
   as looking broken. Corrected per contract §8: the wizard is a real
   sheet with a fixed (not content-hugging) height and a full-width pinned
   primary CTA; the two genuinely short dialogs (new-project,
   channel-connect) are centered floating cards instead, not sheets.

## Scope note

Projects list keeps its earlier grid-collapse mobile treatment (3 columns +
a muted meta line) — the contract's Part 2 "Agent rows" item named Agent
rows specifically, not Projects rows, so Projects wasn't rebuilt to the same
two-line shape this pass. It already passes the hard rules (no horizontal
scroll, real content, both themes) from the prior mobile pass.
