"use client";

/**
 * THE delete-agent confirmation. One dialog, two call sites (the agent
 * detail header's "⋯" menu, and AgentsList's row menu if that surface is
 * ever revived) — lifted out of AgentsList.tsx, where it was written inline
 * and then went unreachable with the rest of that file.
 *
 * Shape and copy are AgentsList's own, kept deliberately: the existing
 * `.fleet-detail-backdrop` + `.fleet-small-dialog` + Cancel/`.fleet-btn--danger`
 * shell every other confirm on this surface already uses (StopAgentConfirmDialog
 * next door is the same shell), so this introduces no new dialog vocabulary.
 * Rendered through a portal so the fixed overlay covers the viewport wherever
 * the trigger happens to sit — a menu item inside a scrolled header included.
 *
 * `--danger` is the one place a non-accent, saturated fill is right
 * (fleet-theme.css says so at `.fleet-btn--danger`). It is NOT an accent
 * control and must never become one: `agent-create-accent.ts` owns the
 * single accent fill in any agent view, and delete is not a create action.
 *
 * The dialog never decides ANYTHING — not whether the agent may be deleted
 * (`canDeleteAgent`, so the trigger is not rendered at all for the
 * workspace operator), not what happened (`deleteFleetAgent`'s three
 * outcomes). It shows a question and whatever sentence it is handed.
 */

import { useEffect } from "react";
import { createPortal } from "react-dom";

export function AgentDeleteDialog({
  agentName,
  busy,
  /** Whatever `deleteFleetAgent` said. A `refused` message and an
   *  `unconfirmed` one read differently on purpose and are passed through
   *  verbatim — collapsing them here would undo the whole point of having
   *  three outcomes. */
  error,
  onCancel,
  onConfirm,
}: {
  agentName: string;
  busy: boolean;
  error: string | null;
  onCancel: () => void;
  onConfirm: () => void;
}) {
  useEffect(() => {
    const onKeyDown = (e: globalThis.KeyboardEvent) => {
      if (e.key === "Escape" && !busy) {
        // Stopped so the page's own Escape handler doesn't also fire on the
        // keypress that just dismissed this.
        e.stopPropagation();
        onCancel();
      }
    };
    window.addEventListener("keydown", onKeyDown, true);
    return () => window.removeEventListener("keydown", onKeyDown, true);
  }, [busy, onCancel]);

  if (typeof document === "undefined") return null;

  return createPortal(
    <div
      className="fleet-detail-backdrop"
      onClick={() => {
        if (!busy) onCancel();
      }}
    >
      <div
        role="alertdialog"
        aria-modal="true"
        aria-labelledby="fleet-delete-agent-title"
        className="fleet-small-dialog"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="fleet-small-dialog-header">
          <span id="fleet-delete-agent-title" className="fleet-title">Delete agent</span>
        </div>
        <div className="fleet-small-dialog-body">
          <p style={{ margin: 0, fontSize: 13, color: "var(--text-primary)", lineHeight: 1.5 }}>
            Delete <strong>{agentName}</strong>? This removes its memory, credentials, and channel
            connections and can&apos;t be undone.
          </p>
          {error && <p style={{ margin: 0, fontSize: 12, color: "var(--offline-text)" }}>{error}</p>}
        </div>
        <div className="fleet-small-dialog-footer">
          <button type="button" className="fleet-btn" onClick={onCancel} disabled={busy}>
            Cancel
          </button>
          <button type="button" className="fleet-btn fleet-btn--danger" onClick={onConfirm} disabled={busy}>
            {busy ? "Deleting…" : "Delete agent"}
          </button>
        </div>
      </div>
    </div>,
    document.body,
  );
}
