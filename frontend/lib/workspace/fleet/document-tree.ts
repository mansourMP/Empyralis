/**
 * The document TREE — GIT'S MODEL IS THE SPEC (founder, approved design).
 *
 * A document carries a `path` with slashes ("specs/api/auth.md"). There are
 * NO FOLDER OBJECTS anywhere: a folder is an INFERRED PREFIX, exactly the
 * way git stores blobs by path and infers directories from them. Nothing in
 * this file, and nothing downstream of it, may create a folder entity, a
 * folder row, or a filesystem abstraction — a folder exists for precisely
 * as long as a document's path mentions it, and stops existing the moment
 * the last document under it moves away.
 *
 *   GitHub          Empyralis
 *     org             workspace     /w/{id}/context
 *     repo            project       one project's document tree
 *     file path       document.path specs/api/auth.md
 *
 * PURE — no React, no next/navigation, nothing that needs a DOM — so a
 * plain `tsx` test imports the REAL derivation instead of re-deriving it,
 * the same discipline channel-doors.ts / agent-count-shape.ts /
 * primary-rail-nav.ts already follow here (CLAUDE.md: "a check that derives
 * its own expectations from the thing it checks is blind, and reports
 * 'passed'").
 *
 * ORDERING is GitHub's own: directories before files, each alphabetically.
 * The comparator is hand-rolled rather than `localeCompare`, deliberately —
 * `localeCompare`'s tie-breaking and numeric handling vary by ICU build, so
 * a tree ordered by it would render differently on two machines and a test
 * pinning one order would be pinning an environment. Case-insensitive
 * first, then a byte-order tie-break so the result is TOTAL (two names
 * differing only in case still get a stable, reproducible order).
 *
 * DEFENSIVE, BECAUSE `path` IS FREE TEXT THAT AGENTS WRITE. Empty, missing,
 * leading/trailing/duplicated slashes, whitespace-only segments and "."/".."
 * all arrive eventually; every one of them resolves to something renderable
 * rather than throwing or silently dropping a row. THE ONE INVARIANT THAT
 * MATTERS: every document handed in comes out somewhere in the tree exactly
 * once. A document that cannot be placed is a document the customer can no
 * longer reach, and the list is the only way to reach it.
 *
 * A PATH THAT COLLIDES WITH AN INFERRED FOLDER ("notes" as a file, plus
 * "notes/a.md") is impossible in git and possible here, because nothing
 * enforces uniqueness across the two namespaces. Both are KEPT, as
 * siblings — the folder sorts first (directories before files), and the
 * file keeps its own row. Resolving the collision by dropping one of them
 * would be the invariant above, broken quietly.
 */

import type { FleetDocument } from "./documents-data";

export type DocumentFileNode = {
  kind: "file";
  /** The last path segment ("auth.md") — the FILE's name, which is what a
   *  git-shaped tree shows. Falls back to the document's title only when
   *  the path is unusable, so a row is never blank. */
  name: string;
  /** The normalized full path, "" for a document that carried none. */
  path: string;
  document: FleetDocument;
};

export type DocumentFolderNode = {
  kind: "folder";
  /** The single segment this folder is ("api"). */
  name: string;
  /** The normalized prefix it stands for ("specs/api") — unique among its
   *  siblings, so it doubles as a stable React key and collapse-state key. */
  path: string;
  children: DocumentTreeNode[];
  /** Files at or below this folder, at any depth. */
  fileCount: number;
};

export type DocumentTreeNode = DocumentFolderNode | DocumentFileNode;

/** One project's subtree in the cross-project (workspace) view — the top
 *  level of /w/{id}/context, where a project stands in for a repo. */
export type DocumentProjectGroup = {
  /** "" for a document carrying no project at all. */
  projectId: string;
  name: string;
  children: DocumentTreeNode[];
  fileCount: number;
};

/** A document with no project of its own. Its own group rather than a
 *  silent omission: an unfiled document is still the customer's document. */
export const UNFILED_PROJECT_LABEL = "Unfiled";

/** A project id we hold documents for but have no name for — the projects
 *  list has not resolved yet, or the project is not visible to this reader.
 *  Never the raw id: printing an opaque `project_<hex>` back at somebody is
 *  the same "raw IDs on first paint" defect Breadcrumbs.tsx guards against. */
export const UNKNOWN_PROJECT_LABEL = "Untitled project";

const FALLBACK_DOCUMENT_NAME = "Untitled document";

/**
 * Splits a raw `path` into renderable segments. Everything git would refuse
 * or normalize away is dropped here rather than half a dozen call sites
 * down: empty segments (leading, trailing and duplicated slashes all
 * produce them), whitespace-only segments, and the relative markers "." and
 * ".." — a document must never be able to draw itself outside its own tree.
 * Backslashes are NOT treated as separators: "/" is the separator in this
 * model, and a Windows-shaped path would otherwise silently restructure.
 */
export function documentPathSegments(rawPath: string | null | undefined): string[] {
  return String(rawPath || "")
    .split("/")
    .map((segment) => segment.trim())
    .filter((segment) => segment !== "" && segment !== "." && segment !== "..");
}

/** The normalized path a node is filed under — the round trip of the split
 *  above, so "/specs//api/auth.md/" and "specs/api/auth.md" are one path. */
export function normalizeDocumentPath(rawPath: string | null | undefined): string {
  return documentPathSegments(rawPath).join("/");
}

/** Total, reproducible, and independent of the host's ICU data: fold case
 *  first so "API" and "api" sort together, then fall back to a plain
 *  code-unit comparison so names differing only in case still have ONE
 *  order rather than an arbitrary one. */
function compareNames(a: string, b: string): number {
  const lowerA = a.toLowerCase();
  const lowerB = b.toLowerCase();
  if (lowerA < lowerB) return -1;
  if (lowerA > lowerB) return 1;
  if (a < b) return -1;
  if (a > b) return 1;
  return 0;
}

/** Directories before files, each alphabetically — GitHub's own ordering.
 *  Two nodes that tie on kind AND name are ordered by their path, then (for
 *  files) by document id, so two documents filed at the identical path
 *  still have a stable order instead of one that depends on input order. */
function compareNodes(a: DocumentTreeNode, b: DocumentTreeNode): number {
  if (a.kind !== b.kind) return a.kind === "folder" ? -1 : 1;
  const byName = compareNames(a.name, b.name);
  if (byName !== 0) return byName;
  const byPath = compareNames(a.path, b.path);
  if (byPath !== 0) return byPath;
  if (a.kind === "file" && b.kind === "file") return compareNames(a.document.id, b.document.id);
  return 0;
}

type FolderBuilder = {
  name: string;
  path: string;
  /** Folders are keyed so a prefix seen twice is ONE folder. Files are a
   *  plain list, never keyed — keying them would make two documents at the
   *  same path collapse into one, i.e. lose a row. */
  folders: Map<string, FolderBuilder>;
  files: DocumentFileNode[];
};

function newFolder(name: string, path: string): FolderBuilder {
  return { name, path, folders: new Map(), files: [] };
}

function fileNameFor(document: FleetDocument, segments: string[]): string {
  const leaf = segments[segments.length - 1];
  if (leaf) return leaf;
  const title = String(document.title || "").trim();
  return title || FALLBACK_DOCUMENT_NAME;
}

function insert(root: FolderBuilder, document: FleetDocument): void {
  const segments = documentPathSegments(document.path);
  let folder = root;
  // Every segment but the last is a folder PREFIX — created on demand,
  // reused when another document mentions it again. The last segment is the
  // file itself and never becomes a folder.
  for (let i = 0; i < segments.length - 1; i++) {
    const name = segments[i];
    const path = folder.path ? `${folder.path}/${name}` : name;
    let next = folder.folders.get(name);
    if (!next) {
      next = newFolder(name, path);
      folder.folders.set(name, next);
    }
    folder = next;
  }
  folder.files.push({
    kind: "file",
    name: fileNameFor(document, segments),
    path: segments.join("/"),
    document,
  });
}

function materialize(folder: FolderBuilder): { children: DocumentTreeNode[]; fileCount: number } {
  const nodes: DocumentTreeNode[] = [];
  let fileCount = folder.files.length;
  for (const child of folder.folders.values()) {
    const { children, fileCount: nested } = materialize(child);
    fileCount += nested;
    nodes.push({
      kind: "folder",
      name: child.name,
      path: child.path,
      children,
      fileCount: nested,
    });
  }
  nodes.push(...folder.files);
  nodes.sort(compareNodes);
  return { children: nodes, fileCount };
}

/**
 * One project's flat document list -> its inferred tree. The input array is
 * never mutated (it is state owned by a React hook upstream), and the
 * output holds every document exactly once.
 */
export function buildDocumentTree(documents: FleetDocument[]): DocumentTreeNode[] {
  const root = newFolder("", "");
  for (const document of documents || []) {
    if (!document) continue;
    insert(root, document);
  }
  return materialize(root).children;
}

/**
 * The cross-project (workspace) view: PROJECT at the top level, that
 * project's own inferred tree beneath it — the org/repo/path mapping the
 * founder approved, with a project standing in for a repo.
 *
 * `projects` is the caller's already-loaded list, used for names only.
 * Grouping never depends on it: a document whose project is missing from
 * that list still gets a group, because dropping it would hide a document
 * behind a list that had not finished loading.
 */
export function buildProjectDocumentTree(
  documents: FleetDocument[],
  projects: { id: string; name: string }[] = [],
): DocumentProjectGroup[] {
  const names = new Map<string, string>();
  for (const project of projects || []) {
    const id = String(project?.id || "").trim();
    if (!id) continue;
    names.set(id, String(project?.name || "").trim() || UNKNOWN_PROJECT_LABEL);
  }

  const buckets = new Map<string, FleetDocument[]>();
  for (const document of documents || []) {
    if (!document) continue;
    const projectId = String(document.project_id || "").trim();
    const bucket = buckets.get(projectId);
    if (bucket) bucket.push(document);
    else buckets.set(projectId, [document]);
  }

  const groups: DocumentProjectGroup[] = [];
  for (const [projectId, rows] of buckets.entries()) {
    const root = newFolder("", "");
    for (const document of rows) insert(root, document);
    const { children, fileCount } = materialize(root);
    groups.push({
      projectId,
      name: projectId ? names.get(projectId) || UNKNOWN_PROJECT_LABEL : UNFILED_PROJECT_LABEL,
      children,
      fileCount,
    });
  }

  // Alphabetical by project name, with the unfiled group ALWAYS last — it is
  // the exception bucket, and an exception that sorts into the middle of the
  // real projects reads as one of them.
  groups.sort((a, b) => {
    if (!a.projectId !== !b.projectId) return a.projectId ? -1 : 1;
    const byName = compareNames(a.name, b.name);
    return byName !== 0 ? byName : compareNames(a.projectId, b.projectId);
  });
  return groups;
}
