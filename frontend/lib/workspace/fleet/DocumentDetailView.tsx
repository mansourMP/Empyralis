"use client";

/**
 * A project document's own page — read it rendered as markdown, or (with
 * write access) edit its title/body directly in place. Structural sibling of
 * TaskDetailView.tsx (a real page, not a drawer, so it deep-links and works
 * with browser back/forward) but far smaller: a document has no status,
 * assignee, or comment thread — just a title and a body.
 *
 * DIRECT MANIPULATION, NOT A MODE TOGGLE. The founder's own verdict on the
 * first pass (an Edit button that flipped a read/write mode, a red Delete
 * button stacked above the body): "why the fuck do I need to edit for? I
 * edit it, it changed. That's it." So for a `canWrite` reader the title and
 * body are ALWAYS live form controls — an <input> and a <textarea>, not a
 * rendered view that becomes one after a click. There is no Save button:
 * typing schedules an autosave (see AUTOSAVE_DEBOUNCE_MS below), and blurring
 * either field flushes it immediately. CLAUDE.md is explicit this stays a
 * plain markdown-source textarea, never a rich/WYSIWYG editor.
 *
 * A `viewer` (no write access) gets the opposite: plain rendered markdown
 * (MarkdownLite) and a plain heading, with NO editing affordance anywhere —
 * no input, no cursor change, no placeholder text hinting it could be
 * clicked into. `canWrite === null` (still resolving) reads as `false` here
 * too, so a viewer never sees an editable control flash into view for a
 * moment on load — same contract useCanWriteProject's own doc comment
 * establishes.
 *
 * DELETE moved off the page into a "⋯" overflow menu, top-right of the
 * header row (see DocumentMenu below) — the founder's second complaint was a
 * standing red Delete button doing nothing but wait to be misclicked.
 * Confirmation is a small inline swap INSIDE the menu ("Delete" → "Delete
 * document? / Cancel"), not a blocking modal — this codebase's approval-gate
 * rule (CLAUDE.md) is about workflow approval states, not this: a destructive
 * action asking "are you sure" once, inline, is a different thing and stays.
 *
 * Esc returns to the project's Documents list, mirroring TaskDetailView's
 * identical behavior (ignored while a control has focus, so Escaping out of
 * the body textarea doesn't also navigate away — it just blurs, which also
 * flushes any pending autosave).
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { MoreHorizontal } from "lucide-react";

import type { FleetDocument } from "./documents-data";
import { MarkdownLite } from "./markdown-lite";
import { timeAgo } from "./fleet-presentation";
import "./document-detail.css";

// Long enough that a normal typing cadence never fires a save mid-word,
// short enough that stopping to think for a beat is enough to persist.
const AUTOSAVE_DEBOUNCE_MS = 900;
// How long the "Saved" confirmation holds before quietly folding back into
// the plain "Updated {time}" line — long enough to register, not so long it
// reads as stuck.
const SAVED_DISPLAY_MS = 2000;

type SaveStatus = "idle" | "saving" | "saved" | "error";

export function DocumentDetailView({
  document,
  projectHref,
  canWrite,
  onSave,
  onDelete,
}: {
  document: FleetDocument;
  /** `${projectBase}/documents` — where Esc and a successful delete return to. */
  projectHref: string;
  /** null while still resolving — the title/body stay read-only rendered
   *  markdown, and the "⋯" menu stays unrendered, until this settles (see
   *  file header). */
  canWrite: boolean | null;
  onSave: (patch: { title: string; body: string }) => Promise<void>;
  onDelete: () => Promise<void>;
}) {
  const router = useRouter();
  const headingRef = useRef<HTMLHeadingElement | null>(null);

  // The draft is the source of truth for the title/body controls while this
  // document is open. It is reseeded from `document` ONLY when a genuinely
  // DIFFERENT document loads (the effect below keys on `document.id` alone)
  // — never on every render, and never on the `document` prop updating from
  // this view's OWN autosave round-trip (see documents/[documentId]/page.tsx's
  // handleSave, which folds a save's response straight into `document`
  // without a refetch, but even a refetch landing mid-keystroke must not be
  // allowed to stomp what's still being typed).
  const [draftTitle, setDraftTitleState] = useState(document.title);
  const [draftBody, setDraftBodyState] = useState(document.body ?? "");
  // Refs mirror the two states above so the debounce timer and the recursive
  // "a change landed while a save was in flight" retry (inside runSave)
  // always read the LATEST typed value, never the stale one closed over at
  // the moment the timer/save was scheduled.
  const draftRef = useRef({ title: document.title, body: document.body ?? "" });
  const setDraftTitle = useCallback((v: string) => {
    draftRef.current = { ...draftRef.current, title: v };
    setDraftTitleState(v);
  }, []);
  const setDraftBody = useCallback((v: string) => {
    draftRef.current = { ...draftRef.current, body: v };
    setDraftBodyState(v);
  }, []);
  // What the server last confirmed — the autosave no-ops once the draft
  // matches this, so blurring a field that was never touched costs nothing.
  const lastSavedRef = useRef({ title: document.title, body: document.body ?? "" });

  const [status, setStatus] = useState<SaveStatus>("idle");
  const [error, setError] = useState<string | null>(null);

  const savingRef = useRef(false);
  const pendingRef = useRef(false);
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const savedFadeRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  // A different document opened — reseed the draft and the "last saved"
  // baseline. Deliberately keyed on `document.id` alone (see the draft
  // comment above for why title/body are NOT also dependencies).
  useEffect(() => {
    setDraftTitleState(document.title);
    setDraftBodyState(document.body ?? "");
    draftRef.current = { title: document.title, body: document.body ?? "" };
    lastSavedRef.current = { title: document.title, body: document.body ?? "" };
    setStatus("idle");
    setError(null);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [document.id]);

  // Focus the page's own content on open — screen-reader/SPA-navigation
  // convention (same as TaskDetailView's identical effect). Only for the
  // VIEWER's plain <h2>: a canWrite reader's title is a real form control,
  // and auto-focusing text into it on every open would drop a blinking
  // cursor into the title unasked, which reads as "you're now editing" —
  // exactly the unsolicited mode-switch the direct-editing rework exists to
  // remove. A control only becomes focused because someone clicked it.
  useEffect(() => {
    if (!canWrite) headingRef.current?.focus();
  }, [document.id, canWrite]);

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key !== "Escape" || event.defaultPrevented) return;
      const el = window.document.activeElement as HTMLElement | null;
      const tag = el?.tagName;
      if (tag === "INPUT" || tag === "TEXTAREA" || el?.isContentEditable) return;
      router.push(projectHref);
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [router, projectHref]);

  const clearSavedFade = () => {
    if (savedFadeRef.current) {
      clearTimeout(savedFadeRef.current);
      savedFadeRef.current = null;
    }
  };

  const runSave = useCallback(async () => {
    // A save is already in flight — record that a newer draft exists and
    // let THAT save's own `finally` re-run this once it resolves, rather
    // than firing two overlapping PATCHes that could land out of order.
    if (savingRef.current) {
      pendingRef.current = true;
      return;
    }
    const title = draftRef.current.title.trim() || "Untitled document";
    const body = draftRef.current.body;
    if (title === lastSavedRef.current.title && body === lastSavedRef.current.body) {
      return;
    }
    savingRef.current = true;
    clearSavedFade();
    setStatus("saving");
    setError(null);
    try {
      await onSave({ title, body });
      lastSavedRef.current = { title, body };
      setStatus("saved");
      savedFadeRef.current = setTimeout(() => setStatus("idle"), SAVED_DISPLAY_MS);
    } catch (e) {
      // The draft is untouched here — a failed save never clears or reverts
      // what was typed (CLAUDE.md-adjacent: never silently lose the user's
      // text). The error stays up until the next successful save.
      setError(e instanceof Error ? e.message : "Could not save this document.");
      setStatus("error");
    } finally {
      savingRef.current = false;
      if (pendingRef.current) {
        pendingRef.current = false;
        void runSave();
      }
    }
  }, [onSave]);

  const scheduleSave = useCallback(() => {
    if (timerRef.current) clearTimeout(timerRef.current);
    timerRef.current = setTimeout(() => {
      timerRef.current = null;
      void runSave();
    }, AUTOSAVE_DEBOUNCE_MS);
  }, [runSave]);

  const flushSave = useCallback(() => {
    if (timerRef.current) {
      clearTimeout(timerRef.current);
      timerRef.current = null;
    }
    void runSave();
  }, [runSave]);

  // Leaving the page (Esc, breadcrumb, browser back) with a debounce still
  // pending must not drop the last few keystrokes — flush once on unmount,
  // best-effort (the component is gone regardless of whether it resolves).
  useEffect(() => {
    return () => {
      if (timerRef.current) {
        clearTimeout(timerRef.current);
        void runSave();
      }
      clearSavedFade();
    };
  }, [runSave]);

  return (
    <div className="fleet-task-page">
      <div className="fleet-task-page-main">
        <div className="fleet-task-page-body">
          <div className="fleet-doc-header-row">
            {canWrite ? (
              <input
                className="fleet-doc-title-input"
                value={draftTitle}
                maxLength={200}
                placeholder="Untitled document"
                aria-label="Document title"
                onChange={(e) => {
                  setDraftTitle(e.currentTarget.value);
                  scheduleSave();
                }}
                onBlur={flushSave}
                onKeyDown={(e) => {
                  if (e.key === "Enter") {
                    e.preventDefault();
                    e.currentTarget.blur();
                  }
                }}
              />
            ) : (
              <h2 className="fleet-task-page-title" tabIndex={-1} ref={headingRef}>
                {document.title || "Untitled document"}
              </h2>
            )}

            {/* Write-gated, right end of the header row, aligned with the
                title — see DocumentMenu's own header for why this replaced
                the standing red Delete button. `null` (still resolving)
                hides it too, same contract as the title/body controls.
                DocumentMenu owns the delete call's own busy/error state —
                it is a distinct action from the autosave this view's own
                `status`/`error` track, and conflating the two would mislabel
                a failed delete as "Couldn't save". */}
            {canWrite ? <DocumentMenu onDelete={onDelete} /> : null}
          </div>

          <div className={`fleet-doc-meta${status === "error" ? " fleet-doc-meta--error" : ""}`}>
            {status === "saving"
              ? "Saving…"
              : status === "saved"
                ? "Saved"
                : status === "error"
                  ? `Couldn't save — ${error || "try again"}`
                  : `Updated ${timeAgo(document.updated_at)}`}
          </div>

          {canWrite ? (
            <textarea
              className="fleet-doc-editor"
              value={draftBody}
              placeholder="Write in markdown…"
              spellCheck={false}
              aria-label="Document body"
              onChange={(e) => {
                setDraftBody(e.currentTarget.value);
                scheduleSave();
              }}
              onBlur={flushSave}
            />
          ) : document.body?.trim() ? (
            <div className="fleet-doc-body">
              <MarkdownLite text={document.body} />
            </div>
          ) : (
            <p className="fleet-doc-empty-body">This document is empty.</p>
          )}
        </div>
      </div>
    </div>
  );
}

/**
 * The "⋯" overflow trigger + its menu — reuses the SAME row-menu primitive
 * HardwareSection.tsx's own HardwareRowMenu already established
 * (`.fleet-list-row-menu-wrap` / `.fleet-list-row-menu` /
 * `.fleet-list-row-menu-item[--danger]`, defined once in fleet-theme.css)
 * rather than inventing a third dropdown implementation on top of that one
 * and TaskLabelEditor's `.fleet-composer-pop`. The trigger itself borrows
 * `.fleet-task-detail-icon-btn` (task-detail.css) instead of
 * `.fleet-list-row-menu-trigger`, because that class only reveals on
 * row-hover — right for a list row, wrong for a page header action that
 * must be reachable without hovering a whole row first.
 */
function DocumentMenu({ onDelete }: { onDelete: () => Promise<void> }) {
  const [open, setOpen] = useState(false);
  // Inline confirm swap INSIDE the menu, not a modal — see file header.
  const [confirming, setConfirming] = useState(false);
  const [deleting, setDeleting] = useState(false);
  // This menu's OWN error, distinct from DocumentDetailView's autosave
  // `status`/`error` — a failed delete is a different action with a
  // different message, and showing it here (next to the control that
  // caused it) keeps it from being mislabeled as a save failure.
  const [error, setError] = useState<string | null>(null);
  const ref = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    if (!open) return;
    const onPointerDown = (e: PointerEvent) => {
      if (ref.current?.contains(e.target as Node)) return;
      setOpen(false);
    };
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        // Stopped so the page's own Escape handler (back to Documents) does
        // not also fire — closing this menu and leaving the page on one
        // keypress would be the classic double-dismiss bug.
        e.stopPropagation();
        setOpen(false);
      }
    };
    window.addEventListener("pointerdown", onPointerDown);
    window.addEventListener("keydown", onKeyDown, true);
    return () => {
      window.removeEventListener("pointerdown", onPointerDown);
      window.removeEventListener("keydown", onKeyDown, true);
    };
  }, [open]);

  // Closing the menu always drops a pending confirm (and any error it
  // surfaced) — reopening it always starts from a plain "Delete", never
  // mid-confirmation or showing a stale failure from a previous attempt.
  useEffect(() => {
    if (!open) {
      setConfirming(false);
      setError(null);
    }
  }, [open]);

  const handleDelete = useCallback(async () => {
    if (deleting) return;
    setDeleting(true);
    setError(null);
    try {
      await onDelete();
      // On success the caller navigates away (documents/[documentId]/page.tsx's
      // handleDelete pushes back to the list) — nothing left to reset here.
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not delete this document.");
      setDeleting(false);
    }
  }, [deleting, onDelete]);

  return (
    <div className="fleet-list-row-menu-wrap" ref={ref}>
      <button
        type="button"
        className="fleet-task-detail-icon-btn"
        aria-haspopup="menu"
        aria-expanded={open}
        aria-label="Document actions"
        onClick={() => setOpen((v) => !v)}
      >
        <MoreHorizontal size={16} strokeWidth={1.75} />
      </button>
      {open ? (
        <div className="fleet-list-row-menu" role="menu">
          {confirming ? (
            <div className="fleet-doc-menu-confirm">
              <span className="fleet-doc-menu-confirm-text">Delete document?</span>
              <div className="fleet-doc-menu-confirm-actions">
                <button
                  type="button"
                  className="fleet-list-row-menu-item"
                  disabled={deleting}
                  onClick={() => setConfirming(false)}
                >
                  Cancel
                </button>
                <button
                  type="button"
                  className="fleet-list-row-menu-item fleet-list-row-menu-item--danger"
                  disabled={deleting}
                  onClick={() => void handleDelete()}
                >
                  {deleting ? "Deleting…" : "Delete"}
                </button>
              </div>
              {error ? (
                <span className="fleet-doc-menu-confirm-error" role="alert">
                  {error}
                </span>
              ) : null}
            </div>
          ) : (
            <button
              type="button"
              role="menuitem"
              className="fleet-list-row-menu-item fleet-list-row-menu-item--danger"
              onClick={() => setConfirming(true)}
            >
              Delete
            </button>
          )}
        </div>
      ) : null}
    </div>
  );
}
