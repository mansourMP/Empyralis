"use client";

/**
 * A project document's own page — read it rendered as markdown, or (with
 * write access) edit its title/body directly in place. Structural sibling of
 * TaskDetailView.tsx (a real page, not a drawer, so it deep-links and works
 * with browser back/forward) but far smaller: a document has no status,
 * assignee, or comment thread — just a title and a body.
 *
 * RENDERED BY DEFAULT, FOR EVERYONE (2026-08-12 fix). The first pass got
 * "no mode toggle" right and something else badly wrong: it made a
 * `canWrite` reader's title/body ALWAYS-LIVE form controls — an <input> and
 * a <textarea> — so the person the product is FOR (the document's own
 * author, the one with write access) never once saw `# Heading`, `**bold**`
 * or a `| pipe | table |` actually render. Only a read-only viewer got
 * MarkdownLite. Two founder complaints ("this is documents I'm telling this
 * again... yet it renders differently") before anyone found it. Fixed by
 * flipping the default: the rendered view is what LOADS, for a writer and a
 * viewer alike, and editing is entered deliberately per region (title,
 * body) — click the text, it becomes a field; blur or Escape, it flushes
 * and goes back to being text. This is still direct manipulation, not the
 * mode-toggle button the founder rejected on the first pass — there is no
 * "Edit" button anywhere, no global read/write flip, just TaskDetailView's
 * own click-to-edit idiom (`editingTitle`/`editingDescription`, the
 * skipBlurCommit ref pattern also in FleetAgentDetail.tsx's AgentTitle)
 * applied here as `editingTitle`/`editingBody`. There is still no Save
 * button: typing schedules an autosave (see AUTOSAVE_DEBOUNCE_MS below),
 * and blurring either field flushes it immediately AND returns to the
 * rendered view. CLAUDE.md is explicit this stays a plain markdown-source
 * textarea, never a rich/WYSIWYG editor.
 *
 * ONE EDITOR OVER THE WHOLE BODY, not per-block. A document has no block
 * model today (MarkdownLite parses text straight into React elements, it
 * doesn't keep an editable node per block) and `empyralis_edit_document`'s
 * old_string/new_string MCP contract plus the diff-based revision history
 * both operate on the body as one string — splitting the editor per block
 * would mean either inventing that block model or reassembling one string
 * from N boxes on every keystroke. Clicking anywhere in the rendered body
 * opens the same single textarea the direct-editing rework already built;
 * exiting it re-renders the (possibly larger) whole.
 *
 * BOTH REGIONS RENDER FROM THE DRAFT, NEVER FROM `document.title`/
 * `document.body`, even outside edit mode. If a save fails, the draft
 * (`draftTitle`/`draftBody`) is what stays on screen (the file's own
 * pre-existing rule: "a failed save never clears or reverts what was
 * typed") — rendering from the stale server prop after exiting edit mode
 * would have silently un-shown a change the person just made. A `viewer`
 * never diverges the draft from the prop at all (no write access, nothing
 * to type), so this is a no-op for them.
 *
 * A `viewer` (no write access) gets plain rendered markdown (MarkdownLite)
 * and a plain heading, with NO editing affordance anywhere — no input, no
 * cursor change, no click target, no placeholder text hinting it could be
 * clicked into. `canWrite === null` (still resolving) reads as `false` here
 * too, so a viewer never sees an editable control flash into view for a
 * moment on load — same contract useCanWriteProject's own doc comment
 * establishes.
 *
 * ESCAPE FLUSHES, IT DOES NOT REVERT — the one deliberate deviation from
 * TaskDetailView's own title/description Escape handling, which resets the
 * draft back to the last-known server value and discards whatever was
 * typed. A document autosaves while open; discarding on Escape would throw
 * away keystrokes typed inside the debounce window (up to
 * AUTOSAVE_DEBOUNCE_MS) that the person has no reason to think are at risk
 * — "typing autosaves, clicking away commits, with no lost characters" is a
 * hard requirement here in a way it never was for a task's single-shot
 * title/description commit. Escape still reuses the exact same
 * skipTitleBlur/skipBodyBlur ref mechanics (so the field's own onBlur
 * doesn't double-fire the exit), it just calls flushSave() instead of
 * resetting the draft.
 *
 * THE "⋯" MENU lives in the breadcrumb TOPBAR (via HeaderAction), not in the
 * page's own header row. It used to sit at the right edge of
 * .fleet-task-page-body — correct relative to ITS OWN column, wrong on a
 * wide screen: that column is capped at 720px and left-aligned (a task page
 * shares this width to leave room for its Properties sidebar; a document has
 * no sidebar, see the centering rule in document-detail.css), so on a
 * ~1730px screen the menu sat visually mid-page, nowhere near "the top" a
 * person would look for it. HeaderAction is the same portal FleetAgentDetail
 * and the Agents/Projects list pages already use for their own primary
 * action — reused, not reinvented — and it renders on the SAME row as the
 * breadcrumb, genuinely at the top of the page regardless of column width.
 * DELETE moved off the page into this menu originally (the founder's second
 * complaint was a standing red Delete button doing nothing but wait to be
 * misclicked); Copy link / Duplicate / Export as .md joined it (see
 * DocumentMenu below for which and why). Delete's confirmation is still a
 * small inline swap INSIDE the menu ("Delete" → "Delete document? /
 * Cancel"), not a blocking modal — this codebase's approval-gate rule
 * (CLAUDE.md) is about workflow approval states, not this: a destructive
 * action asking "are you sure" once, inline, is a different thing and stays.
 *
 * Esc returns to the project's Documents list, mirroring TaskDetailView's
 * identical behavior (ignored while a control has focus, so Escaping out of
 * the body textarea doesn't also navigate away — it just blurs, which also
 * flushes any pending autosave).
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { MoreHorizontal, Pencil } from "lucide-react";

import {
  createFleetDocument,
  documentExportFilename,
  duplicateDocumentTitle,
  DocumentConflictError,
  type FleetDocument,
} from "./documents-data";
import {
  diffDocumentBodies,
  planDocumentConflict,
  type DiffLine,
  type DocumentConflictPlan,
} from "./document-conflict";
import { MarkdownLite } from "@/lib/workspace/markdown-lite";
import { timeAgo } from "./fleet-presentation";
import { DocumentHistory } from "./DocumentHistory";
import { HeaderAction } from "./Breadcrumbs";
import type { FleetAgent } from "./fleet-data";
import type { WorkspaceMember } from "./members-data";
import "./document-detail.css";

/** Grow the editor to fit its content so the PAGE scrolls, never a box
 *  inside the page. The old fixed-height textarea scrolled internally, which
 *  is the clearest "this is a form field, not a document" signal there is —
 *  every document editor people actually use (Linear, Notion, Google Docs)
 *  grows downward and lets the page take the scroll.
 *
 *  Height is reset to "auto" before measuring: scrollHeight can only ever be
 *  read as >= the element's current height, so without the reset the editor
 *  would grow as you type and never shrink back when you delete. */
function autosizeEditor(el: HTMLTextAreaElement | null): void {
  if (!el) return;
  el.style.height = "auto";
  el.style.height = `${el.scrollHeight}px`;
}

// Long enough that a normal typing cadence never fires a save mid-word,
// short enough that stopping to think for a beat is enough to persist.
const AUTOSAVE_DEBOUNCE_MS = 900;
// How long the "Saved" confirmation holds before quietly folding back into
// the plain "Updated {time}" line — long enough to register, not so long it
// reads as stuck.
const SAVED_DISPLAY_MS = 2000;

// "conflict" is deliberately its OWN status rather than a flavour of
// "error": a save that failed and a save that was REFUSED because somebody
// else changed the document are two different facts, and only one of them is
// fixed by trying again (CLAUDE.md's standing "failed / couldn't confirm /
// succeeded never share one message" law). Collapsing them would put
// "Couldn't save — try again" over a document that is perfectly healthy and
// a change that is safely still on screen.
type SaveStatus = "idle" | "saving" | "saved" | "error" | "conflict";

export function DocumentDetailView({
  document,
  projectHref,
  canWrite,
  workspaceId,
  agents,
  members,
  identityLookupFailed,
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
  /** Threaded down to DocumentHistory (see that file's own header) purely
   *  for its own fetch + actor resolution — this view never reads them
   *  itself. */
  workspaceId: string;
  agents: FleetAgent[];
  members: WorkspaceMember[];
  /** True when the agents/members fetch itself failed — threaded straight
   *  through to DocumentHistory (see TaskDetailView.tsx's identical prop
   *  for the full reasoning: a failed lookup must read as a failed lookup,
   *  never as an anonymous "Someone"). This view never reads it itself. */
  identityLookupFailed?: boolean;
  /** Resolves with the SAVED document, whose `state_sha256` is the
   *  precondition for the next autosave — without it the second save of a
   *  session would conflict with the first one's own result. Rejects with
   *  DocumentConflictError when the write was refused because the document
   *  moved on. */
  onSave: (patch: { title: string; body: string; base_sha256?: string }) => Promise<FleetDocument>;
  onDelete: () => Promise<void>;
}) {
  const router = useRouter();
  const headingRef = useRef<HTMLHeadingElement | null>(null);
  const editorRef = useRef<HTMLTextAreaElement | null>(null);
  const titleInputRef = useRef<HTMLInputElement | null>(null);

  // ── Per-region click-to-edit ────────────────────────────────────────────
  // Two independent toggles (title, body) rather than one "editing"
  // boolean — TaskDetailView's own editingTitle/editingDescription split,
  // reused here as editingTitle/editingBody. Both regions still share the
  // SAME draftTitle/draftBody + autosave machinery below: the backend PATCH
  // (`onSave({title, body})`) always writes both fields together, so
  // opening one region for editing must not disturb whatever's pending in
  // the other. skipTitleBlur/skipBodyBlur are the identical skipBlurCommit
  // ref AgentTitle (FleetAgentDetail.tsx) and TaskDetailView's own title/
  // description edits already use, so Escape's own exit doesn't also let
  // the field's onBlur fire a second, redundant flush.
  const [editingTitle, setEditingTitle] = useState(false);
  const [editingBody, setEditingBody] = useState(false);
  const skipTitleBlur = useRef(false);
  const skipBodyBlur = useRef(false);

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
  // THE STALE-WRITE PRECONDITION. The document state this draft was composed
  // on top of; sent as `base_sha256` on every save so the server can refuse
  // a write whose base has moved on instead of blindly overwriting an
  // agent's edit with a body snapshotted when the page opened. Re-seeded
  // from every successful save's own response — a session's second autosave
  // would otherwise conflict with what its first one wrote.
  const baseShaRef = useRef<string | undefined>(document.state_sha256);

  const [status, setStatus] = useState<SaveStatus>("idle");
  const [error, setError] = useState<string | null>(null);
  // The incoming version, held so the person can SEE what they would have
  // overwritten. Non-null is what stops the autosave loop — see runSave.
  const [conflict, setConflict] = useState<FleetDocument | null>(null);
  const [showConflictDiff, setShowConflictDiff] = useState(false);

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
    baseShaRef.current = document.state_sha256;
    setStatus("idle");
    setError(null);
    // A conflict belongs to the document it was raised on. Carrying one
    // across a navigation would offer someone a choice about a document
    // they are no longer looking at.
    setConflict(null);
    setShowConflictDiff(false);
    // A different document opening mid-edit (task nav arrows have no
    // document-page equivalent today, but Cmd+K / browser back can still
    // swap `document` out without unmounting this view) must not leave a
    // stale editing region open over the NEW document's own draft.
    setEditingTitle(false);
    setEditingBody(false);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [document.id]);

  // Focus the page's own content on open — screen-reader/SPA-navigation
  // convention, unconditional now (same as TaskDetailView's identical
  // effect): both the viewer's plain heading AND a writer's click-to-edit
  // button render as a real <h2> by default, so there is no longer a
  // canWrite branch here to auto-focus text INTO — that risk only existed
  // when a canWrite reader's title was an always-live <input> (see file
  // header). A control only becomes focused/editable because someone
  // clicked it; this only moves screen-reader focus to the heading.
  useEffect(() => {
    headingRef.current?.focus();
  }, [document.id]);

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

  // Non-null while a conflict is waiting on the person. Mirrors the
  // `conflict` state so the debounce timer and the in-flight retry below
  // read it without closing over a stale render — the same reason
  // draftRef/lastSavedRef exist.
  const conflictRef = useRef(false);

  const runSave = useCallback(async () => {
    // A conflict is unresolved — every save from here would be refused for
    // the same reason, so stop firing them. Typing stays completely free;
    // it just stops being autosaved until the person decides. Retrying into
    // a 409 on every keystroke would turn one honest question into a
    // stream of failures.
    if (conflictRef.current) return;
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
      const saved = await onSave({ title, body, base_sha256: baseShaRef.current });
      lastSavedRef.current = { title, body };
      // The state we just wrote becomes the base for the next save.
      baseShaRef.current = saved.state_sha256;
      setStatus("saved");
      savedFadeRef.current = setTimeout(() => setStatus("idle"), SAVED_DISPLAY_MS);
    } catch (e) {
      if (e instanceof DocumentConflictError) {
        // NOTHING was written and NOTHING is discarded. The draft stays
        // exactly as typed; the incoming version is held so the person can
        // look at both and choose. An identical-content write is not a
        // conflict at all (planDocumentConflict decides that) — in that
        // case just adopt the new base and let the save through.
        const plan = planDocumentConflict(
          { title, body },
          { title: e.current.title, body: e.current.body ?? "" },
        );
        baseShaRef.current = e.current.state_sha256;
        if (!plan.needsResolution) {
          lastSavedRef.current = { title, body };
          setStatus("saved");
          savedFadeRef.current = setTimeout(() => setStatus("idle"), SAVED_DISPLAY_MS);
        } else {
          conflictRef.current = true;
          setConflict(e.current);
          setStatus("conflict");
        }
      } else {
        // The draft is untouched here — a failed save never clears or reverts
        // what was typed (CLAUDE.md-adjacent: never silently lose the user's
        // text). The error stays up until the next successful save.
        setError(e instanceof Error ? e.message : "Could not save this document.");
        setStatus("error");
      }
    } finally {
      savingRef.current = false;
      if (pendingRef.current) {
        pendingRef.current = false;
        void runSave();
      }
    }
  }, [onSave]);

  // ── Resolving a conflict ────────────────────────────────────────────────
  // Two explicit ways out, neither of them a default, and the product picks
  // NEITHER on the person's behalf. See document-conflict.ts's header for
  // why an automatic three-way merge is deliberately not built here.
  const resolveKeepMine = useCallback(() => {
    // Rebase onto the state the refusal handed back and save the person's
    // version. The other version is not destroyed — it is already a
    // revision, visible in this document's own history.
    conflictRef.current = false;
    setConflict(null);
    setShowConflictDiff(false);
    setStatus("saving");
    // lastSavedRef is deliberately NOT touched: it still holds what the
    // server last confirmed, so runSave's own "nothing changed" early
    // return cannot swallow this retry.
    void runSave();
  }, [runSave]);

  const resolveTakeTheirs = useCallback(() => {
    // Replaces the draft with the incoming version. This DOES discard what
    // the person typed — it was never saved, so no revision holds it —
    // which is exactly why the button says so and why nothing reaches this
    // path without a click.
    const incoming = conflict;
    if (!incoming) return;
    const title = incoming.title;
    const body = incoming.body ?? "";
    setDraftTitle(title);
    setDraftBody(body);
    lastSavedRef.current = { title, body };
    baseShaRef.current = incoming.state_sha256;
    conflictRef.current = false;
    setConflict(null);
    setShowConflictDiff(false);
    // Idle, not "saved": nothing was written. Saying "Saved" here would be
    // the same lie in a new costume.
    setStatus("idle");
    setError(null);
  }, [conflict, setDraftTitle, setDraftBody]);

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

  // Who changed it, in the words a person uses. Resolved off the SAME
  // agents/members lists DocumentHistory already resolves a revision's
  // actor from — `updated_by` is an opaque id and must never be shown as
  // one. An unresolved actor becomes "Someone else" inside
  // planDocumentConflict, never a guess and never the raw id.
  const conflictActorName = useMemo(() => {
    const id = String(conflict?.updated_by || "").trim();
    if (!id) return null;
    const agent = agents.find((a) => a.agent_id === id);
    if (agent?.label) return agent.label;
    const member = members.find((m) => m.user_id === id);
    if (member) return member.display_name || member.email || null;
    return null;
  }, [conflict?.updated_by, agents, members]);

  const conflictPlan: DocumentConflictPlan | null = useMemo(() => {
    if (!conflict) return null;
    return planDocumentConflict(
      { title: draftTitle.trim() || "Untitled document", body: draftBody },
      { title: conflict.title, body: conflict.body ?? "" },
      conflictActorName,
    );
  }, [conflict, draftTitle, draftBody, conflictActorName]);

  const conflictDiff = useMemo(() => {
    if (!conflict || !showConflictDiff) return null;
    return diffDocumentBodies(draftBody, conflict.body ?? "");
  }, [conflict, showConflictDiff, draftBody]);

  // ── Entering each region's edit mode ────────────────────────────────────
  // Same shape as TaskDetailView's enterTitleEdit/enterDescriptionEdit:
  // flip the toggle, then focus (and for the title, select — replacing the
  // whole title is the common case) on the next paint, once the control
  // actually exists in the DOM.
  const enterTitleEdit = useCallback(() => {
    if (!canWrite) return;
    setEditingTitle(true);
    requestAnimationFrame(() => {
      titleInputRef.current?.focus();
      titleInputRef.current?.select();
    });
  }, [canWrite]);

  const enterBodyEdit = useCallback(() => {
    if (!canWrite) return;
    setEditingBody(true);
    // autosizeEditor here replaces the old "size the editor to the document
    // it just loaded" effect — the textarea no longer mounts on load at
    // all, only once this fires, so sizing it belongs here instead. Same
    // reasoning as that effect's own comment: without this an existing
    // document would open its editor at min-height with its own internal
    // scrollbar, the exact box this surface was rebuilt to stop being.
    requestAnimationFrame(() => {
      autosizeEditor(editorRef.current);
      editorRef.current?.focus();
    });
  }, [canWrite]);

  // Copies the page's own URL — available to a viewer too (read-only access
  // is exactly when "let me hand you a link" comes up). navigator.clipboard
  // is undefined over plain http:// in some browsers; DocumentMenu's own
  // click handler is fire-and-forget either way, same as the pattern
  // MembersSection.tsx already uses for its invite-link copy button.
  const handleCopyLink = useCallback(async () => {
    await navigator.clipboard?.writeText(window.location.href);
  }, []);

  // Downloads exactly what's on screen right now (draftRef, not the last-
  // saved `document` prop) — no network round trip, so there's nothing to
  // fail. window.document, not the bare `document` global: this component's
  // own `document` PROP shadows it (see the Esc handler above, which
  // already has to do the same thing).
  const handleExport = useCallback(() => {
    const blob = new Blob([draftRef.current.body], { type: "text/markdown;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const link = window.document.createElement("a");
    link.href = url;
    link.download = documentExportFilename(draftRef.current.title, document.slug);
    window.document.body.appendChild(link);
    link.click();
    link.remove();
    URL.revokeObjectURL(url);
  }, [document.slug]);

  // canWrite-only (DocumentMenu never renders this item for a viewer).
  // Duplicates the DRAFT, not the last-saved document — a duplicate should
  // match what the person is looking at, not silently drop whatever they
  // typed in the seconds before the autosave debounce (900ms) settles.
  const [duplicating, setDuplicating] = useState(false);
  const [duplicateError, setDuplicateError] = useState<string | null>(null);
  const handleDuplicate = useCallback(async () => {
    if (duplicating) return;
    setDuplicating(true);
    setDuplicateError(null);
    try {
      const created = await createFleetDocument(workspaceId, {
        project_id: document.project_id || "",
        title: duplicateDocumentTitle(draftRef.current.title),
        body: draftRef.current.body,
      });
      // No `finally` resetting `duplicating` on success — this component is
      // about to unmount on navigation, and flipping it back to false first
      // would let the menu item flash "Duplicate" again for one frame.
      router.push(`${projectHref}/${encodeURIComponent(created.id)}`);
    } catch (e) {
      setDuplicateError(e instanceof Error ? e.message : "Could not duplicate this document.");
      setDuplicating(false);
    }
  }, [duplicating, workspaceId, document.project_id, projectHref, router]);

  return (
    <div className="fleet-task-page fleet-doc-detail-page">
      {/* canWrite === null (still resolving) renders nothing here too — same
          contract as the title/body controls below, so the menu doesn't pop
          into the topbar a beat after the rest of the page. */}
      {canWrite !== null ? (
        <HeaderAction>
          <DocumentMenu
            canWrite={canWrite}
            onCopyLink={handleCopyLink}
            onExport={handleExport}
            onDuplicate={canWrite ? handleDuplicate : undefined}
            duplicating={duplicating}
            duplicateError={duplicateError}
            onDelete={canWrite ? onDelete : undefined}
          />
        </HeaderAction>
      ) : null}
      <div className="fleet-task-page-main">
        <div className="fleet-task-page-body">
          <div className="fleet-doc-header-row">
            {/* Same h2-always, swap-the-inside idiom as TaskDetailView's own
                title (see that file around line 977): tabIndex tracks
                whether the CONTROL inside is what should own tab order
                (editing — the real <input> is focusable on its own,
                `undefined` leaves the h2 out of the tab sequence) or the
                heading itself is the programmatically-focused target
                (not editing — `-1`, focusable via headingRef.focus() for
                the screen-reader announce above, never via Tab). */}
            <h2 className="fleet-task-page-title" tabIndex={editingTitle ? undefined : -1} ref={headingRef}>
              {editingTitle ? (
                <input
                  ref={titleInputRef}
                  className="fleet-task-page-title-input"
                  value={draftTitle}
                  maxLength={200}
                  placeholder="Untitled document"
                  aria-label="Document title"
                  onChange={(e) => {
                    setDraftTitle(e.currentTarget.value);
                    scheduleSave();
                  }}
                  onBlur={() => {
                    if (skipTitleBlur.current) {
                      skipTitleBlur.current = false;
                      return;
                    }
                    flushSave();
                    setEditingTitle(false);
                  }}
                  onKeyDown={(e) => {
                    if (e.key === "Enter") {
                      e.preventDefault();
                      e.currentTarget.blur();
                    } else if (e.key === "Escape") {
                      // Flushes rather than reverting — see this file's own
                      // header ("ESCAPE FLUSHES, IT DOES NOT REVERT") for why
                      // this deliberately differs from TaskDetailView's own
                      // Escape handling.
                      e.preventDefault();
                      skipTitleBlur.current = true;
                      flushSave();
                      setEditingTitle(false);
                    }
                  }}
                />
              ) : canWrite ? (
                <button type="button" className="fleet-task-page-title-edit" onClick={enterTitleEdit}>
                  {draftTitle || "Untitled document"}
                  <Pencil size={14} strokeWidth={1.75} className="fleet-task-page-title-pencil" />
                </button>
              ) : (
                draftTitle || "Untitled document"
              )}
            </h2>
          </div>

          {/* Directly under the title, small and muted (12px, --text-muted)
              — assessed as part of this same pass and kept, not moved.
              Two reasons: it is Notion/Linear's own byline placement for
              "who/when touched this", and unlike a static byline it is ALSO
              this view's live autosave status (Saving…/Saved/error) — the
              one piece of feedback that must stay visible right next to the
              field someone is typing into. Moving it up into the topbar
              alongside the "⋯" menu would separate that feedback from the
              editing surface it reports on. */}
          <div className={`fleet-doc-meta${status === "error" ? " fleet-doc-meta--error" : ""}`}>
            {status === "saving"
              ? "Saving…"
              : status === "saved"
                ? "Saved"
                : status === "error"
                  ? `Couldn't save — ${error || "try again"}`
                  : status === "conflict"
                    ? // NOT "Couldn't save". Nothing failed and nothing was
                      // lost — the write was refused and the banner below
                      // holds the decision. Telling someone to retry here
                      // would send them at a save that can only be refused
                      // again.
                      "Paused — this document changed elsewhere"
                    : `Updated ${timeAgo(document.updated_at)}`}
          </div>

          {conflict && conflictPlan?.needsResolution ? (
            <section className="fleet-doc-conflict" aria-live="polite">
              <h3 className="fleet-doc-conflict-title">{conflictPlan.headline}</h3>
              <p className="fleet-doc-conflict-reassurance">{conflictPlan.reassurance}</p>
              <div className="fleet-doc-conflict-actions">
                {conflictPlan.actions.map((action) => (
                  <button
                    key={action.key}
                    type="button"
                    className={`fleet-btn${action.accent ? " fleet-btn--accent-fill" : ""}`}
                    onClick={action.key === "keep-mine" ? resolveKeepMine : resolveTakeTheirs}
                  >
                    {action.label}
                  </button>
                ))}
                <button
                  type="button"
                  className="fleet-doc-conflict-toggle"
                  onClick={() => setShowConflictDiff((v) => !v)}
                  aria-expanded={showConflictDiff}
                >
                  {showConflictDiff ? "Hide what changed" : "See what changed"}
                </button>
              </div>
              {/* The consequence rides on each option rather than in a
                  paragraph above them — a choice between two versions of
                  your own writing is decided at the button. */}
              <ul className="fleet-doc-conflict-consequences">
                {conflictPlan.actions.map((action) => (
                  <li key={action.key}>
                    <span className="fleet-doc-conflict-consequence-label">{action.label}</span>
                    {" — "}
                    {action.consequence}
                  </li>
                ))}
              </ul>
              {conflictDiff ? (
                <>
                  <pre className="fleet-doc-history-diff fleet-doc-conflict-diff">
                    {conflictDiff.lines.map((line: DiffLine, i: number) => (
                      <div
                        key={i}
                        className={
                          line.kind === "add"
                            ? "fleet-doc-diff-line fleet-doc-diff-line--add"
                            : line.kind === "remove"
                              ? "fleet-doc-diff-line fleet-doc-diff-line--remove"
                              : "fleet-doc-diff-line"
                        }
                      >
                        {line.kind === "add" ? "+ " : line.kind === "remove" ? "- " : "  "}
                        {line.text}
                      </div>
                    ))}
                  </pre>
                  <p className="fleet-doc-conflict-legend">
                    {"- is yours, + is theirs"}
                    {conflictDiff.truncated ? " · showing the first changes only" : ""}
                  </p>
                </>
              ) : null}
            </section>
          ) : null}

          {editingBody ? (
            <textarea
              ref={editorRef}
              className="fleet-doc-editor"
              value={draftBody}
              placeholder="Write in markdown…"
              spellCheck={false}
              aria-label="Document body"
              onChange={(e) => {
                setDraftBody(e.currentTarget.value);
                autosizeEditor(e.currentTarget);
                scheduleSave();
              }}
              onBlur={() => {
                if (skipBodyBlur.current) {
                  skipBodyBlur.current = false;
                  return;
                }
                flushSave();
                setEditingBody(false);
              }}
              onKeyDown={(e) => {
                // No Enter-submits here (a document body is many lines,
                // unlike the title) — only Escape has a special meaning.
                // Flushes rather than reverting, same as the title above;
                // see this file's own header for why.
                if (e.key === "Escape") {
                  e.preventDefault();
                  skipBodyBlur.current = true;
                  flushSave();
                  setEditingBody(false);
                }
              }}
            />
          ) : canWrite ? (
            <div
              className={`fleet-doc-body fleet-doc-body--editable${draftBody.trim() ? "" : " fleet-cell-muted"}`}
              role="button"
              tabIndex={0}
              aria-label={draftBody.trim() ? "Edit document body" : "Add document content"}
              onClick={(e) => {
                // A click inside the RENDERED body means "edit this" only
                // when it wasn't already a click on something. Two cases the
                // bare handler got wrong, both of which cost the person the
                // thing they were actually doing:
                //
                //  - A LINK. MarkdownLite renders `target="_blank"`, so the
                //    click both opened a tab and (bubbling to here) flipped
                //    the document behind it into a raw textarea. Come back
                //    from the new tab and your document is source again.
                //  - A TEXT SELECTION. Drag-selecting a paragraph to copy it
                //    ends in a click; swapping in the textarea discards the
                //    selection, so the document could not be quoted from
                //    without being edited first.
                if ((e.target as HTMLElement).closest("a")) return;
                if (!window.getSelection()?.isCollapsed) return;
                enterBodyEdit();
              }}
              onKeyDown={(e) => {
                if (e.key === "Enter" || e.key === " ") {
                  e.preventDefault();
                  enterBodyEdit();
                }
              }}
            >
              {draftBody.trim() ? <MarkdownLite text={draftBody} /> : "This document is empty. Click to start writing."}
            </div>
          ) : document.body?.trim() ? (
            <div className="fleet-doc-body">
              <MarkdownLite text={document.body} />
            </div>
          ) : (
            <p className="fleet-doc-empty-body">This document is empty.</p>
          )}

          <DocumentHistory
            workspaceId={workspaceId}
            documentId={document.id}
            updatedAt={document.updated_at}
            agents={agents}
            members={members}
            identityLookupFailed={identityLookupFailed}
          />
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
 * must be reachable without hovering a whole row first. Portaled into the
 * breadcrumb topbar via HeaderAction (see DocumentDetailView's own render —
 * the founder's "top of the page" complaint), not rendered inline here.
 *
 * FOUR ACTIONS, each earning its own place (CLAUDE.md: "best, not most" /
 * "a surface must earn its place") — every candidate that came up and got
 * cut is named below so the next pass doesn't re-litigate them from
 * scratch:
 *
 *  - Copy link / Export as .md — read-only, so both render for a VIEWER
 *    too (canWrite=false), not just a writer. Copy link writes the page's
 *    own URL; Export downloads exactly what's on screen as `.md`. Neither
 *    touches the network — nothing to fail, nothing to test against a
 *    fake backend.
 *  - Duplicate — canWrite only, since it's a write. Calls the SAME
 *    fleet_create_document route the "New document" list action already
 *    uses (documents-data.ts's createFleetDocument); nothing new on the
 *    backend.
 *  - Delete — unchanged from the first pass; still the only destructive
 *    item, still the only one behind a confirm swap.
 *
 *  CUT, and why:
 *  - Rename — the title is already a live, always-editable <input> for a
 *    canWrite reader (see this file's own header, "DIRECT MANIPULATION").
 *    A "Rename" menu item next to it would be the exact "why the fuck do I
 *    need to edit for" mode-toggle the founder rejected the first time,
 *    just moved one level down — a second way to do a thing that already
 *    has zero friction is not a feature, it's a decoy control.
 *  - Move to another project — needs a genuinely new backend concept
 *    (fleet_patch_document only accepts title/body today; moving a
 *    document means a destination-project membership check and extending
 *    update_document's COALESCE contract to a third column) rather than
 *    reusing an existing route the way Duplicate does. Per this task's own
 *    instruction ("build it properly with tests, or leave it out — never
 *    render a control that does nothing"), that is follow-up work, not a
 *    CSS-and-wiring pass; flagged separately rather than shipped half-done.
 */
function DocumentMenu({
  canWrite,
  onCopyLink,
  onExport,
  onDuplicate,
  duplicating,
  duplicateError,
  onDelete,
}: {
  canWrite: boolean;
  onCopyLink: () => void | Promise<void>;
  onExport: () => void;
  /** Present only when canWrite — the item itself is omitted otherwise,
   *  never rendered disabled (CLAUDE.md: no dead controls). */
  onDuplicate?: () => void;
  duplicating?: boolean;
  duplicateError?: string | null;
  /** Present only when canWrite — same reasoning as onDuplicate. */
  onDelete?: () => Promise<void>;
}) {
  const [open, setOpen] = useState(false);
  // Inline confirm swap INSIDE the menu, not a modal — see file header.
  const [confirming, setConfirming] = useState(false);
  const [deleting, setDeleting] = useState(false);
  // This menu's OWN delete error, distinct from DocumentDetailView's
  // autosave `status`/`error` — a failed delete is a different action with
  // a different message, and showing it here (next to the control that
  // caused it) keeps it from being mislabeled as a save failure.
  const [deleteError, setDeleteError] = useState<string | null>(null);
  // "Copy link" / "Copied!" label swap, same convention MembersSection.tsx's
  // invite-link copy button already uses — state change only, no icon
  // animation, reverts on its own so the menu never needs a second click to
  // reset it.
  const [copied, setCopied] = useState(false);
  const copyTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
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

  // Closing the menu always drops a pending confirm (and any error/copied
  // state it surfaced) — reopening it always starts clean, never
  // mid-confirmation or showing a stale failure from a previous attempt.
  useEffect(() => {
    if (!open) {
      setConfirming(false);
      setDeleteError(null);
      setCopied(false);
    }
  }, [open]);

  useEffect(() => {
    return () => {
      if (copyTimerRef.current) clearTimeout(copyTimerRef.current);
    };
  }, []);

  const handleCopyClick = useCallback(() => {
    void onCopyLink();
    setCopied(true);
    if (copyTimerRef.current) clearTimeout(copyTimerRef.current);
    copyTimerRef.current = setTimeout(() => setCopied(false), 1500);
  }, [onCopyLink]);

  const handleDelete = useCallback(async () => {
    if (deleting || !onDelete) return;
    setDeleting(true);
    setDeleteError(null);
    try {
      await onDelete();
      // On success the caller navigates away (documents/[documentId]/page.tsx's
      // handleDelete pushes back to the list) — nothing left to reset here.
    } catch (e) {
      setDeleteError(e instanceof Error ? e.message : "Could not delete this document.");
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
              {deleteError ? (
                <span className="fleet-doc-menu-confirm-error" role="alert">
                  {deleteError}
                </span>
              ) : null}
            </div>
          ) : (
            <>
              <button
                type="button"
                role="menuitem"
                className="fleet-list-row-menu-item"
                onClick={handleCopyClick}
              >
                {copied ? "Copied!" : "Copy link"}
              </button>
              {canWrite && onDuplicate ? (
                <button
                  type="button"
                  role="menuitem"
                  className="fleet-list-row-menu-item"
                  disabled={duplicating}
                  onClick={onDuplicate}
                >
                  {duplicating ? "Duplicating…" : "Duplicate"}
                </button>
              ) : null}
              <button
                type="button"
                role="menuitem"
                className="fleet-list-row-menu-item"
                onClick={() => {
                  onExport();
                  setOpen(false);
                }}
              >
                Export as .md
              </button>
              {duplicateError ? (
                <span className="fleet-list-row-menu-error" role="alert">
                  {duplicateError}
                </span>
              ) : null}
              {canWrite && onDelete ? (
                <>
                  <div className="fleet-list-row-menu-divider" />
                  <button
                    type="button"
                    role="menuitem"
                    className="fleet-list-row-menu-item fleet-list-row-menu-item--danger"
                    onClick={() => setConfirming(true)}
                  >
                    Delete
                  </button>
                </>
              ) : null}
            </>
          )}
        </div>
      ) : null}
    </div>
  );
}
