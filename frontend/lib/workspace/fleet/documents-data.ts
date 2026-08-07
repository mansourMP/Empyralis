"use client";

// Project documents (MAN-115 follow-up) — a project's own flat markdown
// knowledge base. Backend contract (server_modules/routes_fleet.py, verified
// file:line at the time this was written):
//   GET    /api/w/{workspace_id}/fleet/documents?project_id=...   fleet_list_documents   :1071
//   GET    /api/w/{workspace_id}/fleet/documents/{document_id}    fleet_get_document     :1104
//   POST   /api/w/{workspace_id}/fleet/documents                  fleet_create_document  :1137
//   PATCH  /api/w/{workspace_id}/fleet/documents/{document_id}    fleet_patch_document   :1180
//   DELETE /api/w/{workspace_id}/fleet/documents/{document_id}    fleet_delete_document  :1217
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
// NOT POLLED, unlike useFleetTasks. A project's documents are edited by
// whoever has the page open, not mutated out from under the reader by an
// agent working a board the way tasks are — project-members-data.ts's
// useProjectMembers makes the same "plain fetch + manual refresh" choice for
// the same reason. Every write below calls back into the caller's own
// refresh.

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

async function documentsRequest(
  workspaceId: string,
  method: string,
  suffix: string,
  body?: Record<string, unknown>,
): Promise<any> {
  const path = `/api/w/${encodeURIComponent(workspaceId)}/fleet/documents${suffix}`;
  const res = await fetch(path, {
    method,
    credentials: "include",
    headers: buildCookieAuthHeaders(method, { "Content-Type": "application/json" }),
    body: body ? JSON.stringify(body) : undefined,
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok || data?.ok === false) {
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

export async function patchFleetDocument(
  workspaceId: string,
  documentId: string,
  patch: { title?: string; body?: string },
): Promise<FleetDocument> {
  const data = await documentsRequest(workspaceId, "PATCH", `/${encodeURIComponent(documentId)}`, patch);
  return normalizeDocument(data.document);
}

export async function deleteFleetDocument(workspaceId: string, documentId: string): Promise<boolean> {
  const data = await documentsRequest(workspaceId, "DELETE", `/${encodeURIComponent(documentId)}`);
  return Boolean(data?.ok);
}
