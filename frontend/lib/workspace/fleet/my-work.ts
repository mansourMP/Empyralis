/**
 * WHAT LANDS IN "MY WORK" — the whole rule, as a pure function, in the same
 * idiom as agent-count-shape.ts and channel-doors.ts: data and pure
 * functions in their own dependency-light module, imported directly by a
 * plain `tsx` test, so the expected set and the actual set can never come
 * from one place.
 *
 * The question the surface exists to answer is "what is assigned to ME,
 * across every project" — which before this had no surface at all: you
 * opened each project and scanned its board. The rail's Projects list tells
 * you where work lives; nothing told you what was yours.
 *
 * TWO BUCKETS, NEVER ONE. Founder's decision, made before this was built:
 * agent-assigned work shows here too, CLEARLY MARKED. "A person still needs
 * to see what their agents owe them; hiding it would make My work a lie by
 * omission." So the split is the product, not a formatting choice — merging
 * them would say an agent's task is a thing the reader has to go do, and
 * dropping them would say there is nothing outstanding when there is.
 *
 *   mine   assignee_user_id === me
 *          Work a person accepted. Unambiguous: the backend's own
 *          project_tasks_single_assignee_check guarantees a task carries
 *          EITHER a human assignee or an agent one, never both.
 *
 *   agent  assigned to an agent, AND created_by === me
 *          "What my agents owe me" — work I HANDED OVER. The `created_by`
 *          half is what makes this honest rather than noisy, and it is the
 *          narrowest rule the data actually supports: FleetAgent carries no
 *          owner/creator field at all (checked — `audience: owner |
 *          external` is a trust tier, not an owner id), so "agents that are
 *          mine" is NOT expressible today. Dropping the created_by clause
 *          would silently turn this into "every agent-assigned task in every
 *          project I can see", i.e. a teammate's agents' work filed under MY
 *          name. If an owner field ever lands on the agent record, widen
 *          this to `mine-or-my-agents` THERE and delete this note — do not
 *          widen it by removing a clause.
 *
 * Everything else is deliberately absent. Unassigned/backlog tasks are NOT
 * my work: `list_my_tasks` on the backend folds them in (an agent asking
 * "what can I pick up" genuinely wants unclaimed work), but a person's own
 * "My work" is an ownership list, not a job board — the project's own
 * Tasks board is where unclaimed work is browsed. Tasks I merely CREATED and
 * handed to another PERSON are theirs, not mine.
 *
 * NOT a second backend query. `GET /api/w/{ws}/fleet/tasks` with no
 * `project_id` already returns every task in every project the caller can
 * see (routes_fleet.fleet_list_tasks' own filter-when-not-scoped branch,
 * `_visible_project_ids`), with `assignee_user_id` / `assignee_agent_id` /
 * `created_by` on every row. The backend's `list_my_tasks` was checked first,
 * per the brief, and is the WRONG function here despite the name: it scopes
 * on `assignee_agent_id` only — it is the MCP tool's "what should this AGENT
 * work on" query and has no human-assignee clause at all.
 */

/** The subset of a task row this module reads. Structural on purpose (not
 *  `Pick<FleetTask, …>`) so this module imports nothing — a real FleetTask
 *  satisfies it, and so does a test fixture, without dragging fleet-data.ts's
 *  React dependencies into a plain tsx test run. */
export type MyWorkTaskShape = {
  status?: string | null;
  assignee_user_id?: string | null;
  assignee_agent_id?: string | null;
  created_by?: string | null;
};

export type MyWorkBucket = "mine" | "agent";

/** The one place the rule lives. `null` means "not my work" — every caller
 *  (the list, the badge count, the test) goes through this, so a fourth
 *  reader cannot invent a fifth interpretation. */
export function myWorkBucket(task: MyWorkTaskShape, userId: string | null): MyWorkBucket | null {
  const me = String(userId || "").trim();
  if (!me) return null;
  if (String(task.assignee_user_id || "").trim() === me) return "mine";
  const agentAssignee = String(task.assignee_agent_id || "").trim();
  if (agentAssignee && String(task.created_by || "").trim() === me) return "agent";
  return null;
}

/** A task still needing someone's attention. `done` is the only terminal
 *  status in FLEET_TASK_STATUSES — `blocked`/`awaiting_input`/`in_review` all
 *  describe work that is still open, and burying them would be exactly the
 *  "empty and could-not-load are different facts" family of lie one level
 *  over: "nothing to do" is not the same as "nothing left to finish". */
export function isOpenMyWork(task: MyWorkTaskShape): boolean {
  return String(task.status || "").trim().toLowerCase() !== "done";
}

/** Both buckets in one pass, input order preserved within each. */
export function selectMyWork<T extends MyWorkTaskShape>(
  tasks: readonly T[],
  userId: string | null,
): { mine: T[]; agent: T[] } {
  const mine: T[] = [];
  const agent: T[] = [];
  for (const task of tasks) {
    const bucket = myWorkBucket(task, userId);
    if (bucket === "mine") mine.push(task);
    else if (bucket === "agent") agent.push(task);
  }
  return { mine, agent };
}

/** The rail badge. Counts OPEN items in BOTH buckets — the rail's number and
 *  the page's contents have to agree, and a badge that silently omitted the
 *  agent half would understate exactly the thing the founder asked to be
 *  shown. Zero renders no badge at all (the caller's job): a zero badge is
 *  noise, and this returning 0 is how it says so. */
export function myWorkBadgeCount(tasks: readonly MyWorkTaskShape[], userId: string | null): number {
  let count = 0;
  for (const task of tasks) {
    if (myWorkBucket(task, userId) !== null && isOpenMyWork(task)) count++;
  }
  return count;
}
