"use client";

import { fleetAuthorizedFetch } from "@/lib/workspace/fleet/fleet-authorized-fetch";

import { useEffect, useRef, useState } from "react";
import { Bug } from "lucide-react";

import { buildCookieAuthHeaders } from "@/lib/auth/csrf";
import { getErrorMessage } from "@/lib/ui/api-error";

const MAX_TITLE = 200;
const MAX_DESCRIPTION = 5000;

async function submitBugReport(
  workspaceId: string,
  input: { title: string; description: string; page_path: string }
): Promise<void> {
  const res = await fleetAuthorizedFetch(`/api/w/${workspaceId}/fleet/bug-reports`, {
    method: "POST",
    credentials: "include",
    headers: buildCookieAuthHeaders("POST", { "Content-Type": "application/json" }),
    body: JSON.stringify(input),
  });
  const data = await res.json().catch(() => ({}));
  // Route returns {ok:false,error} with HTTP 200 on a service-level
  // failure -- see routes_fleet.py's fleet_create_bug_report -- so res.ok
  // alone would silently swallow a rejected submission.
  if (!res.ok || data?.ok === false) {
    throw new Error(getErrorMessage(data, `Could not send report (HTTP ${res.status})`));
  }
}

/**
 * "Report a bug" rail control (MAN-106) -- a small icon button living in
 * .fleet-rail-controls alongside System health (see PrimaryRail.tsx and
 * SystemHealthButton.tsx, the closest sibling this deliberately copies:
 * same anchored-popover-off-an-icon-button idiom, not a new modal/dialog
 * pattern). Unlike that one, the popover here is a small form rather than
 * a read-only panel -- title + description, submitted to
 * POST /api/w/{workspaceId}/fleet/bug-reports, which persists a durable row
 * in the `bug_reports` table (server_modules/bug_report_service.py). No
 * ticketing workflow: submit, see a one-line confirmation, done.
 *
 * Diagnostic context is captured automatically (current page path, browser
 * user-agent set server-side from the request header, workspace id implicit
 * in the URL, timestamp implicit in created_at) so the reporter never has to
 * type "this happened on the Agents page" themselves.
 */
export function BugReportButton({ workspaceId }: { workspaceId: string }) {
  const [open, setOpen] = useState(false);
  const [title, setTitle] = useState("");
  const [description, setDescription] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [sent, setSent] = useState(false);
  const ref = useRef<HTMLDivElement | null>(null);
  const closeTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  function reset() {
    setTitle("");
    setDescription("");
    setError(null);
    setSent(false);
    setSubmitting(false);
  }

  function close() {
    setOpen(false);
    if (closeTimerRef.current) {
      clearTimeout(closeTimerRef.current);
      closeTimerRef.current = null;
    }
    reset();
  }

  useEffect(() => {
    if (!open) return;
    const onPointerDown = (event: PointerEvent) => {
      if (ref.current?.contains(event.target as Node)) return;
      close();
    };
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") close();
    };
    window.addEventListener("pointerdown", onPointerDown);
    window.addEventListener("keydown", onKeyDown);
    return () => {
      window.removeEventListener("pointerdown", onPointerDown);
      window.removeEventListener("keydown", onKeyDown);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  useEffect(() => {
    return () => {
      if (closeTimerRef.current) clearTimeout(closeTimerRef.current);
    };
  }, []);

  async function handleSubmit(event: React.FormEvent) {
    event.preventDefault();
    const trimmedTitle = title.trim();
    if (!trimmedTitle || submitting) return;
    setSubmitting(true);
    setError(null);
    try {
      await submitBugReport(workspaceId, {
        title: trimmedTitle,
        description: description.trim(),
        page_path: typeof window !== "undefined" ? window.location.pathname : "",
      });
      setSent(true);
      closeTimerRef.current = setTimeout(close, 1600);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not send report.");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div ref={ref} className="fleet-bugreport-float">
      {open && (
        <div className="fleet-bugreport-popover" role="dialog" aria-label="Report a bug">
          {sent ? (
            <p className="fleet-bugreport-sent">Thanks -- your report was sent.</p>
          ) : (
            <form onSubmit={handleSubmit}>
              <div className="fleet-bugreport-popover-title">Report a bug</div>
              <input
                type="text"
                className="fleet-wizard-input"
                placeholder="What went wrong?"
                value={title}
                maxLength={MAX_TITLE}
                onChange={(event) => setTitle(event.target.value)}
                autoFocus
                required
              />
              <textarea
                className="fleet-bugreport-textarea"
                placeholder="Any details that would help us reproduce it (optional)"
                value={description}
                maxLength={MAX_DESCRIPTION}
                onChange={(event) => setDescription(event.target.value)}
                rows={3}
              />
              {error && <p className="fleet-bugreport-error">{error}</p>}
              <div className="fleet-bugreport-actions">
                <button type="button" className="fleet-btn" onClick={close}>
                  Cancel
                </button>
                <button
                  type="submit"
                  className="fleet-btn fleet-btn--accent"
                  disabled={submitting || !title.trim()}
                >
                  {submitting ? "Sending…" : "Send"}
                </button>
              </div>
            </form>
          )}
        </div>
      )}
      <button
        type="button"
        className={`fleet-rail-control-btn${open ? " is-active" : ""}`}
        onClick={() => setOpen((v) => !v)}
        title="Report a bug"
        aria-label="Report a bug"
        aria-haspopup="dialog"
        aria-expanded={open}
      >
        <Bug size={16} strokeWidth={1.75} />
      </button>
    </div>
  );
}
