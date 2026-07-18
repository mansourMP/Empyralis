"use client";

import { useEffect, useRef, useState } from "react";
import { Pencil } from "lucide-react";

import { buildCookieAuthHeaders } from "@/lib/auth/csrf";

/** Rename a paired box's nickname. The title text itself is inert (its
 *  container row may have its own click behavior — e.g. the Hardware list's
 *  row opens the machine-detail page — so a click on the title must not
 *  start an edit and must not be swallowed by one). gatewayLabel() (used
 *  everywhere this registration is displayed — the Hardware tab's "Running
 *  on" header, box pickers, the machine detail page, etc.) already prefers
 *  display_name over the derived "Provider · Region" label, so setting it
 *  here is the whole fix. Shared by the Hardware list and the machine
 *  detail page — one rename control, not two.
 *
 *  Editing can be triggered two ways:
 *   - Self-contained (default): omit `editing`/`onEditingChange` and this
 *     component owns its own state, entered via its own hover-reveal pencil
 *     button. This is what the machine-detail page uses.
 *   - Controlled: pass `editing` + `onEditingChange` and the caller decides
 *     when edit mode starts (e.g. a row's own "⋯" menu) — no pencil button
 *     is rendered in this mode since the caller owns the trigger. This is
 *     what the Hardware list's row menu uses. */
export function HardwareRenameField({
  gatewayId,
  displayName,
  onRenamed,
  editing: controlledEditing,
  onEditingChange,
}: {
  gatewayId: string;
  displayName: string;
  onRenamed: (next: string) => void;
  editing?: boolean;
  onEditingChange?: (editing: boolean) => void;
}) {
  const isControlled = controlledEditing !== undefined;
  const [internalEditing, setInternalEditing] = useState(false);
  const editing = isControlled ? controlledEditing : internalEditing;
  const [draft, setDraft] = useState(displayName);
  const [saving, setSaving] = useState(false);
  const inputRef = useRef<HTMLInputElement | null>(null);

  const setEditing = (next: boolean) => {
    if (isControlled) onEditingChange?.(next);
    else setInternalEditing(next);
  };

  useEffect(() => {
    if (!editing) setDraft(displayName);
  }, [displayName, editing]);

  useEffect(() => {
    if (editing) {
      inputRef.current?.focus();
      inputRef.current?.select();
    }
  }, [editing]);

  async function commit() {
    if (saving) return;
    const next = draft.trim();
    if (!next || next === displayName) {
      setDraft(displayName);
      setEditing(false);
      return;
    }
    setSaving(true);
    try {
      const res = await fetch(`/api/gateway/registrations/${encodeURIComponent(gatewayId)}/rename`, {
        method: "POST",
        credentials: "include",
        headers: buildCookieAuthHeaders("POST", { "Content-Type": "application/json" }),
        body: JSON.stringify({ display_name: next }),
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      setEditing(false);
      onRenamed(next);
    } catch {
      setDraft(displayName); // roll back — the row still shows the last-saved name
    } finally {
      setSaving(false);
    }
  }

  if (editing) {
    return (
      <input
        ref={inputRef}
        className="fleet-list-row-title-input"
        value={draft}
        disabled={saving}
        maxLength={80}
        onChange={(e) => setDraft(e.currentTarget.value)}
        onClick={(e) => e.stopPropagation()}
        onBlur={commit}
        onKeyDown={(e) => {
          if (e.key === "Enter") { e.preventDefault(); commit(); }
          if (e.key === "Escape") { setDraft(displayName); setEditing(false); }
        }}
      />
    );
  }

  return (
    <span className="fleet-list-row-title-display">
      <span className="fleet-list-row-title">{displayName}</span>
      {isControlled ? null : (
        <button
          type="button"
          className="fleet-list-row-title-rename-btn"
          title="Rename this computer"
          aria-label={`Rename ${displayName}`}
          onClick={(e) => { e.stopPropagation(); setEditing(true); }}
        >
          <Pencil size={11} strokeWidth={1.75} aria-hidden="true" />
        </button>
      )}
    </span>
  );
}
