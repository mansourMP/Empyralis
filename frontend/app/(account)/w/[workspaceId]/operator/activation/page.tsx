"use client";

import { useCallback, useEffect, useState } from "react";
import { Lock } from "lucide-react";

import { fleetAuthorizedFetch } from "@/lib/workspace/fleet/fleet-authorized-fetch";
import { FleetSurfaceError } from "@/lib/workspace/fleet/fleet-states";
import { getErrorMessage } from "@/lib/ui/api-error";
import {
  planPlatformActivationView,
  platformActivationDetailStats,
  platformActivationSummaryStats,
  type PlatformActivationSnapshot,
  type PlatformActivationStat,
} from "@/lib/workspace/fleet/platform-activation";

/**
 * MAN-149 ("Activation, not acquisition") — an operator-only instrument, not
 * a marketing page: is anyone actually using Empyralis. Backed by
 * `GET /api/internal/platform-activation` (server_modules/routes_health.py),
 * gated server-side on `has_platform_fleet_operator_access` — a non-operator
 * gets a 403 here, rendered as its own `forbidden` state (see
 * platform-activation.ts's header), never a generic error.
 *
 * Reachable only by direct URL — deliberately NOT a PrimaryRail destination
 * (primary-rail-nav.test.ts's own fixed RAIL_ITEMS list is untouched by this
 * page), the same "live, unlinked route" treatment /context and
 * /conversations already have. It still renders inside the ordinary
 * FleetShell/breadcrumb chrome (see the workspace layout), so no page-title
 * header here — the breadcrumb ("Operator / Activation") is this page's real
 * `<h1>` (MAN-145 title-dedup), matching every other routed page in this
 * directory.
 *
 * SUMMARY BEFORE DETAIL: `platformActivationSummaryStats` (the small,
 * activation-shaped set — signed-up users, users who ever did something,
 * multi-member workspaces, agents that ran) renders first and is visually
 * distinct from `platformActivationDetailStats` (raw totals) via each
 * stat's own `isActivation` flag — never inferred twice.
 */

function usePlatformActivationSnapshot() {
  const [snapshot, setSnapshot] = useState<PlatformActivationSnapshot | null>(null);
  const [status, setStatus] = useState<number | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    setLoading(true);
    try {
      const res = await fleetAuthorizedFetch("/api/internal/platform-activation", {
        credentials: "include",
      });
      setStatus(res.status);
      let body: unknown = null;
      try {
        body = await res.json();
      } catch {
        body = null;
      }
      if (!res.ok) {
        // "Empty" and "could not load" are different facts (CLAUDE.md) — a
        // non-ok response never becomes a snapshot, even a zeroed one.
        setSnapshot(null);
        setError(getErrorMessage(body, `HTTP ${res.status}`));
        return;
      }
      setSnapshot(body as PlatformActivationSnapshot);
      setError(null);
    } catch (e) {
      // The request never produced a response at all (offline, DNS, CORS) —
      // status stays null, which planPlatformActivationView reads as
      // "error", never "forbidden" (that needs a real 401/403 from the
      // server) and never "ready".
      setStatus(null);
      setSnapshot(null);
      setError(e instanceof Error ? e.message : "Could not reach the server.");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  return { snapshot, status, loading, error, refresh };
}

function StatCard({ stat }: { stat: PlatformActivationStat }) {
  return (
    <div
      className={`fleet-stat-card${stat.isActivation ? " fleet-stat-card--activation" : ""}`}
    >
      <div className="fleet-stat-value">{stat.value.toLocaleString("en-US")}</div>
      <div className="fleet-stat-label">{stat.label}</div>
      {stat.caption && <div className="fleet-stat-caption">{stat.caption}</div>}
    </div>
  );
}

function StatGridSkeleton({ count }: { count: number }) {
  return (
    <div className="fleet-stat-grid" aria-busy="true" aria-label="Loading activation numbers">
      {Array.from({ length: count }).map((_, i) => (
        <div key={i} className="fleet-stat-card">
          <div className="fleet-skeleton-bar" style={{ width: "45%", height: 18 }} />
          <div className="fleet-skeleton-bar" style={{ width: "70%", height: 11, marginTop: 6, opacity: 0.7 }} />
        </div>
      ))}
    </div>
  );
}

export default function PlatformActivationPage() {
  const { snapshot, status, loading, error, refresh } = usePlatformActivationSnapshot();
  const view = planPlatformActivationView({ loading, status, error, snapshot });

  if (view.kind === "loading") {
    return (
      <main className="fleet-content fleet-content--wide">
        <StatGridSkeleton count={4} />
        <StatGridSkeleton count={8} />
      </main>
    );
  }

  if (view.kind === "forbidden") {
    return (
      <main className="fleet-content fleet-content--wide">
        <div className="fleet-page-state" role="alert">
          <Lock size={22} strokeWidth={1.75} />
          <div className="fleet-page-state-title">Operator access required</div>
          <div className="fleet-page-state-body">
            This page shows every workspace&rsquo;s numbers, not just yours — only a platform operator can see it.
          </div>
        </div>
      </main>
    );
  }

  if (view.kind === "error") {
    return (
      <main className="fleet-content fleet-content--wide">
        <FleetSurfaceError
          title="Couldn’t load the activation snapshot"
          message={view.message}
          onRetry={refresh}
        />
      </main>
    );
  }

  const { snapshot: data } = view;
  const summary = platformActivationSummaryStats(data);
  const detail = platformActivationDetailStats(data);

  return (
    <main className="fleet-content fleet-content--wide">
      <p className="fleet-page-state-body" style={{ marginBottom: "var(--space-3)" }}>
        Is anyone actually using the platform — signed-up totals below, real activation above.
      </p>

      <div className="fleet-stat-grid">
        {summary.map((stat) => (
          <StatCard key={stat.key} stat={stat} />
        ))}
      </div>

      <h2 className="fleet-detail-section-title" style={{ marginTop: "var(--space-6)" }}>
        Platform totals
      </h2>
      <div className="fleet-stat-grid">
        {detail.map((stat) => (
          <StatCard key={stat.key} stat={stat} />
        ))}
      </div>
    </main>
  );
}
