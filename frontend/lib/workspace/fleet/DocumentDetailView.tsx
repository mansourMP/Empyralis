"use client";

/**
 * A project document's own page — read it rendered as markdown, or edit its
 * title/body as a plain markdown textarea. Structural sibling of
 * TaskDetailView.tsx (a real page, not a drawer, so it deep-links and works
 * with browser back/forward) but far smaller: a document has no status,
 * assignee, or comment thread — just a title and a body, and CLAUDE.md is
 * explicit editing that body is a plain textarea, never a rich editor.
 *
 * WRITE GATE: `canWrite` decides whether Edit/Delete render at all — a
 * `viewer` never sees a dead control (CLAUDE.md), it simply isn't in the
 * DOM. `null` (still resolving the caller's role) also hides both, so a
 * viewer never sees them blink into view for a moment on load — same
 * contract useCanWriteProject's own doc comment establishes.
 *
 * Esc returns to the project's Documents list, mirroring TaskDetailView's
 * identical behavior (ignored while a control has focus, so Escaping out of
 * the edit textarea doesn't also navigate away — it just blurs).
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { Pencil, Trash2, X } from "lucide-react";

import type { FleetDocument } from "./documents-data";
import { MarkdownLite } from "./markdown-lite";
import { timeAgo } from "./fleet-presentation";
import "./document-detail.css";

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
  /** null while still resolving — Edit/Delete stay unrendered rather than
   *  flash on then off (see file header). */
  canWrite: boolean | null;
  onSave: (patch: { title: string; body: string }) => Promise<void>;
  onDelete: () => Promise<void>;
}) {
  const router = useRouter();
  const headingRef = useRef<HTMLHeadingElement | null>(null);
  const [editing, setEditing] = useState(false);
  const [draftTitle, setDraftTitle] = useState(document.title);
  const [draftBody, setDraftBody] = useState(document.body ?? "");
  const [saving, setSaving] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // A fresh open of edit mode is the source of truth for the draft — same
  // choice ProjectSettings' rename popover makes (its own comment names the
  // reason: a stale edit from a previous open must never linger into this
  // one).
  useEffect(() => {
    if (!editing) {
      setDraftTitle(document.title);
      setDraftBody(document.body ?? "");
    }
  }, [document.id, document.title, document.body, editing]);

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

  const startEdit = useCallback(() => {
    setDraftTitle(document.title);
    setDraftBody(document.body ?? "");
    setError(null);
    setEditing(true);
  }, [document.title, document.body]);

  const cancelEdit = useCallback(() => {
    setDraftTitle(document.title);
    setDraftBody(document.body ?? "");
    setError(null);
    setEditing(false);
  }, [document.title, document.body]);

  const save = useCallback(async () => {
    const title = draftTitle.trim();
    if (!title || saving) return;
    setSaving(true);
    setError(null);
    try {
      await onSave({ title, body: draftBody });
      setEditing(false);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not save this document.");
    } finally {
      setSaving(false);
    }
  }, [draftTitle, draftBody, onSave, saving]);

  const remove = useCallback(async () => {
    if (deleting) return;
    setDeleting(true);
    setError(null);
    try {
      await onDelete();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not delete this document.");
      setDeleting(false);
    }
  }, [onDelete, deleting]);

  const canSave = draftTitle.trim().length > 0 && !saving;

  return (
    <div className="fleet-task-page">
      <div className="fleet-task-page-main">
        <div className="fleet-task-page-body">
          {editing ? (
            <input
              className="fleet-doc-title-input"
              value={draftTitle}
              maxLength={200}
              disabled={saving}
              placeholder="Document title"
              autoFocus
              onChange={(e) => setDraftTitle(e.currentTarget.value)}
              onKeyDown={(e) => {
                if (e.key === "Escape") {
                  e.preventDefault();
                  e.stopPropagation();
                  cancelEdit();
                }
              }}
            />
          ) : (
            <h2 className="fleet-task-page-title" tabIndex={-1} ref={headingRef}>
              {document.title || "Untitled document"}
            </h2>
          )}

          {!editing && (
            <div className="fleet-doc-meta">
              Updated {timeAgo(document.updated_at)}
            </div>
          )}

          <div className="fleet-doc-actions">
            {editing ? (
              <>
                <button
                  type="button"
                  className="fleet-btn fleet-btn--accent-fill"
                  onClick={() => void save()}
                  disabled={!canSave}
                >
                  {saving ? "Saving…" : "Save"}
                </button>
                <button type="button" className="fleet-btn" onClick={cancelEdit} disabled={saving}>
                  Cancel
                </button>
              </>
            ) : canWrite ? (
              <>
                <button type="button" className="fleet-btn" onClick={startEdit}>
                  <Pencil size={14} strokeWidth={1.75} /> Edit
                </button>
                <button
                  type="button"
                  className="fleet-btn fleet-btn--danger-outline"
                  onClick={() => void remove()}
                  disabled={deleting}
                >
                  <Trash2 size={14} strokeWidth={1.75} /> {deleting ? "Deleting…" : "Delete"}
                </button>
              </>
            ) : null}
          </div>

          {error ? <p className="fleet-doc-error" role="alert">{error}</p> : null}

          {editing ? (
            <textarea
              className="fleet-doc-editor"
              value={draftBody}
              disabled={saving}
              placeholder="Write in markdown…"
              spellCheck={false}
              onChange={(e) => setDraftBody(e.currentTarget.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) {
                  e.preventDefault();
                  void save();
                }
              }}
            />
          ) : document.body?.trim() ? (
            <div className="fleet-doc-body">
              <MarkdownLite text={document.body} />
            </div>
          ) : (
            <p className="fleet-doc-empty-body">
              This document is empty.{canWrite ? " Click Edit to write into it." : ""}
            </p>
          )}
        </div>
      </div>
    </div>
  );
}
