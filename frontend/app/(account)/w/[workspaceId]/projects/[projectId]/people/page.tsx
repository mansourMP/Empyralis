// The project's People view — a real route, mirroring tasks/page.tsx and
// documents/page.tsx exactly (see those files' headers): ProjectDetailPage
// derives which of Tasks/Documents/Agents/People to render from
// `usePathname`, so `/people` only needs to exist as a distinct history
// entry, not carry its own logic.
export { default } from "../page";
