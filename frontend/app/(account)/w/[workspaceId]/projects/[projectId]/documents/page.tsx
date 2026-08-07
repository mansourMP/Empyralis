// The project's Documents view — a real route, mirroring tasks/page.tsx
// exactly (see that file's own header): ProjectDetailPage derives which of
// Overview/Agents/Tasks/Documents to render from `usePathname`, so `/documents`
// only needs to exist as a distinct history entry, not carry its own logic.
export { default } from "../page";
