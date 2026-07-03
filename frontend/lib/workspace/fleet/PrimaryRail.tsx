"use client";

import { useRouter, useSelectedLayoutSegment } from "next/navigation";
import {
  Home as HomeIcon,
  Bot,
  Radio,
  Layers,
  Cpu,
  Brain,
  CreditCard,
  Zap,
} from "lucide-react";

const PRIMARY_RAIL_ITEMS = [
  { id: "home", label: "Home", icon: <HomeIcon size={18} />, segment: "fleet" },
  { id: "agents", label: "Agents", icon: <Bot size={18} />, segment: "agents" },
  { id: "channels", label: "Channels", icon: <Radio size={18} />, segment: "channels" },
  { id: "connectors", label: "Connectors", icon: <Layers size={18} />, segment: "integrations" },
  { id: "hardware", label: "Hardware", icon: <Cpu size={18} />, segment: "hardware" },
  { id: "memory", label: "Memory", icon: <Brain size={18} />, segment: "memory" },
  { id: "billing", label: "Billing", icon: <CreditCard size={18} />, segment: "settings" },
];

/**
 * Persistent primary rail — renders in the workspace layout and never swaps.
 * Phase UX-C: extracted from FleetHome so the rail persists across all workspace sub-routes.
 */
export function PrimaryRail({ workspaceId }: { workspaceId: string }) {
  const router = useRouter();
  const segment = useSelectedLayoutSegment();

  // Determine active item: the current URL segment, or "home" if at the fleet root
  const activeSegment = segment || "fleet";

  return (
    <nav className="fleet-rail">
      <div className="fleet-rail-brand">
        <Zap size={20} />
      </div>
      <div className="fleet-rail-items">
        {PRIMARY_RAIL_ITEMS.map((item) => (
          <button
            key={item.id}
            className={`fleet-rail-item ${activeSegment === item.segment ? "fleet-rail-item--active" : ""}`}
            onClick={() => {
              const route = item.segment;
              if (route === "fleet") {
                router.push(`/w/${encodeURIComponent(workspaceId)}/fleet`);
              } else {
                router.push(`/w/${encodeURIComponent(workspaceId)}/${route}`);
              }
            }}
            title={item.label}
          >
            {item.icon}
          </button>
        ))}
      </div>
    </nav>
  );
}
