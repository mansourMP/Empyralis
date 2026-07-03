"use client";

import { useRouter, useSelectedLayoutSegment } from "next/navigation";

// ── Design tokens (matching FleetHome.reference.tsx) ──
const C = {
  railBg: "#161618",
  border: "rgba(255,255,255,0.08)",
  textPrimary: "#f4f4f5",
  textSecondary: "#a1a1aa",
  textMuted: "#71717a",
  accent: "#7c3aed",
  activeBg: "rgba(255,255,255,0.06)",
};

const RAIL_ITEMS = [
  { key: "home", label: "Home", segment: "fleet" },
  { key: "agents", label: "Agents", segment: "agents" },
  { key: "channels", label: "Channels", segment: "channels" },
  { key: "connectors", label: "Connectors", segment: "integrations" },
  { key: "hardware", label: "Hardware", segment: "hardware" },
  { key: "memory", label: "Memory", segment: "memory" },
  { key: "billing", label: "Billing", segment: "settings" },
];

/**
 * Persistent primary rail — 220px wide, lives in the workspace layout.
 * Matches FleetHome.reference.tsx exactly.
 */
export function PrimaryRail({
  workspaceId,
  ownerName = "Owner",
}: {
  workspaceId: string;
  ownerName?: string;
}) {
  const router = useRouter();
  const segment = useSelectedLayoutSegment();
  const activeSegment = segment || "fleet"; // landing page = fleet

  return (
    <aside
      style={{
        width: 220,
        flexShrink: 0,
        background: C.railBg,
        borderRight: `0.5px solid ${C.border}`,
        padding: "1rem 0.75rem",
        display: "flex",
        flexDirection: "column",
        fontFamily: "var(--font-dm-sans, system-ui)",
      }}
    >
      {/* Brand */}
      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: 10,
          padding: "0 8px 1.25rem",
        }}
      >
        <div
          style={{
            width: 30,
            height: 30,
            borderRadius: 8,
            background: C.accent,
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            fontSize: 16,
            fontWeight: 700,
            color: "#fff",
            flexShrink: 0,
          }}
        >
          E
        </div>
        <span style={{ fontSize: 16, fontWeight: 500, color: C.textPrimary }}>
          Empyralis
        </span>
      </div>

      {/* Nav items */}
      <nav style={{ display: "flex", flexDirection: "column", gap: 2 }}>
        {RAIL_ITEMS.map((item) => {
          const active = activeSegment === item.segment;
          return (
            <button
              key={item.key}
              onClick={() => {
                if (item.segment === "fleet") {
                  router.push(`/w/${encodeURIComponent(workspaceId)}/fleet`);
                } else {
                  router.push(
                    `/w/${encodeURIComponent(workspaceId)}/${item.segment}`
                  );
                }
              }}
              style={{
                display: "flex",
                alignItems: "center",
                gap: 11,
                padding: "9px 11px",
                borderRadius: 8,
                border: "none",
                cursor: "pointer",
                textAlign: "left" as const,
                background: active ? C.activeBg : "transparent",
                color: active ? C.textPrimary : C.textSecondary,
                fontSize: 14.5,
                fontFamily: "inherit",
                width: "100%",
              }}
            >
              {item.label}
            </button>
          );
        })}
      </nav>

      {/* Owner footer */}
      <div
        style={{
          marginTop: "auto",
          display: "flex",
          alignItems: "center",
          gap: 10,
          padding: "10px 8px 0",
          borderTop: `0.5px solid ${C.border}`,
        }}
      >
        <div
          style={{
            width: 30,
            height: 30,
            borderRadius: "50%",
            background: "#0C447C",
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            fontSize: 13,
            fontWeight: 500,
            color: "#85B7EB",
            flexShrink: 0,
          }}
        >
          {ownerName.charAt(0).toUpperCase()}
        </div>
        <div style={{ lineHeight: 1.25, minWidth: 0 }}>
          <div
            style={{
              fontSize: 13.5,
              color: C.textPrimary,
              overflow: "hidden",
              textOverflow: "ellipsis",
              whiteSpace: "nowrap" as const,
            }}
          >
            {ownerName}
          </div>
          <div style={{ fontSize: 11.5, color: C.textMuted }}>Owner</div>
        </div>
      </div>
    </aside>
  );
}
