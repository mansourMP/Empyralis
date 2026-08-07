"use client";

/**
 * "New document" — asks for a title only, same "paper, not a form" idiom
 * TaskComposer.tsx established (no bordered field, no label — the
 * placeholder says everything a label would have), just without that
 * file's property chips: a document has no status/priority/assignee to set
 * before filing it, only a title and a body, and the body is what the plain
 * markdown textarea on the document's own page is for (CLAUDE.md: create/
 * edit both land there, not duplicated into this dialog too). Created
 * empty; the reader opens it and clicks Edit to start writing.
 *
 * Reuses .fleet-composer-backdrop / .fleet-composer / .fleet-composer-head /
 * .fleet-composer-close / .fleet-composer-paper / .fleet-composer-title
 * wholesale from fleet-theme.css — the exact same modal chrome TaskComposer
 * draws, just without the chips row, the description field, or the
 * "Create more" switch this surface has no use for.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { X } from "lucide-react";

import { createFleetDocument, type FleetDocument } from "./documents-data";

export function DocumentComposer({
  workspaceId,
  projectId,
  projectName,
  onClose,
  onCreated,
}: {
  workspaceId: string;
  projectId: string;
  projectName?: string;
  onClose: () => void;
  onCreated: (document: FleetDocument) => void;
}) {
  const [title, setTitle] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const titleRef = useRef<HTMLTextAreaElement | null>(null);

  useEffect(() => {
    titleRef.current?.focus();
  }, []);

  const autosize = (el: HTMLTextAreaElement | null) => {
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${el.scrollHeight}px`;
  };

  const canCreate = title.trim().length > 0 && !busy;

  const create = useCallback(async () => {
    const clean = title.trim();
    if (!clean || busy) return;
    setBusy(true);
    setError(null);
    try {
      const document = await createFleetDocument(workspaceId, { project_id: projectId, title: clean });
      onCreated(document);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not create this document.");
    } finally {
      setBusy(false);
    }
  }, [busy, onCreated, projectId, title, workspaceId]);

  return (
    <div
      className="fleet-composer-backdrop"
      onMouseDown={onClose}
      onKeyDown={(e) => {
        if (e.key === "Escape") {
          e.stopPropagation();
          onClose();
        } else if (e.key === "Enter" && !e.shiftKey) {
          e.preventDefault();
          void create();
        }
      }}
    >
      <div
        className="fleet-composer"
        role="dialog"
        aria-modal="true"
        aria-label="New document"
        onMouseDown={(e) => e.stopPropagation()}
      >
        <div className="fleet-composer-head">
          <span className="fleet-composer-crumb">
            {projectName ? <span className="fleet-composer-crumb-project">{projectName}</span> : null}
            {projectName ? <span aria-hidden>›</span> : null}
            <span>New document</span>
          </span>
          <button type="button" className="fleet-composer-close" onClick={onClose} aria-label="Close">
            <X size={14} strokeWidth={2} />
          </button>
        </div>

        <div className="fleet-composer-paper">
          <textarea
            ref={titleRef}
            className="fleet-composer-title"
            placeholder="Document title"
            rows={1}
            maxLength={200}
            value={title}
            disabled={busy}
            onChange={(e) => {
              setTitle(e.currentTarget.value.replace(/\n/g, ""));
              autosize(e.currentTarget);
            }}
          />
        </div>

        {error ? (
          <p className="fleet-composer-error" role="alert">
            {error}
          </p>
        ) : null}

        <div className="fleet-composer-foot">
          <span />
          <button
            type="button"
            className="fleet-btn fleet-btn--accent-fill"
            onClick={() => void create()}
            disabled={!canCreate}
          >
            {busy ? "Creating…" : "Create document"}
          </button>
        </div>
      </div>
    </div>
  );
}
