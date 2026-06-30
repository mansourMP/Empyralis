'use client';

import {
  type PropsWithChildren,
  createContext,
  useContext,
  useEffect,
  useMemo,
  useSyncExternalStore,
} from 'react';

import { buildCookieAuthHeaders } from '@/lib/auth/csrf';
import type { WorkspaceBootstrapPayload } from '@/lib/workspace/workspace-bootstrap';
import {
  createWorkstationClient,
  type WorkstationClient,
} from '@/lib/workspace/workstation-client';
import {
  createWorkstationStreamManager,
  type WorkstationStreamManager,
  type WorkstationStreamState,
} from '@/lib/workspace/workstation-stream-manager';

type QueryExecutor<T> = (context: { signal: AbortSignal; cacheKey: string }) => Promise<T>;
type StoreListener<T> = (state: T) => void;
type DisposeFn = () => void;
export type WorkspaceApiEnv = Partial<Record<'NEXT_PUBLIC_ORION_API_URL' | 'NEXT_PUBLIC_API_URL', string | undefined>>;

function normalizeCacheKey(prefix: string, key: string): string {
  return `${prefix}:query:${key}`;
}

function normalizeStorageKey(prefix: string, key: string): string {
  return `${prefix}:persist:${key}`;
}

export function resolveWorkspaceApiBaseUrl(
  env: WorkspaceApiEnv = process.env as WorkspaceApiEnv,
  windowOrigin?: string,
): string {
  if (windowOrigin && windowOrigin.trim()) {
    return windowOrigin.replace(/\/+$/, '');
  }

  if (typeof window !== 'undefined') {
    return window.location.origin.replace(/\/+$/, '');
  }

  const envBase =
    env.NEXT_PUBLIC_ORION_API_URL
    ?? env.NEXT_PUBLIC_API_URL
    ?? '';

  if (envBase.trim()) {
    return envBase.replace(/\/+$/, '');
  }

  return '';
}

function resolveApiBaseUrl(): string {
  return resolveWorkspaceApiBaseUrl(
    process.env as WorkspaceApiEnv,
    typeof window !== 'undefined' ? window.location.origin : undefined,
  );
}

export type WorkstationKernelScope = {
  accountId: string;
  tenantId: string;
  workspaceId: string;
  membershipVersion: string;
  shellProfileId: string;
  kernelKey: string;
};

const WORKSTATION_UI_PREFERENCE_PATTERNS: RegExp[] = [
  /^feature:[^:]+:surface$/i,
  /^ui:/i,
  /^layout:/i,
  /^pane:/i,
  /^rail:/i,
  /^switcher:/i,
  /^workstation:/i,
];

export function isWorkstationPreferencePersistenceKey(key: string): boolean {
  return WORKSTATION_UI_PREFERENCE_PATTERNS.some((pattern) => pattern.test(key));
}

class WorkspaceDisposableRegistry {
  private readonly disposers = new Set<DisposeFn>();
  private readonly objectUrls = new Set<string>();
  private readonly intervals = new Set<number>();
  private readonly timeouts = new Set<number>();
  private readonly workers = new Set<Worker>();
  private readonly sockets = new Set<WebSocket>();
  private readonly eventSources = new Set<EventSource>();
  private readonly abortControllers = new Set<AbortController>();

  add(disposer: DisposeFn): DisposeFn {
    this.disposers.add(disposer);
    return () => {
      this.disposers.delete(disposer);
    };
  }

  trackObjectUrl(url: string): string {
    this.objectUrls.add(url);
    return url;
  }

  trackInterval(id: number): number {
    this.intervals.add(id);
    return id;
  }

  trackTimeout(id: number): number {
    this.timeouts.add(id);
    return id;
  }

  trackWorker<T extends Worker>(worker: T): T {
    this.workers.add(worker);
    return worker;
  }

  trackSocket<T extends WebSocket>(socket: T): T {
    this.sockets.add(socket);
    return socket;
  }

  trackEventSource<T extends EventSource>(source: T): T {
    this.eventSources.add(source);
    return source;
  }

  trackAbortController(controller: AbortController): AbortController {
    this.abortControllers.add(controller);
    return controller;
  }

  snapshot() {
    return {
      disposerCount: this.disposers.size,
      objectUrlCount: this.objectUrls.size,
      intervalCount: this.intervals.size,
      timeoutCount: this.timeouts.size,
      workerCount: this.workers.size,
      socketCount: this.sockets.size,
      eventSourceCount: this.eventSources.size,
      abortControllerCount: this.abortControllers.size,
    };
  }

  dispose(): void {
    for (const disposer of Array.from(this.disposers)) {
      try {
        disposer();
      } catch {
        // Ignore teardown failures during boundary disposal.
      }
    }
    this.disposers.clear();

    for (const controller of Array.from(this.abortControllers)) {
      controller.abort();
    }
    this.abortControllers.clear();

    for (const interval of Array.from(this.intervals)) {
      window.clearInterval(interval);
    }
    this.intervals.clear();

    for (const timeout of Array.from(this.timeouts)) {
      window.clearTimeout(timeout);
    }
    this.timeouts.clear();

    for (const source of Array.from(this.eventSources)) {
      source.close();
    }
    this.eventSources.clear();

    for (const socket of Array.from(this.sockets)) {
      try {
        socket.close();
      } catch {
        // Ignore socket close errors during boundary disposal.
      }
    }
    this.sockets.clear();

    for (const worker of Array.from(this.workers)) {
      worker.terminate();
    }
    this.workers.clear();

    for (const url of Array.from(this.objectUrls)) {
      URL.revokeObjectURL(url);
    }
    this.objectUrls.clear();
  }
}

class WorkspacePersistenceNamespace {
  private readonly indexKey: string;

  constructor(
    private readonly prefix: string,
    private readonly isAllowedKey: (key: string) => boolean,
    private readonly legacyPrefixes: string[] = [],
  ) {
    this.indexKey = `${prefix}:persist-index`;
    this.purgeLegacyPrefixes();
    this.purgeDisallowedEntries();
  }

  keyFor(key: string): string {
    return normalizeStorageKey(this.prefix, key);
  }

  private canUseStorage(): boolean {
    return typeof window !== 'undefined' && typeof window.localStorage !== 'undefined';
  }

  private readIndex(): string[] {
    if (!this.canUseStorage()) {
      return [];
    }

    const rawValue = window.localStorage.getItem(this.indexKey);
    if (!rawValue) {
      return [];
    }

    try {
      const parsed = JSON.parse(rawValue);
      return Array.isArray(parsed) ? parsed.filter((entry): entry is string => typeof entry === 'string') : [];
    } catch {
      window.localStorage.removeItem(this.indexKey);
      return [];
    }
  }

  private writeIndex(entries: string[]): void {
    if (!this.canUseStorage()) {
      return;
    }

    window.localStorage.setItem(this.indexKey, JSON.stringify(Array.from(new Set(entries))));
  }

  private storageKeyToLogicalKey(storageKey: string): string | null {
    const prefix = `${this.prefix}:persist:`;
    if (!storageKey.startsWith(prefix)) {
      return null;
    }
    return storageKey.slice(prefix.length);
  }

  private dropStorageKey(storageKey: string): void {
    if (!this.canUseStorage()) {
      return;
    }

    window.localStorage.removeItem(storageKey);
    this.writeIndex(this.readIndex().filter((entry) => entry !== storageKey));
  }

  private dropKey(key: string): void {
    this.dropStorageKey(this.keyFor(key));
  }

  private purgeLegacyPrefixes(): void {
    if (!this.canUseStorage()) {
      return;
    }

    if (this.legacyPrefixes.length === 0) {
      return;
    }

    const keys: string[] = [];
    for (let index = 0; index < window.localStorage.length; index += 1) {
      const key = window.localStorage.key(index);
      if (key) {
        keys.push(key);
      }
    }

    for (const key of keys) {
      if (this.legacyPrefixes.some((prefix) => key.startsWith(prefix))) {
        window.localStorage.removeItem(key);
      }
    }
  }

  private purgeDisallowedEntries(): void {
    if (!this.canUseStorage()) {
      return;
    }

    for (const storageKey of this.readIndex()) {
      const logicalKey = this.storageKeyToLogicalKey(storageKey);
      if (!logicalKey || !this.isAllowedKey(logicalKey)) {
        this.dropStorageKey(storageKey);
      }
    }
  }

  getJson<T>(key: string): T | null {
    if (!this.canUseStorage()) {
      return null;
    }

    if (!this.isAllowedKey(key)) {
      this.dropKey(key);
      return null;
    }

    const storageKey = this.keyFor(key);
    const rawValue = window.localStorage.getItem(storageKey);
    if (!rawValue) {
      return null;
    }

    try {
      return JSON.parse(rawValue) as T;
    } catch {
      window.localStorage.removeItem(storageKey);
      return null;
    }
  }

  setJson(key: string, value: unknown): void {
    if (!this.canUseStorage()) {
      return;
    }

    if (!this.isAllowedKey(key)) {
      this.dropKey(key);
      return;
    }

    const storageKey = this.keyFor(key);
    window.localStorage.setItem(storageKey, JSON.stringify(value));
    this.writeIndex([...this.readIndex(), storageKey]);
  }

  remove(key: string): void {
    if (!this.canUseStorage()) {
      return;
    }

    this.dropKey(key);
  }

  clear(): void {
    if (!this.canUseStorage()) {
      return;
    }

    for (const storageKey of this.readIndex()) {
      window.localStorage.removeItem(storageKey);
    }
    window.localStorage.removeItem(this.indexKey);
  }

  snapshot() {
    const entries = this.readIndex();
    return {
      prefix: this.prefix,
      trackedKeyCount: entries.length,
      disallowedKeyCount: entries.reduce((count, storageKey) => {
        const logicalKey = this.storageKeyToLogicalKey(storageKey);
        return count + (!logicalKey || !this.isAllowedKey(logicalKey) ? 1 : 0);
      }, 0),
    };
  }
}

class WorkspaceTransportAdapter {
  private readonly inFlightControllers = new Map<string, AbortController>();
  private authRedirectInFlight = false;

  private static readonly RETRYABLE_STATUSES = new Set([408, 425, 429, 500, 502, 503, 504]);

  constructor(
    private readonly apiBaseUrl: string,
    private readonly workspaceId: string,
    private readonly disposableRegistry: WorkspaceDisposableRegistry,
  ) {}

  private resolveTimeout(method: string, requestedTimeoutMs?: number): number {
    const fallback = ['GET', 'HEAD'].includes(method) ? 10_000 : 15_000;
    const timeoutMs = requestedTimeoutMs ?? fallback;
    return Math.max(1_000, Math.min(timeoutMs, 60_000));
  }

  private resolveRetryCount(method: string, requestedRetryCount?: number): number {
    if (typeof requestedRetryCount === 'number') {
      return Math.max(0, Math.min(requestedRetryCount, 3));
    }
    return ['GET', 'HEAD'].includes(method) ? 1 : 0;
  }

  private async delay(ms: number): Promise<void> {
    await new Promise<void>((resolve) => {
      this.disposableRegistry.trackTimeout(window.setTimeout(resolve, ms));
    });
  }

  private async refreshBrowserSession(): Promise<boolean> {
    try {
      const response = await fetch(`${this.apiBaseUrl}/api/auth/refresh`, {
        method: 'POST',
        credentials: 'include',
        headers: buildCookieAuthHeaders('POST', {
          accept: 'application/json',
          'content-type': 'application/json',
        }),
        body: JSON.stringify({ channel: 'web' }),
      });
      return response.ok;
    } catch {
      return false;
    }
  }

  private redirectToLogin(): void {
    if (typeof window === 'undefined' || this.authRedirectInFlight) {
      return;
    }
    const currentPath = `${window.location.pathname}${window.location.search}${window.location.hash}`;
    if (currentPath.startsWith('/login')) {
      return;
    }
    this.authRedirectInFlight = true;
    const next = encodeURIComponent(currentPath || '/');
    window.location.assign(`/login?next=${next}`);
  }

  private async performRequest(
    url: string,
    init: RequestInit,
    timeoutMs: number,
  ): Promise<Response> {
    const controller = this.disposableRegistry.trackAbortController(new AbortController());
    const requestId = `${Date.now()}:${Math.random().toString(16).slice(2)}`;
    this.inFlightControllers.set(requestId, controller);
    const headers = buildCookieAuthHeaders(init.method ?? 'GET', init.headers ?? {});
    headers.set('x-empyralis-workspace-id', this.workspaceId);
    let timeoutTriggered = false;
    const timeoutHandle = this.disposableRegistry.trackTimeout(window.setTimeout(() => {
      timeoutTriggered = true;
      controller.abort();
    }, timeoutMs));

    const upstreamAbort = init.signal;
    const abortFromUpstream = () => {
      controller.abort();
    };
    if (upstreamAbort) {
      if (upstreamAbort.aborted) {
        controller.abort();
      } else {
        upstreamAbort.addEventListener('abort', abortFromUpstream, { once: true });
      }
    }

    try {
      return await fetch(url, {
        ...init,
        headers,
        credentials: 'include',
        signal: controller.signal,
      });
    } catch (error) {
      if (timeoutTriggered) {
        throw new Error(`Workstation request timed out after ${timeoutMs}ms.`);
      }
      throw error;
    } finally {
      window.clearTimeout(timeoutHandle);
      if (upstreamAbort) {
        upstreamAbort.removeEventListener('abort', abortFromUpstream);
      }
      this.inFlightControllers.delete(requestId);
    }
  }

  async request(
    path: string,
    init: RequestInit = {},
    policy: {
      timeoutMs?: number;
      retryCount?: number;
      retryOnStatuses?: number[];
      refreshSessionOn401?: boolean;
    } = {},
  ): Promise<Response> {
    const method = String(init.method ?? 'GET').trim().toUpperCase();
    const url = /^https?:\/\//.test(path) ? path : `${this.apiBaseUrl}${path.startsWith('/') ? path : `/${path}`}`;
    const timeoutMs = this.resolveTimeout(method, policy.timeoutMs);
    const retryCount = this.resolveRetryCount(method, policy.retryCount);
    const retryOnStatuses = new Set(policy.retryOnStatuses ?? Array.from(WorkspaceTransportAdapter.RETRYABLE_STATUSES));
    const refreshSessionOn401 = policy.refreshSessionOn401 ?? true;
    let refreshed = false;
    let attempt = 0;

    while (true) {
      try {
        const response = await this.performRequest(url, init, timeoutMs);
        if (response.status === 401 && refreshSessionOn401 && !refreshed) {
          refreshed = await this.refreshBrowserSession();
          if (refreshed) {
            continue;
          }
          this.redirectToLogin();
        }

        if (attempt < retryCount && retryOnStatuses.has(response.status)) {
          attempt += 1;
          await this.delay(250 * attempt);
          continue;
        }

        return response;
      } catch (error) {
        if (init.signal?.aborted) {
          throw error;
        }
        if (attempt >= retryCount) {
          throw error;
        }
        attempt += 1;
        await this.delay(250 * attempt);
      }
    }
  }

  async requestJson<T>(path: string, init: RequestInit = {}): Promise<T> {
    const response = await this.request(path, init);
    if (!response.ok) {
      throw new Error(`Workspace transport request failed with status ${response.status}.`);
    }
    try {
      return (await response.json()) as T;
    } catch (error) {
      if (init.signal?.aborted || (error instanceof Error && /aborted/i.test(error.message))) {
        throw error;
      }
      throw error;
    }
  }

  snapshot() {
    return {
      apiBaseUrl: this.apiBaseUrl,
      inFlightRequestCount: this.inFlightControllers.size,
    };
  }

  dispose(): void {
    for (const controller of this.inFlightControllers.values()) {
      controller.abort();
    }
    this.inFlightControllers.clear();
  }
}

class WorkspaceQueryClient {
  private readonly cache = new Map<string, unknown>();
  private readonly cacheMeta = new Map<string, { updatedAt: number }>();
  private readonly inFlight = new Map<string, {
    controller: AbortController;
    promise: Promise<unknown>;
  }>();

  constructor(
    private readonly prefix: string,
    private readonly disposableRegistry: WorkspaceDisposableRegistry,
  ) {}

  peek<T>(key: string): T | null {
    const scopedKey = normalizeCacheKey(this.prefix, key);
    return (this.cache.get(scopedKey) as T | undefined) ?? null;
  }

  set<T>(key: string, value: T): T {
    const scopedKey = normalizeCacheKey(this.prefix, key);
    this.cache.set(scopedKey, value);
    this.cacheMeta.set(scopedKey, { updatedAt: Date.now() });
    return value;
  }

  async run<T>(key: string, executor: QueryExecutor<T>): Promise<T> {
    const scopedKey = normalizeCacheKey(this.prefix, key);
    const existing = this.inFlight.get(scopedKey);
    if (existing) {
      return existing.promise as Promise<T>;
    }
    const controller = this.disposableRegistry.trackAbortController(new AbortController());
    const promise = executor({ signal: controller.signal, cacheKey: scopedKey })
      .then((value) => {
        this.cache.set(scopedKey, value);
        this.cacheMeta.set(scopedKey, { updatedAt: Date.now() });
        return value;
      })
      .finally(() => {
        this.inFlight.delete(scopedKey);
      });
    this.inFlight.set(scopedKey, { controller, promise });

    return promise;
  }

  async readThrough<T>(
    key: string,
    executor: QueryExecutor<T>,
    ttlMs = 10_000,
  ): Promise<T> {
    const scopedKey = normalizeCacheKey(this.prefix, key);
    const cached = this.cache.get(scopedKey) as T | undefined;
    const meta = this.cacheMeta.get(scopedKey);
    if (cached !== undefined && meta && Date.now() - meta.updatedAt <= Math.max(ttlMs, 0)) {
      return cached;
    }
    return this.run(key, executor);
  }

  invalidate(key?: string): void {
    if (!key) {
      this.cache.clear();
      this.cacheMeta.clear();
      return;
    }

    const scopedKey = normalizeCacheKey(this.prefix, key);
    this.cache.delete(scopedKey);
    this.cacheMeta.delete(scopedKey);
  }

  snapshot() {
    return {
      cacheEntryCount: this.cache.size,
      inFlightCount: this.inFlight.size,
    };
  }

  dispose(): void {
    for (const pending of this.inFlight.values()) {
      pending.controller.abort();
    }
    this.inFlight.clear();
    this.cache.clear();
    this.cacheMeta.clear();
  }
}

class WorkspaceRealtimeAdapter {
  private readonly pollingHandles = new Set<number>();

  constructor(private readonly disposableRegistry: WorkspaceDisposableRegistry) {}

  registerPoller(callback: () => void, intervalMs: number): DisposeFn {
    const handle = this.disposableRegistry.trackInterval(window.setInterval(callback, intervalMs));
    this.pollingHandles.add(handle);
    return () => {
      window.clearInterval(handle);
      this.pollingHandles.delete(handle);
    };
  }

  trackSocket<T extends WebSocket>(socket: T): T {
    return this.disposableRegistry.trackSocket(socket);
  }

  trackEventSource<T extends EventSource>(source: T): T {
    return this.disposableRegistry.trackEventSource(source);
  }

  snapshot() {
    return {
      pollerCount: this.pollingHandles.size,
    };
  }

  dispose(): void {
    for (const handle of Array.from(this.pollingHandles)) {
      window.clearInterval(handle);
    }
    this.pollingHandles.clear();
  }
}

type WorkspaceStore<T> = {
  getState: () => T;
  setState: (nextState: T) => void;
  subscribe: (listener: StoreListener<T>) => DisposeFn;
};

class WorkspaceStoreFactory {
  private readonly stores = new Map<string, WorkspaceStore<unknown>>();
  private readonly storeListeners = new Map<string, Set<StoreListener<unknown>>>();

  createStore<T>(name: string, initialState: T): WorkspaceStore<T> {
    const existing = this.stores.get(name) as WorkspaceStore<T> | undefined;
    if (existing) {
      return existing;
    }

    let currentState = initialState;
    const listeners = new Set<StoreListener<unknown>>();
    this.storeListeners.set(name, listeners);

    const store: WorkspaceStore<T> = {
      getState: () => currentState,
      setState: (nextState) => {
        currentState = nextState;
        for (const listener of listeners) {
          (listener as StoreListener<T>)(currentState);
        }
      },
      subscribe: (listener) => {
        listeners.add(listener as StoreListener<unknown>);
        return () => {
          listeners.delete(listener as StoreListener<unknown>);
        };
      },
    };

    this.stores.set(name, store as WorkspaceStore<unknown>);
    return store;
  }

  snapshot() {
    return {
      storeCount: this.stores.size,
      listenerCount: Array.from(this.storeListeners.values()).reduce(
        (total, listeners) => total + listeners.size,
        0,
      ),
    };
  }

  dispose(): void {
    this.storeListeners.clear();
    this.stores.clear();
  }
}

export type WorkspaceServices = {
  kernelKey: string;
  scopeKey: string;
  scope: WorkstationKernelScope;
  client: WorkstationClient;
  streams: WorkstationStreamManager;
  storagePolicy: {
    preferencePersistenceOnly: true;
  };
  queryClient: WorkspaceQueryClient;
  transport: WorkspaceTransportAdapter;
  realtime: WorkspaceRealtimeAdapter;
  persistence: WorkspacePersistenceNamespace;
  disposables: WorkspaceDisposableRegistry;
  stores: WorkspaceStoreFactory;
  snapshot: () => {
    kernelKey: string;
    scopeKey: string;
    scope: WorkstationKernelScope;
    client: ReturnType<WorkstationClient['snapshot']>;
    streams: ReturnType<WorkstationStreamManager['snapshot']>;
    storagePolicy: {
      preferencePersistenceOnly: true;
    };
    persistence: ReturnType<WorkspacePersistenceNamespace['snapshot']>;
    queryClient: ReturnType<WorkspaceQueryClient['snapshot']>;
    transport: ReturnType<WorkspaceTransportAdapter['snapshot']>;
    realtime: ReturnType<WorkspaceRealtimeAdapter['snapshot']>;
    disposables: ReturnType<WorkspaceDisposableRegistry['snapshot']>;
    stores: ReturnType<WorkspaceStoreFactory['snapshot']>;
  };
  dispose: () => void;
};

const WorkspaceServicesContext = createContext<WorkspaceServices | null>(null);

function createWorkstationKernel(
  kernelKey: string,
  shellProfileId: string,
  bootstrap: WorkspaceBootstrapPayload,
): WorkspaceServices {
  const scope: WorkstationKernelScope = {
    accountId: bootstrap.account.id,
    tenantId: bootstrap.workspace.tenantId,
    workspaceId: bootstrap.workspace.id,
    membershipVersion: bootstrap.membership.version,
    shellProfileId,
    kernelKey,
  };
  const scopeKey = kernelKey;
  const persistencePrefix = `empyralis.workspace.ui.v3:${scope.accountId}:${scope.tenantId}:${scope.workspaceId}`;
  const disposables = new WorkspaceDisposableRegistry();
  const persistence = new WorkspacePersistenceNamespace(
    persistencePrefix,
    isWorkstationPreferencePersistenceKey,
    [
      `empyralis.workspace.v2:${scope.accountId}:${scope.workspaceId}:persist:`,
      `empyralis.workspace.v2:${scope.accountId}:${scope.workspaceId}:persist-index`,
    ],
  );
  const transport = new WorkspaceTransportAdapter(
    resolveApiBaseUrl(),
    bootstrap.workspace.id,
    disposables,
  );
  const queryClient = new WorkspaceQueryClient(
    kernelKey,
    disposables,
  );
  const realtime = new WorkspaceRealtimeAdapter(disposables);
  const client = createWorkstationClient({
    scope: {
      workspaceId: scope.workspaceId,
      tenantId: scope.tenantId,
      kernelKey: scope.kernelKey,
    },
    transport,
    queryClient,
    realtime,
    getApiBaseUrl: () => transport.snapshot().apiBaseUrl,
  });
  const streams = createWorkstationStreamManager({
    client,
  });
  const stores = new WorkspaceStoreFactory();

  return {
    kernelKey,
    scopeKey,
    scope,
    client,
    streams,
    storagePolicy: {
      preferencePersistenceOnly: true,
    },
    queryClient,
    transport,
    realtime,
    persistence,
    disposables,
    stores,
    snapshot: () => ({
      kernelKey,
      scopeKey,
      scope,
      client: client.snapshot(),
      streams: streams.snapshot(),
      storagePolicy: {
        preferencePersistenceOnly: true,
      },
      persistence: persistence.snapshot(),
      queryClient: queryClient.snapshot(),
      transport: transport.snapshot(),
      realtime: realtime.snapshot(),
      disposables: disposables.snapshot(),
      stores: stores.snapshot(),
    }),
    dispose: () => {
      streams.dispose();
      queryClient.dispose();
      transport.dispose();
      realtime.dispose();
      stores.dispose();
      disposables.dispose();
    },
  };
}

export function WorkstationKernelProvider({
  kernelKey,
  shellProfileId,
  bootstrap,
  children,
}: PropsWithChildren<{
  kernelKey: string;
  shellProfileId: string;
  bootstrap: WorkspaceBootstrapPayload;
}>) {
  const services = useMemo(
    () => createWorkstationKernel(kernelKey, shellProfileId, bootstrap),
    [bootstrap, kernelKey, shellProfileId],
  );

  useEffect(() => {
    const liveStreamsEnabled = String(process.env.NEXT_PUBLIC_WORKSTATION_LIVE_STREAMS ?? '')
      .trim()
      .toLowerCase();
    if (liveStreamsEnabled === '1' || liveStreamsEnabled === 'true') {
      services.streams.start();
    }
    return () => {
      services.dispose();
    };
  }, [services]);

  return (
    <WorkspaceServicesContext.Provider value={services}>
      {children}
    </WorkspaceServicesContext.Provider>
  );
}

export function WorkspaceServicesProvider({
  boundaryKey,
  bootstrap,
  children,
}: PropsWithChildren<{
  boundaryKey: string;
  bootstrap: WorkspaceBootstrapPayload;
}>) {
  return (
    <WorkstationKernelProvider
      kernelKey={boundaryKey}
      shellProfileId={bootstrap.shellHints.preferredProfile || 'unknown'}
      bootstrap={bootstrap}
    >
      {children}
    </WorkstationKernelProvider>
  );
}

export function useWorkstationKernel(): WorkspaceServices {
  const value = useContext(WorkspaceServicesContext);
  if (!value) {
    throw new Error('useWorkstationKernel must be used inside WorkstationKernelProvider.');
  }
  return value;
}

export function useWorkspaceServices(): WorkspaceServices {
  return useWorkstationKernel();
}

export function useWorkstationStreamState(): WorkstationStreamState {
  const services = useWorkstationKernel();
  return useSyncExternalStore(
    services.streams.subscribe.bind(services.streams),
    services.streams.getState.bind(services.streams),
    services.streams.getState.bind(services.streams),
  );
}

export function useWorkstationStreamSelector<T>(
  selector: (state: WorkstationStreamState) => T,
): T {
  const services = useWorkstationKernel();
  return useSyncExternalStore(
    services.streams.subscribe.bind(services.streams),
    () => selector(services.streams.getState()),
    () => selector(services.streams.getState()),
  );
}

export function useWorkstationActivityVersion(): number {
  return useWorkstationStreamSelector((state) => state.activity.version);
}
