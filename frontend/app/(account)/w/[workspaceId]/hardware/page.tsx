"use client";

import { useEffect, useState } from "react";
import { useParams } from "next/navigation";
import { Cpu } from "lucide-react";

import { GatewayPairPanel } from "@/lib/gateway/GatewayPairPanel";

type Registration = {
  gateway_id?: string;
  id?: string;
  display_name?: string;
  platform?: string;
  status?: string;
  connection_status?: string;
  last_seen_at?: string;
};

export default function HardwarePage() {
  const params = useParams();
  const workspaceId = String(params?.workspaceId || "");
  const [regs, setRegs] = useState<Registration[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    fetch(`/api/gateway/registrations?workspace_id=${encodeURIComponent(workspaceId)}`, { credentials: "include" })
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error(`HTTP ${r.status}`))))
      .then((d) => {
        if (cancelled) return;
        const list = d?.items || d?.registrations || (Array.isArray(d) ? d : []);
        setRegs(Array.isArray(list) ? list : []);
      })
      .catch((e) => { if (!cancelled) setError(e instanceof Error ? e.message : "Could not load computers"); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [workspaceId]);

  const isOnline = (r: Registration) => {
    const s = `${r.connection_status || ""} ${r.status || ""}`.toLowerCase();
    return s.includes("online") || s.includes("active") || s.includes("connected");
  };

  return (
    <main className="fleet-content">
      <div className="fleet-header">
        <div>
          <h1 className="fleet-title">Hardware</h1>
          <p className="fleet-subtitle">Computers and servers your agents can run on.</p>
        </div>
      </div>

      <div className="fleet-detail-section-title">Your computers</div>
      {loading ? (
        <div className="fleet-list"><div className="fleet-list-row"><div className="fleet-skeleton-bar" style={{ width: "35%", height: 12 }} /></div></div>
      ) : error ? (
        <div className="fleet-page-state-body">{error}</div>
      ) : regs.length === 0 ? (
        <div className="fleet-empty">
          <div className="fleet-empty-title">No computers paired yet</div>
          <div className="fleet-empty-desc">Pair one below — a cloud server or your own machine — to give agents hardware access.</div>
        </div>
      ) : (
        <div className="fleet-list">
          {regs.map((r) => (
            <div key={r.gateway_id || r.id} className="fleet-list-row" style={{ cursor: "default" }}>
              <span className="fleet-list-row-icon"><Cpu size={16} strokeWidth={1.75} /></span>
              <span className="fleet-list-row-main">
                <span className="fleet-list-row-title">{r.display_name || r.platform || r.gateway_id || "Computer"}</span>
                <span className="fleet-list-row-desc">
                  {r.platform || "unknown platform"}
                  {r.last_seen_at ? ` · last seen ${new Date(r.last_seen_at).toLocaleString()}` : ""}
                </span>
              </span>
              <span className="fleet-list-row-meta" style={{ display: "inline-flex", alignItems: "center", gap: 6 }}>
                <span className={`fleet-detail-dot ${isOnline(r) ? "is-online" : "is-offline"}`} />
                {r.connection_status || r.status || "unknown"}
              </span>
            </div>
          ))}
        </div>
      )}

      <div className="fleet-detail-section-title" style={{ marginTop: "var(--space-6)" }}>Add a computer</div>
      <GatewayPairPanel workspaceId={workspaceId} compact />
    </main>
  );
}
