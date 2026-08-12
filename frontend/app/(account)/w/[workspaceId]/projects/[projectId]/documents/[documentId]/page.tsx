"use client";

/**
 * A document's own page — /w/{ws}/projects/{projectId}/documents/{documentId}.
 * Structural sibling of tasks/[taskId]/page.tsx: this page owns the data and
 * the writes, DocumentDetailView draws the layout (and, since 2026-08-07,
 * the autosave debounce that decides WHEN a write fires — this file only
 * ever sends the patch it's handed).
 *
 * UNLIKE useFleetTasks, documents are not read out of a shared polled cache
 * — the list route never carries a body (see documents-data.ts's own file
 * header), so a document's OWN page has to fetch it directly via
 * fetchFleetDocument, once, on mount. A save does NOT refetch: handleSave
 * folds the PATCH response straight into `document` instead, because
 * DocumentDetailView's own autosave can fire every ~900ms while a person is
 * still typing the next word, and a refetch landing mid-keystroke would
 * fight the in-place editor over what's on screen. See that file's own note
 * on exactly when it does (and does not) reseed its draft from this prop.
 */

import { useCallback, useEffect, useMemo, useState } from "react";
import { useParams, useRouter } from "next/navigation";

import { useFleetProjects, useFleetAgents } from "@/lib/workspace/fleet/fleet-data";
import { useCanWriteProject } from "@/lib/workspace/fleet/project-members-data";
import { useWorkspaceMembers } from "@/lib/workspace/fleet/members-data";
import {
  fetchFleetDocument,
  patchFleetDocument,
  deleteFleetDocument,
  type FleetDocument,
} from "@/lib/workspace/fleet/documents-data";
import { DocumentDetailView } from "@/lib/workspace/fleet/DocumentDetailView";
import { useBreadcrumbLabel, useBreadcrumbIcon } from "@/lib/workspace/fleet/Breadcrumbs";
import { ProjectIcon } from "@/lib/workspace/fleet/fleet-project-identity";
import { FleetDocumentSkeleton } from "@/lib/workspace/fleet/fleet-states";

export default function DocumentDetailPage() {
  const params = useParams();
  const router = useRouter();
  const workspaceId = String(params?.workspaceId || "");
  const projectId = String(params?.projectId || "");
  const documentId = String(params?.documentId || "");
  const base = `/w/${encodeURIComponent(workspaceId)}`;
  const projectHref = `${base}/projects/${encodeURIComponent(projectId)}`;
  const documentsHref = `${projectHref}/documents`;

  const { projects } = useFleetProjects(workspaceId);
  const project = projects.find((p) => p.id === projectId);
  const canWrite = useCanWriteProject(workspaceId, projectId);
  // Both feed DocumentHistory's own actor resolution (who changed this
  // document -- see that file's own header) -- the identical pair
  // TaskDetailView already threads down for the same reason on a task's
  // Activity feed. useWorkspaceMembers, not useProjectMembers: a workspace
  // owner (the single most common human editor) has no project_memberships
  // row to resolve against (see DocumentHistory's own prop doc).
  const { agents } = useFleetAgents(workspaceId);
  const { members: workspaceMembers } = useWorkspaceMembers(workspaceId);

  const [document, setDocument] = useState<FleetDocument | null>(null);
  const [loading, setLoading] = useState(true);
  const [notFound, setNotFound] = useState(false);

  const load = useCallback(async () => {
    if (!workspaceId || !documentId) return;
    setLoading(true);
    setNotFound(false);
    try {
      const doc = await fetchFleetDocument(workspaceId, documentId);
      setDocument(doc);
    } catch {
      // A vanished document (deleted elsewhere, or a plain bad id) gets its
      // own state below rather than a silent bounce — see tasks/[taskId]'s
      // identical reasoning.
      setDocument(null);
      setNotFound(true);
    } finally {
      setLoading(false);
    }
  }, [workspaceId, documentId]);

  useEffect(() => {
    void load();
  }, [load]);

  useBreadcrumbLabel(projectId, project?.name);
  useBreadcrumbIcon(
    projectId,
    useMemo(
      () => (project ? <ProjectIcon icon={project.icon} tint={project.tint} size={16} /> : null),
      [project],
    ),
  );
  useBreadcrumbLabel(documentId, document?.title || undefined);

  // Applies an autosave patch and folds the server's own response straight
  // into `document` — NOT a `load()` refetch. Autosave fires on a ~900ms
  // debounce while a person may still be mid-keystroke on the next word; a
  // full refetch here would replace `document.title`/`document.body` on
  // every save, and DocumentDetailView reseeds its draft from those whenever
  // `document.id` changes... which it doesn't here (same id, same object
  // identity churn avoided) so this is safe by construction rather than by
  // coincidence — see that file's own note on why it only reseeds on a
  // genuinely different document.
  const handleSave = useCallback(
    async (patch: { title: string; body: string }) => {
      const updated = await patchFleetDocument(workspaceId, documentId, patch);
      setDocument(updated);
    },
    [workspaceId, documentId],
  );

  const handleDelete = useCallback(async () => {
    await deleteFleetDocument(workspaceId, documentId);
    router.push(documentsHref);
  }, [workspaceId, documentId, documentsHref, router]);

  if (loading && !document) {
    return (
      <main className="fleet-content fleet-content--chat">
        <FleetDocumentSkeleton />
      </main>
    );
  }

  if (notFound || !document) {
    return (
      <main className="fleet-content">
        <div className="fleet-empty">
          <div className="fleet-empty-title">Document not found</div>
          <div className="fleet-empty-desc">
            This document isn’t in this project any more. It may have been deleted, or the link
            may point at another project.
          </div>
          <div className="fleet-empty-actions">
            <button type="button" className="fleet-btn fleet-btn--accent-fill" onClick={() => router.push(documentsHref)}>
              Back to documents
            </button>
          </div>
        </div>
      </main>
    );
  }

  return (
    <main className="fleet-content fleet-content--chat">
      <DocumentDetailView
        document={document}
        projectHref={documentsHref}
        canWrite={canWrite}
        workspaceId={workspaceId}
        agents={agents}
        members={workspaceMembers}
        onSave={handleSave}
        onDelete={handleDelete}
      />
    </main>
  );
}
