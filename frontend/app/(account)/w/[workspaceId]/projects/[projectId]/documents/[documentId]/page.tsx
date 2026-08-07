"use client";

/**
 * A document's own page — /w/{ws}/projects/{projectId}/documents/{documentId}.
 * Structural sibling of tasks/[taskId]/page.tsx: this page owns the data and
 * the writes, DocumentDetailView draws the layout.
 *
 * UNLIKE useFleetTasks, documents are not read out of a shared polled cache
 * — the list route never carries a body (see documents-data.ts's own file
 * header), so a document's OWN page has to fetch it directly via
 * fetchFleetDocument. Refetched after every save/delete via the same
 * function, not merged optimistically — a document's body is a single
 * textarea a person is done typing into when they click Save, so there is
 * no "in-flight while polling" gap the tasks board's pendingStatus overlay
 * exists to paper over.
 */

import { useCallback, useEffect, useMemo, useState } from "react";
import { useParams, useRouter } from "next/navigation";

import { useFleetProjects } from "@/lib/workspace/fleet/fleet-data";
import { useWorkspaceMembers } from "@/lib/workspace/fleet/members-data";
import { useCanWriteProject } from "@/lib/workspace/fleet/project-members-data";
import {
  fetchFleetDocument,
  patchFleetDocument,
  deleteFleetDocument,
  type FleetDocument,
} from "@/lib/workspace/fleet/documents-data";
import { DocumentDetailView } from "@/lib/workspace/fleet/DocumentDetailView";
import { useBreadcrumbLabel, useBreadcrumbIcon } from "@/lib/workspace/fleet/Breadcrumbs";
import { ProjectIcon } from "@/lib/workspace/fleet/fleet-project-identity";
import { FleetListSkeleton } from "@/lib/workspace/fleet/fleet-states";

export default function DocumentDetailPage() {
  const params = useParams();
  const router = useRouter();
  const workspaceId = String(params?.workspaceId || "");
  const projectId = String(params?.projectId || "");
  const documentId = String(params?.documentId || "");
  const base = `/w/${encodeURIComponent(workspaceId)}`;
  const projectHref = `${base}/projects/${encodeURIComponent(projectId)}`;
  const documentsHref = `${projectHref}/documents`;

  const { members } = useWorkspaceMembers(workspaceId);
  const { projects } = useFleetProjects(workspaceId);
  const project = projects.find((p) => p.id === projectId);
  const canWrite = useCanWriteProject(workspaceId, projectId, members);

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

  const handleSave = useCallback(
    async (patch: { title: string; body: string }) => {
      await patchFleetDocument(workspaceId, documentId, patch);
      await load();
    },
    [workspaceId, documentId, load],
  );

  const handleDelete = useCallback(async () => {
    await deleteFleetDocument(workspaceId, documentId);
    router.push(documentsHref);
  }, [workspaceId, documentId, documentsHref, router]);

  if (loading && !document) {
    return (
      <main className="fleet-content fleet-content--chat">
        <div style={{ padding: "28px 32px" }}>
          <FleetListSkeleton rows={3} rowHeight={52} />
        </div>
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
        onSave={handleSave}
        onDelete={handleDelete}
      />
    </main>
  );
}
