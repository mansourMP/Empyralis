/**
 * The workspace home's project cards summarize WORK, never agent
 * headcount — CLAUDE.md's own positioning correction: "The WORKSPACE is
 * the product. The agent layer is the second thing, not the headline...
 * Linear never says 'AI'. They say track your issues, work with your
 * teammates." The founder's own diagnosis of the page this replaces:
 * every number on it was agent plumbing ("10 agents", "1 agent"), and not
 * one project card said how much work was actually inside it.
 *
 * `task_count`/`document_count` ride on FleetProject already — routes_fleet
 * .fleet_projects composes them alongside the existing `agent_count`, via
 * project_tasks_service.count_tasks_by_project /
 * project_documents_repository.count_documents_by_project (same
 * one-query-per-workspace shape as the pre-existing count_agents_by_project
 * — not a third counting path, the same one extended). This module is only
 * the DISPLAY rule, same split as agent-count-shape.ts / channel-doors.ts:
 * a pure function a plain test imports directly, so the expected text and
 * the actual text can never come from two places.
 *
 * Run: npx tsx lib/workspace/fleet/project-work-summary.test.ts
 */

export function formatProjectWorkSummary(
  taskCount: number | undefined | null,
  documentCount: number | undefined | null,
): string {
  const tasks = Math.max(0, Number(taskCount) || 0);
  const documents = Math.max(0, Number(documentCount) || 0);

  const parts: string[] = [];
  if (tasks > 0) parts.push(`${tasks} ${tasks === 1 ? "task" : "tasks"}`);
  if (documents > 0) parts.push(`${documents} ${documents === 1 ? "document" : "documents"}`);

  // Both zero is a real, common state (a brand-new project) — say so
  // plainly rather than rendering an empty meta line, which would read as
  // a loading glitch rather than an honest "nothing here yet."
  if (parts.length === 0) return "No tasks or documents yet";
  return parts.join(" · ");
}
