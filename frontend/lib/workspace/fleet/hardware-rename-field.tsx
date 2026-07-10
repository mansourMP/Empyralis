"use client";

import { useEffect, useRef, useState } from "react";

import { buildCookieAuthHeaders } from "@/lib/auth/csrf";

/** Click-to-rename a paired box's nickname. gatewayLabel() (used everywhere
 *  this registration is displayed — the Hardware tab's "Running on" header,
 *  box pickers, the machine detail page, etc.) already prefers display_name
 *  over the derived "Provider · Region" label, so setting it here is the
 *  whole fix. Shared by the Hardware list and the machine detail page —
 *  one rename control, not two. */
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
    <button
      type="button"
      className="fleet-list-row-title-edit"
      title="Rename this computer"
      onClick={(e) => { e.stopPropagation(); setEditing(true); }}
    >
      {displayName}
    </button>
  );
}
