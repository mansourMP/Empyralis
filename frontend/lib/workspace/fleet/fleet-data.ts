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

export type FleetMemoryFile = {
  path: string;
  size: number;
  modified: string;
};

export type FleetChannel = {
  id: string;
  label: string;
  summary: string;
  image: string | null;
  connected: boolean;
  setupHint: string;
};

export type FleetConnector = {
  id: string;
  label: string;
  summary: string;
  image: string | null;
  connected: boolean;
  kind: string;
  oauthUrl: string | null;
};

export type FleetTool = {
  id: string;
  label: string;
  description: string;
  action_class: string;
};

export function useFleetAgentMemory(workspaceId: string, agentId: string | null) {
  const [files, setFiles] = useState<FleetMemoryFile[]>([]);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (!agentId) { setFiles([]); return; }
    let cancelled = false;
    (async () => {
      setLoading(true);
      try {
        const res = await fetch(
          `/api/w/${workspaceId}/fleet/agent-memory?agent_id=${encodeURIComponent(agentId)}`
        );
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const data = await res.json();
        if (!cancelled) setFiles(data.files || []);
      } catch { if (!cancelled) setFiles([]); }
      finally { if (!cancelled) setLoading(false); }
    })();
    return () => { cancelled = true; };
  }, [workspaceId, agentId]);

  return { files, loading };
}

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
