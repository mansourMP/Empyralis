"use client";

import { AlertCircle } from "lucide-react";

/**
 * Shared loading / error states for fleet API surfaces. Skeletons stand in for
 * lists while they load (never a bare spinner); errors are always
 * human-readable (never raw JSON or a silent empty state).
 */

export function FleetListSkeleton({
  rows = 5,
  rowHeight,
}: {
  rows?: number;
  /** Match the real content's row height (e.g. AgentsList/TasksList's
   *  .fleet-agent-row / .fleet-task-row, both `min-height: 52px`) so this
   *  placeholder doesn't reflow the page when the fetch resolves and the
   *  real list swaps in. Every caller standing in for a *list* passes this:
   *  52 for agent/task/project rows, 44 for .fleet-inbox-row. Left unset, rows fall
   *  back to ~37px (12px bar + 12px top/bottom padding), which is right only
   *  for the plain `.fleet-list-row` shape — today that's Billing, whose
   *  ~37.8px .fleet-usage-legend-row already matches within a pixel.
   *  MAN-113: the un-set default caused a visible
   *  "renders one way, then snaps to another" jump on the project detail
   *  page — its Agents/Tasks views render into 52px grid rows, ~15px taller
   *  than this skeleton's un-pinned rows, so the whole list resized the
   *  moment the loading skeleton was replaced by real content. */
  rowHeight?: number;
}) {
  return (
    <div className="fleet-list" aria-busy="true" aria-label="Loading">
      {Array.from({ length: rows }).map((_, i) => (
        <div
          key={i}
          className="fleet-list-row"
          style={rowHeight ? { minHeight: rowHeight, boxSizing: "border-box" } : undefined}
        >
          <div className="fleet-skeleton-bar" style={{ width: `${38 + (i % 3) * 16}%`, height: 12 }} />
          <div className="fleet-skeleton-bar" style={{ width: 48, height: 12, marginLeft: "auto" }} />
        </div>
      ))}
    </div>
  );
}

/**
 * Row-shaped skeleton for icon+two-line content — activity feeds,
 * conversation history, schedules, tool/capability lists: anywhere the real
 * content is a vertical list of "small dot/icon, a title line, a quieter
 * meta line underneath". Extracted from OverviewTab's Recent Activity and
 * AgentChat's conversation-history loading state, which had this exact
 * markup duplicated inline — a third caller copying it again is the
 * bespoke-per-screen drift CLAUDE.md warns about, so this is the one
 * version every list-shaped tab now shares.
 *
 * Was previously reached for tabs whose loading state is NOT a short
 * single-line summary (Schedule/Tools/Capabilities/Connectors) via a single
 * 8px `.fleet-skeleton-bar` — visibly tiny next to the many-row `.fleet-
 * config` toggle list or card grid it gets replaced by the moment the fetch
 * resolves, the exact "small placeholder in what is actually a very big
 * area" complaint. Rows default to 4, wide enough to cover a typical tab
 * without reserving so much height an empty result snaps the page shorter.
 */
export function FleetRowsSkeleton({ rows = 4, label = "Loading" }: { rows?: number; label?: string }) {
  const widths = [68, 52, 60, 74, 46, 64];
  return (
    // Reuses `.fleet-activity`/`.fleet-activity-item`/`.fleet-activity-dot`
    // verbatim — its documented origin, OverviewTab's Recent Activity — so
    // each row's 12px/20px padding and dot-plus-two-lines geometry come
    // straight from the real row's own CSS instead of the old hand-tuned
    // `.fleet-activity-skeleton`/`.fleet-skeleton-row` pair, which had
    // drifted noticeably shorter than a real `.fleet-activity-item` (no
    // 12px/20px padding, no margin under the title line). Other callers on
    // this surface whose real content is NOT an activity-style icon+2-line
    // row (a toggle row with a switch/button, a chat bubble) have their own
    // dedicated skeletons now — see FleetToggleRowsSkeleton/
    // FleetAgentChatSkeleton below — rather than reaching for this one.
    <div className="fleet-activity" aria-busy="true" aria-label={label}>
      {Array.from({ length: rows }).map((_, i) => {
        const w = widths[i % widths.length];
        return (
          <div key={i} className="fleet-activity-item">
            <div className="fleet-activity-dot" style={{ background: "var(--border-strong)" }} />
            <div style={{ flex: 1, minWidth: 0 }}>
              <div className="fleet-skeleton-bar" style={{ width: `${w}%`, height: 13, marginBottom: 6 }} />
              <div className="fleet-skeleton-bar" style={{ width: `${Math.max(w - 24, 20)}%`, height: 11, opacity: 0.6 }} />
            </div>
          </div>
        );
      })}
    </div>
  );
}

/**
 * Card-grid skeleton matching `.fleet-channel-grid`/`.fleet-connector-grid`
 * — the square-card-per-platform grid (icon + label + one pill, 4 across,
 * 2 at <=900px per CLAUDE.md's "square-card grid" ruling). Reuses the real
 * grid/card classes so the placeholder is pixel-identical to what replaces
 * it — no separate width/columns math to keep in sync by hand.
 */
export function FleetCardGridSkeleton({ cards = 8, label = "Loading" }: { cards?: number; label?: string }) {
  return (
    <div className="fleet-channel-grid" aria-busy="true" aria-label={label}>
      {Array.from({ length: cards }).map((_, i) => (
        <div key={i} className="fleet-channel-card" style={{ cursor: "default", pointerEvents: "none" }}>
          <div className="fleet-skeleton-bar" style={{ width: 22, height: 22, borderRadius: 6 }} />
          <div className="fleet-skeleton-bar" style={{ width: "70%", height: 11 }} />
          <div className="fleet-skeleton-bar" style={{ width: 44, height: 16, borderRadius: 999 }} />
        </div>
      ))}
    </div>
  );
}

/**
 * Kanban-shaped skeleton matching `.fleet-board`'s column layout — used
 * while the tasks fetch is in flight AND the saved view is "board", so the
 * loading placeholder doesn't reflow from a flat list into multi-column
 * cards the instant the fetch resolves (MAN-113's row-height fix solved
 * this for the list/grouped layouts; the board layout had no equivalent
 * and fell back to `FleetListSkeleton`'s flat rows). Column count/labels
 * are unknowable before the fetch returns — real columns exist only when
 * they hold a task — so this renders a generic, unlabeled set purely to
 * reserve the right SHAPE (columns of cards, not a list), never real
 * status names.
 */
export function FleetBoardSkeleton({ label = "Loading" }: { label?: string }) {
  const columns = [2, 3, 1, 2];
  return (
    <div className="fleet-board" aria-busy="true" aria-label={label}>
      {columns.map((cardCount, ci) => (
        <section key={ci} className="fleet-board-column">
          <header className="fleet-board-column-header">
            <div className="fleet-skeleton-bar" style={{ width: 14, height: 14, borderRadius: 4 }} />
            <div className="fleet-skeleton-bar" style={{ width: 64, height: 11 }} />
          </header>
          <div className="fleet-board-column-body">
            {Array.from({ length: cardCount }).map((_, i) => (
              <div key={i} className="fleet-board-card" style={{ cursor: "default" }}>
                <div className="fleet-skeleton-bar" style={{ width: `${70 - i * 8}%`, height: 12 }} />
                <div className="fleet-skeleton-bar" style={{ width: "40%", height: 10, opacity: 0.6 }} />
              </div>
            ))}
          </div>
        </section>
      ))}
    </div>
  );
}

/**
 * Toggle-row skeleton matching `.fleet-config`/`.fleet-toggle-row` — the
 * label+description-on-the-left, switch-or-button-on-the-right row every
 * per-item settings list on an agent's detail page actually renders
 * (ScheduleSection, ToolsTab, CapabilitiesTab). These three previously fell
 * back to `FleetRowsSkeleton`, an icon+2-line ACTIVITY-feed row shape (no
 * left icon exists on a toggle row, and a toggle row's real trailing
 * content — a switch or a Cancel button — was never reserved), which is
 * FleetRowsSkeleton's own documented ORIGINAL shape drifting into callers
 * it was never built for (see that component's doc comment). `trailing`
 * matches the two real shapes on this surface: `"switch"` (ToolsTab/
 * CapabilitiesTab's `.fleet-toggle`) and `"button"` (ScheduleSection's
 * Cancel button).
 */
export function FleetToggleRowsSkeleton({
  rows = 4,
  trailing = "switch",
  label = "Loading",
}: {
  rows?: number;
  trailing?: "switch" | "button";
  label?: string;
}) {
  return (
    <div className="fleet-config" style={{ padding: 0 }} aria-busy="true" aria-label={label}>
      {Array.from({ length: rows }).map((_, i) => (
        <div key={i} className="fleet-toggle-row">
          <div style={{ minWidth: 0 }}>
            <div className="fleet-skeleton-bar" style={{ width: 120 + (i % 3) * 30, height: 13 }} />
            <div className="fleet-skeleton-bar" style={{ width: 90, height: 11, marginTop: 6, opacity: 0.7 }} />
          </div>
          {trailing === "switch" ? (
            <div className="fleet-skeleton-bar" style={{ width: 34, height: 20, borderRadius: 999, flexShrink: 0 }} />
          ) : (
            <div className="fleet-skeleton-bar" style={{ width: 76, height: 28, borderRadius: 6, flexShrink: 0 }} />
          )}
        </div>
      ))}
    </div>
  );
}

/**
 * Chat-transcript skeleton matching `.fleet-work-msg` bubbles (WorkTab's and
 * ConversationsView's shared transcript-pane markup) — alternating
 * user/agent alignment so the placeholder reads as a conversation, not a
 * stack of identical boxes. Replaces a bare "Loading…" line that sat alone
 * in what is otherwise a large, scrollable message pane.
 */
export function FleetChatSkeleton({ bubbles = 4, label = "Loading" }: { bubbles?: number; label?: string }) {
  const shapes = [
    { side: "user", width: 58 },
    { side: "agent", width: 74 },
    { side: "user", width: 40 },
    { side: "agent", width: 62 },
  ];
  return (
    <div className="fleet-work-transcript" aria-busy="true" aria-label={label}>
      {Array.from({ length: bubbles }).map((_, i) => {
        const shape = shapes[i % shapes.length];
        return (
          <div key={i} className={`fleet-work-msg fleet-work-msg--${shape.side}`} style={{ width: `${shape.width}%` }}>
            <div className="fleet-skeleton-bar" style={{ width: "90%", height: 10, marginBottom: 6 }} />
            <div className="fleet-skeleton-bar" style={{ width: "55%", height: 10, opacity: 0.6 }} />
          </div>
        );
      })}
    </div>
  );
}

/**
 * Document-detail skeleton matching DocumentDetailView's real element tree
 * (`.fleet-task-page.fleet-doc-detail-page` > `.fleet-task-page-main` >
 * `.fleet-task-page-body` > `.fleet-doc-header-row` / `.fleet-doc-meta` /
 * prose). Reuses those exact classNames so the 720px-capped, centered
 * column (document-detail.css's `.fleet-doc-detail-page
 * .fleet-task-page-body { margin: auto }`) and the page's own padding come
 * from CSS, not a hand-guessed width — the same "structurally the same
 * element tree" discipline FleetBoardSkeleton/FleetCardGridSkeleton already
 * use. Was previously `FleetListSkeleton rows={3} rowHeight={52}` — three
 * flat 52px table rows standing in for a full-page prose document, the
 * founder's own screenshotted complaint. A document's real body length is
 * unknowable before the fetch resolves, same as FleetBoardSkeleton's column
 * contents; this reserves a plausible paragraph run rather than trying to
 * predict the real one.
 */
export function FleetDocumentSkeleton({ label = "Loading" }: { label?: string }) {
  const paragraphWidths = [96, 88, 92, 60, 80, 94, 70, 85, 55];
  return (
    <div className="fleet-task-page fleet-doc-detail-page" aria-busy="true" aria-label={label}>
      <div className="fleet-task-page-main">
        <div className="fleet-task-page-body">
          <div className="fleet-doc-header-row">
            {/* Matches .fleet-task-page-title's 24px/1.25 line box — the
                title now RENDERS by default (2026-08-12), reusing the same
                shared class TaskDetailView's own title does, so this bar's
                dimensions are unchanged from when it stood in for the old
                always-live .fleet-doc-title-input (same 24px/1.25 box). */}
            <div className="fleet-skeleton-bar" style={{ width: "55%", height: 30, marginTop: 6 }} />
          </div>
          {/* .fleet-doc-meta: 12px byline/autosave-status line. */}
          <div className="fleet-skeleton-bar" style={{ width: 130, height: 11, marginTop: 10, opacity: 0.75 }} />
          {/* .fleet-doc-body: 15px/1.6 prose, .fleet-doc-paragraph's 16px
              rhythm (both re-decided 2026-08-12 for a page whose default
              state is the rendered body, not the editor — see
              document-detail.css's own comment on .fleet-doc-body). gap:16
              matches .fleet-doc-paragraph's margin-bottom directly, so a
              bounding-box diff against the loaded page's real paragraph
              spacing is exact, not eyeballed. */}
          <div style={{ marginTop: 20, display: "flex", flexDirection: "column", gap: 16 }}>
            {paragraphWidths.map((w, i) => (
              <div key={i} className="fleet-skeleton-bar" style={{ width: `${w}%`, height: 15 }} />
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}

/**
 * Task-detail skeleton matching TaskDetailView's real element tree
 * (`.fleet-task-page.fleet-task-detail-page` > `.fleet-task-page-main` >
 * `.fleet-task-page-scroll` > `.fleet-task-page-body`, PLUS the sibling
 * `.fleet-task-page-side` Properties column) — reusing the exact classNames
 * task-detail.css already styles, so the 720px reading column, the 300px
 * side rail and both panes' padding come from CSS rather than a guess.
 * Was previously the same flat `FleetListSkeleton rows={3} rowHeight={52}`
 * table-row skeleton the document page had — same bug, same fix: a task
 * page is a title + description + sections beside a Properties sidebar,
 * never a list.
 */
export function FleetTaskDetailSkeleton({ label = "Loading" }: { label?: string }) {
  return (
    <div className="fleet-task-page fleet-task-detail-page" aria-busy="true" aria-label={label}>
      <div className="fleet-task-page-main">
        <div className="fleet-task-page-scroll">
          <div className="fleet-task-page-body">
            {/* .fleet-task-page-eyebrow: the short "TASK-123" id line. */}
            <div className="fleet-skeleton-bar" style={{ width: 64, height: 11 }} />
            {/* .fleet-task-page-title: 24px/1.25. */}
            <div className="fleet-skeleton-bar" style={{ width: "58%", height: 24, marginTop: 8 }} />
            {/* .fleet-task-page-desc: 13px/1.65 body copy. */}
            <div style={{ marginTop: 16, display: "flex", flexDirection: "column", gap: 10 }}>
              <div className="fleet-skeleton-bar" style={{ width: "94%", height: 13 }} />
              <div className="fleet-skeleton-bar" style={{ width: "80%", height: 13 }} />
              <div className="fleet-skeleton-bar" style={{ width: "62%", height: 13 }} />
            </div>
          </div>
        </div>
      </div>
      <aside className="fleet-task-page-side" aria-hidden="true">
        <div className="fleet-task-page-side-inner">
          <div className="fleet-skeleton-bar" style={{ width: 70, height: 11, marginBottom: 14 }} />
          {[0, 1, 2, 3].map((i) => (
            <div key={i} className="fleet-panel-row">
              <span className="fleet-panel-row-label">
                <span className="fleet-skeleton-bar" style={{ width: 15, height: 15, borderRadius: 4 }} />
                <span className="fleet-skeleton-bar" style={{ width: 46 + (i % 2) * 10, height: 11 }} />
              </span>
              <span className="fleet-skeleton-bar" style={{ width: 60, height: 11 }} />
            </div>
          ))}
        </div>
      </aside>
    </div>
  );
}

/**
 * AgentChat transcript skeleton — a sibling to FleetChatSkeleton but built
 * against AgentChat's OWN real markup (`.fleet-sage-chat-list` of
 * `article.app-chat-message[data-chat-role]` from lib/workspace/chat-message.tsx),
 * which is a different element tree than WorkTab/ConversationsView's
 * `.fleet-work-transcript`/`.fleet-work-msg` — FleetChatSkeleton's classes
 * don't exist on this surface, so reusing it here would style nothing and
 * silently fall back to unstyled divs. `data-chat-role` is a real HTML data
 * attribute, so setting it directly on the skeleton bubbles picks up
 * `.app-chat-message[data-chat-role='user'/'assistant']`'s own
 * width/alignment/bubble rules from chrome.css — no separate geometry to
 * keep in sync by hand. Replaces `FleetRowsSkeleton`, an icon+2-line
 * activity-row shape with no relation to a chat bubble's alignment or
 * width.
 */
export function FleetAgentChatSkeleton({ bubbles = 4, label = "Loading conversation" }: { bubbles?: number; label?: string }) {
  const shapes: { role: "user" | "assistant"; width: number }[] = [
    { role: "assistant", width: 70 },
    { role: "user", width: 45 },
    { role: "assistant", width: 55 },
    { role: "user", width: 30 },
  ];
  return (
    <div aria-busy="true" aria-label={label}>
      {Array.from({ length: bubbles }).map((_, i) => {
        const shape = shapes[i % shapes.length];
        return (
          <article key={i} className="app-chat-message" data-chat-role={shape.role}>
            <div className="app-chat-message__content" style={shape.role === "user" ? { width: `${shape.width}%` } : undefined}>
              <div className="fleet-skeleton-bar" style={{ width: shape.role === "user" ? "100%" : `${shape.width}%`, height: 13 }} />
            </div>
          </article>
        );
      })}
    </div>
  );
}

export function FleetSurfaceError({
  title = "Couldn’t load this",
  // No hardcoded "it'll keep trying" suffix — that's only true for a
  // surface that actually auto-retries in the background, which isn't every
  // caller. Say exactly what happened and let `onRetry` be the recovery
  // path; a caller can still opt back into the old, softer copy by passing
  // its own message.
  message = "Something went wrong on our side. Try again.",
  onRetry,
}: {
  title?: string;
  message?: string | null;
  onRetry?: () => void;
}) {
  return (
    <div className="fleet-page-state" role="alert">
      <AlertCircle size={22} strokeWidth={1.75} />
      <div className="fleet-page-state-title">{title}</div>
      <div className="fleet-page-state-body">{message}</div>
      {onRetry && (
        <div className="fleet-empty-actions">
          <button type="button" className="fleet-btn" onClick={onRetry}>Try again</button>
        </div>
      )}
    </div>
  );
}
