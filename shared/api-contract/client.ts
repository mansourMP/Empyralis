import type {
  AgentTurnRequest,
  AgentTurnResponse,
  ApprovalListResponse,
  ApprovalResolveRequest,
  ApprovalResolveResponse,
  ArtifactListResponse,
  ConnectorListResponse,
  HealthResponse,
  MachineListResponse,
  NotificationDeviceRegistrationRequest,
  NotificationDeviceRegistrationResponse,
  NotificationListResponse,
  NotificationReadRequest,
  NotificationReadResponse,
  RunDetailResponse,
  RunListResponse,
  RunReplayResponse,
  SessionCreateRequest,
  SessionResponse,
  ThreadListResponse,
  ThreadRecord,
} from "./index";

export class ApiClientError extends Error {
  readonly status: number;
  readonly details?: unknown;

  constructor(message: string, status: number, details?: unknown) {
    super(message);
    this.name = "ApiClientError";
    this.status = status;
    this.details = details;
  }
}

export type EventStreamMessage = {
  event?: string;
  data?: string;
  id?: string | null;
};

export type EventStreamConnection = {
  close: () => void;
};

export type TurnStreamRequestOptions = {
  clientRequestId?: string;
  lastEventId?: string;
  signal?: AbortSignal | null;
};

export type NotificationStreamOptions = {
  workspace_id?: string;
  channel?: string;
  session_key?: string;
  direction?: string;
  action?: string;
  run_id?: string;
  trace_id?: string;
  limit?: number;
  include_backlog?: boolean;
  since_id?: string;
  since_ts?: string;
  onOpen?: () => void;
  onEvent?: (event: EventStreamMessage) => void;
  onError?: (error: unknown) => void;
  onClose?: () => void;
};

export type ApiClientInit = {
  fetchFn?: typeof fetch;
  beforeRequest?: () => Promise<void> | void;
  getHeaders?: () => Promise<HeadersInit | undefined> | HeadersInit | undefined;
  buildUrl?: (path: string) => string;
  openEventStream?: (options: {
    url: string;
    onOpen?: () => void;
    onEvent?: (event: EventStreamMessage) => void;
    onError?: (error: unknown) => void;
    onClose?: () => void;
  }) => EventStreamConnection;
};

function normalizePath(path: string): string {
  if (!path) return "";
  return path.startsWith("/") ? path : `/${path}`;
}

function buildQuery(params: Record<string, unknown>): string {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value == null || value === "") continue;
    search.set(key, String(value));
  }
  return search.toString();
}

async function parseError(response: Response): Promise<ApiClientError> {
  const raw = await response.text().catch(() => "");
  let details: unknown;
  let message = `API request failed (${response.status})`;
  if (raw.trim()) {
    try {
      details = JSON.parse(raw) as unknown;
      const record = details && typeof details === "object" ? (details as Record<string, unknown>) : {};
      if (typeof record.detail === "string" && record.detail.trim()) message = record.detail.trim();
      else if (typeof record.message === "string" && record.message.trim()) message = record.message.trim();
      else message = raw.trim();
    } catch {
      message = raw.trim();
    }
  }
  return new ApiClientError(message, response.status, details);
}

export function createApiClient(init: ApiClientInit = {}) {
  const fetchFn = init.fetchFn || fetch;
  const buildUrl = init.buildUrl || ((path: string) => normalizePath(path));

  async function send(path: string, requestInit: RequestInit = {}): Promise<Response> {
    if (init.beforeRequest) await init.beforeRequest();
    const baseHeaders = init.getHeaders ? await init.getHeaders() : undefined;
    const headers = new Headers(baseHeaders || {});
    const requestHeaders = new Headers(requestInit.headers || {});
    requestHeaders.forEach((value, key) => headers.set(key, value));
    return fetchFn(buildUrl(normalizePath(path)), {
      ...requestInit,
      headers,
      cache: requestInit.cache || "no-store",
      credentials: requestInit.credentials || "same-origin",
    });
  }

  async function jsonRequest<T>(path: string, requestInit: RequestInit = {}): Promise<T> {
    const response = await send(path, requestInit);
    if (!response.ok) throw await parseError(response);
    if (response.status === 204) return undefined as T;
    return response.json() as Promise<T>;
  }

  return {
    createSession(request: SessionCreateRequest): Promise<SessionResponse> {
      return jsonRequest<SessionResponse>("/sessions", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(request),
      });
    },

    getSession(sessionId: string): Promise<SessionResponse> {
      return jsonRequest<SessionResponse>(`/sessions/${encodeURIComponent(sessionId)}`);
    },

    listThreads(params: {
      workspace_id?: string;
      include_turns?: boolean;
      limit?: number;
    } = {}): Promise<ThreadListResponse> {
      const query = buildQuery(params);
      return jsonRequest<ThreadListResponse>(`/threads${query ? `?${query}` : ""}`);
    },

    getThread(threadId: string): Promise<ThreadRecord> {
      return jsonRequest<ThreadRecord>(`/threads/${encodeURIComponent(threadId)}`);
    },

    turn(request: AgentTurnRequest): Promise<AgentTurnResponse> {
      return jsonRequest<AgentTurnResponse>("/turn", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(request),
      });
    },

    openTurnStreamResponse(
      request: AgentTurnRequest,
      options: TurnStreamRequestOptions = {},
    ): Promise<Response> {
      const hints = request.context_hints && typeof request.context_hints === "object"
        ? { ...request.context_hints }
        : {};
      if (options.clientRequestId) hints.request_id = options.clientRequestId;
      if (options.lastEventId) hints.last_event_id = options.lastEventId;
      const payload: AgentTurnRequest = {
        ...request,
        execution_mode: request.execution_mode || "sync",
        response_mode: request.response_mode || "stream",
        context_hints: hints,
      };
      const headers = new Headers({ "Content-Type": "application/json" });
      if (options.lastEventId) headers.set("Last-Event-ID", options.lastEventId);
      return send("/turn", {
        method: "POST",
        headers,
        body: JSON.stringify(payload),
        signal: options.signal || undefined,
      });
    },

    listRuns(params: {
      workspace_id?: string;
      limit?: number;
      offset?: number;
      status?: string;
      pack_id?: string;
    } = {}): Promise<RunListResponse> {
      const query = buildQuery(params);
      return jsonRequest<RunListResponse>(`/runs${query ? `?${query}` : ""}`);
    },

    getRunDetail(runId: string): Promise<RunDetailResponse> {
      return jsonRequest<RunDetailResponse>(`/runs/${encodeURIComponent(runId)}`);
    },

    getRunReplay(runId: string): Promise<RunReplayResponse> {
      return jsonRequest<RunReplayResponse>(`/runs/${encodeURIComponent(runId)}/replay`);
    },

    listApprovals(params: { workspace_id?: string } = {}): Promise<ApprovalListResponse> {
      const query = buildQuery(params);
      return jsonRequest<ApprovalListResponse>(`/approvals${query ? `?${query}` : ""}`);
    },

    resolveApproval(
      approvalId: string,
      request: ApprovalResolveRequest,
    ): Promise<ApprovalResolveResponse> {
      return jsonRequest<ApprovalResolveResponse>(`/approvals/${encodeURIComponent(approvalId)}/resolve`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(request),
      });
    },

    listArtifacts(params: {
      workspace_id?: string;
      history_limit?: number;
      limit?: number;
    } = {}): Promise<ArtifactListResponse> {
      const query = buildQuery(params);
      return jsonRequest<ArtifactListResponse>(`/artifacts${query ? `?${query}` : ""}`);
    },

    async fetchArtifactContent(path: string): Promise<Blob> {
      const response = await send(`/artifacts/content?path=${encodeURIComponent(String(path || "").trim())}`, {
        method: "GET",
      });
      if (!response.ok) throw await parseError(response);
      return response.blob();
    },

    listMachines(): Promise<MachineListResponse> {
      return jsonRequest<MachineListResponse>("/machines");
    },

    listConnectors(params: { workspace_id?: string } = {}): Promise<ConnectorListResponse> {
      const query = buildQuery(params);
      return jsonRequest<ConnectorListResponse>(`/connectors${query ? `?${query}` : ""}`);
    },

    listNotifications(params: {
      workspace_id?: string;
      channel?: string;
      session_key?: string;
      direction?: string;
      action?: string;
      run_id?: string;
      trace_id?: string;
      limit?: number;
      include_backlog?: boolean;
      since_id?: string;
      since_ts?: string;
    } = {}): Promise<NotificationListResponse> {
      const query = buildQuery(params);
      return jsonRequest<NotificationListResponse>(`/notifications${query ? `?${query}` : ""}`);
    },

    openNotificationsStream(options: NotificationStreamOptions): EventStreamConnection {
      if (!init.openEventStream) {
        throw new ApiClientError("Notifications streaming is not configured for this client.", 500);
      }
      const query = buildQuery({
        workspace_id: options.workspace_id,
        channel: options.channel,
        session_key: options.session_key,
        direction: options.direction,
        action: options.action,
        run_id: options.run_id,
        trace_id: options.trace_id,
        limit: options.limit,
        include_backlog: options.include_backlog,
        since_id: options.since_id,
        since_ts: options.since_ts,
        stream: true,
      });
      return init.openEventStream({
        url: buildUrl(`/notifications${query ? `?${query}` : "?stream=true"}`),
        onOpen: options.onOpen,
        onEvent: options.onEvent,
        onError: options.onError,
        onClose: options.onClose,
      });
    },

    markNotificationsRead(request: NotificationReadRequest): Promise<NotificationReadResponse> {
      return jsonRequest<NotificationReadResponse>("/notifications", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(request),
      });
    },

    registerNotificationDevice(
      request: NotificationDeviceRegistrationRequest,
    ): Promise<NotificationDeviceRegistrationResponse> {
      return jsonRequest<NotificationDeviceRegistrationResponse>("/notifications/devices", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(request),
      });
    },

    getHealth(): Promise<HealthResponse> {
      return jsonRequest<HealthResponse>("/health");
    },
  };
}
