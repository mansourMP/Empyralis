"use client";

/**
 * A small icon-only "Copy link" control, reused wherever a row or detail
 * page needs one (project detail toolbar, project list rows — see their
 * own call sites). CLAUDE.md: "the board is the product; nothing of value
 * may exist only in a conversation" — every item that has its own real URL
 * needs a one-click way to hand that URL to someone else, not just to the
 * one page that happens to already render a menu.
 *
 * Visible to a viewer, not just a writer — copying a link is read-only, and
 * "let me hand you a link" is exactly the moment a read-only visitor wants
 * this. Callers should never gate this behind an owner/writer check.
 *
 * Shares lib/ui/copy-link.ts's honest three-state hook with every other
 * Copy link control in this codebase (TaskDetailView, DocumentDetailView):
 * "Copied!" only shows once the write is CONFIRMED, never optimistically on
 * the click alone (CLAUDE.md: "never claim success if navigator.clipboard
 * rejected").
 */

import { Check, Link as LinkIcon } from "lucide-react";

import { useCopyLinkState } from "./copy-link";

export function CopyLinkButton({
  path,
  label = "this item",
  className = "fleet-icon-btn",
  iconSize = 16,
}: {
  /** The item's own app-relative path (e.g. `/w/ws_1/projects/proj_1` —
   *  same shape `router.push`/`<Link href>` already take everywhere else in
   *  this codebase). Combined with `window.location.origin` LAZILY, inside
   *  the click handler, never at render time — this component (and every
   *  page it is used on) still gets server-rendered for the initial HTML,
   *  where `window` does not exist; reading it during render would crash
   *  that pass. Building from `origin + path` rather than reading
   *  `window.location.href` also means the SAME component works correctly
   *  on a LIST ROW (whose current URL is the list page, not this item's) as
   *  well as on the item's own detail page. */
  path: string;
  /** Named in the aria-label/title so a screen reader (or a tooltip) says
   *  "Copy link to <label>" rather than a bare "Copy link" that reads
   *  identically on every row in a list. */
  label?: string;
  className?: string;
  iconSize?: number;
}) {
  const { state, copy } = useCopyLinkState(() => `${window.location.origin}${path}`);
  const text =
    state === "copied" ? "Link copied" : state === "failed" ? "Couldn't copy link" : `Copy link to ${label}`;

  return (
    <button
      type="button"
      className={className}
      aria-label={text}
      title={text}
      onClick={(e) => {
        // Rows are often themselves a <Link> (project list, a future task/
        // document row) — this button must act on its own, never also
        // trigger the row's own navigation underneath it.
        e.preventDefault();
        e.stopPropagation();
        void copy();
      }}
    >
      {state === "copied" ? (
        <Check size={iconSize} strokeWidth={2} />
      ) : (
        <LinkIcon size={iconSize} strokeWidth={1.75} />
      )}
    </button>
  );
}
