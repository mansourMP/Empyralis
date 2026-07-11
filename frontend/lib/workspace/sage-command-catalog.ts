import {
  Activity,
  CircleAlert,
  Coins,
  Cpu,
  LayoutGrid,
  LifeBuoy,
  Puzzle,
  SquareActivity,
  Table,
} from 'lucide-react';
import type { LucideIcon } from 'lucide-react';

import type { WorkspaceRouteId } from '../../../shared/nav-manifest';

export type SageCommandActionKind =
  | 'open_status'
  | 'open_usage'
  | 'open_tools'
  | 'open_runtime'
  | 'run_doctor';

export type SageCommandMetadata = {
  id: string;
  slash: `/${string}`;
  title: string;
  description: string;
  actionKind: SageCommandActionKind;
  icon: LucideIcon;
  keywords?: string[];
};

export type SageWorkspaceCommandMetadata = {
  id: string;
  title: string;
  description: string;
  routeId: WorkspaceRouteId;
  icon: LucideIcon;
  keywords?: string[];
};

export const SAGE_COMMAND_CATALOG: readonly SageCommandMetadata[] = [
  {
    id: 'status',
    slash: '/status',
    title: 'Status',
    description: 'Show Sage health, readiness, and connectivity.',
    actionKind: 'open_status',
    icon: Activity,
    keywords: ['system', 'health', 'ready'],
  },
  {
    id: 'usage',
    slash: '/usage',
    title: 'Credits and usage',
    description: 'Open credits, estimated cost, and recent usage signals.',
    actionKind: 'open_usage',
    icon: Coins,
    keywords: ['quota', 'cost', 'billing', 'stats'],
  },
  {
    id: 'tools',
    slash: '/tools',
    title: 'Sage connectors',
    description: 'Open Sage channels, apps, skills, plugins, and AI account setup.',
    actionKind: 'open_tools',
    icon: Puzzle,
    keywords: ['connectors', 'connections', 'apps', 'channels', 'skills', 'plugins'],
  },
  {
    id: 'runtime',
    slash: '/runtime',
    title: 'AI setup',
    description: 'Check which AI path Sage will use and where to manage it.',
    actionKind: 'open_runtime',
    icon: Cpu,
    keywords: ['target', 'provider', 'agent computer', 'cloud', 'routing'],
  },
  {
    id: 'doctor',
    slash: '/doctor',
    title: 'Check setup',
    description: 'Run a Sage readiness and connectivity check.',
    actionKind: 'run_doctor',
    icon: LifeBuoy,
    keywords: ['health check', 'diagnostic', 'connectivity', 'connectors', 'gateway'],
  },
];

export const SAGE_WORKSPACE_COMMAND_CATALOG: readonly SageWorkspaceCommandMetadata[] = [
  {
    id: 'skills',
    title: 'Installed skills',
    description: 'Open connected skills and reusable AI procedures.',
    routeId: 'integrations',
    icon: Puzzle,
    keywords: ['extensions', 'skills', 'install', 'tool server', 'playbook'],
  },
  {
    id: 'tasks',
    title: 'Sage tasks',
    description: 'Open scheduled actions and pending automation tasks.',
    routeId: 'tasks',
    icon: SquareActivity,
    keywords: ['heartbeat', 'tasks', 'scheduler', 'next action', 'automation'],
  },
  {
    id: 'memory',
    title: 'Memory',
    description: 'Open memory surfaces for facts, corrections, and preferences.',
    routeId: 'memory',
    icon: Table,
    keywords: ['context', 'facts', 'profile', 'knowledge', 'preference'],
  },
  {
    id: 'integrations',
    title: 'Connectors',
    description: 'Open Sage channels, apps, skills, plugins, and AI accounts.',
    routeId: 'integrations',
    icon: LayoutGrid,
    keywords: ['connect', 'apps', 'providers', 'channels', 'services'],
  },
];

export function normalizeSageCommandSlash(value: string): string {
  return value.trim().toLowerCase();
}

export function isSageCommandSlash(value: string): value is `/${string}` {
  return normalizeSageCommandSlash(value).startsWith('/');
}

export function resolveSageCommandBySlash(slash: string): SageCommandMetadata | null {
  const normalized = normalizeSageCommandSlash(slash);
  if (!isSageCommandSlash(normalized)) {
    return null;
  }
  return SAGE_COMMAND_CATALOG.find((command) => command.slash === normalized) ?? null;
}

export function resolveSageCommandById(id: string): SageCommandMetadata | null {
  return SAGE_COMMAND_CATALOG.find((command) => command.id === id.trim().toLowerCase()) ?? null;
}
