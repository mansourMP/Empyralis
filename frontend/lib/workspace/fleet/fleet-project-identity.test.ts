/**
 * fleet-project-identity.tsx — the icon and colour vocabulary a project
 * picks from, and their fallbacks.
 *
 * Half of this is a cross-source contract check, same discipline as
 * agent-create-job.test.ts's own: PROJECT_ICON_MAP/TINTS is a frontend
 * vocabulary, but server_modules/projects_repository.py validates every
 * PATCH against its OWN independently-authored lists
 * (VALID_PROJECT_ICONS/PROJECT_TINTS) before it ever reaches this file.
 * Two hand-typed lists that happen to agree today are exactly the kind of
 * thing that silently drifts apart — "Changing a pinned literal means
 * grepping the LITERAL repo-wide" (CLAUDE.md) — so this reads the actual
 * Python source and diffs it against the actual TypeScript source, never
 * one confirming itself. Each scan carries a canary that fails loudly if
 * the regex ever stops matching real content.
 *
 * The other half is the render-adjacent logic ProjectIcon/
 * ProjectIdentityPicker lean on: an unknown or missing icon/tint must fall
 * back to a real, renderable value — never a blank tile, never a throw.
 *
 * Run: npx tsx lib/workspace/fleet/fleet-project-identity.test.ts
 */

import { readFileSync } from "node:fs";
import path from "node:path";

import { PROJECT_ICON_MAP, projectIconComponent } from "./fleet-project-identity";
import { TINTS } from "./fleet-presentation";

let passed = 0;
let failed = 0;

function assert(condition: boolean, label: string): void {
  if (condition) passed++;
  else {
    failed++;
    console.error(`FAIL: ${label}`);
  }
}

const REPO_ROOT = path.resolve(__dirname, "../../../..");
function readRepoFile(rel: string): string {
  return readFileSync(path.join(REPO_ROOT, rel), "utf8");
}

const PROJECTS_REPOSITORY_PY = readRepoFile("server_modules/projects_repository.py");

function extractPyStringList(source: string, constName: string): string[] {
  const block = new RegExp(`${constName}\\s*=\\s*\\[([^\\]]*)\\]`).exec(source);
  if (!block) return [];
  return [...block[1].matchAll(/"([^"]+)"/g)].map((m) => m[1]);
}

function extractPyString(source: string, constName: string): string {
  const m = new RegExp(`^${constName}\\s*=\\s*"([^"]+)"`, "m").exec(source);
  return m ? m[1] : "";
}

const PY_PROJECT_ICONS = extractPyStringList(PROJECTS_REPOSITORY_PY, "PROJECT_ICONS");
const PY_PROJECT_TINTS = extractPyStringList(PROJECTS_REPOSITORY_PY, "PROJECT_TINTS");
const PY_DEFAULT_PROJECT_ICON = extractPyString(PROJECTS_REPOSITORY_PY, "DEFAULT_PROJECT_ICON");

// ── Canaries — the scans above must reach real content, or every assertion
//    below is vacuously true against two empty sets. ─────────────────────
assert(PY_PROJECT_ICONS.length >= 10, `expected PROJECT_ICONS to parse a real list from projects_repository.py; got ${PY_PROJECT_ICONS.length}`);
assert(PY_PROJECT_TINTS.length >= 4, `expected PROJECT_TINTS to parse a real list from projects_repository.py; got ${PY_PROJECT_TINTS.length}`);
assert(PY_DEFAULT_PROJECT_ICON.length > 0, "expected DEFAULT_PROJECT_ICON to parse a real value from projects_repository.py");

// ── The icon vocabulary: backend VALID_PROJECT_ICONS is PROJECT_ICONS plus
//    DEFAULT_PROJECT_ICON — PROJECT_ICON_MAP (the frontend's own render
//    vocabulary, one lucide component per key) must be exactly that set,
//    or a PATCH the backend accepts renders with no glyph, and a name the
//    frontend offers gets rejected server-side. ──────────────────────────
const expectedIconNames = new Set([...PY_PROJECT_ICONS, PY_DEFAULT_PROJECT_ICON]);
const actualIconNames = new Set(Object.keys(PROJECT_ICON_MAP));

assert(
  expectedIconNames.size === actualIconNames.size,
  `PROJECT_ICON_MAP has ${actualIconNames.size} entries; projects_repository.py's icon vocabulary has ${expectedIconNames.size} — ` +
    `${[...expectedIconNames].filter((n) => !actualIconNames.has(n)).join(", ") || "(none missing from frontend)"} missing on the frontend, ` +
    `${[...actualIconNames].filter((n) => !expectedIconNames.has(n)).join(", ") || "(none extra on frontend)"} extra on the frontend`,
);
for (const name of expectedIconNames) {
  assert(actualIconNames.has(name), `PROJECT_ICON_MAP has a glyph for backend icon "${name}"`);
}
for (const name of actualIconNames) {
  assert(expectedIconNames.has(name), `frontend icon "${name}" is one the backend will actually accept/assign`);
}

// ── The colour vocabulary: TINTS (fleet-presentation.ts, the frontend's
//    own render/validation source) must carry exactly the backend's
//    PROJECT_TINTS keys — same reasoning as above, for colour instead of
//    shape. ──────────────────────────────────────────────────────────────
const expectedTintKeys = new Set(PY_PROJECT_TINTS);
const actualTintKeys = new Set(Object.keys(TINTS));
assert(
  expectedTintKeys.size === actualTintKeys.size &&
    [...expectedTintKeys].every((k) => actualTintKeys.has(k)),
  `TINTS keys (${[...actualTintKeys].join(", ")}) must exactly match projects_repository.PROJECT_TINTS (${[...expectedTintKeys].join(", ")})`,
);

// ── Fallback behaviour — an unset/unknown icon must never render blank or
//    throw; it renders the same default shape the backend reserves for the
//    workspace's own "General" project. ──────────────────────────────────
assert(
  projectIconComponent(undefined) === projectIconComponent(null),
  "projectIconComponent(undefined) and (null) resolve to the same fallback",
);
assert(
  projectIconComponent("this-icon-name-does-not-exist") === projectIconComponent(undefined),
  "an unrecognised icon name falls back to the same default as no icon at all",
);
assert(
  projectIconComponent(PY_DEFAULT_PROJECT_ICON) === projectIconComponent(undefined),
  `projectIconComponent("${PY_DEFAULT_PROJECT_ICON}") IS the default fallback shape (the "General" project's own icon)`,
);
assert(
  projectIconComponent("rocket") !== projectIconComponent(undefined),
  "a real, non-default icon name resolves to a genuinely different component than the fallback",
);

// ── Wired, not just built — the failure mode this codebase hits most
//    (CLAUDE.md: "complete, correct, tested code with zero callers"). A
//    vocabulary that matches on both sides proves nothing if the PATCH
//    route never actually calls the function, or no frontend surface ever
//    imports the picker. ────────────────────────────────────────────────
const ROUTES_FLEET_PY = readRepoFile("server_modules/routes_fleet.py");
const PROJECTS_PAGE_TSX = readRepoFile("frontend/app/(account)/w/[workspaceId]/projects/page.tsx");
const PROJECT_SETTINGS_TSX = readRepoFile("frontend/lib/workspace/fleet/ProjectSettings.tsx");
const FLEET_DATA_TS = readRepoFile("frontend/lib/workspace/fleet/fleet-data.ts");

assert(
  PROJECTS_REPOSITORY_PY.includes("async def set_project_identity("),
  "projects_repository.py defines set_project_identity",
);
assert(
  ROUTES_FLEET_PY.includes("await projects.set_project_identity("),
  "fleet_patch_project's PATCH handler actually calls set_project_identity — not just defined, reachable",
);
function pydanticFieldsOf(source: string, className: string): string {
  // From the class line to the next top-level `class ` or `@router` line —
  // i.e. just this one Pydantic model's own body, comments included.
  const m = new RegExp(`class ${className}\\(BaseModel\\):[\\s\\S]*?(?=\\nclass |\\n@router)`).exec(source);
  if (!m) return "";
  return m[0];
}
const CREATE_PROJECT_REQUEST_PY = pydanticFieldsOf(ROUTES_FLEET_PY, "FleetCreateProjectRequest");
const PATCH_PROJECT_REQUEST_PY = pydanticFieldsOf(ROUTES_FLEET_PY, "FleetPatchProjectRequest");
assert(CREATE_PROJECT_REQUEST_PY.length > 0, "expected to isolate FleetCreateProjectRequest's own body from routes_fleet.py");
assert(PATCH_PROJECT_REQUEST_PY.length > 0, "expected to isolate FleetPatchProjectRequest's own body from routes_fleet.py");

assert(
  /icon: Optional\[str\]/.test(CREATE_PROJECT_REQUEST_PY) && /tint: Optional\[str\]/.test(CREATE_PROJECT_REQUEST_PY),
  "FleetCreateProjectRequest accepts icon and tint — a picker choice made before the project exists can reach create_project",
);
assert(
  /icon: Optional\[str\]/.test(PATCH_PROJECT_REQUEST_PY) && /tint: Optional\[str\]/.test(PATCH_PROJECT_REQUEST_PY),
  "FleetPatchProjectRequest accepts icon and tint",
);
assert(
  PROJECTS_PAGE_TSX.includes("ProjectIdentityPicker"),
  "the New-project composer (projects/page.tsx) actually renders ProjectIdentityPicker — a picker nobody can open is not a feature",
);
assert(
  PROJECT_SETTINGS_TSX.includes("ProjectIdentityPicker"),
  "ProjectSettings.tsx (the only surface for editing an EXISTING project) actually renders ProjectIdentityPicker",
);
assert(
  /patch:\s*\{[^}]*icon\?:\s*string[^}]*tint\?:\s*string/s.test(FLEET_DATA_TS),
  "patchFleetProject's own patch type carries icon/tint through to the PATCH request body",
);

console.log(`\n${passed} passed, ${failed} failed`);
if (failed > 0) process.exit(1);
