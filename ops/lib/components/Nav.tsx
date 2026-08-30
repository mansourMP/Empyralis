"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

type NavItem = { href: string; label: string };

const NAV_ITEMS: NavItem[] = [
  { href: "/overview", label: "Overview" },
  { href: "/accounts", label: "Accounts" },
  { href: "/funnel", label: "Funnel" },
  { href: "/retention", label: "Retention" },
  { href: "/failures", label: "Failures" },
  { href: "/spend", label: "Spend" },
];

/**
 * The whole nav for this app -- six links, real <a> elements via next/link
 * (cmd-click works, CLAUDE.md's own rule for primary navigation). No
 * PrimaryRail import from frontend/lib/workspace/fleet/ -- that component
 * carries agent/channel/hardware picking logic this app has no use for and
 * would be exactly the "drag in customer UI" the founder said not to do.
 *
 * Selection is weight and shape (a bottom border + full-weight text),
 * never hue -- see lib/ops-theme.css's header.
 */
export function Nav() {
  const pathname = usePathname();

  return (
    <div className="ops-topbar">
      <span className="ops-topbar-brand">
        Empyralis <span className="ops-topbar-brand-mark">Operator</span>
      </span>
      <nav className="ops-nav" aria-label="Operator console sections">
        {NAV_ITEMS.map((item) => {
          const active = pathname === item.href || pathname?.startsWith(`${item.href}/`);
          return (
            <Link
              key={item.href}
              href={item.href}
              className="ops-nav-link"
              aria-current={active ? "page" : undefined}
            >
              {item.label}
            </Link>
          );
        })}
      </nav>
    </div>
  );
}
