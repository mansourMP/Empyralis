// The project's Agents view — a real route so leaving it for an agent's or
// task's own page and pressing the browser's own back button returns here
// instead of the project's Overview. ProjectDetailPage itself derives which
// of Overview/Agents/Tasks to render from `usePathname`, so this file has
// nothing of its own to add — it exists purely so `/agents` is a real,
// distinct history entry instead of client-only state.
export { default } from "../page";
