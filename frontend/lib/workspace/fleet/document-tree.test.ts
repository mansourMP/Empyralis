/**
 * The document tree's rules, proven by importing the REAL derivation
 * (buildDocumentTree / buildProjectDocumentTree) — the same "expected and
 * actual come from two different places" discipline every other pure-rule
 * module here follows (agent-count-shape.test.ts, primary-rail-nav.test.ts).
 *
 * THE EXPECTED TREES ARE HAND-WRITTEN LITERALS. This file deliberately does
 * NOT rebuild a tree with the same splitting/sorting logic and diff the two
 * — that is the vacuous shape CLAUDE.md names outright ("a check that
 * derives its own expectations from the thing it checks is blind, and
 * reports 'passed'"), and it would agree with the implementation on every
 * bug it contains. `render()` below is a dumb serializer: it walks whatever
 * it is given and prints it. It makes no ordering, grouping or
 * normalization decision of its own, so it cannot launder one.
 *
 * Run: npx tsx lib/workspace/fleet/document-tree.test.ts
 */

import {
  UNFILED_PROJECT_LABEL,
  UNKNOWN_PROJECT_LABEL,
  buildDocumentTree,
  buildProjectDocumentTree,
  documentPathSegments,
  normalizeDocumentPath,
  type DocumentTreeNode,
} from "./document-tree";
import type { FleetDocument } from "./documents-data";

let passed = 0;
let failed = 0;

function assert(condition: boolean, label: string): void {
  if (condition) {
    passed++;
  } else {
    failed++;
    console.error(`FAIL: ${label}`);
  }
}

function assertEqual(actual: string, expected: string, label: string): void {
  if (actual === expected) {
    passed++;
  } else {
    failed++;
    console.error(`FAIL: ${label}\n--- expected ---\n${expected}\n--- actual ---\n${actual}\n`);
  }
}

let seq = 0;
function doc(path: string, extra: Partial<FleetDocument> = {}): FleetDocument {
  seq += 1;
  // `path` is the thing under test, so it is applied AFTER the spread and
  // pulled out of `extra` first — a plain spread would silently overwrite the
  // positional argument (and "" is a real, meaningful value here).
  const { path: overridePath, ...rest } = extra;
  return {
    id: `doc${seq}`,
    project_id: "proj_a",
    title: "",
    created_by: null,
    updated_by: null,
    metadata: {},
    created_at: null,
    updated_at: null,
    ...rest,
    path: overridePath !== undefined ? overridePath : path,
  };
}

/** A dumb serializer. Folders print with a trailing "/" and their subtree
 *  file count; files print their name and their document id. It sorts
 *  nothing and infers nothing. */
function render(nodes: DocumentTreeNode[], depth = 0): string {
  const pad = "  ".repeat(depth);
  return nodes
    .map((node) =>
      node.kind === "folder"
        ? `${pad}${node.name}/ (${node.fileCount})\n${render(node.children, depth + 1)}`
        : `${pad}${node.name} [${node.document.id}]`,
    )
    .filter((line) => line !== "")
    .join("\n");
}

// ── documentPathSegments / normalizeDocumentPath ─────────────────────────
assert(documentPathSegments("specs/api/auth.md").join("|") === "specs|api|auth.md", "a plain path splits on /");
assert(documentPathSegments("auth.md").join("|") === "auth.md", "a path with no slash is one segment");
assert(documentPathSegments("").length === 0, "an empty path has no segments");
assert(documentPathSegments(null).length === 0, "a null path has no segments");
assert(documentPathSegments(undefined).length === 0, "a missing path has no segments");
assert(
  documentPathSegments("/specs//api/auth.md/").join("|") === "specs|api|auth.md",
  "leading, trailing and duplicated slashes all collapse away",
);
assert(documentPathSegments("  /  /a.md").join("|") === "a.md", "whitespace-only segments are dropped");
assert(documentPathSegments(" specs / api ").join("|") === "specs|api", "each segment is trimmed");
assert(
  documentPathSegments("../../etc/passwd").join("|") === "etc|passwd",
  "'..' is dropped — a document may never draw itself outside its own tree",
);
assert(documentPathSegments("./a.md").join("|") === "a.md", "'.' is dropped");
assert(
  documentPathSegments("specs\\api\\auth.md").join("|") === "specs\\api\\auth.md",
  "a backslash is NOT a separator — '/' is the model, and a Windows-shaped path must not silently restructure",
);
assert(normalizeDocumentPath("/specs//api/auth.md/") === "specs/api/auth.md", "normalize round-trips the split");
assert(normalizeDocumentPath("") === "", "an empty path normalizes to an empty path, not to a name");

// ── Flat: files only, alphabetical ───────────────────────────────────────
assertEqual(
  render(buildDocumentTree([doc("b.md", { id: "b" }), doc("a.md", { id: "a" }), doc("C.md", { id: "c" })])),
  ["a.md [a]", "b.md [b]", "C.md [c]"].join("\n"),
  "root-level files sort alphabetically, case-insensitively",
);

// ── Nesting: folders are INFERRED PREFIXES, never objects ────────────────
assertEqual(
  render(
    buildDocumentTree([
      doc("specs/api/auth.md", { id: "auth" }),
      doc("specs/api/billing.md", { id: "billing" }),
      doc("specs/overview.md", { id: "overview" }),
      doc("readme.md", { id: "readme" }),
    ]),
  ),
  [
    "specs/ (3)",
    "  api/ (2)",
    "    auth.md [auth]",
    "    billing.md [billing]",
    "  overview.md [overview]",
    "readme.md [readme]",
  ].join("\n"),
  "a nested tree: two documents naming the same prefix produce ONE folder, and fileCount counts the whole subtree",
);

// ── Directories before files, each alphabetically (GitHub's ordering) ────
assertEqual(
  render(
    buildDocumentTree([
      doc("alpha.md", { id: "alpha" }),
      doc("zulu/inner.md", { id: "inner" }),
      doc("beta.md", { id: "beta" }),
      doc("apex/inner.md", { id: "apexinner" }),
    ]),
  ),
  [
    "apex/ (1)",
    "  inner.md [apexinner]",
    "zulu/ (1)",
    "  inner.md [inner]",
    "alpha.md [alpha]",
    "beta.md [beta]",
  ].join("\n"),
  "every directory sorts before every file, even a directory named 'zulu' against a file named 'alpha'",
);

// ── Defensive input ──────────────────────────────────────────────────────
assertEqual(
  render(
    buildDocumentTree([
      doc("", { id: "empty", title: "Launch plan" }),
      doc("", { id: "blank", title: "   " }),
      doc("/specs//api/auth.md/", { id: "messy" }),
    ]),
  ),
  [
    "specs/ (1)",
    "  api/ (1)",
    "    auth.md [messy]",
    "Launch plan [empty]",
    "Untitled document [blank]",
  ].join("\n"),
  "an empty path is a ROOT-LEVEL file named by its title (or a fixed label); a messy path normalizes",
);

// THE INVARIANT: every document handed in comes out exactly once. A document
// that cannot be placed is a document the customer can no longer reach.
{
  const inputs = [
    doc("", { id: "d1" }),
    doc("   ", { id: "d2" }),
    doc("../../x.md", { id: "d3" }),
    doc("a/b/c/d/e.md", { id: "d4" }),
    doc("a.md", { id: "d5" }),
    doc("a.md", { id: "d6" }),
  ];
  const ids: string[] = [];
  const walk = (nodes: DocumentTreeNode[]) => {
    for (const node of nodes) {
      if (node.kind === "folder") walk(node.children);
      else ids.push(node.document.id);
    }
  };
  walk(buildDocumentTree(inputs));
  assert(ids.length === inputs.length, `every document is placed exactly once (got ${ids.length} of ${inputs.length})`);
  assert(new Set(ids).size === ids.length, "no document is placed twice");
  assert(inputs.every((d) => ids.includes(d.id)), "no document is dropped, however malformed its path");
}

// ── A file colliding with an inferred folder name ────────────────────────
assertEqual(
  render(buildDocumentTree([doc("notes", { id: "flat" }), doc("notes/a.md", { id: "nested" })])),
  ["notes/ (1)", "  a.md [nested]", "notes [flat]"].join("\n"),
  "a path that collides with an inferred folder keeps BOTH as siblings — the folder first, the file still reachable",
);
// Same collision, INSERTION ORDER FLIPPED. Worth its own case: with the flat
// document first the folder does not exist yet when it is placed, so an
// implementation that "resolves" the collision by looking for an existing
// folder passes the case above and silently drops a document here.
assertEqual(
  render(buildDocumentTree([doc("notes/a.md", { id: "nested2" }), doc("notes", { id: "flat2" })])),
  ["notes/ (1)", "  a.md [nested2]", "notes [flat2]"].join("\n"),
  "the collision resolves identically whichever document arrives first",
);

// ── Two documents filed at the identical path ────────────────────────────
assertEqual(
  render(buildDocumentTree([doc("dup.md", { id: "zzz" }), doc("dup.md", { id: "aaa" })])),
  ["dup.md [aaa]", "dup.md [zzz]"].join("\n"),
  "two documents at one path both survive, ordered stably by id rather than by input order",
);

// ── Purity ───────────────────────────────────────────────────────────────
{
  const inputs = [doc("b.md", { id: "b" }), doc("a.md", { id: "a" })];
  const order = inputs.map((d) => d.id).join(",");
  buildDocumentTree(inputs);
  assert(inputs.map((d) => d.id).join(",") === order, "the input array is never reordered in place");
  assert(buildDocumentTree([]).length === 0, "an empty list produces an empty tree, not a phantom root folder");
}

// ── Cross-project grouping: project at the top level ─────────────────────
{
  const groups = buildProjectDocumentTree(
    [
      doc("specs/auth.md", { id: "z1", project_id: "p_zeta" }),
      doc("readme.md", { id: "a1", project_id: "p_alpha" }),
      doc("notes.md", { id: "u1", project_id: null }),
      doc("specs/billing.md", { id: "z2", project_id: "p_zeta" }),
      doc("orphan.md", { id: "o1", project_id: "p_gone" }),
    ],
    [
      { id: "p_zeta", name: "Zeta" },
      { id: "p_alpha", name: "Alpha" },
    ],
  );
  assertEqual(
    groups.map((g) => `${g.name} [${g.projectId}] (${g.fileCount})\n${render(g.children, 1)}`).join("\n"),
    [
      "Alpha [p_alpha] (1)",
      "  readme.md [a1]",
      `${UNKNOWN_PROJECT_LABEL} [p_gone] (1)`,
      "  orphan.md [o1]",
      "Zeta [p_zeta] (2)",
      "  specs/ (2)",
      "    auth.md [z1]",
      "    billing.md [z2]",
      `${UNFILED_PROJECT_LABEL} [] (1)`,
      "  notes.md [u1]",
    ].join("\n"),
    "projects sort alphabetically by name, a project whose name has not loaded still gets a group, and Unfiled is always last",
  );
}

{
  // The names list is for LABELS ONLY. Grouping must never depend on it, or
  // a document disappears while the projects fetch is still in flight.
  const groups = buildProjectDocumentTree([doc("a.md", { id: "a", project_id: "p1" })], []);
  assert(groups.length === 1, "a document still gets a group with no projects list at all");
  assert(groups[0].name === UNKNOWN_PROJECT_LABEL, "an unresolvable project is labelled, never printed as its raw id");
  assert(groups[0].projectId === "p1", "the id is still carried, so the group can still link somewhere");
}

assert(buildProjectDocumentTree([], [{ id: "p1", name: "Alpha" }]).length === 0, "a project with no documents gets no group");

console.log(`\n${passed} passed, ${failed} failed`);
if (failed > 0) {
  process.exit(1);
}
