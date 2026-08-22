/**
 * The workspace Agents card face — drives the REAL rule
 * (agent-card-face.ts) rather than re-deriving it here, the same discipline
 * agent-count-shape.test.ts / openclaw-channel-copy.test.ts already apply, and
 * for the reason CLAUDE.md states outright: "a check that derives its own
 * expectations from the thing it checks is blind, and reports 'passed'".
 *
 * TWO KINDS OF ASSERTION LIVE HERE, and the second kind is the one that
 * matters more:
 *
 *   BEHAVIOURAL   what a face says for a given agent + status + tasks.
 *   STRUCTURAL    that the surface actually renders it, that the picker column
 *                 it replaces is really gone, and that the grid spends no
 *                 accent. A behavioural test cannot see any of those three —
 *                 a perfectly correct module wired to nothing compiles, runs,
 *                 and passes, which is this codebase's single most-repeated
 *                 defect ("built, tested, and never wired").
 *
 * Every structural assertion below was proven RED before it was green, by
 * checking the pre-change files back out of git and watching it fail.
 *
 * Run: npx tsx lib/workspace/fleet/agent-card-face.test.ts
 */
import { readFileSync, existsSync } from "node:fs";

import {
  agentCardReach,
  groupTasksByAgent,
  matchesAgentCardQuery,
  planAgentCardFace,
  planAgentCards,
  type AgentCardAgentInput,
  type AgentCardTaskInput,
  type AgentRuntimeStatus,
} from "./agent-card-face";

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

const READY: AgentRuntimeStatus = { tone: "ready", label: "Ready" };
const OFFLINE: AgentRuntimeStatus = { tone: "offline", label: "Computer offline" };
const STOPPED: AgentRuntimeStatus = { tone: "stopped", label: "Stopped" };
const NEEDS_SIGNIN: AgentRuntimeStatus = { tone: "degraded", label: "Needs sign-in" };
const NOT_DEPLOYED: AgentRuntimeStatus = { tone: "unknown", label: "Not deployed" };
const RUNTIME_WORKING: AgentRuntimeStatus = { tone: "working", label: "Working" };

function agent(over: Partial<AgentCardAgentInput> = {}): AgentCardAgentInput {
  return { agent_id: "ainstall_a", label: "Support Desk", channel: "", ...over };
}

function task(over: Partial<AgentCardTaskInput> = {}): AgentCardTaskInput {
  return {
    id: "task_69d656aa",
    title: "Fix the invoice importer",
    status: "todo",
    assignee_agent_id: "ainstall_a",
    number: null,
    project_task_key: null,
    ...over,
  };
}

// ── The reach line: the founder's own priority order ──────────────────────
// "the task it is on, or the channel it serves, or plainly that it has
// neither."

{
  const reach = agentCardReach(agent(), [task({ status: "in_progress", number: 12, project_task_key: "GEN" })]);
  assert(reach.kind === "task", "an in-progress task is the reach");
  assert(reach.label === "GEN-12 Fix the invoice importer", `the task line carries the display id and the title, got "${reach.label}"`);
  assert(reach.channelKey === "", "a task reach carries no channel key — never a guess at one");
}

{
  // A task predating the per-project sequence numbers still gets an honest
  // handle rather than a blank — taskDisplayId's own hex-slice fallback.
  const reach = agentCardReach(agent(), [task({ status: "in_progress" })]);
  assert(reach.label.startsWith("69D656"), `an unnumbered task falls back to its short handle, got "${reach.label}"`);
}

{
  const reach = agentCardReach(agent(), [
    task({ id: "task_1", status: "in_progress", number: 1, project_task_key: "GEN" }),
    task({ id: "task_2", status: "in_progress", number: 2, project_task_key: "GEN" }),
  ]);
  assert(reach.label.endsWith(" +1"), `two in-flight tasks name one and count the rest, got "${reach.label}"`);
}

{
  const reach = agentCardReach(agent(), [task({ status: "todo" }), task({ id: "t2", status: "backlog" })]);
  assert(reach.kind === "tasks" && reach.label === "2 tasks waiting", `open-but-not-started tasks are counted, got "${reach.label}"`);
}

{
  const reach = agentCardReach(agent(), [task({ status: "todo" })]);
  assert(reach.label === "1 task waiting", `one waiting task is singular, got "${reach.label}"`);
}

{
  // Only `done` is terminal in FLEET_TASK_STATUSES. Everything else — including
  // blocked and awaiting_input — is still work this agent is holding.
  const reach = agentCardReach(agent(), [task({ status: "done" }), task({ id: "t2", status: "done" })]);
  assert(reach.kind === "none", "finished tasks are not reach — a card must not claim work that is closed");
}

{
  const reach = agentCardReach(agent({ channel: "sage_telegram_hosted" }), []);
  assert(reach.kind === "channel", "with no tasks, the channel is the reach");
  assert(reach.label === "Answers on Telegram", `the channel line uses the SHARED key->name map, got "${reach.label}"`);
  assert(reach.channelKey === "sage_telegram_hosted", "the raw key is carried through for the icon lookup");
}

{
  const reach = agentCardReach(agent({ channel: "slack +2" }), []);
  assert(reach.label === "Answers on Slack +2", `the "+N" suffix survives the parse, got "${reach.label}"`);
  assert(reach.channelKey === "slack", "the +N is stripped from the icon key");
}

{
  // An unmapped key is still a real channel. Naming it raw beats naming it
  // wrong, and beats dropping the line entirely.
  const reach = agentCardReach(agent({ channel: "some_new_channel" }), []);
  assert(reach.label === "Answers on some_new_channel", `an unmapped channel key is named raw, got "${reach.label}"`);
}

{
  // WORK BEATS CHANNEL — the ordering the module's own header defends.
  const reach = agentCardReach(agent({ channel: "slack" }), [task({ status: "in_progress", number: 3, project_task_key: "GEN" })]);
  assert(reach.kind === "task", "a task in flight outranks the channel it also answers on");
}

{
  const reach = agentCardReach(agent(), []);
  assert(reach.kind === "none" && reach.label === "No channel or tasks yet", `nothing reaches it, said plainly, got "${reach.label}"`);
  // The whole point of the redesign: this must NOT be the lifecycle verb the
  // old surface printed on all nineteen rows.
  assert(!/created|configured/i.test(reach.label), "the reach line is never a lifecycle verb");
}

// ── The state slot ────────────────────────────────────────────────────────

{
  const face = planAgentCardFace(agent(), STOPPED, [task({ status: "in_progress" })]);
  assert(face.state === "stopped", "owner-stopped wins over everything, including a task in progress");
  assert(face.stateLabel === "Stopped" && face.tone === "stopped", "a stopped agent keeps the shared vocabulary's own word");
  assert(face.needsAttention === false, "a deliberate stop is not a defect — it must not wear the warning colour");
}

for (const status of [OFFLINE, NEEDS_SIGNIN, NOT_DEPLOYED]) {
  const face = planAgentCardFace(agent({ channel: "slack" }), status, []);
  assert(face.state === "blocked", `${status.label} is a blocked state`);
  assert(face.stateLabel === status.label, `${status.label} is reported in the runtime status's OWN words, never a word invented here`);
  assert(face.needsAttention === true, `${status.label} is something to go and fix`);
}

{
  const face = planAgentCardFace(agent(), READY, [task({ status: "in_progress" })]);
  assert(face.state === "working" && face.stateLabel === "Working" && face.tone === "working", "a Ready agent holding an in-progress task reads as Working");
}

{
  // current_run_id is de-facto always null on a real fleet, but when the
  // runtime DOES say working, that is honoured with no task needed.
  const face = planAgentCardFace(agent(), RUNTIME_WORKING, []);
  assert(face.state === "working", "the runtime's own working signal is still honoured on its own");
}

{
  const face = planAgentCardFace(agent({ channel: "slack" }), READY, []);
  assert(face.state === "idle" && face.stateLabel === "Ready", "reachable and quiet is idle, and keeps the word Ready");
  assert(face.needsAttention === false, "an agent something can reach is not flagged");
}

{
  // THE TWO SLOTS MAY DISAGREE, and this is the case that proves it: the
  // brain is fine, and nothing will ever ask it anything.
  const face = planAgentCardFace(agent(), READY, []);
  assert(face.stateLabel === "Ready", "slot 1 stays honest about the runtime");
  assert(face.reach.kind === "none", "slot 2 stays honest about reach");
  assert(face.needsAttention === true, "…and the card as a whole reads as something to go fix");
}

// ── Grouping ──────────────────────────────────────────────────────────────

{
  const byAgent = groupTasksByAgent([
    task({ id: "t1", assignee_agent_id: "ainstall_a" }),
    task({ id: "t2", assignee_agent_id: "ainstall_b" }),
    task({ id: "t3", assignee_agent_id: "ainstall_a" }),
    task({ id: "t4", assignee_agent_id: null }),
    task({ id: "t5", assignee_agent_id: "   " }),
  ]);
  assert(byAgent.get("ainstall_a")?.length === 2, "tasks bucket by their agent assignee");
  assert(byAgent.get("ainstall_b")?.length === 1, "…for every agent, not just the first");
  assert(byAgent.size === 2, "a task assigned to a human, or to nobody, belongs to no agent's card");
}

// ── Search ────────────────────────────────────────────────────────────────

{
  const face = planAgentCardFace(agent({ channel: "slack" }), READY, []);
  assert(matchesAgentCardQuery(agent(), face, "") === true, "a blank query matches everything");
  assert(matchesAgentCardQuery(agent(), face, "  ") === true, "…and so does whitespace");
  assert(matchesAgentCardQuery(agent(), face, "SUPPORT") === true, "the name matches case-insensitively");
  assert(matchesAgentCardQuery(agent(), face, "slack") === true, "the REACH LINE is searchable — it is on the face");
  assert(matchesAgentCardQuery(agent(), face, "telegram") === false, "…and something no card shows matches nothing");
}

// ── The whole grid: filter, then sort ─────────────────────────────────────

{
  const agents = [
    agent({ agent_id: "healthy", label: "Zeta", channel: "slack" }),
    agent({ agent_id: "broken", label: "Yankee" }),
    agent({ agent_id: "busy", label: "Xray" }),
    agent({ agent_id: "unreached", label: "Whiskey" }),
    agent({ agent_id: "paused", label: "Victor", channel: "slack" }),
  ];
  const statusFor = (a: AgentCardAgentInput): AgentRuntimeStatus =>
    a.agent_id === "broken" ? OFFLINE : a.agent_id === "paused" ? STOPPED : READY;
  const tasksByAgent = groupTasksByAgent([task({ id: "t1", status: "in_progress", assignee_agent_id: "busy" })]);

  const cards = planAgentCards(agents, statusFor, tasksByAgent, "");
  assert(cards.length === 5, "every agent gets a card");
  assert(
    cards.map((c) => c.agent.agent_id).join(",") === "broken,busy,unreached,paused,healthy",
    `scan order is blocked, working, unfinished setup, stopped, healthy — got ${cards.map((c) => c.agent.agent_id).join(",")}`,
  );

  // Alphabetical WITHIN a rank, so a card's position is stable — never
  // reordered by a ticking timestamp the way the chat-list this replaces was.
  const twoIdle = planAgentCards(
    [agent({ agent_id: "b", label: "Beta", channel: "slack" }), agent({ agent_id: "a", label: "Alpha", channel: "slack" })],
    () => READY,
    new Map(),
    "",
  );
  assert(twoIdle.map((c) => c.agent.label).join(",") === "Alpha,Beta", "ties break alphabetically, not by input order");

  const filtered = planAgentCards(agents, statusFor, tasksByAgent, "whiskey");
  assert(filtered.length === 1 && filtered[0].agent.agent_id === "unreached", "the query narrows the grid");
  assert(planAgentCards(agents, statusFor, tasksByAgent, "zzzz").length === 0, "a query matching nothing returns nothing, never everything");
}

// ── STRUCTURAL: the picker column is gone, and the grid is wired ──────────
// The three things a behavioural test structurally cannot see.

const AGENTS_DIR = new URL("../../../app/(account)/w/[workspaceId]/agents/", import.meta.url);

assert(
  !existsSync(new URL("layout.tsx", AGENTS_DIR)),
  "agents/layout.tsx is deleted — no layout may reintroduce a persistent picker column beside the agent's own page",
);
for (const gone of ["AgentConversationList.tsx", "agents-conversation-list.ts", "agents-split-pane.ts"]) {
  assert(!existsSync(new URL(gone, import.meta.url)), `${gone} is deleted, not left orphaned for someone to rewire`);
}

const pageSource = readFileSync(new URL("page.tsx", AGENTS_DIR), "utf8");
assert(pageSource.length > 500, "CANARY: the Agents page source was actually read");
assert(
  pageSource.includes('from "@/lib/workspace/fleet/AgentCards"') && /<AgentCards\b/.test(pageSource),
  "the Agents page renders the card grid — built, and WIRED",
);
assert(
  /useFleetWorkspaceTasks\s*\(/.test(pageSource),
  "…and feeds it the workspace's tasks, or a card can never say what an agent is on",
);
assert(
  /useWorkspaceGateways\s*\(/.test(pageSource),
  "…and the paired boxes, or a brain-blocked agent would read green here while reading red on its own page",
);
assert(
  !/Pick an agent to watch it work/.test(pageSource),
  "the prompt that existed only to sit beside the deleted picker is gone with it",
);
assert(
  /findSageAgent\s*\(/.test(pageSource) && /planAgentCountShape\s*\(/.test(pageSource),
  "the Operator exclusion and the 0/1/2+ shape still come from the SHARED rules, never a second opinion grown here",
);

const cardsSource = readFileSync(new URL("./AgentCards.tsx", import.meta.url), "utf8");
assert(cardsSource.length > 500, "CANARY: AgentCards.tsx was actually read");
assert(
  /planAgentCards\s*\(/.test(cardsSource) && /groupTasksByAgent\s*\(/.test(cardsSource),
  "the component renders what THIS module decided rather than deciding for itself",
);
assert(
  /deriveAgentStatus\s*\(/.test(cardsSource),
  "the status comes from the one shared vocabulary (deriveAgentStatus), not a fork of it",
);
// No composer, no send, no message input — anywhere on this surface. Chat left
// the platform; a card grid is the last place it should grow back.
assert(
  !/<textarea|onSend|placeholder="Message|sendMessage/i.test(cardsSource),
  "the Agents surface has no composer of any kind",
);

const cardsCss = readFileSync(new URL("./agent-cards.css", import.meta.url), "utf8");
assert(cardsCss.length > 500, "CANARY: agent-cards.css was actually read");
// DECLARATIONS ONLY. Comments are stripped first, or this file's own header —
// which states the rule it is enforcing, in the words it bans — trips its own
// tripwire. Exactly the trap CLAUDE.md names for the destructive-awareness
// guard: a test that cannot describe what it forbids is a test nobody can
// document.
const cardsCssDecls = cardsCss.replace(/\/\*[\s\S]*?\*\//g, "");
assert(
  /\.fleet-agent-card-grid\s*\{/.test(cardsCssDecls),
  "CANARY: stripping comments left real rules behind — the two scans below can actually fail",
);
assert(
  !/var\(--[a-z-]*accent/.test(cardsCssDecls),
  "the grid spends NO accent — the view's one filled primary action is the topbar's New agent button",
);
assert(
  !/:focus[^{]*\{[^}]*outline:\s*(?!none)/.test(cardsCssDecls),
  "no focus ring is drawn here (founder, 2026-08-21: there is no focus ring in this product)",
);

console.log(`\n${passed} passed, ${failed} failed`);
if (failed > 0) {
  process.exit(1);
}
