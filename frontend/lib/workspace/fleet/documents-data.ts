"use client";

import { fleetAuthorizedFetch } from "@/lib/workspace/fleet/fleet-authorized-fetch";

// Project documents (MAN-115 follow-up) — a project's own flat markdown
// knowledge base. Backend contract (server_modules/routes_fleet.py, verified
// file:line at the time this was written):
//   GET    /api/w/{workspace_id}/fleet/documents?project_id=...          fleet_list_documents            :1071
//   GET    /api/w/{workspace_id}/fleet/documents/{document_id}           fleet_get_document              :1104
//   GET    /api/w/{workspace_id}/fleet/documents/{document_id}/revisions fleet_list_document_revisions   :1243
//   POST   /api/w/{workspace_id}/fleet/documents                         fleet_create_document           :1137
//   PATCH  /api/w/{workspace_id}/fleet/documents/{document_id}           fleet_patch_document             :1180
//   DELETE /api/w/{workspace_id}/fleet/documents/{document_id}           fleet_delete_document            :1217
//
// The revisions route closes a "built, tested, and never wired" gap
// (CLAUDE.md): project_document_revisions (patch-native diffs,
// changed_by_type human/agent/external_agent/system) shipped with its only
// caller being mcp_server.py's empyralis_list_document_revisions -- an
// AGENT-only tool. A human editing a document had no way to see what
// changed, who changed it, or when, until this route + useFleetDocument
// Revisions below existed.
//
// Response shape: {"ok": true, "document"/"documents": ...} on success,
// {"ok": false, "error": "..."} on a caught business-logic failure — never a
// raised HTTPException for those. Auth failures (not a project viewer/member)
// DO raise, so every caller below checks `res.ok` too, not just `data.ok`.
// Same contract project-members-data.ts already follows for the sibling
// project_memberships routes.
//
// LIST OMITS BODY (project_documents_repository.list_documents's own
// include_body=False default — see fleet_list_documents' docstring): a list
// row never carries `body`. Fetch a single document (fetchFleetDocument) for
// its content. This mirrors FleetTask's list/detail split in spirit, except
// tasks always carry description inline and documents deliberately do not —
// a project can hold many documents and a sidebar/list read should never
// ship every one's full markdown body over the wire.
//
// NOT POLLED, unlike useFleetTasks. Every write below calls back into the
// caller's own refresh — the same "plain fetch + manual refresh" choice
// project-members-data.ts's useProjectMembers makes.
//
// THIS FILE USED TO JUSTIFY THAT BY CLAIMING a project's documents are
// "edited by whoever has the page open, not mutated out from under the
// reader by an agent." THAT WAS FALSE, and the false half was load-bearing:
// document__edit (skills_service.py) and empyralis_edit_document /
// empyralis_update_document (mcp_server.py) all mutate these rows, so an
// agent edit landing while somebody had the page open was silently
// overwritten by the next autosave — which PATCHes the WHOLE body from a
// draft snapshotted at page load — and the revision history then recorded
// the reversion as the HUMAN's own edit.
//
// The fix is NOT polling (a refetch landing mid-keystroke fights the
// in-place editor; see the document page's own note). It is a stale-write
// PRECONDITION: every body-bearing read carries `state_sha256`, every patch
// sends it back as `base_sha256`, and the server refuses a write whose base
// has moved on — HTTP 409 + DocumentConflictError below, resolved by the
// person rather than by whichever writer happened to be last. Not polling
// is now a choice this file can defend, instead of one it was getting away
// with.

import { useCallback, useEffect, useState } from "react";

import { buildCookieAuthHeaders } from "@/lib/auth/csrf";

export type FleetDocument = {
  id: string;
  project_id: string | null;
  title: string;
  slug: string;
  /** Present on a single-document read (GET .../documents/{id}) and on the
   *  create/patch responses. Absent on a list row — see the file header. */
  body?: string;
  /** The stale-write precondition token for THIS exact title+body
   *  (project_documents_repository.document_state_sha256). Present on every
   *  read that carries a body and absent on a list row, for the same reason
   *  `body` is: the token covers title AND body, so a bodyless row could
   *  only ever carry a wrong one. Send it back as `base_sha256` on a patch
   *  to make that write a compare-and-swap. */
  state_sha256?: string;
  created_by: string | null;
  updated_by: string | null;
  metadata: Record<string, unknown>;
  created_at: string | null;
  updated_at: string | null;
};

/** Same shape project_documents_repository._row_to_document already returns
 *  — defensive coercion only, no renaming, so a field this file doesn't
 *  recognize still passes through untouched for a future caller. */
function normalizeDocument(raw: any): FleetDocument {
  return {
    id: String(raw?.id || ""),
    project_id: raw?.project_id ? String(raw.project_id) : null,
    title: String(raw?.title || ""),
    slug: String(raw?.slug || ""),
    ...(typeof raw?.body === "string" ? { body: raw.body as string } : {}),
    ...(typeof raw?.state_sha256 === "string" && raw.state_sha256
      ? { state_sha256: raw.state_sha256 as string }
      : {}),
    created_by: raw?.created_by ? String(raw.created_by) : null,
    updated_by: raw?.updated_by ? String(raw.updated_by) : null,
    metadata: raw?.metadata && typeof raw.metadata === "object" ? raw.metadata : {},
    created_at: raw?.created_at ?? null,
    updated_at: raw?.updated_at ?? null,
  };
}

/** Same "[object Object]" trap fleet-data.ts's own apiErrorMessage guards
 *  against — a caught {ok:false,error:"..."} is a plain string, but an auth
 *  or validation failure comes back as FastAPI's `detail`, which can be an
 *  object or a list. */
function apiErrorMessage(data: unknown, fallback: string): string {
  const body = (data || {}) as Record<string, unknown>;
  const raw = body.error ?? body.detail;
  if (typeof raw === "string" && raw.trim()) return raw.trim();
  if (Array.isArray(raw)) {
    const first = raw.find((item) => typeof (item as { msg?: unknown })?.msg === "string");
    if (first) return String((first as { msg: string }).msg);
  }
  if (raw && typeof raw === "object") {
    const nested = raw as Record<string, unknown>;
    for (const key of ["message", "error", "detail", "reason"]) {
      const value = nested[key];
      if (typeof value === "string" && value.trim()) return value.trim();
    }
  }
  return fallback;
}

/** A write that was REFUSED because the document moved on since it was read
 *  — never a failure to retry, and never a success. A distinct Error
 *  subclass rather than a message the caller has to pattern-match, because
 *  the client genuinely has to BRANCH here: every other save failure means
 *  "try again", and this one means "stop, a person has to choose." A
 *  conflict wearing the same clothes as a network blip is a conflict the
 *  autosave loop will retry straight over the top of. */
export class DocumentConflictError extends Error {
  /** What is on the server RIGHT NOW, body included — so the person can be
   *  shown the version they would have overwritten instead of only being
   *  told one exists. */
  readonly current: FleetDocument;

  constructor(message: string, current: FleetDocument) {
    super(message);
    this.name = "DocumentConflictError";
    this.current = current;
  }
}

/** The stable CODE the backend sends for a refused stale write
 *  (routes_fleet.fleet_patch_document). Matched on the code, never on the
 *  prose — this codebase already lost five weeks to an error bucket that
 *  matched a sentence somebody later reworded. */
const DOCUMENT_CONFLICT_CODE = "document_conflict";

async function documentsRequest(
  workspaceId: string,
  method: string,
  suffix: string,
  body?: Record<string, unknown>,
): Promise<any> {
  const path = `/api/w/${encodeURIComponent(workspaceId)}/fleet/documents${suffix}`;
  const res = await fleetAuthorizedFetch(path, {
    method,
    credentials: "include",
    headers: buildCookieAuthHeaders(method, { "Content-Type": "application/json" }),
    body: body ? JSON.stringify(body) : undefined,
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok || data?.ok === false) {
    // The conflict branch first: a 409 carries a whole document, and
    // collapsing it into the generic Error below would leave the caller
    // with a sentence and nothing to render. Shape is the platform error
    // envelope every raised HTTPException goes through
    // (error_response_service): {detail, error:{code, message, details}},
    // where `details` holds every key of the route's own detail dict
    // except `code`/`message`.
    const platformError = (data as { error?: Record<string, unknown> })?.error;
    if (res.status === 409 && platformError && platformError.code === DOCUMENT_CONFLICT_CODE) {
      const details = (platformError.details || {}) as Record<string, unknown>;
      const current = details.document;
      if (current && typeof current === "object") {
        throw new DocumentConflictError(
          apiErrorMessage(data, "This document changed while you were editing."),
          normalizeDocument(current),
        );
      }
    }
    throw new Error(apiErrorMessage(data, `HTTP ${res.status}`));
  }
  return data;
}

/** A project's documents, alphabetically by title (the backend's own order —
 *  a table of contents, not a feed). No `body` on these rows; see the file
 *  header. */
export function useFleetDocuments(workspaceId: string, projectId: string | null) {
  const [documents, setDocuments] = useState<FleetDocument[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    if (!workspaceId || !projectId) {
      setLoading(false);
      return;
    }
    setLoading(true);
    setError(null);
    try {
      const data = await documentsRequest(
        workspaceId,
        "GET",
        `?project_id=${encodeURIComponent(projectId)}`,
      );
      const items = Array.isArray(data?.documents) ? data.documents.map(normalizeDocument) : [];
      setDocuments(items);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not load this project's documents.");
    } finally {
      setLoading(false);
    }
  }, [workspaceId, projectId]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  return { documents, loading, error, refresh };
}

/** One document, WITH its body — the read a detail page needs, never served
 *  by the list route (see the file header). */
export async function fetchFleetDocument(workspaceId: string, documentId: string): Promise<FleetDocument> {
  const data = await documentsRequest(workspaceId, "GET", `/${encodeURIComponent(documentId)}`);
  return normalizeDocument(data.document);
}

export async function createFleetDocument(
  workspaceId: string,
  input: { project_id: string; title: string; body?: string },
): Promise<FleetDocument> {
  const data = await documentsRequest(workspaceId, "POST", "", {
    project_id: input.project_id,
    title: input.title,
    body: input.body ?? "",
  });
  return normalizeDocument(data.document);
}

/** `base_sha256` is the `state_sha256` of the document state this edit was
 *  composed on top of. Sending it makes the write a compare-and-swap: it
 *  lands only if nothing changed underneath, and otherwise throws
 *  DocumentConflictError carrying what is actually there. Omitting it is an
 *  UNCONDITIONAL overwrite of whatever an agent may have written in the
 *  meantime — allowed (the wire field is optional so an existing integration
 *  keeps working) but never what this app's own document page does. */
export async function patchFleetDocument(
  workspaceId: string,
  documentId: string,
  patch: { title?: string; body?: string; base_sha256?: string },
): Promise<FleetDocument> {
  const data = await documentsRequest(workspaceId, "PATCH", `/${encodeURIComponent(documentId)}`, patch);
  return normalizeDocument(data.document);
}

export async function deleteFleetDocument(workspaceId: string, documentId: string): Promise<boolean> {
  const data = await documentsRequest(workspaceId, "DELETE", `/${encodeURIComponent(documentId)}`);
  return Boolean(data?.ok);
}

/** "My Notes" -> "My Notes (copy)" for the "⋯" menu's Duplicate action
 *  (DocumentDetailView.tsx) -- pure so it can be unit-tested without a DOM
 *  (see documents-data.test.ts). Falls back to a fixed label for a blank
 *  title so a duplicated document is never left with an empty one; there is
 *  no independent default on the create_document call site for this,
 *  unlike a document created from the list view's own "New document" flow. */
export function duplicateDocumentTitle(title: string): string {
  const trimmed = (title || "").trim() || "Untitled document";
  return `${trimmed} (copy)`;
}

/** Filesystem-safe `.md` filename for the "⋯" menu's Export action -- there
 *  is no server round-trip to validate this, so whatever this returns goes
 *  straight onto the browser's `<a download>` attribute. Strips the
 *  characters invalid on Windows/macOS/Linux path segments (a title is free
 *  text and commonly contains `/` or `:`), collapses whitespace, and falls
 *  back to the document's slug and then a fixed name so an untitled
 *  document still downloads something sane instead of a bare ".md". */
export function documentExportFilename(title: string, slug: string): string {
  const base = (title || "").trim() || (slug || "").trim() || "document";
  const safe = base.replace(/[\\/:*?"<>|]+/g, "-").replace(/\s+/g, " ").trim();
  return `${safe || "document"}.md`;
}

/** One entry in a document's history -- the same shape
 *  project_documents_repository._row_to_revision returns, `body` omitted
 *  (the revisions route never requests include_body=True; see
 *  fleet_list_document_revisions' own docstring for why there is no
 *  restore surface to feed). `diff` is a human-readable unified diff
 *  against the immediately-prior revision -- "this line changed," the
 *  founder's own framing -- and is null only for the rare case where
 *  nothing textual changed (see _compute_document_diff's own docstring).
 *  `changed_by_type` reuses project_tasks_service.add_task_comment's exact
 *  author_type vocabulary (human / agent / external_agent / system) --
 *  DocumentHistory.tsx resolves it the same way TaskDetailView.tsx already
 *  resolves a comment's author. */
export type FleetDocumentRevision = {
  id: string;
  document_id: string;
  project_id: string | null;
  title: string;
  diff: string | null;
  changed_by_type: string;
  changed_by_id: string | null;
  changed_by_display_name: string | null;
  revision_number: number;
  created_at: string | null;
};

function normalizeDocumentRevision(raw: any): FleetDocumentRevision {
  return {
    id: String(raw?.id || ""),
    document_id: String(raw?.document_id || ""),
    project_id: raw?.project_id ? String(raw.project_id) : null,
    title: String(raw?.title || ""),
    diff: typeof raw?.diff === "string" ? raw.diff : null,
    changed_by_type: String(raw?.changed_by_type || "unknown"),
    changed_by_id: raw?.changed_by_id ? String(raw.changed_by_id) : null,
    changed_by_display_name: raw?.changed_by_display_name ? String(raw.changed_by_display_name) : null,
    revision_number: Number.isFinite(Number(raw?.revision_number)) ? Number(raw.revision_number) : 0,
    created_at: raw?.created_at ?? null,
  };
}

/** A document's history, newest first. NOT polled (same reasoning
 *  useFleetDocuments gives for its own plain fetch: a document's history
 *  changes only when someone on THIS page saves). `refreshKey` -- pass
 *  something that changes when a save lands (DocumentHistory.tsx passes
 *  the document's own `updated_at`) and this hook re-fetches, the same way
 *  documents/[documentId]/page.tsx already folds a save's response
 *  straight into `document` for the body/title above. Without this, a
 *  document opened once and edited three times in the same sitting would
 *  show a History list stuck at whatever existed the moment the page
 *  loaded -- correct after a reload, stale until one. Loading starts
 *  `true` and only ever the caller decides what to render while it's in
 *  flight or empty -- DocumentHistory.tsx renders nothing at all for a
 *  document with one or zero revisions (CLAUDE.md: no dead controls), so
 *  this hook does not try to guess that itself. */
export function useFleetDocumentRevisions(workspaceId: string, documentId: string, refreshKey?: string | null) {
  const [revisions, setRevisions] = useState<FleetDocumentRevision[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    if (!workspaceId || !documentId) {
      setLoading(false);
      return;
    }
    setLoading(true);
    setError(null);
    try {
      const data = await documentsRequest(workspaceId, "GET", `/${encodeURIComponent(documentId)}/revisions`);
      const items = Array.isArray(data?.revisions) ? data.revisions.map(normalizeDocumentRevision) : [];
      setRevisions(items);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not load this document's history.");
    } finally {
      setLoading(false);
    }
  }, [workspaceId, documentId]);

  useEffect(() => {
    void refresh();
    // eslint-disable-next-line react-hooks/exhaustive-deps -- refreshKey is
    // intentionally an EXTRA trigger alongside `refresh`'s own identity
    // (workspaceId/documentId), not a value this effect reads.
  }, [refresh, refreshKey]);

  return { revisions, loading, error, refresh };
}
