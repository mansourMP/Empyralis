// The project's Tasks view — a real route so leaving it for a task's own
// page and pressing the in-app ‹ back button returns here instead of the
// project's Overview (see fleet-tabs.ts: each FleetTab keeps its own history
// of the urls it has visited, keyed on pathname). ProjectDetailPage itself
// derives which of Overview/Agents/Tasks to render from `usePathname`, so
// this file has nothing of its own to add — it exists purely so `/tasks` is
// a real, distinct entry in that history instead of client-only state.
export { default } from "../page";
