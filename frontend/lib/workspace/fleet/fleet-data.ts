"use client";

import { useCallback, useEffect, useState } from "react";

export type FleetAgent = {
  agent_id: string;
  label: string;
  role: string;
  purpose_preset?: "customer_facing" | "internal_assistant" | "operator";
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

export type FleetChannel = {
  id: string;
  label: string;
  summary: string;
  connected: boolean;
  requiresGateway: boolean;
  gatewayCount: number;
  onlineGatewayCount: number;
  nextAction: string;
  runtimeUsable: boolean;
  setupAvailable: boolean;
};

export type FleetConnector = {
  id: string;
  label: string;
  summary: string;
  connected: boolean;
  kind: string;
  nextAction: string;
  healthStatus: string;
  authRequiredFields: string[];
};

export type FleetTool = {
  id: string;
  label: string;
  description: string;
  action_class: string;
};

export function useFleetAgentChannels(workspaceId: string, agentId: string | null) {
  const [channels, setChannels] = useState<FleetChannel[]>([]);
  const [hostedTelegramConfigured, setHostedTelegramConfigured] = useState(false);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (!agentId) { setChannels([]); return; }
    let cancelled = false;
    (async () => {
      setLoading(true);
      try {
        const res = await fetch(
          `/api/w/${workspaceId}/fleet/agent-channels?agent_id=${encodeURIComponent(agentId)}`
        );
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const data = await res.json();
        if (!cancelled) {
          setChannels(data.channels || []);
          setHostedTelegramConfigured(data.hosted_telegram_configured || false);
        }
      } catch { if (!cancelled) setChannels([]); }
      finally { if (!cancelled) setLoading(false); }
    })();
    return () => { cancelled = true; };
  }, [workspaceId, agentId]);

  return { channels, hostedTelegramConfigured, loading };
}

export function useFleetAgentConnectors(workspaceId: string, agentId: string | null) {
  const [connectors, setConnectors] = useState<FleetConnector[]>([]);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (!agentId) { setConnectors([]); return; }
    let cancelled = false;
    (async () => {
      setLoading(true);
      try {
        const res = await fetch(
          `/api/w/${workspaceId}/fleet/agent-connectors?agent_id=${encodeURIComponent(agentId)}`
        );
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const data = await res.json();
        if (!cancelled) setConnectors(data.connectors || []);
      } catch { if (!cancelled) setConnectors([]); }
      finally { if (!cancelled) setLoading(false); }
    })();
    return () => { cancelled = true; };
  }, [workspaceId, agentId]);

  return { connectors, loading };
}

export function useFleetAgentTools(workspaceId: string, agentId: string | null) {
  const [tools, setTools] = useState<FleetTool[]>([]);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (!agentId) { setTools([]); return; }
    let cancelled = false;
    (async () => {
      setLoading(true);
      try {
        const res = await fetch(
          `/api/w/${workspaceId}/fleet/agent-tools?agent_id=${encodeURIComponent(agentId)}`
        );
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const data = await res.json();
        if (!cancelled) setTools(data.tools || []);
      } catch { if (!cancelled) setTools([]); }
      finally { if (!cancelled) setLoading(false); }
    })();
    return () => { cancelled = true; };
  }, [workspaceId, agentId]);

  return { tools, loading };
}

export type WorkspaceActivityEvent = {
  id: string | null;
  title: string | null;
  summary: string | null;
  event_class: string | null;
  action: string | null;
  status: string | null;
  created_at: string | null;
};

export function useWorkspaceActivity(workspaceId: string, limit = 8) {
  const [events, setEvents] = useState<WorkspaceActivityEvent[]>([]);
  const [loading, setLoading] = useState(true);

  const refresh = useCallback(async () => {
    try {
      const res = await fetch(`/api/activity/timeline?workspace_id=${encodeURIComponent(workspaceId)}&limit=${limit}`, {
        credentials: "include",
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      setEvents(Array.isArray(data.items) ? data.items : []);
    } catch {
      setEvents([]);
    } finally {
      setLoading(false);
    }
  }, [workspaceId, limit]);

  useEffect(() => {
    refresh();
    const interval = setInterval(refresh, 30_000);
    return () => clearInterval(interval);
  }, [refresh]);

  return { events, loading };
}

export type WorkspaceStatusStrip = {
  channelsConnected: number;
  channelsTotal: number;
  connectorsConnected: number;
  connectorsTotal: number;
  hardwareOnline: number;
  hardwareTotal: number;
  loading: boolean;
};

export function useWorkspaceStatusStrip(workspaceId: string): WorkspaceStatusStrip {
  const [state, setState] = useState<Omit<WorkspaceStatusStrip, "loading">>({
    channelsConnected: 0,
    channelsTotal: 0,
    connectorsConnected: 0,
    connectorsTotal: 0,
    hardwareOnline: 0,
    hardwareTotal: 0,
  });
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        // Phase 2: channels/connectors counters are the HONEST workspace
        // aggregate — the sum of ENABLED per-agent bindings over catalog size —
        // served by fleet/connection-summary. Computers still come from live
        // gateway registrations.
        const [summaryRes, gatewaysRes] = await Promise.all([
          fetch(`/api/w/${encodeURIComponent(workspaceId)}/fleet/connection-summary`, { credentials: "include" }),
          fetch(`/api/gateway/registrations?workspace_id=${encodeURIComponent(workspaceId)}`, { credentials: "include" }),
        ]);
        const summary = summaryRes.ok ? await summaryRes.json() : {};
        const gatewaysData = gatewaysRes.ok ? await gatewaysRes.json() : { items: [] };

        const connectors = summary.connectors || { connected: 0, total: 0 };
        const channels = summary.channels || { connected: 0, total: 0 };
        const gatewayItems = gatewaysData.items || [];

        if (!cancelled) {
          setState({
            channelsConnected: Number(channels.connected) || 0,
            channelsTotal: Number(channels.total) || 0,
            connectorsConnected: Number(connectors.connected) || 0,
            connectorsTotal: Number(connectors.total) || 0,
            hardwareOnline: gatewayItems.filter((g: any) =>
              String(g.connection_status || g.status || "").toLowerCase() === "online").length,
            hardwareTotal: gatewayItems.length,
          });
        }
      } catch {
        // Leave defaults — status strip just shows zeros rather than erroring the page.
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => { cancelled = true; };
  }, [workspaceId]);

  return { ...state, loading };
}
