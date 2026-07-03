"use client";

import { useCallback, useEffect, useState } from "react";

export type FleetAgent = {
  agent_id: string;
  label: string;
  role: string;
  status: string;
  enabled: boolean;
  runtime_target: string;
  hardware_status: "online" | "offline" | "unknown";
  last_heartbeat: string | null;
  last_activity?: string | null;
  activity_preview?: string;
  model_config?: Record<string, any>;
};

export type FleetAgentActivity = {
  event_id: string;
  action: string;
  event_class: string;
  title: string;
  status: string;
  created_at: string;
};

export function useFleetAgents(workspaceId: string) {
  const [agents, setAgents] = useState<FleetAgent[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await fetch(`/api/w/${workspaceId}/fleet/agents`);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      setAgents(data.agents || []);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to load agents");
    } finally {
      setLoading(false);
    }
  }, [workspaceId]);

  useEffect(() => {
    refresh();
    const interval = setInterval(refresh, 30_000);
    return () => clearInterval(interval);
  }, [refresh]);

  return { agents, loading, error, refresh };
}

export function useFleetAgentActivity(workspaceId: string, agentId: string | null) {
  const [events, setEvents] = useState<FleetAgentActivity[]>([]);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (!agentId) {
      setEvents([]);
      return;
    }
    let cancelled = false;
    (async () => {
      setLoading(true);
      try {
        const res = await fetch(
          `/api/w/${workspaceId}/fleet/agent-activity?agent_id=${encodeURIComponent(agentId)}`
        );
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const data = await res.json();
        if (!cancelled) setEvents(data.events || []);
      } catch {
        if (!cancelled) setEvents([]);
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => { cancelled = true; };
  }, [workspaceId, agentId]);

  return { events, loading };
}
