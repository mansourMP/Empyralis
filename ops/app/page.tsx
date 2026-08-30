import { redirect } from "next/navigation";

// The bare root has no content of its own -- it lands on Overview, the
// platform-wide summary, same "landing = the real first page, not a stub"
// shape as frontend's own workspace-root redirect (see that file's comment
// for why this is a real Next.js page-level redirect and not a
// next.config.ts rewrite, which resolves ahead of the router and could make
// a route permanently unreachable with no error anywhere -- CLAUDE.md).
export default function RootPage() {
  redirect("/overview");
}
