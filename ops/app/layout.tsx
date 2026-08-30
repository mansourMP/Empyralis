import type { Metadata } from "next";
import type { ReactNode } from "react";

import { Nav } from "@/lib/components/Nav";

import "@/lib/theme-tokens.css";
import "@/lib/ops-theme.css";

export const metadata: Metadata = {
  title: "Empyralis Operator Console",
  description: "Internal, cross-tenant instrument for the Empyralis platform.",
  robots: { index: false, follow: false },
};

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="en">
      <body>
        <div className="ops-shell">
          <Nav />
          {children}
        </div>
      </body>
    </html>
  );
}
