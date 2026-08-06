// The project's Tasks view — a real route so leaving it for a task's own
// page and pressing the browser's own back button returns here instead of
// the project's Overview. ProjectDetailPage itself derives which of
// Overview/Agents/Tasks to render from `usePathname`, so this file has
// nothing of its own to add — it exists purely so `/tasks` is a real,
// distinct history entry instead of client-only state.
export { default } from "../page";
