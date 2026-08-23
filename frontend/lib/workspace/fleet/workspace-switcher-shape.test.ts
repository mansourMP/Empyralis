/**
 * ONE WORKSPACE PER ACCOUNT — the rule, plus the structural half a
 * behavioural test cannot see.
 *
 * The behavioural assertions cover planWorkspaceSwitcher. The structural ones
 * read WorkspaceSwitcher.tsx's own source, because the two regressions that
 * matter here both TYPE-CHECK and both render perfectly:
 *
 *   - a "New workspace" link coming back into the popover (the create path
 *     this change exists to remove), and
 *   - the component computing its own picker/label rule inline instead of
 *     calling this module, which is how the rail and the grid came to say
 *     different things about the same fleet once already.
 *
 * Same discipline as agent-card-face.test.ts and connector-card-face.test.ts,
 * including the canary: if the source scan cannot reach real code, it must
 * fail loudly rather than enforce nothing.
 */

import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import path from "node:path";

import { planWorkspaceSwitcher } from "./workspace-switcher-shape";

const HERE = path.dirname(fileURLToPath(import.meta.url));

let failures = 0;
function check(name: string, condition: boolean, detail = "") {
  if (condition) return;
  failures += 1;
  console.error(`FAIL  ${name}${detail ? `\n      ${detail}` : ""}`);
}

// ── The rule ───────────────────────────────────────────────────────────────

{
  const solo = planWorkspaceSwitcher({ workspaceCount: 1, pendingInviteCount: 0 });
  check("one workspace, no invites -> label", solo.mode === "label", `got ${solo.mode}`);
  check("a label is not interactive", solo.interactive === false);
  check("a label draws no chevron", solo.showsChevron === false);
}

{
  const many = planWorkspaceSwitcher({ workspaceCount: 2, pendingInviteCount: 0 });
  check("two workspaces -> picker", many.mode === "picker", `got ${many.mode}`);
  check("a picker is interactive", many.interactive === true);
  check("a picker draws a chevron", many.showsChevron === true);

  const lots = planWorkspaceSwitcher({ workspaceCount: 9, pendingInviteCount: 0 });
  check("existing multi-workspace accounts still switch", lots.mode === "picker");
}

{
  // The one action a person with a single workspace can take in that popover.
  const invited = planWorkspaceSwitcher({ workspaceCount: 1, pendingInviteCount: 1 });
  check("one workspace + a pending invite -> picker", invited.mode === "picker", `got ${invited.mode}`);
  check("...and it is interactive, or Join/Decline is unreachable", invited.interactive === true);
}

{
  // A shell that failed to load presents as 0. There is nothing to pick.
  const none = planWorkspaceSwitcher({ workspaceCount: 0, pendingInviteCount: 0 });
  check("zero workspaces -> label, never an empty picker", none.mode === "label", `got ${none.mode}`);

  const nonsense = planWorkspaceSwitcher({
    workspaceCount: Number.NaN,
    pendingInviteCount: Number.NaN,
  });
  check("NaN degrades to label rather than throwing", nonsense.mode === "label");

  const negative = planWorkspaceSwitcher({ workspaceCount: -3, pendingInviteCount: -1 });
  check("negative counts degrade to label", negative.mode === "label");
}

{
  check(
    "chevron never renders without interactivity",
    [
      planWorkspaceSwitcher({ workspaceCount: 1, pendingInviteCount: 0 }),
      planWorkspaceSwitcher({ workspaceCount: 2, pendingInviteCount: 0 }),
      planWorkspaceSwitcher({ workspaceCount: 1, pendingInviteCount: 4 }),
      planWorkspaceSwitcher({ workspaceCount: 0, pendingInviteCount: 0 }),
    ].every((shape) => !shape.showsChevron || shape.interactive),
  );
}

// ── The structural half ────────────────────────────────────────────────────

const switcherPath = path.join(HERE, "WorkspaceSwitcher.tsx");
const switcherSource = readFileSync(switcherPath, "utf8");

// COMMENTS ARE STRIPPED BEFORE ANY "must not contain" scan. The file's own
// header explains that the "New workspace" row was removed, and a scan that
// reads prose trips on the sentence describing the fix -- the same tripwire
// agent-card-face.test.ts already documents for its CSS scans.
const switcherCode = switcherSource
  .replace(/\/\*[\s\S]*?\*\//g, " ")
  .replace(/^[ \t]*\/\/.*$/gm, " ")
  .replace(/\{\s*\/\*[\s\S]*?\*\/\s*\}/g, " ");

// CANARY. A scan that reads the wrong file, or an empty one, would pass every
// "must not contain" assertion below while enforcing nothing at all.
check(
  "CANARY: the switcher source is really being read",
  switcherSource.includes("fleet-rail-workspace-trigger") && switcherSource.length > 2000,
  `read ${switcherSource.length} bytes from ${switcherPath}`,
);
check(
  "CANARY: comment stripping left real code behind",
  switcherCode.includes("fleet-rail-workspace-trigger")
    && switcherCode.includes("planWorkspaceSwitcher")
    && !switcherCode.includes("ONE WORKSPACE PER ACCOUNT"),
  "Stripping must remove prose and keep code; an over-eager strip would make every scan below vacuous.",
);

check(
  "the create-workspace path is gone from the switcher",
  !switcherCode.includes("/workspaces/new"),
  'WorkspaceSwitcher.tsx still links /workspaces/new. One workspace per account: the self-serve create path is removed, not hidden or disabled.',
);
check(
  'no "New workspace" copy survives',
  !/New workspace/i.test(switcherCode),
  "A label for a control that no longer exists is how the control comes back.",
);
check(
  "the add-row styling went with the row it styled",
  !switcherCode.includes("--add"),
  "Leftover CSS hooks are how the next author rebuilds a removed control.",
);

check(
  "the switcher composes the shared rule rather than re-deriving one",
  switcherCode.includes("planWorkspaceSwitcher"),
  "Two places computing picker-vs-label is how two surfaces come to disagree.",
);
check(
  "the old inline hasOtherWorkspaces rule is gone",
  !switcherCode.includes("hasOtherWorkspaces"),
  "That local was the second opinion this module replaces.",
);

// The popover must be genuinely unreachable in label mode, not merely
// chevron-less: a trigger that still opens a one-row menu is the dead
// control, chevron or no chevron.
check(
  "label mode renders no control at all, not a disabled one",
  /if \(!interactive\)/.test(switcherCode)
    && /<span[^>]*fleet-rail-workspace-trigger--static/.test(switcherCode),
  "There must be a branch that renders the name as a plain span. A `disabled` button still announces a control that could work later, which is not true here.",
);
check(
  "menu semantics exist on exactly one branch",
  (switcherCode.match(/aria-haspopup/g) || []).length === 1
    && (switcherCode.match(/aria-expanded/g) || []).length === 1,
  "aria-haspopup/aria-expanded on the label branch would announce a menu that cannot open.",
);
check(
  "nothing opens the popover in label mode",
  /interactive && open/.test(switcherCode),
  "The popover render must be gated on the plan, not only on `open` — otherwise a stale `open` from a previous render still paints a one-row menu.",
);
check(
  'no "disabled" escape hatch was used instead of not rendering',
  !/disabled=\{[^}]*interactive/.test(switcherCode),
  'CLAUDE.md: a control that cannot be used is not rendered.',
);

if (failures > 0) {
  console.error(`\n${failures} failure(s)`);
  process.exit(1);
}
console.log("workspace-switcher-shape: all checks passed");
