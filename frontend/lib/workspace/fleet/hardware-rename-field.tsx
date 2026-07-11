"use client";

import { useEffect, useRef, useState } from "react";
import { Pencil } from "lucide-react";

import { buildCookieAuthHeaders } from "@/lib/auth/csrf";

/** Rename a paired box's nickname via a dedicated pencil button, not the
 *  title text itself. The title used to be the click target for rename —
 *  but on the Hardware list, the title sits inside a row whose OWN click
 *  opens the machine-detail page, and the rename button's necessary
 *  stopPropagation() silently ate that click. Renaming now only starts from
 *  the pencil icon; the title itself is inert and lets the row's click
 *  through. gatewayLabel() (used everywhere this registration is displayed —
 *  the Hardware tab's "Running on" header, box pickers, the machine detail
 *  page, etc.) already prefers display_name over the derived "Provider ·
 *  Region" label, so setting it here is the whole fix. Shared by the
 *  Hardware list and the machine detail page — one rename control, not two. */
export function HardwareRenameField({
  gatewayId,
  displayName,
  onRenamed,
}: {
  gatewayId: string;
  displayName: string;
  onRenamed: (next: string) => void;
}) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(displayName);
  const [saving, setSaving] = useState(false);
  const inputRef = useRef<HTMLInputElement | null>(null);

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
      <button
        type="button"
        className="fleet-list-row-title-rename-btn"
        title="Rename this computer"
        aria-label={`Rename ${displayName}`}
        onClick={(e) => { e.stopPropagation(); setEditing(true); }}
      >
        <Pencil size={11} strokeWidth={1.75} aria-hidden="true" />
      </button>
    </span>
  );
}
