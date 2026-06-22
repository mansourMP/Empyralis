'use client';

import { useEffect, useMemo, useState, type ReactNode } from 'react';
import { BookOpen, Clock, Code2, Download, Eye, FileText, Pencil, Pin, Plus, Shield } from 'lucide-react';
import type { LucideIcon } from 'lucide-react';

import { CommandSheet } from '@/lib/ui/command-sheet';
import { FormField, FormGrid, FormInput, FormSection, FormTextarea } from '@/lib/ui/form-controls';
import { DataPaneError } from '@/lib/workspace/data-pane-error';
import { PlatformNotification } from '@/lib/ui/platform-notification';
import { AppButton, AppTextarea } from '@/lib/ui/primitives';
import { SkeletonBlock } from '@/lib/ui/skeleton-block';
import type {
  WorkstationSageContextFileRecord,
  WorkstationSageMemoryRecord,
  WorkstationSageProfileQuestionRecord,
  WorkstationSageProfileRecord,
} from '@/lib/workspace/workstation-client';
import { useWorkspaceBoundary } from '@/lib/workspace/workspace-boundary';
import { useWorkspaceServices, useWorkstationStreamState } from '@/lib/workspace/workspace-services';
import {
  WorkstationSurfaceRoot,
} from '@/lib/workspace/workstation-surface-primitives';
import { WorkstationSplitWorkbench } from '@/lib/workspace/workstation-split-workbench';

type SageMemoryCategoryRecord = {
  id: string;
  label: string;
};

type SageMemorySnapshot = {
  items: WorkstationSageMemoryRecord[];
  categories: SageMemoryCategoryRecord[];
};

type SageProfileSnapshot = {
  profile: {
    user_name: string;
    identity_summary: string;
    communication_style: string;
    recurring_responsibility: string;
    standing_rules: string[];
    standing_rules_text: string;
  };
  bootstrap: {
    complete: boolean;
    answered_count: number;
    total_count: number;
    progress_label: string;
    current_question: WorkstationSageProfileQuestionRecord | null;
  };
  storagePolicy: Record<string, unknown>;
  projections: Record<string, string>;
  accountSeed: {
    display_name: string;
    email: string;
  };
};

type SageProfileDraft = {
  user_name: string;
  identity_summary: string;
  communication_style: string;
  recurring_responsibility: string;
  standing_rules_text: string;
};

type MemoryContextDocuments = Record<string, string>;

type SageMemoryStoragePolicy = Record<string, unknown> & {
  authority?: string | null;
  max_entries?: number | null;
  used_entries?: number | null;
  remaining_entries?: number | null;
};

type SageMemoryDraft = {
  entryId: string | null;
  category: string;
  title: string;
  content: string;
  pinned: boolean;
};

type MemorySensitivityClass = 'green' | 'yellow' | 'orange' | 'red';
type MemoryDocumentViewMode = 'preview' | 'source';
type MemoryDocumentNavItem = {
  id: string;
  label: string;
  description: string;
  icon: LucideIcon;
};

const memoryPaneCache = new Map<string, SageMemorySnapshot>();

const DEFAULT_MEMORY_CATEGORIES: readonly SageMemoryCategoryRecord[] = [
  { id: 'safe_general', label: 'Safe' },
  { id: 'sensitive', label: 'Sensitive' },
  { id: 'private', label: 'Private' },
  { id: 'critical_restricted', label: 'Critical' },
] as const;

const MEMORY_SENSITIVITY_ORDER: readonly MemorySensitivityClass[] = ['green', 'yellow', 'orange', 'red'] as const;

const MEMORY_SENSITIVITY_META: Record<MemorySensitivityClass, { label: string; description: string }> = {
  green: {
    label: 'Safe',
    description: 'General context Sage can reuse freely.',
  },
  yellow: {
    label: 'Sensitive',
    description: 'Useful context Sage should handle carefully.',
  },
  orange: {
    label: 'Private',
    description: 'Personal context with tighter handling.',
  },
  red: {
    label: 'Critical',
    description: 'Restricted facts such as secrets or credentials.',
  },
};

const MEMORY_ITEM_LIMIT = 50;
const MEMORY_FILE_LIMIT = 20;

function formatMemoryDocumentLabel(filename: string): string {
  const parts = filename.split('/');
  return parts[parts.length - 1] || filename;
}

function formatMemoryDocumentDescription(filename: string): string {
  if (filename.startsWith('memory/files/')) {
    return 'Memory file';
  }
  if (filename.startsWith('memory/')) {
    return 'Memory note';
  }
  return 'Markdown projection';
}

function normalizeUserMemoryFilename(input: string): string {
  const baseName = input
    .trim()
    .replace(/\.md$/i, '')
    .replace(/[^A-Za-z0-9._-]+/g, '_')
    .replace(/_+/g, '_')
    .replace(/^[._-]+|[._-]+$/g, '')
    .slice(0, 48);
  if (!baseName) {
    return '';
  }
  return `memory/files/${baseName.toUpperCase()}.md`;
}

function titleFromMemoryFilename(filename: string): string {
  return formatMemoryDocumentLabel(filename)
    .replace(/\.md$/i, '')
    .replace(/[_-]+/g, ' ')
    .replace(/\s+/g, ' ')
    .trim();
}

function defaultUserMemoryFileContent(filename: string): string {
  const title = titleFromMemoryFilename(filename) || 'Memory file';
  return `# ${title}\n\n- Add durable memory here.\n`;
}

function readString(value: unknown): string {
  return typeof value === 'string' && value.trim() ? value.trim() : '';
}

function readNumber(value: unknown, fallback = 0): number {
  const parsed = typeof value === 'number' ? value : Number(value);
  return Number.isFinite(parsed) ? parsed : fallback;
}

function normalizeMemorySnapshot(payload: unknown): SageMemorySnapshot {
  const record = payload && typeof payload === 'object' ? payload as Record<string, unknown> : {};
  const items = Array.isArray(record.items)
    ? record.items.filter((item): item is WorkstationSageMemoryRecord => Boolean(item) && typeof item === 'object')
    : [];
  const parsedCategories = Array.isArray(record.categories)
    ? record.categories.flatMap((item) => {
      if (!item || typeof item !== 'object') {
        return [];
      }
      const category = item as Record<string, unknown>;
      const id = readString(category.id);
      const label = readString(category.label);
      if (!id || !label) {
        return [];
      }
      return [{ id, label }];
    })
    : [];
  return {
    items,
    categories: parsedCategories.length > 0 ? parsedCategories : [...DEFAULT_MEMORY_CATEGORIES],
  };
}

function defaultProfileSnapshot(): SageProfileSnapshot {
  return {
    profile: {
      user_name: '',
      identity_summary: '',
      communication_style: '',
      recurring_responsibility: '',
      standing_rules: [],
      standing_rules_text: '',
    },
    bootstrap: {
      complete: false,
      answered_count: 0,
      total_count: 5,
      progress_label: '0/5',
      current_question: null,
    },
    storagePolicy: {},
    projections: {},
    accountSeed: {
      display_name: '',
      email: '',
    },
  };
}

function normalizeProfileSnapshot(payload: unknown): SageProfileSnapshot {
  const record = payload && typeof payload === 'object' ? payload as WorkstationSageProfileRecord : {};
  const profileRecord = record.profile && typeof record.profile === 'object'
    ? record.profile as Record<string, unknown>
    : {};
  const bootstrapRecord = record.bootstrap && typeof record.bootstrap === 'object'
    ? record.bootstrap as Record<string, unknown>
    : {};
  const standingRules = Array.isArray(profileRecord.standing_rules)
    ? profileRecord.standing_rules.flatMap((item) => {
      const rule = readString(item);
      return rule ? [rule] : [];
    })
    : [];
  const projectionsRecord = record.projections && typeof record.projections === 'object'
    ? record.projections as Record<string, unknown>
    : {};
  const accountSeedRecord = record.account_seed && typeof record.account_seed === 'object'
    ? record.account_seed as Record<string, unknown>
    : {};
  const answeredCount = readNumber(bootstrapRecord.answered_count);
  const totalCount = Math.max(1, readNumber(bootstrapRecord.total_count, 5));
  return {
    profile: {
      user_name: readString(profileRecord.user_name),
      identity_summary: readString(profileRecord.identity_summary),
      communication_style: readString(profileRecord.communication_style),
      recurring_responsibility: readString(profileRecord.recurring_responsibility),
      standing_rules: standingRules,
      standing_rules_text: readString(profileRecord.standing_rules_text) || standingRules.join('\n'),
    },
    bootstrap: {
      complete: Boolean(bootstrapRecord.complete),
      answered_count: answeredCount,
      total_count: totalCount,
      progress_label: readString(bootstrapRecord.progress_label) || `${answeredCount}/${totalCount}`,
      current_question: bootstrapRecord.current_question && typeof bootstrapRecord.current_question === 'object'
        ? bootstrapRecord.current_question as WorkstationSageProfileQuestionRecord
        : null,
    },
    storagePolicy: record.storage_policy && typeof record.storage_policy === 'object'
      ? record.storage_policy as Record<string, unknown>
      : {},
    projections: Object.entries(projectionsRecord).reduce<Record<string, string>>((accumulator, [key, value]) => {
      const normalized = readString(value);
      if (normalized) {
        accumulator[key] = normalized;
      }
      return accumulator;
    }, {}),
    accountSeed: {
      display_name: readString(accountSeedRecord.display_name),
      email: readString(accountSeedRecord.email),
    },
  };
}

function normalizeContextDocuments(payload: unknown): MemoryContextDocuments {
  const record = payload && typeof payload === 'object' ? payload as Record<string, unknown> : {};
  const files = Array.isArray(record.files) ? record.files : [];
  return files.reduce<MemoryContextDocuments>((accumulator, item) => {
    if (!item || typeof item !== 'object') {
      return accumulator;
    }
    const file = item as Partial<WorkstationSageContextFileRecord>;
    const filename = typeof file.filename === 'string' ? file.filename.trim() : '';
    if (!filename.endsWith('.md')) {
      return accumulator;
    }
    accumulator[filename] = typeof file.content === 'string' ? file.content : '';
    return accumulator;
  }, {});
}

function draftFromProfileSnapshot(snapshot: SageProfileSnapshot): SageProfileDraft {
  return {
    user_name: snapshot.profile.user_name,
    identity_summary: snapshot.profile.identity_summary,
    communication_style: snapshot.profile.communication_style,
    recurring_responsibility: snapshot.profile.recurring_responsibility,
    standing_rules_text: snapshot.profile.standing_rules_text,
  };
}

function defaultMemoryDraft(category = 'safe_general'): SageMemoryDraft {
  return {
    entryId: null,
    category,
    title: '',
    content: '',
    pinned: false,
  };
}

function sortMemoryEntries(items: WorkstationSageMemoryRecord[]): WorkstationSageMemoryRecord[] {
  return [...items].sort((left, right) => {
    const leftPinned = Boolean(left.pinned);
    const rightPinned = Boolean(right.pinned);
    if (leftPinned !== rightPinned) {
      return rightPinned ? 1 : -1;
    }
    const leftUpdated = Date.parse(readString(left.updated_at) || readString(left.created_at) || '');
    const rightUpdated = Date.parse(readString(right.updated_at) || readString(right.created_at) || '');
    if (Number.isFinite(leftUpdated) && Number.isFinite(rightUpdated) && leftUpdated !== rightUpdated) {
      return rightUpdated - leftUpdated;
    }
    return readString(left.id).localeCompare(readString(right.id));
  });
}

function formatMemoryTimestamp(value: string | null): string | null {
  if (!value) {
    return null;
  }
  const parsed = Date.parse(value);
  if (!Number.isFinite(parsed)) {
    return null;
  }
  const diffMinutes = Math.round((parsed - Date.now()) / 60000);
  const formatter = new Intl.RelativeTimeFormat(undefined, { numeric: 'auto' });
  if (Math.abs(diffMinutes) < 60) {
    return formatter.format(diffMinutes, 'minute');
  }
  const diffHours = Math.round(diffMinutes / 60);
  if (Math.abs(diffHours) < 24) {
    return formatter.format(diffHours, 'hour');
  }
  const diffDays = Math.round(diffHours / 24);
  if (Math.abs(diffDays) < 7) {
    return formatter.format(diffDays, 'day');
  }
  return new Date(value).toLocaleDateString([], {
    month: 'short',
    day: 'numeric',
  });
}

function inferMemorySensitivity(entry: WorkstationSageMemoryRecord): MemorySensitivityClass {
  const metadata = entry.metadata && typeof entry.metadata === 'object'
    ? entry.metadata as Record<string, unknown>
    : {};
  const explicit = readString(
    entry.sensitivity_class
    ?? entry.classification
    ?? metadata.sensitivity_class
    ?? metadata.sensitivity,
  ).toLowerCase();
  if (explicit === 'green' || explicit === 'yellow' || explicit === 'orange' || explicit === 'red') {
    return explicit;
  }
  const content = `${readString(entry.title)} ${readString(entry.content)}`.toLowerCase();
  if (/(api key|token|password|secret|credential|ssh|private key|recovery code)/i.test(content)) {
    return 'red';
  }
  const category = readString(entry.category).toLowerCase();
  if (category === 'safe_general' || category === 'green') {
    return 'green';
  }
  if (category === 'sensitive' || category === 'yellow') {
    return 'yellow';
  }
  if (category === 'private' || category === 'orange') {
    return 'orange';
  }
  if (category === 'critical_restricted' || category === 'critical' || category === 'red') {
    return 'red';
  }
  if (category === 'work_context' || category === 'profile_fact' || category === 'saved_preference') {
    return 'green';
  }
  if (
    category === 'top_of_mind'
    || category === 'brief_history'
    || category === 'earlier_context'
    || category === 'long_term_background'
    || category === 'project_context'
  ) {
    return 'yellow';
  }
  if (category === 'personal_context' || category === 'app_state') {
    return 'orange';
  }
  return 'yellow';
}

function memoryPaneErrorMessage(error: unknown): string {
  const message = error instanceof Error ? error.message.trim() : '';
  if (!message) {
    return 'Memory is unavailable right now.';
  }
  if (message === 'Sage cannot run that request in this workspace right now.') {
    return 'Memory is unavailable in this workspace.';
  }
  if (message === 'The requested item could not be found.') {
    return 'Memory has not been set up for this workspace yet.';
  }
  return message;
}

function buildFallbackMemoryDocuments(
  profileSnapshot: SageProfileSnapshot,
  displayNameHint: string,
  memoryUsed: number,
  memoryLimit: number,
): Array<{ filename: string; content: string }> {
  const profile = profileSnapshot.profile;
  const standingRules = profile.standing_rules.length > 0
    ? profile.standing_rules.map((rule) => `- ${rule}`).join('\n')
    : '- No standing rules saved yet.';
  return [
    {
      filename: 'SOUL.md',
      content: `# SOUL\n\nRole and personality boundaries for the main agent.\n\nRecurring responsibility: ${profile.recurring_responsibility || 'Not set yet.'}\n\nCommunication style: ${profile.communication_style || 'Not learned yet.'}`,
    },
    {
      filename: 'IDENTITY.md',
      content: `# IDENTITY\n\n${profile.identity_summary || 'No durable identity summary saved yet.'}`,
    },
    {
      filename: 'USER.md',
      content: `# USER\n\nName: ${profile.user_name || displayNameHint}\n\nStable profile: ${profile.identity_summary || 'Not learned yet.'}`,
    },
    {
      filename: 'AGENTS.md',
      content: `# AGENTS\n\nOperating rules Sage should follow across sessions.\n\n${standingRules}`,
    },
    {
      filename: 'MEMORY.md',
      content: `# MEMORY\n\nCurated long-term memory.\n\nSaved memory entries: ${memoryUsed}/${memoryLimit}`,
    },
    {
      filename: 'SELF_MODEL.md',
      content: `# SELF_MODEL\n\nWorking model of how the user prefers to think, decide, and collaborate.\n\n${profile.communication_style || 'Not learned yet.'}`,
    },
    {
      filename: 'LIFE_STORY.md',
      content: '# LIFE_STORY\n\nNarrative identity and important background will appear here when explicitly saved.',
    },
    {
      filename: 'GOALS.md',
      content: `# GOALS\n\nFuture direction, projects, and intentions.\n\n${profile.recurring_responsibility || 'No durable goals saved yet.'}`,
    },
    {
      filename: 'PROCEDURES.md',
      content: `# PROCEDURES\n\nHow the user likes work done.\n\n${profile.communication_style || 'No procedures saved yet.'}`,
    },
    {
      filename: 'REFLECTION.md',
      content: '# REFLECTION\n\nLessons, mistakes, and behavior improvements will appear here when explicitly saved.',
    },
    {
      filename: 'HEARTBEAT.md',
      content: `# HEARTBEAT\n\nIdentity setup: ${profileSnapshot.bootstrap.complete ? 'Complete' : `In progress (${profileSnapshot.bootstrap.progress_label})`}\nMemory entries: ${memoryUsed}/${memoryLimit}`,
    },
  ];
}

function stripMarkdownTitle(markdown: string): string {
  return markdown
    .split('\n')
    .filter((line, index) => !(index === 0 && line.trim().startsWith('# ')))
    .join('\n')
    .trim();
}

function markdownSection(markdown: string, heading: string): string {
  const lines = markdown.split('\n');
  const headingLine = `## ${heading}`;
  const startIndex = lines.findIndex((line) => line.trim().toLowerCase() === headingLine.toLowerCase());
  if (startIndex < 0) {
    return stripMarkdownTitle(markdown);
  }
  const sectionLines: string[] = [];
  for (const line of lines.slice(startIndex + 1)) {
    if (line.trim().startsWith('## ')) {
      break;
    }
    sectionLines.push(line);
  }
  return sectionLines.join('\n').trim();
}

export function WorkstationActivityPane() {
  const { workspaceId } = useWorkspaceBoundary();
  const services = useWorkspaceServices();
  const streamState = useWorkstationStreamState();
  const cachedSnapshot = memoryPaneCache.get(workspaceId) ?? null;
  const [snapshot, setSnapshot] = useState<SageMemorySnapshot>(() => cachedSnapshot ?? normalizeMemorySnapshot(null));
  const [isLoading, setIsLoading] = useState(() => cachedSnapshot === null);
  const [error, setError] = useState<unknown>(null);
  const [statusMessage, setStatusMessage] = useState<string | null>(null);
  const [storagePolicy, setStoragePolicy] = useState<SageMemoryStoragePolicy | null>(null);
  const cachedProfilePayload = services.queryClient.peek<unknown>('chat:canonical:sage-profile');
  const cachedProfile = cachedProfilePayload ? normalizeProfileSnapshot(cachedProfilePayload) : null;
  const [profileSnapshot, setProfileSnapshot] = useState<SageProfileSnapshot>(() => cachedProfile ?? defaultProfileSnapshot());
  const [contextDocuments, setContextDocuments] = useState<MemoryContextDocuments>({});
  const [profileDraft, setProfileDraft] = useState<SageProfileDraft>(() => draftFromProfileSnapshot(cachedProfile ?? defaultProfileSnapshot()));
  const [isProfileLoading, setIsProfileLoading] = useState(() => cachedProfile === null);
  const [isProfileSaving, setIsProfileSaving] = useState(false);
  const [isIdentitySheetOpen, setIsIdentitySheetOpen] = useState(false);
  const [isMemorySheetOpen, setIsMemorySheetOpen] = useState(false);
  const [isMemoryFileSheetOpen, setIsMemoryFileSheetOpen] = useState(false);
  const [memoryFileNameDraft, setMemoryFileNameDraft] = useState('');
  const [memoryDraft, setMemoryDraft] = useState<SageMemoryDraft>(() => defaultMemoryDraft());
  const [mutatingMemory, setMutatingMemory] = useState<string | null>(null);
  const [selectedMemoryDocumentId, setSelectedMemoryDocumentId] = useState('overview');
  const [memoryDocumentViewMode, setMemoryDocumentViewMode] = useState<MemoryDocumentViewMode>('preview');
  const [editingDocumentId, setEditingDocumentId] = useState<string | null>(null);
  const [editingDocumentText, setEditingDocumentText] = useState('');

  const refresh = async (showLoading = false) => {
    if (showLoading) {
      setIsLoading(true);
    }
    setError(null);
    const [payload, policyPayload] = await Promise.all([
      services.client.listSageMemory(),
      services.client.getSageMemoryStoragePolicy().catch(() => null),
    ]);
    const nextSnapshot = normalizeMemorySnapshot(payload);
    const normalizedSnapshot = {
      ...nextSnapshot,
      items: sortMemoryEntries(nextSnapshot.items),
    };
    memoryPaneCache.set(workspaceId, normalizedSnapshot);
    setSnapshot(normalizedSnapshot);
    if (policyPayload && typeof policyPayload === 'object') {
      setStoragePolicy(policyPayload as SageMemoryStoragePolicy);
    }
    setIsLoading(false);
  };

  const refreshProfile = async (showLoading = false) => {
    if (showLoading) {
      setIsProfileLoading(true);
    }
    setError(null);
    const [payload, contextPayload] = await Promise.all([
      services.client.getSageProfile(),
      services.client.listSageContextFiles().catch(() => null),
    ]);
    const nextSnapshot = normalizeProfileSnapshot(payload);
    services.queryClient.set('chat:canonical:sage-profile', nextSnapshot);
    setProfileSnapshot(nextSnapshot);
    setProfileDraft(draftFromProfileSnapshot(nextSnapshot));
    if (contextPayload) {
      setContextDocuments(normalizeContextDocuments(contextPayload));
    }
    setIsProfileLoading(false);
  };

  useEffect(() => {
    let cancelled = false;
    void refresh(cachedSnapshot === null).catch((loadError) => {
      if (!cancelled) {
        setError(loadError);
        setIsLoading(false);
      }
    });
    return () => {
      cancelled = true;
    };
  }, [services.client, workspaceId]);

  useEffect(() => {
    let cancelled = false;
    void refreshProfile(cachedProfile === null).catch((loadError) => {
      if (!cancelled) {
        setError(loadError);
        setIsProfileLoading(false);
      }
    });
    return () => {
      cancelled = true;
    };
  }, [services.client]);

  useEffect(() => {
    if (!statusMessage) {
      return;
    }
    const timeout = window.setTimeout(() => {
      setStatusMessage(null);
    }, 4200);
    return () => {
      window.clearTimeout(timeout);
    };
  }, [statusMessage]);

  const categoryLabels = useMemo(
    () => new Map(snapshot.categories.map((category) => [readString(category.id), readString(category.label)])),
    [snapshot.categories],
  );
  const groupedMemoryItems = useMemo(() => {
    const grouped = new Map<MemorySensitivityClass, WorkstationSageMemoryRecord[]>(
      MEMORY_SENSITIVITY_ORDER.map((sensitivity) => [sensitivity, []]),
    );
    snapshot.items.forEach((entry) => {
      grouped.get(inferMemorySensitivity(entry))?.push(entry);
    });
    return grouped;
  }, [snapshot.items]);
  const memoryLimit = readNumber(storagePolicy?.max_entries, MEMORY_ITEM_LIMIT) || MEMORY_ITEM_LIMIT;
  const memoryUsed = readNumber(storagePolicy?.used_entries, snapshot.items.length);
  const pinnedMemoryItems = useMemo(
    () => snapshot.items.filter((entry) => Boolean(entry.pinned)),
    [snapshot.items],
  );
  const recentMemoryItems = useMemo(
    () => sortMemoryEntries(snapshot.items.filter((entry) => !Boolean(entry.pinned) && inferMemorySensitivity(entry) !== 'red')).slice(0, 12),
    [snapshot.items],
  );
  const sensitiveMemoryItems = useMemo(
    () => snapshot.items.filter((entry) => {
      const sensitivity = inferMemorySensitivity(entry);
      return sensitivity === 'orange' || sensitivity === 'red';
    }),
    [snapshot.items],
  );
  const bootstrapQuestion = profileSnapshot.bootstrap.current_question;
  const displayNameHint = profileSnapshot.accountSeed.display_name || profileSnapshot.accountSeed.email || 'Set a preferred name';
  const projectionDocuments = useMemo(() => {
    const sourceDocuments = Object.keys(contextDocuments).length > 0
      ? contextDocuments
      : profileSnapshot.projections;
    const entries = Object.entries(sourceDocuments).flatMap(([filename, content]) => {
      const normalizedFilename = typeof filename === 'string' ? filename.trim() : '';
      if (!normalizedFilename || !normalizedFilename.endsWith('.md')) {
        return [];
      }
      return [{ filename: normalizedFilename, content: typeof content === 'string' ? content : '' }];
    });
    if (entries.length > 0) {
      const defaultFilenames = new Set(entries.map((entry) => entry.filename));
      const fallbackDocuments = buildFallbackMemoryDocuments(profileSnapshot, displayNameHint, memoryUsed, memoryLimit)
        .filter((document) => !defaultFilenames.has(document.filename));
      return [...entries, ...fallbackDocuments];
    }
    return buildFallbackMemoryDocuments(profileSnapshot, displayNameHint, memoryUsed, memoryLimit);
  }, [contextDocuments, displayNameHint, memoryLimit, memoryUsed, profileSnapshot]);
  const memoryDocumentNavItems = useMemo<MemoryDocumentNavItem[]>(() => [
    {
      id: 'overview',
      label: 'Overview',
      description: 'Profile, rules, and carry-forward context',
      icon: BookOpen,
    },
    ...projectionDocuments.map((document) => ({
      id: `projection:${document.filename}`,
      label: formatMemoryDocumentLabel(document.filename),
      description: formatMemoryDocumentDescription(document.filename),
      icon: FileText,
    })),
    {
      id: 'pinned',
      label: 'Pinned',
      description: `${pinnedMemoryItems.length} high-signal facts`,
      icon: Pin,
    },
    {
      id: 'recent',
      label: 'Recent',
      description: `${recentMemoryItems.length} recent facts`,
      icon: Clock,
    },
    {
      id: 'sensitive',
      label: 'Sensitive',
      description: `${sensitiveMemoryItems.length} private facts`,
      icon: Shield,
    },
  ], [pinnedMemoryItems.length, projectionDocuments, recentMemoryItems.length, sensitiveMemoryItems.length]);
  const userMemoryFileCount = useMemo(
    () => projectionDocuments.filter((document) => document.filename.startsWith('memory/files/')).length,
    [projectionDocuments],
  );
  const memoryFileLimitReached = userMemoryFileCount >= MEMORY_FILE_LIMIT;

  useEffect(() => {
    if (!memoryDocumentNavItems.some((item) => item.id === selectedMemoryDocumentId)) {
      setSelectedMemoryDocumentId('overview');
    }
  }, [memoryDocumentNavItems, selectedMemoryDocumentId]);

  useEffect(() => {
    setEditingDocumentId(null);
    setEditingDocumentText('');
  }, [selectedMemoryDocumentId]);

  const saveProfileDraft = async () => {
    if (isProfileSaving) {
      return;
    }
    setIsProfileSaving(true);
    setError(null);
    setStatusMessage(null);
    try {
      const payload = await services.client.updateSageProfile({
        userName: profileDraft.user_name,
        identitySummary: profileDraft.identity_summary,
        communicationStyle: profileDraft.communication_style,
        recurringResponsibility: profileDraft.recurring_responsibility,
        standingRulesText: profileDraft.standing_rules_text,
      });
      const nextSnapshot = normalizeProfileSnapshot(payload);
      services.queryClient.set('chat:canonical:sage-profile', nextSnapshot);
      setProfileSnapshot(nextSnapshot);
      setProfileDraft(draftFromProfileSnapshot(nextSnapshot));
      setStatusMessage('Memory identity updated.');
    } catch (saveError) {
      setError(saveError instanceof Error ? saveError.message : 'Could not update memory identity.');
    } finally {
      setIsProfileSaving(false);
    }
  };

  const openCreateMemoryFile = () => {
    if (memoryFileLimitReached) {
      setStatusMessage(`Memory files are limited to ${MEMORY_FILE_LIMIT}.`);
      return;
    }
    setMemoryFileNameDraft('');
    setError(null);
    setStatusMessage(null);
    setIsMemoryFileSheetOpen(true);
  };

  const openEditMemory = (entry: WorkstationSageMemoryRecord) => {
    setMemoryDraft({
      entryId: readString(entry.id) || null,
      category: readString(entry.category) || 'safe_general',
      title: readString(entry.title),
      content: readString(entry.content),
      pinned: Boolean(entry.pinned),
    });
    setIsMemorySheetOpen(true);
  };

  const submitMemoryDraft = async () => {
    if (mutatingMemory) {
      return;
    }
    const category = readString(memoryDraft.category);
    const title = readString(memoryDraft.title);
    const content = readString(memoryDraft.content);
    if (!category || !title || !content) {
      setStatusMessage('Memory entries need a category, title, and content.');
      return;
    }
    setMutatingMemory(memoryDraft.entryId || 'new');
    setStatusMessage(null);
    try {
      const payload = memoryDraft.entryId
        ? await services.client.updateSageMemoryEntry({
          entryId: memoryDraft.entryId,
          category,
          title,
          content,
          pinned: memoryDraft.pinned,
        })
        : await services.client.createSageMemoryEntry({
          category,
          title,
          content,
          pinned: memoryDraft.pinned,
        });
      const nextSnapshot = normalizeMemorySnapshot(payload);
      const normalizedSnapshot = {
        ...nextSnapshot,
        items: sortMemoryEntries(nextSnapshot.items),
      };
      memoryPaneCache.set(workspaceId, normalizedSnapshot);
      setSnapshot(normalizedSnapshot);
      setIsMemorySheetOpen(false);
      setMemoryDraft(defaultMemoryDraft(category));
      setStatusMessage(memoryDraft.entryId ? 'Memory corrected.' : 'Memory saved.');
    } catch (saveError) {
      setError(saveError instanceof Error ? saveError.message : 'Memory update failed.');
    } finally {
      setMutatingMemory(null);
    }
  };

  const exportMemory = async () => {
    if (mutatingMemory || snapshot.items.length === 0) {
      return;
    }
    setMutatingMemory('export');
    setStatusMessage(null);
    try {
      const payload = await services.client.exportSageMemory();
      const markdown = readString(payload.markdown);
      const content = markdown || JSON.stringify(payload, null, 2);
      const extension = markdown ? 'md' : 'json';
      const blob = new Blob([content], {
        type: markdown ? 'text/markdown;charset=utf-8' : 'application/json;charset=utf-8',
      });
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement('a');
      anchor.href = url;
      anchor.download = `sage-memory-${workspaceId}.${extension}`;
      anchor.click();
      URL.revokeObjectURL(url);
      setStatusMessage('Memory export prepared.');
    } catch (exportError) {
      setError(exportError instanceof Error ? exportError.message : 'Could not export memory.');
    } finally {
      setMutatingMemory(null);
    }
  };

  const saveDocumentEdit = async (filename: string, content: string) => {
    if (mutatingMemory) {
      return;
    }
    const normalizedContent = content.trim();
    if (!normalizedContent) {
      setError('Memory document cannot be empty.');
      return;
    }
    setMutatingMemory(`document:${filename}`);
    setStatusMessage(null);
    setError(null);
    try {
      const payload = await services.client.updateSageContextFile({
        filename,
        content: normalizedContent,
      });
      const savedFilename = readString((payload as Record<string, unknown>).filename) || filename;
      const savedContent = typeof (payload as Record<string, unknown>).content === 'string'
        ? (payload as Record<string, unknown>).content as string
        : normalizedContent;
      setContextDocuments((current) => ({
        ...current,
        [savedFilename]: savedContent,
      }));
      setEditingDocumentId(null);
      setEditingDocumentText('');
      setStatusMessage(`${filename} updated.`);
    } catch (saveError) {
      setError(saveError instanceof Error ? saveError.message : 'Could not update memory document.');
    } finally {
      setMutatingMemory(null);
    }
  };

  const createMemoryFile = async () => {
    if (mutatingMemory) {
      return;
    }
    if (memoryFileLimitReached) {
      setError(`Memory files are limited to ${MEMORY_FILE_LIMIT}.`);
      return;
    }
    const filename = normalizeUserMemoryFilename(memoryFileNameDraft);
    if (!filename) {
      setError('Enter a file name for the new memory file.');
      return;
    }
    const filenameLabel = formatMemoryDocumentLabel(filename).toLowerCase();
    if (projectionDocuments.some((document) => (
      document.filename.toLowerCase() === filename.toLowerCase()
      || formatMemoryDocumentLabel(document.filename).toLowerCase() === filenameLabel
    ))) {
      setError(`${formatMemoryDocumentLabel(filename)} already exists.`);
      return;
    }
    const content = defaultUserMemoryFileContent(filename);
    setMutatingMemory(`document:${filename}`);
    setStatusMessage(null);
    setError(null);
    try {
      const payload = await services.client.updateSageContextFile({
        filename,
        content,
      });
      const savedFilename = readString((payload as Record<string, unknown>).filename) || filename;
      const savedContent = typeof (payload as Record<string, unknown>).content === 'string'
        ? (payload as Record<string, unknown>).content as string
        : content;
      setContextDocuments((current) => ({
        ...current,
        [savedFilename]: savedContent,
      }));
      setSelectedMemoryDocumentId(`projection:${savedFilename}`);
      setMemoryDocumentViewMode('preview');
      setEditingDocumentId(`projection:${savedFilename}`);
      setEditingDocumentText(savedContent);
      setMemoryFileNameDraft('');
      setIsMemoryFileSheetOpen(false);
      setStatusMessage(`${formatMemoryDocumentLabel(savedFilename)} created.`);
    } catch (createError) {
      setError(createError instanceof Error ? createError.message : 'Could not create memory file.');
    } finally {
      setMutatingMemory(null);
    }
  };

  const buildOverviewContent = () => {
    const cleanIdentitySummary = markdownSection(profileSnapshot.profile.identity_summary, 'Identity') || profileSnapshot.profile.identity_summary;
    return `# What Sage carries forward\n\n## Identity\n${cleanIdentitySummary || 'No durable identity summary saved yet.'}\n\n## Communication style\n${profileSnapshot.profile.communication_style || 'No communication preference saved yet.'}\n\n## Recurring responsibility\n${profileSnapshot.profile.recurring_responsibility || 'No recurring responsibility saved yet.'}\n\n## Standing rules\n${profileSnapshot.profile.standing_rules.length === 0 ? 'No standing rules saved yet.' : profileSnapshot.profile.standing_rules.map((rule) => `- ${rule}`).join('\n')}`;
  };

  const renderMemoryDocumentRows = (items: WorkstationSageMemoryRecord[], emptyLabel: string) => (
    <div className="app-memory-document-rows">
      {items.length === 0 ? (
        <div className="app-memory-document-empty">{emptyLabel}</div>
      ) : items.map((entry) => {
        const entryId = readString(entry.id);
        const categoryLabel = categoryLabels.get(readString(entry.category)) || 'Saved memory';
        const updatedLabel = formatMemoryTimestamp(readString(entry.updated_at) || readString(entry.created_at) || null);
        const contentLabel = [readString(entry.title), readString(entry.content)]
          .filter(Boolean)
          .join(' — ');
        return (
          <div key={entryId || `memory-${readString(entry.title)}`} className="app-memory-document-row">
            <button
              type="button"
              className="app-memory-document-row__copy"
              onClick={() => {
                openEditMemory(entry);
              }}
            >
              <span className="app-memory-document-row__content" title={contentLabel || 'Saved memory'}>
                {contentLabel || 'Saved memory'}
              </span>
              <span className="app-memory-document-row__meta">
                {[categoryLabel, updatedLabel, entry.pinned ? 'Pinned' : null].filter(Boolean).join(' · ')}
              </span>
            </button>
          </div>
        );
      })}
    </div>
  );
  const renderDocumentModeSwitch = () => (
    <div className="app-memory-document-mode" role="tablist" aria-label="Memory document view mode">
      <button
        type="button"
        role="tab"
        aria-selected={memoryDocumentViewMode === 'preview'}
        className={`app-memory-document-mode__button${memoryDocumentViewMode === 'preview' ? ' app-memory-document-mode__button--active' : ''}`}
        onClick={() => {
          setMemoryDocumentViewMode('preview');
        }}
      >
        <Eye size={16} strokeWidth={2} aria-hidden="true" />
        <span>Preview</span>
      </button>
      <button
        type="button"
        role="tab"
        aria-selected={memoryDocumentViewMode === 'source'}
        className={`app-memory-document-mode__button${memoryDocumentViewMode === 'source' ? ' app-memory-document-mode__button--active' : ''}`}
        onClick={() => {
          setMemoryDocumentViewMode('source');
        }}
      >
        <Code2 size={16} strokeWidth={2} aria-hidden="true" />
        <span>Source</span>
      </button>
    </div>
  );
  const stripYamlFrontmatter = (raw: string): string => {
    const trimmed = raw.trimStart();
    if (!trimmed.startsWith('---')) {
      return raw;
    }
    const afterFirstDelim = trimmed.indexOf('\n', 3);
    if (afterFirstDelim === -1) {
      return raw;
    }
    const closingIndex = trimmed.indexOf('\n---', afterFirstDelim + 1);
    if (closingIndex === -1) {
      // No closing delimiter — not valid frontmatter, render as-is
      return raw;
    }
    // Skip past the closing "---" line (including its trailing newline)
    const afterClosing = trimmed.indexOf('\n', closingIndex + 4);
    if (afterClosing === -1) {
      // Document is only frontmatter, no body
      return '';
    }
    return trimmed.slice(afterClosing + 1);
  };

  const renderMarkdownPreview = (markdown: string) => {
    const blocks: ReactNode[] = [];
    let listItems: string[] = [];
    const flushList = () => {
      if (listItems.length === 0) {
        return;
      }
      const listKey = `list-${blocks.length}`;
      blocks.push(
        <ul key={listKey}>
          {listItems.map((item, index) => (
            <li key={`${listKey}-${index}`}>{item}</li>
          ))}
        </ul>,
      );
      listItems = [];
    };

    markdown.split('\n').forEach((line, index) => {
        const trimmed = line.trim();
        if (!trimmed) {
          flushList();
          return;
        }
        if (trimmed.startsWith('# ')) {
          flushList();
          blocks.push(<h3 key={`${index}:${trimmed}`}>{trimmed.slice(2)}</h3>);
          return;
        }
        if (trimmed.startsWith('## ')) {
          flushList();
          blocks.push(<h4 key={`${index}:${trimmed}`}>{trimmed.slice(3)}</h4>);
          return;
        }
        if (trimmed.startsWith('### ')) {
          flushList();
          blocks.push(<h5 key={`${index}:${trimmed}`}>{trimmed.slice(4)}</h5>);
          return;
        }
        if (trimmed.startsWith('- ')) {
          listItems.push(trimmed.slice(2));
          return;
        }
        flushList();
        blocks.push(<p key={`${index}:${trimmed}`}>{trimmed}</p>);
      });
    flushList();

    return (
      <div className="app-memory-document-prose">
        {blocks}
      </div>
    );
  };
  const renderEditableDocumentBody = (documentId: string, filename: string, content: string) => {
    const isEditing = editingDocumentId === documentId;
    if (isEditing) {
      return (
        <div className="app-memory-document-editor">
          <AppTextarea
            className="app-memory-document-textarea"
            value={editingDocumentText}
            onChange={(event) => {
              setEditingDocumentText(event.currentTarget.value);
            }}
            rows={Math.max(18, editingDocumentText.split('\n').length + 2)}
            spellCheck
          />
        </div>
      );
    }
    return memoryDocumentViewMode === 'source'
      ? <pre className="app-memory-document-markdown">{content}</pre>
      : renderMarkdownPreview(stripYamlFrontmatter(content));
  };
  const renderDocumentToolbar = (documentId: string, filename: string, content: string) => {
    const isEditing = editingDocumentId === documentId;
    return (
    <div className="app-inline-actions app-inline-actions--between app-memory-document-toolbar">
      {renderDocumentModeSwitch()}
      <div className="app-memory-document-toolbar__actions">
        {isEditing ? (
          <>
            <AppButton
              type="button"
              tone="secondary"
              onClick={() => {
                setEditingDocumentId(null);
                setEditingDocumentText('');
              }}
              disabled={Boolean(mutatingMemory)}
            >
              Cancel
            </AppButton>
            <AppButton
              type="button"
              tone="primary"
              onClick={() => {
                void saveDocumentEdit(filename, editingDocumentText);
              }}
              disabled={Boolean(mutatingMemory)}
            >
              {mutatingMemory === `document:${filename}` ? 'Saving…' : 'Save'}
            </AppButton>
          </>
        ) : (
          <AppButton
            type="button"
            tone="primary"
            onClick={() => {
              setEditingDocumentId(documentId);
              setEditingDocumentText(content);
            }}
          >
            <Pencil size={15} strokeWidth={2} aria-hidden="true" />
            Edit
          </AppButton>
        )}
      </div>
    </div>
    );
  };
  const renderSelectedMemoryDocument = () => {
    const selectedProjection = selectedMemoryDocumentId.startsWith('projection:')
      ? projectionDocuments.find((document) => `projection:${document.filename}` === selectedMemoryDocumentId) ?? null
      : null;
    if (selectedProjection) {
      const documentId = `projection:${selectedProjection.filename}`;
      return (
        <div className="app-memory-document-shell">
          {renderDocumentToolbar(documentId, selectedProjection.filename, selectedProjection.content)}
          <article className="settings-detail-card app-memory-document-panel">
            {renderEditableDocumentBody(documentId, selectedProjection.filename, selectedProjection.content)}
          </article>
        </div>
      );
    }
    if (selectedMemoryDocumentId === 'pinned') {
      return (
        <article className="settings-detail-card app-memory-document-panel">
          <div className="app-memory-document-panel__header">
            <span className="app-memory-document-panel__eyebrow">Carry-forward memory</span>
            <h2>Pinned facts</h2>
            <p>High-signal facts Sage should keep prominent across future conversations.</p>
          </div>
          {renderMemoryDocumentRows(pinnedMemoryItems, 'No pinned facts yet.')}
        </article>
      );
    }
    if (selectedMemoryDocumentId === 'recent') {
      return (
        <article className="settings-detail-card app-memory-document-panel">
          <div className="app-memory-document-panel__header">
            <span className="app-memory-document-panel__eyebrow">Carry-forward memory</span>
            <h2>Recent memory</h2>
            <p>Facts Sage recently saved or updated.</p>
          </div>
          {renderMemoryDocumentRows(recentMemoryItems, 'No recent memory yet.')}
        </article>
      );
    }
    if (selectedMemoryDocumentId === 'sensitive') {
      return (
        <article className="settings-detail-card app-memory-document-panel">
          <div className="app-memory-document-panel__header">
            <span className="app-memory-document-panel__eyebrow">Protected memory</span>
            <h2>Sensitive memory</h2>
            <p>Private and restricted facts that require tighter handling.</p>
          </div>
          {renderMemoryDocumentRows(sensitiveMemoryItems, 'No sensitive memory saved.')}
        </article>
      );
    }
    const overviewContent = contextDocuments['IDENTITY.md'] || buildOverviewContent();
    return (
      <div className="app-memory-document-shell">
        {renderDocumentToolbar('overview', 'IDENTITY.md', overviewContent)}
        <article className="settings-detail-card app-memory-document-panel">
          {renderEditableDocumentBody('overview', 'IDENTITY.md', overviewContent)}
        </article>
      </div>
    );
  };

  return (
    <>
      {error ? <DataPaneError error={error} onRetry={() => { void refresh(false); void refreshProfile(false); }} label="Memory" /> : null}
      {!error && statusMessage ? (
        <PlatformNotification
          tone="success"
          title="Memory updated"
          detail={statusMessage}
          onClose={() => {
            setStatusMessage(null);
          }}
        />
      ) : null}
      <WorkstationSurfaceRoot surface="memory">
      <main className="app-memory-document-page">
        {isLoading || isProfileLoading ? (
          <div className="app-stack-3">
            <SkeletonBlock height="7rem" />
            <SkeletonBlock height="12rem" />
            <SkeletonBlock height="18rem" />
          </div>
        ) : (
          <>
            <WorkstationSplitWorkbench
              ariaLabel="Memory"
              className="app-memory-split-workbench"
              sidebar={(
                <div className="app-memory-document-nav">
                  {memoryDocumentNavItems.map((item) => {
                    const Icon = item.icon;
                    return (
                      <button
                        key={item.id}
                        type="button"
                        aria-selected={item.id === selectedMemoryDocumentId}
                        className={`app-memory-document-nav__item${item.id === selectedMemoryDocumentId ? ' app-memory-document-nav__item--active' : ''}`}
                        title={item.description}
                        onClick={() => {
                          setSelectedMemoryDocumentId(item.id);
                        }}
                      >
                        <Icon size={16} strokeWidth={2} aria-hidden="true" />
                        <span>{item.label}</span>
                      </button>
                    );
                  })}
                </div>
              )}
              sidebarFooter={(
                <div className="app-memory-document-sidebar__footer">
                  <button
                    type="button"
                    onClick={openCreateMemoryFile}
                    disabled={Boolean(mutatingMemory) || memoryFileLimitReached}
                  >
                    <Plus size={14} strokeWidth={2} aria-hidden="true" />
                    <span>New file</span>
                  </button>
                  <button
                    type="button"
                    onClick={() => {
                      void exportMemory();
                    }}
                    disabled={Boolean(mutatingMemory) || snapshot.items.length === 0}
                  >
                    <Download size={14} strokeWidth={2} aria-hidden="true" />
                    <span>Export</span>
                  </button>
                </div>
              )}
            >
              {renderSelectedMemoryDocument()}
            </WorkstationSplitWorkbench>
          </>
        )}
      </main>

      <CommandSheet
        open={isIdentitySheetOpen}
        title="Correct Sage memory"
        description="Use this only to correct durable identity, tone, or standing rules. Normal learning should happen through chat."
        onClose={() => {
          setIsIdentitySheetOpen(false);
          setProfileDraft(draftFromProfileSnapshot(profileSnapshot));
        }}
        actions={(
          <>
            <button
              type="button"
              className="app-memory-sheet__link"
              disabled={isProfileSaving}
              onClick={() => {
                setIsIdentitySheetOpen(false);
                setProfileDraft(draftFromProfileSnapshot(profileSnapshot));
              }}
            >
              Cancel
            </button>
            <button
              type="button"
              className="app-button"
              disabled={isProfileSaving}
              onClick={() => {
                void saveProfileDraft().then(() => {
                  setIsIdentitySheetOpen(false);
                });
              }}
            >
              {isProfileSaving ? 'Saving…' : 'Save correction'}
            </button>
          </>
        )}
      >
        <FormSection
          title="Durable identity"
          description="This updates the structured backend truth Sage carries into future turns."
        >
          <FormGrid columns="repeat(2, minmax(0, 1fr))">
            <FormField label="Name">
              <FormInput
                value={profileDraft.user_name}
                onChange={(event) => {
                  setProfileDraft((current) => ({ ...current, user_name: event.currentTarget.value }));
                }}
                placeholder={displayNameHint}
              />
            </FormField>
            <FormField label="Recurring responsibility">
              <FormInput
                value={profileDraft.recurring_responsibility}
                onChange={(event) => {
                  setProfileDraft((current) => ({ ...current, recurring_responsibility: event.currentTarget.value }));
                }}
                placeholder="Example: Keep my inbox triaged."
              />
            </FormField>
          </FormGrid>
          <FormGrid columns="1fr">
            <FormField label="Identity">
              <FormTextarea
                rows={4}
                value={profileDraft.identity_summary}
                onChange={(event) => {
                  setProfileDraft((current) => ({ ...current, identity_summary: event.currentTarget.value }));
                }}
                placeholder="Example: I run product and engineering for Empyralis."
              />
            </FormField>
            <FormField label="Tone and communication style">
              <FormTextarea
                rows={4}
                value={profileDraft.communication_style}
                onChange={(event) => {
                  setProfileDraft((current) => ({ ...current, communication_style: event.currentTarget.value }));
                }}
                placeholder="Example: Be direct, concise, and lead with the answer."
              />
            </FormField>
            <FormField label="Standing rules" hint="One rule per line. These are stronger than casual chat preferences.">
              <FormTextarea
                rows={5}
                value={profileDraft.standing_rules_text}
                onChange={(event) => {
                  setProfileDraft((current) => ({ ...current, standing_rules_text: event.currentTarget.value }));
                }}
                placeholder="Example: Never send external messages without approval."
              />
            </FormField>
          </FormGrid>
        </FormSection>
      </CommandSheet>

      <CommandSheet
        open={isMemoryFileSheetOpen}
        title="New memory file"
        description={`Create a durable markdown file for Sage. Custom memory files are capped at ${MEMORY_FILE_LIMIT}.`}
        onClose={() => {
          setIsMemoryFileSheetOpen(false);
          setMemoryFileNameDraft('');
        }}
        actions={(
          <>
            <button
              type="button"
              className="app-memory-sheet__link"
              disabled={Boolean(mutatingMemory)}
              onClick={() => {
                setIsMemoryFileSheetOpen(false);
                setMemoryFileNameDraft('');
              }}
            >
              Cancel
            </button>
            <button
              type="button"
              className="app-button"
              disabled={Boolean(mutatingMemory) || memoryFileLimitReached}
              onClick={() => {
                void createMemoryFile();
              }}
            >
              {mutatingMemory?.startsWith('document:memory/files/') ? 'Creating…' : 'Create file'}
            </button>
          </>
        )}
      >
        <FormSection
          title="File name"
          description="Use a focused name. It will appear in the Memory list and open directly as an editable markdown file."
        >
          <FormGrid columns="1fr">
            <FormField
              label="Name"
              hint={`Example: SHOP_PLAYBOOK. Stored as ${normalizeUserMemoryFilename(memoryFileNameDraft) || 'memory/files/NAME.md'}.`}
            >
              <FormInput
                value={memoryFileNameDraft}
                onChange={(event) => {
                  setMemoryFileNameDraft(event.currentTarget.value);
                }}
                placeholder="SHOP_PLAYBOOK"
                autoFocus
              />
            </FormField>
          </FormGrid>
        </FormSection>
      </CommandSheet>

      <CommandSheet
        open={isMemorySheetOpen}
        title={memoryDraft.entryId ? 'Edit memory' : 'Add memory'}
        description="Save only what Sage should carry into future turns."
        onClose={() => {
          setIsMemorySheetOpen(false);
          setMemoryDraft(defaultMemoryDraft('safe_general'));
        }}
        actions={(
          <>
            <button
              type="button"
              className="app-memory-sheet__link"
              disabled={Boolean(mutatingMemory)}
              onClick={() => {
                setIsMemorySheetOpen(false);
                setMemoryDraft(defaultMemoryDraft('safe_general'));
              }}
            >
              Cancel
            </button>
            <button
              type="button"
              className="app-button"
              disabled={Boolean(mutatingMemory)}
              onClick={() => {
                void submitMemoryDraft();
              }}
            >
              {mutatingMemory ? 'Saving…' : 'Save'}
            </button>
          </>
        )}
      >
        <FormSection
          title="Memory entry"
          description="Choose the right category so Sage treats it with the right weight."
        >
          <FormGrid columns="repeat(2, minmax(0, 1fr))">
            <FormField label="Category">
              <select
                className="app-select"
                value={memoryDraft.category}
                onChange={(event) => {
                  setMemoryDraft((current) => ({ ...current, category: event.currentTarget.value }));
                }}
              >
                {snapshot.categories.map((category) => (
                  <option key={category.id} value={category.id}>
                    {category.label}
                  </option>
                ))}
              </select>
            </FormField>
            <FormField label="Pinned">
              <button
                type="button"
                className="app-button app-button--secondary"
                onClick={() => {
                  setMemoryDraft((current) => ({ ...current, pinned: !current.pinned }));
                }}
              >
                {memoryDraft.pinned ? 'Pinned' : 'Pin memory'}
              </button>
            </FormField>
          </FormGrid>
          <FormGrid columns="1fr">
            <FormField label="Title">
              <FormInput
                value={memoryDraft.title}
                onChange={(event) => {
                  setMemoryDraft((current) => ({ ...current, title: event.currentTarget.value }));
                }}
                placeholder="Example: Preferred working style"
              />
            </FormField>
            <FormField label="Content">
              <FormTextarea
                rows={5}
                value={memoryDraft.content}
                onChange={(event) => {
                  setMemoryDraft((current) => ({ ...current, content: event.currentTarget.value }));
                }}
                placeholder="Example: Prefers concise updates with clear next actions."
              />
            </FormField>
          </FormGrid>
        </FormSection>
      </CommandSheet>

      </WorkstationSurfaceRoot>
    </>
  );
}
